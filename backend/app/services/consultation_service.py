"""Live, real-time-interpreted face-to-face consultation.

One shared device, used together by doctor and patient in the room. Each
utterance is captured, translated, and spoken back — always as an explicit,
already-decided routing outcome's follow-up, never a routing decision itself.

_auto_draft() deliberately does NOT gate drafting on the deterministic
risk_state the way the async chat-intake path does (see
case_service.CaseService.finalise_assessment). A case only reaches a live
consultation after a doctor has already examined the patient in person —
that examination is the supervision an async URGENT/UNCERTAIN gate exists to
require, so re-applying it here would mostly just re-detect whatever
originally sent the patient to this visit (it never leaves case.symptoms or
the intake transcript) and permanently block drafting for exactly the cases
this path is meant to serve. The safety assessment still runs and updates
the case's record (triage_status, red_flags, recommended_specialty), but its
result is informational here, not a gate. The real safety gate for this path
is the doctor's own Approve/Modify — nothing reaches the patient without it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List

from app.ai.service import ai_service
from app.safety import guards, specialty
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
        """Extract facts from the finished transcript and draft a prescription.

        Always attempts to draft — see the module docstring for why this
        does not gate on risk_state the way the async intake path does. The
        assessment still runs so the case's record (triage_status, red_flags,
        recommended_specialty) reflects reality; only the allergy-conflict
        guard inside _generate_draft can still block the draft itself.
        """
        extracted = await ai_service.extract_from_consultation(exchange_lines)
        if extracted is not None:
            TriageService.merge_extraction(case, extracted)

        if case.prescription_id is not None:
            return

        # Rebuild the summary from the now-merged facts — it's part of
        # build_draft's retrieval query and the rationale prompt, so without
        # this the draft would still be matched against the pre-visit
        # picture even though everything discussed in the consultation was
        # just folded into case.symptoms/associated_symptoms/etc above.
        case.summary_en = await TriageService.build_summary(case)

        hints = extracted.possible_red_flags if extracted is not None else ()
        assessment = TriageService.assess_case(case, latest_text="", llm_hints=hints)
        if assessment.risk_state in (RiskState.URGENT, RiskState.UNCERTAIN):
            case.recommended_specialty = specialty.recommend_specialty(assessment.red_flag_codes)

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
