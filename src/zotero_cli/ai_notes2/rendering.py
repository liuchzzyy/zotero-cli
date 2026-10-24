"""从规范三链文档派生单篇 Markdown、HTML 与鱼骨状 SVG。

内容只定义一次，其他分支用全局 index 引用。渲染器负责转义，模型不输出
HTML/CSS。线型表示该范围内的支持可靠性：实线须有覆盖该关系的正向评议，
缺少对应审查时默认虚线并标注待核。
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from zotero_cli.ai_notes2.models import Note2Document
from zotero_cli.ai_notes2.validation import resolve_index, validate_document

_META_FIELDS = (
    ("条目信息", ("key", "title", "item_type", "doi")),
    ("PDF", ("source_set_id", "attachment_key", "pdf_role", "role_basis")),
)
_CHAIN_TITLES = {"thought": "思维链", "evidence": "证据链", "review": "审查链"}


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
    """返回线型与说明；只有正向评议覆盖该目标时才用实线。"""
    data = document.to_dict()
    for assessment in data["review"]["claim_assessments"]:
        try:
            target_index = resolve_index(document, assessment["target_ref"])
        except ValueError:
            continue
        if target_index != index:
            continue
        conclusion = assessment["reliability_statement"]["conclusion"]
        if conclusion in {"当前材料内支持较充分", "有支持但须限定范围"}:
            return "solid", conclusion
        return "dashed", conclusion
    return "dashed", "无对应审查，待核"


def render_markdown(document: Note2Document) -> str:
    """按“元信息 → 核心论点 → 分论点 → 支持链／证据／审查 → 就地评议”输出。"""
    data = document.to_dict()
    lines: list[str] = [
        "# 框架分析 - " + str(data["parent_item_key"]),
        "",
        "## 元信息",
        "",
        "| 项目 | 值 |",
        "| --- | --- |",
    ]
    item_inputs = data["inputs"]
    note = item_inputs["source_note"]
    lines.append(f"| 父条目 | `{data['parent_item_key']}` |")
    lines.append(f"| AI 分析笔记 | `{note['note_key']}`；摘要 `{note['text_digest'][:12]}…` |")
    for entry in item_inputs["documents"]:
        aliases = "、".join(entry["attachment_aliases"]) or "无"
        lines.append(
            f"| PDF `{entry['attachment_key']}` | 角色 {entry['pdf_role']}；别名 {aliases}；"
            f"身份 {entry['document_identity_status']}；依据 {entry['role_basis']} |"
        )
    lines.append(
        f"| 运行 | `{data['parent_item_key']}-{data['run_number']:04d}`；对象数 {len(data['object_registry'])} |"
    )
    lines.append("| 文本范围 | PDF 文本、表格与公式文字、图注和脚注，无图片；旧 AI 笔记文本 |")
    lines.extend(["", "## 思维链 - 证据链 - 审查链", ""])

    nodes = data["thought"]["nodes"]
    registry = {entry["object_id"]: entry["index"] for entry in data["object_registry"]}
    children: dict[str, list[dict[str, Any]]] = {}
    for edge in data["thought"]["support_edges"]:
        children.setdefault(edge["to_node_id"], []).append(edge)

    for node in nodes:
        if node["node_type"] != "核心论点":
            continue
        lines.extend(_render_node(document, node, children, registry, level=2))
    if not any(node["node_type"] == "核心论点" for node in nodes):
        lines.extend(["（原文未形成可靠核心论点，见候选记录。）", ""])

    for chain in ("evidence", "review"):
        lines.extend([f"## {_CHAIN_TITLES[chain]}", ""])
        for table, _identity, prefix in (
            ("evidence_records", "evidence_id", "EC-E"),
            ("process_relations", "process_id", "EC-P"),
            ("provenance_groups", "group_id", "EC-G"),
            ("use_groups", "use_group_id", "EC-U"),
            ("mappings", "mapping_id", "EC-M"),
            ("candidate_mappings", "candidate_id", "EC-C"),
            ("background_records", "background_id", "RC-B"),
            ("criterion_sources", "criterion_source_id", "RC-K"),
            ("review_items", "review_id", "RC-Q"),
            ("coverage_records", "coverage_id", "RC-S"),
            ("claim_assessments", "assessment_id", "RC-A"),
            ("change_suggestions", "suggestion_id", "RC-D"),
        ):
            for record in data[chain].get(table, []):
                lines.append(_render_record(record, prefix, registry))
    lines.append("")
    return "\n".join(lines)


def _render_node(
    document: Note2Document,
    node: dict[str, Any],
    children: dict[str, list[dict[str, Any]]],
    registry: dict[str, str],
    *,
    level: int,
) -> list[str]:
    data = document.to_dict()
    index = node["index"]
    lines = [f"{'#' * level} [{node['node_id']}｜{index}] {node['node_type']}｜{node['text']}", ""]
    lines.append(
        f"- 规范字段：`node_type={node['node_type']}`；`attribution={node['attribution']}`；"
        f"`extraction_status={node['extraction_status']}`；`presentation_status={node['presentation_status']}`"
    )
    if node["original_qualifiers"]:
        lines.append("- 原文限定：" + "；".join(node["original_qualifiers"]))
    for location in node["text_locations"]:
        lines.append(f"- 作者表述定位：〔{_location_text(location)}〕")
    lines.append("")

    for edge in children.get(node["node_id"], []):
        style, reason = _reliability(document, edge["index"])
        lines.extend([f"**[{edge['edge_id']}｜{edge['index']}] 通向 {node['node_id']} 的支持链**", ""])
        lines.append(
            f"- 关系：`{registry.get(edge['from_node_id'], edge['from_node_id'])} → "
            f"{registry.get(edge['to_node_id'], edge['to_node_id'])}`；"
            f"`attribution={edge['attribution']}`；`extraction_status={edge['extraction_status']}`；"
            f"`combination={edge['combination']['mode']}`；线型 {'实线' if style == 'solid' else '虚线'}（{reason}）"
        )
        lines.append(f"- 目标部分：{edge['target_part']}")
        lines.append(f"- 推理桥 `bridge`：{edge['bridge']}")
        for location in edge["text_locations"]:
            lines.append(f"- 关系出处：〔{_location_text(location)}〕")
        lines.append("")

        for mapping in data["evidence"]["mappings"]:
            if mapping["target_ref"]["target_id"] == edge["edge_id"]:
                lines.append(
                    f"- 用途关联 [{mapping['mapping_id']}｜{mapping['index']}]："
                    f"`{_ref_label(document, mapping['subject_ref'])} → "
                    f"{_ref_label(document, mapping['target_ref'])}`；"
                    f"`usage_role={mapping['usage_role']}`；`use_topic={mapping['use_topic']}`"
                )
        for question in data["review"]["review_items"]:
            target = question.get("primary_target_ref")
            if target and target["target_id"] == edge["edge_id"]:
                lines.append(_render_record(question, "RC-Q", registry))
        lines.append("")

        source = next((n for n in data["thought"]["nodes"] if n["node_id"] == edge["from_node_id"]), None)
        if source is not None and source["node_type"] != "论证理由":
            lines.extend(_render_node(document, source, children, registry, level=min(level + 1, 4)))
    lines.append("")
    return lines


def _render_record(record: dict[str, Any], prefix: str, registry: dict[str, str]) -> str:
    identity_field = next(key for key in record if key.endswith("_id") and key not in {"target_id"})
    index = record.get("index", "")
    label = f"[{record[identity_field]}｜{index}]" if index else f"[{record[identity_field]}]"
    body = json.dumps(record, ensure_ascii=False, indent=1)
    return f"- **{prefix}** {label}\n\n```json\n{body}\n```"


def render_html(document: Note2Document, *, title: str) -> str:
    """把 Markdown 结构渲染为 Zotero 可显示的 HTML 树，含可见 ID。"""
    markdown = render_markdown(document)
    body: list[str] = []
    for raw in markdown.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            body.append(f"<h{min(level, 6)}>{_esc(line[level:].strip())}</h{min(level, 6)}>")
        elif line.startswith("- "):
            body.append(f"<p>{_esc(line[2:])}</p>")
        elif line.startswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if set("".join(cells)) <= {"-"}:
                continue
            body.append("<p>" + " ｜ ".join(_esc(cell) for cell in cells) + "</p>")
        else:
            body.append(f"<p>{_esc(line)}</p>")
    return (
        f"<h1>{_esc(title)}</h1>\n" + "\n".join(body) + "\n<p>鱼骨图见本地 tree.svg；Zotero 内按可见 ID 查找对象。</p>"
    )


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
            style, reason = _reliability(document, branch["index"])
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
    directory: Path, document: Note2Document, *, title: str, manifest: dict[str, Any]
) -> dict[str, str]:
    """原子写本次产物，返回文件名到 SHA-256 的映射。"""
    from hashlib import sha256

    directory.mkdir(parents=True, exist_ok=True)
    files = {
        "analysis.json": json.dumps(document.to_dict(), ensure_ascii=False, indent=2),
        "note.md": render_markdown(document),
        "note.html": render_html(document, title=title),
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
