"""请求规划与实际发送共用的消息构造和字节计量。"""

from __future__ import annotations

import json
from typing import Any

from zotero_cli.config import AiNoteConfig

SYSTEM_PROMPT = "你是一位专业的科研文献分析助手，擅长深入分析学术论文并提取关键信息。"


def user_content(prompt: str, figures: list[tuple[int, str]]) -> Any:
    """图号标签与图片逐一绑定。"""
    if not figures:
        return prompt
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for number, data_url in figures:
        content.extend(
            [
                {"type": "text", "text": f"\n\n【图 {number}】"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]
        )
    return content


def build_request(
    prompt: str, figures: list[tuple[int, str]], model: str, config: AiNoteConfig, temperature: float | None = None
) -> dict[str, Any]:
    """构造 SDK 接收的同一份请求参数，防止规划与发送漂移。"""
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content(prompt, figures)},
        ],
        "temperature": config.temperature if temperature is None else temperature,
    }
    if config.max_tokens > 0:
        body["max_tokens"] = config.max_tokens
    if config.reasoning_effort:
        body["reasoning_effort"] = config.reasoning_effort
    return body


def request_bytes(body: dict[str, Any]) -> int:
    """与 httpx2 的 JSON 编码参数一致，包含 base64、标点和元数据。"""
    return len(json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
