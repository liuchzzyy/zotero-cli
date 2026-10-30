# ai-note2 结构化编码与派生索引设计

## 1. 目标与边界

本设计解决当前单份 `note.md` 同时展开思维链、证据链和审查链，导致对象与关系难以区分的问题。它不改变科学内容的权威归属，也不通过新的模型调用生成事实。

- `analysis.json` 中的 `Note2Document` 继续是单篇论文、单次运行的唯一科学内容源。
- `object-index.json` 是可删除、可重建的派生索引；它只保存摘要、指针、关系投影和核对状态，不允许独立编写科学内容。
- Markdown、HTML 和 SVG 都是从已校验文档与派生索引生成的视图。Zotero child note 也是视图，不是独立权威记录。
- 当前输入只有文本、表格文字、公式文字、图注和脚注。图号或图注定位不能表述为已查看图像像素。

## 2. 现有权威流程

1. `inputs.collect_inputs()` 选定旧 AI note，按 PDF 摘要去重附件，并冻结 `inputs.documents` 与 `source_blocks`。
2. `generation.generate_document()` 让模型产生三链对象的临时 ID；程序覆盖库、父条目、运行、输入与来源快照。
3. `validation.populate_source_excerpts()` 从冻结块填入原文；`allocate_indexes()` 按对象类型分配规范 ID 和运行内 index；`validate_document()` 校验结构、引用、来源、支持图、处理图和审查覆盖。
4. `verification.verify_document()` 先校验来源块、片段与图注图号，再可选地让模型判断对象陈述是否被所给文字支持。该判断不修改 `Note2Document`。
5. `rendering.write_artifacts()` 保存本地产物；`persistence.publish_document()` 发布 HTML 视图并回读 note 与父条目标签。

## 3. 对象编码合同

下表只整理已实现类型，不增加推测层。“必需”表示 JSON 字段必须存在；某些必需字段的值允许 `null` 或空数组，必须同时保留现有状态或缺失说明，不得伪造填充值。

| 范围 | 类型 | 主要必需字段 | 可空／条件字段 | 派生字段 |
| --- | --- | --- | --- | --- |
| 论文与运行 | `Note2Document` | `library_ref`, `parent_item_key`, `run_number`, `inputs`, `source_blocks` | `inputs.documents[*].parse_metadata` 的未取得版本或时间可为 null | 分析摘要、运行命名空间、索引新鲜度摘要 |
| 思维 | `TC-N` 论点 | `node_type`, `text`, `attribution`, `extraction_status`, `text_locations` | `claim_kind`, `argument_angle`, `presentation_note` | 简短摘要、未评议标记 |
| 思维 | `TC-L` 支持关系 | `from_node_id`, `to_node_id`, `bridge`, `target_part`, `combination` | 联合支持时才有 `edge_ids` | `argument_support` 关系投影 |
| 思维 | `TC-C` 候选 | `candidate_kind`, `unresolved_description`, `decision` | 联系候选才有两端；接受后才有 `resolution_refs` | `candidate_argument` 投影与未解标记 |
| 证据 | `EC-E` 报告结果 | 对象与条件、方法、原始结果表述、结果类型、来源 | 数值、单位、归一化基准、比较、不确定度、作者解释、文献来源可为 null；缺失语义用 `field_statuses` | 未关联证据标记 |
| 证据 | `EC-P` | 输入结果、输出结果、处理类型、操作 | 模型或参数可为 null | `processing_derivation` 投影 |
| 证据 | `EC-G` | 成员与共同来源说明 | 不适用于表示独立支持 | `shared_provenance` 投影 |
| 证据 | `EC-U` | 成员角色、组类型、联合说明 | 非比较用途时 `comparison_statement` 可 null | `combined_use` 投影 |
| 证据 | `EC-M` | 证据主体、论点／支持链目标、用途角色和描述 | 不得将作者联系改写为因果证明 | `evidence_use` 投影 |
| 证据 | `EC-C` | 证据主体、候选目标、未解说明、决策 | 接受后才有 `resolved_mapping_ids` | `candidate_evidence_use` 投影 |
| 审查 | `RC-Q` | 问题、领域、判准、依据、推理、范围 | 未实际判断时 `finding_status=null`；当前无法判断与已定位问题不得混同 | `reviews` 投影 |
| 审查 | `RC-S` | 范围单元、目标、领域、检查状态 | 不可检查与不适用分别要求原因 | `review_coverage` 投影 |
| 审查 | `RC-A` | 命题目标、评议范围、材料平衡、可靠性表述、未评部分 | 只有审查者建议保留时才有 `retained_claim` | `claim_assessment` 投影 |
| 审查 | `RC-B` / `RC-K` / `RC-D` | 现有 schema 声明的背景、判准来源、修改建议字段 | 未取得与不适用使用各自状态 | 背景、判准、建议的引用投影 |

记录的 `record_meta`、`index` 和 `figure_refs` 沿用现有合同。`display` 只是可选展示建议，不参与身份或科学判断。

## 4. 身份与引用

- 短身份沿用已有类型 ID，例如 `TC-N001`、`EC-E002`、`RC-Q001`。它们易读且可以直接区分类型。
- 长展示 index 沿用 `<parent_item_key>-<run_number>-<ordinal>`。它是已产生分析内的稳定展示号，不表示永久身份或支持强度。
- 索引的规范引用是 `{library_ref, parent_item_key, run_number, object_id}`。引用 bridge 时使用现有 `target_field=bridge`，不创建第四级对象。
- 索引条目按 `object_registry` 排列，不按当前数组位置重新分配身份。已编号分析只重排展示时，对象 ID、index 和规范引用不变。
- 新运行可重新分配对象。本实现不用文本相似度对齐、合并或迁移跨运行对象。

## 5. 类型化关系

`object-index.json.relations` 只投影已有记录，不为图形完整性补边。

| 索引关系 | 权威来源 | 语义 |
| --- | --- | --- |
| `argument_support` | `TC-L` | 作者明示或从文本重构的理由→分论点／分论点→核心论点 |
| `candidate_argument` | `TC-C` | 尚未接受的命题或联系候选；保留 `decision` |
| `processing_derivation` | `EC-P` | 已登记证据的处理、拟合、计算或汇总方向 |
| `shared_provenance` | `EC-G` | 成员共用数据或来源；明确防止将其计为独立证据 |
| `combined_use` | `EC-U` | 作者联合、比较或汇集使用多项结果 |
| `evidence_use` | `EC-M` | 报告结果／联合组对论点、支持关系或 bridge 的作者用途 |
| `candidate_evidence_use` | `EC-C` | 未解用途候选；不升级为正式支持 |
| `reviews` | `RC-Q` | 审查问题对对象或关系的评论 |
| `review_coverage` | `RC-S` | 已声明范围单元的检查状态 |
| `claim_assessment` | `RC-A` | 对命题在指定范围内的综合评议 |

关系的 `status` 只使用来源记录已有生命周期或候选决策。`shared_provenance` 和 `combined_use` 不换算为多份独立支持；`evidence_use` 不把作者解释升级为已证明因果。

## 6. 来源、核对与新鲜度

每个索引条目保存：

- 完整对象的 JSON Pointer；
- 来源定位所在的对象内 JSON Pointer；
- `source_set_id`、`attachment_key`、`block_id`、`span`、PDF 页、章节和 `source_blocks` 指针。

数值、单位、归一化基准、电流／倍率、电压窗口、循环数、样品、拟合方法、模型约束和不确定度继续保存在 `EC-E` 的原字段。索引只显示紧凑摘要与缺失状态，不重算、平滑、单位换算或替换原值。

视图必须区分四层结论：

1. `schema_and_references`：结构、枚举、引用、图与覆盖合同是否通过。
2. `source_location_consistency`：位置、span 和 excerpt 是否与本次冻结块一致；这不验证图像像素或科学正确性。
3. `model_source_assessment`：可选模型是否认为陈述被所给文字支持。缺失时明示 `absent`，不推导为通过。
4. scientific review：`RC-Q/RC-S/RC-A` 的问题、范围、判准、发现与限制。它们不被前三层替代。

`object-index.json.source` 保存规范 JSON 的 SHA-256、schema 版本、库／父条目／运行身份、输入文档摘要和核对报告摘要。校验器重新生成索引并做全等比较；任一摘要、引用、关系类型或核对状态不一致都视为过期或失真。

## 7. 输出结构

| 文件 | 用途 |
| --- | --- |
| `analysis.json` | 唯一完整科学内容源 |
| `object-index.json` | 可机器读取的紧凑对象与类型关系索引 |
| `overview.md` | 论文级概览、警示、核心论点、跨链关系与对象总索引 |
| `thought.md` | 论点、支持关系和候选的完整字段视图 |
| `evidence.md` | 报告结果、条件、处理、同源、联合用途、映射和未关联证据视图 |
| `review.md` | 审查问题、判准、发现、覆盖、限制与未评命题视图 |
| `note.md` / `note.html` | 适合 Zotero 的紧凑总览；使用可见 ID 和来源定位，不复制完整记录 |
| `tree.svg` | 辅助图；不作为 Zotero note 的必需交互元素 |
| `run.json` | 执行、模型、用量、核对和文件摘要；不复制科学对象 |

该组合将人阅读的三链分开，并用一份索引承担跨链导航。本地详细视图与 Zotero 紧凑视图都由同一个转换器生成，避免两套人工维护的内容。

## 8. 合成示例（不是真实科学数据）

```json
{
  "schema_version": "ai-note2-index/1",
  "source": {
    "library_ref": {"library_type": "user", "library_id": "synthetic"},
    "parent_item_key": "SYNITEM1",
    "run_number": 1,
    "analysis_sha256": "<synthetic-sha256>"
  },
  "objects": [
    {
      "short_id": "EC-E001",
      "object_type": "EC-E",
      "summary": "合成样品在未提供的测试条件下报告一项结果",
      "full_object_pointer": "/evidence/evidence_records/0",
      "flags": ["missing_context", "unlinked_evidence"],
      "model_source_assessment": {"state": "absent"}
    }
  ],
  "relations": [
    {
      "relation_id": "shared_provenance:EC-G001",
      "relation_type": "shared_provenance",
      "status": "active",
      "member_refs": ["EC-E001", "EC-E002"],
      "note": "成员共用合成原始数据，不是两份独立证据"
    }
  ]
}
```

实际输出中的摘要会由程序计算；示例中的值只用于说明结构，不代表任何论文、样品或实验。

## 9. 兼容与迁移

- 不修改、删除或覆盖旧 AI note、旧 ai-note2 产物或已发布 Zotero note。
- 仅新运行生成新索引与分链视图。已存在的 `analysis.json` 仍可由当前 `ai-note2/1.0` 校验器读取。
- 本次不需要数据迁移，也不引入旧格式兼容包装。如果未来要为旧运行批量补生索引，应作为单独、只读的明示命令审批和实现。
