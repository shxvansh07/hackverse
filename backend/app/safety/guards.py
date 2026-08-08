"""Enforcement points for the project's non-negotiable safety rules.

The rules in the spec are stated as prohibitions ("URGENT cases must not
continue the normal prescription workflow"). A prohibition that lives only in
a router's if-statement gets bypassed the first time someone adds a second
call site. Each one is therefore a named function here, and every call site
routes through it.

Four guards:
  may_generate_draft    - can this case receive an AI draft at all?
  check_allergy_conflict - does a drafted medication collide with an allergy?
  may_release_to_patient - is this prescription finalised by a doctor?
  verify_translation     - did translation preserve clinical content?
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from app.safety.engine import SafetyAssessment, normalise
from app.shared.models import Medication, Prescription, PrescriptionStatus, RiskState


@dataclass
class GuardResult:
    allowed: bool
    reason: str
    details: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {"allowed": self.allowed, "reason": self.reason, "details": self.details}


# ---------------------------------------------------------------------------
# 1. Draft generation gate
# ---------------------------------------------------------------------------

def may_generate_draft(assessment: SafetyAssessment) -> GuardResult:
    """Only LOW_RISK cases with complete intake may receive an AI draft.

    URGENT enters the escalation pathway and never the prescription pathway.
    UNCERTAIN goes to a doctor with no draft to anchor on — an AI suggestion
    attached to a case we could not classify is worse than no suggestion.
    """
    if assessment.risk_state == RiskState.URGENT:
        return GuardResult(
            allowed=False,
            reason="URGENT_ESCALATION",
            details=[
                "Case classified URGENT by the deterministic safety engine.",
                "Red flags: " + (", ".join(assessment.red_flags) or "unspecified"),
                "Urgent cases do not enter the prescription workflow.",
            ],
        )

    if assessment.risk_state == RiskState.UNCERTAIN:
        return GuardResult(
            allowed=False,
            reason="UNCERTAIN_REQUIRES_DOCTOR",
            details=["Case classified UNCERTAIN; doctor review required without an AI draft."]
            + assessment.reasons,
        )

    if not assessment.eligible_for_draft:
        return GuardResult(
            allowed=False,
            reason="NOT_ELIGIBLE",
            details=assessment.reasons or ["Case did not meet drafting preconditions."],
        )

    return GuardResult(allowed=True, reason="LOW_RISK_ELIGIBLE", details=assessment.reasons)


# ---------------------------------------------------------------------------
# 2. Allergy cross-check
# ---------------------------------------------------------------------------

#: Class-level cross-reactivity. Deliberately over-inclusive: a false positive
#: costs one doctor review, a false negative could cost a patient.
_ALLERGY_CLASS_MAP: Dict[str, List[str]] = {
    "penicillin": ["penicillin", "amoxicillin", "ampicillin", "augmentin", "cloxacillin", "beta-lactam"],
    "cephalosporin": ["cephalosporin", "cefixime", "ceftriaxone", "cefuroxime", "beta-lactam"],
    "sulfa": ["sulfa", "sulphonamide", "sulfonamide", "cotrimoxazole", "trimethoprim"],
    "nsaid": ["nsaid", "ibuprofen", "diclofenac", "aspirin", "naproxen", "aceclofenac", "ketorolac"],
    "paracetamol": ["paracetamol", "acetaminophen"],
    "antihistamine": ["antihistamine", "cetirizine", "levocetirizine", "hydroxyzine", "chlorpheniramine"],
    "macrolide": ["macrolide", "azithromycin", "erythromycin", "clarithromycin"],
    "quinolone": ["quinolone", "ciprofloxacin", "levofloxacin", "ofloxacin"],
    "ppi": ["ppi", "proton pump inhibitor", "pantoprazole", "omeprazole", "esomeprazole", "rabeprazole"],
}


def _allergy_tokens(allergy_text: str) -> List[str]:
    """Expand a stated allergy into the terms it should block.

    "I'm allergic to penicillin" yields the whole beta-lactam family, so a
    later amoxicillin draft is caught even though the words differ.
    """
    text = normalise(allergy_text)
    if not text:
        return []

    tokens = {text}
    for word in re.findall(r"[a-z]{4,}", text):
        tokens.add(word)

    expanded = set(tokens)
    for family in _ALLERGY_CLASS_MAP.values():
        if any(normalise(member) in text for member in family):
            expanded.update(normalise(member) for member in family)

    noise = {
        "allergic", "allergy", "reaction", "medicine", "medication", "drug", "tablet",
        "have", "from", "with", "that", "this", "मुझे", "एलर्जी", "hai", "mujhe",
    }
    return sorted({t for t in expanded if t and t not in noise and len(t) >= 4})


def check_allergy_conflict(
    medications: Sequence[Medication],
    allergies: Sequence[str],
    formulary_entries: Optional[Sequence[Dict[str, Any]]] = None,
) -> GuardResult:
    """Block a draft whose medication collides with a documented allergy.

    Matches on the drug name, on the formulary's declared contraindications,
    and on class cross-reactivity.
    """
    if not allergies:
        return GuardResult(True, "NO_ALLERGIES_DOCUMENTED", [])

    patient_tokens: List[str] = []
    for allergy in allergies:
        patient_tokens.extend(_allergy_tokens(allergy))
    if not patient_tokens:
        return GuardResult(True, "NO_PARSEABLE_ALLERGY_TERMS", [])

    declared: Dict[str, List[str]] = {}
    for entry in formulary_entries or []:
        for med in entry.get("medications", []):
            name = normalise(med.get("name", ""))
            if name:
                declared[name] = [
                    normalise(c) for c in med.get("contraindicated_allergies", [])
                ]

    conflicts: List[str] = []
    for med in medications:
        med_name = normalise(med.name)
        if not med_name:
            continue

        blocked = set(declared.get(med_name, []))
        for family in _ALLERGY_CLASS_MAP.values():
            if any(normalise(member) in med_name for member in family):
                blocked.update(normalise(member) for member in family)

        for token in patient_tokens:
            if token in med_name or any(token == b or token in b for b in blocked if b):
                conflicts.append(
                    f"{med.name} conflicts with documented allergy ('{token}')"
                )
                break

    if conflicts:
        return GuardResult(False, "ALLERGY_CONFLICT", conflicts)
    return GuardResult(True, "NO_CONFLICT", [])


# ---------------------------------------------------------------------------
# 3. Patient release gate
# ---------------------------------------------------------------------------

_FINAL_STATUSES = {PrescriptionStatus.APPROVED, PrescriptionStatus.MODIFIED}


def may_release_to_patient(prescription: Optional[Prescription]) -> GuardResult:
    """Only a doctor-approved or doctor-modified prescription is final.

    Enforced at the API boundary so a draft cannot reach a patient through any
    endpoint, including a direct prescription_id fetch.
    """
    if prescription is None:
        return GuardResult(False, "NO_PRESCRIPTION", ["No prescription exists for this case."])

    if prescription.status == PrescriptionStatus.REJECTED:
        return GuardResult(
            False, "REJECTED_BY_DOCTOR",
            ["This draft was rejected by the reviewing doctor and is not a prescription."],
        )

    if prescription.status not in _FINAL_STATUSES:
        return GuardResult(
            False, "AWAITING_DOCTOR_REVIEW",
            [f"Prescription status is {prescription.status.value}; awaiting doctor decision."],
        )

    if prescription.is_ai_draft:
        # Defensive: status and flag disagreeing means a write path is buggy.
        return GuardResult(
            False, "STILL_FLAGGED_AS_AI_DRAFT",
            ["Prescription is still flagged as an AI draft despite a final status."],
        )

    if not prescription.doctor_id or not prescription.approved_at:
        return GuardResult(
            False, "MISSING_DOCTOR_ATTRIBUTION",
            ["Prescription lacks doctor attribution or approval timestamp."],
        )

    return GuardResult(True, "DOCTOR_FINALISED", [])


# ---------------------------------------------------------------------------
# 4. Translation integrity
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def _numeric_multiset(text: str) -> List[str]:
    return sorted(_NUMBER_RE.findall(normalise(text)))


def verify_translation(source: str, translated: str) -> GuardResult:
    """Reject a translation that changed the numbers.

    Doses, frequencies and durations are numeric. If the digit multiset
    differs, the translation altered clinical content and the caller must fall
    back to the source text rather than display it.
    """
    if not translated or not translated.strip():
        return GuardResult(False, "EMPTY_TRANSLATION", ["Translator returned nothing."])

    source_numbers = _numeric_multiset(source)
    target_numbers = _numeric_multiset(translated)

    if source_numbers != target_numbers:
        return GuardResult(
            False, "NUMERIC_DRIFT",
            [
                f"Numbers differ between source and translation. "
                f"Source: {source_numbers or 'none'}. Translation: {target_numbers or 'none'}."
            ],
        )

    # A translation many times longer than its source has had content added.
    if len(translated) > max(400, len(source) * 4):
        return GuardResult(
            False, "LENGTH_ANOMALY",
            ["Translation is disproportionately long; content may have been added."],
        )

    return GuardResult(True, "PRESERVED", [])


def verify_medication_preserved(original: Medication, presented: Dict[str, Any]) -> GuardResult:
    """Assert that a presented medication is field-identical to the canonical one
    on every clinically load-bearing field."""
    mismatches: List[str] = []
    for field_name in ("name", "dosage", "duration"):
        if str(presented.get(field_name, "")).strip() != getattr(original, field_name).strip():
            mismatches.append(
                f"{field_name}: expected {getattr(original, field_name)!r}, "
                f"got {presented.get(field_name)!r}"
            )

    if mismatches:
        return GuardResult(False, "MEDICATION_ALTERED", mismatches)
    return GuardResult(True, "MEDICATION_PRESERVED", [])
