"""Patient-facing endpoints.

Nothing here reaches a prescription directly. Draft creation goes through
CaseService, and the only route that returns prescription content
(`GET /api/prescriptions/{id}`) is gated by guards.may_release_to_patient, so
a draft cannot reach a patient even if its id is known.

Appointment booking is the one exception to "nothing here decides anything":
booking is never a risk decision (that's app.safety.engine's job alone), it's
just recording that a patient confirmed a visit the case already recommended.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query

from app.safety import guards
from app.services.case_service import CaseService
from app.services.prescription_service import PrescriptionService
from app.services.triage_service import TriageService
from app.shared.database import db
from app.shared.languages import all_languages, is_supported, resolve
from app.shared.models import (
    Appointment,
    AppointmentType,
    AssessRequest,
    AssessResponse,
    BookAppointmentRequest,
    CreateCaseRequest,
    CreateSessionRequest,
    PatientSession,
    PatientStatus,
    PatientStatusResponse,
    PresentedPrescription,
    PrescriptionStatus,
    RiskState,
    TriageCase,
    TriageMessageRequest,
    TriageMessageResponse,
)

router = APIRouter(tags=["patient"])


@router.get("/api/languages")
def list_languages():
    """Language menu for the patient UI, including Web Speech API tags."""
    return {
        "languages": [
            {
                "code": lang.code,
                "english_name": lang.english_name,
                "native_name": lang.native_name,
                "speech_tag": lang.speech_tag,
                "mvp": lang.mvp,
            }
            for lang in all_languages()
        ]
    }


@router.post("/api/patient/session", response_model=PatientSession)
def create_patient_session(payload: CreateSessionRequest):
    if not is_supported(payload.preferred_language):
        raise HTTPException(status_code=400, detail="Unsupported language")
    session, _ = TriageService.create_session(
        preferred_language=payload.preferred_language,
        patient_name=payload.patient_name or "Patient",
    )
    return session


@router.post("/api/triage/message", response_model=TriageMessageResponse)
async def handle_triage_message(payload: TriageMessageRequest):
    session = db.get_session(payload.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    case = db.get_case_by_session(payload.session_id)
    if not case:
        raise HTTPException(status_code=404, detail="No case for this session")

    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    result = await TriageService.process_message(session, case, payload.message.strip())
    assessment = result["assessment"]

    # URGENT hands off immediately. Waiting for the patient to finish an
    # interview they should not be finishing is the wrong behaviour.
    if result["urgent"]:
        await CaseService.finalise_assessment(session, case)
        await CaseService.hand_off(session, case)

    return TriageMessageResponse(
        session_id=session.session_id,
        case_id=case.case_id,
        ai_response=result["reply"],
        language=session.preferred_language,
        patient_status=session.status,
        triage_status=assessment.risk_state,
        is_complete=result["is_complete"],
        missing_information=assessment.missing_information,
        red_flags=assessment.red_flags,
        urgent_guidance=result.get("urgent_guidance"),
        recommend_appointment=assessment.risk_state in (RiskState.URGENT, RiskState.UNCERTAIN),
        recommended_specialty=case.recommended_specialty,
        clinical_state=case,
    )


@router.post("/api/triage/assess", response_model=AssessResponse)
async def assess_session(payload: AssessRequest):
    """Run the final safety assessment and, only where permitted, draft."""
    session = db.get_session(payload.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    case = db.get_case_by_session(payload.session_id)
    if not case:
        raise HTTPException(status_code=404, detail="No case for this session")

    outcome = await CaseService.finalise_assessment(session, case)
    assessment = outcome["assessment"]

    return AssessResponse(
        session_id=session.session_id,
        case_id=case.case_id,
        triage_status=assessment.risk_state,
        safety_signal=case.safety_signal,
        summary_en=case.summary_en,
        draft_generated=outcome["draft_generated"],
        draft_blocked_reason=outcome["blocked_reason"],
        patient_status=session.status,
        recommend_appointment=assessment.risk_state in (RiskState.URGENT, RiskState.UNCERTAIN),
        recommended_specialty=case.recommended_specialty,
    )


@router.get("/api/triage/{session_id}")
def get_triage_state(session_id: str):
    """Current structured clinical state.

    Never includes prescription content: the patient view fetches that through
    the guarded prescription route once review is complete.
    """
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    case = db.get_case_by_session(session_id)
    return {
        "session": session,
        "case": case,
        "triage_status": case.triage_status.value if case else RiskState.UNCERTAIN.value,
        "missing_information": case.missing_information if case else [],
        "recommend_appointment": (
            case.triage_status in (RiskState.URGENT, RiskState.UNCERTAIN) if case else False
        ),
        "recommended_specialty": case.recommended_specialty if case else None,
        "appointment": (
            db.get_appointment(case.appointment_id)
            if case and case.appointment_id
            else None
        ),
    }


@router.post("/api/cases", response_model=TriageCase)
async def create_case(payload: CreateCaseRequest):
    """Hand the completed intake off to the doctor queue."""
    session = db.get_session(payload.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    case = db.get_case_by_session(payload.session_id)
    if not case:
        raise HTTPException(status_code=404, detail="No case for this session")

    if not case.summary_en or case.safety_signal is None:
        await CaseService.finalise_assessment(session, case)

    return await CaseService.hand_off(session, case)


@router.get("/api/patient/status/{session_id}", response_model=PatientStatusResponse)
def get_patient_status(session_id: str):
    """Polled by the waiting screen.

    Deliberately coarse. A patient learns that review is complete, never that
    a doctor rejected an AI suggestion.
    """
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    case = db.get_case_by_session(session_id)
    if not case:
        return PatientStatusResponse(
            session_id=session_id,
            patient_status=session.status,
            triage_status=RiskState.UNCERTAIN,
            message="Session in progress.",
        )

    prescription = db.get_prescription_for_case(case.case_id)
    release = guards.may_release_to_patient(prescription)
    rejected = (
        prescription is not None
        and prescription.status == PrescriptionStatus.REJECTED
    )

    if release.allowed:
        message = "Your doctor has completed the review. Your prescription is ready."
    elif rejected:
        message = (
            "Your doctor has completed the review and did not issue a prescription. "
            "Please follow up with your doctor for next steps."
        )
    elif case.triage_status == RiskState.URGENT:
        message = (
            "This case has been escalated for urgent medical attention. "
            "Please seek emergency care now."
        )
    else:
        message = "Your information has been sent for doctor review."

    return PatientStatusResponse(
        session_id=session_id,
        case_id=case.case_id,
        patient_status=session.status,
        triage_status=case.triage_status,
        review_complete=release.allowed or rejected,
        prescription_available=release.allowed,
        prescription_id=prescription.prescription_id if release.allowed else None,
        rejected=rejected,
        message=message,
        recommend_appointment=case.triage_status in (RiskState.URGENT, RiskState.UNCERTAIN),
        recommended_specialty=case.recommended_specialty,
        appointment=db.get_appointment(case.appointment_id) if case.appointment_id else None,
    )


@router.post("/api/appointments/book", response_model=Appointment)
def book_appointment(payload: BookAppointmentRequest):
    """Record a patient-confirmed booking.

    Never called automatically — app.services never books on a patient's
    behalf, even for URGENT. This is the explicit confirmation step.
    """
    case = db.get_case(payload.case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    # URGENT always resolves to an emergency slot regardless of whether a
    # specialty was also passed — case triage status takes precedence.
    if case.triage_status == RiskState.URGENT:
        apt_type = AppointmentType.URGENT_EMERGENCY
    elif payload.specialty:
        apt_type = AppointmentType.SPECIALIST_CONSULT
    else:
        apt_type = AppointmentType.OPTIONAL_CONSULT

    if payload.slot_time:
        slot = payload.slot_time
    elif apt_type == AppointmentType.URGENT_EMERGENCY:
        slot = (datetime.now(timezone.utc) + timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M")
    else:
        slot = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d 10:00 AM")

    location = payload.clinic_location or "Main OPD Clinic, Room 102"

    notes = "Patient requested consultation appointment"
    if apt_type == AppointmentType.URGENT_EMERGENCY:
        notes = (
            f"PATIENT-CONFIRMED EMERGENCY APPOINTMENT: Red Flags {', '.join(case.red_flags)}"
            if case.red_flags else "Patient-confirmed emergency appointment"
        )

    appointment = Appointment(
        case_id=case.case_id,
        patient_id=case.patient_id,
        type=apt_type,
        slot_time=slot,
        clinic_location=location,
        notes=notes,
        specialty=payload.specialty or case.recommended_specialty,
    )
    db.save_appointment(appointment)
    case.appointment_id = appointment.appointment_id
    db.save_case(case)
    db.record_audit(
        "APPOINTMENT_BOOKED", case_id=case.case_id, actor="patient",
        detail=f"{apt_type.value} booked for {slot}",
        metadata={"specialty": appointment.specialty},
    )
    return appointment


@router.get("/api/appointments/{appointment_id}", response_model=Appointment)
def get_appointment(appointment_id: str):
    appointment = db.get_appointment(appointment_id)
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return appointment


@router.get("/api/prescriptions/{prescription_id}", response_model=PresentedPrescription)
async def get_prescription(prescription_id: str, lang: str = Query("en")):
    """Return a prescription in the requested language.

    THE release gate. A draft, a rejected draft, or anything lacking doctor
    attribution returns 409 with the reason rather than any content.
    """
    prescription = db.get_prescription(prescription_id)
    if not prescription:
        raise HTTPException(status_code=404, detail="Prescription not found")

    release = guards.may_release_to_patient(prescription)
    if not release.allowed:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": release.reason,
                "message": release.details[0] if release.details else "Not available.",
            },
        )

    if not is_supported(lang):
        lang = "en"

    return await PrescriptionService.present(prescription, resolve(lang).code)
