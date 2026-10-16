# 先例调研（prior art）：mentor 的审美与 UI 从哪里借

原则（沿用本项目一贯的做法）：**只写真正读到原文的条目**；每条注明来源、借什么思想、怎么落到 mentor、代价。
引用的都是公开资料或本机已存在的成果，不写"印象里有个仓库"。

调研时间：2026-10-16（低谷轮次）。调研人：主 Agent。

---

## 1. Anthropic 公开 skill：`pptx`（版式与"不要像 AI 做的"）

来源：[anthropics/skills → skills/pptx/SKILL.md](https://raw.githubusercontent.com/anthropics/skills/main/skills/pptx/SKILL.md)
（公开仓库 [anthropics/skills](https://github.com/anthropics/skills)；实测 raw 链接 HTTP 200）

它是做 PPT 的 skill，但里面**关于设计判断的那一段与介质无关**，而且直说得罪人：

| 借什么思想 | 它原话的意思 | 怎么落到 mentor | 代价 |
|---|---|---|---|
| **配色要"为这个主题做的"** | "如果把这套颜色换到完全另一个演示里还成立，说明你选得不够具体" | mentor 现在是通用深色/纸白 + 靛蓝强调，换到任何阅读器都成立 → 见决策页 B | 改令牌即可，但要重做对比度审计与像素亮度基线 |
| **主次不能平均** | 一个主色占 60~70% 视觉权重，1~2 个辅色，1 个锐利强调色 | mentor 已经是"底/面/线/墨 + 少量强调"，方向一致，只需检查强调色是否被滥用 | 低 |
| **绝不用边缘色条与标题下划线** | "标题下划线、卡片单边色条、页眉页脚色带，都是 AI 生成的标志；要区分卡片请用淡底、阴影或图标" | **mentor 现有 4 处这类条纹**（命中行 4px 竖标、左栏命中片段 2px 竖线、选中模式下划线、目录项 3px 竖线）→ 见决策页 B | 去掉后"命中"的指示需要替代方案（淡底/图标），否则可读性下降 |
| **别默认奶油/米色底** | 点名避免 `F5F5DC`/`FAF0E6`/`FAEBD7`/`FFF8E1` 这类暖白 | mentor 浅色主题纸面是 `#fbfaf7`（暖白）→ 见决策页 B | 换成纯白或冷白会影响"纸感"，需要重新算对比度 |
| **字号对比不要抠** | 标题 36–44pt 对正文 14–16pt（约 2.5×），"标题要明显跳出正文" | mentor 正文 15.5px，标题目前偏保守 → 见决策页 A | 标题变大意味着同屏信息量下降 |
| **间距要一致且留白** | 0.5" 边距、0.3–0.5" 块间距，"别把每一寸都填满" | mentor 有间距令牌，但段距实测 8px（0.30× 行距）偏小 → 见决策页 A | 段距变大会让长文档更长 |
| **首轮渲染一定有真问题，要重新看图** | "第一版通常有重叠、溢出、错位；盯着生成代码会看到你以为的东西" | mentor 的 `accept_ui.py` 已经是这个思路（+ 用模型做第二双眼，但**必须复核**） | 已具备 |

**不适用**的部分：pptxgenjs 的具体坑（EMU、`charSpacing`、图表 XML）与 mentor 无关，不抄。

---

## 2. 本机已有成果：DSH 阅读/流式稳定性插件调研

来源：`~/Documents/deepseek-harness/default-workspace/projects/dsh-reading-stability/docs/prior-art.md`
（约 38 小时前另一个对话产出；29KB，含 GitHub/npm 实检结论）

它最有价值的三条，**mentor 逐条对照**：

| 借什么思想 | 它的依据 | mentor 现状（实测） | 动作 |
|---|---|---|---|
| **滚动锚定在"钉底 + 追加"场景下是有害的**，内核已主动关掉 | [W3C CSS Scroll Anchoring](https://drafts.csswg.org/css-scroll-anchoring/) + [MDN `overflow-anchor`](https://developer.mozilla.org/en-US/docs/Web/CSS/overflow-anchor) + 两个插件的独立验证 | `ui/app.js` 第 807 行确实在做"接近底部才自动滚动"，但 CSS 里 `overflow-anchor` 仍是 `auto`（实测计算值 `auto`） | **本轮已修**：滚动容器加 `overflow-anchor: none` |
| **增量 markdown + 块级记忆化**：完成块不重解析 | [Streamdown 记忆化](https://streamdown.ai/docs/memoization)、[Vercel AI SDK 的推荐路径](https://ai-sdk.dev/cookbook/next/markdown-chatbot-with-memoization) | mentor 的组件按 id 幂等追加（`seen` 去重），完成块不会重渲染；**目前不流式渲染"半个 markdown"** | 不改；若将来做"逐字流入正文"，必须先做块级记忆化 |
| 长文 **排版比例** 用可引用数值 | [W3C WAI — SC 1.4.8 视觉呈现](https://www.w3.org/WAI/WCAG21/Understanding/visual-presentation.html)：行宽 ≤80 字符（CJK 40）、行距 ≥1.5×、**段距 ≥1.5× 行距**、200% 放大不横向滚动 | 实测：行长 **44.8 CJK 字**（超）、行距 1.70×（达标）、段距 **0.30× 行距**（不达标）、200% 无横向滚动（达标） | 列宽与段距进**决策页 A**（观感问题，不擅改） |

**注意**：那份文档是给 DSH 插件写的，它的 §4.4 还纠了它自己 SPEC 里一个数量级笔误（`calc(size * 8)` 应为别的量纲）。
借它的**方法**（用 WCAG 阈值独立核算自己的令牌），而不是照抄它的数值。

---

## 3. 本机已有成果：`dsh-replica`（DSH Web UI 的复刻 + 令牌审计）

来源：`~/Documents/deepseek-harness/default-workspace/projects/dsh-replica/`（README + `shots/v28-audit.json`）

| 借什么思想 | 怎么落到 mentor | 代价 |
|---|---|---|
| **壳体/排版先复刻再做减法**：从真实安装包里读出的界面骨架与设计令牌做参照，而不是凭感觉设计 | mentor 的"三栏 + 画布 + 右栏原文"已经定型；可借它的**令牌命名与层级组织方式**做一次令牌审计 | 一次纯重构，不改观感，但会动很多 CSS 行 |
| **动效克制**：只做状态变化的一次性动效，不做循环 | mentor 已经是（骨架静态、`widget-in` 200ms、无无限动画） | 无 |
| **复刻版是"可交互"的**，不是静态稿 | 决策页也应可交互（能真的 hover/点开），而不是画两张图 —— 见本轮 `runs/decisions/*` | 生成脚本要能注入真实 CSS |

---

## 4. 结论：本轮借了什么、还欠什么

**已落地**（有验证）：
1. `overflow-anchor: none` —— 依据 §2 第一条，且与 `app.js` 自己的"接近底部才滚动"配套。
2. 用 WCAG 1.4.8 的四个阈值**独立核算** mentor 的排版（行长 / 行距 / 段距 / 200% 缩放），量出两处不达标。

**进决策页**（观感问题，按规则第 5 条不擅改）：
- A 排版：阅读列宽（760 / 668 / 700）、段距（8px / WCAG 对齐）、字号阶梯。
- B 视觉语言：边缘色条去留、暖白纸面、强调色用量。
- C 结构：正文与组件卡的优先级、`aside` 类默认收起、窄屏两栏。

**还欠**（下一轮候选）：
- `content-visibility: auto` 用在离屏轮次上（依据 §2 的 `dsh-clearview` 做法，mentor 多轮场景同样受益）。
- 令牌审计（借 §3 的组织方式重排 `:root`）。
- 用 [Streamdown 的排版做法](https://streamdown.ai/docs/typography) 对照 mentor 的标题响应式字号。
