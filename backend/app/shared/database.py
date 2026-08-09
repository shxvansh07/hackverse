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
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from app.shared.models import (
    Appointment,
    AuditEvent,
    ChatMessage,
    Doctor,
    LiveConsultation,
    PatientSession,
    PatientStatus,
    Prescription,
    PrescriptionStatus,
    ReviewStatus,
    RiskState,
    SafetySignal,
    TriageCase,
    PatientProfile,
)

logger = logging.getLogger(__name__)

_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def _data_dir() -> Path:
    override = os.getenv("DATA_DIR")
    path = Path(override).expanduser() if override else _DEFAULT_DATA_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _new_patient_id() -> str:
    """Same shape as PatientSession.patient_id's own default factory
    (models.py's private _new_id) — kept as a small local helper rather than
    importing a leading-underscore name across modules."""
    return f"PAT-{uuid.uuid4().hex[:6].upper()}"


def normalize_phone(phone: str) -> str:
    """Strip everything but digits, and a leading country code of '91'
    (India), so '+91 98765-43210' and '9876543210' key the same patient.
    Empty/whitespace-only input normalises to ''."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    return digits


class ClinicalStore:
    """Repository for sessions, cases, prescriptions and audit events."""

    def __init__(self, persist: bool = True, seed_demo: bool = True):
        self.sessions: Dict[str, PatientSession] = {}
        self.cases: Dict[str, TriageCase] = {}
        self.prescriptions: Dict[str, Prescription] = {}
        self.appointments: Dict[str, Appointment] = {}
        self.consultations: Dict[str, LiveConsultation] = {}
        self.patients: Dict[str, PatientProfile] = {}
        self.audit: List[AuditEvent] = []
        #: normalized phone -> patient_id. The only mechanism by which a
        #: returning patient's new visit is linked to their past ones.
        self.phone_index: Dict[str, str] = {}
        self.doctors: Dict[str, Doctor] = {}
        #: lowercased username -> doctor_id. Usernames are case-insensitive
        #: on login; this is the only place that decision is encoded.
        self.doctor_username_index: Dict[str, str] = {}

        self._persist = persist and os.getenv("PERSIST_STATE", "1") != "0"
        self._lock = threading.RLock()

        if self._persist and self._state_path().exists():
            self._restore()
        elif seed_demo and os.getenv("SEED_DEMO_DATA", "1") != "0":
            self._seed_demo()
            self._save()

        # Doctor accounts are configured identities, not demo content — seed
        # whenever the store starts genuinely empty of them (a fresh store,
        # or a restored snapshot written before doctor accounts existed),
        # regardless of whether SEED_DEMO_DATA produced the rest of the demo
        # data. Extra demo doctors (beyond the one from env vars) are only
        # added when demo seeding is actually on.
        if not self.doctors:
            self._seed_doctors(
                include_demo_extras=seed_demo and os.getenv("SEED_DEMO_DATA", "1") != "0"
            )

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
            "consultations": {k: v.model_dump() for k, v in self.consultations.items()},
            "phone_index": dict(self.phone_index),
            "patients": {k: v.model_dump() for k, v in self.patients.items()},
            "doctors": {k: v.model_dump() for k, v in self.doctors.items()},
            "doctor_username_index": dict(self.doctor_username_index),
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
            self.consultations = {
                k: LiveConsultation.model_validate(v)
                for k, v in payload.get("consultations", {}).items()
            }
            self.phone_index = dict(payload.get("phone_index", {}))
            self.patients = {
                k: PatientProfile.model_validate(v)
                for k, v in payload.get("patients", {}).items()
            }
            self.doctors = {
                k: Doctor.model_validate(v) for k, v in payload.get("doctors", {}).items()
            }
            self.doctor_username_index = dict(payload.get("doctor_username_index", {}))
            logger.info(
                "Restored %d sessions, %d cases, %d prescriptions, %d appointments, %d consultations, %d patients, %d doctors",
                len(self.sessions), len(self.cases), len(self.prescriptions),
                len(self.appointments), len(self.consultations), len(self.patients), len(self.doctors)
            )
        except Exception as exc:  # noqa: BLE001 - a bad snapshot must not block boot
            logger.error("State snapshot incompatible, starting empty: %s", exc)
            self.sessions, self.cases, self.prescriptions = {}, {}, {}
            self.appointments, self.consultations, self.patients = {}, {}, {}
            self.phone_index = {}
            self.doctors, self.doctor_username_index = {}, {}

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

    def save_consultation(self, consultation: LiveConsultation) -> LiveConsultation:
        with self._lock:
            self.consultations[consultation.consultation_id] = consultation
            self._save()
        return consultation

    def save_patient(self, patient: PatientProfile) -> PatientProfile:
        with self._lock:
            self.patients[patient.patient_id] = patient
            self._save()
        return patient

    def save_doctor(self, doctor: Doctor) -> Doctor:
        with self._lock:
            self.doctors[doctor.doctor_id] = doctor
            self.doctor_username_index[doctor.username.strip().lower()] = doctor.doctor_id
            self._save()
        return doctor

    def get_doctor(self, doctor_id: str) -> Optional[Doctor]:
        return self.doctors.get(doctor_id)

    def get_doctor_by_username(self, username: str) -> Optional[Doctor]:
        doctor_id = self.doctor_username_index.get((username or "").strip().lower())
        return self.doctors.get(doctor_id) if doctor_id else None

    # -------------------------------------------------------------- queries

    def get_session(self, session_id: str) -> Optional[PatientSession]:
        return self.sessions.get(session_id)

    def get_patient(self, patient_id: str) -> Optional[PatientProfile]:
        return self.patients.get(patient_id)

    def is_known_patient_id(self, patient_id: str) -> bool:
        """Whether patient_id was actually issued by this store — either a
        registered profile or a phone-linked guest identity — rather than an
        arbitrary client-supplied string. See TriageService.create_session,
        the only caller: a session-creation request's patient_id is
        client-controlled (the frontend reads it back from localStorage),
        and nothing upstream of this check has ever verified it was
        legitimately issued rather than forged or stale."""
        if not patient_id:
            return False
        if patient_id in self.patients:
            return True
        return patient_id in self.phone_index.values()

    def get_case(self, case_id: str) -> Optional[TriageCase]:
        return self.cases.get(case_id)

    def get_case_by_session(self, session_id: str) -> Optional[TriageCase]:
        for case in self.cases.values():
            if case.session_id == session_id:
                return case
        return None

    def get_or_create_patient_id(self, phone: str) -> str:
        """Look up a returning patient by phone, or mint a new patient_id and
        record it. Blank/unnormalisable phone always gets a fresh id — no
        identity linking without a real phone number."""
        normalized = normalize_phone(phone)
        if not normalized:
            return _new_patient_id()
        with self._lock:
            existing = self.phone_index.get(normalized)
            if existing:
                return existing
            patient_id = _new_patient_id()
            self.phone_index[normalized] = patient_id
            self._save()
            return patient_id

    def resolve_profile_patient_id(self, phone: str) -> str:
        """The patient_id a *new* named-profile registration should use for
        the given phone — see patient_backend.router.create_patient_profile,
        the only caller.

        Three cases:
        - No phone, or phone never seen before: a fresh id (also links the
          phone to it, so a future phone-only guest visit finds this profile).
        - Phone already links to an id with no profile yet (prior guest
          visits): reuse that id — this *is* the merge, safe because there's
          no other real identity attached to it yet, and it makes the
          guest's case history the new profile's history for free.
        - Phone already links to an id that already has its own profile:
          deliberately do not merge two distinct registered identities (e.g.
          a shared family phone) — a fresh id, and this phone number's
          existing link is left untouched.
        """
        normalized = normalize_phone(phone)
        if not normalized:
            return _new_patient_id()
        with self._lock:
            existing = self.phone_index.get(normalized)
            if not existing:
                patient_id = _new_patient_id()
                self.phone_index[normalized] = patient_id
                self._save()
                return patient_id
            if self.patients.get(existing) is None:
                return existing
            return _new_patient_id()

    def get_cases_by_patient(
        self, patient_id: str, exclude_case_id: Optional[str] = None, handed_off_only: bool = True,
    ) -> List[TriageCase]:
        """One patient's past cases, newest first. Used to build the
        doctor-facing visit history and to carry forward known allergy/
        history facts into a new visit — see TriageService.create_session.

        handed_off_only=True (the default, and what both callers above use)
        excludes a case still mid-conversation — same principle as
        list_cases()'s doctor queue: an abandoned or in-progress chat is not
        a completed visit, and showing it as "history" would be misleading.
        """
        cases = [
            c for c in self.cases.values()
            if c.patient_id == patient_id
            and c.case_id != exclude_case_id
            and (c.handed_off or not handed_off_only)
        ]
        cases.sort(key=lambda c: c.created_at, reverse=True)
        return cases

    def get_prescription(self, prescription_id: str) -> Optional[Prescription]:
        return self.prescriptions.get(prescription_id)

    def get_prescription_for_case(self, case_id: str) -> Optional[Prescription]:
        case = self.get_case(case_id)
        if case and case.prescription_id:
            return self.prescriptions.get(case.prescription_id)
        return None

    def get_appointment(self, appointment_id: str) -> Optional[Appointment]:
        return self.appointments.get(appointment_id)

    def get_consultation(self, consultation_id: str) -> Optional[LiveConsultation]:
        return self.consultations.get(consultation_id)

    def get_consultation_for_case(self, case_id: str) -> Optional[LiveConsultation]:
        case = self.get_case(case_id)
        if case and case.consultation_id:
            return self.consultations.get(case.consultation_id)
        return None

    def check_integrity(self) -> List[str]:
        """Verify every cross-entity reference this store holds actually
        resolves, and resolves both ways where the relationship is meant to
        be bidirectional (a case pointing at a prescription that itself
        points back at a different case would be silent data corruption).

        Nothing here ever deletes entities, so a violation can only mean a
        bug wrote a bad id somewhere — this exists to catch that, not to
        handle routine deletion (there isn't any).
        """
        problems: List[str] = []

        for case in self.cases.values():
            if case.session_id not in self.sessions:
                problems.append(f"{case.case_id}: session_id {case.session_id} does not exist")

            if case.prescription_id:
                prescription = self.prescriptions.get(case.prescription_id)
                if prescription is None:
                    problems.append(
                        f"{case.case_id}: prescription_id {case.prescription_id} does not exist"
                    )
                elif prescription.case_id != case.case_id:
                    problems.append(
                        f"{case.case_id}: prescription {case.prescription_id} points back at "
                        f"{prescription.case_id} instead"
                    )

            if case.appointment_id:
                appointment = self.appointments.get(case.appointment_id)
                if appointment is None:
                    problems.append(
                        f"{case.case_id}: appointment_id {case.appointment_id} does not exist"
                    )
                elif appointment.case_id != case.case_id:
                    problems.append(
                        f"{case.case_id}: appointment {case.appointment_id} points back at "
                        f"{appointment.case_id} instead"
                    )

            if case.consultation_id:
                consultation = self.consultations.get(case.consultation_id)
                if consultation is None:
                    problems.append(
                        f"{case.case_id}: consultation_id {case.consultation_id} does not exist"
                    )
                elif consultation.case_id != case.case_id:
                    problems.append(
                        f"{case.case_id}: consultation {case.consultation_id} points back at "
                        f"{consultation.case_id} instead"
                    )

        for prescription in self.prescriptions.values():
            if prescription.case_id not in self.cases:
                problems.append(
                    f"prescription {prescription.prescription_id}: case_id "
                    f"{prescription.case_id} does not exist"
                )

        for consultation in self.consultations.values():
            if consultation.case_id not in self.cases:
                problems.append(
                    f"consultation {consultation.consultation_id}: case_id "
                    f"{consultation.case_id} does not exist"
                )

        for appointment in self.appointments.values():
            if appointment.case_id not in self.cases:
                problems.append(
                    f"appointment {appointment.appointment_id}: case_id "
                    f"{appointment.case_id} does not exist"
                )

        return problems

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
        """Test helper. Clears memory without touching the audit file.

        Doctor accounts are cleared and re-seeded (rather than left alone)
        so every test starts from the same known registry — tests that
        register their own doctor never leak into a later test, and
        auth_headers() can still always log into the baseline env-var
        account afterwards.
        """
        with self._lock:
            self.sessions.clear()
            self.cases.clear()
            self.prescriptions.clear()
            self.appointments.clear()
            self.consultations.clear()
            self.patients.clear()
            self.audit.clear()
            self.phone_index.clear()
            self.doctors.clear()
            self.doctor_username_index.clear()
            self._seed_doctors(include_demo_extras=False)

    # ----------------------------------------------------------- demo seeds

    def _seed_doctors(self, include_demo_extras: bool) -> None:
        """The env-var account (DOCTOR_USERNAME/DOCTOR_PASSWORD/DOCTOR_ID/
        DOCTOR_NAME) always exists — it's the operator's configured account,
        documented in .env, and every existing demo flow/credential set
        depends on it still working unchanged. include_demo_extras adds a
        couple more so a live demo shows multiple real doctor accounts
        without a live signup step.
        """
        from app.shared.auth import hash_password  # local import avoids a load-time cycle

        def _seed(doctor_id: str, username: str, password: str, name: str, qualification: str, registration_no: str) -> None:
            if username.strip().lower() in self.doctor_username_index:
                return
            password_hash, password_salt = hash_password(password)
            self.doctors[doctor_id] = Doctor(
                doctor_id=doctor_id, username=username, password_hash=password_hash,
                password_salt=password_salt, name=name, qualification=qualification,
                registration_no=registration_no,
            )
            self.doctor_username_index[username.strip().lower()] = doctor_id

        _seed(
            os.getenv("DOCTOR_ID", "DR-101"),
            os.getenv("DOCTOR_USERNAME", "doctor"),
            os.getenv("DOCTOR_PASSWORD", "doctorpassword123"),
            os.getenv("DOCTOR_NAME", "Dr. Sharma, MD"),
            "MBBS, MD (General Medicine)", "KMC/00000/2026",
        )
        if include_demo_extras:
            _seed("DR-102", "dr.mehta", "demopassword123", "Dr. Mehta, MD", "MBBS, MD (Pediatrics)", "KMC/00001/2026")
            _seed("DR-103", "dr.rao", "demopassword123", "Dr. Rao, MD", "MBBS, DM (Cardiology)", "KMC/00002/2026")
        self._save()

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
