"""Patient-facing endpoints: intake session, triage conversation, prescription
retrieval, and appointment booking. Owned by the Patient Backend part of the
team (conversation/triage/safety/RAG/translation engines this router calls
into live in ai_service.py, triage_engine.py, safety_engine.py, rag_engine.py,
translation.py — same ownership).

Shares app.database.db / ws_manager with routers/doctor.py — that's the same
in-memory store both portals read and write, not a duplication.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query

from app.shared.database import db, ws_manager
from app.shared.models import (
    Appointment,
    AppointmentType,
    BookAppointmentRequest,
    CreateSessionRequest,
    PatientSession,
    RiskState,
    SeverityLevel,
    TriageCase,
    TriageMessageRequest,
    TriageMessageResponse,
)
from app.patient_backend.rag_engine import RAGEngine
from app.patient_backend.translation import TranslationEngine
from app.patient_backend.triage_engine import TriageEngine

router = APIRouter()


@router.post("/api/patient/session", response_model=PatientSession)
def create_patient_session(payload: CreateSessionRequest):
    sess = PatientSession(preferred_language=payload.preferred_language)
    db.sessions[sess.session_id] = sess
    case = TriageCase(session_id=sess.session_id, patient_id=sess.patient_id)
    db.cases[case.case_id] = case
    return sess


@router.post("/api/triage/message", response_model=TriageMessageResponse)
async def handle_triage_message(payload: TriageMessageRequest):
    sess = db.sessions.get(payload.session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    case = next((c for c in db.cases.values() if c.session_id == payload.session_id), None)
    if not case:
        case = TriageCase(session_id=sess.session_id, patient_id=sess.patient_id)
        db.cases[case.case_id] = case

    updated_case, ai_reply, is_complete, auto_apt, recommend_appointment = TriageEngine.process_message(
        current_case=case,
        patient_text=payload.message,
        lang=sess.preferred_language
    )

    db.cases[updated_case.case_id] = updated_case

    # Severity, not just triage_status, decides whether a draft gets written:
    # MILD/MODERATE -> draft + send to doctor for review/approval (as before,
    # now also covering UNCERTAIN cases that resolve to MODERATE, which
    # previously never got a draft at all). SEVERE (red-flag URGENT, or a
    # self-reported "severe" caught in triage_engine's step 2) never drafts —
    # skip straight to appointment recommendation instead. (Deliberately NOT
    # triage_status in (LOW_RISK, URGENT) -- URGENT must never get a draft,
    # a case needing an emergency appointment has no business also getting a
    # home prescription.)
    if (
        is_complete
        and updated_case.severity_level in (SeverityLevel.MILD, SeverityLevel.MODERATE)
        and not updated_case.prescription_draft_id
    ):
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
        "severity_level": updated_case.severity_level.value,
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
        severity_level=updated_case.severity_level,
        missing_information=updated_case.missing_information,
        case_id=updated_case.case_id,
        auto_booked_appointment=auto_apt,
        recommend_appointment=recommend_appointment,
        recommended_specialty=updated_case.recommended_specialty
    )


@router.get("/api/triage/{session_id}")
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


@router.get("/api/prescriptions/{prescription_id}")
def get_prescription(prescription_id: str, lang: str = Query("en")):
    rx = db.prescriptions.get(prescription_id)
    if not rx:
        raise HTTPException(status_code=404, detail="Prescription not found")

    return TranslationEngine.get_translated_prescription(rx, lang=lang)


@router.post("/api/appointments/book", response_model=Appointment)
def book_appointment(payload: BookAppointmentRequest):
    case = db.cases.get(payload.case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    slot = payload.slot_time if payload.slot_time else (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d 10:00 AM")
    location = payload.clinic_location if payload.clinic_location else "Main OPD Clinic, Room 102"

    if payload.specialty:
        apt_type = AppointmentType.SPECIALIST_CONSULT
    elif case.triage_status == RiskState.URGENT:
        apt_type = AppointmentType.URGENT_EMERGENCY
    else:
        apt_type = AppointmentType.OPTIONAL_CONSULT

    apt = Appointment(
        case_id=case.case_id,
        patient_id=case.patient_id,
        type=apt_type,
        slot_time=slot,
        clinic_location=location,
        notes="Patient requested consultation appointment",
        specialty=payload.specialty
    )
    db.appointments[apt.appointment_id] = apt
    case.appointment_id = apt.appointment_id
    return apt


@router.get("/api/appointments/{appointment_id}", response_model=Appointment)
def get_appointment(appointment_id: str):
    apt = db.appointments.get(appointment_id)
    if not apt:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return apt
