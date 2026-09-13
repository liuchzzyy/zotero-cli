from __future__ import annotations

from typing import Any

from openai import OpenAI

from zotero_cli.config import AiNoteConfig

_SYSTEM_PROMPT = "你是一位专业的科研文献分析助手，擅长深入分析学术论文并提取关键信息。"


class AiClient:
    """Minimal OpenAI-compatible chat client for AI note generation."""

    def __init__(self, config: AiNoteConfig) -> None:
        self.config = config
        self._client = OpenAI(base_url=config.base_url, api_key=config.api_key)

    def chat(self, prompt: str, *, temperature: float | None = None) -> str:
        create_kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.config.temperature if temperature is None else temperature,
        }
        if self.config.max_tokens > 0:
            create_kwargs["max_tokens"] = self.config.max_tokens
        response = self._client.chat.completions.create(**create_kwargs)
        content = response.choices[0].message.content
        return (content or "").strip()
