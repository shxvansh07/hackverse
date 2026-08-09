"""Loader for the curated clinical knowledge files in /knowledge.

Content lives as data, not Python, so a clinician can review and amend it
without reading code. Files are read once and cached; KNOWLEDGE_DIR can be
overridden by env for tests.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# backend/app/shared/knowledge.py -> backend/app/shared -> backend/app -> backend -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DIR = _REPO_ROOT / "knowledge"


def knowledge_dir() -> Path:
    override = os.getenv("KNOWLEDGE_DIR")
    return Path(override).expanduser().resolve() if override else _DEFAULT_DIR


@lru_cache(maxsize=None)
def _load(filename: str) -> Dict[str, Any]:
    path = knowledge_dir() / filename
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        logger.error("Knowledge file missing: %s", path)
        return {}
    except json.JSONDecodeError as exc:
        logger.error("Knowledge file %s is not valid JSON: %s", path, exc)
        return {}


def clear_cache() -> None:
    _load.cache_clear()


def red_flags() -> List[Dict[str, Any]]:
    return _load("red_flags.json").get("red_flags", [])


def uncertain_combinations() -> List[Dict[str, Any]]:
    return _load("red_flags.json").get("uncertain_combinations", [])


def protocols() -> List[Dict[str, Any]]:
    return _load("formulary.json").get("protocols", [])


def guidance_passages() -> List[Dict[str, Any]]:
    return _load("clinical_guidance.json").get("passages", [])


def icd10_codes() -> List[Dict[str, Any]]:
    return _load("icd10.json").get("codes", [])


def icd10_title(code: str) -> str:
    for entry in icd10_codes():
        if entry.get("code") == code:
            return entry.get("title", "")
    return ""


def health() -> Dict[str, Any]:
    """Surfaced by /api/health so a missing knowledge file is caught at boot,
    not mid-demo."""
    return {
        "knowledge_dir": str(knowledge_dir()),
        "red_flags": len(red_flags()),
        "uncertain_combinations": len(uncertain_combinations()),
        "protocols": len(protocols()),
        "guidance_passages": len(guidance_passages()),
        "icd10_codes": len(icd10_codes()),
    }
