# 外部评审记录：Kimi（桌面客户端 K2.8 Preview）

来源：本机 Kimi Work 客户端（账号 henry 2427李泽诚 / Plus），2026-10-15 20:38 一轮问答。
提问方式：把 mentor 的布局、组件协议、视觉令牌与流式流程写进一段 500 字 Brief，要求"四节各 3 点、每点一句话、不要代码"。
原始回答抄录在 `runs/kimi/kimi-guidance.txt`（截图 `runs/kimi/reply-02.png`）。

## Kimi 的四节意见

**1) 审美**
1. 组件间无呼吸感最易显廉价 → 统一间距刻度（8/16/24/40px），同组组件用细分隔线或留白分层，而不是边框堆叠。
2. 17 种组件各画各的最显乱 → 锁定设计令牌：一种圆角族、两级阴影、统一标题字阶（14/12px 层级），组件只换内容不换骨架。
3. 流式追加时新旧内容"挤"在一起 → 每个组件预留稳定最小高度骨架，落地不跳动；引用锚点用靛蓝下划线而非按钮样式，保持纸面感。

**2) 架构**
1. 缺组件 schema 版本号与 unknown 降级策略 → 第 18 种组件出现时旧客户端要能安全忽略或降级，不能崩布局。
2. 缺流的生命周期契约（cancel / resume / 断点续传）→ 断连后要明确重试、去重与幂等规则，否则 widget 会重复插入。
3. 缺组件间的布局语义层 → 槽位/分组/主次应由后端下发，前端只负责渲染决策。

**3) 验证**
1. 流协议一致性测试：模拟乱序、截断、重复 chunk 的 fuzz，断言任何情况下 DOM 与最终态一致。
2. 视觉回归 + 排版度量：程序化断言行高溢出、组件截断、窄屏抽屉可及性，而不只是截图比对。
3. 引用完整性端到端：随机抽引用跳真实行号，验证锚点存在且上下文匹配，防止幻觉行号。

**4) 动效**
1. 入场 160–240ms 的 opacity + 8px 位移，ease-out `cubic-bezier(0.2, 0, 0, 1)`；骨架→实体替换 ≤120ms，避免闪烁。
2. 布局位移交给 FLIP 或 `content-visibility` 占位，位移补间 200ms `ease-in-out`；尊重 `prefers-reduced-motion`，降级为直接渲染 + 只保留焦点指示。
3. 必须避免：无限循环的加载指示条、组件高度跳变引发的级联重排、打字机式逐字渲染、滚动位置被新内容强行顶走。

## 采纳情况

| 意见 | 处置 | 落在哪 |
|---|---|---|
| 1.1 间距刻度 | 已具备（`--sp-1..7`），核对后未改 | `ui/styles.css` `:root` |
| 1.2 令牌统一（一种圆角族、两级阴影、标题字阶） | 已具备（`--r-sm/md/lg/pill`、`--shadow-1/2`），核对后未改 | 同上 |
| 1.3 骨架最小高度 + 引用改下划线 | **已改**：`.widget--skeleton{min-height:112px}`；`.cite-chip` 去掉边框与填充，改靛蓝下划线 + hover 底色，保留 32px 命中区 | `ui/styles.css` |
| 2.1 协议版本 + unknown 降级 | **已加**：`protocol.PROTOCOL_VERSION="0.1"`，plan/surface/health 都下发；界面收到不认识的版本会提示并按 0.1 渲染，未知组件走 `unknown` 兜底 | `mentor/protocol.py`、`ui/app.js` |
| 2.2 流生命周期（去重/幂等） | **已具备并写入契约**：widget 带稳定 id，重复下发按 id/序号就地替换；`docs/api.md` 补了重放与重试规则 | `ui/app.js`、`docs/api.md` |
| 2.3 后端下发槽位/主次 | **已加**：`SLOT_BY_TYPE` 把 17 种组件映射到 `primary/support/evidence/aside`，`Widget.slot` 随组件下发，界面按 slot 调轻重（不猜布局） | `mentor/protocol.py`、`ui/styles.css` |
| 3.1 流协议 fuzz | **已加**：`tests/test_stream_contract.py` 11 条（provider 中途抛错、未知类型、畸形组件、重复类型、越界引用、超长列表、空流），断言事件流不变式 + 最终 surface 永远合法。**顺带查出真 bug**：安全网补的 `sources` 组件自带空列表，反而违约，已修 | `tests/test_stream_contract.py`、`mentor/surface.py` |
| 3.1(DOM 一致) | **已加**：交互自检里把画布 DOM 的组件类型序列与后端 `/api/turns/<id>` 的最终 surface 逐项对比 | `ui/self-test.html` |
| 3.2 排版度量 | **已加**：验收脚本程序化检查可见文字 <12px、无省略号兜底的文字裁切、窄屏抽屉可开可关 | `scripts/accept_ui.py` |
| 3.3 引用完整性 | **已加**：`tests/test_integration.py::CitationIntegrityTest`，6 个问题随机抽 12 条引用，打开真实讲义核对"该行是内容调用且上下文包含引用片段" | 同上 |
| 4.1 入场动效参数 | **已改**：`widget-in` 200ms `cubic-bezier(0.2,0,0,1)` + 8px 位移；状态点改单次淡入（320ms） | `ui/styles.css` |
| 4.2 reduce-motion | **已改**：`prefers-reduced-motion: reduce` 下关闭组件入场与全部过渡，只留焦点指示 | 同上 |
| 4.3 避免无限动效/级联重排/打字机/滚动被顶走 | **已改**：删掉骨架 shimmer 与状态点 pulse 两个 `infinite` 动画；骨架预留高度；正文不做逐字打字；自动滚动只在用户本已贴底时跟随（原有实现，保留） | 同上、`ui/app.js` |

## 未采纳

- 无。Kimi 的四节意见全部落在可执行范围内；其中 1.1/1.2 属于"已满足，核对后不动"，
  其余 10 条都改进了代码或测试。

---

# 第二节：Claude Code（Kimi for coding）看图评审

## 怎么接上的

本机已装 **Claude Code v2.1.148**（`~/.local/claude-2.1.148/claude.exe`），它的
`~/.claude/settings.json` 把 `ANTHROPIC_BASE_URL` 指向 **本地网关 `http://127.0.0.1:4000`**、
模型固定为 `kimi-for-coding`（Kimi 编码订阅，1M 上下文）。所以"让 Kimi 直接看 UI"不需要
驱动桌面客户端，直接无头调用即可：

```powershell
Get-Content prompt.txt -Raw -Encoding utf8 |
  & "$env:USERPROFILE\.local\claude-2.1.148\claude.exe" -p --output-format text
```

提示里写"先用 Read 工具读这几张图"，它就会真的把 PNG 当视觉输入读进去（`~/.claude/settings.json`
里还挂了 `ocr_hook.py`）。这条路径**不花 DeepSeek 额度、也不花 moonshot API 余额**，且不抢焦点。

对比之前驱动 Kimi 桌面客户端的做法：桌面版要 `AttachThreadInput` + 真实键鼠 + 剪贴板，
还会抢焦点，且拿不到仓库源码上下文；CLI 版可脚本化、可直接 Read 仓库文件，明显更合适。
（`kimiim-cli.exe` 只是 Kimi 群聊工具，不是 agent；本机还有 Codex CLI 配了 Playwright/Chrome MCP，
若要"agent 自己驱动浏览器看 live 页面"可用它，但本项目已有 CDP 验收脚本，用不上。）

## CC 的 12 条看图评审（原文 `runs/kimi/cc-review.md`）

它把 1440 那张切成 6 块逐区核对后给出：① widget 标题与下拉按钮重复同一个词；
② 三种绿色语义打架；③ "计划渲染"chip 实心/描边缺图例；④ 空状态"跳到行"预填 `66`；
⑤ 概念图节点虚线/实线混用（动画终点状态错）；⑥ 边标签约 10px 不可读、列标题未对齐；
⑦ 左栏命中片段一坨灰字、无引用竖线、无行数截断；⑧ 390 下红色"停止"压住输入框；
⑨ 行尾引用角标可点性弱、底部引用行约 12px；⑩ 空状态贴顶、下方约 500px 空白；
⑪ 顶栏面板开关无 active 底色/tooltip；⑫ 390 底部快捷键提示两行拥挤。
总评：*"最像 VS Code + AI 插件的暗色版；三栏全是 chip 和徽标，真正要读的正文和讲义代码视觉优先级最低。"*

## 执行过程中的真实情况

- **CC 只改完第 ② 条就被 5 小时额度挡住**：`403 You've reached your 5-hour usage limit`。
  它留下的那处改动（命中数徽标从中性绿改中性灰）是干净的，予以保留；三个 UI 文件已与备份做过哈希对比，
  确认没有半成品。moonshot API 余额 ¥0，没有 API 兜底。
- 于是 ③–⑫ **由主会话在 DeepSeek 低谷窗口（22:00 后，flash 半价）自己落地**，改动量
  `styles.css +2701B / app.js +1686B / widgets.js +437B`，全部限制在三个 UI 文件内。

## 逐条采纳与验证

| # | 处置 | 验证方式 |
|---|---|---|
| ① | `widgets.js:renderWidget` 标题等于类型名时不再渲染同词徽标 | 截图：卡片头只剩"回答" + chevron |
| ② | CC 原文保留：命中徽标改 `--surface-3/--ink-2` | 截图：左栏命中数变中性灰 |
| ③ | `app.js:handlePlan/handleWidget` chip 带 `data-state=planned/arrived` + title，plan-bar 加图例"实心=已渲染 · 描边=待渲染" | 截图（390/1440 均可见图例） |
| ④ | `app.js` `#line-input` placeholder `66` → `行号` | **自动化断言**：未打开原文时 `value === ""`，否则验收失败 |
| ⑤ | `widgets.js:renderConceptMap` 节点加 `data-state="ready"`，描边宽度下限 1.6；CSS 显式 `stroke-dasharray:none` | **自动化断言**：`[data-state="ready"]` 节点 `borderStyle` 必须是 solid/none；概念图状态集合只能是 `ready` |
| ⑥ | 边标签与分组标题按项目约束做到 12.5px 并加深（CC 建议 ≥11px 与项目"可见文字 ≥12px"冲突，取更严者），边标签 pill 宽度同步放大 | **自动化断言**：可见文字 <12px 即失败（第一次跑就抓到 11.5px，已修） |
| ⑦ | `.src-item__hit` 改靛蓝引用竖线 + 2 行 `-webkit-line-clamp` + 行高 1.5 | 截图：setup-notes 片段两行截断、带竖线 |
| ⑧ | 按钮文案包进 `.btn__label`，≤620px 只留图标（带 aria-label）；`composer__row` 不换行、`composer__field` `min-width:0`；窄屏"停止"降为中性强调 | 截图（390）：停止按钮不再压住输入框 |
| ⑨ | `.cite-chip` 加 3px 前置间距、hover 下划线；`.widget__cites` 提到 `--fs-sm` | 截图：角标靛蓝、底部引用行变大 |
| ⑩ | `.canvas__inner` 改 flex 列 + `min-height:100%`，空状态 `margin-block:auto` | **自动化断言**：空状态卡片中心与画布可视区中心偏差 <22% |
| ⑪ | 新增 `syncPanelToggles()`：按宽屏折叠/窄屏抽屉两种布局同步 `aria-pressed` + `.is-active` + tooltip，并在 resize 时刷新 | **自动化断言**：每个 `[data-toggle]` 必须有 aria-pressed 与 aria-label/title |
| ⑫ | `.composer__hint` ≤760px 只留前两条提示、字号 ≥12.5px | 截图（390）：底部提示压成一行 |

验证结果：`python -m unittest discover -s tests -t .` **211 tests OK**；
`python scripts/accept_ui.py` **全绿**（1440/390 两视口、交互自检 15 条、排版度量、抽屉可达性、
空状态居中、DOM 与最终 surface 一致）。改前/改后截图分别在 `runs/ui/before-cc/` 与 `runs/ui/after-cc/`，
UI 文件备份在 `runs/ui/backup-ui/`。

## 还没做的

- CC 的额度恢复后，值得让它**再看一遍改后的截图**做一次收尾确认（这是独立复核，不是自证）；
  恢复时间由 Kimi 的 5 小时窗口决定，等用户方便时再跑。
- CC 总评里"正文与讲义代码的视觉优先级最低"是结构性的，只靠 CSS 只能缓解；真要解决需要调整版面权重
  （例如默认收起 aside 类组件、正文列宽再放大），留待下一轮与用户确认方向。

---

# 第三节：视觉评审该用哪个模型（2026-10-15 实测）

用户要求："以后用其他审美好但便宜的模型"。实测本机可用路由后，结论如下。

## 本机可用的模型全貌

DSH 自带网关 `~/.dsh/gateway/kimi-gateway.mjs`（`http://127.0.0.1:4000`，OpenAI 兼容）
把两条上游统一成一个入口，**13 个模型名**：

| 模型名 | 实际接到哪 | 计费 | 能吃图片 |
|---|---|---|---|
| `k3` / `k3-256k` / `k3-1m` | api.kimi.com/coding | Kimi Code **会员额度**（不额外计费） | ✅ |
| `kimi-for-coding` / `kimi-for-coding-highspeed` | 同上 | 同上（highspeed 需 Pro） | ✅ |
| `deepseek-flash` / `deepseek-local` | api.deepseek.com | **按量 ¥1/¥4**（低谷半价） | ✅ |
| `deepseek-pro` / `deepseek-v4-pro` | deepseek-reasoner | 按量，贵一档 | ✅（但见下） |
| `claude-sonnet-4-5` / `claude-opus-4-1` / `claude-haiku-4-5` / `claude-3-5-haiku` | **也是 Kimi** | 会员额度 | ✅ |

> ⚠️ **坑**：那四个 `claude-*` 名字在网关里只是"回落到 `KIMI_DEFAULT_MODEL`"的别名，
> **不是真的 Anthropic 模型**。想"用 Claude 的审美"在这台机器上是做不到的。

Kimi 会员额度受 **5 小时窗口**约束（`GET :4000/` 看 `window5hUsedPercent`）：
本次实测时 `100/100` 已用满，`window5hResetTime = 2026-10-15T15:05:17Z`（= 23:05 +08:00），
而**月度只用了 4.85%**——所以瓶颈是短窗口，不是总量。

## 实测结果（同一张 1440×1100 界面截图，同一个问题）

| 模型 | 结果 | 耗时 | 花费 |
|---|---|---|---|
| `deepseek-flash` | HTTP 200，**准确指出**右栏代码高亮与行内 `[12]` 引用缺视觉关联 | 2.0s | ≈¥0.0006 |
| `deepseek-flash`（两张图，要 8 条清单） | 给出 8 条具体问题，含行号列对比度、引用热区、4 个胶囊标签杂糅等 | 5.8s | ≈¥0.0034 |
| `deepseek-v4-pro` | HTTP 200 但**正文为空**：1400 token 全进了 `reasoning_tokens` | 11.2s | ≈¥0.02 |
| `kimi-for-coding` | HTTP 403 `5-hour usage limit`（额度已满） | 0.8s | — |

## 定下来的默认

**日常视觉评审用 `deepseek-flash`**（便宜、能看图、秒回），已封装成可复用脚本：

```powershell
cd D:\CS336\mentor
python scripts/visual_review.py runs/ui/selftest-1440.png runs/ui/index-1440.png `
    --out runs/kimi/vision-review-after-fixes.md
# 换模型： --model k3-256k（Kimi 额度，审美更好，受 5 小时窗口限制）
```

### 补充（2026-10-15 深夜实测）：评审 Kimi 必须关思考

`kimi-for-coding` 与 K3 一样是推理型：即使显式传 `reasoning_effort: low`，它也会把 max_tokens
全花在 reasoning 上、**正文返回空字符串**（实测 900/1400 token 两轮都是空的，耗时 21~42 秒）。
网关支持 `thinking:false` 路由到"无思考版"，改用之后 13.9 秒返回完整评审。
`scripts/visual_review.py` 因此把 `--effort` 默认设为 **`off`**：

```powershell
python scripts/visual_review.py shot.png --model kimi-for-coding --effort off --max-tokens 700
```

这也符合用户给的原则：**Kimi 的推理不值得用在这里，只借它的审美**。

- **reasoner 类**（`deepseek-v4-pro`）不要用于"一次成型"的评审：它的输出先花在思考上，
  必须把 `--max-tokens` 提到 4000+ 才拿得到正文。
- **要更高的审美判断**时用 `k3-256k`（会员额度内不加钱），额度窗口满了就退回 `deepseek-flash`。
- 自查脚本 `scripts/probe_gateway_vision.py` 可以随时复测"哪个模型还能看图 / 额度还剩多少"。

## deepseek-flash 对"改后界面"的 8 条评审（`runs/kimi/vision-review-after-fixes.md`）

其中可直接验收的：右侧代码行号对比度偏低；行末引用角标点击热区偏小；顶栏 4 个胶囊标签杂糅
（概念展开/offline/意图 explore/6 个组件）；引导句与列表之间留白不足；"跳转"按钮权重过低；
概念图节点间距过大、连线偏细；底部快捷键提示对比度低。
需要打折扣的：它写"截图 1 到截图 2 的按钮切换没有过渡动画"——静态截图看不出过渡，
这条属于**推测**，不能作为依据（凡模型自述都要先验证）。
