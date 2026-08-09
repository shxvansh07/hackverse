"""Assembles the permanent case record — for the doctor's audit trail today,
and reusable as-is for a future per-patient history feature.

Nothing here writes anything new. The initial conversation, the consultation
transcript and the final prescription are already persisted by their own
services; this module only reads them back into one shape.
"""

from __future__ import annotations

from typing import List, Optional

from app.rag.vector_store import VectorStore
from app.shared.auth import get_doctor_profile
from app.shared.database import db
from app.shared.models import CaseRecord, PrescriptionStatus, SimilarCase, TriageCase

#: Picked empirically against this demo's case volume/style, the same way
#: rag/engine.py's MIN_DRAFT_CONFIDENCE was — would need re-tuning if either
#: changes materially.
_SIMILAR_CASE_MIN_SCORE = 0.1


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

    @staticmethod
    def find_similar_cases(case: TriageCase, limit: int = 3) -> List[SimilarCase]:
        """De-identified peer cases with a similar presentation, across ALL
        patients — decision support, not continuity of care (that's
        build_history_digest, scoped to one patient). Only ever clinical
        content and doctor attribution; patient_id/name/phone are never read
        here, so there is nothing identifying to leak by construction.

        Purely advisory: the caller must never fold this into this patient's
        own drafting query — see CaseService._generate_draft.
        """
        query_text = " ".join([*case.symptoms, *case.associated_symptoms, case.chief_complaint])
        if not query_text.strip():
            return []

        store = VectorStore()
        for other in db.cases.values():
            if other.patient_id == case.patient_id or other.case_id == case.case_id:
                continue
            prescription = db.get_prescription_for_case(other.case_id)
            if not prescription or not prescription.is_final:
                continue
            text = " ".join([*other.symptoms, *other.associated_symptoms, other.chief_complaint])
            if not text.strip():
                continue
            store.add(other.case_id, text, {"case": other, "prescription": prescription})

        hits = store.search(query_text, top_k=limit, min_score=_SIMILAR_CASE_MIN_SCORE)

        results: List[SimilarCase] = []
        for hit in hits:
            other = hit.metadata["case"]
            prescription = hit.metadata["prescription"]
            doctor_profile = get_doctor_profile(prescription.doctor_id) if prescription.doctor_id else None
            results.append(
                SimilarCase(
                    similarity_score=round(hit.score, 3),
                    chief_complaint=other.chief_complaint or (other.symptoms[0] if other.symptoms else ""),
                    diagnosis=prescription.matched_condition or prescription.icd10_title or "Unspecified",
                    medications=[m.name for m in prescription.medications],
                    doctor_name=prescription.doctor_name or "Unknown",
                    doctor_years_experience=doctor_profile["years_experience"] if doctor_profile else None,
                    was_modified_from_ai_draft=prescription.status == PrescriptionStatus.MODIFIED,
                    doctor_notes=prescription.doctor_notes,
                    approved_at=prescription.approved_at,
                )
            )
        return results
