"""Live, real-time-interpreted face-to-face consultation.

One shared device, used together by doctor and patient in the room. Each
utterance is captured, translated, and spoken back — always as an explicit,
already-decided routing outcome's follow-up, never a routing decision itself.

_auto_draft() is the one exception: at the end of a consultation it re-runs
the same deterministic safety assessment and drafting gate the async
chat-intake path uses (see case_service.CaseService.finalise_assessment).
A doctor being physically present is supervision of the conversation, not a
substitute for that gate — a patient describing something URGENT mid-visit
must not silently receive a drafted medication list just because they were
seen in person rather than triaged async. The extraction step that runs
first can surface exactly that kind of new information: it reads the live
dialogue itself, not just what intake already knew, so the risk picture can
genuinely change here.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List

from app.ai.service import ai_service
from app.safety import guards, specialty
from app.services import deterministic_extraction
from app.services.case_service import CaseService
from app.services.triage_service import TriageService
from app.shared.database import db
from app.shared.models import (
    ConsultationTurn,
    LiveConsultation,
    ReviewStatus,
    RiskState,
    TriageCase,
)
from app.websocket.manager import WSEvent, case_queue_payload, ws_manager

logger = logging.getLogger(__name__)


class ConsultationService:
    @classmethod
    def start(cls, case: TriageCase) -> LiveConsultation:
        """Create a consultation for this case, or resume the existing one.

        Idempotent by design: opening the doctor's consultation screen twice
        for the same case (a refresh, a re-click) must not fork the record.
        """
        if case.consultation_id:
            existing = db.get_consultation(case.consultation_id)
            if existing:
                return existing

        consultation = LiveConsultation(
            case_id=case.case_id,
            patient_lang=case.preferred_language or "en",
        )
        db.save_consultation(consultation)
        case.consultation_id = consultation.consultation_id
        db.save_case(case)
        db.record_audit(
            "CONSULTATION_STARTED", case_id=case.case_id, actor="doctor",
            detail=f"Live consultation {consultation.consultation_id} started",
        )
        return consultation

    @classmethod
    async def add_turn(
        cls,
        consultation: LiveConsultation,
        speaker: str,
        text: str,
        source_lang: str,
        target_lang: str,
    ) -> ConsultationTurn:
        """Translate one utterance and append it to the running transcript.

        A translation that fails the same numeric/length guard used for
        prescriptions falls back to the original text — the patient/doctor
        sees untranslated speech rather than an unverified rendering.
        """
        translated = await ai_service.translate_dialogue(text, source_lang, target_lang)
        translated_text = text
        if translated:
            check = guards.verify_translation(text, translated)
            if check.allowed:
                translated_text = translated
            else:
                logger.warning(
                    "Consultation turn translation rejected by guard (%s)", check.reason,
                )

        turn = ConsultationTurn(
            speaker=speaker,
            original_text=text,
            original_lang=source_lang,
            translated_text=translated_text,
            translated_lang=target_lang,
        )
        consultation.turns.append(turn)
        db.save_consultation(consultation)
        return turn

    @classmethod
    async def end(cls, consultation: LiveConsultation, case: TriageCase) -> LiveConsultation:
        """Generate the English encounter note for the case record, close out.

        The note is never translated or sent to the patient — only the
        eventual doctor-approved prescription reaches them (see _auto_draft).
        """
        exchange_lines: List[str] = []
        for turn in consultation.turns:
            # English rendering of both sides: a doctor turn's original_text
            # already is English; a patient turn's translated_text is.
            english_text = turn.original_text if turn.speaker == "doctor" else turn.translated_text
            label = "Doctor" if turn.speaker == "doctor" else "Patient"
            exchange_lines.append(f"{label}: {english_text}")

        report_en = await ai_service.generate_visit_report(case.summary_en, exchange_lines)
        consultation.report_en = report_en or "No report could be generated for this consultation."
        consultation.status = "COMPLETED"
        consultation.ended_at = datetime.now(timezone.utc).isoformat()
        db.save_consultation(consultation)

        db.record_audit(
            "CONSULTATION_ENDED", case_id=case.case_id, actor="doctor",
            detail=f"Consultation {consultation.consultation_id} closed with {len(consultation.turns)} turns",
        )

        await cls._auto_draft(case, exchange_lines)

        return consultation

    @classmethod
    async def _auto_draft(cls, case: TriageCase, exchange_lines: List[str]) -> None:
        """Extract facts from the finished transcript, re-assess risk, and
        draft a prescription only if that assessment still permits one.
        """
        extracted = await ai_service.extract_from_consultation(exchange_lines)
        if extracted is not None:
            TriageService.merge_extraction(case, extracted)

        # A live consultation has no equivalent of chat intake's fixed
        # question order — whether duration/allergies ever get established
        # depends entirely on the LLM extraction above succeeding, and
        # unlike chat intake this path previously had no fallback: with no
        # provider configured, or the call failing, nothing from the
        # consultation was ever captured. A genuinely safe case would then
        # compute UNCERTAIN and be blocked purely for missing data, not
        # because it was risky. Back it with the same deterministic keyword
        # extractor chat intake already falls back to — merge_extraction is
        # additive, so this only ever fills gaps the LLM extraction (if any)
        # left, never overwrites what it already established.
        patient_lines = " ".join(
            line[len("Patient: "):] for line in exchange_lines if line.startswith("Patient: ")
        )
        if patient_lines:
            fallback_extracted = deterministic_extraction.extract(patient_lines)
            if fallback_extracted is not None:
                TriageService.merge_extraction(case, fallback_extracted)

        if case.prescription_id is not None:
            return

        # A case pulled straight into a consultation (no prior chat intake)
        # already has a summary_en — a generic "unspecified symptoms"
        # placeholder written by finalise_assessment back when /api/cases
        # first handed it off, before anything was known. That placeholder
        # text is part of the RAG retrieval query (rag_engine.build_query),
        # and its boilerplate phrasing ("Allergy status not established")
        # can outscore the real symptoms just extracted above, silently
        # dragging a confident match below the drafting threshold. Rebuild
        # it now that the consultation has actually established facts.
        case.summary_en = await TriageService.build_summary(case)

        hints = extracted.possible_red_flags if extracted is not None else ()
        assessment = TriageService.assess_case(case, latest_text="", llm_hints=hints)
        gate = guards.may_generate_draft(assessment)

        if not gate.allowed:
            case.review_status = (
                ReviewStatus.URGENT if assessment.risk_state == RiskState.URGENT
                else ReviewStatus.NEEDS_REVIEW
            )
            if assessment.risk_state in (RiskState.URGENT, RiskState.UNCERTAIN):
                case.recommended_specialty = specialty.recommend_specialty(assessment.red_flag_codes)
            db.save_case(case)
            db.record_audit(
                "CONSULTATION_DRAFT_BLOCKED", case_id=case.case_id, actor="system",
                detail=f"Draft blocked after consultation: {gate.reason}",
                metadata={"details": gate.details},
            )
            await ws_manager.broadcast(WSEvent.CASE_CREATED, case_queue_payload(case))
            return

        prescription = await CaseService._generate_draft(case)
        case.review_status = ReviewStatus.NEW if prescription is not None else ReviewStatus.NEEDS_REVIEW
        db.save_case(case)

        db.record_audit(
            "CONSULTATION_DRAFT_GENERATED" if prescription is not None else "CONSULTATION_DRAFT_BLOCKED",
            case_id=case.case_id, actor="system",
            detail="Draft prescription generated from consultation transcript"
            if prescription is not None else "Draft blocked (allergy conflict) after consultation",
        )

        await ws_manager.broadcast(WSEvent.CASE_CREATED, case_queue_payload(case))
