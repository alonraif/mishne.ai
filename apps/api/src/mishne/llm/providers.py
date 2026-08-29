"""Adapters for the four vendors, over plain HTTP.

No vendor SDKs. Four SDKs would be four dependency trees, four release cadences
and four ways for an unrelated upgrade to break a render job, in exchange for
convenience this module does not need: one POST, one JSON body, one string back.
`urllib` is in the standard library and the request shapes below are stable
public API.

Three of the four speak the OpenAI chat-completions shape — OpenAI itself,
xAI, and Google's OpenAI-compatible endpoint — so they are one class with three
base URLs. Anthropic's Messages API differs in two ways that matter: the system
prompt is a top-level field rather than a message, and `max_tokens` is required.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .base import Completion, LLMError, ProviderConfig

TIMEOUT_S = 120

PROVIDERS = {
    "anthropic": ProviderConfig(
        "anthropic", "ANTHROPIC_API_KEY", "https://api.anthropic.com/v1"),
    "openai": ProviderConfig(
        "openai", "OPENAI_API_KEY", "https://api.openai.com/v1"),
    "google": ProviderConfig(
        "google", "GEMINI_API_KEY",
        "https://generativelanguage.googleapis.com/v1beta/openai"),
    "xai": ProviderConfig("xai", "XAI_API_KEY", "https://api.x.ai/v1"),
}


def _post(url: str, headers: dict, body: dict) -> tuple[dict, int]:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"content-type": "application/json", **headers})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:400]
        # 4xx other than rate limiting is our bug — a bad model id, a malformed
        # request. Retrying it on another vendor will fail the same way, and
        # saying so stops the router burning three keys on one mistake.
        retryable = exc.code in (408, 409, 429) or exc.code >= 500
        raise LLMError(f"HTTP {exc.code}: {detail}", retryable=retryable)
    except Exception as exc:  # noqa: BLE001 — timeouts, DNS, connection resets
        raise LLMError(f"{type(exc).__name__}: {exc}", retryable=True)
    return payload, int((time.time() - t0) * 1000)


@dataclass
class OpenAICompatible:
    """OpenAI, xAI, and Gemini's compatibility endpoint."""

    config: ProviderConfig

    @property
    def name(self) -> str:
        return self.config.name

    def complete(self, *, model: str, system: str, user: str,
                 max_tokens: int = 4096,
                 temperature: float = 0.0) -> Completion:
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_completion_tokens": max_tokens,
        }
        # Reasoning models reject an explicit temperature. Sending the default
        # and letting the vendor choose is both safer and what we want anyway.
        if temperature:
            body["temperature"] = temperature

        data, ms = _post(f"{self.config.base_url}/chat/completions",
                         {"authorization": f"Bearer {self.config.api_key}"},
                         body)
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            raise LLMError(f"unexpected response shape: {exc}") from exc
        usage = data.get("usage") or {}
        return Completion(
            text=text, model=data.get("model", model), provider=self.name,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0), latency_ms=ms)


@dataclass
class Anthropic:
    config: ProviderConfig

    @property
    def name(self) -> str:
        return self.config.name

    def complete(self, *, model: str, system: str, user: str,
                 max_tokens: int = 4096,
                 temperature: float = 0.0) -> Completion:
        body = {
            "model": model,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "max_tokens": max_tokens,
        }
        if temperature:
            body["temperature"] = temperature

        data, ms = _post(
            f"{self.config.base_url}/messages",
            {"x-api-key": self.config.api_key,
             "anthropic-version": "2023-06-01"},
            body)
        # Content is a list of blocks; a thinking model puts reasoning blocks
        # before the text one, so take the text blocks rather than block zero.
        text = "".join(b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text")
        usage = data.get("usage") or {}
        return Completion(
            text=text, model=data.get("model", model), provider=self.name,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0), latency_ms=ms)


def get(name: str):
    cfg = PROVIDERS.get(name)
    if cfg is None:
        raise ValueError(f"unknown provider: {name}")
    if not cfg.available:
        raise LLMError(f"{name}: {cfg.api_key_env} is not set", retryable=False)
    return Anthropic(cfg) if name == "anthropic" else OpenAICompatible(cfg)


def available() -> list[str]:
    """Vendors this deployment actually has a key for."""
    return [n for n, c in PROVIDERS.items() if c.available]
