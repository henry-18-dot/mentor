"""离线 provider：不调用任何模型，用确定性规则从检索片段里"组装"出界面。

存在的理由有三个：
1. 没配 API key 也能把全链路跑通（演示、CI、离线机器）；
2. 模型返回不合法内容时是安全网，界面永远不会空；
3. 它是"引用必须可回溯"的下限实现——每句话都直接来自材料原文，不编造。

它不做的事也要说清楚：它是**抽取式**的，不做改写、不做推理链、不做跨材料综合。
那部分是 `openai_compat` 的活。
"""

from __future__ import annotations

import random
import re
import zlib
from dataclasses import dataclass, field
from typing import Any, Iterable

from .. import textutil as tu
from .base import GenerateRequest, Provider

_HEAD_RE = re.compile(r"^\s{0,3}#{1,6}\s*")
_BULLET_RE = re.compile(r"^\s{0,3}([-*+]|\d+[.)])\s+")
_CODE_RE = re.compile(r"```([a-zA-Z0-9+#]*)\n(.*?)```", re.S)
_CODEISH_RE = re.compile(r"^\s*(def |class |import |from .+ import |>>> |\$ )|torch\.|np\.|=")
_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b")
_DEF_RE = re.compile(
    r"^(?P<term>[^。.!?;；]{2,42}?)\s*(?:是|指|表示|定义为|:|：)\s*(?P<body>.{6,})",
)
_DEF_EN_RE = re.compile(
    r"^(?P<term>[A-Z][^.!?;]{1,40}?)\s+(?:is|are|refers to|means|denotes)\s+(?P<body>.{6,})"
)
_IMAGE_RE = re.compile(r"^(https?://\S+\.(?:png|jpe?g|gif|webp|svg)|[\w./\-]+\.(?:png|jpe?g|gif|webp|svg))$", re.I)


@dataclass
class Item:
    text: str
    n: int
    locator: str
    title: str
    source_id: str
    line: int = 0
    kind: str = "prose"
    score: float = 0.0
    raw: str = ""

    @property
    def clean(self) -> str:
        return _clean_sentence(self.text)


def _clean_sentence(text: str) -> str:
    line = _HEAD_RE.sub("", text)
    line = _BULLET_RE.sub("", line)
    line = tu.strip_markdown(line)
    return " ".join(line.split()).strip()


class Pool:
    """把检索片段拆成可挑选的句子，并给出与问题的相关度。"""

    def __init__(self, context: list[dict], terms: Iterable[str], keyphrases: Iterable[str] = ()) -> None:
        self.terms = [t.lower() for t in terms if len(t) > 1]
        self.keyphrases = [k.lower() for k in keyphrases if len(k) > 1]
        self.items: list[Item] = []
        seen: set[str] = set()
        for block in context:
            raw_text = block.get("text", "")
            for sent in tu.split_sentences(raw_text):
                body = sent.text.strip()
                if len(_clean_sentence(body)) < 18:
                    continue
                key = _clean_sentence(body)[:60].lower()
                if key in seen:
                    continue
                seen.add(key)
                item = Item(text=body, n=int(block.get("n", 1)), locator=block.get("locator", ""),
                            title=block.get("title", ""), source_id=block.get("source_id", ""),
                            line=int(block.get("line", 0) or 0), kind=block.get("kind", "prose"),
                            raw=raw_text)
                item.score = self.score(item) + _rank_prior(item.n, len(context))
                self.items.append(item)

    def score(self, item: Item) -> float:
        low = item.text.lower()
        hits = sum(low.count(term) for term in self.terms)
        bonus = sum(0.6 for phrase in self.keyphrases if phrase in low)
        length_penalty = 1.0 + len(item.text) / 600.0
        return (hits + bonus) / length_penalty

    def top(self, k: int = 5, minimum: float = 0.0) -> list[Item]:
        ranked = [i for i in self.items if i.score > minimum]
        ranked.sort(key=lambda i: (-i.score, i.locator, i.text))
        return ranked[:k]

    def containing(self, needle: str, k: int = 3) -> list[Item]:
        low = needle.lower()
        found = [i for i in self.items if low in i.text.lower()]
        found.sort(key=lambda i: (-i.score, i.locator))
        return found[:k]

    def definitions(self, limit: int = 10) -> list[Item]:
        out: list[Item] = []
        for item in sorted(self.items, key=lambda i: (-i.score, i.locator)):
            if _def_parts(item.text):
                out.append(item)
            if len(out) >= limit:
                break
        return out

    def with_years(self) -> list[Item]:
        out = [i for i in self.items if tu.has_year(i.text)]
        out.sort(key=lambda i: (tu.years_in(i.text)[0], i.locator))
        return out

    def numbers(self) -> list[Item]:
        return [i for i in self.items if _NUMBER_RE.search(i.text) and len(i.text) < 220]


def _def_parts(text: str) -> tuple[str, str] | None:
    clean = _clean_sentence(text)
    match = _DEF_RE.match(clean) or _DEF_EN_RE.match(clean)
    if not match:
        return None
    term = match.group("term").strip(" *`\"'")
    body = match.group("body").strip()
    if len(term) < 2 or len(body) < 6 or len(term) > 42:
        return None
    return term, body


def _rank_prior(n: int, total: int) -> float:
    """检索排名靠前的片段，里面的句子更值得入选（抽取式回答的排序依据）。"""
    if total <= 1:
        return 0.0
    return 0.6 * (total - n) / (total - 1)


def _cite(prefix: str, item: Item) -> str:
    return f"{prefix} [{item.n}]"


def _short(text: str, width: int = 90) -> str:
    flat = " ".join(_clean_sentence(text).split())
    return flat[:width] + ("…" if len(flat) > width else "")


class OfflineProvider(Provider):
    name = "offline"

    def available(self) -> bool:
        return True

    def generate(self, request: GenerateRequest) -> list[dict[str, Any]]:
        pool = Pool(request.context, tu.content_tokens(request.question), request.keyphrases)
        builders = {
            "answer": self.answer,
            "quote": self.quote,
            "sources": self.sources,
            "concept_map": self.concept_map,
            "timeline": self.timeline,
            "steps": self.steps,
            "compare_table": self.compare_table,
            "cheatsheet": self.cheatsheet,
            "flashcards": self.flashcards,
            "quiz": self.quiz,
            "code": self.code,
            "formula": self.formula,
            "image": self.image,
            "followups": self.followups,
            "audio_script": self.audio_script,
            "progress": self.progress,
            "heatmap": self.heatmap,
        }
        widgets: list[dict[str, Any]] = []
        for wtype in request.planned:
            builder = builders.get(wtype)
            if builder is None:
                continue
            widget = builder(request, pool)
            if widget:
                widgets.append(widget)
        return widgets

    # ------------------------------------------------------------- 正文类
    def answer(self, req: GenerateRequest, pool: Pool) -> dict[str, Any] | None:
        top = pool.top(5)
        if not top:
            return None
        cites = sorted({item.n for item in top})
        confidence = "high" if len(top) >= 3 else ("medium" if len(top) == 2 else "low")
        if req.intent == "compare":
            lead = "材料里对这几个对象的说法（方括号是来源编号）："
        elif req.intent == "study":
            lead = "先把材料里能直接考的点列出来："
        elif req.intent == "brief":
            lead = "相关材料分成这几块："
        else:
            lead = f"材料里有 {len(top)} 段直接相关的内容，要点如下："
        lines = [lead, ""]
        for item in top:
            lines.append(f"- {_short(item.text, 180)} [{item.n}]")
        if req.intent == "brief":
            by_title: dict[str, list[Item]] = {}
            for item in top:
                by_title.setdefault(item.title or item.locator, []).append(item)
            lines = ["按材料分组：", ""]
            for title, items in by_title.items():
                lines.append(f"**{title}**")
                for item in items:
                    lines.append(f"- {_short(item.text, 160)} [{item.n}]")
                lines.append("")
        return {"type": "answer", "title": "回答", "cites": cites,
                "data": {"markdown": "\n".join(lines).strip(), "confidence": confidence}}

    def quote(self, req: GenerateRequest, pool: Pool) -> dict[str, Any] | None:
        top = pool.top(1)
        if not top:
            return None
        item = top[0]
        return {"type": "quote", "title": "原文", "cites": [item.n],
                "data": {"text": _clean_sentence(item.text), "locator": item.locator,
                         "source_id": item.source_id}}

    def sources(self, req: GenerateRequest, pool: Pool) -> dict[str, Any] | None:
        if not req.context:
            return None
        terms = [t for t in req.question.lower().split() if len(t) > 1]
        items = []
        for block in req.context:
            snippet = tu.strip_markdown(tu.snippet(block.get("text", ""), terms, width=200))
            items.append({"n": block.get("n"), "locator": block.get("locator", ""),
                          "title": block.get("title", ""), "snippet": snippet,
                          "source_id": block.get("source_id", "")})
        return {"type": "sources", "title": "来源", "cites": [i["n"] for i in items],
                "data": {"items": items}}

    # ------------------------------------------------------------- 结构类
    def concept_map(self, req: GenerateRequest, pool: Pool) -> dict[str, Any] | None:
        top = pool.top(6)
        text = "\n".join(i.text for i in top)
        candidates: dict[str, tuple[str, int]] = {}
        for term in tu.keyphrases(text, limit=20) + list(req.keyphrases):
            low = term.lower()
            if not low or low in candidates:
                continue
            count = sum(1 for item in pool.items if low in item.text.lower())
            # SM / TB 这类两字母缩写多半是量纲或噪声，只在反复出现时才留。
            if count == 0 or (len(term) < 3 and count < 2):
                continue
            candidates[low] = (term, count)
        ranked = sorted(candidates.values(), key=lambda pair: -pair[1])
        labels = [term for term, _ in ranked[:10]]
        if len(labels) < 3:
            return None
        nodes = []
        for label in labels:
            group = "概念"
            for item in pool.items:
                if label.lower() in item.text.lower():
                    group = item.title or group
                    break
            weight = sum(1 for item in pool.items if label.lower() in item.text.lower())
            nodes.append({"id": re.sub(r"\W+", "-", label.lower()).strip("-") or label,
                          "label": label, "group": group, "weight": float(max(1, weight))})
        by_id = {n["id"]: n["label"] for n in nodes}
        edges: list[dict[str, Any]] = []
        for i, left in enumerate(nodes):
            for right in nodes[i + 1:]:
                shared = [item for item in pool.items
                          if left["label"].lower() in item.text.lower()
                          and right["label"].lower() in item.text.lower()]
                if shared:
                    edges.append({"from": left["id"], "to": right["id"],
                                  "label": f"同现 {len(shared)}"})
        edges.sort(key=lambda e: -int(e["label"].split()[-1]))
        if not edges:
            first = nodes[0]["id"]
            edges = [{"from": first, "to": n["id"], "label": "同段出现"} for n in nodes[1:6]]
        return {"type": "concept_map", "title": "概念关系", "cites": sorted({i.n for i in top}),
                "data": {"nodes": nodes, "edges": edges[:20]}}

    def timeline(self, req: GenerateRequest, pool: Pool) -> dict[str, Any] | None:
        items = pool.with_years()
        if len(items) < 2:
            return None
        out = []
        for item in items[:10]:
            year = tu.years_in(item.text)[0]
            out.append({"at": year, "label": _short(item.text, 60),
                        "detail": _cite(_short(item.text, 150), item)})
        return {"type": "timeline", "title": "时间线", "cites": sorted({i.n for i in items[:10]}),
                "data": {"items": out}}

    def steps(self, req: GenerateRequest, pool: Pool) -> dict[str, Any] | None:
        numbered: list[tuple[str, Item]] = []
        for item in pool.items:
            for line in item.raw.split("\n"):
                match = re.match(r"^\s*(?:\d+[.)]|第[一二三四五六七八九十]步|Step\s*\d+[:.]?)\s*(.+)", line)
                if match and len(match.group(1).strip()) > 8:
                    numbered.append((match.group(1).strip(), item))
        out: list[dict[str, str]] = []
        if len(numbered) >= 2:
            for i, (body, item) in enumerate(numbered[:8], start=1):
                out.append({"title": f"第 {i} 步", "detail": _cite(_short(body, 160), item)})
        else:
            top = pool.top(4)
            if len(top) < 2:
                return None
            for i, item in enumerate(top, start=1):
                out.append({"title": f"第 {i} 步（材料出现顺序）",
                            "detail": _cite(_short(item.text, 160), item)})
        cites = sorted({i.n for i in pool.top(6)})
        return {"type": "steps", "title": "步骤", "cites": cites, "data": {"items": out}}

    def compare_table(self, req: GenerateRequest, pool: Pool) -> dict[str, Any] | None:
        left, right = _two_entities(req.question, pool)
        if not left or not right:
            return None
        left_items = pool.containing(left)
        right_items = pool.containing(right)
        if not left_items or not right_items:
            return None
        rows = [
            ["材料里的定义", _define(left, left_items), _define(right, right_items)],
            ["最相关的一段", _short(left_items[0].text, 120), _short(right_items[0].text, 120)],
            ["出处", left_items[0].locator, right_items[0].locator],
        ]
        cites = sorted({left_items[0].n, right_items[0].n})
        return {"type": "compare_table", "title": f"{left} 与 {right}", "cites": cites,
                "data": {"columns": ["维度", left, right], "rows": rows,
                         "caption": "两边的说法都来自材料原文，未做推断"}}

    # ------------------------------------------------------------- 学习类
    def cheatsheet(self, req: GenerateRequest, pool: Pool) -> dict[str, Any] | None:
        defs = pool.definitions(limit=10)
        if not defs:
            return None
        items = []
        for item in defs:
            parts = _def_parts(item.text)
            if not parts:
                continue
            term, body = parts
            items.append({"term": term, "definition": _cite(_short(body, 140), item)})
        if not items:
            return None
        return {"type": "cheatsheet", "title": "速查表", "cites": sorted({i.n for i in defs}),
                "data": {"items": items}}

    def flashcards(self, req: GenerateRequest, pool: Pool) -> dict[str, Any] | None:
        defs = pool.definitions(limit=10)
        cards = []
        for item in defs:
            parts = _def_parts(item.text)
            if not parts:
                continue
            term, body = parts
            cards.append({"front": term, "back": _cite(_short(body, 160), item)})
        if len(cards) < 2:
            return None
        return {"type": "flashcards", "title": "闪卡", "cites": sorted({i.n for i in defs}),
                "data": {"cards": cards[:10]}}

    def quiz(self, req: GenerateRequest, pool: Pool) -> dict[str, Any] | None:
        defs = pool.definitions(limit=8)
        parsed = [(*(_def_parts(item.text) or ("", "")), item) for item in defs]
        parsed = [p for p in parsed if p[0] and p[1]]
        questions: list[dict[str, Any]] = []
        rng = random.Random(zlib.crc32(req.question.encode("utf-8")))
        for term, body, item in parsed[:3]:
            others = [_short(b, 60) for t, b, _ in parsed if t != term][:4]
            if len(others) < 2:
                continue
            choices = [_short(body, 60)] + others[:2]
            rng.shuffle(choices)
            questions.append({
                "q": f"「{term}」指的是什么？",
                "choices": choices,
                "answer_index": choices.index(_short(body, 60)),
                "why": f"{item.locator}：{_short(item.text, 140)} [{item.n}]",
            })
        for item in pool.numbers():
            if len(questions) >= 4:
                break
            match = _NUMBER_RE.search(item.text)
            if not match or match.group(0) in ("0", "1", "2"):
                continue
            answer = match.group(0)
            blanked = _clean_sentence(item.text).replace(answer, "____", 1)
            if "____" not in blanked or len(blanked) > 180:
                continue
            distractors = _numeric_distractors(answer)
            choices = [answer] + distractors
            rng.shuffle(choices)
            questions.append({
                "q": f"补全材料里的这个数字：{blanked}",
                "choices": choices,
                "answer_index": choices.index(answer),
                "why": f"{item.locator} 原文给出的值是 {answer} [{item.n}]",
            })
        if not questions:
            return None
        return {"type": "quiz", "title": "自测", "cites": sorted({i.n for i in defs}),
                "data": {"questions": questions[:5]}}

    def heatmap(self, req: GenerateRequest, pool: Pool) -> dict[str, Any] | None:
        """主题 × 材料的覆盖矩阵：一眼看出哪个主题在哪份材料里讲得多。"""
        order: list[str] = []
        titles: dict[str, str] = {}
        for block in req.context:
            sid = block.get("source_id", "?")
            if sid not in titles:
                # 用文件名当列名：讲义标题常常就是当前小节，容易误读。
                titles[sid] = str(block.get("locator", "")).split(":")[0] or sid
                order.append(sid)
        topics = [k for k in req.keyphrases if len(k) > 1][:6]
        if len(order) < 2 or len(topics) < 2:
            return None
        cells: list[list[float]] = []
        for topic in topics:
            low = topic.lower()
            row = []
            for sid in order:
                count = sum(1 for item in pool.items
                            if item.source_id == sid and low in item.text.lower())
                row.append(round(min(1.0, count / 3.0), 2))
            cells.append(row)
        return {"type": "heatmap", "title": "主题 × 材料", "cites": [],
                "data": {"rows": topics, "columns": [titles[sid] or sid for sid in order],
                         "cells": cells, "caption": "颜色越深，表示这个主题在这份材料里出现越多"}}

    def progress(self, req: GenerateRequest, pool: Pool) -> dict[str, Any] | None:
        by_source: dict[str, dict[str, Any]] = {}
        for block in req.context:
            entry = by_source.setdefault(block.get("source_id", "?"),
                                         {"title": block.get("title") or block.get("locator", ""),
                                          "locator": block.get("locator", ""), "count": 0})
            entry["count"] += 1
        if len(by_source) < 2:
            return None
        total = sum(e["count"] for e in by_source.values())
        items = []
        for source_id, entry in sorted(by_source.items(), key=lambda kv: -kv[1]["count"]):
            share = entry["count"] / total
            items.append({"label": entry["title"] or source_id,
                          "value": round(share, 2),
                          "note": f"命中 {entry['count']} 段 · {entry['locator']}"})
        return {"type": "progress", "title": "材料覆盖", "cites": [], "data": {"items": items}}

    # ------------------------------------------------------------- 代码/公式
    def code(self, req: GenerateRequest, pool: Pool) -> dict[str, Any] | None:
        for block in req.context:
            text = block.get("text", "")
            fenced = _CODE_RE.search(text)
            if fenced:
                return {"type": "code", "title": "讲义代码", "cites": [block["n"]],
                        "data": {"language": fenced.group(1) or "text",
                                 "code": fenced.group(2).strip()[:1600],
                                 "note": f"来自 {block.get('locator', '')}"}}
        for item in pool.items:
            lines = [ln for ln in item.raw.split("\n") if _CODEISH_RE.search(ln)]
            if len(lines) >= 2:
                return {"type": "code", "title": "讲义代码", "cites": [item.n],
                        "data": {"language": _guess_lang(lines), "code": "\n".join(lines[:14])[:1600],
                                 "note": f"来自 {item.locator}"}}
        return None

    def formula(self, req: GenerateRequest, pool: Pool) -> dict[str, Any] | None:
        for item in pool.items:
            for line in item.raw.split("\n"):
                body = _clean_sentence(line)
                if "=" in body and re.search(r"[A-Za-z]\s*=", body) and len(body) < 120:
                    return {"type": "formula", "title": "公式", "cites": [item.n],
                            "data": {"plain": body, "latex": "", "note": f"来自 {item.locator}"}}
        return None

    def image(self, req: GenerateRequest, pool: Pool) -> dict[str, Any] | None:
        for block in req.context:
            if block.get("kind") != "image":
                continue
            src = " ".join(block.get("text", "").split())
            if _IMAGE_RE.match(src):
                return {"type": "image", "title": "讲义插图", "cites": [block["n"]],
                        "data": {"src": src, "caption": block.get("title", "")}}
        return None

    # ------------------------------------------------------------- 生成类
    def followups(self, req: GenerateRequest, pool: Pool) -> dict[str, Any] | None:
        terms = [k for k in req.keyphrases if len(k) > 2][:4]
        if not terms:
            return None
        items = [f"{terms[0]} 和 {terms[1]} 的关系是什么？" if len(terms) > 1 else f"{terms[0]} 是什么？"]
        if len(terms) > 1:
            items.append(f"为什么需要 {terms[1]}？")
        items.append(f"{terms[0]} 在这门课里对应哪个作业？")
        if len(terms) > 2:
            items.append(f"把 {terms[2]} 展开讲一遍")
        return {"type": "followups", "title": "接着可以问", "cites": [], "data": {"items": items[:4]}}

    def audio_script(self, req: GenerateRequest, pool: Pool) -> dict[str, Any] | None:
        top = pool.top(6)
        if not top:
            return None
        segments = [{"speaker": "主持", "text": f"今天这段材料围绕「{req.question}」展开，我们挑几个要点过一遍。"}]
        for i, item in enumerate(top):
            speaker = "主持" if i % 2 == 0 else "讲解"
            segments.append({"speaker": speaker, "text": f"{_short(item.text, 180)}（{item.locator}）"})
        segments.append({"speaker": "讲解", "text": "以上内容都来自材料原文，界面右侧可以直接跳回原文核对。"})
        return {"type": "audio_script", "title": "音频概览脚本", "cites": sorted({i.n for i in top}),
                "data": {"segments": segments}}


def _define(term: str, items: list[Item]) -> str:
    for item in items:
        parts = _def_parts(item.text)
        if parts and term.lower() in parts[0].lower():
            return _short(parts[1], 110)
    return _short(items[0].text, 110)


def _two_entities(question: str, pool: Pool) -> tuple[str, str]:
    """从问句里取两个比较对象；取不到就用材料里最相关的两个术语。"""
    parts = re.split(r"\s+(?:vs\.?|versus)\s+|和|与|、|对比|相比|区别|difference between", question, flags=re.I)
    candidates = [p.strip(" ?？。，,.") for p in parts if len(p.strip()) > 2]
    named: list[str] = []
    for cand in candidates:
        tokens = [t for t in tu.keyphrases(cand, limit=2)]
        named.extend(tokens or ([cand] if len(cand) <= 24 else []))
    named = list(dict.fromkeys(named))
    if len(named) >= 2:
        return named[0], named[1]
    likely: list[str] = []
    for item in pool.top(8):
        for token in tu.keyphrases(item.text, limit=3):
            if token.lower() not in {t.lower() for t in likely}:
                likely.append(token)
    if len(likely) >= 2:
        return likely[0], likely[1]
    return "", ""


def _numeric_distractors(answer: str) -> list[str]:
    try:
        value = float(answer)
    except ValueError:
        return ["0", "1"]
    out = []
    for factor in (0.5, 2.0, 10.0):
        candidate = value * factor
        text = f"{candidate:g}"
        if text != answer and text not in out:
            out.append(text)
    while len(out) < 2:
        out.append(f"{value + len(out) + 1:g}")
    return out[:2]


def _guess_lang(lines: list[str]) -> str:
    joined = "\n".join(lines)
    if re.search(r"\b(import|def|class|self)\b", joined):
        return "python"
    if re.search(r"\b(const|let|function|=>)\b", joined):
        return "javascript"
    return "text"
