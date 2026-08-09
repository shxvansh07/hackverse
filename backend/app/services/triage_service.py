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
_NEGATIVE_PATTERNS = [
    r"\bno\b", r"\bnope\b", r"\bnone\b", r"\bnothing\b", r"\bnothing else\b",
    r"\bthat'?s all\b", r"\bthats all\b", r"\bthat'?s it\b", r"\bthats it\b",
    r"\bthat will be it\b", r"\bthat is all\b", r"\bthat is it\b",
    r"\bno more\b", r"\bno other\b", r"\bno thank you\b", r"\bno thanks\b",
    r"\bthank you\b", r"\bthanks\b", r"\bok\b", r"\bokay\b", r"\ball good\b",
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
    def create_session(preferred_language: str, patient_name: str = "Patient") -> Tuple[PatientSession, TriageCase]:
        from app.shared.languages import resolve

        language = resolve(preferred_language)
        session = PatientSession(
            preferred_language=language.code,
            patient_name=patient_name or "Patient",
            status=PatientStatus.COLLECTING_INFORMATION,
        )
        case = TriageCase(
            session_id=session.session_id,
            patient_id=session.patient_id,
            preferred_language=language.code,
        )
        case.transcript.append(ChatMessage(sender="ai", text=fq.greeting(language.code)))

        db.save_session(session)
        db.save_case(case)
        db.record_audit(
            "SESSION_CREATED", case_id=case.case_id, actor="patient",
            detail=f"Session opened in {language.english_name}",
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

        if extracted.allergies:
            case.allergies_confirmed = True
        if extracted.medical_history:
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
        history = [{"sender": m.sender, "text": m.text} for m in case.transcript[:-1]]
        extracted = await ai_service.extract_clinical_info(patient_text, history)

        llm_hints: List[str] = []
        denies_more = False

        if extracted is not None:
            cls.merge_extraction(case, extracted)
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
        # Only a "no" to the closing question ends intake. Ending on ANY "no"
        # (e.g. "no allergies") would skip the rest of history-taking and
        # complete before all fixed-order questions have been asked — see
        # test_intake_completes_with_zero_llm_providers.
        awaiting_closing_question = awaiting_field == "anything_else"
        intake_complete = denies_more and awaiting_closing_question

        if intake_complete:
            reply = fq.handoff_message(lang)
            case.transcript.append(ChatMessage(sender="ai", text=reply))
            session.status = PatientStatus.ASSESSING
            case.allergies_confirmed = True
            case.history_confirmed = True
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
        )

        if not reply:
            reply = fq.next_question(lang, previously_asked)

        # Catch if generated reply indicates handoff/completion to avoid infinite question loops
        reply_lower = reply.lower()
        handoff_keywords = [
            "doctor know", "speak with them", "wrap up", "hand off", "handoff",
            "send this to your doctor", "let the doctor", "send your details",
            "logged all your information", "ready now", "ready to speak"
        ]
        if any(kw in reply_lower for kw in handoff_keywords):
            case.transcript.append(ChatMessage(sender="ai", text=reply))
            session.status = PatientStatus.ASSESSING
            case.allergies_confirmed = True
            case.history_confirmed = True
            db.save_case(case)
            db.save_session(session)
            return {
                "reply": reply, "is_complete": True,
                "assessment": assessment, "urgent": False,
            }

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
