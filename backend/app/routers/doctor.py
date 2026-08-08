"""Doctor-facing endpoints: authentication, real-time case queue (WebSocket),
case review, and clinical decisions (approve/modify/reject/refer/offline
appointment). Owned by the Doctor Backend part of the team.

Shares app.database.db / ws_manager with routers/patient.py — that's the same
in-memory store both portals read and write, not a duplication. Calls into
rag_engine.py to lazily generate a draft if a doctor opens a case that never
got one from the patient-side flow (e.g. URGENT cases that skip drafting).
"""

from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app.database import db, ws_manager
from app.models import (
    Appointment,
    AppointmentType,
    DecisionType,
    DoctorDecisionRequest,
    DoctorLoginRequest,
    DoctorLoginResponse,
    PrescriptionStatus,
    ReferralInfo,
    TriageCase,
)
from app.rag_engine import RAGEngine

router = APIRouter()


@router.post("/api/auth/doctor/login", response_model=DoctorLoginResponse)
def doctor_login(payload: DoctorLoginRequest):
    if payload.username == "doctor" and payload.password == "doctorpassword123":
        return DoctorLoginResponse(
            token="doc_token_secret_892347923489",
            doctor_id="DR-101",
            doctor_name="Dr. Sharma, MD"
        )
    raise HTTPException(status_code=401, detail="Invalid doctor username or password")


@router.websocket("/api/ws/doctor")
async def websocket_doctor_queue(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


@router.get("/api/doctor/cases", response_model=List[TriageCase])
def get_doctor_cases(risk_filter: Optional[str] = None, status_filter: Optional[str] = None):
    cases_list = list(db.cases.values())
    if risk_filter:
        cases_list = [c for c in cases_list if c.triage_status.value == risk_filter]
    if status_filter:
        cases_list = [c for c in cases_list if c.review_status == status_filter]
    return sorted(cases_list, key=lambda x: x.created_at, reverse=True)


@router.get("/api/doctor/cases/{case_id}")
def get_doctor_case_detail(case_id: str):
    case = db.cases.get(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    rx = db.prescriptions.get(case.prescription_draft_id) if case.prescription_draft_id else None
    apt = db.appointments.get(case.appointment_id) if case.appointment_id else None
    return {
        "case": case,
        "prescription_draft": rx,
        "appointment": apt,
        "referral": case.referral
    }


@router.post("/api/doctor/cases/{case_id}/decision")
async def post_doctor_decision(case_id: str, payload: DoctorDecisionRequest):
    case = db.cases.get(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    rx = db.prescriptions.get(case.prescription_draft_id) if case.prescription_draft_id else None
    if not rx and payload.decision in [DecisionType.APPROVE, DecisionType.MODIFY, DecisionType.REFERRAL, DecisionType.OFFLINE_APPOINTMENT]:
        rx = RAGEngine.generate_draft_prescription(case.case_id, case.symptoms, case.summary_en)
        db.prescriptions[rx.prescription_id] = rx
        case.prescription_draft_id = rx.prescription_id

    # 1. Standard Approval / Modification
    if payload.decision == DecisionType.APPROVE:
        case.review_status = "APPROVED"
        if rx:
            rx.status = PrescriptionStatus.APPROVED
            rx.doctor_id = payload.doctor_id
            rx.doctor_notes = payload.notes
            rx.approved_at = datetime.now().isoformat()
            rx.is_ai_draft = False

    elif payload.decision == DecisionType.MODIFY:
        case.review_status = "MODIFIED"
        if rx:
            rx.status = PrescriptionStatus.MODIFIED
            if payload.modified_medications:
                rx.medications = payload.modified_medications
            if payload.modified_instructions:
                rx.instructions = payload.modified_instructions
            rx.doctor_id = payload.doctor_id
            rx.doctor_notes = payload.notes
            rx.approved_at = datetime.now().isoformat()
            rx.is_ai_draft = False

    # 2. Specialist Referral Option
    elif payload.decision == DecisionType.REFERRAL:
        case.review_status = "REFERRED"
        ref_info = ReferralInfo(
            specialty=payload.referral_specialty or "Specialist Consultation",
            referral_notes=payload.referral_notes or "Referred for specialist evaluation.",
            doctor_name=payload.doctor_name
        )
        case.referral = ref_info
        if rx:
            rx.status = PrescriptionStatus.APPROVED
            rx.referral = ref_info
            rx.approved_at = datetime.now().isoformat()
            rx.is_ai_draft = False

    # 3. Offline / In-Person Appointment Option
    elif payload.decision == DecisionType.OFFLINE_APPOINTMENT:
        case.review_status = "OFFLINE_SCHEDULED"
        slot_time = payload.offline_appointment_time or (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d 11:00 AM")
        location = payload.offline_clinic_location or "Main Hospital OPD Clinic, Room 102"

        offline_apt = Appointment(
            case_id=case.case_id,
            patient_id=case.patient_id,
            doctor_id=payload.doctor_id,
            doctor_name=payload.doctor_name,
            type=AppointmentType.DOCTOR_SCHEDULED_OFFLINE,
            slot_time=slot_time,
            clinic_location=location,
            notes=payload.notes or "Doctor scheduled in-person physical consultation."
        )
        db.appointments[offline_apt.appointment_id] = offline_apt
        case.appointment_id = offline_apt.appointment_id

        if rx:
            rx.status = PrescriptionStatus.APPROVED
            rx.approved_at = datetime.now().isoformat()
            rx.is_ai_draft = False

    elif payload.decision == DecisionType.REJECT:
        case.review_status = "REJECTED"
        if rx:
            rx.status = PrescriptionStatus.REJECTED

    elif payload.decision == DecisionType.NEEDS_REVIEW:
        case.review_status = "NEEDS_REVIEW"
        if rx:
            rx.status = PrescriptionStatus.NEEDS_REVIEW

    db.cases[case.case_id] = case
    if rx:
        db.prescriptions[rx.prescription_id] = rx

    await ws_manager.broadcast({
        "event": "CASE_DECISION_UPDATED",
        "case_id": case.case_id,
        "review_status": case.review_status
    })

    return {
        "status": "success",
        "case_id": case.case_id,
        "review_status": case.review_status,
        "prescription": rx,
        "referral": case.referral,
        "appointment": db.appointments.get(case.appointment_id) if case.appointment_id else None
    }
