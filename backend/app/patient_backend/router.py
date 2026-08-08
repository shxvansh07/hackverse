"""Patient-facing endpoints.

Nothing here reaches a prescription directly. Draft creation goes through
CaseService, and the only route that returns prescription content
(`GET /api/prescriptions/{id}`) is gated by guards.may_release_to_patient, so
a draft cannot reach a patient even if its id is known.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.safety import guards
from app.services.case_service import CaseService
from app.services.prescription_service import PrescriptionService
from app.services.triage_service import TriageService
from app.shared.database import db
from app.shared.languages import all_languages, is_supported, resolve
from app.shared.models import (
    AssessRequest,
    AssessResponse,
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
    )


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
