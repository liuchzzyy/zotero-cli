# zotero-cli

面向科研工作流和 AI Agent 的 Zotero CLI，命令入口为 `zot`。

- **本地读取**：只读 Zotero SQLite 数据库。
- **远程写入**：通过 Zotero Web API，不直接修改 `zotero.sqlite`。
- **Agent 接口**：稳定 JSON envelope、类型化退出码、`--dry-run`、幂等键和运行时 schema。
- **PDF 解析**：MinerU 云端解析、内容哈希缓存、结构化 `content_list.json`。
- **AI 条目笔记**：`ai_analyze` 自动生成带插图的 HTML note 与关键词简记，写回 Zotero。
- **Workspace RAG**：工作区全文检索（FTS5/BM25）加可选 embedding/rerank。

## 安装

需要 Python 3.10–3.13 和 [uv](https://docs.astral.sh/uv/)。

```bash
git clone git@github.com:liuchzzyy/zotero-cli.git
cd zotero-cli
uv sync --dev
uv run zot --help
```

## 配置

复制示例配置并按本机环境填写：

```bash
cp .zot/config.example.toml .zot/config.toml
```

至少配置 Zotero 数据目录：

```toml
[default]
profile = "macbook"

[profile.macbook]
data_dir = "/Users/your-name/Zotero"
prefs_js_path = "/Users/your-name/Library/Application Support/Zotero/Profiles/<profile>/prefs.js"
```

涉及写入时，还需要 Zotero User ID 和具有写权限的 API key。MinerU PDF 解析需要配置 `mineru_token`，AI 笔记需要配置 `[ai_notes]`。真实配置文件、缓存和本地索引不会提交到 Git。

AI 笔记相关配置（`[ai_notes]`）：

```toml
[ai_notes]
api_key = ""
base_url = "http://127.0.0.1:8317/v1"
model = "deepseek-flash"
fallback_models = ["<备用模型1>", "<备用模型2>"]
reasoning_effort = "max"
api_mode = "auto"
chat_token_param = "auto"
temperature = 0.7
```

`fallback_models` 可以是 TOML 数组，也可以是逗号分隔字符串。主模型调用失败或拒绝图片输入时，按顺序回退到后续模型。

常用环境变量：

```text
ZOT_DATA_DIR
ZOT_LIBRARY_ID
ZOT_API_KEY
ZOT_MINERU_TOKEN
ZOT_MINERU_MODEL_VERSION
ZOT_PROFILE
ZOT_FORMAT
```

## 常用命令

```bash
# 搜索、读取和引用
uv run zot search "attention mechanism"
uv run zot --json read ITEMKEY
uv run zot cite ITEMKEY --style apa
uv run zot export ITEMKEY --format bibtex

# 浏览与检索辅助
uv run zot list --collection "00 | 收件箱"
uv run zot recent
uv run zot relate ITEMKEY
uv run zot duplicates
uv run zot summarize ITEMKEY
uv run zot stats

# 回收站
uv run zot trash list
uv run zot trash restore ITEMKEY

# 查看完整命令契约
uv run zot schema
uv run zot schema search
```

程序化调用统一使用 `--json`。命令按安全等级分组：read / write / destructive，分组结果同时反映在 `zot --help` 和 `zot schema` 中。

## PDF 阅读

一个 Zotero 父条目可能包含正文、Supporting Information 和其他 PDF。先列出附件，再按 attachment key 选择，避免同名文件被错误解析：

```bash
# 只列出附件，不调用 MinerU
uv run zot --json pdf ITEMKEY --list-attachments

# 解析指定附件
uv run zot --json pdf ITEMKEY --attachment ATTACHMENTKEY

# 获取结构化 JSON
uv run zot --json pdf ITEMKEY \
  --attachment ATTACHMENTKEY --structured

# 查看目录或读取指定章节
uv run zot --json pdf ITEMKEY --attachment ATTACHMENTKEY --outline
uv run zot --json pdf ITEMKEY --attachment ATTACHMENTKEY --section 2
```

当一个父条目包含多个 PDF 时，直接运行 `zot pdf ITEMKEY` 会返回校验错误，要求明确指定 `--attachment`。

MinerU 结果按 PDF 内容哈希和模型版本缓存到 `.zot/state/mineru/`。正文 Markdown 可供 `ai_analyze` 使用，`content_list.json` 可供 workspace RAG 使用；图片位于缓存包的 `raw/images/`。

## AI 条目笔记（ai_analyze）

```bash
# 预览分类结果和将发给模型的输入，不写 note
uv run zot ai_analyze ITEMKEY --dry-run

# 生成 note，打 ai/noted tag，并写关键词简记
uv run zot ai_analyze ITEMKEY

# 只生成 note，不打 tag / 不生成简记
uv run zot ai_analyze ITEMKEY --no-tag
uv run zot ai_analyze ITEMKEY --no-short-note

# 根据已有 note 回填关键词简记（不重新调模型生成 note）
uv run zot ai_analyze ITEMKEY --short-note-only

# 已处理过的条目重跑
uv run zot ai_analyze ITEMKEY --force
```

行为要点：

- note 按条目类型选择模板（研究论文 / 综述 / 书籍），模板位于 `tools/ainote/templates/`。
- MinerU Markdown 中的图片引用会被替换为 `【图 N】` 标记并作为多模态输入发送，默认最多 12 张，使图表证据不依赖图注文字。
- 同一文档的重复附件会被识别并去重，正文保持排在附录之前。
- 渲染前统一处理上下标与中英文间距（`MnO₂` → `MnO2`，`Zn²⁺` → `Zn2+`，`g⁻¹` → `g^-1`）。
- 关键词简记写入条目 Extra 的 `short-note` 字段，格式与约束由 `validate_short_note` 校验。

## 写入与安全操作

所有写入命令都支持 `--dry-run` 预览；会改变 Zotero 数据的命令不要在未确认范围时直接执行。

```bash
# 预览
uv run zot add --doi "10.1038/example" --dry-run
uv run zot update ITEMKEY --title "New title" --dry-run
uv run zot attach ITEMKEY --file paper.pdf --dry-run
uv run zot delete ITEMKEY --dry-run

# 幂等重试
uv run zot update ITEMKEY \
  --title "New title" --idempotency-key update-001
```

主要写入命令：`add`、`update`、`attach`、`note`、`tag`、`ai_analyze`。集合管理（`collection`）属于读取分组，不修改条目内容。

## Workspace RAG

```bash
uv run zot workspace new papers --description "Selected papers"
uv run zot workspace add papers ITEMKEY1 ITEMKEY2
uv run zot workspace index papers --no-embed
uv run zot workspace embed papers
uv run zot --json workspace query \
  "reaction mechanism" --workspace papers
```

普通 `workspace index` 会根据 PDF、条目元数据和笔记内容判断是否需要重新索引。只有在需要强制重建时使用 `workspace reindex`。

持久数据位置：`.workspace/<name>/`（工作区与 SQLite RAG 索引）、`.workspace/_qdrant/`（本地向量）、`.zot/state/mineru/`（共享 MinerU 解析包）。

## 辅助工具

`tools/` 下的脚本不是 `zot` 子命令，用于批量工作流：

- `tools/paperrag/`：`run-rag-workspace.zsh`（inventory、增量索引、embedding 回填）、`run-rag-evidence-search.zsh`（检索）、`zotero-workflow-common.zsh`（共享函数）、`workspace-inventory.py`。生成物写入 `log/`。
- `tools/paper2zotero/`：`zotero_inbox.py`（PaperCrossref 收件箱导入）、`backfill_titles.py`（全库译名回填）。
- `tools/ainote/templates/`：`ai_analyze` 的 prompt 模板。

详细用法见 `tools/paperrag/Instructions.md`。

## 开发与验证

```bash
uv sync --dev
uv run ruff check src tests
uv run python -m mypy src/zotero_cli
uv run pytest -q
uv build
```

代码位于 `src/zotero_cli/`，测试位于 `tests/`（`tools/*/test_*.py` 也纳入默认测试收集），Agent Skill 位于 `skill/zotero-cli/`。

## 退出码

| 退出码 | 含义 |
|---:|---|
| 0 | 成功 |
| 1 | 运行时错误 |
| 2 | 认证信息缺失或无效 |
| 3 | 参数或配置错误 |
| 4 | 未找到 |
| 5 | 网络或限流错误 |
| 6 | 冲突，例如检测到重复项 |

## 许可

本项目基于 [Agents365-ai/zotero-cli-cc](https://github.com/Agents365-ai/zotero-cli-cc) 扩展开发，采用 [MIT License](LICENSE)。
