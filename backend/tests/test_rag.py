"""Tests for the RAG retrieval/drafting layer.

Covers the two failure modes the formulary expansion and confidence gating
(see app.rag.engine) were built to fix: a weak or ambiguous TF-IDF match
being silently trusted (the "everything defaults to paracetamol" bug), and
the ML classifier ever supplying a medication of its own rather than only
picking between protocols a human already wrote.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("PERSIST_STATE", "0")
os.environ.setdefault("SEED_DEMO_DATA", "0")

from app.rag import engine as rag_engine_module  # noqa: E402
from app.rag.engine import (  # noqa: E402
    MAX_AMBIGUITY_RATIO,
    MIN_DRAFT_CONFIDENCE,
    _ml_corroborates,
    rag_engine,
)
from app.shared import knowledge  # noqa: E402


# ---------------------------------------------------------------------------
# Correct-protocol-selection: the direct regression test for "almost always
# paracetamol". Each case is realistic patient-language phrasing that must
# resolve to the medically correct protocol, not a generic fallback.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "symptoms,expected_id",
    [
        (["burning urination", "frequent urination"], "PROTO-UTI"),
        (["sticky eyes", "eye discharge yellow"], "PROTO-CONJUNCTIVITIS-BACTERIAL"),
        (["itchy ring shaped rash"], "PROTO-TINEA"),
        (["throbbing one sided headache", "light sensitivity and nausea"], "PROTO-MIGRAINE"),
        (["headache"], "PROTO-TENSION-HEADACHE"),
        (["fever", "body ache"], "PROTO-FEVER-VIRAL"),
        (["acidity", "heartburn"], "PROTO-GERD"),
        (["period cramps", "menstrual pain"], "PROTO-DYSMENORRHEA"),
        (["pulled muscle", "muscle strain from exercise"], "PROTO-MUSCLE-STRAIN"),
        (["dull lower back pain", "back pain after lifting"], "PROTO-LOW-BACK-PAIN"),
        (["intense itching at night", "itching between fingers"], "PROTO-SCABIES"),
        (["vaginal itching", "thick white discharge"], "PROTO-VAGINAL-CANDIDIASIS"),
        (["tooth pain", "toothache"], "PROTO-TOOTHACHE"),
        (["white patches on tonsils", "severe throat pain with fever"], "PROTO-TONSILLITIS-BACTERIAL"),
        (["facial pain with pressure", "sinus pressure"], "PROTO-SINUSITIS"),
    ],
)
def test_realistic_complaint_selects_the_correct_protocol(symptoms, expected_id):
    prescription, grounding = rag_engine.build_draft(
        case_id="CASE-TEST-SELECTION", symptoms=symptoms, summary=" ".join(symptoms),
    )
    matched = [e["id"] for e in grounding["matched_entries"]]
    assert matched == [expected_id]
    assert prescription.medications  # a real match must produce real medication


def test_generic_malaise_no_longer_defaults_to_the_fever_protocol():
    """The original bug: near-every sick patient's description shares
    generic words (weakness, tiredness) with the fever/viral protocol's
    keyword list, so it won by default regardless of the actual complaint.
    A genuinely distinctive complaint sharing only incidental vocabulary
    with fever must not be swallowed by it."""
    prescription, grounding = rag_engine.build_draft(
        case_id="CASE-TEST-GENERIC",
        symptoms=["burning urination", "frequent urination", "weakness"],
        summary="burning urination, frequent urination and weakness",
    )
    matched = [e["id"] for e in grounding["matched_entries"]]
    assert matched == ["PROTO-UTI"]


# ---------------------------------------------------------------------------
# Confidence-threshold fallback
# ---------------------------------------------------------------------------

def test_out_of_formulary_condition_does_not_draft_a_medication():
    """Hypothyroidism-pattern symptoms are in the ML classifier's 41-class
    vocabulary but have no formulary protocol. A weak/spurious TF-IDF hit
    must not be trusted just because the list is non-empty."""
    prescription, grounding = rag_engine.build_draft(
        case_id="CASE-TEST-OOF",
        symptoms=["yellowish skin", "dark urine", "abdominal pain"],
        summary="yellowish skin, dark urine and abdominal pain",
    )
    assert prescription.medications == []
    assert not grounding["matched_entries"]
    assert "clinician assessment required" in prescription.instructions.lower()


def test_nonsense_query_never_drafts():
    prescription, grounding = rag_engine.build_draft(
        case_id="CASE-TEST-NONSENSE",
        symptoms=["asdkfj qwoeiru nonsense"],
        summary="asdkfj qwoeiru nonsense",
    )
    assert prescription.medications == []
    assert not grounding["matched_entries"]


def test_ambiguous_top_two_candidates_do_not_draft_without_ml_corroboration():
    """A constructed case where two protocols score within
    MAX_AMBIGUITY_RATIO of each other, and the ML hypothesis (mocked here to
    avoid depending on a coincidental real dataset row) corroborates
    neither. Must fall through to no-confident-match rather than guessing."""
    retrieval = {
        "protocols": [
            {"id": "PROTO-A", "score": 0.40, "condition": "Condition A", "protocol": {
                "id": "PROTO-A", "condition": "Condition A", "icd10": "X00", "medications": [],
            }},
            {"id": "PROTO-B", "score": 0.32, "condition": "Condition B", "protocol": {
                "id": "PROTO-B", "condition": "Condition B", "icd10": "X01", "medications": [],
            }},
        ],
        "guidance": [],
        "query": "test",
    }
    assert retrieval["protocols"][1]["score"] > retrieval["protocols"][0]["score"] * MAX_AMBIGUITY_RATIO

    def fake_retrieve(self, query):
        return retrieval

    def fake_predict_condition(**kwargs):
        return {"condition": "Something Unrelated", "confidence": 1.0, "description": "", "precautions": []}

    original_retrieve = rag_engine_module.ClinicalRAGEngine.retrieve
    rag_engine_module.ClinicalRAGEngine.retrieve = fake_retrieve
    original_predict = rag_engine_module.predict_condition
    rag_engine_module.predict_condition = fake_predict_condition
    try:
        prescription, grounding = rag_engine.build_draft(
            case_id="CASE-TEST-AMBIGUOUS", symptoms=["x"], summary="x",
        )
    finally:
        rag_engine_module.ClinicalRAGEngine.retrieve = original_retrieve
        rag_engine_module.predict_condition = original_predict

    assert prescription.medications == []
    assert not grounding["matched_entries"]


def test_ambiguous_top_two_candidates_draft_when_ml_corroborates_one():
    """Same ambiguous pair, but this time the mocked ML hypothesis names a
    condition matching PROTO-B — the tie-break should pick PROTO-B, and its
    medications (written by a human into the protocol, unchanged) are what
    gets drafted, never anything derived from the ML output itself."""
    retrieval = {
        "protocols": [
            {"id": "PROTO-A", "score": 0.40, "condition": "Condition A", "protocol": {
                "id": "PROTO-A", "condition": "Condition A", "icd10": "X00", "medications": [],
            }},
            {"id": "PROTO-B", "score": 0.32, "condition": "Fungal Skin Infection", "protocol": {
                "id": "PROTO-B", "condition": "Fungal Skin Infection", "icd10": "X01",
                "medications": [
                    {"name": "Clotrimazole", "dosage": "1%", "frequency": "Twice daily",
                     "duration": "14 days", "instructions": "Apply topically"}
                ],
            }},
        ],
        "guidance": [],
        "query": "test",
    }

    def fake_retrieve(self, query):
        return retrieval

    def fake_predict_condition(**kwargs):
        return {"condition": "Fungal Infection", "confidence": 1.0, "description": "", "precautions": []}

    original_retrieve = rag_engine_module.ClinicalRAGEngine.retrieve
    rag_engine_module.ClinicalRAGEngine.retrieve = fake_retrieve
    original_predict = rag_engine_module.predict_condition
    rag_engine_module.predict_condition = fake_predict_condition
    try:
        prescription, grounding = rag_engine.build_draft(
            case_id="CASE-TEST-TIEBREAK", symptoms=["x"], summary="x",
        )
    finally:
        rag_engine_module.ClinicalRAGEngine.retrieve = original_retrieve
        rag_engine_module.predict_condition = original_predict

    assert [e["id"] for e in grounding["matched_entries"]] == ["PROTO-B"]
    assert [m.name for m in prescription.medications] == ["Clotrimazole"]


# ---------------------------------------------------------------------------
# _ml_corroborates: the invariant that it only ever picks between two
# already-curated protocols, never supplies a medication of its own.
# ---------------------------------------------------------------------------

def test_ml_corroborates_matches_on_shared_condition_words():
    protocol = {"condition": "Dermatophytosis (fungal skin infection / ringworm)"}
    hypothesis = {"condition": "Fungal infection"}
    assert _ml_corroborates(protocol, hypothesis) is True


def test_ml_corroborates_false_on_unrelated_condition():
    protocol = {"condition": "Migraine"}
    hypothesis = {"condition": "Fungal infection"}
    assert _ml_corroborates(protocol, hypothesis) is False


def test_ml_corroborates_false_when_hypothesis_is_none():
    protocol = {"condition": "Migraine"}
    assert _ml_corroborates(protocol, None) is False


def test_rag_draft_never_gets_a_medication_from_the_ml_hypothesis_extended():
    """Regression coverage extended from test_ml_predictor.py's original
    invariant test to the new tie-break path specifically: even when the ML
    hypothesis corroborates a candidate, the medication that ends up on the
    prescription must trace verbatim to that candidate's own
    knowledge/formulary.json entry, never to anything in ml_hypothesis."""
    retrieval = {
        "protocols": [
            {"id": "PROTO-A", "score": 0.40, "condition": "Condition A", "protocol": {
                "id": "PROTO-A", "condition": "Condition A", "icd10": "X00", "medications": [],
            }},
            {"id": "PROTO-B", "score": 0.32, "condition": "Fungal Skin Infection", "protocol": {
                "id": "PROTO-B", "condition": "Fungal Skin Infection", "icd10": "X01",
                "medications": [
                    {"name": "Clotrimazole", "dosage": "1%", "frequency": "Twice daily",
                     "duration": "14 days", "instructions": "Apply topically"}
                ],
            }},
        ],
        "guidance": [],
        "query": "test",
    }

    def fake_retrieve(self, query):
        return retrieval

    def fake_predict_condition(**kwargs):
        return {
            "condition": "Fungal Infection", "confidence": 1.0,
            "description": "should never appear on a prescription",
            "precautions": ["should never appear on a prescription"],
        }

    original_retrieve = rag_engine_module.ClinicalRAGEngine.retrieve
    rag_engine_module.ClinicalRAGEngine.retrieve = fake_retrieve
    original_predict = rag_engine_module.predict_condition
    rag_engine_module.predict_condition = fake_predict_condition
    try:
        prescription, _ = rag_engine.build_draft(
            case_id="CASE-TEST-TIEBREAK-INVARIANT", symptoms=["x"], summary="x",
        )
    finally:
        rag_engine_module.ClinicalRAGEngine.retrieve = original_retrieve
        rag_engine_module.predict_condition = original_predict

    for med in prescription.medications:
        assert "should never appear" not in med.name
        assert "should never appear" not in med.instructions


# ---------------------------------------------------------------------------
# Corpus sanity: catches a three-file (formulary/icd10/clinical_guidance)
# sync miss mechanically rather than relying on manual review.
# ---------------------------------------------------------------------------

def test_every_protocol_icd10_resolves_to_a_title():
    for protocol in knowledge.protocols():
        title = knowledge.icd10_title(protocol["icd10"])
        assert title, f"{protocol['id']} has icd10={protocol['icd10']!r} with no matching entry in icd10.json"


def test_no_duplicate_protocol_or_icd10_ids():
    protocols = knowledge.protocols()
    ids = [p["id"] for p in protocols]
    icds = [p["icd10"] for p in protocols]
    assert len(ids) == len(set(ids)), "duplicate protocol id in formulary.json"
    assert len(icds) == len(set(icds)), "duplicate icd10 code in formulary.json"


def test_corpus_loads_with_a_substantial_protocol_count():
    """Loose regression guard: the formulary should stay well above the
    original 6-condition set that caused the keyword-collision bug."""
    rag_engine.load(force=True)
    assert len(knowledge.protocols()) >= 25
