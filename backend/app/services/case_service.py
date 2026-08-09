"""Case lifecycle: assessment, draft generation, handoff, doctor decisions.

Every state transition that could put medication in front of a patient passes
through a guard in app.safety.guards. There is no direct write path from a
router to a prescription's status.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.ai.service import ai_service
from app.rag.engine import rag_engine
from app.safety import guards, specialty
from app.safety.engine import SafetyAssessment
from app.services.record_service import RecordService
from app.services.triage_service import TriageService
from app.shared.database import db
from app.shared.models import (
    Appointment,
    AppointmentType,
    DecisionType,
    Medication,
    PatientSession,
    PatientStatus,
    Prescription,
    PrescriptionStatus,
    ReferralInfo,
    ReviewStatus,
    RiskState,
    TriageCase,
)
from app.websocket.manager import WSEvent, case_queue_payload, ws_manager

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CaseService:
    # ------------------------------------------------------------- assessment

    @classmethod
    async def finalise_assessment(
        cls, session: PatientSession, case: TriageCase,
    ) -> Dict[str, Any]:
        """Run the final safety assessment and, only if permitted, draft.

        Called by POST /api/triage/assess. Idempotent: re-running on an
        already-drafted case does not produce a second prescription.
        """
        assessment = TriageService.assess_case(case, latest_text="")

        if not case.summary_en:
            case.summary_en = await TriageService.build_summary(case)

        draft_generated = False
        blocked_reason: Optional[str] = None

        gate = guards.may_generate_draft(assessment)

        if not gate.allowed:
            blocked_reason = gate.reason
            db.record_audit(
                "DRAFT_BLOCKED", case_id=case.case_id, actor="system",
                detail=gate.reason, metadata={"details": gate.details},
            )
            if assessment.risk_state == RiskState.URGENT:
                case.review_status = ReviewStatus.URGENT
                session.status = PatientStatus.URGENT_ESCALATION
            else:
                case.review_status = ReviewStatus.NEEDS_REVIEW
                session.status = PatientStatus.WAITING_FOR_DOCTOR

            if assessment.risk_state in (RiskState.URGENT, RiskState.UNCERTAIN):
                case.recommended_specialty = specialty.recommend_specialty(assessment.red_flag_codes)

        elif case.prescription_id:
            draft_generated = True  # already drafted; nothing further to do
            session.status = PatientStatus.WAITING_FOR_DOCTOR

        else:
            prescription = await cls._generate_draft(case)
            if prescription is not None:
                draft_generated = True
                case.review_status = ReviewStatus.NEW
            else:
                blocked_reason = "ALLERGY_CONFLICT"
                case.review_status = ReviewStatus.NEEDS_REVIEW
            session.status = PatientStatus.WAITING_FOR_DOCTOR

        db.save_case(case)
        db.save_session(session)

        return {
            "assessment": assessment,
            "draft_generated": draft_generated,
            "blocked_reason": blocked_reason,
        }

    @classmethod
    async def _generate_draft(cls, case: TriageCase) -> Optional[Prescription]:
        """Build a RAG-grounded draft, then allergy-check it before storing.

        Returns None when the allergy guard blocks the draft — the case then
        goes to a doctor with no prescription attached, which is the correct
        outcome, not a failure.
        """
        history_context = RecordService.build_history_digest(
            case.patient_id, exclude_case_id=case.case_id,
        ) or ""

        prescription, grounding = rag_engine.build_draft(
            case_id=case.case_id,
            symptoms=case.symptoms,
            associated_symptoms=case.associated_symptoms,
            summary=case.summary_en,
            history_context=history_context,
        )

        allergy_check = guards.check_allergy_conflict(
            medications=prescription.medications,
            allergies=case.allergies,
            formulary_entries=grounding.get("matched_entries", []),
        )

        if not allergy_check.allowed:
            db.record_audit(
                "DRAFT_BLOCKED_ALLERGY", case_id=case.case_id, actor="system",
                detail="Drafted medication conflicts with a documented allergy",
                metadata={"conflicts": allergy_check.details},
            )
            case.grounding = {
                **grounding,
                "blocked": True,
                "block_reason": "ALLERGY_CONFLICT",
                "conflicts": allergy_check.details,
            }
            return None

        # Rationale is prose only; medications are already fixed by the
        # formulary and are not affected by whether this succeeds.
        rationale = await ai_service.generate_rationale(
            case_dict={
                "symptoms": case.symptoms,
                "duration": case.duration,
                "severity": case.severity,
                "medical_history": case.medical_history,
                "allergies": case.allergies,
                "prior_history": history_context or "None on record.",
            },
            medications=[m.model_dump() for m in prescription.medications],
            guidance=grounding.get("guidance", []),
        )
        if rationale and rationale.rationale:
            prescription.rationale = rationale.rationale
            if rationale.referenced_guidance_ids:
                prescription.grounding_sources = list(
                    dict.fromkeys(prescription.grounding_sources + rationale.referenced_guidance_ids)
                )

        db.save_prescription(prescription)
        case.prescription_id = prescription.prescription_id

        # Advisory decision-support only — deliberately computed after the
        # draft is final and never fed back into it. A similar case belongs
        # to a different patient, so unlike history_context it must never
        # influence this patient's own medication matching.
        similar_cases = RecordService.find_similar_cases(case)
        if similar_cases:
            grounding["similar_cases"] = [c.model_dump() for c in similar_cases]
        case.grounding = grounding

        db.record_audit(
            "DRAFT_GENERATED", case_id=case.case_id,
            prescription_id=prescription.prescription_id, actor="system",
            detail=f"AI draft grounded in {', '.join(prescription.grounding_sources) or 'no sources'}",
            metadata={"medications": [m.name for m in prescription.medications]},
        )
        return prescription

    # ---------------------------------------------------------------- handoff

    @classmethod
    async def hand_off(cls, session: PatientSession, case: TriageCase) -> TriageCase:
        """Publish the case to the doctor queue.

        The first point at which a doctor can see the case. Broadcasts over the
        WebSocket so the dashboard updates without a refresh.
        """
        if not case.handed_off:
            case.handed_off = True
            case.handed_off_at = _now()

        if case.triage_status == RiskState.URGENT:
            case.review_status = ReviewStatus.URGENT
            session.status = PatientStatus.URGENT_ESCALATION
        elif case.review_status == ReviewStatus.NEW and not case.prescription_id:
            case.review_status = ReviewStatus.NEEDS_REVIEW
            session.status = PatientStatus.WAITING_FOR_DOCTOR
        else:
            session.status = PatientStatus.WAITING_FOR_DOCTOR

        db.save_case(case)
        db.save_session(session)
        db.record_audit(
            "CASE_HANDED_OFF", case_id=case.case_id, actor="system",
            detail=f"Handed to doctor queue as {case.triage_status.value}",
        )

        payload = case_queue_payload(case)
        await ws_manager.broadcast(WSEvent.CASE_CREATED, payload)
        if case.triage_status == RiskState.URGENT:
            await ws_manager.broadcast(WSEvent.URGENT_ALERT, payload)

        return case

    # -------------------------------------------------------------- decisions

    @classmethod
    async def apply_decision(
        cls,
        case: TriageCase,
        decision: DecisionType,
        doctor_id: str,
        doctor_name: str,
        notes: Optional[str] = None,
        modified_medications: Optional[List[Medication]] = None,
        modified_instructions: Optional[str] = None,
        referral_specialty: Optional[str] = None,
        referral_notes: Optional[str] = None,
        offline_appointment_time: Optional[str] = None,
        offline_clinic_location: Optional[str] = None,
    ) -> Tuple[ReviewStatus, Optional[Prescription], bool, str]:
        """Record a doctor decision.

        Returns (review_status, prescription, released_to_patient, message).

        Only APPROVE and MODIFY can produce a final prescription. MODIFY on a
        case with no draft (URGENT, or allergy-blocked) creates the canonical
        record from the doctor's own entry — the doctor is always permitted to
        prescribe; the AI is not.
        """
        prescription = db.get_prescription_for_case(case.case_id)
        released = False

        if decision == DecisionType.APPROVE:
            if prescription is None:
                return (
                    case.review_status, None, False,
                    "There is no draft to approve. Use MODIFY to enter a prescription directly.",
                )
            if not prescription.medications:
                return (
                    case.review_status, prescription, False,
                    "The draft contains no medication. Use MODIFY to enter a prescription.",
                )
            prescription.status = PrescriptionStatus.APPROVED
            case.review_status = ReviewStatus.APPROVED
            message = "Prescription approved and released to the patient."
            released = True

        elif decision == DecisionType.MODIFY:
            if not modified_medications:
                return (
                    case.review_status, prescription, False,
                    "A modified prescription must include at least one medication.",
                )

            if prescription is None:
                prescription = Prescription(case_id=case.case_id)
                case.prescription_id = prescription.prescription_id

            # The doctor's submission replaces the draft wholesale and becomes
            # canonical. We do not merge — a partial merge could silently
            # retain a drafted drug the doctor meant to remove.
            prescription.medications = modified_medications
            if modified_instructions is not None:
                prescription.instructions = modified_instructions
            prescription.status = PrescriptionStatus.MODIFIED
            case.review_status = ReviewStatus.MODIFIED
            message = "Modified prescription saved and released to the patient."
            released = True

        elif decision == DecisionType.REJECT:
            if prescription is not None:
                prescription.status = PrescriptionStatus.REJECTED
                prescription.is_ai_draft = True  # never presentable as final
            case.review_status = ReviewStatus.REJECTED
            message = "Draft rejected. It will not be shown to the patient as a prescription."

        elif decision == DecisionType.REFERRAL:
            referral = ReferralInfo(
                specialty=referral_specialty or case.recommended_specialty or "Specialist Consultation",
                referral_notes=referral_notes or "",
                doctor_name=doctor_name,
            )
            case.referral = referral
            case.review_status = ReviewStatus.REFERRED
            message = f"Patient referred to {referral.specialty}."
            if prescription is not None:
                prescription.referral = referral
                prescription.status = PrescriptionStatus.APPROVED
                released = True

        elif decision == DecisionType.OFFLINE_APPOINTMENT:
            appointment = Appointment(
                case_id=case.case_id,
                patient_id=case.patient_id,
                doctor_id=doctor_id,
                doctor_name=doctor_name,
                type=AppointmentType.DOCTOR_SCHEDULED_OFFLINE,
                slot_time=offline_appointment_time or "",
                clinic_location=offline_clinic_location or "Main Hospital Clinic, Room 102",
                notes=notes or "Doctor-scheduled in-person appointment.",
                specialty=case.recommended_specialty,
            )
            db.save_appointment(appointment)
            case.appointment_id = appointment.appointment_id
            case.review_status = ReviewStatus.OFFLINE_SCHEDULED
            message = f"In-person appointment scheduled for {appointment.slot_time or 'a time to be confirmed'}."
            if prescription is not None:
                prescription.status = PrescriptionStatus.APPROVED
                released = True

        else:  # NEEDS_REVIEW
            if prescription is not None:
                prescription.status = PrescriptionStatus.NEEDS_REVIEW
            case.review_status = ReviewStatus.NEEDS_REVIEW
            message = "Case kept in review."

        if prescription is not None:
            prescription.doctor_id = doctor_id
            prescription.doctor_name = doctor_name
            prescription.doctor_notes = notes
            if decision in (
                DecisionType.APPROVE, DecisionType.MODIFY,
                DecisionType.REFERRAL, DecisionType.OFFLINE_APPOINTMENT,
            ):
                prescription.approved_at = _now()
                prescription.is_ai_draft = False
                # Cached translations belong to the superseded content.
                prescription.translations = {}
            db.save_prescription(prescription)

        # Patient-facing status stays coarse: the patient learns that review is
        # complete, not that a doctor rejected an AI suggestion.
        session = db.get_session(case.session_id)
        if session:
            if decision in (DecisionType.APPROVE, DecisionType.MODIFY):
                session.status = PatientStatus.APPROVED
            elif decision == DecisionType.REJECT:
                session.status = PatientStatus.REVIEW_COMPLETE
            elif decision in (DecisionType.REFERRAL, DecisionType.OFFLINE_APPOINTMENT):
                session.status = PatientStatus.APPROVED if released else PatientStatus.REVIEW_COMPLETE
            else:
                session.status = PatientStatus.WAITING_FOR_DOCTOR
            db.save_session(session)

        db.save_case(case)
        db.record_audit(
            "DOCTOR_DECISION",
            case_id=case.case_id,
            prescription_id=prescription.prescription_id if prescription else None,
            actor=doctor_id,
            detail=f"{decision.value} by {doctor_name}",
            metadata={
                "decision": decision.value,
                "review_status": case.review_status.value,
                "released_to_patient": released,
                "notes_present": bool(notes),
                "medication_count": len(prescription.medications) if prescription else 0,
            },
        )

        await ws_manager.broadcast(WSEvent.CASE_DECIDED, case_queue_payload(case))
        return case.review_status, prescription, released, message
