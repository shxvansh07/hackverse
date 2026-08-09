"""Keyword-based clinical extraction used when no LLM provider is reachable.

Without this, a fully offline deployment (or any turn where every provider
fails) has no way to populate structured state at all — the interview would
loop the fallback question bank forever and never reach LOW_RISK. This module
exists solely to keep the deterministic-only configuration functional, which is
also the configuration the test suite runs in.

Same rule as the LLM path: extract only what is actually said. A symptom
lexicon miss is a missing field, never a guess. Where a signal is present but
cannot be cleanly parsed into a structured value (an allergy phrase whose
substance we cannot isolate, say), the patient's own words are kept verbatim
rather than discarded — losing the fact that an allergy was mentioned is worse
than an untidy string in the allergies list.

Output is an `ExtractedClinicalInfo`, the identical schema the LLM path
produces, so both flow through the same `TriageService.merge_extraction`.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional

from app.ai.schemas import ExtractedClinicalInfo

_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def _fold(text: str) -> str:
    if not text:
        return ""
    return unicodedata.normalize("NFKC", text).translate(_DEVANAGARI_DIGITS).lower()


# term -> keywords that imply it, across English / Hindi / Hinglish
_SYMPTOM_LEXICON: Dict[str, List[str]] = {
    "fever": ["fever", "bukhar", "बुखार", "temperature", "feverish", "high temp"],
    "body ache": ["body ache", "body pain", "badan dard", "बदन दर्द", "अंगों में दर्द", "myalgia"],
    "cough": ["cough", "khansi", "खांसी"],
    "cold / runny nose": ["runny nose", "sneeze", "sneezing", "chink", "जुकाम", "छींक", "nazla", "blocked nose"],
    "sore throat": ["sore throat", "gala dard", "गले में खराश", "throat pain"],
    "headache": ["headache", "head pain", "sir dard", "sar dard", "सिरदर्द", "सर दर्द"],
    "acidity / heartburn": ["acidity", "heartburn", "acid reflux", "pet me jalan", "पेट में जलन", "khatti dakar"],
    "stomach ache": ["stomach ache", "stomach pain", "pet me dard", "पेट दर्द", "pet kharab", "पेट खराब"],
    "diarrhoea": ["diarrhea", "diarrhoea", "loose motion", "dast", "दस्त", "loose stool"],
    "vomiting / nausea": ["vomiting", "nausea", "ulti", "उल्टी", "जी मिचलाना", "throwing up"],
    "dizziness": ["dizziness", "chakkar", "चक्कर", "lightheaded"],
    "rash": ["rash", "skin rash", "khujli ke sath rash", "चकत्ते", "दाने"],
    "fatigue / weakness": ["weakness", "fatigue", "kamzori", "कमजोरी", "tired all the time"],
    "chest pain": ["chest pain", "seene me dard", "छाती में दर्द", "chest tightness"],
    "breathing difficulty": ["breathing difficulty", "shortness of breath", "saans lene me dikkat", "सांस लेने में तकलीफ"],
}

_SEVERITY_TERMS = {
    "Severe": ["severe", "bahut tez", "गंभीर", "बहुत तेज", "unbearable", "asahay"],
    "Moderate": ["moderate", "thoda tez", "मध्यम", "medium"],
    "Mild": ["mild", "halka", "हल्का", "slight"],
}

_HISTORY_TERMS = {
    "Diabetes": ["diabetes", "sugar", "शुगर", "madhumeh"],
    "Hypertension": ["hypertension", "high bp", "blood pressure", "बीपी", "बी.पी."],
    "Asthma": ["asthma", "dama", "दमा"],
    "Thyroid disorder": ["thyroid"],
    "Heart disease": ["heart disease", "cardiac", "dil ki bimari", "दिल की बीमारी"],
}

# Stems rather than whole words: "allerg" catches allergy/allergies/allergic
# in one match instead of enumerating every inflection.
_ALLERGY_STEMS = ["allerg", "एलर्जी", "elergy"]

_NEGATION_TERMS = [
    "no", "not", "none", "nahi", "nahin", "nai", "koi nahi", "kuch nahi",
    "नहीं", "नही", "कुछ नहीं", "कोई नहीं",
]

# Denies-history phrasing that names no specific condition. Without this a
# patient who says "no ongoing conditions" is left with history_confirmed
# still False, and the interview never stops asking.
_HISTORY_DENIAL_PATTERNS = [
    "no ongoing condition", "no medical history", "no other condition",
    "no health issue", "no health problem", "no past illness",
    "not have any condition", "no chronic", "nothing chronic",
    "koi bimari nahi", "koi purani bimari nahi", "purani bimari nahi",
    "कोई बीमारी नहीं", "कोई पुरानी बीमारी नहीं",
]

_DURATION_PATTERNS = [
    r"(\d+(?:\.\d+)?\s*(?:hour|hours|hr|hrs|ghante|ghanta|घंटे|घंटा|घंटों))",
    r"(\d+(?:\.\d+)?\s*(?:day|days|din|dino|dinon|दिन|दिनों))",
    r"(\d+(?:\.\d+)?\s*(?:week|weeks|hafte|hafta|hafton|हफ्ते|हफ़्ते|सप्ताह))",
    r"(\d+(?:\.\d+)?\s*(?:month|months|mahine|maheena|महीने|महीना))",
    r"(since\s+(?:yesterday|this morning|last night|today))",
    r"((?:kal|aaj|subah)\s*se)",
    r"(कल\s*से|आज\s*से|सुबह\s*से)",
]


def _has_nearby_negation(text: str, keyword: str, window: int = 25) -> bool:
    """Best-effort check for a negation word within `window` characters of a
    matched keyword, so "no allergy" and "allergy" are told apart."""
    idx = text.find(keyword)
    if idx == -1:
        return False
    span = text[max(0, idx - window) : idx + len(keyword) + window]
    return any(neg in span for neg in _NEGATION_TERMS)


def extract(patient_text: str) -> Optional[ExtractedClinicalInfo]:
    """Best-effort structured extraction from one patient message.

    Returns None only when the text is empty; otherwise returns a (possibly
    mostly-empty) schema instance, matching the LLM path's contract.
    """
    if not patient_text or not patient_text.strip():
        return None

    folded = _fold(patient_text)

    symptoms: List[str] = [
        label for label, keywords in _SYMPTOM_LEXICON.items()
        if any(kw in folded for kw in keywords)
    ]

    duration: Optional[str] = None
    for pattern in _DURATION_PATTERNS:
        match = re.search(pattern, folded)
        if match:
            duration = match.group(1).strip()
            break

    severity: Optional[str] = None
    for label, keywords in _SEVERITY_TERMS.items():
        if any(kw in folded for kw in keywords):
            severity = label
            break

    medical_history: List[str] = [
        label for label, keywords in _HISTORY_TERMS.items()
        if any(kw in folded for kw in keywords)
    ]
    # A history term appearing is always a positive statement in this lexicon
    # (no negated-history phrasing is matched here), so this is safe to log.
    denies_medical_history = not medical_history and any(
        phrase in folded for phrase in _HISTORY_DENIAL_PATTERNS
    )

    allergies: List[str] = []
    denies_allergies = False
    if any(stem in folded for stem in _ALLERGY_STEMS):
        matched_stem = next(stem for stem in _ALLERGY_STEMS if stem in folded)
        if _has_nearby_negation(folded, matched_stem):
            denies_allergies = True
        else:
            # Substance parsing is unreliable across scripts; keep the
            # patient's own words so nothing is silently dropped.
            allergies.append(patient_text.strip())

    return ExtractedClinicalInfo(
        symptoms=symptoms,
        associated_symptoms=[],
        duration=duration,
        severity=severity,
        medical_history=medical_history,
        medications=[],
        allergies=allergies,
        age=None,
        possible_red_flags=[],
        patient_denies_more_info=False,
        denies_allergies=denies_allergies,
        denies_medical_history=denies_medical_history,
    )
