"""摄取层：把磁盘上的材料读成 `Source`，再切成可检索、可引用的 `Chunk`。

设计要点（对应 docs/api.md §1）：

1. **行号是第一等公民**。每种格式都先产出若干"块单元"（unit），每个单元
   携带它在源文件里的行号与在 `Source.text` 里的绝对偏移；于是任意偏移都能
   反查回原文件行号（`line_for_offset`），切块时单元偏移直接就是
   `Span.start/end/line`，所以 `lecture_01.py:66` 这样的定位串是真的。
2. **绝不执行业讲义文件**。`.py` 讲义只用 `ast` 静态解析，取 `text()` /
   `link()` / `image()` 的字符串字面量；语法错误的文件降级为 `code`，把原因
   写进 `Source.meta["warnings"]`。
3. **切块不切断句子**。优先在标题 / 空行 / 列表项边界（即单元边界）切；
   若单个单元仍超长，退到 `textutil.split_sentences` 的句子边界，最后才按
   空白兜底。
4. **只用标准库**。`pypdf` / `fitz` 用 try/except 可选导入，缺失或解析失败时
   把该文件记进 `IngestReport.skipped`，绝不让整次摄取崩掉。
5. **幂等**：同一路径、同一内容重复调用，`Source.id` 与 `Chunk.id` 完全相同。
6. 不打印任何东西；只写 `logging.getLogger(__name__)`。
"""

from __future__ import annotations

import ast
import bisect
import io
import json
import logging
import os
import re
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable, NamedTuple, Sequence

from . import protocol, textutil
from .protocol import Chunk, Source, Span

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# 可选依赖
# --------------------------------------------------------------------------
try:  # PDF 解析优先级 1
    import pypdf  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - 取决于环境
    pypdf = None  # type: ignore[assignment]

try:  # PDF 解析优先级 2
    import fitz  # type: ignore[import-not-found]  # PyMuPDF
except Exception:  # pragma: no cover - 取决于环境
    fitz = None  # type: ignore[assignment]


# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------
SUPPORTED_SUFFIXES: set[str] = {
    ".py", ".md", ".markdown", ".txt", ".pdf", ".ipynb", ".html", ".htm",
}

#: 超过这个大小直接跳过，避免把仓库里的大二进制拖进内存。
MAX_FILE_BYTES: int = 20 * 1024 * 1024

#: 合并短块时的容忍度：允许比 max_chars / soft_max 稍微超一点点。
_BOUNDARY_SLACK = 24

#: markdown / html 软边界的合并上限：单块再大也不超过这个字数（检索质量考虑）。
_SOFT_CEILING = 1400

#: `## / ###` 行决定讲义与 markdown 的 section 路径。
_HEADING_RE = re.compile(r"^(#{2,6})\s*(.+)$")

#: 列表项起始标记（切块边界优先落在这里）。
_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")

#: 没有句界可用时的兜底切分宽度。
_FALLBACK_SLICE = 400


class IngestReport(NamedTuple):
    """一次批量摄取的结果。`skipped` 里每项是 `{path, reason}`。"""

    sources: list[Source]
    chunks: list[Chunk]
    skipped: list[dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        """给服务层用的纯数据视图（不含正文）。

        `Source.meta` 里带 `_` 前缀的是本模块的私有缓存（原始字节 / 原始文本），
        序列化前必须滤掉，否则 JSON 里会混进解不出来的 bytes。
        """
        return {
            "sources": [_public_source(s) for s in self.sources],
            "chunks": [c.to_dict() for c in self.chunks],
            "skipped": [dict(item) for item in self.skipped],
        }


def _public_source(source: Source) -> dict[str, Any]:
    """`Source.to_dict()` 的可序列化版本：去掉 `_` 开头的私有 meta。"""
    data = source.to_dict()
    meta = {key: value for key, value in (data.get("meta") or {}).items()
            if not key.startswith("_")}
    data["meta"] = meta
    return data


@dataclass
class _Unit:
    """`Source.text` 的一个原子片段：文字 + 它在源文件里的位置。

    `offset` 是它在 `Source.text` 里的绝对起始偏移，切块时直接当 `Span.start`。
    `break_before` 表示"这里是块边界"——首选项，放不下时就在此切开；只有
    `hard=True` 的边界（小节标题、新 cell、新页、代码定义）才绝对不许跨过去。
    """

    text: str
    line: int = 0
    section: str = ""
    kind: str = "prose"
    offset: int = 0
    break_before: bool = False
    hard: bool = False


@dataclass
class _Parsed:
    """解析产物：块单元 + 拼装好的全文 + 偏移→行号映射。"""

    units: list[_Unit] = field(default_factory=list)
    text: str = ""
    line_map: list[tuple[int, int]] = field(default_factory=list)
    offsets: dict[int, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    title: str = ""


# --------------------------------------------------------------------------
# 归一化：与 textutil.normalize_text 同规格，但保留行号
# --------------------------------------------------------------------------
def _normalize_with_map(raw: str) -> tuple[str, list[tuple[int, int]]]:
    """归一化整段文本，同时返回 `[(输出偏移, 源文件行号), ...]`。

    与 :func:`mentor.textutil.normalize_text` 同一套规则：统一换行、去零宽与
    软连字符、NFKC、行尾去空白、连续空行压成一行。这里额外把每行裁掉前导空行
    后的起始偏移记下来，于是 `line_for_offset` 可以精确反查。

    注意 NFKC 会把全角标点折成半角（`，`→`,`），所以映射必须在归一化后的
    文本上按"行"计算，不能拿原始偏移去猜。
    """
    if not raw:
        return "", []
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u200b", "").replace("\ufeff", "").replace("\u00ad", "")

    rows: list[tuple[str, int]] = []  # (该行归一化后的正文, 源文件行号)
    for index, line in enumerate(text.split("\n")):
        row = line.rstrip()
        if row.strip():
            rows.append((row, index + 1))
        elif not rows or rows[-1][0]:
            # 连续空行压成一行，与 textutil.normalize_text 保持一致。
            rows.append(("", index + 1))

    out: list[str] = []
    out_lines: list[int] = []
    for body, line_no in rows:
        if not body and out and not out[-1]:
            continue  # 连续空行只留一个（rows 已折叠，这里是最后一道保险）
        out.append(body)
        out_lines.append(line_no)

    joined = "\n".join(out)
    stripped = joined.strip("\n")
    shift = joined.index(stripped[0]) if stripped else 0
    marks = [(offset - shift, line_no) for offset, line_no in zip(_cumulative(out), out_lines)]
    return stripped, marks


def _cumulative(pieces: Sequence[str]) -> list[int]:
    """每个片段在 `"\\n".join(pieces)` 里的起始偏移。"""
    offsets: list[int] = []
    pos = 0
    for piece in pieces:
        offsets.append(pos)
        pos += len(piece) + 1
    return offsets


# --------------------------------------------------------------------------
# 拼装器：单元 → 全文 + line_map，并给每个单元记下绝对偏移
# --------------------------------------------------------------------------
class _Builder:
    """按顺序累积块单元，同时算出每个单元在全文中的偏移。"""

    def __init__(self) -> None:
        self.units: list[_Unit] = []
        self._parts: list[str] = []
        self._lines: list[int] = []

    def add(self, text: str, line: int = 0, *, section: str = "", kind: str = "prose",
            break_before: bool = False, hard: bool = False) -> _Unit | None:
        """追加一个单元；归一化后为空则忽略并返回 None。"""
        body = textutil.normalize_text(text)
        if not body:
            return None
        unit = _Unit(text=body, line=line, section=section, kind=kind,
                     break_before=break_before, hard=hard)
        self.units.append(unit)
        self._parts.append(body)
        self._lines.append(line)
        return unit

    @property
    def text(self) -> str:
        return "\n".join(self._parts)

    def assign_offsets(self) -> None:
        """把全文偏移写回每个单元（必须在所有 `add` 之后调用一次）。"""
        for unit, offset in zip(self.units, _cumulative(self._parts)):
            unit.offset = offset + (len(unit.text) - len(unit.text.lstrip()))

    def finish(self) -> tuple[str, list[tuple[int, int]], dict[int, int]]:
        self.assign_offsets()
        text = self.text
        offsets = {unit.offset: unit.line for unit in self.units}
        return text, sorted(offsets.items()), offsets


# --------------------------------------------------------------------------
# 从磁盘读取
# --------------------------------------------------------------------------
def _decode(raw: bytes) -> str:
    """宽进严出：优先 utf-8，失败再退 utf-8-sig / gbk，最后用 replace 兜底。"""
    for encoding in ("utf-8", "utf-8-sig", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _relative_posix(path: Path, root: Path | None) -> str:
    """`Source.path` 一律是 `/` 分隔的相对路径（Windows 上也一样）。"""
    try:
        resolved = path.resolve()
    except OSError:  # pragma: no cover - 极少数畸形 Windows 路径
        resolved = path.absolute()
    if root is not None:
        try:
            return resolved.relative_to(Path(root).resolve()).as_posix()
        except ValueError:
            pass
    return resolved.as_posix()


def detect_kind(path: Path) -> str:
    """按后缀给出材料的 kind；后缀不在支持范围内返回 `unknown`。

    `.py` 只有正文里出现 `text(` 调用且能解析成 AST、且确实含有 `text()` /
    `link()` / `image()` 调用时才算 `lecture-note`，其余算 `code`。
    探测只静态读文件，绝不 import、绝不执行。
    """
    suffix = path.suffix.lower()
    if suffix == ".py":
        try:
            raw = path.read_bytes()
        except OSError:
            return "code"
        if b"text(" not in raw:
            return "code"
        try:
            tree = ast.parse(_decode(raw))
        except SyntaxError:
            return "code"
        return "lecture-note" if _collect_note_calls(tree) else "code"
    return {
        ".md": "markdown",
        ".markdown": "markdown",
        ".txt": "text",
        ".pdf": "pdf",
        ".ipynb": "notebook",
        ".html": "html",
        ".htm": "html",
    }.get(suffix, "unknown")


def line_for_offset(source: Source, offset: int) -> int:
    """把 `Source.text` 里的字符偏移翻译回原文件行号；查不到时返回 0。

    对 `.pdf` 返回的是页码（`Source.meta["line_means"] == "page"`），对
    `.ipynb` 返回的是 cell 序号。

    性能注意：这个函数**按片段调用一次**，所以每次调用都重建整张映射表
    会让摄取退化成 O(n²)（4000 片段实测 6.9 秒、占整个摄取的 75%，
    3200 万次 list.append）。这里用 `bisect(key=...)` 直接二分，不再重建表。
    """
    line_map = source.meta.get("line_map") or []
    if not line_map:
        return 0
    try:
        target = int(offset)
        index = bisect.bisect_right(line_map, target, key=lambda entry: int(entry[0])) - 1
        return int(line_map[max(index, 0)][1])
    except (TypeError, ValueError, IndexError):
        logger.warning("line_map 形状不对，忽略：%s", source.id)
        return 0


# --------------------------------------------------------------------------
# `.py` 讲义：ast 静态提取
# --------------------------------------------------------------------------
def _str_literal(node: ast.AST | None) -> str | None:
    """取字符串字面量；其它表达式（变量、f-string 拼接）一律返回 None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _argument(call: ast.Call, name: str, index: int = 0) -> ast.AST | None:
    """按"关键字名优先、位置参数兜底"取调用实参。"""
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    if len(call.args) > index:
        return call.args[index]
    return None


def _called_name(call: ast.Call) -> str:
    """取被调用的函数名：`text(...)` → `text`，`m.text(...)` → `text`。"""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _collect_note_calls(tree: ast.AST) -> list[ast.Call]:
    """按源码顺序收集 `text()` / `link()` / `image()` 调用（不执行文件）。"""
    wanted = {"text", "link", "image"}
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _called_name(node) in wanted
    ]
    calls.sort(key=lambda call: (getattr(call, "lineno", 0), getattr(call, "col_offset", 0)))
    return calls


def _note_piece(call: ast.Call) -> _Unit | None:
    """把一个讲义调用翻译成块单元；取不到字符串字面量就返回 None。"""
    line = getattr(call, "lineno", 0)
    name = _called_name(call)
    if name == "image":
        src = _str_literal(_argument(call, "src"))
        return None if src is None else _Unit(text=src, line=line, kind="image")
    if name == "link":
        # 只有字面量 title 才有意义：`link(shannon_1950)` 是运行时对象，静态拿不到。
        # 取到的 title 是正文的一部分（引用标题），所以沿用 prose 这一 kind，
        # 让下游"按 kind 分派"的逻辑不必为它开特例。
        title = _str_literal(_argument(call, "title"))
        return None if title is None else _Unit(text=title, line=line, kind="prose")
    body = _str_literal(_argument(call, "s"))
    return None if body is None else _Unit(text=body, line=line, kind="prose")


def _parse_lecture_note(raw: str, warnings: list[str]) -> _Parsed | None:
    """解析 `.py` 讲义；语法错误时返回 None，由调用方降级为 code。"""
    try:
        tree = ast.parse(raw)
    except SyntaxError as exc:
        warnings.append(f"语法错误，降级为 code：{exc.msg}（原文件第 {exc.lineno} 行）")
        return None

    builder = _Builder()
    section = ""
    headings = 0
    for call in _collect_note_calls(tree):
        unit = _note_piece(call)
        if unit is None:
            continue
        if unit.kind == "prose":
            match = _HEADING_RE.match(unit.text)
            if match:
                section = match.group(2).strip()
                headings += 1
        unit.section = section
        # 讲义一句一次调用：每个调用自成一块，`Span.line` 就能精确指向那一次
        # `text(...)`（这才是"引用能落回原文"的关键）。真正超长的调用仍会在
        # `_make_units` 里退到句子边界再切。
        # kind 只区分"图片"和"文字"两种：讲义里的标题行 / 列表项本身就是正文，
        # 分类信息由 `section` 承载，下游按 kind 分派的地方不用为此开特例。
        kind = "image" if unit.kind == "image" else "prose"
        builder.add(unit.text, unit.line, section=section, kind=kind,
                    break_before=True, hard=True)

    parsed = _Parsed(units=builder.units, warnings=warnings)
    parsed.text, parsed.line_map, parsed.offsets = builder.finish()
    parsed.warnings.append(f"讲义含 {headings} 个二级以上标题")
    return parsed


# --------------------------------------------------------------------------
# `.py` 代码：ast 取 def / class
# --------------------------------------------------------------------------
def _source_lines(raw: str, start: int, end: int) -> str:
    """取原文第 `start`..`end` 行（1 起算，含端点）。"""
    lines = raw.split("\n")
    return textutil.normalize_text("\n".join(lines[max(start - 1, 0):max(end, start)]))


def _parse_python_code(raw: str, warnings: list[str]) -> _Parsed:
    """按 `def` / `class` 切块；语法错误时整文件一块，并把原因写进 warnings。"""
    builder = _Builder()
    try:
        tree = ast.parse(raw)
    except SyntaxError as exc:
        builder.add(raw, 1, section="source", kind="code")
        parsed = _Parsed(units=builder.units,
                         warnings=list(warnings) + [
                             f"语法错误，整文件降级为一块：{exc.msg}（原文件第 {exc.lineno} 行）"])
        parsed.text, parsed.line_map, parsed.offsets = builder.finish()
        return parsed

    doc = ast.get_docstring(tree)
    if doc and tree.body:
        builder.add(doc, getattr(tree.body[0], "lineno", 1), section="module",
                    kind="docstring", break_before=True, hard=True)

    defs = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    # `ast.walk` 是广度优先，嵌套方法会浮到外层函数前面；必须按源码位置重排，
    # 否则 section 与 chunk 顺序跟原文件的阅读顺序对不上。
    defs.sort(key=lambda node: (getattr(node, "lineno", 0), getattr(node, "col_offset", 0),
                                getattr(node, "end_lineno", 0)))

    for node in defs:
        start = getattr(node, "lineno", 0)
        end = getattr(node, "end_lineno", None) or start
        keyword = "async def" if isinstance(node, ast.AsyncFunctionDef) else (
            "class" if isinstance(node, ast.ClassDef) else "def")
        name = f"{keyword} {getattr(node, 'name', '')}".strip()
        builder.add(_source_lines(raw, start, end), start, section=name, kind="code",
                    break_before=True, hard=True)

    parsed = _Parsed(units=builder.units, warnings=list(warnings))
    parsed.text, parsed.line_map, parsed.offsets = builder.finish()
    return parsed


# --------------------------------------------------------------------------
# 行式文本：markdown / txt
# --------------------------------------------------------------------------
def _slice_words(text: str, width: int) -> list[str]:
    """按空白处切长串，避免把单词拦腰截断（单个超长 token 只能硬切）。"""
    pieces: list[str] = []
    current = ""
    for word in text.split(" "):
        if current and len(current) + 1 + len(word) > width:
            pieces.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        pieces.append(current)

    out: list[str] = []
    for piece in pieces or [text]:
        while len(piece) > width:
            out.append(piece[:width])
            piece = piece[width:]
        if piece:
            out.append(piece)
    return out


def _split_unit(unit: _Unit, width: int) -> list[_Unit]:
    """把一个超长单元退到句子边界；句子仍超长再按空白切。

    `unit.offset` 是它在 `Source.text` 里的起始偏移，这里必须原样继承，
    否则切出来的块会丢掉可回溯的行号。
    """
    base = unit.offset
    units: list[_Unit] = []
    for sentence in textutil.split_sentences(unit.text):
        body = sentence.text.strip()
        if not body:
            continue
        offset = base + sentence.start
        if len(body) <= width:
            units.append(_Unit(text=body, line=unit.line, section=unit.section,
                               kind=unit.kind, offset=offset))
            continue
        for piece in _slice_words(body, width):
            units.append(_Unit(text=piece, line=unit.line, section=unit.section,
                               kind=unit.kind, offset=offset))
            offset += len(piece) + 1
    if units:
        return units
    return [_Unit(text=unit.text, line=unit.line, section=unit.section,
                  kind=unit.kind, offset=base)]


def _parse_lines(raw: str, *, keep_headings: bool, blank_hard: bool = True) -> _Parsed:
    """markdown / txt 共用：按标题 + 空行切单元，标题行做 section。

    标题行永远是硬边界（`break_before=True`）。空行的性质按格式定：`.txt` 里
    空行是唯一的分段依据，所以是硬边界；`.md` 里空行只是软边界——同一节内相邻
    的几小段允许在 `chunk_source` 里合并，避免碎成一堆一两行的块。
    """
    builder = _Builder()
    section = ""
    paragraph: list[str] = []
    para_line = 0
    in_fence = False
    fresh = True  # 上一行是空行 / 标题 / 文件开头
    hard = False  # 当前段落是不是硬边界（txt 的空行、md 的标题之后）

    def flush() -> None:
        nonlocal paragraph, para_line, fresh, hard
        if paragraph:
            body = textutil.normalize_text("\n".join(paragraph))
            if body:
                kind = "list" if _LIST_RE.match(body) else "prose"
                builder.add(body, para_line, section=section, kind=kind,
                            break_before=fresh, hard=hard)
        paragraph = []
        para_line = 0
        fresh = True
        hard = False

    for index, line in enumerate(raw.split("\n"), start=1):
        stripped = line.strip()
        if keep_headings and stripped.startswith("```"):
            in_fence = not in_fence
        if not stripped:
            flush()
            continue
        if keep_headings and not in_fence:
            match = _HEADING_RE.match(stripped)
            if match:
                flush()
                section = match.group(2).strip()
                builder.add(stripped, index, section=section, kind="heading",
                            break_before=True, hard=True)
                continue
        if not paragraph:
            para_line = index
            # 文件开头必然是边界；空行之后：txt 硬边界，md 只是软边界。
            hard = fresh or blank_hard
        # 保留原始行内容（含缩进），归一化交给 Builder，行首缩进不该被吃掉。
        paragraph.append(line)
    flush()

    parsed = _Parsed(units=builder.units)
    parsed.text, parsed.line_map, parsed.offsets = builder.finish()
    return parsed


# --------------------------------------------------------------------------
# `.ipynb`
# --------------------------------------------------------------------------
def _parse_notebook(raw: str, warnings: list[str]) -> _Parsed:
    """markdown + code cell 顺序拼接；行号 = cell 序号，section = `cell N`。"""
    builder = _Builder()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        warnings.append(f"ipynb 解析失败，按纯文本处理：{exc.msg}")
        parsed = _parse_lines(raw, keep_headings=False)
        parsed.warnings.extend(warnings)
        return parsed

    cells = data.get("cells") if isinstance(data, dict) else None
    if not isinstance(cells, list):
        warnings.append("ipynb 缺少 cells 列表")
        cells = []

    for index, cell in enumerate(cells, start=1):
        if not isinstance(cell, dict):
            continue
        source = cell.get("source")
        if isinstance(source, list):
            body = "".join(str(part) for part in source).strip()
        elif isinstance(source, str):
            body = source.strip()
        else:
            body = ""
        if not body:
            continue
        cell_type = str(cell.get("cell_type") or "raw")
        section = f"cell {index}"
        if cell_type == "markdown":
            builder.add(textutil.strip_markdown(body), index, section=section,
                        kind="markdown", break_before=True, hard=True)
        else:
            builder.add(body, index, section=section, kind="code",
                        break_before=True, hard=True)

    parsed = _Parsed(units=builder.units, warnings=warnings)
    parsed.text, parsed.line_map, parsed.offsets = builder.finish()
    return parsed


# --------------------------------------------------------------------------
# `.html`
# --------------------------------------------------------------------------
_BLOCK_TAGS = {
    "p", "div", "li", "tr", "td", "th", "section", "article", "header",
    "footer", "main", "aside", "blockquote", "pre", "figure", "figcaption",
    "dt", "dd", "ul", "ol", "table", "br", "hr", "address", "details", "summary",
}
_HEADING_TAGS = ("h1", "h2", "h3")
_ALL_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")


class _TextCollector(HTMLParser):
    """去标签取正文；`<h1..h3>` 既做节名也单独成块，行号来自 `getpos()`。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[int, str, str]] = []  # (行号, 节名, 正文)
        self.title = ""
        self._skip = 0
        self._buffer: list[str] = []
        self._line = 0
        self._heading_tag = ""
        self._heading_parts: list[str] = []
        self._section = ""

    def _flush(self) -> None:
        body = " ".join(part for part in self._buffer if part).strip()
        if body:
            self.blocks.append((self._line, self._section, body))
        self._buffer = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
            return
        self._flush()
        if tag in _BLOCK_TAGS or tag in _ALL_HEADING_TAGS:
            self._line = self.getpos()[0]
        if tag in _HEADING_TAGS:
            self._heading_tag = tag
            self._heading_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
            return
        if tag in _HEADING_TAGS and self._heading_tag == tag:
            heading = textutil.normalize_text(" ".join(self._heading_parts))
            self._heading_tag = ""
            self._heading_parts = []
            if heading:
                self._section = heading
                if not self.title:
                    self.title = heading
                # 还原成 markdown 标题：h1 → `##`（整篇题目占一级），h3 → `####`。
                marker = "#" * (int(tag[1]) + 1)
                self.blocks.append((self._line, self._section, f"{marker} {heading}"))
                self._buffer = []
                return
        if tag in _BLOCK_TAGS or tag in _ALL_HEADING_TAGS:
            self._flush()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        body = unescape(data).strip()
        if not body:
            return
        if self._heading_tag:
            self._heading_parts.append(body)
            return
        if not self._buffer:
            self._line = self.getpos()[0]
        self._buffer.append(body)

    def close(self) -> None:
        super().close()
        self._flush()


def _parse_html(raw: str, warnings: list[str]) -> _Parsed:
    """把 HTML 正文拆成块单元；节名取最近的 `<h1..h3>`，行号尽力而为。

    标题按层级还原成 markdown 形式（`<h1>`→`##`、`<h2>`→`###`，因为正文里
    的一级标题由整篇文档名承担），再用 `_parse_lines` 那套"标题行 + 软空行"
    规则切块，HTML 的小节就不会跟正文混成一块。
    """
    collector = _TextCollector()
    try:
        collector.feed(raw)
        collector.close()
    except Exception as exc:  # pragma: no cover - HTMLParser 极少抛错
        warnings.append(f"HTML 解析中断，已尽最大努力：{exc}")

    builder = _Builder()
    section = ""
    for line, block_section, body in collector.blocks:
        if block_section != section:
            section = block_section  # 进新小节 → 硬边界
            builder.add(body, line, section=section, kind="prose",
                        break_before=True, hard=True)
            continue
        # 标题行一定会换 section；走到这里说明是同节内的普通块，软边界即可。
        builder.add(body, line, section=section, kind="prose", break_before=True)
    if not builder.units and raw.strip():
        builder.add(textutil.strip_markdown(raw), 0, section="", kind="prose")

    parsed = _Parsed(units=builder.units, warnings=warnings, title=collector.title)
    parsed.text, parsed.line_map, parsed.offsets = builder.finish()
    return parsed


# --------------------------------------------------------------------------
# `.pdf`
# --------------------------------------------------------------------------
def _pdf_payload(raw: bytes | str) -> bytes:
    """把 `meta["raw_text"]` 里的 PDF 载荷还原成字节。

    写入时用 **latin-1**（真正的单字节恒等映射：每个字节 ↔ 一个码点），读回也用 latin-1。
    老库里可能存的是 `surrogateescape` 形式，按 utf-8/surrogateescape 兜底。

    这里以前写的是严格 `raw.encode("utf-8")`，而存进去的是 surrogateescape 解码的字符串
    （含 `\\udc80`~`\\udcff` 孤立代理码元）——于是**所有真实 PDF 都会被
    "surrogates not allowed" 跳过**（实测 6 份 CS336 作业 PDF 全中，整个 PDF 路径实际是死的）。
    """
    if isinstance(raw, bytes):
        return raw
    for encoding, errors in (("latin-1", "strict"), ("utf-8", "surrogateescape")):
        try:
            return raw.encode(encoding, errors)
        except UnicodeEncodeError:
            continue
    return raw.encode("utf-8", "replace")


def _pdf_pages(raw: bytes | str) -> tuple[list[str], str, list[str]]:
    """返回 (每页文本, 使用的解析器, warnings)；两个后端都不可用时页列表为空。

    接受 `bytes`（原始文件）或 `str`（`meta` 里存的载荷）；字符串走 `_pdf_payload()`
    还原成字节，这样 `Source.meta` 只需要留可 JSON 序列化的字符串，
    服务层把源存进 SQLite 时不会撞上 bytes。
    """
    payload = _pdf_payload(raw)
    warnings: list[str] = []
    if pypdf is not None:
        try:
            reader = pypdf.PdfReader(io.BytesIO(payload))
            return [(page.extract_text() or "") for page in reader.pages], "pypdf", warnings
        except Exception as exc:
            warnings.append(f"pypdf 失败：{type(exc).__name__}: {exc}")
    if fitz is not None:
        try:
            with fitz.open(stream=payload, filetype="pdf") as document:
                return [page.get_text() or "" for page in document], "fitz", warnings
        except Exception as exc:
            warnings.append(f"fitz 失败：{type(exc).__name__}: {exc}")
    if not warnings:
        warnings.append("未安装 pypdf / fitz，无法解析 PDF")
    return [], "", warnings


def _parse_pdf(raw: bytes | str, warnings: list[str]) -> _Parsed:
    """按页切块：`Span.line` 是页码，`line_map` 也以页码为单位。"""
    pages, backend, pdf_warnings = _pdf_pages(raw)
    warnings.extend(pdf_warnings)
    if backend:
        warnings.append(f"PDF 解析器：{backend}")

    builder = _Builder()
    for page_no, body in enumerate(pages, start=1):
        if not body.strip():
            continue
        # 一页一块：页码就是这一块的定位，所以分页是硬边界。
        builder.add(body, page_no, section=f"第 {page_no} 页", kind="prose",
                    break_before=True, hard=True)

    parsed = _Parsed(units=builder.units, warnings=warnings)
    parsed.text, parsed.line_map, parsed.offsets = builder.finish()
    return parsed


# --------------------------------------------------------------------------
# 解析总入口
# --------------------------------------------------------------------------
def _parse_source(source: Source, raw_text: str | None) -> _Parsed:
    """按 `source.kind` 分派到具体解析器。`.py` 降级时会就地改 `source.kind`。"""
    warnings: list[str] = []
    kind = source.kind
    text = raw_text or ""

    if kind == "lecture-note":
        parsed = _parse_lecture_note(text, warnings)
        if parsed is not None:
            return parsed
        source.kind = "code"  # 语法错误 → 降级，warnings 保留
        return _parse_python_code(text, warnings)
    if kind == "code":
        return _parse_python_code(text, warnings)
    if kind == "notebook":
        return _parse_notebook(text, warnings)
    if kind == "html":
        return _parse_html(text, warnings)
    if kind in ("markdown", "text"):
        # .md 的空行是软边界（同节内可并块），.txt 的空行是硬边界（唯一的分段依据）。
        parsed = _parse_lines(text, keep_headings=(kind == "markdown"),
                              blank_hard=(kind == "text"))
        if not parsed.units and text.strip():
            builder = _Builder()
            builder.add(text, 1, section="", kind="prose")
            parsed = _Parsed(units=builder.units)
            parsed.text, parsed.line_map, parsed.offsets = builder.finish()
        return parsed
    if kind == "pdf":
        # PDF 的原始字节按 latin-1 存入 meta["raw_text"]（单字节恒等映射），
        # `chunk_source` 才能在不重新读盘的情况下重放分页解析；字符串 meta 也
        # 才能安全地写进 SQLite / JSON。
        return _parse_pdf(source.meta.get("raw_text") or b"", warnings)

    parsed = _parse_lines(text, keep_headings=False)
    parsed.warnings.append(f"未知类型 {kind}，按纯文本处理，行号尽力而为")
    return parsed


def _title_for(path: Path, declared_kind: str, raw_text: str) -> str:
    """标题：讲义取第一个 `##` 标题的文字，其余格式用文件名（去后缀）。

    讲义里的标题写在 `text("## …")` 调用里，普通行扫不到，所以走一次 AST 取
    字面量（不执行文件）；这样单引号 / 全角引号 / 转义都能正确处理。用
    `detect_kind` 的判定而不是解析后的 kind，是为了让"中部有语法错误而降级"
    的讲义仍然拿到标题。
    """
    stem = path.stem
    if declared_kind == "lecture-note":
        try:
            tree = ast.parse(raw_text)
        except SyntaxError:
            return stem
        for call in _collect_note_calls(tree):
            if _called_name(call) != "text":
                continue
            body = _str_literal(_argument(call, "s"))
            if not body:
                continue
            match = re.match(r"^##(?!#)\s*(.+)$", body.strip())
            if match and match.group(1).strip():
                return match.group(1).strip()
    return stem


# --------------------------------------------------------------------------
# read_source
# --------------------------------------------------------------------------
def read_source(path: Path, *, root: Path | None = None) -> Source:
    """读取一份材料：解析正文、拆出块单元、拼成 `Source.text` 并写好 meta。

    - `Source.id = stable_id("src", 相对路径)`，`Source.path` 用 `/` 分隔。
    - `meta["line_map"]` 记录 `[[offset, line], ...]`；`.pdf` 额外写
      `line_means="page"`、`.ipynb` 写 `"cell"`、其余写 `"line"`。
    - 解析不出内容时返回空 `text` 的 `Source`（由调用方决定是否记录）。
    - 读不到 / 超过 20 MB / 后缀不支持时抛 `ValueError`（批量入口转成 skipped）。
    """
    path = Path(path)
    rel = _relative_posix(path, root)
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(f"不支持的后缀 {path.suffix!r}")

    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError(f"文件过大 {size / (1024 * 1024):.1f} MB > {MAX_FILE_BYTES // (1024 * 1024)} MB")

    raw_bytes = path.read_bytes()
    kind = detect_kind(path)
    # PDF 载荷要无损留在 meta 里（`chunk_source` 靠它重放分页解析），而 JSON 只能放字符串：
    # latin-1 是字节 ↔ 码点的恒等映射，所以存进去、读回来都一个字节不差。
    raw_text = raw_bytes.decode("latin-1") if kind == "pdf" else _decode(raw_bytes)

    source = Source(id=protocol.stable_id("src", rel), path=rel,
                    title=_title_for(path, kind, raw_text),
                    kind=kind, text="", meta={"raw_text": raw_text},
                    bytes=len(raw_bytes))
    parsed = _parse_source(source, raw_text if kind != "pdf" else "")

    actual_kind = source.kind
    meta: dict[str, Any] = {
        "line_map": [[offset, line] for offset, line in parsed.line_map],
        "line_means": {"pdf": "page", "notebook": "cell"}.get(actual_kind, "line"),
        "units": len(parsed.units),
        "suffix": path.suffix.lower(),
        # 原文留在 meta 里：右栏按真实行号显示靠它（protocol.Source.raw_text），
        # `chunk_source` 也靠它避免重新读盘。字符串，可安全进 JSON / SQLite。
        "raw_text": raw_text,
    }
    if parsed.warnings:
        meta["warnings"] = list(parsed.warnings)

    source.title = parsed.title or source.title  # html 的 `<h1>` 会补一个更准的标题
    source.kind = actual_kind
    source.text = parsed.text
    source.meta = meta
    logger.debug("摄取 %s：kind=%s，%d 个单元 / %d 字",
                 rel, actual_kind, len(parsed.units), len(parsed.text))
    return source


# --------------------------------------------------------------------------
# chunk_source
# --------------------------------------------------------------------------
def _make_chunk(source: Source, body: str, start: int, end: int, section: str,
                kind: str, order: int) -> Chunk:
    """构造一个 Chunk：偏移翻译成 Span，id 由内容决定（因此幂等）。"""
    line = line_for_offset(source, start)
    span = Span(start=max(start, 0), end=max(end, start), line=line,
                section=section, source_id=source.id)
    # id 由**全文**决定：只哈希前 80 字符会让"第 80 字之后的编辑"复用旧 id，
    # 于是历史引用会被解析到新内容上，破坏"引用回溯当时原文"。
    chunk_id = protocol.stable_id("ck", source.id, str(order), body)
    return Chunk(id=chunk_id, source_id=source.id, text=body, span=span,
                 heading=section, kind=kind, order=order)


def _parsed_for_chunking(source: Source) -> _Parsed:
    """重新解析一次，拿到切块用的块单元（只在 `Source` 的浅拷贝上动刀）。

    在拷贝上解析的原因：`.py` 讲义语法错误时解析器会就地降级 `kind`，不该改到
    调用方手里的对象；PDF 的单元来自字节流，只能重新走一遍分页解析。手工构造的
    `Source`（没有私有缓存）退化为直接解析它的 `text`。
    """
    working = Source(id=source.id, path=source.path, title=source.title,
                     kind=source.kind, text=source.text,
                     meta=dict(source.meta), bytes=source.bytes)
    raw_text = source.meta.get("raw_text")
    if raw_text is None and working.kind != "pdf":
        raw_text = source.text
    parsed = _parse_source(working, raw_text)
    # 降级（讲义 → code）要回写到调用方手里的 Source，界面才看得到；
    # 讲义语法错误时标题仍按"第一个 ## 标题"取，所以这里别动 title。
    source.kind = working.kind
    for warning in parsed.warnings:
        if warning not in source.meta.setdefault("warnings", []):
            source.meta["warnings"].append(warning)
    return parsed


def _make_units(source: Source, units: Sequence[_Unit], max_chars: int) -> list[_Unit]:
    """把过长的单元退到句子边界，保证没有单个单元超过 max_chars。"""
    out: list[_Unit] = []
    first_piece = True
    for unit in units:
        if len(unit.text) > max_chars:
            for piece in _split_unit(unit, max_chars):
                # 边界属性只跟着拆分后的第一个碎片。
                piece.break_before = unit.break_before and first_piece
                piece.hard = unit.hard and first_piece
                first_piece = False
                out.append(piece)
        else:
            out.append(_Unit(text=unit.text, line=unit.line, section=unit.section,
                             kind=unit.kind, offset=unit.offset,
                             break_before=unit.break_before, hard=unit.hard))
        first_piece = True
    return out


def chunk_source(source: Source, *, max_chars: int = 900, min_chars: int = 60) -> list[Chunk]:
    """把一个 `Source` 切成 `Chunk` 列表。

    规则：

    - 边界优先落在标题 / 空行 / 列表项（也就是块单元边界），硬边界（小节标题、
      新 cell、新页、`def` / `class`）两侧绝不合块；
    - markdown / html 的空行只算软边界，同一节内的相邻小段可以并到
      `max_chars * 1.5` 左右，免得整篇碎成一堆一两行的块；其它格式严守
      `max_chars`；
    - 单个单元超长时先退到 `textutil.split_sentences` 的句子边界，句子本身还
      超长才按空白兜底切；
    - `min_chars` 给"还不够一个块"的短 buffer 一点合并余量。

    同一 `Source` 重复调用得到完全相同的 id 与顺序。
    """
    if not source.text.strip():
        return []
    if max_chars <= 0:
        raise ValueError("max_chars 必须为正数")

    units = _parsed_for_chunking(source).units
    if not units:
        builder = _Builder()
        builder.add(source.text, 0, section="", kind="prose")
        parsed = _Parsed(units=builder.units)
        parsed.text, parsed.line_map, parsed.offsets = builder.finish()
        units = parsed.units

    units = _make_units(source, units, max_chars)
    # markdown / html 里空行只是软边界：同一节内相邻小段可以并到 soft_max，
    # 免得整篇碎成一堆一两行的块；其它格式严守 max_chars。
    soft_max = max_chars
    if source.kind in ("markdown", "html"):
        soft_max = min(max(max_chars * 3 // 2, max_chars), _SOFT_CEILING)
    chunks: list[Chunk] = []
    order = 0
    buffer: list[_Unit] = []

    def size_of(group: Sequence[_Unit]) -> int:
        return sum(len(unit.text) for unit in group) + max(len(group) - 1, 0)

    def close(group: list[_Unit]) -> None:
        nonlocal order
        if not group:
            return
        start = group[0].offset
        end = group[-1].offset + len(group[-1].text)
        body = "\n".join(unit.text for unit in group).strip()
        section = group[0].section
        kind = group[0].kind
        # 单单元仍然超长（例如一整段没有句末标点）：按空白兜底切。
        if len(group) == 1 and len(body) > soft_max:
            offset = start
            for piece in _slice_words(body, max_chars):
                chunks.append(_make_chunk(source, piece, offset, offset + len(piece),
                                          section, kind, order))
                offset += len(piece) + 1
                order += 1
            return
        chunks.append(_make_chunk(source, body, start, end, section, kind, order))
        order += 1

    has_hard = False  # 当前 buffer 里是否已经含硬边界起点
    for unit in units:
        if not buffer:
            buffer.append(unit)
            has_hard = unit.hard
            continue
        used = size_of(buffer)
        merged = used + len(unit.text) + 1
        # 硬边界两侧绝不合块；软边界只在放得下时并入。`min_chars` 用来给
        # "还不够一个块"的短 buffer 一点额外余量，让它把下一句也带上。
        fits = (merged <= soft_max
                or (used < min_chars and merged <= soft_max + _BOUNDARY_SLACK))
        if not unit.hard and fits:
            buffer.append(unit)
            has_hard = has_hard or unit.hard
            continue
        close(buffer)
        buffer = [unit]
        has_hard = unit.hard
    close(buffer)
    return chunks


# --------------------------------------------------------------------------
# ingest_paths
# --------------------------------------------------------------------------
def _expand(paths: Iterable[str | os.PathLike[str]], root: Path | None) -> list[Path]:
    """目录递归展开成文件，其余原样；顺序稳定，便于幂等。"""
    out: list[Path] = []
    for item in paths:
        path = Path(item)
        if not path.is_absolute() and root is not None and not path.exists():
            candidate = Path(root) / path
            if candidate.exists():
                path = candidate
        if path.is_dir():
            out.extend(sorted(p for p in path.rglob("*") if p.is_file()))
        else:
            out.append(path)
    return out


def ingest_paths(
    paths: Iterable[str | os.PathLike[str]],
    *,
    root: Path | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> IngestReport:
    """批量摄取：逐份读取并切块；单个文件失败只记 skipped，不影响其它文件。

    返回 `IngestReport(sources, chunks, skipped)`，`skipped` 每项含 `path` 与
    `reason`（例如后缀不支持、文件不存在、超过 20 MB、PDF 库缺失）。每处理完
    一个文件调用一次 `on_progress(done, total, path)`。
    """
    root_path = Path(root) if root is not None else None
    candidates = _expand(paths, root_path)
    sources: list[Source] = []
    chunks: list[Chunk] = []
    skipped: list[dict[str, str]] = []
    total = len(candidates)

    for done, path in enumerate(candidates, start=1):
        rel = _relative_posix(path, root_path)
        try:
            if not path.exists():
                raise FileNotFoundError(f"文件不存在：{rel}")
            if not path.is_file():
                raise ValueError(f"不是普通文件：{rel}")
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                raise ValueError(f"不支持的后缀 {path.suffix!r}")
            size = path.stat().st_size
            if size > MAX_FILE_BYTES:
                raise ValueError(
                    f"文件过大 {size / (1024 * 1024):.1f} MB > {MAX_FILE_BYTES // (1024 * 1024)} MB")

            source = read_source(path, root=root_path)
            if not source.text.strip():
                # 一个字都没抽出来：PDF 多半是扫描件或坏文件，如实记进 skipped，
                # 别让一个空 Source 混进来源列表。
                detail = "；".join(source.meta.get("warnings") or []) or "解析不出任何文字"
                raise ValueError(f"没有可提取的文字（{detail}）")
            sources.append(source)
            chunks.extend(chunk_source(source))
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            logger.warning("跳过 %s：%s", rel, reason)
            skipped.append({"path": rel, "reason": reason})
        finally:
            if on_progress is not None:
                try:
                    on_progress(done, total, rel)
                except Exception:  # 回调抛错不该影响摄取
                    logger.warning("on_progress 回调抛错，已忽略", exc_info=True)

    logger.info("摄取完成：%d 份材料 / %d 个块 / 跳过 %d 个",
                len(sources), len(chunks), len(skipped))
    return IngestReport(sources=sources, chunks=chunks, skipped=skipped)
