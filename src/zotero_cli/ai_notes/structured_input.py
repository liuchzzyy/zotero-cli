"""把 MinerU 块渲染为带来源标注的证据，供一次多模态笔记请求使用。"""

from __future__ import annotations

import base64
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 这是保守的预检，不是服务商的 tokenizer。启用其他路由前，必须先
# 对照实际代理核实其路由限制与图片上限。
_ROUTE_LIMITS = {
    "deepseek-flash": (1_000_000, 4_096),
    "gemini-3.8-flash-high": (1_048_576, 4_096),
    "gpt-5.6-sol": (1_000_000, 4_096),
}
REQUEST_CEILING = 500_000
OUTPUT_RESERVE = 32_768
SAFETY_RESERVE = 50_000
MAX_SI_IMAGES = 30
MAX_REQUEST_BYTES = 20 * 1024 * 1024
# 真实的图表面板不可能只有几百字节；MinerU 确实会输出 1x1 的占位 JPEG。
# 超出此范围的图像会被跳过，并在 prompt 中声明。
MIN_IMAGE_BYTES = 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024
_IMAGE_TYPES = {"image", "chart"}
_SKIP_TYPES = {"header", "footer", "page_number", "page_footnote", "aside_text"}


class EvidenceBudgetError(ValueError):
    """未改动的证据无法装入该请求已核实的限制。"""


@dataclass(frozen=True)
class Evidence:
    text: str
    main_images: list[tuple[int, Path]]
    si_candidates: list[tuple[int, Path]]
    main_count: int
    si_count: int
    attachments: list[str]
    captions: dict[int, str]
    main_text: str


@dataclass(frozen=True)
class RequestPlan:
    model: str
    images: list[tuple[int, str]]
    si_images: list[tuple[int, str]]
    estimated_tokens: int
    text_tokens_upper_bound: int
    request_bytes: int
    image_tokens_reserved: int
    omitted_si: int
    # MinerU 生成但无法附上的图像（1x1 占位图，或超出大小上限的文件）。
    # 它们会在 prompt 中声明，绝不静默丢弃。
    skipped: list[int]


def _blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    if isinstance(content, dict):
        for key in ("content_list", "blocks", "content"):
            if isinstance(content.get(key), list):
                return _blocks(content[key])
    return []


def _join(value: Any) -> str:
    """MinerU 图注以 span 列表形式到达，偶尔为裸字符串。"""
    return " ".join(str(part) for part in value) if isinstance(value, list) else str(value or "")


def build_evidence(documents: list[tuple[str, str, Any]]) -> Evidence:
    """保留有序块、表格 HTML、页码与来源，包括未发送图像的标注。"""
    lines: list[str] = []
    main: list[tuple[int, Path]] = []
    si: list[tuple[int, Path]] = []
    attachments: list[str] = []
    captions: dict[int, str] = {}
    main_lines: list[str] = []
    next_image = 1
    for key, kind, parsed in documents:
        attachments.append(f"{kind}:{key}")
        lines.append(f"\n## {'正文' if kind == 'main' else '支撑信息'} [attachment {key}]")
        section = ""
        raw = parsed.root / "raw"
        safe_root = raw.resolve()
        blocks = _blocks(parsed.content_list)
        if not blocks:
            raise ValueError(f"attachment {key} has no structured blocks")
        for block in blocks:
            typ = str(block.get("type") or "")
            if typ in _SKIP_TYPES:
                continue
            page = block.get("page_idx")
            label = f"[{key} | {kind} | PDF p.{page + 1 if type(page) is int else '?'}"
            text = str(block.get("text") or "").strip()
            if isinstance(block.get("text_level"), int) and text:
                section = text
            if section:
                label += f" | {section}"
            label += "] "
            if typ in _IMAGE_TYPES:
                number = next_image
                next_image += 1
                caption = _join(block.get(f"{typ}_caption"))
                captions[number] = caption
                lines.append(f"{label}【图 {number}】{typ}: {caption}")
                path_value = block.get("img_path")
                if not isinstance(path_value, str) or not path_value:
                    raise ValueError(f"image block without img_path in {key}")
                path = (raw / path_value).resolve()
                if not path.is_relative_to(safe_root):
                    raise ValueError(f"image path escapes cache for {key}")
                (main if kind == "main" else si).append((number, path))
            elif typ == "table":
                caption_text = _join(block.get("table_caption"))
                body = block.get("table_body") or ""
                if body:
                    lines.append(f"{label}{caption_text}\n{body}")
                else:
                    lines.append(f"{label}{caption_text} [表格解析缺失，请勿据此推断数值]")
            elif typ in {"text", "title", "heading", "ref_text", "equation"} and text:
                lines.append(label + text)
                if kind == "main":
                    main_lines.append(text)
    return Evidence("\n".join(lines), main, si, len(main), len(si), attachments, captions, "\n".join(main_lines))


def _si_figure_number(caption: str) -> int | None:
    match = re.search(r"\b(?:Fig(?:ure)?\.?\s*S)(\d+)\b", caption, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _select_si(
    candidates: list[tuple[int, Path]], count: int, *, main_text: str, captions: dict[int, str]
) -> list[tuple[int, Path]]:
    """优先选取正文引用过的 SI 图，其余均匀分散选取。"""
    if count >= len(candidates):
        return candidates
    if count <= 0:
        return []
    referenced: list[int] = []
    for index, (number, _) in enumerate(candidates):
        figure_no = _si_figure_number(captions.get(number, ""))
        if figure_no is not None and re.search(
            rf"\b(?:Fig(?:ure)?\.?\s*S){figure_no}\b", main_text, flags=re.IGNORECASE
        ):
            referenced.append(index)
    chosen = set(referenced[:count])
    # count < len(candidates) 使剩余数量小于待选池，因此步进必定落在不同下标上。
    remaining = count - len(chosen)
    available = [i for i in range(len(candidates)) if i not in chosen]
    if remaining:
        chosen.update(available[math.floor((i + 0.5) * len(available) / remaining)] for i in range(remaining))
    return [value for i, value in enumerate(candidates) if i in chosen]


def plan_request(evidence: Evidence, prompt: str, model: str, *, output_reserve: int = OUTPUT_RESERVE) -> RequestPlan:
    """为一个完整请求做预算；未知路由或缺少正文插图时按失败处理。"""
    if model not in _ROUTE_LIMITS:
        raise ValueError(f"unverified context/image budget for model {model}")
    context, per_image = _ROUTE_LIMITS[model]
    limit = min(context, REQUEST_CEILING)
    # 字符数是保守的文本上限，不是精确的 token 计数。
    text_bound = len(prompt)
    if text_bound + output_reserve + SAFETY_RESERVE >= limit:
        raise EvidenceBudgetError("required text exceeds the model budget; do not truncate evidence")
    image_allowance = limit - text_bound - output_reserve - SAFETY_RESERVE
    if len(evidence.main_images) * per_image > image_allowance:
        raise EvidenceBudgetError("required main images exceed model budget")
    max_si_by_budget = max(0, image_allowance // per_image - len(evidence.main_images))
    selected_si = _select_si(
        evidence.si_candidates,
        min(MAX_SI_IMAGES, max_si_by_budget),
        main_text=evidence.main_text,
        captions=evidence.captions,
    )
    selected = evidence.main_images + selected_si
    required = set(evidence.main_images)
    si_chosen = set(selected_si)
    payload: list[tuple[int, str]] = []
    si_payload: list[tuple[int, str]] = []
    skipped: list[int] = []
    byte_count = len(prompt.encode("utf-8"))
    for number, path in selected:
        if not path.is_file() or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            if (number, path) in required:
                raise ValueError(f"required main image {number} is unavailable")
            # SI 图像是可选/补充材料；跳过而不是让整个请求失败
            skipped.append(number)
            continue
        data = path.read_bytes()
        if not MIN_IMAGE_BYTES <= len(data) <= MAX_IMAGE_BYTES:
            # MinerU 会输出退化占位图（151 px 的面板也已有约 4 kB）；
            # 真实面板不可能只有几百字节。声明即可，不要失败。
            skipped.append(number)
            continue
        mime = "jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else path.suffix.lower().lstrip(".")
        encoded = base64.b64encode(data).decode("ascii")
        byte_count += len(encoded) + 60
        record = (number, f"data:image/{mime};base64,{encoded}")
        payload.append(record)
        if (number, path) in si_chosen:
            si_payload.append(record)
    if byte_count > MAX_REQUEST_BYTES:
        raise EvidenceBudgetError("request body exceeds safe byte limit")
    image_bound = len(payload) * per_image
    return RequestPlan(
        model,
        payload,
        si_payload,
        text_bound + image_bound,
        text_bound,
        byte_count,
        image_bound,
        len(evidence.si_candidates) - len(si_payload),
        skipped,
    )


def plan_with_coverage(evidence: Evidence, prompt: str, model: str) -> tuple[RequestPlan, str, list[int]]:
    """重新规划，直到每一张未发送的图像都在最终 prompt 中被点名。"""
    base_prompt = prompt
    requested_numbers = [number for number, _ in evidence.main_images + evidence.si_candidates]
    omitted_numbers: list[int] = []
    plan = plan_request(evidence, prompt, model)
    while True:
        sent_numbers = {number for number, _ in plan.images}
        current_omitted = [number for number in requested_numbers if number not in sent_numbers]
        if current_omitted == omitted_numbers:
            return plan, prompt, omitted_numbers
        omitted_numbers = current_omitted
        prompt = base_prompt + (
            "\n\n【图像覆盖说明】以下图像只有文字/图注而未附图像，请勿声称直接看过图内曲线、坐标或形貌："
            + "、".join(f"【图 {number}】" for number in omitted_numbers)
        )
        plan = plan_request(evidence, prompt, model)


def model_supports_request(model: str, prompt: str, image_count: int) -> bool:
    """备用模型能否接收这份已预先校验的确切文本与图像集合。"""
    route = _ROUTE_LIMITS.get(model)
    if route is None:
        return False
    context, per_image = route
    limit = min(context, REQUEST_CEILING)
    text_and_reserve = len(prompt) + OUTPUT_RESERVE + SAFETY_RESERVE
    return text_and_reserve < limit and text_and_reserve + image_count * per_image <= limit
