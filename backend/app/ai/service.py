"""The single entry point the application uses to talk to any LLM.

Responsibilities:
  * hold the ordered provider chain and fail over between vendors
  * validate every structured response against a Pydantic schema
  * degrade to deterministic behaviour when no provider succeeds

Callers get either validated data or a documented fallback. They never see a
raw model string that has not been through a schema, and they never see a
provider exception.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from app.ai import prompts
from app.ai.base import LLMMessage, LLMProvider, LLMProviderError, LLMRequest, LLMResponse
from app.ai.providers import (
    DeepSeekProvider,
    GeminiProvider,
    GroqProvider,
    NvidiaNIMProvider,
    OpenAIProvider,
    extract_json_object,
)
from app.ai.schemas import DraftRationale, ExtractedClinicalInfo, TranslatedText
from app.shared.languages import resolve

logger = logging.getLogger(__name__)

# Vendor id -> (class, env key var, env model var, default model).
# Adding a provider is one line here plus the adapter class.
_REGISTRY: Dict[str, Tuple[type, str, str, str]] = {
    "nvidia": (NvidiaNIMProvider, "NVIDIA_API_KEY", "NVIDIA_MODEL", "meta/llama-3.3-70b-instruct"),
    "gemini": (GeminiProvider, "GEMINI_API_KEY", "GEMINI_MODEL", "gemini-1.5-flash"),
    "groq": (GroqProvider, "GROQ_API_KEY", "GROQ_MODEL", "llama-3.3-70b-versatile"),
    "openai": (OpenAIProvider, "OPENAI_API_KEY", "OPENAI_MODEL", "gpt-4o-mini"),
    "deepseek": (DeepSeekProvider, "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "deepseek-chat"),
}

# Spec default: NVIDIA NIM primary, Gemini as the documented alternative.
_DEFAULT_ORDER = "nvidia,gemini,groq,openai,deepseek"


class AIService:
    """Ordered chain of providers with schema-validated outputs."""

    def __init__(self, order: Optional[List[str]] = None, timeout: float = 20.0):
        self.timeout = timeout
        self._order = order or [
            name.strip().lower()
            for name in os.getenv("LLM_PROVIDER_ORDER", _DEFAULT_ORDER).split(",")
            if name.strip()
        ]
        self._providers: List[LLMProvider] = self._build_chain()

    def _build_chain(self) -> List[LLMProvider]:
        chain: List[LLMProvider] = []
        for name in self._order:
            entry = _REGISTRY.get(name)
            if not entry:
                logger.warning("Unknown LLM provider %r in LLM_PROVIDER_ORDER; skipping", name)
                continue
            cls, key_var, model_var, default_model = entry
            provider = cls(
                api_key=os.getenv(key_var, "").strip(),
                model=os.getenv(model_var, default_model).strip() or default_model,
                timeout=self.timeout,
            )
            if provider.is_configured:
                chain.append(provider)
        return chain

    def reload(self) -> None:
        """Rebuild the chain after env changes (used by tests and /api/health)."""
        self._providers = self._build_chain()

    @property
    def available(self) -> bool:
        return bool(self._providers)

    @property
    def provider_names(self) -> List[str]:
        return [p.name for p in self._providers]

    # ---------------------------------------------------------------- core

    async def _complete(
        self, messages: List[Dict[str, str]], *, json_mode: bool = False,
        temperature: float = 0.2, max_tokens: int = 512,
    ) -> Optional[LLMResponse]:
        """Try each provider in order. Return None if the whole chain fails."""
        if not self._providers:
            return None

        request = LLMRequest(
            messages=[LLMMessage(role=m["role"], content=m["content"]) for m in messages],
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )

        for provider in self._providers:
            try:
                return await provider.complete(request)
            except LLMProviderError as exc:
                # Provider identity and status only. Prompts contain patient
                # speech and must not reach the log.
                logger.warning(
                    "LLM provider %s failed (status=%s); trying next",
                    provider.name, exc.status_code,
                )
            except Exception:  # noqa: BLE001 - never let a vendor bug 500 the API
                logger.exception("Unexpected error from LLM provider %s", provider.name)

        logger.error("All %d LLM providers failed", len(self._providers))
        return None

    # ------------------------------------------------------- public methods

    async def extract_clinical_info(
        self, patient_message: str, history: List[Dict[str, str]]
    ) -> Optional[ExtractedClinicalInfo]:
        """Structured extraction. None means "we learned nothing new".

        Callers must treat None as no-op, not as empty findings — the
        difference is whether we overwrite known state with blanks.
        """
        response = await self._complete(
            prompts.build_extraction_messages(patient_message, history),
            json_mode=True, temperature=0.0, max_tokens=600,
        )
        if response is None:
            return None

        try:
            payload = extract_json_object(response.text)
        except ValueError:
            logger.warning("Extraction from %s was not valid JSON", response.provider)
            return None

        try:
            return ExtractedClinicalInfo.model_validate(payload)
        except ValidationError:
            logger.warning("Extraction from %s failed schema validation", response.provider)
            return None

    async def generate_reply(
        self,
        patient_message: str,
        history: List[Dict[str, str]],
        lang: str,
        known_facts: Dict[str, Any],
        missing_info: List[str],
        previously_asked: List[str],
    ) -> Optional[str]:
        """One conversational turn in the patient's language."""
        response = await self._complete(
            prompts.build_conversation_messages(
                patient_message, history, lang, known_facts, missing_info, previously_asked
            ),
            temperature=0.3, max_tokens=220,
        )
        if response is None:
            return None

        reply = response.text.strip().strip('"')
        if not reply:
            return None

        # A verbatim repeat means the model ignored the do-not-repeat
        # instruction; fall through to the deterministic question bank.
        if reply in previously_asked:
            logger.info("LLM repeated a previous question; using deterministic fallback")
            return None
        return reply

    async def generate_summary(self, case_dict: Dict[str, Any]) -> Optional[str]:
        """English clinical summary for the doctor."""
        response = await self._complete(
            prompts.build_summary_messages(case_dict), temperature=0.1, max_tokens=260,
        )
        if response is None:
            return None
        summary = " ".join(response.text.split())
        return summary or None

    async def generate_rationale(
        self,
        case_dict: Dict[str, Any],
        medications: List[Dict[str, Any]],
        guidance: List[Dict[str, Any]],
    ) -> Optional[DraftRationale]:
        """Explain a formulary-selected draft. Never selects medication."""
        response = await self._complete(
            prompts.build_rationale_messages(case_dict, medications, guidance),
            json_mode=True, temperature=0.2, max_tokens=320,
        )
        if response is None:
            return None
        try:
            return DraftRationale.model_validate(extract_json_object(response.text))
        except (ValueError, ValidationError):
            # Prose without JSON is still usable as a rationale string.
            text = " ".join(response.text.split())
            return DraftRationale(rationale=text) if text else None

    async def translate(self, text: str, target_lang: str) -> Optional[str]:
        """Translate patient-facing instruction text.

        Callers must run the result through safety.translation_guard before
        showing it. A translation that alters clinical content is rejected
        there, not here.
        """
        if not text.strip():
            return ""
        language = resolve(target_lang)
        if language.code == "en":
            return text

        response = await self._complete(
            prompts.build_translation_messages(text, language.code),
            json_mode=True, temperature=0.0, max_tokens=900,
        )
        if response is None:
            return None

        try:
            validated = TranslatedText.model_validate(extract_json_object(response.text))
            return validated.text or None
        except (ValueError, ValidationError):
            candidate = response.text.strip()
            # Bare prose is acceptable; a stray JSON fragment is not.
            return candidate if candidate and not candidate.startswith("{") else None


#: Process-wide instance. Constructed once at import so the provider chain is
#: reported accurately by /api/health at startup.
ai_service = AIService()
