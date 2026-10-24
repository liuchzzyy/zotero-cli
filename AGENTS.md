# AGENTS.md

本文件是本仓库唯一的指导文件，面向在 `zotero-cli` 中工作的编码代理。先读本文再动手；命令面以运行中的 CLI（`zot --help`、`zot <子命令> --help`、`zot schema`）为准，不依赖散文描述。

## 适用范围

本文件用于指导 Python CLI 项目的新增与修改。维护已有项目时，先了解现有结构和调用关系；在本次任务范围内直接更新受影响的实现，不为保留旧行为添加兼容层，也不批量改动无关文件。

## 注释语言

- Python 代码中的独立注释、行尾注释和 docstring 的说明文字一律使用简体中文，不写英文说明或中英对照。
- 代码标识符、库名、命令，以及 `# noqa`、`# type: ignore` 等工具指令保持原样；它们不是说明文字。
- 注释重点解释意图、约束和非显然的原因，不逐行复述代码；修改逻辑时同步更新相关注释。

## 技术选型

- 使用 CPython 3.12 系列。在 `pyproject.toml` 中设置 `requires-python = ">=3.12,<3.13"`，在 `.python-version` 中写入 `3.12`；不限定为单一补丁版本 3.12.0。
- 使用 `uv` 创建项目、管理依赖与虚拟环境、运行命令及构建发布包。保留 `pyproject.toml`、`.python-version` 和 `uv.lock`，不混用其他依赖锁文件。
- 新建 CLI 使用 Typer 定义命令和参数，在 `pyproject.toml` 的 `[project.scripts]` 中注册入口。命令回调只负责参数解析和调用，把核心逻辑放入可独立测试的函数。
- 使用 Ruff 进行检查与格式化，并在 `pyproject.toml` 的 `[tool.ruff]` 中设置 `target-version = "py312"`。

## 项目目录结构

- 采用 `src/zotero_cli/` 布局；按职责或业务领域组织 Python 文件，不把所有模块堆在包根目录或一个笼统的子目录中。目录名使用 `snake_case`，名称须能说明该目录管理什么。
- 本项目按以下结构组织代码与测试：

  ```text
  src/zotero_cli/
  ├── __init__.py
  ├── cli/                 # 命令入口、参数解析与终端输出
  │   ├── app/             # CLI 根入口、全局选项提升与统一错误边界
  │   ├── commands/        # 子命令实现（按命令组划分）
  │   └── renderers.py     # 面向终端的表格与富文本渲染
  ├── zotero/              # Zotero 领域模型与应用服务（条目、笔记、集合、标签等）
  │   └── access/          # 底层访问封装（SQLite 读取器、Web API 写入器、幂等缓存）
  ├── pdf_rag/             # PDF RAG 索引、向量库与检索服务（含 providers/）
  ├── ai_pdf/              # MinerU 解析与文档提取服务（含 access/）
  ├── ai_notes/            # AI 笔记生成流水线与提示词（含 access/、templates/）
  ├── config/              # 配置加载、路径与上下文解析
  └── output/              # 跨层契约（envelope、退出码、数据脱敏、进度流）
  tests/
  ├── cli/                 # 命令与输出契约测试（使用 CliRunner）
  ├── unit/                # 领域层与底层访问单元测试（绝不调用 CLI）
  ├── workflows/           # 跨模块端到端业务流程测试
  └── sample_data/         # 测试用 SQLite、PDF 等样例数据
  ```

- CLI 层调用核心逻辑，不把业务流程写进命令回调；领域层不依赖 `cli/`。同一职责的文件放在一起，测试目录严格区分层级，使读者能从目录和文件名直接定位功能。
- 只有出现真实职责时才建立相应目录，不创建空目录或多余嵌套；小功能可以先放在一个明确命名的模块中，职责扩展后再拆分。不要用 `utils/`、`misc/` 等泛称容纳不同领域的代码，也不要为每个函数单独建文件。
- 维护已有项目时，在任务涉及的范围内调整不合理的目录结构；移动文件时，同步更新导入、入口、测试和文档，并运行相关验证。

## 业务契约与文档同步

- 命令风险等级由 `src/zotero_cli/cli/app/safety.py` 定义。`collection reorganize` 属于 destructive：整体替换计划内条目的全部旧集合归属（包含计划外集合），不删除条目和旧集合；执行前须 `--yes` 或交互确认，dry-run 必须说明替换范围。不要改成合并语义。
- `search` / `list` 未知 `--type` 报 `validation_error`；附件 `storage:` / `attachments:` 的相对路径必须受各自根目录约束，检查 Windows 路径伪装和 symlink 越界。
- 运行策略默认值只在 `src/zotero_cli/config/defaults.toml` 定义；模块、CLI、provider 与脚本读取 `config.runtime.section()`，不得再写第二份默认值。优先级：显式 CLI > env > profile > 顶层 TOML > 内置默认。
- 未知配置键、空模型链、错误类型必须报 `configuration_error`；显式 `[]`/`0`/`false` 不得被 `or` 回退覆盖。`config show` 必须列出每个键的值、来源与单位（凭据脱敏）。
- AI Note 共用预算不设固定输出预留；仅将明确配置的 `max_tokens` 计入预算，`0` 时不发送该 API 参数，不等于无限输出。
- AI Note 第一阶段只检查 JSON 结构、必要字段、来源标签、图号和预算；不得加入具体数值或文本内容的字面硬阻断。来源合法不能证明数值、样品或实验条件已验证。
- AI Note 写回不是整体事务。note 已创建后标签失败、Extra 已写入后关键词标签失败，都须保留 `committed=True` 并要求核对远端；不得缓存为成功或盲目补写 `ai/no_keywords`。只有确定未写入的失败才释放相应幂等预留。
- `ai-note2` 必须先检查旧 AI 分析笔记：没有则正常 `skipped`，不解析 PDF、不调用模型、不写 Zotero；多份候选是 conflict。发布须回读 note 与父条目 `ai/evidence` 才记 `verified`；note 已创建但标签、回读或账本失败时保留 `committed=True` 与已知 note key，恢复时只补未完成步骤。其账本位于状态根 `ai_note2/`，不复用 `ai-note` 的幂等命名空间。鱼骨图线型只表达审查范围内的支持可靠性，缺审查时默认虚线。
- MinerU 缓存命中无需 token；缺失 token 使用 `MinerUTokenMissingError`，调用方按类型分类。确认存活的锁持有者不得仅按年龄回收；归档 GET（含重试）须 `stream=True`、有界读取并关闭响应，跨源跳转重新检查鉴权。
- MinerU staging 到目标的替换不等于发布事务：删除旧目录后的失败可丢失旧缓存，按路径延迟读取也无快照保证。不要把它与 PDF RAG 候选索引的原子发布混为一谈。
- 批量写入错误字段复用 `zotero/items.py::_write_error_fields()`；`apply_publication_updates()` 保留不含 `committed` 的三字段 `update_error`，不得因内部去重改变输出契约。
- 行为变更同步 [README](README.md)、[仓库 Skill](skill/zotero-cli/SKILL.md)及相关参考文档。只维护本仓库副本；修改其他 Hermes 档案或共享安装需另有明确指令。
- 验证使用隔离 fixture、mock 和临时目录；不把测试通过写成真实 Zotero、MinerU 或模型质量验证。真实服务验证须符合当次授权，记录实际请求与写入范围。

## 辅助脚本

- 新增的独立执行脚本统一放在项目根目录的 `tool/`；不散放在仓库根目录、`src/`、`tests/` 或其他位置。
- 脚本统一使用 Zsh：文件名使用小写 `kebab-case` 和 `.zsh` 后缀，例如 `tool/run-checks.zsh`；首行使用 `#!/usr/bin/env zsh`。不新增 Bash 脚本或以 `.sh` 命名的脚本。脚本内的说明性注释也只使用中文；shebang 属于解释器指令，按原样保留。
- `tool/` 中的脚本只负责调用与编排，不在其中实现 Python 业务逻辑。可复用的 Python 逻辑仍放在 `src/<package_name>/`，由脚本通过 `uv run` 调用；Typer CLI 入口属于 Python 包，不视为 `tool/` 中的脚本。
- 修改脚本后至少运行 `zsh -n tool/<脚本名>.zsh` 检查语法，并验证其实际功能。修改涉及旧脚本时，在任务范围内将其更新为 `tool/` 下的 Zsh 脚本，不保留旧脚本作为兼容入口。

## 命名规范

- Python 文件、模块、函数、变量和参数使用 `snake_case`；类和异常使用 `PascalCase`；常量使用 `UPPER_SNAKE_CASE`。CLI 命令和选项使用小写 `kebab-case`，例如 Python 函数 `publication_status` 对应命令 `publication-status`。标识符使用英文；注释和 docstring 的说明文字仍只用中文。
- 名字要准确表达操作和对象。函数优先采用“动词＋对象”，例如 `extract_doi`、`export_pdf`、`load_workspace`；避免含糊的 `process_data`、`handle_result`。变量使用具体名词，例如 `raw_content`、`item_metadata`；仅在含义明确时才用 `data`、`result` 等泛称。
- 名字力求简洁，但不牺牲可理解性。局部作用域内可以简短；公开接口和跨模块名称必须能区分对象。保留 `doi`、`pdf`、`rag` 等通行缩写，不自造 `cfg`、`proc` 等难读缩写，也不添加无用的重复前缀。
- 同一概念始终使用同一个名称，不在不同层交替称为 `document`、`paper`、`item`（Zotero 数据记录一律称 `item`）。布尔量优先使用 `is_`、`has_`、`can_` 等正向名称；路径用 `_path`，目录用 `_dir`，集合名称用复数。
- 新代码遵守以上规则；维护旧项目时不为统一风格批量重命名。确需修改公开命令、参数或 API 名称时，同步处理调用方、测试和文档。

## 更新与兼容性

- 实现新需求时以当前确定的接口、代码结构和文本格式为准；不要求兼容旧代码、旧命令、旧参数或旧版输入输出文本格式。任务范围内直接更新原实现和所有内部调用方，不同时维护新旧两套路径。
- 不增加过时别名、兼容包装、双格式解析、静默回退等仅为支持旧版本的代码；删除被替代的旧实现及其无效测试和文档，更新有效测试以验证新行为。不要仅靠删除失败的测试来使检查通过。
- 修改接口或文本格式前，找出受影响的入口、调用方、示例、测试与文档，按新约定同步更新并验证。若会使用户现有数据无法读取、需要迁移或可能造成数据丢失，先说明影响并征得确认；不因“无需兼容”而擅自删除或覆盖用户数据。

## Git 提交信息

- 提交信息遵循 Conventional Commits：`类型(范围): 中文说明`；范围可省略，写作 `类型: 中文说明`。类型和范围是协议字段，保留小写英文；冒号后的标题与正文说明只能使用简体中文。文件名、命令、版本号、问题编号等必要标识符可保留原样，但不得用英文句子代替中文说明。
- 按实际目的选类型：`feat` 新增功能，`fix` 修复缺陷，`refactor` 调整代码结构但不改变行为，`perf` 提升性能，`docs` 仅修改文档，`test` 仅修改测试，`style` 仅调整格式，`build` 修改构建或打包配置，`ci` 修改持续集成配置，`chore` 其他维护工作。不要把功能变更笼统写成 `chore`。
- 范围使用简短、稳定的小写英文模块名；多词用 `kebab-case`，例如 `cli`、`data-import`。标题写清楚具体动作和对象，简洁但不能笼统写“更新代码”“修复问题”。需要解释原因、影响或验证方式时，空一行后写中文正文。
- 示例：`feat(cli): 增加批量导入命令`；`fix(data-import): 修复空文件导致的解析失败`；`docs: 补充安装说明`；`chore: 更新开发依赖`。
- 一次提交只包含一个可说明的目的。提交前检查实际改动与类型、范围和标题一致；未获明确要求时，不自行执行 `git commit` 或 `git push`。
- 每次更新前，请你更新 `.gitignore` 文件，防止个人隐私信息被保存。

## 建立与验证

在新项目目录中执行：

```bash
uv init --package --python 3.12
uv python pin 3.12
uv add typer
uv add --dev ruff ty
uv sync
```

完成实现后运行相关测试，并执行：

```bash
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest -q
uv build
```

实际运行注册的 CLI 入口，确认 `--help` 正常输出。检查失败时修复后重跑；只报告真实运行过的结果。
