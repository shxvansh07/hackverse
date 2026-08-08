"""ML condition classifier — trained on a real public dataset, not fabricated
data.

Dataset: "Disease Prediction Using Machine Learning" (Kaggle-origin, mirrored at
github.com/sohamvsonar/Disease-Prediction-and-Medical-Recommendation-System)
— 4,920 rows, exactly 120 per class, 41 diseases, 132 binary symptom features.
Files live in patient_backend/data/. It's a clean, balanced teaching dataset
(not real de-identified EHR data), so don't be surprised the classifier looks
very confident — that reflects how tidy the data is, not real-world
diagnostic accuracy. Good enough to broaden diagnostic *reasoning breadth*
beyond the 6-condition curated formulary in knowledge/formulary.json; not a
clinical-grade diagnostic model and not a source of medication truth.

DELIBERATELY NOT USED: this mirror's medications.csv/diets.csv files. Cross-
checking them against description.csv/precautions_df.csv (which line up
correctly) found scrambled content — e.g. "Heart attack" was mapped to
varicose-vein treatments (compression stockings, sclerotherapy) and
"Varicose veins" was mapped to thyroid medications (Levothyroxine,
radioactive iodine). Training.csv, description.csv, and precautions_df.csv
were spot-checked and are correctly aligned, so only those are used here.

SAFETY BOUNDARY, unchanged by where this is called from: predicting a
*condition label* from symptoms is a legitimate, well-scoped use of a real
dataset. Predicting a *medication dosage* is not something this (or any)
public symptom dataset can responsibly support. app.rag.engine only ever
surfaces this module's output as diagnostic-hypothesis text for the doctor's
rationale (condition name, confidence, description, precautions) — it is
never permitted to add, substitute, or dose a medication. Every medication in
a draft still comes verbatim from knowledge/formulary.json, exactly as
app.safety.guards and the rest of the RAG layer already enforce.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sklearn.ensemble import RandomForestClassifier

from app.safety.engine import normalise

DATA_DIR = Path(__file__).parent / "data"

_training = pd.read_csv(DATA_DIR / "Training.csv")
_training.columns = [c.strip() for c in _training.columns]
SYMPTOM_COLUMNS = [c for c in _training.columns if c != "prognosis"]

#: Dataset column names double as the matching vocabulary ("high_fever" ->
#: "high fever") — richer and less brittle than hand-maintaining a separate
#: symptom-label map, and it stays in sync with the model by construction.
_COLUMN_PHRASES = [col.replace("_", " ").strip() for col in SYMPTOM_COLUMNS]

_X = _training[SYMPTOM_COLUMNS].values
_y = _training["prognosis"].values

# RandomForest, not a single decision tree or Naive Bayes: both of those were
# tried first and badly overfit to sparse/partial input on this dataset — a
# single tree snaps to 100%/0% on this very clean data, and Naive Bayes'
# independence assumption made "fever + body ache" alone predict AIDS at 96%
# confidence (an artifact of AIDS having an unusually minimal symptom
# footprint in this particular teaching dataset, not a real signal).
# Averaging across many trees on bootstrapped samples was the one approach
# that gave plausible top guesses (hepatitis A / Malaria / Dengue) for that
# same input instead of a single implausible high-confidence outlier.
_model = RandomForestClassifier(n_estimators=200, random_state=42)
_model.fit(_X, _y)

_description_df = pd.read_csv(DATA_DIR / "description.csv")
DESCRIPTIONS = dict(zip(_description_df["Disease"], _description_df["Description"]))

_precautions_df = pd.read_csv(DATA_DIR / "precautions_df.csv")
_precaution_cols = [c for c in _precautions_df.columns if c.startswith("Precaution")]
PRECAUTIONS = {
    row["Disease"]: [row[c] for c in _precaution_cols if isinstance(row[c], str) and row[c].strip()]
    for _, row in _precautions_df.iterrows()
}

# A handful of very generic symptom combinations (e.g. "fever" + "body ache"
# alone) are shared by dozens of diseases in this dataset, and a small model
# can still land on a confident-looking but implausible top guess for them.
# Require at least this many matched columns before trusting the prediction
# at all, on top of the confidence floor.
MIN_MATCHED_COLUMNS = 3


def predict_condition(
    symptoms: list[str],
    associated_symptoms: list[str] | None = None,
    free_text: str = "",
    min_confidence: float = 0.15,
) -> dict | None:
    """Maps free-text clinical terms onto the dataset's 132-column feature
    space and returns the top predicted condition, or None if too few terms
    match a known column, or confidence is too low to be worth showing (41
    classes => 1/41 ≈ 2.4% is chance level).

    Matching is deliberately a simple substring check against normalised
    text, same technique app.safety.engine uses for red flags — this is a
    soft diagnostic hint, not a safety gate, so it doesn't need that
    function's proximity-matching rigor.
    """
    haystack = normalise(" ".join([*symptoms, *(associated_symptoms or []), free_text]))
    if not haystack:
        return None

    vector = [0] * len(SYMPTOM_COLUMNS)
    matched = 0
    for i, phrase in enumerate(_COLUMN_PHRASES):
        if phrase and phrase in haystack:
            vector[i] = 1
            matched += 1

    if matched < MIN_MATCHED_COLUMNS:
        return None

    proba = _model.predict_proba([vector])[0]
    best_idx = proba.argmax()
    confidence = float(proba[best_idx])
    if confidence < min_confidence:
        return None

    disease = _model.classes_[best_idx]
    return {
        "condition": str(disease),
        "confidence": round(confidence, 3),
        "description": DESCRIPTIONS.get(disease, ""),
        "precautions": PRECAUTIONS.get(disease, []),
    }
