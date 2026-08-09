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

from pydantic import BaseModel, Field, field_validator


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
    REFERRAL = "REFERRAL"
    OFFLINE_APPOINTMENT = "OFFLINE_APPOINTMENT"


class PrescriptionStatus(str, Enum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    MODIFIED = "MODIFIED"
    REJECTED = "REJECTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class AppointmentType(str, Enum):
    """Not a routing decision — an appointment is booked only after a risk
    state has already been decided by app.safety.engine. URGENT_EMERGENCY is
    never created automatically; the patient always confirms first."""

    OPTIONAL_CONSULT = "OPTIONAL_CONSULT"
    URGENT_EMERGENCY = "URGENT_EMERGENCY"
    DOCTOR_SCHEDULED_OFFLINE = "DOCTOR_SCHEDULED_OFFLINE"
    SPECIALIST_CONSULT = "SPECIALIST_CONSULT"


class ReviewStatus(str, Enum):
    """Doctor-side lifecycle of a case."""

    NEW = "NEW"
    IN_REVIEW = "IN_REVIEW"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    APPROVED = "APPROVED"
    MODIFIED = "MODIFIED"
    REJECTED = "REJECTED"
    URGENT = "URGENT"
    REFERRED = "REFERRED"
    OFFLINE_SCHEDULED = "OFFLINE_SCHEDULED"


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
    # min_length=1 alone is not enough — Pydantic checks raw string length,
    # so "   " would pass. A doctor-submitted medication with a blank dose or
    # duration reaching a patient is a real defect, not a cosmetic one, so
    # this is enforced here rather than trusted to the frontend form.
    name: str = Field(..., min_length=1, description="Generic or trade name. Never translated.")
    dosage: str = Field(..., min_length=1, description="e.g. 500 mg")
    frequency: str = Field(..., min_length=1, description="e.g. Every 6 to 8 hours as needed")
    duration: str = Field(..., min_length=1, description="e.g. 3-5 days")
    instructions: str = Field(default="", description="e.g. Take after food with water")

    @field_validator("name", "dosage", "frequency", "duration")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class ReferralInfo(BaseModel):
    """A doctor's manual referral to a specialist. Distinct from
    TriageCase.recommended_specialty, which is the deterministic engine's
    suggestion — this is the doctor's own decision."""

    specialty: str
    referral_notes: str = ""
    doctor_name: str = ""
    created_at: str = Field(default_factory=_now)


class Appointment(BaseModel):
    """A booked visit. Always the result of an explicit confirmation — patient
    clicking "confirm", or a doctor decision — never created implicitly by the
    safety engine or triage service."""

    appointment_id: str = Field(default_factory=lambda: _new_id("APT"))
    case_id: str
    patient_id: str
    doctor_id: str = "DR-101"
    doctor_name: str = "Dr. Sharma, MD"
    type: AppointmentType
    slot_time: str
    clinic_location: str = "Main Hospital Clinic, Room 102"
    status: str = "CONFIRMED"
    notes: str = ""
    specialty: Optional[str] = None
    created_at: str = Field(default_factory=_now)


class ChatMessage(BaseModel):
    sender: str  # "patient" | "ai" | "system"
    text: str
    timestamp: str = Field(default_factory=_now)


class CaseNote(BaseModel):
    """One doctor-to-doctor note on a case — e.g. a senior doctor flagging
    context for whoever reviews it next. Distinct from Prescription.doctor_notes
    (a single field tied to one prescription decision, overwritten each time):
    this is an append-only thread at the case level, visible regardless of
    which prescription is current."""

    note_id: str = Field(default_factory=lambda: _new_id("NOTE", 6))
    doctor_id: str
    doctor_name: str
    text: str = Field(..., min_length=1, max_length=2000)
    created_at: str = Field(default_factory=_now)


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


class ConsultationTurn(BaseModel):
    """One utterance in a live face-to-face consultation, captured both as
    said and as translated. Both sides are kept so the transcript is legible
    to a reviewer in either language."""

    speaker: str  # "doctor" | "patient"
    original_text: str
    original_lang: str
    translated_text: str
    translated_lang: str
    timestamp: str = Field(default_factory=_now)


class LiveConsultation(BaseModel):
    """An in-person visit, real-time-interpreted on one shared device.

    Booking (Appointment) and holding this consultation are deliberately
    separate concerns — a consultation can be started against a case with any
    kind of appointment, or resumed, without re-touching booking state.
    """

    consultation_id: str = Field(default_factory=lambda: _new_id("CONSULT"))
    case_id: str
    doctor_id: str = "DR-101"
    doctor_name: str = "Dr. Sharma, MD"
    patient_lang: str = "en"
    status: str = "IN_PROGRESS"  # "IN_PROGRESS" | "COMPLETED"
    turns: List[ConsultationTurn] = Field(default_factory=list)
    #: English-only clinical note. Never translated or shown to the patient —
    #: it lives in the case record for the doctor (see CaseRecord below).
    report_en: Optional[str] = None
    started_at: str = Field(default_factory=_now)
    ended_at: Optional[str] = None


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

    #: Set only by a REFERRAL decision. A referral can coexist with a released
    #: prescription — the doctor may release symptomatic treatment while also
    #: sending the patient to a specialist.
    referral: Optional[ReferralInfo] = None

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


class PatientProfile(BaseModel):
    patient_id: str = Field(default_factory=lambda: _new_id("PAT", 6))
    name: str = ""
    age: str = ""
    #: Optional, and separate from the identity-linking phone number stored
    #: in database.phone_index — this is display/contact info on the
    #: profile itself. See patient_backend.router.create_patient_profile for
    #: how a matching phone_index entry gets linked or reused at signup.
    phone: str = ""
    created_at: str = Field(default_factory=_now)


class CaseRecord(BaseModel):
    """The permanent record of one patient encounter: what was said and what
    was prescribed. Kept on the doctor's side for audit/liability, never sent
    to the patient — the patient only ever receives the released prescription
    itself, the same way the mild-case path already works.

    Built from data that is already persisted elsewhere (case.transcript, a
    LiveConsultation's turns, the case's Prescription); this model is just a
    stable, self-contained shape for presenting it. It takes only a case to
    build (see RecordService.build_case_record), so a future per-patient
    history feature can assemble a list of these — one per past case — with
    no redesign.
    """

    case_id: str
    patient_id: str
    chief_complaint: str
    created_at: str
    finalized_at: Optional[str] = None
    review_status: ReviewStatus
    initial_conversation: List[ChatMessage] = Field(default_factory=list)
    consultation_transcript: List[ConsultationTurn] = Field(default_factory=list)
    encounter_note: Optional[str] = None
    prescription: Optional[Prescription] = None


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
    patient_name: str = "Patient"
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

    #: True when allergies/medical_history above were pre-populated from a
    #: prior visit by the same (phone-linked) patient rather than stated in
    #: this visit's own conversation. Doctor-facing signal only — never used
    #: by the safety engine, which treats *_confirmed as settled either way.
    carried_forward_from_previous_visit: bool = False

    #: True exactly when the AI's last turn was the closing question ("is
    #: there anything else..."). This is explicit state the code sets
    #: itself — never inferred by re-matching the AI's last message text —
    #: because an LLM-authored question never matches the fixed question
    #: bank verbatim, which made intake completion nearly unreachable
    #: through a working LLM. See TriageService.process_message.
    awaiting_closing_question: bool = False

    #: One deterministic (not LLM-generated) sentence summarising this
    #: patient's prior visits, set once at case creation from
    #: database.get_cases_by_patient. Passed into the conversation prompt as
    #: informational context only — same "hint, never fact" framing as
    #: possible_red_flags — so the AI can ask more relevant follow-ups
    #: without the safety engine ever depending on it. Empty for a
    #: first-time or non-phone-linked patient.
    prior_visit_note: str = ""

    # --- safety ----------------------------------------------------------
    red_flags: List[str] = Field(default_factory=list)
    missing_information: List[str] = Field(default_factory=list)
    triage_status: RiskState = RiskState.UNCERTAIN
    safety_signal: Optional[SafetySignal] = None

    #: Deterministic specialty suggestion — see app.safety.specialty. Set
    #: whenever triage_status is URGENT or UNCERTAIN. Never drives a routing
    #: decision by itself; it only informs what the patient/doctor may book.
    recommended_specialty: Optional[str] = None

    # --- workflow --------------------------------------------------------
    review_status: ReviewStatus = ReviewStatus.NEW
    summary_en: str = ""
    transcript: List[ChatMessage] = Field(default_factory=list)
    prescription_id: Optional[str] = None
    appointment_id: Optional[str] = None
    referral: Optional[ReferralInfo] = None
    consultation_id: Optional[str] = None
    grounding: Dict[str, Any] = Field(default_factory=dict)
    handed_off: bool = False
    handed_off_at: Optional[str] = None

    #: Doctor-to-doctor notes on this case — e.g. a senior doctor flagging
    #: something for whoever reviews it next. Append-only, never edited or
    #: cleared by a prescription decision (unlike Prescription.doctor_notes).
    case_notes: List[CaseNote] = Field(default_factory=list)

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
    #: Optional. When provided, links this visit to any past visits by the
    #: same phone number (see database.get_or_create_patient_id) so the
    #: doctor sees history and settled allergy/history facts carry forward.
    #: Left blank, behaviour is unchanged from before this field existed.
    phone: Optional[str] = None
    #: Optional. A client-supplied patient_id (e.g. from a prior
    #: POST /api/patient/profile registration) takes precedence over phone
    #: lookup when both are present — an explicit identity the client
    #: already has is a stronger signal than a fresh phone lookup.
    patient_id: Optional[str] = None


class CreateProfileRequest(BaseModel):
    name: str = Field(..., min_length=1)
    age: str = Field(..., min_length=1)
    phone: Optional[str] = None


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
    #: True for URGENT or UNCERTAIN — tells the patient UI to offer booking
    #: instead of waiting for a prescription. Never implies anything is
    #: already booked; see Appointment / BookAppointmentRequest.
    recommend_appointment: bool = False
    recommended_specialty: Optional[str] = None
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
    recommend_appointment: bool = False
    recommended_specialty: Optional[str] = None


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


class Doctor(BaseModel):
    """A real doctor account. password_hash/password_salt only — the plain
    password is never stored, never logged, never round-tripped through the
    API (see app.shared.auth.hash_password/verify_password)."""

    doctor_id: str = Field(default_factory=lambda: _new_id("DR", 4))
    username: str
    password_hash: str
    password_salt: str
    name: str
    qualification: str = ""
    registration_no: str = ""
    created_at: str = Field(default_factory=_now)


class DoctorRegisterRequest(BaseModel):
    username: str = Field(..., min_length=3)
    password: str = Field(..., min_length=8)
    name: str = Field(..., min_length=1)
    qualification: str = ""
    registration_no: str = ""


class BookAppointmentRequest(BaseModel):
    case_id: str
    slot_time: Optional[str] = None
    clinic_location: Optional[str] = None
    specialty: Optional[str] = None


class StartConsultationRequest(BaseModel):
    case_id: str


class ConsultationTurnRequest(BaseModel):
    speaker: str  # "doctor" | "patient"
    text: str = Field(..., min_length=1, max_length=2000)
    source_lang: str
    target_lang: str


class DoctorDecisionRequest(BaseModel):
    decision: DecisionType
    notes: Optional[str] = None
    #: Required for MODIFY. The submitted list replaces the draft entirely and
    #: becomes the canonical prescription.
    modified_medications: Optional[List[Medication]] = None
    modified_instructions: Optional[str] = None
    #: Required for REFERRAL.
    referral_specialty: Optional[str] = None
    referral_notes: Optional[str] = None
    #: Required for OFFLINE_APPOINTMENT.
    offline_appointment_time: Optional[str] = None
    offline_clinic_location: Optional[str] = None


class DoctorDecisionResponse(BaseModel):
    case_id: str
    review_status: ReviewStatus
    prescription: Optional[Prescription] = None
    released_to_patient: bool
    message: str


class AddCaseNoteRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


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
    recommend_appointment: bool = False
    recommended_specialty: Optional[str] = None
    appointment: Optional[Appointment] = None


class SymptomTrendPoint(BaseModel):
    date: str
    count: int


class SymptomTrend(BaseModel):
    """One symptom's aggregate case counts over the lookback window.

    Anonymized by construction — see PublicHealthService.compute_trends,
    which is the only place a SymptomTrend is built. No case_id, patient_id,
    or free text ever appears here; a symptom this thin is dropped by the
    MIN_BUCKET_COUNT floor before it ever reaches this model.
    """

    symptom: str
    total_count: int
    daily_counts: List[SymptomTrendPoint] = Field(default_factory=list)
    baseline_avg: float = 0.0
    recent_count: int = 0
    ratio: Optional[float] = None
    flagged: bool = False
    #: True when there isn't enough baseline history yet to compute a
    #: trustworthy ratio (e.g. a same-day demo). ratio/flagged are then
    #: meaningless placeholders, never a guess dressed up as a signal.
    insufficient_history: bool = True


class PublicHealthTrendsResponse(BaseModel):
    generated_at: str
    window_days: int
    min_bucket_count: int
    trends: List[SymptomTrend] = Field(default_factory=list)
