"""本地 Zotero SQLite 读取与引文导出封装。"""

from __future__ import annotations

import html
import json
import re
import shutil
import sqlite3
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Iterator
from difflib import SequenceMatcher
from pathlib import Path

from zotero_cli.zotero.access.errors import ZoteroSchemaError
from zotero_cli.zotero.models import (
    Attachment,
    Collection,
    Creator,
    DuplicateGroup,
    Item,
    Note,
    SearchResult,
)

_HTML_TAG_RE = re.compile(r"<[^>]+>")


_BIBTEX_ESCAPE_RE = re.compile(r"([\\&%#_{}$])")


_BIBTEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "$": r"\$",
}


_YEAR_RE = re.compile(r"\b(\d{4})\b")


_DATE_PARTS_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?")


_BIBTEX_TYPES = {
    "journalArticle": "article",
    "book": "book",
    "bookSection": "incollection",
    "conferencePaper": "inproceedings",
    "thesis": "phdthesis",
    "report": "techreport",
    "manuscript": "unpublished",
    "preprint": "unpublished",
    "webpage": "misc",
    "computerProgram": "misc",
    "blogPost": "misc",
}


_RIS_TYPES = {
    "journalArticle": "JOUR",
    "book": "BOOK",
    "bookSection": "CHAP",
    "conferencePaper": "CONF",
    "thesis": "THES",
    "report": "RPRT",
    "webpage": "ELEC",
    "preprint": "JOUR",
    "patent": "PAT",
    "newspaperArticle": "NEWS",
    "magazineArticle": "MGZN",
    "manuscript": "MANSCPT",
    "computerProgram": "COMP",
    "blogPost": "BLOG",
}


_CSL_TYPES = {
    "journalArticle": "article-journal",
    "book": "book",
    "bookSection": "chapter",
    "conferencePaper": "paper-conference",
    "thesis": "thesis",
    "report": "report",
    "webpage": "webpage",
    "preprint": "article",
    "manuscript": "manuscript",
    "computerProgram": "software",
    "blogPost": "post-weblog",
    "patent": "patent",
}


def _clean_text(value: str) -> str:
    """去除 Zotero 字段值中的 HTML 标记并解码实体。"""
    return html.unescape(_HTML_TAG_RE.sub("", value)).strip()


def _item_year(item: Item) -> str | None:
    """从 Zotero 保存的日期字符串中返回 4 位年份（若存在）。"""
    if not item.date:
        return None
    match = _YEAR_RE.search(item.date)
    return match.group(1) if match else item.date


def _item_date_parts(item: Item) -> list[list[int]] | None:
    """把 Zotero 保存的日期字符串转换为 CSL date-parts。"""
    if not item.date:
        return None
    match = _DATE_PARTS_RE.match(item.date.strip())
    if match is None:
        return None
    parts = [int(match.group(1))]
    for group in (match.group(2), match.group(3)):
        if group is None:
            continue
        value = int(group)
        if value <= 0:
            break
        parts.append(value)
    return [parts]


class CitationExportMixin(ABC):
    """为能够加载条目的读取器提供 BibTeX/CSL-JSON/RIS 渲染能力。"""

    @abstractmethod
    def get_item(self, key: str) -> Item | None:
        """返回 *key* 对应的完整条目（由具体读取器实现）。"""

    def export_citation(self, key: str, fmt: str = "bibtex") -> str | None:
        item = self.get_item(key)
        if item is None:
            return None
        if fmt == "bibtex":
            return self._to_bibtex(item)
        if fmt in ("csl", "csl-json", "json"):
            return self._to_csl_json(item)
        if fmt == "ris":
            return self._to_ris(item)
        return None

    @staticmethod
    def _escape_bibtex(value: str) -> str:
        """转义字段值中的 LaTeX/BibTeX 特殊字符。

        这里只进行单次正则替换，避免为反斜杠转义插入的花括号在后续循环中
        被再次转义。
        """
        return _BIBTEX_ESCAPE_RE.sub(lambda match: _BIBTEX_ESCAPES[match.group(1)], value)

    @staticmethod
    def _to_bibtex(item: Item) -> str:
        bib_type = _BIBTEX_TYPES.get(item.item_type, "misc")
        cite_key = item.key.lower()
        esc = CitationExportMixin._escape_bibtex
        authors = " and ".join(
            f"{esc(_clean_text(c.last_name))}, {esc(_clean_text(c.first_name))}"
            for c in item.creators
            if c.creator_type == "author"
        )
        lines = [f"@{bib_type}{{{cite_key},"]
        title = _clean_text(item.title)
        if title:
            lines.append(f"  title = {{{esc(title)}}},")
        if authors:
            lines.append(f"  author = {{{authors}}},")
        year = _item_year(item)
        if year:
            lines.append(f"  year = {{{esc(year)}}},")
        if item.doi:
            lines.append(f"  doi = {{{esc(item.doi)}}},")
        if item.url:
            lines.append(f"  url = {{{esc(item.url)}}},")
        lines.append("}")
        return "\n".join(lines)

    @staticmethod
    def _to_csl_json(item: Item) -> str:
        """把 Item 转换为 CSL-JSON 格式（单个条目，而非数组）。"""
        csl: dict = {
            "id": item.key,
            "type": _CSL_TYPES.get(item.item_type, "article"),
            "title": _clean_text(item.title),
        }
        authors = [
            {"family": _clean_text(c.last_name), "given": _clean_text(c.first_name)}
            for c in item.creators
            if c.creator_type == "author"
        ]
        if authors:
            csl["author"] = authors
        date_parts = _item_date_parts(item)
        if date_parts:
            csl["issued"] = {"date-parts": date_parts}
        if item.abstract:
            csl["abstract"] = _clean_text(item.abstract)
        if item.doi:
            csl["DOI"] = item.doi
        if item.url:
            csl["URL"] = item.url
        return json.dumps(csl, indent=2, ensure_ascii=False)

    @staticmethod
    def _to_ris(item: Item) -> str:
        """把 Item 转换为 RIS 格式。"""
        lines = [f"TY  - {_RIS_TYPES.get(item.item_type, 'GEN')}"]
        title = _clean_text(item.title)
        if title:
            lines.append(f"TI  - {title}")
        for c in item.creators:
            if c.creator_type == "author":
                lines.append(f"AU  - {_clean_text(c.last_name)}, {_clean_text(c.first_name)}")
        year = _item_year(item)
        if year:
            lines.append(f"PY  - {year}")
        if item.abstract:
            lines.append(f"AB  - {_clean_text(item.abstract)}")
        if item.doi:
            lines.append(f"DO  - {item.doi}")
        if item.url:
            lines.append(f"UR  - {item.url}")
        for tag in item.tags:
            lines.append(f"KW  - {_clean_text(tag)}")
        lines.append("ER  - ")
        return "\n".join(lines)

    @staticmethod
    def _html_to_markdown(html_value: str) -> str:
        from markdownify import markdownify as md

        return md(html_value, strip=["img"]).strip()


_EXCLUDED_TYPE_NAMES = ("attachment", "note", "annotation")


_ITEM_BATCH_SIZE = 900


MIN_SCHEMA_VERSION = 100


MAX_SCHEMA_VERSION = 200


_FULLTEXT_CACHE_FILENAME = ".zotero-ft-cache"


_FULLTEXT_CACHE_MAX_FILES = 4000


_FULLTEXT_CACHE_MAX_BYTES_PER_FILE = 8 * 1024 * 1024


_FULLTEXT_CACHE_MAX_TOTAL_BYTES = 256 * 1024 * 1024


class ZoteroReader(CitationExportMixin):
    def __init__(self, db_path: Path, library_id: int = 1, prefs_js_path: Path | None = None) -> None:
        self._db_path = db_path
        self._library_id = library_id
        self._conn: sqlite3.Connection | None = None
        self._tmp_dir: Path | None = None
        self._excluded_sql: str | None = None
        self._excluded_ids: tuple[int, ...] | None = None
        self._tmp_dir_obj: tempfile.TemporaryDirectory[str] | None = None
        self._fulltext_mode: str | None = None
        self._schema_checked = False
        from zotero_cli.zotero.attachments import AttachmentResolver

        self._resolver = AttachmentResolver(db_path, prefs_js_path)

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        if not self._db_path.exists():
            raise FileNotFoundError(
                f"Zotero database not found: {self._db_path}\n"
                f"  Run 'zot config show' to check your configuration.\n"
                f"  Run 'zot config init --data-dir <path>' to set the correct data directory."
            )
        # immutable=1 会跳过 WAL，因此在 Zotero Desktop 运行期间会返回过期快照：
        # 最近同步的附件看起来丢失，其 PDF 路径也似乎失效。仅在没有待处理 WAL 时
        # 才走这条快速路径；否则从临时副本重放 DB+WAL。
        wal_path = self._db_path.with_suffix(".sqlite-wal")
        try:
            if wal_path.exists() and wal_path.stat().st_size > 0:
                return self._connect_from_copy()
        except OSError:
            pass
        uri_path = self._db_path.as_posix()
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(
                f"file:{uri_path}?mode=ro&immutable=1",
                uri=True,
                timeout=5.0,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("SELECT 1 FROM items LIMIT 1")
            self._validate_schema(conn)
            self._conn = conn
            return conn
        except ZoteroSchemaError:
            if conn is not None:
                conn.close()
            raise
        except sqlite3.Error:
            # 备选方案：把数据库复制到临时文件（例如 immutable 读取遇到损坏时）
            if conn is not None:
                conn.close()
            return self._connect_from_copy()

    def _validate_schema(self, conn: sqlite3.Connection) -> None:
        """在执行 SQL 之前拒绝超出已验证范围的 userdata schema。"""
        if self._schema_checked:
            return
        self._schema_checked = True
        try:
            row = conn.execute("SELECT version FROM version WHERE schema = 'userdata'").fetchone()
        except sqlite3.Error:
            # 无 schema 的测试夹具数据库（以及缺少 version 列的情况）是可以接受的。
            return
        if row is None:
            return
        version = row["version"]
        if not MIN_SCHEMA_VERSION <= version <= MAX_SCHEMA_VERSION:
            raise ZoteroSchemaError(version)

    def _get_excluded_ids(self) -> tuple[int, ...]:
        """按名称查询需排除的类型 ID（首次调用后缓存）。"""
        if self._excluded_ids is not None:
            return self._excluded_ids
        conn = self._connect()
        placeholders = ",".join("?" * len(_EXCLUDED_TYPE_NAMES))
        rows = conn.execute(
            f"SELECT itemTypeID FROM itemTypes WHERE typeName IN ({placeholders})",
            _EXCLUDED_TYPE_NAMES,
        ).fetchall()
        self._excluded_ids = tuple(r["itemTypeID"] for r in rows) if rows else (-1,)
        return self._excluded_ids

    def _get_excluded_sql(self) -> str:
        """构造包含字面量 ID 的 SQL 片段（用于简单的字符串拼接）。"""
        if self._excluded_sql is not None:
            return self._excluded_sql
        ids = self._get_excluded_ids()
        self._excluded_sql = f"NOT IN ({','.join(str(i) for i in ids)})"
        return self._excluded_sql

    def _excluded_filter(self) -> tuple[str, tuple[int, ...]]:
        """返回用于排除类型的（带 ? 占位符的 SQL 片段, 参数元组）。"""
        ids = self._get_excluded_ids()
        ph = ",".join("?" * len(ids))
        return f"NOT IN ({ph})", ids

    def _library_filter(self) -> tuple[str, tuple[int, ...]]:
        """返回将条目查询限定到本库的（SQL 片段, 参数）。

        必须显式过滤：用户库是 libraryID 1，Zotero 群组库有各自的 ID，
        省略该过滤条件会使群组条目混入用户库结果。
        """
        return "AND i.libraryID = ?", (self._library_id,)

    def _connect_from_copy(self) -> sqlite3.Connection:
        """从私有临时副本重放 DB+WAL，以避免 WAL 锁。

        文件在私有临时目录中以原子方式落盘，生成的连接会标记为 query_only，
        确保读取过程绝不会写回副本或正在使用的数据库。
        """
        tmp_dir_obj = tempfile.TemporaryDirectory()
        tmp_dir = Path(tmp_dir_obj.name)
        try:
            conn = self._connect_copy_connection(tmp_dir)
        except BaseException:
            tmp_dir_obj.cleanup()
            raise
        self._tmp_dir_obj = tmp_dir_obj
        self._tmp_dir = tmp_dir
        self._conn = conn
        return conn

    def _connect_copy_connection(self, tmp_dir: Path) -> sqlite3.Connection:
        tmp = tmp_dir / "zotero.sqlite"
        sources: list[tuple[Path, Path]] = [(self._db_path, tmp)]
        for suffix in ("-wal", "-shm"):
            source = Path(f"{self._db_path}{suffix}")
            if source.exists():
                sources.append((source, Path(f"{tmp}{suffix}")))
        for source, target in sources:
            scratch = target.with_name(target.name + ".tmp")
            try:
                shutil.copy2(source, scratch)
                scratch.replace(target)
            except OSError:
                scratch.unlink(missing_ok=True)
                raise
        conn = sqlite3.connect(str(tmp), timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("SELECT 1 FROM items LIMIT 1")
            conn.execute("PRAGMA query_only = ON")
            self._validate_schema(conn)
        except BaseException:
            conn.close()
            raise
        return conn

    def close(self) -> None:
        """关闭 SQLite 连接，并清理所有 WAL 重放副本。"""
        if self._conn:
            self._conn.close()
            self._conn = None
        if hasattr(self, "_tmp_dir_obj") and self._tmp_dir_obj is not None:
            self._tmp_dir_obj.cleanup()
            self._tmp_dir_obj = None
            self._tmp_dir = None

    def __enter__(self) -> ZoteroReader:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def get_schema_version(self) -> int | None:
        """返回 userdata schema 版本；表不存在时返回 None。"""
        conn = self._connect()
        row = conn.execute("SELECT version FROM version WHERE schema = 'userdata'").fetchone()
        return row["version"] if row else None

    def resolve_group_library_id(self, group_id: int) -> int | None:
        """根据公开的 groupID 查询 Zotero 群组对应的 SQLite libraryID。"""
        conn = self._connect()
        row = conn.execute(
            "SELECT libraryID FROM groups WHERE groupID = ?",
            (group_id,),
        ).fetchone()
        return row["libraryID"] if row else None

    def get_item(self, key: str) -> Item | None:
        """按键查询顶级条目，范围限定为本读取器所属的库。"""
        conn = self._connect()
        lib_sql, lib_params = self._library_filter()
        row = conn.execute(
            "SELECT itemID, itemTypeID, key, dateAdded, dateModified "
            "FROM items i WHERE key = ? AND itemTypeID " + self._get_excluded_sql() + " " + lib_sql,
            (key, *lib_params),
        ).fetchone()
        if row is None:
            return None
        item_id = row["itemID"]
        item_type = conn.execute(
            "SELECT typeName FROM itemTypes WHERE itemTypeID = ?",
            (row["itemTypeID"],),
        ).fetchone()["typeName"]
        fields = self._get_item_fields(conn, item_id)
        creators = self._get_item_creators(conn, item_id)
        tags = self._get_item_tags(conn, item_id)
        collections = self._get_item_collections(conn, item_id)
        return Item(
            key=key,
            item_type=item_type,
            title=fields.get("title", ""),
            creators=creators,
            abstract=fields.get("abstractNote"),
            date=fields.get("date"),
            url=fields.get("url"),
            doi=fields.get("DOI"),
            tags=tags,
            collections=collections,
            date_added=row["dateAdded"],
            date_modified=row["dateModified"],
            extra={k: v for k, v in fields.items() if k not in ("title", "abstractNote", "date", "url", "DOI")},
        )

    def search(
        self,
        query: str,
        collection: str | None = None,
        item_type: str | None = None,
        sort: str | None = None,
        direction: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> SearchResult:
        """按标题、摘要、作者、标签或全文搜索条目；不含回收站中的条目。"""
        conn = self._connect()
        item_ids: set[int] = set()
        fulltext_limited = False
        excl_sql, excl_params = self._excluded_filter()
        lib_sql, lib_params = self._library_filter()

        if query:
            like = self._like_pattern(query)
            # 搜索标题、摘要以及所有其他元数据字段值
            rows = conn.execute(
                "SELECT DISTINCT i.itemID FROM items i "
                "JOIN itemData id ON i.itemID = id.itemID "
                "JOIN itemDataValues iv ON id.valueID = iv.valueID "
                f"WHERE iv.value LIKE ? ESCAPE '\\' AND i.itemTypeID {excl_sql} {lib_sql} "
                "AND i.itemID NOT IN (SELECT itemID FROM deletedItems)",
                (like, *excl_params, *lib_params),
            ).fetchall()
            item_ids.update(r["itemID"] for r in rows)

            # 搜索作者
            rows = conn.execute(
                "SELECT DISTINCT ic.itemID FROM itemCreators ic "
                "JOIN creators c ON ic.creatorID = c.creatorID "
                "JOIN items i ON ic.itemID = i.itemID "
                "WHERE (c.firstName LIKE ? ESCAPE '\\' OR c.lastName LIKE ? ESCAPE '\\') "
                f"AND i.itemTypeID {excl_sql} {lib_sql} "
                "AND i.itemID NOT IN (SELECT itemID FROM deletedItems)",
                (like, like, *excl_params, *lib_params),
            ).fetchall()
            item_ids.update(r["itemID"] for r in rows)

            # 搜索标签
            rows = conn.execute(
                "SELECT DISTINCT it.itemID FROM itemTags it "
                "JOIN tags t ON it.tagID = t.tagID "
                "JOIN items i ON it.itemID = i.itemID "
                f"WHERE t.name LIKE ? ESCAPE '\\' AND i.itemTypeID {excl_sql} {lib_sql} "
                "AND i.itemID NOT IN (SELECT itemID FROM deletedItems)",
                (like, *excl_params, *lib_params),
            ).fetchall()
            item_ids.update(r["itemID"] for r in rows)

            # 搜索全文（旧版分词表或 v130 的逐附件缓存）。
            fulltext_ids, fulltext_limited = self._search_fulltext(
                conn, query, excl_sql, excl_params, lib_sql, lib_params
            )
            item_ids.update(fulltext_ids)
        else:
            rows = conn.execute(
                f"SELECT itemID FROM items i WHERE itemTypeID {excl_sql} {lib_sql} "
                "AND i.itemID NOT IN (SELECT itemID FROM deletedItems)",
                (*excl_params, *lib_params),
            ).fetchall()
            item_ids.update(r["itemID"] for r in rows)

        # 按集合过滤（接受键或名称）
        if collection:
            col_row = conn.execute(
                "SELECT collectionID FROM collections WHERE libraryID = ? AND (key = ? OR collectionName = ?)",
                (self._library_id, collection, collection),
            ).fetchone()
            if col_row:
                col_items = conn.execute(
                    "SELECT itemID FROM collectionItems WHERE collectionID = ?",
                    (col_row["collectionID"],),
                ).fetchall()
                col_item_ids = {r["itemID"] for r in col_items}
                item_ids &= col_item_ids
            else:
                raise ValueError(
                    f"Collection '{collection}' not found. Use 'zot zotero collection list' to see available names."
                )

        # 按条目类型过滤（分批处理以遵守 SQLite 的变量数量上限）
        if item_type:
            type_row = conn.execute(
                "SELECT itemTypeID FROM itemTypes WHERE typeName = ?",
                (item_type,),
            ).fetchone()
            if type_row and item_ids:
                typed_ids: set[int] = set()
                for chunk in self._chunks(list(item_ids)):
                    placeholders = ",".join("?" * len(chunk))
                    typed_rows = conn.execute(
                        f"SELECT itemID FROM items WHERE itemTypeID = ? AND itemID IN ({placeholders})",
                        (type_row["itemTypeID"], *chunk),
                    ).fetchall()
                    typed_ids.update(r["itemID"] for r in typed_rows)
                item_ids = typed_ids
            else:
                item_ids = set()

        # 批量解析条目
        total = len(item_ids)

        if sort and item_ids:
            target_ids = self._sorted_item_ids(conn, item_ids, sort, direction, limit, offset)
        else:
            target_ids = sorted(item_ids)[offset : offset + limit]

        items = self._get_items_batch(conn, target_ids) if target_ids else []

        return SearchResult(items=items, total=total, query=query, fulltext_limited=fulltext_limited)

    # --- 搜索辅助函数 ---

    @staticmethod
    def _like_pattern(query: str) -> str:
        """构造把用户输入的未转义通配符当作字面文本处理的 LIKE 模式。"""
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    def _fulltext_schema(self, conn: sqlite3.Connection) -> str:
        """返回该数据库使用的全文 schema（"legacy"/"v130"/"none"）。"""
        if self._fulltext_mode is not None:
            return self._fulltext_mode
        try:
            names = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name IN ('fulltextItemWords', 'fulltextWords', 'fulltextItems')"
                )
            }
        except sqlite3.Error:
            names = set()
        if {"fulltextItemWords", "fulltextWords"} <= names:
            mode = "legacy"
        elif "fulltextItems" in names:
            mode = "v130"
        else:
            mode = "none"
        self._fulltext_mode = mode
        return mode

    def _search_fulltext(
        self,
        conn: sqlite3.Connection,
        query: str,
        excl_sql: str,
        excl_params: tuple[int, ...],
        lib_sql: str,
        lib_params: tuple[int, ...],
    ) -> tuple[set[int], bool]:
        """解析附件全文与查询匹配的条目 ID。

        Zotero 6 数据库把分词后的全文保存在 fulltextWords /
        fulltextItemWords 中；Zotero 7+（userdata v130）只保存 fulltextItems，
        并把提取的文本放在每个附件的 .zotero-ft-cache 文件中。
        两种形式都做了防御性处理：表缺失或缓存不可读时降级为仅搜索元数据，
        而不是崩溃。
        """
        mode = self._fulltext_schema(conn)
        if mode == "legacy":
            try:
                rows = conn.execute(
                    "SELECT DISTINCT ia.parentItemID FROM fulltextItemWords fw "
                    "JOIN fulltextWords w ON fw.wordID = w.wordID "
                    "JOIN itemAttachments ia ON fw.itemID = ia.itemID "
                    "JOIN items i ON ia.parentItemID = i.itemID "
                    f"WHERE w.word LIKE ? ESCAPE '\\' AND ia.parentItemID IS NOT NULL "
                    f"AND i.itemTypeID {excl_sql} {lib_sql} "
                    "AND i.itemID NOT IN (SELECT itemID FROM deletedItems)",
                    (self._like_pattern(query), *excl_params, *lib_params),
                ).fetchall()
            except sqlite3.Error:
                return set(), False
            return {row["parentItemID"] for row in rows}, False
        if mode == "v130":
            return self._cached_fulltext_ids(conn, query, excl_sql, excl_params, lib_sql, lib_params)
        return set(), False

    def _cached_fulltext_ids(
        self,
        conn: sqlite3.Connection,
        query: str,
        excl_sql: str,
        excl_params: tuple[int, ...],
        lib_sql: str,
        lib_params: tuple[int, ...],
    ) -> tuple[set[int], bool]:
        """在有限数量的 .zotero-ft-cache 文件中扫描查询文本。"""
        needle = query.casefold()
        if not needle:
            return set(), False
        base_join = (
            "FROM fulltextItems ft "
            "JOIN itemAttachments ia ON ia.itemID = ft.itemID "
            "JOIN items att ON att.itemID = ft.itemID "
            "JOIN items i ON ia.parentItemID = i.itemID "
            "WHERE ia.parentItemID IS NOT NULL "
            f"AND i.itemTypeID {excl_sql} {lib_sql} "
            "AND i.itemID NOT IN (SELECT itemID FROM deletedItems)"
        )
        try:
            total_indexed = conn.execute(
                f"SELECT COUNT(*) AS count {base_join}",
                (*excl_params, *lib_params),
            ).fetchone()["count"]
            rows = conn.execute(
                "SELECT ia.parentItemID AS parent_id, att.key AS attachment_key "
                f"{base_join} ORDER BY ft.itemID LIMIT ?",
                (*excl_params, *lib_params, _FULLTEXT_CACHE_MAX_FILES),
            ).fetchall()
        except sqlite3.Error:
            return set(), False
        limited = total_indexed > len(rows)
        storage_dir = self._resolver.storage_dir
        matched: set[int] = set()
        scanned_bytes = 0
        for position, row in enumerate(rows):
            cache_path = storage_dir / row["attachment_key"] / _FULLTEXT_CACHE_FILENAME
            try:
                if not cache_path.is_file():
                    continue
                with cache_path.open("rb") as handle:
                    data = handle.read(_FULLTEXT_CACHE_MAX_BYTES_PER_FILE)
            except OSError:
                continue
            scanned_bytes += len(data)
            if needle in data.decode("utf-8", errors="ignore").casefold():
                matched.add(row["parent_id"])
            if scanned_bytes >= _FULLTEXT_CACHE_MAX_TOTAL_BYTES:
                limited = limited or position + 1 < len(rows)
                break
        return matched, limited

    @staticmethod
    def _chunks(values: list[int], size: int | None = None) -> Iterator[list[int]]:
        size = _ITEM_BATCH_SIZE if size is None else size
        """Yield fixed-size slices so IN (...) queries stay under SQLite limits."""
        for start in range(0, len(values), size):
            yield values[start : start + size]

    def _sorted_item_ids(
        self,
        conn: sqlite3.Connection,
        item_ids: set[int],
        sort: str,
        direction: str,
        limit: int,
        offset: int,
    ) -> list[int]:
        """返回按页排序的 ID，并把 IN (...) 查询分批以符合 SQLite 限制。"""
        ids = list(item_ids)
        descending = direction == "desc"
        if sort in ("dateAdded", "dateModified"):
            rows: list[tuple[str, int]] = []
            for chunk in self._chunks(ids):
                placeholders = ",".join("?" * len(chunk))
                rows.extend(
                    (str(row["sort_value"] or ""), row["itemID"])
                    for row in conn.execute(
                        f"SELECT itemID, {sort} AS sort_value FROM items WHERE itemID IN ({placeholders})",
                        chunk,
                    )
                )
            rows.sort(key=lambda row: row[0], reverse=descending)
            return [item_id for _, item_id in rows[offset : offset + limit]]
        if sort == "title":
            title_field = conn.execute("SELECT fieldID FROM fields WHERE fieldName = 'title'").fetchone()
            field_id = title_field["fieldID"] if title_field else 4
            title_rows: list[tuple[str, int]] = []
            for chunk in self._chunks(ids):
                placeholders = ",".join("?" * len(chunk))
                title_rows.extend(
                    (self._nocase_key(str(row["sort_value"] or "")), row["itemID"])
                    for row in conn.execute(
                        "SELECT i.itemID AS itemID, COALESCE(iv_t.value, '') AS sort_value "
                        "FROM items i "
                        "LEFT JOIN itemData id_t ON i.itemID = id_t.itemID AND id_t.fieldID = ? "
                        "LEFT JOIN itemDataValues iv_t ON id_t.valueID = iv_t.valueID "
                        f"WHERE i.itemID IN ({placeholders})",
                        (field_id, *chunk),
                    )
                )
            title_rows.sort(key=lambda row: row[0], reverse=descending)
            return [item_id for _, item_id in title_rows[offset : offset + limit]]
        if sort == "creator":
            creator_rows: list[tuple[str, int]] = []
            for chunk in self._chunks(ids):
                placeholders = ",".join("?" * len(chunk))
                creator_rows.extend(
                    (self._nocase_key(str(row["sort_value"] or "")), row["itemID"])
                    for row in conn.execute(
                        "SELECT i.itemID AS itemID, COALESCE(c.lastName, '') AS sort_value "
                        "FROM items i "
                        "LEFT JOIN itemCreators ic ON i.itemID = ic.itemID AND ic.orderIndex = 0 "
                        "LEFT JOIN creators c ON ic.creatorID = c.creatorID "
                        f"WHERE i.itemID IN ({placeholders})",
                        chunk,
                    )
                )
            creator_rows.sort(key=lambda row: row[0], reverse=descending)
            return [item_id for _, item_id in creator_rows[offset : offset + limit]]
        return sorted(item_ids)[offset : offset + limit]

    @staticmethod
    def _nocase_key(value: str) -> str:
        """近似 SQLite NOCASE 排序规则（仅对 ASCII 做大小写折叠）。"""
        return "".join(chr(ord(char) + 32) if "A" <= char <= "Z" else char for char in value)

    def get_recent_items(
        self,
        since: str,
        sort: str = "dateAdded",
        limit: int = 50,
    ) -> list[Item]:
        """返回在给定时间戳之后新增或修改的条目。"""
        conn = self._connect()
        excl_sql, excl_params = self._excluded_filter()
        lib_sql, lib_params = self._library_filter()
        col = "dateModified" if sort == "dateModified" else "dateAdded"
        rows = conn.execute(
            f"SELECT itemID FROM items i WHERE {col} >= ? AND itemTypeID {excl_sql} {lib_sql} "
            f"AND i.itemID NOT IN (SELECT itemID FROM deletedItems) ORDER BY {col} DESC LIMIT ?",
            (since, *excl_params, *lib_params, limit),
        ).fetchall()
        item_ids = [r["itemID"] for r in rows]
        return self._get_items_batch(conn, item_ids) if item_ids else []

    def get_trash_items(self, limit: int = 50) -> list[Item]:
        """返回回收站中的条目，按删除日期排序（最新的在前）。"""
        conn = self._connect()
        excl_sql, excl_params = self._excluded_filter()
        lib_sql, lib_params = self._library_filter()
        rows = conn.execute(
            f"SELECT i.itemID FROM items i "
            f"JOIN deletedItems d ON i.itemID = d.itemID "
            f"WHERE i.itemTypeID {excl_sql} {lib_sql} "
            f"ORDER BY d.dateDeleted DESC LIMIT ?",
            (*excl_params, *lib_params, limit),
        ).fetchall()
        item_ids = [r["itemID"] for r in rows]
        return self._get_items_batch(conn, item_ids) if item_ids else []

    def find_duplicates(
        self,
        strategy: str = "both",
        threshold: float = 0.85,
        limit: int = 50,
    ) -> list[DuplicateGroup]:
        """按 DOI 和/或标题相似度查找潜在的重复条目。

        候选项限定在本库范围内。回收站中的条目仍可能被包含：
        find_duplicates 遵循文档中 M-01 的混合回收站范围约定，
        因此调用方不应假定存在全局的 deletedItems 过滤。
        标题模糊匹配使用分块键加上低廉的相似度预筛，
        因此不会在大库中比较每一对标题。
        """
        if limit <= 0:
            return []

        conn = self._connect()
        excl_sql, excl_params = self._excluded_filter()
        lib_sql, lib_params = self._library_filter()

        # 加载用于比较的条目（最多取最近 10k 条）。按 M-01，回收站中的条目
        # 保持文档所述的混合范围约定，因此可能被包含在此。
        rows = conn.execute(
            f"SELECT i.itemID, i.key FROM items i WHERE i.itemTypeID {excl_sql} {lib_sql} "
            "ORDER BY i.dateAdded DESC LIMIT 10000",
            (*excl_params, *lib_params),
        ).fetchall()

        item_keys = {r["itemID"]: r["key"] for r in rows}
        item_ids = list(item_keys.keys())
        if not item_ids:
            return []

        groups: list[DuplicateGroup] = []
        seen_group_keys: set[frozenset[str]] = set()

        def _reached_limit() -> bool:
            return len(groups) >= limit

        # --- DOI 策略 ---
        if strategy in ("doi", "both"):
            doi_map: dict[str, list[int]] = {}
            for chunk in self._chunks(item_ids):
                placeholders = ",".join("?" * len(chunk))
                doi_rows = conn.execute(
                    "SELECT id.itemID, iv.value FROM itemData id "
                    "JOIN fields f ON id.fieldID = f.fieldID "
                    "JOIN itemDataValues iv ON id.valueID = iv.valueID "
                    f"WHERE f.fieldName = 'DOI' AND id.itemID IN ({placeholders}) AND iv.value != ''",
                    chunk,
                ).fetchall()
                for row in doi_rows:
                    doi_map.setdefault(row["value"].strip().lower(), []).append(row["itemID"])

            for ids in doi_map.values():
                if len(ids) < 2:
                    continue
                group_key = frozenset(item_keys[i] for i in ids)
                if group_key in seen_group_keys:
                    continue
                seen_group_keys.add(group_key)
                items = self._get_items_batch(conn, ids)
                if len(items) >= 2:
                    groups.append(DuplicateGroup(items=items, match_type="doi", score=1.0))
                    if _reached_limit():
                        return groups

        # --- 标题策略 ---
        if strategy in ("title", "both"):
            title_values: dict[int, str] = {}
            doi_by_item_id: dict[int, str] = {}
            for chunk in self._chunks(item_ids):
                placeholders = ",".join("?" * len(chunk))
                title_rows = conn.execute(
                    "SELECT id.itemID, iv.value FROM itemData id "
                    "JOIN fields f ON id.fieldID = f.fieldID "
                    "JOIN itemDataValues iv ON id.valueID = iv.valueID "
                    f"WHERE f.fieldName = 'title' AND id.itemID IN ({placeholders})",
                    chunk,
                ).fetchall()
                title_values.update({row["itemID"]: row["value"] for row in title_rows})
                doi_rows = conn.execute(
                    "SELECT id.itemID, LOWER(TRIM(iv.value)) AS doi FROM itemData id "
                    "JOIN fields f ON id.fieldID = f.fieldID "
                    "JOIN itemDataValues iv ON id.valueID = iv.valueID "
                    f"WHERE f.fieldName = 'DOI' AND id.itemID IN ({placeholders}) AND TRIM(iv.value) != ''",
                    chunk,
                ).fetchall()
                doi_by_item_id.update({row["itemID"]: row["doi"] for row in doi_rows})

            # 当两条记录带有不同 DOI 时，仅标题匹配并不充分：图书、书评、
            # 译作和会议论文章节常常有几乎相同的标题。保留 DOI 相同的匹配
            # （已由 DOI 策略找到）以及至少一方缺少 DOI 的标题匹配，
            # 但把 DOI 相互冲突的配对作为误报抑制掉。
            def _compatible_title_ids(ids: list[int]) -> bool:
                dois = {doi_by_item_id[item_id] for item_id in ids if item_id in doi_by_item_id}
                return len(dois) <= 1

            normalized_titles = [(item_id, self._normalize_title(title)) for item_id, title in title_values.items()]
            norm_by_id = dict(normalized_titles)

            # 按归一化后的完全一致进行分组（O(n)）
            norm_groups: dict[str, list[int]] = {}
            for item_id, norm in normalized_titles:
                norm_groups.setdefault(norm, []).append(item_id)

            for ids in norm_groups.values():
                if len(ids) >= 2 and _compatible_title_ids(ids):
                    group_key = frozenset(item_keys[i] for i in ids)
                    if group_key not in seen_group_keys:
                        seen_group_keys.add(group_key)
                        items = self._get_items_batch(conn, ids)
                        if len(items) >= 2:
                            groups.append(DuplicateGroup(items=items, match_type="title", score=1.0))
                            if _reached_limit():
                                return groups

            # 只对单元素分组做模糊匹配；分块使复杂度远低于 O(n^2)。
            singletons = [(item_id, norm) for item_id, norm in normalized_titles if len(norm_groups[norm]) == 1]
            candidates = self._duplicate_candidates([norm for _item_id, norm in singletons])
            matched: set[int] = set()
            for idx, (id_a, norm_a) in enumerate(singletons):
                if id_a in matched:
                    continue
                cluster = [id_a]
                candidate_indexes = sorted(index for index in candidates[idx] if index > idx)
                for j in candidate_indexes:
                    id_b, norm_b = singletons[j]
                    if id_b in matched:
                        continue
                    if not self._title_lengths_can_match(norm_a, norm_b, threshold):
                        continue
                    matcher = SequenceMatcher(None, norm_a, norm_b)
                    if matcher.real_quick_ratio() < threshold or matcher.quick_ratio() < threshold:
                        continue
                    ratio = matcher.ratio()
                    if ratio >= threshold:
                        if not _compatible_title_ids([*cluster, id_b]):
                            continue
                        cluster.append(id_b)
                        matched.add(id_b)
                if len(cluster) >= 2:
                    matched.add(id_a)
                    group_key = frozenset(item_keys[cid] for cid in cluster)
                    if group_key not in seen_group_keys:
                        seen_group_keys.add(group_key)
                        items = self._get_items_batch(conn, cluster)
                        best_score = (
                            max(SequenceMatcher(None, norm_a, norm_by_id[cid]).ratio() for cid in cluster[1:])
                            if len(items) >= 2
                            else 0.0
                        )
                        groups.append(DuplicateGroup(items=items, match_type="title", score=round(best_score, 3)))
                        if _reached_limit():
                            return groups

        return groups[:limit]

    @staticmethod
    def _normalize_title(title: str) -> str:
        """为标题匹配转为小写，并去除标点与连续空白。"""
        stripped = re.sub(r"[^\w\s]", "", title.lower()).strip()
        return re.sub(r"\s+", " ", stripped)

    @staticmethod
    def _title_lengths_can_match(norm_a: str, norm_b: str, threshold: float) -> bool:
        """为两个标题字符串给出 SequenceMatcher.ratio() 的廉价上界。"""
        total_len = len(norm_a) + len(norm_b)
        if total_len == 0:
            return True
        max_ratio = 2 * min(len(norm_a), len(norm_b)) / total_len
        return max_ratio >= threshold

    @staticmethod
    def _title_block_keys(normalized: str) -> tuple[str, ...]:
        """近似重复标题可能共有的分块键。"""
        if not normalized:
            return ("empty",)
        keys = ["prefix:" + normalized[:6]]
        tokens = normalized.split()
        if tokens:
            keys.append("first:" + tokens[0])
            keys.append("last:" + tokens[-1])
        return tuple(keys)

    def _duplicate_candidates(self, normalized_titles: list[str]) -> list[set[int]]:
        """使用分块键为每个标题返回候选配对索引。"""
        buckets: dict[str, list[int]] = {}
        for index, normalized in enumerate(normalized_titles):
            for key in self._title_block_keys(normalized):
                buckets.setdefault(key, []).append(index)

        candidates: list[set[int]] = [set() for _ in normalized_titles]
        # 安全阀：分块已经削减了大部分配对，但仍需对异常分块设上限，
        # 使大库绝不会退化回全量扫描。
        max_pairs = 250_000
        pair_count = 0
        for members in buckets.values():
            if len(members) < 2:
                continue
            # 过大的分桶通常是通用前缀（如 "the " 等）；
            # 在配对之前先用更长前缀再细分一次。
            groups = [members]
            if len(members) > 128:
                refined: dict[str, list[int]] = {}
                for index in members:
                    refined.setdefault(normalized_titles[index][:12], []).append(index)
                groups = list(refined.values())
            for group in groups:
                if len(group) > 256:
                    # 异常分桶：只比较限定大小的滑动窗口。
                    group = group[:256]
                for left_pos, left in enumerate(group):
                    for right in group[left_pos + 1 :]:
                        if pair_count >= max_pairs:
                            return candidates
                        candidates[left].add(right)
                        candidates[right].add(left)
                        pair_count += 1
        return candidates

    def get_notes(self, key: str) -> list[Note]:
        """返回条目的注记，跳过回收站中的注记及其父条目在回收站中的情况。"""
        conn = self._connect()
        lib_sql, lib_params = self._library_filter()
        parent = conn.execute(
            f"SELECT itemID FROM items i WHERE key = ? {lib_sql} AND i.itemID NOT IN (SELECT itemID FROM deletedItems)",
            (key, *lib_params),
        ).fetchone()
        if parent is None:
            return []
        rows = conn.execute(
            "SELECT i.itemID, i.key, n.note FROM itemNotes n JOIN items i ON n.itemID = i.itemID "
            "WHERE n.parentItemID = ? AND i.itemID NOT IN (SELECT itemID FROM deletedItems) ORDER BY i.itemID",
            (parent["itemID"],),
        ).fetchall()
        if not rows:
            return []
        # 批量获取所有注记条目的标签
        note_ids = [r["itemID"] for r in rows]
        ph = ",".join("?" * len(note_ids))
        tag_rows = conn.execute(
            f"SELECT it.itemID, t.name FROM itemTags it JOIN tags t ON it.tagID = t.tagID WHERE it.itemID IN ({ph})",
            note_ids,
        ).fetchall()
        tags_by_id: dict[int, list[str]] = {}
        for tr in tag_rows:
            tags_by_id.setdefault(tr["itemID"], []).append(tr["name"])
        notes = []
        for r in rows:
            content = self._html_to_markdown(r["note"] or "")
            tags = tags_by_id.get(r["itemID"], [])
            notes.append(Note(key=r["key"], parent_key=key, content=content, tags=tags))
        return notes

    def get_collections(self) -> list[Collection]:
        """返回库的集合树，子集合嵌套在父集合之下。"""
        conn = self._connect()
        rows = conn.execute(
            "SELECT collectionID, collectionName, parentCollectionID, key FROM collections WHERE libraryID = ?",
            (self._library_id,),
        ).fetchall()
        coll_map: dict[int, Collection] = {}
        parent_map: dict[int, int | None] = {}
        for r in rows:
            coll_map[r["collectionID"]] = Collection(
                key=r["key"],
                name=r["collectionName"],
                parent_key=None,
                children=[],
            )
            parent_map[r["collectionID"]] = r["parentCollectionID"]

        for cid, parent_cid in parent_map.items():
            if parent_cid and parent_cid in coll_map:
                coll_map[cid].parent_key = coll_map[parent_cid].key
                coll_map[parent_cid].children.append(coll_map[cid])

        return [c for cid, c in coll_map.items() if parent_map[cid] is None]

    def get_collection_items(self, collection_key: str) -> list[Item]:
        """返回集合中的顶级条目（不含注记和附件）。"""
        conn = self._connect()
        _, lib_params = self._library_filter()
        col_row = conn.execute(
            "SELECT collectionID FROM collections WHERE key = ? " + ("AND libraryID = ?" if lib_params else ""),
            (collection_key, *lib_params),
        ).fetchone()
        if col_row is None:
            return []
        rows = conn.execute(
            "SELECT i.itemID FROM collectionItems ci "
            "JOIN items i ON ci.itemID = i.itemID "
            "WHERE ci.collectionID = ? AND i.itemTypeID " + self._get_excluded_sql(),
            (col_row["collectionID"],),
        ).fetchall()
        return self._get_items_batch(conn, [r["itemID"] for r in rows])

    def get_attachments(self, key: str) -> list[Attachment]:
        """返回条目的附件，跳过回收站中的附件及其父条目在回收站中的情况。"""
        conn = self._connect()
        lib_sql, lib_params = self._library_filter()
        parent = conn.execute(
            f"SELECT itemID FROM items i WHERE key = ? {lib_sql} AND i.itemID NOT IN (SELECT itemID FROM deletedItems)",
            (key, *lib_params),
        ).fetchone()
        if parent is None:
            return []
        rows = conn.execute(
            "SELECT i.key, ia.contentType, ia.path "
            "FROM itemAttachments ia "
            "JOIN items i ON ia.itemID = i.itemID "
            "WHERE ia.parentItemID = ? AND i.itemID NOT IN (SELECT itemID FROM deletedItems)",
            (parent["itemID"],),
        ).fetchall()
        attachments = []
        for r in rows:
            raw_path = r["path"] or ""
            resolved = self._resolver.resolve(r["key"], raw_path)
            if resolved:
                filename = resolved.name
            elif raw_path.startswith("storage:"):
                parts = raw_path.replace("storage:", "").split("/")
                filename = parts[-1] if parts else ""
            else:
                filename = raw_path.split("/")[-1] if raw_path else ""
            attachments.append(
                Attachment(
                    key=r["key"],
                    parent_key=key,
                    filename=filename,
                    content_type=r["contentType"] or "",
                    path=resolved,
                )
            )
        return attachments

    def get_pdf_attachment(self, key: str) -> Attachment | None:
        """返回条目的第一个 PDF 附件（若存在）。"""
        for att in self.get_attachments(key):
            if att.content_type == "application/pdf":
                return att
        return None

    def get_pdf_attachments(self, key: str) -> list[Attachment]:
        """返回条目的 PDF 附件，并去除解析结果重复的路径。"""
        pdf_attachments: list[Attachment] = []
        seen_paths: set[str] = set()
        for att in self.get_attachments(key):
            if att.content_type != "application/pdf":
                continue
            marker = str(att.path.resolve()) if att.path and att.path.exists() else f"{att.filename}:{att.key}"
            if marker in seen_paths:
                continue
            seen_paths.add(marker)
            pdf_attachments.append(att)
        return pdf_attachments

    def get_arxiv_preprints(
        self,
        collection: str | None = None,
        limit: int = 200,
    ) -> list[Item]:
        """按 URL、DOI、extra 或 itemType 查找预印本条目（arXiv、bioRxiv、medRxiv）。"""
        conn = self._connect()
        excl_sql, excl_params = self._excluded_filter()
        lib_sql, lib_params = self._library_filter()

        # 查找在 URL、DOI 或 extra 字段中带有 arXiv 或 bioRxiv/medRxiv 引用的条目
        rows = conn.execute(
            "SELECT DISTINCT id.itemID FROM itemData id "
            "JOIN fields f ON id.fieldID = f.fieldID "
            "JOIN itemDataValues iv ON id.valueID = iv.valueID "
            "JOIN items i ON id.itemID = i.itemID "
            f"WHERE f.fieldName IN ('url', 'DOI', 'extra') "
            f"AND (iv.value LIKE '%arxiv%' OR iv.value LIKE '%biorxiv%' OR iv.value LIKE '%medrxiv%' "
            f"OR iv.value LIKE '%10.1101/%') "
            f"AND i.itemTypeID {excl_sql} {lib_sql}",
            (*excl_params, *lib_params),
        ).fetchall()
        item_ids = {r["itemID"] for r in rows}

        # 同时查找所有 itemType = preprint 的条目
        preprint_type = conn.execute("SELECT itemTypeID FROM itemTypes WHERE typeName = 'preprint'").fetchone()
        if preprint_type:
            rows = conn.execute(
                f"SELECT i.itemID FROM items i WHERE i.itemTypeID = ? {lib_sql}",
                (preprint_type["itemTypeID"], *lib_params),
            ).fetchall()
            for r in rows:
                item_ids.add(r["itemID"])

        if not item_ids:
            return []

        # 若指定了集合则按集合过滤
        if collection:
            col_row = conn.execute(
                "SELECT collectionID FROM collections WHERE libraryID = ? AND (key = ? OR collectionName = ?)",
                (self._library_id, collection, collection),
            ).fetchone()
            if col_row:
                col_items = conn.execute(
                    "SELECT itemID FROM collectionItems WHERE collectionID = ?",
                    (col_row["collectionID"],),
                ).fetchall()
                item_ids &= {r["itemID"] for r in col_items}
            else:
                raise ValueError(
                    f"Collection '{collection}' not found. Use 'zot zotero collection list' to see available names."
                )

        target_ids = sorted(item_ids)[:limit]
        return self._get_items_batch(conn, target_ids) if target_ids else []

    def get_stats(self) -> dict:
        """返回库统计信息。"""
        conn = self._connect()
        lib_sql, lib_params = self._library_filter()
        # 条目总数（不含附件和注记）
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM items i WHERE itemTypeID " + self._get_excluded_sql() + " " + lib_sql,
            lib_params,
        ).fetchone()["cnt"]

        # 按类型统计条目
        type_rows = conn.execute(
            "SELECT t.typeName, COUNT(*) as cnt FROM items i "
            "JOIN itemTypes t ON i.itemTypeID = t.itemTypeID "
            "WHERE i.itemTypeID " + self._get_excluded_sql() + " " + lib_sql + " "
            "GROUP BY t.typeName ORDER BY cnt DESC",
            lib_params,
        ).fetchall()
        by_type = {r["typeName"]: r["cnt"] for r in type_rows}

        # 热门标签
        tag_rows = conn.execute(
            "SELECT t.name, COUNT(*) as cnt FROM itemTags it "
            "JOIN tags t ON it.tagID = t.tagID "
            "JOIN items i ON it.itemID = i.itemID "
            f"WHERE 1=1 {lib_sql} GROUP BY t.name ORDER BY cnt DESC LIMIT 20",
            lib_params,
        ).fetchall()
        top_tags = {r["name"]: r["cnt"] for r in tag_rows}

        # 带条目数的集合
        coll_rows = conn.execute(
            "SELECT c.collectionName, COUNT(ci.itemID) as cnt "
            "FROM collections c "
            "LEFT JOIN collectionItems ci ON c.collectionID = ci.collectionID "
            + ("WHERE c.libraryID = ? " if lib_params else "")
            + "GROUP BY c.collectionName ORDER BY cnt DESC",
            lib_params,
        ).fetchall()
        collections = {r["collectionName"]: r["cnt"] for r in coll_rows}

        # 附件
        pdf_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM itemAttachments ia "
            "JOIN items i ON ia.itemID = i.itemID "
            f"WHERE ia.contentType = 'application/pdf' {lib_sql}",
            lib_params,
        ).fetchone()["cnt"]

        # 注记数量
        notes_count = conn.execute(
            f"SELECT COUNT(*) as cnt FROM itemNotes n JOIN items i ON n.itemID = i.itemID WHERE 1=1 {lib_sql}",
            lib_params,
        ).fetchone()["cnt"]

        return {
            "total_items": total,
            "by_type": by_type,
            "top_tags": top_tags,
            "collections": collections,
            "pdf_attachments": pdf_count,
            "notes": notes_count,
        }

    def get_related_items(self, key: str, limit: int = 20) -> list[Item]:
        """按显式关联、共享集合和标签对相关条目排序。"""
        conn = self._connect()
        lib_sql, lib_params = self._library_filter()
        parent = conn.execute(
            f"SELECT itemID FROM items i WHERE key = ? {lib_sql}",
            (key, *lib_params),
        ).fetchone()
        if parent is None:
            return []
        item_id = parent["itemID"]
        related_ids: dict[int, int] = {}

        # 显式关联：解析 dc:relation 谓词，而不是硬编码一个在不同 Zotero
        # 数据库中可能不同的 ID。
        predicate = conn.execute(
            "SELECT predicateID FROM relationPredicates WHERE predicate = 'dc:relation'"
        ).fetchone()
        if predicate is not None:
            rows = conn.execute(
                "SELECT object FROM itemRelations WHERE itemID = ? AND predicateID = ?",
                (item_id, predicate["predicateID"]),
            ).fetchall()
            for r in rows:
                obj = r["object"]
                rel_key = obj.rsplit("/", 1)[-1] if "/" in obj else obj
                rel_row = conn.execute(
                    f"SELECT itemID FROM items i WHERE key = ? {lib_sql}",
                    (rel_key, *lib_params),
                ).fetchone()
                if rel_row:
                    related_ids[rel_row["itemID"]] = related_ids.get(rel_row["itemID"], 0) + 100

        # 隐式：共享集合
        my_cols = {
            r["collectionID"]
            for r in conn.execute("SELECT collectionID FROM collectionItems WHERE itemID = ?", (item_id,)).fetchall()
        }
        if my_cols:
            placeholders = ",".join("?" * len(my_cols))
            rows = conn.execute(
                f"SELECT itemID, COUNT(*) as cnt FROM collectionItems "
                f"WHERE collectionID IN ({placeholders}) AND itemID != ? "
                f"GROUP BY itemID",
                (*my_cols, item_id),
            ).fetchall()
            for r in rows:
                related_ids[r["itemID"]] = related_ids.get(r["itemID"], 0) + r["cnt"]

        # 隐式：共享标签（重叠 2 个及以上）
        my_tags = {
            r["tagID"] for r in conn.execute("SELECT tagID FROM itemTags WHERE itemID = ?", (item_id,)).fetchall()
        }
        if my_tags:
            placeholders = ",".join("?" * len(my_tags))
            rows = conn.execute(
                f"SELECT itemID, COUNT(*) as cnt FROM itemTags "
                f"WHERE tagID IN ({placeholders}) AND itemID != ? "
                f"GROUP BY itemID HAVING cnt >= 2",
                (*my_tags, item_id),
            ).fetchall()
            for r in rows:
                related_ids[r["itemID"]] = related_ids.get(r["itemID"], 0) + r["cnt"] * 5

        sorted_ids = sorted(related_ids, key=lambda x: related_ids[x], reverse=True)[:limit]
        return self._get_items_batch(conn, sorted_ids)

    # --- 私有辅助函数 ---

    def _get_items_batch(self, conn: sqlite3.Connection, item_ids: list[int]) -> list[Item]:
        """使用批量查询把多个条目 ID 解析为 Item，避免 N+1 查询。"""
        if not item_ids:
            return []
        if len(item_ids) > _ITEM_BATCH_SIZE:
            return [
                item
                for start in range(0, len(item_ids), _ITEM_BATCH_SIZE)
                for item in self._get_items_batch(conn, item_ids[start : start + _ITEM_BATCH_SIZE])
            ]

        placeholders = ",".join("?" * len(item_ids))

        # 获取条目的基础行
        lib_sql, lib_params = self._library_filter()
        rows = conn.execute(
            f"SELECT itemID, itemTypeID, key, dateAdded, dateModified "
            f"FROM items i WHERE itemID IN ({placeholders}) AND itemTypeID {self._get_excluded_sql()} {lib_sql}",
            (*item_ids, *lib_params),
        ).fetchall()
        if not rows:
            return []

        id_to_row = {r["itemID"]: r for r in rows}
        valid_ids = list(id_to_row.keys())
        valid_ph = ",".join("?" * len(valid_ids))

        # 批量获取条目类型
        type_ids = list({r["itemTypeID"] for r in rows})
        type_ph = ",".join("?" * len(type_ids))
        type_rows = conn.execute(
            f"SELECT itemTypeID, typeName FROM itemTypes WHERE itemTypeID IN ({type_ph})",
            type_ids,
        ).fetchall()
        type_map = {r["itemTypeID"]: r["typeName"] for r in type_rows}

        # 批量获取字段
        field_rows = conn.execute(
            f"SELECT id.itemID, f.fieldName, iv.value FROM itemData id "
            f"JOIN fields f ON id.fieldID = f.fieldID "
            f"JOIN itemDataValues iv ON id.valueID = iv.valueID "
            f"WHERE id.itemID IN ({valid_ph})",
            valid_ids,
        ).fetchall()
        fields_map: dict[int, dict[str, str]] = {}
        for r in field_rows:
            fields_map.setdefault(r["itemID"], {})[r["fieldName"]] = r["value"]

        # 批量获取作者
        creator_rows = conn.execute(
            f"SELECT ic.itemID, c.firstName, c.lastName, ct.creatorType "
            f"FROM itemCreators ic "
            f"JOIN creators c ON ic.creatorID = c.creatorID "
            f"JOIN creatorTypes ct ON ic.creatorTypeID = ct.creatorTypeID "
            f"WHERE ic.itemID IN ({valid_ph}) ORDER BY ic.itemID, ic.orderIndex",
            valid_ids,
        ).fetchall()
        creators_map: dict[int, list[Creator]] = {}
        for r in creator_rows:
            creators_map.setdefault(r["itemID"], []).append(
                Creator(r["firstName"] or "", r["lastName"] or "", r["creatorType"])
            )

        # 批量获取标签
        tag_rows = conn.execute(
            f"SELECT it.itemID, t.name FROM itemTags it "
            f"JOIN tags t ON it.tagID = t.tagID "
            f"WHERE it.itemID IN ({valid_ph})",
            valid_ids,
        ).fetchall()
        tags_map: dict[int, list[str]] = {}
        for r in tag_rows:
            tags_map.setdefault(r["itemID"], []).append(r["name"])

        # 批量获取集合
        coll_rows = conn.execute(
            f"SELECT ci.itemID, c.key FROM collectionItems ci "
            f"JOIN collections c ON ci.collectionID = c.collectionID "
            f"WHERE ci.itemID IN ({valid_ph})",
            valid_ids,
        ).fetchall()
        colls_map: dict[int, list[str]] = {}
        for r in coll_rows:
            colls_map.setdefault(r["itemID"], []).append(r["key"])

        # 按原始顺序组装条目
        items: list[Item] = []
        for item_id in item_ids:
            if item_id not in id_to_row:
                continue
            row = id_to_row[item_id]
            fields = fields_map.get(item_id, {})
            items.append(
                Item(
                    key=row["key"],
                    item_type=type_map.get(row["itemTypeID"], "unknown"),
                    title=fields.get("title", ""),
                    creators=creators_map.get(item_id, []),
                    abstract=fields.get("abstractNote"),
                    date=fields.get("date"),
                    url=fields.get("url"),
                    doi=fields.get("DOI"),
                    tags=tags_map.get(item_id, []),
                    collections=colls_map.get(item_id, []),
                    date_added=row["dateAdded"],
                    date_modified=row["dateModified"],
                    extra={k: v for k, v in fields.items() if k not in ("title", "abstractNote", "date", "url", "DOI")},
                )
            )
        return items

    def _get_item_fields(self, conn: sqlite3.Connection, item_id: int) -> dict[str, str]:
        rows = conn.execute(
            "SELECT f.fieldName, iv.value FROM itemData id "
            "JOIN fields f ON id.fieldID = f.fieldID "
            "JOIN itemDataValues iv ON id.valueID = iv.valueID "
            "WHERE id.itemID = ?",
            (item_id,),
        ).fetchall()
        return {r["fieldName"]: r["value"] for r in rows}

    def _get_item_creators(self, conn: sqlite3.Connection, item_id: int) -> list[Creator]:
        rows = conn.execute(
            "SELECT c.firstName, c.lastName, ct.creatorType "
            "FROM itemCreators ic "
            "JOIN creators c ON ic.creatorID = c.creatorID "
            "JOIN creatorTypes ct ON ic.creatorTypeID = ct.creatorTypeID "
            "WHERE ic.itemID = ? ORDER BY ic.orderIndex",
            (item_id,),
        ).fetchall()
        return [Creator(r["firstName"] or "", r["lastName"] or "", r["creatorType"]) for r in rows]

    def _get_item_tags(self, conn: sqlite3.Connection, item_id: int) -> list[str]:
        rows = conn.execute(
            "SELECT t.name FROM itemTags it JOIN tags t ON it.tagID = t.tagID WHERE it.itemID = ?",
            (item_id,),
        ).fetchall()
        return [r["name"] for r in rows]

    def _get_item_collections(self, conn: sqlite3.Connection, item_id: int) -> list[str]:
        rows = conn.execute(
            "SELECT c.key FROM collectionItems ci "
            "JOIN collections c ON ci.collectionID = c.collectionID "
            "WHERE ci.itemID = ?",
            (item_id,),
        ).fetchall()
        return [r["key"] for r in rows]
