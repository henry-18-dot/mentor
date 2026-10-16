/**
 * mentor 界面（真实模式 + 离线预览模式）。
 *
 * 真实模式：
 *   GET  /api/health, /api/notebooks, /api/notebooks/<id>, /api/sources/<sid>
 *   POST /api/ask            —— SSE 流，用 fetch + ReadableStream 手动解析（要 POST，不能用 EventSource）
 *   GET  /api/turns/<id>, /api/search?notebook_id=&q=
 *   事件顺序 plan → retrieval → widget* → surface → done（error 可随时插入）
 *
 * 预览模式（preview.html）：
 *   不碰网络，直接读页面里 id="demo-surface" / id="demo-sources" 的内联 JSON（与 fixtures/ 同源），
 *   并用同一套流式管线（plan → retrieval → widget* → surface → done）逐帧喂给界面，方便验收。
 */

import { renderWidget, renderWidgetSkeleton, renderCiteChips, WIDGET_LABELS, KNOWN_TYPES }
  from "./widgets.js";

// ---------------------------------------------------------------------------
// 小工具
// ---------------------------------------------------------------------------

const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

function h(tag, attrs, children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = String(v);
      else if (k === "text") node.textContent = String(v);
      else if (k === "html") node.innerHTML = String(v); // 仅用于本文件内的 SVG 常量
      else if (k === "dataset") Object.assign(node.dataset, v);
      else if (k.startsWith("on") && typeof v === "function") {
        node.addEventListener(k.slice(2).toLowerCase(), v);
      } else node.setAttribute(k, v === true ? "" : String(v));
    }
  }
  for (const child of [].concat(children || [])) {
    if (child === null || child === undefined || child === false) continue;
    node.appendChild(typeof child === "string" || typeof child === "number"
      ? document.createTextNode(String(child))
      : child);
  }
  return node;
}

const ICON = {
  menu: '<svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
  panelLeft: '<svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true"><rect x="3" y="4.5" width="18" height="15" rx="2" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M9.5 4.5v15" stroke="currentColor" stroke-width="1.6"/></svg>',
  panelRight: '<svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true"><rect x="3" y="4.5" width="18" height="15" rx="2" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M14.5 4.5v15" stroke="currentColor" stroke-width="1.6"/></svg>',
  send: '<svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true"><path d="M4 12 20 4.5 15 12l5 7.5z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>',
  stop: '<svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor"/></svg>',
  search: '<svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true"><circle cx="11" cy="11" r="6" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="m15.6 15.6 4 4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
  info: '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M12 11v5.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><circle cx="12" cy="7.9" r="1" fill="currentColor"/></svg>',
};

const MODES = [
  { id: "ask", label: "提问", hint: "一问一答：正文 + 引用 + 来源" },
  { id: "brief", label: "梳理", hint: "时间线 / 步骤 + 正文" },
  { id: "explore", label: "概念", hint: "概念图 + 正文 + 卡片" },
  { id: "study", label: "复习", hint: "速查表 + 闪卡 + 自测" },
  { id: "compare", label: "对比", hint: "表格 + 正文" },
  { id: "listen", label: "音频", hint: "口播脚本 + 来源" },
];

const LAYOUT_LABELS = {
  focus: "聚焦问答",
  brief: "梳理",
  explore: "概念展开",
  study: "复习",
  compare: "对比",
  listen: "音频概览",
};

// 界面认识的组件协议版本（后端在 plan/surface 里下发 protocol 字段）
const PROTOCOL_SUPPORTED = "0.1";

// ---------------------------------------------------------------------------
// 状态
// ---------------------------------------------------------------------------

const PREVIEW = Boolean(window.__MENTOR_PREVIEW__);

const state = {
  preview: PREVIEW,
  health: null,
  notebooks: [],
  notebook: null,
  sources: [],
  sourceById: new Map(),
  chunkIndex: new Map(),        // chunk_id -> {source_id, line}
  hits: [],                     // 最近一次 retrieval / search
  hitChunkIds: new Set(),
  turnByChunk: new Map(),       // chunk_id -> turn 元素，供左栏一键跳转
  turns: [],
  mode: "ask",
  streaming: false,
  controller: null,
  current: null,                // 当前流式轮次
  activeSourceId: null,
  activeLine: 0,
  docLines: [],
};

function makeTurn(question, layout, intent) {
  const section = h("section", { class: "turn", "data-turn": "live" });
  const canvas = $("#canvas-inner");
  // 第一轮提问开始后，空状态就该退场，否则会一直挂在正文上方
  $$("[data-role=empty]", canvas).forEach((node) => node.remove());
  const qMark = h("span", { class: "turn__q-mark", "aria-hidden": "true" }, ["问"]);
  const qText = h("h2", { class: "turn__q-text" }, [question]);
  section.appendChild(h("div", { class: "turn__q" }, [qMark, qText]));
  const meta = h("div", { class: "turn__meta" });
  section.appendChild(meta);
  section.appendChild(h("div", { class: "canvas__notice", "data-role": "notice" }));
  const planBar = h("div", { class: "plan-bar", hidden: true, "data-role": "plan" });
  section.appendChild(planBar);
  const widgets = h("div", { class: "widgets", "data-role": "widgets" });
  section.appendChild(widgets);
  canvas.appendChild(section);
  return {
    section, meta, planBar, widgets,
    question, layout, intent,
    widgetIndex: new Map(),     // widget index -> 元素
    widgetIds: new Map(),       // widget id -> 元素
    plannedChips: new Map(),    // 类型 -> chip
    skeletonNodes: [],
    receivedWidgets: [],
    streamNote: null,
    done: false,
  };
}

function turnContext(turn) {
  return {
    citations: (turn.surface && turn.surface.citations) || [],
    openCitation: (n) => openCitation(n, turn),
    openSourceAt: (target) => openSourceAt(target),
    copy: (text) => copyText(text),
    ask: (text) => submit(text),
    announce: (text) => announce(text),
    toast: (text) => showNotice(text, "info"),
    getSource: (id) => state.sourceById.get(id) || null,
  };
}

// ---------------------------------------------------------------------------
// 无障碍播报 / 顶部提示
// ---------------------------------------------------------------------------

function announce(text) {
  const host = state.current && state.current.meta ? state.current.meta : $("#canvas-inner");
  if (!host) return;
  let region = $("[data-role=sr]", host);
  if (!region) {
    region = h("span", {
      class: "visually-hidden", "data-role": "sr", role: "status", "aria-live": "polite",
    });
    host.appendChild(region);
  }
  region.textContent = "";
  window.setTimeout(() => { region.textContent = text; }, 30);
}

function showNotice(text, level, parent) {
  const host = parent || $("#canvas-inner");
  const notice = h("div", { class: `notice notice--${level || "info"}`, role: "status" }, [
    h("span", { class: "icon", html: ICON.info }),
    h("span", {}, [text]),
    h("button", {
      type: "button", class: "notice__close", "aria-label": "关闭提示",
      onclick: () => notice.remove(),
    }, ["×"]),
  ]);
  host.insertBefore(notice, host.firstChild);
  return notice;
}

function setConnection(level, text) {
  const dot = $("#conn-dot");
  const label = $("#conn-text");
  if (dot) dot.className = `status-dot is-${level}`;
  if (label) label.textContent = text;
}

// ---------------------------------------------------------------------------
// 剪贴板
// ---------------------------------------------------------------------------

async function copyText(text) {
  const value = String(text === null || text === undefined ? "" : text);
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
    } else {
      const area = h("textarea", { class: "visually-hidden" });
      area.value = value;
      document.body.appendChild(area);
      area.select();
      const ok = document.execCommand && document.execCommand("copy");
      area.remove();
      if (!ok) throw new Error("execCommand copy 失败");
    }
    announce("已复制到剪贴板");
  } catch (err) {
    // 兜底：把文本放进取词区并全选，用户手动 Ctrl+C
    const area = $("#copy-fallback");
    if (area) {
      area.value = value;
      area.hidden = false;
      area.focus();
      area.select();
      announce("自动复制不可用，文本已放入下方文本框，请按 Ctrl+C 复制");
    }
  }
}

// ---------------------------------------------------------------------------
// 左栏：笔记本 / 来源
// ---------------------------------------------------------------------------

function renderNotebooks() {
  const host = $("#nb-list");
  if (!host) return;
  host.textContent = "";
  if (!state.notebooks.length) {
    host.appendChild(h("p", { class: "rail__empty" }, [
      state.preview ? "离线预览没有笔记本列表。" : "还没有笔记本。用 POST /api/notebooks 建一个。",
    ]));
    return;
  }
  state.notebooks.forEach((nb) => {
    const item = h("button", {
      type: "button",
      class: "nb-item" + (state.notebook && state.notebook.id === nb.id ? " is-active" : ""),
      onclick: () => loadNotebook(nb.id),
    }, [
      h("span", { class: "nb-item__title" }, [nb.title || nb.id]),
      h("span", { class: "nb-item__meta" }, [
        `${nb.source_count || 0} 份材料 · ${nb.turn_count || 0} 轮`,
      ]),
    ]);
    host.appendChild(item);
  });
}

function renderSources() {
  const host = $("#src-list");
  if (!host) return;
  host.textContent = "";
  if (!state.sources.length) {
    host.appendChild(h("p", { class: "rail__empty" }, [
      state.preview ? "离线预览使用 fixtures 里的示例材料。" : "这个笔记本还没有材料。",
    ]));
    return;
  }
  state.sources.forEach((src) => {
    const isHit = state.hits.some((hit) => hit.source_id === src.id);
    const hitCount = state.hits.filter((hit) => hit.source_id === src.id).length;
    const item = h("button", {
      type: "button",
      class: "src-item",
      "data-source-id": src.id,
      title: `打开 ${src.path || src.id}`,
      onclick: () => openSource(src.id, 0, state.searchQuery || ""),
    }, [
      h("span", { class: "src-item__row" }, [
        h("span", { class: "src-item__name" }, [src.title || src.path || src.id]),
        isHit ? h("span", { class: "hit-flag" }, [`命中 ${hitCount}`]) : null,
      ]),
      h("span", { class: "src-item__meta" }, [
        h("span", { class: "kind-tag" }, [src.kind || "?"]),
        h("span", {}, [src.path || ""]),
      ]),
    ]);
    if (isHit) {
      const first = state.hits.find((hit) => hit.source_id === src.id);
      if (first) {
        item.appendChild(h("span", { class: "src-item__hit" }, [
          `${first.locator || ""} ${String(first.snippet || "").slice(0, 60)}…`,
        ]));
      }
    }
    host.appendChild(item);
  });
}

function renderSearchHits(hits, query) {
  const host = $("#src-list");
  if (!host) return;
  host.textContent = "";
  host.appendChild(h("p", { class: "section-label" }, [`搜索 “${query}” · ${hits.length} 条命中`]));
  if (!hits.length) {
    host.appendChild(h("p", { class: "rail__empty" }, ["没有命中。换一个关键词试试。"]));
    return;
  }
  hits.forEach((hit) => {
    host.appendChild(h("button", {
      type: "button",
      class: "src-item",
      onclick: () => openSourceAt({ source_id: hit.source_id, chunk_id: hit.chunk_id, locator: hit.locator, line: hit.line }),
    }, [
      h("span", { class: "src-item__row" }, [
        h("span", { class: "src-item__name" }, [hit.title || hit.source_id]),
        h("span", { class: "hit-flag" }, [`${(Number(hit.score) || 0).toFixed(2)}`]),
      ]),
      h("span", { class: "src-item__meta" }, [h("span", {}, [hit.locator || ""])]),
      h("span", { class: "src-item__hit" }, highlightTerms(String(hit.snippet || ""), hit.matched)),
    ]));
  });
}

function escapeRe(text) {
  return String(text).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * 把命中词在文本里高亮出来（返回可直接塞进 DOM 的子节点数组）。
 * 只标"这行命中了"信息量不够——用户要知道到底命中了哪几个字。
 */
function highlightTerms(text, terms) {
  const source = String(text == null ? "" : text);
  const list = [...new Set((terms || []).filter(Boolean).map(String))]
    .sort((a, b) => b.length - a.length);
  if (!source || !list.length) return [source];
  const parts = list.map((term) => (/[\u4e00-\u9fff]/.test(term)
    ? escapeRe(term)
    : `(?<![A-Za-z0-9_])${escapeRe(term)}(?![A-Za-z0-9_])`));
  let pattern;
  try {
    pattern = new RegExp(parts.join("|"), "gi");
  } catch (err) {
    return [source];
  }
  const out = [];
  let last = 0;
  let match = pattern.exec(source);
  while (match) {
    if (match.index > last) out.push(source.slice(last, match.index));
    out.push(h("mark", { class: "term-hit" }, [match[0]]));
    last = match.index + match[0].length;
    if (match[0].length === 0) pattern.lastIndex += 1;
    match = pattern.exec(source);
  }
  if (last < source.length) out.push(source.slice(last));
  return out.length ? out : [source];
}

// ---------------------------------------------------------------------------
// 右栏：原文对照
// ---------------------------------------------------------------------------

function sourceLines(source) {
  // 讲义类材料带 raw（原始文件），行号与引用 locator 一致，优先用它；
  // 其它格式退回归一化后的 text。列表接口里的源对象没有正文，会得到空文档——
  // 那种情况由 openSource 负责去取全文，不在这里兜。
  const text = String((source && (source.raw || source.text)) || "");
  return text.replace(/\r\n?/g, "\n").split("\n");
}

async function openSource(sourceId, line, term = "") {
  const body = $("#srcdoc-body");
  if (!body) return;
  let source = state.sourceById.get(sourceId) || null;
  // 左栏列表拿到的源对象不含正文，缓存里若没有正文就必须重新取，
  // 否则右栏会渲染成一篇空文档（曾经就是这样：点引用后只有一行）。
  const cachedWithoutText = source && !source.text && !source.raw;
  // 两种情况要请求后端：① 缓存里没有正文；② 带着搜索词打开但没指定行号 ——
  // 后者即使正文已在缓存里也要问一次，否则"跳到首个命中行"永远不会发生。
  const needFetch = (!source || cachedWithoutText || (term && !line)) && !state.preview && sourceId;
  if (needFetch) {
    try {
      // 带着搜索词打开时，让后端直接告诉我们首个命中在第几行
      const q = term ? `?highlight=${encodeURIComponent(term)}` : "";
      const payload = await api(`/api/sources/${encodeURIComponent(sourceId)}${q}`);
      source = payload.source;
      if (!line && payload.highlight && payload.highlight.line) {
        line = payload.highlight.line;
      }
      if (source) {
        state.sourceById.set(source.id, source);
        (payload.chunks || []).forEach((chunk) => {
          state.chunkIndex.set(chunk.id, {
            source_id: chunk.source_id,
            line: (chunk.span && chunk.span.line) || 0,
          });
        });
      }
    } catch (err) {
      showNotice(`读取原文失败：${err.message}`, "error");
      return;
    }
  }
  if (!source) {
    body.textContent = "";
    body.appendChild(h("p", { class: "rail__empty" }, ["找不到这份原文（source_id 未知）。"]));
    return;
  }
  state.activeSourceId = sourceId;
  renderSourceDoc(source, line || 0);
}

function renderSourceDoc(source, line) {
  const head = $("#srcdoc-head");
  const body = $("#srcdoc-body");
  if (!head || !body) return;
  head.textContent = "";
  head.appendChild(h("div", { class: "srcdoc__title" }, [source.title || source.id]));
  head.appendChild(h("div", { class: "srcdoc__path" }, [source.path || source.id]));

  const lines = sourceLines(source);
  state.docLines = lines;
  body.textContent = "";
  const view = h("div", { class: "srcview" });
  const code = h("div", { class: "srcview__code" });
  const hitLines = new Set(state.hits.filter((hit) => hit.source_id === source.id).map((hit) => Number(hit.line) || 0));
  // 命中词按行归拢：右栏在命中的那一行里把词高亮出来，而不是只把整行染色
  const termsByLine = new Map();
  state.hits.filter((hit) => hit.source_id === source.id).forEach((hit) => {
    const line = Number(hit.line) || 0;
    if (!line) return;
    const bucket = termsByLine.get(line) || [];
    (hit.matched || []).forEach((term) => {
      if (term && !bucket.includes(term)) bucket.push(term);
    });
    termsByLine.set(line, bucket);
  });
  lines.forEach((text, i) => {
    const n = i + 1;
    code.appendChild(h("div", {
      class: "line" + (hitLines.has(n) ? " is-hit" : ""),
      "data-line": String(n),
    }, [
      h("span", { class: "line__n" }, [String(n)]),
      h("span", { class: "line__text" }, highlightTerms(text === "" ? " " : text, termsByLine.get(n))),
    ]));
  });
  view.appendChild(code);
  body.appendChild(view);
  const input = $("#line-input");
  if (input && line) input.value = String(line);
  jumpToLine(line || 0);
}

function jumpToLine(line, scroll = true) {
  const body = $("#srcdoc-body");
  if (!body) return;
  const target = line ? $(`.line[data-line="${Number(line)}"]`, body) : null;
  $$(".line.is-target", body).forEach((node) => node.classList.remove("is-target"));
  if (!target) {
    if (scroll) body.scrollTop = 0;
    return;
  }
  target.classList.add("is-target");
  state.activeLine = Number(line);
  if (scroll !== false) {
    // 注意：.line 的 offsetParent 是 .rail（position:relative），不是滚动容器，
    // 所以必须用 getBoundingClientRect 的相对位置算滚动量。
    const bodyRect = body.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    const delta = targetRect.top - bodyRect.top;
    body.scrollTop = Math.max(0, body.scrollTop + delta - body.clientHeight / 6);
  }
  target.setAttribute("tabindex", "-1");
  target.focus({ preventScroll: true });
}

/** 引用 chip / 来源项的统一入口。 */
function openCitation(n, turn) {
  const citations = (turn && turn.surface && turn.surface.citations) || [];
  const cite = citations.find((c) => Number(c.n) === Number(n));
  if (!cite) {
    announce(`引用 ${n} 没有对应来源`);
    return;
  }
  openDrawer("right");
  openSourceAt({
    source_id: cite.source_id,
    chunk_id: cite.chunk_id,
    locator: cite.locator,
    line: lineFromLocator(cite.locator),
  });
}

function lineFromLocator(locator) {
  const m = /:(\d+)\s*$/.exec(String(locator || ""));
  return m ? Number(m[1]) : 0;
}

// ---------------------------------------------------------------------------
// 画布键盘光标：j/k 在组件间移动，Enter 打开该组件的首条引用
// ---------------------------------------------------------------------------

function canvasWidgets() {
  return $$("#canvas article.widget:not(.widget--skeleton)");
}

function canvasCursorWidget() {
  const active = document.activeElement;
  if (active && active.closest) {
    const widget = active.closest("article.widget");
    if (widget) return widget;
  }
  return document.querySelector("#canvas article.widget.is-cursor");
}

/** 移动画布光标。返回是否真的移动了（没得移动就不拦截按键）。 */
function moveCanvasCursor(delta) {
  const widgets = canvasWidgets();
  if (!widgets.length) return false;
  const current = canvasCursorWidget();
  let index = current ? widgets.indexOf(current) : -1;
  index = index < 0 ? (delta > 0 ? 0 : widgets.length - 1)
                    : Math.min(widgets.length - 1, Math.max(0, index + delta));
  const next = widgets[index];
  if (!next) return false;
  widgets.forEach((node) => node.classList.remove("is-cursor"));
  next.classList.add("is-cursor");
  // 组件本身可聚焦（tabindex=-1），这样键盘/读屏都能接住后续按键
  next.setAttribute("tabindex", "-1");
  next.focus({ preventScroll: true });
  next.scrollIntoView({ block: "nearest", behavior: "smooth" });
  const label = next.querySelector(".widget__title")?.textContent?.trim() || "组件";
  const chipCount = next.querySelectorAll(".cite-chip[data-cite-n]").length;
  announce(`${label}（${index + 1}/${widgets.length}）${chipCount ? `，${chipCount} 条引用，按 Enter 打开第一条` : ""}`);
  return true;
}

// ---------------------------------------------------------------------------
// 引用悬停预览：不点击也能先看清"这条引用指向哪一行、原文说了什么"
// ---------------------------------------------------------------------------

let citePreviewTimer = 0;

function ensureCitePreview() {
  let card = document.getElementById("cite-preview");
  if (card) return card;
  card = h("div", { id: "cite-preview", class: "cite-preview", role: "tooltip", hidden: true });
  document.body.appendChild(card);
  return card;
}

function hideCitationPreview() {
  if (citePreviewTimer) {
    clearTimeout(citePreviewTimer);
    citePreviewTimer = 0;
  }
  const card = document.getElementById("cite-preview");
  if (card) card.hidden = true;
}

function showCitationPreview(chip) {
  if (!chip) return;
  const n = chip.dataset.citeN;
  const locator = chip.dataset.citeLocator || "";
  const title = chip.dataset.citeTitle || "";
  const quote = chip.dataset.citeQuote || "";
  if (!locator && !quote) return;
  const card = ensureCitePreview();
  card.textContent = "";
  card.appendChild(h("div", { class: "cite-preview__head" }, [
    h("span", { class: "cite-preview__n" }, [`[${n}]`]),
    h("span", { class: "cite-preview__locator" }, [locator || "来源"]),
    title ? h("span", { class: "cite-preview__title" }, [title]) : null,
  ]));
  if (quote) card.appendChild(h("p", { class: "cite-preview__quote" }, [quote]));
  card.appendChild(h("p", { class: "cite-preview__hint" }, ["点击可在右栏打开原文并跳到该行"]));
  card.hidden = false;

  // 贴着 chip 放，但不许出视口
  const rect = chip.getBoundingClientRect();
  const box = card.getBoundingClientRect();
  const left = Math.min(Math.max(8, rect.left), Math.max(8, window.innerWidth - box.width - 8));
  const below = rect.bottom + 8;
  const top = below + box.height > window.innerHeight - 8
    ? Math.max(8, rect.top - box.height - 8)
    : below;
  card.style.left = `${Math.round(left)}px`;
  card.style.top = `${Math.round(top)}px`;
}

/** 画布上的引用 chip 用事件代理接管：无论内联引用还是"引用"行都走同一条路径。 */
function wireCitationPreview() {
  const onChip = (event) => {
    const target = event.target;
    return target && target.closest ? target.closest(".cite-chip[data-cite-n]") : null;
  };
  document.addEventListener("mouseover", (event) => {
    const chip = onChip(event);
    if (chip) showCitationPreview(chip);
  });
  document.addEventListener("mouseout", (event) => {
    const chip = onChip(event);
    if (!chip) return;
    const to = event.relatedTarget;
    if (to && chip.contains(to)) return;
    // 稍等一下再收：鼠标从 chip 移到卡片上（或斜着移开）时不闪
    citePreviewTimer = window.setTimeout(hideCitationPreview, 120);
  });
  document.addEventListener("focusin", (event) => {
    const chip = onChip(event);
    if (chip) showCitationPreview(chip);
  });
  document.addEventListener("focusout", hideCitationPreview);
  document.addEventListener("scroll", hideCitationPreview, true);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hideCitationPreview();
  });
}

/** 打开某份原文并滚到目标行。优先用 chunk_id → 行号映射，其次解析 locator。 */
async function openSourceAt(target) {
  const t = target || {};
  let sourceId = t.source_id;
  let line = Number(t.line) || 0;
  if (t.chunk_id && state.chunkIndex.has(t.chunk_id)) {
    const known = state.chunkIndex.get(t.chunk_id);
    sourceId = sourceId || known.source_id;
    line = line || known.line;
  }
  if (!line) line = lineFromLocator(t.locator);
  if (!sourceId) {
    announce("这条引用没有 source_id，无法定位原文");
    return;
  }
  await openSource(sourceId, line);
  markDrawerHint(sourceId, line);
}

function markDrawerHint(sourceId, line) {
  $$(".src-item").forEach((node) => node.classList.toggle("is-active", node.dataset.sourceId === sourceId));
  announce(`已在右栏打开 ${sourceId}${line ? `，跳到第 ${line} 行` : ""}`);
}

// ---------------------------------------------------------------------------
// 抽屉（窄屏）
// ---------------------------------------------------------------------------

function openDrawer(side) {
  const app = $("#app");
  if (!app || !isNarrow()) return;
  app.classList.remove("is-drawer-left", "is-drawer-right");
  app.classList.add(side === "left" ? "is-drawer-left" : "is-drawer-right");
  const rail = $(side === "left" ? ".rail--left" : ".rail--right", app);
  const focusable = rail && rail.querySelector("button, input, [tabindex]");
  if (focusable) focusable.focus({ preventScroll: true });
  announce(side === "left" ? "已打开资料来源" : "已打开原文对照");
  syncPanelToggles();
}

function closeDrawers() {
  const app = $("#app");
  if (!app) return;
  app.classList.remove("is-drawer-left", "is-drawer-right");
  syncPanelToggles();
}

/**
 * ⑪ 顶栏两个面板开关的状态同步：打开中 → aria-pressed=true + 填充底 + tooltip。
 * 宽屏看折叠类，窄屏看抽屉类，两种布局共用一套状态。
 */
function syncPanelToggles() {
  const app = $("#app");
  if (!app) return;
  $$("[data-toggle]").forEach((btn) => {
    const which = btn.dataset.toggle === "right" ? "right" : "left";
    const name = which === "left" ? "资料来源" : "原文对照";
    const open = isNarrow()
      ? app.classList.contains(which === "left" ? "is-drawer-left" : "is-drawer-right")
      : !app.classList.contains(which === "left" ? "is-left-collapsed" : "is-right-collapsed");
    btn.setAttribute("aria-pressed", open ? "true" : "false");
    btn.classList.toggle("is-active", open);
    const hint = `${name}：${open ? "点击收起" : "点击展开"}`;
    btn.setAttribute("title", hint);
    btn.setAttribute("aria-label", hint);
  });
}

function isNarrow() {
  return window.matchMedia("(max-width: 899px)").matches;
}

// ---------------------------------------------------------------------------
// 流式渲染
// ---------------------------------------------------------------------------

function handlePlan(turn, data) {
  const d = data || {};
  turn.layout = d.layout || turn.layout;
  turn.intent = d.intent || turn.intent;
  // 协议版本：更新的后端下发未知组件时，界面靠未知组件兜底渲染，不崩也不静默丢数据
  if (d.protocol && d.protocol !== PROTOCOL_SUPPORTED) {
    showNotice(
      `后端协议版本 ${d.protocol}，当前界面按 ${PROTOCOL_SUPPORTED} 渲染；` +
      "不认识的组件会降级显示，不会丢内容。",
      "info",
    );
  }
  turn.meta.textContent = "";
  // 顶栏只留两个胶囊：版面 + 意图。后端名、协议细节放进 title，
  // 避免"概念展开 / offline / 意图 explore / 6 个组件"四个标签互相打架。
  const layoutText = LAYOUT_LABELS[turn.layout] || turn.layout || "版面";
  const intentText = d.intent ? `意图 ${d.intent}` : "";
  const detail = [
    `版面 ${layoutText}`,
    d.intent ? `意图 ${d.intent}` : "",
    `生成后端 ${d.provider || "offline"}`,
    d.protocol ? `协议 ${d.protocol}` : "",
    d.describe || "",
  ].filter(Boolean).join(" · ");
  turn.meta.appendChild(h("span", { class: "pill", title: detail }, [layoutText]));
  if (intentText) turn.meta.appendChild(h("span", { class: "pill", title: detail }, [intentText]));
  turn.metaTitle = detail;
  if (d.degraded) {
    turn.meta.appendChild(h("span", { class: "pill pill--medium" }, ["降级模式"]));
  }
  turn.meta.appendChild(h("span", { class: "stream-status" }, [
    h("span", { class: "stream-status__pulse" }),
    h("span", { "data-role": "stream-text" }, ["规划完成，正在检索材料…"]),
  ]));

  // 立刻把"将要出现的组件"画成骨架占位：这是智能 UI 最关键的可见动作。
  const planned = Array.isArray(d.planned) ? d.planned : [];
  turn.planBar.hidden = false;
  turn.planBar.textContent = "";
  turn.planBar.appendChild(h("span", {}, ["计划渲染"]));
  const typeWrap = h("span", { class: "plan-bar__types" });
  planned.forEach((type) => {
    const label = WIDGET_LABELS[type] || type;
    // ③ 每个 chip 带状态与说明，实心/描边两种状态有出处
    const chip = h("span", {
      class: "plan-chip", "data-state": "planned", title: `${label}：待渲染`,
    }, [label]);
    turn.plannedChips.set(type, chip);
    typeWrap.appendChild(chip);
  });
  turn.planBar.appendChild(typeWrap);
  turn.planBar.appendChild(h("span", { class: "plan-bar__legend" }, ["实心=已渲染 · 描边=待渲染"]));
  planned.forEach((type) => {
    const skeleton = renderWidgetSkeleton(type, WIDGET_LABELS[type] || type);
    turn.skeletonNodes.push(skeleton);
    turn.widgets.appendChild(skeleton);
  });
  announce(`已规划 ${planned.length} 个组件：${planned.map((t) => WIDGET_LABELS[t] || t).join("、")}。正在检索材料。`);
}

function handleRetrieval(turn, data) {
  const d = data || {};
  state.hits = Array.isArray(d.hits) ? d.hits : [];
  state.hitChunkIds = new Set(state.hits.map((hit) => hit.chunk_id).filter(Boolean));
  state.hits.forEach((hit) => {
    if (hit.chunk_id) {
      state.chunkIndex.set(hit.chunk_id, { source_id: hit.source_id, line: Number(hit.line) || lineFromLocator(hit.locator) });
    }
  });
  renderSources();
  const streamText = $("[data-role=stream-text]", turn.meta);
  if (streamText) {
    streamText.textContent = `检索到 ${state.hits.length} 条命中，正在生成组件…`;
  }
  if (state.activeSourceId) renderSourceDoc(state.sourceById.get(state.activeSourceId), state.activeLine);
  announce(`检索完成，命中 ${state.hits.length} 条材料，来源列表已标记。`);
}

function consumeSkeleton(turn) {
  const node = turn.skeletonNodes.shift();
  if (node && node.parentNode) node.remove();
}

function handleWidget(turn, data) {
  const d = data || {};
  const widget = d.widget || d;
  if (!widget || !widget.type) return;
  const index = Number.isFinite(Number(d.index)) ? Number(d.index) : turn.receivedWidgets.length;
  const id = widget.id || `idx-${index}`;
  const existing = turn.widgetIndex.get(index) || turn.widgetIds.get(id);
  const node = renderWidget(widget, turnContext(turn));
  consumeSkeleton(turn);
  if (existing && existing.parentNode) {
    existing.replaceWith(node);
  } else {
    turn.widgets.appendChild(node);
  }
  turn.widgetIndex.set(index, node);
  turn.widgetIds.set(id, node);
  turn.receivedWidgets[index] = widget;
  const chip = turn.plannedChips.get(widget.type);
  if (chip) {
    chip.classList.add("is-arrived");
    chip.dataset.state = "arrived";
    chip.title = `${WIDGET_LABELS[widget.type] || widget.type}：已渲染`;
  }
  const streamText = $("[data-role=stream-text]", turn.meta);
  if (streamText) {
    streamText.textContent = `已渲染 ${widget.type}（${turn.receivedWidgets.filter(Boolean).length} 个组件）`;
  }
  announce(`组件 ${WIDGET_LABELS[widget.type] || widget.type} 已渲染`);
  scheduleScroll(node);
}

let scrollTimer = 0;
function scheduleScroll(node) {
  // 只在用户本来就贴着底部时自动跟随，避免抢走滚动位置。
  const canvas = $("#canvas");
  if (!canvas || !node) return;
  const nearBottom = canvas.scrollHeight - canvas.scrollTop - canvas.clientHeight < 220;
  if (!nearBottom) return;
  window.clearTimeout(scrollTimer);
  scrollTimer = window.setTimeout(() => {
    node.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, 60);
}

function handleSurface(turn, data) {
  const surface = (data && data.surface) || data;
  if (!surface || !Array.isArray(surface.widgets)) return;
  turn.surface = surface;
  // 供离线自检/调试读取当前引用表（只读，不参与渲染逻辑）
  window.__surfaceCitations = surface.citations || [];
  turn.planBar.hidden = false;
  const streamText = $("[data-role=stream-text]", turn.meta);
  if (streamText) streamText.textContent = `已收到完整界面（${surface.widgets.length} 个组件）`;
  // 用完整对象校正：按 index 就地更新，不整块重建（避免闪烁、保住折叠状态）。
  surface.widgets.forEach((widget, index) => {
    const id = widget.id || `idx-${index}`;
    const existing = turn.widgetIndex.get(index) || turn.widgetIds.get(id);
    if (existing && existing.dataset.widgetType === widget.type) {
      const body = $(".widget__body", existing);
      const current = turn.receivedWidgets[index];
      const same = current && JSON.stringify(current.data) === JSON.stringify(widget.data)
        && String(current.title || "") === String(widget.title || "");
      if (same) return;
      existing.replaceWith(renderWidget(widget, turnContext(turn)));
    } else {
      const node = renderWidget(widget, turnContext(turn));
      if (existing && existing.parentNode) existing.replaceWith(node);
      else turn.widgets.appendChild(node);
      turn.widgetIndex.set(index, node);
      turn.widgetIds.set(id, node);
    }
    turn.receivedWidgets[index] = widget;
  });
  turn.skeletonNodes.forEach((node) => node.remove());
  turn.skeletonNodes = [];
  // 让正文里的 [n] chip 在 surface 到齐后一定拿得到引用表
  $$(".cite-chips", turn.widgets).forEach((old) => {
    const wrap = old;
    const nums = $$(".cite-chip", old).map((btn) => Number(btn.dataset.citeN));
    wrap.replaceWith(renderCiteChips(nums, turnContext(turn)));
  });
  turn.meta.querySelectorAll(".pill--accent").forEach((n) => n.remove());
  if (surface.degraded) turn.meta.appendChild(h("span", { class: "pill pill--medium" }, ["降级模式"]));
  if (surface.provider && surface.degraded) {
    // 正常情况后端名只在顶栏胶囊的 tooltip 里；只有降级（x→offline）才值得单独提示
    const shown = [...turn.meta.querySelectorAll(".pill")].some((n) => n.textContent === surface.provider);
    if (!shown) {
      turn.meta.appendChild(h("span", { class: "pill", title: detailOf(turn) }, [surface.provider]));
    }
  }
  if (Array.isArray(surface.notes) && surface.notes.length) {
    showNotice(surface.notes.join(" "), "info", turn.section);
  }
}

function handleError(turn, data) {
  const message = (data && data.message) || "服务端返回了错误";
  showNotice(message, "error", turn.section.querySelector("[data-role=notice]"));
  turn.meta.appendChild(h("span", { class: "pill pill--low" }, ["失败"]));
  announce(`出错：${message}。已保留已渲染的内容。`);
}

function handleDone(turn) {
  turn.done = true;
  const stream = $(".stream-status", turn.meta);
  if (stream) {
    const text = $("[data-role=stream-text]", stream);
    if (text) text.textContent = "完成";
    stream.remove();
  }
  turn.meta.appendChild(h("span", { class: "pill pill--accent" }, [
    `${turn.receivedWidgets.filter(Boolean).length} 个组件`,
  ]));
  announce("本轮回答完成");
}

function handleEvent(turn, name, data) {
  switch (name) {
    case "plan": handlePlan(turn, data); break;
    case "retrieval": handleRetrieval(turn, data); break;
    case "widget": handleWidget(turn, data); break;
    case "surface": handleSurface(turn, data); break;
    case "error": handleError(turn, data); break;
    case "done": handleDone(turn); break;
    default:
      // 未知事件只记录，不打断流
      if (typeof console !== "undefined" && console.info) console.info("[mentor] 未知 SSE 事件", name, data);
  }
}

// ---------------------------------------------------------------------------
// SSE：fetch + ReadableStream 手动解析（POST 不能用 EventSource）
// ---------------------------------------------------------------------------

/**
 * 解析 text/event-stream 字节流。逐帧回调 onEvent(name, data)。
 * 按 SSE 规范处理多行 data、注释行、CRLF 与跨 chunk 的半帧。
 */
export async function readEventStream(response, onEvent) {
  if (!response.body || !response.body.getReader) {
    throw new Error("当前环境不支持流式响应（response.body 不可读）");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let eventName = "message";
  let dataLines = [];

  const flush = () => {
    if (!dataLines.length) { eventName = "message"; return; }
    const raw = dataLines.join("\n");
    let parsed = raw;
    try {
      parsed = JSON.parse(raw);
    } catch (err) {
      parsed = { raw };
    }
    onEvent(eventName, parsed);
    eventName = "message";
    dataLines = [];
  };

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let index;
    // 帧分隔：空行（\n\n 或 \r\n\r\n）
    while ((index = buffer.search(/\r?\n\r?\n/)) !== -1) {
      const frame = buffer.slice(0, index);
      buffer = buffer.slice(index + buffer.slice(index).match(/^\r?\n\r?\n/)[0].length);
      for (const line of frame.split(/\r?\n/)) {
        if (!line || line.startsWith(":")) continue;
        const colon = line.indexOf(":");
        const field = colon === -1 ? line : line.slice(0, colon);
        const value_ = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
        if (field === "event") eventName = value_;
        else if (field === "data") dataLines.push(value_);
      }
      flush();
    }
  }
  buffer += decoder.decode();
  if (buffer.trim()) {
    for (const line of buffer.split(/\r?\n/)) {
      if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
      else if (line.startsWith("event:")) eventName = line.slice(6).trim();
    }
  }
  flush();
}

// ---------------------------------------------------------------------------
// 真实 API
// ---------------------------------------------------------------------------

async function api(path, options) {
  const res = await fetch(path, {
    headers: { Accept: "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = "";
    try {
      const payload = await res.json();
      detail = payload && (payload.message || payload.error) ? `：${payload.message || payload.error}` : "";
    } catch (err) { /* 非 JSON 错误体 */ }
    throw new Error(`${res.status} ${res.statusText}${detail}`);
  }
  return res.json();
}

async function bootstrap() {
  setConnection("warn", "连接中…");
  try {
    const health = await api("/api/health");
    state.health = health;
    setConnection("ok", `v${health.version || "?"} · ${health.provider || "offline"}`);
  } catch (err) {
    setConnection("error", "后端不可达");
    showNotice(`无法连接后端（${err.message}）。界面可离线预览：打开 ui/preview.html。`, "error");
    renderNotebooks();
    renderSources();
    return;
  }
  try {
    const payload = await api("/api/notebooks");
    state.notebooks = payload.notebooks || [];
    renderNotebooks();
    if (state.notebooks.length) await loadNotebook(state.notebooks[0].id);
    else {
      renderSources();
      emptyState("还没有笔记本", "先用 POST /api/notebooks 建一个笔记本并摄入材料，然后在这里提问。");
    }
  } catch (err) {
    showNotice(`读取笔记本列表失败：${err.message}`, "error");
  }
}

async function loadNotebook(id) {
  try {
    const payload = await api(`/api/notebooks/${encodeURIComponent(id)}`);
    state.notebook = payload.notebook;
    state.sources = payload.sources || [];
    state.sourceById = new Map(state.sources.map((src) => [src.id, src]));
    renderNotebooks();
    renderSources();
    setConnection("ok", `${payload.notebook.title || id} · ${state.sources.length} 份材料`);
    const canvas = $("#canvas-inner");
    if (canvas) {
      $$("[data-role=empty]", canvas).forEach((n) => n.remove());
      const hasTurns = (payload.turns || []).length > 0 ||
        (payload.stats && payload.stats.turns > 0);
      const hasWidgets = canvas.querySelectorAll("article.widget").length > 0;
      if (!hasTurns && !hasWidgets) {
        // 还没提过问：画布别留白，给一句"接下来做什么"和可直接点的示例
        emptyState(
          `${payload.notebook.title || "笔记本"} · 可以开始提问`,
          `已导入 ${state.sources.length} 份材料。回答不是一整段文字，而是按组件协议现场编排的界面；` +
          "正文里的 [n] 可以点开右栏原文、按行核对。",
        );
      }
    }
  } catch (err) {
    showNotice(`打开笔记本失败：${err.message}`, "error");
  }
}

async function loadTurn(id) {
  try {
    const payload = await api(`/api/turns/${encodeURIComponent(id)}`);
    const turn = payload.turn;
    if (!turn || !turn.surface) return;
    const view = makeTurn(turn.question, turn.surface.layout, turn.surface.intent);
    view.surface = turn.surface;
    handlePlan(view, {
      intent: turn.surface.intent, layout: turn.surface.layout,
      planned: turn.surface.widgets.map((w) => w.type),
      provider: turn.surface.provider, degraded: turn.surface.degraded,
    });
    handleSurface(view, { surface: turn.surface });
    handleDone(view);
  } catch (err) {
    showNotice(`读取历史轮次失败：${err.message}`, "error");
  }
}

async function runSearch(query) {
  if (state.preview) return;
  if (!state.notebook) {
    showNotice("先选一个笔记本再搜索。", "info");
    return;
  }
  try {
    const payload = await api(`/api/search?notebook_id=${encodeURIComponent(state.notebook.id)}&q=${encodeURIComponent(query)}`);
    state.hits = payload.hits || [];
    // 记住当前搜索词：之后点左栏来源卡时，右栏会直接跳到该词的首个命中行
    state.searchQuery = query;
    renderSearchHits(state.hits, query);
    renderSources0();
  } catch (err) {
    showNotice(`搜索失败：${err.message}`, "error");
  }
}

// 搜索后恢复来源列表按钮
function renderSources0() {
  const host = $("#src-list");
  if (!host) return;
  host.appendChild(h("button", {
    type: "button", class: "ghost-btn", style: "margin-top:8px",
    onclick: () => renderSources(),
  }, ["← 返回来源列表"]));
}

// ---------------------------------------------------------------------------
// 提问
// ---------------------------------------------------------------------------

function emptyState(title, text) {
  const canvas = $("#canvas-inner");
  if (!canvas) return;
  $$("[data-role=empty]", canvas).forEach((n) => n.remove());
  canvas.appendChild(h("div", { class: "empty-state", "data-role": "empty" }, [
    h("p", { class: "empty-state__title" }, [title]),
    h("p", { class: "empty-state__text" }, [text]),
    h("div", { class: "empty-state__hints" }, MODES.slice(0, 4).map((mode) =>
      h("button", {
        type: "button", class: "mode-chip", "data-mode": mode.id,
        "aria-pressed": state.mode === mode.id ? "true" : "false",
        onclick: () => { setMode(mode.id); submit(sampleQuestion(mode.id)); },
      }, [`${mode.label} 示例`]))),
  ]));
}

function sampleQuestion(mode) {
  const samples = {
    ask: "BPE 分词器的合并依据是什么？",
    brief: "把这门课的作业安排梳理成时间线。",
    explore: "帮我展开「自注意力」这个概念，画出它和相关概念的关系。",
    study: "给我一份第 1 讲的速查表和三道自测题。",
    compare: "对比字符级、字节级和 BPE 三种分词。",
    listen: "把第 1 讲的核心内容写成三分钟口播稿。",
  };
  return samples[mode] || samples.ask;
}

function setMode(mode) {
  state.mode = mode;
  $$(".mode-chip").forEach((chip) => {
    chip.setAttribute("aria-pressed", chip.dataset.mode === mode ? "true" : "false");
  });
  const hint = MODES.find((m) => m.id === mode);
  const label = $("#mode-hint");
  if (label && hint) label.textContent = hint.hint;
}

function submit(text) {
  const input = $("#composer-input");
  const question = (text === undefined ? (input ? input.value : "") : text).trim();
  if (!question) {
    if (input) input.focus();
    return;
  }
  if (state.streaming) {
    showNotice("上一轮还在生成。按「停止」或 Esc 中断后再发送。", "info");
    return;
  }
  if (input && text === undefined) input.value = "";
  if (state.preview) {
    previewStream(question);
    return;
  }
  streamAsk(question);
}

async function streamAsk(question) {
  if (!state.notebook) {
    showNotice("先选一个笔记本（左栏）再提问。", "info");
    return;
  }
  const turn = makeTurn(question, "", state.mode);
  state.current = turn;
  state.streaming = true;
  state.controller = new AbortController();
  setStreamingUi(true);
  announce("已发送问题，等待规划。");
  try {
    const res = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ notebook_id: state.notebook.id, question, mode: state.mode }),
      signal: state.controller.signal,
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    await readEventStream(res, (name, data) => {
      try {
        handleEvent(turn, name, data);
      } catch (err) {
        handleError(turn, { message: `渲染 ${name} 事件时出错：${err.message}` });
      }
    });
    if (!turn.done) handleDone(turn);
  } catch (err) {
    if (err && err.name === "AbortError") {
      handleError(turn, { message: "已中断。已生成的内容保留在画布上。" });
      const stream = $(".stream-status", turn.meta);
      if (stream) stream.remove();
    } else {
      handleError(turn, { message: `请求失败：${err.message}` });
    }
  } finally {
    state.streaming = false;
    state.controller = null;
    setStreamingUi(false);
  }
}

function setStreamingUi(active) {
  const send = $("#send-btn");
  const stop = $("#stop-btn");
  const input = $("#composer-input");
  if (send) send.hidden = active;
  if (stop) stop.hidden = !active;
  if (input) input.setAttribute("aria-busy", active ? "true" : "false");
}

function interrupt() {
  state.aborted = true;
  if (state.controller) state.controller.abort();
}

// ---------------------------------------------------------------------------
// 预览模式：用同一套事件管线回放 fixture
// ---------------------------------------------------------------------------

function readInlineJson(id) {
  const node = document.getElementById(id);
  if (!node) return null;
  try {
    return JSON.parse(node.textContent);
  } catch (err) {
    throw new Error(`内联 fixture ${id} 不是合法 JSON：${err.message}`);
  }
}

function loadPreviewData() {
  const surface = readInlineJson("demo-surface");
  const sourceFixture = readInlineJson("demo-sources");
  const sources = (sourceFixture && sourceFixture.sources) || [];
  state.sources = sources;
  state.sourceById = new Map(sources.map((src) => [src.id, src]));
  state.notebook = { id: "nb_demo", title: "离线预览 · CS336 语言模型课程", source_count: sources.length, turn_count: 1 };
  state.notebooks = [state.notebook];
  surface.citations.forEach((cite) => {
    state.chunkIndex.set(cite.chunk_id, { source_id: cite.source_id, line: lineFromLocator(cite.locator) });
  });
  renderNotebooks();
  renderSources();
  state.hits = surface.citations.map((cite) => ({
    chunk_id: cite.chunk_id, source_id: cite.source_id, locator: cite.locator,
    title: cite.title, snippet: cite.quote, line: lineFromLocator(cite.locator), score: 1,
  }));
  return surface;
}

async function previewStream(question) {
  const surface = state.demoSurface;
  const turn = makeTurn(question || surface.question, surface.layout, surface.intent);
  state.current = turn;
  state.streaming = true;
  state.aborted = false;
  setStreamingUi(true);
  const wait = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));
  const stopped = () => state.aborted === true;
  handlePlan(turn, {
    intent: surface.intent, layout: surface.layout,
    planned: surface.widgets.map((w) => w.type),
    provider: surface.provider || "offline", degraded: Boolean(surface.degraded),
  });
  await wait(240);
  handleRetrieval(turn, { terms: [], hits: state.hits });
  await wait(240);
  for (let i = 0; i < surface.widgets.length; i += 1) {
    if (stopped()) break;
    handleWidget(turn, { index: i, widget: surface.widgets[i] });
    await wait(40);
  }
  if (stopped()) {
    const stream = $(".stream-status", turn.meta);
    if (stream) stream.remove();
    handleError(turn, { message: "已中断。已生成的内容保留在画布上。" });
  } else {
    handleSurface(turn, { surface });
    await wait(80);
    handleDone(turn);
  }
  state.streaming = false;
  setStreamingUi(false);
}

// ---------------------------------------------------------------------------
// 输入 / 键盘
// ---------------------------------------------------------------------------

function wireComposer() {
  const form = $("#composer");
  const input = $("#composer-input");
  if (form) {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      submit();
    });
  }
  if (input) {
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        submit();
      }
    });
  }
  $$(".mode-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      setMode(chip.dataset.mode || "ask");
      if (input) input.focus();
    });
  });
}

function wireKeyboard() {
  document.addEventListener("keydown", (event) => {
    const target = event.target;
    const typing = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
    if (event.key === "/" && !typing && !event.metaKey && !event.ctrlKey) {
      event.preventDefault();
      const input = $("#composer-input");
      if (input) input.focus();
      return;
    }
    if (event.key === "Escape") {
      if (state.streaming) interrupt();
      closeDrawers();
      return;
    }
    // j / k：在画布组件之间移动"光标"（不抢输入框里的按键）
    if (!typing && !event.metaKey && !event.ctrlKey && !event.altKey && (event.key === "j" || event.key === "k")) {
      if (moveCanvasCursor(event.key === "j" ? 1 : -1)) {
        event.preventDefault();
      }
      return;
    }
    // Enter：光标所在组件里，打开它的第一条引用
    if (event.key === "Enter" && !typing && !event.metaKey && !event.ctrlKey) {
      const widget = canvasCursorWidget();
      const chip = widget && widget.querySelector(".cite-chip[data-cite-n]");
      if (chip) {
        event.preventDefault();
        chip.click();
        return;
      }
    }
  });
  const scrim = $("#drawer-scrim");
  if (scrim) scrim.addEventListener("click", closeDrawers);
  $$("[data-toggle]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const which = btn.dataset.toggle === "right" ? "right" : "left";
      const app = $("#app");
      if (!app) return;
      if (isNarrow()) {
        // 窄屏：左右栏是抽屉，切换当前抽屉
        const has = app.classList.contains(which === "left" ? "is-drawer-left" : "is-drawer-right");
        closeDrawers();
        if (!has) openDrawer(which);
        return;
      }
      if (which === "left") app.classList.toggle("is-left-collapsed");
      else app.classList.toggle("is-right-collapsed");
      const collapsed = which === "left"
        ? app.classList.contains("is-left-collapsed")
        : app.classList.contains("is-right-collapsed");
      btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
      announce(`${which === "left" ? "资料来源" : "原文对照"}${collapsed ? "已折叠" : "已展开"}`);
      syncPanelToggles();
    });
  });
  syncPanelToggles();
  window.addEventListener("resize", syncPanelToggles);
  const search = $("#search-input");
  if (search) {
    let timer = 0;
    search.addEventListener("input", () => {
      window.clearTimeout(timer);
      const value = search.value.trim();
      timer = window.setTimeout(() => {
        if (value.length >= 2) runSearch(value);
        else renderSources();
      }, 260);
    });
  }
  const lineGo = $("#line-go");
  const lineInput = $("#line-input");
  const jump = () => jumpToLine(Number(lineInput && lineInput.value) || 0, true);
  if (lineGo) lineGo.addEventListener("click", jump);
  if (lineInput) {
    lineInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); jump(); }
    });
  }
}

// ---------------------------------------------------------------------------
// 外壳
// ---------------------------------------------------------------------------

function mountShell() {
  const app = $("#app");
  if (!app) throw new Error("index.html 里缺少 #app");
  app.innerHTML = "";
  app.appendChild(h("a", { class: "skip-link", href: "#canvas" }, ["跳到画布"]));

  app.appendChild(h("header", { class: "topbar" }, [
    h("button", {
      type: "button", class: "btn btn--icon drawer-only", "data-toggle": "left",
      "aria-expanded": "false", title: "资料来源（窄屏抽屉）",
    }, [h("span", { class: "icon", html: ICON.menu }), "来源"]),
    h("div", { class: "brand" }, [
      h("span", { class: "brand__mark" }, ["mentor"]),
      h("span", { class: "brand__sub" }, ["由组件协议即时编排的智能界面"]),
    ]),
    h("span", { class: "topbar__spacer" }),
    h("div", { class: "topbar__status" }, [
      h("span", { class: "status-dot", id: "conn-dot" }),
      h("span", { id: "conn-text" }, [PREVIEW ? "离线预览（fixtures）" : "未连接"]),
    ]),
    h("button", {
      type: "button", class: "btn btn--icon", "data-toggle": "left",
      "aria-expanded": "true", title: "折叠资料来源",
    }, [h("span", { class: "icon", html: ICON.panelLeft }), h("span", { class: "visually-hidden" }, ["资料来源"])]),
    h("button", {
      type: "button", class: "btn btn--icon", "data-toggle": "right",
      "aria-expanded": "true", title: "折叠原文对照",
    }, [h("span", { class: "icon", html: ICON.panelRight }), h("span", { class: "visually-hidden" }, ["原文对照"])]),
  ]));

  app.appendChild(h("aside", { class: "rail rail--left", "aria-label": "资料来源" }, [
    h("div", { class: "rail__head" }, [
      h("span", { class: "rail__title" }, ["资料来源"]),
      h("button", {
        type: "button", class: "btn btn--icon drawer-only", style: "min-height:32px",
        onclick: closeDrawers, "aria-label": "关闭资料来源抽屉",
      }, ["×"]),
    ]),
    h("div", { class: "rail__body" }, [
      h("div", { class: "rail__section" }, [
        h("span", { class: "section-label" }, ["笔记本"]),
        h("div", { class: "nb-list", id: "nb-list" }),
      ]),
      h("div", { class: "rail__section" }, [
        h("span", { class: "section-label" }, ["材料与命中"]),
        h("div", { class: "search-row" }, [
          h("input", {
            id: "search-input", type: "search", placeholder: "搜索片段（≥2 字）",
            "aria-label": "在当前笔记本里搜索片段",
          }),
        ]),
        h("div", { class: "src-list", id: "src-list" }),
      ]),
    ]),
  ]));

  app.appendChild(h("main", { class: "canvas", id: "canvas", tabindex: "-1", "aria-label": "智能画布" }, [
    h("div", { class: "canvas__inner", id: "canvas-inner" }),
  ]));

  app.appendChild(h("aside", { class: "rail rail--right", "aria-label": "原文对照" }, [
    h("div", { class: "rail__head" }, [
      h("span", { class: "rail__title" }, ["原文对照"]),
      h("button", {
        type: "button", class: "btn btn--icon drawer-only", style: "min-height:32px",
        onclick: closeDrawers, "aria-label": "关闭原文抽屉",
      }, ["×"]),
    ]),
    h("div", { id: "srcdoc-head" }),
    h("div", { class: "srcdoc__toolbar" }, [
      h("label", { for: "line-input", class: "meta-label" }, ["跳到行"]),
      h("input", { id: "line-input", type: "number", min: "1", step: "1", placeholder: "行号" }),
      h("button", { type: "button", class: "btn btn--quiet", id: "line-go" }, ["跳转"]),
    ]),
    h("div", { class: "rail__body", id: "srcdoc-body" }, [
      h("p", { class: "rail__empty" }, ["还没有打开任何材料。"]),
    ]),
  ]));

  app.appendChild(h("button", {
    type: "button", class: "drawer-scrim", id: "drawer-scrim", "aria-label": "关闭抽屉",
  }));

  const modeChips = MODES.map((mode) => h("button", {
    type: "button", class: "mode-chip", "data-mode": mode.id,
    "aria-pressed": mode.id === "ask" ? "true" : "false",
    title: mode.hint,
  }, [`${mode.label}`]));

  app.appendChild(h("footer", { class: "composer" }, [
    h("div", { class: "composer__inner" }, [
      h("form", { id: "composer" }, [
        h("div", { class: "mode-chips", role: "group", "aria-label": "回答模式" }, modeChips),
        h("div", { class: "composer__row" }, [
          h("div", { class: "composer__field" }, [
            h("label", { class: "visually-hidden", for: "composer-input" }, ["提问"]),
            h("textarea", {
              id: "composer-input", class: "composer__input", rows: "2",
              placeholder: "问点什么，例如：BPE 的合并依据是什么？",
            }),
          ]),
          h("button", {
            type: "submit", class: "btn btn--primary", id: "send-btn", "aria-label": "发送",
          }, [
            h("span", { class: "icon", html: ICON.send }),
            h("span", { class: "btn__label" }, ["发送"]),
          ]),
          h("button", {
            type: "button", class: "btn btn--danger", id: "stop-btn", hidden: true,
            "aria-label": "停止生成", onclick: interrupt,
          }, [
            h("span", { class: "icon", html: ICON.stop }),
            h("span", { class: "btn__label" }, ["停止"]),
          ]),
        ]),
        h("div", { class: "composer__hint" }, [
          h("span", {}, [h("kbd", {}, ["/"]), " 聚焦输入框"]),
          h("span", {}, [h("kbd", {}, ["Enter"]), " 发送"]),
          h("span", {}, [h("kbd", {}, ["Shift"]), "+", h("kbd", {}, ["Enter"]), " 换行"]),
          h("span", {}, [h("kbd", {}, ["Esc"]), " 中断、关抽屉"]),
        ]),
        // 版面/模式的说明不再混进快捷键行：读起来是一组纯快捷键，不会被斜杠和冒号搅混
        h("div", { class: "composer__modehint", id: "mode-hint" }, [MODES[0].hint]),
      ]),
    ]),
  ]));

  app.appendChild(h("textarea", {
    id: "copy-fallback", class: "kv-pre", hidden: true, "aria-label": "复制失败时的文本区",
  }));
}

function wireResize() {
  // 宽屏下左右栏可拖拽调宽（只改 CSS 变量，不动结构）。
  $$(".rail").forEach((rail) => {
    const isLeft = rail.classList.contains("rail--left");
    const handle = h("div", {
      class: "rail__resize", "data-side": isLeft ? "left" : "right",
      role: "separator", "aria-orientation": "vertical", tabindex: "0",
      "aria-label": isLeft ? "调整资料来源栏宽度" : "调整原文对照栏宽度",
    });
    rail.appendChild(handle);
    let startX = 0;
    let startWidth = 0;
    const onMove = (event) => {
      const dx = event.clientX - startX;
      const width = Math.min(520, Math.max(200, startWidth + (isLeft ? dx : -dx)));
      document.documentElement.style.setProperty(isLeft ? "--left-w" : "--right-w", `${width}px`);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      document.body.style.userSelect = "";
    };
    handle.addEventListener("pointerdown", (event) => {
      if (window.matchMedia("(max-width: 899px)").matches) return;
      startX = event.clientX;
      startWidth = rail.getBoundingClientRect().width;
      document.body.style.userSelect = "none";
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    });
    // 方向键也能调（键盘可达）
    handle.addEventListener("keydown", (event) => {
      const step = event.shiftKey ? 40 : 12;
      const delta = event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0;
      if (!delta) return;
      event.preventDefault();
      const current = rail.getBoundingClientRect().width;
      const width = Math.min(520, Math.max(200, current + (isLeft ? delta : -delta)));
      document.documentElement.style.setProperty(isLeft ? "--left-w" : "--right-w", `${width}px`);
    });
  });
}

// ---------------------------------------------------------------------------
// 启动
// ---------------------------------------------------------------------------

const PLACEHOLDER_HEAD = ["尚未打开原文", "点击正文里的 [n] 或左栏来源项，右栏会打开原文并跳到该行"];

function resetSourcePane() {
  const head = $("#srcdoc-head");
  const body = $("#srcdoc-body");
  if (!head || !body) return;
  head.textContent = "";
  head.appendChild(h("div", { class: "srcdoc__title" }, [PLACEHOLDER_HEAD[0]]));
  head.appendChild(h("div", { class: "srcdoc__path" }, [PLACEHOLDER_HEAD[1]]));
  body.textContent = "";
  body.appendChild(h("p", { class: "rail__empty" }, ["还没有打开任何材料。"]));
}

function main() {
  mountShell();
  wireComposer();
  wireKeyboard();
  wireResize();
  wireCitationPreview();
  setMode("ask");
  resetSourcePane();

  if (state.preview) {
    try {
      state.demoSurface = loadPreviewData();
    } catch (err) {
      showNotice(`预览数据读取失败：${err.message}`, "error");
      return;
    }
    showNotice("这是离线预览：不连后端，数据来自 ui/fixtures。按「发送」可回放一遍流式渲染。", "info");
    emptyState(
      "离线预览 · 17 种组件",
      "点「发送」用同一套流式管线（plan → retrieval → widget* → surface → done）渲染 fixtures/surface-demo.json；" +
      "点正文里的 [n] 或左栏来源项，右栏会打开原文并跳到对应行。",
    );
    const input = $("#composer-input");
    if (input) input.value = state.demoSurface.question;
    return;
  }
  bootstrap();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", main, { once: true });
} else {
  main();
}

export { state, submit, openCitation, openSourceAt, copyText, runSearch };
