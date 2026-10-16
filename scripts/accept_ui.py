"""界面验收：真实后端 + 无头浏览器走完整主路径，并留下可复查的证据。

    python scripts/accept_ui.py [--port 8484] [--root D:\\CS336]

为什么用 DevTools 协议而不是 `browser_probe.py`：探针按**虚拟时间**取快照，
而我们要验的是 SSE 流式渲染（plan → retrieval → widget* → surface → done）——
虚拟时间预算一到就截图，流还没推完。这里用真实时钟等待，顺便能读真实 DOM。

三类证据，互不替代：
1. **交互**：`ui/self-test.html` 用真实事件走"选模式 → 输入 → 发送 → 等组件 → 点引用"，
   结果写进页面 DOM，由脚本按真实 DOM 复核（不只看页面自述）。
2. **DOM 断言**：组件数、引用 chip 数、右栏是否高亮、横向是否溢出。
3. **截图**：1440×900 与 390×844 各一张，落在 `runs/ui/`。
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
SKILL_SCRIPTS = Path(r"C:\Users\Henry_1\.dsh\skills\webapp-testing\scripts")
sys.path.insert(0, str(SKILL_SCRIPTS))

from mentor.server import MentorApp, ui_dir  # noqa: E402
from mentor.store import Store  # noqa: E402

try:
    import cdp  # type: ignore  # 技能自带的最小 CDP 客户端
except ImportError as exc:  # pragma: no cover
    raise SystemExit(f"需要 webapp-testing 技能的 scripts/cdp.py：{exc}")

INTERACTIVE_TIMEOUT = 90.0


def start_server(app: MentorApp, port: int) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), app.handler())
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _log_lines(chrome: "cdp.Chrome", sid: str) -> list[str]:
    text = chrome.evaluate(
        sid, "document.getElementById('selftest-log')?.textContent || ''") or ""
    return [line.strip() for line in text.splitlines() if line.strip()]


def check_index(chrome: "cdp.Chrome", sid: str, label: str) -> list[str]:
    problems: list[str] = []
    data = chrome.evaluate(sid, """(() => ({
        input: !!document.getElementById('composer-input'),
        leftRail: !!document.querySelector('.rail--left'),
        rightRail: !!document.querySelector('.rail--right'),
        modes: document.querySelectorAll('.composer .mode-chip').length,
        sources: document.querySelectorAll('.src-item').length,
        emptyState: document.querySelectorAll('#canvas [data-role=empty]').length,
        emptyHints: document.querySelectorAll('#canvas .empty-state__hints .mode-chip').length,
        emptyCentered: (() => {
          const card = document.querySelector('#canvas [data-role=empty]');
          const host = document.querySelector('#canvas-inner') || document.querySelector('.canvas');
          if (!card || !host) return null;
          const c = card.getBoundingClientRect();
          const h = host.getBoundingClientRect();
          const visibleH = Math.min(h.height, window.innerHeight - 40);
          const cardCenter = c.top + c.height / 2 - h.top;
          return Math.abs(cardCenter - visibleH / 2) < Math.max(120, visibleH * 0.22);
        })(),
        lineInputEmpty: (() => {
          const el = document.getElementById('line-input');
          return el ? el.value === '' : null;
        })(),
        lineInputPlaceholder: (document.getElementById('line-input') || {}).placeholder || '',
        overflow: document.documentElement.scrollWidth > window.innerWidth + 1,
        docWidth: document.documentElement.scrollWidth,
        viewWidth: window.innerWidth,
        text: (document.body.innerText || '').slice(0, 4000),
    }))()""") or {}
    print(f"\n== {label}（静态首页）==")
    print(f"  输入框 {data.get('input')}｜模式 chip {data.get('modes')}｜"
          f"来源项 {data.get('sources')}｜空状态 {data.get('emptyState')}"
          f"（示例 {data.get('emptyHints')} 个）｜文档宽 {data.get('docWidth')}/{data.get('viewWidth')}")
    if not data.get("input"):
        problems.append(f"{label}: 没有渲染出输入框")
    if not (data.get("leftRail") and data.get("rightRail")):
        problems.append(f"{label}: 三栏结构缺失")
    if (data.get("modes") or 0) < 5:
        problems.append(f"{label}: 模式 chip 只有 {data.get('modes')} 个")
    if not data.get("emptyState"):
        problems.append(f"{label}: 首页画布是空白的（没有空状态引导）")
    elif (data.get("emptyHints") or 0) < 2:
        problems.append(f"{label}: 空状态没有给可点的示例问题")
    if data.get("emptyCentered") is False:
        problems.append(f"{label}: 空状态卡片没有在画布可视区居中（重心压在底部）")
    if data.get("lineInputEmpty") is False:
        problems.append(f"{label}: 未打开原文时「跳到行」预填了 {data.get('lineInputPlaceholder')!r}，"
                        "会让人以为已经定位过")
    if data.get("overflow"):
        problems.append(f"{label}: 横向溢出 {data.get('docWidth')} > {data.get('viewWidth')}")
    for needle in ("资料来源", "原文对照"):
        if needle not in (data.get("text") or ""):
            problems.append(f"{label}: 界面文字里缺少「{needle}」")
    return problems


def check_interactive(chrome: "cdp.Chrome", sid: str, label: str) -> list[str]:
    problems: list[str] = []
    print(f"\n== {label}（交互主路径）==")
    deadline = time.time() + INTERACTIVE_TIMEOUT
    lines: list[str] = []
    while time.time() < deadline:
        lines = _log_lines(chrome, sid)
        if any(line.startswith("SELFTEST-DONE") for line in lines):
            break
        time.sleep(0.5)
    for line in lines:
        if line.startswith("SELFTEST-OK"):
            print("  ✓ " + line[len("SELFTEST-OK"):].strip())
        elif line.startswith("SELFTEST-FAIL"):
            print("  ✗ " + line[len("SELFTEST-FAIL"):].strip())
            problems.append(f"{label}: {line[len('SELFTEST-FAIL'):].strip()}")
    if not any(line.startswith("SELFTEST-DONE") for line in lines):
        problems.append(f"{label}: 自检 {INTERACTIVE_TIMEOUT:.0f}s 内没有跑完")

    # 不信页面自述：直接量真实 DOM
    dom = chrome.evaluate(sid, """(() => ({
        widgets: document.querySelectorAll('#canvas article.widget:not(.widget--skeleton)').length,
        types: [...new Set([...document.querySelectorAll('#canvas article.widget:not(.widget--skeleton)')]
                 .map(w => w.dataset.widgetType))],
        chips: document.querySelectorAll('.cite-chip[data-cite-n]').length,
        termHits: document.querySelectorAll('.line .term-hit').length,
        railTermHits: document.querySelectorAll('.src-item__hit .term-hit').length,
        hitLines: document.querySelectorAll('.line.is-hit').length,
        readerLines: document.querySelectorAll('.line').length,
        emptyState: document.querySelectorAll('#canvas [data-role=empty]').length,
        conceptStates: [...new Set([...document.querySelectorAll('[data-widget-type="concept_map"] [data-state]')]
                 .map((n) => n.dataset.state))],
        dashedReadyNodes: [...document.querySelectorAll('[data-widget-type="concept_map"] [data-state="ready"]')]
                 .filter((n) => !['solid', 'none'].includes(getComputedStyle(n).borderStyle)).length,
        panelToggles: [...document.querySelectorAll('[data-toggle]')].map((n) => ({
          side: n.dataset.toggle,
          pressed: n.getAttribute('aria-pressed'),
          label: (n.getAttribute('aria-label') || n.getAttribute('title') || '').trim(),
        })),
        overflow: document.documentElement.scrollWidth > window.innerWidth + 1,
    }))()""") or {}
    print(f"  · DOM 实测：组件 {dom.get('widgets')} 个（{'/'.join(dom.get('types') or [])}）｜"
          f"引用 chip {dom.get('chips')}｜右栏行 {dom.get('readerLines')}（高亮 {dom.get('hitLines')}）")
    if (dom.get("widgets") or 0) < 3:
        problems.append(f"{label}: 画布上只有 {dom.get('widgets')} 个真实组件")
    if not (dom.get("types") and "answer" in dom["types"]):
        problems.append(f"{label}: 画布上没有 answer 组件")
    if (dom.get("chips") or 0) < 1:
        problems.append(f"{label}: 正文里没有可点的引用 chip")
    if (dom.get("hitLines") or 0) < 1:
        problems.append(f"{label}: 点引用后右栏没有高亮行")
    if dom.get("emptyState"):
        problems.append(f"{label}: 提问后空状态没有退场（仍挂在正文上方）")
    if dom.get("conceptStates") and any(s != "ready" for s in dom["conceptStates"]):
        problems.append(f"{label}: 概念图节点仍停在非终态 {dom['conceptStates']}（虚线/加载态没收尾）")
    if dom.get("dashedReadyNodes"):
        problems.append(f"{label}: 概念图有 {dom['dashedReadyNodes']} 个 ready 节点还是虚线（动画终点状态不一致）")
    for toggle in dom.get("panelToggles") or []:
        if not toggle.get("label"):
            problems.append(f"{label}: 面板开关 {toggle.get('side')} 没有 aria-label/title")
        if toggle.get("pressed") not in ("true", "false"):
            problems.append(f"{label}: 面板开关 {toggle.get('side')} 没有 aria-pressed 状态")
    if dom.get("overflow"):
        problems.append(f"{label}: 横向溢出")
    return problems


def prepare_store(store: Store, root: str) -> dict[str, str | None]:
    """验收用自己的空笔记本：没有历史轮次，首页空状态才可复现。

    返回 `{"lecture_nb": <id>}`：交互自检要用**指定的**讲义笔记本打开，
    不能让它靠 `notebooks[0]`（`list_notebooks` 按只到秒的 `created_at` 排序，
    `mentor/store.py:105`；跨秒时后建的排第一，实测同一份代码两次运行顺序不同）。
    """
    if store.list_notebooks():
        return {"lecture_nb": None}
    from mentor.ingest import ingest_paths
    from mentor.targets import expand

    notebook = store.create_notebook("验收笔记本")
    targets = expand([
        "lectures/lecture_01.py", "lectures/lecture_06.py", "docs/setup-notes.md",
    ], root=root)
    if not targets:
        print("警告：在 %s 下没找到验收材料，引用相关断言会失败" % root)
        return {"lecture_nb": None}
    report = ingest_paths(targets, root=root)
    store.attach(notebook.id, report.sources, report.chunks)
    print(f"验收笔记本：{notebook.id}（{len(report.sources)} 份材料、{len(report.chunks)} 个片段）")

    # PDF 单独一个笔记本：真实 PDF 的检索构成与讲义不同，混在一起会让既有断言变得脆弱
    pdf_notebook = store.create_notebook("PDF 验收笔记本")
    pdf_targets = expand(["assignments/assignment1-basics/cs336_assignment1_basics.pdf"], root=root)
    if not pdf_targets:
        print("提示：找不到验收用 PDF，`--case pdf` 的相关断言会跳过")
        return {"lecture_nb": notebook.id}
    pdf_report = ingest_paths(pdf_targets, root=root)
    store.attach(pdf_notebook.id, pdf_report.sources, pdf_report.chunks)
    print(f"PDF 验收笔记本：{pdf_notebook.id}（{len(pdf_report.sources)} 份材料、"
          f"{len(pdf_report.chunks)} 个片段）")
    return {"lecture_nb": notebook.id}


def check_pdf(chrome: "cdp.Chrome", base: str, label: str,
              shot: Path | None = None) -> list[str]:
    """PDF 材料的产品级验收：能不能选到、能不能答、引用能不能跳、右栏显示的是不是正文。

    由来：PDF 摄取曾经 100% 失败（surrogates not allowed），修完数据层还得证明**界面这一层**
    真的能用——尤其是右栏不能把二进制载荷当原文渲染。
    """
    problems: list[str] = []
    sid = chrome.new_page(1440, 1000)
    chrome.goto(sid, base + "/")
    for _ in range(40):
        if chrome.evaluate(sid, "document.querySelectorAll('#nb-list .nb-item').length"):
            break
        time.sleep(0.25)

    picked = chrome.evaluate(sid, """(() => {
      const items = [...document.querySelectorAll('#nb-list .nb-item')];
      const pdf = items.find((n) => n.textContent.includes('PDF'));
      if (!pdf) return { ok: false, items: items.map((n) => n.textContent.trim().slice(0, 20)) };
      pdf.click();
      return { ok: true };
    })()""") or {}
    if not picked.get("ok"):
        problems.append(f"{label}: 左栏没有 PDF 笔记本（{picked.get('items')}）")
        return problems

    # 等来源列表里出现 .pdf
    listed = False
    for _ in range(40):
        listed = bool(chrome.evaluate(sid, """[...document.querySelectorAll('.src-item')]
            .some((n) => n.textContent.toLowerCase().includes('.pdf'))"""))
        if listed:
            break
        time.sleep(0.25)
    if not listed:
        problems.append(f"{label}: 左栏没有列出 PDF 材料")
        return problems

    # 提问（用英文词，PDF 正文是英文）
    chrome.evaluate(sid, """(() => {
      const input = document.getElementById('composer-input');
      input.value = 'BPE tokenizer';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      document.getElementById('composer').dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }));
      return true;
    })()""")
    for _ in range(120):
        if chrome.evaluate(sid, "document.querySelectorAll('.widget--skeleton').length") == 0 \
                and chrome.evaluate(sid, "document.querySelectorAll('.widget').length"):
            break
        time.sleep(0.25)
    time.sleep(0.8)

    data = chrome.evaluate(sid, """({
      widgets: document.querySelectorAll('.widget:not(.widget--skeleton)').length,
      chips: document.querySelectorAll('.cite-chip[data-cite-n]').length,
      pdfChips: [...document.querySelectorAll('.cite-chip[data-cite-locator]')]
        .filter((n) => (n.dataset.citeLocator || '').toLowerCase().includes('.pdf')).length,
      locator: (document.querySelector('.cite-chip[data-cite-locator]') || {}).dataset?.citeLocator || '',
    })""") or {}
    print(f"\n== {label} PDF 材料端到端 ==")
    print(f"  组件 {data.get('widgets')}｜引用 chip {data.get('chips')}"
          f"｜其中指向 PDF 的 {data.get('pdfChips')}｜示例定位 {data.get('locator')}")
    if not data.get("widgets"):
        problems.append(f"{label}: 对 PDF 材料提问没有任何组件产出")
    if not data.get("pdfChips"):
        problems.append(f"{label}: 引用里没有指向 PDF 的定位（{data.get('locator')}）")

    # 点引用 → 右栏必须显示可读正文（不是二进制载荷）
    if data.get("pdfChips"):
        chrome.evaluate(sid, """(() => {
          const chip = [...document.querySelectorAll('.cite-chip[data-cite-locator]')]
            .find((n) => (n.dataset.citeLocator || '').toLowerCase().includes('.pdf'));
          chip.click();
          return true;
        })()""")
        for _ in range(60):
            if (chrome.evaluate(sid, "document.querySelectorAll('.line').length") or 0) > 5:
                break
            time.sleep(0.25)
        time.sleep(0.5)
        reader = chrome.evaluate(sid, """({
          path: (document.querySelector('.srcdoc__path') || {}).textContent || '',
          lines: document.querySelectorAll('.line').length,
          target: document.querySelectorAll('.line.is-target').length,
          sample: (document.querySelector('#srcdoc-body') || {}).textContent?.slice(0, 160) || '',
          binary: (document.querySelector('#srcdoc-body') || {}).textContent?.includes('%PDF') || false,
        })""") or {}
        print(f"  右栏：{reader.get('lines')} 行｜命中行 {reader.get('target')}"
              f"｜二进制标记 {reader.get('binary')}｜样例 {str(reader.get('sample'))[:60]!r}")
        if ".pdf" not in str(reader.get("path", "")).lower():
            problems.append(f"{label}: 右栏打开的不是 PDF 材料（{reader.get('path')}）")
        if (reader.get("lines") or 0) < 5:
            problems.append(f"{label}: PDF 右栏只有 {reader.get('lines')} 行，原文没渲染出来")
        if reader.get("binary"):
            problems.append(f"{label}: PDF 右栏渲染了二进制载荷（出现了 %PDF）")
        if not reader.get("target"):
            problems.append(f"{label}: 点引用后右栏没有跳转到对应位置")

    # 搜索也要能命中 PDF
    chrome.evaluate(sid, """(() => {
      const s = document.getElementById('search-input');
      s.value = 'BPE';
      s.dispatchEvent(new Event('input', { bubbles: true }));
      return true;
    })()""")
    hits = 0
    for _ in range(40):
        info = chrome.evaluate(sid, """({
          pdf: [...document.querySelectorAll('.src-item')]
            .filter((n) => n.textContent.toLowerCase().includes('.pdf')).length,
          marks: document.querySelectorAll('.src-item__hit .term-hit').length,
        })""") or {}
        hits = info.get("pdf") or 0
        if info.get("marks"):
            break
        time.sleep(0.25)
    print(f"  搜索 BPE：命中卡片含 PDF 的 {hits} 条")
    if not hits:
        problems.append(f"{label}: 搜索 PDF 内容没有结果")
    if shot is not None:
        chrome.screenshot(sid, shot)
    return problems


def verify_theme_pixels(path: Path, theme: str, label: str) -> list[str]:
    """用像素亮度验证截图确实是那个配色。

    CDP 的 media 覆盖、CSS 变量、截图三者任一环节出错，都会让"浅色验收"变成空头支票；
    这里直接量画布区域的平均亮度：浅色应明显偏亮，深色应明显偏暗。
    """
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow 缺失时只能跳过
        print(f"  （跳过像素校验：没有 Pillow）")
        return []
    with Image.open(path) as img:
        gray = img.convert("L")
        width, height = gray.size
        box = gray.crop((int(width * 0.30), int(height * 0.10),
                         int(width * 0.45), int(height * 0.25)))
        pixels = list(box.getdata())
    average = sum(pixels) / max(1, len(pixels))
    print(f"  · {label} 画布区平均亮度 {average:.0f}（{theme}）")
    if theme == "light" and average < 150:
        return [f"{label}: 声明浅色但截图偏暗（平均亮度 {average:.0f}），配色没真正生效"]
    if theme == "dark" and average > 90:
        return [f"{label}: 声明深色但截图偏亮（平均亮度 {average:.0f}），配色没真正生效"]
    return []


def set_theme(chrome: "cdp.Chrome", sid: str, theme: str) -> None:
    """让页面按指定配色方案渲染（浅色/深色各验收一轮）。"""
    chrome.call("Emulation.setEmulatedMedia", {
        "features": [{"name": "prefers-color-scheme", "value": theme}],
    }, sid)


CONTRAST_JS = """(() => {
  const parse = (value) => {
    const m = String(value).match(/rgba?\\(([^)]+)\\)/);
    if (!m) return null;
    const parts = m[1].split(',').map((v) => parseFloat(v));
    return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 };
  };
  const over = (fg, bg) => ({
    r: fg.r * fg.a + bg.r * (1 - fg.a),
    g: fg.g * fg.a + bg.g * (1 - fg.a),
    b: fg.b * fg.a + bg.b * (1 - fg.a), a: 1,
  });
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  const ratio = (a, b) => {
    const l1 = lum(a), l2 = lum(b);
    const hi = Math.max(l1, l2), lo = Math.min(l1, l2);
    return (hi + 0.05) / (lo + 0.05);
  };
  const effectiveBg = (node) => {
    let cur = node;
    let acc = null;
    while (cur && cur !== document.documentElement.parentNode) {
      const bg = parse(getComputedStyle(cur).backgroundColor);
      if (bg && bg.a > 0) acc = acc ? over(acc, bg) : bg;
      if (acc && acc.a >= 1) return acc;
      cur = cur.parentElement;
    }
    return acc || { r: 255, g: 255, b: 255, a: 1 };
  };
  const targets = [
    ['正文段落', '.widget__body p'],
    ['要点列表', '.widget__body li'],
    ['原文代码行', '.line__text'],
    ['右栏行号', '.line__n'],
    ['左栏来源名', '.src-item__name'],
    ['左栏命中片段', '.src-item__hit'],
    ['输入区提示', '.composer__hint'],
    ['顶栏胶囊', '.pill'],
    ['引用 chip', '.cite-chip'],
    ['空状态正文', '.empty-state__text'],
    ['概念图节点字', '.cmap__node-label'],
    ['概念图边标签', '.cmap__edge-label'],
  ];
  const out = [];
  targets.forEach(([name, sel]) => {
    const node = document.querySelector(sel);
    if (!node || !node.getClientRects().length) return;
    const style = getComputedStyle(node);
    const fg = parse(style.color);
    if (!fg) return;
    const bg = effectiveBg(node);
    const size = parseFloat(style.fontSize) || 16;
    const bold = (parseInt(style.fontWeight, 10) || 400) >= 700;
    const large = size >= 24 || (bold && size >= 18.66);
    const value = ratio(over(fg, bg), bg);
    out.push({ name, sel, ratio: Math.round(value * 100) / 100, size, need: large ? 3 : 4.5 });
  });
  const bodyBg = parse(getComputedStyle(document.body).backgroundColor);
  return { pairs: out, background: bodyBg, scheme: window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light' };
})()"""


def contrast_audit(chrome: "cdp.Chrome", sid: str, label: str) -> list[str]:
    """程序化算 WCAG 对比度（不靠肉眼，也不靠模型）。"""
    data = chrome.evaluate(sid, CONTRAST_JS) or {}
    pairs = data.get("pairs") or []
    problems: list[str] = []
    print(f"\n== {label} 对比度（{data.get('scheme')} 配色）==")
    for item in pairs:
        mark = "✓" if item["ratio"] >= item["need"] else "✗"
        print(f"  {mark} {item['name']:<10} {item['ratio']:>5}:1（需 {item['need']}，{item['size']}px）")
        if item["ratio"] < item["need"]:
            problems.append(f"{label}: {item['name']} 对比度 {item['ratio']}:1 < {item['need']}"
                            f"（{item['sel']}，{item['size']}px）")
    if not pairs:
        problems.append(f"{label}: 对比度审计没采到任何可见元素")
    return problems


HOSTILE_JS = """(() => ({
  pwned: Boolean(window.__PWNED__),
  errors: (window.__ERRORS__ || []).slice(0, 3),
  widgets: document.querySelectorAll('.widget:not(.widget--skeleton)').length,
  skeletons: document.querySelectorAll('.widget--skeleton').length,
  imgX: document.querySelectorAll('img[src="x"]').length,
  scriptTags: document.querySelectorAll('script').length,
  jsHrefs: [...document.querySelectorAll('a[href]')]
    .filter((a) => /^\\s*(javascript|vbscript|data):/i.test(a.getAttribute('href') || '')).length,
  blockedLinks: document.querySelectorAll('.md-link--blocked').length,
  safeLinks: [...document.querySelectorAll('a.md-link[href]')]
    .filter((a) => /^(\\.\\/|#|https?:)/.test(a.getAttribute('href') || '')).length,
  literalScriptAsText: document.body.textContent.includes('<script>window.__PWNED__'),
  literalImgAsText: document.body.textContent.includes('<img src=x onerror='),
  overflow: document.documentElement.scrollWidth - window.innerWidth,
}))()"""


def check_hostile(chrome: "cdp.Chrome", base: str, label: str,
                  shot: Path | None = None) -> list[str]:
    """敌意载荷（ui/hostile.html）：注入不许执行、坏组件不许崩、字符串必须以文本出现。

    载荷是"模型/上游可能吐出来的坏东西"：script 标签、img onerror、`javascript:` 链接、
    错类型、缺字段、未知组件类型、80k 巨串、双向覆盖字符、空字符、77 个组件。
    由来：对抗性验证时发现 markdown 渲染器会把 `[x](javascript:...)` 原样塞进 href。
    """
    problems: list[str] = []
    sid = chrome.new_page(1280, 900)
    chrome.goto(sid, base + "/hostile")
    # 预览页先把每个组件的骨架铺出来，所以"有 .widget"不等于渲染完成：
    # 必须等①壳子挂载（#composer 出现）②点"发送"回放③出现非 skeleton 的真组件。
    for _ in range(60):
        if chrome.evaluate(sid, "Boolean(document.getElementById('composer'))"):
            break
        time.sleep(0.25)
    chrome.evaluate(sid, """(() => {
      const input = document.getElementById('composer-input');
      if (input) { input.value = '回放'; input.dispatchEvent(new Event('input', { bubbles: true })); }
      const form = document.getElementById('composer');
      if (form) form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
      return true;
    })()""")
    # 预览回放是**流式**的（逐个组件到达），骨架清零才代表这一轮结束
    for _ in range(120):
        pending = chrome.evaluate(sid, "document.querySelectorAll('.widget--skeleton').length") or 0
        if pending == 0:
            break
        time.sleep(0.25)
    time.sleep(0.4)
    data = chrome.evaluate(sid, HOSTILE_JS) or {}
    print(f"\n== {label} 敌意载荷 ==")
    print(f"  组件 {data.get('widgets')}｜script 标签 {data.get('scriptTags')}"
          f"｜img[src=x] {data.get('imgX')}｜危险协议链接 {data.get('jsHrefs')}"
          f"｜被拦截链接 {data.get('blockedLinks')}｜正常链接 {data.get('safeLinks')}"
          f"｜溢出 {data.get('overflow')}")
    if data.get("pwned"):
        problems.append(f"{label}: 敌意载荷里的脚本被执行了（window.__PWNED__ 被置位）")
    if data.get("errors"):
        problems.append(f"{label}: 敌意载荷下出现未捕获错误 {data['errors']}")
    if (data.get("widgets") or 0) < 20:
        problems.append(f"{label}: 恶意载荷下只渲染出 {data.get('widgets')} 个组件，渲染被中止")
    if data.get("imgX"):
        problems.append(f"{label}: 载荷里的 <img onerror> 变成了真的元素（{data['imgX']} 个）")
    if data.get("jsHrefs"):
        problems.append(f"{label}: 出现了 {data['jsHrefs']} 个 javascript:/data:/vbscript: 链接")
    if (data.get("blockedLinks") or 0) < 3:
        problems.append(f"{label}: 危险协议链接没有被拦截并降级成纯文本"
                        f"（blockedLinks={data.get('blockedLinks')}）")
    if (data.get("safeLinks") or 0) < 3:
        problems.append(f"{label}: 正常链接（相对/https/锚点）被误伤"
                        f"（safeLinks={data.get('safeLinks')}）")
    if (data.get("scriptTags") or 0) > 8:
        problems.append(f"{label}: script 标签涨到 {data['scriptTags']} 个，说明字符串被当成标记解析了")
    if not data.get("literalScriptAsText"):
        problems.append(f"{label}: 恶意字符串没有以文本形式显示")
    if data.get("overflow"):
        problems.append(f"{label}: 敌意载荷导致横向溢出 {data['overflow']}px")
    if shot is not None:
        chrome.screenshot(sid, shot)
    return problems


DECISIONS_JS = """(() => {
  const widths = [...document.querySelectorAll('.d-canvas .canvas__inner')]
    .map((n) => Math.round(n.getBoundingClientRect().width));
  const cs = (sel, prop) => { const n = document.querySelector(sel); return n ? getComputedStyle(n)[prop] : null; };
  const frames = [...document.querySelectorAll('.d-shell')].map((n) => Math.round(n.getBoundingClientRect().width));
  return {
    title: document.title,
    variants: document.querySelectorAll('.variant').length,
    errors: (window.__ERRORS__ || []).slice(0, 3),
    overflow: document.documentElement.scrollWidth - window.innerWidth,
    widths, frames,
    hitMarkNow: (() => { const n = document.querySelector('.v-current .line.is-hit'); return n ? getComputedStyle(n).boxShadow : null; })(),
    hitMarkNew: (() => { const n = document.querySelector('.v-B2 .line.is-hit, .v-nostripe .line.is-hit, .v-cool .line.is-hit'); return n ? getComputedStyle(n).boxShadow : null; })(),
    stripeNow: cs('.v-current .src-item__hit', 'borderLeftWidth'),
    stripeNew: cs('.v-B2 .src-item__hit, .v-nostripe .src-item__hit', 'borderLeftWidth'),
    asideNow: cs('.v-current .widget--aside .widget__body', 'display'),
    asideNew: cs('.v-C2 .widget--aside .widget__body, .v-aside .widget--aside .widget__body', 'display'),
    stylesLoaded: getComputedStyle(document.body).backgroundColor,
  };
})()"""


def check_decisions(chrome: "cdp.Chrome", base: str, label: str,
                    shots: Path | None = None) -> list[str]:
    """审美决策页自身的验收：页面能用、且**变体是真的不同**（不是画出来的对比）。

    为什么要专门验它：决策页是"用户据此拍板"的输入。如果几个变体在浏览器里渲染成同一个样子，
    用户就是在对着无效信息做决定 —— 那不是观感问题，是正确性问题。
    踩过的坑：`variant()` 拼 CSS 时少一个 `{`，让后一条规则变成全局选择器，
    把 B2 的"去条纹"泄漏给了所有变体（当时看页面文字完全看不出来）。
    """
    problems: list[str] = []
    pages = ("index", "a-typography", "b-visual-language", "c-structure")
    print(f"\n== {label} 审美决策页 ==")
    for name in pages:
        sid = chrome.new_page(1440, 1000)
        chrome.goto(sid, f"{base}/docs/decisions/{name}.html")
        time.sleep(0.7)
        data = chrome.evaluate(sid, DECISIONS_JS) or {}
        tag = f"decisions/{name}"
        print(f"  {name}: 变体 {data.get('variants')}｜横向溢出 {data.get('overflow')}"
              f"｜列宽 {data.get('widths')}｜窗口 {data.get('frames')}"
              f"｜命中竖标 现在={data.get('hitMarkNow')} 新={data.get('hitMarkNew')}")
        if not str(data.get("title", "")).startswith(("决策", "mentor")):
            problems.append(f"{tag}: 页面标题不对（{data.get('title')}），可能没加载成功")
        if data.get("errors"):
            problems.append(f"{tag}: 页面有未捕获错误 {data['errors']}")
        if data.get("overflow"):
            problems.append(f"{tag}: 页面横向溢出 {data['overflow']}px")
        if name != "index" and (data.get("variants") or 0) < 2:
            problems.append(f"{tag}: 只有 {data.get('variants')} 个变体，没法做选择")

        # 变体必须真的不同 —— 每个决策页验它自己的关键差异
        widths = data.get("widths") or []
        if name == "a-typography":
            if len(set(widths)) < 3:
                problems.append(f"{tag}: 三个方案的正文列宽没有区别（{widths}），对比是假的")
        if name == "b-visual-language":
            if not data.get("hitMarkNow") or data["hitMarkNow"] == "none":
                problems.append(f"{tag}: 「现在」的命中竖标没渲染出来（{data.get('hitMarkNow')}）")
            if data.get("hitMarkNew") not in (None, "none"):
                problems.append(f"{tag}: 「去条纹」方案仍有竖标（{data.get('hitMarkNew')}）")
            if data.get("stripeNew") not in (None, "0px"):
                problems.append(f"{tag}: 「去条纹」方案的左栏片段仍有 {data.get('stripeNew')} 竖线")
        if name == "c-structure":
            if data.get("asideNow") == data.get("asideNew"):
                problems.append(f"{tag}: aside 收起的方案和现状一样（{data.get('asideNow')}）")
            if 420 not in (data.get("frames") or []):
                problems.append(f"{tag}: 窄屏方案没有用 420px 的窄窗口（{data.get('frames')}）")
        if shots is not None:
            chrome.screenshot(sid, shots / f"decision-{name}.png")
    return problems


def style_evidence(chrome: "cdp.Chrome", sid: str, label: str) -> list[str]:
    """把"模型看图说的问题"落到计算样式上：模型说没修，就量给它看。

    这些断言对应外部评审的条目，避免"看着像没改"与"确实没改"混在一起。
    """
    data = chrome.evaluate(sid, """(() => {
      const px = (n, prop) => {
        if (!n) return null;
        const v = getComputedStyle(n)[prop];
        return v;
      };
      const hit = document.querySelector('.src-item__hit');
      const edge = document.querySelector('.cmap__edge-label');
      const cmapGroup = document.querySelector('.cmap__group');
      const cites = document.querySelector('.widget__cites .cite-chip');
      const marks = [...document.querySelectorAll('.src-item [class*=hit], .src-item [class*=badge]')];
      const toggle = document.querySelector('[data-toggle]');
      const planned = document.querySelector('.plan-chip[data-state="planned"]');
      const stop = document.querySelector('#stop-btn');
      return {
        hitBorderLeft: hit ? getComputedStyle(hit).borderLeftWidth : null,
        hitBorderColor: hit ? getComputedStyle(hit).borderLeftColor : null,
        hitLineClamp: hit ? (getComputedStyle(hit).webkitLineClamp
                            || getComputedStyle(hit).lineClamp) : null,
        edgeFont: edge ? getComputedStyle(edge).fontSize : null,
        groupFont: cmapGroup ? getComputedStyle(cmapGroup).fontSize : null,
        citeFont: cites ? getComputedStyle(cites).fontSize : null,
        citeHeight: cites ? getComputedStyle(cites).height : null,
        hitBadges: marks.map((n) => ({
          cls: n.className, bg: getComputedStyle(n).backgroundColor,
          color: getComputedStyle(n).color,
        })).slice(0, 4),
        togglePressed: toggle ? toggle.getAttribute('aria-pressed') : null,
        toggleBg: toggle ? getComputedStyle(toggle).backgroundColor : null,
        toggleActive: toggle ? toggle.classList.contains('is-active') : null,
        plannedBorder: planned ? getComputedStyle(planned).borderTopStyle : null,
        plannedBorderColor: planned ? getComputedStyle(planned).borderTopColor : null,
        stopBg: stop ? getComputedStyle(stop).backgroundColor : null,
        // 代码列对齐：行号列宽与正文起点各自必须是唯一值（模型说过"忽宽忽窄"，要能证伪）
        lineNoWidths: [...new Set([...document.querySelectorAll('.line__n')]
          .slice(0, 60).map((n) => Math.round(n.getBoundingClientRect().width)))],
        lineTextLefts: [...new Set([...document.querySelectorAll('.line__text')]
          .slice(0, 60).map((n) => Math.round(n.getBoundingClientRect().left)))],
        hitMarker: (() => {
          const hit = document.querySelector('.line.is-hit');
          return hit ? getComputedStyle(hit).boxShadow : null;
        })(),
        // 顶/底栏降权重的证据：选中 chip 不再填充、键帽去掉边框，但点击目标不许变小
        modeChipHeights: [...document.querySelectorAll('.composer .mode-chip')]
          .map((n) => Math.round(n.getBoundingClientRect().height)),
        activeChipBg: (() => {
          const chip = document.querySelector('.composer .mode-chip[aria-pressed="true"]');
          return chip ? getComputedStyle(chip).backgroundColor : null;
        })(),
        activeChipShadow: (() => {
          const chip = document.querySelector('.composer .mode-chip[aria-pressed="true"]');
          return chip ? getComputedStyle(chip).boxShadow : null;
        })(),
        kbdBorder: (() => {
          const kbd = document.querySelector('.composer__hint kbd');
          return kbd ? getComputedStyle(kbd).borderTopWidth : null;
        })(),
        chipRowGap: (() => {
          const chips = document.querySelector('.mode-chips');
          const row = document.querySelector('.composer__row');
          if (!chips || !row) return null;
          return Math.round(row.getBoundingClientRect().top - chips.getBoundingClientRect().bottom);
        })(),
        // 快捷键行里不该再混状态说明（"一问一答：正文 + 引用 + 来源"这类）
        hintSpansWithoutKbd: [...document.querySelectorAll('.composer__hint > span')]
          .filter((n) => !n.querySelector('kbd')).length,
        modeHintOutsideHints: Boolean(document.querySelector('.composer__modehint#mode-hint'))
          && !document.querySelector('.composer__hint #mode-hint'),
        // 输入区过渡：硬边框应当为 0，改为上方 24px 渐变淡出
        composerBorderTop: (() => {
          const c = document.querySelector('.composer');
          return c ? getComputedStyle(c).borderTopWidth : null;
        })(),
        composerFade: (() => {
          const c = document.querySelector('.composer');
          if (!c) return null;
          const s = getComputedStyle(c, '::before');
          return { image: s.backgroundImage, height: s.height, pointer: s.pointerEvents };
        })(),
        // 窄屏"计划渲染"必须是单行（可横向滚动），不能折成两三行砖墙
        planRows: (() => {
          const chips = [...document.querySelectorAll('.plan-chip')];
          if (!chips.length) return null;
          return new Set(chips.map((c) => Math.round(c.getBoundingClientRect().top))).size;
        })(),
        planGap: (() => {
          const box = document.querySelector('.plan-bar__types');
          return box ? getComputedStyle(box).columnGap : null;
        })(),
        previewPointer: (() => {
          const card = document.getElementById('cite-preview');
          return card ? getComputedStyle(card).pointerEvents : null;
        })(),
      };
    })()""") or {}
    problems: list[str] = []
    print(f"\n== {label} 样式证据 ==")
    print(f"  左栏片段：border-left {data.get('hitBorderLeft')} {data.get('hitBorderColor')}"
          f"｜line-clamp {data.get('hitLineClamp')}")
    print(f"  概念图：边标签 {data.get('edgeFont')}｜分组标题 {data.get('groupFont')}")
    print(f"  引用：底部 chip {data.get('citeFont')}/{data.get('citeHeight')}")
    print(f"  命中徽标：{data.get('hitBadges')}")
    print(f"  面板开关：aria-pressed={data.get('togglePressed')}｜is-active={data.get('toggleActive')}"
          f"｜底色 {data.get('toggleBg')}")
    print(f"  计划 chip（待渲染）：border {data.get('plannedBorder')} {data.get('plannedBorderColor')}"
          f"｜停止按钮底色 {data.get('stopBg')}")

    if data.get("hitBorderLeft") not in (None, "0px"):
        pass
    elif data.get("hitBorderLeft") == "0px":
        problems.append(f"{label}: 左栏命中片段没有引用竖线（border-left = 0）")
    if data.get("hitLineClamp") not in (None, "none"):
        try:
            if int(str(data["hitLineClamp"])) < 2:
                problems.append(f"{label}: 片段截断行数 {data['hitLineClamp']} < 2")
        except ValueError:
            pass
    for name, value in (("边标签", data.get("edgeFont")), ("分组标题", data.get("groupFont")),
                        ("底部引用", data.get("citeFont"))):
        if value and float(str(value).replace("px", "")) < 12:
            problems.append(f"{label}: 概念图/引用区 {name} 字号 {value} < 12px")
    if data.get("togglePressed") == "true" and not data.get("toggleActive"):
        problems.append(f"{label}: 面板开关 aria-pressed=true 但没有 is-active 底色")
    widths = data.get("lineNoWidths") or []
    lefts = data.get("lineTextLefts") or []
    print(f"  代码列对齐：行号列宽 {widths}｜正文起点 {lefts}｜命中竖标 {data.get('hitMarker')}")
    if len(widths) > 1:
        problems.append(f"{label}: 行号列宽不统一 {widths}（正文起点会跟着抖）")
    if len(lefts) > 1:
        problems.append(f"{label}: 正文列起点不统一 {lefts}")
    if widths and widths[0] < 24:
        problems.append(f"{label}: 行号列只有 {widths[0]}px，三位数行号会挤到正文")
    heights = data.get("modeChipHeights") or []
    print(f"  顶/底栏权重：chip 高度 {heights}｜选中底色 {data.get('activeChipBg')}"
          f"｜下划线 {data.get('activeChipShadow')}｜键帽边框 {data.get('kbdBorder')}")
    if heights and min(heights) < 32:
        problems.append(f"{label}: 模式 chip 点击目标只有 {min(heights)}px（要求 ≥32px）")
    if data.get("activeChipBg") not in (None, "rgba(0, 0, 0, 0)"):
        problems.append(f"{label}: 选中的模式 chip 仍是实心填充 {data.get('activeChipBg')}，权重没降下来")
    if data.get("kbdBorder") not in (None, "0px"):
        problems.append(f"{label}: 键位提示仍有边框 {data.get('kbdBorder')}，视觉上像一排按钮")
    gap = data.get("chipRowGap")
    print(f"  输入区：模式行与输入框间距 {gap}px｜无键帽的提示项 {data.get('hintSpansWithoutKbd')}"
          f"｜状态说明独立成行 {data.get('modeHintOutsideHints')}")
    if gap is not None and gap < 8:
        problems.append(f"{label}: 模式按钮与输入框只隔 {gap}px，选中下划线会显得局促")
    if data.get("hintSpansWithoutKbd"):
        problems.append(f"{label}: 快捷键行里混进了 {data.get('hintSpansWithoutKbd')} 条非快捷键说明")
    if data.get("modeHintOutsideHints") is False:
        problems.append(f"{label}: 模式说明仍在快捷键行里（应独立成行）")
    fade = data.get("composerFade") or {}
    print(f"  输入区过渡：上边框 {data.get('composerBorderTop')}｜渐变 {str(fade.get('image'))[:48]}"
          f"｜高度 {fade.get('height')}｜pointer-events {fade.get('pointer')}")
    if data.get("composerBorderTop") not in (None, "0px"):
        problems.append(f"{label}: 输入区仍有硬边框 {data.get('composerBorderTop')}，没有换成渐变过渡")
    if "gradient" not in str(fade.get("image")):
        problems.append(f"{label}: 输入区上方没有渐变过渡（{fade.get('image')}）")
    if fade.get("pointer") != "none":
        problems.append(f"{label}: 渐变层没有设 pointer-events:none，会挡住画布点击")
    rows = data.get("planRows")
    print(f"  计划渲染：图片行数 {rows}｜chip 间距 {data.get('planGap')}")
    if rows is not None and rows > 1 and "390" in label:
        problems.append(f"{label}: 窄屏计划 chip 折成 {rows} 行（应单行横向滚动）")
    gap_value = data.get("planGap")
    if "390" in label and gap_value and float(str(gap_value).replace("px", "")) < 8:
        problems.append(f"{label}: 窄屏计划 chip 间距 {gap_value} < 8px")
    if data.get("previewPointer") not in (None, "none"):
        problems.append(f"{label}: 引用预览卡 pointer-events={data.get('previewPointer')}，会吃掉 chip 的 hover")
    return problems


def check_readability(chrome: "cdp.Chrome", sid: str, label: str, *, mobile: bool) -> list[str]:
    """Kimi 建议 #3.2：程序化量排版，而不是只看截图顺眼。

    检查：可见文字最小字号、组件标题是否被裁切、窄屏抽屉是否真的可达。
    """
    problems: list[str] = []
    data = chrome.evaluate(sid, """(() => {
      const visible = (n) => {
        const s = getComputedStyle(n);
        return s.display !== 'none' && s.visibility !== 'hidden' && n.getClientRects().length > 0;
      };
      const tiny = [];
      document.querySelectorAll('.widget__body *, .rail *, .composer *').forEach((n) => {
        if (!n.children.length && n.textContent.trim() && visible(n)) {
          const fs = parseFloat(getComputedStyle(n).fontSize);
          if (fs && fs < 12) tiny.push(`${n.className || n.tagName}@${fs}px`);
        }
      });
      const clipped = [];
      document.querySelectorAll('.widget__title, .widget__badge, .src-item__name').forEach((n) => {
        if (!visible(n) || n.scrollWidth <= n.clientWidth + 1) return;
        const s = getComputedStyle(n);
        // 有省略号或 title 兜底的是"设计上截断"，不算缺陷；否则文字真的看不全
        const hasEllipsis = s.textOverflow === 'ellipsis';
        const hasTitle = n.hasAttribute('title') && n.getAttribute('title').trim().length > 0;
        if (!hasEllipsis && !hasTitle) clipped.push(n.textContent.trim().slice(0, 18));
      });
      const toggle = document.querySelector('[data-toggle="left"]');
      return {
        tiny: [...new Set(tiny)].slice(0, 6),
        clipped: [...new Set(clipped)].slice(0, 6),
        hasLeftToggle: Boolean(toggle),
      };
    })()""") or {}
    if data.get("tiny"):
        problems.append(f"{label}: 可见文字小于 12px：{data['tiny']}")
    if data.get("clipped"):
        problems.append(f"{label}: 文字被裁切：{data['clipped']}")

    if mobile:
        # 窄屏：左右栏是抽屉，必须能打开也能关掉（不靠 hover）
        opened = chrome.evaluate(sid, """(async () => {
          const rail = document.querySelector('.rail--left');
          if (!rail) return {ok: false, why: '没有左栏'};
          const toggle = document.querySelector('[data-toggle="left"]');
          if (!toggle) return {ok: false, why: '没有抽屉开关'};
          const hidden0 = rail.getBoundingClientRect().right <= 1;
          toggle.click();
          await new Promise((r) => setTimeout(r, 350));
          const shown = rail.getBoundingClientRect().right > 1;
          const scrim = document.querySelector('#drawer-scrim');
          if (scrim) scrim.click();
          await new Promise((r) => setTimeout(r, 350));
          const closed = rail.getBoundingClientRect().right <= 1;
          return {ok: shown && closed, hidden0, shown, closed};
        })()""") or {}
        if not opened.get("ok"):
            problems.append(f"{label}: 窄屏抽屉不可用 {opened}")
        else:
            print("  · 窄屏抽屉：可打开、可关闭")
    return problems


def _setup_console() -> None:
    """Windows 控制台默认 GBK：脚本里有中文和 ¥，不切 UTF-8 会直接崩在 print 上。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def main() -> int:
    _setup_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8484)
    parser.add_argument("--root", default=r"D:\CS336")
    parser.add_argument("--db", default=None, help="默认用临时库，跑完即弃")
    parser.add_argument("--case", default="all", choices=["all", "index", "interactive", "hostile", "pdf", "decisions"])
    parser.add_argument("--themes", default="dark,light",
                        help="要验收的配色方案，逗号分隔（默认两种都跑）")
    args = parser.parse_args()

    themes = [t.strip() for t in args.themes.split(",") if t.strip()] or ["dark"]

    shots = PROJECT / "runs" / "ui"
    shots.mkdir(parents=True, exist_ok=True)
    tmpdir = tempfile.TemporaryDirectory()
    store = Store(args.db) if args.db else Store(Path(tmpdir.name) / "accept.db")
    ctx = prepare_store(store, args.root)
    app = MentorApp(store, root=args.root)
    httpd = start_server(app, args.port)
    base = f"http://127.0.0.1:{args.port}"
    print(f"服务：{base}（笔记本 {len(store.list_notebooks())} 本，材料根 {args.root}）")
    print(f"界面目录：{ui_dir()}")
    if not (ui_dir() / "index.html").exists():
        print("界面文件缺失，先产出 ui/index.html")
        return 2

    problems: list[str] = []
    # 决策页在 docs/decisions/ 下，而 mentor 的服务器只服务 ui/：给它们开一个只读静态服务，
    # 而不是往应用里加一条只给开发用的路由。
    docs_base = base
    docs_srv = None
    if args.case in ("all", "decisions"):
        from functools import partial
        from http.server import SimpleHTTPRequestHandler

        class _Quiet(SimpleHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

        docs_srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Quiet, directory=str(PROJECT)))
        docs_srv.daemon_threads = True
        threading.Thread(target=docs_srv.serve_forever, daemon=True).start()
        docs_base = f"http://127.0.0.1:{docs_srv.server_address[1]}"

    chrome = cdp.Chrome(1440, 900)
    try:
        # 顺序很重要：index 轮必须全部跑完再跑 interactive 轮。
        # interactive 会往验收库里写问答记录，之后首页就不再是"未提问"的空状态了。
        if args.case in ("all", "index"):
            for theme in themes:
                for label, width, height in (("index-1440", 1440, 900), ("index-390", 390, 844)):
                    sid = chrome.new_page(width, height)
                    set_theme(chrome, sid, theme)
                    chrome.goto(sid, base + "/")
                    time.sleep(1.0)
                    tag = f"{label}-{theme}"
                    problems += check_index(chrome, sid, tag)
                    problems += check_readability(chrome, sid, tag, mobile=width < 900)
                    problems += contrast_audit(chrome, sid, tag)
                    shot = shots / f"{tag}.png"
                    chrome.screenshot(sid, shot)
                    problems += verify_theme_pixels(shot, theme, tag)
        if args.case in ("all", "interactive"):
            for theme in themes:
                for label, width, height in (("selftest-1440", 1440, 1100), ("selftest-390", 390, 844)):
                    # 390 的交互只在第一种配色跑一遍：结论与配色无关，但能省一半时间
                    if width < 900 and theme != themes[0]:
                        continue
                    sid = chrome.new_page(width, height)
                    set_theme(chrome, sid, theme)
                    selftest_url = base + "/self-test"
                    if ctx.get("lecture_nb"):
                        selftest_url += "?nb=" + ctx["lecture_nb"]
                    chrome.goto(sid, selftest_url)
                    tag = f"{label}-{theme}"
                    problems += check_interactive(chrome, sid, tag)
                    problems += style_evidence(chrome, sid, tag)
                    problems += check_readability(chrome, sid, tag, mobile=width < 900)
                    problems += contrast_audit(chrome, sid, tag)
                    shot = shots / f"{tag}.png"
                    chrome.screenshot(sid, shot)
                    problems += verify_theme_pixels(shot, theme, tag)
        if args.case in ("all", "decisions"):
            problems += check_decisions(chrome, docs_base, "decisions", shots=shots)
        if args.case in ("all", "pdf"):
            problems += check_pdf(chrome, base, "pdf", shot=shots / "pdf-1440.png")
        if args.case in ("all", "hostile"):
            problems += check_hostile(chrome, base, "hostile", shot=shots / "hostile-1280.png")
    finally:
        chrome.close()
        httpd.shutdown()
        httpd.server_close()
        if docs_srv is not None:
            docs_srv.shutdown()
            docs_srv.server_close()
        store.close()
        tmpdir.cleanup()

    print("\n=== 结论 ===")
    for item in problems:
        print(f"  ✗ {item}")
    if problems:
        return 1
    print("  ✓ 主路径通过：三栏渲染、组件流式抵达、引用可跳原文、窄屏无溢出")
    print(f"  截图：{shots}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
