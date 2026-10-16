# mentor 通宵迭代台账（2026-10-15 晚 → 16 日晨）

自律规则（每轮都适用，别每次重新想）：

1. **模型分工**：分析/实现/验证一律用便宜档——主会话 `deepseek-flash`（低谷 ¥1/¥4）；
   视觉评审用 `python scripts/visual_review.py`（默认 `auto`：Kimi 通道通就用 `kimi-for-coding`，
   不通自动降级 `deepseek-flash`）。**不要用 K3**（`k3`/`k3-256k`/`k3-1m`）——它是推理型，看图评审美是大材小用。
2. **验证**：每轮结束必须跑
   `python -m unittest discover -s tests -t .`（当前 **333** 全绿，起点 211）与
   `python scripts/accept_ui.py`（全绿，九类检查）。任一不通过就只回滚本轮改动，别整批推倒。
3. **模型自述不算证据**：Kimi/deepseek-flash 说"有/没有"的地方，都要用计算样式、DOM 断言或截图复核。
   （已踩过：k3 说 6 条没修好，实测 4 条是它看错——它拿到的是下采样图。）
4. **结构性方向不擅改**（等用户拍板）：正文与讲义代码的视觉优先级、默认收起 aside 类组件、三栏是否该减栏。
5. **重大审美决策一律先做"展示页"**（2026-10-16 用户新要求）：凡是要改变观感/交互结构的选择，
   不要只在对话里描述或直接实现 —— 生成一个**可离线打开的决策页**（同屏对比 2~3 个方案、
   标注各自的取舍与代价、写明"选它会发生什么"），让用户**在页面上选**并在那里留修改意见。
   决策落定后再进实现轮次，并把结论回写进本台账。参考形态：`ui/preview.html` 那种零依赖、双击即开的单页。
6. **借鉴已有优秀实践是一项基本工程**（同上）：多去 GitHub / 公开资料看现成的审美与 UI 配置，
   不闭门造车。已知值得先挖的：**Anthropic 公开的 PPT Skill**（`pptx`，版式与信息层级思想）、
   本机 `~/Documents/deepseek-harness/default-workspace/projects/dsh-replica`（DSH Web UI 的复刻 +
   设计令牌审计）与 `dsh-reading-stability/docs/prior-art.md`（29KB 的先例调研）。
   借用时**标注来源**，并说明借了什么思想、为什么适配 mentor。
7. 每轮往本文件追加一段：轮次 / 改了什么 / 证据 / 下一项。

## 优先级队列

### P1 产品能力（优先，比样式值钱）
- [x] **多轮追问上下文**：`Tutor.ask_stream(..., context_turns=2)`，把上一轮引用过的 chunk 作为本轮附加上下文；
      `/api/ask` 支持 `context_turns`（默认 2，0 关闭）。验收：5 条新测试 + 真材料端到端（见第 6 轮）。
- [x] **整本导出**：`mentor export-notebook --limit 20 --out study-guide.md`。验收：5 条新测试（时间正序、
  引用索引、limit 取最近 N 轮、空笔记本也有可读文件、CLI 真写文件）+ 真材料导出 9204 字（见第 7 轮）。
- [x] **命中位置**：`/api/search` 每条命中返回 `matched`（命中词）与 `matches`（词在片段内的绝对偏移），
  左栏片段与右栏命中行都做**词级高亮**（`<mark class="term-hit">`，用 `--mark` 令牌）。验收：10 条新测试 +
  交互自检新增"搜索 → 左栏高亮 → 点开右栏同一行也高亮"两步（见第 8 轮）。

### P2 质量与验证
- [x] **浅色模式验收**：`accept_ui.py --themes dark,light`，用 CDP `Emulation.setEmulatedMedia` 分别渲染并截图；
  并用**像素亮度**断言配色真的生效（浅色 >150、深色 <90），避免"浅色验收"变成空头支票。见第 9 轮。
- [x] **对比度审计**：程序化算 12 组关键文字/底色对的 WCAG 对比度（含透明背景向上合成），
  两种模式全部达标（最低 8.86:1）。见第 9 轮。
- [x] **性能基线**：`scripts/bench.py`（摄取/冷建索引/热缓存/检索/端到端问答，写 `runs/bench.md`）
  + `docs/performance.md` 记录数字与解读 + `tests/test_performance_guard.py` 宽松护栏。见第 10 轮。

### P3 审美（来自评审，逐条可验收）
- [x] 右栏代码行号对比度、引导句与列表留白、跳转按钮权重、快捷键提示对比度、概念图连线粗细（第五轮已做）
- [x] **右栏代码面板留白**：代码改放"代码纸"卡片（比栏底色亮一档 + 内边距 + 圆角）、行高 1.65→1.75、
  行间 1px、命中行从"整行糊黄"改为 4px 左侧竖标 + 淡底、行号列与正文之间加竖沟。见第 11 轮。
- [x] **顶/底栏 chip 权重**：模式按钮改"标签式"（未选中无底色、选中=靛蓝文字+2px 下划线、不做填充）、
  键位提示去掉键帽边框变纯文字、模式说明从快捷键行里拆出来独立成行、模式行与输入框间距 12→16px。
  点击目标仍 34px（≥32px）。见第 12 轮。
- [x] **输入区"悬浮感"**：去掉横贯的硬边框，改用输入区上方 24px 渐变淡出（`pointer-events:none`）；
  另按收尾评审把窄屏"计划渲染"改成单行横向滚动（间距 8px）。见第 13 轮。

- [x] **整轮交付说明**：`docs/handover.md` —— 能力清单、每条结论对应的命令/测试、架构改动地图、
  "不要动的地方"、已知取舍与待拍板项。验收：文档里 6 条易错声明逐条跑命令核实（见第 17 轮）。

- [x] **对抗性验证（第二轮）**：`ingest` 畸形文件全部被安全处理（进 `skipped` 或正常解析）；
  并发提问 + 反复重摄录 0 错误；并**抓出并修掉一个 O(n²) 摄取退化**（8000 行 12.9s → 0.49s）。
  见第 19 轮。

### P4 等用户拍板（只记录）
- 正文/讲义代码的视觉优先级最高（CC 总评）；是否默认收起 `aside` 组件；三栏是否在窄屏变两栏。

### 新队列（P4 之外，可验收、非结构性；P1–P3 已清空后启用）
- [x] **审美决策页（已完成：`docs/decisions/` 四页 + `--case decisions`，等用户选）**：把 P4 三条 + 现有观感的候选方案做成 `runs/decisions/*.html`
  决策页（每页同屏对比 2~3 方案、标取舍、可离线打开），请用户在页面上选；
  选完再改代码，并同步更新验收断言。**不要先改后问。**
- [x] **先例调研（已完成：`docs/prior-art.md`）**：读 Anthropic `pptx` skill 的版式/层级做法、`dsh-replica` 的
  设计令牌与壳体结构、`dsh-reading-stability/docs/prior-art.md`，产出一份
  `docs/prior-art.md`（每条：来源链接 + 借什么思想 + 怎么落到 mentor + 代价）。
- [x] **`dsh-reading-stability` 安装确认（隔离实例已装并验证；真机待 GUI 一步 + 重启）**（用户问过"为什么没看到"）：它已建好但**未注册进 desktop
  profile**（当前 bundles 只有 base / web-app / agent-team / schedule / session-bus）。
  按它自己 `docs/INSTALL.md`：桌面版只能走**应用内的插件页**添加（命令行会被 Electron 独占拒绝）。
  待用户决定是否今晚装（装法：左侧栏 → 插件 → 添加插件 → 粘贴
  `C:\Users\Henry_1\Documents\deepseek-harness\default-workspace\projects\dsh-reading-stability`）。
- [x] **引用悬停预览**：chip 上带 `data-cite-locator/title/quote`，`app.js` 用事件代理（mouseover/focusin）
  在 chip 旁弹出预览卡（含行号、标题、原文片段、"点击打开原文"提示），移开/滚动/Esc 收起；
  卡片 `pointer-events:none` 以免吃掉 hover。验收：交互自检两条断言（见第 14 轮）。
- [x] **键盘浏览画布**：`j`/`k` 在 `#canvas article.widget` 之间移动光标（`tabindex=-1` + `.is-cursor`
  显式焦点样式 + `scrollIntoView`），`Enter` 打开该组件首条引用；输入框/文本域里按 `j` 不触发。
  验收：交互自检 5 条（见第 15 轮）。
- [x] **搜索命中定位**：`/api/sources/<id>?highlight=<term>` 返回该词在原始文件里的首个命中行
  （`server._locate_term`，找不到老实返回 line=0）；界面记住当前搜索词，点左栏来源卡时右栏直接跳到命中行。
  验收：6 条接口测试 + 自检 2 条断言（见第 16 轮）。

## 轮次记录

### 第 32 轮 安装 `dsh-reading-stability`（用户指令「装」）
- 走它自己 `docs/INSTALL.md` 里那条**不碰 `~/.dsh`、不碰正在跑的进程**的路径：
  `tools/dsh-test-instance.ps1 install <插件绝对路径>` → `boot -Port 19390`（DSH_HOME 隔离到
  `%TEMP%\dsh-scratch-home`）。
- 安装结果（隔离 profile `web`）：`dependencies` 里出现
  `"dsh-reading-stability": "link:C:/.../projects/dsh-reading-stability"`，`dsh.profile.bundles` 里也加上了它，
  `node_modules/dsh-reading-stability` 是指向项目的符号链接。
- **真浏览器验证三项生效证据**（无头 Chrome 打开带 token 的真实 GUI）：
  `hasBoot=true`、`bodyClass="dsh-reading-stability"`、`<style data-plugin="dsh-reading-stability">` 存在、
  内联 `--dsh-rs-*` 令牌 **15 个**（与 INSTALL.md 的预期数字一致，样例含
  `--dsh-rs-delta=min(calc(var(--dsh-content-font-size,14px) - 14px), …)`）。截图
  `runs/ui/dsh-instance-plugin.png`（真实 GUI：左侧「插件/工作区/设置」）。
  （我另外探的 `script[src]` 里没有它——那是探针写错了：它进启动图是动态 import，不是 script 标签。）
- **真机（desktop profile）还没装**：`dsh plugin --profile desktop add` 会被 Electron 拒绝
  （"profile desktop is managed exclusively by the Electron application"），必须走应用内
  插件页 → 添加插件 → 粘贴 `C:\Users\Henry_1\Documents\deepseek-harness\default-workspace\projects\dsh-reading-stability`
  → 安装 → **重启应用**（客户端插件在启动时进图）。重启会结束当前会话的运行，但会话记录保留、可恢复。
- 收尾：隔离实例仍在 `http://127.0.0.1:19390/?token=…` 跑着，用户可自查；用完 `tools/dsh-test-instance.ps1 clean`
  可整个删掉（只删 `%TEMP%\dsh-scratch-home`）。

### 第 31 轮 先例调研 + 审美决策页（用户新规则的第一轮）
- 用户 2026-10-16 上午新增两条规则（已写进「自律规则」第 5、6 条）：**重大审美决策先出展示页**、
  **借鉴已有优秀实践**。本轮是这两条的第一次执行。
- **先例调研**：产出 `docs/prior-art.md`。读到的原文并借用的：
  - Anthropic 公开的 `pptx` skill（[SKILL.md](https://raw.githubusercontent.com/anthropics/skills/main/skills/pptx/SKILL.md)，实测 raw 链接 200）：
    配色要为题材而选、主次不能平均、**绝不用边缘色条与标题下划线**（点名这是「AI 生成的标志」）、
    别默认奶油米色底、字号对比别抠、间距一致且留白、首轮渲染一定有真问题要重新看图。
  - 本机 `dsh-reading-stability/docs/prior-art.md`（另一个对话 38 小时前的成果）：滚动锚定在「钉底+追加」
    场景**有害**、增量 markdown 块级记忆化、`content-visibility`、WCAG 1.4.8 的可引用阈值。
  - 本机 `dsh-replica`：壳体/令牌先复刻再减法、动效只做一次性。
- **据此落地的客观修复**（不属观感、故直接改）：画布加 `overflow-anchor: none`
  —— `app.js` 自己就在做「接近底部才自动滚动」，而 W3C/MDN 与内核实践都指出这种场景下原生锚定有害。
  实测计算样式已生效（`none`）。
- **WCAG 1.4.8 独立核算**（真浏览器读计算样式）：行长 **44.8 CJK 字**（建议 ≤40）、行距 1.70×（达标）、
  段距 **0.30× 行距**（建议 ≥1.5×）、200% 放大无横向滚动（达标）→ 前两项是观感问题，进决策页。
- **决策页**：`scripts/build_decisions.py` 生成 `docs/decisions/{index,a-typography,b-visual-language,c-structure}.html`
  （零依赖、可离线双击打开、引用 mentor **真实** `ui/styles.css` 与组件类，所以变体看到的就是实现后的样子）。
  每个决策 2~3 个方案 + 取舍表 + 「选它会发生什么（代码与验收层面）」 + 我的建议与理由。
- **验收扩容**：`accept_ui.py` 新增 **`--case decisions`**（第 10 类检查）。它不只看页面能不能打开，
  还断言**变体必须真的不同**：A 页三个正文列宽必须互不相同、B 页「现在有竖标/新方案无竖标」、
  C 页 aside 收起与现状不同、窄屏方案必须用 420px 窗口。实测：
  `a:[760,668,700]｜b: 竖标 4px inset→none、左栏竖线 2px→0px｜c: aside block→none、窗口 [1440,1440,420]`。
- **过程中修掉自己的两个 bug**（都是真浏览器复核才发现的，各留了一条回归测试）：
  1. `variant()` 拼 CSS 少一个 `{` → 后续规则变**全局选择器**，B2 的「去条纹」泄漏到所有变体
     （页面文字完全看不出来）；测试 `test_decision_pages.test_variant_css_is_scoped` 现在要求
     变体内每条规则都挂在 `.v-<code>` 下。
  2. 决策页窗口 1440 宽时，grid 项的 `min-width:auto` 把整页撑到 1606px（横向滚动）→ 改
     `grid-template-columns: minmax(0,1fr)` + `min-width: 0`；验收里断言页面零横向溢出。
- 证据：`python -m unittest discover -s tests -t .` → **341 tests OK**（333+8）；
  `python scripts/accept_ui.py` → **十类全绿**（原始输出 `runs/ui/accept-r31.txt`；
  决策页截图 `runs/ui/decision-*.png`）。
- **下一步：等用户在决策页上选 A/B/C**，选完再改代码并同步断言（按规则第 5 条，不先改后问）。

### 第 30 轮 上传 GitHub（用户指令）
- 本机状况：`git` 2.51.2 有；`gh` **没装**；`winget` 不可用；全局 git 身份是 `henry-18-dot`；
  系统级 `credential.helper=manager`（凭证管理器里有 github.com 条目）；无 `GITHUB_TOKEN`。
- 做法：
  1. **提交前先审计**（这类操作不可逆，先看清再动手）：`git init` + 暂存后核对 —— 索引里
     **76 个文件 / 2.27 MB**；`config.json`、`data/*.db`、`runs/**` 经 `git check-ignore` 确认被忽略；
     用 `git grep --cached` 扫密钥模式（`sk-`/`ghp_`/`AKIA`/`Bearer`/私钥头）**零命中**。
  2. 提交 `c90f395`（76 个文件，工作区干净）。
  3. 按用户选择：下载 gh 便携包（v2.102.0，装到 `%LOCALAPPDATA%\gh-cli`，不需管理员）→
     设备码流程登录（用户在浏览器授权一次，token 存进系统钥匙串，**没有经过对话记录**）→
     `gh repo create mentor --public --source . --push`。
- 核验（都做完了）：仓库 **PUBLIC**、默认分支 `main`、`createdAt 2026-10-16T01:31:35Z`；
  本地 HEAD 与 `origin/main` 同为 `c90f395`；远端文件数 **76**（与本地一致）；
  `config.json`/`data/`/`runs/`/`*.db`/`*.pyc` **确实都不在远端**；最大文件 `ui/hostile.html` 1.46 MB（生成物）；
  已加 topic；最后用 `web_fetch` 做**外部验证**：`https://github.com/henry-18-dot/mentor` 返回 HTTP 200，
  标题即仓库描述。
- 结果：**https://github.com/henry-18-dot/mentor**

### 第 29 轮 整晚收尾复核（低谷窗口结束前的最终确认）
- 目的：整晚改了 20+ 处（含 15 个真缺陷修复），确认交付物**仍然自洽**，不靠记忆下结论。
- 全套复核（都是当场跑的，不是回述）：
  - `python -m unittest discover -s tests -t .` → **333 tests OK**（24 个测试文件）；
  - `python scripts/smoke.py` → 退出码 0（降级问答 0/4）；
  - `python scripts/accept_ui.py` → **九类检查全绿**，原始输出 `runs/ui/accept-final.txt`：
    index 2 配色 × 2 视口（像素亮度 深 24/24、浅 250/250）、interactive ×3（右栏 763 行、引用 25 条）、
    **pdf**（15/15 引用指向 PDF、右栏 1877 行正文、无 `%PDF`、搜索命中 8 条）、
    **hostile**（79 组件、script 标签仍 5 个、危险协议 0、被拦截 6、正常链接 3、零溢出）；
  - README 快速开始**逐条实跑**（doctor / seed / ask / export-notebook 全部成功）。
- 顺手修掉**文档漂移**（都是整晚改动带来的）：`handover.md` 的"242 条"→333、README 的
  "四类互不替代的检查"→**九类**（并逐条列全）、`overnight-log.md` 自律规则区的"当前 211 全绿"→333（起点 211）。
  另外清掉探针残留（`runs/hostile-probe.db`、`runs/ui/theme-probe-*.png`），`runs/` 只剩 `ui/` 与 `kimi/`。
- 结论：**交付物自洽，无遗留红项**。剩余待办只有 P4 三条结构性方向（等用户拍板）。

## 整晚小结（22:00–08:00）

- **对抗性验证 9 轮**，修掉 **15 个真缺陷**：
  SSE 流不结束 · 摄取 O(n²)（8000 行 12.9s→0.49s）· NUL 路径 500 · 忽略 `stream` 的端点丢内容 ·
  失败不标降级（把离线兜底冒充模型输出）· markdown 链接 XSS · **PDF 全路径死**（6 份真实 PDF 100% 被跳过）·
  PDF 二进制载荷被当原文发给界面 · 坏配置静默忽略 · 同一秒内问答互相覆盖（200 次只存 26 轮）·
  CLI `--db` 指目录与非法端口裸抛 traceback · 依赖源码（`site-packages`）被当材料 ·
  上限静默截断 · **README 第一条命令跑不通**（`seed --root` 顺序）。
  另有 2 个有价值的**否定结论**（SSE 断连不泄漏线程/内存、验收脚本幂等）。
- **产品/交付改进 3 轮**：交付说明 `docs/handover.md` · 性能基线 `docs/performance.md` + `scripts/bench.py` ·
  跨文字体系提问会说明原因（不再是沉默的"材料未覆盖"）。
- **一以贯之的方法**：模型自述不算证据 —— 每条外部评审意见都用计算样式/DOM 断言/真浏览器复核后才采纳
  （期间驳回了 6 条站不住的意见，并记进 `docs/kimi-review.md`）。
- 数字：测试 **211 → 333**（24 个测试文件）；验收 **3 类 → 9 类**；台账 29 轮。

### 第 28 轮 大目录批量摄取：三个真缺陷（含"文档里的第一条命令跑不通"）
- 背景：`default_seed_targets` 的兜底是 `return out or [str(base)]` —— 把根目录整个交给摄取。
  这个仓库有 **39,539 个 cache 文件、35,968 个 .py**，于是先量了"脏目录"下的展开行为。
- 缺陷 1：**依赖源码被当材料**。忽略表里有 `node_modules/.venv/cache/__pycache__/.git`，但**没有
  `site-packages`**。脏目录实测（120 份真材料 + 各类垃圾共 1420 个文件）：`expand` 返回 **200** 份，
  多出的 80 份全是 `site-packages/lib/*.py`。修：忽略表补 `site-packages`、`__pypackages__`、`.eggs`、
  `bower_components`、`.tox`、`.nox`、`.cache`、`.sass-cache`、`.terraform`、`.gradle`、`.m2`。
  复测：**120 份，且全是真材料**（显式点名的文件仍不受忽略表影响）。
- 缺陷 2：**上限静默截断**。`expand()` 直接 `return found[:limit]`：2500 份材料的目录只返回 2000 份，
  调用方拿不到任何信号，用户以为"全都导进来了"。修：新增 `Expansion` 报告与 `expand_report()`，
  满了之后继续扫到**第一个多余文件**即确证截断（正好等于上限时不误报）；
  CLI `seed`/`add` 新增 `--limit` 并打印 `⚠ 材料数量超过上限 N…`；HTTP 接口返回
  `truncated/limit/warning`。
- 缺陷 3（最扎眼）：**README 与工具自己的提示语推荐的写法跑不通**。`--root/--db/--provider` 是全局选项，
  而 README 第 25-28 行、`cli.py` 顶部 docstring 与 `notebooks` 的引导语都写成
  `mentor seed --root D:\CS336 …` / `mentor serve --root D:\CS336 --open` —— 实测直接报
  `unrecognized arguments: --root`。用户照抄**第一条命令**就撞墙。
  修：所有子命令都接受这三个通用选项（`default=argparse.SUPPRESS`，避免子命令把全局值冲成 None）。
  两种写法都验证通过，并把 README 快速开始**逐条实跑**：doctor ✓、seed（5 份材料/658 片段）✓、
  ask（组件+引用）✓、export-notebook（2227 字指南）✓。
- 新增：`tests/test_targets_expansion.py` **8 条**（脏目录只收真材料、依赖目录被忽略、显式文件优先、
  截断要报告、正好等于上限不误报、默认上限、兼容入口）+ `tests/test_cli_boundaries.py` 加 5 条
  （选项写在子命令后可用、写在前面也可用、子命令不覆盖全局 root、CLI 会播报截断、`--limit` 可解析）。
- 证据：`python -m unittest discover -s tests -t .` → **333 tests OK**（320+13）；`accept_ui.py` 全绿。

### 第 27 轮 产品改进：跨文字体系提问要说明原因（不是只回"材料未覆盖"）
- 由来：CS336 的作业 PDF 全是英文，用中文提问时离线检索**必然**一个词都命中不了。
  以前界面只回一句"材料里没有找到与这个问题直接相关的内容"，用户只会以为"这工具搜不到东西"。
- 改：`textutil.script_of()`（`cjk`/`latin`/`mixed`/`unknown`；两种文字各自 ≥30% 才算 mixed，
  这样"中文句子夹英文术语"仍算 cjk，"英文为主零星中文"算 latin）；`tutor._script_hint()`
  在**空结果**时判断问题与材料的文字体系（材料只采样前 40 个片段），不一致就写进答复与 notes：
  「注意：材料正文主要是英文，而问题是中文。离线检索按字面匹配，换成英文关键词（例如 BPE tokenizer）更容易命中。」
- 真实材料实测（assignment1 PDF）：
  - 中文提问「作业 1 要我们实现什么？」→ 0 引用（仍然诚实）、notes 两句含跨语言提示、答复正文也写了原因；
  - 英文提问「What does assignment 1 ask us to implement?」→ **6 条引用**，且**没有**多余的跨语言提示。
- 新增：`tests/test_script_hint.py` **11 条**（`script_of` 五种情形 + 中↔英两个方向 + 同语言不该出现提示 +
  有命中时不塞提示）。
- 顺带：验收脚本**连跑两次**（全量 + `--case index`）结论一致、像素亮度一致 —— 说明它自身是幂等的，
  之前那些"全绿"的结论不会因为状态泄漏而失真。
- 证据：`python -m unittest discover -s tests -t .` → **320 tests OK**（309+11）；`accept_ui.py` 全绿。

### 第 26 轮 对抗性验证（第八轮）：CLI 边界 + SSE 断连资源回收
- **否定结论（也是收获）**：SSE 反复断连**不泄漏**。50 次强制 RST 后线程数回到基线（2）、
  tracemalloc 峰值 3.39 MB、服务仍然健康（`/api/health` 200）。已把它固化成常驻护栏
  `test_client_disconnect_does_not_leak_threads`（8 次断连 + 线程数回落 + 服务仍可用），
  防止以后把 `Connection: close` 或异常处理改回去。
- **两个真缺陷（CLI 把用户误用变成 traceback）**：
  1. `--db` 指向一个**目录** → sqlite 只回 `unable to open database file`，
     用户完全看不出问题在哪。修：`Store.__init__` 显式拒绝目录路径（`ValueError`，库层面），
     `cli.main` 在派发前给出人话并以退出码 2 结束；实测
     `错误：--db 指向的是一个目录（…），请给它数据库文件路径。`
  2. `serve --port 99999` → 一路炸到 socketserver 的 `OverflowError: bind(): port must be 0-65535`。
     修：`cmd_serve` 校验 0~65535（0 = 让系统挑端口）并给中文说明；负数同样拒绝。
- 同时确认既有边界本来就好（补了回归测试）：`forget` 必须 `--yes`、未知笔记本有中文提示、
  空库有引导语、`doctor` 对坏配置返回非零。
- 新增：`tests/test_cli_boundaries.py` **9 条** + `test_sse_termination.py` 加 1 条 = **+10**。
- 证据：`python -m unittest discover -s tests -t .` → **309 tests OK**（299+10）；
  `accept_ui.py` 全绿；CLI 探针复跑显示两处误用都是干净的单行中文提示、无 traceback。
- 备注（测试卫生，不是用户缺陷）：CLI 命令在**同进程**内会保留 SQLite 连接（真实使用时进程退出即释放），
  所以 CLI 测试的临时目录用 `ignore_cleanup_errors=True`。

### 第 25 轮 对抗性验证（第七轮）：坏配置静默忽略 + 问答历史静默丢失
- 怀疑两处，**都成立**，各配一条真修复：
  1. **坏配置静默忽略**：`load_config()` 吞掉 `JSONDecodeError`/`OSError` 直接返回 `{}`。
     `MENTOR_CONFIG` 指向写坏的 JSON 时，程序照常起来、照常回答，只是默默用离线兜底 ——
     用户会以为"模型在工作"或"我没配模型"，真相是**配置根本没读进来**。
     （同一项目里 `build_provider("openai")` 对"缺字段"是会报错的，说明作者在意这件事，
     只是坏文件这条路绕过了那道检查。）
     修：错误改为**按路径记录**（全局单值会被 `build_provider` 的第二次读取冲掉），新增 `config_error()`；
     `provider_info()` 带上 `config_error`（`/api/health` 因此可见）；显式 `build_provider("openai")`
     报错时**带上配置文件线索**；`mentor doctor` 把它列为问题。坏文件仍然不会让程序起不来。
  2. **问答历史静默丢失**：`Surface.id` = `stable_id(..., question, created_at, provider)`，而
     `created_at` 只到秒 → **同一个问题在同一秒内问两次会算出同一个 id** → `save_turn` 的
     `INSERT OR REPLACE` 把上一轮覆盖。实测**连问 200 次只留下 26 轮（13%）**。
     用户能碰到：连点两次发送、回车按两下、脚本里循环提问、两个进程共用一个库。
     修：每次提问带唯一 nonce（`pid-序号`）；复测 **200 次 → 200 轮**。
- 顺带量了存储特性（没改）：每轮约 **5.4 KB**、`turns` 有 `turns_by_notebook` 索引、
  `list_turns(limit=5)` 单次约 0.06 ms、`list_turn_payloads` 正序正确。
  **没有保留策略**（轮次会一直累积：1 万轮≈54 MB）—— 记为已知特性，改动语义的事不擅做。
- 新增：`tests/test_config_honesty.py` **9 条**（缺文件不算错、好配置清错误、坏 JSON/非对象要报错、
  `provider_info` 暴露错误、显式 openai 必须报错且带文件线索、auto 不因坏文件起不来、doctor 会报）；
  `tests/test_turn_history.py` **4 条**（同秒同问保留全部、12 次批处理线性增长、不同 mode 分开、payload 可取回）。
- 证据：`python -m unittest discover -s tests -t .` → **299 tests OK**（286+13）；
  `accept_ui.py` 全绿；`mentor doctor` 对坏配置输出
  `配置文件有问题（已忽略，当前按默认跑）：…JSONDecodeError…`。

### 第 24 轮 PDF 材料的产品级验收（新 `--case pdf`）
- 动机：上一轮修好了 PDF 的**数据层**（摄取 + 载荷往返），但"产品层真的能用吗"没有证据 ——
  浏览器里能不能选到 PDF 笔记本、能不能答、引用能不能跳页、右栏显示的是不是正文，全没测过。
- 改：`accept_ui.py` 新增 **`prepare_store` 里的 PDF 笔记本**（单独一本，避免真实 PDF 的检索构成
  扰动既有断言）与 **`check_pdf()`**；新增 `--case pdf`，`all` 时自动跑。
  检查链路：选笔记本 → 确认左栏列出 `.pdf` → 提问（英文词，匹配 PDF 正文）→ 引用里必须有指向 PDF 的定位 →
  点引用 → 右栏必须出现原文且**不含 `%PDF` 二进制标记**、必须有命中行 → 搜索必须命中 PDF。
- 实测（真实 assignment1 PDF，47 页 / 159 块）：
  `组件 4｜引用 chip 15｜其中指向 PDF 的 15｜示例定位 cs336_assignment1_basics.pdf:5`；
  `右栏：1877 行｜命中行 1｜二进制标记 False｜样例 '1CS336 Assignment 1 (basics): Building a Transformer LM2Vers…'`；
  `搜索 BPE：命中卡片含 PDF 的 8 条`。
- 证据：`python -m unittest discover -s tests -t .` → **286 tests OK**；
  `accept_ui.py` 全量（index ×2 配色 ×2 视口 + interactive ×3 + **pdf** + **hostile**）全绿，
  原始输出 `runs/ui/accept-r24.txt`，截图 `runs/ui/pdf-1440.png`。
- 队列状态：无未完成项。下一步候选：超长会话的 SQLite 增长与 `list_turns` 分页；
  `load_config` 对坏配置文件**静默忽略**（疑似诚实性问题，待验证）。

### 第 23 轮 对抗性验证（第六轮）：真实 PDF 摄取 —— 发现整个 PDF 路径是死的
- 出发点：PDF 分支只用"手搓的 ASCII 最小 PDF"测过，**真实课程 PDF** 从没走过。
- **重大发现**：CS336 的 **6 份真实 PDF 全部摄取失败**（0.2 秒内全被跳过）：
  `UnicodeEncodeError: surrogates not allowed`。根因在 `ingest.py`：
  - 写入：`raw_text = raw_bytes.decode("utf-8", "surrogateescape")`（第 867 行）→ 二进制字节变成
    `\udc80`~`\udcff` 孤立代理码元；
  - 读回：`_pdf_pages` 里 `raw.encode("utf-8")`（严格）→ 直接抛异常；
  - 而第 808 行的注释写着"单字节恒等映射"——**实现和自己的文档矛盾**，且单元测试用的是纯 ASCII PDF，
    所以这个 bug 一直没暴露。等于**整个 PDF 支持是死的**。
- 修：写入改用 **latin-1**（真正的字节↔码点恒等映射），读回走新的 `_pdf_payload()`
  （先 latin-1，再对老库里的 surrogateescape 形式兜底）。
- 修复后实测：6 份材料 **0 skipped**、共 568 块、最多 48 页（assignment1 = 47 页/12.2 万字），
  首片是真实内容 `'CS336 Assignment 1 (basics): Building a Transf…'`；
  PDF 检索也通了：问"BPE tokenizer 的训练目标"→ 6 条引用指向真实页
  （`cs336_assignment1_basics.pdf:10` · "Serialize the resulting vocabulary and merges to disk…"）。
- 连带修掉第二个真缺陷：PDF 的 `meta["raw_text"]` 是**二进制载荷**，而 `to_dict(with_text=True)`
  把它当 `raw` 发给界面，右栏又优先渲染 `raw` → 打开 PDF 会看到 **48100 行乱码**（ipynb 同理，
  它发的是 notebook JSON）。新增 `Source.display_text()`：只有"行号=真实行"的材料才给 `raw`；
  顺带把 `_locate_term`（搜索定位）也改成搜可读正文，而不是在二进制载荷里搜。
- 新增：`tests/test_pdf_ingest.py` **11 条**（256 个字节值往返无损、meta 可 JSON 序列化、
  老库 surrogateescape 形式兼容、真实 PDF 不被跳过、页码合理、载荷可重放、PDF 不发二进制给 UI、
  讲义仍带 raw、PDF 搜索定位命中正文）。
- 证据：`python -m unittest discover -s tests -t .` → **286 tests OK**（275+11）；
  `accept_ui.py` 全绿（原始输出 `runs/ui/accept-r23.txt`）。
- 已知限制（不是 bug，记录备查）：离线检索是纯词面 BM25，中文提问配英文 PDF 会老实返回
  "材料未覆盖"（不给假答案）；PDF 的 `line_means="page"`，右栏行号与页码的语义差异还没统一。

### 第 22 轮 对抗性验证（第五轮）：敌意 surface 载荷 / XSS
- 静态先查：DOM 构造走 `createElement` + `textContent`，`innerHTML` 逃生口只喂文件内图标常量
  （17 处 `html:` 全部是 `ICON.*`），markdown 渲染器明确不用 innerHTML —— 注入面在设计上就是关的。
- 造了 `ui/hostile.html`（`ui/build_hostile.py` 生成）：79 个组件，含 script 标签 / `img onerror` /
  5 种危险协议链接（`javascript:`、大小写变体、`vbscript:`、`java\tscript:` 制表符绕过、`data:text/html`）/
  错类型 / 缺字段 / 未知组件类型 / 80k 巨串 / 双向覆盖字符 / 空字符，另有 3 条正常链接做**正对照**。
- 过程中先踩了自己的坑（诚实记账）：载荷里的 `</script>` 字面量把内联 JSON 块提前闭合，导致注入真的执行了、
  JSON 被截断、渲染器压根没跑。这不是 mentor 的漏洞，而是"内联 JSON 必须转义 `</script>`"的经典坑；
  `build_preview.py` 同样有隐患 → 两个生成器都加了 `inline_json()`（`<` + `/` → `<\/`，JSON 语义不变）。
- **抓到真漏洞**：markdown 渲染器把 `[x](javascript:…)` 原样塞进 `href`（`isExternal` 只决定 `target`，
  不做安全判断），点击即执行。修：新增 `safeUrl()` 白名单（http/https/mailto/锚点/相对路径放行，
  其它协议拒绝并剥掉控制字符防 `java\tscript:` 绕过）；markdown 链接不安全时降级成带 `title` 说明的纯文本；
  图片 src 只允许 http(s)/相对/data:image。
- 修复后实测（真浏览器，`accept_ui.py --case hostile`）：
  `组件 79｜script 标签 5｜img[src=x] 0｜危险协议链接 0｜被拦截链接 6｜正常链接 3｜溢出 0`，
  `window.__PWNED__` 未被置位、无未捕获错误、恶意字符串全部以文本显示。
- 验收扩容：`accept_ui.py` 新增 **`--case hostile`**（成为第 8 类检查，`all` 时自动跑），
  含 9 条断言（含"危险链接必须被拦截 ≥3"与"正常链接不许被误伤 ≥3"两条正反约束）。
  过程中还修了它自身的两个采样错误：预览页会**先铺 skeleton 再流式回放**，必须等"骨架清零"才是终态。
- 新增：`tests/test_ui_security.py` 6 条（内联 JSON 可解析、preview 与 fixture 同步、两个生成器确实转义、
  markdown/图片都走白名单、载荷里确实带危险协议用例）。
- 证据：`python -m unittest discover -s tests -t .` → **275 tests OK**（269+6）；
  `accept_ui.py` 全量（含敌意载荷）全绿，原始输出存 `runs/ui/accept-r22.txt`。

### 第 21 轮 对抗性验证（第四轮）：模型 provider 的失败路径
- 方法：在本机起一个**假上游**，把 OpenAI 兼容接口的各种坏行为演一遍（SSE 正常 / 忽略 stream 只回完整 JSON /
  未知组件 / HTTP 500 / 非 JSON / 空 choices / 坏 SSE / 空 SSE / 超时 / 拒绝连接），
  看 tutor 是否"有限时间内返回合法 surface + 正确标记降级 + 说明原因"。
- 抓到**两个真缺陷**（都在"诚实性"上，正是这个项目的立身之本）：
  1. **内容被静默丢掉**：有的兼容端点忽略 `stream:true`、直接回完整 JSON。原流式读取只认 `data:` 帧，
     于是**模型的回答被扔掉**，用户拿到的是离线兜底结果，而 `degraded` 还是 False。
     修：没有 SSE 帧时，把整个响应体当一次完整响应解析（顺带防了 2MB 上限）。
  2. **失败不标记降级**：坏 SSE / 空 SSE / 非 JSON / 空 choices 属于"HTTP 成功但内容不可用"，
     流会安静结束，界面把离线兜底**冒充**模型输出。修：provider 层显式抛 `ProviderError`
     （"上游返回为空"），tutor 层再补一道"跑完但零组件"的检查（`not widgets and not degraded`）→ 标降级 + 写原因。
- 修后逐条实测：`valid_sse` / `valid_json` → `degraded=False` 且回答确实来自模型；
  其余 8 种 → `degraded=True`、1 秒内返回、notes 里有可读原因、`surface_problems` 全为空（不白屏）。
- 新增：`tests/test_provider_failures.py` 11 条（两种正常路径 + 8 种失败路径 + "任何失败都要渲染出组件"）。
- 证据：`python -m unittest discover -s tests -t .` → **269 tests OK**（258+11）；
  `accept_ui.py` 两种配色全绿。

### 第 20 轮 对抗性验证（第三轮）：静态文件服务与断连噪音
- 静态文件路径穿越：`/../pyproject.toml`、`/..%2f..%2f`、`/%2e%2e/`、`/..%5c..%5c`（反斜杠变体）、
  `C:/Windows/win.ini`、`/../mentor/server.py` —— **全部 403「越界路径」**，防护有效。
- 抓到真缺陷：**含 NUL 的路径返回 500**（`/%00`、`/index.html%00.png`）。
  `Path.exists()` 抛 `ValueError: embedded null character`，被兜底 except 当成内部故障。
  但这是**客户端发了非法请求**，应当是 400。修：`_static()` 先拦 NUL → 400，
  并把路径解析的 `ValueError/OSError` 也归为 400（不再冒充 500）。
- 顺带修：`serve()` 换成 `_Server`（覆盖 `handle_error`），客户端提前断开
  （关标签页 / Esc 中断 / 切页面）不再打印整段 traceback —— 自从第 18 轮把 SSE 改成
  `Connection: close`，提前断开变成了**正常事件**，不该看起来像服务坏了。
  实测：5 次强行 RST 断开后 **stderr 零字节**；真正的内部错误仍照旧打印，不静音。
- 假警报（同第 18 轮）：`/不存在的页面` 的 `UnicodeEncodeError` 仍是 `http.client` 拒绝在请求行放非 ASCII。
- 新增：`tests/test_static_safety.py` 7 条（正常资源、无扩展名入口、404、7 种穿越变体 403、
  绝对路径 403、3 种 NUL 路径 400、被打过之后服务仍健康）。
- 证据：`python -m unittest discover -s tests -t .` → **258 tests OK**（251+7）；
  `accept_ui.py` 两种配色全绿；`scripts/smoke.py` 退出码 0。

### 第 19 轮 对抗性验证（第二轮）：抓出并修掉摄取的 O(n²)
- 畸形材料（10 个用例：空 / 全空白 / 二进制 / BOM / GBK / 2MB 单行 / CRLF / 未闭合括号 / 无后缀 / 5000 行）
  → **全部安全**：该跳过的进 `skipped` 并带上可读原因，能解析的行号都正确（BOM、GBK、CRLF 均无串行）。
  不存在的路径、目录、空路径列表也都有确定行为；软链因 Windows 权限跳过（客户端无特权，非代码问题）。
- 规模曲线暴露**真缺陷**：行数翻倍（4000→8000）摄取时间涨 **8 倍**，每片段耗时从 0.12ms 涨到 1.61ms
  —— 典型的 O(n²)。profile 定位到 `ingest.line_for_offset()`：它**每次调用都重建整张 line_map**
  （3200 万次 `list.append`，占摄取 75%），而它按片段调用一次。
- 修：改用 `bisect.bisect_right(line_map, offset, key=lambda e: int(e[0]))` 直接二分（Python 3.11 支持 `key`）。
  实测 4000 行 **1616ms → 208ms**、8000 行 **12914ms → 491ms（26 倍）**，增长回到线性。
- 并发验证：4 个提问线程 + 1 个反复重摄录线程，84 次问答 **0 错误**、引用始终完整（每次 6 条、locator 齐全）、
  最终状态一致；并验证"重摄录后旧引用仍能查到归档片段"（`chunk_archive` 的设计意图）。
- 新增：`tests/test_performance_guard.py::IngestScalingTest`（6000 片段 < 2.5s；修复后 0.35s，O(n²) 时约 7s）
  与 `tests/test_concurrency.py` 4 条（旧引用可解析、历史问答仍可渲染、重摄录不产生重复来源、
  提问与摄录并发一致）。
- 证据：`python -m unittest discover -s tests -t .` → **251 tests OK**（247+4）；
  `accept_ui.py` 两种配色全绿；`docs/performance.md` 已更新基线（摄取 0.047s、冷建索引 30.2ms、
  检索 0.2ms、端到端 16.4ms）与新旧对照曲线。
- 队列状态：全部完成。**下一步可选**：继续对抗性验证第三轮（`serve` 的静态文件与路径穿越、
  SSE 中途断连的资源回收、超长会话的 SQLite 增长），或按用户拍板做 P4 结构性改动。

### 第 18 轮 对抗性验证：找出并修掉 SSE 流不结束的缺陷
- 方法：队列清空后不"发明功能"，而是拿脏输入打真实接口（26 个用例：空 id、超长问题、
  未知 mode、非法 context_turns、目录穿越、非 ASCII 路径、不存在的资源…）。
- 结论：**26 个用例里只有 1 个是真缺陷**，另 1 个是我探针自己的错：
  - ✅ **真缺陷**：`/api/ask` 的 SSE 发 `Connection: keep-alive` 又不给 Content-Length，
    浏览器用 `fetch + ReadableStream` 感觉正常，但 curl/urllib/脚本**永远读不到流结束**
    （实测 30 秒超时）。协议里明明有终止事件 `done`。**修**：SSE 改发 `Connection: close`
    并设 `close_connection = True`（前端 `readEventStream` 正好需要这个 EOF）。
    修复后同样请求 **0.7 秒**读到底。
  - ✗ 假警报：`GET /api/notebooks/不存在的id`（原始非 ASCII 进请求行）报 `UnicodeEncodeError`
    —— 那是 `http.client` 拒绝在请求行放非 ASCII，**客户端限制**；浏览器会先百分号编码。
    已补一条测试固化"百分号编码的未知 id 得到干净的 404"。
- 顺带发现：现有测试只覆盖 `stream=0`（JSON），**SSE 读到 EOF 这条路从来没被测过**——这正是缺陷能存活的原因。
- 新增：`tests/test_sse_termination.py` 4 条（读到 EOF 且在 10 秒内、最后一个是 `done`、
  `Connection: close`、每帧都是合法 JSON、百分号编码路径干净 404）。
- 证据：`python -m unittest discover -s tests -t .` → **246 tests OK**（242+4）；
  `accept_ui.py` 两种配色全绿（前端流式路径不受影响）。
- 下一项：继续对抗性验证另外两块没人测过的边界——`ingest` 遇到畸形文件、以及并发提问时的
  Store/索引缓存一致性。

### 第 17 轮 交付说明 `docs/handover.md`
- 写：`docs/handover.md`（8 节：是什么 / 怎么跑 / 怎么验收 / 性能 / 架构改动地图 / 已验证能力 /
  待拍板与已知取舍 / 相关文档）。所有数字都是**当场量出来的**，不是回忆：
  17 种组件、4 类槽位、12 个子命令、242 条测试（13 个测试文件）、42 个文件 12,946 行。
- 逐条核实了 6 条易错声明：`smoke.py` 退出码 0（输出"降级问答：0/4"）、`SLOTS` 四类名、
  离线 provider 路径 `mentor/llm/offline.py`、`ui/fixtures/surface-demo.json` 里确实只有
  `attention_flow` 一个未知组件、`docs/api.md` 有 SSE 事件序列、`test_ui_contract.py` 有未知组件兜底断言。
- 顺手改正两处不够精确的措辞：验收的真实配色×视口组合（interactive@390 只跑第一种配色）、
  对比度实测值（一次完整验收 **45 组、最低 8.11:1**，原稿写的 12 组是最早的采样数）。
- 证据：`python scripts/smoke.py` → exit 0；`python -m unittest discover -s tests -t .` → **242 tests OK**；
  `python scripts/accept_ui.py` 全绿（原始输出存 `runs/ui/accept-handover.txt`，
  像素亮度 深 24/24/41/48、浅 240/250，对比度 45 组最低 8.11:1）。
- 队列状态：**全部完成**。下一步只剩 P4 结构性方向（等用户拍板），以及可选：若用户同意，
  按新方向开新一轮。

### 第 16 轮 新队列③ 搜索命中定位（非结构性队列清空）
- 改：`server.py` 新增 `_locate_term(source, term)`（按**原始文件**逐行找首个命中，返回 `{term,line,found}`；
  空白词直接判未命中，找不到老实给 line=0），`/api/sources/<id>` 支持 `?highlight=`；
  `app.js` 记住当前搜索词（`state.searchQuery`），左栏来源卡点击时带上它，
  `openSource(sourceId, line, term)` 在**没有显式行号**时用后端返回的命中行。
- 过程中修掉两个真 bug：
  1. 来源已在缓存里时 `openSource` 会跳过请求 → `?highlight=` 永不触发（自检直接抓到）；
     改成"带搜索词且没给行号时总是问一次后端"。
  2. `renderSources()` 会给**命中的来源卡**也追加 `.src-item__hit`，自检里用"没有该子元素"筛来源卡是错的，
     改用 `data-source-id` 精确定位。
- 新增：`tests/test_source_highlight.py` 6 条（拉丁词首个命中、词边界不误命中、中文词、找不到返回 0、
  空白词、CRLF 不串行）。
- 证据：`python -m unittest discover -s tests -t .` → **242 tests OK**（236+6）；
  `accept_ui.py` 两种配色全绿；自检输出 `/api/sources?highlight= 返回首个命中行 — BPE tokenizer → 第 307 行`
  与 `带搜索词打开原文时定位到该行 — 第 307 行`。
- 队列状态：**P1/P2/P3 + 新队列全部完成**。下一项：整轮交付说明（`docs/handover.md`，
  汇总功能清单、验收证据、架构与运行方式），然后只余 P4 结构性方向等用户拍板。

### 第 15 轮 新队列② 键盘浏览画布
- 改：`app.js` 新增 `canvasWidgets()` / `canvasCursorWidget()` / `moveCanvasCursor(delta)`，
  `wireKeyboard()` 里接 `j`/`k`（移动光标）与 `Enter`（打开光标组件的首条引用），
  并保持"输入框/文本域里不抢键"的既有守卫；`styles.css` 第十四节给 `.widget.is-cursor` 显式焦点环
  （程序化 focus 不一定触发 `:focus-visible`）。`scrollIntoView({block:"nearest"})` 保证光标组件在视野内，
  `announce()` 播报"组件名（第 n/N）＋引用条数"。
- 新增断言（交互自检 5 条）：j 聚焦首个组件、再按 j 到下一个、k 退回上一个、Enter 打开引用原文、
  输入框里按 j 不影响画布光标。两视口实测：`回答 → 概念关系 → 退回`，Enter 后右栏 763 行。
- 证据：`python -m unittest discover -s tests -t .` → **236 tests OK**；`accept_ui.py` 两种配色全绿。
- 下一项：新队列③ 搜索命中定位（`/api/sources/<id>?highlight=<term>`）。

### 第 14 轮 新队列① 引用悬停预览
- 改：`widgets.js` 的 `renderCiteChips` 给每个 chip 挂 `data-cite-locator / data-cite-title / data-cite-quote`
  （出处数据来自 Surface 自带字段，**不需要额外请求**）；`app.js` 新增 `wireCitationPreview()`
  （document 级事件代理：mouseover/mouseout/focusin/focusout/scroll/Esc），
  `showCitationPreview()` 会贴着 chip 定位并做视口夹取，`hideCitationPreview()` 带 120ms 延迟避免闪烁；
  `styles.css` 第十三节 `.cite-preview` 卡片（`pointer-events:none`）。
- 新增断言：交互自检「悬停引用出现预览（含行号与原文）」「移开后预览消失」；
  `accept_ui.py` 样式证据要求 `#cite-preview` 的 `pointer-events` 必须是 `none`。
- 证据：`accept_ui.py` 两视口自检输出 `lecture_01.py:703 · 27 字` 且移开即消失；
  `python -m unittest discover -s tests -t .` → **236 tests OK**；两种配色验收全绿。
- 下一项：新队列② 键盘浏览画布（j/k 移动焦点、Enter 打开引用）。

### 第 13 轮 P3 输入区悬浮感 + 收尾评审裁决（P3 队列清空）
- 改：`styles.css` 第十一节（`.composer` 去掉 `border-top`，改为上方 `--sp-5`(24px) 线性渐变淡出到栏底色，
  `pointer-events:none`；断言要求上边框为 0 且渐变层不吃点击）；第十二节（≤620px 时 `.plan-bar__types`
  改单行横向滚动、间距 8px、chip 不压缩）。
- 收尾评审（deepseek-flash 看三张图，¥0.0029/4.5s）给三条，逐条核实后**只采纳一条**：
  - ✗ "右栏代码对比度过高、字体渲染偏重"：计算样式实测字重 **400**、代码底 `rgb(31,33,36)`（比栏底色更亮而非"漆黑补丁"）、
    正文 13:1 —— 属口味判断，不为它牺牲可读性；故不改。
  - ✗ "提示文字偏淡、需要 AA"：实测 `.composer__hint` 浅色 9.11:1 / 深色 9.28:1，远超 AA 4.5:1；不改。
  - ✗ "输入框需要 2px 高亮描边"：全局 `:focus-visible { outline: 2px solid var(--focus) }` 已存在；不改。
  - ✓ "390 下计划 chip 折成砖墙"：实测 `chipTopRows` 桌面 1 行 / 移动 **2 行** → 已改单行滚动 + 8px 间距，
    现在移动端实测 **1 行 / 8px**。
- 证据：`python -m unittest discover -s tests -t .` → **236 tests OK**；`accept_ui.py` 两种配色全绿；
  新增断言：输入区上边框必须 0、必须有渐变、渐变层 `pointer-events:none`、窄屏计划 chip 必须单行且间距 ≥8px。
- 队列状态：**P1/P2/P3 全部完成**。剩余只有 P4 结构性方向（等用户拍板）。

### 第 12 轮 P3 顶/底栏 chip 权重
- 改：`styles.css` 第九节（`.mode-chip` 透明底 + 透明边框，选中态改"靛蓝文字 + 2px 下划线"，
  hover 才给底色；`kbd` 去掉边框/底边/底色，只留等宽文字；模式 chips 间距收紧）+
  第十节（模式行与输入框间距 `--sp-2`→`--sp-4`；新增 `.composer__modehint` 让状态说明独立成行；
  快捷键行间距收紧成一组）。`app.js`：把 `#mode-hint`（"一问一答：正文 + 引用 + 来源"）移出
  `.composer__hint`，并把"Esc 中断 / 关抽屉"改成"Esc 中断、关抽屉"（斜杠与 `/` 快捷键易混）。
- 交叉验证（Kimi 看图，`--effort off`）：确认"抢正文风头"**基本修好**；它另提两条——
  模式按钮与输入框间距不足（已改 16px）、快捷键行混进状态说明与斜杠歧义（已拆行 + 改顿号）。
- 新增断言（`accept_ui.py` 样式证据）：模式 chip 高度必须 ≥32px、选中 chip 底色必须透明、
  `kbd` 边框必须为 0、模式行与输入框间距 ≥8px、快捷键行里不得出现无 `kbd` 的说明项、
  模式说明必须独立成行。实测 `chip 高度 [34×6]｜选中底色 rgba(0,0,0,0)｜下划线 rgb(127,168,212) -2px inset｜
  键帽边框 0px｜间距 16px｜非快捷键项 0｜独立成行 True`。
- 证据：`python -m unittest discover -s tests -t .` → **236 tests OK**；`accept_ui.py` 两种配色全绿
  （像素亮度深 24/39/51、浅 250/242；对比度 12 组全过）。
- 下一项：P3 输入区"悬浮感"（composer 与画布之间用渐变过渡代替硬边）——队列最后一条。

### 第 11 轮 P3 右栏代码面板留白
- 改：`styles.css` 第七节（`.srcview` 加内边距；`.srcview__code` 行高 1.75 + `--surface` 卡片 + 边框圆角；
  `.line` 行间 1px；命中行改 `--accent-soft` 底 + 4px 左侧竖标，把整行糊黄让位给词级高亮）；
  第八节（行号列加右侧竖沟 `border-right`，折行不再顶到行号；正文列统一左缩进）。
- 操作发现（重要）：**评审用的 Kimi 必须关思考**。`kimi-for-coding` 在 `reasoning_effort=low` 下仍会把
  1400 个 max_tokens 全花在 reasoning 上、正文为空；改发 `thinking:false`（脚本 `--effort off`）后
  13.9 秒返回完整评审。`scripts/visual_review.py` 的默认 effort 因此定为 `off`。
- 交叉验证（Kimi 看图，`--effort off`）：确认"整块底色太满、像一堵墙"**已修好**；它另提两条——
  折行没有悬挂缩进、行号列与正文挤在一起（已用竖沟修）；以及"行号列宽忽宽忽窄、正文起点不齐"。
- 结论裁决：新增计算样式断言后实测 **行号列宽唯一值 42px、正文起点唯一值（1440 下 1164、390 下 151）**，
  说明它那条"忽宽忽窄"是看错；命中竖标实测 `rgb(127,168,212) 4px inset`。
- 证据：`python -m unittest discover -s tests -t .` → **236 tests OK**（本轮无新增测试，改动为样式）；
  `accept_ui.py` 两种配色全绿（含对比度 12 组、像素亮度、代码列对齐三条新断言）。
- 下一项：P3 顶/底栏 chip 权重。

### 第 10 轮 P2 性能基线
- 改：新增 `scripts/bench.py`（固定材料 + 8 个问题 + 可调重复次数，量摄取 / 冷建索引 / 热缓存 /
  逐查询检索 / 端到端离线问答，输出 markdown 到 `runs/bench.md`）；新增 `docs/performance.md`
  （数字 + 解读 + 复现方式）；新增 `tests/test_performance_guard.py`（5 条宽松护栏，只盯灾难性退化）。
- 基线数字（3 份材料 / 604 片段 / 1823 索引词）：摄取 0.072s；冷建索引 **31.3ms**（峰值 1.8MB）、
  热缓存 0.001ms；检索 120 次中位 **0.2ms**（p95 0.3ms）；端到端离线问答 8 次中位 **17.4ms**（p95 26.9ms）。
- 解读：离线问答是十毫秒级，接真实模型后瓶颈在网络与生成，检索层再快也不是收益点——
  优化该花在"少发请求、少塞无关上下文"上（多轮上下文与槽位机制正是在做这件事）。
- 证据：`python -m unittest discover -s tests -t .` → **236 tests OK**（231+5）；
  `accept_ui.py` 全绿；`runs/bench.md` 与 `docs/performance.md` 同源。
- 下一项：P3 审美三条（右栏代码面板留白 → 顶/底栏 chip 权重 → 输入区悬浮感）。

### 第 9 轮 P2 浅色模式验收 + WCAG 对比度审计
- 改：`scripts/accept_ui.py` 支持 `--themes dark,light`（默认两种都跑）；新增 `set_theme()`（CDP media 覆盖）、
  `contrast_audit()`（浏览器内算 12 组文字/底色对比度，背景透明时向上合成）、
  `verify_theme_pixels()`（截图画布区平均亮度：light>150、dark<90，否则判失败）。
  截图命名改为 `<label>-<theme>.png`。
- 修：**测试顺序缺陷**——interactive 轮会往验收库写问答记录，若先跑 interactive 再跑 index，
  首页就不再是"未提问"空状态、断言会假失败。现在 index 轮全部先跑完。
- 证据：`accept_ui.py` 全绿；像素证据 `index-1440-dark 24 / index-390-dark 24 / index-1440-light 250 /
  index-390-light 250 / selftest-1440-dark 39 / selftest-390-dark 51 / selftest-1440-light 242`（深浅分明）；
  对比度审计 12 组全部达标（最低 8.86:1，深色/浅色都过）；
  `python -m unittest discover -s tests -t .` → 231 tests OK。
- 附带发现：浅色主题首次被真正截图（`runs/ui/*-light.png`），纸面配色正常，无对比度或裁切问题。
- 下一项：P2 性能基线（`scripts/bench.py`）。

### 第 8 轮 P1 命中位置（词级高亮）
- 改：`textutil.find_term_spans()`（大小写不敏感、拉丁词按词边界、CJK 按字面，限 2~3 处）与
  `merge_spans()`；`protocol.Hit` 增加 `matches`（term/start/end，指回 `chunk.text` 的绝对偏移）；
  `index.search` 为每条命中填 `matches`；`server.App.search` 返回 `matched` + `matches`；
  前端 `app.js` 新增 `highlightTerms()`（词边界正则，CJK 单独处理），左栏片段与右栏命中行都用它，
  样式 `.term-hit` 复用 `--mark/--mark-ink` 令牌；交互自检新增"搜索 → 左栏高亮 → 右栏同行高亮"两步。
- 新增：`tests/test_search_offsets.py` 10 条（大小写、词边界、上限、CJK 偏移、空输入、区间合并、
  `Hit.matches` 指回原文、序列化、每词最多 2 处）。
- 证据：`python -m unittest discover -s tests -t .` → **231 tests OK**（221+10）；
  `accept_ui.py` 全绿，交互自检输出「左栏片段高亮命中词 — 10 处」「右栏同一行高亮命中词 — 第 273 行，10 处」；
  截图确认左栏 8 条命中的片段里命中词标黄、右栏 `lecture_01.py:307` 行内标黄。
- 下一项：P2 浅色模式验收（acceptance 增加 `prefers-color-scheme: light` 一轮截图）。

### 第 7 轮 P1 整本导出（`export-notebook`）
- 改：`store.list_turn_payloads()`（最近 N 轮完整问答，同秒用 rowid 兜底，按时间正序返回）；
  `render.notebook_to_markdown()`（材料清单 → 问题目录 → 逐轮正文 → 引用总索引，索引按被引次数排序）；
  `cli.export-notebook` 子命令（`--notebook/--limit/--out`，显式关库以免同进程测试占住文件）；
  `surface_to_markdown` 在已有 `sources` 组件时不再重复输出尾部"来源"。
- 新增：`tests/test_export.py` 5 条。
- 证据：`python -m unittest discover -s tests -t .` → **221 tests OK**（216+5）；
  真材料 `python -m mentor.cli export-notebook --out runs/study-guide.md` → 3 份材料 / 6 轮 / 9204 字，
  引用总索引显示 `lecture_01.py:307`、`lecture_01.py:699` 各被 6 轮引用；`accept_ui.py` 全绿。
- 下一项：P1 命中位置（`/api/search` 返回命中词偏移 + 右栏行内高亮）。

### 第 6 轮（23:2x–23:4x）P1 多轮追问上下文
- 改：`tutor.py` 新增 `_prior_context()` 与 `ask_stream(..., context_turns=2)`：
  取最近 N 轮的 citations → 用 `store.get_chunk()` 解析（含已归档片段）→ 作为"上一轮引用过的材料"
  追加到本轮 hits 之后（词面命中优先，追问时它是唯一上下文）；`GenerateRequest.history` 把最近几个问题带给模型；
  `server.py /api/ask` 支持 `context_turns`；追问场景的覆盖度提示改为"本轮主要沿用上一轮的上下文"。
- 新增：`tests/test_followup.py` 5 条（无词面重合的追问能复用上一轮片段、关掉上下文时走诚实的空状态、
  上下文去重且 ≤8 条、history 传给 provider、context_turns=0 不带历史）。
- 证据：`python -m unittest discover -s tests -t .` → **216 tests OK**（211+5）；
  `accept_ui.py` 全绿。真材料端到端：第 1 轮引用 `lecture_01.py:699/703/307`，
  追问"继续展开讲讲"复用同一批 4 段（交集 4 个 chunk），notes 为"已带上上一轮的 4 段上下文"。
- 下一项：P1 整本导出（`export-notebook`）。

### 第 5 轮（23:0x–23:2x）AI 侧
- 改：`styles.css` 加第五节（行号对比度、段落留白、`.btn--quiet`、提示对比度、概念图连线 1.8px）；
  `widgets.js` 概念图 `COL_GAP 96→68 / NODE_GAP 14→10`；`app.js` 顶栏胶囊从 4 个减到「版面 + 意图」、
  后端名进 tooltip、placeholder 去掉重复快捷键说明；第四节：`text-wrap: pretty` 修引用角标孤立换行、
  待渲染 chip 边框提对比、窄屏停止按钮改中性、底部引用 chip 24px。
- 证据：211 tests OK；`accept_ui.py` 全绿（含新增样式证据断言）；截图对比 390 引用角标不再孤立换行。
- 下一项：P1 多轮追问上下文。
