"""Assembles the permanent case record — for the doctor's audit trail today,
and reusable as-is for a future per-patient history feature.

Nothing here writes anything new. The initial conversation, the consultation
transcript and the final prescription are already persisted by their own
services; this module only reads them back into one shape.
"""

from __future__ import annotations

from typing import Optional

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

    @staticmethod
    def build_history_digest(patient_id: str, exclude_case_id: str) -> Optional[str]:
        """A compact, clinical-vocabulary digest of a returning patient's most
        recent prior visits — dates, conditions, drugs, nothing narrative.

        Feeds two different consumers (see CaseService._generate_draft): a
        TF-IDF retrieval query, where free prose would just dilute the match,
        and an LLM rationale prompt, where it lets the model note continuity
        with a prior visit. Never a source of medication — only the curated
        formulary/ML fallback inside rag_engine.build_draft can do that.
        """
        prior_cases = db.get_cases_by_patient(patient_id, exclude_case_id=exclude_case_id)
        if not prior_cases:
            return None

        lines = []
        for case in prior_cases[:3]:
            date = case.created_at.split("T")[0]
            complaint = case.chief_complaint or (case.symptoms[0] if case.symptoms else "unspecified complaint")

            prescription = db.get_prescription_for_case(case.case_id)
            if prescription and prescription.is_final:
                condition = prescription.matched_condition or prescription.icd10_title or "unspecified condition"
                meds = ", ".join(m.name for m in prescription.medications) or "no medication"
                outcome = f"diagnosed {condition}, prescribed {meds}"
            else:
                outcome = "no medication on record"

            lines.append(f"{date}: {complaint} — {outcome}.")

        return " ".join(lines)
