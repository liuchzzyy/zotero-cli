"""测试公共支撑：CLI 调用封装、隔离环境与工作区后端的离线替身。

本模块只放跨测试文件复用的辅助，不放断言；断言留在各自的 `test_*` 模块里，
避免测试之间靠导入彼此的实现细节产生隐式耦合。
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

from click.testing import CliRunner, Result
from PIL import Image

from zotero_cli.ai_pdf import MinerUParseResult
from zotero_cli.cli import main
from zotero_cli.config import VectorStoreConfig

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DATA_DIR = REPO_ROOT / "tests" / "sample_data"

ISOLATED_ENV: dict[str, str] = {
    "ZOT_DATA_DIR": str(SAMPLE_DATA_DIR),
    "ZOT_FORMAT": "table",
    "ZOT_LIBRARY_ID": "",
    "ZOT_API_KEY": "",
    "ZOT_MINERU_TOKEN": "",
    "ZOT_EMBEDDING_URL": "",
    "ZOT_EMBEDDING_KEY": "",
    "ZOT_EMBEDDING_MODEL": "",
    "ZOT_RERANK_URL": "",
    "ZOT_RERANK_KEY": "",
    "ZOT_RERANK_MODEL": "",
}


def jpeg_bytes(size: tuple[int, int] = (400, 300)) -> bytes:
    """真实可解码的科学图夹具；路径与预算测试不能使用伪 JPEG。"""
    output = BytesIO()
    Image.new("RGB", size, (120, 130, 140)).save(output, format="JPEG", quality=95)
    return output.getvalue()


def invoke_cli(
    args: Sequence[str],
    *,
    json_output: bool = False,
    env: Mapping[str, str] | None = None,
    runner: CliRunner | None = None,
) -> Result:
    """在隔离夹具环境中调用 CLI，统一处理 `--json` 与驱动参数。"""
    command = ["--json", *args] if json_output else list(args)
    command_env = {**ISOLATED_ENV, **dict(env or {})}
    return (runner or CliRunner()).invoke(main, command, env=command_env)


def invoke_agent_cli(
    args: Sequence[str],
    env: Mapping[str, str] | None = None,
    *,
    runner: CliRunner | None = None,
) -> Result:
    """保留 TTY 自动探测地调用 CLI，用于代理契约测试。"""
    return invoke_cli(args, env={"ZOT_FORMAT": "", **dict(env or {})}, runner=runner)


def parse_json_output(output: str) -> dict[str, Any]:
    """解析末尾的 JSON envelope，并跳过结构化进度行。"""
    lines = [line for line in output.splitlines() if not line.lstrip().startswith('{"event"')]
    parsed = json.loads("\n".join(lines))
    if not isinstance(parsed, dict):
        raise AssertionError(f"期望 JSON 对象，实际为 {type(parsed).__name__}")
    return parsed


def _fake_mineru_result(pdf_path: Path, pdf_rag_root: Path) -> MinerUParseResult:
    """按内容指纹在临时目录中构造一份 MinerU 解析产物。"""
    fingerprint = sha256(pdf_path.read_bytes()).hexdigest()
    root = pdf_rag_root / "mineru" / fingerprint / "vlm"
    root.mkdir(parents=True, exist_ok=True)
    markdown_path = root / "document.md"
    content_list_path = root / "content_list.json"
    manifest_path = root / "manifest.json"
    markdown_path.write_text("# Introduction\nAttention mechanism evidence")
    content_list_path.write_text(
        json.dumps(
            [
                {"type": "text", "text": "Introduction", "text_level": 1, "page_idx": 0},
                {"type": "text", "text": "Attention mechanism evidence", "page_idx": 0},
            ]
        )
    )
    manifest_path.write_text("{}")
    return MinerUParseResult(
        source_path=pdf_path,
        fingerprint=fingerprint,
        model_version="vlm",
        root=root,
        markdown_path=markdown_path,
        content_list_path=content_list_path,
        manifest_path=manifest_path,
    )


@contextmanager
def isolate_pdf_rag_backend(pdf_rag_root: Path) -> Iterator[None]:
    """隔离工作区目录、向量库配置与 MinerU 缓存，让 RAG 流程完全离线。

    索引与查询都经由命令层进入，因此补丁必须落在命令层实际引用的名字上，
    否则真实实现仍会被调用并触碰用户状态。
    """

    def fake_ensure_many(paths: Sequence[Path], _progress: object = None) -> dict:
        return {pdf_path: _fake_mineru_result(pdf_path, pdf_rag_root) for pdf_path in paths}

    with ExitStack() as stack:
        stack.enter_context(patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=pdf_rag_root))
        stack.enter_context(
            patch(
                "zotero_cli.cli.commands.pdf_rag.indexing.load_vector_store_config",
                return_value=VectorStoreConfig(path=str(pdf_rag_root / "_qdrant")),
            )
        )
        cache_cls = stack.enter_context(patch("zotero_cli.cli.commands.pdf_rag.indexing.MinerUParseCache"))
        cache_cls.return_value.ensure_many.side_effect = fake_ensure_many
        yield
