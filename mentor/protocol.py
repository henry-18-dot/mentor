"""mentor 的数据契约。

所有层（摄取 / 检索 / 编排 / 服务 / 界面）只通过这里定义的对象通信。
两条硬约束：

1. **引用必须可回溯**：任何回答里的 [n] 都能查到 chunk → 原文件 → 行号。
2. **组件是封闭集合**：模型只能在 WIDGET_TYPES 里选类型，界面只渲染这些类型，
   所以"智能 UI"生成出来的东西永远在可控范围内。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from .mdcode import shield_code

# 允许 `[1]`、`[1,2]`、`[1、2]`、`[ 1 ]`；紧跟 `(` 的是 markdown 链接，不是引用。
_CITE_GROUP_RE = re.compile(r"\[\s*(\d+(?:\s*[,，、]\s*\d+)*)\s*\](?!\()")


# --------------------------------------------------------------------------
# 组件目录：智能界面的"识字表"
# --------------------------------------------------------------------------
# key = 组件类型；value = data 字段形状（给模型看，也给界面作者看）。
WIDGET_TYPES: dict[str, str] = {
    "answer": '{"markdown": str, "confidence": "high"|"medium"|"low"}',
    "quote": '{"text": str, "locator": str, "source_id": str}',
    "sources": '{"items": [{"n": int, "locator": str, "title": str, "snippet": str, "source_id": str}]}',
    "concept_map": '{"nodes": [{"id": str, "label": str, "group": str, "weight": float}], '
                   '"edges": [{"from": str, "to": str, "label": str}]}',
    "timeline": '{"items": [{"at": str, "label": str, "detail": str}]}',
    "steps": '{"items": [{"title": str, "detail": str}]}',
    "compare_table": '{"columns": [str], "rows": [[str]], "caption": str}',
    "cheatsheet": '{"items": [{"term": str, "definition": str}]}',
    "flashcards": '{"cards": [{"front": str, "back": str}]}',
    "quiz": '{"questions": [{"q": str, "choices": [str], "answer_index": int, "why": str}]}',
    "code": '{"language": str, "code": str, "note": str}',
    "formula": '{"plain": str, "latex": str, "note": str}',
    "image": '{"src": str, "caption": str}',
    "followups": '{"items": [str]}',
    "audio_script": '{"segments": [{"speaker": str, "text": str}]}',
    "progress": '{"items": [{"label": str, "value": float, "note": str}]}',
    "heatmap": '{"rows": [str], "columns": [str], "cells": [[float]], "caption": str}',
}

# 常用组合。规划器选组合，界面按组合决定版面重心。
LAYOUTS: dict[str, str] = {
    "focus": "一问一答：正文 + 引用 + 来源",
    "explore": "概念展开：概念图 + 正文 + 卡片",
    "study": "复习：速查表 + 闪卡 + 自测",
    "brief": "梳理：时间线 / 步骤 + 正文",
    "compare": "对比：表格 + 正文",
    "listen": "音频概览：脚本 + 来源",
}

# 协议版本：新增组件类型时递增。旧客户端遇到不认识的类型必须降级渲染
# （界面里的 `unknown` 兜底），而不是崩布局或静默丢弃数据。
PROTOCOL_VERSION = "0.1"

# 组件的版面角色：由后端下发，界面不猜。primary=正文主体，support=支撑细节，
# evidence=证据（原文/来源），aside=可略过的辅助内容。
SLOT_BY_TYPE: dict[str, str] = {
    "answer": "primary",
    "compare_table": "primary",
    "cheatsheet": "primary",
    "concept_map": "primary",
    "heatmap": "primary",
    "steps": "primary",
    "audio_script": "primary",
    "code": "support",
    "formula": "support",
    "timeline": "support",
    "quote": "evidence",
    "sources": "evidence",
    "progress": "evidence",
    "image": "evidence",
    "flashcards": "aside",
    "quiz": "aside",
    "followups": "aside",
}
SLOTS = ("primary", "support", "evidence", "aside")


def slot_for(widget_type: str) -> str:
    return SLOT_BY_TYPE.get(widget_type, "primary")

# 界面右侧面板能显示的类型（有原文可跳转的类型）。
LOCATABLE_KINDS = ("lecture-note", "markdown", "text", "code", "notebook", "html", "pdf")


def stable_id(prefix: str, *parts: str) -> str:
    """由内容决定的稳定 id：同样的输入永远得到同样的 id。"""
    digest = hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


# --------------------------------------------------------------------------
# 材料与片段
# --------------------------------------------------------------------------
@dataclass
class Span:
    """一段文字在源文件里的位置。line 为 0 表示该格式拿不到行号。"""

    start: int
    end: int
    line: int = 0
    section: str = ""
    source_id: str = ""

    def locator(self, path: str = "") -> str:
        """人能读的位置串，例如 `lecture_01.py:66`。"""
        name = path or self.source_id
        if self.line:
            return f"{name}:{self.line}"
        return f"{name}@{self.start}-{self.end}"

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "line": self.line,
                "section": self.section, "source_id": self.source_id}


@dataclass
class Chunk:
    """检索与引用的最小单位。"""

    id: str
    source_id: str
    text: str
    span: Span
    heading: str = ""
    kind: str = "prose"
    order: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "source_id": self.source_id, "text": self.text,
                "span": self.span.to_dict(), "heading": self.heading,
                "kind": self.kind, "order": self.order}


@dataclass
class Hit:
    """一次检索结果。why 说明它为什么被选中，界面直接展示。

    `matches` 是命中词在 `chunk.text` 里的绝对偏移（term/start/end），
    界面用它做行内高亮——只标"这一行命中了"信息量不够。
    """

    chunk: Chunk
    score: float
    why: str = ""
    matched: list[str] = field(default_factory=list)
    matches: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"chunk": self.chunk.to_dict(), "score": round(self.score, 3),
                "why": self.why, "matched": self.matched, "matches": self.matches}


@dataclass
class Source:
    """一份被摄取的材料。text 是归一化全文，行号与 offsets 对应它。

    `meta` 可能很大（讲义会把原文件原文放进 `meta["raw_text"]`，供右栏显示真实行号），
    所以 `to_dict()` 默认只带轻量元数据，只有打开单份材料时才带上全文与原文。
    """

    id: str
    path: str
    title: str
    kind: str
    text: str
    meta: dict[str, Any] = field(default_factory=dict)
    added_at: str = field(default_factory=now_iso)
    bytes: int = 0

    @property
    def locator_root(self) -> str:
        return self.path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]

    def light_meta(self, limit: int = 400) -> dict[str, Any]:
        """去掉超长字符串（原文、line_map 这类），只留能进列表页的元数据。"""
        out: dict[str, Any] = {}
        for key, value in self.meta.items():
            if isinstance(value, str) and len(value) > limit:
                out[key] = f"<{len(value)} 字符>"
            elif isinstance(value, list) and len(value) > 24:
                out[key] = f"<{len(value)} 项>"
            else:
                out[key] = value
        return out

    def raw_text(self) -> str:
        """原始文件文本（有的话）。右栏按真实行号显示，靠它。"""
        raw = self.meta.get("raw_text")
        return raw if isinstance(raw, str) and raw else self.text

    def display_text(self) -> str:
        """给界面看的原文。

        只有"行号=真实行"的材料才把 `raw_text` 交给右栏：PDF 的 `raw_text` 是
        **二进制载荷**（单字节恒等映射存的，用来重放分页解析）、ipynb 的是 notebook JSON——
        两者交给右栏都会渲染成一堆乱码（实测 PDF 有 4.8 万"行"乱码）。
        其它情况退回抽出来的正文。
        """
        if self.meta.get("line_means", "line") == "line":
            return self.raw_text()
        return self.text or self.raw_text()

    def to_dict(self, with_text: bool = False) -> dict[str, Any]:
        data = {"id": self.id, "path": self.path, "title": self.title,
                "kind": self.kind, "meta": self.light_meta(), "added_at": self.added_at,
                "bytes": self.bytes or len(self.text.encode("utf-8"))}
        if with_text:
            data["text"] = self.text
            display = self.display_text()
            # 只有"有额外原文可比"时才带 raw（PDF/ipynb 的 display 就是正文，不用重复传）
            if display and display != self.text:
                data["raw"] = display
            data["meta"] = self.meta
        return data


@dataclass
class Notebook:
    """一本笔记本：一组材料 + 问答历史。对标 NotebookLM 的 notebook。"""

    id: str
    title: str
    created_at: str = field(default_factory=now_iso)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title,
                "created_at": self.created_at, "meta": self.meta}


@dataclass
class Citation:
    """回答里的 [n]。"""

    n: int
    chunk_id: str
    source_id: str
    locator: str
    title: str = ""
    quote: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"n": self.n, "chunk_id": self.chunk_id, "source_id": self.source_id,
                "locator": self.locator, "title": self.title, "quote": self.quote}


# --------------------------------------------------------------------------
# 智能界面的最小单元
# --------------------------------------------------------------------------
@dataclass
class Widget:
    """界面的一块。type 必须在 WIDGET_TYPES 里，cites 指向 surface.citations 的序号。

    `slot` 是后端给出的版面角色（primary/support/evidence/aside），留空时按类型推断。
    """

    type: str
    data: dict[str, Any]
    title: str = ""
    cites: list[int] = field(default_factory=list)
    id: str = ""
    note: str = ""
    slot: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id or stable_id("w", self.type, json.dumps(self.data, sort_keys=True, ensure_ascii=False)),
                "type": self.type, "title": self.title, "data": self.data,
                "cites": self.cites, "note": self.note,
                "slot": self.slot if self.slot in SLOTS else slot_for(self.type)}


@dataclass
class Surface:
    """一次回答的完整界面描述。前端渲染它，导出也用它。"""

    id: str
    question: str
    layout: str
    widgets: list[Widget]
    citations: list[Citation] = field(default_factory=list)
    provider: str = "offline"
    intent: str = "ask"
    degraded: bool = False
    notes: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "question": self.question, "layout": self.layout,
                "widgets": [w.to_dict() for w in self.widgets],
                "citations": [c.to_dict() for c in self.citations],
                "provider": self.provider, "intent": self.intent,
                "degraded": self.degraded, "notes": self.notes,
                "protocol": PROTOCOL_VERSION,
                "created_at": self.created_at}


@dataclass
class Turn:
    """一次问答的存档。"""

    id: str
    notebook_id: str
    question: str
    surface: Surface
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "notebook_id": self.notebook_id,
                "question": self.question, "surface": self.surface.to_dict(),
                "created_at": self.created_at}


# --------------------------------------------------------------------------
# 校验：生成式界面的安全网
# --------------------------------------------------------------------------
def widget_problems(widget: Widget | dict[str, Any], known_types: Iterable[str] | None = None) -> list[str]:
    """检查单个组件：类型是否在目录里、必填字段是否齐全。"""
    known = set(known_types or WIDGET_TYPES)
    data = widget.data if isinstance(widget, Widget) else widget.get("data") or {}
    wtype = widget.type if isinstance(widget, Widget) else widget.get("type", "")
    problems: list[str] = []
    if wtype not in known:
        return [f"未知组件类型 {wtype!r}"]
    if not isinstance(data, dict) or not data:
        return [f"{wtype} 缺少 data"]

    required: dict[str, tuple[str, type]] = {
        "answer": ("markdown", str),
        "quote": ("text", str),
        "sources": ("items", list),
        "concept_map": ("nodes", list),
        "timeline": ("items", list),
        "steps": ("items", list),
        "compare_table": ("columns", list),
        "cheatsheet": ("items", list),
        "flashcards": ("cards", list),
        "quiz": ("questions", list),
        "code": ("code", str),
        "formula": ("plain", str),
        "image": ("src", str),
        "followups": ("items", list),
        "audio_script": ("segments", list),
        "progress": ("items", list),
        "heatmap": ("rows", list),
    }
    key, kind = required[wtype]
    if key not in data:
        problems.append(f"{wtype} 缺少字段 {key}")
    elif not isinstance(data[key], kind):
        problems.append(f"{wtype}.{key} 类型应为 {kind.__name__}")
    elif kind is list and not data[key]:
        problems.append(f"{wtype}.{key} 为空")
    return problems


def surface_problems(surface: Surface) -> list[str]:
    """检查整个界面：组件合法、引用序号存在、正文里的引用也在范围内。"""
    problems: list[str] = []
    if surface.layout not in LAYOUTS:
        problems.append(f"未知版面 {surface.layout!r}")
    if not surface.widgets:
        problems.append("界面没有任何组件")
    valid = {c.n for c in surface.citations}
    for widget in surface.widgets:
        problems.extend(widget_problems(widget))
        for n in widget.cites:
            if n not in valid:
                problems.append(f"组件 {widget.type} 引用了不存在的来源 [{n}]")
        if widget.type == "answer":
            # 正文里的 [n] 也必须落在本次 citations 内（enforce 之外的第二道闸）。
            # 先屏蔽代码区：`y = a[0]`、`x[1]` 不是引用，误报会让整篇回答被标降级。
            markdown, _blocks, _nonce = shield_code(str(widget.data.get("markdown", "")))
            for group in _CITE_GROUP_RE.findall(markdown):
                for raw in re.split(r"[,，、]", group):
                    if raw.strip().isdigit() and int(raw) not in valid:
                        problems.append(f"正文引用了不存在的来源 [{int(raw)}]")
    if not any(w.type == "answer" for w in surface.widgets):
        problems.append("缺少 answer 组件")
    return problems


def dumps(obj: Any) -> str:
    if hasattr(obj, "to_dict"):
        obj = obj.to_dict()
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
