"""编排层：一次提问从检索到界面的事件流。

固定流程（每一步都是一个可观察事件，界面据此即时反应）：

    plan    → 意图、版面、这次要摆哪些组件（先给出骨架）
    retrieval → 命中了哪些片段、为什么命中
    widget  → 逐个组件产出（模型流式时是真流式，离线时是瞬间）
    surface → 完整对象校正一次
    done

接地校验发生在 `widget` 之前：正文里越界的引用会被删掉，没引用会补上，
没被材料支撑的句子会进 notes。
"""

from __future__ import annotations

import logging
from typing import Any, Iterator

from . import surface as surface_mod
from .ground import Grounding
from .index import merge_hits
from .llm import GenerateRequest, Provider, ProviderError
from .llm.offline import OfflineProvider
from .protocol import (PROTOCOL_VERSION, Chunk, Hit, Surface, Turn, Widget, now_iso,
                       slot_for, surface_problems)
from .store import Store

log = logging.getLogger(__name__)

MAX_HITS = 8
MAX_QUESTION = 400


class Tutor:
    """一本笔记本的问答编排器。无状态：所有状态都在 Store 里。"""

    def __init__(self, store: Store, provider: Provider | None = None, *,
                 max_hits: int = MAX_HITS) -> None:
        self.store = store
        self.provider = provider or OfflineProvider()
        self.fallback = OfflineProvider()
        self.max_hits = max_hits

    # ---------------------------------------------------------------- 事件流
    def ask_stream(self, notebook_id: str, question: str, mode: str | None = None, *,
                   context_turns: int = 2) -> Iterator[dict[str, Any]]:
        """`context_turns>0` 时把最近几轮引用过的片段带进本轮上下文。

        "展开第 2 点""继续"这类追问和材料没有词面重合，纯检索会得到空上下文；
        带上上一轮引用过的块，追问才接得上。
        """
        question = (question or "").strip()[:MAX_QUESTION]
        notebook = self.store.get_notebook(notebook_id)
        if notebook is None:
            yield _ev("error", message=f"找不到笔记本 {notebook_id}")
            yield _ev("done")
            return
        if not question:
            yield _ev("error", message="问题为空")
            yield _ev("done")
            return

        index = self.store.index(notebook_id)
        sources = {s.id: s for s in self.store.notebook_sources(notebook_id)}
        intent = surface_mod.detect_intent(question, mode)
        hits = merge_hits(index.search(question, limit=self.max_hits), limit=self.max_hits)
        prior_chunks, prior_questions = self._prior_context(notebook_id, context_turns)
        leading_notes: list[str] = []
        if prior_chunks:
            known = {hit.chunk.id for hit in hits}
            carried = [chunk for chunk in prior_chunks if chunk.id not in known][:4]
            if carried:
                # 上一轮的片段排在本轮检索结果之后：有词面命中时它只是补充，
                # 完全没命中时它就是唯一的上下文（追问场景）。
                hits = (hits + [Hit(chunk=chunk, score=0.01,
                                    why="上一轮引用过的材料（追问上下文）", matched=[])
                                for chunk in carried])[: self.max_hits]
                leading_notes.append(f"已带上上一轮的 {len(carried)} 段上下文")
        not_a_topic = False
        if intent in ("study", "brief", "listen"):
            # "给我一套复习材料"这类问题本身没有主题词：按笔记本的主题词补检索，
            # 而不是回一句"材料里没有"或只抓到一个偶然命中的片段。
            topics = index.keyphrases(12)
            topic_hits = (merge_hits(index.search(" ".join(topics), limit=self.max_hits),
                                     limit=self.max_hits) if topics else [])
            if len(hits) < 2 and topic_hits:
                hits = topic_hits
                not_a_topic = True
                leading_notes.append("问题没有指定具体主题，已按笔记本主题词检索：" +
                                     "、".join(topics[:6]))
            elif topic_hits:
                known = {h.chunk.id for h in hits}
                extra = [h for h in topic_hits if h.chunk.id not in known]
                if extra:
                    hits = (hits + extra)[: self.max_hits]
                    leading_notes.append("已补入主题词命中的片段，让材料覆盖更全")
        grounding = Grounding(hits, sources)

        layout = surface_mod.choose_layout(intent, hits, question)
        planned = surface_mod.plan_widgets(intent, question, hits, source_count=len(sources))
        if not hits:
            planned = ["answer", "followups"]
        provider_name = self.provider.name
        yield _ev("plan", intent=intent, layout=layout, planned=planned,
                  provider=provider_name, degraded=False,
                  protocol=PROTOCOL_VERSION,
                  slots={name: slot_for(name) for name in planned},
                  question=question, describe=surface_mod.describe_plan(intent, layout, planned))

        yield _ev("retrieval", terms=index.query_terms(question),
                  hits=_hit_dicts(grounding, hits),
                  coverage=round(grounding.coverage(question, hits), 2),
                  stats=index.stats())

        if not hits:
            yield from self._empty_stream(question, notebook_id, intent, layout, index)
            return

        request = GenerateRequest(
            question=question, intent=intent, layout=layout, planned=planned,
            context=grounding.as_context(),
            keyphrases=index.keyphrases(12),
            material=index.stats(),
            history=prior_questions,
        )

        widgets: list[Widget] = []
        notes: list[str] = list(leading_notes)
        seen: set[str] = set()
        degraded = False

        def handle(raw: Any) -> Iterator[dict[str, Any]]:
            nonlocal widgets
            normalized, problems = surface_mod.normalize([raw], grounding)
            notes.extend(problems)
            for widget in normalized:
                if widget.type in seen:
                    continue
                if widget.type == "answer":
                    cleaned, extra = grounding.enforce(str(widget.data.get("markdown", "")))
                    widget.data["markdown"] = cleaned
                    widget.data.setdefault("confidence", "medium")
                    notes.extend(extra)
                seen.add(widget.type)
                widgets.append(widget)
                yield _ev("widget", index=len(widgets) - 1, widget=widget.to_dict())

        try:
            for raw in self.provider.generate_stream(request):
                yield from handle(raw)
        except ProviderError as exc:
            degraded = True
            notes.append(f"{provider_name} 不可用，已用离线抽取结果补齐：{exc}")
            log.warning("provider %s 失败：%s", provider_name, exc)
            for raw in self.fallback.generate_stream(request):
                yield from handle(raw)
        except Exception as exc:  # pragma: no cover - 兜底，界面不能白屏
            degraded = True
            notes.append(f"生成过程出错，已回退离线结果：{exc}")
            log.exception("生成失败")
            for raw in self.fallback.generate_stream(request):
                yield from handle(raw)

        # 上游"跑完了但一个组件都没产出"：界面接下来看到的是 ensure_minimum 补的离线内容，
        # 那就必须标成降级并说清楚，不能让它冒充模型输出。
        if not widgets and not degraded and provider_name != getattr(self.fallback, "name", "offline"):
            degraded = True
            notes.append(f"{provider_name} 没有产出任何可用组件，已用离线抽取结果补齐")
            log.warning("provider %s 没有产出任何组件", provider_name)
            for raw in self.fallback.generate_stream(request):
                yield from handle(raw)

        widgets = surface_mod.ensure_minimum(widgets, planned, grounding, layout=layout)
        coverage = grounding.coverage(question, hits)
        # 追问场景下"问题与材料无词面重合"是正常的：说清楚它是沿用上下文，而不是覆盖不足
        carried = any(str(hit.why).startswith("上一轮") for hit in hits)
        if coverage < 0.4 and not not_a_topic:
            notes.append("本轮主要沿用上一轮的上下文（问题本身与材料没有词面重合）" if carried
                         else "材料对这个问题的覆盖有限，回答只包含能对上原文的部分")
        surface = surface_mod.build_surface(
            question, intent, layout, widgets, grounding,
            provider=provider_name if not degraded else f"{provider_name}→offline",
            degraded=degraded, notes=notes, created_at=now_iso(),
        )
        problems = surface_problems(surface)
        if problems:
            surface.degraded = True
            surface.notes.extend(problems)
        self._save(notebook_id, question, surface)
        yield _ev("surface", surface=surface.to_dict())
        yield _ev("done")

    def ask(self, notebook_id: str, question: str, mode: str | None = None) -> Surface:
        """同步版：跑完事件流，返回最终 Surface（CLI、测试、导出用）。"""
        result: Surface | None = None
        for event in self.ask_stream(notebook_id, question, mode):
            if event["event"] == "surface":
                result = event["data"]["surface"]
            if event["event"] == "error":
                raise RuntimeError(event["data"].get("message", "生成失败"))
        if result is None:
            raise RuntimeError("没有生成界面")
        return _surface_from_dict(result)

    # ---------------------------------------------------------------- 辅助
    def _prior_context(self, notebook_id: str, context_turns: int) -> tuple[list[Chunk], list[str]]:
        """取最近 N 轮引用过的块与问过的问题。取不到就返回空（不是错误）。"""
        if context_turns <= 0:
            return [], []
        chunks: list[Chunk] = []
        questions: list[str] = []
        seen: set[str] = set()
        try:
            turns = self.store.list_turns(notebook_id, limit=max(1, context_turns))
            for meta in turns:
                turn = self.store.get_turn(meta["id"])
                if not turn:
                    continue
                questions.append(str(turn.get("question") or ""))
                for cite in (turn.get("surface") or {}).get("citations") or []:
                    chunk_id = cite.get("chunk_id")
                    if not chunk_id or chunk_id in seen:
                        continue
                    chunk = self.store.get_chunk(chunk_id)
                    if chunk is not None:
                        seen.add(chunk_id)
                        chunks.append(chunk)
        except Exception:  # pragma: no cover - 上下文拿不到不该毁掉这次回答
            log.exception("读取历史上下文失败")
            return [], questions
        return chunks, [q for q in questions if q]

    def _empty_stream(self, question: str, notebook_id: str, intent: str, layout: str,
                      index) -> Iterator[dict[str, Any]]:
        """材料里没有任何相关内容：给一个诚实的空状态，并指几条能走的路。"""
        topics = index.keyphrases(6)
        markdown = "材料里没有找到与这个问题直接相关的内容。\n\n可以换一种说法，或者先问这些材料确实讲过的内容。"
        notes = ["没有可用来源：材料未覆盖该问题"]
        # 中文提问配英文材料时，"没找到"是必然的（离线检索按字面匹配）。
        # 把原因说清楚，否则用户只会以为"这工具搜不到东西"。
        hint = self._script_hint(question, index)
        if hint:
            markdown += f"\n\n{hint}"
            notes.append(hint)
        widget = Widget(type="answer", title="回答",
                        data={"markdown": markdown, "confidence": "low"})
        yield _ev("widget", index=0, widget=widget.to_dict())
        if topics:
            follow = Widget(type="followups", title="材料里有的主题",
                            data={"items": [f"{t} 是什么？" for t in topics[:4]]})
            yield _ev("widget", index=1, widget=follow.to_dict())
        grounding = Grounding([], {})
        surface = surface_mod.build_surface(question, intent, "focus",
                                           [widget, follow] if topics else [widget],
                                           grounding, provider=self.provider.name,
                                           degraded=True, notes=notes,
                                           created_at=now_iso())
        self._save(notebook_id, question, surface)
        yield _ev("surface", surface=surface.to_dict())
        yield _ev("done")

    def _script_hint(self, question: str, index) -> str | None:
        """问题与材料的文字体系不一致时，解释"为什么一个词都没命中"。

        只采样前几十个片段：大笔记本上扫全量不值得，判断文字体系看开头就够。
        """
        from . import textutil as tu

        asked = tu.script_of(question)
        if asked not in ("cjk", "latin"):
            return None
        chunks = list(getattr(index, "chunks", []) or [])[:40]
        material = tu.script_of(" ".join(chunk.text for chunk in chunks))
        if material in ("mixed", "unknown") or material == asked:
            return None
        if asked == "cjk" and material == "latin":
            return ("注意：材料正文主要是英文，而问题是中文。离线检索按字面匹配，"
                    "换成英文关键词（例如 BPE tokenizer）更容易命中。")
        return ("注意：材料正文主要是中文，而问题是英文。离线检索按字面匹配，"
                "换成中文关键词更容易命中。")

    def _save(self, notebook_id: str, question: str, surface: Surface) -> None:
        try:
            self.store.save_turn(Turn(id=surface.id, notebook_id=notebook_id,
                                      question=question, surface=surface))
        except Exception:  # pragma: no cover - 存档失败不该毁掉这次回答
            log.exception("保存问答失败")


def _ev(name: str, **data: Any) -> dict[str, Any]:
    return {"event": name, "data": data}


def _hit_dicts(grounding: Grounding, hits) -> list[dict[str, Any]]:
    from . import textutil as tu

    out: list[dict[str, Any]] = []
    for hit in hits:
        chunk = hit.chunk
        n = grounding.number_for(chunk.id)
        out.append({
            "n": n, "chunk_id": chunk.id, "source_id": chunk.source_id,
            "locator": grounding.locator(chunk), "title": grounding.title(chunk),
            "snippet": tu.strip_markdown(tu.snippet(chunk.text, hit.matched, width=220)),
            "score": round(hit.score, 3), "why": hit.why, "line": chunk.span.line,
            "kind": chunk.kind,
        })
    return out


def _surface_from_dict(data: dict[str, Any]) -> Surface:
    """把 dict 还原成 Surface（用于同步接口）。"""
    from .protocol import Citation, Widget as W

    widgets = [W(type=w["type"], data=w.get("data", {}), title=w.get("title", ""),
                 cites=list(w.get("cites", [])), id=w.get("id", ""), note=w.get("note", ""),
                 slot=w.get("slot") or slot_for(w["type"]))
               for w in data.get("widgets", [])]
    citations = [Citation(**{k: c.get(k) for k in
                             ("n", "chunk_id", "source_id", "locator", "title", "quote")})
                 for c in data.get("citations", [])]
    return Surface(id=data.get("id", ""), question=data.get("question", ""),
                   layout=data.get("layout", "focus"), widgets=widgets, citations=citations,
                   provider=data.get("provider", ""), intent=data.get("intent", "ask"),
                   degraded=bool(data.get("degraded")), notes=list(data.get("notes", [])),
                   created_at=data.get("created_at", now_iso()))
