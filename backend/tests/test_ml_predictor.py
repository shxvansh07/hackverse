"""Tests for the ML condition classifier and its integration into the RAG
drafting path.

The invariant that matters most here: the classifier may suggest a
*condition*, but it must never be able to put a medication into a draft.
Every medication still comes verbatim from knowledge/formulary.json.
"""

from __future__ import annotations

import os

os.environ.setdefault("PERSIST_STATE", "0")
os.environ.setdefault("SEED_DEMO_DATA", "0")

from app.patient_backend.ml_predictor import predict_condition  # noqa: E402
from app.rag.engine import rag_engine  # noqa: E402


def test_plausible_condition_from_a_clean_symptom_pattern():
    # Diarrhoea + vomiting is a clean, well-represented pattern in the
    # training data — should confidently predict Gastroenteritis.
    result = predict_condition(symptoms=["diarrhoea", "vomiting", "nausea"])
    assert result is not None
    assert result["condition"] == "Gastroenteritis"
    assert result["confidence"] > 0.15
    assert result["description"]


def test_no_prediction_below_matched_column_threshold():
    result = predict_condition(symptoms=["fever"])
    assert result is None


def test_no_prediction_for_empty_input():
    assert predict_condition(symptoms=[], associated_symptoms=[], free_text="") is None


def test_rag_draft_never_gets_a_medication_from_the_ml_hypothesis():
    """Symptoms outside the 6-condition curated formulary but inside the
    41-condition classifier's vocabulary should surface a hypothesis in
    grounding/instructions, while medications stays empty — never populated
    from the classifier's output."""
    prescription, grounding = rag_engine.build_draft(
        case_id="CASE-TEST-ML",
        symptoms=["itching", "skin rash", "nodal skin eruptions"],
        associated_symptoms=[],
        summary="Patient reports itching and a skin rash.",
    )
    assert prescription.medications == []
    assert prescription.is_ai_draft is True
    if grounding.get("ml_hypothesis"):
        assert "condition" in grounding["ml_hypothesis"]
        assert "clinician assessment required" in prescription.instructions.lower()
