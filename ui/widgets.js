/**
 * mentor 智能画布的组件渲染器（零依赖原生 ESM）。
 *
 * 契约见 docs/api.md §2 与 mentor/protocol.py。
 * 入口：
 *   renderWidget(widget, ctx) -> HTMLElement
 *   renderers  —— type -> renderer 映射表（协议里 16 种类型 + 未知类型兜底）
 *
 * ctx 至少提供：
 *   citations: Citation[]            —— 当前 Surface 的引用表（{n, chunk_id, source_id, locator, title, quote}）
 *   openCitation(n)                  —— 点击正文 [n] / 组件引用 chip
 *   openSourceAt({source_id, chunk_id, line, locator})
 *   copy(text)                       —— 复制文本
 * 可选：ask(text)（followups 点击）、announce(text)、toast(text)
 *
 * 渲染器只负责"这一块长什么样"，不碰全局状态；所有交互通过 ctx 回调出去。
 */

// ---------------------------------------------------------------------------
// 常量
// ---------------------------------------------------------------------------

export const WIDGET_LABELS = {
  answer: "回答",
  quote: "原文引用",
  sources: "来源",
  concept_map: "概念图",
  timeline: "时间线",
  steps: "步骤",
  compare_table: "对比表",
  cheatsheet: "速查表",
  flashcards: "闪卡",
  quiz: "自测",
  code: "代码",
  formula: "公式",
  image: "插图",
  followups: "追问",
  audio_script: "音频脚本",
  progress: "学习进度",
  heatmap: "主题 × 材料",
  unknown: "未知组件",
};

/** 已知类型（与 protocol.WIDGET_TYPES 一致，当前 17 种）。 */
export const KNOWN_TYPES = [
  "answer", "quote", "sources", "concept_map", "timeline", "steps",
  "compare_table", "cheatsheet", "flashcards", "quiz", "code", "formula",
  "image", "followups", "audio_script", "progress", "heatmap",
];

const SVG_NS = "http://www.w3.org/2000/svg";

const ICONS = {
  copy:
    '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false">' +
    '<path d="M9 3h9a2 2 0 0 1 2 2v9" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>' +
    '<rect x="4" y="7" width="12" height="14" rx="2" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>',
  check:
    '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false">' +
    '<path d="M4 12.5 9.5 18 20 6.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  chevron:
    '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false">' +
    '<path d="M6 9.5 12 15.5 18 9.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  warn:
    '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false">' +
    '<path d="M12 4 2.5 20h19z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>' +
    '<path d="M12 9.5v5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>' +
    '<circle cx="12" cy="17.3" r="1" fill="currentColor"/></svg>',
  play:
    '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">' +
    '<path d="M8 5.5v13l10-6.5z" fill="currentColor"/></svg>',
  image:
    '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" focusable="false">' +
    '<rect x="3" y="4.5" width="18" height="15" rx="2" fill="none" stroke="currentColor" stroke-width="1.5"/>' +
    '<path d="M3.5 16.5 9 11l4 4 2.5-2.5 5 5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>' +
    '<circle cx="8.5" cy="9" r="1.4" fill="currentColor"/></svg>',
  quote:
    '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" focusable="false">' +
    '<path d="M9 6.5C6.5 8 5 10.2 5 13.2c0 2.6 1.4 4.3 3.4 4.3 1.8 0 3-1.2 3-2.9 0-1.6-1.1-2.7-2.6-2.7-.3 0-.6 0-.8.1.3-1.4 1.3-2.6 2.7-3.6z" fill="currentColor"/>' +
    '<path d="M18 6.5c-2.5 1.5-4 3.7-4 6.7 0 2.6 1.4 4.3 3.4 4.3 1.8 0 3-1.2 3-2.9 0-1.6-1.1-2.7-2.6-2.7-.3 0-.6 0-.8.1.3-1.4 1.3-2.6 2.7-3.6z" fill="currentColor"/>' +
    "</svg>",
  lock:
    '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false">' +
    '<rect x="5" y="10.5" width="14" height="9" rx="2" fill="none" stroke="currentColor" stroke-width="1.6"/>' +
    '<path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>',
};

// ---------------------------------------------------------------------------
// 小工具
// ---------------------------------------------------------------------------

/**
 * URL 白名单：模型/上游说什么就信什么，是把自己交给对方。
 *
 * 实测过的洞：answer 里的 `[点我](javascript:window.__PWNED__=1)` 会被渲染成真的
 * `<a href="javascript:...">`，一点就执行。`data:text/html` 同理。
 * 规则：http/https/mailto/锚点/相对路径放行，其它协议一律拒绝（返回空串 → 调用方降级成纯文本）；
 * 顺带剥掉控制字符，避免 `java\nscript:` 这种绕过。
 */
export function safeUrl(raw, { allowDataImage = false } = {}) {
  const text = String(raw == null ? "" : raw).replace(/[\u0000-\u001f\u007f]/g, "").trim();
  if (!text) return "";
  if (/^data:/i.test(text)) {
    return allowDataImage && /^data:image\//i.test(text) ? text : "";
  }
  if (/^[a-z][a-z0-9+.-]*:/i.test(text)) {
    return /^(https?|mailto):/i.test(text) ? text : "";
  }
  return text; // 相对路径 / #锚点
}

export function el(tag, attrs, children) {
  const node = document.createElement(tag);
  applyAttrs(node, attrs);
  appendAll(node, children);
  return node;
}

export function svgEl(tag, attrs, children) {
  const node = document.createElementNS(SVG_NS, tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined) continue;
      node.setAttribute(k, String(v));
    }
  }
  appendAll(node, children);
  return node;
}

function applyAttrs(node, attrs) {
  if (!attrs) return;
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = String(v);
    else if (k === "text") node.textContent = String(v);
    else if (k === "html") node.innerHTML = String(v); // 仅用于本文件内置 SVG 常量
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k === "style") node.setAttribute("style", String(v));
    else if (k.startsWith("on") && typeof v === "function") {
      node.addEventListener(k.slice(2).toLowerCase(), v);
    } else node.setAttribute(k, v === true ? "" : String(v));
  }
}

function appendAll(node, children) {
  if (children === null || children === undefined) return;
  const list = Array.isArray(children) ? children : [children];
  for (const child of list) {
    if (child === null || child === undefined || child === false) continue;
    node.appendChild(typeof child === "string" || typeof child === "number"
      ? document.createTextNode(String(child))
      : child);
  }
}

export function asText(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(asText).join("、");
  if (typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch (err) {
      return String(value);
    }
  }
  return String(value);
}

function asArray(value) {
  if (Array.isArray(value)) return value;
  if (value === null || value === undefined) return [];
  return [value];
}

function normalizeCtx(ctx) {
  const c = ctx || {};
  const citations = Array.isArray(c.citations) ? c.citations : [];
  return {
    citations,
    citationByN: (n) => citations.find((item) => Number(item && item.n) === Number(n)) || null,
    openCitation: typeof c.openCitation === "function" ? c.openCitation : () => {},
    openSourceAt: typeof c.openSourceAt === "function" ? c.openSourceAt : () => {},
    copy: typeof c.copy === "function" ? c.copy : () => {},
    ask: typeof c.ask === "function" ? c.ask : null,
    announce: typeof c.announce === "function" ? c.announce : () => {},
    toast: typeof c.toast === "function" ? c.toast : () => {},
    getSource: typeof c.getSource === "function" ? c.getSource : () => null,
  };
}

function locatorLine(locator) {
  const m = /:(\d+)\s*$/.exec(String(locator || ""));
  return m ? Number(m[1]) : 0;
}

// ---------------------------------------------------------------------------
// 引用 chip
// ---------------------------------------------------------------------------

/** 渲染一组 [n] 引用 chip；点击交给 ctx.openCitation。 */
export function renderCiteChips(nums, ctx, options) {
  const opts = options || {};
  const c = normalizeCtx(ctx);
  const wrap = el("span", { class: "cite-chips" + (opts.compact ? " is-compact" : "") });
  const seen = new Set();
  for (const raw of asArray(nums)) {
    const n = Number(raw);
    if (!Number.isFinite(n) || seen.has(n)) continue;
    seen.add(n);
    const cite = c.citationByN(n);
    const label = cite && cite.locator ? `${cite.locator}` : "";
    const btn = el(
      "button",
      {
        type: "button",
        class: "cite-chip",
        "data-cite-n": n,
        // 悬停预览用的出处数据直接挂在 chip 上：预览不需要发请求，也不会拿错行
        "data-cite-locator": cite ? String(cite.locator || "") : "",
        "data-cite-title": cite ? String(cite.title || "") : "",
        "data-cite-quote": cite ? String(cite.quote || "") : "",
        title: cite
          ? `[${n}] ${cite.title || ""} ${label}`.trim()
          : `[${n}] 引用不存在`,
        "aria-label": cite
          ? `引用 ${n}，${cite.title || label || "来源"}，打开原文`
          : `引用 ${n} 没有对应来源`,
      },
      [`[${n}]`],
    );
    if (!cite) btn.classList.add("is-missing");
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      c.openCitation(n);
    });
    wrap.appendChild(btn);
  }
  return wrap;
}

// ---------------------------------------------------------------------------
// Markdown 子集渲染：标题 / 段落 / 列表 / 行内 code / 粗斜体 / 链接 / [n] 引用
// ---------------------------------------------------------------------------

const INLINE_RE = /(\*\*[^*]+\*\*|`[^`]+`|\[[0-9]+(?:\s*,\s*[0-9]+)*\]|\[[^\]]+\]\([^)]+\))/g;

function renderInline(text, ctx) {
  const c = normalizeCtx(ctx);
  const frag = document.createDocumentFragment();
  const source = String(text === null || text === undefined ? "" : text);
  let last = 0;
  for (const m of source.matchAll(INLINE_RE)) {
    const token = m[0];
    const at = m.index || 0;
    if (at > last) frag.appendChild(document.createTextNode(source.slice(last, at)));
    last = at + token.length;
    if (token.startsWith("**")) {
      frag.appendChild(el("strong", null, [token.slice(2, -2)]));
    } else if (token.startsWith("`")) {
      frag.appendChild(el("code", { class: "inline-code" }, [token.slice(1, -1)]));
    } else if (/^\[[0-9]/.test(token)) {
      const nums = token.slice(1, -1).split(",").map((s) => Number(s.trim()));
      const chip = renderCiteChips(nums, c, { compact: true });
      if (chip.childElementCount) frag.appendChild(chip);
      else frag.appendChild(document.createTextNode(token));
    } else {
      const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token);
      if (link) {
        const href = safeUrl(link[2]);
        if (!href) {
          // 不安全协议：不生成链接，退化成纯文本（并说明原因），绝不把 javascript: 放进 href
          frag.appendChild(el("span", { class: "md-link md-link--blocked",
                                        title: `已拦截不安全链接：${link[2]}` }, [link[1]]));
        } else {
          const isExternal = /^https?:/i.test(href);
          frag.appendChild(
            el(
              "a",
              {
                href,
                class: "md-link",
                ...(isExternal ? { target: "_blank", rel: "noopener noreferrer" } : {}),
              },
              [link[1]],
            ),
          );
        }
      } else {
        frag.appendChild(document.createTextNode(token));
      }
    }
  }
  if (last < source.length) frag.appendChild(document.createTextNode(source.slice(last)));
  return frag;
}

/** 把受支持的 Markdown 子集渲染成 DOM 片段（不用 innerHTML，天然免疫注入）。 */
export function renderMarkdown(markdown, ctx) {
  const c = normalizeCtx(ctx);
  const root = el("div", { class: "md" });
  const lines = String(markdown === null || markdown === undefined ? "" : markdown).split(/\r?\n/);
  let list = null;
  let ordered = false;
  let paragraph = [];

  const flushParagraph = () => {
    if (!paragraph.length) return;
    root.appendChild(el("p", null, [renderInline(paragraph.join(" "), c)]));
    paragraph = [];
  };
  const flushList = () => {
    if (list) root.appendChild(list);
    list = null;
  };

  for (const rawLine of lines) {
    const line = rawLine.replace(/\s+$/, "");
    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      flushParagraph();
      flushList();
      const level = Math.min(6, heading[1].length + 2);
      root.appendChild(el(`h${level}`, { class: "md-h" }, [renderInline(heading[2], c)]));
      continue;
    }
    const ul = /^\s*[-*•]\s+(.*)$/.exec(line);
    const ol = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (ul || ol) {
      flushParagraph();
      const wantOrdered = Boolean(ol);
      if (!list || ordered !== wantOrdered) {
        flushList();
        ordered = wantOrdered;
        list = el(ordered ? "ol" : "ul", { class: "md-list" });
      }
      list.appendChild(el("li", null, [renderInline((ul || ol)[1], c)]));
      continue;
    }
    const quote = /^>\s?(.*)$/.exec(line);
    if (quote) {
      flushParagraph();
      flushList();
      root.appendChild(el("blockquote", { class: "md-quote" }, [renderInline(quote[1], c)]));
      continue;
    }
    paragraph.push(line.trim());
  }
  flushParagraph();
  flushList();
  return root;
}

// ---------------------------------------------------------------------------
// 组件卡片外壳：类型徽标 + 标题 + 复制 / 收起 + 引用 chip
// ---------------------------------------------------------------------------

function typeLabel(widget) {
  const type = String(widget && widget.type ? widget.type : "unknown");
  return WIDGET_LABELS[type] || type;
}

/**
 * 渲染一个组件为 <article class="widget">。
 * @param {{type:string,data?:object,title?:string,cites?:number[],note?:string,id?:string}} widget
 * @param {object} ctx 见文件头
 * @returns {HTMLElement}
 */
export function renderWidget(widget, ctx) {
  const w = widget && typeof widget === "object" ? widget : { type: "unknown", data: {} };
  const c = normalizeCtx(ctx);
  const type = String(w.type || "unknown");
  const renderer = renderers[type] || renderers.unknown;
  const data = w.data && typeof w.data === "object" ? w.data : {};

  const article = el("article", {
    class: `widget widget--${type.replace(/[^a-z0-9_-]/gi, "")}`,
    "data-widget-type": type,
    "data-widget-id": w.id ? String(w.id) : null,
    // 版面角色由后端下发（primary/support/evidence/aside），界面只按它调轻重
    "data-slot": w.slot ? String(w.slot) : null,
  });

  const rawText = collectWidgetText(type, data);
  const details = el("details", { class: "widget__collapse", open: true });
  const typeText = typeLabel(w);
  const heading = w.title ? String(w.title) : typeText;
  const summary = el("summary", { class: "widget__head" }, [
    // ① 标题已经等于类型名时不再重复一个同词徽标（原来"回答"卡会出现两次"回答"）
    heading === typeText
      ? null
      : el("span", { class: "widget__badge", "aria-hidden": "true" }, [typeText]),
    el("span", { class: "widget__title", title: heading }, [heading]),
    // 动作按钮是 summary 的兄弟节点（summary 内不能嵌 button），这里留出等宽空间。
    el("span", { class: "widget__reserve", "aria-hidden": "true" }),
    el("span", { class: "widget__chevron", html: ICONS.chevron }),
  ]);
  details.appendChild(summary);

  const actions = el("div", { class: "widget__actions" }, [
    el(
      "button",
      {
        type: "button",
        class: "icon-btn js-widget-copy",
        title: "复制这一块的内容",
        "aria-label": `复制${typeLabel(w)}内容`,
        "data-widget-copy": "",
      },
      [el("span", { class: "icon", html: ICONS.copy }), el("span", { class: "icon-btn__text" }, ["复制"])],
    ),
    el(
      "button",
      {
        type: "button",
        class: "icon-btn js-widget-toggle",
        title: "收起或展开这一块",
        "aria-label": `收起或展开${typeLabel(w)}`,
        "aria-expanded": "true",
      },
      [el("span", { class: "icon", html: ICONS.chevron }), el("span", { class: "icon-btn__text" }, ["收起"])],
    ),
  ]);

  const body = el("div", { class: "widget__body" });
  try {
    const rendered = renderer(data, c, w);
    if (rendered) body.appendChild(rendered);
  } catch (err) {
    body.appendChild(renderFailure(type, err, data));
    if (typeof console !== "undefined" && console.error) {
      console.error(`[mentor] 渲染 ${type} 失败`, err);
    }
  }

  if (Array.isArray(w.cites) && w.cites.length) {
    body.appendChild(el("div", { class: "widget__cites" }, [
      el("span", { class: "meta-label" }, ["引用"]),
      renderCiteChips(w.cites, c, { compact: true }),
    ]));
  }
  if (w.note) {
    body.appendChild(el("p", { class: "widget__note" }, [asText(w.note)]));
  }

  details.appendChild(body);
  article.appendChild(details);
  article.appendChild(actions);

  actions.querySelector(".js-widget-copy").addEventListener("click", (event) => {
    event.preventDefault();
    const btn = event.currentTarget;
    c.copy(rawText);
    btn.classList.add("is-done");
    const textNode = btn.querySelector(".icon-btn__text");
    if (textNode) textNode.textContent = "已复制";
    window.setTimeout(() => {
      btn.classList.remove("is-done");
      if (textNode) textNode.textContent = "复制";
    }, 1400);
  });

  const toggle = actions.querySelector(".js-widget-toggle");
  toggle.addEventListener("click", (event) => {
    event.preventDefault();
    setCollapsed(article, details.open);
  });
  // summary 上的点击在部分浏览器里也会触发 toggle，这里统一状态。
  details.addEventListener("toggle", () => {
    toggle.setAttribute("aria-expanded", details.open ? "true" : "false");
    const textNode = toggle.querySelector(".icon-btn__text");
    if (textNode) textNode.textContent = details.open ? "收起" : "展开";
    article.classList.toggle("is-collapsed", !details.open);
  });

  article.__widgetRawText = rawText;
  return article;
}

function setCollapsed(article, collapsed) {
  const details = article.querySelector("details.widget__collapse");
  if (!details) return;
  details.open = !collapsed;
  article.classList.toggle("is-collapsed", collapsed);
}

function renderFailure(type, err, data) {
  return el("div", { class: "widget__failure", role: "note" }, [
    el("p", null, [
      el("span", { class: "icon", html: ICONS.warn }),
      ` ${typeLabel({ type })}渲染出错：${asText((err && err.message) || err)}`,
    ]),
    el("pre", { class: "kv-pre" }, [safeJson(data)]),
  ]);
}

function safeJson(value) {
  try {
    return JSON.stringify(value, null, 2);
  } catch (err) {
    return String(value);
  }
}

/** 复制/导出用的纯文本还原。 */
export function collectWidgetText(type, data) {
  const d = data && typeof data === "object" ? data : {};
  const lines = [];
  const push = (s) => lines.push(s);
  switch (type) {
    case "answer":
      push(asText(d.markdown));
      if (d.confidence) push(`置信度：${asText(d.confidence)}`);
      break;
    case "quote":
      push(asText(d.text));
      if (d.locator) push(`—— ${asText(d.locator)}`);
      break;
    case "sources":
      asArray(d.items).forEach((it) => {
        push(`[${asText(it && it.n)}] ${asText(it && it.title)} ${asText(it && it.locator)}`);
        if (it && it.snippet) push(`    ${asText(it.snippet)}`);
      });
      break;
    case "concept_map":
      push(asArray(d.nodes).map((n) => asText(n && n.label)).filter(Boolean).join(" / "));
      asArray(d.edges).forEach((e) =>
        push(`${asText(e && e.from)} --${asText(e && e.label)}--> ${asText(e && e.to)}`),
      );
      break;
    case "timeline":
      asArray(d.items).forEach((it) => push(`${asText(it && it.at)} ${asText(it && it.label)}：${asText(it && it.detail)}`));
      break;
    case "steps":
      asArray(d.items).forEach((it, i) => push(`${i + 1}. ${asText(it && it.title)}：${asText(it && it.detail)}`));
      break;
    case "compare_table":
      if (Array.isArray(d.columns)) push(d.columns.map(asText).join(" | "));
      asArray(d.rows).forEach((row) => push(asArray(row).map(asText).join(" | ")));
      if (d.caption) push(asText(d.caption));
      break;
    case "cheatsheet":
      asArray(d.items).forEach((it) => push(`${asText(it && it.term)}：${asText(it && it.definition)}`));
      break;
    case "flashcards":
      asArray(d.cards).forEach((card) => push(`${asText(card && card.front)} → ${asText(card && card.back)}`));
      break;
    case "quiz":
      asArray(d.questions).forEach((q, i) => {
        push(`Q${i + 1}. ${asText(q && q.q)}`);
        asArray(q && q.choices).forEach((choice, j) => push(`   ${String.fromCharCode(65 + j)}. ${asText(choice)}`));
        if (q && q.answer_index !== undefined) push(`   答案：${String.fromCharCode(65 + Number(q.answer_index))}`);
        if (q && q.why) push(`   解析：${asText(q.why)}`);
      });
      break;
    case "code":
      push(asText(d.code));
      if (d.note) push(`# ${asText(d.note)}`);
      break;
    case "formula":
      push(asText(d.plain));
      if (d.note) push(asText(d.note));
      break;
    case "image":
      push(`[图片] ${asText(d.src)}`);
      if (d.caption) push(asText(d.caption));
      break;
    case "followups":
      asArray(d.items).forEach((s) => push(`- ${asText(s)}`));
      break;
    case "audio_script":
      asArray(d.segments).forEach((s) => push(`${asText(s && s.speaker)}：${asText(s && s.text)}`));
      break;
    case "progress":
      asArray(d.items).forEach((it) =>
        push(`${asText(it && it.label)} ${Math.round(Number(it && it.value) || 0)}% ${asText(it && it.note)}`));
      break;
    default:
      push(safeJson(d));
  }
  return lines.filter((l) => l !== "").join("\n");
}

// ---------------------------------------------------------------------------
// 各类型渲染器
// ---------------------------------------------------------------------------

function renderAnswer(data, ctx) {
  const d = data || {};
  const wrap = el("div", { class: "answer" });
  const confidence = String(d.confidence || "").toLowerCase();
  if (confidence) {
    const map = { high: "高", medium: "中", low: "低" };
    wrap.appendChild(el("p", { class: "answer__confidence" }, [
      el("span", { class: `pill pill--${confidence}` }, [`置信度 ${map[confidence] || confidence}`]),
    ]));
  }
  wrap.appendChild(renderMarkdown(d.markdown, ctx));
  return wrap;
}

function renderQuote(data, ctx) {
  const d = data || {};
  const c = normalizeCtx(ctx);
  const wrap = el("figure", { class: "quote" }, [
    el("span", { class: "quote__mark", html: ICONS.quote }),
    el("blockquote", { class: "quote__text" }, [asText(d.text)]),
  ]);
  if (d.locator || d.source_id) {
    const btn = el(
      "button",
      {
        type: "button",
        class: "locator-btn",
        title: "在右栏打开原文并跳到这一行",
      },
      [asText(d.locator || d.source_id)],
    );
    btn.addEventListener("click", () =>
      c.openSourceAt({ source_id: d.source_id, locator: d.locator, line: locatorLine(d.locator) }),
    );
    wrap.appendChild(el("figcaption", { class: "quote__caption" }, [el("span", { class: "meta-label" }, ["原文"]), btn]));
  }
  return wrap;
}

function renderSources(data, ctx) {
  const d = data || {};
  const c = normalizeCtx(ctx);
  const list = el("ol", { class: "source-list" });
  asArray(d.items).forEach((item) => {
    const it = item || {};
    const btn = el("button", { type: "button", class: "source-list__open" }, ["打开原文"]);
    btn.addEventListener("click", () =>
      c.openSourceAt({
        source_id: it.source_id,
        chunk_id: it.chunk_id,
        locator: it.locator,
        line: locatorLine(it.locator),
      }),
    );
    list.appendChild(
      el("li", { class: "source-list__item" }, [
        el("div", { class: "source-list__head" }, [
          el("span", { class: "source-list__n" }, [`[${asText(it.n)}]`]),
          el("span", { class: "source-list__title" }, [asText(it.title)]),
          it.locator ? el("code", { class: "locator-code" }, [asText(it.locator)]) : null,
        ]),
        it.snippet ? el("p", { class: "source-list__snippet" }, [asText(it.snippet)]) : null,
        el("div", { class: "source-list__actions" }, [
          btn,
          el(
            "button",
            {
              type: "button",
              class: "ghost-btn js-copy-snippet",
              title: "复制这条片段",
            },
            ["复制片段"],
          ),
        ]),
      ]),
    );
  });
  list.querySelectorAll(".js-copy-snippet").forEach((btn, i) => {
    const it = asArray(d.items)[i] || {};
    btn.addEventListener("click", () => c.copy(asText(it.snippet || it.title)));
  });
  return list;
}

function renderConceptMap(data) {
  const d = data || {};
  const nodes = asArray(d.nodes).map((n, i) => {
    const node = n || {};
    return {
      id: asText(node.id || `n${i}`),
      label: asText(node.label || node.id || `节点${i + 1}`),
      group: asText(node.group || "其他"),
      weight: Number(node.weight) || 1,
    };
  });
  const edges = asArray(d.edges).map((e) => ({
    from: asText(e && e.from),
    to: asText(e && e.to),
    label: asText(e && e.label),
  }));
  if (!nodes.length) return el("p", { class: "empty-line" }, ["没有节点"]);

  // 布局：每个 group 一列，节点是写全标签的圆角矩形（不做文字截断），
  // 列宽与行高都由标签长度推导，所以任何 label 都不会被裁掉。
  const groups = [];
  nodes.forEach((n) => {
    if (!groups.includes(n.group)) groups.push(n.group);
  });
  const groupsPerRow = groups.length <= 3 ? groups.length : Math.ceil(Math.sqrt(groups.length));
  const groupRows = Math.ceil(groups.length / groupsPerRow);
  const BOX_H = 32;
  const NODE_GAP = 10;      // 同列节点之间的空隙
  const HEADER = 24;        // 分组标题占高
  const COL_GAP = 68;       // 列间留白，给跨列连线与边标签
  const PAD = 14;
  const LABEL_PX = 12.5;

  const boxWidthOf = (label) => Math.ceil(textUnits(label) * LABEL_PX + 26);

  const colWidths = [];
  for (let c = 0; c < groupsPerRow; c += 1) {
    let widest = 96;
    groups.forEach((group, gi) => {
      if (gi % groupsPerRow !== c) return;
      widest = Math.max(widest, Math.ceil(textUnits(group) * LABEL_PX + 8));
      nodes.filter((n) => n.group === group).forEach((n) => {
        widest = Math.max(widest, boxWidthOf(n.label));
      });
    });
    colWidths[c] = widest;
  }
  const colX = [];
  let cursor = PAD;
  for (let c = 0; c < groupsPerRow; c += 1) {
    colX[c] = cursor + colWidths[c] / 2;
    cursor += colWidths[c] + COL_GAP;
  }
  const width = Math.round(cursor - COL_GAP + PAD);

  let maxMembers = 1;
  groups.forEach((group) => {
    maxMembers = Math.max(maxMembers, nodes.filter((n) => n.group === group).length);
  });
  const rowHeight = HEADER + PAD * 2 + maxMembers * BOX_H + (maxMembers - 1) * NODE_GAP;
  const height = Math.round(groupRows * rowHeight);
  const stackHeight = maxMembers * BOX_H + (maxMembers - 1) * NODE_GAP;

  const pos = new Map();
  groups.forEach((group, gi) => {
    const members = nodes.filter((n) => n.group === group);
    const col = gi % groupsPerRow;
    const row = Math.floor(gi / groupsPerRow);
    const block = members.length * BOX_H + (members.length - 1) * NODE_GAP;
    const top = row * rowHeight + HEADER + PAD + (stackHeight - block) / 2;
    members.forEach((n, mi) => {
      const w = Math.max(96, Math.min(colWidths[col], boxWidthOf(n.label)));
      const y = top + mi * (BOX_H + NODE_GAP);
      pos.set(n.id, {
        x: colX[col] - w / 2, y, w, h: BOX_H,
        cx: colX[col], cy: y + BOX_H / 2,
      });
    });
  });

  const svg = svgEl("svg", {
    class: "cmap",
    viewBox: `0 0 ${width} ${height}`,
    style: `aspect-ratio:${width} / ${height};max-height:${Math.min(620, height)}px`,
    role: "img",
    "aria-label": `概念图，${nodes.length} 个节点，${edges.length} 条关系`,
    preserveAspectRatio: "xMidYMid meet",
  });

  // 分组标题
  groups.forEach((group, gi) => {
    const col = gi % groupsPerRow;
    const row = Math.floor(gi / groupsPerRow);
    svg.appendChild(
      svgEl("text", {
        x: colX[col],
        y: row * rowHeight + 17,
        class: "cmap__group",
        "text-anchor": "middle",
      }, [group]),
    );
  });

  const defs = svgEl("defs", null, [
    svgEl("marker", {
      id: "cmap-arrow", viewBox: "0 0 10 10", refX: "9", refY: "5",
      markerWidth: "6", markerHeight: "6", orient: "auto-start-reverse",
    }, [svgEl("path", { d: "M 0 0 L 10 5 L 0 10 z", class: "cmap__arrowhead" })]),
  ]);
  svg.appendChild(defs);

  // 边：从源框中心指向目标框中心，两端按矩形边界裁掉，箭头落在框外
  edges.forEach((edge) => {
    const a = pos.get(edge.from);
    const b = pos.get(edge.to);
    if (!a || !b) return;
    const dx = b.cx - a.cx;
    const dy = b.cy - a.cy;
    const len = Math.hypot(dx, dy) || 1;
    const ux = dx / len;
    const uy = dy / len;
    const onEdge = (box, sign) => {
      const tx = Math.abs(ux) < 1e-6 ? Infinity : box.w / 2 / Math.abs(ux);
      const ty = Math.abs(uy) < 1e-6 ? Infinity : box.h / 2 / Math.abs(uy);
      const t = Math.min(tx, ty) + 3;
      return { x: box.cx + sign * ux * t, y: box.cy + sign * uy * t };
    };
    const p1 = onEdge(a, 1);
    const p2 = onEdge(b, -1);
    svg.appendChild(svgEl("line", {
      x1: p1.x, y1: p1.y, x2: p2.x, y2: p2.y, class: "cmap__edge", "marker-end": "url(#cmap-arrow)",
    }));
    if (edge.label) {
      const mx = (p1.x + p2.x) / 2;
      const my = (p1.y + p2.y) / 2;
      // ⑥ 边标签 pill 按 12.5px 字号反推宽度（原按 12px 算，字变大后会挤）
      const w = Math.ceil(textUnits(edge.label) * 12.9 + 16);
      svg.appendChild(svgEl("rect", {
        x: mx - w / 2, y: my - 10, width: w, height: 19, rx: 9, class: "cmap__edge-bg",
      }));
      svg.appendChild(svgEl("text", {
        x: mx, y: my + 3, class: "cmap__edge-label", "text-anchor": "middle",
      }, [edge.label]));
    }
  });

  // 节点：写全标签的圆角矩形（权重体现为边框粗细）
  nodes.forEach((node) => {
    const p = pos.get(node.id);
    if (!p) return;
    // ⑤ 节点终态显式标 ready：虚线只属于加载态，流式结束后必须统一实线
    const group = svgEl("g", {
      class: "cmap__node", "data-weight": String(node.weight), "data-state": "ready",
    });
    group.appendChild(svgEl("rect", {
      x: p.x, y: p.y, width: p.w, height: p.h, rx: 8,
      class: "cmap__node-box",
      "stroke-width": String(1.6 + Math.min(3, Math.max(0, node.weight)) * 0.3),
    }));
    group.appendChild(svgEl("text", {
      x: p.x + p.w / 2, y: p.y + p.h / 2 + 4, class: "cmap__node-label", "text-anchor": "middle",
    }, [node.label]));
    group.appendChild(svgEl("title", null, [`${node.label}（${node.group}，权重 ${node.weight}）`]));
    svg.appendChild(group);
  });

  return el("div", { class: "cmap-wrap" }, [svg]);
}

/** 中英混排按 em 估宽：CJK/全角约 1em，拉丁字母约 0.6em。 */
function textUnits(text) {
  return Array.from(asText(text)).reduce((acc, ch) => acc + (ch.charCodeAt(0) > 0x2e80 ? 1 : 0.6), 0);
}

function renderTimeline(data) {
  const d = data || {};
  const list = el("ol", { class: "timeline" });
  asArray(d.items).forEach((item) => {
    const it = item || {};
    list.appendChild(
      el("li", { class: "timeline__item" }, [
        el("span", { class: "timeline__dot", "aria-hidden": "true" }),
        el("div", { class: "timeline__body" }, [
          el("div", { class: "timeline__head" }, [
            it.at ? el("code", { class: "timeline__at" }, [asText(it.at)]) : null,
            el("span", { class: "timeline__label" }, [asText(it.label)]),
          ]),
          it.detail ? el("p", { class: "timeline__detail" }, [asText(it.detail)]) : null,
        ]),
      ]),
    );
  });
  return list;
}

function renderSteps(data) {
  const d = data || {};
  const list = el("ol", { class: "steps" });
  asArray(d.items).forEach((item, i) => {
    const it = item || {};
    list.appendChild(
      el("li", { class: "steps__item" }, [
        el("span", { class: "steps__n", "aria-hidden": "true" }, [String(i + 1)]),
        el("div", { class: "steps__body" }, [
          el("div", { class: "steps__title" }, [asText(it.title)]),
          it.detail ? el("p", { class: "steps__detail" }, [asText(it.detail)]) : null,
        ]),
      ]),
    );
  });
  return list;
}

function renderCompareTable(data) {
  const d = data || {};
  const columns = asArray(d.columns).map(asText);
  const rows = asArray(d.rows).map((row) => asArray(row).map(asText));
  const wrap = el("div", { class: "table-wrap", tabindex: "0", role: "region", "aria-label": "对比表格，可横向滚动" });
  const table = el("table", { class: "table" });
  if (columns.length) {
    table.appendChild(
      el("thead", null, [
        el("tr", null, columns.map((col) => el("th", { scope: "col" }, [col]))),
      ]),
    );
  }
  table.appendChild(
    el("tbody", null, rows.map((row) =>
      el("tr", null, row.map((cell, i) =>
        i === 0 ? el("th", { scope: "row" }, [cell]) : el("td", null, [cell])))),
    ),
  );
  wrap.appendChild(table);
  const out = el("figure", { class: "figure" }, [wrap]);
  if (d.caption) out.appendChild(el("figcaption", { class: "figure__caption" }, [asText(d.caption)]));
  return out;
}

function renderCheatsheet(data) {
  const d = data || {};
  const list = el("dl", { class: "cheatsheet" });
  asArray(d.items).forEach((item) => {
    const it = item || {};
    list.appendChild(el("div", { class: "cheatsheet__row" }, [
      el("dt", { class: "cheatsheet__term" }, [asText(it.term)]),
      el("dd", { class: "cheatsheet__def" }, [asText(it.definition)]),
    ]));
  });
  return list;
}

function renderFlashcards(data) {
  const d = data || {};
  const grid = el("ul", { class: "flashcards", role: "list" });
  asArray(d.cards).forEach((card) => {
    const c = card || {};
    const face = el("span", { class: "flashcard__face" }, [asText(c.front)]);
    const back = el("span", { class: "flashcard__back" }, [asText(c.back)]);
    const flip = el(
      "button",
      {
        type: "button",
        class: "flashcard",
        "aria-pressed": "false",
        "aria-label": `闪卡：${asText(c.front)}，按下翻面`,
      },
      [face, back],
    );
    flip.addEventListener("click", () => {
      const open = flip.getAttribute("aria-pressed") === "true";
      flip.setAttribute("aria-pressed", open ? "false" : "true");
    });
    grid.appendChild(el("li", { class: "flashcards__cell" }, [flip]));
  });
  return grid;
}

function renderQuiz(data) {
  const d = data || {};
  const wrap = el("div", { class: "quiz" });
  const runId = Math.random().toString(36).slice(2, 8);
  asArray(d.questions).forEach((question, qi) => {
    const q = question || {};
    const choices = asArray(q.choices).map(asText);
    const answer = Number(q.answer_index);
    const box = el("fieldset", { class: "quiz__q" }, [
      el("legend", { class: "quiz__legend" }, [`${qi + 1}. ${asText(q.q)}`]),
    ]);
    const why = el("p", { class: "quiz__why", hidden: true, id: `quiz-why-${runId}-${qi}` }, [
      el("span", { class: "meta-label" }, ["解析"]),
      asText(q.why || "（未提供解析）"),
    ]);
    choices.forEach((choice, ci) => {
      const input = el("input", {
        type: "radio",
        name: `quiz-${runId}-${qi}`,
        value: String(ci),
        id: `quiz-${runId}-${qi}-${ci}`,
      });
      const label = el("label", { class: "quiz__choice", for: input.id }, [
        input,
        el("span", { class: "quiz__letter", "aria-hidden": "true" }, [String.fromCharCode(65 + ci)]),
        el("span", { class: "quiz__choice-text" }, [choice]),
      ]);
      input.setAttribute("aria-describedby", why.id);
      input.addEventListener("change", () => {
        box.querySelectorAll(".quiz__choice").forEach((node) => node.classList.remove("is-correct", "is-wrong"));
        label.classList.add(ci === answer ? "is-correct" : "is-wrong");
        why.hidden = false;
      });
      box.appendChild(label);
    });
    box.appendChild(why);
    wrap.appendChild(box);
  });
  return wrap;
}

function renderCode(data) {
  const d = data || {};
  const figure = el("figure", { class: "code-block" }, [
    el("div", { class: "code-block__bar" }, [
      el("span", { class: "code-block__lang" }, [asText(d.language || "text")]),
    ]),
    el("pre", { class: "code-block__pre", tabindex: "0" }, [
      el("code", null, [asText(d.code)]),
    ]),
  ]);
  if (d.note) figure.appendChild(el("figcaption", { class: "code-block__note" }, [asText(d.note)]));
  return figure;
}

function renderFormula(data) {
  const d = data || {};
  // 零依赖：不引 KaTeX，直接给排好版的纯文本公式（等宽 + 居中）。
  const figure = el("figure", { class: "formula" }, [
    el("div", { class: "formula__plain", role: "math", "aria-label": asText(d.plain) }, [
      el("code", null, [asText(d.plain)]),
    ]),
  ]);
  if (d.latex) {
    figure.appendChild(el("details", { class: "formula__latex" }, [
      el("summary", null, ["LaTeX 源码"]),
      el("pre", { class: "kv-pre" }, [asText(d.latex)]),
    ]));
  }
  if (d.note) figure.appendChild(el("figcaption", { class: "formula__note" }, [asText(d.note)]));
  return figure;
}

function renderImage(data, ctx) {
  const d = data || {};
  const rawSrc = asText(d.src).trim();
  // 图片只允许 http(s)/相对路径/内嵌 data:image —— 别的协议一律当"没有图片"
  const src = safeUrl(rawSrc, { allowDataImage: true });
  const figure = el("figure", { class: "image" });
  if (!src) {
    figure.appendChild(el("div", { class: "image__placeholder" }, [
      el("span", { class: "icon image__placeholder-icon", html: ICONS.image }),
      el("p", null, [rawSrc ? "这一块引用的图片地址不安全，已拦截。" : "这一块没有可显示的图片（src 为空）。"]),
      rawSrc ? el("code", { class: "locator-code" }, [rawSrc]) : null,
    ]));
  } else {
    const img = el("img", { class: "image__img", src, alt: asText(d.caption) || "讲义插图", loading: "lazy" });
    img.addEventListener("error", () => {
      const holder = el("div", { class: "image__placeholder" }, [
        el("span", { class: "icon image__placeholder-icon", html: ICONS.image }),
        el("p", null, ["图片无法加载（离线或路径不可达）。"]),
        el("code", { class: "locator-code" }, [src]),
      ]);
      img.replaceWith(holder);
      if (ctx && typeof ctx.announce === "function") ctx.announce("插图加载失败");
    });
    figure.appendChild(img);
  }
  if (d.caption) figure.appendChild(el("figcaption", { class: "image__caption" }, [asText(d.caption)]));
  return figure;
}

function renderFollowups(data, ctx) {
  const d = data || {};
  const c = normalizeCtx(ctx);
  const wrap = el("ul", { class: "followups", role: "list" });
  asArray(d.items).forEach((item) => {
    const text = asText(item);
    const btn = el("button", { type: "button", class: "followup", title: `继续追问：${text}` }, [
      el("span", { class: "followup__text" }, [text]),
    ]);
    btn.addEventListener("click", () => {
      if (c.ask) c.ask(text);
      else c.announce(`追问：${text}（当前环境没有提问通道）`);
    });
    wrap.appendChild(el("li", null, [btn]));
  });
  return wrap;
}

function renderAudioScript(data) {
  const d = data || {};
  const wrap = el("div", { class: "audio" });
  const segments = asArray(d.segments);
  const words = segments.reduce((acc, s) => acc + asText(s && s.text).length, 0);
  const minutes = Math.max(1, Math.round(words / 240));
  wrap.appendChild(el("p", { class: "audio__meta" }, [
    el("span", { class: "icon", html: ICONS.play }),
    `共 ${segments.length} 段 · 约 ${minutes} 分钟口播`,
  ]));
  const list = el("ol", { class: "audio__list" });
  segments.forEach((segment, i) => {
    const s = segment || {};
    list.appendChild(el("li", { class: "audio__segment" }, [
      el("span", { class: "audio__n" }, [`${i + 1}`]),
      el("div", { class: "audio__body" }, [
        el("span", { class: "audio__speaker" }, [asText(s.speaker || "讲述")]),
        el("p", { class: "audio__text" }, [asText(s.text)]),
      ]),
    ]));
  });
  wrap.appendChild(list);
  return wrap;
}

function renderProgress(data) {
  const d = data || {};
  const list = el("ul", { class: "progress-list", role: "list" });
  asArray(d.items).forEach((item) => {
    const it = item || {};
    const value = Math.max(0, Math.min(100, Number(it.value) || 0));
    const bar = el("div", {
      class: "progress__bar",
      role: "progressbar",
      "aria-valuemin": "0",
      "aria-valuemax": "100",
      "aria-valuenow": String(Math.round(value)),
      "aria-label": `${asText(it.label)} 完成 ${Math.round(value)}%`,
    }, [el("span", { class: "progress__fill", style: `width:${value}%` })]);
    list.appendChild(el("li", { class: "progress__item" }, [
      el("div", { class: "progress__head" }, [
        el("span", { class: "progress__label" }, [asText(it.label)]),
        el("span", { class: "progress__value" }, [`${Math.round(value)}%`]),
      ]),
      bar,
      it.note ? el("p", { class: "progress__note" }, [asText(it.note)]) : null,
    ]));
  });
  return list;
}

/** 主题 × 材料覆盖矩阵：颜色越深说明该主题在这份材料里出现越多。 */
function renderHeatmap(data) {
  const d = data || {};
  const rows = asArray(d.rows).map((row) => asText(row));
  const columns = asArray(d.columns).map((col) => asText(col));
  const cells = asArray(d.cells);
  if (!rows.length || !columns.length) {
    return renderUnknown(data, null, { type: "heatmap" });
  }
  const head = el("tr", null, [
    el("th", { scope: "col", class: "heatmap__corner" }, ["主题"]),
    ...columns.map((col) => el("th", { scope: "col" }, [col])),
  ]);
  const body = el("tbody");
  rows.forEach((label, ri) => {
    const row = el("tr", null, [el("th", { scope: "row" }, [label])]);
    const rowCells = asArray(cells[ri]);
    columns.forEach((col, ci) => {
      const raw = Number(rowCells[ci]);
      const value = Number.isFinite(raw) ? Math.max(0, Math.min(1, raw)) : 0;
      row.appendChild(el("td", {
        class: "heatmap__cell",
        style: `--heat:${value.toFixed(2)}`,
        title: `${label} × ${col}：${value.toFixed(2)}`,
      }, [
        el("span", { class: "heatmap__value" }, [value > 0 ? value.toFixed(2) : "·"]),
      ]));
    });
    body.appendChild(row);
  });
  const table = el("table", {
    class: "heatmap",
    "aria-label": d.caption || "主题与材料的覆盖矩阵",
  }, [el("thead", null, [head]), body]);
  const wrap = el("div", { class: "heatmap-wrap" }, [table]);
  if (d.caption) wrap.appendChild(el("p", { class: "heatmap__caption" }, [asText(d.caption)]));
  return wrap;
}

/** 未知类型：可折叠 + 显示原始 JSON，绝不抛错。 */
function renderUnknown(data, ctx, widget) {
  const c = normalizeCtx(ctx);
  const type = asText((widget && widget.type) || "unknown");
  const wrap = el("div", { class: "unknown" }, [
    el("p", { class: "unknown__hint" }, [
      el("span", { class: "icon", html: ICONS.warn }),
      ` 界面不认识组件类型 “${type}”，已保留原始数据。`,
    ]),
  ]);
  const details = el("details", { class: "unknown__raw" }, [
    el("summary", null, ["查看原始 data"]),
    el("pre", { class: "kv-pre", tabindex: "0" }, [safeJson(data)]),
  ]);
  wrap.appendChild(details);
  const copyBtn = el("button", { type: "button", class: "ghost-btn" }, ["复制原始 JSON"]);
  copyBtn.addEventListener("click", () => c.copy(safeJson(data)));
  wrap.appendChild(copyBtn);
  return wrap;
}

// ---------------------------------------------------------------------------
// 映射表（导出给 app.js / preview.html）
// ---------------------------------------------------------------------------

export const renderers = {
  answer: renderAnswer,
  quote: renderQuote,
  sources: renderSources,
  concept_map: renderConceptMap,
  timeline: renderTimeline,
  steps: renderSteps,
  compare_table: renderCompareTable,
  cheatsheet: renderCheatsheet,
  flashcards: renderFlashcards,
  quiz: renderQuiz,
  code: renderCode,
  formula: renderFormula,
  image: renderImage,
  followups: renderFollowups,
  audio_script: renderAudioScript,
  progress: renderProgress,
  heatmap: renderHeatmap,
  unknown: renderUnknown,
};

/** 骨架占位：plan 事件到达时先画出"将要出现的组件"。 */
export function renderWidgetSkeleton(type, title) {
  const type_ = String(type || "unknown");
  const article = el("article", { class: "widget widget--skeleton", "data-widget-type": type_, "aria-hidden": "true" }, [
    el("div", { class: "widget__head" }, [
      el("span", { class: "widget__badge" }, [WIDGET_LABELS[type_] || type_]),
      el("span", { class: "widget__title" }, [title || WIDGET_LABELS[type_] || type_]),
    ]),
    el("div", { class: "widget__body" }, [
      el("div", { class: "skel skel--lg" }),
      el("div", { class: "skel skel--md" }),
      el("div", { class: "skel skel--sm" }),
    ]),
  ]);
  return article;
}
