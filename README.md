# zotero-cli

面向科研工作流和 AI Agent 的 Zotero CLI，命令入口为 `zot`。

- **本地读取**：只读打开 Zotero SQLite 数据库；Zotero 7 的 userdata v130 使用 `fulltextItems` + 有界扫描 `storage/*/.zotero-ft-cache` 的全文回退（受文件数/字节预算限制，见下），不会因缺少旧全文表而崩溃。
- **远程写入**：通过 Zotero Web API，不直接修改 `zotero.sqlite`。
- **Agent 接口**：稳定 JSON envelope、类型化退出码（0/1/2/3/4/5/6）、`--dry-run`、`--yes` 确认、`--idempotency-key`、`--stream` NDJSON 和运行时 `zot schema`。
- **PDF 解析**：MinerU 云端解析、内容哈希缓存、结构化 `content_list.json`，下载/解压有大小与条目上限。
- **AI 条目笔记**：`ai_analyze` 自动生成带插图的 HTML note 与关键词简记，写回 Zotero。
- **Workspace RAG**：工作区全文检索（FTS5/BM25）加可选 embedding/rerank。

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
profile = "main"

[profile.main]
data_dir = "/Users/your-name/Zotero"
prefs_js_path = "/Users/your-name/Library/Application Support/Zotero/Profiles/<profile>/prefs.js"
```

涉及写入时，还需要 Zotero User ID 和具有写权限的 API key。MinerU PDF 解析需要配置 `mineru_token`，AI 笔记需要配置 `[ai_notes]`。真实配置文件、缓存和本地索引不会提交到 Git。

AI 笔记相关配置（`[ai_notes]`）：

```toml
[ai_notes]
api_key = ""
base_url = "http://127.0.0.1:8317/v1"
model = "deepseek-flash"
fallback_models = ["<备用模型1>", "<备用模型2>"]
reasoning_effort = "max"
temperature = 0.7
max_tokens = 0
max_extracted_chars = 180000
timeout_seconds = 180.0
max_retries = 2
```

`fallback_models` 可以是 TOML 数组，也可以是逗号分隔字符串；主模型调用失败或拒绝图片输入时按顺序回退。`timeout_seconds` 是单次请求超时，`max_retries` 是同一模型的可重试失败重试次数。

所有 `[ai_notes]` 配置项都可以用环境变量覆盖（优先级：env > 配置文件 > 默认值），便于在不改动已跟踪的 `.zot/config.toml` 时切换 endpoint 或模型：

```text
ZOT_DATA_DIR
ZOT_LIBRARY_ID
ZOT_API_KEY
ZOT_MINERU_TOKEN
ZOT_MINERU_MODEL_VERSION
ZOT_PROFILE
ZOT_FORMAT
ZOT_PREFS_JS_PATH
ZOT_CROSSREF_MAILTO
ZOT_STATE_DIR
ZOT_DEBUG
ZOT_NO_UPDATE_CHECK
ZOT_AI_API_KEY
ZOT_AI_BASE_URL
ZOT_AI_MODEL
ZOT_AI_FALLBACK_MODELS
ZOT_AI_REASONING_EFFORT
ZOT_AI_TEMPERATURE
ZOT_AI_MAX_TOKENS
ZOT_AI_MAX_EXTRACTED_CHARS
ZOT_AI_TIMEOUT
ZOT_AI_MAX_RETRIES
ZOT_EMBEDDING_*（ACTIVE/PROVIDER/URL/KEY/MODEL/BATCH_SIZE）
ZOT_RERANK_*（ACTIVE/PROVIDER/URL/KEY/MODEL/BATCH_SIZE）
```

配置相关说明：

- 配置解析顺序：CLI flag > 环境变量 > 当前 profile > 默认值；`config show` 同样遵循 `--profile` / `ZOT_PROFILE`。
- `config init` 和 `config profile set` 会合并并保留所有已存在 section 与 active profile，原子写入并保留 `.bak`；配置文件权限 0600、目录 0700。
- `config cache clear` 需要 `--yes`，支持 `--dry-run`。
- `ZOT_DATA_DIR`、`ZOT_PREFS_JS_PATH` 支持 `~` 展开；显式指定的缺失 `--config-path`、损坏 TOML 或类型错误都会返回 `configuration_error`（exit 3）。
- `ZOT_STATE_DIR` 覆盖持久状态目录；源码 checkout 默认使用仓库内 `.zot/state`，安装版默认使用 `$XDG_STATE_HOME/zotero-cli`（Windows 为 `%LOCALAPPDATA%/zotero-cli`）。
- `.zot/config.example.toml` 必须保持无凭据、无机器绝对路径，profile 名为中性的 `main`。

## 常用命令

```bash
# 搜索、读取和引用
uv run zot search "attention mechanism"
uv run zot --json read ITEMKEY
uv run zot cite ITEMKEY --style apa
uv run zot export ITEMKEY --format bibtex

# 浏览与检索辅助
uv run zot list --collection "00 | 收件箱"
uv run zot recent
uv run zot relate ITEMKEY
uv run zot duplicates
uv run zot summarize ITEMKEY
uv run zot stats

# 回收站
uv run zot trash list
uv run zot trash restore ITEMKEY --dry-run
uv run zot trash restore ITEMKEY

# 查看完整命令契约
uv run zot schema
uv run zot schema search
```

程序化调用统一使用 `--json`。命令按安全等级分组：read / write / destructive，分组结果同时反映在 `zot --help` 和 `zot schema` 中；嵌套的 workspace/config 变更子命令也分别归入 write/destructive。

## 输出契约、错误与流式输出

- 每次调用在 stdout 只输出一个 JSON envelope（`ok`、`data`/`error`、`meta`），进度和警告走 stderr。
- 参数/用法错误也输出 JSON envelope，返回 `validation_error`（exit 3）；未捕获异常、数据库错误、损坏 TOML 等会转换成类型化错误，不泄漏 traceback。`--verbose`（或 `ZOT_DEBUG`）才把内部 traceback 写到 stderr。
- `--stream` 对 `list` / `recent` / `search` 输出 NDJSON（每行一个 item envelope + 汇总行），优先于 `--no-json`；`recent` 的汇总带 `has_more`。
- `stats`、`summarize`、`summarize-all`、`config profile list/set/cache stats/clear`、所有 `workspace`/`collection` 命令在 JSON 模式下都恰好输出一个 envelope。
- `export --format json` 在文本模式输出原始 JSON 元数据；加 `--json` 时仍使用 envelope。
- 退出码：0 成功；1 运行时/内部错误；2 认证缺失或无效；3 参数/配置/确认错误；4 未找到；5 网络或限流；6 冲突。

## PDF 阅读

一个 Zotero 父条目可能包含正文、Supporting Information 和其他 PDF。先列出附件，再按 attachment key 选择，避免同名文件被错误解析：

```bash
# 只列出附件，不调用 MinerU
uv run zot --json pdf ITEMKEY --list-attachments

# 解析指定附件
uv run zot --json pdf ITEMKEY --attachment ATTACHMENTKEY

# 获取结构化 JSON
uv run zot --json pdf ITEMKEY --attachment ATTACHMENTKEY --structured

# 查看目录或读取指定章节
uv run zot --json pdf ITEMKEY --attachment ATTACHMENTKEY --outline
uv run zot --json pdf ITEMKEY --attachment ATTACHMENTKEY --section 2
```

当一个父条目包含多个 PDF 时，直接运行 `zot pdf ITEMKEY` 会返回校验错误，要求明确指定 `--attachment`；`--list-attachments` 遇到不存在的条目返回 `not_found`（exit 4）。MinerU 结果按 PDF 内容哈希和模型版本缓存到 `.zot/state/mineru/`：正文 Markdown 供 `ai_analyze` 使用，`content_list.json` 供 workspace RAG 使用，图片位于缓存包的 `raw/images/`。

安全上限：单文件上传/解析 200MB；下载归档 512MB；归档内单成员 200MB；解压总量 1GB；归档条目数 10,000。缓存写入使用跨进程文件锁、隐藏 `.locks/` 与 staging 目录，PDF 只计算一次哈希；仅瞬时错误重试，MinerU batch POST 不自动重试。

## AI 条目笔记（ai_analyze）

```bash
# 预览 MinerU 解析结果、按条目类型的默认模板和将发给模型的输入；不需要 AI 配置，不写 note（会解析 PDF，可能消耗 MinerU）
uv run zot ai_analyze ITEMKEY --dry-run

# 生成 note，打 ai/noted tag，并写关键词简记（需要写权限凭据）
uv run zot ai_analyze ITEMKEY

# 只生成 note，不打 tag / 不生成简记
uv run zot ai_analyze ITEMKEY --no-tag
uv run zot ai_analyze ITEMKEY --no-short-note

# 根据已有 note 回填关键词简记（不重新调模型生成 note）
uv run zot ai_analyze ITEMKEY --short-note-only

# 已处理过的条目重跑（会追加新 note，不替换旧 note）
uv run zot ai_analyze ITEMKEY --force

# 不确定网络写结果时的安全重试
uv run zot ai_analyze ITEMKEY --idempotency-key analyze-ITEMKEY-001
```

行为要点：

- `--dry-run` 不调用分类模型：`paper_type` 是条目类型默认值（book 类型映射 book，其余 research_article）；它仍会按缓存/云端解析 PDF，只是跳过生成 note 的模型调用和所有写入。它也会预览已打 `ai/noted` 的条目（不因已分析而提前返回）。
- note 按条目类型/分类选择模板（研究论文 / 综述 / 书籍），模板位于 `tools/ainote/templates/`。
- 标题、作者、期刊、日期、DOI 和正文作为不可信数据用定界符包裹后进入 prompt，模板声明指令层级，降低论文内容注入风险；prompt 总长上限 220k 字符。
- MinerU Markdown 中的图片引用会被替换为 `【图 N】` 标记并作为多模态输入发送，默认最多 12 张；同一文档的重复附件会先去重再编号，避免编号与图引用错位；随请求发送的原始图片总量上限 20MB。
- 渲染前统一处理上下标与中英文间距（`MnO₂` → `MnO2`，`Zn²⁺` → `Zn2+`，`g⁻¹` → `g^-1`）。
- 关键词简记写入条目 Extra 的 `short-note` 字段；校验在 CJK 间距补齐之后执行，长度上限 500 字符。
- 写入失败返回类型化错误：发生在任何写已可能提交之后时标记 `committed`，不会盲目重试造成重复 note；`--force` 只追加，不删除旧 note。
- 进度输出在 TTY 上使用单行刷新（`\\r`），非 TTY/日志使用 `--progress-lines` 按行输出。

同一条文本规则也适用于导入流程写入的中文标题与中文摘要（见「辅助工具」中的 `zh_text.py`）：不得含上下标，中英文之间加半角空格。

## 写入与安全操作

`--dry-run` 覆盖所有写入/破坏性命令及子命令并保证零副作用（envelope 带 `dry_run` 标记），包括 `workspace new/add/remove/import/index/reindex/embed/delete`、`collection create/move/rename`（以及 `delete`）、`config init/profile set/cache clear`、`update-status`。

破坏性操作需要确认：`delete`、`workspace delete`、`collection delete`、`config cache clear` 要求 `--yes`（或先 `--dry-run`）；`--no-interaction` 绝不隐含 `--yes`；非交互终端下缺少 `--yes`/`--dry-run` 返回 `confirmation_required`（exit 3）。`trash restore` 同样支持 `--dry-run` 预览。

```bash
# 预览
uv run zot add --doi "10.1038/example" --dry-run
uv run zot update ITEMKEY --title "New title" --dry-run
uv run zot attach ITEMKEY --file paper.pdf --dry-run
uv run zot delete ITEMKEY --dry-run
uv run zot workspace delete papers --dry-run

# 幂等重试
uv run zot update ITEMKEY \
  --title "New title" --idempotency-key update-001
```

主要写入命令：`add`、`update`、`attach`、`note`、`tag`、`ai_analyze`。`note --add` 空内容返回 `validation_error`；条目不存在与"条目存在但没有 note"分别返回带上下文的 `not_found`。集合管理（`collection`）父命令属于读取分组，但其 create/move/rename/delete/reorganize 子命令按安全等级归入 write/destructive，且都支持 `--dry-run`。

## Workspace RAG

```bash
uv run zot workspace new papers --description "Selected papers"          # write
uv run zot workspace add papers ITEMKEY1 ITEMKEY2                        # write
uv run zot workspace index papers --no-embed                             # write
uv run zot workspace embed papers                                        # write
uv run zot --json workspace query \
  "reaction mechanism" --workspace papers
```

- 所有 workspace 变更命令都支持 `--dry-run`；`workspace delete` 需要 `--yes`，并会删除该工作区的 Qdrant collection（`ws_<name>`）并清理空向量库。
- `workspace index` 会移除已从工作区和 Zotero 中消失/缺失的条目；`--force` 重建前会先准备/校验；`reindex` 支持 `--no-embed`；`embed` 心跳与逐条进度通过 `--progress-lines` 输出。
- `workspace import` 在解析为空时输出 warning，并报告 resolved/imported/skipped 数量。
- `workspace query` 的 `meta` 暴露 requested/effective mode、degraded、semantic_available、rerank_applied；`top-k` 最小为 1。`--pdf-kind` 只保留 main 或 supplementary。
- 普通 `workspace index` 会根据 PDF、条目元数据和笔记内容判断是否需要重新索引。只有在需要强制重建时使用 `workspace reindex`。
- 持久数据位置：`.workspace/<name>/`（工作区与 SQLite RAG 索引）、`.workspace/_qdrant/`（本地向量）、`.zot/state/mineru/`（共享 MinerU 解析包）。

## 辅助工具

`tools/` 下的脚本不是 `zot` 子命令：`zot schema` 不会列出它们，它们只负责批量工作流。总览见 `tools/README.md`。

- `tools/paperrag/`：`run-rag-workspace.zsh`（inventory、增量索引、embedding 回填）、`run-rag-evidence-search.zsh`（检索）、`zotero-workflow-common.zsh`（共享函数）、`workspace_inventory.py`（inventory 辅助脚本）。生成物写入唯一命名的 `log/` 运行目录；详细用法见 `tools/paperrag/Instructions.md`。
- `tools/paper2zotero/`：`zotero_inbox.py`（PaperCrossref 收件箱导入）、`backfill_titles.py`（全库译名回填）、`translate_abstracts.py`（集合与条目中文摘要批量翻译与回填）、`batch_translate_metadata.py`（按集合同步中文标题与中文摘要）、`zh_text.py`（中文格式化规则）。断点状态自本次改动起位于 `.zot/state/paper2zotero/`（旧 `tools/paper2zotero/*_state.json` 不会自动迁移，需要手动移动一次以继续断点续跑）。预览仍需 Zotero API 凭据读取远端库，但不会写入。详见 `tools/paper2zotero/README.md`。
- `tools/ainote/`：`batch_analyze.py`（按集合批量运行 `ai_analyze`，`--collection` 必填，dry-run 零副作用）和 `templates/`（`ai_analyze` prompt 模板，构建 wheel 时随包发布）。
- 工具测试使用 `tools` marker，纳入默认 `testpaths`。

## 开发与验证

```bash
uv sync --dev
uv run ruff check .
uv run ruff format --check .
uv run python -m mypy src/zotero_cli tools --exclude '(^|/)test_[^/]*\.py$'
uv run pytest -q -rf
uv build
```

完整测试套件预期全绿：0 failed、0 skipped（没有 skip 标记）。CI（`.github/workflows/ci.yml`）在 Python 3.10 与 3.13 上运行 lint、format、mypy、完整测试和 wheel/sdist 构建；另有每周/手动触发、best-effort 的 `pip-audit` 依赖审计（网络依赖，不阻塞 PR）。

源码布局：

- `src/zotero_cli/cli/`：Click 根命令、全局选项、安全分级（`safety.py`）、错误边界与命令注册。
- `src/zotero_cli/commands/`：按功能域拆分的命令包（`items`、`notes`、`attachments`、`collections`、`workspace`、`system`）；每个包用 `commands.py` 存放命令实现。
- `src/zotero_cli/core/`：领域逻辑（`zotero` 读取与写入、`notes` AI 笔记流水线、`mineru` 解析、`rag` 检索），以及 `workspace_store.py`、`platform_paths.py`、`version_check.py`。
- `src/zotero_cli/output/`：JSON envelope、渲染、流式输出与退出码（`envelope.py`、`renderers.py`、`streaming.py`、`exit_codes.py`）。
- `src/zotero_cli/config/`：配置路径、模型、加载与 Zotero 解析，公共 API 位于 `__init__.py`。

测试位于 `tests/`（`tools/**/test_*.py` 也纳入默认收集），Agent Skill 位于 `skill/zotero-cli/`，批量工具位于 `tools/`。

## 退出码

| 退出码 | 含义 |
|---:|---|
| 0 | 成功 |
| 1 | 运行时或内部错误（含 API 错误） |
| 2 | 认证信息缺失或无效 |
| 3 | 参数、配置用法错误或需要确认（`confirmation_required`） |
| 4 | 未找到 |
| 5 | 网络或限流错误 |
| 6 | 冲突，例如检测到重复项 |

## 许可

本项目基于 [Agents365-ai/zotero-cli-cc](https://github.com/Agents365-ai/zotero-cli-cc) 扩展开发，采用 [MIT License](LICENSE)。
