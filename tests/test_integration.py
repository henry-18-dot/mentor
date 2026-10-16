"""真实材料验收：用 D:\\CS336 的讲义回答，并检查"引用能不能落回原文"。

这台机器上有 CS336 课程文件时才会跑（否则 skip）。它验证三件在单元测试里
证不了的事：
1. 讲义 `.py` 解析出来的行号，指向的确实是源码里那一次 `text(...)` 调用；
2. 引用里的 chunk 文本，等于源文件全文按 span 切出来的那一段；
3. 引用位置串形如 `lecture_01.py:699`，能被人直接打开核对。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

ROOT = Path(r"D:\CS336")
LECTURES = ROOT / "lectures"

try:
    from mentor.ingest import ingest_paths
    INGEST_ERROR = ""
except Exception as exc:  # pragma: no cover - 摄取层缺失时跳过
    ingest_paths = None  # type: ignore[assignment]
    INGEST_ERROR = str(exc)


@unittest.skipUnless(LECTURES.exists() and ingest_paths is not None,
                     f"本机没有 CS336 讲义或摄取层不可用：{INGEST_ERROR}")
class RealLectureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.lecture = LECTURES / "lecture_01.py"
        if not cls.lecture.exists():
            raise unittest.SkipTest("没有 lecture_01.py")
        cls.report = ingest_paths([cls.lecture], root=ROOT)
        cls.source = cls.report.sources[0]
        cls.chunks = cls.report.chunks
        cls.raw_lines = cls.lecture.read_text(encoding="utf-8").splitlines()

    def test_lecture_is_parsed_as_slides(self) -> None:
        self.assertEqual(self.source.kind, "lecture-note")
        self.assertGreater(len(self.chunks), 5)
        self.assertIn("CS336", self.source.text)

    def test_every_line_number_is_inside_the_file(self) -> None:
        for chunk in self.chunks:
            if chunk.span.line:
                self.assertGreaterEqual(chunk.span.line, 1, chunk.id)
                self.assertLessEqual(chunk.span.line, len(self.raw_lines), chunk.id)

    def test_slide_lines_point_at_text_calls(self) -> None:
        """讲义片段的行号必须是源码里的一次内容调用。

        讲义片段有四种 kind（prose / list / heading / image），全部来自
        `text(...)`、`link(...)`、`image(...)` 这类调用，所以每一个带行号的
        片段都应该指回一次调用——这正是"引用能落到行"的前提。
        """
        markers = ("text(", "link(", "image(", "article_link(")
        with_line = [c for c in self.chunks if c.span.line]
        checked = 0
        offenders: list[tuple[int, str]] = []
        for chunk in with_line:
            line = self.raw_lines[chunk.span.line - 1]
            if any(marker in line for marker in markers):
                checked += 1
            else:
                offenders.append((chunk.span.line, line.strip()[:60]))
        self.assertGreater(len(with_line), len(self.chunks) * 0.9,
                           "绝大多数片段都应当带行号")
        self.assertEqual(offenders[:5], [], f"这些行号没有落在内容调用上：{offenders[:5]}")
        self.assertEqual(checked, len(with_line))

    def test_chunk_text_matches_source_slice(self) -> None:
        for chunk in self.chunks:
            if chunk.kind == "image":
                continue
            sliced = self.source.text[chunk.span.start:chunk.span.end]
            self.assertEqual(sliced.strip(), chunk.text.strip(), chunk.id)


@unittest.skipUnless(LECTURES.exists() and ingest_paths is not None, "本机没有 CS336 讲义")
class RealNotebookCitationTest(unittest.TestCase):
    """端到端：材料 → 提问 → 每条引用都指得回原文。"""

    @classmethod
    def setUpClass(cls) -> None:
        from mentor.llm import OfflineProvider
        from mentor.store import Store
        from mentor.tutor import Tutor

        cls.tmp = tempfile.TemporaryDirectory()
        cls.store = Store(Path(cls.tmp.name) / "real.db")
        paths = [p for p in (LECTURES / "lecture_01.py", LECTURES / "lecture_06.py") if p.exists()]
        report = ingest_paths(paths, root=ROOT)
        cls.report = report
        notebook = cls.store.create_notebook("真实材料验收")
        cls.store.attach(notebook.id, report.sources, report.chunks)
        cls.notebook_id = notebook.id
        cls.tutor = Tutor(cls.store, OfflineProvider())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.store.close()
        cls.tmp.cleanup()

    def test_answer_citations_point_back_into_files(self) -> None:
        surface = self.tutor.ask(self.notebook_id, "BPE 是怎么训练的？", "explore")
        self.assertTrue(surface.citations)
        hits = 0
        for cite in surface.citations:
            source = self.store.get_source(cite.source_id)
            self.assertIsNotNone(source, cite.chunk_id)
            chunks = {c.id: c for c in self.store.source_chunks(cite.source_id)}
            self.assertIn(cite.chunk_id, chunks)
            chunk = chunks[cite.chunk_id]
            start, end = chunk.span.start, chunk.span.end
            self.assertEqual(source.text[start:end].strip(), chunk.text.strip())
            self.assertIn(":", cite.locator)
            file_name, _, line = cite.locator.rpartition(":")
            self.assertEqual(file_name, Path(source.path).name)
            self.assertTrue(line.isdigit() and int(line) > 0)
            if source.kind == "lecture-note":
                raw = (ROOT / source.path).read_text(encoding="utf-8").splitlines()
                hits += 1
        self.assertGreater(hits, 0, "讲义引用应当能定位到行")

    def test_progress_and_heatmap_need_multiple_sources(self) -> None:
        surface = self.tutor.ask(self.notebook_id, "BPE 和分词的关系", "explore")
        types = {w.type for w in surface.widgets}
        self.assertIn("answer", types)
        if len(self.report.sources) >= 2:
            self.assertTrue(types & {"heatmap", "progress", "sources"}, types)


@unittest.skipUnless(LECTURES.exists() and ingest_paths is not None, "本机没有 CS336 讲义")
class CitationIntegrityTest(unittest.TestCase):
    """Kimi 建议 #3.3：随机抽引用，验证"锚点存在且上下文匹配"，防止幻觉行号。

    与 `test_answer_citations_point_back_into_files` 的区别：那条查的是数据结构
    自洽，这条查的是**打开真实文件、那一行确实讲这句内容**。
    """

    QUESTIONS = [
        ("BPE 是怎么训练的？", "explore"),
        ("这个语法分析器怎么处理符号？", "ask"),
        ("对比字节分词和 BPE", "compare"),
        ("这门课的作业安排是什么？", "brief"),
        ("Kernel 和并行是怎么讲的？", "ask"),
        ("给我一份复习材料", "study"),
    ]

    @classmethod
    def setUpClass(cls) -> None:
        from mentor.llm import OfflineProvider
        from mentor.store import Store
        from mentor.tutor import Tutor

        cls.tmp = tempfile.TemporaryDirectory()
        cls.store = Store(Path(cls.tmp.name) / "cite.db")
        paths = [p for p in sorted(LECTURES.glob("lecture_0*.py"))][:3]
        if not paths:
            raise unittest.SkipTest("没有讲义文件")
        report = ingest_paths(paths, root=ROOT)
        notebook = cls.store.create_notebook("引用完整性")
        cls.store.attach(notebook.id, report.sources, report.chunks)
        cls.notebook_id = notebook.id
        cls.tutor = Tutor(cls.store, OfflineProvider())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.store.close()
        cls.tmp.cleanup()

    def test_random_citations_open_real_lines_with_matching_context(self) -> None:
        import random
        from mentor import textutil as tu

        rng = random.Random(20261015)
        citations = []
        for question, mode in self.QUESTIONS:
            surface = self.tutor.ask(self.notebook_id, question, mode)
            citations.extend(surface.citations)
        self.assertGreater(len(citations), 8, "样本太少，说明检索没命中")

        checked = 0
        for cite in rng.sample(citations, min(12, len(citations))):
            source = self.store.get_source(cite.source_id)
            self.assertIsNotNone(source)
            if source.kind != "lecture-note":
                continue
            path = ROOT / source.path
            self.assertTrue(path.exists(), source.path)
            raw_lines = path.read_text(encoding="utf-8").splitlines()
            _, _, line_text = cite.locator.rpartition(":")
            line = int(line_text)
            self.assertTrue(1 <= line <= len(raw_lines), f"{cite.locator} 越界")

            # 那一行必须是一次内容调用，且带上这段文字的开头
            needle = tu.strip_markdown(cite.quote)[:24].strip()
            window = "\n".join(raw_lines[line - 1: line + 8])
            self.assertTrue(
                any(marker in raw_lines[line - 1] for marker in ("text(", "link(", "image(", "article_link(")),
                f"{cite.locator} 指向的不是内容调用：{raw_lines[line - 1][:60]!r}")
            if needle:
                flat = " ".join(window.split())
                head = " ".join(needle.split())
                self.assertIn(head[:16], flat,
                              f"{cite.locator} 的上下文对不上引用内容：{head[:16]!r}")
            checked += 1
        self.assertGreater(checked, 0, "没有抽到讲义引用")

    def test_citations_never_outnumber_the_available_chunks(self) -> None:
        surface = self.tutor.ask(self.notebook_id, "BPE 分词器是什么？", "explore")
        numbers = [c.n for c in surface.citations]
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)),
                         "引用编号必须从 1 连续，界面才能按序号渲染 chip")


if __name__ == "__main__":
    unittest.main()
