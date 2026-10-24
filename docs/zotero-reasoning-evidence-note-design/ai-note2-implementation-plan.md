# AI-NOTE2 实施计划

状态：已实现。基线提交 `e9ce473`，实现位于分支 `feat/ai-note2`；`main` 未包含本功能。本文件记录实现后的实际契约与验证结果，取代实现前的规划版本；真实服务验收（T10）尚未执行。

## 1. 目标与依据

先检查父条目是否有可用的 AI 分析笔记。没有则正常跳过，不解析 PDF、不调用模型、不写 Zotero。有笔记时，一次发送同一父条目的 MinerU 文本 JSON 和 AI 笔记文本，分两步生成完整 TC/EC/RC：第一步生成三链结构，第二步核对每条内容能在冻结 PDF 文本中准确定位。生成一份“元信息＋论证树”的完整笔记，并生成鱼骨状 SVG。发布成功后为父条目添加 `ai/evidence`。TC 组织作者公开论证，EC 和 RC 放在它们实际对应的论点或支持链旁。全部 TC/EC/RC 对象，包括候选、用途和覆盖记录，具有唯一全局 index。

当前展示以 [单篇笔记](ai-note2-single-note-display.md) 为准。科学语义参考 [思维链](thought-chain-structure-design.md)、[证据链](evidence-chain-structure-design.md)、[审查链](review-chain-structure-design.md) 和 [属性字典](three-chain-field-catalog.md)。属性字典中的旧 z3n、bundle 和分链发布段落属于历史方案，不作为本功能的生产格式。

文字采用 ASD-STE100 的清晰写作原则：一个概念使用一个术语，句子明确说明对象、条件和判断。中文稿不宣称符合英文 STE 的词典和字数规则。

首版只接受 `itemType=journalArticle` 的期刊论文；综述、书籍、会议论文等其他类型返回 `validation_error`，不套用本模板。首版以研究论文为主要验收对象。不增加书籍/综述专用模板，也不调用旧分类器强行套用实验论文结构；原文不适用的字段或论证层级明确注明，不补内容。

## 2. 现有 ai-note：复用范围与差异

源码基线：`zotero-cli` 的 `main`，基线提交 `e9ce473`；实现提交位于分支 `feat/ai-note2`。开始开发前需重新检查状态，保留用户后续改动。

| 当前入口或模块 | 核对结果 | ai-note2 的处理 |
|---|---|---|
| `cli/commands/ai_notes.py`、`cli/app/root.py`、`cli/app/safety.py` | `ai-note` 是 write 命令；CLI 负责参数和 envelope | 按同一边界注册独立命令，不在回调写业务逻辑 |
| `ai_notes/execution.py` | 加载配置、创建客户端和 reader/writer、处理幂等 | 复用访问能力和错误契约；新编排支持只生成本地文件 |
| `ai_notes/analysis.py` | 分类 → 文字证据 → 图像成稿 → note → tag/Extra | 不直接调用；新流程无图片、分类和 Extra 简记 |
| `ai_pdf/parsing.py`、`ai_pdf/models.py` | 可取得 PDF 指纹、模型版本和 `content_list.json` 路径 | 复用缓存解析服务；读取后另建本次不可变输入快照 |
| `ai_notes/documents.py` | 收集 PDF、启发式 MS/SI 分类、按长文本重合去重 | 不照搬整套收集；按附件选择、字节指纹去重并保留别名 |
| `ai_notes/evidence.py` | 将块转成带页码的文本，并规划图片 | 不调用图像构建路径；新增文本 JSON 适配器 |
| `zotero/access/reader.py::get_notes` | 按 itemID 排序，返回已转 Markdown 的内容；没有远端版本 | 复用本地 note 读取；记录 key、文本摘要和本地读取范围，不冒充最新修改时间 |
| `ai_notes/access/client.py` | 支持 `figures=[]`、有序 fallback、请求上限和截断检测 | 复用纯文本调用；每次请求及修复重试重新预检预算 |
| `ai_notes/generation.py` | 解析旧 `sections`，含宽松 JSON 解码和内容数量重试 | 不复用其 sections 契约；新结构独立解析、校验和有界修复 |
| `zotero/access/writer.py::add_note`、`add_tags` | 创建 child note、给父条目添加标签；不负责发布回读 | 复用创建与追加标签；回读确切 note 和父条目的 `ai/evidence` |
| `ai_notes/text.py` | 平排化学式与中英间距 | 只作用于显示文本，不改冻结来源和原文偏移 |
| `ai_notes/templates.py` | 模板目录解析、占位符填充、不可信内容包裹 | 新增 `ai_notes2/templates.py` 与独立模板目录，占位符集合不同，不复用旧 sections 模板 |

已实际执行 `uv run zot ai-note --help` 和 `uv run zot schema ai-note`，确认现有选项与 write 分级。未运行旧 `ai-note --dry-run`，因为缓存缺失时它可能调用 MinerU。

## 3. 首版命令契约（已实现）

下列命令均已实现并通过 `uv run zot ai-note2 --help` 与 `uv run zot schema ai-note2` 核对。

```text
uv run zot ai-note2 ITEMKEY --dry-run
uv run zot ai-note2 ITEMKEY --source-note NOTEKEY --local-only
uv run zot ai-note2 ITEMKEY --source-note NOTEKEY
uv run zot ai-note2 ITEMKEY --source-note NOTEKEY --attachment MSKEY --attachment SIKEY
uv run zot ai-note2 ITEMKEY --force --idempotency-key RUNKEY
uv run zot ai-note2 ITEMKEY --reconcile
```

| 参数 | 行为 |
|---|---|
| `ITEMKEY` | 必须是当前库内的父条目，且 `itemType` 必须为 `journalArticle`；不能直接传 note 或 PDF 附件。其他条目类型返回 `validation_error` |
| `--source-note KEY` | 指定同父条目的输入笔记。未指定时，仅在唯一一份可识别的旧 AI 分析笔记存在时自动选用；多份返回 conflict 并列候选 key；没有可用 AI 笔记则正常跳过，不自动运行 ai-note |
| `--attachment KEY`（可重复） | 限定 PDF 集合。未指定时清点该父条目的本地 PDF；有缺失附件或解析失败时不静默丢弃，输出可操作错误 |
| `--dry-run` | 不调用分析模型、不写 Zotero；预览所选笔记、附件、输入范围、字符量及请求预算。缓存缺失仍可能调用 MinerU并写解析缓存，帮助文案必须说明 |
| `--local-only` | 实际调用模型，生成本地 JSON、Markdown、HTML 和 SVG；不创建 writer，不要求写权限，不改 Zotero |
| `--output-dir DIR` | 设置新的输出目录；默认在当前配置的 state 下 `ai_note2/<库>/<父条目>/<run_id>/`。不覆盖非本次文件，拒绝已有目标和不安全的路径 |
| `--force` | 已有本功能发布记录时允许新建一次运行并追加新 note；不覆盖或删除旧 note，不消除未知写入结果 |
| `--idempotency-key KEY` | 同一范围与输入的确认成功可重放；进行中或写入未知时拒绝重复创建。dry-run 忽略写入幂等键 |
| `--reconcile` | 对已知 note 只补未完成的标签/回读步骤；创建响应未知时按运行标识查找远端子笔记，唯一匹配才继续。无匹配仍阻止重试，不重新生成或创建 note。用于 `committed=true` 后的恢复 |
| `--progress-lines` | 进度走 stderr；JSON 模式 stdout 仅一个 envelope |

沿用根命令的 `--profile`、库类型和 group 选项。模型配置和写入凭据的必需性检查放在笔记检查之后；无 AI 笔记时即使这些配置缺失也正常跳过。首版复用 `[ai_notes]` 与 `ZOT_AI_*` 模型配置，不新增重复凭据配置。`max_extracted_chars` 约束实际送入的来源文字与旧笔记文字总量；最终序列化请求仍独立检查上下文与字节预算。

旧 AI 笔记识别支持 ATX、Setext 与 HTML，只读取首个标题及现有 `NOTE_TITLE_PREFIX`；不在全文任意位置搜索前缀，不选“最长笔记”，不从 itemID 顺序推断最新版本。自动候选排除 `ai-note2` 标题。显式选其他笔记时仍要求属于当前父条目、内容非空，且不能把本功能输出递归当作旧 AI 分析输入。

默认发布只新增一份 child note，标题为 `框架分析 - <父条目标题>`。note 回读确认后为父条目追加 `ai/evidence`，保留其他标签。该 tag 表示已生成三链笔记，不表示证据均可靠。首版不写 Extra 或关键词简记。是否已生成以本地发布账本和远端确切 note 为依据，不能复用 `ai/noted`。账本缺失或远端发现未登记的同类笔记时提示冲突核对，不自动以标题认同对象。

## 4. 输入快照与来源定位

1. 先验证父条目并检查 AI 分析笔记。自动选择时没有可识别且非空的 AI 笔记，返回 `status=skipped`、`reason=no_ai_note`，exit 0；不检查或解析 PDF、不创建 AI 客户端或 writer、不分配运行序号、不预留写入幂等，也不创建生成文件。显式指定的 note key 不存在、为空或不属于该父条目属于参数错误，不能伪装成正常跳过。有可用笔记后，再验证附件归属并解析 PDF。
2. 按 PDF 内容 SHA-256 去除字节完全相同的副本，同时保留全部附件 key 别名。相似文本不作为删除来源的依据。
3. 复用 MS/SI 启发式结果，但保存判断依据。不能凭唯一附件就保证文献身份正确；不确定时保留 UNKNOWN/待复核，混合附件允许 COMBINED。模型不得改写真实附件身份。
4. 一次读取并复制本次使用的 MinerU JSON。计算输出摘要，记录实际模型版本和适配器版本；上游没有解析器精确版本时留空，不编造。生成、修复和渲染只读本次快照，不延迟重读可被替换的缓存。
5. 为冻结原始块分配 `block_id`，附附件 key、来源集和从 1 起的 PDF 页码。原文保留 LaTeX、表格 HTML、表头和脚注；不先做数字拼接或空格压缩。无法解析的表格结构保留原文和缺损状态，不造单元格关系。
6. 发送给模型的是保留原块类型与顺序的文本 JSON 投影，以及所选 AI 笔记文本。图像块只保留文字图注；图片路径、URL、base64 和像素均不进入请求。原始解析 JSON 可留本地快照。
7. 模型引用已有 `block_id` 和可选原文区间，程序从冻结块填入原文片段。缺区间可引用整个块，不要求生成句子与原文逐字匹配。区间越界和未知来源 ID 是结构错误；科学数值、条件解释及引句相关性由模型和人工复核，不作字面硬阻断。
8. 现有 AI 笔记作为带 note key 和内容摘要的辅助输入。它不能代替 PDF 定位，也不能让新模型声称读取图像。父条目元信息只取父条目。

整个输入超过字符、上下文或请求体预算时，明确失败并保留预检信息；不静默截断正文、SI 或旧笔记。首版不做隐式分块摘要，防止悄悄降低覆盖。

## 5. 两步生成：先成稿，再核对原文

### 5.1 提示词模板

提示词不再硬编码在 Python 中，而是位于 `src/zotero_cli/ai_notes2/templates/`，随包分发：

- `evidence-chain.md`：第一步生成三链结构的模板。
- `verification.md`：第二步核对陈述与原文的模板。

`ai_notes2/templates.py` 负责路径解析、占位符填充与不可信内容包裹。占位符用正则替换而非 `str.format`，因此论文正文中的花括号不会被误解释；缺失值写“未知”。所有论文内容均按 `<untrusted-...>` 包裹。

### 5.2 第一步：生成三链

同一请求同时发送完整文本 JSON 投影、已有 AI 分析笔记、父条目元信息和结构契约。模型一次返回完整 `thought`、`evidence`、`review`，包含 TC 论点/支持链/候选、EC 结果/处理/同源/联合/用途/候选，以及 RC 审查/覆盖/命题评议和实际需要的建议。不拆分提取与审查。

### 5.3 第二步：核对原文可定位性

`ai_notes2/verification.py` 分两层执行：

1. 程序层：每个正式对象的每条来源定位必须指向本次冻结块，`text_excerpt` 与冻结块（含 span）逐字一致；`figure_refs` 必须在该附件图注中出现。不一致直接失败，不发布。派生记录（RC-A/RC-S 等）可以没有自己的定位，但必须引用其他已定位对象。
2. 模型层：把对象清单与冻结文本块发给模型逐条复核，判定 `支持 / 部分支持 / 不支持 / 无法核对`；未知对象 ID、遗漏、重复或非法状态视为核对结果无效。

`不支持` 等结论不阻止发布，但写入 `run.json` 与笔记的“第二步核对”段，标记为待人工复核；核对只判断不改写三链内容。

最多三级论点，推理桥属于支持链。节点出处、支持链出处和作者用途出处分别填写。结果清楚但方法缺失时保留结果和字段缺失状态；联系未明确时用候选，不补桥。保留文献依据和完全未关联的结果，不为整齐强挂到某个论点。

审查必须有主要对象、单一领域、具体判准、依据、范围和结论。区分原文问题、解析问题、整理问题和关联问题。保留正向材料、相反材料、作者回应及未解项；缺少信息不等于论文有错。正向评议不能由空问题表推导。RC 只引用 TC/EC，不用审查者建议改写作者命题。

覆盖范围先列具体单位，再标未检查、已检查、当前不可检查或不适用。已检查必须引用该范围实际完成判断的 RC-Q。无法核对图内信息时说明边界，不读取图像或默认联网查外部文献。

### 输出修复与停止条件

第一步正常成功路径只有一次分析请求。仅在 JSON 或结构校验失败时，最多增加一轮结构修复请求；仍使用原输入和错误路径，完整返回三链后重新全量校验。第二步核对本身不重试修复。网络重试、fallback 和结构修复可能增加实际 HTTP 请求数，但不增加独立生成阶段。记录实际模型与服务返回的 usage；没有返回时写未知。

JSON 无效、必要字段类型错、引用悬空、图循环或截断时不发布。合法的 null、候选和“当前无法判断”不视为生成失败。禁止用数值、文本字面比对或固定内容数量作为失败条件。最终 JSON 严格有效；不直接复用旧 `strict=False` 解析结果而跳过校验。

## 6. 数据、身份和校验

规范结果采用一个 `Note2Document`，含 `schema_version`、输入清单、来源块映射、`thought`、`evidence`、`review` 和对象登记表。文档只存一份权威内容；树形笔记、索引和鱼骨图均由它派生。

先将旧属性字典的当前科学对象字段固化为首版 JSON Schema（拟定 `ai-note2/1.0`），明确排除历史 z3n/bundle 容器。程序可用临时 ID 接收模型结果，再统一分配类型 ID 和全局 index；所有引用同时改写，不让模型生成 Zotero key 或最终全局号。

全局 index 为 `<父条目 key>-<运行序号四位>-<对象序号四位>`。运行序号在库＋父条目范围内原子预留；对象序号跨 TC/EC/RC 类型唯一，超过四位自然扩展。库身份单独保存，以免 group/user 同 key 冲突。已写入本次结果的号不因显示重排改变；失败序号不复用。`--force` 新运行使用新序号，不承诺跨次重提取自动对齐旧对象。候选接受生成新的正式对象号；bridge 用支持链 index＋`.bridge`。

硬校验只控制结构和引用：Schema、受控枚举与状态组合、真实来源 ID、区间边界、唯一 ID/index、端点和对象类型、TC 深度/方向/无环、联合成员一致、EC 处理无环、候选不进入正式支持图、RC 覆盖和依据引用闭合。原文内容、样品解释和数值合理性不由正则或字面匹配裁决。

当前术语固定：`research_origin=实验依据|文献依据|未明确`；`primary_aspect` 包含制备/表征/性能/理论/机制。“实验依据”不保证是实验测量；`result_type` 另记。机制内容类别不自动生成机制用途。

## 7. 单篇笔记与鱼骨图

本地先生成 `analysis.json`、`note.md`、`note.html`、`tree.svg` 和 `run.json`。日志与清单不得含凭据；内容文件放受保护的 state 目录，不进入 Git。输入快照、提示词版本、实际调用信息与第二步核对结论在本地记录，不扩写用户已精简的可见元信息表。Markdown/HTML 显示可读字段，完整嵌套结构保存在 `analysis.json`，不将 JSON 代码块直接写入 Zotero 笔记。

Markdown/HTML 使用当前展示顺序：元信息 → 核心论点 → 分论点 → 支持链、证据、用途、审查与下级理由 → 就地评议。共享论点与证据只定义一次，其他分支引用同一 ID。无可靠来源的占位草稿不能作为正式发布对象；有来源的候选可以展示。模型不得输出 HTML/CSS，程序负责转义和渲染。

SVG 采用鱼骨状布局：主干指向鱼头的核心论点；分论点在主干两侧形成主骨，论证理由与相关证据沿支骨展开。推理桥标在对应支持链上，不另造第四级论点。多个核心论点分别画鱼骨单元，不因布局新增共同结论。

鱼骨图由规范对象和明确关系生成，不用传统因果鱼骨图的布局暗示已证明因果。主干中仅用于排版的部分使用中性底轨，不作为可靠性线条。证据用途仍精确指向论点、支持链整体或 `.bridge`；同一对象多处引用时沿用其全局 index。审查、评议和覆盖用细灰线旁挂并标注类型，不作为证据支持。候选明确标注，完全未关联的结果保留在笔记中，不为填满鱼骨而强建关系。

线型按用户要求：实线＝指定范围内支持可靠；虚线＝存疑或尚未核实。仅有格式通过、出处存在或“未发现明确问题”不足以画实线。实线须有覆盖该具体关系和范围的正向材料、实际 RC-Q/RC-S 和限定性 RC-A；没有对应审查时默认虚线，并标待核。候选不能因线型变实就升级为正式关系。派生线型不新增可靠性评分。

Zotero child note 使用 HTML 树状缩进和可见 ID；鱼骨图首先作为本地 `tree.svg` 交付。首版不依赖 Zotero 对 SVG、脚本、折叠或锚点的支持。可选内部跳转失败时仍能按可见 ID 查找。

## 8. 持久化、幂等与发布

1. 在配置的 state 根内建立独立 `ai_note2` 账本，记录输入摘要、运行序号、执行状态、对象登记和本地文件摘要；不要混用旧 ai-note 的幂等命名空间。
2. 生成产物先写临时文件，校验成功后原子替换本次文件；保存 `prepared → generated → validated → publishing → tagging → verified`，失败另记 `reconcile` 或 `failed`。中断后的候选产物可以检查，不伪装已发布。
3. 同一幂等键与不同输入摘要冲突时返回 conflict，不重放旧结果。成功重放不再调用模型、创建 note 或重复写标签；发布未知时 `--force` 和新键都不能绕过该运行的对账锁。
4. 完成本地校验、渲染和文件保存后才发 Zotero 创建请求。`--local-only` 到此结束。
5. 创建 note 后按返回 key 用 Web API 回读；核对库、父条目、note 类型、可见运行身份和主要对象 ID。允许 Zotero 等价的 HTML 排版规范化，不逐字比对科学文本。回读不一致或失败，保存已知 key 和 `committed=true`，状态为需要对账，不重新创建。
6. 若创建请求结果未知，先用运行标识查询该父条目的远端 notes。唯一匹配再回读；无匹配不自动证明未写入，多匹配报冲突。明确拒绝且肯定未写入才允许释放写入预留。
7. note 回读确认后，复用 `add_tags` 给父条目追加 `ai/evidence`，并用 Web API 回读父条目确认标签存在。保存 `note_key`、note 回读结果和 tag 写入/回读状态，二者均确认后才记 `verified`。标签已存在时不重复添加，但仍须核对本次 note；不能仅凭标签跳过或宣称本次完成。
8. note 已创建但标签写入失败、结果未知或回读失败时，保留 `note_key`、已完成步骤和 `committed=true`，不缓存整体成功，不释放写入预留。恢复时用 `zot ai-note2 ITEMKEY --reconcile` 先对账，确认 note 已存在后只补标签，不重新生成或创建 note；不删除已有 note 或其他标签。
9. note 或 tag 成功但本地账本保存失败也属需要对账，不能宣称整体事务成功。源码输出保留，远端对象不删除。返回同步提示；本地 SQLite 不用于证明刚完成的远程写入。

## 9. 代码范围与实施结果

实现位于 `src/zotero_cli/ai_notes2/`，命令为 `cli/commands/ai_notes2.py`：

| 文件 | 职责 |
|---|---|
| `models.py` | `ai-note2/1.0` 结构契约与 JSON Schema |
| `validation.py` | 结构、引用、图、覆盖校验与全局 index 分配 |
| `inputs.py` | 条目类型限制、旧笔记选择、PDF 去重、冻结来源块 |
| `templates.py` + `templates/` | 提示词模板解析与不可信内容包裹 |
| `generation.py` | 第一步生成及最多一轮结构修复 |
| `verification.py` | 第二步：来源核对与模型逐条复核 |
| `rendering.py` | Markdown、HTML、鱼骨状 SVG 与原子写文件 |
| `persistence.py` | 独立账本、运行预占、note/tag 回读与对账 |
| `execution.py` | 编排 dry-run、local-only、发布与 `--reconcile` |
| `errors.py` | 带 `committed` 语义的类型化错误 |

| 顺序 | 单项交付 | 实现状态 |
|---|---|---|
| T1 | `Note2Document` Schema、引用类型和人工 fixture | 已完成；`test_ai_note2_contract.py` 70 项 |
| T2 | 笔记优先检查与 `inputs.py::collect_inputs()` | 已完成；`test_ai_note2_inputs.py` |
| T3 | 运行登记与 ID 分配 | 已完成；并入 `test_ai_note2_persistence.py` |
| T4 | 第一步生成、预算与结构修复 | 已完成；`test_ai_note2_generation.py` |
| T5 | 结构、来源、图和覆盖校验 | 已完成；并入 `test_ai_note2_contract.py` |
| T6 | Markdown/HTML 与鱼骨 SVG 渲染 | 已完成；并入 `test_ai_note2_generation.py` |
| T7 | note 创建/回读、tag 追加/回读及对账恢复 | 已完成；`test_ai_note2_persistence.py` |
| T8 | CLI、write 分级、schema、错误和 envelope | 已完成；`tests/cli/test_ai_note2_command.py` |
| T9 | 第二步核对与文档同步 | 已完成；`test_ai_note2_verification.py`，README/AGENTS/Skill 已更新 |
| T10 | 真实服务验收 | 未执行；需指定授权条目 |

依赖未新增：未引入 `jsonschema`，未引入 Markdown 输入解析器。`.gitignore` 已加入 `ai_note2/`、`.ai-note2/`、`*.ai-note2-run.json`。研究输入、模型响应、运行状态和凭据均不入库；未迁移旧 ai-note 的历史笔记，未触碰其他 Hermes profile 或共享安装。

## 10. 验收与检查

- 无可用 AI 笔记时返回正常 skipped；不要求模型或写入凭据，不触发 MinerU、模型、Zotero 写入或生成文件。
- 非 `journalArticle` 条目在读取笔记和 PDF 之前返回 `validation_error`，不触发 MinerU 或模型调用。
- 第一步返回完整 TC/EC/RC，所有请求均无图片 parts；第二步程序层确认每条来源与原文片段来自冻结块，模型层给出逐条判定。
- 最终只生成一份含元信息和交织三链的 note，以及鱼骨状 SVG；每个对象有唯一全局 index，bridge 不变成第四层。证据可靠路径用实线，存疑/尚未核实用虚线；审查挂线与排版底轨不冒充支持路径。
- 发布成功须回读确认 note 和父条目 `ai/evidence`，并保留其他标签。无 AI 笔记跳过、dry-run 和 local-only 均不写 tag；标签不代表科研可靠性认证。
- 每个正式 TC/EC 对象均可回查冻结来源。所有候选、缺失、相反材料和未检查范围保留，不伪造“已检查”。
- 文本、Schema 和图通过只证明结构合格；实际质量另抽查命题限定、条件、单位、用途相关性和相反材料。正文与 SI 冲突不得任意挑一方。
- 任一模型/校验/渲染失败均不写 Zotero；远端结果未知保留对账状态；不自动重试创建，不覆盖旧笔记。
- 旧 ai-note 回归测试通过；文档、CLI help 和 schema 一致。当前实现已在分支 `feat/ai-note2` 提交。

实现后的检查命令（已全部实际运行）：

```text
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest -q
uv build
uv run zot ai-note2 --help
uv run zot schema ai-note2
```

真实质量验收建议使用：正文＋SI＋旧 AI Note 的研究条目，以及一个文本缺损/条件不全的条目。先 `--local-only` 生成并人工核对来源，不要求固定论点数或指定科学结论。真实 Zotero note/tag 写入验证必须单独指定目标条目；计划批准不等于已发生发布。

## 11. Review Focus

以下是实现后仍需重点检验的输入。单元测试覆盖结构契约，但不将测试通过当成真实论文质量验证；这些场景需要人工抽查。

| 输入或失败模式 | 预期行为 | 负责 |
|---|---|---|
| 旧 note 仅含图像观察，PDF 文字无报告 | 保留待核线索，不能生成伪 PDF 原文或视觉核验结论 | T2 / T4 / T5 |
| 短 SI 与正文高度重合、或单个混合附件 | 不用文本相似度丢来源；保存角色依据和不确定性 | T2 |
| 笔记/缓存读取后用户同步或替换输入 | 本次固定快照继续使用；下次输入摘要变化，不错误重放 | T2 / T3 |
| 某个 RC-Q 正向，但评议范围不覆盖整条支持链 | 不把局部“无问题”投影为整链可靠实线 | T5 / T6 |
| note 已创建，tag 写入/回读或状态落盘失败 | 返回已知远端 key 和需要对账；恢复时只补未完成步骤，不追加重复 note | T7 / T8 |

## 12. 首版取舍

已采用：两步纯文本生成、无可用 AI 笔记正常跳过、只接受 `journalArticle`、唯一旧 note 自动选择/多份显式指定、默认新增一份 note、本地生成可独立运行、写 `ai/evidence` 但不写 Extra、鱼骨图作为本地 SVG，完整历史对象编辑与跨运行身份对齐暂不实现。

本次审查后的检查：`ruff check`、`ruff format --check`、`ty check`、`pytest`（2399 项）、`uv build` 均通过，`zot ai-note2 --help` 与 `zot schema ai-note2` 正常。真实条目 `GFTEK9P6` 的 dry-run 已验证 Setext 旧笔记自动选择。真实服务验收（T10）已尝试，但尚未完成生成和发布；超时、预算不足和服务额度错误均不算通过。
