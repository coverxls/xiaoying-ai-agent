from __future__ import annotations

import json
import os
from typing import Any, Protocol

import yaml
from openai import OpenAI

from .mock_scripts import ScenarioMockLLM, ScriptedLLM
from .models import AgentAction


class LLMError(RuntimeError):
    """Raised when the model cannot produce a usable action."""


class LLMClient(Protocol):
    name: str
    model: str

    def complete(self, messages: list[dict[str, str]]) -> str: ...


def key_from_credentials_file(path: str) -> str | None:
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    refs = data.get("refs") or {}
    return refs.get("DEEPSEEK_API_KEY") or refs.get("OPENAI_API_KEY")


def resolve_api_key(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    for name in ("LLM_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        value = os.getenv(name)
        if value:
            return value
    credentials_path = os.getenv("DSH_CREDENTIALS_FILE")
    if credentials_path and os.path.exists(credentials_path):
        return key_from_credentials_file(credentials_path)
    return None


class DeepSeekClient:
    """OpenAI-compatible chat client with JSON-object output."""

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-chat",
        base_url: str = "https://api.deepseek.com",
    ):
        if not api_key:
            raise LLMError("missing LLM API key")
        self.name = "deepseek"
        self.model = model
        self.base_url = base_url
        self.last_response_model: str | None = None
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=90.0,
            max_retries=2,
        )

    def complete(self, messages: list[dict[str, str]]) -> str:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 1600,
            "response_format": {"type": "json_object"},
        }
        try:
            response = self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            text = str(exc).lower()
            if "response_format" in text or "json_object" in text:
                kwargs.pop("response_format", None)
                response = self.client.chat.completions.create(**kwargs)
            else:
                raise LLMError(f"model call failed: {type(exc).__name__}: {exc}") from exc
        self.last_response_model = getattr(response, "model", None)
        content = response.choices[0].message.content if response.choices else None
        if not content:
            raise LLMError("model returned empty content")
        return content


def parse_action(text: str) -> AgentAction:
    """Parse one JSON action, tolerating code fences and short preambles."""
    if not isinstance(text, str) or not text.strip():
        raise LLMError("empty model output")
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].lstrip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise LLMError("model output does not contain a JSON object")
    try:
        raw = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"invalid JSON: {exc}") from exc
    try:
        return AgentAction.from_dict(raw)
    except ValueError as exc:
        raise LLMError(str(exc)) from exc


def build_llm(
    provider: str,
    model: str,
    api_key: str | None = None,
    base_url: str = "https://api.deepseek.com",
    scenario: str = "normal",
) -> LLMClient:
    provider = (provider or "mock").lower()
    if provider == "mock":
        return ScenarioMockLLM(scenario)
    if provider == "scripted":
        raise ValueError("scripted provider is only used directly by tests")
    if provider in ("deepseek", "openai"):
        key = resolve_api_key(api_key)
        if not key:
            raise LLMError(
                "没有找到模型 API key。请设置 LLM_API_KEY，或把 DSH_CREDENTIALS_FILE 指向本地凭据文件。"
            )
        return DeepSeekClient(api_key=key, model=model, base_url=base_url)
    raise ValueError(f"unknown provider {provider!r}")


__all__ = [
    "DeepSeekClient",
    "LLMClient",
    "LLMError",
    "ScenarioMockLLM",
    "ScriptedLLM",
    "build_llm",
    "key_from_credentials_file",
    "parse_action",
    "resolve_api_key",
]
