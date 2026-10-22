"""基于 OpenAI 兼容接口、带回退模型链的对话客户端。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from openai import DefaultHttpx2Client, OpenAI

from zotero_cli.config import AiNoteConfig

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = "你是一位专业的科研文献分析助手，擅长深入分析学术论文并提取关键信息。"

# 仅在模型拒绝图像输入时追加：所有文本/表格块仍会被分析，但未看到任何图像像素。
TEXT_ONLY_NOTICE = (
    "\n\n【图像说明】当前模型不支持图像输入，本次请求未附任何图片。"
    "图号与图注仍可作为文字证据；不得声称看过图内曲线、坐标、形貌或颜色。"
)

# 各服务商对同一限制的措辞不同；此处只匹配能力类拒绝。仅提及 "image"
# 的普通错误（载荷过大、URL 错误）不得让请求静默降级为纯文本。
_IMAGE_REJECTION_HINTS = (
    "image input is not supported",
    "image input not supported",
    "does not support image",
    "doesn't support image",
    "image_url is not supported",
    "unsupported image input",
    "vision is not supported",
    "multimodal is not supported",
)


class AiClient:
    """基于 OpenAI 兼容接口、带回退模型链的对话客户端。"""

    def __init__(self, config: AiNoteConfig) -> None:
        self.config = config
        # 由配置控制的请求边界（环境变量 ZOT_AI_TIMEOUT / ZOT_AI_MAX_RETRIES）。
        timeout_seconds = float(config.timeout_seconds)
        max_retries = int(config.max_retries)
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        base_url = (config.base_url or "").strip()
        is_local = "127.0.0.1" in base_url or "localhost" in base_url
        # OpenAI 3.x 内部使用 httpx2；DefaultHttpx2Client 是其公开客户端类，
        # 因此 http_client 的类型与 SDK 签名一致。
        http_client = DefaultHttpx2Client(trust_env=False, timeout=timeout_seconds) if is_local else None
        self._client = OpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
            http_client=http_client,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )
        # 最近一次观测到的服务商用量，仅用于诊断；从不携带载荷内容。
        self.last_usage: dict[str, int] | None = None
        self.last_model: str = ""
        self.usage_history: list[dict[str, Any]] = []

    def get_model_chain(self) -> list[str]:
        chain = [self.config.model] if self.config.model else []
        for m in getattr(self.config, "fallback_models", []):
            if m and m not in chain:
                chain.append(m)
        return chain or ["group/daily", "group/deepseek", "group/advanced"]

    def _user_content(self, prompt: str, figures: list[tuple[int, str]]) -> Any:
        """构造用户消息；提供图片时把图像作为 image 部分附上。"""
        if not figures:
            return prompt
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for number, data_url in figures:
            content.append({"type": "text", "text": f"\n\n【图 {number}】"})
            content.append({"type": "image_url", "image_url": {"url": data_url}})
        return content

    def _send(self, **kwargs: Any) -> str:
        response = self._client.chat.completions.create(**kwargs)
        usage = getattr(response, "usage", None)
        self.last_usage = (
            {
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            }
            if usage is not None
            else None
        )
        self.last_model = str(getattr(response, "model", "") or kwargs.get("model", ""))
        if self.last_usage is not None:
            self.usage_history.append({**self.last_usage, "model": self.last_model})
        choice = response.choices[0]
        # 因长度而停止意味着 note JSON 不完整；它绝不能作为已完成的分析写入，
        # 因此抛出错误而不是返回部分文本。
        if getattr(choice, "finish_reason", None) == "length":
            raise RuntimeError(
                "AI truncated (finish_reason=length): the analysis output hit the "
                "max_tokens limit; raise the output allowance or shorten the prompt"
            )
        return (choice.message.content or "").strip()

    def chat(
        self,
        prompt: str,
        *,
        temperature: float | None = None,
        progress: Callable[[str, str], None] | None = None,
        figures: list[tuple[int, str]] | None = None,
        allowed_models: set[str] | None = None,
    ) -> str:
        models = [model for model in self.get_model_chain() if allowed_models is None or model in allowed_models]
        last_error: Exception | None = None

        for idx, model in enumerate(models):
            # 能力限制按模型各自生效：此处拒绝图像，不能让链中后续的视觉模型也丢失图片。
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
            except Exception as error:
                err_str = str(error).lower()
                # 纯文本模型会拒绝图像部分；改用不带图片的方式重试该模型，
                # 而不是让整次分析失败。
                if attempt_figures and any(hint in err_str for hint in _IMAGE_REJECTION_HINTS):
                    msg = f"模型 {model} 不接受图片输入，改为纯文本重试"
                    logger.warning(msg)
                    if progress:
                        progress("fallback", msg)
                    # 保留所有文本/表格块；只去掉图像部分并明确说明，
                    # 使笔记无法声称拥有视觉证据。
                    create_kwargs["messages"][1]["content"] = prompt + TEXT_ONLY_NOTICE
                    try:
                        return self._send(**create_kwargs)
                    except Exception as text_only_error:
                        msg = f"模型 {model} 纯文本重试亦失败: {type(text_only_error).__name__}: {str(text_only_error)[:80]}"
                        logger.warning(msg)
                        if progress:
                            progress("fallback", msg)
                        last_error = text_only_error
                        continue
                if "reasoning_effort" in create_kwargs and ("reasoning_effort" in err_str or "extra inputs" in err_str):
                    try:
                        return self._send(**{k: v for k, v in create_kwargs.items() if k != "reasoning_effort"})
                    except Exception as inner_e:
                        error = inner_e

                last_error = error
                next_model = models[idx + 1] if idx + 1 < len(models) else None
                if next_model:
                    msg = f"模型 {model} 调用失败 ({type(error).__name__}: {str(error)[:80]})，切换到备用模型 {next_model}"
                    logger.warning(msg)
                    if progress:
                        progress("fallback", msg)
                else:
                    msg = f"模型 {model} 调用失败: {type(error).__name__}: {str(error)[:120]}"
                    logger.error(msg)

        if last_error is not None:
            raise last_error
        raise RuntimeError("无可用模型")
