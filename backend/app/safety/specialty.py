"""Deterministic specialty recommendation.

This is downstream of app.safety.engine, not a peer to it: it never decides a
risk state, it only maps a risk state's red flags to a suggested specialist so
the patient/doctor knows who to book with. Nothing here can escalate or
de-escalate a case.
"""

from __future__ import annotations

from typing import List

FALLBACK_SPECIALTY = "General Physician / Internal Medicine"

#: Keyed by the stable `code` field on each app.shared.knowledge.red_flags()
#: entry (see SafetyAssessment.red_flag_codes), not the human-readable label.
RED_FLAG_TO_SPECIALTY = {
    "CHEST_PAIN": "Cardiology",
    "RESPIRATORY_DISTRESS": "Pulmonology",
    "STROKE_NEURO": "Neurology",
    "LOSS_OF_CONSCIOUSNESS": "Neurology",
    "SEVERE_BLEEDING": "General Surgery",
    "ANAPHYLAXIS": "General Surgery",
    "SEVERE_ABDOMINAL": "Gastroenterology",
    "HIGH_FEVER_INFANT": FALLBACK_SPECIALTY,
    "SUICIDAL_IDEATION": "Psychiatry",
    "PREGNANCY_EMERGENCY": "Obstetrics & Gynecology",
}


def recommend_specialty(red_flag_codes: List[str]) -> str:
    """First matching code wins. Falls back to a general physician for
    UNCERTAIN cases (no red flag code at all) or any unmapped code."""
    for code in red_flag_codes:
        if code in RED_FLAG_TO_SPECIALTY:
            return RED_FLAG_TO_SPECIALTY[code]
    return FALLBACK_SPECIALTY
