from typing import Dict, List, Optional
from datetime import datetime, timedelta
from fastapi import WebSocket
from app.models import (
    PatientSession, TriageCase, Prescription, Medication, RiskState, PrescriptionStatus, ChatMessage, Appointment, AppointmentType
)
#here is my change
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass

ws_manager = ConnectionManager()

class InMemoryDB:
    def __init__(self):
        self.sessions: Dict[str, PatientSession] = {}
        self.cases: Dict[str, TriageCase] = {}
        self.prescriptions: Dict[str, Prescription] = {}
        self.appointments: Dict[str, Appointment] = {}

        self._seed_demo_cases()

    def _seed_demo_cases(self):
        sess1 = PatientSession(session_id="SESS-DEMO-01", patient_id="PAT-1001", preferred_language="hi")
        case1 = TriageCase(
            case_id="CASE-DEMO-01",
            session_id=sess1.session_id,
            patient_id=sess1.patient_id,
            symptoms=["fever", "body ache"],
            duration="3 days",
            severity="Moderate",
            medical_history=["Hypertension"],
            allergies=["Penicillin"],
            red_flags=[],
            missing_information=[],
            triage_status=RiskState.LOW_RISK,
            review_status="PENDING",
            summary_en="Patient reports moderate fever and generalized body ache for 3 days. History of hypertension. Known allergy to Penicillin.",
            transcript=[
                ChatMessage(sender="patient", text="Mujhe 3 din se fever hai aur body pain bhi hai"),
                ChatMessage(sender="ai", text="यह समस्या आपको कितने दिनों या घंटों से हो रही है?"),
                ChatMessage(sender="patient", text="3 din se hai aur mild headache bhi hai"),
                ChatMessage(sender="ai", text="क्या आपको किसी दवा से allergy है?"),
                ChatMessage(sender="patient", text="Ha mujhe Penicillin se allergy hai")
            ]
        )
        rx1 = Prescription(
            prescription_id="RX-DEMO-01",
            case_id=case1.case_id,
            status=PrescriptionStatus.DRAFT,
            medications=[
                Medication(
                    name="Paracetamol (Acetaminophen)",
                    dosage="500 mg",
                    frequency="Every 6 to 8 hours as needed",
                    duration="3-5 days",
                    instructions="Take with water after food. Do not exceed 4,000 mg per day."
                )
            ],
            instructions="Drink warm water, rest, and follow up if fever persists beyond 3 days.",
            is_ai_draft=True
        )
        case1.prescription_draft_id = rx1.prescription_id

        sess2 = PatientSession(session_id="SESS-DEMO-02", patient_id="PAT-1002", preferred_language="en")
        case2 = TriageCase(
            case_id="CASE-DEMO-02",
            session_id=sess2.session_id,
            patient_id=sess2.patient_id,
            symptoms=["chest pain", "breathing difficulty"],
            duration="2 hours",
            severity="Severe",
            medical_history=["Diabetes Type 2"],
            allergies=[],
            red_flags=["Chest Pain", "Respiratory Distress"],
            missing_information=[],
            triage_status=RiskState.URGENT,
            review_status="NEEDS_REVIEW",
            summary_en="CRITICAL: Patient experiencing severe chest tightness radiating to left arm with acute shortness of breath for 2 hours.",
            transcript=[
                ChatMessage(sender="patient", text="I am having severe chest pain and cannot breathe properly for the last 2 hours"),
                ChatMessage(sender="ai", text="⚠️ Emergency Alert: The symptoms you described indicate a high-risk condition requiring immediate medical evaluation.")
            ]
        )
        # Auto-book urgent appointment for Case 2
        apt2 = Appointment(
            case_id=case2.case_id,
            patient_id=case2.patient_id,
            type=AppointmentType.URGENT_EMERGENCY,
            slot_time=(datetime.now() + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M"),
            notes="EMERGENCY AUTO-BOOKED: Urgent Red Flag Chest Pain / Dyspnea"
        )
        case2.appointment_id = apt2.appointment_id

        self.sessions[sess1.session_id] = sess1
        self.cases[case1.case_id] = case1
        self.prescriptions[rx1.prescription_id] = rx1

        self.sessions[sess2.session_id] = sess2
        self.cases[case2.case_id] = case2
        self.appointments[apt2.appointment_id] = apt2

db = InMemoryDB()
