# 三链 Note 属性字典：配套单篇笔记展示稿

> 草案。字段、枚举与对象关系以 [单篇笔记展示稿](ai-note2-single-note-display.md) 的术语为准。下述 bundle、z3n 和分链版本条款属于已归档的历史序列化方案，不是当前单篇笔记的发布格式。本文不是已实现的 JSON Schema。

## 1. 阅读约定

- `S`：Unicode 字符串；ID、公式、原值、单位保持字符串，避免精度或上标损失。
- `M`：Markdown 字符串，可放 JSON 字符串或 H4 路径正文，不能两处重复。
- 配套 syntax_profile=z3n-records/1.0.0：特定 M 字段也可由 z3n 的 `[ID]:` 或 `<TC-L>:...` 赋值；TC-L 端点/combination 和 TC/EC 受控归属标签可用缩写。JSON、H4、z3n 三者不能对同一路径重复赋值，历史白名单已归档，本稿不要求 z3n 围栏。这一段仅说明旧版字段的存放方式，不规定当前单篇笔记格式。
- M 若嵌在数组成员内，本版本只能放 JSON 字符串；H4 只允许对象键路径，不允许为数组成员创建正文路径。数组中长说明将来可另设稳定子对象，但不能偷偷用数组位置充当身份。
- `I`：整数；`I+` 为正整数；`B` 为布尔值；`O` 为严格对象；`T[]` 为类型 T 的列表。
- `?`：值可为 null，不是字段可省略。除明确写“可选”的 display/extensions，全部列出的类型字段都必须在组装后存在。
- `Ref`：固定版本链对象引用；`SourceRef`：原文片段引用；`CriterionRef`：判准来源引用；`MaterialRef=Ref|SourceRef|CriterionRef`。
- 同模块局部 ID 列表只允许指向当前文档对象；跨文档必须 Ref，不允许仅一个 TC-N001 字符串。
- 引用自文档可用 doc=self，指向本文件当前 revision；解析导出后展开完整身份，不生成动态“最新版”。
- 默认值**没有自动填充权**。以下默认只是模板初始值；正式输出必须显式设置和校验。

## 2. 所有对象的公共属性

本节“对象”指 TC/EC/RC 记录。第三节的历史 bundle 清单不使用 `record_meta`。

### 2.1 record_meta（必需）

| 字段 | 类型 | 约束 |
|---|---|---|
| state | S | active / resolved / retired；resolved 仅候选记录使用 |
| replaces | Ref[] | 固定旧版本对象，可空；不按含义相似自动补 |
| replaced_by | Ref[] | 固定新版本对象，可空；已发布旧 revision 不原地补反链 |
| note | M? | 生命周期说明，不充当科学结论 |

当前 state=retired 的对象不参与现行支持、处理和正式用途图，但仍可被历史审查寻址。候选 state=resolved 必须有实际 resolution_refs/正式 mapping IDs。state 不代替 finding_status、extraction_status 或 RC-D.status。

### 2.2 可选 display 与 extensions

- display：`{label:S?, order:I?}`，仅建议顺序与短标签；不得拿来反推层级、身份或可靠性。正文标题后中文名称同属展示。
- extensions：O，键为登记过的命名空间；未知扩展只能原样保留或明确拒绝，不改变已知字段。
- 不允许对象自由加入 color、score、confidence、truth 等未经 schema 声明的字段。

### 2.3 定位 Location

Location 是 source 引用的 source_ref 数据体，包含 library_ref、parent_item_key、source_set_id、attachment_key、block_id、span、pdf_role、page_pdf、page_label、section_path、text_excerpt、table_locator、applies_to。

| 字段 | 类型 | 约束 |
|---|---|---|
| library_ref | O | 真实库身份；fixture 可 null |
| parent_item_key | S | 真实父条目；fixture 可 null |
| source_set_id | S | 不可变解析集合；必须已登记 |
| attachment_key | S | 真实附件；fixture 可 null 且使用 fixture_document_id |
| block_id | S | 对应冻结块；单独不保证跨解析稳定 |
| span | I[2]? | null 指块；否则 0≤start<end≤原块码点长度 |
| pdf_role | S | MS / SI / COMBINED / UNKNOWN / RELATED |
| page_pdf | I+? | 附件内页码，未知 null |
| page_label | S? | 印刷页/罗马字页等，不替代 page_pdf |
| section_path | S[] | 章节路径，未知 [] |
| text_excerpt | S | 实际原文连续片段，不得是重构释义 |
| table_locator | O? | table_id、cell_ids:S[]、header_path:S[]、note_ids:S[] |
| applies_to | S[] | 本对象字段 JSON Pointer 列表；[] 表示整个对象 |

fixture 的额外 fixture_document_id 只在 mode=fixture 合法；production 禁止它代替附件 key。

## 3. 历史 Bundle 外壳、父条目与来源

### 3.1 bundle 文档头

protocol=`z3n-md/2.0.0`、syntax_profile=`z3n-records/1.0.0`、schema=`bundle/1.0.0`、module=`bundle`、mode、publication_state、bundle_id、bundle_revision、library_ref、parent_item_key。bundle 不允许 z3n 科研对象赋值。fixture 时库/父条目为 null；production 要真实且各模块同父条目。bundle_revision I+，不是三个 chain revision 的最大值或总和。

四节对象定义：

| 分节 | H3 ID | 主键 | 字段 |
|---|---|---|---|
| BUNDLE.META | BMETA001（恰一项） | record_id | parent_item_metadata、metadata_source、snapshot_ref |
| BUNDLE.SOURCES | BSET001 等 | record_id | source_set_id、documents、blocks_ref、input_policy、role_resolution |
| BUNDLE.MODULES | BMOD001 等 | record_id | module、document_ref、file_hint、selected、compatibility_checks |
| BUNDLE.PUBLICATION | BPUB001 等 | record_id | publication_status、remote_targets、renderer_version、source_digest、verification、note |

BMETA/BSET/BMOD/BPUB 只是 bundle 清单记录键，不是新科研对象前缀，不接受 EC/RC 以它们替代科学目标。

metadata_source 固定 `zotero_parent_item`；parent_item_metadata 为 `{item_type,title,creators,publication_title,date,doi,url,tags,extra}`。未知标量 null，creators/tags 数组；creators 保存父条目实际姓名结构与 creator_type，不从 PDF 回填缺项。snapshot_ref 保存实际读取的 parent key、Zotero version、captured_at、metadata_digest；未取得不能编造。fixture 可以用明确 synthetic 标识，不假装 Zotero API 返回。

链文档头的 parent_snapshot_ref 是 `{bundle_id:S,bundle_revision:I+,record_id:S}`，解析到 BMETA 实际记录；它与 BMETA.snapshot_ref（真实 Zotero 读取快照）是不同层身份，不用文件名或渲染 anchor 替代。

BSET.documents 每项含 attachment_key、pdf_sha256、pdf_role、document_identity_status、role_basis、parse_metadata；parse_metadata 为 parser、parser_version、adapter_version、parsed_at、parse_artifact_digest。production 指纹必须实算；未知可 null，但最低来源追溯与版本冻结不得缺失。document_identity_status 取已匹配父条目/身份未明确/不属于目标论文；角色和文献身份不同，UNKNOWN 不自动等于不相关。COMBINED 可在块级标具体 MS/SI 段，否则保留 COMBINED，不一刀切当 MS。

BSET.blocks_ref 是实际清单/快照 locator，可为相对安全文件路径或不可变对象标识。来源集合改变任何语义块/角色/适配输出，创建新 source_set_id，不能复用旧块指针。

历史 `input_policy` 不适用于当前笔记。当前输入为 MinerU JSON 的文本、表格文字、公式、图注、脚注，以及指定 AI 分析笔记的文本；不发送图片。正式论文事实以 PDF 文本定位。

role_resolution 为 `{status:"明确|待复核",basis:S,unresolved:S[]}`，保存按条目集合判断的实际依据，不能默认只有一个 MS。

BMOD.module 仅 thought/evidence/review，selected B；每种至少一个 selected=true 版本且同种最多一个主选，历史依赖允许另登记 selected=false。document_ref 为完整身份，file_hint 只查找提示。compatibility_checks 每项为 old_ref、new_ref、status(unchanged/changed/retired/unresolved)、rationale；没有更新比较可 []，不倒改旧判断。

BPUB.publication_status 取未发布/部分发布/已发布并回读/需要对账；remote_targets 每项含 module、chain_id、revision、note_key、zotero_version、verified_at，未发生则 []。source_digest、renderer_version 未生成则 null，verification 保存实际 readback 结果或 null；不能预填成功。BPUB 是运行派生 sidecar 的可读镜像，不反写源 module 的科学对象。

### 3.2 来源块 JSONL（拟定适配输出）

每行严格 JSON，字段固定：source_set_id、attachment_key、block_id、block_type、pdf_role、page_pdf、page_label、section_path、text、table。文本 string 保持适配输出的冻结形式；表格 table 非空时保留单元格、表头与脚注。可有 fixture_document_id，但仅 fixture。

block_type 取 text/table/equation/caption/footnote/reference；不输出 img_path、base64、image_url 或图片数据。标题可作为 text 块及 section_path，页眉水印需由适配器说明过滤，不把有条件的脚注丢掉。

MinerU 原有 content_list.json 和 Markdown 不自动成为这个 JSONL schema；需显式适配及保留来源版本。JSONL 与 Markdown 引文都必须指向同一冻结块集，不各自重新切块。

## 4. TC 属性

### 4.1 nodes / TC-N

| 字段 | 类型 | 约束 |
|---|---|---|
| node_id | S | TC-N 正整数至少三位 |
| node_type | S | 核心论点 / 分论点 / 论证理由 |
| text | M | 单一完整命题，原文语气和限定不变 |
| claim_kind | S? | 描述 / 比较 / 机制 / 因果 / 方法有效性 / 推广性；无法分类 null |
| argument_angle | S? | 论证角度标签，不代替命题 |
| original_qualifiers | S[] | 原文限定；没定位到不能自行补 |
| attribution | S | 作者明示或文本重构 |
| extraction_status | S | 明确或可重构，配对归属 |
| text_locations | Location[] | 正式节点至少一个实际来源 |
| presentation_status | S | 已组织 / 未进一步展开 / 层级待整理 |
| presentation_note | M? | 层级待整理必填具体未表达部分 |

上属/下属、复用和 C1/B1/R1 标签从 support_edges 派生，不在每个节点再存 parent_id/children_ids，避免双向关系冲突。

### 4.2 support_edges / TC-L

edge_id:S、from_node_id:S、to_node_id:S、bridge:M、target_part:M、combination:O、attribution:S、extraction_status:S、text_locations:Location[]，另加公共属性。

combination 为 mode＋edge_ids。mode 取单独提供理由/联合使用/未明确。单独与未明确时 edge_ids=[]；联合时同组至少两条且包含自身，端点 to 相同、组成员列表相同，不能重复或指候选边。组合方式未明确不必等于支持关系未明确：联系可靠而组合方式不清时，边仍可正式，但不推定独立。

活动联合组成员全部 active；退休联合组保留原字段，成员全部 retired，不能因退休仍保存 combination 就判错。组内任一边退出现行图时，旧组全体退休；剩余论证若有继续用途，以新的单独边/联合边和 replaces 表达。不能在同组混用活动/退休，也不能把旧联合成员静默改成单独支持。具体校验区分见语法详规 6.2。

边与 bridge 的归属/提取状态在本 schema 作为一体记录；若边明确、桥只能重构，为避免将桥冒充作者直接表达，整个规范边使用文本重构＋可重构，并在 text_locations/applies_to 说明哪部分直接明示。未来若要独立归属必须升 schema 增加 bridge 子属性，不能偷偷混搭。

### 4.3 candidate_records / TC-C（新增载体）

| 字段 | 类型 | 约束 |
|---|---|---|
| candidate_id | S | TC-C，不是正式 node_id/edge_id |
| candidate_kind | S | 命题候选 / 联系候选 |
| candidate_text | M? | 无法可靠成命题时只忠实摘录/描述，不当正式命题 |
| proposed_node_type | S? | TC 节点类型或 null |
| from_node_id、to_node_id | S? | 已存在端点或 null；不得造未知节点 |
| unresolved_description | M | 不能形成正式对象的原因 |
| attribution | null | 不给候选伪归属 |
| extraction_status | S | 固定未明确 |
| text_locations | Location[] | 已有线索位置；无定位只保留 draft，不升级正式 |
| resolution_refs | Ref[] | 形成正式对象后固定新 ID/版本；初始 [] |

命题候选不能同时有边端点；联系候选可以有已知端点，但不包含 bridge 权威字段。resolved 后保留候选历史，resolution_refs 至少一个正式节点/边，不能指回自身。

TC-C 和 EC-C 均增加 decision:S、decision_reason:M?。decision 取 pending/accepted/rejected/withdrawn：pending 配 state=active 且结果为空；accepted 配 state=resolved 且正式结果非空，结果需对应同一提议的已确定主体/目标，实质不同另建候选；rejected/withdrawn 配 state=retired、decision_reason 非空、不能伪造正式结果。unresolved_description 保留原疑点历史，不随接受改成作者明示；extraction_status 仍描述候选记录的历史提取状态，正式状态在新对象中。

## 5. EC 属性

### 5.1 evidence_records / EC-E

| 字段 | 类型 | 约束 |
|---|---|---|
| evidence_id | S | EC-E |
| research_origin | S | 实验依据 / 文献依据 / 未明确；前者不等于全是实验测量 |
| primary_aspect | S? | 制备 / 表征 / 性能 / 理论 / 机制 |
| additional_aspects | S[] | 同五值，不重复、不含 primary_aspect |
| object_and_conditions | O | 见下文 |
| field_statuses | O[] | 字段级缺失/定位，不是科学评价 |
| method_or_acquisition | O | 见下文 |
| reported_result | O | 见下文 |
| result_type | S | 六值枚举见下文 |
| author_interpretation | O? | ref 或 local，不双存命题 |
| text_locations | Location[] | 至少一个报告来源，可通过 applies_to 分字段 |
| source_access | O | forms、reading_scope |
| reference_info | O? | 既有研究身份；无法取得保留 null 并说明，不杜撰 DOI |
| attribution | S | 作者明示 / 文本重构 |
| extraction_status | S | 明确 / 可重构 |

object_and_conditions：`{description:M,conditions:O[]}`。每个 condition 为 `{name:S,raw_value:S?,unit_raw:S?,applies_to:S,source_refs:SourceRef[]}`；不同样品/比较方需明确 applies_to，不继承同名条件。description 不代替可提取的关键条件。

field_statuses：每项 `{path:S,status:S,reason:M?,source_refs:SourceRef[]}`；path 用本对象 JSON Pointer，可定位 conditions 数组的实际字段，但数组位置只限当前版本字段状态，不能成为跨版本身份。status 仅已定位/当前输入未找到/原文明确未报告或未测量/不适用/文本损坏。关键字段 null 要有对应状态；“当前输入未找到”不声称作者未报告。

method_or_acquisition：`{description:M?,parameters:O[]}`；参数项同 condition。只写方法名无法补齐模型、参考或参数，未知保留。

reported_result：`{statement:M,values:O[],comparison:M?,uncertainty:M?,original_qualifiers:S[]}`。values 每项 `{quantity:S,raw_value:S,unit_raw:S?,normalization_basis:S?,applies_to:S,source_refs:SourceRef[]}`；不默认重算或修复数值，不引入精度不明的 float。

result_type 仅实验测量结果/数据处理或统计结果/模型拟合结果/模拟计算结果/理论推导结果/未明确；作者解释不是结果类型。

author_interpretation：

- `{mode:"ref",ref:Ref}`：只能指 TC 节点或 bridge，不另存 text；
- `{mode:"local",text:M,text_locations:Location[]}`：局部解释有来源、尚无相应 TC；
- null：没有可登记解释，说明当前读取情况。

source_access：`{forms:S[],reading_scope:S}`，forms 允许正文文本/结构化表格/图注文本/公式推导文本/脚注文本；reading_scope 为当前论文的本研究报告/当前论文对既有研究的转述/另有提供的原始文献文本。原始文献如另提供必须有自己的来源身份，不冒充本父条目的 PDF。

reference_info：`{citation_label:S?,bibliographic_text:S?,doi:S?,other_identifiers:O,reading_scope:S}`。仅引文编号不补写结果；本研究通常为 null。

### 5.2 process_relations / EC-P

process_id:S、input_evidence_ids:S[]、output_evidence_ids:S[]、process_type:S、operation:M、parameters_or_model:M?、attribution:S、extraction_status:S、text_locations:Location[]。

process_type 为处理得到/拟合得到/计算得到/汇总得到；输入输出各至少一项、无交集、无环。只引用实际已登记 E；无明确输入不能建空壳 E 以满足 schema。正式归属/状态配对同 TC。

### 5.3 provenance_groups / EC-G

group_id:S、member_evidence_ids:S[]（至少两个不同 E）、shared_source_description:M、text_locations:Location[]。无共同来源依据不建确定同源组；分组不能代替处理方向或统计独立性。

### 5.4 use_groups / EC-U

use_group_id:S、members:O[]（每项 evidence_id、role:S）、group_type:S、combination_description:M、comparison_statement:M?、attribution:S、extraction_status:S、text_locations:Location[]。

group_type 仅联合论证/比较使用/解释汇集；至少两个不同 E；角色和组合任务属于身份含义，变更需新 ID。comparison_statement 只在原文明示比较时保存，不新增混合 research_origin。

### 5.5 mappings / EC-M

mapping_id:S、subject_ref:Ref、target_ref:Ref、target_part:M、usage_role:S、use_topic:S、usage_description:M、attribution:S、extraction_status:S、text_locations:Location[]。

subject_ref 为本 EC 文档的 evidence 或 use_group，二选一；不用临时 ID 列表。target_ref 为固定 TC 的 node/edge；field 为 null 或 bridge。引用 object 不等于自动建立 mapping。

usage_role：论证依据/比较材料/背景依据/方法依据；use_topic：机制论证/其他/未明确。可重构用途需全部必要定位，未明确用途只入 EC-C。

### 5.6 candidate_mappings / EC-C

candidate_id:S、subject_ref:Ref、candidate_targets:Ref[]、unresolved_description:M、attribution:null、extraction_status:"未明确"、text_locations:Location[]、resolved_mapping_ids:S[]。

candidate_targets 可 []；若有须实际可寻址，不伪造目标。resolved_mapping_ids 只指当前文档实际 EC-M，是当前结果索引，不代表首次接受版本。首次接受事实由当时发布的不可变候选/正式关联 revision 保留，不能在迁移中重写。TC-C.resolution_refs 与 record_meta 中的历史固定 Ref 不批量改指新版。若 EC 结果在其他 chain，用 record_meta.replaced_by 固定 Ref 并保留当前候选 retired，不把它叫本地 resolved；不能把另一 chain 的结果 ID 塞进本地列表。

## 6. RC 属性

### 6.1 background_records / RC-B

background_id:S、parent_item_ref:O?、journal_identity:O?、journal_metrics:O[]、personal_rating:O、publication_notes:M?、acquisition_status:S、recorded_at:S?、context_note:M?。

parent_item_ref 为 library_ref、parent_item_key、snapshot_ref；journal_identity 为 title:S?、issn:S[]。取得情况 acquisition_status 仅未取得/部分取得/已取得，不把空数组当作没有指标。

journal_metrics 项：metric_name:S、raw_value:S?、metric_year:I?、subject_category:S?、source_identity:O?、source_locator:O?、retrieved_at:S?、reading_scope:S?、check_status:S、custom_score:S?、scale:S?、scoring_rule:S?。未用自定分时后三项 null；不与真实 JIF/CiteScore 合成。

personal_rating：raw_tags:S[]、parsed_rating:S?、scale:S?、parsing_rule:S?、rating_meaning:S?、user_note:M?、snapshot_ref:S?、read_at:S?、check_status:S。只解析用户已定义规则，不填默认五颗星。原 tag 冲突原样保存。

背景 check_status 仅未取得/已记录待核对/已核对/存在冲突/不适用；与 RC-S.check_status 的枚举空间不同。时间采用实际 RFC 3339 字符串，未知 null；不生成虚构抓取时间。

### 6.2 criterion_sources / RC-K

criterion_source_id:S、source_identity:O、edition_or_version:S?、source_revision:I+、content_snapshot_ref:S?、retrieved_at:S?、access_scope:S、check_status:S、locations:O[]、purpose_note:M。

source_identity 为 creators:S[]、title:S?、year:S?、doi:S?、standard_id:S?、url:S?；真实已有值才填。access_scope 为仅书目信息/仅摘要/已读取相关内容；check_status 为待核对/已核对/存在冲突。

locations 每项：location_id:S、section:S?、page_label:S?、clause:S?、text_excerpt:S?；实际读过位置才填，已核对的具体判准引用必须能解析到相关已读取位置。改 location 字符串或版次不能仅留原 source_revision。

### 6.3 review_items / RC-Q

| 字段 | 类型 | 约束 |
|---|---|---|
| review_id | S | RC-Q |
| primary_target_ref | MaterialRef? | 一个正式目标；null 为待定位，不进入正式综合 |
| related_target_refs | MaterialRef[] | 可跨模块、不同版本 |
| related_review_refs | Ref[] | RC-Q，不存领域名称 |
| review_domain | S | 下列七值之一，单值 |
| consistency_subtype | S? | 仅来源与一致性条件必填 |
| question | M | 一个中心问题 |
| issue_origin | S | 作者自述 / 审查者提出 |
| problem_locus | S? | 问题所在层，不是领域 |
| observed_basis | M | 实际材料描述 |
| criterion | O | statement:M、criterion_type:S、criterion_source_refs:CriterionRef[] |
| basis_refs | O[] | ref:MaterialRef、role:S、relevance:M |
| reasoning | M | 简明公开判断依据 |
| finding_status | S? | 四种结果；未检查 null |
| impact | S? | 关键 / 重要 / 局部 / 待定；无负面发现可 null |
| impact_reason | M | 承重影响或说明不涉及负面影响 |
| direct_effect | M | 本对象的直接影响与条件 |
| response_refs | MaterialRef[] | 作者已有回应，非虚构后续回复 |
| resolution_status | S? | 开放 / 已有针对性处理 / 需复核 / null |
| resolution_requirements | M? | 需要的信息或处理 |
| review_scope | M | 实际检查范围 |
| suggestion_refs | Ref[] | RC-D |
| text_locations | Location[] | 原文位置；未定位项可 [] 并说明 |

review_domain：来源与一致性/实验条件/测试参数/数据分析/结论匹配/实证推断/支持关系与推理桥。

consistency_subtype：提取一致/论文内部一致/物理化学一致；非该领域必须 null。

problem_locus：原文报告/解析文本/结构化整理/跨模块关联/当前无法区分；无问题 null。不能把提取错写成论文错。

criterion_type：一般逻辑/方法学原则/特定领域规则。使用特定外部规律或阈值时引用真实已读 RC-K；不默认联网。basis_refs.role 仅支持疑点/缓解疑点/限定范围/提供判准；正向核查可用缓解疑点，不能由角色名本身推断 finding_status。

finding_status：未发现明确问题/存在条件性疑点/已定位具体问题/当前无法判断。issue_origin 不代替本次 judgment_author；作者自述局限可直接 primary 引 source:text_span，再 related 引受限节点。

### 6.4 coverage_records / RC-S

coverage_id:S、scope_unit_id:S、target_refs:MaterialRef[]（至少一项）、review_domain:S、consistency_subtype:S?、criterion_scope:M、check_status:S、inspected_inputs:MaterialRef[]、review_refs:Ref[]、assessment_refs:Ref[]、unavailable_reason:M?、not_applicable_reason:M?。

review 文档头 coverage_scope 为 `{units:O[],exclusions:M[],declaration:S}`，其他链该字段 null。units 每项含 scope_unit_id:S、target_refs:MaterialRef[]、review_domain:S、consistency_subtype:S?、criterion_scope:M；scope_unit_id 在本审查文档内唯一，仅作覆盖单元键，不是新的科学对象。declaration 取部分覆盖/已完成声明范围。若采用后者，单位全集非空且每项均有匹配 RC-S，状态只能已检查或有理由的不适用；当前不可检查不算已完成。相同 scope_unit_id 的 RC-S 目标、版本、领域、子类及范围必须一致；一个 revision 内每单位最多一条现行 RC-S。没有对应 RC-S 的单位视为未检查，不视作不存在。

覆盖统计必须分别给出已检查、不适用、未检查、当前不可检查，不把不适用算作“已检查通过”。字段 exclusions 限定审查全集之外的部分；即便 declaration 完成，也只指显式单位，不指全文任意命题与判准。scope 元数据中的 Ref 和 RC-S 的重复引用只是需验证一致的索引，科学判断仍在 RC-Q。

- 已检查：review_refs 非空且实际结果不能 null/当前无法判断；范围/领域/子类和目标对应。
- 未检查：review_refs=[]，不能拿历史结果代表本轮；不要求 inspected_inputs 非空。
- 当前不可检查：unavailable_reason 必填；有尝试可引当前无法判断 Q。
- 不适用：not_applicable_reason 必填，不自动绿色通过。

assessment_refs 只是索引，不能被当作 RC-Q 的判准。检查部分范围后另有缺口，应拆具体 criterion_scope，不在一个已检查上附一句“其余没查”掩盖。

### 6.5 claim_assessments / RC-A

| 字段 | 类型 | 约束 |
|---|---|---|
| assessment_id | S | RC-A |
| target_ref | Ref | 固定 TC node |
| assessed_scope | M | 哪个命题部分和条件 |
| background_refs | Ref[] | RC-B，背景不加权 |
| evidence_refs | Ref[] | 实际纳入 EC 对象，可空但不能伪造 |
| mapping_refs | Ref[] | 作者 EC-M；无作者用途不造 |
| evidence_review | O[] | review_ref:Ref、role:M；方法/证据/来源问题 |
| logic_review | O[] | review_ref:Ref、role:M；命题支持/桥问题 |
| provenance_handling | M | 同源、派生与联合怎么处理 |
| supporting_basis | O[] | 正向材料项，结构见下文 |
| counter_basis | O[] | 相反材料项，结构见下文 |
| unresolved_reviews | O[] | review_ref:Ref、remaining_effect:M |
| synthesis | O | 五部分综合，见下文 |
| reliability_statement | O | conclusion:S、scope:M、rationale:M |
| retained_claim | O? | origin 固定审查者建议、suggestion_ref:Ref；文本从 RC-D 派生 |
| review_scope | M | 实际核对范围 |
| unassessed_parts | O[] | part:M、reason:M |
| coverage_refs | Ref[] | 实际 RC-S |

supporting_basis/counter_basis 每项为 ref:MaterialRef、relevance:M、conditions_match:M、response_refs:MaterialRef[]。conditions_match 不用一个 true 掩盖部分条件不对应；明确写对应与未知条件。相反材料可以已定位，不能只丢进 unresolved_reviews。

synthesis 固定五字段：load_bearing_dependencies:M、material_balance:M、issue_handling:M、remaining_gaps:M、conclusion_reason:M；五者允许 H4 指针正文。不是按问题计票，不生成概率。

reliability_statement.conclusion 五值：当前材料内支持较充分/有支持但须限定范围/关键支持仍待核实/存在实质性反对或不一致，尚未协调/当前无法评议；scope 与 assessed_scope 对应，理由独立清楚。

**单一真源收紧：**旧审查链描述 retained_claim={text,origin,suggestion_ref} 并要求 text 与 RC-D.proposed_content 相同。本交换 schema 改为只存 origin、suggestion_ref，阅读时从固定版本 RC-D.proposed_content 派生 text；不维护第二份可编辑正文。这是明确的序列化修订，不静默把旧 text 丢掉。迁移旧输出必须先验证二者一致，不一致则冲突拒绝迁移。无建议时 null。

### 6.6 change_suggestions / RC-D

suggestion_id:S、origin:"审查者建议"、review_refs:Ref[]、assessment_refs:Ref[]、target_ref:Ref、proposed_object_kind:S?、change_type:S、proposed_content:M、rationale:M、status:S、result_refs:Ref[]、verification_refs:Ref[]。

proposed_object_kind 仅新增登记时填相应目标类型，否则 null；新对象尚不存在只能 target_kind=document 指已有输出容器，不生成未来 ID。

change_type：调整命题表述/调整层级或连接/更正证据字段/补充证据登记/调整用途关联/补充来源或说明。

status：待处理/已采纳待落实/已拒绝/暂缓/已落实待复核/复核完成。后两项 result_refs 必须真实非空；复核完成还需 verification_refs 对应实际复核 Q。无依据未定位建议不伪装可执行对象；不自动回写 TC/EC/用户 tag。

## 7. 基础、派生与冗余冲突

| 内容 | 权威存储 | 只能派生或索引 |
|---|---|---|
| 命题 | TC-N.text | C1/B1/R1、EC 作者解释中的可读文字 |
| 推理桥 | TC-L.bridge | 树边显示文字、RC 目标预览 |
| 论文结果 | EC-E.reported_result | 机制页/命题下证据预览 |
| 作者用途 | EC-M，必要时 EC-U | 节点下证据清单、按角度汇总 |
| 正反材料评议 | RC-A 对实际材料的引用和相关性 | 红/绿标签不能新增事实 |
| 建议表述 | RC-D.proposed_content | RC-A.retained_claim 的可读 text |
| 模块版本 | document_ref | 文件名、Note 标题、DOM anchor |
| 原文片段 | 冻结来源块＋Location | 显示规范化片段不改定位依据 |

若人为了阅读在对象正文外另加一个人工摘要，该摘要必须归明确非权威导出视图，不允许混入规范对象后让解析器猜哪个是准。对同一字段发生冲突时拒绝发布，不选最后一个或让模型自行合并。

z3n 不是派生摘要，而是规范源码内的受限赋值形式；其 `[RC-A]:` 只写 assessed_scope，`[RC-Q]:` 只写 question，不能因为名称类似 statement 就把审查提问或范围当作作者命题。`@[...]` 是可解析导航，不替代上述任何正式关系或依据表。

## 8. 完整与缺失不混同

一份结构完整的笔记仍可以注明缺失：缺方法/部分条件、未检查领域、无外部判准、完全未关联结果、候选用途，均正常保留。它不能包含伪造定位、悬空对象、同字段双写或虚假的“已检查”。

每个正式 TC/EC 对象都需最低可定位来源；RC 的范围、判准和依据需与结果对应。格式闭合不替代内容审查；fixture 只验证设计内部一致性，不是生产发布成功。
