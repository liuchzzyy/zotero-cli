"""Render MinerU blocks as source-labelled evidence for one multimodal note request."""

from __future__ import annotations

import base64
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# This is a conservative preflight, not a provider tokenizer. Route limits and
# image ceilings must be checked against the actual proxy before enabling others.
_ROUTE_LIMITS = {"deepseek-flash": (1_000_000, 4_096), "gemini-3.8-flash-high": (1_048_576, 4_096)}
REQUEST_CEILING = 500_000
OUTPUT_RESERVE = 32_768
SAFETY_RESERVE = 50_000
MAX_SI_IMAGES = 30
MAX_REQUEST_BYTES = 20 * 1024 * 1024
# A real figure panel is never a few hundred bytes; MinerU does emit 1x1 spacer
# JPEGs. Anything outside this window is skipped and announced in the prompt.
MIN_IMAGE_BYTES = 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024
_IMAGE_TYPES = {"image", "chart"}
_SKIP_TYPES = {"header", "footer", "page_number", "page_footnote", "aside_text"}


class EvidenceBudgetError(ValueError):
    """The unchanged evidence cannot fit this request's verified limits."""


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
    # Images MinerU produced that cannot be attached (a 1x1 spacer, or a file
    # beyond the size ceiling). They are announced in the prompt, never dropped
    # silently.
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
    """MinerU captions arrive as a list of spans, occasionally as a bare string."""
    return " ".join(str(part) for part in value) if isinstance(value, list) else str(value or "")


def build_evidence(documents: list[tuple[str, str, Any]]) -> Evidence:
    """Preserve ordered blocks, table HTML, page and source, including unsent-image labels."""
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
    """Take main-text-referenced SI panels first, then spread the rest evenly."""
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
    # count < len(candidates) makes the leftover smaller than the remaining pool,
    # so the stride always lands on distinct indices.
    remaining = count - len(chosen)
    available = [i for i in range(len(candidates)) if i not in chosen]
    if remaining:
        chosen.update(available[math.floor((i + 0.5) * len(available) / remaining)] for i in range(remaining))
    return [value for i, value in enumerate(candidates) if i in chosen]


def plan_request(evidence: Evidence, prompt: str, model: str, *, output_reserve: int = OUTPUT_RESERVE) -> RequestPlan:
    """Budget one complete request; fail closed for unknown routes or missing main figures."""
    if model not in _ROUTE_LIMITS:
        raise ValueError(f"unverified context/image budget for model {model}")
    context, per_image = _ROUTE_LIMITS[model]
    limit = min(context, REQUEST_CEILING)
    # Characters are a conservative text ceiling, not an exact token count.
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
            role = "main" if (number, path) in required else "SI"
            raise ValueError(f"required {role} image {number} is unavailable")
        data = path.read_bytes()
        if not MIN_IMAGE_BYTES <= len(data) <= MAX_IMAGE_BYTES:
            # MinerU emits degenerate spacers (a 151 px panel is already ~4 kB);
            # a real panel is never a few hundred bytes. Announce, do not fail.
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
    """Replan until every unsent figure is named in the final prompt."""
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
    """Whether a fallback can receive the exact preflighted text and image set."""
    route = _ROUTE_LIMITS.get(model)
    if route is None:
        return False
    context, per_image = route
    limit = min(context, REQUEST_CEILING)
    text_and_reserve = len(prompt) + OUTPUT_RESERVE + SAFETY_RESERVE
    return text_and_reserve < limit and text_and_reserve + image_count * per_image <= limit
