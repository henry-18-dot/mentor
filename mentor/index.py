"""混合检索：BM25 + 标题加权 + 短语/邻近加成 + 来源分散。

为什么自己写：这套语料是"讲义 + 中文说明 + 代码"混排，通用向量库在这里
既重又不准。BM25 加几条结构先验，零依赖、可解释（每个结果都带 why），
而且离线也能跑。需要向量时由 `mentor.llm` 提供旁路，不进这里。
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

from . import textutil as tu
from .protocol import Chunk, Hit, Source

K1 = 1.4
B = 0.72
HEADING_BOOST = 2.4
PHRASE_BONUS = 2.0
PROXIMITY_BONUS = 1.2
PROXIMITY_BONUS_CJK = 0.6
LATIN_BONUS = 1.0
LATIN_BONUS_CAP = 2
CJK_WEIGHT = 0.55
LATIN_WEIGHT = 2.0
# 结构加成（短语/邻近/标题/拉丁词）总和不得超过词法得分本身的这个比例。
# 没有这条上限时，一个 3 字中文短语就能把一张作业路线表顶到 BPE 讲义前面。
BONUS_CAP_RATIO = 0.8
TITLE_BONUS = 0.35
PER_SOURCE_CAP = 3
PROXIMITY_WINDOW = 120
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]{2,}")


class Index:
    """一份 notebook 内的全部块。构造是 O(总词数)，几千块毫秒级。"""

    def __init__(self, chunks: list[Chunk], sources: dict[str, Source] | None = None) -> None:
        self.chunks = list(chunks)
        self.sources = sources or {}
        self._tf: list[Counter[str]] = []
        self._len: list[int] = []
        self._head: list[set[str]] = []
        self._df: Counter[str] = Counter()
        self._lower: list[str] = []
        for chunk in self.chunks:
            toks = tu.tokens_for_index(chunk.text)
            counts = Counter(toks)
            self._tf.append(counts)
            self._len.append(max(1, len(toks)))
            head = set(tu.tokens_for_index(chunk.heading)) if chunk.heading else set()
            self._head.append(head)
            self._df.update(counts.keys())
            self._lower.append(chunk.text.lower())
        self._avg_len = (sum(self._len) / len(self._len)) if self._len else 1.0
        self._n = max(1, len(self.chunks))

    # ------------------------------------------------------------------ 查询
    def query_terms(self, query: str) -> list[str]:
        """查询词：去停用词与中文噪声；全是噪声时退回原 token（否则"分"这类单字查询零结果）。"""
        terms = tu.content_tokens(query)
        if terms:
            return terms
        return [t for t in tu.tokens_for_index(query) if not tu.is_stopword(t)]

    def search(
        self,
        query: str,
        *,
        limit: int = 8,
        source_ids: set[str] | None = None,
        per_source_cap: int = PER_SOURCE_CAP,
    ) -> list[Hit]:
        terms = self.query_terms(query)
        if not terms:
            return []
        phrases = _phrases(query, terms)
        scored: list[tuple[float, int, list[str], str]] = []
        uniq_terms = list(dict.fromkeys(terms))

        for i, chunk in enumerate(self.chunks):
            if source_ids and chunk.source_id not in source_ids:
                continue
            tf = self._tf[i]
            if not tf:
                continue
            lexical = 0.0
            bonus = 0.0
            matched: list[str] = []
            latin_matched = 0
            for term in uniq_terms:
                n = tf.get(term, 0)
                if not n:
                    continue
                matched.append(term)
                if not tu.is_cjk(term[0]):
                    latin_matched += 1
                idf = self.idf(term)
                denom = n + K1 * (1 - B + B * self._len[i] / self._avg_len)
                weight = CJK_WEIGHT if tu.is_cjk(term[0]) else LATIN_WEIGHT
                lexical += weight * idf * n * (K1 + 1) / denom
                if term in self._head[i]:
                    bonus += weight * idf * (HEADING_BOOST - 1.0)
            if not matched:
                continue

            why: list[str] = []
            text_low = self._lower[i]
            for phrase in phrases:
                if phrase in text_low:
                    bonus += PHRASE_BONUS * (1 + 0.3 * phrase.count(" "))
                    why.append(f"整句命中「{phrase}」")
                    break
            # 中英混排语料里，"命中一个英文术语"比"命中几个中文 bigram"强得多，
            # 否则中文说明文档会靠词形噪声盖过真正的讲义段落。
            if latin_matched:
                bonus += LATIN_BONUS * min(latin_matched, LATIN_BONUS_CAP)
            if len(matched) > 1 and _near(text_low, matched, window=PROXIMITY_WINDOW):
                bonus += PROXIMITY_BONUS if latin_matched else PROXIMITY_BONUS_CJK
                why.append("查询词在同一段内相邻出现")
            if self._head[i] and (self._head[i] & set(matched)):
                why.append("标题命中")
            if _is_overview(chunk):
                bonus += TITLE_BONUS
            if chunk.kind == "image":
                lexical -= 0.5
            if not why:
                why.append(f"覆盖 {len(matched)}/{len(uniq_terms)} 个查询词")
            score = lexical + min(bonus, BONUS_CAP_RATIO * max(lexical, 0.0))
            spans = [
                {"term": term, "start": start, "end": end}
                for term in matched
                for start, end in tu.find_term_spans(chunk.text, term, limit=2)
            ]
            scored.append((score, i, matched, "；".join(why), spans))

        # 高分优先；同一来源最多 per_source_cap 条，保证结果来自多份材料。
        scored.sort(key=lambda item: (-item[0], self.chunks[item[1]].order))
        if not scored:
            # 词表里没有对应 token（单字中文查询、带点的缩写、拼写特殊）时，
            # 退回字面扫描——宁可慢一点，也不要对用户说"没有相关内容"。
            return self._substring_hits(query, terms, limit, source_ids)
        picked: list[Hit] = []
        used: Counter[str] = Counter()
        deferred: list[Hit] = []
        for score, i, matched, why, spans in scored:
            chunk = self.chunks[i]
            hit = Hit(chunk=chunk, score=score, why=why, matched=matched, matches=spans)
            if used[chunk.source_id] >= per_source_cap:
                deferred.append(hit)
                continue
            used[chunk.source_id] += 1
            picked.append(hit)
            if len(picked) >= limit:
                break
        for hit in deferred:
            if len(picked) >= limit:
                break
            picked.append(hit)
        return picked

    def idf(self, term: str) -> float:
        df = self._df.get(term, 0)
        return math.log(1 + (self._n - df + 0.5) / (df + 0.5))

    def _substring_hits(self, query: str, terms: list[str], limit: int,
                        source_ids: set[str] | None) -> list[Hit]:
        """字面扫描兜底：查询串（或查询词）直接出现在片段里就算命中。"""
        needle = " ".join(query.split()).lower()
        probes = [needle] if 1 < len(needle) <= 12 else [t for t in terms if t]
        if not probes:
            return []
        found: list[Hit] = []
        for i, chunk in enumerate(self.chunks):
            if source_ids and chunk.source_id not in source_ids:
                continue
            text = self._lower[i]
            count = sum(text.count(probe) for probe in probes)
            if count <= 0:
                continue
            matched = [probe for probe in probes if probe in text]
            found.append(Hit(chunk=chunk, score=1.0 + math.log1p(count),
                             why=f"字面包含「{matched[0]}」", matched=matched))
        found.sort(key=lambda hit: (-hit.score, hit.chunk.order))
        return found[:limit]

    # ------------------------------------------------------------ 材料侧信息
    def keyphrases(self, limit: int = 14) -> list[str]:
        """整本笔记本的主题词：用于概念图与"下一步可以问什么"。"""
        joined = "\n".join(c.text for c in self.chunks[:2000])
        return tu.keyphrases(joined, limit=limit)

    def stats(self) -> dict:
        per_source = Counter(c.source_id for c in self.chunks)
        return {
            "chunks": len(self.chunks),
            "sources": len(per_source) or len(self.sources),
            "terms": len(self._df),
            "avg_chunk_tokens": round(self._avg_len, 1),
            "per_source": dict(per_source),
        }


def _phrases(query: str, terms: list[str], max_len: int = 3) -> list[str]:
    """候选短语，长的优先。

    中文没有空格，所以中文短语直接从查询里的连续汉字串切 4/3/2 字窗口；
    英文才用"词 + 空格"拼接。早先只做英文拼接，导致中文查询永远拿不到
    "整句命中"加成。
    """
    out: list[str] = []
    for run in _CJK_RUN_RE.findall(query):
        if len(run) < 2:
            continue
        for size in (4, 3, 2):
            if size > len(run):
                continue
            for i in range(len(run) - size + 1):
                candidate = run[i : i + size]
                if candidate not in out:
                    out.append(candidate)
    for size in range(min(max_len, len(terms)), 1, -1):
        for i in range(len(terms) - size + 1):
            window = terms[i : i + size]
            if any(tu.is_cjk(token[0]) for token in window):
                continue
            out.append(" ".join(window))
    return out


def _first_pos(text: str, term: str) -> int:
    """词边界查找：拉丁词不匹配子串（"en" 不该命中 "engine"），中文按字面。"""
    if tu.is_cjk(term[0]):
        return text.find(term)
    match = re.search(rf"(?<![A-Za-z0-9_]){re.escape(term)}(?![A-Za-z0-9_])", text)
    return match.start() if match else -1


def _near(text: str, terms: list[str], window: int = PROXIMITY_WINDOW) -> bool:
    """所有命中词能否被一个 window 宽的窗口包住（取各自首次出现的位置）。"""
    positions: list[int] = []
    for term in terms:
        pos = _first_pos(text, term)
        if pos < 0:
            return False
        positions.append(pos)
    return max(positions) - min(positions) <= window


_OVERVIEW_RE = re.compile(r"^(#|\s*$)|summary|overview|tl;?dr|总结|概览|提纲", re.I)


def _is_overview(chunk: Chunk) -> bool:
    return bool(_OVERVIEW_RE.match(chunk.text)) or bool(_OVERVIEW_RE.search(chunk.heading or ""))


def merge_hits(hits: list[Hit], limit: int = 6) -> list[Hit]:
    """去重（同块只留一次）并按分数排序。"""
    seen: set[str] = set()
    out: list[Hit] = []
    for hit in sorted(hits, key=lambda h: -h.score):
        if hit.chunk.id in seen:
            continue
        seen.add(hit.chunk.id)
        out.append(hit)
        if len(out) >= limit:
            break
    return out


def term_coverage(hits: list[Hit]) -> dict[str, int]:
    """每个命中词在检索结果里出现的次数，用于判断材料是否真的覆盖了问题。"""
    counts: dict[str, int] = defaultdict(int)
    for hit in hits:
        for term in hit.matched:
            counts[term] += 1
    return dict(counts)
