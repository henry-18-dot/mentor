"""接地（grounding）：把检索片段变成带编号的引用，并校验生成内容。

这是 mentor 和"随便聊"的分界线：三条规则
1. 引用编号只来自本次检索到的块，模型编不出第 7 条来源；
2. 正文里越界的 [n] 直接删掉，一条都没有时补上最强的那条；
3. 没被任何引用支撑的句子会被记进 notes，交给界面显式提示"材料未覆盖"。
"""

from __future__ import annotations

import re
from typing import Iterable

from . import textutil as tu
from .mdcode import shield_code, unshield_code
from .protocol import Chunk, Citation, Hit, Source

# 允许 `[1]`、`[1,2]`、`[1、2]`、`[ 1 ]`；后面紧跟 `(` 的是 markdown 链接，不是引用。
CITE_RE = re.compile(r"\[\s*(\d+(?:\s*[,，、]\s*\d+)*)\s*\](?!\()")
MAX_CITES = 6
SUPPORT_FLOOR = 0.22

# 界面/生成器自己的引导句，不参与"未支撑断言"判定。
_META_MARKERS = ("要点如下", "方括号", "来源编号", "按材料分组", "先看", "下面", "以上", "这次按")


def _is_meta(sentence: str) -> bool:
    body = sentence.strip()
    return body.endswith(("：", ":")) or any(marker in body for marker in _META_MARKERS)


_CONTAINS_CACHE: dict[str, "re.Pattern[str]"] = {}


def contains_term(text: str, term: str) -> bool:
    """词边界包含判断：避免 "en" 命中 "engine" 这类子串误判。

    拉丁词用边界正则；中文按字面包含（没有词边界这回事）。
    """
    if not term:
        return False
    if any("\u4e00" <= ch <= "\u9fff" for ch in term):
        return term in text
    pattern = _CONTAINS_CACHE.get(term)
    if pattern is None:
        pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(term)}(?![A-Za-z0-9_])")
        _CONTAINS_CACHE[term] = pattern
    return pattern.search(text) is not None


class Grounding:
    def __init__(self, hits: Iterable[Hit], sources: dict[str, Source] | None = None,
                 max_cites: int = MAX_CITES) -> None:
        self.sources = sources or {}
        self.max_cites = max_cites
        self.citations: list[Citation] = []
        self._by_chunk: dict[str, int] = {}
        for hit in hits:
            if len(self.citations) >= max_cites:
                break
            chunk = hit.chunk
            if chunk.id in self._by_chunk:
                continue
            n = len(self.citations) + 1
            self._by_chunk[chunk.id] = n
            self.citations.append(
                Citation(n=n, chunk_id=chunk.id, source_id=chunk.source_id,
                         locator=self.locator(chunk), title=self.title(chunk),
                         quote=self.quote(chunk))
            )
        self._chunk_by_id = {c.n: c for c in self.citations}
        self._hit_by_chunk: dict[str, Hit] = {h.chunk.id: h for h in hits}
        self._tokens: dict[int, set[str]] = {}
        for cite in self.citations:
            hit = self._hit_by_chunk.get(cite.chunk_id)
            text = hit.chunk.text if hit else cite.quote
            self._tokens[cite.n] = set(tu.content_tokens(text))

    # ------------------------------------------------------------ 引用信息
    def locator(self, chunk: Chunk) -> str:
        source = self.sources.get(chunk.source_id)
        path = source.path if source else chunk.source_id
        name = path.rsplit("/", 1)[-1]
        if chunk.span.section and not chunk.span.line:
            return f"{name} · {chunk.span.section}"
        return chunk.span.locator(name)

    def title(self, chunk: Chunk) -> str:
        source = self.sources.get(chunk.source_id)
        heading = chunk.heading or (source.title if source else "")
        return heading or chunk.span.section or "材料"

    def quote(self, chunk: Chunk, width: int = 180) -> str:
        flat = " ".join(chunk.text.split())
        return flat[:width] + ("…" if len(flat) > width else "")

    def number_for(self, chunk_id: str) -> int | None:
        return self._by_chunk.get(chunk_id)

    def cite(self, n: int) -> Citation | None:
        return self._chunk_by_id.get(n)

    def valid_numbers(self) -> set[int]:
        return {c.n for c in self.citations}

    def as_context(self) -> list[dict]:
        """给模型的检索上下文：带编号、带位置、带命中理由。"""
        out: list[dict] = []
        for cite in self.citations:
            hit = self._hit_by_chunk.get(cite.chunk_id)
            out.append({
                "n": cite.n, "locator": cite.locator, "title": cite.title,
                "text": hit.chunk.text if hit else cite.quote,
                "why": hit.why if hit else "",
                "kind": hit.chunk.kind if hit else "prose",
                "source_id": cite.source_id, "chunk_id": cite.chunk_id,
                "line": hit.chunk.span.line if hit else 0,
            })
        return out

    # ---------------------------------------------------------------- 校验
    def enforce(self, markdown: str) -> tuple[str, list[str]]:
        """清掉越界引用；一条引用都没有时补一条；挑出未被支撑的句子。

        代码区（围栏块与行内代码）先占位屏蔽，清洗完再放回：那里面的 `[1]`、
        连续空格、缩进都是代码本身，不是引用也不是排版。
        """
        notes: list[str] = []
        valid = self.valid_numbers()
        source = markdown or ""
        if not source.strip():
            return "", notes

        shielded, blocks, nonce = shield_code(source)

        def _clean(match: re.Match[str]) -> str:
            numbers = [int(x) for x in re.split(r"[,，、]", match.group(1))]
            keep = [n for n in numbers if n in valid]
            if len(keep) != len(numbers):
                notes.append("已删除指向不存在来源的引用编号")
            if not keep:
                return ""
            return "[" + ",".join(str(n) for n in dict.fromkeys(keep)) + "]"

        cleaned = CITE_RE.sub(_clean, shielded)
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        cleaned = re.sub(r"[ \t]+([，。；、,.!?])", r"\1", cleaned).strip()
        cleaned = unshield_code(cleaned, blocks, nonce)
        if not cleaned.strip():
            return "", notes
        # 引用校验也在屏蔽后的文本上做：代码区里没有引用这回事
        if not CITE_RE.search(shielded) and self.citations:
            cleaned = cleaned.rstrip("。.") + f" [{self.citations[0].n}]"
            notes.append("正文没有引用，已补上最相关的一条")

        unsupported = self.unsupported_sentences(cleaned)
        if unsupported:
            notes.append(f"{len(unsupported)} 句在材料里找不到直接支撑，已在界面标注")
        return cleaned, notes

    def unsupported_sentences(self, markdown: str, limit: int = 3) -> list[str]:
        """没有任何引用、且与已引用材料词重合过低的句子。

        只挑真正的"断言"：界面自己的引导句（"要点如下："）不算，
        代码区也不算（代码行天然不可能"被材料支撑"），否则每篇都会挂上
        一堆无意义的告警，告警就不值钱了。
        """
        support: set[str] = set()
        for tokens in self._tokens.values():
            support |= tokens
        # 代码区先换成占位符：既不会被逐句判定，也不会污染行号
        shielded, _blocks, _nonce = shield_code(markdown or "")
        out: list[str] = []
        for sent in tu.split_sentences(shielded):
            body = sent.text.strip()
            if len(body) < 8 or CITE_RE.search(body) or _is_meta(body):
                continue
            if "\x00mentor-" in body:
                continue
            tokens = set(tu.content_tokens(body))
            if not tokens:
                continue
            overlap = len(tokens & support) / len(tokens)
            if overlap < SUPPORT_FLOOR:
                out.append(body[:80])
                if len(out) >= limit:
                    break
        return out

    def coverage(self, question: str, hits: list[Hit]) -> float:
        """材料对问题的覆盖度：查询词有多少落进检索结果。0~1。"""
        terms = [t for t in tu.content_tokens(question)]
        if not terms:
            return 0.0
        found = 0
        for term in terms:
            if any(term in hit.matched or contains_term(hit.chunk.text, term) for hit in hits):
                found += 1
        return found / len(terms)

    def to_dicts(self) -> list[dict]:
        return [c.to_dict() for c in self.citations]
