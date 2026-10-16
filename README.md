# mentor

把一堆材料变成**可以追问、可以引用、可以复习**的笔记本。对标 NotebookLM，本机运行，纯标准库。

```
材料 → 片段（带行号）→ 混合检索 → 组件规划 → 逐个组件流式抵达界面 → 引用可跳回原文
```

## 和 NotebookLM 的三处不同

| | NotebookLM | mentor |
|---|---|---|
| 引用粒度 | 页码 / 段落 | **行号**：`lecture_01.py:699`，点一下右栏跳到原文那一行 |
| 回答形态 | 一段文字 | **智能界面**：模型从封闭的 17 种组件里现场编排（概念图、闪卡、自测、时间线、主题×材料矩阵…），边生成边出现 |
| 运行位置 | 云端 | **本机**，零依赖，材料不出机器；没配模型也能跑（离线抽取模式） |

界面不是"让模型直接吐 HTML"——那种做法既不可校验也没法导出。这里模型只能在**组件协议**里选类型、填字段、挂引用编号，剩下的交给渲染层。

## 快速开始

```powershell
cd D:\CS336\mentor

python -m mentor.cli doctor                       # 环境自检
python -m mentor.cli seed "lectures/lecture_01.py" "lectures/lecture_06.py" "docs/*.md" `
    --root D:\CS336 --title "CS336 学习笔记本"     # 建笔记本并摄取材料
python -m mentor.cli ask "BPE 分词器是怎么训练的？" --mode explore    # 命令行提问
python -m mentor.cli serve --root D:\CS336 --open                    # 打开界面（默认 127.0.0.1:8477）
```

不想起后端也能看界面：直接打开 `ui/preview.html`（读 `ui/fixtures/` 的示例数据，渲染全部 17 种组件）。

## 界面怎么用

三栏：**资料来源**（左）｜**智能画布**（中）｜**原文对照**（右）。

- 提一个问题，或点模式：**提问 / 梳理 / 概念 / 复习 / 对比**。
- 回答不是一次性出现：先到 `plan`（版面 + 将要出现的组件清单，画布上先立骨架），再到 `retrieval`（左栏标出哪几段被命中、为什么命中），然后组件一个个出现。
- 正文里的 `[n]` 是可点的；点了右栏打开那份材料、滚到对应行并高亮。左栏的来源项同理。
- 每个组件右上角可以复制、收起。窄屏时左右两栏变成抽屉。
- 键盘：`/` 聚焦输入框，`Enter` 发送，`Shift+Enter` 换行；画布上 `j`/`k` 在组件之间移动光标，
  `Enter` 打开该组件的首条引用（右栏跳行）；引用 chip 悬停会弹出"指向哪一行、原文说什么"的预览。

## "智能 UI"具体指什么

一次问答会经过四道决定，全部可观察：

1. **意图 → 版面**。问"对比 A 和 B"给 `compare` 版面，"给我出几道题"给 `study` 版面；版面决定画布重心。材料不支持时降级（比如材料里没有年份就不放时间线）。
2. **组件清单**。`mentor/surface.py` 的 `PLAN_TEMPLATES` 决定候选组件；`plan` 事件把清单先发给界面，所以用户看到的是"界面正在被编排"，而不是转圈。
3. **生成**。离线 provider 用确定性规则从原文抽句组装（每句都带引用，不编造）；接了模型则由模型填组件，但只能填计划内的类型。
4. **校验**（生成式界面的安全网，`mentor/ground.py` + `protocol.surface_problems`）：
   - 类型不在目录里、字段缺失 → 整个组件丢掉并在 `notes` 里说明；
   - 引用编号越界 → 删掉；正文一条引用都没有 → 补上最相关的一条；
   - 没有任何引用支撑的句子 → 记进 `notes`，界面上显式标注"材料未覆盖"；
   - 超长列表统一裁剪（概念图 ≤14 节点、自测 ≤5 题…）。

组件目录（17 种，`protocol.WIDGET_TYPES`）：`answer` `quote` `sources` `concept_map` `heatmap` `timeline` `steps` `compare_table` `cheatsheet` `flashcards` `quiz` `code` `formula` `image` `followups` `audio_script` `progress`。

## 架构

```
mentor/
  protocol.py    数据契约：Source/Span/Chunk/Citation/Widget/Surface + 组件目录 + 校验
  textutil.py    归一化、切句（带偏移与行号）、中英混排分词、关键词
  ingest.py      摄取：.py 讲义（ast 取 text() 并回溯行号）/ .md / .txt / .pdf / .ipynb / .html / 代码
  index.py       混合检索：BM25 + 标题加权 + 短语/邻近加成 + 中英权重 + 来源分散
  store.py       SQLite：笔记本、材料、片段、问答存档；索引按需构建并缓存
  ground.py      接地：引用编号、越界清理、未支撑句检测、覆盖率
  surface.py     意图 → 版面 → 组件清单 → 校验/裁剪/补全
  tutor.py       编排：plan → retrieval → widget* → surface → done
  llm/           offline（确定性抽取）/ openai_compat（任何 OpenAI 兼容端点，支持流式增量解析）
  render.py      导出 markdown（卡片 → 文档，引用与出处一起带走）
  audio.py       音频概览：先出脚本，配了 TTS 再合成 mp3
  server.py      http.server + SSE，零框架
  cli.py         命令行
  ui/            前端：原生 ESM，无构建、无 CDN；preview.html 可离线验收
```

材料只存一份（按路径去重），同一份讲义可以被多本笔记本引用。

## 命令行

| 命令 | 用途 |
|---|---|
| `doctor` | 环境自检：Python 版本、PDF 库、摄取层、界面文件、生成后端 |
| `seed [路径…] --title` | 新建笔记本并摄取材料（支持文件 / 目录 / 通配符） |
| `add 路径… --notebook` | 追加材料 |
| `ask "问题" --mode explore --out x.md` | 提问；`--no-stream` 只要结果，`--out` 导出 markdown |
| `search "关键词"` | 只看检索结果与命中理由，不生成 |
| `show <source_id>` | 看某份材料的片段与行号 |
| `notebooks` / `export <surface_id>` / `forget --yes` | 列出 / 导出单次问答 / 清库 |
| `export-notebook --limit 20 --out study-guide.md` | 把整本笔记本导成**学习指南**：材料清单 + 问题目录 + 逐轮正文（带出处）+ 引用总索引（哪些原文被反复引用） |
| `audio "主题" --mp3` | 生成音频概览脚本（配了 TTS 才合成 mp3） |
| `serve --host --port --open` | 起服务与界面 |

## 接模型

默认 `auto`：配好 key 就用真实模型，否则离线抽取。两种配置方式（环境变量优先）：

```powershell
$env:MENTOR_LLM_BASE="https://api.deepseek.com/v1"
$env:MENTOR_LLM_KEY="sk-..."
$env:MENTOR_LLM_MODEL="deepseek-chat"
```

或复制 `config.example.json` 为 `config.json` 填同样的键。任何 OpenAI 兼容端点（DeepSeek、通义、vLLM、Ollama 的兼容层…）都可以。

- 模型返回不合法内容、超时、网络失败 → 自动回退离线结果，`surface.degraded=true`，界面顶部说明原因，**不会白屏也不会假装成功**。
- 流式：`MENTOR_LLM_STREAM=1` 时用 SSE 增量解析，组件一到就渲染（`MENTOR_LLM_STREAM=0` 关闭）。
- 音频 mp3 需要 `MENTOR_TTS_BASE` / `MENTOR_TTS_KEY`；没配只导出脚本文本。

## 性能

本地优先的工具要说清"本地多快"：3 份材料 / **604 个片段** 的语料上，冷建索引 **31 ms**（峰值内存 1.8 MB），
检索中位 **0.2 ms**（120 次采样），端到端离线问答（检索 + 编排组件 + 接地校验 + 存档）中位 **17 ms**。
数字与解读见 [docs/performance.md](docs/performance.md)，随时复现：

```powershell
python scripts/bench.py --repeat 30 --out runs/bench.md
```

`tests/test_performance_guard.py` 用宽松阈值盯灾难性退化（检索写成 O(n²) 之类）。

## 测试与验收

```powershell
cd D:\CS336\mentor
python -m unittest discover -s tests -t .       # 190 个单元与集成测试
python scripts/accept_ui.py                     # 起真实服务 + 无头浏览器走完整主路径
python scripts/smoke.py --root D:\CS336         # 真实材料端到端冒烟（命令行）
python -m mentor.cli doctor                     # 环境自检
```

测试分层：`test_core.py`（文本/检索/接地/组件规划/契约）、`test_pipeline.py`（Store/Tutor/导出）、
`test_server.py`（真起 HTTP 服务走接口与 SSE）、`test_stream_contract.py`（**流协议 fuzz**：
provider 抛错/未知类型/畸形组件/重复/超长，断言事件流不变式与最终 surface 永远合法）、
`test_ingest.py`（各格式解析与行号）、`test_integration.py`（真实讲义：引用落回行 +
**随机抽样核对"那一行确实讲这句内容"**）、`test_ui_contract.py`（界面与 17 种组件、6 个模式、
接口路径、槽位、协议版本的静态一致性）、`test_review_fixes.py`（两轮外部审查发现问题的回归）。

`scripts/accept_ui.py` 用 DevTools 协议（不是虚拟时间快照，否则 SSE 流会被截断）做**九类互不替代**的检查
（`--case index|interactive|pdf|hostile`，默认 `all` 全跑）：

1. **交互**：`ui/self-test.html` 用真实事件走"选模式 → 输入 → 发送 → 等组件流 → 点引用 → 右栏跳行 →
   搜索命中词高亮 → 悬停引用预览 → `j/k/Enter` 键盘导航 → 带搜索词打开原文并定位命中行"，
   并把结果写回页面 DOM；
2. **DOM 断言**：脚本再独立量一遍真实 DOM（组件数、引用 chip、右栏行数与高亮行、词级高亮、横向溢出）；
3. **排版与配色度量**：可见文字 <12px、无省略号兜底的裁切、窄屏抽屉可达性、WCAG 对比度、
   以及**像素亮度**证明深浅两种配色真的生效（`--themes dark,light`，默认两种都跑）；
4. **样式证据**：把外部评审提过的条目落到计算样式上（命中行竖标、概念图连线、面板开关、代码列对齐、
   模式 chip 权重、输入区渐变、预览卡 `pointer-events`）；
5. **代码列对齐**：行号列宽与正文列起点必须各自唯一；
6. **顶/底栏权重**：模式 chip 点击目标 ≥32px、选中态不许是实心填充、键帽不许有边框；
7. **输入区过渡**：上边框必须为 0、必须有渐变淡出、渐变层不许吃点击；
8. **敌意载荷**（`ui/hostile.html`）：79 个坏组件（注入、错类型、缺字段、未知类型、80k 巨串）下
   不许执行脚本、不许出现 `javascript:` 链接，同时正常链接不许被误伤；
9. **PDF 材料端到端**：提问 → 引用必须指向 PDF → 右栏必须是**可读正文**（不许出现 `%PDF`）→ 搜索命中。

验收用的是**临时空笔记本**（每次现建、跑完即弃；PDF 用单独一本），所以首页空状态与"引用可跳原文"
两条都能稳定复现。整脚本连跑两次结论一致（幂等）。

## 外部评审与改进来源

- **架构/检索/接地**：两轮独立对抗性审查（`deepseek-v4-pro`，只读复核）共提出 4 个必须修 + 9 个建议修，
  全部修复并各自留下回归测试（`tests/test_review_fixes.py`）。二轮复核确认 8 条修复成立，
  又查出 6 条新问题（含"引用校验误伤代码块"这处由修复引入的回归），已一并修掉。
- **审美 / 架构 / 验证 / 动效**：由本机 Kimi Work 客户端（K2.8 Preview）四节指导，逐条处置见
  [docs/kimi-review.md](docs/kimi-review.md)。采纳要点：引用锚点改靛蓝下划线、删掉无限循环动画、
  组件入场 200ms/8px + `prefers-reduced-motion` 降级、协议版本号与 unknown 降级策略、
  后端下发槽位、以及新增的流协议 fuzz 与引用完整性测试。
- **看图评审（更有效的一条）**：本机 **Claude Code** 已配置成走本地网关 `127.0.0.1:4000` →
  `kimi-for-coding`（Kimi 编码订阅，不花 DeepSeek / moonshot API 余额）。无头调用即可让它
  **直接 Read UI 截图**当视觉输入，给出带位置的"问题→改法"清单：

  ```powershell
  Get-Content prompt.txt -Raw -Encoding utf8 |
    & "$env:USERPROFILE\.local\claude-2.1.148\claude.exe" -p --output-format text
  ```

  它两轮共提出 12 条问题（12 条全部落地，逐条采纳与验证见 `docs/kimi-review.md` 第二节），
  改前/改后截图在 `runs/ui/before-cc/`、`runs/ui/after-cc/`。

- **日常视觉评审走便宜模型**（实测见 `docs/kimi-review.md` 第三节）：本机网关
  `http://127.0.0.1:4000` 把 Kimi Code 会员额度与 DeepSeek 按量两条上游统一成一个 OpenAI 兼容入口，
  共 13 个模型名。其中 **`deepseek-flash` 能吃图片、2 秒返回、单次约 ¥0.003**，已封装成：

  ```powershell
  python scripts/visual_review.py runs/ui/selftest-1440.png runs/ui/index-1440.png `
      --out runs/kimi/vision-review-after-fixes.md
  ```

  要更高审美判断时换 `--model k3-256k`（Kimi 会员额度，不加钱，受 5 小时窗口限制）；
  `python scripts/probe_gateway_vision.py` 可随时复测哪个模型还能看图、额度剩多少。
  注意：网关里 `claude-*` 四个名字**只是回落到 Kimi 的别名**，不是真的 Claude 模型。

## 已知限制

- 离线模式是**抽取式**：只摘原文句子，不改写、不做跨材料推理；要真正的综合与改写需要接模型。
- 检索是 BM25 家族，不是向量检索：同义改写（"如何压缩词表" vs "vocabulary size"）可能漏召；
  词法零命中时会退回字面扫描兜底，只保证"找得到"，不保证排序最优。
- PDF 需要 `pypdf` 或 PyMuPDF；两者都没有时 PDF 会被记入 `skipped` 并说明原因（`.py` 讲义、Markdown 不需要任何依赖）。
- 讲义切块粒度＝每次 `text()`/`link()`/`image()` 调用一块（第 1 讲 404 块）。好处是每块都能精确指到某一行，
  代价是块多、单块偏短；想换更大粒度改 `ingest._parse_lecture_note` 的边界传递即可。
- 音频只有脚本是确定可用的；mp3 依赖外部 TTS 端点。
- 服务只监听 `127.0.0.1`、无鉴权，不要直接暴露到公网。
- 未做多用户、权限、增量索引；材料更新靠重新摄取（幂等，按路径覆盖；被覆盖的旧片段进 `chunk_archive`，
  历史问答的引用仍然查得到原文）。
