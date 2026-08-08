from typing import List, Tuple
from app.models import RiskState

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
