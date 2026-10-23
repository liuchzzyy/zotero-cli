# ai-note 两阶段与无损图片处理验证记录

状态：实现已留在工作区，未提交。**离线验证通过；真实图像覆盖通过；文本质量门槛尚未确认通过。**

## 已验证的行为

- 文字阶段不带图；视觉阶段带文字证据包、图号/附件/页码/图注索引和全部有效正文图，SI 无固定 30 张上限。
- 单图 5 MiB 与请求体 20 MiB 为项目阈值；规划及发送使用相同 JSON 参数构造，发送边界再次测量实际 UTF-8 字节数。
- 图片不缩放、不降低 JPEG 质量。超限时尝试无损 PNG 重编码，未变小时保留原图；解码失败的正文图报错，确认的 1x1 占位图显式记录。
- 测试用细线与标签 PNG、透明/调色板 PNG 及 16 位 PNG 的重编码后解码像素完全一致，原文件不变；真实样本中抽查的 6 张正文图无损候选未更小，最终仍保留原图，不能声称本样本的收益来自压缩。
- 客户端视觉请求遭图像能力拒绝时保留同组图片尝试备用路由，不保存纯文本降级结果。
- 来源与图号验证、预算边界、损坏图、无远端写入的两阶段 HTTP 流程、预览估算边界已有回归测试。

## 本地命令证据

在当前工作区实际运行：

| 命令 | 最近观察到的结果 |
| --- | --- |
| `uv run --no-sync pytest -q --tb=short` | 2226 passed；1 条既有 getpass 终端回显警告；exit 0 |
| `uv run --no-sync ruff check .` | All checks passed；exit 0 |
| `uv run --no-sync ruff format --check .` | 228 files already formatted；exit 0 |
| `uv run --no-sync ty check` | All checks passed；exit 0 |
| `git diff --check` | 无错误；exit 0 |
| `uv build` | wheel/sdist 成功；exit 0 |
| `uv run --no-sync zot ai-note --help` | 正常输出帮助；exit 0 |
| `uv run --no-sync zot --json ai-note BGGDG4UN --dry-run` | 只读缓存预览成功，未调分析模型/写笔记 |

这些检查不代表远端 CI、PR review 或科研事实全部通过核查。实现仍未提交，远端库没有写入。

## 真实缓存覆盖对照

旧策略列只计算旧上下文预算与 30 张上限，不是对旧代码运行的完整请求体复现；新策略列为 dry-run 给最大证据包预留后的估算，实际以真实第二阶段为准。

| 条目 | 正文图 | SI 总数 | 旧策略 SI 计数 | 新策略 SI 估算 | 限制原因 |
| --- | ---: | ---: | ---: | ---: | --- |
| BGGDG4UN | 24 | 68 | 30 | 68 | 全部可放入 |
| IRWKS6VT | 50 | 201 | 30 | 42 | 上下文预算 |
| Q6MV2D7K | 44 | 87 | 30 | 56 | 上下文预算 |

## 真实模型生成与对照

条目：Regulating proton distribution by ion exchange resin to achieve long lifespan aqueous Zn-MnO2 battery。使用本机配置的模型网关；PDF 与 MinerU 内容均来自本地真实缓存。

所有 `add_note` 调用被本地 HTML 接收器截留，`no_tag=True`、`no_short_note=True`，**Zotero 写入为 0**。对照采用完整文字与 24 张正文图、跨页抽取 30 张 SI，是单次输入策略对照；不是历史提交完整回放，也不是有统计功效的重复试验。

| 指标 | 单次输入对照 | 两阶段首轮 |
| --- | ---: | ---: |
| 实际发送正文 / SI 图 | 24 / 30 | 24 / 68 |
| 请求体（视觉成稿） | 2,320,053 bytes | 3,515,503 bytes |
| 内容块 | 38 | 40 |
| h2 主章节 | 10 | 10 |
| 表格 | 3 | 3 |
| 列表项 | 49 | 46 |
| 纯文本字符（BeautifulSoup，空格分隔） | 20,663 | 17,931 |
| 运行耗时 | 376.19 s | 718.12 s，含分类和文字取证 |
| 成功响应 usage.prompt_tokens | 100,662 | 180,418，含分类和取证 |
| 成功响应 usage.completion_tokens | 18,807 | 71,980，含分类和取证/模型推理计数 |
| 视觉成稿模型 | Gemini 3.8 Flash | Gemini 3.8 Flash |

两次均先尝试 DeepSeek，视觉请求超时后由 Gemini 完成。usage 不含未返回用量的超时请求，不代表总费用；未取得价格或账单数据。耗时不是无超时条件下的性能基准。

## 科研内容抽查

- 两版均覆盖 1450 h、170/384 mAh g^-1、99.84%、75.6 mAh、489.2/9.95 Ω、b=0.50156。以上主要数字已在结构化内容或原 PDF 的 Zotero 全文层查到；具体条件仍需逐条审阅，数字出现并不证明所有语义正确。
- 两阶段首轮指出 Fig. S24 图注写 50 次循环而图中写 200 次。已实际查看原始 SEM 图片，内嵌文字为 `After 200 cycles`，比例尺 `2 μm`；因此此项视觉观察有直接支持。
- **首轮质量缺陷**：两阶段笔记将合成用量写为 `1.448 g`，原 PDF 全文层实际是 `1.44 g`。MinerU 输入为 `1.44` 后紧跟 LaTeX 中被识别为 `8` 的单位字符；不能把两者合成新小数。单次对照未出现 `1.448`。据此，首轮不能通过“输出质量不下降”门槛。
- 已新增修订：证据包附带程序从原来源页提取的 `source_excerpt`；核对 claim/data/conditions 中的小数是否存在于原输入；所引页没有而其他页有时附加实际页码片段，明确其仅为数值字符支持，不能自动确认数值与样品/条件相符；全部输入找不到则拒绝，避免拼接 OCR 单位。针对该缺陷先观察 RED，再改实现，GREEN 已通过。该检查不验证整数、所有换算或最终生成的全部数值，也不等同完整科学审稿。
- 首轮还凭输入以外的信息补出了期刊分区与 IF > 18，该外部评价没有本次输入证据；已在提示词中禁止凭记忆补充，约束效果仍待真实输出核查。
- v2 真实重测于 389.87 s 停在中间包校验，未写任何笔记。拒绝 `0.3` 的直接原因是校验器误将句号后的空格去掉；另有模型把 SI 表格数值写在正文来源标签下。两项均已定位并修订，新增句子边界与跨页来源测试均观察到 RED/GREEN。
- 修复后离线重放 v2 的真实 43 条证据包通过，含来源片段共 114,859 字符，附 32 项跨页数字字符线索。该结果不等同 43 条科学主张均正确。
- v3 重放真实第一阶段输出、重新在线调用视觉成稿，仍不写 Zotero。运行结果尚待观察；不会称为两次全新在线请求。进程 `proc_266f772d0aac` 有 600 s 硬期限。已补录 7 个生产源码 SHA-256 与输入包哈希至 `v3/audit-manifest.json`；该记录是启动后审计快照，首轮/对照没有启动时的源码哈希收据，不能宣称为严格版本固定实验。

## 模型顺序

`[ai_notes]` 现默认 `model = "antigravity/gemini-3.8-flash"`、`fallback_models = ["group/deepseek"]`；代码默认、示例配置与 README 同步。依据是本篇 160k token / 92 图视觉请求下 DeepSeek 连续两次 `APITimeoutError`（300 s 上限），Gemini 在同一请求上完成。顺序由 `get_model_chain()` 决定，链条顺序不被 `allowed_models` 集合去序。`REQUEST_CEILING = 800_000` 仍对两条路由同时生效，切序不改变图片覆盖。deepseek 仍可作备用；两者质量对比未做受控实验，不能声称 Gemini 文本质量更高。

## 本地审查材料

- `.zot/log/two-stage-qa/report.json`：首轮两阶段执行/用量与图像清单。
- `.zot/log/two-stage-qa/BGGDG4UN.html`：首轮笔记，包含已知 `1.448 g` 问题，勿当成已校正笔记。
- `.zot/log/two-stage-qa/baseline-report.json`、`baseline.html`：单次输入策略对照。
- `.zot/log/two-stage-qa/coverage.json`、`compression.json`：离线覆盖与像素抽查记录。
- `.zot/log/two-stage-qa/quality-comparison.json`：结构计数，不能取代内容核查。

以上本地文件可能包含论文内容，位于现有忽略目录，不加入 Git；此文档仅记录有界的验证结论。
