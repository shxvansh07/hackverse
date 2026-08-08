"""End-to-end workflow tests against the real API surface.

Runs with no LLM keys configured, so these exercise the deterministic paths:
extraction is skipped, the fallback question bank drives the interview, and the
safety engine does all routing. That is exactly the configuration in which the
safety properties must still hold.
"""

from __future__ import annotations

import os

import pytest

os.environ["PERSIST_STATE"] = "0"
os.environ["SEED_DEMO_DATA"] = "0"
os.environ["LLM_PROVIDER_ORDER"] = ""
for _key in ("NVIDIA_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
    os.environ.pop(_key, None)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.shared.database import db  # noqa: E402

client = TestClient(app)

DOCTOR_USER = os.getenv("DOCTOR_USERNAME", "doctor")
DOCTOR_PASS = os.getenv("DOCTOR_PASSWORD", "doctorpassword123")


@pytest.fixture(autouse=True)
def clean_store():
    db.reset()
    yield
    db.reset()


def auth_headers() -> dict:
    resp = client.post(
        "/api/auth/doctor/login", json={"username": DOCTOR_USER, "password": DOCTOR_PASS}
    )
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def start_session(language: str = "en") -> str:
    resp = client.post("/api/patient/session", json={"preferred_language": language})
    assert resp.status_code == 200
    return resp.json()["session_id"]


def send(session_id: str, message: str) -> dict:
    resp = client.post("/api/triage/message", json={"session_id": session_id, "message": message})
    assert resp.status_code == 200
    return resp.json()


# ---------------------------------------------------------------------------
# Health and auth
# ---------------------------------------------------------------------------

def test_health_reports_knowledge_loaded():
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["knowledge"]["red_flags"] > 0
    assert body["rag"]["protocols_indexed"] > 0


def test_doctor_endpoints_require_auth():
    assert client.get("/api/doctor/cases").status_code == 401
    assert client.get("/api/doctor/cases/CASE-X").status_code == 401


def test_bad_credentials_rejected():
    resp = client.post(
        "/api/auth/doctor/login", json={"username": "doctor", "password": "wrong"}
    )
    assert resp.status_code == 401


def test_repeated_failed_logins_lock_out_even_correct_credentials():
    """Brute-force protection: enough wrong attempts against one username
    locks it out for a cooldown window, regardless of what is tried next —
    including the real password. Explicitly restores auth module state
    afterwards so later tests' auth_headers() fixture is unaffected."""
    from app.shared import auth as auth_module

    saved_attempts = dict(auth_module._FAILED_ATTEMPTS)
    saved_tokens = dict(auth_module._ACTIVE_TOKENS)
    try:
        auth_module._FAILED_ATTEMPTS.clear()
        for _ in range(auth_module._MAX_FAILED_ATTEMPTS):
            resp = client.post(
                "/api/auth/doctor/login",
                json={"username": DOCTOR_USER, "password": "definitely-wrong"},
            )
            assert resp.status_code == 401

        # One more attempt, this time with the real password — still locked out.
        resp = client.post(
            "/api/auth/doctor/login", json={"username": DOCTOR_USER, "password": DOCTOR_PASS}
        )
        assert resp.status_code == 401
    finally:
        auth_module._FAILED_ATTEMPTS.clear()
        auth_module._FAILED_ATTEMPTS.update(saved_attempts)
        auth_module._ACTIVE_TOKENS.clear()
        auth_module._ACTIVE_TOKENS.update(saved_tokens)


# ---------------------------------------------------------------------------
# The URGENT invariant
# ---------------------------------------------------------------------------

def test_urgent_case_never_receives_a_prescription():
    """The single most important test in the suite."""
    session_id = start_session("en")
    body = send(session_id, "I have severe chest pain and cannot breathe")

    assert body["triage_status"] == "URGENT"
    assert body["red_flags"]
    assert body["urgent_guidance"]

    case = db.get_case_by_session(session_id)
    assert case.prescription_id is None
    assert db.get_prescription_for_case(case.case_id) is None

    # Even explicitly asking for an assessment must not produce one.
    resp = client.post("/api/triage/assess", json={"session_id": session_id})
    assert resp.status_code == 200
    assert resp.json()["draft_generated"] is False
    assert resp.json()["draft_blocked_reason"] == "URGENT_ESCALATION"
    assert db.get_prescription_for_case(case.case_id) is None


def test_urgent_case_is_handed_off_immediately():
    """Escalation must reach the doctor without waiting for intake to finish."""
    session_id = start_session("en")
    send(session_id, "I am vomiting blood")

    cases = client.get("/api/doctor/cases", headers=auth_headers()).json()
    assert len(cases) == 1
    assert cases[0]["triage_status"] == "URGENT"
    assert cases[0]["review_status"] == "URGENT"


def test_urgent_sorts_above_routine_in_the_queue():
    routine = start_session("en")
    send(routine, "I have a mild headache")
    client.post("/api/cases", json={"session_id": routine})

    urgent = start_session("en")
    send(urgent, "severe chest pain radiating to my left arm")

    cases = client.get("/api/doctor/cases", headers=auth_headers()).json()
    assert cases[0]["triage_status"] == "URGENT"


# ---------------------------------------------------------------------------
# The UNCERTAIN invariant
# ---------------------------------------------------------------------------

def test_incomplete_case_cannot_bypass_doctor_review():
    session_id = start_session("en")
    send(session_id, "I feel unwell")

    resp = client.post("/api/triage/assess", json={"session_id": session_id}).json()
    assert resp["triage_status"] == "UNCERTAIN"
    assert resp["draft_generated"] is False
    assert resp["draft_blocked_reason"] == "UNCERTAIN_REQUIRES_DOCTOR"


# ---------------------------------------------------------------------------
# Full happy path
# ---------------------------------------------------------------------------

def _complete_low_risk_intake() -> tuple[str, str]:
    """Drive a case to LOW_RISK with a draft, without any LLM."""
    session_id = start_session("en")
    case = db.get_case_by_session(session_id)

    # Populate state directly: extraction needs an LLM, and this test is about
    # the workflow downstream of extraction.
    case.symptoms = ["fever", "body ache"]
    case.duration = "3 days"
    case.severity = "Moderate"
    case.allergies = []
    case.allergies_confirmed = True
    case.medical_history = []
    case.history_confirmed = True
    case.chief_complaint = "fever"
    db.save_case(case)

    assessed = client.post("/api/triage/assess", json={"session_id": session_id}).json()
    assert assessed["triage_status"] == "LOW_RISK"
    assert assessed["draft_generated"] is True

    client.post("/api/cases", json={"session_id": session_id})
    return session_id, case.case_id


def test_low_risk_case_receives_grounded_draft():
    _, case_id = _complete_low_risk_intake()
    prescription = db.get_prescription_for_case(case_id)

    assert prescription is not None
    assert prescription.is_ai_draft is True
    assert prescription.status.value == "DRAFT"
    assert prescription.medications
    # Draft must be grounded in the curated formulary, not invented.
    assert prescription.grounding_sources
    assert prescription.icd10_code


def test_draft_is_not_visible_to_patient():
    """A patient hitting the prescription endpoint with a known id gets 409."""
    _, case_id = _complete_low_risk_intake()
    prescription = db.get_prescription_for_case(case_id)

    resp = client.get(f"/api/prescriptions/{prescription.prescription_id}")
    assert resp.status_code == 409
    assert resp.json()["detail"]["reason"] == "AWAITING_DOCTOR_REVIEW"


def test_approved_prescription_reaches_the_patient():
    session_id, case_id = _complete_low_risk_intake()
    headers = auth_headers()

    resp = client.post(
        f"/api/doctor/cases/{case_id}/decision",
        json={"decision": "APPROVE", "notes": "Reviewed and agreed."},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["released_to_patient"] is True

    status = client.get(f"/api/patient/status/{session_id}").json()
    assert status["prescription_available"] is True

    presented = client.get(f"/api/prescriptions/{status['prescription_id']}?lang=en")
    assert presented.status_code == 200
    assert presented.json()["is_ai_draft"] is False


def test_rejected_prescription_never_reaches_the_patient():
    session_id, case_id = _complete_low_risk_intake()
    prescription_id = db.get_prescription_for_case(case_id).prescription_id

    client.post(
        f"/api/doctor/cases/{case_id}/decision",
        json={"decision": "REJECT", "notes": "Not appropriate."},
        headers=auth_headers(),
    )

    resp = client.get(f"/api/prescriptions/{prescription_id}")
    assert resp.status_code == 409
    assert resp.json()["detail"]["reason"] == "REJECTED_BY_DOCTOR"

    status = client.get(f"/api/patient/status/{session_id}").json()
    assert status["prescription_available"] is False
    assert status["rejected"] is True


def test_modified_prescription_becomes_canonical():
    session_id, case_id = _complete_low_risk_intake()

    resp = client.post(
        f"/api/doctor/cases/{case_id}/decision",
        json={
            "decision": "MODIFY",
            "notes": "Reduced duration.",
            "modified_medications": [
                {
                    "name": "Ibuprofen",
                    "dosage": "400 mg",
                    "frequency": "Twice daily",
                    "duration": "2 days",
                    "instructions": "Take after food.",
                }
            ],
            "modified_instructions": "Rest and hydrate.",
        },
        headers=auth_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["released_to_patient"] is True

    status = client.get(f"/api/patient/status/{session_id}").json()
    presented = client.get(f"/api/prescriptions/{status['prescription_id']}?lang=en").json()

    # The doctor's version replaced the draft wholesale.
    assert len(presented["medications"]) == 1
    assert presented["medications"][0]["name"] == "Ibuprofen"
    assert presented["status"] == "MODIFIED"


def test_needs_review_does_not_release():
    session_id, case_id = _complete_low_risk_intake()
    client.post(
        f"/api/doctor/cases/{case_id}/decision",
        json={"decision": "NEEDS_REVIEW", "notes": "Want more history."},
        headers=auth_headers(),
    )
    status = client.get(f"/api/patient/status/{session_id}").json()
    assert status["prescription_available"] is False


def test_approve_without_a_draft_is_refused():
    """URGENT cases have no draft; APPROVE must not fabricate one."""
    session_id = start_session("en")
    send(session_id, "I have severe chest pain")
    case_id = db.get_case_by_session(session_id).case_id

    resp = client.post(
        f"/api/doctor/cases/{case_id}/decision",
        json={"decision": "APPROVE"},
        headers=auth_headers(),
    )
    assert resp.json()["released_to_patient"] is False


def test_doctor_may_prescribe_on_an_urgent_case_via_modify():
    """The AI is blocked from drafting; the doctor is never blocked."""
    session_id = start_session("en")
    send(session_id, "I have severe chest pain")
    case_id = db.get_case_by_session(session_id).case_id

    resp = client.post(
        f"/api/doctor/cases/{case_id}/decision",
        json={
            "decision": "MODIFY",
            "modified_medications": [
                {
                    "name": "Aspirin", "dosage": "300 mg", "frequency": "Once",
                    "duration": "Single dose", "instructions": "Chew immediately.",
                }
            ],
        },
        headers=auth_headers(),
    )
    assert resp.json()["released_to_patient"] is True


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------

def test_language_change_does_not_alter_clinical_content():
    """Acceptance criterion: medication, dosage and duration survive translation."""
    session_id, case_id = _complete_low_risk_intake()
    client.post(
        f"/api/doctor/cases/{case_id}/decision",
        json={"decision": "APPROVE"},
        headers=auth_headers(),
    )
    prescription_id = client.get(f"/api/patient/status/{session_id}").json()["prescription_id"]

    english = client.get(f"/api/prescriptions/{prescription_id}?lang=en").json()
    hindi = client.get(f"/api/prescriptions/{prescription_id}?lang=hi").json()

    assert len(english["medications"]) == len(hindi["medications"])
    for en_med, hi_med in zip(english["medications"], hindi["medications"]):
        assert en_med["name"] == hi_med["name"]
        assert en_med["dosage"] == hi_med["dosage"]
        assert en_med["duration"] == hi_med["duration"]


def test_unsupported_language_falls_back_to_english():
    session_id, case_id = _complete_low_risk_intake()
    client.post(
        f"/api/doctor/cases/{case_id}/decision",
        json={"decision": "APPROVE"},
        headers=auth_headers(),
    )
    prescription_id = client.get(f"/api/patient/status/{session_id}").json()["prescription_id"]

    body = client.get(f"/api/prescriptions/{prescription_id}?lang=xx").json()
    assert body["language"] == "en"


# ---------------------------------------------------------------------------
# Amendment and audit
# ---------------------------------------------------------------------------

def test_patch_requires_a_finalised_prescription():
    _, case_id = _complete_low_risk_intake()
    prescription_id = db.get_prescription_for_case(case_id).prescription_id

    resp = client.patch(
        f"/api/prescriptions/{prescription_id}",
        json={"instructions": "Changed."},
        headers=auth_headers(),
    )
    assert resp.status_code == 409


def test_patch_clears_cached_translations():
    """A stale translation of superseded content must never be served."""
    session_id, case_id = _complete_low_risk_intake()
    headers = auth_headers()
    client.post(
        f"/api/doctor/cases/{case_id}/decision", json={"decision": "APPROVE"}, headers=headers
    )
    prescription_id = client.get(f"/api/patient/status/{session_id}").json()["prescription_id"]

    client.get(f"/api/prescriptions/{prescription_id}?lang=hi")

    resp = client.patch(
        f"/api/prescriptions/{prescription_id}",
        json={"instructions": "Updated instructions."},
        headers=headers,
    )
    assert resp.status_code == 200
    assert db.get_prescription(prescription_id).translations == {}


def test_decisions_are_audited():
    _, case_id = _complete_low_risk_intake()
    headers = auth_headers()
    client.post(
        f"/api/doctor/cases/{case_id}/decision",
        json={"decision": "APPROVE", "notes": "Fine."},
        headers=headers,
    )

    events = client.get(f"/api/doctor/audit/{case_id}", headers=headers).json()["events"]
    actions = [e["action"] for e in events]
    assert "DOCTOR_DECISION" in actions
    assert "DRAFT_GENERATED" in actions


# ---------------------------------------------------------------------------
# Conversation behaviour
# ---------------------------------------------------------------------------

def test_intake_survives_total_llm_outage():
    """No providers configured: the interview must still progress."""
    session_id = start_session("hi")
    first = send(session_id, "mujhe bukhar hai")
    second = send(session_id, "3 din se")

    assert first["ai_response"]
    assert second["ai_response"]
    assert first["ai_response"] != second["ai_response"]


def test_questions_are_not_repeated():
    session_id = start_session("en")
    replies = [send(session_id, msg)["ai_response"] for msg in
               ["I have a fever", "about 3 days", "also a headache", "no allergies"]]
    assert len(set(replies)) == len(replies)


def test_intake_completes_with_zero_llm_providers():
    """Regression test for a real bug: without an LLM, extraction never ran,
    so symptoms/duration (both required) could never leave
    missing_information and intake could never complete — the fallback
    question bank would cycle through every question once and then repeat
    "anything else?" forever. The very first patient answer was also being
    silently dropped, because the greeting wasn't recognised as implicitly
    asking about symptoms.

    This reproduces exactly the reported sequence (symptom, duration, then a
    run of "no" answers) and asserts it actually completes."""
    session_id = start_session("en")

    first = send(session_id, "i have a strong headache")
    assert first["is_complete"] is False

    second = send(session_id, "2 days")
    assert second["is_complete"] is False

    # Enough "no" answers to clear every remaining tracked question — must
    # terminate, not loop indefinitely repeating the same question.
    result = None
    for _ in range(6):
        result = send(session_id, "no")
        if result["is_complete"]:
            break

    assert result is not None and result["is_complete"] is True

    case = db.get_case(result["case_id"])
    assert case.symptoms, "symptoms should have been captured from the raw answer"
    assert case.duration, "duration should have been captured from the raw answer"


def test_first_patient_answer_is_not_dropped():
    """The greeting implicitly asks about symptoms; a patient's very first
    reply must be captured, not silently discarded while the bot re-asks the
    same question."""
    session_id = start_session("en")
    send(session_id, "i have a headache")

    case = db.get_case_by_session(session_id)
    assert case.symptoms == ["i have a headache"]


def test_empty_message_rejected():
    session_id = start_session("en")
    resp = client.post("/api/triage/message", json={"session_id": session_id, "message": "   "})
    assert resp.status_code in (400, 422)


def test_unknown_session_returns_404():
    resp = client.post("/api/triage/message", json={"session_id": "SESS-NOPE", "message": "hi"})
    assert resp.status_code == 404


def test_unsupported_session_language_rejected():
    resp = client.post("/api/patient/session", json={"preferred_language": "zz"})
    assert resp.status_code == 400


def test_only_handed_off_cases_appear_in_queue():
    """A patient mid-conversation is not a case a doctor should see."""
    start_session("en")
    assert client.get("/api/doctor/cases", headers=auth_headers()).json() == []
