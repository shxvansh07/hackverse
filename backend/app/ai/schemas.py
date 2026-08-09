"""Typed schemas for every structured thing the LLM is allowed to emit.

The model never writes into clinical state directly. It emits JSON, that JSON
is validated here, and only validated fields are merged into the case. A
malformed or hallucinated response fails validation and is discarded rather
than corrupting the record.

Note what is deliberately absent: there is no field by which the model can set
a triage state or declare a case safe. `possible_red_flags` is advisory input
to the safety engine, never a routing decision.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


def _clean_terms(values: List[str], limit: int = 12) -> List[str]:
    """Normalise a model-emitted list: trim, drop empties/placeholders, dedupe."""
    placeholders = {
        "none", "n/a", "na", "null", "unknown", "not specified", "not mentioned",
        "nil", "-", "not provided", "unspecified", "not stated",
    }
    seen: set[str] = set()
    out: List[str] = []
    for raw in values:
        if not isinstance(raw, str):
            continue
        term = " ".join(raw.strip().split())
        if not term or term.lower() in placeholders or len(term) > 120:
            continue
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(term)
        if len(out) >= limit:
            break
    return out


class ExtractedClinicalInfo(BaseModel):
    """What the model believes the patient just told us.

    Every field is optional. An absent field means "the patient did not say",
    which is materially different from a guess — the merge step only writes
    fields that are actually present, so silence never becomes fabricated data.
    """

    symptoms: List[str] = Field(
        default_factory=list,
        description="Symptoms explicitly stated by the patient, in English clinical terms",
    )
    associated_symptoms: List[str] = Field(default_factory=list)
    duration: Optional[str] = Field(
        default=None, description="Verbatim duration if stated, e.g. '3 days'. Null if not stated."
    )
    severity: Optional[str] = Field(
        default=None, description="One of Mild, Moderate, Severe. Null if not stated."
    )
    medical_history: List[str] = Field(default_factory=list)
    medications: List[str] = Field(default_factory=list)
    allergies: List[str] = Field(default_factory=list)
    age: Optional[str] = None
    possible_red_flags: List[str] = Field(
        default_factory=list,
        description="ADVISORY ONLY. Passed to the deterministic safety engine as a hint. "
        "Never used on its own to escalate or to clear a case.",
    )
    patient_denies_more_info: bool = Field(
        default=False,
        description="True only when the patient explicitly indicates they have nothing further to add.",
    )

    @field_validator("symptoms", "associated_symptoms", "medical_history", "medications", "allergies", "possible_red_flags", mode="before")
    @classmethod
    def _coerce_list(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list):
            return []
        return _clean_terms([str(x) for x in v])

    @field_validator("duration", "severity", "age", mode="before")
    @classmethod
    def _coerce_optional_str(cls, v):
        if v is None:
            return None
        text = " ".join(str(v).strip().split())
        if not text or text.lower() in {"none", "n/a", "null", "unknown", "not specified", "not mentioned"}:
            return None
        return text[:120]

    @field_validator("severity")
    @classmethod
    def _normalise_severity(cls, v):
        if v is None:
            return None
        lowered = v.lower()
        for level in ("severe", "moderate", "mild"):
            if level in lowered:
                return level.capitalize()
        return None


class DraftRationale(BaseModel):
    """Model-authored justification attached to a draft prescription.

    Explanatory prose only. The medications themselves come from the curated
    formulary, not from the model, so a bad rationale cannot introduce a drug.
    """

    rationale: str = Field(default="", max_length=1200)
    referenced_guidance_ids: List[str] = Field(default_factory=list)

    @field_validator("rationale", mode="before")
    @classmethod
    def _coerce_rationale(cls, v):
        if v is None:
            return ""
        return " ".join(str(v).strip().split())[:1200]

    @field_validator("referenced_guidance_ids", mode="before")
    @classmethod
    def _coerce_ids(cls, v):
        if not isinstance(v, list):
            return []
        return _clean_terms([str(x) for x in v], limit=6)


class TranslatedText(BaseModel):
    """A translated string plus the language it claims to be in."""

    text: str = Field(default="", max_length=4000)
    language: str = Field(default="en", max_length=8)

    @field_validator("text", mode="before")
    @classmethod
    def _coerce_text(cls, v):
        if v is None:
            return ""
        return str(v).strip()[:4000]
