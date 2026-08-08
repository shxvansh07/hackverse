"""Concrete LLM vendor adapters.

Two wire formats cover every provider we support:

* OpenAI-compatible chat completions — NVIDIA NIM, Groq, OpenAI, DeepSeek.
  These differ only by base URL, so one base class handles all four.
* Google Gemini generateContent — different enough to warrant its own class.

Adding a vendor is a subclass plus a registry entry in service.py.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

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
