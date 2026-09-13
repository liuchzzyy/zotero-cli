## 通用执行规则

本文件中的 Zotero workflow 默认通过对应 `tools\*.ps1` wrapper 重新打开一个新的 PowerShell 窗口运行。wrapper 会在新窗口中输出阶段进度，写入运行目录中的 `run.log` 和 `progress.jsonl`；不要再依赖额外的 watch 命令作为主进度来源。

以下规则也已分别内嵌到每个“推荐给代理的直接提示词”中，避免单独复制某一节时遗漏执行约束。

推荐操作顺序：
1. 在仓库根目录运行对应 wrapper，让它打开新的 PowerShell 窗口。
2. 保留新窗口，进度以新窗口中的 wrapper 输出和 `run.log` / `progress.jsonl` 为主。
3. 必要时再检查 `log\...` 运行目录里的 `run.log` / `progress.jsonl` / `inventory.json`。
4. 汇报进度必须基于 wrapper 输出、`run.log`、`progress.jsonl` 或实际产物变化；不要只说“已开始”。
5. 所有 stdout/stderr 和中间日志都放在对应 `log\...` 运行目录；不要散落在仓库根目录、`tools\`、`tmp\` 或临时 `.workspace\...` 中。
6. 仅限调试/CI 时使用 `-RunInCurrentWindow` 在当前 PowerShell 中运行实际流程。

## Workspace RAG Incremental Index

### 推荐给代理的直接提示词
```text
当前项目名称统一为 `zotero-cli`，MacBook 仓库位于
`/Users/liuchzzyy/python-code/zotero-cli`。日常工作区目标为
`.workspace/501-mno2-zn`，Windows PowerShell 包装器仍按仓库根目录相对寻址。
对应 Zotero 集合 501-MnO2-Zn 及其子集合。

集合 key：
KRI7W5QZ,8FAPWVJM,8J9NPUPR,PD6IDJ2R,PUR627E3,R7VCZW46,S67ZHINI,WR5PK9MT,XIWCAHQT,ZF6UGG6U

安全边界：
- 只通过 tools\run-rag-workspace.ps1 更新 workspace/index/embedding。
- 不直接写 rag.idx.sqlite，不直接写 zotero.sqlite，不写 Zotero Web API。
- .workspace\501-mno2-zn 是持久 RAG workspace，保留。
- .zot\state\pdf_cache.sqlite 是 MinerU/PDF 抽取共享缓存，保留；删除它只会导致以后重抽 PDF。
- log\rag-workspace-* 和 log\rag-evidence-search-* 是运行日志；确认不需要复盘后可以删除。

先 dry-run 看是否有新增或缺口：
$collections = 'KRI7W5QZ,8FAPWVJM,8J9NPUPR,PD6IDJ2R,PUR627E3,R7VCZW46,S67ZHINI,WR5PK9MT,XIWCAHQT,ZF6UGG6U'
pwsh -NoProfile -ExecutionPolicy Bypass -File tools\run-rag-workspace.ps1 `
  -WorkspaceName 501-mno2-zn `
  -Collections $collections `
  -DryRun `
  -KeepLog `
  -RunInCurrentWindow

正式增量更新。默认新窗口运行，进度看新窗口和 log\rag-workspace-*\logs\index.log / embed.log：
$collections = 'KRI7W5QZ,8FAPWVJM,8J9NPUPR,PD6IDJ2R,PUR627E3,R7VCZW46,S67ZHINI,WR5PK9MT,XIWCAHQT,ZF6UGG6U'
pwsh -NoProfile -ExecutionPolicy Bypass -File tools\run-rag-workspace.ps1 `
  -WorkspaceName 501-mno2-zn `
  -Collections $collections `
  -KeepLog

只补 embedding，不重新 PDF 抽取或 item index：
pwsh -NoProfile -ExecutionPolicy Bypass -File tools\run-rag-workspace.ps1 `
  -WorkspaceName 501-mno2-zn `
  -EmbedOnly `
  -KeepLog

完成后复核计数（chunks 在 SQLite，向量在 Qdrant local）：
@'
import sqlite3
from pathlib import Path
from zotero_cli.config import load_vector_store_config
from zotero_cli.core.semantic_search import QdrantVectorStore, resolve_vector_store_path

p = Path(".workspace/501-mno2-zn/rag.idx.sqlite")
con = sqlite3.connect(p)
cur = con.cursor()
items = cur.execute("select count(distinct item_key) from chunks").fetchone()[0]
chunks = cur.execute("select count(*) from chunks").fetchone()[0]
con.close()

vs = QdrantVectorStore(resolve_vector_store_path(load_vector_store_config()), "ws_501-mno2-zn")
with_embedding = vs.count()
vs.close()

print("items", items)
print("chunks", chunks)
print("with_embedding", with_embedding)
print("missing_embedding", max(chunks - with_embedding, 0))
'@ | uv run python -

查证据。BM25 和 embedding 已完成时默认用 auto，也就是 hybrid；rerank 是查询时在线精排，不是离线构建步骤：
pwsh -NoProfile -ExecutionPolicy Bypass -File tools\run-rag-evidence-search.ps1 `
  -WorkspaceName 501-mno2-zn `
  -Question "MnO2 zinc battery failure degradation mechanism Mn dissolution structural collapse capacity fading electrolyte evidence" `
  -Mode auto `
  -TopK 8 `
  -RerankTopN 50 `
  -KeepLog `
  -RunInCurrentWindow

如果 rerank provider 异常，先加 -NoRerank 退回 hybrid 原始排序；如果只看正文或补充信息，加 -PdfKind main 或 -PdfKind supplementary。

清理中间文件：
- 可以删除 log\rag-workspace-* 和 log\rag-evidence-search-*。
- 不删除 .workspace\501-mno2-zn。
- 不删除 .zot\state\pdf_cache.sqlite。
- 不删除 .workspace\_qdrant（本地 Qdrant 向量库）。
```
