from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime
import uuid

class RiskState(str, Enum):
    LOW_RISK = "LOW_RISK"
    UNCERTAIN = "UNCERTAIN"
    URGENT = "URGENT"

class SeverityLevel(str, Enum):
    """Deterministic severity tier — see safety_engine.classify_severity().
    Distinct from RiskState: RiskState is the red-flag/escalation routing
    decision, SeverityLevel is what drives prescription-vs-appointment
    branching (MILD/MODERATE -> draft + doctor review, SEVERE -> skip
    drafting, recommend an in-person appointment directly)."""
    MILD = "MILD"
    MODERATE = "MODERATE"
    SEVERE = "SEVERE"

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
    OPTIONAL_CONSULT = "OPTIONAL_CONSULT"
    URGENT_EMERGENCY = "URGENT_EMERGENCY"
    DOCTOR_SCHEDULED_OFFLINE = "DOCTOR_SCHEDULED_OFFLINE"

class Medication(BaseModel):
    name: str = Field(..., description="Medication generic or trade name")
    dosage: str = Field(..., description="E.g., 500mg, 10ml, 1 tablet")
    frequency: str = Field(..., description="E.g., Twice daily, Every 8 hours")
    duration: str = Field(..., description="E.g., 5 days, 1 week")
    instructions: str = Field(..., description="E.g., Take after meals with water")

class ReferralInfo(BaseModel):
    specialty: str
    referral_notes: str
    doctor_name: str = "Dr. Sharma, MD"
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())

class Prescription(BaseModel):
    prescription_id: str = Field(default_factory=lambda: f"RX-{uuid.uuid4().hex[:8]}")
    case_id: str
    status: PrescriptionStatus = PrescriptionStatus.DRAFT
    medications: List[Medication] = []
    instructions: str = ""
    doctor_id: Optional[str] = None
    doctor_notes: Optional[str] = None
    approved_at: Optional[str] = None
    is_ai_draft: bool = True
    referral: Optional[ReferralInfo] = None
    translations: Dict[str, Any] = {}

class PatientSession(BaseModel):
    session_id: str = Field(default_factory=lambda: f"SESS-{uuid.uuid4().hex[:8]}")
    patient_id: str = Field(default_factory=lambda: f"PAT-{uuid.uuid4().hex[:6]}")
    preferred_language: str = "en"
    status: str = "ACTIVE"
    awaiting_final_confirmation: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())

class ChatMessage(BaseModel):
    sender: str
    text: str
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())

class Appointment(BaseModel):
    appointment_id: str = Field(default_factory=lambda: f"APT-{uuid.uuid4().hex[:8]}")
    case_id: str
    patient_id: str
    doctor_id: str = "DR-101"
    doctor_name: str = "Dr. Sharma, MD"
    type: AppointmentType
    slot_time: str
    clinic_location: str = "Main Hospital Clinic, Room 102"
    status: str = "CONFIRMED"
    notes: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())

class TriageCase(BaseModel):
    case_id: str = Field(default_factory=lambda: f"CASE-{uuid.uuid4().hex[:8]}")
    session_id: str
    patient_id: str
    symptoms: List[str] = []
    associated_symptoms: List[str] = []
    duration: str = ""
    severity: str = "Mild"
    severity_level: SeverityLevel = SeverityLevel.MILD
    medical_history: List[str] = []
    medications: List[str] = []
    allergies: List[str] = []
    red_flags: List[str] = []
    missing_information: List[str] = []
    triage_status: RiskState = RiskState.LOW_RISK
    review_status: str = "PENDING"
    summary_en: str = ""
    transcript: List[ChatMessage] = []
    prescription_draft_id: Optional[str] = None
    appointment_id: Optional[str] = None
    referral: Optional[ReferralInfo] = None
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())

# Auth API Requests & Responses
class DoctorLoginRequest(BaseModel):
    username: str
    password: str

class DoctorLoginResponse(BaseModel):
    token: str
    doctor_id: str
    doctor_name: str

class BookAppointmentRequest(BaseModel):
    case_id: str
    slot_time: Optional[str] = None
    clinic_location: Optional[str] = None

class CreateSessionRequest(BaseModel):
    preferred_language: str = "en"
    patient_name: Optional[str] = "Patient"

class TriageMessageRequest(BaseModel):
    session_id: str
    message: str

class TriageMessageResponse(BaseModel):
    session_id: str
    ai_response: str
    language: str
    is_complete: bool
    triage_status: RiskState
    severity_level: SeverityLevel
    missing_information: List[str]
    case_id: Optional[str] = None
    auto_booked_appointment: Optional[Appointment] = None
    recommend_appointment: bool = False

class DoctorDecisionRequest(BaseModel):
    decision: DecisionType
    doctor_id: str = "DR-101"
    doctor_name: str = "Dr. Sharma, MD"
    notes: Optional[str] = None
    modified_medications: Optional[List[Medication]] = None
    modified_instructions: Optional[str] = None
    # Referral & Offline Appointment fields
    referral_specialty: Optional[str] = None
    referral_notes: Optional[str] = None
    offline_appointment_time: Optional[str] = None
    offline_clinic_location: Optional[str] = "Main OPD Clinic, Room 104"
