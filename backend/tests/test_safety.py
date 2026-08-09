"""Tests for the safety invariants.

These are the assertions that matter most: each one corresponds to a "never"
in the project spec. They run with no API keys configured — the deterministic
engine is the thing under test, and it must not depend on a model.
"""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

os.environ.setdefault("PERSIST_STATE", "0")
os.environ.setdefault("SEED_DEMO_DATA", "0")

from app.safety import engine, guards  # noqa: E402
from app.shared.models import (  # noqa: E402
    Medication,
    Prescription,
    PrescriptionStatus,
    RiskState,
)


def _complete_case(**overrides):
    """Baseline of a LOW_RISK-eligible case; override to test one variable."""
    params = dict(
        symptoms=["fever"],
        associated_symptoms=[],
        raw_text="I have had a fever for three days",
        duration="3 days",
        allergies=[],
        medical_history=[],
        allergies_confirmed=True,
        history_confirmed=True,
    )
    params.update(overrides)
    return engine.assess(**params)


# ---------------------------------------------------------------------------
# URGENT detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "I have severe chest pain",
        "mujhe seene me dard ho raha hai",
        "मुझे छाती में दर्द है",
        "I cannot breathe properly",
        "saans lene me dikkat ho rahi hai",
        "my face is drooping and speech is slurred",
        "he fainted this morning",
        "I am vomiting blood",
        "I have blood in my saliva",
        "my lips are swelling and throat closing",
    ],
)
def test_red_flags_escalate_to_urgent(text):
    """Configured red flags must classify URGENT in every supported script."""
    result = engine.assess(
        symptoms=[], associated_symptoms=[], raw_text=text, duration="2 hours",
    )
    assert result.risk_state == RiskState.URGENT
    assert result.red_flags
    assert result.eligible_for_draft is False


def test_urgent_takes_priority_over_incomplete_intake():
    """A red flag escalates even when nothing else is known about the patient."""
    result = engine.assess(
        symptoms=[], associated_symptoms=[], raw_text="crushing chest pain", duration="",
    )
    assert result.risk_state == RiskState.URGENT


# ---------------------------------------------------------------------------
# UNCERTAIN cannot be skipped
# ---------------------------------------------------------------------------

def test_missing_symptoms_yields_uncertain():
    result = _complete_case(symptoms=[], raw_text="not sure how to describe it")
    assert result.risk_state == RiskState.UNCERTAIN
    assert "symptoms" in result.missing_information


def test_missing_duration_yields_uncertain():
    result = _complete_case(duration="")
    assert result.risk_state == RiskState.UNCERTAIN
    assert "duration" in result.missing_information


def test_unasked_allergy_status_yields_uncertain():
    """An empty allergy list is not the same as a patient saying 'none'."""
    result = _complete_case(allergies=[], allergies_confirmed=False)
    assert result.risk_state == RiskState.UNCERTAIN
    assert "allergies" in result.missing_information


def test_fever_with_rash_yields_uncertain():
    result = _complete_case(
        symptoms=["fever", "rash"], raw_text="I have fever and a rash on my arms",
    )
    assert result.risk_state == RiskState.UNCERTAIN


def test_llm_hint_can_raise_concern():
    """A model hint we did not pattern-match still forces doctor review."""
    result = _complete_case(llm_hints=["patient sounds confused and disoriented"])
    assert result.risk_state == RiskState.UNCERTAIN


def test_llm_hint_cannot_clear_a_red_flag():
    """Hints are one-directional. No model output can downgrade URGENT."""
    result = engine.assess(
        symptoms=["chest pain"], associated_symptoms=[],
        raw_text="I have chest pain", duration="1 hour",
        allergies_confirmed=True, history_confirmed=True,
        llm_hints=["this is probably just acidity, low risk"],
    )
    assert result.risk_state == RiskState.URGENT


def test_self_reported_severe_yields_uncertain_without_a_red_flag():
    """The red-flag list is a fixed set of dangerous phrases; it will not
    catch every way a patient signals something feels serious. A patient
    explicitly describing their own symptoms as severe still routes to
    doctor review even when nothing else in the case looks alarming."""
    result = _complete_case(severity="Severe")
    assert result.risk_state == RiskState.UNCERTAIN
    assert not result.eligible_for_draft


def test_self_reported_mild_does_not_force_uncertain():
    result = _complete_case(severity="Mild")
    assert result.risk_state == RiskState.LOW_RISK


def test_self_reported_severe_never_overrides_an_actual_red_flag():
    """Self-reported severity is additive, never a substitute for or a
    downgrade of the red-flag check — a red flag alone is enough for URGENT
    regardless of what the severity field says."""
    result = engine.assess(
        symptoms=["chest pain"], associated_symptoms=[],
        raw_text="I have chest pain", duration="1 hour",
        allergies_confirmed=True, history_confirmed=True,
        severity="Mild",
    )
    assert result.risk_state == RiskState.URGENT


def test_complete_low_risk_case_is_eligible():
    result = _complete_case()
    assert result.risk_state == RiskState.LOW_RISK
    assert result.eligible_for_draft is True


# ---------------------------------------------------------------------------
# Draft gate
# ---------------------------------------------------------------------------

def test_urgent_case_cannot_generate_draft():
    """The headline invariant: URGENT never enters the prescription pathway."""
    assessment = engine.assess(
        symptoms=[], associated_symptoms=[], raw_text="severe chest pain", duration="1 hour",
    )
    gate = guards.may_generate_draft(assessment)
    assert gate.allowed is False
    assert gate.reason == "URGENT_ESCALATION"


def test_uncertain_case_cannot_generate_draft():
    assessment = _complete_case(duration="")
    gate = guards.may_generate_draft(assessment)
    assert gate.allowed is False
    assert gate.reason == "UNCERTAIN_REQUIRES_DOCTOR"


def test_low_risk_case_may_generate_draft():
    assert guards.may_generate_draft(_complete_case()).allowed is True


# ---------------------------------------------------------------------------
# Allergy cross-check
# ---------------------------------------------------------------------------

def test_direct_allergy_match_blocks_draft():
    conflict = guards.check_allergy_conflict(
        medications=[
            Medication(name="Paracetamol (Acetaminophen)", dosage="500 mg",
                       frequency="Twice daily", duration="3 days", instructions="After food")
        ],
        allergies=["paracetamol"],
    )
    assert conflict.allowed is False


def test_class_cross_reactivity_blocks_draft():
    """Penicillin allergy must block amoxicillin even though the words differ."""
    conflict = guards.check_allergy_conflict(
        medications=[
            Medication(name="Amoxicillin", dosage="500 mg", frequency="Thrice daily",
                       duration="5 days", instructions="After food")
        ],
        allergies=["I am allergic to penicillin"],
    )
    assert conflict.allowed is False


def test_unrelated_allergy_does_not_block():
    conflict = guards.check_allergy_conflict(
        medications=[
            Medication(name="Paracetamol", dosage="500 mg", frequency="Twice daily",
                       duration="3 days", instructions="After food")
        ],
        allergies=["dust", "pollen"],
    )
    assert conflict.allowed is True


# ---------------------------------------------------------------------------
# Patient release gate
# ---------------------------------------------------------------------------

def _draft() -> Prescription:
    return Prescription(
        case_id="CASE-TEST",
        medications=[
            Medication(name="Paracetamol", dosage="500 mg", frequency="Twice daily",
                       duration="3 days", instructions="After food")
        ],
    )


def test_draft_is_not_releasable():
    assert guards.may_release_to_patient(_draft()).allowed is False


def test_rejected_prescription_is_never_releasable():
    rx = _draft()
    rx.status = PrescriptionStatus.REJECTED
    result = guards.may_release_to_patient(rx)
    assert result.allowed is False
    assert result.reason == "REJECTED_BY_DOCTOR"


def test_approved_without_doctor_attribution_is_not_releasable():
    """Status alone is insufficient — a real decision leaves attribution."""
    rx = _draft()
    rx.status = PrescriptionStatus.APPROVED
    rx.is_ai_draft = False
    assert guards.may_release_to_patient(rx).allowed is False


def test_properly_approved_prescription_is_releasable():
    rx = _draft()
    rx.status = PrescriptionStatus.APPROVED
    rx.is_ai_draft = False
    rx.doctor_id = "DR-101"
    rx.approved_at = "2026-08-09T10:00:00+00:00"
    assert guards.may_release_to_patient(rx).allowed is True


def test_missing_prescription_is_not_releasable():
    assert guards.may_release_to_patient(None).allowed is False


# ---------------------------------------------------------------------------
# Translation integrity
# ---------------------------------------------------------------------------

def test_translation_preserving_numbers_passes():
    result = guards.verify_translation(
        "Take 500 mg twice daily for 3 days",
        "500 mg दिन में दो बार 3 दिन तक लें",
    )
    assert result.allowed is True


def test_translation_altering_a_dose_is_rejected():
    """The invariant that makes multilingual prescribing safe."""
    result = guards.verify_translation(
        "Take 500 mg twice daily for 3 days",
        "5000 mg दिन में दो बार 3 दिन तक लें",
    )
    assert result.allowed is False
    assert result.reason == "NUMERIC_DRIFT"


def test_translation_dropping_a_number_is_rejected():
    result = guards.verify_translation(
        "Take 500 mg twice daily for 3 days", "दिन में दो बार लें",
    )
    assert result.allowed is False


def test_empty_translation_is_rejected():
    assert guards.verify_translation("Take 500 mg", "").allowed is False


def test_medication_field_alteration_is_caught():
    med = Medication(name="Paracetamol", dosage="500 mg", frequency="Twice daily",
                     duration="3 days", instructions="After food")
    altered = {"name": "पैरासिटामोल", "dosage": "500 mg", "duration": "3 days"}
    assert guards.verify_medication_preserved(med, altered).allowed is False


def test_medication_preserved_passes():
    med = Medication(name="Paracetamol", dosage="500 mg", frequency="Twice daily",
                     duration="3 days", instructions="After food")
    intact = {"name": "Paracetamol", "dosage": "500 mg", "duration": "3 days"}
    assert guards.verify_medication_preserved(med, intact).allowed is True


@pytest.mark.parametrize("field", ["name", "dosage", "frequency", "duration"])
def test_medication_rejects_a_blank_required_field(field):
    """Regression test: a doctor's MODIFY submission with an incomplete row
    (e.g. a medication name typed but dosage left blank) used to be accepted
    — Medication's fields were `str` with no min_length, so an empty string
    satisfied the type check. A prescription reaching a patient with a blank
    dose or duration is a real defect, not cosmetic."""
    values = dict(name="Paracetamol", dosage="500 mg", frequency="Twice daily", duration="3 days")
    values[field] = ""
    with pytest.raises(ValidationError):
        Medication(**values)


def test_medication_rejects_a_whitespace_only_field():
    """min_length alone would let '   ' through — must be checked after strip."""
    with pytest.raises(ValidationError):
        Medication(name="   ", dosage="500 mg", frequency="Twice daily", duration="3 days")


def test_medication_trims_surrounding_whitespace():
    med = Medication(name="  Paracetamol  ", dosage="500 mg", frequency="Twice daily", duration="3 days")
    assert med.name == "Paracetamol"


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("3 days", 3.0), ("3 din", 3.0), ("3 दिन", 3.0),
        ("2 weeks", 14.0), ("24 hours", 1.0), ("teen din", 3.0),
    ],
)
def test_duration_parsing(text, expected):
    assert engine._duration_in_days(text) == pytest.approx(expected)


def test_unparseable_duration_returns_none():
    """Better to admit we do not know than to guess a number."""
    assert engine._duration_in_days("a little while") is None
