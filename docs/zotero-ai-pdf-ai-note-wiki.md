---
title: Zotero、AI PDF 与 AI Note 代码导览
summary: 从 zot 命令入口追踪 Zotero 读写、MinerU PDF 解析和 AI 笔记生成的调用链、数据边界及失败状态。
last_reviewed: 2026-10-23
source_revision: ab6f21a
source_scope: base revision plus uncommitted working-tree changes
status: source-reviewed
maintainer: 待指定
---

# Zotero、AI PDF 与 AI Note 代码导览

> 阅读范围：`src/zotero_cli/` 的 `zotero`、`ai_pdf`、`ai_notes` 及对应 CLI 入口；基线为 `ab6f21a`，包含当前未提交的修复和内部重构。本文结合源码、CLI schema 和隔离回归测试核对；未执行真实 Zotero Web API、MinerU 或模型请求。源码链接定位文件，正文函数名用于检索，避免行号随改动失效。更新模块后复查对应段落及修订范围。

## 从哪里开始

| 目标 | 入口 | 继续阅读 |
| --- | --- | --- |
| 查询文献、附件或修改 Zotero 库 | [`cli/commands/zotero/items.py`](../src/zotero_cli/cli/commands/zotero/items.py) | [Zotero 读写链](#1-zotero本地读远端写) |
| 读取一份 PDF 的 Markdown、结构化块或章节 | [`cli/commands/ai_pdf.py`](../src/zotero_cli/cli/commands/ai_pdf.py) | [AI PDF 解析链](#2-ai-pdf附件选择解析缓存与输出) |
| 为一个条目生成 HTML 笔记和关键词简记 | [`cli/commands/ai_notes.py`](../src/zotero_cli/cli/commands/ai_notes.py) | [AI Note 生成链](#3-ai-note从所有附件到笔记写回) |
| 查找输入、输出和副作用 | [`cli/app/root.py`](../src/zotero_cli/cli/app/root.py) | [共享契约与数据边界](#4-共享契约与数据边界) |

文档入口：[README](../README.md) · [编码约定](../AGENTS.md) · [Agent Skill](../skill/zotero-cli/SKILL.md) · [文档维护流程](../skill/zotero-cli/references/doc-maintenance.md)。

**概念约定：** `item` 是 Zotero 条目；`attachment` 是其 PDF 附件；`note` 是子笔记；`short-note` 是父条目 Extra 内的一行文本。`ai-pdf` 处理一份选定的 PDF，`ai-note` 处理一个 item 下所有本地可用的 PDF。两者都复用 MinerU 解析包，但只有 `ai-note` 会调用笔记模型并写回 Zotero。

```text
zot zotero ── 本地 Zotero SQLite ──> item、note、attachment、collection
           └─ Zotero Web API ─────> 远端新增、更新、上传、标签、删除
                      │
                      └─ 附件关联与本地 PDF 路径
                                   │
zot ai-pdf ── 选择一个附件 ───────> MinerUParseCache / MinerUClient
                                   │
                                   ├─ document.md、content_list.json、raw/、manifest.json
                                   │
zot ai-note ─ 收集全部可用附件 ────┘
             └─ 文字证据包 → 图像核查 → sections → HTML note → Extra short-note
                                                        └─ Zotero Web API
```

## 1. Zotero：本地读、远端写

### 1.1 命令如何进入代码

[`root.py`](../src/zotero_cli/cli/app/root.py) 将基础操作注册在 `zot zotero` 下；具体命令按对象分布于 `cli/commands/zotero/`。条目入口在 [`items.py`](../src/zotero_cli/cli/commands/zotero/items.py)，集合、PDF、note、tag、trash 有各自的命令文件。CLI 解析参数、选择 profile 和 library，调用访问层或领域函数，最后输出人类文本或 JSON。[`models.py`](../src/zotero_cli/zotero/models.py) 定义 `Item`、`Attachment`、`Note`、`Collection` 等返回对象。

| 操作 | 关键调用路径 | 结果与副作用 |
| --- | --- | --- |
| `search` / `list` | [`search()`](../src/zotero_cli/cli/commands/zotero/items.py) → [`ZoteroReader.search()`](../src/zotero_cli/zotero/access/reader.py) | 只读本地 SQLite；`list` 用空查询词复用搜索。 |
| `read KEY` | [`read()`](../src/zotero_cli/cli/commands/zotero/items.py) → `get_item()` + `get_notes()` | 读取当前本地视图，组装条目及笔记。 |
| `zotero pdf list/path` | [`pdf.py`](../src/zotero_cli/cli/commands/zotero/pdf.py) → `get_pdf_attachments()` / `select_pdf_attachment()` | 只定位附件，不调用 MinerU。 |
| `add --doi/--url` | [`add_cmd()`](../src/zotero_cli/cli/commands/zotero/items.py) → 可选 Crossref → [`ZoteroWriter.add_item()`](../src/zotero_cli/zotero/access/writer.py) | 通过 Web API 创建条目；DOI 元数据解析失败时可创建基础条目并报告警告。 |
| `add --pdf` | [`add_item_from_pdf()`](../src/zotero_cli/zotero/pdf.py) → MinerU 提取 DOI → 创建 item → 上传附件 | 两次远端操作；条目成功而附件失败属于部分成功。 |
| `update` / `tag` / `note` / `collection` | 对应 CLI → `zotero/items.py` 等领域函数 → `ZoteroWriter` | Web API 写入；组合操作不是数据库事务。 |
| `delete` / `trash restore` | 条目删除或 [`restore_from_trash()`](../src/zotero_cli/zotero/access/writer.py) | `delete` 是永久删除；`restore` 只恢复原本已在回收站的远端条目。 |

### 1.2 本地 Reader 的具体逻辑

[`_create_reader()`](../src/zotero_cli/cli/commands/zotero/items.py) 加载 profile、定位 `zotero.sqlite` 并解析 library ID。[`ZoteroReader._connect()`](../src/zotero_cli/zotero/access/reader.py) 在没有非空 WAL 时使用只读连接；检测到 WAL 时，[`_connect_from_copy()`](../src/zotero_cli/zotero/access/reader.py) 复制主库和 WAL/SHM 到私有临时目录重放，以免 `immutable=1` 漏掉 Zotero Desktop 最新的本地改动。Reader 校验支持的 userdata schema，并用 library ID 限制查询，防止个人库与群组库混入。

`search()` 将元数据字段、作者、标签和附件全文的命中 `itemID` 合并，排除回收站条目，然后按集合、类型、排序与上限取回 `Item`。全文检索在旧数据库走分词表；在 Zotero 7 v130 走附件 `.zotero-ft-cache` 的[有界扫描](../src/zotero_cli/zotero/access/reader.py)。因此 `fulltext_limited` 表示全文覆盖可能不足；空结果不能证明未扫描到的附件不含关键词。[`AttachmentResolver`](../src/zotero_cli/zotero/attachments.py) 再将 `storage:`、`file://`、绝对路径或链接附件路径解析到本地文件。`storage:` 相对路径限于当前附件目录，`attachments:` 限于配置的链接附件基准目录；拒绝目录穿越、绝对路径伪装及解析后的 symlink 越界，显式链接附件仍可指向库外文件。

### 1.3 Writer、失败与重试

[`ZoteroWriter`](../src/zotero_cli/zotero/access/writer.py) 封装 pyzotero 的 Web API 调用，不直接写 `zotero.sqlite`；修改后本地 Reader 可能要等 Zotero Desktop 同步才能看到结果。写入错误由 [`ZoteroWriteError`](../src/zotero_cli/zotero/access/errors.py) 携带类型及 `committed` 状态。版本检查和 `If-Unmodified-Since-Version` 用于拒绝过期变更，例如回收站恢复。

显式提供 `--idempotency-key` 的操作可通过 [`begin_mutation()`](../src/zotero_cli/zotero/access/idempotency.py) 在发请求前持久化预留 `(scope, key)`；scope 绑定命令、库和请求摘要，不包含 API key。确认成功后缓存 envelope；确定被拒绝时释放预留；网络中断等结果不明时保留预留，要求先核对远端。这是**避免盲目重复提交**的机制，不代表任意 Web API 调用具有全局恰好一次语义；未提供幂等键的调用也不能得到这层保障。

批量写入服务在 `zotero/items.py` 通过 `_write_error_fields(error, include_committed=True)` 统一生成错误字段。导入、更新、删除和恢复保留各自外层结构及字段；发表状态更新显式设置 `include_committed=False`，仍返回 `code`、`message`、`retryable` 三字段的 `update_error`。这是内部去重，不改变 JSON 输出契约。

### 1.4 集合重组与查询参数

`collection reorganize` 保留整体替换语义：计划中条目的全部旧集合归属（包括计划外集合）替换为本次新建的目标集合，条目和旧集合本身不删除。CLI 在创建 Writer 之前要求交互确认或 `--yes`；`--no-interaction` 不隐含同意，`--dry-run` 的 JSON 与人类文本均明确提示替换范围。帮助页和 schema 将该命令归为 destructive。`search` / `list` 的未知 `--type` 返回 `validation_error`（exit 3），已定义类型无匹配条目时正常返回空结果。

## 2. AI PDF：附件选择、解析缓存与输出

### 2.1 单份 PDF 的调用路径

```text
ai_pdf(KEY, --attachment?)
  → ZoteroReader(db_path, library_id)
  → ZoteroPdfDocuments.select(KEY, attachment_key)
      → get_pdf_attachments(KEY) → select_pdf_attachment() → 检查本地文件
  → PdfParsingService.parse(pdf_path)
      → MinerUParseCache.lookup()：SHA-256 + 模型版本
      → 命中：返回缓存；未命中：MinerUClient.parse_many([pdf_path])
  → read_pdf_content() → Markdown / content_list / outline / section
```

[`ZoteroPdfDocuments.select()`](../src/zotero_cli/ai_pdf/documents.py) 区分 item 不存在、无 PDF、多个附件未指定 key、选中的 PDF 文件缺失；多个候选不会自动选一个。[`ai_pdf()`](../src/zotero_cli/cli/commands/ai_pdf.py) 对 `--outline`、`--section`、`--structured` 做互斥检查，处理领域错误并构造输出。`--outline` 从 Markdown `#` 标题编号，`--section N` 截取该标题到下一个同级或更高级标题；`--structured` 返回 MinerU 的 `content_list.json`。[`content.py`](../src/zotero_cli/ai_pdf/content.py)

### 2.2 MinerU 客户端与缓存

[`PdfParsingService.parse()`](../src/zotero_cli/ai_pdf/parsing.py) 首先查缓存，只有未命中才要求 MinerU token。[`MinerUClient.parse_many()`](../src/zotero_cli/ai_pdf/access/client.py) 验证文件大小、按批取得上传 URL、PUT PDF、轮询批次、下载 ZIP，分别保留各 PDF 的成功或异常。创建批次的 POST 不自动重试：响应丢失时再发可能创建第二个云端任务；可重试的上传 PUT 与下载 GET 则按各自规则处理。[`_upload_batch()`](../src/zotero_cli/ai_pdf/access/client.py)

[`_read_archive()`](../src/zotero_cli/ai_pdf/access/client.py) 要求解析包包含 Markdown 与 `content_list.json`，检查 ZIP 成员数和大小；[`_safe_extract_zip()`](../src/zotero_cli/ai_pdf/access/client.py) 拒绝逃出目标目录的成员路径。缓存 [`MinerUParseCache`](../src/zotero_cli/ai_pdf/access/cache.py) 以 PDF SHA-256 和模型版本定位，使用跨进程指纹锁防止重复解析；写入 `document.md`、`content_list.json`、`raw/` 与带来源哈希和解析信息的 `manifest.json`。再次读取时核对 manifest；不匹配视为未命中。[`_load_result()`](../src/zotero_cli/ai_pdf/access/cache.py) · [`_store()`](../src/zotero_cli/ai_pdf/access/cache.py)

**解析边界：** MinerU 的 Markdown 和结构化块仍是 PDF 的自动提取结果；页码、图表内容和数字若用于科研结论，仍需与原 PDF 核对。`ai-pdf` 本身不生成科学解释，也不写 Zotero note。

### 2.3 锁、下载与缓存发布的验证边界

确认存活的指纹锁持有者不会仅因锁文件年龄被抢占；等待者默认最多等 1,800 秒后报错。确认死亡时立即回收，无法探测存活性时才按 stale 年龄（默认 900 秒）回收。PID 存活只证明进程存在，不证明解析健康。归档 GET 显式使用 `stream=True`，在读取前检查声明长度、读取中检查累计字节，并在成功、失败、超限和重定向路径关闭响应。HTTP 错误不读取整份错误正文。单份与批量未命中缓存且缺 token 时使用 `MinerUTokenMissingError`，ai-note 和 RAG 索引调用方按类型映射配置错误，不依赖错误文案中的英文子串；注入 client 的测试路径和全部缓存命中仍无需 token。

上传/解析上限为 200 MiB，归档下载 512 MiB，单成员 200 MiB，解压累计 1 GiB，ZIP 条目 10,000。轮询最多 360 次、未完成后休眠 5 秒；请求、重试与限流耗时另外累计，不能当作严格的 30 分钟总超时。跨源下载跳转会重新判定来源并移除不应发送的鉴权。

**仍存在的发布边界：** 隔离合成缓存的失败注入已证实，`_store()` 删除旧目标后若 `staging.replace()` 失败，旧缓存会丢失；同一 `MinerUParseResult` 的属性按路径延迟读取，在一次替换前后可能分别读到旧版、新版内容。这是有序读写交错与失败注入证据，不是多进程压力测试。本轮只修复锁、token 分类与下载；缓存发布架构仍待独立方案。

## 3. AI Note：从所有附件到笔记写回

### 3.1 入口与预检查

[`ai_note()`](../src/zotero_cli/cli/commands/ai_notes.py) 将 CLI 选项和进度回调交给 [`execute_ai_note()`](../src/zotero_cli/ai_notes/execution.py)。后者读取 AI 与 Zotero 配置、解析远端写入凭据、处理可选幂等键、打开本地 Reader 并按是否 dry-run 创建 Writer；最后调用 [`analyze_item()`](../src/zotero_cli/ai_notes/analysis.py)。成功 envelope 可缓存供相同请求重放；确定失败释放预留，可能已提交的失败保留预留。

`analyze_item()` 先确认 item 存在。非 dry-run 且已有 `ai/noted` tag 时，默认返回 `already_analyzed`；`--force` 绕过此检查，但会**追加新 note**。`--short-note-only` 转到已有笔记回填分支，不运行完整 PDF 分析。[`analysis.py`](../src/zotero_cli/ai_notes/analysis.py) · [`short_notes.py`](../src/zotero_cli/ai_notes/short_notes.py)

### 3.2 文档与证据是怎样形成的

[`collect_documents()`](../src/zotero_cli/ai_notes/documents.py) 收集父 item 下全部本地可用的 PDF，批量读取 MinerU 解析包，依据附件之间的图注线索区分正文（main）与支撑信息（supplementary），正文优先排序后跨全部附件去重：以长度至少 60 字符的去空白长行集合计算单向覆盖，某文档至少 90% 的长行存在于更长文档时丢弃该副本，等长优先靠前者。不是按 main/SI 分组去重；保留者沿用自身角色。与 `ai-pdf` 不同，`ai-note` 没有 `--attachment` 单选参数。需要先检查附件列表，否则无关 PDF 可能进入同一份模型输入。`resolve_pdf_kinds()` 对单附件返回 main；多附件依次结合图表编号、前置信息、文件名/封面启发式与最长文档回退。三个及以上附件时，部分规则可留下多份 main，不保证任意输入始终恰好一份正文。

[`build_evidence()`](../src/zotero_cli/ai_notes/evidence.py) 遍历 `content_list` 的文字、标题、表格、独立公式、图片和图注，形成带附件 key、main/SI、PDF 页码的文本标签；图片则记录编号和缓存图片路径，并拒绝逃离缓存根目录的路径。分类器根据条目与正文片段选择研究论文、综述或书籍模板；dry-run 不调分类模型，而采用条目类型默认值。[`classification.py`](../src/zotero_cli/ai_notes/classification.py) · [`analysis.py`](../src/zotero_cli/ai_notes/analysis.py)

### 3.3 两阶段模型调用

**第一阶段：文字取证。** [`text_evidence_prompt()`](../src/zotero_cli/ai_notes/stages.py) 发送带来源标签的全文、表格与图注，要求模型返回包含 `source`、`claim`、`conditions`、`data`、`figure_refs`、`unresolved` 的 JSON。[`parse_text_packet()`](../src/zotero_cli/ai_notes/stages.py) 核验必需字段、来源附件/页码、图号与包大小，并在对应原文中提取 `source_excerpt`。它**不验证具体数字或样品条件的真实性**：来源合法不等于科学断言已被证实。

**第二阶段：图像核查与成稿。** [`plan_with_coverage()`](../src/zotero_cli/ai_notes/evidence.py) 为模型规划图像与精确请求字节数。有效正文图必须容纳请求，SI 图先选正文引用者，再考虑其余图片；容量不足时记录未发送图号和原因，并在 prompt 中禁止描述未见图像细节。单图可尝试无损重编码，不修改 MinerU 原图。[`plan_request()`](../src/zotero_cli/ai_notes/evidence.py) 随后 [`visual_note_prompt()`](../src/zotero_cli/ai_notes/stages.py) 合并证据包、来源片段、参考文献和笔记模板；模型输出 `sections` JSON，格式失败可带同组图片重试。模型链只尝试通过请求预算预检的路线，视觉成稿要求图片时不会改成纯文本成稿。[`analysis.py`](../src/zotero_cli/ai_notes/analysis.py) · [`generation.py`](../src/zotero_cli/ai_notes/generation.py) · [`AiClient`](../src/zotero_cli/ai_notes/access/client.py)

标题、元数据、PDF 文本和证据包以 `untrusted` 定界包装，以降低论文文本中的指令干扰；模型请求仍须把论文视为数据。成稿前检查 `sections` 非空且可渲染出正文，再把文字整理并转为 HTML。[`wrap_untrusted()`](../src/zotero_cli/ai_notes/generation.py) · [`render_note()`](../src/zotero_cli/ai_notes/rendering.py)

### 3.4 写回顺序与非原子状态

```text
sections 有效 → 创建 HTML 子 note → 可选写 ai/noted tag
                                     → 可选生成 short-note
                                     → 更新父 item 的 Extra
                                     → 写 ai/keywords tag
```

[`save_analysis_note()`](../src/zotero_cli/ai_notes/persistence.py) 先创建 note 后打标；tag 失败时 note 已存在，报错保留 `committed`，不能直接重跑。[`save_short_note()`](../src/zotero_cli/ai_notes/persistence.py) 更新 Extra 后再打关键词 tag，后者失败会抛出 `committed=True` 的错误；Extra 写入结果不确定时也向上传播，保留幂等预留供远端核对，不缓存为成功结果或补写 `ai/no_keywords`。简记生成失败会尽力标记 `ai/no_keywords`。[`_generate_short_note()`](../src/zotero_cli/ai_notes/short_notes.py) 会规范化模型 JSON 中的文本并验证实际写入的简记格式。这些远端操作**不是整体事务**；命令成功状态也不能替代逐项核对远端 note key、tag 和 Extra。

`--dry-run` 跳过笔记模型调用和 Zotero 写入，但会按需调用 MinerU 解析 PDF，可能发生云端解析请求；展示的图像数量是预检估算。[`analysis.py`](../src/zotero_cli/ai_notes/analysis.py) `--short-note-only` 读取本地已同步 notes，选最近带 AI 标题前缀的 note（无匹配时取内容最长者），从其文本生成 Extra 简记；本地同步延迟会影响这个选择。[`short_notes.py`](../src/zotero_cli/ai_notes/short_notes.py)

## 4. 共享契约与数据边界

| 边界 | 当前代码的约束 | 阅读提示 |
| --- | --- | --- |
| 配置与库选择 | profile、user/group library 分别解析；源码检出与安装版采用不同的配置/状态目录。 | [`config/loaders.py`](../src/zotero_cli/config/loaders.py)、[`config/paths.py`](../src/zotero_cli/config/paths.py) |
| CLI 输出 | JSON envelope 的 `ok` 为 `true`、`false` 或 `"partial"`；可选 `dry_run`、`next`；进度走 stderr。 | [`output/envelope.py`](../src/zotero_cli/output/envelope.py)、[`output/exit_codes.py`](../src/zotero_cli/output/exit_codes.py) |
| 命令风险等级 | read / write / destructive 映射用于帮助页和 schema；`ai-pdf` 分类为 read，但缓存未命中会发起 MinerU 请求并写本地缓存。 | [`cli/app/safety.py`](../src/zotero_cli/cli/app/safety.py) |
| 本地与远端可见性 | Reader 读取本地库，Writer 修改远端库；远端成功后本地可能未同步。 | [`reader.py`](../src/zotero_cli/zotero/access/reader.py)、[`writer.py`](../src/zotero_cli/zotero/access/writer.py) |
| 科研证据 | 来源标签/图号校验不能替代数据真实性、曲线或实验条件核对。 | [`stages.py`](../src/zotero_cli/ai_notes/stages.py) |

**文档核对：** [`README.md`](../README.md) 已同步说明第一阶段不做数值内容的比对与阻断，与 `parse_text_packet()` 一致；来源标签和图号合法不代表数值或实验条件已被程序验证。

## 5. 按问题寻找代码与测试

| 要回答的问题 | 先读代码 | 再读测试 |
| --- | --- | --- |
| 为什么搜索漏掉某篇论文？ | [`reader.search()`](../src/zotero_cli/zotero/access/reader.py)、[`_search_fulltext()`](../src/zotero_cli/zotero/access/reader.py) | [`tests/unit/test_reader.py`](../tests/unit/test_reader.py)、[`tests/unit/test_reader_wal_visibility.py`](../tests/unit/test_reader_wal_visibility.py) |
| 为什么刚写入 Zotero 却读取不到？ | [`ZoteroWriter`](../src/zotero_cli/zotero/access/writer.py)、[`ZoteroReader`](../src/zotero_cli/zotero/access/reader.py) | [`tests/unit/test_writer.py`](../tests/unit/test_writer.py)、[`tests/cli/test_uncertain_retry.py`](../tests/cli/test_uncertain_retry.py) |
| 为什么 PDF 要求指定附件？ | [`ZoteroPdfDocuments.select()`](../src/zotero_cli/ai_pdf/documents.py) | [`tests/cli/test_pdf_selection.py`](../tests/cli/test_pdf_selection.py) |
| MinerU 何时重复解析或缓存失效？ | [`MinerUParseCache`](../src/zotero_cli/ai_pdf/access/cache.py) | [`tests/unit/test_mineru.py`](../tests/unit/test_mineru.py)、[`tests/unit/test_mineru_safety.py`](../tests/unit/test_mineru_safety.py) |
| 图像为什么未被送入笔记模型？ | [`build_evidence()`](../src/zotero_cli/ai_notes/evidence.py)、[`plan_request()`](../src/zotero_cli/ai_notes/evidence.py) | [`tests/unit/test_ai_image_budget.py`](../tests/unit/test_ai_image_budget.py)、[`tests/unit/test_ai_two_stage.py`](../tests/unit/test_ai_two_stage.py) |
| note 已存在但命令报错，能否重跑？ | [`save_analysis_note()`](../src/zotero_cli/ai_notes/persistence.py)、[`execute_ai_note()`](../src/zotero_cli/ai_notes/execution.py) | [`tests/cli/test_ai_note_command.py`](../tests/cli/test_ai_note_command.py)、[`tests/unit/test_idempotency.py`](../tests/unit/test_idempotency.py) |

## 6. 本轮变更与回归证据

| 变更或约束 | 主要测试/核对入口 |
| --- | --- |
| 集合重组保留全量替换，增加确认与 destructive 分类 | [集合命令测试](../tests/cli/test_collection_commands.py) |
| 附件相对路径及 symlink 约束 | [附件解析测试](../tests/unit/test_attachment_resolver.py) |
| 未知 `--type` 返回校验错误 | [搜索命令测试](../tests/cli/test_search_command.py) |
| 批量错误构造去重，保留发表更新三字段形状 | [批量服务测试](../tests/unit/test_batch_write_services.py) |
| Extra 已写入后标签失败继续报错、保留预留 | [AI 分析测试](../tests/unit/test_ai_analysis.py)、[AI Note CLI 测试](../tests/cli/test_ai_note_command.py) |
| 第一阶段不做数值内容硬阻断 | [阶段契约测试](../tests/unit/test_ai_stage_contract.py) |
| 存活持有者不按锁年龄抢占、token 类型化及缓存命中 | [MinerU 测试](../tests/unit/test_mineru.py)、[RAG workflow](../tests/workflows/test_pdf_rag_rag.py) |
| 真实 `requests.Session` 路径延迟读取及响应关闭 | [流式下载测试](../tests/unit/test_mineru_streaming.py)、[鉴权边界测试](../tests/unit/test_mineru_safety.py) |

这些测试使用隔离数据、mock 或本地传输层，证明所断言的代码行为；不证明真实服务可用性、云端解析等价性、模型笔记质量或多进程压力下的全部性质。缓存发布失败和延迟读取边界来自前一轮合成文件系统探针，未增加为发布架构修复。

## 7. 维护规则

- **维护者：待指定。** 暂按“触及相关调用链时同步更新”维护；如果无人负责，本文只作为带修订号的代码阅读索引，不能当成持续更新的运行手册。
- 每个判断优先链接到实现函数；模型效果、网络可用性和远端写入成功需另附当次实际运行证据。改变命令参数、数据契约、缓存格式或写入顺序时，同步检查本文、README、Agent Skill 和相关测试。
- 页面入口是本文顶部表格；同一概念沿用 `item`、`attachment`、`note`、`short-note`，避免同一对象出现多种名字。若内容扩大，再拆成按问题检索的独立页面，并保留本页导航及旧链接。
- 上次源码核对：2026-10-23，基线 `ab6f21a` 加当前未提交改动。下次阅读时先对照当前 `git log -1` 与相关文件差异；版本不同则复查受影响章节。
