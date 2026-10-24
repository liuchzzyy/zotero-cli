# 审查链结构设计：论文内部一致性、命题可靠性与证据—推理审查

配套文件：`thought-chain-structure-design.md`（思维链第七节）、`evidence-chain-structure-design.md`（证据链第十一节）。三种记录在单篇笔记中按论证树穿插展示。审查对象仍需固定来源与版本。本文件定义审查输入、输出和判断方法，不讨论实现。

**审查链＝背景参考＋定点审查＋命题评议＋检查覆盖。** 修改建议和外部判准来源分别登记，原文段落也可直接寻址。 每个审查对象使用单篇笔记的全局 index；重复引用沿用该号。

审查以论文本身为依据，检查报告是否准确一致、条件与方法是否适用、材料如何支持命题、命题之间的推理是否合理。不另设数据鉴真或科研诚信调查模块。

---

# 一、职责与术语

## 1.1 定义

> **审查链：针对论文文本、思维链和证据链中的具体对象，依据明确判准和可定位材料，形成对报告、方法、实证推断及命题推理的定点评议，并综合评价指定命题的可靠性。**

正式用词统一为“命题可靠性”，覆盖核心论点、分论点、论证理由三个层级。“论点”仍用于描述作者论证，不用来缩小评议对象。

## 1.2 分层判断

| 判断 | 问题 | 不应混同 |
|---|---|---|
| 提取准确性 | TC/EC 的表述、类型、层级及字段是否对应原文 | 整理错误不是作者论证错误 |
| 论文内部一致性 | 原文不同部分的值、单位、对象和描述是否一致 | 条件不同不直接构成矛盾 |
| 方法合理性 | 条件、参数、处理与模型是否适合目标 | 参数缺失不自动等于参数错误 |
| 实证支持 | 材料怎样支持指定命题及其范围 | 作者使用了材料不等于支持充分 |
| 命题推理 | 下位命题是否提供接受上位命题的理由 | 可定位推理桥不等于推理合理 |
| 命题可靠性 | 前述支持与限制综合后，指定范围内能保留什么判断 | 不等于期刊声望、个人喜好或真实概率 |

## 1.3 权威表述与非回写

思维链保存命题和桥；证据链保存结果、形成过程与作者用途；审查链保存判准和判断。审查只引用固定版本的对象，不替作者重写命题，不静默修改 EC 结果、映射或 Zotero 星级。

有利材料、相反材料、作者回应与当前不能判断之处都需保留。没有问题记录不等于通过；没有支持不等于反证。

---

# 二、背景参考：期刊与个人星级

## 2.1 使用边界

保留期刊影响力和 Zotero 父条目的星级 tag，但与科学评议分栏显示。

- 期刊指标是出版背景，不是单篇命题可靠性的直接测量。
- 星级只按用户已定义含义解释，不假定星数尺度、符号或评分目的。
- 高期刊指标或高星级不能抵消已定位的方法、数据分析或逻辑问题。
- 未取得指标、未标星不自动扣分，不把二者与证据/逻辑混合求总分或概率。
- 不擅自改动用户 tag。个人评价和自动评议可以不一致，并列保留。

## 2.2 背景记录 RC-B 的完整字段

所有字段保留；未知为 null，未取得的集合为空并有 acquisition_status，不把“未读取”当作“没有”。

| 字段 | 定义 |
|---|---|
| background_id | RC-B 后接至少三位数字 |
| parent_item_ref | library_ref＋parent_item_key，以及实际读取的条目快照标识；不借用链 revision 冒充 Zotero 版本 |
| journal_identity | 父条目期刊名称、已有 ISSN 等标识；不从 PDF Info/XMP 补入 |
| journal_metrics | 指标记录列表；每项结构见下文 |
| personal_rating | 原始星级 tag、规则和含义快照；结构见下文 |
| publication_notes | 已有的更正/版本说明、涉及版本与内容；无外部调查时明确未查询 |
| acquisition_status | 未取得／部分取得／已取得 |
| recorded_at | 本背景快照实际建立时间，不能编造 |
| context_note | 对背景来源、尺度或冲突的说明，不作科学证据 |

每项 journal_metrics 包含 metric_name、raw_value、metric_year、subject_category、source_identity、source_locator、retrieved_at、reading_scope、check_status。若指标涉及多个学科分类，分别保留，不择优。用户自定期刊分另存 custom_score、scale、scoring_rule，不与 JIF/CiteScore 原值混合。

personal_rating 包含 raw_tags、parsed_rating、scale、parsing_rule、rating_meaning、user_note、snapshot_ref、read_at、check_status。只读父条目 tag。未标星用 parsed_rating=null，不填 0；规则未知不猜；多个冲突 tag 原样保留。

这里的 check_status 取“未取得／已记录待核对／已核对／存在冲突／不适用”，与 RC-S 的 check_status 是不同对象上的不同枚举；不得混用。时间未知可 null 并说明，不填当前时间冒充获取时间。

source_identity/source_locator 使用真实已有来源的名称、标识、版本或 URL 及位置；未取得为 null。背景来源不当作科学 EC-E 或判准依据。期刊信息和用户星级从已有字段或明确提供的数据读取；本设计不默认额外联网。

---

# 三、审查领域与分类规则

## 3.1 review_domain：单值

每条 RC-Q 只有一个主要 review_domain；RC-S 同样如此。相关对象通过 related_target_refs 表达；相关审查通过 related_review_refs 表达。related_target_refs 不是其他领域的存放字段。

| 合法值 | 内容 | 文档位置 |
|---|---|---|
| 来源与一致性 | 提取对应、论文内部报告一致、量纲和物理化学对应 | 第四节 |
| 实验条件 | 研究和比较条件是否适合目标 | 5.1 |
| 测试参数 | 测量/计算设置是否能完成所需任务 | 5.2 |
| 数据分析 | 输入结果到处理输出的方法、模型与约束 | 5.3 |
| 结论匹配 | 结果和目标命题的内容、强弱、范围是否对应 | 5.4 |
| 实证推断 | 结果解释、组合及其到命题的推断 | 5.5 |
| 支持关系与推理桥 | 命题到命题的支持步骤及其解释 | 第六节 |

第五节的五个环节对应上述中间五值；第四节与第六节各对应另一个值，不强行并入五环节。

同一个根本问题跨多个领域时，选择最直接的主要领域、列明其他受影响对象；只有判准、所需处理和判断确实不同，才另建 RC-Q 并互引，不靠拆分增加问题数量。

## 3.2 consistency_subtype：条件必填、单值

review_domain=来源与一致性时，consistency_subtype 必须取：

- **提取一致**：链记录、字段、类型或层级与原文的对应。
- **论文内部一致**：原文不同位置对同一对象/条件的报告对应。
- **物理化学一致**：论文报告与明确的量纲、数量级、守恒或适用物理化学关系的对应。

其他领域该字段为 null。提取错误与论文内部矛盾同时出现时，先定位原文，必要时拆成两条有不同依据的审查，不让一条记录同时宣称问题既在解析又在原文。

## 3.3 problem_locus：问题所在层，单值

正式值为“原文报告／解析文本／结构化整理／跨模块关联／当前无法区分”。它与 review_domain 互不替代：前者说问题在哪里，后者说检查什么。

例：思维链把原文中的一个下位理由错误归为分论点，首先是 review_domain=来源与一致性、consistency_subtype=提取一致、problem_locus=结构化整理；不自动属于论文支持逻辑缺陷。只有原文本身的支持步骤确实有问题，才另作支持关系审查。

未发现问题时 problem_locus=null；它不是必填一个“缺陷位置”的任务。

---

# 四、报告准确性与内部一致性

检查数值、单位、有效数字、误差、样品、状态、分母、方法、图注身份和文献/本研究归属。

提取一致与原文内部一致分别输出；原文写得一致而链提取错误，不能判论文矛盾。摘要、结果、方法、结论和 SI 的比较需先确认对象、状态和版本。

数量级、量纲、守恒或换算检查必须针对明确条件；涉及运算时实际计算并保留输入和公式，不编造结果。异常先排除单位、归一化、版本差异与解析损坏，不能仅凭“不常见”判错。

缺少必要信息时说明缺哪项、影响哪项判断，不添加与该问题无关的核验任务。

---

# 五、证据审查的五个环节

## 5.1 实验条件

条件合理性相对于研究命题，不以“越接近应用越好”作为统一标准。

检查样品、对照、批次、制备/后处理引入的变化、状态和区域，及电池构型、参比、mass loading、active-material basis、electrolyte composition/amount、N/P ratio、温度、协议、重复与样本定义等适用项。

对照是否区分目标变量、条件是否允许比较、bulk/surface/interface 与 ex situ/in situ/operando 是否被混用，都需对应具体材料。

## 5.2 测试参数

检查电流/倍率、窗口、扫描或采样设置、检测时间、量程、检出限、校准、状态控制，以及模拟边界、尺寸、时间尺度和收敛设置是否适合目标。

参数常用不等于适用；参数缺失不等于参数错误。特定阈值需有来源，不凭空补一个通用标准。

## 5.3 数据分析

审 EC-P 及其 EC-E 输入/输出、方法字段：基线、背景、平滑、归一化、积分、校准、模型、参考、约束、自由参数、不确定性和统计单位。

谱学与多变量分析按其具体任务考虑 EXAFS 的 k/R 范围和参数相关性、PCA/MCR-ALS 的预处理和约束及模型非唯一性。没有重算不声称已重算，没有残差信息不声称已检查残差。

## 5.4 结论匹配

审 EC-M.target_part 与 TC 节点：反应、物种、区域、样品、状态、强弱和范围是否对应。

未检出不自动成为不存在，无显著差异不自动成为等效，相容不自动成为唯一证明。现行关联若已限定局部范围，不能批评它没有限定。

相反结果单独保存，不塞进“疑点”；其与命题的条件是否对应要说明。作者回应也保存，不只取不利材料。

## 5.5 实证推断

审结果怎样被解释、组合并用于命题：指标是否能代表目标概念、模型解释是否有中间步骤、联合材料是否实际互补，同源/派生是否被重复当作独立验证，相关结果是否被扩成因果。

EC-G 是同源，EC-U 是联合用途，二者不互代。“机制”可作为证据内容角度，也可作为作者用途主题；这不自动生成新的证据记录。

| 路径 | 常用对象 | review_domain |
|---|---|---|
| 信号/数值到参数 | EC-P、EC-E | 数据分析 |
| 结果到命题 | EC-M、EC-U、TC 节点 | 实证推断或结论匹配，按中心问题选一 |
| 命题到命题 | TC 边或 bridge | 支持关系与推理桥 |

---

# 六、支持关系与推理桥审查

## 6.1 两个层面

本节同时覆盖 TC-L 边整体和其 bridge 属性：

- **边整体**：即使下位命题成立，它是否对上位命题提供支持？属于相关、串联、联合还是误设的连接？
- **bridge**：给出的“为什么能连接”是否合理，是否有必要前提、概念变化或省略步骤？

目标为边整体时 target_field=null，目标为推理桥时 target_field=bridge，均归“支持关系与推理桥”。不把边整体审查塞入结果到命题的“实证推断”。

## 6.2 内容

检查相关性、必要与充分、循环、概念/对象转换、串联与并列、联合依赖、相关与因果，以及机制可能性、主要性、唯一性的区分。

隐含前提、异议、竞争解释和范围限定都是审查内容，但不必成为额外对象种类；以一个问题为中心进入 RC-Q。竞争解释只是候选可能性时明确条件，不能冒充新事实或实验结果。

作者自述局限可以直接以原文段落为主目标，并把受限命题列为 related_target_refs。限制范围不等于反证，缩小范围也不必削弱范围内判断。

## 6.3 不自动向上否定

局部桥或结果有问题，只直接影响它涉及的部分。向核心命题讨论影响需列实际依赖路径、其他路径和条件。不能将机制解释待核实写成性能现象不存在。

---

# 七、审查项 RC-Q 的字段与依据

## 7.1 主体结构

所有字段必须出现；不适用可用 null/空列表并说明，不靠省略暗示通过。

| 字段 | 定义 |
|---|---|
| review_id | 稳定 RC-Q ID |
| primary_target_ref | 一个固定版本链对象或可定位原文片段；待定位项可 null |
| related_target_refs | 可跨模块、跨版本的相关对象或文本引用 |
| related_review_refs | 其他 RC-Q 的引用，不存领域名称 |
| review_domain | 第三节的单值枚举 |
| consistency_subtype | 来源与一致性领域的单值子类；其他领域为 null |
| question | 一个中心问题 |
| issue_origin | 作者自述／审查者提出 |
| problem_locus | 第三节问题所在层；无问题时 null |
| observed_basis | 实际材料描述；不夹新增事实 |
| criterion | 判准陈述、criterion_type、criterion_source_refs；具体结构见 7.3 |
| basis_refs | 各项引用＋role＋relevance，分别说明支持疑点、缓解疑点、限定范围或判准作用 |
| reasoning | 简明公开判断理由，不是内部思考过程 |
| finding_status | 第八节局部结论；未检查为 null |
| impact | 关键／重要／局部／待定；无已定位或潜在问题时可 null |
| impact_reason | 实际或条件性影响的理由；impact=null 时说明不涉及负面影响 |
| direct_effect | 对主要对象哪部分有影响；区分已发生与若成立 |
| response_refs | 作者已有回应/说明；可为空 |
| resolution_status | 开放／已有针对性处理／需复核；无需处理时 null |
| resolution_requirements | 解除、缩小或区分疑点的必要信息；无需时 null |
| review_scope | 实际检查的文本、条件、范围 |
| suggestion_refs | RC-D 建议对象引用，可为空 |
| text_locations | 涉及原文的位置；没有位置的未定位项需解释 |

待定位项保存在 review_items，但不作为正式定点判断或负面影响。原文片段作为主要目标不属于“未定位”，见第十一节。

## 7.2 issue_origin 与判断归属

作者明确讨论的限制、假设或未解问题用“作者自述”；审查者新增问题用“审查者提出”。两种情况下 reasoning/finding_status 都是本次审查者的评议，不冒充作者原话。

文档 judgment_author 标明 AI／人工／共同评阅及可用标识。TC/EC 的 attribution 和 extraction_status 不因审查而扩充或改写。

## 7.3 判准与外部来源

criterion 包含 statement、criterion_type、criterion_source_refs。criterion_type 单取“一般逻辑／方法学原则／特定领域规则”。

一般逻辑可以直接陈述并说明适用性；方法学原则也需具体到当前任务。特定阈值、材料规律或新引入事实若依赖外部来源，必须引用实际读取的来源，不能用模型记忆填 DOI 或引文。

外部判准来源登记在 criterion_sources，ID 为 RC-K001 一类，完整字段：criterion_source_id、source_identity、edition_or_version、source_revision、content_snapshot_ref、retrieved_at、access_scope、check_status、locations、purpose_note。

- source_identity 保存实际作者/机构、题名、年份、已有 DOI/标准号/URL；未知为 null。
- edition_or_version 固定教材版次、标准版本或文献版本；source_revision 是该登记记录从 1 起的本地修订号，不能冒充原文版本。
- content_snapshot_ref 保存实际读取内容的本地副本或可追溯快照引用；没有就 null，不编路径。
- access_scope 取“仅书目信息／仅摘要／已读取相关内容”；check_status 取“待核对／已核对／存在冲突”。
- locations 记录实际章节、页码、条款或段落及原文片段；未读正文不声称取得正文位置。
- purpose_note 说明提供的是判准，不是被审论文新增实验结果。

criterion_source_refs 必须固定 RC-K 的 source_revision 和具体 location。仅书目信息或未读取到相关内容，不能作为已核对阈值的来源。外部快照集合以 criterion_source_set_id 标识，和论文 input_source_sets 分开。

没有实际使用外部来源时 criterion_sources=[]、criterion_source_set_id=null；不默认检索，也不要求每篇都引入外部材料。来源未取得的特定规则只列待核查问题，不据其作确定缺陷裁决。

## 7.4 依据引用格式

basis_refs 每项为 ref、role、relevance：

- ref 是固定版本链对象引用、论文原文片段引用，或固定 RC-K/source_revision/location 的判准引用；三种结构见 11.2。
- role 只取“支持疑点／缓解疑点／限定范围／提供判准”。
- relevance 写该材料具体说明什么，不能只列 ID。

新发现的论文材料尚未登记为 EC-E 时，可直接引用文本并标未登记，建议补录，不伪造 EC-E ID。

---

# 八、局部判断、影响与处理状态

## 8.1 finding_status

正式取值为“未发现明确问题／存在条件性疑点／已定位具体问题／当前无法判断”。未检查为 null。

- 未发现明确问题：只对实际范围和判准成立，不是全文通过。
- 条件性疑点：有可说明的依赖或疑点，尚不能确认具体问题。
- 已定位具体问题：能指出具体不匹配或错误及出处，不等于整个命题为假。
- 当前无法判断：必要信息或允许读取范围不足，不等于负面事实。

## 8.2 impact 与 resolution_status

impact 说明影响的承重程度，不是置信度或加权分：关键影响中心命题或必需步骤，重要影响分论点/解释/范围，局部影响非承重内容，待定表示范围不明。

条件性疑点可以潜在关键，但 impact_reason 必须有“若该前提/问题成立”的条件。没有负面发现可 impact=null，不填“局部”假装存在缺陷。

处理状态与发现状态独立。作者回应不自动解除；说明是否针对同一对象、解除到何范围。无问题或只是作者明确限定且无需处理时 resolution_status=null。版本变化后是否适用需复核。

---

# 九、命题可靠性评议 RC-A

## 9.1 评议对象

一个正式 RC-A 指向一个 TC 节点，覆盖任意层级命题，并明确 assessed_scope。需要综合不同部分时分开评议；不对全文求平均可信度。

## 9.2 完整字段

| 字段 | 定义 |
|---|---|
| assessment_id | 稳定 RC-A ID |
| target_ref | 固定版本 TC 节点 |
| assessed_scope | 命题部分、对象与条件 |
| background_refs | RC-B 背景引用，不作直接科学支持 |
| evidence_refs | 纳入的 EC-E/EC-U 等对象引用 |
| mapping_refs | 有来源的作者用途 EC-M；不得用审查者用途冒充 |
| evidence_review | 有关条件、参数、分析、匹配和实证推断的 RC-Q 引用及作用 |
| logic_review | 支持链/bridge 的 RC-Q 引用及作用 |
| provenance_handling | 同源、派生、重复和联合如何处理 |
| supporting_basis | 支持该命题的材料引用、相关部分与条件；不是“没有问题” |
| counter_basis | 方向相反或实质不一致的材料引用、针对部分、条件对应与作者回应；无则空列表并限于当前范围 |
| unresolved_reviews | 尚未协调的 RC-Q 及剩余影响；不代替 counter_basis |
| synthesis | 承重依赖、已处理问题、未解问题、反对材料处理和综合理由，见 9.3 |
| reliability_statement | 结论枚举与限定理由，见 9.4 |
| retained_claim | 本地建议对象或 null，见下文；不是作者命题 |
| review_scope | 实际核对范围 |
| unassessed_parts | 未评议部分及原因 |
| coverage_refs | 实际对应 RC-S 引用 |

supporting_basis/counter_basis 每项保存 ref、relevance、conditions_match、response_refs。conditions_match 说明对象、状态和条件对应；不能把条件不同材料直接当反证。counter_basis 保存的是可定位的相反报告，不意味着其科学解释必定正确，也不只是“存在疑点”。

retained_claim 如有内容，结构为 text、origin 固定“审查者建议”、suggestion_ref。它是 RC-A 本地文本，引用对应 RC-D；不进入 TC/EC attribution 二分体系，不新增“审查者建议”到作者表述枚举。无需重写建议则 null。

## 9.3 综合规则：定性、有依赖，不计票

审查者先确定命题和实际范围，再结合 supporting_basis、counter_basis、RC-Q.finding_status、impact、作者回应及检查覆盖定性综合，不按 RC-Q 数量、impact 权重或期刊/星级求分。

synthesis 必须说明：

1. **承重依赖**：哪些方法、条件、关联和推理是该部分判断所必需；哪些只是辅助路径。
2. **有利与相反材料**：各自针对什么，是否同源、派生或条件不对应；作者解释是否协调了相反材料。
3. **问题归层与当前处理**：整理错误不直接扣给论文；已处理问题按剩余影响看，不只看旧发现标签。
4. **剩余缺口**：哪些必要检查未完成、哪项未解问题能改变结论；未知不当作确定反证。
5. **结论理由**：为何保留、限定、暂缓或指出不一致；可能上游影响不得自动扩散。

优先判断目标可否清楚评议；承重条件未核清时不能给强支持；已定位实质相反材料且未协调时必须进入综合理由，不能被多条正向材料“抵消”。若问题仅影响外推且范围内材料充分，可限定范围；若无此支持仍不能强行选肯定结论。

一条潜在关键疑点并不自动否决，必须说明它是否确实承重，以及有无独立于该路径的其他支持。正向评议需要正向材料和有结论的覆盖，不能从空问题表推导。

## 9.4 reliability_statement

结论值保留：

- 当前材料内支持较充分；
- 有支持但须限定范围；
- 关键支持仍待核实；
- 存在实质性反对或不一致，尚未协调；
- 当前无法评议。

每项附 assessed_scope 和理由，不能只输出标签。若只是整理层错误未修、无法忠实知道原命题，优先说明当前不能评议，不把错误载荷当成论文反证。

---

# 十、检查覆盖、文本边界与盲区

## 10.1 RC-S 完整字段与约束

coverage_id、target_refs、review_domain、consistency_subtype、criterion_scope、check_status、inspected_inputs、review_refs、assessment_refs、unavailable_reason、not_applicable_reason。

check_status 只取“未检查／已检查／当前不可检查／不适用”。同一条覆盖范围必须具体，不用一个“已检查”覆盖多个尚未查的维度。RC-S 的目标可包含论文原文片段；类型和完整身份规则同 11.2。

| check_status | review_refs 约束 | 评议含义 |
|---|---|---|
| 已检查 | 至少一条实际完成判断的 RC-Q，目标、领域、子类与范围对应；finding_status 不得为 null 或当前无法判断 | 已执行并有结果，结果可以正面、疑点或问题 |
| 未检查 | 当前范围 review_refs=[]，如有历史记录另存历史版本 | 不作为支持或否定 |
| 当前不可检查 | 可为空；若已有尝试，引用 finding_status=当前无法判断 的 RC-Q，并给 unavailable_reason | 有具体不能判断的原因，不等于负面结论 |
| 不适用 | 给 not_applicable_reason；不要求创建 RC-Q | 不得伪装检查通过 |

因此“已检查但 review_refs 为空”是不完整正式记录，不与“已检查且未发现明确问题”等价。仅翻过文档但没记录结论，不能标正式已检查。部分可查、部分不可查应按 criterion_scope 拆分覆盖。

RC-A.coverage_refs 需覆盖其实际使用的承重判断；仅有不可检查或未检查项不能支持“支持较充分”。所有已检查 RC-S 不等于充分支持，仍需第九节的正向材料与综合理由。

## 10.2 文本范围与问题归层

只使用允许正文、SI、表格、公式、图注和脚注，不读取图像像素。元信息和星级只读 Zotero 父条目，附件字段仅追溯。

只依赖图内细节而无文字报告时说明具体缺失；外部文献仅有转述时不声称读过全文。不会默认扩展为图像核验、外部检索或其他调查。

解析拆坏数字：problem_locus=解析文本；原文正确而记录层级错：结构化整理；EC-M 错挂已知样品/目标：跨模块关联。三者均不直接构成论文自身缺陷。只有对应原文中的实际不匹配才归原文报告。

## 10.3 作者自述与原文目标

作者明确限制、假设或回应不必被强塞进 TC/EC 新节点。RC-Q 可直接以原文片段为主要对象，issue_origin=作者自述，通过 related_target_refs 指明其约束的 TC/EC 对象。

它可以是正式审查项，只要文本可定位；不因目标是原文而排除。若无法确定限制影响哪个命题，可保留正式文本审查，但不自动进入某个 RC-A 的负面综合，需先说明关联。

---

# 十一、跨模块引用、对象与修改建议

## 11.1 模块身份与独立版本

module 固定为 thought／evidence／review／source。thought/evidence/review 是链模块，source 是论文解析文本的来源命名空间，不新增第四条链。

链文档身份字段为 library_ref、parent_item_key、chain_id、revision、source_set_id。审查链还含 judgment_author、input_chain_refs、input_source_sets、criterion_source_set_id 和第 11.3 各输出表。

**同一审查项的主对象与相关对象允许来自不同模块、不同 chain、不同 revision。** 例如引用 thought revision 3 的节点和 evidence revision 5 的关联完全合法；每个引用独立绑定完整身份，不选择一个“主链 revision”覆盖全部对象。这里的 3/5 仅示意版本编号，不指实际数据。

input_chain_refs 显式列出本次消费的每份输入版本；每个 TC/EC 引用必须能解析到其中对应项。若 EC-M 固定的 TC 版本与本次引用的 TC 版本不同，分别保存两者并复核对应，不因 ID 相同而默认为同一表述。来源集不同分别记录，不强行复制成一个共同来源集。

## 11.2 三种引用结构

### A. 链对象引用

包含 module、document_ref（完整链身份）、target_kind、target_id、target_field。target_field 不指字段时为 null。

TC：node 对应 node_id，edge 对应 edge_id；bridge 使用 edge＋target_field=bridge。

EC：严格消费证据链新增的类型映射：

| target_kind | 对象表 | ID 字段 |
|---|---|---|
| evidence | evidence_records | evidence_id |
| process | process_relations | process_id |
| provenance_group | provenance_groups | group_id |
| use_group | use_groups | use_group_id |
| mapping | mappings | mapping_id |
| candidate_mapping | candidate_mappings | candidate_id |

类型标签与表名不是同一字段，但映射由两侧共同承诺；不能随意写 evidence_records 当 target_kind，也不按单复数自动猜。

### B. 论文原文片段引用

module=source、target_kind=text_span；source_ref 包含 library_ref、parent_item_key、source_set_id、attachment_key、block_id、span、page_pdf、section_path、text_excerpt，target_field=null。

block_id 配合来源版本与附件固定一个块；span 为该不可变解析块中的明确字符区间，或 null 表示整个块。若有区间，使用从 0 起、左闭右开的 Unicode 码点偏移，不能对格式化后的文本重新算位置。页码/章节未知可 null，正式定位至少有来源集、附件、块及对应文本。这里不强制 target_id，因为文本身份已由 source_ref 完整表达；不能以空链对象 ID 伪装成节点。

这既可作为作者自述的主要目标，也可作为依据或回应；纯原文目标和 primary_target_ref=null 的待定位项不同。

### C. 判准来源引用

module=review、target_kind=criterion_source、document_ref 指本审查链固定版本、target_id=RC-K ID，并指定 source_revision 与 location。它不冒充 source 模块的论文片段，不占用 TC/EC source_set。

本 review 内其他对象 target_kind 的映射为 background／review_item／claim_assessment／coverage／suggestion，分别指 RC-B／RC-Q／RC-A／RC-S／RC-D。内部引用可简写 ID 展示，正式记录仍绑定文档版本；本地互引只表示链接，不用 RC-A 反过来作为其自身的科学依据，避免循环。

## 11.3 输出表

| 表 | ID | 用途 |
|---|---|---|
| background_records | RC-B001 | 第二节背景完整字段 |
| review_items | RC-Q001 | 第七节审查项，含待定位项 |
| claim_assessments | RC-A001 | 第九节命题评议 |
| coverage_records | RC-S001 | 第十节覆盖 |
| change_suggestions | RC-D001 | 第 11.4 修改建议 |
| criterion_sources | RC-K001 | 7.3 外部判准登记，未使用时为空 |

各前缀后至少三位十进制数字，同一 chain 内唯一，不复用退役 ID。空表保留。新依据改变同一问题的结论可保留 ID、发布新 revision；中心问题或对象含义实质改变时分配新 ID并记录替代。发布 revision 不原位覆盖，不把链 revision 与 Zotero 同步版本混用。

## 11.4 可消费的修改建议 RC-D

字段：suggestion_id、origin 固定“审查者建议”、review_refs、assessment_refs、target_ref、change_type、proposed_content、rationale、status、result_refs、verification_refs。

change_type 取“调整命题表述／调整层级或连接／更正证据字段／补充证据登记／调整用途关联／补充来源或说明”。涉及尚不存在对象时，target_ref 指已有文档级父容器并另列 proposed_object_kind，不能捏造未来 ID。文档级父容器引用使用真实链 document_ref、target_kind=document、target_id=null、target_field 为已有对象表名或 null；只是建议目的地，不是科学审查对象。

status 取“待处理／已采纳待落实／已拒绝／暂缓／已落实待复核／复核完成”。后两项需要真实 result_refs；复核完成还需 verification_refs 指向复核记录。已采纳不是已落实，已建议不是已修改。

retained_claim 的 suggestion_ref 指向 RC-D；其文本与该建议的 proposed_content 对应，不另建立无身份的第三套修改内容。全部建议保留在审查链，不自动回写 TC/EC/星级。

---

# 十二、完整示例与困难情形

## 12.1 示例约定

以下是人工构造的设计案例，不是实际论文审查，不伪造 DOI、期刊数值、Zotero tag、附件 key、页码或版本身份。表中“示例未提供”明确表示不能直接作为正式发布载荷。

对象标签 TC-N002、TC-N003、TC-L001、EC-E001、EC-M001 沿用配套展示，但只有 Q1 指向其现行限定用途。其他案例是明确标注的独立假想变体，不暗指配套示例或真实论文存在这些问题。

原文片段以 Pα/Pβ/Pγ 作为**本节说明标签，不是 source_ref 或正式 ID**：

- Pα/Pβ：假想论文在同对象、同状态、同单位下，结果段报告 H2 比较方向为 X 较低，SI 表报告 X 较高，且没有明确版本或条件差别。这是内部不一致案例，不是实测结果。
- Pγ：假想作者明确限定只讨论指定电解液/工况，未把命题推广至其他体系。不是反证。

## 12.2 背景 RC-B001：全部字段出现

| 字段 | 示例值 |
|---|---|
| background_id | RC-B001 |
| parent_item_ref | null；未读取真实条目，仅示例 |
| journal_identity | null |
| journal_metrics | []；指标与获取时间未取得，不填假值 |
| personal_rating | raw_tags=[]；parsed_rating=null；scale=null；parsing_rule=null；rating_meaning=null；user_note=null；snapshot_ref=null；read_at=null；check_status=未取得 |
| publication_notes | null；未查询，非“没有版本说明” |
| acquisition_status | 未取得 |
| recorded_at | null；示例未生成真实运行快照 |
| context_note | 本例无背景分，不加分或扣分 |

实际有 journal_metrics 时必须填第二节列出的全部子字段；空列表不是略去 schema。

## 12.3 四类审查项：完整字段对照

列名 Q1—Q4 是阅读简称，实际 review_id 如表。所有出处均未实际提供，因此这些只是字段与逻辑示例。

| 字段 | Q1：正向范围核查 | Q2：假想内部不一致 | Q3：作者自述限定 | Q4：桥依据暂不足 |
|---|---|---|---|---|
| review_id | RC-Q001 | RC-Q002 | RC-Q003 | RC-Q004 |
| primary_target_ref | evidence:mapping EC-M001.target_part；真实版本未提供 | source:text_span Pα 的实际 source_ref 未提供（说明标签） | source:text_span Pγ 的实际 source_ref 未提供 | thought:edge TC-L001.bridge；真实版本未提供 |
| related_target_refs | EC-E001、TC-N002，各自身份未提供 | Pβ；相应命题和结果的真实引用未提供，不编 EC ID | 受限 TC 命题的真实版本未提供 | TC-N003、TC-N002，各自身份未提供 |
| related_review_refs | [] | [] | [] | [] |
| review_domain | 结论匹配 | 来源与一致性 | 结论匹配 | 支持关系与推理桥 |
| consistency_subtype | null | 论文内部一致 | null | null |
| question | H2 结果用途是否被扩成全部副反应？ | 同一比较为何方向相反？ | 作者的明确范围限定是否在评议中被保留？ | 下位命题到副反应命题的解释联系是否有相应前提？ |
| issue_origin | 审查者提出 | 审查者提出 | 作者自述 | 审查者提出 |
| problem_locus | null，现行映射无此问题 | 原文报告，假定已排除解析/状态差别 | null，明确限制不是缺陷 | 当前无法区分 |
| observed_basis | 现行 EC-M001 已限定析氢部分 | Pα/Pβ 在设定条件下方向不一致 | Pγ 明确限定讨论范围 | 示例未提供桥涉及的反应条件和依据 |
| criterion | statement=特定反应不覆盖全部反应；criterion_type=方法学原则；criterion_source_refs=[] | statement=同对象同条件的同一比较应对应；criterion_type=方法学原则；criterion_source_refs=[] | statement=保留作者限定，不自行外推；criterion_type=方法学原则；criterion_source_refs=[] | statement=命题支持须有对应联系；criterion_type=一般逻辑；criterion_source_refs=[] |
| basis_refs | EC-E001、EC-M001，role=缓解疑点；relevance=已有范围限定 | Pα/Pβ，role=支持疑点；relevance=对应比较方向相反 | Pγ，role=限定范围；relevance=只涉及指定工况 | 已有桥及节点，role=限定范围；relevance=仅可识别结构，不能据此确认联系 |
| reasoning | 关联没有宣称覆盖全部副反应，不制造范围问题 | 排除条件和转录差别后存在报告不一致，需协调；不能据此选择哪个结果正确 | 作者限定明确，评议应限定范围；不将“未研究其他体系”当反证 | 缺所需信息，不能凭主题相近认定桥合理，也不认定桥错误 |
| finding_status | 未发现明确问题 | **已定位具体问题**（假想案例内） | 未发现明确问题，仅针对范围忠实性 | 当前无法判断 |
| impact | null | 重要 | null | 待定 |
| impact_reason | 无该项负面发现 | 若未协调，影响该比较的方向判断，不否定全部结果 | 无负面发现；仅限定评议范围 | 未完成判断，不能确定承重影响 |
| direct_effect | 只评价 EC-M001 用途范围 | 影响相应比较，不自动否定核心命题 | 把评议限制到作者明示范围 | 仅说明该 bridge 尚不能评议 |
| response_refs | [] | []；假想未有对应解释 | Pγ 的明确说明即范围依据 | [] |
| resolution_status | null | 开放 | null | 开放 |
| resolution_requirements | null | 协调原文方向或给出明确状态/版本区别 | null | 提供桥所依赖的反应条件及原文依据 |
| review_scope | 析氢材料到 B1 部分用途 | Pα/Pβ 的同一比较 | 作者明确讨论的对象和工况 | 指定 bridge，不包括全部机制 |
| suggestion_refs | [] | RC-D001；暂缓确定方向的建议，不改数值 | [] | [] |
| text_locations | 示例未提供 | Pα/Pβ 实际定位未提供 | Pγ 实际定位未提供 | 示例未提供 |

上表 criterion 统一使用 statement、criterion_type、criterion_source_refs 三个正式字段，不另设缩写键。Q3 的主要对象就是原文限定片段，不用 null 挤入“待定位”。

## 12.4 覆盖 RC-S：执行与结果分开

| 字段 | RC-S001 | RC-S002 | RC-S003 | RC-S004 |
|---|---|---|---|---|
| coverage_id | RC-S001 | RC-S002 | RC-S003 | RC-S004 |
| target_refs | Q1 对象（实际身份未提供） | Pα/Pβ 实际 source_ref 未提供 | Pγ 实际 source_ref 未提供 | Q4 bridge（实际身份未提供） |
| review_domain | 结论匹配 | 来源与一致性 | 结论匹配 | 支持关系与推理桥 |
| consistency_subtype | null | 论文内部一致 | null | null |
| criterion_scope | H2 用途范围 | 同比较方向一致性 | 作者范围是否保留 | bridge 的联系依据 |
| check_status | 已检查 | 已检查 | 已检查 | 当前不可检查 |
| inspected_inputs | 设定的结果与映射 | 假想 Pα/Pβ | 假想 Pγ | 示例桥；必要内容未提供 |
| review_refs | RC-Q001 | RC-Q002 | RC-Q003 | RC-Q004 |
| assessment_refs | []，除非同一范围实际纳入某评议 | RC-A001（仅下述独立变体） | [] | [] |
| unavailable_reason | null | null | null | 缺反应条件及解释依据 |
| not_applicable_reason | null | null | null | null |

这些表为案例配对，不表示完成了真实核对。“已检查＋空 review_refs”不应被接受为同等示例。

## 12.5 命题评议 RC-A001：支持与相反材料都有位置

**本评议只对应 Pα/Pβ 的独立假想变体，不将其冲突嫁接到现行配套 EC-E001。** 假想被评命题声称同条件下 X 的 H2 较低；目标真实 ID 未提供，不借用一个含义不同的现行节点。

| 字段 | 示例值 |
|---|---|
| assessment_id | RC-A001 |
| target_ref | 假想 TC 节点的真实文档身份及 ID 未提供；发布前必须具备 |
| assessed_scope | Pα/Pβ 对应的同对象、同工况 H2 比较方向 |
| background_refs | RC-B001；无指标，不加扣分 |
| evidence_refs | []；只有直接文本依据，尚未登记 EC，不能造 ID |
| mapping_refs | []；本例不伪造作者用途关联 |
| evidence_review | RC-Q002：已经定位报告方向不一致 |
| logic_review | []；本评议尚未评议其他推理步骤 |
| provenance_handling | 两处报告视为同一比较的不同报告位置，不算独立重复 |
| supporting_basis | ref=Pα；relevance=报告 X 较低；conditions_match=案例设定同条件；response_refs=[] |
| counter_basis | ref=Pβ；relevance=报告同条件 X 较高；conditions_match=案例设定同条件；response_refs=[] |
| unresolved_reviews | RC-Q002：比较方向尚未协调，不只是“可能有疑点” |
| synthesis | 承重依赖是同一比较的方向；有正向报告也有条件对应的相反报告；已排除设定中的转录/状态差别；无回应协调，不能选一个方向，也不把两个位置当两份独立证据计票 |
| reliability_statement | 存在实质性反对或不一致，尚未协调；仅针对该比较范围 |
| retained_claim | text=该比较的报告方向尚待协调，暂不保留确定方向的结论；origin=审查者建议；suggestion_ref=RC-D001。它是本地暂缓表述建议，不改写作者原命题 |
| review_scope | 仅论文内的这项报告对应，不评其他性能和机制 |
| unassessed_parts | 方法、参数和其他支持路径尚未评议 |
| coverage_refs | RC-S002 |

这展示从具体依据、RC-Q 状态及承重影响到结论的路径，不是由“一条重要问题”机械推出负面总评。

## 12.6 修改建议与本地 retained_claim

RC-D001 与 12.5 的同一假想内部不一致案例配套。尚不知哪个方向正确，因此不建议改成“较高”或“较低”，只建议在协调前暂缓确定方向的表述。该建议保存在审查链，不自动修改原文或 TC。

**RC-D001 的全部字段：**

- suggestion_id：RC-D001。
- origin：审查者建议。
- review_refs：RC-Q002。
- assessment_refs：RC-A001。
- target_ref：RC-A001 所评的同一假想 TC 节点；真实版本引用未提供，不虚构 ID。
- change_type：调整命题表述。
- proposed_content：该比较的报告方向尚待协调，暂不保留确定方向的结论。
- rationale：直接对应 RC-Q002 的报告冲突；不在未核清前选择有利方向。
- status：待处理。
- result_refs：[]，未落实。
- verification_refs：[]，未复核。

RC-A001.retained_claim 的 text 与本建议的 proposed_content 相同，并标 origin=审查者建议和 suggestion_ref=RC-D001；不是作者明示或文本重构的 TC 节点，也不是已经修改的结果。没有必要修改表述时仍允许 retained_claim=null。

本示意目标未提供，不作为可执行建议交付。实际创建建议需有现存目标或正式文档容器及相应依据。

## 12.7 外部判准空缺与原文目标

本轮没有读取任何外部判准文献，故 criterion_sources=[]、criterion_source_set_id=null，不生成虚构 RC-K 记录。第 7.3 字段规定的是将来实际使用时的登记要求。

原文目标 Pγ 的实际引用必须填写 source_set_id、attachment_key、block_id 和实际片段，页码可空；本例没有这些信息所以不填假来源。正式 target_ref 可寻址原文，而不是因作者限制不在 TC/EC 就丢失它。

---

# 十三、展示与验收

文献概览并列显示背景与各命题评议，不由期刊/星级直接给“可靠”标签。

定点视图按第三节七个 review_domain 展示，来源与一致性再按子类筛选。节点、边整体、bridge、证据对象和原文目标分开显示，保留主对象与相关对象关系。

命题视图展示 supporting_basis、counter_basis、承重依赖、问题归层、已处理与未处理事项、综合理由、结论及本地建议。未评议部分明确列出。

覆盖视图显示未检查、已检查及其 RC-Q、当前不可检查及原因、不适用及原因。待定位问题、未落实建议、外部来源待核对独立保留，不作负面科学计分。

正式输出的基本检查包括：类型映射双侧一致；每个对象独立固定版本；source 文本可定位；RC-Q 主领域单值；来源子类合法；RC-A 有支持/相反材料落点与综合理由；已检查覆盖有结果；作者限制不会丢失；retained_claim 明示本地建议；建议状态与实际结果对应；外部判准不伪造。

**本模块始终基于论文本身进行内容审查，区分提取质量与论证质量，不自动回写、不把信息不足写成已确定缺陷。**
