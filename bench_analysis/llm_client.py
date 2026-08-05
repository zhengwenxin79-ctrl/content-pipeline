from __future__ import annotations

import json
import os
import ssl
from dataclasses import dataclass
from urllib.request import Request, urlopen


@dataclass
class LLMResponse:
    provider: str
    model: str
    text: str


class LLMNotConfigured(RuntimeError):
    pass


SSL_CONTEXT = ssl._create_unverified_context()


def _post_json(url: str, headers: dict[str, str], payload: dict, timeout: int = 90) -> dict:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout, context=SSL_CONTEXT) as response:
        return json.loads(response.read().decode("utf-8"))


def _complete_with_provider(provider: str, prompt: str, temperature: float) -> LLMResponse:
    if provider == "deepseek":
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            raise LLMNotConfigured("DEEPSEEK_API_KEY is not set.")
        model = os.environ.get("BENCH_LLM_MODEL", "deepseek-chat")
        payload = {
            "model": model,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "You are a careful benchmark paper analyst. Return strict JSON only."},
                {"role": "user", "content": prompt},
            ],
        }
        data = _post_json(
            "https://api.deepseek.com/v1/chat/completions",
            {"Authorization": f"Bearer {api_key}"},
            payload,
        )
        return LLMResponse(provider=provider, model=model, text=data["choices"][0]["message"]["content"])

    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise LLMNotConfigured("OPENAI_API_KEY is not set.")
        model = os.environ.get("BENCH_LLM_MODEL", "gpt-4o-mini")
        payload = {
            "model": model,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "You are a careful benchmark paper analyst. Return strict JSON only."},
                {"role": "user", "content": prompt},
            ],
        }
        data = _post_json(
            "https://api.openai.com/v1/chat/completions",
            {"Authorization": f"Bearer {api_key}"},
            payload,
        )
        return LLMResponse(provider=provider, model=model, text=data["choices"][0]["message"]["content"])

    raise LLMNotConfigured(f"Unsupported BENCH_LLM_PROVIDER: {provider}")


def complete_json(prompt: str, *, temperature: float = 0.1) -> LLMResponse:
    configured_provider = os.environ.get("BENCH_LLM_PROVIDER", "").strip().lower()
    if configured_provider:
        return _complete_with_provider(configured_provider, prompt, temperature)

    providers = []
    if os.environ.get("DEEPSEEK_API_KEY"):
        providers.append("deepseek")
    if os.environ.get("OPENAI_API_KEY"):
        providers.append("openai")
    if not providers:
        raise LLMNotConfigured("No LLM API key configured. Set DEEPSEEK_API_KEY or OPENAI_API_KEY.")

    last_error: Exception | None = None
    for provider in providers:
        try:
            return _complete_with_provider(provider, prompt, temperature)
        except Exception as exc:
            last_error = exc
            continue
    raise RuntimeError(f"All configured LLM providers failed. Last error: {last_error}")
