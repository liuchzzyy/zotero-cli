# AGENTS.md

本文件是本仓库唯一的指导文件，面向在 `zotero-cli` 中工作的编码代理。先读本文再动手；命令面以运行中的 CLI（`zot --help`、`zot COMMAND --help`、`zot schema`）为准，不依赖散文描述。

## 适用范围

本文件用于指导本仓库的新增与修改。维护已有项目时，先了解现有结构和调用关系；在本次任务范围内直接更新受影响的实现，不为保留旧行为添加兼容层，也不批量改动无关文件。

## 项目概览

`zotero-cli`（命令入口 `zot`）是面向 AI Agent 的 Zotero CLI，核心约束是**读走本地、写走远端**：

- **本地读取**：只读打开 `zotero.sqlite`，不写入该文件。
- **远程写入**：通过 Zotero Web API（`pyzotero`）完成，不直接修改本地数据库。
- **Agent 契约**：每次调用只输出一个 JSON envelope、类型化退出码（0–6）、`zot schema` 自省、`--dry-run`、`--yes`、`--idempotency-key`、`--stream` NDJSON。
- **附加能力**：MinerU 云端 PDF 解析、`ai_analyze` 生成带插图笔记、workspace RAG（FTS5/BM25 + 可选 embedding/rerank）。
- `tools/` 下是与 `zot` 并列的批处理助手，**不是**子命令，`zot schema` 不会列出它们。

## 注释语言

- Python 代码中的独立注释、行尾注释和 docstring 的说明文字一律使用简体中文，不写英文说明或中英对照。
- 代码标识符、库名、命令，以及 `# noqa`、`# type: ignore` 等工具指令保持原样；它们不是说明文字。
- 注释重点解释意图、约束和非显然的原因，不逐行复述代码；修改逻辑时同步更新相关注释。
- 存量源码中仍有英文注释和 docstring，属于待改造内容：在任务涉及的文件范围内，把说明文字改为简体中文；不做与任务无关的批量翻译。

## 技术选型

- 使用 CPython 3.12 系列。在 `pyproject.toml` 中设置 `requires-python = ">=3.12,<3.13"`；本地可用 `.python-version` 写入 `3.12` 固定解释器，不限定为单一补丁版本。3.10/3.11/3.13 不受支持。
- 使用 `uv` 管理依赖、虚拟环境、命令执行与打包发布。保留 `pyproject.toml` 和 `uv.lock`，不混用其他依赖锁文件；不为了绕过本地失败而降级依赖。
- CLI 使用 **Click** 定义命令树（`cli/root.py`、`cli/registry.py`、`cli/tiered_group.py`），在 `pyproject.toml` 的 `[project.scripts]` 注册入口 `zot = "zotero_cli.cli:main"`。命令回调只负责参数解析和调用，核心逻辑放入可独立测试的函数。
- 使用 Ruff 检查与格式化：`target-version = "py312"`、`line-length = 120`。类型检查使用 mypy（`src/zotero_cli` 与 `tools` 强制要求类型注解，测试模块排除）。
- 构建后端为 `hatchling`；`tools/ainote/templates` 通过 `force-include` 打包为 `zotero_cli/templates`，安装后仍能解析提示词模板。

## 项目目录结构

- 采用 `src/<package_name>/` 布局；按职责或业务领域组织文件，不把所有模块堆在包根目录。目录名使用 `snake_case`，名称须能说明该目录管理什么。
- 本仓库的实际结构：

  ```text
  pyproject.toml
  src/zotero_cli/
  ├── cli/            # Click 根组、全局参数、安全分级、错误边界、命令注册
  ├── commands/       # 按领域分包：items、notes、attachments、collections、workspace、system
  ├── core/           # 领域逻辑：zotero、notes、mineru、rag、workspace_store、platform_paths、version_check
  ├── output/         # envelope、renderer、streaming、exit_codes（含顶层错误边界）
  ├── config/         # 路径、模型、加载器、Zotero 解析，公开 API 在 __init__.py
  └── models.py       # 共享 dataclass
  tests/              # unit/、cli/、integration/、fixtures/ 与共享 harness
  tools/              # 非 CLI 批处理助手：ainote、paper2zotero、paperrag
  skill/zotero-cli/   # 随包分发的 agent skill
  tool/               # 新增独立脚本的约定位置（见“辅助脚本”）
  ```

- CLI 层调用核心逻辑，不把计算流程写进命令回调；`core/` 不依赖 `cli/`。同一职责的文件放在一起，测试目录尽量对应源码结构，使读者能从目录和文件名直接定位功能。
- 只有出现真实职责时才建立相应目录，不创建空目录或多余嵌套；小功能可以先放在一个明确命名的模块中，职责扩展后再拆分。不要用 `utils/`、`misc/` 等泛称容纳不同领域的代码，也不要为每个函数单独建文件。
- 维护已有项目时，在任务涉及的范围内调整不合理的目录结构；移动文件时，同步更新导入、入口、测试和文档，并运行相关验证。

## 架构不变量

修改任何命令前先确认不破坏以下约束：

1. **读写分离**。读取走 `core/zotero/reader.py` 直连本地 SQLite；写入走 `core/zotero/writer.py` 调用 Web API。绝不写 `zotero.sqlite`，否则会破坏 Zotero 的同步模型。
2. **WAL 分支不可回退**。存在非空 `zotero.sqlite-wal` 时，reader 会复制数据库和 WAL 到临时文件并重放，因为对运行中的库使用 `immutable=1` 会得到过期快照。不要简化成直接 `immutable=1` 重连。
3. **远程写入不会立即出现在本地读取中**。本地库是只读快照，由 Zotero Desktop 自行同步；写入后要用 Web API 回读验证，不能以本地读取结果断定写入失败或成功。
4. **`delete` 是永久删除**，通过 Web API `DELETE` 完成，不是移入回收站。帮助文本、docstring、人类可读输出、JSON 载荷与 `next` 提示都必须说“永久”；不得把 trash/restore 宣传成 `delete` 的后果。仓库没有 `trash empty` 命令，也不要新增。
5. **`trash` 语义有限**。`trash list` 只读本地 `deletedItems` 表，反映 Desktop 已同步的状态；`trash restore` 用带 `If-Unmodified-Since-Version` 的 `PATCH {"deleted": 0}` 恢复已在回收站中的条目，支持 `--dry-run`，但没有 `--yes` 门槛，也不是 `delete` 的逆操作。
6. **全文搜索有预算**。`search` 同时支持旧库的 `fulltextWords`/`fulltextItemWords` 与 Zotero 7 userdata v130 的 `.zotero-ft-cache`。v130 走**有界**扫描（最多 4000 个缓存文件、单文件 8 MiB、总计 256 MiB），预算耗尽时标记 `fulltext_limited` 并降级为元数据匹配。不要改成无上限全量扫描；超出预算的深层命中是可接受的已知限制。
7. **单一 envelope**。JSON 模式每次调用只在 stdout 输出一个 envelope，顶层键仅 `ok`、`data`/`error`、`meta`，以及可选的 `dry_run`、`next`；进度与警告写 stderr。`ok` 为三态：`true`、`false`、`"partial"`（部分成功时带 `data`、不带 `error`，仍以非零退出码返回失败部分）。
8. **写操作必须先有零副作用预览**。所有写入/破坏性命令（含各子命令与 workspace、collection、config、`update-status --apply` 路径）都提供 `--dry-run`，envelope 标记 `dry_run`，人类可读输出也要说明。破坏性命令需 `--yes` 确认；非 TTY 且无 `--yes`/`--dry-run` 时返回 `confirmation_required`（退出 3），`--no-interaction` 永不隐含 `--yes`。
9. **退出码即契约**：0 成功、1 运行时/内部错误、2 认证、3 校验/配置/确认、4 未找到、5 网络或限流、6 冲突。
10. **`duplicates` 用退出码 6 作为正向信号**：发现重复组时输出 `ok: true` envelope 并以 6 退出，无重复时以 0 退出；这不是错误。
11. **`--idempotency-key` 保护可重试写入**：确认成功则重放原结果；结果未知时需核对而不是盲重试。类型化写入错误仅在变更可能已到达服务器时置 `committed: true`。

## 子系统要点

- `core/zotero/`：`reader.py` 是 SQLite 读取层（WAL 感知、schema 感知搜索）；`citations.py` 提供 BibTeX/CSL-JSON/RIS 导出；`writer.py` 是 Web API 写入层，`writer_errors.py` 提供带 `committed` 语义的类型化错误，`short_note_merge.py` 负责 Extra 简记合并，`idempotency.py` 管理幂等缓存与预留。
- `core/mineru/`：上传、轮询、下载流程；解析包按内容哈希缓存在 `.zot/state/mineru/`，跨进程用 `.locks/` 协调；下载与解压有明确的大小和条目上限；只重试瞬时错误。
- `core/notes/`：`ai_analyze` 管线。`client.py` 负责 OpenAI 兼容调用与多模型回退，`pipeline.py` 做逐条目编排，`structured_input.py` 渲染 MinerU 结构化块并对单次请求做预算（主图必须全部放入，SI 最多 30 张，超出或退化的图片要在提示中列明），`renderer.py` 生成内联样式 HTML，`templates.py` 解析模板（checkout 下读 `tools/ainote/templates/`，安装后回退到包内模板）。
- `core/rag/`：`chunking.py` 分块（不产生空块），`index.py` 使用 SQLite FTS5/BM25 并保留原始分数，`fusion.py` 做加权倒数排名融合，`vector_store.py` 管理本地 Qdrant 集合并拒绝删除非本工具创建的目录。
- `core/workspace_store.py`：仓库内 workspace 清单（原子写入、名称校验、路径转义）；删除时清理对应 Qdrant 集合，且仅在目录确属本工具时才修剪。
- `core/notes/` 与 `core/rag/` 的公开 API 由各自 `__init__.py` 暴露，调用方不要深入私有模块。
- `cli/safety.py` 是读/写/破坏性分级的唯一来源，同时供给 `tiered_group` 与 `zot schema`。新增命令必须同时改 `cli/registry.py`（保持可观察顺序）和 `cli/safety.py`，否则帮助分组与 schema 会漂移。

## 配置与 Profile

- 源码检出时配置位于 `.zot/config.toml`；安装版（`uv tool install` / pipx）读取 `$XDG_CONFIG_HOME/zotero-cli/config.toml`（默认 `~/.config/zotero-cli/config.toml`）或 Windows 的 `%APPDATA%/zotero-cli/config.toml`，绝不读当前目录下的 `.zot/`。
- 解析优先级：CLI 参数 > 环境变量 > 当前 profile > 默认值。常用环境变量包括 `ZOT_DATA_DIR`、`ZOT_LIBRARY_ID`、`ZOT_API_KEY`、`ZOT_MINERU_TOKEN`、`ZOT_PROFILE`、`ZOT_STATE_DIR`、`ZOT_DEBUG`、`ZOT_NO_UPDATE_CHECK`，以及 `ZOT_AI_*`、`ZOT_EMBEDDING_*`、`ZOT_RERANK_*` 系列。
- `[profile.<name>.<section>]` 覆盖顶层 `[<section>]`；未知 profile 或空白 `[default] profile` 都按 `configuration_error`（退出 3）失败关闭，不回退到旧结构。修复方式为 `zot config profile set NAME` 或 `zot config init --profile NAME`。
- 配置写入必须原子（临时文件 + fsync + replace）、保留 `.bak`、文件 0600、新建目录 0700；无变化时跳过重写。
- `.zot/config.example.toml` 是模板，必须不含任何真实凭据、不含机器相关的绝对路径，并使用中性 profile 名 `main`。本机实际配置使用 `macbook`，这是刻意保留的现状，不要为此把模板改成 `macbook`。
- 不要提交 `.zot/config.toml`、`.zot/state/`、`.workspace/` 或任何凭据；更新代码前先确认 `.gitignore` 已覆盖新增的隐私文件。

## 持久化本地状态

清理日志或临时文件时，以下目录**不得删除**：

- `.workspace/<name>/`：workspace 清单与 SQLite RAG 索引。
- `.workspace/_qdrant/`：本地 Qdrant 向量数据。
- `.zot/state/mineru/`：内容寻址的 MinerU 解析包。
- `.zot/state/paper2zotero/`：批处理断点。
- `log/`：每次运行的产物（`run.log`、`progress.jsonl`、`inventory.json`），可以安全清理。

## 命名规范

- Python 文件、模块、函数、变量和参数使用 `snake_case`；类和异常使用 `PascalCase`；常量使用 `UPPER_SNAKE_CASE`。CLI 命令和选项使用小写 `kebab-case`，例如函数 `summarize_all` 对应命令 `summarize-all`。标识符使用英文；注释和 docstring 的说明文字仍只用中文。
- 名字要准确表达操作和对象。函数优先采用“动词＋对象”，例如 `load_items`、`normalize_metadata`、`resolve_doi`；避免含糊的 `process_data`、`handle_result`。变量使用具体名词，例如 `raw_items`、`fit_parameters`；仅在含义明确时才用 `data`、`result` 等泛称。
- 名字力求简洁，但不牺牲可理解性。局部作用域内可以简短；公开接口和跨模块名称必须能区分对象。保留 `pdf`、`rag`、`bm25` 等通行缩写，不自造 `cfg`、`proc` 等难读缩写，也不添加无用的重复前缀。
- 同一概念始终使用同一个名称，不在不同层交替称为 `items`、`records`、`data`。布尔量优先使用 `is_`、`has_`、`can_` 等正向名称；路径用 `_path`，目录用 `_dir`，集合名称用复数。
- 新代码遵守以上规则；维护旧项目时不为统一风格批量重命名。确需修改公开命令、参数或 API 名称时，同步处理调用方、测试和文档。

## 更新与兼容性

- 实现新需求时以当前确定的接口、代码结构和文本格式为准；不要求兼容旧代码、旧命令、旧参数或旧版输入输出文本格式。任务范围内直接更新原实现和所有内部调用方，不同时维护新旧两套路径。
- 不增加过时别名、兼容包装、双格式解析、静默回退等仅为支持旧版本的代码；删除被替代的旧实现及其无效测试和文档，更新有效测试以验证新行为。不要仅靠删除失败的测试来使检查通过。
- 修改接口或文本格式前，找出受影响的入口、调用方、示例、测试与文档，按新约定同步更新并验证。若会使用户现有数据无法读取、需要迁移或可能造成数据丢失，先说明影响并征得确认；不因“无需兼容”而擅自删除或覆盖用户数据。
- CLI 表面变化时，必须同步更新：schema 可见行为、契约测试（`tests/cli/test_contract*.py`）、本文件、`README.md`、`tools/` 文档与 `skill/zotero-cli/`。

## 辅助脚本

- 新增的独立执行脚本统一放在项目根目录的 `tool/`（单数）；不散放在仓库根目录、`src/`、`tests/` 或其他位置。
- 脚本统一使用 Zsh：文件名使用小写 `kebab-case` 和 `.zsh` 后缀，例如 `tool/run-checks.zsh`；首行使用 `#!/usr/bin/env zsh`。不新增 Bash 脚本或以 `.sh` 命名的脚本。脚本内的说明性注释也只使用中文；shebang 属于解释器指令，按原样保留。
- `tool/` 中的脚本只负责调用与编排，不在其中实现 Python 业务逻辑。可复用的 Python 逻辑仍放在 `src/zotero_cli/`，由脚本通过 `uv run` 调用；CLI 入口属于 Python 包，不视为 `tool/` 中的脚本。
- 修改脚本后至少运行 `zsh -n tool/<脚本名>.zsh` 检查语法，并验证其实际功能。修改涉及旧脚本时，在任务范围内将其更新为 `tool/` 下的 Zsh 脚本，不保留旧脚本作为兼容入口。
- **存量例外**：当前批处理助手仍在 `tools/`（复数），并被 `pyproject.toml` 的 `force-include`、`[tool.pytest.ini_options]` 的 `testpaths` 和 CI 引用。迁移到 `tool/` 时需一并更新这三处，并把对应的 `tools/**/test_*.py` 同步搬迁；未完成迁移前不要在两处并存同名脚本。

## 常用命令

```bash
uv sync --dev                          # 安装开发环境
uv run zot --help                      # 查看命令树
uv run zot schema                      # 机器可读的契约
uv run ruff check .                    # 静态检查
uv run ruff format --check .           # 格式检查
uv run python -m mypy src/zotero_cli tools --exclude '(^|/)test_[^/]*\.py$'
uv run pytest -q -rf                   # 全量测试
uv run pytest -q -m tools              # 仅批处理助手测试
uv build                               # 构建 wheel 与 sdist
```

## 验证顺序

完成实现后按由小到大的顺序验证，只报告真实运行过的结果：

1. 目标行为的定向测试。
2. `uv run python -m mypy src/zotero_cli tools --exclude '(^|/)test_[^/]*\.py$'`。
3. `uv run ruff check .` 与 `uv run ruff format --check .`；只格式化本次改动过的文件，不对无关文件跑全仓格式化。Ruff 0.17 也会格式化 Markdown 中的 Python 代码块，文档示例必须同样通过。
4. `uv run pytest -q -rf`：全量测试必须全绿，0 failed、0 skipped，不使用 skip 标记。
5. 涉及打包、模板或包数据时执行 `uv build`，并实际运行 `uv run zot --help` 确认入口正常。

检查失败时修复后重跑；测试不得调用线上服务。

## Git 提交信息

- 提交信息遵循 Conventional Commits：`类型(范围): 中文说明`；范围可省略，写作 `类型: 中文说明`。类型和范围是协议字段，保留小写英文；冒号后的标题与正文说明只能使用简体中文。文件名、命令、版本号、问题编号等必要标识符可保留原样，但不得用英文句子代替中文说明。
- 按实际目的选类型：`feat` 新增功能，`fix` 修复缺陷，`refactor` 调整代码结构但不改变行为，`perf` 提升性能，`docs` 仅修改文档，`test` 仅修改测试，`style` 仅调整格式，`build` 修改构建或打包配置，`ci` 修改持续集成配置，`chore` 其他维护工作。不要把功能变更笼统写成 `chore`。
- 范围使用简短、稳定的小写英文模块名；多词用 `kebab-case`，例如 `cli`、`rag-index`。标题写清楚具体动作和对象，简洁但不能笼统写“更新代码”“修复问题”。需要解释原因、影响或验证方式时，空一行后写中文正文。
- 示例：`feat(cli): 增加批量导入命令`；`fix(reader): 修复 WAL 快照导致的附件缺失`；`docs: 补充安装说明`；`chore: 更新开发依赖`。
- 一次提交只包含一个可说明的目的。提交前检查实际改动与类型、范围和标题一致；未获明确要求时，不自行执行 `git commit` 或 `git push`。
- 每次更新前，请你更新 `.gitignore` 文件，防止个人隐私信息被保存。

## 文档地图

- `README.md`：面向用户的中文安装、配置与命令说明。
- `AGENTS.md`：本文件，架构、不变量与验证顺序。
- `tests/README.md`：测试布局、标记与全绿要求。
- `tools/README.md`、`tools/paper2zotero/README.md`、`tools/paperrag/Instructions.md`：非 CLI 助手说明。
- `skill/zotero-cli/SKILL.md` 与 `skill/zotero-cli/references/`：随包分发的 agent skill。
- `.zot/config.example.toml`：无凭据、与本机无关的配置模板。

仓库未包含 `docs/` 目录或 MkDocs 配置；不要把这些缺失路径当作事实来源。
