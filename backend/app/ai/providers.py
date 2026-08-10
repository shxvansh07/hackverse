"""Concrete LLM vendor adapters.

Two wire formats cover every provider we support:

* OpenAI-compatible chat completions — NVIDIA NIM, Groq, OpenAI, DeepSeek.
  These differ only by base URL, so one base class handles all four.
* Google Gemini generateContent — different enough to warrant its own class.

Adding a vendor is a subclass plus a registry entry in service.py.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

import httpx

from app.ai.base import LLMMessage, LLMProvider, LLMProviderError, LLMRequest, LLMResponse


class OpenAICompatibleProvider(LLMProvider):
    """Any vendor exposing POST {base_url}/chat/completions in OpenAI's shape."""

    base_url: str = ""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        if not self.is_configured:
            raise LLMProviderError(self.name, "no API key configured")

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions", headers=headers, json=payload
                )
        except httpx.HTTPError as exc:
            raise LLMProviderError(self.name, f"transport failure: {exc}") from exc

        if resp.status_code != 200:
            # Body is truncated: upstream errors can echo the prompt back, and
            # prompts carry patient text we must not spill into logs.
            raise LLMProviderError(
                self.name, f"HTTP {resp.status_code}: {resp.text[:200]}", resp.status_code
            )

        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMProviderError(self.name, f"malformed response: {exc}") from exc

        if not text or not text.strip():
            raise LLMProviderError(self.name, "empty completion")

        return LLMResponse(
            text=text.strip(), provider=self.name, model=self.model, raw=data
        )


class NvidiaNIMProvider(OpenAICompatibleProvider):
    """NVIDIA NIM — the project's primary provider (llama-3.3-70b-instruct)."""

    name = "nvidia"
    base_url = "https://integrate.api.nvidia.com/v1"


class GroqProvider(OpenAICompatibleProvider):
    name = "groq"
    base_url = "https://api.groq.com/openai/v1"


class OpenAIProvider(OpenAICompatibleProvider):
    name = "openai"
    base_url = "https://api.openai.com/v1"


class DeepSeekProvider(OpenAICompatibleProvider):
    name = "deepseek"
    base_url = "https://api.deepseek.com"


class IBMBobProvider(LLMProvider):
    """IBM watsonx.ai (Granite). Not OpenAI-compatible — separate auth flow
    and wire format, so this does not extend OpenAICompatibleProvider.

    Two steps, not one: `api_key` is an IBM Cloud API key, which must first
    be exchanged for a short-lived IAM bearer token (~1hr) before it can
    authorize an actual inference call, and every call also needs a
    `project_id` (a watsonx project, not a per-request field an API key
    alone implies). Both quirks are invisible to the rest of the app — it
    still just sees `complete(request) -> LLMResponse`.
    """

    name = "ibm"

    #: IBM Cloud's identity service — same for every region/project.
    _IAM_URL = "https://iam.cloud.ibm.com/identity/token"
    #: watsonx.ai's chat API is versioned by date, not semver; this is the
    #: version this integration was written and tested against.
    _API_VERSION = "2023-05-29"

    def __init__(self, api_key: str, model: str, timeout: float = 20.0):
        super().__init__(api_key, model, timeout)
        self.project_id = os.getenv("IBM_PROJECT_ID", "").strip()
        region = os.getenv("IBM_REGION", "us-south").strip() or "us-south"
        self.base_url = f"https://{region}.ml.cloud.ibm.com"
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0

    @property
    def is_configured(self) -> bool:
        # An API key with no project_id can authenticate but every inference
        # call will still fail, so treat it the same as unconfigured — the
        # chain should skip to the next provider rather than burn a request.
        return bool(self.api_key) and bool(self.project_id)

    async def _get_token(self, client: httpx.AsyncClient) -> str:
        # 60s safety margin so a token doesn't expire mid-request.
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token

        try:
            resp = await client.post(
                self._IAM_URL,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                data={
                    "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
                    "apikey": self.api_key,
                },
            )
        except httpx.HTTPError as exc:
            raise LLMProviderError(self.name, f"IAM token exchange failed: {exc}") from exc

        if resp.status_code != 200:
            # Never echo the response body here — a bad apikey request can
            # otherwise get logged verbatim, and the key must never appear
            # in logs.
            raise LLMProviderError(
                self.name, f"IAM token exchange returned HTTP {resp.status_code}", resp.status_code
            )

        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise LLMProviderError(self.name, "IAM response had no access_token")

        self._token = token
        self._token_expires_at = time.time() + float(data.get("expires_in", 3600))
        return token

    async def complete(self, request: LLMRequest) -> LLMResponse:
        if not self.is_configured:
            raise LLMProviderError(
                self.name, "no API key or project_id configured (IBM_BOB_API_KEY / IBM_PROJECT_ID)"
            )

        payload: Dict[str, Any] = {
            "model_id": self.model,
            "project_id": self.project_id,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.json_mode:
            # Best-effort — if watsonx ignores this, extract_json_object()
            # downstream already tolerates free-form text with embedded JSON,
            # the same fallback every other provider relies on.
            payload["response_format"] = {"type": "json_object"}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                token = await self._get_token(client)
                resp = await client.post(
                    f"{self.base_url}/ml/v1/text/chat",
                    params={"version": self._API_VERSION},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except LLMProviderError:
            raise
        except httpx.HTTPError as exc:
            raise LLMProviderError(self.name, f"transport failure: {exc}") from exc

        if resp.status_code != 200:
            raise LLMProviderError(
                self.name, f"HTTP {resp.status_code}: {resp.text[:200]}", resp.status_code
            )

        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMProviderError(self.name, f"malformed response: {exc}") from exc

        if not text or not text.strip():
            raise LLMProviderError(self.name, "empty completion")

        return LLMResponse(text=text.strip(), provider=self.name, model=self.model, raw=data)


class GrokProvider(OpenAICompatibleProvider):
    """xAI Grok — official OpenAI-compatible endpoint (api.x.ai)."""

    name = "grok"
    base_url = "https://api.x.ai/v1"


class GeminiProvider(LLMProvider):
    """Google Gemini via generateContent.

    Gemini has no `system` role, so the system message is folded into the
    first user turn — the documented workaround for this endpoint.
    """

    name = "gemini"
    base_url = "https://generativelanguage.googleapis.com/v1beta/models"

    @staticmethod
    def _to_gemini_contents(messages: List[LLMMessage]) -> tuple[str, List[Dict[str, Any]]]:
        system_parts: List[str] = []
        contents: List[Dict[str, Any]] = []

        for msg in messages:
            if msg.role == "system":
                system_parts.append(msg.content)
                continue
            role = "user" if msg.role == "user" else "model"
            contents.append({"role": role, "parts": [{"text": msg.content}]})

        system_text = "\n\n".join(system_parts)
        if system_text and contents:
            first = contents[0]
            if first["role"] == "user":
                first["parts"][0]["text"] = f"{system_text}\n\n{first['parts'][0]['text']}"
            else:
                contents.insert(0, {"role": "user", "parts": [{"text": system_text}]})
        elif system_text:
            contents.append({"role": "user", "parts": [{"text": system_text}]})

        return system_text, contents

    async def complete(self, request: LLMRequest) -> LLMResponse:
        if not self.is_configured:
            raise LLMProviderError(self.name, "no API key configured")

        _, contents = self._to_gemini_contents(request.messages)

        generation_config: Dict[str, Any] = {
            "temperature": request.temperature,
            "maxOutputTokens": request.max_tokens,
        }
        if request.json_mode:
            generation_config["responseMimeType"] = "application/json"

        url = f"{self.base_url}/{self.model}:generateContent"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    url,
                    params={"key": self.api_key},
                    json={"contents": contents, "generationConfig": generation_config},
                )
        except httpx.HTTPError as exc:
            raise LLMProviderError(self.name, f"transport failure: {exc}") from exc

        if resp.status_code != 200:
            raise LLMProviderError(
                self.name, f"HTTP {resp.status_code}: {resp.text[:200]}", resp.status_code
            )

        try:
            data = resp.json()
            candidate = data["candidates"][0]
            text = "".join(part.get("text", "") for part in candidate["content"]["parts"])
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMProviderError(self.name, f"malformed response: {exc}") from exc

        if not text.strip():
            raise LLMProviderError(self.name, "empty completion")

        return LLMResponse(
            text=text.strip(), provider=self.name, model=self.model, raw=data
        )


def extract_json_object(text: str) -> Dict[str, Any]:
    """Pull the first JSON object out of a completion.

    Models wrap JSON in prose or fences even when told not to. This tolerates
    that without ever trusting the parsed content — the caller still validates
    against a Pydantic schema.
    """
    cleaned = text.strip()

    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1] if "```" in cleaned[3:] else cleaned[3:]
        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
        cleaned = cleaned.strip("`").strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    if start == -1:
        raise ValueError("no JSON object found in completion")

    depth = 0
    in_string = False
    escaped = False
    for idx in range(start, len(cleaned)):
        ch = cleaned[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(cleaned[start : idx + 1])

    raise ValueError("unterminated JSON object in completion")
