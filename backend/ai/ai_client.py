"""Unified AI client that routes to any provider (OpenAI-compatible, Anthropic, Gemini)."""

import json
import logging
import time
from typing import Optional

import requests

from ai.providers import get_provider, PROVIDERS
from db.database import get_sync_session
from db.models import AppSetting

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BACKOFF = [10, 20, 30]  # seconds to wait on 429


def _get_ai_config() -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Read AI provider/key/model from the database settings."""
    try:
        with get_sync_session() as session:
            provider_row = session.query(AppSetting).filter_by(key="ai_provider").first()
            key_row = session.query(AppSetting).filter_by(key="ai_api_key").first()
            model_row = session.query(AppSetting).filter_by(key="ai_model").first()
            return (
                provider_row.value if provider_row else None,
                key_row.value if key_row else None,
                model_row.value if model_row else None,
            )
    except Exception:
        return None, None, None


class AIClient:
    """Provider-agnostic AI client for code analysis and Dockerfile generation."""

    def __init__(self):
        provider_id, api_key, model_id = _get_ai_config()
        if not provider_id or not api_key:
            raise RuntimeError(
                "AI not configured. Go to Settings and add an AI provider + API key."
            )
        provider = get_provider(provider_id)
        if not provider:
            raise RuntimeError(f"Unknown AI provider: {provider_id}")

        self.provider_id = provider_id
        self.api_type = provider["api_type"]
        self.base_url = provider["base_url"]
        self.api_key = api_key
        self.model = model_id or provider["models"][0]["id"]

    def _request_with_retry(self, method, url, **kwargs) -> requests.Response:
        """Make an HTTP request with automatic retry on 429 rate limit."""
        for attempt in range(MAX_RETRIES + 1):
            resp = requests.request(method, url, **kwargs)
            if resp.status_code != 429 or attempt >= MAX_RETRIES:
                resp.raise_for_status()
                return resp
            wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
            # Check if server tells us how long to wait, but cap at 30s
            retry_after = resp.headers.get("retry-after")
            if retry_after:
                try:
                    wait = min(int(retry_after) + 1, 30)
                except ValueError:
                    pass
            logger.warning("Rate limited (429). Retrying in %ds (attempt %d/%d)", wait, attempt + 1, MAX_RETRIES)
            time.sleep(wait)
        resp.raise_for_status()
        return resp

    def _call_openai(self, prompt: str, max_tokens: int = 1024, temperature: float = 0.1) -> str:
        """Call an OpenAI-compatible API (works for Groq, DeepSeek, Mistral, etc.)."""
        resp = self._request_with_retry(
            "POST",
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
            },
            timeout=300,
        )
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"Unexpected OpenAI response format: {e}") from e

    def _call_anthropic(self, prompt: str, max_tokens: int = 1024, temperature: float = 0.1) -> str:
        """Call the Anthropic API."""
        resp = self._request_with_retry(
            "POST",
            f"{self.base_url}/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [{"role": "user", "content": prompt + "\n\nReturn ONLY valid JSON, no markdown."}],
            },
            timeout=300,
        )
        data = resp.json()
        try:
            content = data["content"][0]["text"]
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"Unexpected Anthropic response format: {e}") from e
        # Strip markdown code blocks if present
        if content.startswith("```"):
            parts = content.split("\n", 1)
            content = parts[1].rsplit("```", 1)[0] if len(parts) > 1 else content.strip("`").strip()
        return content

    def _call_gemini(self, prompt: str, max_tokens: int = 1024, temperature: float = 0.1) -> str:
        """Call the Google Gemini API."""
        resp = self._request_with_retry(
            "POST",
            f"{self.base_url}/models/{self.model}:generateContent?key={self.api_key}",
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt + "\n\nReturn ONLY valid JSON, no markdown."}]}],
                "generationConfig": {
                    "temperature": temperature,
                    "maxOutputTokens": max_tokens,
                    "responseMimeType": "application/json",
                },
            },
            timeout=300,
        )
        data = resp.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"Unexpected Gemini response format: {e}") from e

    def generate_json(self, prompt: str, max_tokens: int = 1024) -> dict:
        """Send a prompt and parse the JSON response. Raises on failure."""
        if self.api_type == "openai":
            raw = self._call_openai(prompt, max_tokens)
        elif self.api_type == "anthropic":
            raw = self._call_anthropic(prompt, max_tokens)
        elif self.api_type == "gemini":
            raw = self._call_gemini(prompt, max_tokens)
        else:
            raise RuntimeError(f"Unknown API type: {self.api_type}")

        # Parse JSON — handle potential markdown wrapping
        raw = raw.strip()
        if raw.startswith("```"):
            parts = raw.split("\n", 1)
            raw = parts[1].rsplit("```", 1)[0].strip() if len(parts) > 1 else raw.strip("`").strip()
        return json.loads(raw)
