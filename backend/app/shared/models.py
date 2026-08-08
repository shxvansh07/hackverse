"""Canonical domain models.

Two rules govern everything here:

1. There is exactly ONE prescription record per case. The doctor-approved or
   doctor-modified version of that record is the source of truth. Translations
   are a presentation of it, stored alongside, never in place of it.
2. Clinical state is typed. Nothing writes a free-form model string into a
   field that a clinical decision depends on.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str, length: int = 8) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:length].upper()}"


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class RiskState(str, Enum):
    """The three primary triage states. Set only by the deterministic engine."""

    LOW_RISK = "LOW_RISK"
    UNCERTAIN = "UNCERTAIN"
    URGENT = "URGENT"


class DecisionType(str, Enum):
    APPROVE = "APPROVE"
    MODIFY = "MODIFY"
    REJECT = "REJECT"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class PrescriptionStatus(str, Enum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    MODIFIED = "MODIFIED"
    REJECTED = "REJECTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class ReviewStatus(str, Enum):
    """Doctor-side lifecycle of a case."""

    NEW = "NEW"
    IN_REVIEW = "IN_REVIEW"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    APPROVED = "APPROVED"
    MODIFIED = "MODIFIED"
    REJECTED = "REJECTED"
    URGENT = "URGENT"


class PatientStatus(str, Enum):
    """What the patient is shown. Deliberately coarser than the doctor view —
    a patient never sees 'REJECTED', they see that review is complete."""

    COLLECTING_INFORMATION = "COLLECTING_INFORMATION"
    ASSESSING = "ASSESSING"
    WAITING_FOR_DOCTOR = "WAITING_FOR_DOCTOR"
    URGENT_ESCALATION = "URGENT_ESCALATION"
    APPROVED = "APPROVED"
    REVIEW_COMPLETE = "REVIEW_COMPLETE"


# ---------------------------------------------------------------------------
# Clinical value objects
# ---------------------------------------------------------------------------

class Medication(BaseModel):
    name: str = Field(..., description="Generic or trade name. Never translated.")
    dosage: str = Field(..., description="e.g. 500 mg")
    frequency: str = Field(..., description="e.g. Every 6 to 8 hours as needed")
    duration: str = Field(..., description="e.g. 3-5 days")
    instructions: str = Field(default="", description="e.g. Take after food with water")


class ChatMessage(BaseModel):
    sender: str  # "patient" | "ai" | "system"
    text: str
    timestamp: str = Field(default_factory=_now)


class SafetySignal(BaseModel):
    """A snapshot of one deterministic safety evaluation.

    Persisted on the case so the doctor sees exactly why the system routed as
    it did, and so a decision is auditable after the fact.
    """

    risk_state: RiskState
    red_flags: List[str] = Field(default_factory=list)
    red_flag_codes: List[str] = Field(default_factory=list)
    missing_information: List[str] = Field(default_factory=list)
    reasons: List[str] = Field(default_factory=list)
    eligible_for_draft: bool = False
    evaluated_at: str = Field(default_factory=_now)


class AuditEvent(BaseModel):
    """Append-only record. Never mutated, never deleted."""

    event_id: str = Field(default_factory=lambda: _new_id("EVT", 10))
    case_id: Optional[str] = None
    prescription_id: Optional[str] = None
    actor: str = "system"          # "system" | "patient" | doctor_id
    action: str = ""               # e.g. "DOCTOR_DECISION", "DRAFT_BLOCKED"
    detail: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Aggregates
# ---------------------------------------------------------------------------

class Prescription(BaseModel):
    """The single canonical prescription record for a case."""

    prescription_id: str = Field(default_factory=lambda: _new_id("RX"))
    case_id: str
    status: PrescriptionStatus = PrescriptionStatus.DRAFT

    medications: List[Medication] = Field(default_factory=list)
    instructions: str = ""

    #: True until a doctor approves or modifies. The UI keys its
    #: "AI-GENERATED DRAFT" labelling off this flag.
    is_ai_draft: bool = True

    doctor_id: Optional[str] = None
    doctor_name: Optional[str] = None
    doctor_notes: Optional[str] = None
    approved_at: Optional[str] = None

    # RAG provenance — what the draft was grounded in.
    icd10_code: str = ""
    icd10_title: str = ""
    matched_condition: str = ""
    rationale: str = ""
    grounding_sources: List[str] = Field(default_factory=list)

    #: Cached language presentations of THIS record, keyed by language code.
    #: Populated on demand. Never a substitute for the fields above.
    translations: Dict[str, Any] = Field(default_factory=dict)

    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)

    @property
    def is_final(self) -> bool:
        return self.status in (PrescriptionStatus.APPROVED, PrescriptionStatus.MODIFIED)


class PatientSession(BaseModel):
    session_id: str = Field(default_factory=lambda: _new_id("SESS"))
    patient_id: str = Field(default_factory=lambda: _new_id("PAT", 6))
    patient_name: str = "Patient"
    preferred_language: str = "en"
    status: PatientStatus = PatientStatus.COLLECTING_INFORMATION
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class TriageCase(BaseModel):
    """Structured clinical state for one intake session."""

    case_id: str = Field(default_factory=lambda: _new_id("CASE"))
    session_id: str
    patient_id: str
    preferred_language: str = "en"

    # --- structured clinical fields -------------------------------------
    chief_complaint: str = ""
    symptoms: List[str] = Field(default_factory=list)
    associated_symptoms: List[str] = Field(default_factory=list)
    duration: str = ""
    severity: str = ""
    medical_history: List[str] = Field(default_factory=list)
    medications: List[str] = Field(default_factory=list)
    allergies: List[str] = Field(default_factory=list)
    age: str = ""

    #: Distinguishes "patient said none" from "never asked". Without these,
    #: an empty allergies list is ambiguous and the safety engine cannot tell
    #: whether intake is complete.
    allergies_confirmed: bool = False
    history_confirmed: bool = False

    # --- safety ----------------------------------------------------------
    red_flags: List[str] = Field(default_factory=list)
    missing_information: List[str] = Field(default_factory=list)
    triage_status: RiskState = RiskState.UNCERTAIN
    safety_signal: Optional[SafetySignal] = None

    # --- workflow --------------------------------------------------------
    review_status: ReviewStatus = ReviewStatus.NEW
    summary_en: str = ""
    transcript: List[ChatMessage] = Field(default_factory=list)
    prescription_id: Optional[str] = None
    grounding: Dict[str, Any] = Field(default_factory=dict)
    handed_off: bool = False
    handed_off_at: Optional[str] = None

    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)

    def touch(self) -> None:
        self.updated_at = _now()

    def known_facts(self) -> Dict[str, Any]:
        return {
            "symptoms": self.symptoms,
            "associated_symptoms": self.associated_symptoms,
            "duration": self.duration,
            "severity": self.severity,
            "medical_history": self.medical_history,
            "medications": self.medications,
            "allergies": self.allergies,
            "age": self.age,
        }

    def transcript_text(self) -> str:
        return " ".join(m.text for m in self.transcript if m.sender == "patient")


# ---------------------------------------------------------------------------
# API request / response contracts
# ---------------------------------------------------------------------------

class CreateSessionRequest(BaseModel):
    preferred_language: str = "en"
    patient_name: Optional[str] = "Patient"


class TriageMessageRequest(BaseModel):
    session_id: str
    message: str = Field(..., min_length=1, max_length=2000)


class TriageMessageResponse(BaseModel):
    session_id: str
    case_id: str
    ai_response: str
    language: str
    patient_status: PatientStatus
    triage_status: RiskState
    is_complete: bool
    missing_information: List[str] = Field(default_factory=list)
    red_flags: List[str] = Field(default_factory=list)
    #: Present only for URGENT cases. The patient sees escalation guidance
    #: instead of any prescription pathway.
    urgent_guidance: Optional[str] = None
    clinical_state: Optional[TriageCase] = None


class AssessRequest(BaseModel):
    session_id: str


class AssessResponse(BaseModel):
    session_id: str
    case_id: str
    triage_status: RiskState
    safety_signal: SafetySignal
    summary_en: str
    draft_generated: bool
    draft_blocked_reason: Optional[str] = None
    patient_status: PatientStatus


class CreateCaseRequest(BaseModel):
    """Hand a completed intake session off to the doctor queue."""

    session_id: str


class DoctorLoginRequest(BaseModel):
    username: str
    password: str


class DoctorLoginResponse(BaseModel):
    token: str
    doctor_id: str
    doctor_name: str
    expires_at: str


class DoctorDecisionRequest(BaseModel):
    decision: DecisionType
    notes: Optional[str] = None
    #: Required for MODIFY. The submitted list replaces the draft entirely and
    #: becomes the canonical prescription.
    modified_medications: Optional[List[Medication]] = None
    modified_instructions: Optional[str] = None


class DoctorDecisionResponse(BaseModel):
    case_id: str
    review_status: ReviewStatus
    prescription: Optional[Prescription] = None
    released_to_patient: bool
    message: str


class PrescriptionUpdateRequest(BaseModel):
    """PATCH body. Only a doctor may call this, and only on a finalised record."""

    medications: Optional[List[Medication]] = None
    instructions: Optional[str] = None
    doctor_notes: Optional[str] = None


class PresentedMedication(BaseModel):
    """A medication rendered for patient display in a chosen language.

    Clinically load-bearing fields are copied verbatim from the canonical
    record. Only `frequency_localised` and `instructions_localised` carry
    translated text, and both keep the original alongside.
    """

    name: str
    dosage: str
    duration: str
    frequency: str
    instructions: str
    frequency_localised: str = ""
    instructions_localised: str = ""


class PresentedPrescription(BaseModel):
    """What a patient is shown. Built only from a doctor-finalised record."""

    prescription_id: str
    case_id: str
    status: PrescriptionStatus
    language: str
    requested_language: str
    #: False when translation failed a guard and English was substituted.
    translation_applied: bool = True
    translation_notice: Optional[str] = None
    medications: List[PresentedMedication] = Field(default_factory=list)
    instructions: str = ""
    instructions_localised: str = ""
    doctor_name: Optional[str] = None
    doctor_notes: Optional[str] = None
    approved_at: Optional[str] = None
    icd10_code: str = ""
    is_ai_draft: bool = False


class PatientStatusResponse(BaseModel):
    session_id: str
    case_id: Optional[str] = None
    patient_status: PatientStatus
    triage_status: RiskState
    review_complete: bool = False
    prescription_available: bool = False
    prescription_id: Optional[str] = None
    rejected: bool = False
    message: str = ""
