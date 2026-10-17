你需要先判断一个 Zotero 条目对应的 PDF 属于"原创研究论文"、"综述论文"还是"图书"。

请以 PDF 附件为主要依据，Zotero metadata 只能作为辅助依据。不要只靠标题里的 review、progress、perspective、overview 等词做判断；必须综合摘要、目录/章节、图表类型、方法部分、结果部分、参考文献密度和结论写法。

## 判定目标

只允许输出以下四类之一：

- research_article
- review_article
- book
- uncertain

## 判定标准

### research_article

满足多数条件时判为 research_article：

- 论文报告新的实验、计算、理论推导、数据集、器件测试、材料合成、表征结果或机制验证。
- 有明确的 Experimental / Methods / Materials and methods / Computational methods / Results and discussion 等原创工作结构。
- 图表主要用于呈现作者自己的数据，例如 XRD、SEM/TEM、XPS、XAS、电化学曲线、DFT、统计图、性能对比、原位/后解析表征。
- 结论围绕"本文提出/制备/证明/发现/实现"的新结果展开。
- 参考文献服务于背景、方法来源、对比基准或机制讨论，而不是全文主体。

### review_article

满足多数条件时判为 review_article：

- 论文主要整理、分类、比较、综合一个领域或一个材料/方法/机制体系。
- 通常没有完整的新实验流程；即使有图表，也多为综述框架图、机制示意、文献数据汇总、分类图、路线图或挑战展望。
- 章节通常围绕材料类别、机制类别、策略类别、应用场景、挑战与展望组织。
- 参考文献密集，作者经常比较多个研究之间的共识、争议和发展脉络。
- 结论强调领域趋势、未解决问题、未来方向，而不是单一实验体系的结果。

### book

满足多数条件时判为 book：

- 内容是整本书、专著、教材或学位论文，而非期刊单篇论文。
- 通常有目录、前言、章节编号（如"第 1 章""Chapter 1"）和索引/参考文献附录。
- 章节按知识体系或主题组织，覆盖一个领域的系统知识，而不是报告单一实验或综述一个专题。
- 引用风格偏书籍（章末注释、延伸阅读、索引），而非期刊论文的密集编号引用。
- 若 Zotero 元信息 item_type 为 book 或 bookSection，可作为辅助依据。

### uncertain

仅在以下情况判为 uncertain：

- PDF 不是主文，只有 supporting information、封面、目录、图形摘要或不完整片段。
- PDF 扫描/OCR/读取严重失败，无法看到摘要、正文结构或图表。
- 论文同时有综述、原创研究或图书特征，且证据不足以判断主导属性。

## 输出格式

必须只输出一个紧凑 JSON 对象，不要输出解释文字，不要包裹代码块。

字段固定如下：

- paper_type: 四选一，必须是 research_article、review_article、book 或 uncertain
- confidence: 0 到 1 之间的小数
- evidence: 3 到 5 条判定证据，每条必须包含 PDF 中可见的章节、页码、图表类型或内容线索
- reason: 一句话总结为什么这样分类

示例格式：

{"paper_type":"research_article","confidence":0.86,"evidence":["摘要提出作者制备了新的电极材料并报告性能数据","正文包含 Experimental section 和 Results and discussion","图3-6主要是作者自己的结构表征和电化学曲线"],"reason":"该文以原创材料制备和性能验证为主，而不是领域综合。"}
