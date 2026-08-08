"""Provider-agnostic LLM interface.

Everything above this layer speaks only in `LLMRequest` / `LLMResponse`.
Swapping NVIDIA NIM for Gemini, or adding a new vendor, means adding one
subclass in providers.py and one entry in the registry — no application code
changes.

SAFETY BOUNDARY: nothing in this package is allowed to make a routing or
escalation decision. Providers return text. The safety engine decides.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class LLMMessage:
    """One turn in a conversation. `role` is normalised across vendors."""

    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class LLMRequest:
    messages: List[LLMMessage]
    temperature: float = 0.2
    max_tokens: int = 512
    # When set, the provider is asked to emit strict JSON. Providers that
    # cannot enforce this degrade to a prompt instruction; the caller still
    # validates with Pydantic, so a lying provider cannot corrupt state.
    json_mode: bool = False


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    raw: Dict[str, Any] = field(default_factory=dict)


class LLMProviderError(RuntimeError):
    """Raised when a provider cannot fulfil a request.

    The service layer catches this to advance to the next provider in the
    chain. It never leaks to the API surface.
    """

    def __init__(self, provider: str, message: str, status_code: Optional[int] = None):
        self.provider = provider
        self.status_code = status_code
        super().__init__(f"[{provider}] {message}")


class LLMProvider(abc.ABC):
    """Base class every vendor adapter implements."""

    #: Short stable identifier used in env config and logs.
    name: str = "base"

    def __init__(self, api_key: str, model: str, timeout: float = 20.0):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @property
    def is_configured(self) -> bool:
        """A provider with no key is skipped silently rather than erroring."""
        return bool(self.api_key)

    @abc.abstractmethod
    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Execute one completion. Raise LLMProviderError on any failure."""
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        state = "configured" if self.is_configured else "no-key"
        return f"<{type(self).__name__} model={self.model} {state}>"
