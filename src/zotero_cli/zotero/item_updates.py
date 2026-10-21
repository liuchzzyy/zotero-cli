"""验证条目元数据的单条和 JSONL 批量更新输入。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict


class BatchUpdateRow(TypedDict):
    line: int
    key: str
    fields: dict[str, str]


class UpdateInputError(ValueError):
    """条目更新输入不合法。"""


def _parse_inline_fields(
    *,
    title: str | None,
    date: str | None,
    field: tuple[str, ...],
) -> dict[str, str]:
    fields: dict[str, str] = {}
    if title:
        fields["title"] = title
    if date:
        fields["date"] = date
    for f in field:
        if "=" not in f:
            raise UpdateInputError(f"Invalid field format: '{f}'")
        k, v = f.split("=", 1)
        fields[k] = v
    return fields


def _load_updates_from_jsonl(file_path: Path) -> list[BatchUpdateRow]:
    try:
        raw_lines = file_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise UpdateInputError(f"Could not read JSONL file '{file_path}': {error}")

    updates: list[BatchUpdateRow] = []
    for line_no, raw_line in enumerate(raw_lines, 1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise UpdateInputError(f"Invalid JSON on line {line_no} of '{file_path}': {error.msg}")
        if not isinstance(payload, dict):
            raise UpdateInputError(f"Line {line_no} of '{file_path}' must be a JSON object")

        key = payload.get("key")
        fields = payload.get("fields")
        if not isinstance(key, str) or not key.strip():
            raise UpdateInputError(f"Line {line_no} of '{file_path}' is missing a non-empty string 'key'")
        if not isinstance(fields, dict) or not fields:
            raise UpdateInputError(f"Line {line_no} of '{file_path}' must contain a non-empty object 'fields'")

        clean_fields: dict[str, str] = {}
        for field_name, field_value in fields.items():
            if not isinstance(field_name, str) or not field_name.strip():
                raise UpdateInputError(f"Line {line_no} of '{file_path}' contains an invalid field name")
            if not isinstance(field_value, str):
                raise UpdateInputError(
                    f"Line {line_no} of '{file_path}' field '{field_name}' must be a string in batch mode"
                )
            clean_fields[field_name] = field_value

        updates.append({"line": line_no, "key": key, "fields": clean_fields})

    if not updates:
        raise UpdateInputError(f"JSONL file '{file_path}' is empty or has no valid update rows")

    return updates
