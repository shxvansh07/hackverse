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
from app.services import fallback_questions as fq  # noqa: E402
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


def start_session(language: str = "en", phone: str | None = None) -> str:
    body = {"preferred_language": language}
    if phone:
        body["phone"] = phone
    resp = client.post("/api/patient/session", json=body)
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


def test_clinic_info_is_public_and_has_letterhead_fields():
    """Needed by the patient's printable prescription — must not require
    doctor auth, since the patient is the one rendering it."""
    resp = client.get("/api/clinic-info")
    assert resp.status_code == 200
    body = resp.json()
    for field in (
        "hospital_name", "hospital_address", "hospital_registration_no",
        "doctor_name", "doctor_qualification", "doctor_registration_no",
    ):
        assert body.get(field)


def test_bad_credentials_rejected():
    resp = client.post(
        "/api/auth/doctor/login", json={"username": "doctor", "password": "wrong"}
    )
    assert resp.status_code == 401


def test_repeated_failed_logins_lock_out_even_correct_credentials():
    """Brute-force protection: enough wrong attempts against one username
    locks it out for a cooldown window, regardless of what is tried next —
    including the real password. The locked-out response is a distinct 429
    with a "try again in N minutes" message, not the same generic 401 as a
    plain wrong password — conflating the two is confusing in practice (a
    doctor who mistypes their password a few times can no longer tell
    "locked out" from "wrong password" when they then type it correctly).
    Explicitly restores auth module state afterwards so later tests'
    auth_headers() fixture is unaffected."""
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

        # One more attempt, this time with the real password — still locked
        # out, but distinguishably so (429, not 401).
        resp = client.post(
            "/api/auth/doctor/login", json={"username": DOCTOR_USER, "password": DOCTOR_PASS}
        )
        assert resp.status_code == 429
        assert "try again" in resp.json()["detail"].lower()
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


def _complete_low_risk_intake_with_phone(phone: str, symptom: str = "fever") -> tuple[str, str]:
    """Same as _complete_low_risk_intake, but phone-linked so a second visit
    resolves to the same patient_id."""
    session_id = start_session("en", phone=phone)
    case = db.get_case_by_session(session_id)

    case.symptoms = [symptom]
    case.duration = "3 days"
    case.severity = "Moderate"
    case.allergies = []
    case.allergies_confirmed = True
    case.medical_history = []
    case.history_confirmed = True
    case.chief_complaint = symptom
    db.save_case(case)

    assessed = client.post("/api/triage/assess", json={"session_id": session_id}).json()
    assert assessed["triage_status"] == "LOW_RISK"
    assert assessed["draft_generated"] is True

    client.post("/api/cases", json={"session_id": session_id})
    return session_id, case.case_id


def test_returning_patient_history_feeds_next_draft():
    """A second visit by the same (phone-linked) patient, after the first was
    approved, should carry a history_context into the new draft's grounding —
    the whole point of referencing a past record when inferring a diagnosis."""
    phone = "9876500001"
    session1, case1_id = _complete_low_risk_intake_with_phone(phone, symptom="fever")
    headers = auth_headers()
    approve = client.post(
        f"/api/doctor/cases/{case1_id}/decision",
        json={"decision": "APPROVE", "notes": "Reviewed."},
        headers=headers,
    )
    assert approve.status_code == 200

    session2, case2_id = _complete_low_risk_intake_with_phone(phone, symptom="body ache")
    case1 = db.get_case(case1_id)
    case2 = db.get_case(case2_id)
    assert case1.patient_id == case2.patient_id  # same phone -> same patient

    assert "history_context" in case2.grounding
    assert "fever" in case2.grounding["history_context"]


def test_first_time_patient_has_no_history_context():
    _, case_id = _complete_low_risk_intake()
    case = db.get_case(case_id)
    assert "history_context" not in case.grounding


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
# Deterministic extraction fallback (no LLM configured at all)
# ---------------------------------------------------------------------------

def test_conversation_alone_reaches_a_draft_with_zero_providers():
    """The real regression test: drive intake through actual patient messages
    only, with no LLM configured, and confirm it still reaches a draft.

    _complete_low_risk_intake() elsewhere in this file sets case fields
    directly and does not exercise extraction at all — it would pass even if
    extraction were completely broken. This test does not take that shortcut.
    """
    session_id = start_session("en")

    r1 = send(session_id, "I have had a fever and body ache for 3 days")
    assert "fever" in db.get_case_by_session(session_id).symptoms

    r2 = send(session_id, "it is moderate, not severe")
    r3 = send(session_id, "I have no allergies and no ongoing conditions")

    case = db.get_case_by_session(session_id)
    assert case.duration
    assert case.allergies_confirmed is True
    assert case.history_confirmed is True

    # The interview walks a fixed question order (fallback_questions.
    # QUESTION_ORDER) and only "anything_else" is allowed to close intake, so
    # how many turns remain is a property of that list, not of this test.
    # Keep answering until intake closes rather than hard-coding a turn count
    # that any reordering of the question bank would silently break.
    for _ in range(len(fq.QUESTION_ORDER) + 2):
        r4 = send(session_id, "no, that's everything")
        if r4["is_complete"]:
            break
    assert r4["is_complete"] is True
    assert r4["triage_status"] == "LOW_RISK"

    assessed = client.post("/api/triage/assess", json={"session_id": session_id}).json()
    assert assessed["draft_generated"] is True

    prescription = db.get_prescription_for_case(case.case_id)
    assert prescription is not None
    assert prescription.medications


def test_negated_severity_is_not_read_as_self_reported_severity():
    """"not severe" must not be extracted as Severe.

    Self-reported severity routes a case to UNCERTAIN on its own (safety
    engine rule 6), so a substring match on "severe" inside "not severe"
    would send every patient who denies severity to a clinician and starve
    the low-risk path entirely.
    """
    from app.services import deterministic_extraction as de

    assert de.extract("it is moderate, not severe").severity == "Moderate"
    assert de.extract("no severe pain at all").severity is None

    # The guard must not overshoot in the unsafe direction: a negation in a
    # later clause, or a word merely containing "no", still leaves Severe.
    assert de.extract("severe headache, no allergies").severity == "Severe"
    assert de.extract("i know it is severe").severity == "Severe"


def test_hinglish_symptom_message_is_extracted_without_any_llm():
    session_id = start_session("hi")
    send(session_id, "Mujhe 3 din se fever hai aur body pain bhi hai")

    case = db.get_case_by_session(session_id)
    assert "fever" in case.symptoms
    assert "body ache" in case.symptoms
    assert case.duration


def test_stated_allergy_is_recorded_not_hidden():
    """If we cannot cleanly parse the substance, keep the patient's words
    rather than silently marking allergies as none."""
    session_id = start_session("en")
    send(session_id, "I have a fever")
    send(session_id, "I am allergic to penicillin")

    case = db.get_case_by_session(session_id)
    assert case.allergies_confirmed is True
    assert any("penicillin" in a.lower() for a in case.allergies)


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

    Also covers the fix for a second, related complaint: intake used to be
    able to end the moment ANY question got a "no" (e.g. "no allergies"),
    skipping the rest of the history-taking and never asking whether there
    was anything else before handing off. It must now walk the full fixed
    order (fallback_questions.QUESTION_ORDER) and only end on a "no" to the
    closing question specifically."""
    session_id = start_session("en")

    r = send(session_id, "i have a strong headache")  # answers: symptoms
    assert r["is_complete"] is False

    r = send(session_id, "no")  # answers: associated_symptoms
    assert r["is_complete"] is False

    r = send(session_id, "2 days")  # answers: duration
    assert r["is_complete"] is False

    r = send(session_id, "no")  # answers: allergies
    assert r["is_complete"] is False, "a 'no' mid-interview must not end the whole conversation"

    r = send(session_id, "no")  # answers: medical_history
    assert r["is_complete"] is False

    r = send(session_id, "no")  # answers: medications
    assert r["is_complete"] is False

    r = send(session_id, "no")  # answers: anything_else (the closing question)
    assert r["is_complete"] is True, "must complete once the closing question is answered"

    case = db.get_case(r["case_id"])
    assert case.symptoms, "symptoms should have been captured from the raw answer"
    assert case.duration, "duration should have been captured from the raw answer"
    assert case.allergies_confirmed
    assert case.history_confirmed


def test_first_patient_answer_is_not_dropped():
    """The greeting implicitly asks about symptoms; a patient's very first
    reply must be captured, not silently discarded while the bot re-asks the
    same question."""
    session_id = start_session("en")
    send(session_id, "i have a headache")

    case = db.get_case_by_session(session_id)
    # Deterministic extraction normalises the sentence down to the symptom
    # itself ("headache", not "i have a headache") — the symptom list feeds
    # red-flag matching and specialty routing, so it holds symptoms rather
    # than raw utterances. What this test guards is that the answer survives
    # at all, not the exact wording it is stored under.
    assert case.symptoms
    assert any("headache" in s for s in case.symptoms)


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


# ---------------------------------------------------------------------------
# Live consultation safety gate
# ---------------------------------------------------------------------------

def test_urgent_case_drafts_after_live_consultation_but_is_not_released():
    """A completed live consultation means a doctor has already examined the
    patient in person — that is the supervision an async URGENT/UNCERTAIN
    gate exists to require, so it does not re-apply here. A case originally
    flagged URGENT by the async intake DOES get a draft after its
    consultation ends, ready for the doctor to review. What still must not
    happen is the patient receiving anything before that doctor explicitly
    approves or modifies it — that release gate is untouched."""
    session_id = start_session("en")
    send(session_id, "I have severe chest pain and cannot breathe")
    case = db.get_case_by_session(session_id)
    assert case.triage_status == "URGENT"

    headers = auth_headers()
    consultation = client.post(
        "/api/doctor/consultations/start", json={"case_id": case.case_id}, headers=headers,
    ).json()

    resp = client.post(
        f"/api/doctor/consultations/{consultation['consultation_id']}/end", headers=headers,
    )
    assert resp.status_code == 200

    case = db.get_case(case.case_id)
    assert case.prescription_id is not None
    prescription = db.get_prescription_for_case(case.case_id)
    assert prescription is not None
    assert case.review_status == "NEW"

    # Still not visible to the patient until a doctor decides.
    status = client.get(f"/api/patient/status/{session_id}").json()
    assert status["prescription_available"] is False


# ---------------------------------------------------------------------------
# Patient history (phone-linked identity)
# ---------------------------------------------------------------------------

def test_same_phone_links_two_visits_to_the_same_patient():
    sid1 = start_session("en", phone="+91 98765-43210")
    sid2 = start_session("en", phone="9876543210")  # same number, different formatting
    case1 = db.get_case_by_session(sid1)
    case2 = db.get_case_by_session(sid2)
    assert case1.patient_id == case2.patient_id


def test_different_phones_get_different_patients():
    sid1 = start_session("en", phone="9876543210")
    sid2 = start_session("en", phone="9123456780")
    case1 = db.get_case_by_session(sid1)
    case2 = db.get_case_by_session(sid2)
    assert case1.patient_id != case2.patient_id


def test_blank_phone_behaves_exactly_as_before_phone_identity_existed():
    """No phone given must never accidentally link two strangers."""
    sid1 = start_session("en")
    sid2 = start_session("en")
    case1 = db.get_case_by_session(sid1)
    case2 = db.get_case_by_session(sid2)
    assert case1.patient_id != case2.patient_id


def test_returning_patient_carries_forward_settled_allergy_and_history():
    sid1 = start_session("en", phone="9876543210")
    case1 = db.get_case_by_session(sid1)
    case1.allergies = ["Penicillin"]
    case1.allergies_confirmed = True
    case1.medical_history = ["Hypertension"]
    case1.history_confirmed = True
    case1.handed_off = True  # only a completed visit counts as history
    db.save_case(case1)

    sid2 = start_session("en", phone="9876543210")
    case2 = db.get_case_by_session(sid2)
    assert case2.allergies == ["Penicillin"]
    assert case2.allergies_confirmed is True
    assert case2.medical_history == ["Hypertension"]
    assert case2.history_confirmed is True
    assert case2.carried_forward_from_previous_visit is True


def test_returning_patient_never_carries_forward_symptoms_or_red_flags():
    """Only settled allergy/history status is additive across visits — a
    fresh visit must always be assessed on what THIS visit's patient says,
    not on last time's complaint."""
    sid1 = start_session("en", phone="9876543210")
    send(sid1, "I have severe chest pain and cannot breathe")
    case1 = db.get_case_by_session(sid1)
    assert case1.triage_status == "URGENT"

    sid2 = start_session("en", phone="9876543210")
    case2 = db.get_case_by_session(sid2)
    assert case2.symptoms == []
    assert case2.red_flags == []
    assert case2.triage_status != "URGENT"


def test_abandoned_case_does_not_count_as_history_or_carry_forward():
    """A case still mid-conversation (never handed off) is not a completed
    visit — same principle as the doctor queue itself (list_cases only shows
    handed_off=True). Showing an abandoned chat as 'history', or carrying its
    partial data forward, would be misleading rather than helpful."""
    sid1 = start_session("en", phone="9990001111")
    case1 = db.get_case_by_session(sid1)
    case1.allergies = ["Penicillin"]
    case1.allergies_confirmed = True
    db.save_case(case1)  # handed_off left False: intake never finished

    sid2 = start_session("en", phone="9990001111")
    case2 = db.get_case_by_session(sid2)
    assert case2.allergies == []
    assert case2.allergies_confirmed is False
    assert case2.carried_forward_from_previous_visit is False

    case2.chief_complaint = "headache"
    db.save_case(case2)
    detail = client.get(f"/api/doctor/cases/{case2.case_id}", headers=auth_headers()).json()
    assert detail["patient_history"] is None


# ---------------------------------------------------------------------------
# Doctor-to-doctor case notes
# ---------------------------------------------------------------------------

def test_doctor_can_add_and_read_a_case_note():
    session_id = start_session("en")
    send(session_id, "I have a mild headache")
    case = db.get_case_by_session(session_id)
    client.post("/api/cases", json={"session_id": session_id})

    headers = auth_headers()
    resp = client.post(
        f"/api/doctor/cases/{case.case_id}/notes",
        json={"text": "Recheck BP next visit — was borderline last time."},
        headers=headers,
    )
    assert resp.status_code == 200
    note = resp.json()
    assert note["text"] == "Recheck BP next visit — was borderline last time."
    assert note["doctor_id"] == "DR-101"

    detail = client.get(f"/api/doctor/cases/{case.case_id}", headers=headers).json()
    assert len(detail["case"]["case_notes"]) == 1
    assert detail["case"]["case_notes"][0]["text"] == note["text"]


def test_case_note_requires_doctor_auth():
    session_id = start_session("en")
    case = db.get_case_by_session(session_id)
    resp = client.post(f"/api/doctor/cases/{case.case_id}/notes", json={"text": "unauthorized"})
    assert resp.status_code in (401, 403)


def test_case_note_on_unknown_case_is_404():
    resp = client.post(
        "/api/doctor/cases/CASE-DOES-NOT-EXIST/notes",
        json={"text": "hello"},
        headers=auth_headers(),
    )
    assert resp.status_code == 404


def test_first_time_patient_has_no_history_block():
    sid = start_session("en", phone="9998887776")
    case = db.get_case_by_session(sid)
    case.symptoms = ["fever"]
    case.chief_complaint = "fever"
    db.save_case(case)

    detail = client.get(f"/api/doctor/cases/{case.case_id}", headers=auth_headers()).json()
    assert detail["patient_history"] is None


def test_returning_patient_case_detail_includes_visit_history():
    sid1 = start_session("en", phone="9998887776")
    case1 = db.get_case_by_session(sid1)
    case1.chief_complaint = "fever"
    case1.handed_off = True  # only a completed visit counts as history
    db.save_case(case1)

    sid2 = start_session("en", phone="9998887776")
    case2 = db.get_case_by_session(sid2)
    case2.chief_complaint = "headache"
    db.save_case(case2)

    detail = client.get(f"/api/doctor/cases/{case2.case_id}", headers=auth_headers()).json()
    history = detail["patient_history"]
    assert history is not None
    assert history["visit_count"] == 1
    assert history["recent_cases"][0]["case_id"] == case1.case_id
    assert history["recent_cases"][0]["chief_complaint"] == "fever"
    # No LLM configured in this test file (see module header) — the
    # structured visit list must still be present even when the optional
    # highlight isn't.
    assert history["highlight"] is None
