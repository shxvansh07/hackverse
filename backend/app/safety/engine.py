"""Deterministic triage classification.

This module is the system's safety boundary. It imports no LLM code and makes
no network calls. Given the same inputs it always returns the same answer, and
that answer is what routes the patient.

The model's opinion enters only as `llm_hints`, and only in one direction: a
hint can raise concern, never lower it. There is no code path by which a model
response marks a case LOW_RISK.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from app.shared import knowledge
from app.shared.models import RiskState

#: Fields that must be known before a case can be considered complete enough
#: to be classified LOW_RISK. Missing any of these yields UNCERTAIN.
REQUIRED_FIELDS = ("symptoms", "duration", "allergies")

_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


@dataclass
class SafetyAssessment:
    """Result of one deterministic evaluation."""

    risk_state: RiskState
    red_flags: List[str] = field(default_factory=list)
    red_flag_codes: List[str] = field(default_factory=list)
    missing_information: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    #: True only when every safety condition for symptomatic drafting is met.
    #: Consumed by safety.guards.may_generate_draft.
    eligible_for_draft: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "risk_state": self.risk_state.value,
            "red_flags": self.red_flags,
            "red_flag_codes": self.red_flag_codes,
            "missing_information": self.missing_information,
            "reasons": self.reasons,
            "eligible_for_draft": self.eligible_for_draft,
        }


def normalise(text: str) -> str:
    """Fold text so pattern matching is robust to script and spacing quirks.

    Handles Devanagari digits, NFKC compatibility forms, zero-width joiners
    used in Indic scripts, and punctuation that would otherwise split a phrase.
    """
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", text).translate(_DEVANAGARI_DIGITS).lower()
    folded = folded.replace("‌", "").replace("‍", "")
    folded = re.sub(r"[^\w\sऀ-෿]+", " ", folded, flags=re.UNICODE)
    return re.sub(r"\s+", " ", folded).strip()


def _duration_in_days(duration: str) -> Optional[float]:
    """Best-effort parse of a free-text duration. None when unparseable."""
    if not duration:
        return None
    text = normalise(duration)

    words = {
        "one": 1, "ek": 1, "two": 2, "do": 2, "three": 3, "teen": 3,
        "four": 4, "char": 4, "five": 5, "paanch": 5, "panch": 5,
        "six": 6, "chhe": 6, "seven": 7, "saat": 7, "ten": 10, "das": 10,
    }
    for word, value in words.items():
        text = re.sub(rf"\b{word}\b", str(value), text)

    units = [
        (r"(\d+(?:\.\d+)?)\s*(?:hour|hours|hr|hrs|ghante|ghanta|घंटे|घंटा|घंटों)", 1 / 24),
        (r"(\d+(?:\.\d+)?)\s*(?:day|days|din|dino|dinon|दिन|दिनों)", 1.0),
        (r"(\d+(?:\.\d+)?)\s*(?:week|weeks|hafte|hafta|hafton|हफ्ते|हफ़्ते|सप्ताह)", 7.0),
        (r"(\d+(?:\.\d+)?)\s*(?:month|months|mahine|maheena|महीने|महीना)", 30.0),
        (r"(\d+(?:\.\d+)?)\s*(?:year|years|saal|varsh|साल|वर्ष)", 365.0),
    ]
    for pattern, multiplier in units:
        match = re.search(pattern, text)
        if match:
            try:
                return float(match.group(1)) * multiplier
            except ValueError:
                continue

    if any(w in text for w in ("today", "aaj", "आज", "since morning", "subah", "सुबह")):
        return 0.5
    if any(w in text for w in ("yesterday", "kal", "कल", "last night", "raat", "रात")):
        return 1.0
    return None


#: How far apart a multi-word pattern's tokens may be and still count as a
#: match. Sized to absorb intervening copulas and articles ("face IS drooping",
#: "pain radiating TO THE left arm") without spanning unrelated clauses.
_PROXIMITY_WINDOW = 5


def _proximity_match(pattern_tokens: List[str], haystack_tokens: List[str]) -> bool:
    """True when every pattern token appears within a short window.

    Substring matching alone is too rigid for a safety layer: a patient writing
    "my face is drooping and speech is slurred" would not match the pattern
    "face drooping", and missing a stroke is not an acceptable failure mode.

    The window keeps this from degenerating into "these words appear somewhere
    in the conversation". Erring towards over-detection is deliberate — a false
    URGENT costs one doctor review, a false LOW_RISK costs considerably more.
    """
    if len(pattern_tokens) < 2:
        return False

    positions: List[List[int]] = []
    for token in pattern_tokens:
        hits = [i for i, h in enumerate(haystack_tokens) if h == token]
        if not hits:
            return False
        positions.append(hits)

    # Anchor on the rarest token, then require the rest nearby.
    anchor_idx = min(range(len(positions)), key=lambda i: len(positions[i]))
    span = _PROXIMITY_WINDOW + len(pattern_tokens)

    for anchor in positions[anchor_idx]:
        if all(
            any(abs(pos - anchor) <= span for pos in positions[i])
            for i in range(len(positions))
            if i != anchor_idx
        ):
            return True
    return False


def detect_red_flags(text: str) -> tuple[List[str], List[str]]:
    """Match normalised text against the curated red-flag reference.

    Two passes: exact phrase, then bounded-proximity token match. This is the
    only function permitted to conclude that a case is URGENT.
    """
    haystack = normalise(text)
    haystack_tokens = haystack.split()
    labels: List[str] = []
    codes: List[str] = []

    for entry in knowledge.red_flags():
        code = entry.get("code", "")
        label = entry.get("label", code.replace("_", " ").title())

        for pattern in entry.get("patterns", []):
            normalised_pattern = normalise(pattern)
            if not normalised_pattern:
                continue

            matched = normalised_pattern in haystack
            if not matched:
                matched = _proximity_match(normalised_pattern.split(), haystack_tokens)

            if matched:
                if code not in codes:
                    codes.append(code)
                    labels.append(label)
                break

    return labels, codes


def compute_missing_information(
    symptoms: Sequence[str],
    duration: str,
    allergies: Sequence[str],
    allergies_confirmed: bool,
    medical_history: Sequence[str],
    history_confirmed: bool,
) -> List[str]:
    """List the still-unknown required fields.

    "Patient stated they have no allergies" and "we never asked" are different
    states. The `*_confirmed` flags carry that distinction; an empty list alone
    never counts as answered.
    """
    missing: List[str] = []
    if not symptoms:
        missing.append("symptoms")
    if not duration:
        missing.append("duration")
    if not allergies and not allergies_confirmed:
        missing.append("allergies")
    if not medical_history and not history_confirmed:
        missing.append("medical_history")
    return missing


def _check_uncertain_combinations(haystack: str, duration_days: Optional[float]) -> List[str]:
    reasons: List[str] = []
    for combo in knowledge.uncertain_combinations():
        terms = [normalise(t) for t in combo.get("all_of", [])]
        if not terms or not all(t in haystack for t in terms):
            continue

        threshold = combo.get("duration_exceeds_days")
        if threshold is not None:
            if duration_days is None or duration_days <= float(threshold):
                continue

        reasons.append(combo.get("rationale") or combo.get("label", "Requires clinician evaluation"))
    return reasons


def assess(
    *,
    symptoms: Sequence[str],
    associated_symptoms: Sequence[str],
    raw_text: str,
    duration: str = "",
    allergies: Sequence[str] = (),
    medical_history: Sequence[str] = (),
    allergies_confirmed: bool = False,
    history_confirmed: bool = False,
    llm_hints: Sequence[str] = (),
    transcript_text: str = "",
) -> SafetyAssessment:
    """Classify a case as URGENT, UNCERTAIN or LOW_RISK.

    Order matters and is deliberate: urgency is checked before completeness,
    so a patient reporting chest pain escalates even if we know nothing else
    about them.
    """
    corpus = " ".join(
        [
            " ".join(symptoms), " ".join(associated_symptoms),
            raw_text, transcript_text, " ".join(llm_hints),
        ]
    )
    haystack = normalise(corpus)

    # 1. URGENT — any configured red flag anywhere in the case.
    labels, codes = detect_red_flags(corpus)
    if labels:
        return SafetyAssessment(
            risk_state=RiskState.URGENT,
            red_flags=labels,
            red_flag_codes=codes,
            missing_information=[],
            reasons=[f"Configured red flag detected: {', '.join(labels)}"],
            eligible_for_draft=False,
        )

    duration_days = _duration_in_days(duration)
    missing = compute_missing_information(
        symptoms, duration, allergies, allergies_confirmed,
        medical_history, history_confirmed,
    )

    reasons: List[str] = []

    # 2. UNCERTAIN — combinations that need a clinician even without a red flag.
    reasons.extend(_check_uncertain_combinations(haystack, duration_days))

    # 3. UNCERTAIN — required clinical facts still unknown.
    blocking = [f for f in missing if f in REQUIRED_FIELDS]
    if blocking:
        reasons.append(f"Required information not yet established: {', '.join(blocking)}")

    # 4. UNCERTAIN — the model flagged something our patterns did not match.
    #    Hints can only raise concern. This is the sole effect an LLM has on
    #    routing, and it is strictly one-directional.
    unmatched_hints = [h for h in llm_hints if h and h.strip()]
    if unmatched_hints and not labels:
        reasons.append(
            "Assistant flagged a possible warning sign not matched by the configured "
            f"red-flag list: {', '.join(unmatched_hints[:3])}"
        )

    # 5. UNCERTAIN — symptom burden out of proportion to a routine presentation.
    if len(set(symptoms)) >= 6:
        reasons.append("Unusually broad symptom cluster; warrants clinician review")

    if reasons:
        return SafetyAssessment(
            risk_state=RiskState.UNCERTAIN,
            red_flags=[],
            red_flag_codes=[],
            missing_information=missing,
            reasons=reasons,
            eligible_for_draft=False,
        )

    # 6. LOW_RISK — reached only when nothing above fired.
    return SafetyAssessment(
        risk_state=RiskState.LOW_RISK,
        red_flags=[],
        red_flag_codes=[],
        missing_information=missing,
        reasons=["No configured red flag detected and required intake fields are established"],
        eligible_for_draft=True,
    )
