from fastapi import FastAPI, HTTPException, Query, Body, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

from app.models import (
    PatientSession, TriageCase, Prescription, DecisionType, PrescriptionStatus,
    CreateSessionRequest, TriageMessageRequest, TriageMessageResponse,
    DoctorDecisionRequest, DoctorLoginRequest, DoctorLoginResponse,
    BookAppointmentRequest, Appointment, AppointmentType, ReferralInfo, RiskState
)
from app.database import db, ws_manager
from app.triage_engine import TriageEngine
from app.rag_engine import RAGEngine
from app.translation import TranslationEngine

app = FastAPI(
    title="Clinical Assistant API",
    version="2.1.0",
    description="Multilingual Intake, AI Triage, Real-time Doctor Handoff, Referral & Offline Appointment Engine"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"status": "online", "version": "2.1.0"}

# -------------------------------------------------------------
# 1. Doctor Authentication
# -------------------------------------------------------------
@app.post("/api/auth/doctor/login", response_model=DoctorLoginResponse)
def doctor_login(payload: DoctorLoginRequest):
    if payload.username == "doctor" and payload.password == "doctorpassword123":
        return DoctorLoginResponse(
            token="doc_token_secret_892347923489",
            doctor_id="DR-101",
            doctor_name="Dr. Sharma, MD"
        )
    raise HTTPException(status_code=401, detail="Invalid doctor username or password")

# -------------------------------------------------------------
# 2. WebSockets for Real-Time Doctor Queue Updates
# -------------------------------------------------------------
@app.websocket("/api/ws/doctor")
async def websocket_doctor_queue(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)

# -------------------------------------------------------------
# 3. Patient Intake & Real-Time Handoff
# -------------------------------------------------------------
@app.post("/api/patient/session", response_model=PatientSession)
def create_patient_session(payload: CreateSessionRequest):
    sess = PatientSession(preferred_language=payload.preferred_language)
    db.sessions[sess.session_id] = sess
    case = TriageCase(session_id=sess.session_id, patient_id=sess.patient_id)
    db.cases[case.case_id] = case
    return sess

@app.post("/api/triage/message", response_model=TriageMessageResponse)
async def handle_triage_message(payload: TriageMessageRequest):
    sess = db.sessions.get(payload.session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    case = next((c for c in db.cases.values() if c.session_id == payload.session_id), None)
    if not case:
        case = TriageCase(session_id=sess.session_id, patient_id=sess.patient_id)
        db.cases[case.case_id] = case

    updated_case, ai_reply, is_complete, auto_apt = TriageEngine.process_message(
        current_case=case,
        patient_text=payload.message,
        lang=sess.preferred_language
    )

    db.cases[updated_case.case_id] = updated_case

    if (is_complete or updated_case.triage_status == RiskState.LOW_RISK) and not updated_case.prescription_draft_id:
        draft_rx = RAGEngine.generate_draft_prescription(
            case_id=updated_case.case_id,
            symptoms=updated_case.symptoms,
            summary_en=updated_case.summary_en
        )
        db.prescriptions[draft_rx.prescription_id] = draft_rx
        updated_case.prescription_draft_id = draft_rx.prescription_id
        sess.status = "WAITING_DOCTOR"

    await ws_manager.broadcast({
        "event": "NEW_CASE_UPDATE",
        "case_id": updated_case.case_id,
        "triage_status": updated_case.triage_status.value,
        "symptoms": updated_case.symptoms,
        "summary_en": updated_case.summary_en,
        "created_at": updated_case.created_at
    })

    return TriageMessageResponse(
        session_id=sess.session_id,
        ai_response=ai_reply,
        language=sess.preferred_language,
        is_complete=is_complete,
        triage_status=updated_case.triage_status,
        missing_information=updated_case.missing_information,
        case_id=updated_case.case_id,
        auto_booked_appointment=auto_apt
    )

@app.get("/api/triage/{session_id}")
def get_triage_state(session_id: str):
    sess = db.sessions.get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    case = next((c for c in db.cases.values() if c.session_id == session_id), None)
    rx = db.prescriptions.get(case.prescription_draft_id) if (case and case.prescription_draft_id) else None
    apt = db.appointments.get(case.appointment_id) if (case and case.appointment_id) else None

    return {
        "session": sess,
        "case": case,
        "prescription_draft": rx,
        "appointment": apt
    }

# -------------------------------------------------------------
# 4. Doctor Queue & Decision Endpoints (Includes Referral & Offline Appt)
# -------------------------------------------------------------
@app.get("/api/doctor/cases", response_model=List[TriageCase])
def get_doctor_cases(risk_filter: Optional[str] = None, status_filter: Optional[str] = None):
    cases_list = list(db.cases.values())
    if risk_filter:
        cases_list = [c for c in cases_list if c.triage_status.value == risk_filter]
    if status_filter:
        cases_list = [c for c in cases_list if c.review_status == status_filter]
    return sorted(cases_list, key=lambda x: x.created_at, reverse=True)

@app.get("/api/doctor/cases/{case_id}")
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

@app.post("/api/doctor/cases/{case_id}/decision")
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

# -------------------------------------------------------------
# 5. Prescriptions & Translation
# -------------------------------------------------------------
@app.get("/api/prescriptions/{prescription_id}")
def get_prescription(prescription_id: str, lang: str = Query("en")):
    rx = db.prescriptions.get(prescription_id)
    if not rx:
        raise HTTPException(status_code=404, detail="Prescription not found")

    return TranslationEngine.get_translated_prescription(rx, lang=lang)

# -------------------------------------------------------------
# 6. Appointment Booking
# -------------------------------------------------------------
@app.post("/api/appointments/book", response_model=Appointment)
def book_appointment(payload: BookAppointmentRequest):
    case = db.cases.get(payload.case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    slot = payload.slot_time if payload.slot_time else (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d 10:00 AM")
    location = payload.clinic_location if payload.clinic_location else "Main OPD Clinic, Room 102"

    apt = Appointment(
        case_id=case.case_id,
        patient_id=case.patient_id,
        type=AppointmentType.OPTIONAL_CONSULT if case.triage_status != RiskState.URGENT else AppointmentType.URGENT_EMERGENCY,
        slot_time=slot,
        clinic_location=location,
        notes="Patient requested consultation appointment"
    )
    db.appointments[apt.appointment_id] = apt
    case.appointment_id = apt.appointment_id
    return apt

@app.get("/api/appointments/{appointment_id}", response_model=Appointment)
def get_appointment(appointment_id: str):
    apt = db.appointments.get(appointment_id)
    if not apt:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return apt
