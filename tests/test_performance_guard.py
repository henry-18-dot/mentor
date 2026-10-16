"""性能护栏：只盯"灾难性退化"，不追具体数字。

真实基线在 `docs/performance.md`（检索中位 0.2 ms、端到端 17 ms）。这里把阈值放宽两个数量级：
机器负载波动不该让测试变红，但把检索不小心写成 O(n²) 这种必须变红。
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(r"D:\CS336")
LECTURES = ROOT / "lectures"

try:
    from mentor.ingest import ingest_paths
except Exception:  # pragma: no cover
    ingest_paths = None  # type: ignore[assignment]


@unittest.skipUnless(LECTURES.exists() and ingest_paths is not None, "本机没有 CS336 讲义")
class PerformanceGuardTest(unittest.TestCase):
    COLD_INDEX_BUDGET = 5.0      # 秒
    SEARCH_BUDGET = 0.5          # 秒/次
    ASK_BUDGET = 5.0             # 秒/次

    @classmethod
    def setUpClass(cls) -> None:
        from mentor.store import Store

        cls.tmp = tempfile.TemporaryDirectory()
        cls.store = Store(Path(cls.tmp.name) / "guard.db")
        paths = [p for p in sorted(LECTURES.glob("lecture_0*.py")) if p.exists()][:2]
        report = ingest_paths(paths, root=ROOT)
        notebook = cls.store.create_notebook("性能护栏")
        cls.store.attach(notebook.id, report.sources, report.chunks)
        cls.notebook_id = notebook.id
        cls.chunk_count = len(report.chunks)
        started = time.perf_counter()
        cls.index = cls.store.index(notebook.id)
        cls.cold_seconds = time.perf_counter() - started

    @classmethod
    def tearDownClass(cls) -> None:
        cls.store.close()
        cls.tmp.cleanup()

    def test_corpus_is_big_enough_to_be_meaningful(self) -> None:
        self.assertGreater(self.chunk_count, 100, "护栏要跑在真实规模的语料上")

    def test_cold_index_is_not_quadratic(self) -> None:
        self.assertLess(self.cold_seconds, self.COLD_INDEX_BUDGET,
                        f"冷建索引 {self.cold_seconds:.2f}s，超过 {self.COLD_INDEX_BUDGET}s —— 疑似复杂度退化")

    def test_single_search_is_fast(self) -> None:
        started = time.perf_counter()
        hits = self.index.search("BPE 分词器是怎么训练的？", limit=8)
        elapsed = time.perf_counter() - started
        self.assertTrue(hits)
        self.assertLess(elapsed, self.SEARCH_BUDGET,
                        f"单次检索 {elapsed * 1000:.1f}ms，超过 {self.SEARCH_BUDGET * 1000:.0f}ms")

    def test_end_to_end_ask_is_fast_offline(self) -> None:
        from mentor.llm import OfflineProvider
        from mentor.tutor import Tutor

        tutor = Tutor(self.store, OfflineProvider())
        started = time.perf_counter()
        surface = tutor.ask(self.notebook_id, "BPE 是怎么训练的？", "explore")
        elapsed = time.perf_counter() - started
        self.assertTrue(surface.widgets)
        self.assertLess(elapsed, self.ASK_BUDGET,
                        f"端到端离线问答 {elapsed:.2f}s，超过 {self.ASK_BUDGET}s")

    def test_index_cache_returns_instantly(self) -> None:
        started = time.perf_counter()
        self.store.index(self.notebook_id)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 0.05, "第二次取索引应当命中缓存")


class IngestScalingTest(unittest.TestCase):
    """摄取不能是超线性的。

    由来：`line_for_offset()` 原本每次调用都重建整张 line_map，而它按片段调用一次，
    于是摄取退化成 O(n²)——4000 行 1615ms、8000 行 12914ms（实测），
    每片段耗时随规模线性上涨。这里用一个足够大的规模把这个坑钉住。
    """

    LINES = 6000
    BUDGET = 2.5  # 秒；修复后实测约 0.35s，退化成 O(n²) 时约 7s

    def test_ingesting_many_chunks_stays_linear(self) -> None:
        from mentor.ingest import ingest_paths

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "scale.py"
            path.write_text(
                "\n".join(f'text("line {i} tokenizer merge pair")' for i in range(self.LINES)),
                encoding="utf-8",
            )
            started = time.perf_counter()
            report = ingest_paths([path], root=root)
            elapsed = time.perf_counter() - started
        self.assertEqual(len(report.chunks), self.LINES)
        per_chunk_ms = elapsed * 1000 / self.LINES
        self.assertLess(
            elapsed, self.BUDGET,
            f"摄取 {self.LINES} 片段用了 {elapsed:.2f}s（{per_chunk_ms:.3f} ms/片段），"
            f"疑似超线性退化（O(n²) 时约 7s）",
        )


if __name__ == "__main__":
    unittest.main()
