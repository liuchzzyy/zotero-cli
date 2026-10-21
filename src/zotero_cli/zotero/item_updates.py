"""验证条目元数据的单条和 JSONL 批量更新输入。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

from zotero_cli.output.exit_codes import emit_error


class BatchUpdateRow(TypedDict):
    line: int
    key: str
    fields: dict[str, str]


def _parse_inline_fields(
    *,
    title: str | None,
    date: str | None,
    field: tuple[str, ...],
    json_out: bool,
) -> dict[str, str]:
    fields: dict[str, str] = {}
    if title:
        fields["title"] = title
    if date:
        fields["date"] = date
    for f in field:
        if "=" not in f:
            emit_error(
                "validation_error",
                f"Invalid field format: '{f}'",
                output_json=json_out,
                hint="Use key=value format",
                context="update",
            )
        k, v = f.split("=", 1)
        fields[k] = v
    return fields


def _load_updates_from_jsonl(file_path: Path, *, json_out: bool) -> list[BatchUpdateRow]:
    try:
        raw_lines = file_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        emit_error(
            "validation_error",
            f"Could not read JSONL file '{file_path}': {e}",
            output_json=json_out,
            hint="Provide a UTF-8 JSONL file with one update object per line",
            context="update",
        )

    updates: list[BatchUpdateRow] = []
    for line_no, raw_line in enumerate(raw_lines, 1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as e:
            emit_error(
                "validation_error",
                f"Invalid JSON on line {line_no} of '{file_path}': {e.msg}",
                output_json=json_out,
                hint='Expected JSONL rows like {"key":"ABC123","fields":{"title":"New title"}}',
                context="update",
            )
        if not isinstance(payload, dict):
            emit_error(
                "validation_error",
                f"Line {line_no} of '{file_path}' must be a JSON object",
                output_json=json_out,
                hint='Expected JSONL rows like {"key":"ABC123","fields":{"title":"New title"}}',
                context="update",
            )

        key = payload.get("key")
        fields = payload.get("fields")
        if not isinstance(key, str) or not key.strip():
            emit_error(
                "validation_error",
                f"Line {line_no} of '{file_path}' is missing a non-empty string 'key'",
                output_json=json_out,
                hint='Expected JSONL rows like {"key":"ABC123","fields":{"title":"New title"}}',
                context="update",
            )
        if not isinstance(fields, dict) or not fields:
            emit_error(
                "validation_error",
                f"Line {line_no} of '{file_path}' must contain a non-empty object 'fields'",
                output_json=json_out,
                hint='Expected JSONL rows like {"key":"ABC123","fields":{"title":"New title"}}',
                context="update",
            )

        clean_fields: dict[str, str] = {}
        for field_name, field_value in fields.items():
            if not isinstance(field_name, str) or not field_name.strip():
                emit_error(
                    "validation_error",
                    f"Line {line_no} of '{file_path}' contains an invalid field name",
                    output_json=json_out,
                    hint="Each field name must be a non-empty string",
                    context="update",
                )
            if not isinstance(field_value, str):
                emit_error(
                    "validation_error",
                    f"Line {line_no} of '{file_path}' field '{field_name}' must be a string in batch mode",
                    output_json=json_out,
                    hint="Batch metadata cleanup currently supports string field values only",
                    context="update",
                )
            clean_fields[field_name] = field_value

        updates.append({"line": line_no, "key": key, "fields": clean_fields})

    if not updates:
        emit_error(
            "validation_error",
            f"JSONL file '{file_path}' is empty or has no valid update rows",
            output_json=json_out,
            hint='Expected JSONL rows like {"key":"ABC123","fields":{"title":"New title"}}',
            context="update",
        )

    return updates
