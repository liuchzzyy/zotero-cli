"""从规范三链文档派生紧凑总览、分链视图、HTML 与鱼骨状 SVG。

内容只在 `Note2Document` 定义一次，索引与视图只使用运行内身份和对象指针引用。
渲染器负责转义，模型不输出 HTML/CSS。线型表示该范围内的支持可靠性：实线须有覆盖该关系的正向评议，
缺少对应审查时默认虚线并标注待核。
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from zotero_cli.ai_notes2.indexing import build_object_index
from zotero_cli.ai_notes2.models import RECORD_TABLES, Note2Document
from zotero_cli.ai_notes2.validation import resolve_index, validate_document, validate_object_index

_CHAIN_TITLES = {"thought": "思维链", "evidence": "证据链", "review": "审查链"}
_FLAG_LABELS = {
    "candidate": "候选对象",
    "unresolved_candidate": "候选未决",
    "missing_context": "实验或解读上下文缺失",
    "explicitly_not_reported": "原文明确未报告或未测量",
    "unlinked_evidence": "未与论证建立正式用途",
    "candidate_link_only": "仅有候选用途",
    "unassessed_claim": "未有命题评议",
    "source_assessment_attention": "模型来源评估需人工注意",
    "source_assessment_absent": "无模型来源评估",
    "resolved": "已解决历史对象",
    "retired": "已退役历史对象",
}


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _ref_label(document: Note2Document, ref: dict[str, Any]) -> str:
    """把引用渲染成可见全局号，失败时退回原始 ID。"""
    try:
        return resolve_index(document, ref)
    except ValueError:
        return str(ref.get("target_id", ""))


def _location_text(location: dict[str, Any]) -> str:
    page = location.get("page_pdf")
    page_text = f"PDF p.{page}" if page else "PDF p.?"
    path = " > ".join(location.get("section_path") or [])
    parts = [location.get("attachment_key", ""), page_text, location.get("block_id", "")]
    if path:
        parts.append(path)
    excerpt = " ".join((location.get("text_excerpt") or "").split())
    if excerpt:
        parts.append(f"“{excerpt[:160]}”")
    return " / ".join(part for part in parts if part)


def _reliability(document: Note2Document, index: str) -> tuple[str, str]:
    """只在评议和已检查覆盖均明确指向该支持关系时画实线。"""
    data = document.to_dict()
    edge = next((row for row in data["thought"]["support_edges"] if row["index"] == index), None)
    if edge is None:
        return "dashed", "无对应支持关系，待核"
    reviews = {row["review_id"]: row for row in data["review"]["review_items"]}
    coverages = {row["coverage_id"]: row for row in data["review"]["coverage_records"]}
    for assessment in data["review"]["claim_assessments"]:
        if assessment["target_ref"]["target_id"] != edge["to_node_id"] or assessment["unassessed_parts"]:
            continue
        conclusion = assessment["reliability_statement"]["conclusion"]
        if conclusion not in {"当前材料内支持较充分", "有支持但须限定范围"}:
            continue
        if not assessment["supporting_basis"]:
            continue
        reviewed = {
            entry["review_ref"]["target_id"] for entry in assessment["evidence_review"] + assessment["logic_review"]
        }
        for ref in assessment["coverage_refs"]:
            coverage = coverages.get(ref["target_id"])
            if coverage is None or coverage["check_status"] != "已检查":
                continue
            if not any(target["target_id"] == edge["edge_id"] for target in coverage["target_refs"]):
                continue
            for question_ref in coverage["review_refs"]:
                question_id = question_ref["target_id"]
                question = reviews.get(question_id)
                if (
                    question_id in reviewed
                    and question is not None
                    and question["primary_target_ref"]["target_id"] == edge["edge_id"]
                    and question["finding_status"] not in {None, "当前无法判断"}
                ):
                    return "solid", conclusion
    return "dashed", "支持关系尚无完整正向审查，待核"


def _object_index(
    document: Note2Document,
    verification: dict[str, Any] | None,
) -> dict[str, Any]:
    index = build_object_index(document, verification=verification)
    validate_object_index(document, index, verification=verification)
    return index


def _md(value: Any) -> str:
    return " ".join(str(value or "").split()).replace("|", "\\|")


def _source_labels(entry: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for source in entry["source_locations"]:
        page = f"PDF p.{source['page_pdf']}" if source["page_pdf"] else "PDF p.?"
        section = " > ".join(source["section_path"])
        label = f"{source['attachment_key']} / {page} / {source['block_id']}"
        if section:
            label += f" / {section}"
        labels.append(label)
    return labels


def _assessment_label(entry: dict[str, Any]) -> str:
    assessment = entry["model_source_assessment"]
    if assessment["state"] == "absent":
        return "缺失（未推导为通过）"
    note = _md(assessment["note"])
    return f"{assessment['status']}" + (f"：{note}" if note else "")


def _flag_label(flag: str) -> str:
    return _FLAG_LABELS.get(flag, flag)


def _ref_label_text(reference: dict[str, Any]) -> str:
    suffix = ".bridge" if reference.get("target_field") == "bridge" else ""
    return f"{reference['object_id']}{suffix}"


def _relation_label(relation: dict[str, Any]) -> str:
    sources = " + ".join(_ref_label_text(ref) for ref in relation["from_refs"])
    targets = " + ".join(_ref_label_text(ref) for ref in relation["to_refs"])
    members = "、".join(_ref_label_text(member["ref"]) for member in relation["member_refs"])
    path = f"{sources} → {targets}" if sources or targets else members
    return f"{relation['relation_type']} / {relation['status']} / {path or '无端点'}"


def _metadata_lines(data: dict[str, Any], index: dict[str, Any]) -> list[str]:
    lines = [
        f"- 库：`{data['library_ref']['library_type']}:{data['library_ref']['library_id']}`",
        f"- 父条目：`{data['parent_item_key']}`",
        f"- 运行：`{data['run_number']:04d}`；命名空间 `{index['source']['namespace']}`",
        f"- 旧 AI note：`{data['inputs']['source_note']['note_key']}`；摘要 `{data['inputs']['source_note']['text_digest'][:12]}…`",
    ]
    for source in data["inputs"]["documents"]:
        aliases = "、".join(source["attachment_aliases"]) or "无"
        lines.append(
            f"- PDF：`{source['attachment_key']}`；角色 {source['pdf_role']}；别名 {aliases}；"
            f"身份 {source['document_identity_status']}"
        )
    return lines


def render_markdown(document: Note2Document, *, verification: dict[str, Any] | None = None) -> str:
    """生成适合 Zotero 的紧凑总览，不在单文件里展开全部三链字段。"""
    data = document.to_dict()
    index = _object_index(document, verification)
    lines = [
        f"# 框架分析 - {data['parent_item_key']}",
        "",
        "## 运行与边界",
        "",
        *_metadata_lines(data, index),
        "- 权威源：`analysis.json`；本 note 与 `object-index.json` 均为可重建视图。",
        "- 输入只包含文本、表格／公式文字、图注和脚注；未检查图像像素。",
        "- 结构和来源定位通过不表示科学正确；模型来源评估不替代 RC 科学审查。",
        "",
        "## 索引摘要",
        "",
        f"- 对象 {index['counts']['objects']}；类型化关系 {index['counts']['relations']}。",
    ]
    flags = index["counts"]["flags"]
    attention = [
        f"{_flag_label(flag)} {count}"
        for flag, count in flags.items()
        if flag
        in {
            "unresolved_candidate",
            "missing_context",
            "unlinked_evidence",
            "unassessed_claim",
            "source_assessment_attention",
            "source_assessment_absent",
        }
    ]
    lines.append("- 需注意：" + ("；".join(attention) if attention else "无索引级警示"))
    lines.extend(["", "## 类型化连接", ""])
    if index["relations"]:
        for relation in index["relations"]:
            lines.append(
                f"- `{relation['relation_id']}`：{_relation_label(relation)}；依据对象 "
                f"`{relation['source_object_id']}`。"
            )
    else:
        lines.append("- 无已登记关系；不为使图形完整而补边。")
    for chain in ("thought", "evidence", "review"):
        lines.extend(["", f"## {_CHAIN_TITLES[chain]}索引", ""])
        entries = [entry for entry in index["objects"] if entry["chain"] == chain]
        if not entries:
            lines.append("- 无对象。")
            continue
        for entry in entries:
            sources = "；".join(_source_labels(entry)) or "通过对象引用回溯"
            flags_text = "、".join(_flag_label(flag) for flag in entry["flags"]) or "无"
            lines.append(
                f"- **{entry['short_id']}** `{entry['object_type']}`｜{_md(entry['summary'])}；"
                f"状态 {entry['state']}；标记 {flags_text}；来源 {sources}；"
                f"来源评估 {_assessment_label(entry)}；完整对象 `{entry['full_object_pointer']}`。"
            )
    lines.extend(
        [
            "",
            "## 本地详细视图",
            "",
            "- `overview.md`：论文级概览与对象总索引。",
            "- `thought.md` / `evidence.md` / `review.md`：分链字段和来源展开。",
            "- `tree.svg`：辅助图；线型只表示已声明审查范围内的状态。",
            "",
        ]
    )
    return "\n".join(lines)


def render_overview_markdown(
    document: Note2Document,
    *,
    verification: dict[str, Any] | None = None,
) -> str:
    """生成带摘要和紧凑对象表的论文级总览。"""
    data = document.to_dict()
    index = _object_index(document, verification)
    lines = [
        f"# 对象概览 - {data['parent_item_key']} / 运行 {data['run_number']:04d}",
        "",
        *_metadata_lines(data, index),
        f"- 分析摘要：`{index['source']['analysis_sha256']}`",
        f"- 输入快照摘要：`{index['source']['input_snapshot_sha256']}`",
        f"- 核对摘要：`{index['source']['verification_sha256'] or '无'}`",
        "",
        "## 验证语义",
        "",
        "- schema 与对象引用：通过。",
        "- 来源定位与 excerpt：与本次冻结块一致，不代表科学正确。",
        f"- 模型来源评估：{index['validation_scope']['model_source_assessment']}。",
        "- 科学审查：仅由 RC 对象按其判准和范围表达；未检查图像像素。",
        "",
        "## 核心论点",
        "",
    ]
    records = _records_by_id(data)
    cores = [
        entry
        for entry in index["objects"]
        if entry["object_type"] == "TC-N" and records[entry["short_id"]]["node_type"] == "核心论点"
    ]
    lines.extend(
        [f"- **{entry['short_id']}**：{_md(entry['summary'])}" for entry in cores]
        or ["- 未登记核心论点；查看思维候选。"]
    )
    lines.extend(["", "## 类型化关系", "", "| 关系 | 类型／状态／端点 | 来源对象 |", "| --- | --- | --- |"])
    for relation in index["relations"]:
        lines.append(
            f"| `{relation['relation_id']}` | {_md(_relation_label(relation))} | `{relation['source_object_id']}` |"
        )
    if not index["relations"]:
        lines.append("| 无 | 不补建未登记关系 | 无 |")
    lines.extend(
        [
            "",
            "## 对象索引",
            "",
            "| 短 ID | 类型 | 摘要 | 标记 | 来源评估 | 完整对象 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for entry in index["objects"]:
        flags = "、".join(_flag_label(flag) for flag in entry["flags"]) or "无"
        lines.append(
            f"| `{entry['short_id']}` | {entry['type_label']} | {_md(entry['summary'])} | {_md(flags)} | "
            f"{_md(_assessment_label(entry))} | `{entry['full_object_pointer']}` |"
        )
    lines.append("")
    return "\n".join(lines)


def _records_by_id(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for chain, table, identity, _prefix in RECORD_TABLES:
        for record in data[chain][table]:
            records[record[identity]] = record
    return records


def _field_value(value: Any) -> str:
    if value is None:
        return "未提供／不适用"
    if isinstance(value, str):
        return _md(value) or "空字符串"
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return "；".join(_md(item) for item in value) or "无"
    if isinstance(value, dict) and value.get("kind") == "object":
        return _ref_label_text({"object_id": value["target_id"], "target_field": value["target_field"]})
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def render_chain_markdown(
    document: Note2Document,
    chain: str,
    *,
    verification: dict[str, Any] | None = None,
) -> str:
    """按链展开对象字段，跨链内容仅使用 ID 引用。"""
    if chain not in _CHAIN_TITLES:
        raise ValueError(f"未知链：{chain}")
    data = document.to_dict()
    index = _object_index(document, verification)
    records = _records_by_id(data)
    relations = {relation["relation_id"]: relation for relation in index["relations"]}
    lines = [
        f"# {_CHAIN_TITLES[chain]} - {data['parent_item_key']} / 运行 {data['run_number']:04d}",
        "",
        "本文件是 `analysis.json` 的派生视图。完整权威字段以对象指针为准；跨链对象不在此复制。",
        "",
    ]
    entries = [entry for entry in index["objects"] if entry["chain"] == chain]
    if not entries:
        lines.append("（本链无对象。）")
        return "\n".join(lines) + "\n"
    for entry in entries:
        record = records[entry["short_id"]]
        lines.extend(
            [
                f"## [{entry['short_id']}｜{entry['index']}] {entry['type_label']}｜{entry['summary']}",
                "",
                f"- 状态：`{entry['state']}`；完整对象：`analysis.json#{entry['full_object_pointer']}`",
                "- 标记：" + ("、".join(_flag_label(flag) for flag in entry["flags"]) or "无"),
                "- 来源评估：" + _assessment_label(entry),
            ]
        )
        source_labels = _source_labels(entry)
        lines.append("- 来源：" + ("；".join(source_labels) if source_labels else "通过已登记对象引用回溯"))
        if entry["figure_refs"]:
            labels = [f"{figure['attachment_key']} / 图注图号 {figure['figure_id']}" for figure in entry["figure_refs"]]
            lines.append("- 图号引用：" + "；".join(labels) + "（不表示检查了图像像素）")
        related = [relations[identifier] for identifier in entry["relation_ids"]]
        lines.append(
            "- 关系："
            + (
                "；".join(f"`{relation['relation_id']}` {_relation_label(relation)}" for relation in related)
                if related
                else "无已登记类型关系"
            )
        )
        lines.extend(["", "### 结构字段", ""])
        identity = next(key for key in record if key.endswith("_id") and key != "target_id")
        for field, value in record.items():
            if field in {identity, "index", "display", "text_locations", "figure_refs"}:
                continue
            lines.append(f"- `{field}`：{_field_value(value)}")
        lines.append("")
    return "\n".join(lines)


def render_html(document: Note2Document, *, title: str, verification: dict[str, Any] | None = None) -> str:
    """生成 Zotero 可用的静态紧凑 HTML，不依赖脚本、SVG 或锚点。"""
    data = document.to_dict()
    index = _object_index(document, verification)
    body = [
        f"<h1>{_esc(title)}</h1>",
        "<h2>运行与边界</h2>",
        f"<p>库 {_esc(data['library_ref']['library_type'])}:{_esc(data['library_ref']['library_id'])}；"
        f"父条目 {_esc(data['parent_item_key'])}；运行 {data['run_number']:04d}；"
        f"运行标识 {_esc(data['parent_item_key'])}-{data['run_number']:04d}-。</p>",
        "<p><strong>权威源：</strong>analysis.json。本 note 是可重建视图。"
        "输入不含图像像素；结构、来源定位或模型判定都不证明科学正确。</p>",
        f"<p>对象 {index['counts']['objects']}；类型化关系 {index['counts']['relations']}；"
        f"模型来源评估 {index['validation_scope']['model_source_assessment']}。</p>",
        "<h2>类型化连接</h2>",
        "<ul>",
    ]
    if index["relations"]:
        for relation in index["relations"]:
            body.append(
                f"<li><strong>{_esc(relation['relation_id'])}</strong>："
                f"{_esc(_relation_label(relation))}；依据 {_esc(relation['source_object_id'])}。</li>"
            )
    else:
        body.append("<li>无已登记关系；不为图形完整而补边。</li>")
    body.append("</ul>")
    for chain in ("thought", "evidence", "review"):
        body.extend([f"<h2>{_esc(_CHAIN_TITLES[chain])}索引</h2>", "<ul>"])
        entries = [entry for entry in index["objects"] if entry["chain"] == chain]
        if not entries:
            body.append("<li>无对象。</li>")
        for entry in entries:
            flags = "、".join(_flag_label(flag) for flag in entry["flags"]) or "无"
            sources = "；".join(_source_labels(entry)) or "通过对象引用回溯"
            body.append(
                f"<li><strong>{_esc(entry['short_id'])}</strong> [{_esc(entry['object_type'])}]："
                f"{_esc(entry['summary'])}。状态 {_esc(entry['state'])}；标记 {_esc(flags)}；"
                f"来源 {_esc(sources)}；来源评估 {_esc(_assessment_label(entry))}；"
                f"完整对象 {_esc(entry['full_object_pointer'])}。</li>"
            )
        body.append("</ul>")
    body.append("<p>本地 overview.md 和 thought.md / evidence.md / review.md 提供详细视图；tree.svg 仅作辅助图。</p>")
    return "\n".join(body)


def render_svg(document: Note2Document) -> str:
    """生成鱼骨状布局：主干指向鱼头，分论点为主骨，理由与证据为支骨。"""
    data = document.to_dict()
    nodes = {node["node_id"]: node for node in data["thought"]["nodes"]}
    cores = [node for node in nodes.values() if node["node_type"] == "核心论点"]
    branches = [node for node in nodes.values() if node["node_type"] == "分论点"]
    reasons = [node for node in nodes.values() if node["node_type"] == "论证理由"]

    width = max(1000, 320 + 340 * max(1, len(branches)))
    unit_height = 220
    height = max(520, 180 + unit_height * max(1, len(cores)))
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="三链鱼骨图">',
        '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">'
        '<path d="M0,0 L8,4 L0,8 z" fill="#555"/></marker></defs>',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
    ]
    y_center = height // 2
    head_x = 200
    tail_x = width - 60
    parts.append(
        f'<line x1="{tail_x}" y1="{y_center}" x2="{head_x + 160}" y2="{y_center}" '
        'stroke="#999" stroke-width="3" marker-end="url(#arrow)"/>'
    )
    for offset, core in enumerate(cores):
        core_y = y_center + (offset - (len(cores) - 1) / 2) * unit_height
        core_index = core["index"]
        parts.append(
            f'<rect x="{head_x - 160}" y="{core_y - 32}" width="300" height="64" rx="10" fill="#eef3fb" stroke="#33558b"/>'
        )
        parts.append(
            f'<text x="{head_x - 10}" y="{core_y - 8}" font-size="13" text-anchor="middle">'
            f"{_esc(core_index)} 核心论点</text>"
        )
        parts.append(
            f'<text x="{head_x - 10}" y="{core_y + 12}" font-size="11" text-anchor="middle">'
            f"{_esc(core['text'][:34])}</text>"
        )
        parts.append(
            f'<line x1="{head_x + 140}" y1="{core_y}" x2="{tail_x}" y2="{y_center}" stroke="#ccc" stroke-width="1"/>'
        )

        branch_rows = [b for b in branches if _supports(data, b["node_id"], core["node_id"])]
        for position, branch in enumerate(branch_rows):
            bx = head_x + 260 + position * 320
            up = position % 2 == 0
            by = core_y - 130 if up else core_y + 130
            supporting_edge = next(
                edge
                for edge in data["thought"]["support_edges"]
                if edge["from_node_id"] == branch["node_id"] and edge["to_node_id"] == core["node_id"]
            )
            style, reason = _reliability(document, supporting_edge["index"])
            dash = "" if style == "solid" else ' stroke-dasharray="8 6"'
            parts.append(
                f'<line x1="{bx}" y1="{by}" x2="{head_x + 160}" y2="{core_y}" stroke="#2f6f4f" stroke-width="2"{dash}/>'
            )
            parts.append(
                f'<rect x="{bx - 12}" y="{by - 26}" width="250" height="52" rx="8" fill="#f4fbf6" stroke="#2f6f4f"/>'
            )
            parts.append(f'<text x="{bx}" y="{by - 8}" font-size="12">{_esc(branch["index"])} 分论点</text>')
            parts.append(f'<text x="{bx}" y="{by + 10}" font-size="10">{_esc(branch["text"][:26])}</text>')
            parts.append(
                f'<text x="{bx}" y="{by + 22}" font-size="9" fill="#666">'
                f"{'实线' if style == 'solid' else '虚线（待核）'}：{_esc(reason)}</text>"
            )
            for reason_node in [r for r in reasons if _supports(data, r["node_id"], branch["node_id"])]:
                rx = bx + 40
                ry = by - 70 if up else by + 70
                parts.append(
                    f'<line x1="{rx}" y1="{ry}" x2="{bx}" y2="{by}" stroke="#7a8f7f" '
                    'stroke-width="1" stroke-dasharray="4 4"/>'
                )
                parts.append(f'<text x="{rx}" y="{ry}" font-size="9">{_esc(reason_node["index"])} 理由</text>')
        # 审查与候选旁挂，使用细灰线与明确标注，不冒充支持路径。
        for record in data["review"]["review_items"]:
            target = record.get("primary_target_ref")
            if target and target["target_id"] == core["node_id"]:
                parts.append(
                    f'<text x="{head_x - 150}" y="{core_y + 54}" font-size="9" fill="#777">'
                    f"{_esc(record['index'])} 审查旁挂</text>"
                )
    if not cores:
        parts.append('<text x="60" y="60" font-size="14">原文未形成可靠核心论点；候选记录见笔记。</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def _supports(data: dict[str, Any], child: str, parent: str) -> bool:
    return any(
        edge["from_node_id"] == child and edge["to_node_id"] == parent for edge in data["thought"]["support_edges"]
    )


def write_artifacts(
    directory: Path,
    document: Note2Document,
    *,
    title: str,
    manifest: dict[str, Any],
    verification: dict[str, Any] | None = None,
) -> dict[str, str]:
    """原子写本次产物，返回文件名到 SHA-256 的映射。"""
    from hashlib import sha256

    directory.mkdir(parents=True, exist_ok=True)
    object_index = _object_index(document, verification)
    files = {
        "analysis.json": json.dumps(document.to_dict(), ensure_ascii=False, indent=2),
        "object-index.json": json.dumps(object_index, ensure_ascii=False, indent=2),
        "overview.md": render_overview_markdown(document, verification=verification),
        "thought.md": render_chain_markdown(document, "thought", verification=verification),
        "evidence.md": render_chain_markdown(document, "evidence", verification=verification),
        "review.md": render_chain_markdown(document, "review", verification=verification),
        "note.md": render_markdown(document, verification=verification),
        "note.html": render_html(document, title=title, verification=verification),
        "tree.svg": render_svg(document),
        "run.json": json.dumps(manifest, ensure_ascii=False, indent=2),
    }
    digests: dict[str, str] = {}
    staged: list[tuple[Path, Path]] = []
    for name, content in files.items():
        temporary = directory / f".{name}.tmp"
        temporary.write_text(content, encoding="utf-8")
        staged.append((temporary, directory / name))
    for temporary, target in staged:
        temporary.replace(target)
        digests[target.name] = sha256(target.read_bytes()).hexdigest()
    return digests


def validate_before_render(document: dict[str, Any]) -> Note2Document:
    """渲染前重新执行完整结构校验。"""
    return validate_document(document)
