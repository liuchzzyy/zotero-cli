"""ai-note2 的独立账本、运行序号预留与远端发布对账。

账本与旧 ai-note 的幂等命名空间分离。写入按 prepared → generated →
validated → publishing → tagging → verified 记录；任何不能确认“肯定未写入”
的失败都保留 committed，并要求先对账，不做盲目重试。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zotero_cli.ai_notes2.errors import AiNote2Error
from zotero_cli.config import state_dir
from zotero_cli.config.runtime import section
from zotero_cli.zotero.access.errors import ZoteroWriteError
from zotero_cli.zotero.access.writer import ZoteroWriter


def __getattr__(name: str) -> Any:
    if name == "EVIDENCE_TAG":
        return section("ai_notes2")["evidence_tag"]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _evidence_tag() -> str:
    return str(getattr(sys.modules[__name__], "EVIDENCE_TAG"))


STATUS_PREPARED = "prepared"
STATUS_GENERATED = "generated"
STATUS_VALIDATED = "validated"
STATUS_PUBLISHING = "publishing"
STATUS_TAGGING = "tagging"
STATUS_VERIFIED = "verified"
STATUS_RECONCILE = "reconcile"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

# 这些状态表示写入可能已生效，必须对账后才能继续。
_UNCERTAIN = frozenset({STATUS_PUBLISHING, STATUS_TAGGING, STATUS_RECONCILE, STATUS_VERIFIED})


def ledger_root() -> Path:
    """账本根目录；位于配置的状态根之下，不进入 Git。"""
    return state_dir() / "ai_note2"


def scope_id(library_type: str, library_id: str, item_key: str) -> str:
    return f"{library_type}:{library_id}:{item_key}"


def _connect() -> sqlite3.Connection:
    root = ledger_root()
    root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(root / "ledger.sqlite", timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS runs ("
        " scope TEXT NOT NULL,"
        " run_number INTEGER NOT NULL,"
        " status TEXT NOT NULL,"
        " input_digest TEXT NOT NULL,"
        " idempotency_key TEXT,"
        " note_key TEXT,"
        " committed INTEGER NOT NULL DEFAULT 0,"
        " data TEXT NOT NULL,"
        " PRIMARY KEY (scope, run_number)"
        ")"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS runs_idempotency"
        " ON runs(scope, idempotency_key) WHERE idempotency_key IS NOT NULL"
    )
    conn.commit()
    return conn


@dataclass(frozen=True)
class Reservation:
    """一次运行的序号预留结果。"""

    run_number: int
    replayed: dict[str, Any] | None = None


def reserve_run(
    scope: str,
    *,
    input_digest: str,
    idempotency_key: str | None,
    force: bool,
) -> Reservation:
    """原子预留运行序号；重放已确认成功，冲突进行中或未知结果。"""
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if idempotency_key:
            row = conn.execute(
                "SELECT * FROM runs WHERE scope = ? AND idempotency_key = ?", (scope, idempotency_key)
            ).fetchone()
            if row is not None:
                if row["input_digest"] != input_digest:
                    raise AiNote2Error(
                        "同一幂等键对应不同输入；拒绝重放旧结果",
                        code="conflict",
                        hint_key="idempotency_conflict",
                    )
                if row["status"] == STATUS_VERIFIED:
                    conn.commit()
                    stored = json.loads(row["data"])
                    return Reservation(run_number=row["run_number"], replayed=stored.get("result", stored))
                if row["status"] != STATUS_FAILED or row["committed"]:
                    raise AiNote2Error(
                        f"该幂等键的上一轮状态为 {row['status']}，必须先核对远端结果",
                        code="conflict",
                        hint_key="pending_mutation",
                    )
                # 确认未写入的失败允许重试，但保留失败序号与历史结果。
                conn.execute(
                    "UPDATE runs SET idempotency_key = NULL WHERE scope = ? AND run_number = ?",
                    (scope, row["run_number"]),
                )
        pending = conn.execute(
            "SELECT run_number, status FROM runs WHERE scope = ? AND (status IN (?, ?, ?) OR (committed = 1 AND status != ?))"
            " ORDER BY run_number DESC LIMIT 1",
            (scope, STATUS_PUBLISHING, STATUS_TAGGING, STATUS_RECONCILE, STATUS_VERIFIED),
        ).fetchone()
        if pending is not None:
            raise AiNote2Error(
                f"条目已有未对账的运行 #{pending['run_number']}（{pending['status']}）；先核对再重试",
                code="conflict",
                hint_key="pending_mutation",
            )
        verified = conn.execute(
            "SELECT run_number FROM runs WHERE scope = ? AND status = ? LIMIT 1",
            (scope, STATUS_VERIFIED),
        ).fetchone()
        if verified is not None and not force:
            raise AiNote2Error(
                f"条目已有成功发布的运行 #{verified['run_number']}；追加新 note 请使用 --force",
                code="conflict",
                hint_key="pending_mutation",
            )
        active = conn.execute(
            "SELECT run_number FROM runs WHERE scope = ? AND status IN (?, ?, ?) ORDER BY run_number DESC LIMIT 1",
            (scope, STATUS_PREPARED, STATUS_GENERATED, STATUS_VALIDATED),
        ).fetchone()
        if active is not None:
            raise AiNote2Error(
                f"条目已有进行中的运行 #{active['run_number']}；完成或核对后再试",
                code="conflict",
                hint_key="pending_mutation",
            )
        row = conn.execute("SELECT MAX(run_number) AS latest FROM runs WHERE scope = ?", (scope,)).fetchone()
        run_number = int(row["latest"] or 0) + 1
        payload = {"run_number": run_number, "input_digest": input_digest}
        conn.execute(
            "INSERT INTO runs (scope, run_number, status, input_digest, idempotency_key, committed, data)"
            " VALUES (?, ?, ?, ?, ?, 0, ?)",
            (scope, run_number, STATUS_PREPARED, input_digest, idempotency_key, json.dumps(payload)),
        )
        conn.commit()
        return Reservation(run_number=run_number)
    except sqlite3.Error as exc:
        conn.rollback()
        raise AiNote2Error("无法预留本次运行", code="configuration_error", hint_key="reservation_failed") from exc
    finally:
        conn.close()


def update_run(
    scope: str,
    run_number: int,
    *,
    status: str | None = None,
    note_key: str | None = None,
    committed: bool | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    """更新运行状态；账本保存失败本身必须由调用方升级为需对账。"""
    conn = _connect()
    try:
        current = conn.execute("SELECT * FROM runs WHERE scope = ? AND run_number = ?", (scope, run_number)).fetchone()
        if current is None:
            raise AiNote2Error("运行记录不存在", code="configuration_error", hint_key="ledger_missing")
        merged = json.loads(current["data"])
        if data:
            merged.update(data)
        conn.execute(
            "UPDATE runs SET status = ?, note_key = ?, committed = ?, data = ? WHERE scope = ? AND run_number = ?",
            (
                status or current["status"],
                note_key or current["note_key"],
                int(current["committed"] if committed is None else committed),
                json.dumps(merged),
                scope,
                run_number,
            ),
        )
        conn.commit()
    except sqlite3.Error as exc:
        raise AiNote2Error(
            "远端结果可能已生效，但本地账本保存失败；请先核对远端",
            code="runtime_error",
            committed=True,
            hint_key="ledger_failed",
        ) from exc
    finally:
        conn.close()


def latest_run(scope: str) -> dict[str, Any] | None:
    """返回该条目的最新运行记录，供冲突核对展示。"""
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM runs WHERE scope = ? ORDER BY run_number DESC LIMIT 1", (scope,)).fetchone()
        return dict(row) if row is not None else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _note_contains_run(note: dict[str, Any], run_number: int, item_key: str) -> bool:
    data = note.get("data", {}) if isinstance(note, dict) else {}
    return (
        data.get("itemType") == "note"
        and data.get("parentItem") == item_key
        and f"{item_key}-{run_number:04d}-" in str(data.get("note", ""))
    )


def reconcile_run(
    writer: ZoteroWriter,
    *,
    scope: str,
    run_number: int,
    item_key: str,
) -> PublishResult:
    """远端查找未知创建结果；只有唯一匹配时继续完成发布。"""
    latest = latest_run(scope)
    if latest is None or latest["run_number"] != run_number or not latest["committed"]:
        raise AiNote2Error("没有待核对的写入记录", code="not_found", hint_key="reconcile_missing")
    note_key = latest.get("note_key")
    if not note_key:
        try:
            notes = writer.read_child_notes(item_key, limit=100)
        except ZoteroWriteError as exc:
            raise AiNote2Error(
                f"读取远端 notes 失败（{exc.code}）；结果仍未知",
                code=exc.code,
                committed=True,
                hint_key="publish_unknown",
            ) from exc
        matches = [note for note in notes if _note_contains_run(note, run_number, item_key)]
        if len(matches) != 1 or not matches[0].get("key"):
            raise AiNote2Error(
                f"远端找到 {len(matches)} 份符合本次运行的 note；不能自动确认创建结果",
                code="conflict",
                committed=True,
                hint_key="publish_unknown",
            )
        note_key = str(matches[0]["key"])
        update_run(scope, run_number, status=STATUS_RECONCILE, note_key=note_key, committed=True)
    return publish_document(
        writer,
        scope=scope,
        run_number=run_number,
        item_key=item_key,
        html_note="",
        reconciled_note_key=str(note_key),
    )


@dataclass(frozen=True)
class PublishResult:
    """发布结果；committed 表示远端可能已有写入。"""

    note_key: str
    tagged: bool
    verified: bool
    needs_reconcile: bool
    message: str


def publish_document(
    writer: ZoteroWriter,
    *,
    scope: str,
    run_number: int,
    item_key: str,
    html_note: str,
    reconciled_note_key: str | None = None,
) -> PublishResult:
    """创建 note、回读确认、追加标签并核对；失败保留明确的对账状态。"""
    note_key = reconciled_note_key
    if note_key is None:
        update_run(scope, run_number, status=STATUS_PUBLISHING, committed=True)
        try:
            note_key = writer.add_note(item_key, html_note)
        except ZoteroWriteError as exc:
            if exc.committed:
                update_run(
                    scope,
                    run_number,
                    status=STATUS_RECONCILE,
                    committed=True,
                    data={"publish_error": exc.code},
                )
                raise AiNote2Error(
                    f"创建 note 的结果未知（{exc.code}）；请先核对远端，不要重复创建",
                    code=exc.code,
                    committed=True,
                    retryable=False,
                    hint_key="publish_unknown",
                ) from exc
            update_run(scope, run_number, status=STATUS_FAILED, committed=False, data={"publish_error": exc.code})
            raise AiNote2Error(str(exc), code=exc.code, committed=False, hint_key="publish_rejected") from exc
        update_run(scope, run_number, status=STATUS_PUBLISHING, note_key=note_key, committed=True)

    try:
        remote = writer.read_item(note_key)
    except ZoteroWriteError as exc:
        update_run(scope, run_number, status=STATUS_RECONCILE, note_key=note_key, committed=True)
        raise AiNote2Error(
            f"note {note_key} 已创建，但回读失败（{exc.code}）；请核对远端",
            code=exc.code,
            committed=True,
            hint_key="publish_unknown",
        ) from exc

    data = remote.get("data", {}) if isinstance(remote, dict) else {}
    if data.get("itemType") != "note" or data.get("parentItem") != item_key:
        update_run(scope, run_number, status=STATUS_RECONCILE, note_key=note_key, committed=True)
        raise AiNote2Error(
            f"note {note_key} 回读内容与本次运行不一致；保留 key 并需人工核对",
            code="runtime_error",
            committed=True,
            hint_key="publish_mismatch",
        )
    if not _note_contains_run(remote, run_number, item_key):
        update_run(
            scope,
            run_number,
            status=STATUS_RECONCILE,
            note_key=note_key,
            committed=True,
            data={"note_verified": False},
        )
        raise AiNote2Error(
            f"note {note_key} 未包含本次运行标识 {item_key}-{run_number:04d}；请核对远端",
            code="runtime_error",
            committed=True,
            hint_key="publish_mismatch",
        )
    update_run(
        scope, run_number, status=STATUS_TAGGING, note_key=note_key, committed=True, data={"note_verified": True}
    )

    try:
        item = writer.read_item(item_key)
        existing = {tag.get("tag") for tag in item.get("data", {}).get("tags", [])}
        tagged = _evidence_tag() in existing
        if not tagged:
            writer.add_tags(item_key, [_evidence_tag()])
            item = writer.read_item(item_key)
            existing = {tag.get("tag") for tag in item.get("data", {}).get("tags", [])}
    except ZoteroWriteError as exc:
        update_run(
            scope,
            run_number,
            status=STATUS_RECONCILE,
            note_key=note_key,
            committed=True,
            data={"tag_error": exc.code},
        )
        raise AiNote2Error(
            f"note {note_key} 已创建，但标签写入或回读失败（{exc.code}）；恢复时只核对并补标签",
            code=exc.code,
            committed=True,
            hint_key="tag_unknown",
        ) from exc
    if _evidence_tag() not in existing:
        update_run(
            scope,
            run_number,
            status=STATUS_RECONCILE,
            note_key=note_key,
            committed=True,
            data={"tag_verified": False},
        )
        raise AiNote2Error(
            f"note {note_key} 已创建，但标签 {_evidence_tag()} 回读缺失；请核对远端",
            code="runtime_error",
            committed=True,
            hint_key="tag_unknown",
        )

    update_run(
        scope,
        run_number,
        status=STATUS_VERIFIED,
        note_key=note_key,
        committed=True,
        data={
            "status": "ok",
            "item_key": item_key,
            "run_number": run_number,
            "note_key": note_key,
            "tag": _evidence_tag(),
            "tagged": True,
            "verified": True,
            "needs_reconcile": False,
        },
    )
    return PublishResult(note_key=note_key, tagged=True, verified=True, needs_reconcile=False, message="")
