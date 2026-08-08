"""Supported interaction languages.

Adding a language is one entry here. The conversation engine, the fallback
question bank and the prescription presenter all read from this table, so no
engine needs redesigning to support a new one.

MVP scope per spec is English and Hindi; the remaining Indian languages are
wired through the same path and are exercised by the same code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class Language:
    code: str
    english_name: str
    native_name: str
    #: BCP-47 tag handed to the browser Web Speech API.
    speech_tag: str
    #: True for the two languages the MVP guarantees end to end.
    mvp: bool = False


LANGUAGES: Dict[str, Language] = {
    "en": Language("en", "English", "English", "en-IN", mvp=True),
    "hi": Language("hi", "Hindi", "हिन्दी", "hi-IN", mvp=True),
    "bn": Language("bn", "Bengali", "বাংলা", "bn-IN"),
    "mr": Language("mr", "Marathi", "मराठी", "mr-IN"),
    "ta": Language("ta", "Tamil", "தமிழ்", "ta-IN"),
    "te": Language("te", "Telugu", "తెలుగు", "te-IN"),
    "gu": Language("gu", "Gujarati", "ગુજરાતી", "gu-IN"),
    "kn": Language("kn", "Kannada", "ಕನ್ನಡ", "kn-IN"),
}

DEFAULT_LANGUAGE = "en"


def resolve(code: str | None) -> Language:
    """Return a known language, falling back to English rather than failing."""
    if not code:
        return LANGUAGES[DEFAULT_LANGUAGE]
    return LANGUAGES.get(code.lower().strip(), LANGUAGES[DEFAULT_LANGUAGE])


def is_supported(code: str | None) -> bool:
    return bool(code) and code.lower().strip() in LANGUAGES


def all_languages() -> List[Language]:
    return list(LANGUAGES.values())
