"""把 MinerU 块渲染为带来源标注的证据，供一次多模态笔记请求使用。"""

from __future__ import annotations

import base64
import heapq
import re
from pathlib import Path
from typing import Any

from zotero_cli.ai_notes.errors import EvidenceBudgetError
from zotero_cli.ai_notes.images import EncodedImage, encode_image
from zotero_cli.ai_notes.models import Evidence, RequestPlan
from zotero_cli.ai_notes.payloads import build_request, request_bytes
from zotero_cli.config import AiNoteConfig

# 这是保守的预检，不是服务商的 tokenizer。启用其他路由前，必须先
# 对照实际代理核实其路由限制与图片上限。
# 当前清单中的四个路由均支持图像；上下文上限来自 Magpie /v1/models 元数据。
# 每图 4_096 是保守的预检估计值，不代表实际 tokenizer 或服务端图片限制。
_ROUTE_LIMITS = {
    "group/deepseek": (1_000_000, 4_096),
    "antigravity/gemini-3.8-flash": (1_048_576, 4_096),
    "antigravity/claude-sonnet-4-6": (250_000, 4_096),
    "antigravity/claude-opus-4-6-thinking": (250_000, 4_096),
}
# 单请求的字符上限（不是 target）：图多的综述正文 + 50–90 张正文插图会超过
# 500k 的文本+图像合计上界，而按 4096/图的保守估算它们在 1M 上下文路由里仍然安全。
# 800k 是按实测样本留出余量后的单请求上限（XIPR4NR6：91 张正文图 + 172k 字符 prompt
# = 627,745 估算单位），仍远低于 1M 上下文路由与 20 MiB 请求体。
# 各路由的模型输出上限（Magpie /v1/models 的 max_output_tokens）。仅用于截断后的
# 压缩目标；不发送 max_tokens 时由服务端按同一上限约束。
_ROUTE_OUTPUT_LIMITS = {
    "group/deepseek": 128_000,
    "antigravity/gemini-3.8-flash": 65_536,
}
REQUEST_CEILING = 800_000
OUTPUT_RESERVE = 32_768
SAFETY_RESERVE = 50_000
MAX_REQUEST_BYTES = 20 * 1024 * 1024
# 图片有效性以解码为准，1x1 占位图单独声明；不能仅用文件字节数丢图。
MAX_IMAGE_BYTES = 5 * 1024 * 1024
_IMAGE_TYPES = {"image", "chart"}
_SKIP_TYPES = {"header", "footer", "page_number", "page_footnote", "aside_text"}


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
    figure_sources: dict[int, str] = {}
    main_lines: list[str] = []
    ref_lines: list[str] = []
    next_image = 1
    for key, kind, parsed in documents:
        attachments.append(f"{kind}:{key}")
        lines.append(f"\n## {'正文' if kind == 'main' else '支撑信息'} [attachment {key}]")
        section = ""
        attachment_ref_started = False
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
            p_str = f"PDF p.{page + 1 if type(page) is int else '?'}"
            text = str(block.get("text") or "").strip()
            level = block.get("text_level")

            if typ == "ref_text":
                section = "References"
                if text:
                    ref_lines.append(text)
                if not attachment_ref_started:
                    lines.append(f"\n## [{key} | {kind} | {p_str} | References] 参考文献")
                    attachment_ref_started = True
                if text:
                    lines.append(text)
                continue

            if isinstance(level, int) and text:
                section = text
                header_prefix = "#" * max(1, min(level, 3))
                label = f"[{key} | {kind} | {p_str} | {section}] "
                lines.append(f"\n{header_prefix} {label}{text}")
                if kind == "main":
                    main_lines.append(text)
                continue

            label = f"[{key} | {kind} | {p_str}"
            if section:
                label += f" | {section}"
            label += "] "
            if typ in _IMAGE_TYPES:
                number = next_image
                next_image += 1
                caption = _join(block.get(f"{typ}_caption"))
                captions[number] = caption
                figure_sources[number] = label.strip()
                lines.append(f"{label}[图 {number}]{typ}: {caption}")
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
            elif typ == "equation" and text:
                lines.append(f"{label}[独立公式]\n$$\n{text}\n$$")
                if kind == "main":
                    main_lines.append(text)
            elif typ in {"text", "title", "heading"} and text:
                lines.append(label + text)
                if kind == "main":
                    main_lines.append(text)
    return Evidence(
        "\n".join(lines),
        main,
        si,
        len(main),
        len(si),
        attachments,
        captions,
        "\n".join(main_lines),
        figure_sources,
        "\n".join(ref_lines),
    )


def _si_figure_number(caption: str) -> int | None:
    match = re.search(r"\b(?:Fig(?:ure)?\.?\s*S)(\d+)\b", caption, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _payload_bytes(
    prompt: str,
    images: list[tuple[int, str]],
    model: str = "antigravity/gemini-3.8-flash",
    config: AiNoteConfig | None = None,
) -> int:
    """规划和发送共用同一个请求结构。"""
    return request_bytes(build_request(prompt, images, model, config or AiNoteConfig()))


def _image_record(number: int, image: EncodedImage) -> tuple[int, str]:
    return number, f"data:image/{image.mime};base64,{base64.b64encode(image.data).decode('ascii')}"


def _rank_si(evidence: Evidence) -> list[tuple[int, Path]]:
    """正文明确引用优先，其余按区间中点展开，兼顾前中后页码。"""
    referenced = []
    rest = []
    for candidate in evidence.si_candidates:
        number = _si_figure_number(evidence.captions.get(candidate[0], ""))
        if number is not None and re.search(rf"\b(?:Fig(?:ure)?\.?\s*S){number}\b", evidence.main_text, re.IGNORECASE):
            referenced.append(candidate)
        else:
            rest.append(candidate)
    ranked = list(referenced)
    intervals = [(-len(rest), 0, len(rest))] if rest else []
    while intervals:
        _, first, last = heapq.heappop(intervals)
        middle = (first + last) // 2
        ranked.append(rest[middle])
        for left, right in ((first, middle), (middle + 1, last)):
            if right > left:
                heapq.heappush(intervals, (left - right, left, right))
    return ranked


def plan_request(
    evidence: Evidence,
    prompt: str,
    model: str,
    *,
    output_reserve: int = OUTPUT_RESERVE,
    config: AiNoteConfig | None = None,
    excluded_si: set[int] | None = None,
    prepared: dict[tuple[Path, bool], EncodedImage | None] | None = None,
    excluded_reasons: dict[int, str] | None = None,
) -> RequestPlan:
    """全部有效正文图为必需项；SI 由上下文与最终 JSON 字节共同决定。"""
    if model not in _ROUTE_LIMITS:
        raise ValueError(f"unverified context/image budget for model {model}")
    context, per_image = _ROUTE_LIMITS[model]
    reserve = max(output_reserve, config.max_tokens if config else 0)
    limit = min(context, REQUEST_CEILING)
    text_bound = len(prompt)
    image_allowance = limit - text_bound - reserve - SAFETY_RESERVE
    if image_allowance <= 0:
        raise EvidenceBudgetError("required text exceeds the model budget; do not truncate evidence")
    cache = prepared if prepared is not None else {}
    skipped: list[int] = []
    reasons: dict[int, str] = {}
    chosen: dict[int, EncodedImage] = {}

    def load(number: int, path: Path, main: bool, force: bool = False) -> EncodedImage | None:
        if not path.is_file() or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            if main:
                raise ValueError(f"required main image {number} is unavailable")
            reasons[number] = "unavailable"
            return None
        key = (path, force)
        if key not in cache:
            cache[key] = encode_image(path, MAX_IMAGE_BYTES, force=force)
        image = cache[key]
        if image is None:
            if main:
                raise EvidenceBudgetError(
                    f"required main image {number} exceeds the image size limit or cannot be decoded"
                )
            reasons[number] = "invalid_or_oversized"
        elif image.encoding == "placeholder":
            reasons[number] = "placeholder_1x1"
            return None
        return image

    for number, path in evidence.main_images:
        image = load(number, path, True)
        if image is not None:
            chosen[number] = image
        else:
            skipped.append(number)
    if len(chosen) * per_image > image_allowance:
        raise EvidenceBudgetError("required main images exceed model budget")
    payload = [_image_record(n, image) for n, image in chosen.items()]
    # 正文图整体超限时，逐张尝试无损重编码，不能只压最后一张。
    if _payload_bytes(prompt, payload, model, config) > MAX_REQUEST_BYTES:
        for number, path in sorted(
            evidence.main_images,
            key=lambda candidate: -chosen[candidate[0]].source_bytes if candidate[0] in chosen else 0,
        ):
            if number not in chosen:
                continue
            image = load(number, path, True, force=True)
            if image is not None and len(image.data) < len(chosen[number].data):
                chosen[number] = image
        payload = [_image_record(n, image) for n, image in chosen.items()]
    if _payload_bytes(prompt, payload, model, config) > MAX_REQUEST_BYTES:
        raise EvidenceBudgetError("request body exceeds safe byte limit with required main images")
    image_paths = dict(evidence.main_images + evidence.si_candidates)
    si_numbers: set[int] = set()
    for number, path in _rank_si(evidence):
        if number in (excluded_si or set()):
            reasons[number] = (excluded_reasons or {}).get(number, "coverage_budget")
            continue
        if (len(chosen) + 1) * per_image > image_allowance:
            reasons[number] = "context_budget"
            continue
        image = load(number, path, False)
        if image is None:
            skipped.append(number)
            continue
        record = _image_record(number, image)
        if _payload_bytes(prompt, [*payload, record], model, config) > MAX_REQUEST_BYTES:
            # 当前 SI 放不下时也回收已选图片的无损编码空间。
            for chosen_number, chosen_image in list(chosen.items()):
                smaller_chosen = load(chosen_number, image_paths[chosen_number], False, force=True)
                if smaller_chosen is not None and len(smaller_chosen.data) < len(chosen_image.data):
                    chosen[chosen_number] = smaller_chosen
            payload = [_image_record(n, candidate) for n, candidate in chosen.items()]
            smaller = load(number, path, False, force=True)
            if smaller is not None and len(smaller.data) < len(image.data):
                image = smaller
                record = _image_record(number, image)
        if _payload_bytes(prompt, [*payload, record], model, config) > MAX_REQUEST_BYTES:
            reasons[number] = "request_bytes"
            # 未选候选不保留整份字节，避免大量 SI 积累成无界缓存。
            cache.pop((path, False), None)
            cache.pop((path, True), None)
            continue
        chosen[number] = image
        si_numbers.add(number)
        payload.append(record)
    # 发送次序保持附件/图号顺序，选择优先级仅决定哪些 SI 图入选。
    payload.sort(key=lambda record: record[0])
    si_payload = [record for record in payload if record[0] in si_numbers]
    return RequestPlan(
        model,
        payload,
        si_payload,
        text_bound + len(payload) * per_image,
        text_bound,
        _payload_bytes(prompt, payload, model, config),
        len(payload) * per_image,
        len(evidence.si_candidates) - len(si_payload),
        skipped,
        [
            {"number": number, "source": evidence.figure_sources.get(number, ""), **image.manifest()}
            for number, image in sorted(chosen.items())
        ],
        reasons,
    )


def plan_with_coverage(
    evidence: Evidence, prompt: str, model: str, *, config: AiNoteConfig | None = None
) -> tuple[RequestPlan, str, list[int]]:
    """未发送列表单调增长，使说明文本变化后的重新规划有明确终点。"""
    base_prompt = prompt
    requested = [number for number, _ in evidence.main_images + evidence.si_candidates]
    excluded: set[int] = set()
    reasons: dict[int, str] = {}
    cache: dict[tuple[Path, bool], EncodedImage | None] = {}
    for _ in range(len(requested) + 2):
        plan = plan_request(
            evidence, prompt, model, config=config, excluded_si=excluded, prepared=cache, excluded_reasons=reasons
        )
        sent = {number for number, _ in plan.images}
        omitted = [number for number in requested if number not in sent]
        if set(omitted) == excluded:
            return plan, prompt, omitted
        reasons.update(plan.omitted_reasons)
        excluded.update(omitted)
        prompt = base_prompt + (
            "\n\n[图像覆盖说明]以下图像只有文字/图注而未附图像，请勿声称直接看过图内曲线、坐标或形貌："
            + "、".join(f"[图 {number}]" for number in requested if number in excluded)
        )
    raise EvidenceBudgetError("image coverage planning did not converge")


def output_ceiling(model: str) -> int:
    """返回该路由的模型输出上限；未登记的路由按保守预留计算。"""
    return _ROUTE_OUTPUT_LIMITS.get(model, OUTPUT_RESERVE)


def model_supports_request(model: str, prompt: str, image_count: int, *, output_reserve: int = OUTPUT_RESERVE) -> bool:
    """备用路由必须容纳确切文本、图片数量与输出预留。"""
    route = _ROUTE_LIMITS.get(model)
    if route is None:
        return False
    context, per_image = route
    limit = min(context, REQUEST_CEILING)
    text_and_reserve = len(prompt) + output_reserve + SAFETY_RESERVE
    return text_and_reserve < limit and text_and_reserve + image_count * per_image <= limit
