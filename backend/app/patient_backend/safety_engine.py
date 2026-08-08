from typing import List, Tuple
from app.shared.models import RiskState, SeverityLevel

# Red flag keyword mappings (English & Hindi/Hinglish terms)
RED_FLAG_PATTERNS = {
    "CHEST_PAIN": [
        "chest pain", "chest tightness", "chest pressure", "pain in chest",
        "seene me dard", "seene mein dard", "छाती में दर्द", "छाती में जकड़न",
        "pain radiating to arm", "pain radiating to jaw"
    ],
    "RESPIRATORY_DISTRESS": [
        "shortness of breath", "difficulty breathing", "cannot breathe",
        " सांस लेने में तकलीफ", "sans lene me dikkat", "saans lene me dikkat",
        "heavy breathing", "gasping for air", "wheezing severely"
    ],
    "STROKE_NEURO": [
        "face drooping", "arm weakness", "slurred speech", "sudden numbness",
        "sudden paralysis", "bolne me dikkat", "chehra tedha", "लकवा", "चेहरा टेढ़ा"
    ],
    "HIGH_FEVER_INFANT": [
        "infant fever", "baby fever high", "fever above 104", "fever 103",
        "bachhe ko tez bukhar", "बच्चे को तेज़ बुखार"
    ],
    "LOSS_OF_CONSCIOUSNESS": [
        "fainted", "unconscious", "passed out", "behoosh", "behosh", "बेहोश"
    ],
    "SEVERE_BLEEDING": [
        "uncontrolled bleeding", "coughing blood", "vomiting blood",
        "khoon ki ulti", "khoon beh raha hai", "खून बह रहा है"
    ],
    "ANAPHYLAXIS": [
        "swelling of lips", "swelling of tongue", "throat closing",
        "gala band ho raha hai", "गला बंद होना"
    ],
    "SEVERE_ABDOMINAL": [
        "severe sudden abdominal pain", "stomach pain excruciating",
        "pet me severe dard", "पेट में अत्यधिक दर्द"
    ]
}

def evaluate_safety_triage(
    symptoms: List[str],
    associated_symptoms: List[str],
    patient_text_raw: str,
    allergies: List[str] = [],
    missing_info: List[str] = []
) -> Tuple[RiskState, List[str]]:
    """
    Evaluates safety deterministically over extracted structured fields + raw input text.
    Returns (RiskState, List of detected red flags).
    """
    detected_red_flags = []
    combined_text = f"{' '.join(symptoms)} {' '.join(associated_symptoms)} {patient_text_raw}".lower()

    # Check configured red flags
    for category, patterns in RED_FLAG_PATTERNS.items():
        for pattern in patterns:
            if pattern in combined_text:
                human_readable = category.replace("_", " ").title()
                if human_readable not in detected_red_flags:
                    detected_red_flags.append(human_readable)

    # 1. URGENT: Any red flag detected
    if detected_red_flags:
        return RiskState.URGENT, detected_red_flags

    # 2. UNCERTAIN: Critical information missing or complex allergy conflict
    if "symptoms" in missing_info or len(missing_info) >= 4:
        return RiskState.UNCERTAIN, []

    # Check for ambiguous severe combinations
    if "fever" in combined_text and ("rash" in combined_text or "stiff neck" in combined_text):
        return RiskState.UNCERTAIN, ["Fever with rash/neck stiffness - require clinician evaluation"]

    # 3. LOW_RISK: No red flags and sufficient intake details
    return RiskState.LOW_RISK, []


# Self-reported severity words (English + Hindi/Hinglish) -> normalized tier.
# Same vocabulary triage_engine.py already extracts into TriageCase.severity;
# kept here too since severity classification is safety-adjacent and belongs
# next to the red-flag rules, not duplicated ad hoc at the call site.
_MODERATE_WORDS = {"moderate", "thoda tez", "मध्यम"}
_SEVERE_WORDS = {"severe", "bahut tez", "गंभीर", "बहुत तेज"}

# Below this many combined symptoms, treat the case as MILD by default even
# with no explicit severity word — matches this codebase's existing "keep it
# simple" bias (PRD.md-equivalent principle: don't over-escalate on sparse
# information; UNCERTAIN already exists for genuinely insufficient intake).
MODERATE_SYMPTOM_COUNT_THRESHOLD = 3


def classify_severity(
    risk_state: RiskState,
    self_reported_severity: str,
    symptoms: List[str],
    associated_symptoms: List[str],
) -> SeverityLevel:
    """Deterministic MILD / MODERATE / SEVERE classification, separate from
    (but informed by) the red-flag RiskState above.

    - A detected red flag (RiskState.URGENT) always forces SEVERE — the
      deterministic safety net is authoritative over anything the patient
      says about their own severity.
    - Otherwise, an explicit self-reported "severe" also forces SEVERE, even
      without a matched red-flag phrase — this is the case that lets a
      patient's own words route straight to an appointment recommendation
      without waiting for the red-flag list to catch up (see
      triage_engine.process_message).
    - MODERATE: self-reported "moderate", an UNCERTAIN risk state (safety
      engine wasn't confident enough to call it low-risk), or a broader
      symptom picture (>= MODERATE_SYMPTOM_COUNT_THRESHOLD reported symptoms).
    - Otherwise MILD.
    """
    reported = (self_reported_severity or "").strip().lower()

    if risk_state == RiskState.URGENT or reported in _SEVERE_WORDS:
        return SeverityLevel.SEVERE

    symptom_count = len(symptoms) + len(associated_symptoms)
    if (
        reported in _MODERATE_WORDS
        or risk_state == RiskState.UNCERTAIN
        or symptom_count >= MODERATE_SYMPTOM_COUNT_THRESHOLD
    ):
        return SeverityLevel.MODERATE

    return SeverityLevel.MILD
