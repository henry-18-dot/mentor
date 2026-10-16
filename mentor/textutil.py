"""文本小工具：归一化、切句、中英混排分词、关键词、片段截取。

检索、摄取、接地三处都用这一份实现，避免各写一套导致行为不一致。
纯标准库。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, NamedTuple


class Sentence(NamedTuple):
    """一段可独立引用的文字，start/end 是它在整篇归一化文本中的偏移。"""

    start: int
    end: int
    text: str
    line: int  # 1 起算的行号，未知时为 0


_CJK = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef"
_WORD_RE = re.compile(rf"[A-Za-z][A-Za-z0-9_'\-]*|\d+(?:[.,]\d+)*%?|[{_CJK}]")
# 句末切分：标点后要么跟空白，要么直接跟大写/汉字。
# `(?![A-Z]\.)` 用来挡住缩写链内部（U.|S.|A.），链尾那个句点仍然正常断句。
_SENT_END_RE = re.compile(
    rf"(?<=[.!?。！？；;])(?![A-Z]\.)\s+|(?<=[.!?。！？；;])(?![A-Z]\.)(?=[A-Z\u4e00-\u9fff])"
)
# 列表编号（"1." "2)" "12." "a."）不是句子结尾，切完要并回下一句。
_LIST_MARKER_RE = re.compile(r"^(?:\d{1,3}|[A-Za-z])[.)、]$")
_MD_INLINE_RE = re.compile(r"(\*\*|__|\*|`{1,3}|~~)")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")

# 检索与关键词共用的停用词。中英各一份，不求全，只去掉明显不承载信息的词。
STOPWORDS_EN = frozenset(
    """a an the and or but if then than that this these those of in on at to for from by with without
    as is are was were be been being do does did done have has had can could would should may might must
    will shall not no nor so such it its it's we you they he she them their our your i me my
    what which who whom whose when where why how there here about into over under between during
    more most less least very much many few some any all both each other others same own
    using used use via per also however therefore thus hence because while since though although
    one two three first second next last new old good bad big small see look like just only even
    article link image text post video""".split()
)
STOPWORDS_ZH = frozenset(
    "的了和与及或在是有为对从到把被这那这些那些一个我们你们他们它其中如果因为所以"
    "可以需要应该进行以及并且但是而且如何什么怎么为什么怎样这个那个"
)


def normalize_text(text: str) -> str:
    """统一换行与空白，去掉零宽字符和软连字符，保留段落结构。"""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u200b", "").replace("\ufeff", "").replace("\u00ad", "")
    text = unicodedata.normalize("NFKC", text)
    lines = [line.rstrip() for line in text.split("\n")]
    out: list[str] = []
    blank = 0
    for line in lines:
        if line.strip():
            blank = 0
            out.append(line)
        else:
            blank += 1
            if blank <= 1:
                out.append("")
    return "\n".join(out).strip("\n")


def tokenize(text: str) -> list[str]:
    """切成检索用的最小单位：英文小写词 + 单个 CJK 字。"""
    return [t.lower() for t in _WORD_RE.findall(text)]


def is_cjk(token: str) -> bool:
    return bool(token) and "\u4e00" <= token[0] <= "\u9fff"


def script_of(text: str) -> str:
    """判断一段文字主要是什么文字体系：`cjk` / `latin` / `mixed` / `unknown`。

    判据：两种文字各自占比都 ≥30% 才算 `mixed`，否则算占比大的那种。
    这样"中文句子夹英文术语"（技术材料的常态）仍然算 `cjk`，
    而"英文为主、零星中文注释"算 `latin`。

    用途：离线检索是**按字面**匹配的，中文提问配英文材料时必然一个词都对不上。
    这种"注定空手而归"的情况要能识别出来，好把原因讲给用户听，
    而不是只丢一句"材料未覆盖"。
    """
    cjk = sum(1 for ch in text if "\u3400" <= ch <= "\u9fff")
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    total = cjk + latin
    if total == 0:
        return "unknown"
    if min(cjk, latin) / total >= 0.30:
        return "mixed"
    return "cjk" if cjk > latin else "latin"


def tokens_for_index(text: str) -> list[str]:
    """建索引/查询用的 token：英文词原样，连续 CJK 合成 bigram。

    中文没有空格，单字区分度太低，用 bigram 是零依赖方案里最稳的做法。
    """
    raw = tokenize(text)
    out: list[str] = []
    run: list[str] = []
    for tok in raw:
        if is_cjk(tok):
            run.append(tok)
            continue
        _flush_cjk(run, out)
        out.append(tok)
    _flush_cjk(run, out)
    return out


def _flush_cjk(run: list[str], out: list[str]) -> None:
    if not run:
        return
    if len(run) == 1:
        out.append(run[0])
    else:
        out.extend(run[i] + run[i + 1] for i in range(len(run) - 1))
    run.clear()


def is_stopword(token: str) -> bool:
    return token in STOPWORDS_EN or token in STOPWORDS_ZH


# 查询侧过滤：中文 bigram 会跨词切出大量噪声（"器是"、"是怎"、"么训"），
# 只要两个字段里有一个是高频虚词，这个词就不可能是用户想问的内容词。
_FUNCTION_CHARS = set("的了和与及或在是有为对从到把被这那其我你他她它们"
                      "什怎么怎样吗呢吧啊很就都也还又不用能以会要可应该个些种及"
                      "与或而则于上下中前后内外时次第很更最")


def is_noise_bigram(token: str) -> bool:
    return len(token) == 2 and all(is_cjk(ch) for ch in token) and any(
        ch in _FUNCTION_CHARS for ch in token)


def content_tokens(text: str) -> list[str]:
    """内容词：去停用词与中文噪声 bigram，供查询与关键词使用。"""
    return [t for t in tokens_for_index(text)
            if not is_stopword(t) and len(t) > 1 and not is_noise_bigram(t)]


def split_sentences(text: str) -> list[Sentence]:
    """按行切块，再按句末标点细切；保留每块的字符偏移与行号。"""
    if not text:
        return []
    sentences: list[Sentence] = []
    pos = 0
    line_no = 1
    for raw_line in text.split("\n"):
        line_start = pos
        pos += len(raw_line) + 1  # +1 对应被 split 掉的 \n
        stripped = raw_line.strip()
        if not stripped:
            line_no += 1
            continue
        offset = raw_line.index(stripped[0]) if stripped else 0
        base = line_start + offset
        cursor = 0
        for piece in _SENT_END_RE.split(stripped):
            if not piece:
                continue
            idx = stripped.find(piece, cursor)
            if idx < 0:
                idx = cursor
            cursor = idx + len(piece)
            body = piece.strip()
            if not body:
                continue
            lead = len(piece) - len(piece.lstrip())
            start = base + idx + lead
            sentences.append(Sentence(start, start + len(body), body, line_no))
        line_no += 1
    return _merge_list_markers(sentences, text)


def _merge_list_markers(sentences: list[Sentence], text: str) -> list[Sentence]:
    """把被切碎的列表编号并回去："1." + "Internal: …" → "1. Internal: …"。

    切句是给"抽取原文"用的，切口落在 "1." 后面会让抽出来的句子只剩一个编号。
    合并时直接用原文切片，保证偏移与原文仍然逐字对应。
    """
    merged: list[Sentence] = []
    for sent in sentences:
        if merged and _LIST_MARKER_RE.match(merged[-1].text.strip()):
            prev = merged.pop()
            merged.append(Sentence(prev.start, sent.end, text[prev.start : sent.end], prev.line))
            continue
        merged.append(sent)
    return merged


def strip_markdown(text: str) -> str:
    """去掉 markdown 标记，只留可读文字（引用展示、摘要用）。"""
    text = _MD_LINK_RE.sub(lambda m: f"{m.group(1)} {m.group(2)}".strip(), text)
    text = _MD_INLINE_RE.sub("", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^\s{0,3}>\s?", "", text, flags=re.M)
    text = re.sub(r"^\s{0,3}[-*+]\s+", "", text, flags=re.M)
    return text.strip()


_BOLD_RE = re.compile(r"\*\*([^*]{2,60})\*\*")
_CODE_RE = re.compile(r"`([^`\n]{2,40})`")
_DEF_SUBJECT_RE = re.compile(
    r"(?:^|[.\n]\s*)([A-Z][A-Za-z0-9\-]*(?:\s+[A-Za-z0-9\-]+){0,3})\s+(?:is|are|refers to|means|denotes)\b"
)
_ACRONYM_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,7}\b")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def keyphrases(text: str, limit: int = 12) -> list[str]:
    """按"显式标记 → 定义句主语 → 缩写 → 高频实词"的顺序取候选术语。"""
    scored: dict[str, float] = {}
    order: dict[str, int] = {}

    def bump(term: str, weight: float) -> None:
        term = term.strip(" \t,.;:()[]{}'\"")
        if len(term) < 2 or is_stopword(term.lower()):
            return
        key = term.lower()
        scored[key] = scored.get(key, 0.0) + weight
        order.setdefault(key, len(order))

    for m in _BOLD_RE.finditer(text):
        bump(strip_markdown(m.group(1)), 3.0)
    for m in _CODE_RE.finditer(text):
        bump(m.group(1), 2.0)
    for m in _DEF_SUBJECT_RE.finditer(strip_markdown(text)):
        bump(m.group(1), 2.5)
    for m in _ACRONYM_RE.finditer(text):
        bump(m.group(0), 1.2)

    freq: dict[str, int] = {}
    for tok in content_tokens(text):
        if len(tok) < 3 and not is_cjk(tok):
            continue
        freq[tok] = freq.get(tok, 0) + 1
    for tok, n in freq.items():
        if n >= 2:
            bump(tok, min(n, 8) * 0.4)

    ranked = sorted(scored.items(), key=lambda kv: (-kv[1], order[kv[0]]))
    return [term for term, _ in ranked[:limit]]


def snippet(text: str, terms: Iterable[str], width: int = 240) -> str:
    """取包含查询词的一小段文字，命中词加 ** 强调。"""
    flat = " ".join(text.split())
    low = flat.lower()
    hit = -1
    for term in terms:
        term = term.lower()
        if not term:
            continue
        hit = low.find(term)
        if hit >= 0:
            break
    if hit < 0:
        return flat[:width] + ("…" if len(flat) > width else "")
    start = max(0, hit - width // 3)
    end = min(len(flat), start + width)
    piece = flat[start:end]
    if start:
        piece = "…" + piece
    if end < len(flat):
        piece = piece + "…"
    for term in terms:
        if len(term) < 3:
            continue
        piece = re.sub(rf"(?i)({re.escape(term)})", r"**\1**", piece, count=1)
    return piece


def has_year(text: str) -> bool:
    return bool(_YEAR_RE.search(text))


def years_in(text: str) -> list[str]:
    return _YEAR_RE.findall(text)


def find_term_spans(text: str, term: str, limit: int = 3) -> list[tuple[int, int]]:
    """找 term 在 text 里的出现位置（大小写不敏感；拉丁词按词边界）。

    用来给界面做"行内高亮命中词"：只高亮整行不够，用户要知道到底命中了哪几个字。
    返回的是**原文里的绝对偏移**，所以 `text[start:end]` 就是命中的字面量。
    """
    if not text or not term:
        return []
    spans: list[tuple[int, int]] = []
    if is_cjk(term[0]):
        cursor = 0
        while len(spans) < limit:
            index = text.find(term, cursor)
            if index < 0:
                break
            spans.append((index, index + len(term)))
            cursor = index + len(term)
        return spans
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(term)}(?![A-Za-z0-9_])", re.IGNORECASE)
    for match in pattern.finditer(text):
        spans.append((match.start(), match.end()))
        if len(spans) >= limit:
            break
    return spans


def merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """合并重叠区间：多个查询词命中相邻文字时，界面只该画一个高亮块。"""
    if not spans:
        return []
    ordered = sorted(spans)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged
