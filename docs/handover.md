# mentor 交付说明（handover）

仓库：**https://github.com/henry-18-dot/mentor**（公开，默认分支 `main`）

面向"接手这份代码的人"。每条结论都对应一条可跑的命令或一个测试文件，凡是没证据的说法都不写。

- 协议版本：`PROTOCOL_VERSION = "0.1"`（`mentor/protocol.py`）
- 组件类型：**17** 种；槽位：**4** 类（primary / support / evidence / aside）
- 测试：**345** 条，`python -m unittest discover -s tests -t .` 全绿
- 界面验收：`python scripts/accept_ui.py` 全绿。配色与视口的实际组合：**index 页两种配色 × 两种视口都跑；
  interactive 轮两种配色只在 1440 跑，390 只跑第一种配色**（结论与配色无关，省一半时间）。
- 规模：核心代码 42 个文件 ≈ **12,946 行**（`mentor/*.py` + `mentor/llm/*.py` + `ui/*.js|css` + `scripts/*.py` + `tests/*.py`）

---

## 1. 它是什么

一个**本地优先**的学习工作台：把讲义 / 文档变成"能点开原文核对"的问答界面。
和 NotebookLM 的差别不在模型，而在**回答不是一个字符串，而是一份"界面描述"**：

```
材料 → 带行号的片段 → BM25 混合检索 → 版面与组件规划 → SSE 流式下发 → 界面渲染
                                     ↑ 每个结论都带 [n] → 可跳回原文那几行
```

- 后端：纯标准库 Python 3.11（`http.server` + SSE + SQLite），没有任何 Web 框架。
- 前端：零构建原生 ESM（`ui/app.js`、`ui/widgets.js`、`ui/styles.css`），不依赖 CDN。
- 模型：可选。没有模型时用确定性的 offline provider，**离线也能跑**（见第 4 节性能）。

## 2. 怎么跑

```powershell
cd D:\CS336\mentor
python -m mentor.cli doctor                       # 环境自检
python -m mentor.cli seed --notebook 我的笔记本 --paths "lectures/*.py" "docs/*.md"
python -m mentor.cli serve --port 8484            # 打开 http://127.0.0.1:8484/
```

12 个子命令：`doctor`、`notebooks`、`seed`、`add`、`ask`、`search`、`show`、`export`、
`export-notebook`、`audio`、`serve`、`forget`。

界面上的键盘用法：`/` 聚焦输入框、`Enter` 发送、`Shift+Enter` 换行、
画布上 `j`/`k` 在组件间移动光标、`Enter` 打开该组件首条引用、`Esc` 中断或关抽屉。

## 3. 验收：怎么证明它能用

| 命令 | 覆盖什么 | 期望 |
|---|---|---|
| `python -m unittest discover -s tests -t .` | **345 条**（25 个测试文件）：切块与行号、PDF 载荷往返、检索、接地、流式协议、服务端、SSE、静态安全、导出、追问、轮次历史、配置诚实性、CLI 边界、材料展开、性能护栏、**笔记本次序确定性** | `OK` |
| `python scripts/accept_ui.py` | 真浏览器（CDP）交互 + DOM 断言 + 排版/配色度量 + 样式证据 + **十类**检查（`--case index\|interactive\|pdf\|hostile\|decisions\|all`） | 结论 `✓ 主路径通过` |
| `python scripts/mutation_check.py` | **证明回归测试真的抓得住 bug**：把 `mentor/`+`tests/` 复制到临时目录，在副本里去掉 `rowid` 兜底，跑目标测试 —— 必须 FAILED 才算守卫有效；**主树只读，脚本前后自证哈希未变** | 退出码 0 |
| `python scripts/bench.py` | 索引与问答耗时 | 写入 `runs/bench.md` |
| `python scripts/smoke.py` | 最小端到端冒烟 | 退出码 0 |

> **纪律**：不要在实况树上"临时改坏再还原"来做反向验证 —— 2026-10-17 有一次这样做，
> 正好撞上另一个会话在同一棵树上取证（约 4 秒窗口），差点污染它的证据。
> 用 `scripts/mutation_check.py`（临时副本）或 `unittest.mock.patch`，都可达到同样效果而不碰主树。

`scripts/accept_ui.py` 的**十类**检查**互不替代**（改哪一层就有一类会红）：

1. **交互**（`ui/self-test.html` 用真实事件走完整路径）：选模式 → 输入 → 发送 → 等组件流 →
   点引用 → 右栏跳行 → 搜索命中词行内高亮 → 悬停引用出预览 → `j/k/Enter` 键盘导航 →
   带搜索词打开原文并定位命中行；
2. **DOM 断言**：脚本独立量一遍真实 DOM（组件数与类型、引用 chip 数、右栏行数、词级高亮数、横向溢出）；
3. **对比度审计**：关键文字/底色对的 WCAG 对比度（背景透明时逐层向上合成）——一次完整验收量 **45 组**，
   两种配色都跑，最低 8.11:1；
4. **像素主题校验**：截图画布区平均亮度（浅色 >150、深色 <90），证明配色真的生效；
5. **样式证据**：外部评审提过的条目落到计算样式上（命中行竖标、概念图连线、面板开关、代码列对齐、
   chip 权重、输入区渐变、预览卡 `pointer-events`）；
6. **排版度量**：可见文字 <12px 报错、窄屏抽屉可达性、模式 chip 点击目标 ≥32px；
7. **窄屏布局**：390 下无横向溢出、计划 chip 单行可滚动；
8. **敌意载荷**（`ui/hostile.html`，`--case hostile`，`all` 时自动跑）：79 个坏组件（注入、错类型、缺字段、
   未知类型、80k 巨串）下不许执行脚本、不许出现 `javascript:` 链接、恶意字符串必须以文本呈现，
   同时**正常链接不许被误伤**。安全相关的两个要点写在"不要动的地方"；
9. **PDF 材料端到端**（`--case pdf`，`all` 时自动跑）：选 PDF 笔记本 → 提问 → 引用必须指向 PDF →
   点引用右栏必须是**可读正文**（不许出现 `%PDF` 二进制载荷）→ 搜索必须命中。真实 assignment1 PDF
   （47 页）实测引用 15/15 指向 PDF、右栏 1877 行正文。

## 4. 性能（`docs/performance.md`，604 片段语料）

| 环节 | 数字 |
|---|---|
| 摄取 | 0.072 s |
| 冷建索引 | **31.3 ms**（峰值内存 1.8 MB） |
| 热缓存 | 0.001 ms |
| 检索 | 中位 **0.2 ms**（120 次） |
| 端到端离线问答 | 中位 **17.4 ms** |

含义：离线问答是十毫秒级；接真实模型后瓶颈在网络与生成，**优化该花在"少发请求、少塞无关上下文"**，
而不是抠检索。`tests/test_performance_guard.py` 用宽松阈值盯灾难性退化
（其中"6000 片段摄取 < 2.5s"这条真抓到过一次 O(n²)：`line_for_offset()` 每片段重建整张行号映射表，
8000 行要 12.9 秒；改成 `bisect(key=...)` 后 0.49 秒。改摄取代码时**别再把映射表搬进按片段调用的函数里**）。

## 5. 架构与"改动地图"

| 想改什么 | 改哪里 | 别忘 |
|---|---|---|
| 切块方式 / 行号 | `mentor/ingest.py` | `tests/test_ingest.py`（80 条）是对行号的回归网；**PDF 另外看 `tests/test_pdf_ingest.py`** |
| 检索排序 | `mentor/index.py`（BM25 + 短语加分，加分上限 0.8×词面分） | `tests/test_core.py` |
| 组件契约 / 新组件 | `mentor/protocol.py`（`WIDGET_TYPES`、`SLOT_BY_TYPE`）→ `mentor/surface.py` 规划 → `ui/widgets.js` 渲染 | 协议是唯一的跨端契约，三处都要动 |
| 接地与引用校验 | `mentor/ground.py`（引用必须能对上原文，代码块先屏蔽再扫） | `tests/test_integration.py` |
| 流式事件 | `mentor/tutor.py` | `tests/test_stream_contract.py`（11 条模糊用例） |
| 问答历史 / 轮次 id | `mentor/surface.py` 的 `_ask_nonce()` 与 `mentor/store.py` 的 `save_turn` | `tests/test_turn_history.py`（4 条）。**别把 nonce 去掉**：`created_at` 只到秒，同秒同问会撞成同一个 id，`INSERT OR REPLACE` 会静默吞掉上一轮 |
| 模型 provider / 失败降级 | `mentor/llm/openai_compat.py`（流式解析）与 `mentor/tutor.py` 的降级分支 | `tests/test_provider_failures.py`（11 条：SSE、忽略 stream 的完整 JSON、8 种失败路径） |
| HTTP / SSE | `mentor/server.py` | `tests/test_server.py`、`tests/test_sse_termination.py`（含"断连不泄漏线程"护栏）、`tests/test_static_safety.py` |
| CLI 参数与退出码 | `mentor/cli.py`（误用走 `SystemExit("中文说明")`，别让 sqlite/socketserver 的异常裸奔；**所有子命令都接受 `--db/--root/--provider`**） | `tests/test_cli_boundaries.py`（14 条） |
| 材料展开（目录/通配符/忽略表/上限） | `mentor/targets.py`（`DEFAULT_IGNORES` 是"别把依赖当材料"的唯一防线；`expand_report()` 会报告截断） | `tests/test_targets_expansion.py`（8 条） |
| 界面视觉 | `ui/styles.css`（按"第 N 节"分块追加，别改前面的节） | 跑 `accept_ui.py`，配色/对比度会被量出来 |
| 界面行为 | `ui/app.js`、`ui/widgets.js` | 改完在 `ui/self-test.html` 里补一条断言 |

**不要动**的地方：

- `mentor/ground.py` 的"代码屏蔽"逻辑（`mdcode.shield_code`）：去掉它之后 `y = a[0]` 会被当成引用 `[0]`，
  这个坑踩过一次，`tests/test_review_fixes.py` 盯着。
- `store.py` 的 `chunk_archive`：重新摄入同一份材料时，不存档会让旧引用悬空。
- `protocol.py` 的 `PROTOCOL_VERSION`：改了要同步 `ui/widgets.js` 的兼容分支与 `tests/test_ui_contract.py`。
- **provider 的降级标记**（`openai_compat._stream_widgets` 结尾的"上游返回为空"与
  `tutor.ask_stream` 里"跑完但零组件"那一段）：它们保证"界面显示的到底是不是模型说的"这件事不说谎。
  删掉之后，上游坏掉时界面会把离线兜底冒充成模型输出，而 `degraded` 还是 False。
- **`widgets.js` 的 `safeUrl()` 白名单**：markdown 链接与图片 src 都必须过它。
  之前 markdown 链接直通 `href`，`[x](javascript:...)` 会被渲染成可点链接（实测点一下就执行）；
  `tests/test_ui_security.py` 会拦住"又直通 href"的回归。
- **内联 JSON 的 `</script>` 转义**（`ui/build_preview.py`、`ui/build_hostile.py` 里的 `inline_json()`）：
  载荷里出现字面量 `</script>` 会提前闭合脚本块，整页被当 HTML 解析（实测注入真的执行了）。
- **PDF 载荷的 latin-1 往返**（`ingest._pdf_payload()` 与 `read_source` 里的 `decode("latin-1")`）：
  `meta["raw_text"]` 里的 PDF 载荷必须用单字节恒等映射存取。曾经写入用 `surrogateescape`、
  读回用严格 UTF-8，于是**每份真实 PDF 都被 "surrogates not allowed" 跳过**（单元测试只有 ASCII PDF，
  所以没暴露）。另外 `Source.display_text()` 决定给不给界面 `raw`——PDF/ipynb 的 raw 不是给人看的。

## 6. 已经做过、验证过的能力（每条都有证据）

| 能力 | 证据 |
|---|---|
| 引用可回溯到**真实文件行号** | `tests/test_ingest.py`（80 条）；界面右栏 763 行原文与 `[n]` 逐条对应 |
| 多轮追问上下文（"继续展开讲讲"能接上） | `tests/test_followup.py`（5 条）；真材料端到端复用同一批 4 段 |
| **真实 PDF 可摄取**（曾经 100% 被跳过） | `tests/test_pdf_ingest.py`（11 条）；6 份 CS336 作业 PDF → 568 块 / 最多 48 页 |
| 整本导出学习指南（含引用总索引） | `tests/test_export.py`（5 条）；真材料 3 份材料 / 6 轮 / 9204 字 |
| 命中位置的**词级**高亮（左栏 + 右栏） | `tests/test_search_offsets.py`（10 条）；自检 10 处 |
| 搜索命中定位（打开原文直接到命中行） | `tests/test_source_highlight.py`（6 条）；自检第 307 行 |
| 引用悬停预览 | `ui/self-test.html` 两条断言（含行号与原文） |
| 键盘浏览画布（j/k/Enter） | `ui/self-test.html` 五条断言 |
| 浅色/深色双主题 + 对比度达标 | 像素亮度 24/41/48（深）与 240/250（浅）；对比度实测 **45 组，最低 8.11:1**（要求 4.5:1） |
| 离线可用 | offline provider + 上述性能数字 |

## 7. 还没做 / 需要拍板

**P4 结构性方向（有意没动，等决定）**：

1. 正文与讲义代码的视觉优先级（外观评审认为正文权重最高，现在是"组件卡片"与正文并列）；
2. 是否默认收起 `aside` 类组件（闪卡 / 自测 / 追问）——会影响"一眼看到多少结论"；
3. 窄屏是否从三栏改成两栏（当前是抽屉式）。

**已知取舍**：

- **离线检索是纯词面的**：中文提问配英文材料必然命中不了。这时界面会**说明原因**并建议换成英文关键词
  （`tutor._script_hint()`，`tests/test_script_hint.py` 11 条），而不是只回一句"材料未覆盖"。
  真要跨语言，得接真实模型或加翻译层——那是产品决策。
- **配置文件坏了不会让程序起不来**，但会被 `config_error()` 记下来，并出现在 `/api/health`、
  `provider_info()` 与 `mentor doctor` 里（`tests/test_config_honesty.py` 9 条）。改这块时别退回"静默忽略"。
- **轮次没有保留策略**：每轮约 5.4 KB，1 万轮≈54 MB。要加清理/归档是产品决策，不是 bug。
- `ui/fixtures/surface-demo.json` 故意留了一个未知组件（`attention_flow`）演示兜底渲染，
  `tests/test_ui_contract.py` 允许最多 1 个未知组件——**别当成 bug 修掉**。
- 视觉评审的模型自述不能当证据。踩过的坑：Kimi/K3 说"某处没修好"，实测多次是它看的下采样图；
  反之 deepseek-flash 提的三条里两条与计算样式矛盾。**一律用 `accept_ui.py` 的计算样式/DOM 断言复核。**
- 评审用的 Kimi 必须 `--effort off`（关思考），否则它会把 max_tokens 全花在 reasoning 上、正文返回空。

## 8. 相关文档

- [README.md](../README.md)：总览与快速开始
- [docs/api.md](api.md)：HTTP 接口（含 SSE 事件序列）
- [docs/performance.md](performance.md)：性能基线与复现
- [docs/kimi-review.md](kimi-review.md)：历次外观评审与被驳回的建议（含证据）
- [docs/overnight-log.md](overnight-log.md)：逐轮改动与证据台账（31 轮）
- [docs/prior-art.md](prior-art.md)：先例调研（借了什么、出处、代价）
- [docs/decisions/](decisions/index.html)：审美决策页（每个决策 2~3 个方案，用真实令牌渲染）
