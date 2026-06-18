"""
agents/llm_provider.py
──────────────────────
Unified LLM provider supporting OpenRouter, Claude, OpenAI, Gemini, Groq, Ollama.
Picks the first available provider based on environment keys.

Priority:
  1. OPENROUTER_API_KEY -> OpenRouter free-only model chain
  2. ANTHROPIC_API_KEY  -> Claude (claude-sonnet-4-20250514)
  3. OPENAI_API_KEY     -> OpenAI (gpt-4o-mini)
  4. GEMINI_API_KEY     -> Gemini (gemini-2.0-flash)
  5. GROQ_API_KEY       -> Groq (llama-3.3-70b-versatile)
  6. OLLAMA_BASE_URL    -> Ollama (llama3.2, local)
  7. None               -> Fallback (returns structured stub; agents still work)
"""

from __future__ import annotations

import os
import json
import logging
import asyncio
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("vision_i.llm")

OPENROUTER_FREE_MODELS = [
    "openai/gpt-oss-20b:free",
    "openai/gpt-oss-120b:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "deepseek/deepseek-v4-flash:free",
    "meta-llama/llama-3.3-70b-instruct:free",
]

_PROVIDERS = {
    "openrouter": {
        "env_key": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api",
        "model": OPENROUTER_FREE_MODELS[0],
        "fallback_models": OPENROUTER_FREE_MODELS,
        "call_style": "openai_compat",
        "header_fn": lambda key: {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://vision-i.local",
            "X-Title": "Vision-I Intelligence Platform",
        },
    },
    "claude": {
        "env_key": "ANTHROPIC_API_KEY",
        "base_url": "https://api.anthropic.com",
        "model": "claude-sonnet-4-20250514",
        "call_style": "claude",
        "header_fn": lambda key: {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    },
    "openai": {
        "env_key": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com",
        "model": "gpt-4o-mini",
        "call_style": "openai_compat",
        "header_fn": lambda key: {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    },
    "gemini": {
        "env_key": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com",
        "model": "gemini-2.0-flash",
        "call_style": "gemini",
        "header_fn": lambda key: {"Content-Type": "application/json"},
    },
    "groq": {
        "env_key": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai",
        "model": "llama-3.3-70b-versatile",
        "call_style": "openai_compat",
        "header_fn": lambda key: {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    },
    "ollama": {
        "env_key": "OLLAMA_BASE_URL",
        "base_url": "http://localhost:11434",
        "model": "llama3.2",
        "call_style": "openai_compat",
        "header_fn": lambda key: {"Content-Type": "application/json"},
    },
}

_PROVIDER_ALIASES = {
    "anthropic": "claude",
}


class LLMProvider:
    """
    Async LLM client that auto-detects the best available provider.

    Usage:
        llm = LLMProvider()
        result = await llm.complete(
            system="You are an intelligence analyst.",
            prompt="Summarize these events: ...",
        )
        print(result)  # string response
    """

    def __init__(self) -> None:
        self.runtime_source: str = "environment"
        self.provider: Optional[str] = None
        self.api_key: Optional[str] = None
        self.model: str = ""
        self.fallback_models: List[str] = []
        self.last_model_used: str = ""
        self.base_url: str = ""
        self._headers: Dict[str, str] = {}
        self._client: Optional[httpx.AsyncClient] = None

        self._load_from_environment()

    def _load_from_environment(self) -> None:
        for name, cfg in _PROVIDERS.items():
            env_val = os.getenv(cfg["env_key"], "").strip()

            # Ollama: key is optional; presence of OLLAMA_BASE_URL enables it.
            if name == "ollama":
                base = env_val or cfg["base_url"]
                self.provider = name
                self.api_key = ""
                self.model = os.getenv("LLM_MODEL", cfg["model"])
                self.fallback_models = self._resolve_model_chain(name, self.model)
                self.base_url = base
                self._headers = cfg["header_fn"]("")
                self.runtime_source = "environment"
                # Only use Ollama as fallback if explicitly set; don't auto-select
                if env_val:
                    logger.info("LLM provider: ollama @ %s (model: %s)", base, self.model)
                    return
                else:
                    # Reset; don't lock in Ollama unless explicitly configured.
                    self.provider = None
                    self.api_key = None
                    self.model = ""
                    self.fallback_models = []
                    self.base_url = ""
                    self._headers = {}
                    continue

            if env_val:
                self.provider = name
                self.api_key = env_val
                self.model = os.getenv("LLM_MODEL", cfg["model"])
                self.fallback_models = self._resolve_model_chain(name, self.model)
                self.base_url = cfg["base_url"]
                self._headers = cfg["header_fn"](env_val)
                self.runtime_source = "environment"
                logger.info("LLM provider: %s (model: %s)", name, self.model)
                return

        if not self.provider:
            logger.warning(
                "No LLM API key found (checked OPENROUTER_API_KEY, ANTHROPIC_API_KEY, "
                "OPENAI_API_KEY, GEMINI_API_KEY, GROQ_API_KEY, OLLAMA_BASE_URL). "
                "Agents will use fallback mode."
            )

    @property
    def available(self) -> bool:
        return self.provider is not None

    @staticmethod
    def normalize_provider(provider: str) -> str:
        key = (provider or "").strip().lower()
        return _PROVIDER_ALIASES.get(key, key)

    @staticmethod
    def requires_api_key(provider: str) -> bool:
        return LLMProvider.normalize_provider(provider) != "ollama"

    @staticmethod
    def supported_catalog() -> List[Dict[str, Any]]:
        catalog: List[Dict[str, Any]] = []
        for key, cfg in _PROVIDERS.items():
            catalog.append({
                "key": key,
                "label": (
                    "Anthropic Claude" if key == "claude"
                    else "OpenRouter" if key == "openrouter"
                    else key.title()
                ),
                "aliases": [alias for alias, target in _PROVIDER_ALIASES.items() if target == key],
                "default_model": cfg["model"],
                "default_base_url": cfg["base_url"],
                "requires_api_key": key != "ollama",
                "api_key_label": (
                    "OpenRouter API key" if key == "openrouter"
                    else "API key" if key != "ollama"
                    else "Not required"
                ),
            })
        return catalog

    def apply_runtime_config(
        self,
        provider: str,
        api_key: str,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        provider_key = self.normalize_provider(provider)
        if provider_key not in _PROVIDERS:
            raise ValueError(f"Unsupported provider: {provider}. Valid: {list(_PROVIDERS)}")
        if self.requires_api_key(provider_key) and not (api_key or "").strip():
            raise ValueError(f"{provider_key} requires an API key")

        cfg = _PROVIDERS[provider_key]
        self.provider = provider_key
        self.api_key = (api_key or "").strip()
        self.model = (model or cfg["model"]).strip()
        self.fallback_models = self._resolve_model_chain(provider_key, self.model)
        self.base_url = (base_url or cfg["base_url"]).strip()
        self._headers = cfg["header_fn"](self.api_key)
        self.runtime_source = "runtime"
        logger.info("Runtime LLM config applied: provider=%s model=%s", self.provider, self.model)

    def clear_runtime_config(self) -> None:
        self.provider = None
        self.api_key = None
        self.model = ""
        self.fallback_models = []
        self.last_model_used = ""
        self.base_url = ""
        self._headers = {}
        self.runtime_source = "environment"
        self._load_from_environment()

    def runtime_summary(self) -> Dict[str, Any]:
        return {
            "provider": self.provider or "none",
            "model": self.model or "n/a",
            "models": self.fallback_models,
            "last_model_used": self.last_model_used or None,
            "base_url": self.base_url or None,
            "available": self.available,
            "runtime_source": self.runtime_source,
        }

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
            )
        return self._client

    async def complete(
        self,
        prompt: str,
        system: str = "You are an expert intelligence analyst for the Vision-I global intelligence platform.",
        max_tokens: int = 2048,
        temperature: float = 0.3,
    ) -> str:
        """
        Send a completion request to the active LLM provider.
        Returns the text response, or a fallback message if no provider is configured.
        """
        if not self.available:
            return self._fallback(prompt)

        try:
            client = await self._get_client()
            call_style = _PROVIDERS.get(self.provider or "", {}).get("call_style", "openai_compat")

            if call_style == "claude":
                return await self._call_claude(client, system, prompt, max_tokens, temperature)
            elif call_style == "gemini":
                return await self._call_gemini(client, system, prompt, max_tokens, temperature)
            elif call_style == "openai_compat":
                return await self._call_openai_compat(client, system, prompt, max_tokens, temperature)
            else:
                return self._fallback(prompt)

        except Exception as exc:
            logger.error("LLM call failed (%s): %s", self.provider, exc)
            return self._fallback(prompt)

    async def complete_json(
        self,
        prompt: str,
        system: str = "You are an expert intelligence analyst. Respond ONLY with valid JSON.",
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> Dict[str, Any]:
        """Like complete() but parses the response as JSON."""
        raw = await self.complete(prompt, system, max_tokens, temperature)

        # Try to extract JSON from the response
        try:
            # Handle markdown code blocks
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0].strip()
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0].strip()
            return json.loads(raw)
        except (json.JSONDecodeError, IndexError):
            logger.warning("Failed to parse LLM JSON response, returning raw text")
            return {"raw_response": raw}

    async def test_connection(self) -> Dict[str, Any]:
        if not self.available:
            return {"ok": False, "detail": "No provider configured"}
        try:
            text = await self.complete(
                prompt="Respond with the single word READY.",
                system="You are a connectivity probe. Respond with READY only.",
                max_tokens=16,
                temperature=0.0,
            )
            return {
                "ok": "READY" in text.upper(),
                "detail": text[:120],
                "model_used": self.last_model_used or self.model,
            }
        except Exception as exc:
            return {"ok": False, "detail": str(exc)}

    async def _call_claude(
        self,
        client: httpx.AsyncClient,
        system: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        resp = await client.post(
            f"{self.base_url}/v1/messages",
            headers=self._headers,
            json={
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        # Extract text from content blocks
        blocks = data.get("content", [])
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")

    async def _call_gemini(
        self,
        client: httpx.AsyncClient,
        system: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        url = (
            f"{self.base_url}/v1beta/models/{self.model}:generateContent"
            f"?key={self.api_key}"
        )
        body: Dict[str, Any] = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temperature,
            },
        }
        resp = await client.post(url, headers=self._headers, json=body)
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            return "".join(p.get("text", "") for p in parts)
        return ""

    async def _call_openai_compat(
        self,
        client: httpx.AsyncClient,
        system: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        url = f"{self.base_url}/v1/chat/completions"
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        model_chain = self.fallback_models or [self.model]

        errors: List[str] = []
        for candidate in model_chain:
            try:
                resp = await client.post(
                    url,
                    headers=self._headers,
                    json={
                        "model": candidate,
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                        "messages": messages,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                self.last_model_used = data.get("model") or candidate
                choices = data.get("choices", [])
                if choices:
                    content = choices[0].get("message", {}).get("content")
                    if content:
                        return content
                    errors.append(f"{candidate}: empty response content")
                    logger.warning("LLM model candidate failed (%s): empty response content", candidate)
                    continue
                errors.append(f"{candidate}: no choices returned")
                logger.warning("LLM model candidate failed (%s): no choices returned", candidate)
                continue
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response is not None else "?"
                detail = exc.response.text[:300] if exc.response is not None else str(exc)
                errors.append(f"{candidate}: HTTP {status} {detail}")
                logger.warning(
                    "LLM model candidate failed (%s): HTTP %s %s",
                    candidate,
                    status,
                    detail,
                )
            except Exception as exc:
                errors.append(f"{candidate}: {exc}")
                logger.warning("LLM model candidate failed (%s): %s", candidate, exc)
        raise RuntimeError("All model candidates failed: " + " | ".join(errors[-5:]))

    @staticmethod
    def _resolve_model_chain(provider: str, model: str) -> List[str]:
        if provider == "openrouter":
            return list(OPENROUTER_FREE_MODELS)

        configured = [
            part.strip()
            for raw in (model or "").replace("\n", ",").split(",")
            for part in [raw]
            if part.strip()
        ]
        defaults = list(_PROVIDERS.get(provider, {}).get("fallback_models", []))
        merged: List[str] = []
        for item in configured + defaults:
            if item and item not in merged:
                merged.append(item)
        return merged

    @staticmethod
    def _fallback(prompt: str) -> str:
        """Return a structured stub when no LLM is available."""
        return (
            "[LLM unavailable — no API key configured] "
            "Set OPENROUTER_API_KEY (recommended), ANTHROPIC_API_KEY, OPENAI_API_KEY, "
            "GEMINI_API_KEY, GROQ_API_KEY, or OLLAMA_BASE_URL "
            "to enable AI-powered analysis."
        )

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

