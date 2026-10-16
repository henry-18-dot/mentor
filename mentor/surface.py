"""智能界面层：意图 → 版面 → 组件清单 → 校验后的组件对象。

"智能 UI"的核心是这里：界面不是固定模板，而是每次问答现场决定的组件组合。
但自由度被两件事框住——组件类型来自 `protocol.WIDGET_TYPES`（封闭集合），
组件的字段与引用编号要过校验（`normalize`）。模型可以编排，不可以越界。
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from . import textutil as tu
from .ground import Grounding
from .protocol import (LAYOUTS, WIDGET_TYPES, Citation, Hit, Surface, Widget,
                       now_iso, stable_id, widget_problems)

# 意图 → 版面。mode 由用户显式选择，没有 mode 时从问句里猜。
MODES = ("ask", "brief", "explore", "study", "compare", "listen")
INTENT_TO_LAYOUT = {
    "ask": "focus",
    "brief": "brief",
    "explore": "explore",
    "study": "study",
    "compare": "compare",
    "listen": "listen",
}

INTENT_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("listen", ("音频", "播客", "podcast", "audio overview", "听力")),
    ("study", ("复习", "测验", "自测", "背", "记忆", "考题", "出题", "几道题", "题目", "做题",
               "flashcard", "quiz", "memorize", "刷题")),
    ("compare", ("对比", "区别", "差异", "相比", " vs ", "versus", "difference", "compare", "哪个更好")),
    ("brief", ("梳理", "概览", "总结", "大纲", "提纲", "要点", "有哪些", "流程",
               "summarize", "summary", "overview", "outline", "walk me through")),
    ("explore", ("概念", "关系", "为什么", "原理", "怎么工作", "是什么", "如何",
                 "why", "what is", "how does", "explain", "intuition")),
]

# 每种意图默认要哪些组件。顺序即界面上的顺序。
PLAN_TEMPLATES: dict[str, list[str]] = {
    "focus": ["answer", "quote", "sources", "followups"],
    "brief": ["answer", "steps", "timeline", "progress", "sources"],
    "explore": ["answer", "concept_map", "heatmap", "quote", "cheatsheet", "sources", "followups"],
    "study": ["answer", "cheatsheet", "flashcards", "quiz", "progress", "sources", "followups"],
    "compare": ["answer", "compare_table", "quote", "sources", "followups"],
    "listen": ["audio_script", "answer", "sources"],
}

MAX_NODES = 14
MAX_EDGES = 22
MAX_ROWS = 8
MAX_CARDS = 10
MAX_QUESTIONS = 5
MAX_ITEMS = 14
MAX_SOURCES = 6

_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_CODEISH_RE = re.compile(r"(```|^\s*(def|class|import|from|\$|>>>)\s|\bdef \w+\(|torch\.|\bnp\.)")


def detect_intent(question: str, mode: str | None = None) -> str:
    """用户选择的模式优先；否则按问句里的线索判意图。"""
    if mode in MODES:
        return mode
    low = f" {question.lower()} "
    for intent, hints in INTENT_HINTS:
        if any(h in low for h in hints):
            return intent
    return "ask"


def plan_widgets(intent: str, question: str, hits: list[Hit],
                 *, source_count: int = 1) -> list[str]:
    """决定这次界面要摆哪些组件。材料里没有的东西不要摆出来。"""
    layout = INTENT_TO_LAYOUT.get(intent, "focus")
    planned = list(PLAN_TEMPLATES[layout])

    text_of_hits = "\n".join(hit.chunk.text for hit in hits)
    if "timeline" in planned and not _YEAR_RE.search(text_of_hits) and not _has_ordered_steps(hits):
        planned.remove("timeline")
    if "progress" in planned and source_count < 2:
        planned.remove("progress")
    if "heatmap" in planned and source_count < 2:
        planned.remove("heatmap")
    if _CODEISH_RE.search(text_of_hits) and "code" not in planned:
        planned.insert(1, "code")
    if any(hit.chunk.kind == "image" for hit in hits) and "image" not in planned:
        planned.append("image")
    if intent in ("study", "explore") and not _has_definitions(hits):
        for name in ("flashcards", "cheatsheet", "quiz"):
            if name in planned and len(hits) < 2:
                planned.remove(name)
    return planned


def choose_layout(intent: str, hits: list[Hit], question: str) -> str:
    """在意图基础上做一次微调：材料不支持的版面降级。"""
    layout = INTENT_TO_LAYOUT.get(intent, "focus")
    if layout == "compare" and len(hits) < 2:
        return "focus"
    if layout == "brief" and not hits:
        return "focus"
    return layout if layout in LAYOUTS else "focus"


def _has_definitions(hits: list[Hit]) -> bool:
    return any(re.search(r"\b(is|are|refers to|means)\b|是指|表示|定义为", h.chunk.text) for h in hits)


def _has_ordered_steps(hits: list[Hit]) -> bool:
    for hit in hits:
        lines = [ln for ln in hit.chunk.text.split("\n") if ln.strip()]
        numbered = sum(1 for ln in lines if re.match(r"^\s*(\d+[.)]|第[一二三四五]步|Step\s*\d)", ln))
        if numbered >= 2:
            return True
    return False


# --------------------------------------------------------------------------
# 校验：把模型（或本地生成器）给的东西收进契约
# --------------------------------------------------------------------------
def normalize(raw_widgets: Iterable[Any], grounding: Grounding) -> tuple[list[Widget], list[str]]:
    """过滤非法组件、裁剪超长数据、修正越界引用。返回 (组件, 说明)。"""
    valid_cites = grounding.valid_numbers()
    out: list[Widget] = []
    notes: list[str] = []
    for raw in raw_widgets:
        if isinstance(raw, Widget):
            widget = raw
        elif isinstance(raw, dict):
            widget = Widget(type=str(raw.get("type", "")), data=raw.get("data") or {},
                            title=str(raw.get("title", "")),
                            cites=[int(n) for n in (raw.get("cites") or []) if isinstance(n, (int, str)) and str(n).isdigit()],
                            note=str(raw.get("note", "")))
        else:
            notes.append("丢弃了一个格式不对的组件")
            continue
        problems = widget_problems(widget)
        if problems:
            notes.append(f"丢弃组件 {widget.type or '?'}：{problems[0]}")
            continue
        widget.data = _trim(widget.type, widget.data)
        bad = [n for n in widget.cites if n not in valid_cites]
        if bad:
            notes.append(f"{widget.type} 上删除了 {len(bad)} 个越界引用")
        widget.cites = [n for n in widget.cites if n in valid_cites]
        widget.id = widget.id or stable_id("w", widget.type, str(len(out)), str(widget.title))
        out.append(widget)
    return out, notes


def _trim(wtype: str, data: dict) -> dict:
    """统一裁剪：组件再长，界面也不至于被一条数据撑爆。"""
    limits = {
        "concept_map": ("nodes", MAX_NODES), "timeline": ("items", MAX_ITEMS),
        "steps": ("items", MAX_ITEMS), "cheatsheet": ("items", MAX_ITEMS),
        "flashcards": ("cards", MAX_CARDS), "quiz": ("questions", MAX_QUESTIONS),
        "sources": ("items", MAX_SOURCES), "followups": ("items", 6),
        "audio_script": ("segments", 40), "progress": ("items", 8),
        "heatmap": ("rows", 6),
    }
    if wtype in limits:
        key, cap = limits[wtype]
        if isinstance(data.get(key), list) and len(data[key]) > cap:
            data[key] = data[key][:cap]
    if wtype == "concept_map" and isinstance(data.get("edges"), list):
        data["edges"] = data["edges"][:MAX_EDGES]
    if wtype == "compare_table" and isinstance(data.get("rows"), list):
        data["rows"] = data["rows"][:MAX_ROWS]
    if wtype == "quiz":
        questions: list[dict] = []
        for q in data.get("questions") or []:
            if not isinstance(q, dict) or not q.get("q") or not isinstance(q.get("choices"), list):
                continue
            choices = [str(c) for c in q["choices"]][:5]
            if len(choices) < 2:
                continue
            idx = q.get("answer_index", 0)
            try:
                idx = int(idx)
            except (TypeError, ValueError):
                idx = 0
            q["choices"] = choices
            q["answer_index"] = min(max(idx, 0), len(choices) - 1)
            questions.append(q)
        data["questions"] = questions
    return data


_PLACEHOLDER = {
    "study": "这次按复习材料组织：要点在速查表里，自测题可以用来检验记没记住。",
    "brief": "这次按梳理组织：下面按材料出现的顺序列出要点。",
    "listen": "这次按音频概览组织：下面是可朗读的脚本。",
    "compare": "材料里没有同时讲到要对比的两个对象，先看现有来源。",
}


def ensure_minimum(widgets: list[Widget], planned: list[str], grounding: Grounding,
                   *, layout: str = "focus") -> list[Widget]:
    """正文必须有；引用来源必须有。少的东西在这里补上，避免界面出现空版面。

    补出来的组件本身也必须合法：早先这里补的是 `{"items": []}`，结果安全网
    自己产出了一个违约组件（`sources.items 为空`）。现在用 citations 填充。
    """
    present = {w.type for w in widgets}
    if "answer" not in present:
        widgets.insert(0, Widget(
            type="answer",
            title="回答",
            data={"markdown": _PLACEHOLDER.get(
                      layout, "材料里没有找到足以回答这个问题的内容。可以从下面的来源里挑一段继续追问。"),
                  "confidence": "low"},
            cites=[c.n for c in grounding.citations[:1]],
        ))
    if "sources" not in present and grounding.citations:
        items = [{"n": cite.n, "locator": cite.locator, "title": cite.title,
                  "snippet": cite.quote, "source_id": cite.source_id}
                 for cite in grounding.citations]
        widgets.append(Widget(type="sources", title="来源", data={"items": items},
                              cites=[cite.n for cite in grounding.citations]))
    return widgets


import itertools
import os

_ASK_SEQ = itertools.count(1)


def _ask_nonce() -> str:
    """每次提问的唯一后缀。

    为什么必须有：`Surface.id` 由 `stable_id(..., question, created_at, provider)` 算出，而
    `created_at` 只精确到秒。于是"同一个问题在同一秒内问两次"会算出**同一个 id**，
    `store.save_turn` 的 `INSERT OR REPLACE` 就把上一轮覆盖掉了 —— 实测 200 次提问只留下 26 轮，
    历史会静默丢失（连点两次发送、脚本里循环提问都会中招）。
    带上 pid 是为了多进程（两个 server 共用一个库）也不撞。
    """
    return f"{os.getpid()}-{next(_ASK_SEQ)}"


def build_surface(question: str, intent: str, layout: str, widgets: list[Widget],
                  grounding: Grounding, *, provider: str = "offline",
                  degraded: bool = False, notes: list[str] | None = None,
                  created_at: str = "") -> Surface:
    all_notes = list(notes or [])
    return Surface(
        id=stable_id("sf", question, created_at or now_iso(), provider, _ask_nonce()),
        question=question, layout=layout, widgets=widgets,
        citations=list(grounding.citations), provider=provider, intent=intent,
        degraded=degraded, notes=all_notes, created_at=created_at or now_iso(),
    )


def describe_plan(intent: str, layout: str, planned: list[str]) -> str:
    """给界面用的一句话计划说明。"""
    names = {
        "answer": "正文", "quote": "原文引用", "sources": "来源", "concept_map": "概念图",
        "timeline": "时间线", "steps": "步骤", "compare_table": "对比表", "cheatsheet": "速查表",
        "flashcards": "闪卡", "quiz": "自测题", "code": "代码", "formula": "公式",
        "image": "讲义插图", "followups": "追问建议", "audio_script": "音频脚本",
        "progress": "材料覆盖",
    }
    parts = "、".join(names.get(t, t) for t in planned)
    return f"按「{LAYOUTS.get(layout, layout)}」编排：{parts}（意图 {intent}）"


def widget_catalog_markdown() -> str:
    """把组件目录渲染成给模型看的说明。"""
    lines = ["| 组件 | data 字段 |", "|---|---|"]
    for name, shape in WIDGET_TYPES.items():
        lines.append(f"| `{name}` | `{shape}` |")
    return "\n".join(lines)


def citation_rows(grounding: Grounding) -> list[dict]:
    return [c.to_dict() for c in grounding.citations]


def keywords_of(hits: list[Hit], limit: int = 10) -> list[str]:
    return tu.keyphrases("\n".join(h.chunk.text for h in hits), limit=limit)
