# 生成审美决策页：runs/decisions/*.html
#
# 为什么用脚本而不是手写 HTML：用户给完意见后要能**重新生成**，而不是手改一坨 HTML。
# 为什么页面直接引 ui/styles.css：决策页里的变体必须用 mentor **真实的令牌与组件样式**渲染，
# 否则用户是在对着一张示意图做决定（那种页面对工程判断没有价值）。
#
# 用法： python scripts/build_decisions.py
from __future__ import annotations

import html
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
# 放进 docs/ 而不是 runs/：决策页是要用户打开、要进仓库的过程产物（runs/ 被 gitignore）。
# 相对路径 ../../ui/styles.css 对 docs/decisions/ 同样成立，双击 file:// 打开也有效。
OUT = PROJECT / "docs" / "decisions"

# --------------------------------------------------------------------------
# 页面骨架
# --------------------------------------------------------------------------
SHELL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>__TITLE__</title>
<link rel="stylesheet" href="../../ui/styles.css">
<style>
  /* 决策页自己的外壳：刻意不复用 .app 的网格，避免和 mentor 的响应式布局打架；
     但所有"被评估的观感"都用 mentor 真实的类与令牌渲染。 */
  body { margin: 0; padding: 28px 24px 64px; background: var(--paper); color: var(--ink); }
  .wrap { max-width: 1560px; margin: 0 auto; }
  .kicker { font-size: var(--fs-xs); color: var(--ink-2); letter-spacing: .04em; text-transform: uppercase; }
  h1 { font-size: 26px; line-height: 1.3; margin: 6px 0 10px; }
  h2 { font-size: 18px; margin: 34px 0 10px; padding-bottom: 6px; border-bottom: 1px solid var(--line); }
  h3 { font-size: 15px; margin: 22px 0 8px; }
  p, li { font-size: 14.5px; line-height: 1.75; color: var(--ink); }
  .muted { color: var(--ink-2); }
  .lede { font-size: 15.5px; line-height: 1.8; max-width: 76ch; }
  .measured { display: flex; flex-wrap: wrap; gap: 10px; margin: 14px 0 6px; padding: 0; list-style: none; }
  .measured li { flex: 1 1 200px; background: var(--surface); border: 1px solid var(--line);
                 border-radius: var(--r-md); padding: 10px 12px; font-size: 13px; line-height: 1.6; }
  .measured b { font-size: 15px; }
  .ok::before { content: "✓ "; color: var(--ok, #4e9a6a); }
  .bad::before { content: "✗ "; color: var(--warn, #c2703b); }
  .variants { display: grid; grid-template-columns: minmax(0, 1fr); gap: 22px; margin-top: 12px; }
  .variant { background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-lg);
             padding: 14px 16px 18px; min-width: 0; }
  .variant__head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin-bottom: 4px; }
  .variant__code { font-family: var(--font-mono); font-size: 13px; color: var(--accent-ink);
                   border: 1px solid var(--line-strong); border-radius: var(--r-pill); padding: 1px 8px; }
  .variant__name { font-size: 15px; font-weight: 650; }
  .variant__desc { font-size: 13.5px; line-height: 1.7; color: var(--ink-2); max-width: 86ch; }
  .stage { margin-top: 12px; border: 1px solid var(--line); border-radius: var(--r-md);
           overflow-x: auto; overflow-y: hidden; background: var(--paper); }
  .stage::after { content: "窗口宽 1440px（可横向滚动查看整幅）"; display: block; padding: 4px 10px;
                  font-size: 11.5px; color: var(--ink-2); border-top: 1px solid var(--line); }
  .narrow-hint::after { content: "窗口宽 420px（窄屏真实尺寸）"; }
  /* 决策页里的"窗口"：定宽，靠 CSS 变量区分各变体 */
  .d-shell { display: grid; grid-template-columns: 208px minmax(0, 1fr) 320px; height: 420px; }
  .d-shell.two-col { grid-template-columns: 208px minmax(0, 1fr); }
  .d-shell.no-right { grid-template-columns: 208px minmax(0, 1fr); }
  .d-shell--narrow { grid-template-columns: minmax(0, 1fr); height: 520px; }
  .d-narrow { display: flex; flex-direction: column; min-width: 0; }
  .d-topbar { display: flex; align-items: center; justify-content: space-between; gap: 8px;
              padding: 8px 10px; border-bottom: 1px solid var(--line); background: var(--rail); }
  .d-tabs { display: flex; gap: 10px; }
  .d-tab { font-size: 13px; color: var(--ink-2); padding-bottom: 2px; }
  .d-tab.is-on { color: var(--accent-ink); font-weight: 600; box-shadow: inset 0 -2px 0 0 var(--accent); }
  .d-rail { border-right: 1px solid var(--line); background: var(--rail); padding: 10px; overflow: hidden; }
  .d-main { overflow: hidden; }
  .d-right { border-left: 1px solid var(--line); background: var(--rail); padding: 10px; overflow: hidden; }
  .d-canvas { height: 100%; overflow: hidden; }
  .d-canvas .canvas__inner { max-width: var(--maxw-read); margin: 0 auto; padding: 14px 16px; }
  .d-composer { border-top: 1px solid var(--line); background: var(--rail); padding: 8px 12px; }
  .d-row { display: flex; gap: 6px; flex-wrap: wrap; }
  table { border-collapse: collapse; width: 100%; margin: 10px 0 4px; font-size: 13.5px; }
  th, td { border: 1px solid var(--line); padding: 7px 9px; text-align: left; vertical-align: top;
           line-height: 1.6; }
  th { background: var(--surface-2); font-weight: 650; }
  .verdict { background: var(--accent-soft); border: 1px solid var(--line-strong);
             border-radius: var(--r-md); padding: 12px 14px; margin: 12px 0; }
  .verdict b { color: var(--accent-ink); }
  .how { background: var(--surface); border: 1px dashed var(--line-strong); border-radius: var(--r-md);
         padding: 12px 14px; font-size: 13.5px; line-height: 1.7; }
  code { font-family: var(--font-mono); font-size: .92em; background: var(--surface-2);
         border: 1px solid var(--line); border-radius: 4px; padding: 0 4px; }
  .nav { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 8px; font-size: 13.5px; }
  .nav a { color: var(--accent-ink); }
  .legend { font-size: 12.5px; color: var(--ink-2); margin-top: 6px; }
  @media (max-width: 900px) {
    .d-shell, .d-shell.two-col, .d-shell.no-right, .d-shell--narrow { grid-template-columns: minmax(0, 1fr); }
    .d-shell:not(.d-shell--narrow) { height: auto; }
    .d-rail, .d-right { display: none; }
  }
</style>
</head>
<body>
<div class="wrap">
__BODY__
</div>
<script>
  // 验收脚本会读这个：决策页自己也不许有未捕获错误
  window.__ERRORS__ = [];
  window.addEventListener("error", (e) => window.__ERRORS__.push(String(e.message || e)));
  window.addEventListener("unhandledrejection", (e) => window.__ERRORS__.push("rejection: " + String(e.reason)));
</script>
</body>
</html>
"""

NAV = ('<p class="nav"><a href="index.html">← 返回决策索引</a>'
       '<a href="a-typography.html">A 排版</a>'
       '<a href="b-visual-language.html">B 视觉语言</a>'
       '<a href="c-structure.html">C 结构</a></p>')


def esc(text: str) -> str:
    return html.escape(str(text), quote=True)


# --------------------------------------------------------------------------
# 真实观感的 mock：用 mentor 自己的类与令牌
# --------------------------------------------------------------------------
def answer_widget(paras: list[str], cites: int = 3, title: str = "回答") -> str:
    body = "".join(f"<p>{esc(p)}</p>" for p in paras)
    chips = "".join(f'<button type="button" class="cite-chip" data-cite-n="{i + 1}">[{i + 1}]</button>'
                    for i in range(cites))
    # 真实渲染器在"标题 == 类型标签"时不重复显示徽标，mock 也照做，否则观感会失真
    badge = "" if title == "回答" else f'<span class="widget__badge">{esc(title)}</span>'
    return (f'<article class="widget"><div class="widget__head">{badge}'
            f'<span class="widget__title">{esc(title)}</span></div>'
            f'<div class="widget__body md">{body}'
            f'<div class="md-cites">{chips}</div></div></article>')


def quote_widget() -> str:
    return ('<article class="widget"><div class="widget__head">'
            '<span class="widget__badge">原文引用</span>'
            '<span class="widget__title">原文</span></div>'
            '<div class="widget__body"><blockquote class="quote">'
            '<p>Byte-Pair Encoding (BPE) is a tokenizer that merges the most common pair of '
            'adjacent tokens.</p><cite>lecture_01.py:699</cite></blockquote></div></article>')


def steps_widget() -> str:
    return ('<article class="widget"><div class="widget__head">'
            '<span class="widget__badge">步骤</span><span class="widget__title">训练三步</span></div>'
            '<div class="widget__body"><ol class="md"><li>统计相邻字节对频率</li>'
            '<li>合并最高频的一对</li><li>重复直到达到词表规模</li></ol></div></article>')


def flashcards_widget() -> str:
    return ('<article class="widget widget--aside"><div class="widget__head">'
            '<span class="widget__badge">闪卡</span><span class="widget__title">闪卡 · 3 张</span>'
            '<span class="widget__toggle">展开</span></div>'
            '<div class="widget__body"><div class="cards"><div class="card">BPE 合并的是什么？'
            '<span class="card__back">最高频的相邻 token 对</span></div></div></div></article>')


def quiz_widget() -> str:
    return ('<article class="widget widget--aside"><div class="widget__head">'
            '<span class="widget__badge">自测</span><span class="widget__title">自测 · 2 题</span>'
            '<span class="widget__toggle">展开</span></div>'
            '<div class="widget__body"><div class="quiz"><p>1. BPE 的合并依据是？</p>'
            '<label class="quiz__opt"><input type="radio" name="q"> 词频</label>'
            '<label class="quiz__opt"><input type="radio" name="q"> 相邻对频率</label></div></div></article>')


def concept_map_widget() -> str:
    return ('<article class="widget"><div class="widget__head">'
            '<span class="widget__badge">概念图</span><span class="widget__title">概念关系</span></div>'
            '<div class="widget__body"><svg class="cmap" viewBox="0 0 420 120" width="100%" height="104">'
            '<line class="cmap__edge" x1="70" y1="60" x2="200" y2="60"></line>'
            '<line class="cmap__edge" x1="200" y1="60" x2="340" y2="60"></line>'
            '<rect class="cmap__node" x="20" y="40" width="100" height="40" rx="8"></rect>'
            '<text class="cmap__node-label" x="70" y="64" text-anchor="middle">bpe</text>'
            '<rect class="cmap__node" x="150" y="40" width="100" height="40" rx="8"></rect>'
            '<text class="cmap__node-label" x="200" y="64" text-anchor="middle">tokenizer</text>'
            '<rect class="cmap__node" x="290" y="40" width="100" height="40" rx="8"></rect>'
            '<text class="cmap__node-label" x="340" y="64" text-anchor="middle">vocab</text>'
            '<text class="cmap__edge-label" x="135" y="50">同现 2</text>'
            '<text class="cmap__edge-label" x="270" y="50">同现 1</text></svg></div></article>')


def left_rail() -> str:
    return ('<div class="d-rail"><div class="rail__section"><span class="section-label">资料来源</span>'
            '<button type="button" class="src-item"><span class="src-item__row">'
            '<span class="src-item__name">CS336: Language Models…</span>'
            '<span class="hit-flag">命中 6</span></span>'
            '<span class="src-item__meta"><span class="kind-tag">lecture-note</span>'
            '<span>lectures/lecture_01.py</span></span>'
            '<span class="src-item__hit">lecture_01.py:699 Byte Pair Encoding (BPE)…</span></button>'
            '<button type="button" class="src-item"><span class="src-item__row">'
            '<span class="src-item__name">setup-notes</span></span>'
            '<span class="src-item__meta"><span class="kind-tag">markdown</span>'
            '<span>docs/setup-notes.md</span></span></button></div></div>')


def right_rail(lines: int = 7) -> str:
    body = ('<div class="d-right"><div class="srcdoc__head">'
            '<div class="srcdoc__title">CS336: Language Models From Scratch</div>'
            '<div class="srcdoc__path">lectures/lecture_01.py</div></div>'
            '<div class="srcview"><div class="srcview__code">')
    content = ["702 text(\"(Previously, papers had been using word-based\"",
               "703 text(\"BPE was then used by GPT-2. \"), link(gpt2_2019)",
               "704", "705 text(\"Basic idea: *train* the tokenizer on raw text\")",
               "706 text(\"Intuition: common sequences of bytes → one token\")",
               "707", "708 text(\"Sketch: start with each byte as a token\")"]
    for i, text in enumerate(content[:lines]):
        cls = "line is-hit" if i == 1 else "line"
        body += (f'<div class="{cls}"><span class="line__n">{702 + i}</span>'
                 f'<span class="line__text">{esc(text)}</span></div>')
    return body + '</div></div></div>'


def composer(mode: str = "概念") -> str:
    chips = "".join(
        f'<button type="button" class="mode-chip" aria-pressed="{"true" if m == mode else "false"}">{m}</button>'
        for m in ("提问", "梳理", "概念", "复习", "对比", "音频"))
    return (f'<div class="d-composer"><div class="mode-chips">{chips}</div>'
            f'<div class="composer__row"><textarea class="composer__input" rows="1" '
            f'placeholder="问点什么"></textarea></div></div>')


def shell(main: str, *, right: bool = True, two_col: bool = False, cls: str = "",
          width: int = 1440) -> str:
    """画一个"窗口"。

    `width` 默认 1440：只有窗口够宽，`--maxw-read`（760/700/668）才会成为真正的约束条件。
    早先用 1180 的容器，三个变体的正文列宽都被压成同一个值，对比等于没比。
    窗口比页面宽时由 `.stage` 自己横向滚动，不撑破页面。
    """
    klass = "d-shell" + (" two-col" if two_col else "") + ("" if right else " no-right") + (" " + cls if cls else "")
    cols = left_rail() + f'<div class="d-main">{main}</div>' + (right_rail() if right else "")
    return (f'<div class="stage"><div class="{klass}" style="width:{width}px">{cols}</div></div>')


def narrow_shell(main: str) -> str:
    """窄屏（≤620px）的候选形态：顶栏 + 「画布 / 原文」标签页 + 画布 + 输入区。"""
    bar = ('<div class="d-topbar"><button type="button" class="btn btn--quiet">☰ 来源</button>'
           '<div class="d-tabs"><span class="d-tab is-on">画布</span>'
           '<span class="d-tab">原文</span></div></div>')
    return (f'<div class="stage narrow-hint"><div class="d-shell d-shell--narrow" style="width:420px">'
            f'<div class="d-narrow">{bar}<div class="d-main">{main}</div></div></div></div>')


def variant(code: str, name: str, desc: str, mock: str, extra_css: str = "") -> str:
    """一个候选方案：说明 + 真实渲染的 mock + 只在**这个变体内**生效的 CSS。

    `extra_css` 必须写成完整且已加 `.v-<code>` 前缀的规则块。
    早先这里贪省事写成"前缀 + 片段"，结果少一个 `{` 让后续规则变成全局选择器，
    把 B2 的"去条纹"泄漏到了所有变体上（真浏览器复核才发现）。
    """
    style = f"<style>{extra_css}</style>" if extra_css else ""
    return (f'<div class="variant v-{code}">{style}'
            f'<div class="variant__head"><span class="variant__code">{esc(code)}</span>'
            f'<span class="variant__name">{esc(name)}</span></div>'
            f'<p class="variant__desc">{desc}</p>{mock}</div>')


def table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def how_to_choose() -> str:
    return ('<div class="how">怎么选：把你的选择直接回给我（例如 <code>A2 / B2 / C1</code>），'
            '或者在这页上写意见（例如"B2 但条纹只留命中行"）。'
            '如果想看某个变体的真实效果，我也可以先把那个变体临时实现出来给你点。</div>')


def page(title: str, body: str) -> str:
    return SHELL.replace("__TITLE__", esc(title)).replace("__BODY__", body)


# --------------------------------------------------------------------------
# A. 排版
# --------------------------------------------------------------------------
PARAS = [
    "材料里有 5 段直接相关的内容，要点如下：BPE 先把文本拆成字节，再反复合并最高频的相邻对，"
    "直到词表达到目标规模。",
    "GPT-2 在 2019 年采用了 BPE，此前的论文还在用基于词的分词。按 WikiText-103 的压缩率看，"
    "BPE 把每个 token 覆盖的字节数从 2.1 提到 3.4。",
    "工程上的难点不在合并规则，而在预分词与边界处理：中文和代码上是否好用，几乎完全取决于这一步。",
]


def build_a() -> str:
    body = ['<p class="kicker">决策 A</p><h1>排版：阅读列宽 / 段距 / 字号阶梯</h1>',
            '<p class="lede">这一页只谈“读起来累不累”。依据是 WCAG 1.4.8 的可引用阈值与我<b>实测</b>的 mentor 现状，'
            '以及 Anthropic 公开 pptx skill 里的排版原则（字号对比别抠、间距要一致、别把每一寸都填满）。</p>',
            '<ul class="measured">',
            '<li class="bad"><b>44.8 字/行</b><br>WCAG 建议 CJK ≤40 字/行（拉丁 ≤80）</li>',
            '<li class="ok"><b>1.70×</b><br>行距，要求 ≥1.5×</li>',
            '<li class="bad"><b>0.30× 行距</b><br>段距（8px），WCAG 建议 ≥1.5× 行距</li>',
            '<li class="ok"><b>0 横向滚动</b><br>200% 放大下</li>',
            '</ul>',
            '<p class="muted" style="font-size:13px">实测方式：真浏览器读计算样式（字号 15.5px、行高 26.35px、'
            '正文列宽 694px）。列宽的换算：694 ÷ 15.5 ≈ 44.8 个 CJK 字。</p>',
            '<h2>三个方案（下面都是 mentor 真实令牌渲染的）</h2><div class="variants">']

    body.append(variant(
        "A1", "保持现状（760px 列宽 / 8px 段距）",
        f"省事、信息密度高。代价：行长超 WCAG 建议约 12%，长段落回行时更费眼。",
        shell(f'<div class="d-canvas"><div class="canvas__inner">'
              f'{answer_widget(PARAS)}{quote_widget()}</div></div>' + composer()),
        extra_css=""))
    body.append(variant(
        "A2", "WCAG 对齐（668px 列宽 / 段距 1.5× 行距）",
        "把正文列收到 40 个 CJK 字以内，段落之间留出明显空隙。代价：同屏信息量减少，"
        "右栏与画布的宽度比例需要跟着调；表格/代码这类“宽内容”要允许突破列宽。",
        shell(f'<div class="d-canvas"><div class="canvas__inner">'
              f'{answer_widget(PARAS)}{quote_widget()}</div></div>' + composer()),
        extra_css=(".v-A2 { --maxw-read: 668px; } "
                   ".v-A2 .md p { margin: 0 0 20px; } .v-A2 .md p + p { margin-top: 0; }")))
    body.append(variant(
        "A3", "折中（700px 列宽 / 段距 12px / 加大标题对比）",
        "取 A1 与 A2 之间：行长略超 WCAG 但收窄 8%，同时把段落拉开、标题字号阶梯拉到约 2.2×。"
        "代价：仍不满足 1.4.8 的 AAA 阈值（它本来也不是 AA 要求）。",
        shell(f'<div class="d-canvas"><div class="canvas__inner">'
              f'{answer_widget(PARAS)}{quote_widget()}</div></div>' + composer()),
        extra_css=(".v-A3 { --maxw-read: 700px; } "
                   ".v-A3 .md p { margin: 0 0 12px; } "
                   ".v-A3 .widget__title { font-size: 16.5px; } .v-A3 .md h2 { font-size: 20px; }")))

    body.append('</div>')
    body.append('<h2>取舍与代价</h2>')
    body.append(table(["方案", "好处", "代价", "验收层面要动什么"], [
        ["A1", "不改任何东西", "行长超标、段落挤", "无"],
        ["A2", "最省眼、有标准可引用", "同屏信息变少；宽表格/代码需要例外规则", "改 <code>--maxw-read</code> 与 <code>.md p</code> 间距；"
                                                                       "新增断言：行长 ≤40 CJK、段距 ≥1.5× 行距"],
        ["A3", "观感提升明显，改动小", "不算标准达标", "同上，但阈值放宽"],
    ]))
    body.append('<div class="verdict"><b>我的建议：A3。</b>理由是——1.4.8 是 AAA 级，硬卡 40 字会把'
                '整个界面压窄（包括右栏对照与代码块，它们本来就该更宽）；但"段距只有 0.30× 行距"这一条'
                '无论标准如何都确实偏挤。A3 用最小代价拿下最明显的可读性收益，并把标题对比补齐。</div>')
    body.append(how_to_choose())
    body.append(NAV)
    return page("决策 A · 排版", "".join(body))


# --------------------------------------------------------------------------
# B. 视觉语言
# --------------------------------------------------------------------------
def build_b() -> str:
    body = ['<p class="kicker">决策 B</p><h1>视觉语言：边缘色条 / 纸面底色 / 强调色用量</h1>',
            '<p class="lede">Anthropic 公开的 pptx skill 里有一句很冲的话：'
            '"**绝不要标题下划线、卡片单边色条、页眉页脚色带——那是 AI 生成的标志**；要区分卡片请用淡底、'
            '阴影或图标"。mentor 现在恰好有 4 处这类条纹。这一页决定它们的去留。</p>',
            '<ul class="measured">',
            '<li class="bad"><b>4 处边缘色条</b><br>命中行 4px 竖标、左栏命中片段 2px 竖线、选中模式下划线、目录项 3px 竖线</li>',
            '<li class="bad"><b>暖白纸面 #fbfaf7</b><br>Anthropic 点名避免奶油/米色系（F5F5DC/FAF0E6/FAEBD7/FFF8E1）</li>',
            '<li class="ok"><b>强调色用量克制</b><br>靛蓝只出现在引用/选中/命中，未滥用</li>',
            '</ul>',
            '<h2>三个方案</h2><div class="variants">']

    base = (f'<div class="d-canvas"><div class="canvas__inner">'
            f'{answer_widget(PARAS)}{concept_map_widget()}</div></div>' + composer("概念"))

    body.append(variant("B1", "保持现状", "命中用左侧竖色条 + 选中模式下划线标示。指示明确、改动为零。",
                        shell(base, cls="v-current")))
    body.append(variant(
        "B2", "去掉条纹，改用淡底 + 图标 + 留白",
        "命中行改成整行淡底 + 行首一个小圆点；左栏命中片段改成淡底圆角块；选中模式改成淡底胶囊；"
        "目录项去掉竖线，用缩进与字重区分。",
        shell(base, cls="v-nostripe"),
        extra_css=(".v-B2 .line.is-hit { box-shadow: none; background: var(--accent-soft); } "
                   ".v-B2 .line.is-hit .line__n::before { content: '●'; color: var(--accent); "
                   "margin-right: 4px; } "
                   ".v-B2 .src-item__hit { border-left: 0; background: var(--mark); "
                   "border-radius: 4px; padding: 2px 6px; } "
                   ".v-B2 .mode-chip[aria-pressed=\"true\"] { box-shadow: none; "
                   "background: var(--accent-soft); }")))
    body.append(variant(
        "B3", "B2 + 冷白纸面 + 收紧阴影",
        "在 B2 基础上把浅色纸面从暖白 #fbfaf7 换成冷白 #fcfcfd，并减少卡片阴影层级，"
        "让界面更像“阅读器”而不是“卡片墙”。",
        shell(base, cls="v-cool"),
        extra_css=(".v-B3 .line.is-hit { box-shadow: none; background: var(--accent-soft); } "
                   ".v-B3 .src-item__hit { border-left: 0; background: var(--mark); "
                   "border-radius: 4px; padding: 2px 6px; } "
                   ".v-B3 .mode-chip[aria-pressed=\"true\"] { box-shadow: none; "
                   "background: var(--accent-soft); } "
                   ".v-B3 { --paper: #fcfcfd; } .v-B3 .stage { background: #fcfcfd; } "
                   ".v-B3 .widget { box-shadow: none; }")))

    body.append('</div>')
    body.append('<h2>取舍与代价</h2>')
    body.append(table(["方案", "好处", "代价", "验收层面要动什么"], [
        ["B1", "零风险", "被点名的“AI 标志”仍在", "无"],
        ["B2", "去掉 AI 味；命中指示更柔", "淡底在深色模式下要重新算对比度；左栏命中可辨识度略降，"
                                          "需要靠圆点/图标补回", "改 4 处 CSS + 深色模式各算一遍对比度；"
                                                              "像素亮度基线要重取"],
        ["B3", "最像“专业阅读器”", "暖白带来的“纸感”会丢；卡片层级变平后，组件之间的边界要靠间距撑，"
                                  "间距体系要一起调", "同上 + 改 <code>--paper</code> 与阴影令牌"],
    ]))
    body.append('<div class="verdict"><b>我的建议：B2（先不动纸面颜色）。</b>'
                '"边缘色条是 AI 标志"这条来自一手 skill，值得信；但"暖白纸面"是 mentor 现有的性格，'
                '而 Anthropic 那条规则是为 PPT 写的（投影环境与屏幕阅读不是一回事）。'
                '所以先做 B2 去掉条纹，纸面颜色留到你看过真实渲染再定。</div>')
    body.append(how_to_choose())
    body.append(NAV)
    return page("决策 B · 视觉语言", "".join(body))


# --------------------------------------------------------------------------
# C. 结构
# --------------------------------------------------------------------------
def build_c() -> str:
    body = ['<p class="kicker">决策 C</p><h1>结构：正文与组件的优先级 / aside 默认收起 / 窄屏两栏</h1>',
            '<p class="lede">这是三个 P4 结构性问题的合并决策页。它们都改变"一眼看到什么"，'
            '所以按规则先出页、不擅改。</p>',
            '<ul class="measured">',
            '<li><b>现状</b><br>组件卡平权：回答、概念图、原文、闪卡、自测都是同样的卡片，一次全展开</li>',
            '<li><b>实测同屏量</b><br>1440×900 下首屏能看到约 2 个组件（回答 + 概念图）</li>',
            '<li><b>窄屏现状</b><br>≤620px 时左右栏变抽屉，画布仍是单列</li>',
            '</ul>',
            '<h2>三个方案</h2><div class="variants">']

    all_widgets = (answer_widget(PARAS) + concept_map_widget() + quote_widget()
                   + flashcards_widget() + quiz_widget() + steps_widget())
    body.append(variant("C1", "现状：组件平权，全部展开",
                        "所有组件同样大小、同样展开。代价：一屏塞不下，读者要滚很久才看到结论之外的东西。",
                        shell(f'<div class="d-canvas"><div class="canvas__inner">{all_widgets}</div></div>'
                              + composer(), cls="v-current")))
    body.append(variant(
        "C2", "正文优先：回答占主列，aside 默认收起",
        "回答与原文引用占主列宽度；闪卡/自测/追问这类 aside 收成一个“标题行”再展开。"
        "代价：想看闪卡的人多一次点击；折叠状态要能被解析/分享（URL 或状态里记住）。",
        shell(f'<div class="d-canvas"><div class="canvas__inner">{all_widgets}</div></div>' + composer(),
              cls="v-aside"),
        extra_css=(".v-C2 .widget--aside .widget__body { display: none; } "
                   ".v-C2 .widget--aside { border-style: dashed; } "
                   ".v-C2 .widget--aside .widget__title::after { content: ' · 已收起'; "
                   "color: var(--ink-2); }")))
    body.append(variant(
        "C3", "窄屏两栏：左栏变顶栏，画布与原文切换看",
        "窄屏下不再有左右抽屉，而是“画布 / 原文”两个标签页；来源列表收进顶栏的一个按钮。"
        "代价：窄屏引用跳转后要显式切到“原文”页；验收脚本的窄屏断言要重写。",
        narrow_shell(f'<div class="d-canvas"><div class="canvas__inner">{answer_widget(PARAS)}'
                     f'{quote_widget()}</div></div>' + composer())))

    body.append('</div>')
    body.append('<h2>取舍与代价</h2>')
    body.append(table(["方案", "好处", "代价", "验收层面要动什么"], [
        ["C1", "不改", "首屏信息密度低、要滚很久", "无"],
        ["C2", "一眼看到结论；aside 不抢注意力", "多一次点击；折叠状态需要持久化",
         "改 <code>surface.ensure_minimum</code> 之外的默认展开策略 + widgets.js 初始态；"
         "新增断言：aside 类默认收起、点击可展开、状态可恢复"],
        ["C3", "窄屏信息更完整，不再“抽屉里藏东西”", "引用跳转的上下文切换；抽屉相关的既有断言要重写",
         "改 <code>accept_ui.py</code> 的窄屏检查 + 抽屉断言"],
    ]))
    body.append('<div class="verdict"><b>我的建议：C2 + C3（分两次做）。</b>'
                'C2 直接解决"首屏看不到结论"；C3 影响的是窄屏这一整条交互路径，'
                '建议在 C2 稳定后再动，否则一次改两层，出问题难定位。</div>')
    body.append(how_to_choose())
    body.append(NAV)
    return page("决策 C · 结构", "".join(body))


# --------------------------------------------------------------------------
# 索引
# --------------------------------------------------------------------------
def build_index() -> str:
    body = ['<p class="kicker">mentor · 审美决策</p><h1>三个决策，请你在页面上选</h1>',
            '<p class="lede">这些都是"改了会看得见"的选择，所以按约定先出页、不动代码。'
            '页里的每个方案都用 mentor **真实的 CSS 令牌与组件类**渲染（不是示意图），'
            '所以你在页上看到的就是实现后的样子（同一屏宽下）。</p>',
            table(["决策", "在选什么", "依据", "打开"], [
                ["<b>A</b> 排版", "阅读列宽 / 段距 / 字号阶梯", "WCAG 1.4.8 实测 + Anthropic pptx skill 的排版原则",
                 '<a href="a-typography.html">a-typography.html</a>'],
                ["<b>B</b> 视觉语言", "4 处边缘色条去留 / 纸面底色", "Anthropic pptx skill："
                                                                  '"边缘色条是 AI 生成的标志"',
                 '<a href="b-visual-language.html">b-visual-language.html</a>'],
                ["<b>C</b> 结构", "正文优先级 / aside 默认收起 / 窄屏两栏", "P4 三项结构性方向（此前一直等你拍板）",
                 '<a href="c-structure.html">c-structure.html</a>'],
            ]),
            '<h2>怎么回我</h2>',
            '<div class="how">直接把选择回给我即可，例如 <code>A3 / B2 / C2</code>；'
            '带条件也行（"B2，但条纹只保留命中行"、"A2 但要先看真实效果"）。'
            '你给完，我按选择改代码 + 同步验收断言，并把结论回写进 <code>docs/overnight-log.md</code>。</div>',
            '<h2>这些建议是哪来的</h2>',
            '<p class="muted" style="font-size:13.5px">出处与"借什么/代价"都写在 '
            '<code>docs/prior-art.md</code>：'
            '<a href="https://raw.githubusercontent.com/anthropics/skills/main/skills/pptx/SKILL.md" target="_blank" rel="noopener noreferrer">Anthropic pptx skill</a>（设计判断一节）、'
            '<a href="https://www.w3.org/WAI/WCAG21/Understanding/visual-presentation.html" target="_blank" rel="noopener noreferrer">W3C WCAG 1.4.8</a>、'
            '<a href="https://drafts.csswg.org/css-scroll-anchoring/" target="_blank" rel="noopener noreferrer">W3C CSS Scroll Anchoring</a>、'
            '本机 <code>dsh-reading-stability/docs/prior-art.md</code> 与 <code>dsh-replica</code>。</p>',
            NAV]
    return page("mentor · 审美决策索引", "".join(body))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    files = {
        "index.html": build_index(),
        "a-typography.html": build_a(),
        "b-visual-language.html": build_b(),
        "c-structure.html": build_c(),
    }
    for name, text in files.items():
        (OUT / name).write_text(text, encoding="utf-8")
    total = sum(len(t) for t in files.values())
    print(f"已生成 {len(files)} 个决策页到 {OUT}（共 {total / 1024:.0f} KB）")
    for name in files:
        print("  -", name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
