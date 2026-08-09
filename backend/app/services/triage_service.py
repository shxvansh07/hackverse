"""Orchestration for the patient intake conversation.

The order of operations in `process_message` is the safety design and must not
be rearranged:

    1. record what the patient said
    2. LLM extracts structured facts   <- model may be wrong; output is validated
    3. merge into case state           <- additive only; never overwrites with blanks
    4. DETERMINISTIC safety assessment <- sole authority on routing
    5. branch on the assessment        <- urgent short-circuits everything below
    6. generate a reply

Step 4 never reads a model's opinion of risk. Step 5 never runs before step 4.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.ai.schemas import ExtractedClinicalInfo
from app.ai.service import ai_service
from app.safety import engine as safety_engine
from app.safety import specialty
from app.safety.engine import SafetyAssessment
from app.services import deterministic_extraction
from app.services import fallback_questions as fq
from app.shared.database import db
from app.shared.models import (
    ChatMessage,
    PatientSession,
    PatientStatus,
    RiskState,
    SafetySignal,
    TriageCase,
)

logger = logging.getLogger(__name__)

#: Explicit "nothing further" signals across supported languages.
#:
#: Denials only. Acknowledgements — "ok", "okay", "thank you", "thanks" —
#: are deliberately absent: a patient saying "ok" to a question is agreeing
#: to keep going, not declining to say more, and reading them as denials
#: both ended the interview early and let apply_negative_confirmation record
#: a "no" against whichever clinical field happened to be pending.
_NEGATIVE_PATTERNS = [
    r"\bno\b", r"\bnope\b", r"\bnone\b", r"\bnothing\b", r"\bnothing else\b",
    r"\bthat'?s all\b", r"\bthats all\b", r"\bthat'?s it\b", r"\bthats it\b",
    r"\bthat will be it\b", r"\bthat is all\b", r"\bthat is it\b",
    r"\bno more\b", r"\bno other\b", r"\bno thank you\b", r"\bno thanks\b",
    r"\ball good\b",
    r"\bnahi\b", r"\bnahin\b", r"\bnai\b", r"\bbas\b", r"\bbas itna\b",
    "नहीं", "नही", "कुछ नहीं", "बस", "और कुछ नहीं",
    "இல்லை", "లేదు", "இல்ல", "ના", "নেই", "নাই",
]


def _is_negative(text: str) -> bool:
    normalised = safety_engine.normalise(text)
    if not normalised or len(normalised) > 40:
        return False
    return any(re.search(pattern, normalised) for pattern in _NEGATIVE_PATTERNS)


def _merge_terms(existing: List[str], incoming: Sequence[str], limit: int = 20) -> List[str]:
    """Additive union preserving order. Never removes an established fact."""
    seen = {e.lower() for e in existing}
    merged = list(existing)
    for term in incoming:
        key = term.lower()
        if key not in seen:
            seen.add(key)
            merged.append(term)
        if len(merged) >= limit:
            break
    return merged


class TriageService:
    # ------------------------------------------------------------- sessions

    @staticmethod
    def create_session(
        preferred_language: str,
        patient_name: str = "Patient",
        phone: Optional[str] = None,
        patient_id: Optional[str] = None,
    ) -> Tuple[PatientSession, TriageCase]:
        from app.shared.languages import resolve

        language = resolve(preferred_language)

        # An explicit patient_id (from a prior POST /api/patient/profile
        # registration) is the strongest identity signal — honour it, but
        # only once verified against the store. This value is client-
        # supplied (the frontend reads it back from localStorage) and
        # nothing before this point has ever checked it was actually issued
        # by this app rather than forged, stale, or a typo — trusting it
        # blindly would let one client see another patient's carried-forward
        # history just by supplying their id. An unrecognized id is treated
        # exactly as if none were supplied, falling through to the phone
        # path below.
        if patient_id and not db.is_known_patient_id(patient_id):
            patient_id = None

        # Otherwise a phone number links this visit to any past ones by the
        # same patient (see database.get_or_create_patient_id). Left blank,
        # this behaves exactly as before phone-based identity existed: a
        # fresh patient_id, no history.
        if not patient_id and phone:
            patient_id = db.get_or_create_patient_id(phone)

        session_kwargs: Dict[str, Any] = dict(
            preferred_language=language.code,
            patient_name=patient_name or "Patient",
            status=PatientStatus.COLLECTING_INFORMATION,
        )
        if patient_id:
            session_kwargs["patient_id"] = patient_id
        session = PatientSession(**session_kwargs)

        patient_profile = db.get_patient(session.patient_id) if session.patient_id else None

        case = TriageCase(
            session_id=session.session_id,
            patient_id=session.patient_id,
            patient_name=patient_profile.name if patient_profile else session.patient_name,
            age=patient_profile.age if patient_profile else "",
            preferred_language=language.code,
        )

        # Carry forward settled allergy/history facts from the most recent
        # prior visit — never symptoms or red flags, and always auditable via
        # a transcript entry, never silent. Scoped to the two fields already
        # central to the safety engine's completeness check
        # (safety.engine.compute_missing_information) rather than an
        # open-ended history blob, so a returning patient isn't re-asked
        # settled questions without risking the model treating unverified
        # past narrative as a fact stated this visit.
        prior_cases = db.get_cases_by_patient(patient_id) if patient_id else []
        if prior_cases:
            # Deterministic (not LLM-generated) one-line context for the
            # conversation prompt — see TriageCase.prior_visit_note. Built
            # once here rather than fetched fresh each turn, since the set
            # of prior visits is fixed for the lifetime of this case.
            visit_descriptions = [
                c.chief_complaint or (c.symptoms[0] if c.symptoms else "an unspecified complaint")
                for c in prior_cases[:3]
            ]
            case.prior_visit_note = (
                f"Seen {len(prior_cases)} time(s) before, most recently for: "
                + "; ".join(visit_descriptions)
                + ". Context only — do not assume today's complaint is the same "
                "or related unless the patient says so."
            )

            most_recent = prior_cases[0]
            carried: List[str] = []
            if most_recent.allergies_confirmed:
                case.allergies = list(most_recent.allergies)
                case.allergies_confirmed = True
                carried.append("allergy status")
            if most_recent.history_confirmed:
                case.medical_history = list(most_recent.medical_history)
                case.history_confirmed = True
                carried.append("medical history")
            if carried:
                case.carried_forward_from_previous_visit = True
                case.transcript.append(
                    ChatMessage(
                        sender="ai",
                        text=(
                            f"Carried forward from a previous visit: {', '.join(carried)}. "
                            "The doctor can see and revise this."
                        ),
                    )
                )

        case.transcript.append(ChatMessage(sender="ai", text=fq.greeting(language.code)))

        db.save_session(session)
        db.save_case(case)
        db.record_audit(
            "SESSION_CREATED", case_id=case.case_id, actor="patient",
            detail=f"Session opened in {language.english_name}"
            + (" (returning patient)" if prior_cases else ""),
        )
        return session, case

    # ------------------------------------------------------------ extraction

    @staticmethod
    def merge_extraction(case: TriageCase, extracted: ExtractedClinicalInfo) -> TriageCase:
        """Fold validated model output into case state.

        Additive by construction. A field the model omitted leaves the existing
        value untouched, so a later turn cannot erase an earlier disclosure —
        and the model never gets to blank out a known allergy.
        """
        case.symptoms = _merge_terms(case.symptoms, extracted.symptoms)
        case.associated_symptoms = _merge_terms(case.associated_symptoms, extracted.associated_symptoms)
        case.medical_history = _merge_terms(case.medical_history, extracted.medical_history)
        case.medications = _merge_terms(case.medications, extracted.medications)
        case.allergies = _merge_terms(case.allergies, extracted.allergies)

        # Scalars: first stated value wins. Re-stating is fine; overwriting a
        # known duration with a vaguer one is not.
        if extracted.duration and not case.duration:
            case.duration = extracted.duration
        if extracted.severity and not case.severity:
            case.severity = extracted.severity
        if extracted.age and not case.age:
            case.age = extracted.age

        # A positive finding confirms the field; an explicit denial confirms it
        # too, with an empty list. Only "neither happened" leaves it open.
        if extracted.allergies or extracted.denies_allergies:
            case.allergies_confirmed = True
        if extracted.medical_history or extracted.denies_medical_history:
            case.history_confirmed = True

        if not case.chief_complaint and case.symptoms:
            case.chief_complaint = case.symptoms[0]

        return case

    @staticmethod
    def apply_negative_confirmation(case: TriageCase, awaiting_field: Optional[str]) -> None:
        """A bare "no" answers whichever question we just asked.

        Without this, saying "no allergies" would leave allergies_confirmed
        false forever and the case would sit in UNCERTAIN with nothing to ask.
        """
        if awaiting_field == "allergies":
            case.allergies_confirmed = True
        elif awaiting_field == "medical_history":
            case.history_confirmed = True
        elif awaiting_field in {"associated_symptoms", "medications", "anything_else"}:
            pass

    @staticmethod
    def apply_deterministic_capture(
        case: TriageCase, awaiting_field: Optional[str], patient_text: str,
    ) -> None:
        """Record a direct, literal answer against whichever field was just
        asked about, when no LLM parsed this turn (no provider configured, or
        the call failed) — see the `extracted is None` branch in
        process_message.

        Without this, `case.symptoms` and `case.duration` — both required
        fields — could never be populated at all when running with zero LLM
        providers, since extraction was the only thing that ever wrote them.
        Intake would then loop forever: the fallback question bank correctly
        cycles through every question once, but completion always stays
        blocked because "symptoms"/"duration" never leave missing_information.

        This is literal capture, not interpretation — the same "never invent"
        principle as merge_extraction, just storing what was actually typed
        instead of a model's structured reading of it. A negative answer
        ("no") is handled separately by apply_negative_confirmation and is
        skipped here so a bare "no" doesn't become a literal symptom.
        """
        text = patient_text.strip()
        if not text or _is_negative(text):
            return

        if awaiting_field == "symptoms" and not case.symptoms:
            case.symptoms = [text]
            if not case.chief_complaint:
                case.chief_complaint = text
        elif awaiting_field == "duration" and not case.duration:
            case.duration = text
        elif awaiting_field == "associated_symptoms":
            case.associated_symptoms = _merge_terms(case.associated_symptoms, [text])
        elif awaiting_field == "medications":
            case.medications = _merge_terms(case.medications, [text])
        elif awaiting_field == "allergies":
            case.allergies = _merge_terms(case.allergies, [text])
            case.allergies_confirmed = True
        elif awaiting_field == "medical_history":
            case.medical_history = _merge_terms(case.medical_history, [text])
            case.history_confirmed = True

    @staticmethod
    def infer_awaiting_field(case: TriageCase) -> Optional[str]:
        """Which field the last AI turn was asking about.

        Matched against the deterministic bank across all languages; when the
        LLM authored the question we fall back to the first missing field,
        which is what the prompt told it to ask about. The greeting itself
        implicitly asks about symptoms — without recognising that, a
        patient's very first answer (before any tracked question has been
        asked) has nowhere to be filed and is silently dropped.
        """
        last_ai = next((m.text for m in reversed(case.transcript) if m.sender == "ai"), None)
        if not last_ai:
            return "symptoms"

        for field, translations in fq.QUESTIONS.items():
            if last_ai in translations.values():
                return field

        if last_ai in fq.GREETINGS.values():
            return "symptoms"

        return case.missing_information[0] if case.missing_information else None

    # -------------------------------------------------------------- assessment

    @staticmethod
    def assess_case(case: TriageCase, latest_text: str = "", llm_hints: Sequence[str] = ()) -> SafetyAssessment:
        """Run the deterministic safety engine over current state.

        The only place a triage state is decided.
        """
        assessment = safety_engine.assess(
            symptoms=case.symptoms,
            associated_symptoms=case.associated_symptoms,
            raw_text=latest_text,
            duration=case.duration,
            allergies=case.allergies,
            medical_history=case.medical_history,
            allergies_confirmed=case.allergies_confirmed,
            history_confirmed=case.history_confirmed,
            llm_hints=llm_hints,
            transcript_text=case.transcript_text(),
            severity=case.severity,
        )

        case.triage_status = assessment.risk_state
        case.red_flags = assessment.red_flags
        case.missing_information = assessment.missing_information
        case.safety_signal = SafetySignal(**assessment.to_dict())
        return assessment

    # ------------------------------------------------------------ conversation

    @classmethod
    async def process_message(
        cls, session: PatientSession, case: TriageCase, patient_text: str,
    ) -> Dict[str, Any]:
        lang = session.preferred_language
        awaiting_field = cls.infer_awaiting_field(case)

        case.transcript.append(ChatMessage(sender="patient", text=patient_text))

        # --- step 2: structured extraction (model output, validated) --------
        # Falls back to a keyword-based extractor when no provider is reachable,
        # so a fully offline configuration can still reach LOW_RISK rather than
        # looping the question bank forever with nothing ever recorded.
        history = [{"sender": m.sender, "text": m.text} for m in case.transcript[:-1]]
        extracted = await ai_service.extract_clinical_info(patient_text, history)
        used_fallback_extraction = False

        if extracted is None:
            extracted = deterministic_extraction.extract(patient_text)
            used_fallback_extraction = extracted is not None

        llm_hints: List[str] = []
        denies_more = False

        if extracted is not None:
            cls.merge_extraction(case, extracted)
            if not used_fallback_extraction:
                llm_hints = extracted.possible_red_flags
            denies_more = extracted.patient_denies_more_info
        else:
            # No LLM parsed this turn (no provider configured, or the call
            # failed) — capture the raw answer literally so intake can still
            # progress and complete without one. See apply_deterministic_capture.
            cls.apply_deterministic_capture(case, awaiting_field, patient_text)

        if _is_negative(patient_text):
            denies_more = True
            cls.apply_negative_confirmation(case, awaiting_field)

        # --- step 4: deterministic safety assessment ------------------------
        assessment = cls.assess_case(case, latest_text=patient_text, llm_hints=llm_hints)

        # --- step 5: URGENT short-circuits the entire prescription pathway ---
        if assessment.risk_state == RiskState.URGENT:
            reply = fq.urgent_message(lang)
            case.transcript.append(ChatMessage(sender="ai", text=reply))
            case.recommended_specialty = specialty.recommend_specialty(assessment.red_flag_codes)
            session.status = PatientStatus.URGENT_ESCALATION
            db.record_audit(
                "URGENT_ESCALATION", case_id=case.case_id, actor="system",
                detail="Deterministic red flag match; prescription pathway blocked",
                metadata={"red_flag_codes": assessment.red_flag_codes},
            )
            db.save_case(case)
            db.save_session(session)
            return {
                "reply": reply,
                "is_complete": True,
                "assessment": assessment,
                "urgent": True,
                "urgent_guidance": reply,
            }

        # --- intake completion ---------------------------------------------
        # A "no" mid-interview only answers whatever factual question was
        # just asked (see apply_negative_confirmation) — it must not also end
        # the whole conversation. Completion requires the *closing* question
        # specifically, so the patient always gets asked "anything else, or
        # should I send this to your doctor?" before handoff, rather than the
        # interview ending abruptly on an early "no allergies".
        #
        # Whether this turn IS the closing question is tracked as explicit
        # state (case.awaiting_closing_question), set only by this method,
        # never inferred by re-matching the AI's last message text against
        # the fixed question bank. That matching approach (the previous
        # design) only succeeds when the reply is the exact canned string —
        # an LLM freely phrasing its own questions never produces that
        # string, which made completion reachable only by accident (when the
        # LLM happened to repeat itself and the code fell back to canned
        # text). The model also has its own opinion about when a
        # conversation "sounds" finished (see _CONVERSATION_SYSTEM's
        # constraints in ai/prompts.py) — that opinion is never authoritative
        # here, same rule as every other safety-relevant decision in this
        # module.
        if case.awaiting_closing_question:
            intake_complete = denies_more
            if not intake_complete:
                # Patient added something instead of declining — already
                # captured in step 2/3 above. Drop back to normal questioning
                # rather than treating a non-answer as "done".
                case.awaiting_closing_question = False
        elif len([m for m in case.transcript if m.sender == "ai"]) > len(fq.QUESTION_ORDER):
            # We've now asked as many questions as the fixed interview has
            # categories for (fallback_questions.QUESTION_ORDER) — the same
            # cadence the deterministic bank always walked, kept identical
            # here so an active LLM doesn't wrap up earlier just because the
            # 3 safety-required fields (symptoms/duration/allergies) happen
            # to resolve before associated_symptoms/medical_history/
            # medications get asked about. Ask the closing question now —
            # verbatim, never left to the model to phrase — so next turn's
            # answer can be detected reliably regardless of who is driving
            # the conversation.
            reply = fq.anything_else_question(lang)
            case.transcript.append(ChatMessage(sender="ai", text=reply))
            case.awaiting_closing_question = True
            session.status = PatientStatus.COLLECTING_INFORMATION
            db.save_case(case)
            db.save_session(session)
            return {"reply": reply, "is_complete": False, "assessment": assessment, "urgent": False}
        else:
            intake_complete = False

        if intake_complete:
            reply = fq.handoff_message(lang)
            case.transcript.append(ChatMessage(sender="ai", text=reply))
            session.status = PatientStatus.ASSESSING
            case.awaiting_closing_question = False
            # allergies_confirmed / history_confirmed are NOT set here. They
            # mean "the patient was asked and answered", and reaching the end
            # of the interview is not an answer. Marking them on handoff told
            # the safety engine a required field was established when it had
            # never been raised, which is enough on its own to carry a case to
            # LOW_RISK and auto-draft against an unknown allergy status.
            db.save_case(case)
            db.save_session(session)
            return {
                "reply": reply, "is_complete": True,
                "assessment": assessment, "urgent": False,
            }

        # --- step 6: next conversational turn -------------------------------
        previously_asked = [m.text for m in case.transcript if m.sender == "ai"]
        reply = await ai_service.generate_reply(
            patient_message=patient_text,
            history=history,
            lang=lang,
            known_facts=case.known_facts(),
            missing_info=assessment.missing_information,
            previously_asked=previously_asked,
            history_note=case.prior_visit_note,
        )

        if not reply:
            # AI unreachable or repeating itself: deterministic bank keeps the
            # interview moving rather than dead-ending the patient.
            reply = fq.next_question(lang, previously_asked)

        # Intake is not ended by matching phrases in the model's own prose.
        # Whether the interview is over is a fact about which questions have
        # been asked and answered, and the model does not know that — it was
        # ending intake whenever it happened to write "let the doctor" or
        # "wrap up", which is exactly the "step 4 never reads a model's
        # opinion" rule this module opens with. The fixed question order plus
        # the closing-question gate above is what terminates the interview.

        case.transcript.append(ChatMessage(sender="ai", text=reply))
        session.status = PatientStatus.COLLECTING_INFORMATION
        db.save_case(case)
        db.save_session(session)

        return {"reply": reply, "is_complete": False, "assessment": assessment, "urgent": False}

    # ---------------------------------------------------------------- summary

    @classmethod
    async def build_summary(cls, case: TriageCase) -> str:
        """English clinical summary, with a deterministic fallback.

        The doctor must always get a readable summary, including when every
        LLM provider is down.
        """
        payload = {
            "chief_complaint": case.chief_complaint,
            "symptoms": case.symptoms,
            "associated_symptoms": case.associated_symptoms,
            "duration": case.duration,
            "severity": case.severity,
            "medical_history": case.medical_history,
            "current_medications": case.medications,
            "allergies": case.allergies,
            "allergy_status_established": case.allergies_confirmed,
            "age": case.age,
            "red_flags": case.red_flags,
            "triage_status": case.triage_status.value,
        }

        try:
            summary = await asyncio.wait_for(ai_service.generate_summary(payload), timeout=3.5)
            return summary or cls.deterministic_summary(case)
        except Exception:
            logger.warning("Summary generation timed out or failed; falling back to deterministic summary")
            return cls.deterministic_summary(case)

    @staticmethod
    def deterministic_summary(case: TriageCase) -> str:
        parts: List[str] = []
        symptoms = ", ".join(case.symptoms) if case.symptoms else "unspecified symptoms"
        opening = f"Patient presents with {symptoms}"
        if case.severity:
            opening += f" of {case.severity.lower()} severity"
        if case.duration:
            opening += f", present for {case.duration}"
        parts.append(opening + ".")

        if case.associated_symptoms:
            parts.append(f"Associated symptoms: {', '.join(case.associated_symptoms)}.")

        parts.append(
            f"Medical history: {', '.join(case.medical_history)}."
            if case.medical_history
            else ("Medical history: none reported." if case.history_confirmed
                  else "Medical history: not established.")
        )
        parts.append(
            f"Current medications: {', '.join(case.medications)}."
            if case.medications else "Current medications: none reported."
        )
        parts.append(
            f"Allergies: {', '.join(case.allergies)}."
            if case.allergies
            else ("Allergies: none reported." if case.allergies_confirmed
                  else "Allergy status not established.")
        )
        if case.red_flags:
            parts.append(f"Red flags: {', '.join(case.red_flags)}.")

        return " ".join(parts)

    # -------------------------------------------------------------- history

    @classmethod
    async def build_patient_history(cls, case: TriageCase) -> Optional[Dict[str, Any]]:
        """Doctor-facing visit history for a returning (phone-linked)
        patient, or None for a first-time patient.

        The visit list itself is deterministic and always present when there
        is history — it is a plain read of past cases, not a model output.
        The `highlight` field is a supplementary LLM-generated one-line
        summary and is genuinely optional: a timeout or provider failure
        simply omits it, since the visit list alone is already useful.
        """
        prior_cases = db.get_cases_by_patient(case.patient_id, exclude_case_id=case.case_id)
        if not prior_cases:
            return None

        recent = [
            {
                "case_id": c.case_id,
                "created_at": c.created_at,
                "chief_complaint": c.chief_complaint or (c.symptoms[0] if c.symptoms else ""),
                "triage_status": c.triage_status.value,
            }
            for c in prior_cases[:5]
        ]

        highlight: Optional[str] = None
        try:
            visits_payload = [
                {
                    "created_at": c.created_at,
                    "chief_complaint": c.chief_complaint,
                    "symptoms": c.symptoms,
                    "triage_status": c.triage_status.value,
                    "red_flags": c.red_flags,
                }
                for c in prior_cases[:5]
            ]
            highlight = await asyncio.wait_for(
                ai_service.generate_history_summary(visits_payload), timeout=3.5,
            )
        except Exception:
            logger.warning("History highlight generation timed out or failed; omitting")

        return {
            "visit_count": len(prior_cases),
            "recent_cases": recent,
            "carried_forward": case.carried_forward_from_previous_visit,
            "highlight": highlight,
        }
