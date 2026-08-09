"""Assembles the permanent case record — for the doctor's audit trail today,
and reusable as-is for a future per-patient history feature.

Nothing here writes anything new. The initial conversation, the consultation
transcript and the final prescription are already persisted by their own
services; this module only reads them back into one shape.
"""

from __future__ import annotations

from app.shared.database import db
from app.shared.models import CaseRecord, TriageCase


class RecordService:
    @staticmethod
    def build_case_record(case: TriageCase) -> CaseRecord:
        consultation = db.get_consultation(case.consultation_id) if case.consultation_id else None
        prescription = db.get_prescription_for_case(case.case_id)
        final_prescription = prescription if prescription and prescription.is_final else None

        return CaseRecord(
            case_id=case.case_id,
            patient_id=case.patient_id,
            chief_complaint=case.chief_complaint or (case.symptoms[0] if case.symptoms else ""),
            created_at=case.created_at,
            finalized_at=final_prescription.approved_at if final_prescription else None,
            review_status=case.review_status,
            initial_conversation=case.transcript,
            consultation_transcript=consultation.turns if consultation else [],
            encounter_note=consultation.report_en if consultation else None,
            prescription=final_prescription,
        )
