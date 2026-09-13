# zotero-cli

`zotero-cli` 是面向研究工作流和 AI Agent 的 Zotero 命令行工具，命令入口保持为 `zot`。

读取操作直连本机 Zotero SQLite 数据库；写入操作统一通过 Zotero Web API，绝不直接修改 `zotero.sqlite`。项目支持稳定 JSON envelope、类型化退出码、dry-run、幂等键、PDF 提取和工作区 RAG。

## 命名约定

- 项目、发行包和 skill：`zotero-cli`
- Python 包：`zotero_cli`
- 命令：`zot`
- 本地配置目录：`.zot`

## 功能

- 搜索与阅读：`search`、`list`、`read`、`recent`、`stats`、`relate`
- 引用与导出：`cite`、`export`、`summarize`、`summarize-all`
- PDF：MinerU 云端解析、Markdown/结构化 JSON 缓存、目录与章节读取
- 安全写入：`add`、`update`、`attach`、`note`、`tag`、`collection`、`delete`
- 文献维护：DOI/标题查重、回收站、预印本出版状态检查
- 工作区 RAG：读取 MinerU 结构化 JSON，写入本地 SQLite FTS5、Qdrant、embedding 和 rerank
- AI 分析：读取同一份 MinerU Markdown，调用 OpenAI 兼容接口生成 Zotero note 和 short-note

## 安装

需要 Python 3.10–3.13 和 [uv](https://docs.astral.sh/uv/)。

```bash
git clone git@github.com:liuchzzyy/zotero-cli.git
cd zotero-cli
uv sync --dev
```

当前 MacBook 可直接执行：

```bash
cd /Users/liuchzzyy/python-code/zotero-cli
uv sync --dev
uv run zot --help
```

## MacBook 配置

实际配置位于 `.zot/config.toml`，示例见 [`.zot/config.example.toml`](.zot/config.example.toml)。当前 MacBook 的关键路径为：

```toml
[default]
profile = "macbook"

[profile.macbook]
data_dir = "/Users/liuchzzyy/Zotero"
prefs_js_path = "/Users/liuchzzyy/Library/Application Support/Zotero/Profiles/uffjwrp8.default/prefs.js"
```

写入操作还需要 Zotero User ID 和具有写权限的 API key。真实 `config.toml`、缓存和状态数据库均被 Git 忽略。

配置优先级：命令行参数 > 环境变量 > 活动 profile > 默认值。常用环境变量包括：

- `ZOT_DATA_DIR`
- `ZOT_LIBRARY_ID`
- `ZOT_API_KEY`
- `ZOT_MINERU_TOKEN`
- `ZOT_MINERU_MODEL_VERSION`
- `ZOT_PROFILE`
- `ZOT_FORMAT`

## 快速开始

```bash
uv run zot search "attention mechanism"
uv run zot --json read ITEMKEY
uv run zot cite ITEMKEY --style apa
uv run zot pdf ITEMKEY
uv run zot --json pdf ITEMKEY --structured
uv run zot schema
```

### 写入前预览

```bash
uv run zot add --doi "10.1038/example" --dry-run
uv run zot update ITEMKEY --title "New title" --dry-run
uv run zot delete ITEMKEY --dry-run
uv run zot ai_analyze ITEMKEY --dry-run
```

`ai_analyze --dry-run` 只读取本地 Zotero 数据、MinerU 解析结果并生成预览，
不写入 Zotero，因此不要求 Zotero Web API 写入凭证。实际生成 note 时仍要求有效写入凭证。

确认写入时，建议为可重试操作提供唯一幂等键：

```bash
uv run zot update ITEMKEY --title "New title" --idempotency-key update-001
```

### 工作区检索

```bash
uv run zot workspace new 00_收件箱 --description "Zotero collection AG7NQ5UW"
uv run zot workspace add 00_收件箱 ITEMKEY1 ITEMKEY2
uv run zot workspace index 00_收件箱 --no-embed
uv run zot workspace embed 00_收件箱
uv run zot --json workspace query "reaction mechanism" --workspace 00_收件箱
```

## PDF、AI note 与 RAG 的统一流程

PDF 只上传到 MinerU 云端 API 一次。结果按 PDF SHA-256 与 MinerU 模型版本保存到
`.zot/state/mineru/`：`document.md` 提供给 `ai_analyze`，`content_list.json` 提供给
workspace RAG，manifest 和 MinerU 原始文件/图片同时保留。项目不包含本地 PDF 解析器，
也不会在 MinerU 失败时回退到本地解析。

## Agent 接口

程序化调用使用 `--json`。完整命令、参数与安全等级以运行时 schema 为准：

```bash
uv run zot schema
uv run zot schema search
uv run zot schema collection move
```

直接运行 `uv run zot` 会显示顶层帮助并以退出码 0 结束。

主要退出码：

| 退出码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 1 | 运行时错误 |
| 2 | 认证信息缺失或无效 |
| 3 | 参数或配置错误 |
| 4 | 未找到 |
| 5 | 网络或限流错误 |
| 6 | 冲突，例如检测到重复项 |

## 开发与验证

```bash
uv sync --dev
uv run ruff check src tests
uv run python -m mypy src/zotero_cli
uv run pytest -q
uv build
```

源代码位于 `src/zotero_cli/`，bundled skill 位于 `skill/zotero-cli/`。

Mac 工作流脚本位于 `tools/`，统一使用 zsh：

```bash
tools/run-rag-workspace.zsh --help
tools/run-rag-evidence-search.zsh --help
```

## 来源与许可

本项目基于 [Agents365-ai/zotero-cli-cc](https://github.com/Agents365-ai/zotero-cli-cc) 扩展开发，采用 [MIT License](LICENSE)。
