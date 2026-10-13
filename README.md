# zotero-cli

面向科研工作流和 AI Agent 的 Zotero CLI，命令入口为 `zot`。

- **本地读取**：只读 Zotero SQLite 数据库。
- **远程写入**：通过 Zotero Web API，不直接修改 `zotero.sqlite`。
- **Agent 接口**：稳定 JSON envelope、类型化退出码、`--dry-run`、幂等键和运行时 schema。
- **PDF/RAG**：MinerU PDF 解析、结构化缓存、工作区全文检索和可选 embedding/rerank。

## 安装

需要 Python 3.10–3.13 和 [uv](https://docs.astral.sh/uv/)。

```bash
git clone git@github.com:liuchzzyy/zotero-cli.git
cd zotero-cli
uv sync --dev
uv run zot --help
```

## 配置

复制示例配置并按本机环境填写：

```bash
cp .zot/config.example.toml .zot/config.toml
```

至少配置 Zotero 数据目录：

```toml
[default]
profile = "macbook"

[profile.macbook]
data_dir = "/Users/your-name/Zotero"
prefs_js_path = "/Users/your-name/Library/Application Support/Zotero/Profiles/<profile>/prefs.js"
```

涉及写入时，还需要 Zotero User ID 和具有写权限的 API key。MinerU PDF 解析需要配置 `mineru_token`。真实配置文件、缓存和本地索引不会提交到 Git。

常用环境变量：

```text
ZOT_DATA_DIR
ZOT_LIBRARY_ID
ZOT_API_KEY
ZOT_MINERU_TOKEN
ZOT_MINERU_MODEL_VERSION
ZOT_PROFILE
ZOT_FORMAT
```

## 常用命令

```bash
# 搜索、读取和引用
uv run zot search "attention mechanism"
uv run zot --json read ITEMKEY
uv run zot cite ITEMKEY --style apa
uv run zot export ITEMKEY --format bibtex

# 最近条目和统计
uv run zot recent
uv run zot stats

# 查看完整命令契约
uv run zot schema
uv run zot schema search
```

程序化调用统一使用 `--json`。

## PDF 阅读

一个 Zotero 父条目可能包含正文、Supporting Information 和其他 PDF。先列出附件，再按 attachment key 选择，避免同名文件被错误解析：

```bash
# 只列出附件，不调用 MinerU
uv run zot --json pdf ITEMKEY --list-attachments

# 解析指定附件
uv run zot --json pdf ITEMKEY --attachment ATTACHMENTKEY

# 获取结构化 JSON
uv run zot --json pdf ITEMKEY \
  --attachment ATTACHMENTKEY --structured

# 查看目录或读取指定章节
uv run zot --json pdf ITEMKEY --attachment ATTACHMENTKEY --outline
uv run zot --json pdf ITEMKEY --attachment ATTACHMENTKEY --section 2
```

当一个父条目包含多个 PDF 时，直接运行 `zot pdf ITEMKEY` 会返回校验错误，要求明确指定 `--attachment`。

MinerU 结果按 PDF 内容哈希和模型版本缓存到 `.zot/state/mineru/`。正文 Markdown 可供 `ai_analyze` 使用，`content_list.json` 可供 workspace RAG 使用。

## 写入与安全操作

所有写入命令都支持 `--dry-run` 预览；会改变 Zotero 数据的命令不要在未确认范围时直接执行。

```bash
# 预览
uv run zot add --doi "10.1038/example" --dry-run
uv run zot update ITEMKEY --title "New title" --dry-run
uv run zot attach ITEMKEY --file paper.pdf --dry-run
uv run zot delete ITEMKEY --dry-run

# 幂等重试
uv run zot update ITEMKEY \
  --title "New title" --idempotency-key update-001
```

主要写入命令：`add`、`update`、`attach`、`note`、`tag`、`collection`、`delete` 和 `ai_analyze`。

## Workspace RAG

```bash
uv run zot workspace new papers --description "Selected papers"
uv run zot workspace add papers ITEMKEY1 ITEMKEY2
uv run zot workspace index papers --no-embed
uv run zot workspace embed papers
uv run zot --json workspace query \
  "reaction mechanism" --workspace papers
```

普通 `workspace index` 会根据 PDF、条目元数据和笔记内容判断是否需要重新索引。只有在需要强制重建时使用 `workspace reindex`。

## 开发与验证

```bash
uv sync --dev
uv run ruff check src tests
uv run python -m mypy src/zotero_cli
uv run pytest -q
uv build
```

代码位于 `src/zotero_cli/`，测试位于 `tests/`，Agent Skill 位于 `skill/zotero-cli/`。

## 退出码

| 退出码 | 含义 |
|---:|---|
| 0 | 成功 |
| 1 | 运行时错误 |
| 2 | 认证信息缺失或无效 |
| 3 | 参数或配置错误 |
| 4 | 未找到 |
| 5 | 网络或限流错误 |
| 6 | 冲突，例如检测到重复项 |

## 许可

[MIT License](LICENSE)
