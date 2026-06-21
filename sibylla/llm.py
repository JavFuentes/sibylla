"""Capa LLM agnóstica de proveedor.

El usuario conecta la API que quiera (sin SDKs de proveedor: solo `requests`):
  - anthropic         (Claude)        -> https://api.anthropic.com/v1/messages
  - openai            (OpenAI)        -> https://api.openai.com/v1/chat/completions
  - openrouter        (multi-modelo)  -> https://openrouter.ai/api/v1/chat/completions
  - openai_compatible (Groq, Together, LM Studio, vLLM, ...) -> {LLM_BASE_URL}/chat/completions
  - ollama            (local, sin key)-> {LLM_BASE_URL|localhost:11434}/api/chat

Configuración por entorno (.env):
  LLM_PROVIDER, LLM_MODEL, LLM_API_KEY, LLM_BASE_URL
"""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger("sibylla")


class LLMError(RuntimeError):
    """Error de configuración o de llamada al proveedor LLM."""


class LLMProvider:
    name = "base"
    default_model = ""

    def __init__(self, model: str, api_key: str | None = None,
                 base_url: str | None = None, timeout: int = 120):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout

    def complete(self, system: str, user: str, *, max_tokens: int = 2000,
                 temperature: float = 0.3) -> str:
        raise NotImplementedError

    @staticmethod
    def _post(url: str, headers: dict, payload: dict, timeout: int) -> dict:
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
        if r.status_code >= 400:
            raise LLMError(f"HTTP {r.status_code} de {url}: {r.text[:400]}")
        return r.json()


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    default_model = "claude-opus-4-8"

    def complete(self, system, user, *, max_tokens=2000, temperature=0.3):
        url = (self.base_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
        headers = {
            "x-api-key": self.api_key or "",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        data = self._post(url, headers, payload, self.timeout)
        return "".join(
            b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
        ).strip()


class OpenAICompatibleProvider(LLMProvider):
    """Cubre OpenAI y cualquier endpoint compatible (OpenRouter, Groq, local...)."""
    name = "openai_compatible"
    default_base = "https://api.openai.com/v1"

    def complete(self, system, user, *, max_tokens=2000, temperature=0.3):
        url = (self.base_url or self.default_base).rstrip("/") + "/chat/completions"
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        data = self._post(url, headers, payload, self.timeout)
        return data["choices"][0]["message"]["content"].strip()


class OllamaProvider(LLMProvider):
    name = "ollama"
    default_base = "http://localhost:11434"

    def complete(self, system, user, *, max_tokens=2000, temperature=0.3):
        url = (self.base_url or self.default_base).rstrip("/") + "/api/chat"
        payload = {
            "model": self.model,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        data = self._post(url, {"content-type": "application/json"}, payload, self.timeout)
        return data.get("message", {}).get("content", "").strip()


_PROVIDERS = {
    "anthropic": AnthropicProvider,
    "claude": AnthropicProvider,
    "openai": OpenAICompatibleProvider,
    "openrouter": OpenAICompatibleProvider,
    "openai_compatible": OpenAICompatibleProvider,
    "ollama": OllamaProvider,
}

_DEFAULT_BASE = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}


def get_provider() -> LLMProvider | None:
    """Construye el proveedor desde el entorno. None si no hay LLM configurado."""
    provider = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if not provider:
        return None
    cls = _PROVIDERS.get(provider)
    if cls is None:
        raise LLMError(f"LLM_PROVIDER '{provider}' no soportado. Opciones: {', '.join(sorted(_PROVIDERS))}")

    model = (os.getenv("LLM_MODEL") or "").strip() or cls.default_model
    if not model:
        raise LLMError(f"Falta LLM_MODEL para el proveedor '{provider}'.")

    api_key = (os.getenv("LLM_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
               or os.getenv("OPENAI_API_KEY") or "").strip() or None
    base_url = (os.getenv("LLM_BASE_URL") or "").strip() or _DEFAULT_BASE.get(provider)

    if provider != "ollama" and not api_key:
        raise LLMError(f"Falta LLM_API_KEY para el proveedor '{provider}'.")

    return cls(model=model, api_key=api_key, base_url=base_url)
