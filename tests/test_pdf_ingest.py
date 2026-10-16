"""PDF 摄取：真实 PDF 必须能读进来，载荷必须无损往返。

**这次修的 bug**：`meta["raw_text"]` 里存的 PDF 载荷用的是 `surrogateescape` 解码
（含 `\\udc80`~`\\udcff` 孤立代理码元），而读回时用的是严格 `encode("utf-8")` —— 于是每份
真实 PDF 都抛 `UnicodeEncodeError: surrogates not allowed` 被跳过，**整个 PDF 路径实际是死的**。
（仓库里 6 份 CS336 作业 PDF 全中；单元测试用的是手搓的 ASCII PDF，所以一直没暴露。）
现在写入/读回都用 latin-1 单字节恒等映射，并对老库里的 surrogateescape 形式兜底。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from mentor.ingest import _pdf_pages, _pdf_payload, ingest_paths, read_source

CS336 = Path(r"D:\CS336")
REAL_PDFS = [
    CS336 / "assignments/assignment1-basics/cs336_assignment1_basics.pdf",
    CS336 / "assignments/assignment2-systems/cs336_assignment2_systems.pdf",
]
MINIMAL_PDF = (Path(__file__).resolve().parent / "test_ingest.py").exists()


def has_surrogates(text: str) -> bool:
    return any(0xD800 <= ord(ch) <= 0xDFFF for ch in text)


class PdfPayloadRoundTripTest(unittest.TestCase):
    """载荷必须无损：任何字节都能进 meta、出 meta。"""

    def test_all_byte_values_round_trip(self) -> None:
        payload = bytes(range(256)) * 3
        stored = payload.decode("latin-1")          # 写入方式（read_source 用的就是这个）
        self.assertFalse(has_surrogates(stored), "meta 里不该出现代理码元")
        self.assertEqual(_pdf_payload(stored), payload, "载荷往返必须一个字节不差")

    def test_stored_form_is_json_safe(self) -> None:
        """meta 要能进 SQLite / JSON —— 这正是当初想用字符串存的原因。"""
        stored = bytes(range(256)).decode("latin-1")
        dumped = json.dumps({"raw_text": stored}, ensure_ascii=False).encode("utf-8")
        self.assertTrue(dumped)

    def test_surrogateescape_form_still_supported(self) -> None:
        """老库里存的是 surrogateescape 形式：读回必须还能用（向后兼容）。"""
        payload = b"%PDF-1.4\n" + bytes(range(128, 256))
        legacy = payload.decode("utf-8", "surrogateescape")
        self.assertTrue(has_surrogates(legacy))
        self.assertEqual(_pdf_payload(legacy), payload)

    def test_pages_accept_both_forms(self) -> None:
        raw = bytes(range(256)) * 4
        as_bytes, _, _ = _pdf_pages(raw)
        as_latin1, _, _ = _pdf_pages(raw.decode("latin-1"))
        self.assertEqual(as_bytes, as_latin1)


@unittest.skipUnless(MINIMAL_PDF, "缺少 tests/test_ingest.py 里的最小 PDF 构造器")
class RealPdfIngestTest(unittest.TestCase):
    """真实课程 PDF：以前 100% 被跳过，现在必须能读、页码要对。"""

    @classmethod
    def setUpClass(cls) -> None:
        if not all(p.exists() for p in REAL_PDFS):
            raise unittest.SkipTest("本机没有 CS336 作业 PDF")
        cls.report = ingest_paths(REAL_PDFS, root=CS336)

    def test_nothing_is_skipped(self) -> None:
        self.assertEqual(self.report.skipped, [], f"真实 PDF 不该被跳过：{self.report.skipped}")
        self.assertEqual(len(self.report.sources), len(REAL_PDFS))

    def test_pages_and_text_look_real(self) -> None:
        for source in self.report.sources:
            chunks = [c for c in self.report.chunks if c.source_id == source.id]
            self.assertGreater(len(chunks), 10, f"{source.path} 只切出 {len(chunks)} 块")
            self.assertEqual(source.meta.get("line_means"), "page")
            pages = [c.span.line for c in chunks]
            self.assertGreaterEqual(min(pages), 1)
            self.assertLessEqual(max(pages), len(pages) + 60, "页码不该离谱地大")
            joined = source.text.lower()
            self.assertIn("assignment", joined, "抽出来的不像作业说明")
            self.assertIn("cs336", joined)

    def test_meta_raw_text_is_json_safe_and_lossless(self) -> None:
        for source in self.report.sources:
            stored = source.meta.get("raw_text")
            self.assertIsInstance(stored, str)
            self.assertFalse(has_surrogates(stored), "meta 里出现了代理码元，写库会炸")
            json.dumps({"raw_text": stored}, ensure_ascii=False).encode("utf-8")
            self.assertTrue(_pdf_payload(stored).startswith(b"%PDF"), "载荷还原后应是 PDF 字节")

    def test_single_pdf_read_source_is_reusable_for_chunking(self) -> None:
        """`chunk_source` 靠 meta 里的载荷重放解析，所以必须能再解析一次。"""
        source = read_source(REAL_PDFS[0], root=CS336)
        again, backend, _ = _pdf_pages(source.meta["raw_text"])
        self.assertTrue(again, "meta 里的载荷不能再解析")
        self.assertEqual(backend, "pypdf")
        self.assertTrue(any("Assignment" in page for page in again))

    def test_pdf_does_not_hand_binary_payload_to_the_ui(self) -> None:
        """右栏渲染的是 `raw || text`：PDF 的 raw 是二进制载荷，绝不能给它。"""
        source = read_source(REAL_PDFS[0], root=CS336)
        payload = source.to_dict(with_text=True)
        self.assertNotIn("raw", payload, "PDF 不该把二进制载荷当原文发给界面")
        self.assertEqual(source.display_text(), source.text)
        self.assertIn("Assignment", source.display_text())

    def test_lecture_still_sends_raw_for_real_line_numbers(self) -> None:
        lecture = CS336 / "lectures/lecture_01.py"
        if not lecture.exists():
            self.skipTest("本机没有讲义")
        source = read_source(lecture, root=CS336)
        self.assertIn("raw", source.to_dict(with_text=True),
                      "行号=真实行的材料必须继续带 raw，否则右栏行号对不上")

    def test_search_locator_uses_readable_text_for_pdf(self) -> None:
        from mentor.server import _locate_term

        source = read_source(REAL_PDFS[0], root=CS336)
        found = _locate_term(source, "BPE")
        self.assertTrue(found["found"], "PDF 的搜索定位应当能在正文里找到词")
        self.assertGreater(found["line"], 0)
        self.assertLessEqual(found["line"], len(source.text.split("\n")))


if __name__ == "__main__":
    unittest.main()
