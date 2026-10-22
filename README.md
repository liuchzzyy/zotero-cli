# zotero-cli

面向科研工作流和 AI Agent 的 Zotero CLI，命令入口为 `zot`。

- **本地读取**：只读打开 Zotero SQLite 数据库；Zotero 7 的 userdata v130 使用 `fulltextItems` + 有界扫描 `storage/*/.zotero-ft-cache` 的全文回退（受文件数/字节预算限制，见下），不会因缺少旧全文表而崩溃。
- **远程写入**：通过 Zotero Web API，不直接修改 `zotero.sqlite`。
- **Agent 接口**：稳定 JSON envelope、类型化退出码（0/1/2/3/4/5/6）、`--dry-run`、`--yes` 确认、`--idempotency-key`、`--stream` NDJSON 和运行时 `zot schema`。
- **PDF 解析**：MinerU 云端解析、内容哈希缓存、结构化 `content_list.json`，下载/解压有大小与条目上限。
- **AI 条目笔记**：`ai-note` 自动生成带插图的 HTML note 与关键词简记，写回 Zotero。
- **PDF RAG**：文献集全文检索（FTS5/BM25）加可选 embedding/rerank。

## 安装

需要 Python 3.12（`requires-python = ">=3.12,<3.13"`）和 [uv](https://docs.astral.sh/uv/)。依赖使用当前最新兼容版本（例如 `pyzotero>=1.16,<2`）；Python 3.10/3.11/3.13 不受支持。

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

配置发现：源码 checkout 使用仓库内 `.zot/config.toml`；安装版（`uv tool install` / pipx）使用 per-user 配置 `$XDG_CONFIG_HOME/zotero-cli/config.toml`（默认 `~/.config/zotero-cli/config.toml`，Windows 为 `%APPDATA%/zotero-cli/config.toml`）；相对 `XDG_CONFIG_HOME` 会被忽略（按 XDG 规范回退到默认目录），且安装版不会读取当前目录的 `.zot/config.toml`。

AI 笔记相关配置（`[ai_notes]`）：

```toml
[ai_notes]
api_key = ""
base_url = "http://127.0.0.1:3425/v1"
model = "group/daily"
fallback_models = ["group/deepseek", "group/advanced"]
reasoning_effort = "high"
temperature = 0.7
max_tokens = 0
max_extracted_chars = 250000
timeout_seconds = 300.0
max_retries = 2
```

`fallback_models` 可以是 TOML 数组，也可以是逗号分隔字符串；主模型调用失败或拒绝图片输入时按顺序回退。完整 note 请求只会切换到经预检、能容纳同一份文字和图片的模型。`timeout_seconds` 是单次请求超时，`max_retries` 是同一模型的可重试失败重试次数。

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
- profile 可以覆盖 `[embedding]`、`[rerank]`、`[semantic_search]`、`[vector_store]` 的键（`[profile.<name>.<section>]` 优先，缺省键回退到顶层同名 section）；未知 profile 返回 `configuration_error`（exit 3）。
- `config init` 和 `config profile set` 会合并并保留所有已存在 section 与 active profile，原子写入并保留 `.bak`；配置文件权限 0600、目录 0700。无变化的保存会跳过重写/备份；`--config-path` 指向 symlink 时写穿到目标且保留 symlink，`.bak` 建在真实目标旁。TOML 写出支持日期/时间、inf/nan、数组的 inline table 表示；不支持的值返回可操作的 `configuration_error`。
- `config init` 的 JSON `profile` 字段报告生效的 active profile；无效/空白的默认 profile 会直接失败（`configuration_error`，exit 3），不会回退到旧式 `[zotero]`；`config profile list` 对这类配置同样报告 `configuration_error`。修复方式：`zot config profile set NAME` 或 `zot config init --profile NAME`。
- `config cache list` 支持 `--limit`（默认 100，0 表示全部），JSON `meta` 带 `{count,total,limit}`，人类可读输出会提示截断。
- `config cache clear` 需要 `--yes`，支持 `--dry-run`：预览 data 为 `{entries,total_bytes,would_remove}`，真实清理成功 data 为 `{entries,total_bytes,removed}`。
- `ZOT_DATA_DIR`、`ZOT_PREFS_JS_PATH` 支持 `~` 展开；显式指定的缺失 `--config-path`、损坏 TOML 或类型错误都会返回 `configuration_error`（exit 3）。
- `ZOT_STATE_DIR` / `XDG_STATE_HOME` 只有绝对路径生效（相对值按 XDG 规范忽略并回退到 per-user 默认）；源码 checkout 默认使用仓库内 `.zot/state`，安装版默认使用 `$XDG_STATE_HOME/zotero-cli`（Windows 为 `%LOCALAPPDATA%/zotero-cli`）。
- 版本更新提示只在交互式 TTY 显示；管道、JSON 模式或 `--no-interaction` 不显示，`ZOT_NO_UPDATE_CHECK` 可关闭。
- `.zot/config.example.toml` 必须保持无凭据、无机器绝对路径，profile 名为中性的 `main`。

## 常用命令

```bash
# 搜索、读取和引用
uv run zot zotero search "attention mechanism"
uv run zot --json zotero read ITEMKEY
uv run zot zotero cite ITEMKEY --style apa
uv run zot zotero export ITEMKEY --format bibtex

# 浏览与检索辅助
uv run zot zotero list --collection "00 | 收件箱"
uv run zot zotero recent
uv run zot zotero relate ITEMKEY
uv run zot zotero duplicates                 # 发现重复组时 exit 6（conflict）；无重复 exit 0
uv run zot zotero summary ITEMKEY
uv run zot zotero publication-status         # 检查预印本发表状态（--apply 写回）
uv run zot zotero stats

# 永久删除（Web API DELETE；不可恢复，不是移入回收站）
uv run zot zotero delete ITEMKEY --dry-run
uv run zot zotero delete ITEMKEY --yes

# 回收站：list 读本地 deletedItems；restore 通过 Web API 恢复远端条目
uv run zot zotero trash list
uv run zot zotero trash restore ITEMKEY --dry-run
uv run zot zotero trash restore ITEMKEY

# 引用与打开（JSON 模式不写剪贴板；open 会启动 GUI）
uv run zot zotero cite ITEMKEY --style apa    # 人类模式打印并复制到剪贴板
uv run zot zotero cite ITEMKEY --no-copy     # 只打印，不复制
uv run zot --json zotero cite ITEMKEY        # envelope {citation, style, copied:false}，不写剪贴板
uv run zot --json zotero open ITEMKEY        # 启动默认应用；envelope {target, kind: pdf|url, gui:true}
uv run zot zotero open --url https://example.com
uv run zot zotero open doi:10.1038/example --url   # KEY 可为 http(s) URL 或 doi:...；非 http scheme 返回 validation_error（exit 3）

# 查看完整命令契约
uv run zot schema
uv run zot schema zotero search
uv run zot schema --diff prev.json   # 与缓存 schema 做结构 diff
```

程序化调用统一使用 `--json`。Zotero 基础命令使用 `zot zotero` 分组：条目检索、读取、统计、最近记录、摘要导出、重复项、引文、发表状态检测、PDF 附件定位和基础读写。MinerU 解析、AI 笔记和 pdf-rag 已分别迁移至 `ai_pdf`、`ai_notes` 和 `pdf_rag` 包，并由 Typer 的 `zot ai-pdf`、`zot ai-note`、`zot pdf-rag` 提供。命令按安全等级分组：read / write / destructive，分组结果同时反映在 `zot --help` 和 `zot schema` 中；嵌套的 pdf-rag/config 变更子命令也分别归入 write/destructive。

`zot schema --diff prev.json` 输出结构 diff；缓存文件结构非法返回 `validation_error`（exit 3）。不支持的本地 Zotero userdata schema 返回 `configuration_error`（exit 3，附修复提示）。

`delete` 是**永久删除**（Web API DELETE），不是移入回收站；帮助、人类可读输出、JSON 和 `next` 提示都明确写 permanent（成功 JSON data 带 `permanently_deleted` + `permanent: true`，dry-run 带 `would_delete` + `permanent: true`，确认文案说明不可撤销）。`trash list` 只读本地 `deletedItems`，Zotero Desktop 同步前看不到远端删除；`trash restore` 对已在回收站中的条目发 raw PATCH `{"deleted": 0}`（带 `If-Unmodified-Since-Version`），恢复本身是远端写入。不存在 `trash empty` 命令，任何文档都不得宣传它。所有 Web API 写入完成后，本地 SQLite 读取都可能滞后到 Zotero Desktop 同步为止；不要仅凭本地读判断远程写入成功。

## 输出契约、错误与流式输出

- 每个 JSON envelope 的顶层只使用官方键：`ok`、`data`/`error`、`meta`，以及可选的 `dry_run`、`next`；不存在其他顶层键。`ok` 是三态：`true` 成功、`false` 错误、`"partial"` 部分成功（部分成功时必有 `data`、没有 `error`，并以非零退出码表达失败部分，例如 `add --pdf` 的部分附件失败）。
- `next` 是官方可选的非空字符串数组，只在 success/partial envelope 出现，给出可直接执行的后续安全命令；它不是理解结果的唯一方式，也不保证远程写入会立刻在本地可读。
- 每次调用在 stdout 只输出一个 envelope，进度和警告走 stderr。参数/用法错误也输出 JSON envelope，返回 `validation_error`（exit 3）；未捕获异常、数据库错误、损坏 TOML 等会转换成类型化错误，不泄漏 traceback。`--verbose`（或等价的 `ZOT_DEBUG` 环境变量）才把内部 traceback 写到 stderr；两者都只来自解析后的 flag/env，不匹配原始 argv 子串。
- 写入是远端操作，随后立即读取本地 SQLite 可能仍是旧快照；先用 Web API 回读或等待 Zotero Desktop 同步，再判断写入是否生效。
- `--stream` 对 `zot zotero list` / `zot zotero recent` / `zot zotero search` 输出 NDJSON（每行一个 item envelope + 汇总行），优先于 `--no-json`；`recent` 的汇总带 `has_more`。
- `search` / `list` 在 v130 有界全文扫描（4000 个 cache 文件 / 单文件 8 MiB / 总量 256 MiB）达到文件/字节预算、或退化为仅元数据匹配时，JSON `meta` 带 `fulltext_limited: true`，人类模式在 stderr 提示：这是“结果可能不完整”的提示，不是错误。
- `stats`、`zotero summary`、`config profile list/set/cache stats/clear`、所有 `pdf-rag`/`collection` 命令在 JSON 模式下都恰好输出一个 envelope。`completions --json` 返回 `{shell, script, fallback, warning}`（warning 仍走 stderr）；`zot schema` 根节点名为 `zot`。
- 人类可读输出统一经过 sanitizer：去除 CR（`\r`）等 C0/C1 控制字符（保留 tab/换行），cite/open/publication-status/cache-list 等文本不会把终端控制序列带到 stdout/stderr。
- `export --format json` 在文本模式输出原始 JSON 元数据；加 `--json` 时仍使用 envelope。
- 退出码：0 成功；1 运行时/内部错误；2 认证缺失或无效；3 参数/配置/确认错误；4 未找到；5 网络或限流；6 冲突。**`duplicates` 的 exit 6 是“发现潜在重复组”的正向信号，不是错误**：JSON 仍是 `ok: true` + `data`（组列表）+ `meta.count`，没有重复时 exit 0。脚本可安全地用 `if zot zotero duplicates; then 无重复; else 处理重复; fi` 分支；不要把它当成通用冲突错误。`duplicates` 选项：`--by doi|title|both`（默认 both）、`--threshold`（默认 0.85）、`--limit`。

## PDF 阅读

区块一的基础 PDF 命令读取本地附件元信息与路径，也可安全导出或通过 Zotero Web API 上传：

```bash
uv run zot --json zotero pdf list ITEMKEY
uv run zot --json zotero pdf path ITEMKEY --attachment ATTACHMENTKEY
uv run zot --json zotero pdf export ITEMKEY exported.pdf --attachment ATTACHMENTKEY --dry-run
uv run zot --json zotero pdf export ITEMKEY exported.pdf --attachment ATTACHMENTKEY
uv run zot --json zotero pdf upload ITEMKEY --file paper.pdf --dry-run
uv run zot --json zotero pdf upload ITEMKEY --file paper.pdf --idempotency-key upload-001
```

多个 PDF 时 `path` 和 `export` 必须显式选择附件键；文件不存在则返回 `not_found`。`export` 仅创建全新的目标文件，现有文件（包括 symlink）绝不覆盖，失败会清理本次的残留输出且不改动源文件。`upload` 先验证 PDF 文件头，再使用 Zotero Web API 将其作为父条目附件上传；远端写入需要写权限凭据，重试时使用 `--idempotency-key`，未知写入结果必须先核对远端附件。

MinerU 解析使用 `zot ai-pdf`。一个 Zotero 父条目可能包含正文、Supporting Information 和其他 PDF；先通过基础 PDF 命令列出附件，再按 attachment key 选择，避免同名文件被错误解析：

```bash
# 只列出附件，不调用 MinerU
uv run zot --json zotero pdf list ITEMKEY

# 解析指定附件
uv run zot --json ai-pdf ITEMKEY --attachment ATTACHMENTKEY

# 获取结构化 JSON
uv run zot --json ai-pdf ITEMKEY --attachment ATTACHMENTKEY --structured

# 查看目录或读取指定章节
uv run zot --json ai-pdf ITEMKEY --attachment ATTACHMENTKEY --outline
uv run zot --json ai-pdf ITEMKEY --attachment ATTACHMENTKEY --section 2
```

当一个父条目包含多个 PDF 时，直接运行 `zot ai-pdf ITEMKEY` 会返回校验错误，要求明确指定 `--attachment`；`zotero pdf list` 遇到不存在的条目返回 `not_found`（exit 4）。MinerU 结果按 PDF 内容哈希和模型版本缓存到 `.zot/state/mineru/`：`ai-note` 从 `content_list.json` 构建带来源和页码的证据，pdf_rag RAG 也读取该结构化内容；图片位于缓存包的 `raw/images/`。

安全上限：单文件上传/解析 200MB；下载归档 512MB；归档内单成员 200MB；解压总量 1GB；归档条目数 10,000（超限消息按 MB 精度报告）。PDF 命令统一走 CLI 错误边界：配置类错误返回 `configuration_error`（exit 3），运行时错误返回 `runtime_error`/`internal_error`（exit 1），只有 `--verbose`/`ZOT_DEBUG` 才输出 traceback。缓存写入使用跨进程文件锁、隐藏 `.locks/` 与 staging 目录，PDF 只计算一次哈希：锁记录 owner PID，owner 进程死亡立即回收，其它等待者在 stale 窗口（≤ 最大等待）后回收并输出等待提示；启动时清理过期的隐藏 orphan staging；下载在 request 级重试成功后若流式 body 中断，还会再重试一次；仅瞬时错误重试，MinerU batch POST 不自动重试。

## AI 条目笔记（ai-note）

```bash
# 预览 MinerU 解析结果、按条目类型的默认模板和将发给模型的输入；不需要 AI 配置，不写 note（会解析 PDF，可能消耗 MinerU）
uv run zot ai-note ITEMKEY --dry-run

# 生成 note，打 ai/noted tag，并写关键词简记（需要写权限凭据）
uv run zot ai-note ITEMKEY

# 只生成 note，不打 tag / 不生成简记
uv run zot ai-note ITEMKEY --no-tag
uv run zot ai-note ITEMKEY --no-short-note

# 根据已有 note 回填关键词简记（不重新调模型生成 note）
uv run zot ai-note ITEMKEY --short-note-only

# 已处理过的条目重跑（会追加新 note，不替换旧 note）
uv run zot ai-note ITEMKEY --force

# 不确定网络写结果时的安全重试
uv run zot ai-note ITEMKEY --idempotency-key analyze-ITEMKEY-001
```

行为要点：

- `--dry-run` 不调用分类模型：`paper_type` 是条目类型默认值（book 类型映射 book，其余 research_article）；它仍会按缓存/云端解析 PDF，只是跳过生成 note 的模型调用和所有写入。它也会预览已打 `ai/noted` 的条目（不因已分析而提前返回）。
- note 按条目类型/分类选择模板（研究论文 / 综述 / 书籍），模板位于 `src/zotero_cli/ai_notes/templates/`。
- 标题、作者、期刊、日期、DOI 和正文作为不可信数据用定界符包裹后进入 prompt，模板声明指令层级，降低论文内容注入风险；正文长度超过 `max_extracted_chars` 或整份请求超过已验证的模型预算时会报错，不截断证据（标题在渲染前先转纯文本再转义）。
- `content_list.json` 的文字、表格、图像和图注按附件、正文/SI、PDF 页码标记。重复 PDF 跨整条目去重后编号；所有正文图像必须纳入请求，SI 最多选 30 张并优先选择正文引用的图。未发送的图号在 prompt 中列明；请求体上限 20 MiB。
- 模型返回“不支持图片输入”类错误时只对图片/vision 能力相关提示回退到纯文本模型，不会把 payload 过大等普通错误误判为能力问题。
- 渲染前统一处理上下标与中英文间距（`MnO₂` → `MnO2`，`Zn²⁺` → `Zn2+`，`g⁻¹` → `g^-1`）。
- 关键词简记写入条目 Extra 的 `short-note` 字段；校验在 CJK 间距补齐之后执行，长度上限 500 字符。
- 写入失败返回类型化错误：确定性拒绝（validation/not_found/auth 等，肯定未写入）`committed=false`，只有可能已提交的失败才标记 `committed` 并保留幂等预留，避免盲目重试造成重复 note；`--force` 只追加，不删除旧 note。
- 进度输出在 TTY 上使用单行刷新（`\\r`），非 TTY/日志使用 `--progress-lines` 按行输出。

同一条文本规则也适用于导入流程写入的中文标题与中文摘要（见「辅助工具」中的 `zh_text.py`）：不得含上下标，中英文之间加半角空格。

## 写入与安全操作

`--dry-run` 覆盖所有写入/破坏性命令及子命令并保证零副作用（envelope 带 `dry_run` 标记），包括 `pdf-rag new/add/remove/import/index/reindex/embed/delete`、`zot zotero collection create/move/rename`（以及 `delete`）、`config init/profile set/cache clear`、`zotero publication-status`。

破坏性操作需要确认：`delete`（永久删除）、`pdf-rag delete`、`zot zotero collection delete`、`config cache clear` 要求 `--yes`（或先 `--dry-run`）；`--no-interaction` 绝不隐含 `--yes`；非交互终端下缺少 `--yes`/`--dry-run` 返回 `confirmation_required`（exit 3）。`zot zotero trash restore` 恢复的是回收站中已有的条目，支持 `--dry-run` 预览；它不是 `delete` 的反操作（`delete` 永不进入回收站）。

```bash
# 预览
uv run zot zotero add --doi "10.1038/example" --dry-run
uv run zot zotero update ITEMKEY --title "New title" --dry-run
uv run zot zotero attach ITEMKEY --file paper.pdf --dry-run
uv run zot zotero delete ITEMKEY --dry-run
uv run zot pdf-rag delete papers --dry-run

# 幂等重试
uv run zot zotero update ITEMKEY \
  --title "New title" --idempotency-key update-001
```

主要写入命令：`zot zotero add`、`zot zotero update`、`zot zotero attach`、`zot zotero note`、`zot zotero tag`、`ai-note`。`note --add` 空内容返回 `validation_error`；条目不存在与"条目存在但没有 note"分别返回带上下文的 `not_found`。

部分成功是正式契约：`zot zotero add --from-file`、多条目 `update`/`tag`/`delete`、`add --pdf`（条目已创建但附件上传失败）以及 `zot zotero publication-status --apply`（部分写入失败）都返回 `ok: "partial"` + `data` + `next`，并以非零退出码退出（`zotero publication-status` 全部失败为 network/rate 时 exit 5，否则 exit 1）。`add --pdf` 支持 `--idempotency-key`，在 partial 时保持预留，避免盲目重试重复创建条目；`add --from-file` 不接受 `--idempotency-key`，会显式返回 `validation_error`。`delete` 的 JSON `data` 带 `permanent: true`；确定性拒绝（validation/not_found/auth 等）会释放幂等预留，只有可能已提交的失败才保留预留。类型化写入错误的 error envelope 在可能已提交时带 `error.committed: true`。

集合管理（`zot zotero collection`）父命令属于读取分组，但其 create/move/rename/delete/reorganize 子命令按安全等级归入 write/destructive，且都支持 `--dry-run`。`zot zotero collection delete` 会级联删除子集合，dry-run/确认提示/envelope 都会列出 `descendant_collections`/`descendant_count`；`zot zotero collection reorganize` 是真正的移动（替换旧集合成员关系，data 中 `items_moved[].replaced_existing_memberships=true`）；未知 key 等失败返回类型化 `not_found`/`conflict`（4/5/3/6）。`zot zotero add --url` 在元数据解析失败时会以 URL 作为标题回退，不会创建空标题条目。`zotero publication-status` 默认是 dry-run 预览，`--apply` 才写；部分写入失败返回 `ok: "partial"` 且非零退出。

## PDF RAG

```bash
uv run zot pdf-rag new papers --description "Selected papers"          # write
uv run zot pdf-rag add papers ITEMKEY1 ITEMKEY2                        # write
uv run zot pdf-rag index papers --no-embed                             # write
uv run zot pdf-rag embed papers                                        # write
uv run zot --json pdf-rag query \
  "reaction mechanism" --pdf-rag papers
```

- 所有 `pdf-rag` 变更命令都支持 `--dry-run`；`pdf-rag delete` 需要 `--yes`，会删除该工作区自己的 Qdrant collection（`pdf_rag_<name>`）、清理空向量库与空的 `.zot/pdf_rag/` 根目录（即使 index 文件缺失也按 owned marker 清理），manifest 不可读时仍安全删除并给出类型化错误。
- `pdf-rag index` 会移除已从工作区和 Zotero 中消失/缺失的条目；空工作区（或清空后）会 prune/clear 旧索引与向量，不再提前返回留下陈旧 chunk。`--force`/`reindex` 先完成 MinerU 解析、分块和候选 SQLite 索引构建，成功后才原子替换可用索引；解析或候选构建失败不会清除已有索引。实际清理和重建仅在明确执行命令时发生，先用 `--dry-run` 预览；`reindex` 支持 `--no-embed`。
- `pdf-rag embed` 有全局重试预算 `--deadline-seconds`（0 关闭）；确定性 4xx（不支持模型/额度耗尽）快速失败，另有逐条/逐批重试上限；心跳与逐条进度通过 `--progress-lines` 输出。
- `pdf-rag import` 在解析为空时输出 warning，并报告 resolved/imported/skipped 数量（成功 envelope 不带 dry-run 专用的 `would_add`）；`pdf-rag remove` 会同步 prune 该工作区索引，prune 失败时 warning 说明索引可能仍返回已移除条目。
- `pdf-rag query` 的 `meta` 暴露 requested/effective mode、degraded、可选的 `degradation` 列表、semantic_available、rerank_applied；读路径不会创建 Qdrant store（缺失/只读文件系统时退化为 BM25 并标记 degraded），rerank 失败也会进入 degradation 而没有静默丢结果；`top-k` 最小为 1，`--pdf-kind` 只保留 main 或 supplementary。
- 并发 prune/删除有锁与竞态保护；`.zot/pdf_rag/` 根目录为空时会清理。
- 普通 `pdf-rag index` 会根据 PDF、条目元数据和笔记内容判断是否需要重新索引。只有在需要强制重建时使用 `pdf-rag reindex`。
- 持久数据位置：`.zot/pdf_rag/<name>/`（工作区与 SQLite RAG 索引）、`.zot/pdf_rag/_qdrant/`（本地向量）、`.zot/state/mineru/`（共享 MinerU 解析包）。

## 开发与验证

```bash
uv sync --dev
uv run ruff check .
uv run ruff format --check .   # ruff 0.17 也会格式化 Markdown 代码块
uv run ty check
uv run pytest -q -rf
uv build
```

文档里的 Python 代码块也要通过 `uv run ruff format --check .`；改文档后对相关 Markdown 运行 `uv run ruff format <path>`。

完整测试套件预期全绿：0 failed、0 skipped（没有 skip 标记）。CI（`.github/workflows/ci.yml`）只在 Python 3.12 上运行 lint、format、`ty check`、完整测试和 wheel/sdist 构建；另有每周/手动触发、best-effort 的 `pip-audit` 依赖审计（网络依赖，不阻塞 PR）。依赖已升级到当前最新兼容版本（`typer>=0.27.3`、`pyzotero>=1.16,<2`、新版 `ruff`/`rich`/`openai` 等），类型检查使用 `ty`（`[tool.ty.src] include = ["src"]`）。

源码布局：

- `src/zotero_cli/cli/app/`：Typer 根入口、全局选项提升、错误边界、安全分级与版本更新提示。
- `src/zotero_cli/cli/commands/`：全部子命令；命令组各占一个子包。
- `src/zotero_cli/cli/renderers.py`：面向人类的 Rich 渲染与 `ErrorInfo` 展示模型。
- `src/zotero_cli/output/`：跨层输出契约（`envelope.py`、`exit_codes.py`、`streaming.py`、`sanitize.py`），领域包也依赖它。
- `src/zotero_cli/zotero/access/`：Zotero 访问封装，包括只读 SQLite/WAL 读取、基于 pyzotero 的 Web API 写入、访问错误与幂等预留缓存。
- `src/zotero_cli/zotero/`：按对象组织的应用服务，包括 PDF 导入、条目批量导入和更新、标签批量变更、集合重组、删除与回收站恢复。所有对象的命令入口统一保留在 `cli/commands/zotero/`；基础操作调用访问层，批量和组合操作调用应用服务。服务不调用 CLI，也不启动 CLI 子进程。
- `src/zotero_cli/ai_pdf/`：`access/` 封装 MinerU 请求与解析包缓存，`parsing.py` 提供按文件解析及批量解析，`documents.py` 处理 Zotero 附件选择，`content.py` 读取 Markdown、结构化内容、大纲与区段，`models.py` 和 `errors.py` 定义数据与错误。
- `src/zotero_cli/ai_notes/`：`access/client.py` 访问模型；`execution.py` 管理配置、资源和幂等；`analysis.py` 编排完整分析；`documents.py` 收集与去重文档；`evidence.py` 规划证据预算；`classification.py` 分类；`generation.py` 构造提示词并生成内容；`short_notes.py` 执行简记业务；`persistence.py` 写回笔记与标签；`rendering.py` 生成笔记 HTML。CLI 只处理参数、进度、结果呈现和退出码。
- `src/zotero_cli/pdf_rag/`：文献集清单存储、索引构建与检索实现（`pdf_rag/`）；索引和 AI 笔记复用 AI PDF 的批量解析服务。
- `src/zotero_cli/zotero/items.py`：Semantic Scholar 预印本状态查询（领域逻辑）。
- `src/zotero_cli/zotero/attachments.py`：本地附件定位与 WSL/Windows 路径转换。
- `src/zotero_cli/config/`：配置路径、模型、加载与 Zotero 解析，公共 API 位于 `__init__.py`。

测试位于 `tests/`，Agent Skill 位于 `skill/zotero-cli/`。

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
