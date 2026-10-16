# mentor 接口契约（v0.1）

本文件是并行开发的唯一依据。改动契约要先改这里，再改代码。

- 包目录：`D:\CS336\mentor\mentor`，运行根：`D:\CS336`（`python -m mentor.cli`）。
- 只用标准库。PDF 解析允许 `pypdf` / `fitz` **可选**导入，缺失时跳过并说明原因。
- 数据类全部来自 `mentor/protocol.py`，不要另造并行结构。
- 文本工具来自 `mentor/textutil.py`（归一化、切句、分词、关键词），不要重复实现。

---

## 1. 摄取层（`mentor/ingest.py`）

```python
SUPPORTED_SUFFIXES: set[str]

def detect_kind(path: Path) -> str
def line_for_offset(source: Source, offset: int) -> int
def read_source(path: Path, *, root: Path | None = None) -> Source
def chunk_source(source: Source, *, max_chars: int = 900, min_chars: int = 60) -> list[Chunk]
def ingest_paths(paths, *, root=None, on_progress=None) -> IngestReport   # NamedTuple(sources, chunks, skipped)
```

规则：

| 后缀 | kind | 处理 |
|---|---|---|
| `.py` 且含 `text("…")` 调用 | `lecture-note` | 用 `ast` 取出 `text()` / `link(title=…)` / `image()` 的字符串字面量，按出现顺序拼成 `Source.text`；`Span.line` = 该调用在**原文件**的行号；`## / ###` 行决定 `heading`（section 路径）。`Source.meta["line_map"]` 记录 `[[offset, line], …]` |
| 其他 `.py` | `code` | 按 `def` / `class` 切块，`Span.line` = 定义行号 |
| `.md` | `markdown` | 按标题 + 段落切块，`line` = 原文件行号 |
| `.txt` | `text` | 按空行分段，`line` = 原文件行号 |
| `.pdf` | `pdf` | `pypdf` 优先，其次 `fitz`；按页切块，`Span.line` = 页码，`Source.meta["line_means"]="page"`；都不可用时记入 `skipped` |
| `.ipynb` | `notebook` | markdown + code cell 顺序拼接，`line` = cell 序号，`section` = `cell N` |
| `.html` / `.htm` | `html` | 去标签取正文，`section` = 最近的 `<h1..h3>`，`line` 尽力而为（拿不到就 0） |

- `Source.id = protocol.stable_id("src", 相对路径)`，`Source.path` 用 `/` 分隔的相对路径。
- `Source.title`：讲义取第一个 `##` 标题，否则文件名（去后缀）。
- `Chunk.id = protocol.stable_id("ck", source.id, str(order), text[:80])`。
- 切块边界优先落在标题、空行、列表项，禁止把一个句子拦腰截断。
- 单个文件超过 20 MB 直接跳过并写明原因。
- `on_progress(done:int, total:int, path:str) -> None`，可为 `None`。
- 不打印任何东西，用返回值和 `logging`（`logging.getLogger(__name__)`）。

## 2. 界面层（`mentor/ui/`）

零构建、无外部资源、无 CDN。目录解析顺序：`MENTOR_UI` → 仓库根 `ui/` → 包内 `mentor/ui/`
（`mentor.server.ui_dir()` 取第一个含 `index.html` 的；起服务时 `--host`/`--port` 固定 127.0.0.1:8477）。文件：

```
ui/index.html             # 单一入口，#app 挂载
ui/styles.css             # 设计令牌 + 三栏布局
ui/app.js                 # 状态、请求、SSE、三栏联动（ESM）
ui/widgets.js             # 组件渲染器：export function renderWidget(widget, ctx) -> HTMLElement
ui/preview.html           # 离线预览：读 fixtures/surface-demo.json 渲染，不依赖后端
ui/self-test.html         # 交互自检页（同源，注入自检脚本；由 scripts/accept_ui.py 打开）
ui/fixtures/surface-demo.json
```

静态服务支持无扩展名退化：`GET /self-test` 会命中 `self-test.html`（DevTools/探针按后缀区分
"本地文件 vs URL"，验收脚本要用不带 `.html` 的地址）。

### 2.0 组件目录共 17 种

以 `protocol.WIDGET_TYPES` 为准：`answer` `quote` `sources` `concept_map` `heatmap`
`timeline` `steps` `compare_table` `cheatsheet` `flashcards` `quiz` `code` `formula`
`image` `followups` `audio_script` `progress`。

`heatmap` 的 `data = {"rows": [主题], "columns": [文件名], "cells": [[0~1]], "caption": str}`，
用来显示"哪个主题在哪份材料里讲得多"；只有检索命中 ≥2 份材料时才出现。

### 2.0.1 协议版本、槽位与流的生命周期（Kimi 评审 #2）

- **协议版本**：`protocol.PROTOCOL_VERSION`（当前 `"0.1"`）出现在 `plan` 事件、`Surface.protocol`
  与 `GET /api/health` 里。界面收到不认识的版本要提示并按已知版本渲染，未知组件走兜底渲染
  （`widgets.js` 的 `unknown`），**不允许崩布局或静默丢数据**。
- **槽位**：每个 `widget` 带 `slot` ∈ `primary | support | evidence | aside`（由后端
  `SLOT_BY_TYPE` 决定，界面不猜）。界面只按槽位调整轻重，例如 `aside` 更扁平、`evidence` 不加投影。
- **幂等/重放**：`widget.id` 稳定，同一 id 重复到达时界面**就地替换**而不是追加；`surface` 是权威状态，
  到达后按 `index` 校正。客户端如果断线重连，可以安全地重放整轮 `ask`（服务端不保证去重，界面保证幂等）。
- **取消**：客户端关闭连接即可（服务端检测到 `BrokenPipe` 停止推送）；界面侧用 `AbortController`
  中断后保留已渲染内容，只补一条"已中断"提示。
- **动效约定**：组件入场 200ms `cubic-bezier(0.2,0,0,1)` + 8px 位移；骨架→实体 ≤120ms；
  禁止无限循环动画；`prefers-reduced-motion: reduce` 时全部关闭。参考 `docs/kimi-review.md`。

### 2.1 服务端接口

```
GET  /                      → index.html
GET  /ui/<file>             → 静态文件（只允许 ui/ 目录内）
GET  /api/health            → {"ok":true,"version":"0.1.0","provider":"offline"}
GET  /api/notebooks         → {"notebooks":[{"id","title","source_count","turn_count","created_at"}]}
POST /api/notebooks         {"title":str,"paths":[str]}    → {"notebook":{…},"sources":[Source…],"skipped":[…]}
GET  /api/notebooks/<id>    → {"notebook":{…},"sources":[Source(不含 text)…],"stats":{…}}
GET  /api/sources/<sid>     → {"source":Source(含 text、raw、完整 meta),"chunks":[Chunk…]}
GET  /api/sources/<sid>?highlight=<term>
                            → 额外返回 "highlight":{"term","line","found"}，
                              即该词在**原始文件**里的首个命中行（找不到 line=0）。
                              界面在"搜索后点左栏来源卡"时用它直接把右栏滚到命中行。
POST /api/ask               {"notebook_id":str,"question":str,"mode":str} → SSE
GET  /api/turns/<id>        → {"turn":Turn}
GET  /api/search?notebook_id=<id>&q=<q> → {"hits":[{"chunk_id","source_id","locator","title","snippet","score","why","line","matched":[词],"matches":[{"term","start","end"}]}],"terms":[词]}
```

`mode` ∈ `ask` `brief` `explore` `study` `compare` `listen`（默认 `ask`）。

### 2.2 SSE 事件

`Content-Type: text/event-stream`，每帧 `event: <名>\ndata: <JSON>\n\n`，顺序固定：

| 事件 | data | 界面动作 |
|---|---|---|
| `plan` | `{"intent":str,"layout":str,"planned":[类型…],"provider":str,"degraded":bool}` | 立刻把版面骨架和将要出现的组件类型画出来（这是"智能 UI"的可见动作） |
| `retrieval` | `{"terms":[str],"hits":[{"chunk_id","source_id","locator","title","snippet","score","why"}]}` | 左栏来源打上"命中"标记 |
| `widget` | `{"index":int,"widget":{…到齐的组件…}}` | 按 index 追加渲染 |
| `surface` | `{"surface":Surface}` | 用完整对象校正一次 |
| `error` | `{"message":str}` | 顶部提示，保留已有内容 |
| `done` | `{}` | 收起 loading |

`Surface` / `Widget` / `Citation` 字段见 `protocol.py`。组件示例（`data` 里的字段即契约）：

```json
{"type":"concept_map","title":"概念关系","cites":[1,2],
 "data":{"nodes":[{"id":"bpe","label":"BPE","group":"tokenization","weight":3}],
         "edges":[{"from":"bpe","to":"tokenization","label":"属于"}]}}
{"type":"compare_table","data":{"columns":["维度","字节分词","BPE"],"rows":[["词表大小","256","数据驱动"]],"caption":"两种分词对比"}}
{"type":"quiz","data":{"questions":[{"q":"BPE 的合并依据是什么？","choices":["随机","最高频相邻对","字母序"],"answer_index":1,"why":"讲义原文：merge the most common pair of adjacent tokens。"}]}}
{"type":"sources","data":{"items":[{"n":1,"locator":"lecture_01.py:66","title":"Why did we make this course?","snippet":"…","source_id":"src_ab12"}]}}
```

### 2.3 界面要求

- 三栏：**资料来源**（左，可折叠）｜**智能画布**（中，组件流）｜**原文对照**（右，可折叠）。
  窄屏（<900px）时左右两栏变成抽屉，画布单列。
- 底部输入框 + 模式 chips（提问 / 梳理 / 概念 / 复习 / 对比 / 音频）。`/` 聚焦输入框，`Enter` 发送，`Shift+Enter` 换行。
- 引用：正文里的 `[1]` 渲染成可点 chip；点击 → 右栏打开该 source、把该行滚到视野中并高亮（用 `chunk_id` 或 `line`）。左栏来源项的"命中"标记同理。
- 每个组件右上角有"复制"和"收起"两个动作；组件标题左侧有类型徽标。
- 状态齐全：空（还没笔记本 / 还没有提问）、处理中（骨架 + 流式）、失败（error 事件）、部分成功（流中断仍保留已到组件）。
- 可访问：键盘可达、`aria-live` 播报流式进度、对比度 ≥ 4.5:1、**交互控件点击目标 ≥ 24px（WCAG 2.2 SC 2.5.8 的 AA 下界）**、不靠 hover 才能发现操作。
  - 实测（2026-10-17，`accept_ui.py` 量**全部**可见控件）：68~72 个控件，**最小 24×24（行末引用 `.cite-chip`）**，
    其中 **20~22 个 <32px**（同样是 cite-chip）；正文内联链接另有 5 个 <32px，属 WCAG 2.5.8 的行内例外。
  - **本文档此前的写法是错的**：曾写「点击目标 ≥32px」，而验收当时只量了模式 chip ——
    那是**用一个样本支撑了一个全称断言**（与对比度「取首个匹配」同类缺陷）。模式 chip 确实 ≥32px，但控件不止它。
  - 待定：cite-chip 是否提到 32×32（或视觉不变、只扩大热区）—— 形态问题，进决策队列。
- 视觉：阅读优先，纸面质感（浅色默认 / `prefers-color-scheme: dark` 跟随），低饱和强调色，中英混排字号与行高（正文 15–16px / 1.7），代码与原文用等宽字体。设计令牌写在 `styles.css` 顶部 `:root`。
- `preview.html` 必须能独立打开并渲染全部 17 种组件类型（数据取自 `fixtures/surface-demo.json`），用于无后端验收。
