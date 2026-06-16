"""Swappable LLM clients for JSON generation."""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential


def _load_env_file(path: str | Path = ".env") -> None:
    """Load simple KEY=VALUE pairs from .env without overriding real env vars."""
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _status_code(exc: BaseException) -> int | None:
    """Best-effort extraction of HTTP status from SDK exceptions."""
    for attr in ("status_code", "code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)

    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    if isinstance(value, int):
        return value

    return None


def _is_retryable_error(exc: BaseException) -> bool:
    """Return True for 429/rate-limit and common transient API failures."""
    status = _status_code(exc)
    if status in {408, 409, 429, 500, 502, 503, 504}:
        return True

    message = str(exc).lower()
    retry_markers = (
        "429",
        "rate limit",
        "ratelimit",
        "resource exhausted",
        "quota",
        "temporarily unavailable",
        "unavailable",
        "timeout",
        "timed out",
        "deadline exceeded",
        "internal error",
        "502",
        "503",
        "504",
    )
    return any(marker in message for marker in retry_markers)


class LLMClient(ABC):
    """Abstract interface for LLM providers used by extraction code."""

    @abstractmethod
    def generate_json(self, system_instructions: str, user_text: str) -> dict:
        """Return a JSON object generated from instructions and user text."""
        raise NotImplementedError


class GeminiClient(LLMClient):
    """Gemini implementation using the google-genai SDK."""

    def __init__(self) -> None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set. Add it to your environment or .env file.")

        from google import genai

        self.model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        self.client = genai.Client(api_key=api_key)

    @retry(
        retry=retry_if_exception(_is_retryable_error),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _generate_content(self, contents: str) -> Any:
        """Call Gemini with retries for rate limits and transient failures."""
        from google.genai import types

        return self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )

    def generate_json(self, system_instructions: str, user_text: str) -> dict:
        contents = f"{system_instructions}\n\n{user_text}"
        response = self._generate_content(contents)
        response_text = getattr(response, "text", None)

        if not response_text:
            raise RuntimeError("Gemini response did not include text to parse as JSON.")

        try:
            payload = json.loads(response_text)
        except json.JSONDecodeError as exc:
            snippet = response_text[:500].replace("\n", " ")
            raise RuntimeError(f"Gemini returned invalid JSON: {exc}. Response starts with: {snippet!r}") from exc

        if not isinstance(payload, dict):
            raise RuntimeError("Gemini JSON response must be an object at the top level.")

        return payload


def get_llm_client() -> LLMClient:
    """Return configured LLM client. Default provider: Gemini."""
    _load_env_file()
    provider = os.environ.get("LLM_PROVIDER", "gemini").strip().lower()

    if provider == "gemini":
        return GeminiClient()

    raise ValueError(f"Unsupported LLM_PROVIDER {provider!r}. Supported providers: gemini")
