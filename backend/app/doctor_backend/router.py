"""Doctor-facing endpoints: auth, real-time queue, case review, decisions.

Every route except login requires a bearer token, and the resolved doctor
identity is what lands in the audit log — not a client-supplied doctor_id.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect

from app.safety import guards
from app.services.case_service import CaseService
from app.shared.auth import authenticate, require_doctor, resolve_token
from app.shared.database import db
from app.shared.models import (
    DecisionType,
    DoctorDecisionRequest,
    DoctorDecisionResponse,
    DoctorLoginRequest,
    DoctorLoginResponse,
    Prescription,
    PrescriptionStatus,
    PrescriptionUpdateRequest,
    ReviewStatus,
    TriageCase,
)
from app.websocket.manager import WSEvent, case_queue_payload, ws_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["doctor"])


@router.post("/api/auth/doctor/login", response_model=DoctorLoginResponse)
def doctor_login(payload: DoctorLoginRequest):
    record = authenticate(payload.username, payload.password)
    if not record:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return DoctorLoginResponse(**record)


@router.websocket("/api/ws/doctor")
async def websocket_doctor_queue(websocket: WebSocket, token: Optional[str] = Query(default=None)):
    """Live queue feed.

    The token arrives as a query parameter because browsers cannot set headers
    on a WebSocket handshake. It is validated before the socket is accepted, so
    an unauthenticated client never joins the broadcast set.
    """
    if not token or not resolve_token(token):
        await websocket.close(code=4401)
        return

    await ws_manager.connect(websocket)
    try:
        await websocket.send_json(
            {
                "event": WSEvent.CONNECTED.value,
                "data": {"queue_size": len(db.list_cases())},
            }
        )
        while True:
            # Client pings only; all meaningful traffic is server to client.
            await websocket.receive_text()
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)
    except Exception:  # noqa: BLE001 - never let a socket fault kill the worker
        logger.exception("Doctor WebSocket error")
        await ws_manager.disconnect(websocket)


@router.get("/api/doctor/cases", response_model=List[TriageCase])
def get_doctor_cases(
    risk_filter: Optional[str] = None,
    status_filter: Optional[str] = None,
    doctor: Dict[str, str] = Depends(require_doctor),
):
    """Case queue, URGENT first."""
    return db.list_cases(risk_filter=risk_filter, status_filter=status_filter)


@router.get("/api/doctor/cases/{case_id}")
def get_doctor_case_detail(case_id: str, doctor: Dict[str, str] = Depends(require_doctor)):
    """Full case detail.

    Unlike the patient view this deliberately includes the AI draft even when
    it is unapproved — reviewing it is the doctor's job.
    """
    case = db.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    prescription = db.get_prescription_for_case(case_id)

    return {
        "case": case,
        "prescription_draft": prescription,
        "safety_signal": case.safety_signal,
        "grounding": case.grounding,
        "audit": db.audit_for_case(case_id),
        "draft_blocked": case.grounding.get("blocked", False) if case.grounding else False,
        "draft_block_reason": case.grounding.get("block_reason") if case.grounding else None,
    }


@router.post("/api/doctor/cases/{case_id}/decision", response_model=DoctorDecisionResponse)
async def post_doctor_decision(
    case_id: str,
    payload: DoctorDecisionRequest,
    doctor: Dict[str, str] = Depends(require_doctor),
):
    """Record APPROVE / MODIFY / REJECT / NEEDS_REVIEW.

    Only APPROVE and MODIFY can result in a final prescription.
    """
    case = db.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    if case.review_status == ReviewStatus.NEW:
        case.review_status = ReviewStatus.IN_REVIEW

    review_status, prescription, released, message = await CaseService.apply_decision(
        case=case,
        decision=payload.decision,
        doctor_id=doctor["doctor_id"],
        doctor_name=doctor["doctor_name"],
        notes=payload.notes,
        modified_medications=payload.modified_medications,
        modified_instructions=payload.modified_instructions,
    )

    return DoctorDecisionResponse(
        case_id=case.case_id,
        review_status=review_status,
        prescription=prescription,
        released_to_patient=released,
        message=message,
    )


@router.get("/api/doctor/prescriptions/{prescription_id}", response_model=Prescription)
def get_prescription_raw(
    prescription_id: str, doctor: Dict[str, str] = Depends(require_doctor)
):
    """Canonical record, untranslated. Doctor-only."""
    prescription = db.get_prescription(prescription_id)
    if not prescription:
        raise HTTPException(status_code=404, detail="Prescription not found")
    return prescription


@router.patch("/api/prescriptions/{prescription_id}", response_model=Prescription)
async def update_prescription(
    prescription_id: str,
    payload: PrescriptionUpdateRequest,
    doctor: Dict[str, str] = Depends(require_doctor),
):
    """Amend an already-finalised prescription.

    Doctor-only, and only on a record a doctor has already finalised — an
    unreviewed AI draft is amended through the decision endpoint, not here.
    Any edit invalidates cached translations so a patient cannot be served a
    stale rendering of superseded content.
    """
    prescription = db.get_prescription(prescription_id)
    if not prescription:
        raise HTTPException(status_code=404, detail="Prescription not found")

    if prescription.status not in (PrescriptionStatus.APPROVED, PrescriptionStatus.MODIFIED):
        raise HTTPException(
            status_code=409,
            detail=(
                "Only a doctor-finalised prescription can be amended. "
                "Use the case decision endpoint to act on a draft."
            ),
        )

    changed: List[str] = []
    if payload.medications is not None:
        if not payload.medications:
            raise HTTPException(
                status_code=400, detail="A prescription must contain at least one medication"
            )
        prescription.medications = payload.medications
        changed.append("medications")
    if payload.instructions is not None:
        prescription.instructions = payload.instructions
        changed.append("instructions")
    if payload.doctor_notes is not None:
        prescription.doctor_notes = payload.doctor_notes
        changed.append("doctor_notes")

    if not changed:
        raise HTTPException(status_code=400, detail="No changes supplied")

    prescription.status = PrescriptionStatus.MODIFIED
    prescription.is_ai_draft = False
    prescription.doctor_id = doctor["doctor_id"]
    prescription.doctor_name = doctor["doctor_name"]
    prescription.approved_at = prescription.approved_at or None
    prescription.translations = {}
    db.save_prescription(prescription)

    case = db.get_case(prescription.case_id)
    if case:
        case.review_status = ReviewStatus.MODIFIED
        db.save_case(case)
        await ws_manager.broadcast(WSEvent.CASE_DECIDED, case_queue_payload(case))

    db.record_audit(
        "PRESCRIPTION_AMENDED",
        case_id=prescription.case_id,
        prescription_id=prescription.prescription_id,
        actor=doctor["doctor_id"],
        detail=f"Amended fields: {', '.join(changed)}",
        metadata={"fields": changed},
    )

    return prescription


@router.get("/api/doctor/audit/{case_id}")
def get_case_audit(case_id: str, doctor: Dict[str, str] = Depends(require_doctor)):
    """Append-only decision trail for one case."""
    if not db.get_case(case_id):
        raise HTTPException(status_code=404, detail="Case not found")
    return {"case_id": case_id, "events": db.audit_for_case(case_id)}
