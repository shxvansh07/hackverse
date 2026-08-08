"""ML condition classifier — trained on a real public dataset, not fabricated
data. PERSON 2 owns this file.

Dataset: "Disease Prediction Using Machine Learning" (Kaggle-origin, mirrored at
github.com/sohamvsonar/Disease-Prediction-and-Medical-Recommendation-System)
— 4,920 rows, exactly 120 per class, 41 diseases, 132 binary symptom features.
Files live in patient_backend/data/. It's a clean, balanced teaching dataset
(not real de-identified EHR data), so don't be surprised the classifier looks
very confident — that reflects how tidy the data is, not real-world
diagnostic accuracy. Good enough to broaden diagnostic *reasoning breadth*
for a hackathon demo; not a clinical-grade diagnostic model.

DELIBERATELY NOT USED: this mirror's medications.csv/diets.csv files. Cross-
checking them against description.csv/precautions_df.csv (which line up
correctly) found scrambled content — e.g. "Heart attack" was mapped to
varicose-vein treatments (compression stockings, sclerotherapy) and
"Varicose veins" was mapped to thyroid medications (Levothyroxine,
radioactive iodine). Training.csv, description.csv, and precautions_df.csv
were spot-checked and are correctly aligned, so only those are used here.

SAFETY BOUNDARY: predicting a *condition label* from symptoms is a
legitimate, well-scoped use of a real dataset. Predicting a *medication
dosage* is not something this (or any) public symptom dataset can responsibly
support — see rag_engine.py for how a predicted condition here still only
becomes a concrete medication when it matches the small, manually-verified
DOSAGE_REFERENCE table; otherwise the draft explicitly says "doctor to
determine treatment" rather than inventing a drug/dose.
"""

from pathlib import Path

import pandas as pd
from sklearn.ensemble import RandomForestClassifier

DATA_DIR = Path(__file__).parent / "data"

_training = pd.read_csv(DATA_DIR / "Training.csv")
_training.columns = [c.strip() for c in _training.columns]
SYMPTOM_COLUMNS = [c for c in _training.columns if c != "prognosis"]
_COL_INDEX = {name: i for i, name in enumerate(SYMPTOM_COLUMNS)}

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

# Our conversational symptom labels (triage_engine.py's symptom_keywords) ->
# this dataset's column names. Extend both together if you add a new
# conversational symptom category.
SYMPTOM_LABEL_MAP: dict[str, list[str]] = {
    "fever": ["high_fever", "mild_fever"],
    "body ache": ["muscle_pain", "joint_pain", "back_pain"],
    "cough": ["cough"],
    "cold/runny nose": ["runny_nose", "continuous_sneezing", "congestion", "sinus_pressure"],
    "sore throat": ["throat_irritation", "patches_in_throat"],
    "headache": ["headache"],
    "acidity/heartburn": ["acidity", "indigestion", "stomach_pain"],
    "stomach ache": ["stomach_pain", "abdominal_pain", "belly_pain"],
    "diarrhea": ["diarrhoea"],
    "vomiting/nausea": ["vomiting", "nausea"],
    "dizziness": ["dizziness", "spinning_movements", "loss_of_balance", "unsteadiness"],
    "chest pain": ["chest_pain"],
    "breathing difficulty": ["breathlessness"],
    "feeling unwell": ["fatigue", "malaise", "lethargy"],
}

# A handful of very generic symptom combinations (e.g. "fever" + "body ache"
# alone) are shared by dozens of diseases in this dataset and Naive Bayes can
# still land on a confident-looking but implausible top guess for them (we
# saw "fever + body ache" alone predict "AIDS" at 96% during testing — an
# artifact of how few positive features that class has, not a real signal).
# Require at least this many mapped symptom columns before trusting the
# prediction at all, on top of the confidence floor.
MIN_MAPPED_COLUMNS = 3


def predict_condition(symptom_labels: list[str], min_confidence: float = 0.15) -> dict | None:
    """Maps conversational symptom labels onto the dataset's 132-column
    feature space and returns the top predicted condition. Returns None if
    too few labels map to known columns, or confidence is too low to be
    worth showing (41 classes => 1/41 ≈ 2.4% is chance level)."""
    vector = [0] * len(SYMPTOM_COLUMNS)
    mapped_columns = 0
    for label in symptom_labels:
        for col in SYMPTOM_LABEL_MAP.get(label, []):
            idx = _COL_INDEX.get(col)
            if idx is not None and vector[idx] == 0:
                vector[idx] = 1
                mapped_columns += 1

    if mapped_columns < MIN_MAPPED_COLUMNS:
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
