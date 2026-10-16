"""`mentor.ingest` 的单元测试。

覆盖契约（docs/api.md §1）里逐条要求的格式与边界：

- `.py` 讲义：`text()` / `link()` / `image()` 的字面量、原文件行号、`##` 标题
  决定 heading 与 title、语法错误降级为 code 并写 warnings；
- 其它 `.py`：按 `def` / `class` 切块，定义行号正确；
- `.md` / `.txt`：真实行号、小节、空行分段；
- `.ipynb`：cell 序号 = 行号、section = `cell N`；
- `.html`：去标签取正文、`<h1..h3>` 决定 section、行号尽力而为；
- `.pdf`：页码 = `Span.line`、`meta["line_means"] == "page"`（用 pypdf 现场
  生成最小 PDF，生成失败就 skipTest）；
- 超过 20 MB 的文件跳过、不支持的后缀跳过、缺失文件跳过；
- 幂等：同一路径重复调用得到相同的 `Source.id` 与 `Chunk.id`；
- 切块不切断句子、单块不超过 max_chars。

只用标准库（unittest）；需要 pypdf / fitz 的地方都用 skipTest 兜底。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import unittest
from pathlib import Path

from mentor import ingest, protocol, textutil
from mentor.ingest import Chunk, IngestReport, Source

# 摄取层用 logging 报"跳过 / 警告"；测试里静音，避免污染 -v 输出。
logging.disable(logging.CRITICAL)

FIXTURES = Path(__file__).resolve().parent / "fixtures"

try:
    import pypdf  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - 取决于环境
    pypdf = None  # type: ignore[assignment]


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------
def build_pdf(pages: list[str]) -> bytes:
    """手搓一个最小多页 PDF（Helvetica 12pt，ASCII 文本）。

    不依赖任何第三方库，保证 PDF 分支在只有标准库的环境里也能被测到。
    `pypdf` 能正常抽取这里的文字；抽不出来时上层测试会 skip。
    """
    # 预分配对象号：1=Catalog, 2=Pages，之后每页 2 个（Page + Contents）
    page_ids = [3 + index * 2 for index in range(len(pages))]
    content_ids = [pid + 1 for pid in page_ids]
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % pid for pid in page_ids)
            + b"] /Count %d >>" % len(pages)),
    }
    for pid, cid, text in zip(page_ids, content_ids, pages):
        stream = ("BT /F1 12 Tf 72 720 Td (" + text + ") Tj ET").encode("ascii")
        objects[cid] = (b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        objects[pid] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 "
            b"/BaseFont /Helvetica >> >> >> /Contents %d 0 R >>" % cid
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"
    xref_at = len(out)
    count = max(objects) + 1
    out += b"xref\n0 %d\n" % count
    out += b"0000000000 65535 f \n"
    for number in range(1, count):
        out += b"%010d 00000 n \n" % offsets[number]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (count, xref_at)
    return bytes(out)


class IngestTestBase(unittest.TestCase):
    """公共夹具：临时目录 + fixture 路径。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="mentor-ingest-")
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.root = FIXTURES

    def fixture(self, name: str) -> Path:
        path = FIXTURES / name
        self.assertTrue(path.exists(), f"fixture 缺失：{path}")
        return path

    def read(self, name: str) -> Source:
        return ingest.read_source(self.fixture(name), root=self.root)

    def read_text(self, name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")

    def temp_file(self, name: str, content: str | bytes) -> Path:
        path = self.tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def locators(self, source: Source, chunks: list[Chunk]) -> list[str]:
        return [chunk.span.locator(source.locator_root) for chunk in chunks]

    def chunk_at(self, chunks: list[Chunk], line: int) -> Chunk:
        for chunk in chunks:
            if chunk.span.line == line:
                return chunk
        self.fail(f"没有行号为 {line} 的块：{[(c.span.line, c.text[:20]) for c in chunks]}")

    def chunk_containing(self, chunks: list[Chunk], needle: str) -> Chunk:
        for chunk in chunks:
            if needle in chunk.text:
                return chunk
        self.fail(f"没有包含 {needle!r} 的块：{[c.text[:30] for c in chunks]}")


# --------------------------------------------------------------------------
# 1. kind 识别与后缀
# --------------------------------------------------------------------------
class TestDetectKind(IngestTestBase):
    """`detect_kind` 与 `SUPPORTED_SUFFIXES`。"""

    def test_detect_each_fixture(self) -> None:
        expected = {
            "lecture_sample.py": "lecture-note",
            "code_sample.py": "code",
            "notes_sample.md": "markdown",
            "plain_sample.txt": "text",
            "notebook_sample.ipynb": "notebook",
            "page_sample.html": "html",
        }
        for name, kind in expected.items():
            with self.subTest(name=name):
                self.assertEqual(ingest.detect_kind(self.fixture(name)), kind)

    def test_py_with_text_call_is_lecture_note(self) -> None:
        """只要出现 `text(` 调用就算讲义；被调用的函数名必须是 text/link/image。"""
        path = self.temp_file("mini.py", 'def f():\n    text("标题")\n')
        self.assertEqual(ingest.detect_kind(path), "lecture-note")

    def test_py_mentioning_text_in_comment_is_code(self) -> None:
        """注释里提到 `text(` 不该误判成讲义（AST 里没有调用）。"""
        path = self.temp_file("mention.py", 'value = 1  # 这里调用 text("x") 的样子\n')
        self.assertEqual(ingest.detect_kind(path), "code")

    def test_py_syntax_error_is_code(self) -> None:
        path = self.temp_file("broken.py", 'def f(:\n    text("标题")\n')
        self.assertEqual(ingest.detect_kind(path), "code")

    def test_unknown_suffix(self) -> None:
        path = self.temp_file("thing.json", "{}")
        self.assertEqual(ingest.detect_kind(path), "unknown")
        self.assertNotIn(".json", ingest.SUPPORTED_SUFFIXES)
        with self.assertRaises(ValueError):
            ingest.read_source(path, root=self.tmp)

    def test_supported_suffixes_shape(self) -> None:
        for suffix in (".py", ".md", ".txt", ".pdf", ".ipynb", ".html", ".htm"):
            self.assertIn(suffix, ingest.SUPPORTED_SUFFIXES)


# --------------------------------------------------------------------------
# 2. .py 讲义
# --------------------------------------------------------------------------
class TestLectureNote(IngestTestBase):
    """`.py` 讲义：行号、heading、图片块、title、幂等。"""

    def setUp(self) -> None:
        super().setUp()
        self.source = self.read("lecture_sample.py")
        self.chunks = ingest.chunk_source(self.source)

    def test_kind_and_meta(self) -> None:
        self.assertEqual(self.source.kind, "lecture-note")
        self.assertEqual(self.source.meta["line_means"], "line")
        self.assertTrue(self.source.meta["line_map"])
        self.assertGreater(self.source.meta["units"], 5)

    def test_text_line_number_is_exact(self) -> None:
        """`text("Hello world")` 在原文件第 12 行，并且自己就是一个块。"""
        chunk = self.chunk_containing(self.chunks, "Hello world")
        self.assertEqual(chunk.text, "Hello world")
        self.assertEqual(chunk.span.line, 12)
        self.assertEqual(chunk.span.locator(self.source.locator_root), "lecture_sample.py:12")
        self.assertEqual(chunk.kind, "prose")
        # line_map 也能按任意偏移精确反查（右栏高亮靠它）
        offset = self.source.text.index("Hello world")
        self.assertEqual(ingest.line_for_offset(self.source, offset), 12)

    def test_every_lecture_chunk_points_at_its_own_call(self) -> None:
        """每个块的行号都必须落在一次内容调用上——引用才落得回原文。"""
        raw_lines = self.read_text("lecture_sample.py").split("\n")
        markers = ("text(", "link(", "image(")
        for chunk in self.chunks:
            line = raw_lines[chunk.span.line - 1]
            with self.subTest(line=chunk.span.line):
                self.assertTrue(any(marker in line for marker in markers), line)

    def test_link_title_line_number(self) -> None:
        """`link(title=..., url=...)` 取 title，行号 = 调用所在行。"""
        chunk = self.chunk_containing(self.chunks, "Attention Is All You Need")
        self.assertEqual(chunk.text, "Attention Is All You Need")
        self.assertEqual(chunk.span.line, 24)
        self.assertEqual(ingest.line_for_offset(
            self.source, self.source.text.index("缓存 KV")), 25)

    def test_image_is_own_chunk_with_image_kind(self) -> None:
        images = [c for c in self.chunks if c.kind == "image"]
        texts = {c.text for c in images}
        self.assertIn("images/bpe-merge.png", texts)
        self.assertIn("images/before-heading.png", texts)
        for chunk in images:
            self.assertEqual(chunk.text, chunk.text.strip())
            self.assertNotIn("\n", chunk.text)
            self.assertEqual(chunk.heading, chunk.span.section)
        chunk = self.chunk_containing(self.chunks, "images/bpe-merge.png")
        self.assertEqual(chunk.span.line, 16)
        self.assertEqual(chunk.span.locator(self.source.locator_root), "lecture_sample.py:16")

    def test_heading_becomes_section_path(self) -> None:
        chunk = self.chunk_containing(self.chunks, "BPE 的合并依据")
        self.assertEqual(chunk.span.section, "第一章：分词")
        self.assertEqual(chunk.heading, "第一章：分词")
        self.assertEqual(chunk.span.line, 15)
        # `##` 标题行自己也是内容调用，section 从它开始生效
        head = self.chunk_containing(self.chunks, "## 第一章")
        self.assertEqual(head.span.line, 11)
        self.assertEqual(head.heading, "第一章：分词")

    def test_third_level_heading_updates_section(self) -> None:
        chunk = self.chunk_containing(self.chunks, "代价是 O(n^2)")
        self.assertEqual(chunk.heading, "2.1 复杂度的代价")
        chunk2 = self.chunk_containing(self.chunks, "缓存 KV")
        self.assertEqual(chunk2.heading, "2.1 复杂度的代价")

    def test_preamble_has_empty_section(self) -> None:
        chunk = self.chunk_containing(self.chunks, "第一个二级标题之前")
        self.assertEqual(chunk.span.line, 6)
        self.assertEqual(chunk.span.section, "")

    def test_title_from_first_second_level_heading(self) -> None:
        self.assertEqual(self.source.title, "第一章：分词")
    def test_locator_falls_back_to_offsets_when_no_line(self) -> None:
        span = protocol.Span(start=3, end=9, line=0, source_id=self.source.id)
        self.assertEqual(span.locator("x.md"), "x.md@3-9")

    def test_no_print_and_no_exec_side_effect(self) -> None:
        """讲义里的 `text(...)` 不能被真的执行（本文件里 text 是未定义名）。"""
        self.assertNotIn("NameError", self.source.text)
        self.assertIn("Hello world", self.source.text)

    def test_chunk_ids_are_stable(self) -> None:
        again = ingest.chunk_source(self.read("lecture_sample.py"))
        self.assertEqual([c.id for c in self.chunks], [c.id for c in again])
        self.assertEqual([c.order for c in self.chunks], list(range(len(self.chunks))))
        self.assertEqual(self.source.id, self.read("lecture_sample.py").id)

    def test_line_for_offset(self) -> None:
        offset = self.source.text.index("Hello world")
        self.assertEqual(ingest.line_for_offset(self.source, offset), 12)
        self.assertEqual(ingest.line_for_offset(self.source, 0), 6)
        self.assertEqual(ingest.line_for_offset(self.source, 10 ** 6), 25)

    def test_line_for_offset_without_map(self) -> None:
        bare = Source(id="src_x", path="x.md", title="x", kind="markdown", text="abc")
        self.assertEqual(ingest.line_for_offset(bare, 0), 0)

    def test_syntax_error_downgrades_to_code_with_warning(self) -> None:
        broken = self.temp_file("broken_note.py", 'def f():\n    text("标题"\n')
        # 文件里出现 text( 字样，但不能解析 → detect_kind 已降级为 code
        self.assertEqual(ingest.detect_kind(broken), "code")
        source = ingest.read_source(broken, root=self.tmp)
        self.assertEqual(source.kind, "code")
        chunks = ingest.chunk_source(source)
        self.assertTrue(chunks)
        self.assertTrue(any("语法错误" in w for w in source.meta.get("warnings") or []))

        # 直接送进讲义分支（例如调用方手工构造的 Source）也必须降级并留下原因
        note = self.read("lecture_sample.py")
        note.kind = "lecture-note"
        note.meta["raw_text"] = 'def f():\n    text("标题"\n'
        forced_chunks = ingest.chunk_source(note)
        self.assertEqual(len(forced_chunks), 1)
        self.assertEqual(note.kind, "code")
        warnings = note.meta.get("warnings") or []
        self.assertTrue(any("语法错误" in w for w in warnings), warnings)

    def test_long_string_unit_is_split_at_sentence_boundary(self) -> None:
        """超长 `text()` 要在句子边界切开，不能拦腰截断。"""
        body = "。".join(f"第{i}句讲的是一个很长很长的知识点，需要被单独引用" for i in range(40))
        text = 'def f():\n    text("' + body + '。")\n'
        path = self.temp_file("long_note.py", text)
        source = ingest.read_source(path, root=self.tmp)
        chunks = ingest.chunk_source(source, max_chars=200, min_chars=60)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertTrue(chunk.text.endswith("。"), chunk.text[-20:])
            self.assertLessEqual(len(chunk.text), 200 + ingest._BOUNDARY_SLACK)
        # 归一化会做 NFKC（全角标点折半角），所以比较也用归一化后的文本
        joined = "".join(c.text for c in chunks).replace("\n", "")
        self.assertEqual(joined, textutil.normalize_text(body + "。"))

    def test_chunk_boundaries_do_not_break_sentences(self) -> None:
        for chunk in self.chunks:
            self.assertFalse(chunk.text.startswith("。"))
            self.assertTrue(chunk.text.strip())


# --------------------------------------------------------------------------
# 3. 其它 .py：按 def / class 切块
# --------------------------------------------------------------------------
class TestPythonCode(IngestTestBase):
    """其它 `.py`：`def` / `class` 定义行号与块内容。"""

    def setUp(self) -> None:
        super().setUp()
        self.source = self.read("code_sample.py")
        self.chunks = ingest.chunk_source(self.source)

    def test_kind(self) -> None:
        self.assertEqual(self.source.kind, "code")
        self.assertEqual(self.source.title, "code_sample")

    def test_def_lines(self) -> None:
        expected = {
            "def first": 8,
            "def second": 13,
            "class Helper": 18,
            "def apply": 23,
            "def _touches_math": 28,
        }
        for needle, line in expected.items():
            with self.subTest(needle=needle):
                chunk = self.chunk_at(self.chunks, line)
                self.assertEqual(chunk.span.section, needle)
                self.assertEqual(chunk.span.line, line)
                self.assertTrue(chunk.text.startswith(needle), chunk.text[:30])

    def test_def_body_is_kept(self) -> None:
        chunk = self.chunk_containing(self.chunks, "def second")
        self.assertIn("return first(value) * 2", chunk.text)

    def test_docstring_chunk(self) -> None:
        chunk = self.chunk_containing(self.chunks, "按 def / class 切块")
        self.assertEqual(chunk.kind, "docstring")
        self.assertEqual(chunk.span.line, 1)

    def test_py_without_defs_yields_nothing(self) -> None:
        """只有模块级赋值的 .py 不算材料：没有可引用文字，批量入口如实记 skipped。"""
        path = self.temp_file("facts.py", "A = 1\nB = 2.0\nC = A + B\n")
        source = ingest.read_source(path, root=self.tmp)
        self.assertEqual(source.kind, "code")
        self.assertEqual(source.text, "")
        self.assertEqual(ingest.chunk_source(source), [])
        report = ingest.ingest_paths([path], root=self.tmp)
        self.assertEqual(report.sources, [])
        self.assertEqual(len(report.skipped), 1)
        self.assertIn("没有可提取的文字", report.skipped[0]["reason"])

    def test_syntax_error_code_file(self) -> None:
        broken = self.temp_file("broken_plain.py", "def f(:\n    pass\n")
        source = ingest.read_source(broken, root=self.tmp)
        self.assertEqual(source.kind, "code")
        chunks = ingest.chunk_source(source)
        self.assertEqual(len(chunks), 1)
        self.assertIn("语法错误", " ".join(source.meta.get("warnings") or []))

    def test_async_def_and_indented_method(self) -> None:
        body = (
            '"""示例。"""\n'
            "\n"
            "class A:\n"
            "    async def run(self):\n"
            "        return 1\n"
        )
        path = self.temp_file("async_sample.py", body)
        source = ingest.read_source(path, root=self.tmp)
        chunks = ingest.chunk_source(source)
        chunk = self.chunk_at(chunks, 4)
        self.assertEqual(chunk.span.section, "async def run")
        self.assertEqual(chunk.kind, "code")
        self.assertIn("return 1", chunk.text)
        # 嵌套方法排在所属 class 之后，顺序必须与源码一致
        self.assertEqual([c.span.line for c in chunks], [1, 3, 4])


# --------------------------------------------------------------------------
# 4. markdown
# --------------------------------------------------------------------------
class TestMarkdown(IngestTestBase):
    """`.md`：真实行号、小节、空行分段。"""

    def setUp(self) -> None:
        super().setUp()
        self.source = self.read("notes_sample.md")
        self.chunks = ingest.chunk_source(self.source)

    def test_kind_and_title(self) -> None:
        self.assertEqual(self.source.kind, "markdown")
        self.assertEqual(self.source.title, "notes_sample")
        self.assertEqual(self.source.meta["line_means"], "line")

    def test_real_line_numbers(self) -> None:
        first = self.chunks[0]
        self.assertEqual(first.span.line, 1)
        self.assertIn("示例 Markdown", first.text)
        chunk = self.chunk_containing(self.chunks, "行号必须落在原文件里")
        self.assertEqual(chunk.span.line, 21)

    def test_sections_from_headings(self) -> None:
        chunk = self.chunk_containing(self.chunks, "各自成块")
        self.assertEqual(chunk.heading, "第一节：数据准备")
        head = self.chunk_at(self.chunks, 6)
        self.assertEqual(head.kind, "heading")
        self.assertEqual(head.heading, "第一节：数据准备")

    def test_fenced_code_heading_is_not_a_heading(self) -> None:
        code = self.chunk_containing(self.chunks, "def demo")
        self.assertEqual(code.span.section, "第一节：数据准备")
        self.assertTrue(code.text.startswith("```python"))

    def test_blank_line_paragraph_split(self) -> None:
        chunk = self.chunk_containing(self.chunks, "空行分段与真实行号")
        self.assertEqual(chunk.span.line, 3)

    def test_list_item_becomes_list_kind(self) -> None:
        chunk = self.chunk_containing(self.chunks, "列表项甲")
        self.assertEqual(chunk.kind, "list")

    def test_line_map_matches_every_chunk_start(self) -> None:
        """每个块的起点偏移都必须能反查回它的行号。"""
        for chunk in self.chunks:
            self.assertEqual(ingest.line_for_offset(self.source, chunk.span.start), chunk.span.line)

    def test_trailing_whitespace_does_not_shift_lines(self) -> None:
        path = self.temp_file("padded.md", "第一行   \n\n\n\n第二行\n")
        source = ingest.read_source(path, root=self.tmp)
        chunks = ingest.chunk_source(source)
        self.assertEqual([c.span.line for c in chunks], [1, 5])

    def test_empty_markdown(self) -> None:
        path = self.temp_file("empty.md", "\n\n   \n")
        source = ingest.read_source(path, root=self.tmp)
        self.assertEqual(source.text, "")
        self.assertEqual(ingest.chunk_source(source), [])


# --------------------------------------------------------------------------
# 5. txt
# --------------------------------------------------------------------------
class TestPlainText(IngestTestBase):
    """`.txt`：按空行分段，真实行号。"""

    def setUp(self) -> None:
        super().setUp()
        self.source = self.read("plain_sample.txt")
        self.chunks = ingest.chunk_source(self.source)

    def test_kind(self) -> None:
        self.assertEqual(self.source.kind, "text")
        self.assertEqual(self.source.title, "plain_sample")

    def test_paragraph_lines(self) -> None:
        lines = [chunk.span.line for chunk in self.chunks]
        self.assertEqual(lines, [1, 3, 6, 8])
        self.assertEqual([c.span.locator(self.source.locator_root) for c in self.chunks][0],
                         "plain_sample.txt:1")

    def test_same_paragraph_stays_together(self) -> None:
        chunk = self.chunk_containing(self.chunks, "因此应该合在同一个块里")
        self.assertIn(textutil.normalize_text("第一段：两行文字属于同一段"), chunk.text)
        self.assertEqual(chunk.span.line, 3)

    def test_no_headings_in_txt(self) -> None:
        path = self.temp_file("looks_like_md.txt", "## 这不是标题\n\n正文一段。\n")
        source = ingest.read_source(path, root=self.tmp)
        chunks = ingest.chunk_source(source)
        self.assertTrue(all(chunk.span.section == "" for chunk in chunks))
        self.assertTrue(any(chunk.kind == "prose" for chunk in chunks))

    def test_gbk_file_does_not_crash(self) -> None:
        path = self.temp_file("gbk.txt", "第一段中文。\n\n第二段中文。\n".encode("gbk"))
        source = ingest.read_source(path, root=self.tmp)
        self.assertIn("第二段中文", source.text)


# --------------------------------------------------------------------------
# 6. ipynb
# --------------------------------------------------------------------------
class TestNotebook(IngestTestBase):
    """`.ipynb`：cell 序号 = 行号，section = `cell N`。"""

    def setUp(self) -> None:
        super().setUp()
        self.source = self.read("notebook_sample.ipynb")
        self.chunks = ingest.chunk_source(self.source)

    def test_kind_and_line_means(self) -> None:
        self.assertEqual(self.source.kind, "notebook")
        self.assertEqual(self.source.meta["line_means"], "cell")
        self.assertEqual(self.source.meta["suffix"], ".ipynb")

    def test_cell_locators(self) -> None:
        self.assertEqual(self.chunks[0].span.line, 1)
        self.assertEqual(self.chunks[0].span.section, "cell 1")
        self.assertEqual(self.chunks[0].span.locator(self.source.locator_root),
                         "notebook_sample.ipynb:1")
        code = self.chunk_containing(self.chunks, "def greet")
        self.assertEqual(code.span.line, 2)
        self.assertEqual(code.kind, "code")
        self.assertIn("print(greet", code.text)

    def test_markdown_cell_is_markdown_kind(self) -> None:
        chunk = self.chunk_containing(self.chunks, "合并相邻的 token 对")
        self.assertEqual(chunk.kind, "markdown")
        self.assertEqual(chunk.span.line, 3)
        self.assertNotIn("##", chunk.text)  # strip_markdown 去掉了标题标记

    def test_empty_cell_is_skipped(self) -> None:
        self.assertNotIn(5, [chunk.span.line for chunk in self.chunks])

    def test_broken_json_falls_back(self) -> None:
        path = self.temp_file("broken.ipynb", "{not json")
        source = ingest.read_source(path, root=self.tmp)
        self.assertTrue(source.meta.get("warnings"))
        self.assertTrue(ingest.chunk_source(source))


# --------------------------------------------------------------------------
# 7. html
# --------------------------------------------------------------------------
class TestHtml(IngestTestBase):
    """`.html`：去标签、`<h1..h3>` 决定 section。"""

    def setUp(self) -> None:
        super().setUp()
        self.source = self.read("page_sample.html")
        self.chunks = ingest.chunk_source(self.source)

    def test_kind_and_title(self) -> None:
        self.assertEqual(self.source.kind, "html")
        self.assertEqual(self.source.title, "示例 HTML 讲义")

    def test_tags_removed_and_script_skipped(self) -> None:
        self.assertNotIn("<p>", self.source.text)
        self.assertNotIn("这段脚本文字不该进正文", self.source.text)
        self.assertNotIn("color: red", self.source.text)

    def test_sections_from_headings(self) -> None:
        first = self.chunk_containing(self.chunks, "这是第一段正文")
        self.assertEqual(first.heading, "示例 HTML 讲义")
        second = self.chunk_containing(self.chunks, "第一节的正文")
        self.assertEqual(second.heading, "第一节")
        third = self.chunk_containing(self.chunks, "第二节的正文")
        self.assertEqual(third.heading, "第二节")
        self.assertEqual(third.span.line, 18)

    def test_line_numbers_are_positive(self) -> None:
        for chunk in self.chunks:
            self.assertGreater(chunk.span.line, 0)

    def test_entities_are_unescaped(self) -> None:
        path = self.temp_file("entity.html", "<p>a &amp; b &lt;c&gt;</p>")
        source = ingest.read_source(path, root=self.tmp)
        self.assertIn("a & b <c>", source.text)

    def test_html_without_tags(self) -> None:
        path = self.temp_file("bare.html", "就是一段裸文字。")
        source = ingest.read_source(path, root=self.tmp)
        self.assertIn("就是一段裸文字", source.text)


# --------------------------------------------------------------------------
# 8. pdf
# --------------------------------------------------------------------------
class TestPdf(IngestTestBase):
    """`.pdf`：页码 = `Span.line`，`line_means == "page"`。"""

    def _make_pdf(self) -> Path:
        try:
            return self.temp_file("sample.pdf", build_pdf([
                "Page one mentions alpha tokenization.",
                "Page two mentions beta attention.",
                "Page three mentions gamma scaling.",
            ]))
        except Exception as exc:  # pragma: no cover - 生成器理论上不会失败
            self.skipTest(f"无法生成最小 PDF：{exc}")

    def test_pdf_pages(self) -> None:
        if ingest.pypdf is None and ingest.fitz is None:
            self.skipTest("没有安装 pypdf / fitz")
        path = self._make_pdf()
        source = ingest.read_source(path, root=self.tmp)
        if not source.text.strip():
            self.skipTest("两个 PDF 后端都抽不出文字（生成的最小 PDF 不受支持）")

        self.assertEqual(source.kind, "pdf")
        self.assertEqual(source.meta["line_means"], "page")
        chunks = ingest.chunk_source(source)
        self.assertTrue(chunks)
        for chunk in chunks:
            self.assertGreaterEqual(chunk.span.line, 1)
            self.assertLessEqual(chunk.span.line, 3)
        lines = {chunk.span.line for chunk in chunks if "beta" in chunk.text}
        if lines:
            self.assertEqual(lines, {2})
            locator = chunks[0].span.locator(source.locator_root)
            self.assertRegex(locator, r"sample\.pdf:\d+$")
        self.assertEqual(ingest.line_for_offset(source, 0), 1)

    def test_pdf_bytes_are_retained_for_rechunking(self) -> None:
        if ingest.pypdf is None and ingest.fitz is None:
            self.skipTest("没有安装 pypdf / fitz")
        path = self._make_pdf()
        source = ingest.read_source(path, root=self.tmp)
        if not source.text.strip():
            self.skipTest("PDF 抽不出文字")
        # 原文留在 meta["raw_text"]（字符串），既能重放解析也能直接进 JSON
        self.assertIsInstance(source.meta.get("raw_text"), str)
        self.assertTrue(source.meta["raw_text"])
        self.assertEqual(source.raw_text(), source.meta["raw_text"])
        first = ingest.chunk_source(source)
        second = ingest.chunk_source(source)
        self.assertEqual([c.id for c in first], [c.id for c in second])
        report = ingest.ingest_paths([path], root=self.tmp)
        payload = json.dumps(report.to_dict(), ensure_ascii=False)
        self.assertNotIn("_raw_bytes", payload)

    def test_corrupt_pdf_is_skipped(self) -> None:
        if ingest.pypdf is None and ingest.fitz is None:
            self.skipTest("没有安装 pypdf / fitz")
        path = self.temp_file("corrupt.pdf", b"%PDF-1.4\nthis is not a real pdf\n")
        report = ingest.ingest_paths([path], root=self.tmp)
        self.assertEqual(report.sources, [])
        self.assertEqual(len(report.skipped), 1)
        self.assertIn("corrupt.pdf", report.skipped[0]["path"])


# --------------------------------------------------------------------------
# 9. 超大文件 / 异常输入
# --------------------------------------------------------------------------
class TestLimits(IngestTestBase):
    """超过 20 MB 跳过、缺失文件跳过、目录展开。"""

    def test_oversized_file_is_skipped(self) -> None:
        path = self.tmp / "huge.txt"
        with open(path, "wb") as handle:
            handle.write(b"x" * (ingest.MAX_FILE_BYTES + 1024))
        report = ingest.ingest_paths([path], root=self.tmp)
        self.assertEqual(report.sources, [])
        self.assertEqual(report.chunks, [])
        self.assertEqual(len(report.skipped), 1)
        self.assertEqual(report.skipped[0]["path"], "huge.txt")
        self.assertIn("过大", report.skipped[0]["reason"])
        self.assertIn("ValueError", report.skipped[0]["reason"])

    def test_missing_file_is_skipped(self) -> None:
        report = ingest.ingest_paths([self.tmp / "nope.md"], root=self.tmp)
        self.assertEqual(len(report.skipped), 1)
        self.assertIn("FileNotFoundError", report.skipped[0]["reason"])

    def test_unsupported_suffix_is_skipped(self) -> None:
        path = self.temp_file("data.json", '{"a": 1}')
        report = ingest.ingest_paths([path], root=self.tmp)
        self.assertEqual(report.sources, [])
        self.assertIn("不支持的后缀", report.skipped[0]["reason"])

    def test_read_source_raises_for_bad_input(self) -> None:
        with self.assertRaises(ValueError):
            ingest.read_source(self.temp_file("data.json", "{}"), root=self.tmp)
        with self.assertRaises(FileNotFoundError):
            ingest.read_source(self.tmp / "nope.md", root=self.tmp)


# --------------------------------------------------------------------------
# 10. ingest_paths
# --------------------------------------------------------------------------
class TestIngestPaths(IngestTestBase):
    """批量入口：源 / 块 / 跳过、on_progress、幂等、相对路径。"""

    def test_ingest_all_fixtures(self) -> None:
        names = ["lecture_sample.py", "code_sample.py", "notes_sample.md",
                 "plain_sample.txt", "notebook_sample.ipynb", "page_sample.html"]
        report = ingest.ingest_paths([self.fixture(n) for n in names], root=self.root)
        self.assertIsInstance(report, IngestReport)
        self.assertEqual(len(report.sources), len(names))
        self.assertEqual(report.skipped, [])
        self.assertEqual({s.kind for s in report.sources},
                         {"lecture-note", "code", "markdown", "text", "notebook", "html"})
        self.assertGreater(len(report.chunks), 10)
        self.assertTrue(all(c.source_id for c in report.chunks))
        self.assertTrue(all(c.text.strip() for c in report.chunks))
        # to_dict 必须能直接进 JSON（meta 里不能有 bytes）
        payload = json.dumps(report.to_dict(), ensure_ascii=False)
        self.assertNotIn("_raw_bytes", payload)

    def test_relative_paths_with_slash(self) -> None:
        report = ingest.ingest_paths(["lecture_sample.py", "notes_sample.md"], root=self.root)
        paths = sorted(s.path for s in report.sources)
        self.assertEqual(paths, ["lecture_sample.py", "notes_sample.md"])
        for source in report.sources:
            self.assertNotIn("\\", source.path)

    def test_ingest_is_idempotent(self) -> None:
        paths = [self.fixture("lecture_sample.py"), self.fixture("notes_sample.md")]
        first = ingest.ingest_paths(paths, root=self.root)
        second = ingest.ingest_paths(paths, root=self.root)
        self.assertEqual([s.id for s in first.sources], [s.id for s in second.sources])
        self.assertEqual([c.id for c in first.chunks], [c.id for c in second.chunks])
        self.assertEqual([c.text for c in first.chunks], [c.text for c in second.chunks])
        self.assertEqual([c.order for c in first.chunks], [c.order for c in second.chunks])

    def test_root_relative_id_is_stable_across_cwd(self) -> None:
        """id 只由相对路径决定，跟当前工作目录无关。"""
        one = ingest.read_source(self.fixture("notes_sample.md"), root=self.root)
        two = ingest.read_source(self.fixture("notes_sample.md"),
                                 root=Path(os.path.abspath(str(self.root))))
        self.assertEqual(one.id, two.id)
        self.assertEqual(one.path, two.path)

    def test_on_progress_called_once_per_path(self) -> None:
        calls: list[tuple[int, int, str]] = []
        paths = [self.fixture("notes_sample.md"), self.tmp / "nope.md",
                 self.fixture("plain_sample.txt")]
        report = ingest.ingest_paths(paths, root=self.tmp,
                                    on_progress=lambda done, total, path: calls.append((done, total, path)))
        self.assertEqual([c[0] for c in calls], [1, 2, 3])
        self.assertTrue(all(c[1] == 3 for c in calls))
        self.assertEqual(len(report.sources), 2)
        self.assertEqual(len(report.skipped), 1)

    def test_broken_on_progress_does_not_break_ingest(self) -> None:
        def boom(done: int, total: int, path: str) -> None:
            raise RuntimeError("回调炸了")

        report = ingest.ingest_paths([self.fixture("plain_sample.txt")], root=self.tmp,
                                     on_progress=boom)
        self.assertEqual(len(report.sources), 1)

    def test_directory_is_expanded(self) -> None:
        target = self.tmp / "nested" / "deep"
        target.mkdir(parents=True)
        (target / "a.md").write_text("# A\n\n甲。\n", encoding="utf-8")
        (target / "b.txt").write_text("乙。\n", encoding="utf-8")
        report = ingest.ingest_paths([self.tmp], root=self.tmp)
        self.assertEqual(sorted(s.path for s in report.sources),
                         ["nested/deep/a.md", "nested/deep/b.txt"])

    def test_empty_input(self) -> None:
        report = ingest.ingest_paths([], root=self.tmp)
        self.assertEqual((report.sources, report.chunks, report.skipped), ([], [], []))

    def test_source_text_join_is_used_for_chunks(self) -> None:
        report = ingest.ingest_paths([self.fixture("lecture_sample.py")], root=self.root)
        source = report.sources[0]
        for chunk in report.chunks:
            self.assertIn(chunk.text.split("\n")[0], source.text)


# --------------------------------------------------------------------------
# 11. 切块通用约束
# --------------------------------------------------------------------------
class TestChunking(IngestTestBase):
    """切块：不超限、不切句、块顺序连续、参数校验。"""

    def test_max_chars_soft_bound_for_markdown(self) -> None:
        """markdown 的软边界允许小幅超出 max_chars，但不会失控。"""
        body = "第一句讲分词。第二句讲注意力。" * 40
        path = self.temp_file("big.md", body + "\n")
        big = ingest.read_source(path, root=self.tmp)
        chunks = ingest.chunk_source(big, max_chars=300, min_chars=60)
        self.assertGreater(len(chunks), 1)
        soft_max = min(max(300 * 3 // 2, 300), ingest._SOFT_CEILING)
        for chunk in chunks:
            self.assertLessEqual(len(chunk.text), soft_max + ingest._BOUNDARY_SLACK)
        self.assertGreater(len(ingest.chunk_source(self.read("notes_sample.md"))), 0)

    def test_hard_format_respects_max_chars(self) -> None:
        """非 markdown 格式严守 max_chars（超出一点由 _make_units 先切句）。"""
        body = "第一句讲分词。第二句讲注意力。" * 40
        path = self.temp_file("big.txt", body + "\n")
        big = ingest.read_source(path, root=self.tmp)
        chunks = ingest.chunk_source(big, max_chars=300, min_chars=60)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk.text), 300 + ingest._BOUNDARY_SLACK)

    def test_max_chars_validation(self) -> None:
        source = self.read("plain_sample.txt")
        with self.assertRaises(ValueError):
            ingest.chunk_source(source, max_chars=0)

    def test_min_chars_merges_fragments(self) -> None:
        """很短的同节碎片允许并进相邻块（软边界不是硬切）。"""
        path = self.temp_file(
            "frag.md",
            "## 标题\n\n第一段落。\n\n甲。\n\n乙。\n\n丙。\n\n丁。\n\n戊。\n\n己。\n\n庚。\n",
        )
        source = ingest.read_source(path, root=self.tmp)
        chunks = ingest.chunk_source(source, max_chars=60, min_chars=60)
        self.assertLess(len(chunks), 10)
        self.assertTrue(all(chunk.text.strip() for chunk in chunks))
        joined = "".join(chunk.text for chunk in chunks)
        for piece in ("第一段落。", "甲。", "庚。"):
            self.assertIn(piece, joined)

    def test_chunk_orders_are_contiguous(self) -> None:
        report = ingest.ingest_paths(
            [self.fixture(n) for n in ("code_sample.py", "notebook_sample.ipynb")], root=self.root)
        by_source: dict[str, list[int]] = {}
        for chunk in report.chunks:
            by_source.setdefault(chunk.source_id, []).append(chunk.order)
        for orders in by_source.values():
            self.assertEqual(orders, list(range(len(orders))))

    def test_span_offsets_point_into_source_text(self) -> None:
        source = self.read("notes_sample.md")
        for chunk in ingest.chunk_source(source):
            head = chunk.text.split("\n")[0]
            self.assertEqual(source.text[chunk.span.start:chunk.span.start + len(head)], head)

    def test_empty_source_yields_no_chunks(self) -> None:
        source = Source(id="src_e", path="e.txt", title="e", kind="text", text="   ")
        self.assertEqual(ingest.chunk_source(source), [])

    def test_handmade_source_without_meta_still_chunks(self) -> None:
        source = Source(id=protocol.stable_id("src", "hand.txt"), path="hand.txt",
                        title="hand", kind="text",
                        text="第一段。\n\n第二段。")
        chunks = ingest.chunk_source(source)
        self.assertEqual(len(chunks), 2)

    def test_normalize_matches_textutil(self) -> None:
        """归一化必须与 textutil.normalize_text 同结果（行号映射的前提）。"""
        raw = "第一行   \n\n\n\n第二行\u200b\n\ttab 行\n"
        normalized, marks = ingest._normalize_with_map(raw)
        self.assertEqual(normalized, textutil.normalize_text(raw))
        fake = Source(id="src_n", path="n.txt", title="n", kind="text",
                      text=normalized, meta={"line_map": [[o, line] for o, line in marks]})
        self.assertEqual(ingest.line_for_offset(fake, 0), 1)
        self.assertEqual(ingest.line_for_offset(fake, normalized.index("第二行")), 5)
        self.assertEqual(ingest.line_for_offset(fake, normalized.index("tab 行")), 6)


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
