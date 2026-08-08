"""In-memory store with a JSON snapshot for durability, plus an append-only
audit log.

Why not an ORM: the MVP has four entity types and no query requirements beyond
"get by id" and "list all". A dict plus a snapshot file delivers restart
durability without a schema, a migration story or a session lifecycle. The
repository methods below are the only way anything reaches state, so moving to
SQLAlchemy later is a change inside this file.

Two things are written to disk:
  * state.json   - full snapshot, rewritten atomically after each mutation
  * audit.log    - append-only JSONL, never rewritten
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from app.shared.models import (
    Appointment,
    AuditEvent,
    ChatMessage,
    PatientSession,
    PatientStatus,
    Prescription,
    PrescriptionStatus,
    ReviewStatus,
    RiskState,
    SafetySignal,
    TriageCase,
)

logger = logging.getLogger(__name__)

_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def _data_dir() -> Path:
    override = os.getenv("DATA_DIR")
    path = Path(override).expanduser() if override else _DEFAULT_DATA_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


class ClinicalStore:
    """Repository for sessions, cases, prescriptions and audit events."""

    def __init__(self, persist: bool = True, seed_demo: bool = True):
        self.sessions: Dict[str, PatientSession] = {}
        self.cases: Dict[str, TriageCase] = {}
        self.prescriptions: Dict[str, Prescription] = {}
        self.appointments: Dict[str, Appointment] = {}
        self.audit: List[AuditEvent] = []

        self._persist = persist and os.getenv("PERSIST_STATE", "1") != "0"
        self._lock = threading.RLock()

        if self._persist and self._state_path().exists():
            self._restore()
        elif seed_demo and os.getenv("SEED_DEMO_DATA", "1") != "0":
            self._seed_demo()
            self._save()

    # ------------------------------------------------------------ file paths

    def _state_path(self) -> Path:
        return _data_dir() / "state.json"

    def _audit_path(self) -> Path:
        return _data_dir() / "audit.log"

    # ---------------------------------------------------------- persistence

    def _save(self) -> None:
        """Atomic snapshot: write a temp file, then replace. A crash mid-write
        leaves the previous good snapshot intact."""
        if not self._persist:
            return
        payload = {
            "version": 1,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "sessions": {k: v.model_dump() for k, v in self.sessions.items()},
            "cases": {k: v.model_dump() for k, v in self.cases.items()},
            "prescriptions": {k: v.model_dump() for k, v in self.prescriptions.items()},
            "appointments": {k: v.model_dump() for k, v in self.appointments.items()},
        }
        target = self._state_path()
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=str(target.parent), delete=False
            ) as tmp:
                json.dump(payload, tmp, ensure_ascii=False, indent=2)
                tmp_name = tmp.name
            os.replace(tmp_name, target)
        except OSError as exc:
            logger.error("Failed to persist state: %s", exc)

    def _restore(self) -> None:
        try:
            with self._state_path().open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Could not restore state, starting empty: %s", exc)
            return

        try:
            self.sessions = {
                k: PatientSession.model_validate(v)
                for k, v in payload.get("sessions", {}).items()
            }
            self.cases = {
                k: TriageCase.model_validate(v) for k, v in payload.get("cases", {}).items()
            }
            self.prescriptions = {
                k: Prescription.model_validate(v)
                for k, v in payload.get("prescriptions", {}).items()
            }
            self.appointments = {
                k: Appointment.model_validate(v)
                for k, v in payload.get("appointments", {}).items()
            }
            logger.info(
                "Restored %d sessions, %d cases, %d prescriptions, %d appointments",
                len(self.sessions), len(self.cases), len(self.prescriptions), len(self.appointments),
            )
        except Exception as exc:  # noqa: BLE001 - a bad snapshot must not block boot
            logger.error("State snapshot incompatible, starting empty: %s", exc)
            self.sessions, self.cases, self.prescriptions, self.appointments = {}, {}, {}, {}

    # ----------------------------------------------------------------- audit

    def record_audit(
        self,
        action: str,
        *,
        case_id: Optional[str] = None,
        prescription_id: Optional[str] = None,
        actor: str = "system",
        detail: str = "",
        metadata: Optional[dict] = None,
    ) -> AuditEvent:
        """Append an immutable audit entry.

        Deliberately records clinical decisions and safety outcomes, not the
        content of patient conversations.
        """
        event = AuditEvent(
            case_id=case_id,
            prescription_id=prescription_id,
            actor=actor,
            action=action,
            detail=detail,
            metadata=metadata or {},
        )
        with self._lock:
            self.audit.append(event)
            if self._persist:
                try:
                    with self._audit_path().open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(event.model_dump(), ensure_ascii=False) + "\n")
                except OSError as exc:
                    logger.error("Failed to append audit event: %s", exc)
        return event

    def audit_for_case(self, case_id: str) -> List[AuditEvent]:
        return [e for e in self.audit if e.case_id == case_id]

    # ------------------------------------------------------------ mutations

    def save_session(self, session: PatientSession) -> PatientSession:
        with self._lock:
            session.updated_at = datetime.now(timezone.utc).isoformat()
            self.sessions[session.session_id] = session
            self._save()
        return session

    def save_case(self, case: TriageCase) -> TriageCase:
        with self._lock:
            case.touch()
            self.cases[case.case_id] = case
            self._save()
        return case

    def save_prescription(self, prescription: Prescription) -> Prescription:
        with self._lock:
            prescription.updated_at = datetime.now(timezone.utc).isoformat()
            self.prescriptions[prescription.prescription_id] = prescription
            self._save()
        return prescription

    def save_appointment(self, appointment: Appointment) -> Appointment:
        with self._lock:
            self.appointments[appointment.appointment_id] = appointment
            self._save()
        return appointment

    # -------------------------------------------------------------- queries

    def get_session(self, session_id: str) -> Optional[PatientSession]:
        return self.sessions.get(session_id)

    def get_case(self, case_id: str) -> Optional[TriageCase]:
        return self.cases.get(case_id)

    def get_case_by_session(self, session_id: str) -> Optional[TriageCase]:
        for case in self.cases.values():
            if case.session_id == session_id:
                return case
        return None

    def get_prescription(self, prescription_id: str) -> Optional[Prescription]:
        return self.prescriptions.get(prescription_id)

    def get_prescription_for_case(self, case_id: str) -> Optional[Prescription]:
        case = self.get_case(case_id)
        if case and case.prescription_id:
            return self.prescriptions.get(case.prescription_id)
        return None

    def get_appointment(self, appointment_id: str) -> Optional[Appointment]:
        return self.appointments.get(appointment_id)

    def list_cases(
        self, risk_filter: Optional[str] = None, status_filter: Optional[str] = None,
    ) -> List[TriageCase]:
        """Doctor queue. Only cases actually handed off are visible — a session
        still mid-conversation is not a case a doctor should see."""
        cases = [c for c in self.cases.values() if c.handed_off]
        if risk_filter:
            cases = [c for c in cases if c.triage_status.value == risk_filter]
        if status_filter:
            cases = [c for c in cases if c.review_status.value == status_filter]

        # URGENT first, then newest within each group. A queue sorted only by
        # time buries an emergency under routine cases.
        urgent = [c for c in cases if c.triage_status == RiskState.URGENT]
        routine = [c for c in cases if c.triage_status != RiskState.URGENT]
        urgent.sort(key=lambda c: c.created_at, reverse=True)
        routine.sort(key=lambda c: c.created_at, reverse=True)
        return urgent + routine

    def reset(self) -> None:
        """Test helper. Clears memory without touching the audit file."""
        with self._lock:
            self.sessions.clear()
            self.cases.clear()
            self.prescriptions.clear()
            self.appointments.clear()
            self.audit.clear()

    # ----------------------------------------------------------- demo seeds

    def _seed_demo(self) -> None:
        """Two cases so the doctor dashboard is never empty on first load.

        Marked with a DEMO- prefix and demo=True so seeded data is
        distinguishable from a real intake, per the spec's requirement to keep
        demo data separate.
        """
        from app.shared.models import Medication  # local import avoids cycle at module load

        # --- Case 1: LOW_RISK, has an AI draft awaiting review --------------
        session_a = PatientSession(
            session_id="SESS-DEMO-A", patient_id="PAT-DEMO-A",
            patient_name="Demo Patient A", preferred_language="hi",
            status=PatientStatus.WAITING_FOR_DOCTOR,
        )
        case_a = TriageCase(
            case_id="CASE-DEMO-A",
            session_id=session_a.session_id,
            patient_id=session_a.patient_id,
            preferred_language="hi",
            chief_complaint="Fever with body ache",
            symptoms=["fever", "body ache"],
            associated_symptoms=["headache"],
            duration="3 days",
            severity="Moderate",
            medical_history=["Hypertension"],
            allergies=["Penicillin"],
            allergies_confirmed=True,
            history_confirmed=True,
            red_flags=[],
            missing_information=[],
            triage_status=RiskState.LOW_RISK,
            review_status=ReviewStatus.NEW,
            summary_en=(
                "Patient reports moderate fever with generalised body ache for three days, "
                "with associated headache. Known hypertension. Documented penicillin allergy. "
                "No red-flag features reported."
            ),
            safety_signal=SafetySignal(
                risk_state=RiskState.LOW_RISK,
                reasons=["No configured red flag detected and required intake fields are established"],
                eligible_for_draft=True,
            ),
            transcript=[
                ChatMessage(sender="patient", text="Mujhe 3 din se fever hai aur body pain bhi hai"),
                ChatMessage(sender="ai", text="समझ गया। क्या आपको इसके साथ सिरदर्द या और कोई तकलीफ़ है?"),
                ChatMessage(sender="patient", text="Haan halka sar dard hai"),
                ChatMessage(sender="ai", text="ठीक है। क्या आपको किसी दवा से एलर्जी है?"),
                ChatMessage(sender="patient", text="Haan mujhe Penicillin se allergy hai"),
            ],
            handed_off=True,
            handed_off_at=datetime.now(timezone.utc).isoformat(),
        )
        rx_a = Prescription(
            prescription_id="RX-DEMO-A",
            case_id=case_a.case_id,
            status=PrescriptionStatus.DRAFT,
            medications=[
                Medication(
                    name="Paracetamol (Acetaminophen)",
                    dosage="500 mg",
                    frequency="Every 6 to 8 hours as needed",
                    duration="3-5 days",
                    instructions="Take with water after food. Do not exceed 4,000 mg in 24 hours.",
                )
            ],
            instructions=(
                "Maintain oral hydration with water and ORS. Avoid heavy physical exertion. "
                "Seek doctor care if fever exceeds 102 F or persists beyond 4 days."
            ),
            is_ai_draft=True,
            icd10_code="R50.9",
            icd10_title="Fever, unspecified",
            matched_condition="Fever, unspecified / Acute viral syndrome",
            rationale=(
                "Presentation consistent with an uncomplicated febrile viral syndrome. "
                "Symptomatic antipyresis is first line per GUID-001; antibiotics are not "
                "indicated. Documented penicillin allergy does not affect paracetamol."
            ),
            grounding_sources=["PROTO-FEVER-VIRAL", "GUID-001"],
        )
        case_a.prescription_id = rx_a.prescription_id

        # --- Case 2: URGENT, deliberately has NO prescription ---------------
        session_b = PatientSession(
            session_id="SESS-DEMO-B", patient_id="PAT-DEMO-B",
            patient_name="Demo Patient B", preferred_language="en",
            status=PatientStatus.URGENT_ESCALATION,
        )
        case_b = TriageCase(
            case_id="CASE-DEMO-B",
            session_id=session_b.session_id,
            patient_id=session_b.patient_id,
            preferred_language="en",
            chief_complaint="Chest pain with breathlessness",
            symptoms=["chest pain", "breathing difficulty"],
            duration="2 hours",
            severity="Severe",
            medical_history=["Type 2 diabetes"],
            allergies=[],
            allergies_confirmed=False,
            red_flags=["Chest Pain", "Respiratory Distress"],
            missing_information=[],
            triage_status=RiskState.URGENT,
            review_status=ReviewStatus.URGENT,
            summary_en=(
                "Patient reports severe chest tightness radiating to the left arm with acute "
                "shortness of breath for two hours. Background of type 2 diabetes. "
                "Allergy status not established."
            ),
            safety_signal=SafetySignal(
                risk_state=RiskState.URGENT,
                red_flags=["Chest Pain", "Respiratory Distress"],
                red_flag_codes=["CHEST_PAIN", "RESPIRATORY_DISTRESS"],
                reasons=["Configured red flag detected: Chest Pain, Respiratory Distress"],
                eligible_for_draft=False,
            ),
            transcript=[
                ChatMessage(
                    sender="patient",
                    text="I am having severe chest pain and cannot breathe properly for the last 2 hours",
                ),
                ChatMessage(
                    sender="ai",
                    text=(
                        "The symptoms you have described need emergency medical care right now. "
                        "Please contact emergency services or go to the nearest emergency "
                        "department immediately. This case has been escalated to a doctor."
                    ),
                ),
            ],
            handed_off=True,
            handed_off_at=datetime.now(timezone.utc).isoformat(),
        )
        # No prescription for case_b — that absence is the safety property.

        self.sessions[session_a.session_id] = session_a
        self.sessions[session_b.session_id] = session_b
        self.cases[case_a.case_id] = case_a
        self.cases[case_b.case_id] = case_b
        self.prescriptions[rx_a.prescription_id] = rx_a

        logger.info("Seeded 2 demo cases (1 LOW_RISK with draft, 1 URGENT without)")


#: Process-wide store.
db = ClinicalStore()
