"""OpenAI-compatible chat client with a multi-model fallback chain."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import httpx
from openai import OpenAI

from zotero_cli.config import AiNoteConfig

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = "你是一位专业的科研文献分析助手，擅长深入分析学术论文并提取关键信息。"

# Providers word the same limitation differently; match loosely.
_IMAGE_REJECTION_HINTS = (
    "image",
    "vision",
    "multimodal",
    "unsupported content",
    "invalid content type",
)


class AiClient:
    """OpenAI-compatible chat client with multi-model fallback chain."""

    def __init__(self, config: AiNoteConfig) -> None:
        self.config = config
        base_url = (config.base_url or "").strip()
        is_local = "127.0.0.1" in base_url or "localhost" in base_url
        http_client = httpx.Client(trust_env=not is_local) if is_local else None
        self._client = OpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
            http_client=http_client,
        )

    def get_model_chain(self) -> list[str]:
        chain = [self.config.model] if self.config.model else []
        for m in getattr(self.config, "fallback_models", []):
            if m and m not in chain:
                chain.append(m)
        return chain or ["deepseek-flash", "gemini-3.8-flash-high", "gpt-5.6-sol"]

    def _user_content(self, prompt: str, figures: list[tuple[int, str]]) -> Any:
        """Build the user message, attaching figures as image parts when given."""
        if not figures:
            return prompt
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for number, data_url in figures:
            content.append({"type": "text", "text": f"\n\n【图 {number}】"})
            content.append({"type": "image_url", "image_url": {"url": data_url}})
        return content

    def _send(self, **kwargs: Any) -> str:
        response = self._client.chat.completions.create(**kwargs)
        return (response.choices[0].message.content or "").strip()

    def chat(
        self,
        prompt: str,
        *,
        temperature: float | None = None,
        progress: Callable[[str, str], None] | None = None,
        figures: list[tuple[int, str]] | None = None,
    ) -> str:
        models = self.get_model_chain()
        last_error: Exception | None = None

        for idx, model in enumerate(models):
            # Capability is per model: an image rejection here must not strip
            # the figures from a vision model later in the chain.
            attempt_figures = figures or []
            create_kwargs: dict[str, Any] = {
                "model": model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": self._user_content(prompt, attempt_figures)},
                ],
                "temperature": self.config.temperature if temperature is None else temperature,
            }
            if self.config.max_tokens > 0:
                create_kwargs["max_tokens"] = self.config.max_tokens
            if getattr(self.config, "reasoning_effort", None):
                create_kwargs["reasoning_effort"] = self.config.reasoning_effort

            try:
                return self._send(**create_kwargs)
            except Exception as e:
                err_str = str(e).lower()
                # A text-only model rejects the image parts; retry this model
                # without them rather than losing the whole analysis.
                if attempt_figures and any(hint in err_str for hint in _IMAGE_REJECTION_HINTS):
                    msg = f"模型 {model} 不接受图片输入，改为纯文本重试"
                    logger.warning(msg)
                    if progress:
                        progress("fallback", msg)
                    create_kwargs["messages"][1]["content"] = prompt
                    try:
                        return self._send(**create_kwargs)
                    except Exception as text_only_error:
                        e = text_only_error
                        err_str = str(e).lower()
                if "reasoning_effort" in create_kwargs and ("reasoning_effort" in err_str or "extra inputs" in err_str):
                    try:
                        return self._send(**{k: v for k, v in create_kwargs.items() if k != "reasoning_effort"})
                    except Exception as inner_e:
                        e = inner_e

                last_error = e
                next_model = models[idx + 1] if idx + 1 < len(models) else None
                if next_model:
                    msg = f"模型 {model} 调用失败 ({type(e).__name__}: {str(e)[:80]})，切换到备用模型 {next_model}"
                    logger.warning(msg)
                    if progress:
                        progress("fallback", msg)
                else:
                    msg = f"模型 {model} 调用失败: {type(e).__name__}: {str(e)[:120]}"
                    logger.error(msg)

        if last_error is not None:
            raise last_error
        raise RuntimeError("无可用模型")
