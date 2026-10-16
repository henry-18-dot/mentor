"""编排层测试：Store / Tutor / 导出。全部用手工构造的材料，不依赖摄取层。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mentor.llm import OfflineProvider
from mentor.protocol import Chunk, Source, Span, surface_problems
from mentor.render import surface_to_markdown
from mentor.store import Store
from mentor.tutor import Tutor


def chunk(cid: str, source_id: str, text: str, *, line: int, heading: str = "",
          kind: str = "prose", order: int = 0) -> Chunk:
    return Chunk(id=cid, source_id=source_id, text=text,
                 span=Span(start=0, end=len(text), line=line, section=heading,
                           source_id=source_id),
                 heading=heading, kind=kind, order=order)


def sample_corpus() -> tuple[list[Source], list[Chunk]]:
    lecture = Source(id="src_lecture", path="lectures/lecture_01.py", title="Tokenization",
                     kind="lecture-note", text="")
    notes = Source(id="src_notes", path="docs/notes.md", title="分词笔记",
                   kind="markdown", text="")
    chunks = [
        chunk("c1", "src_lecture",
              "**Byte-Pair Encoding (BPE)** is a tokenizer that merges the most common pair of "
              "adjacent tokens. BPE was introduced in 1994 for data compression.", line=10,
              heading="Byte Pair Encoding"),
        chunk("c2", "src_lecture",
              "In 2019 GPT-2 adopted BPE. In 2017 the Transformer used word-based tokenization. "
              "BPE is data driven, the vocabulary is learned from raw text.", line=40,
              heading="History"),
        chunk("c3", "src_lecture",
              "```python\ndef train_bpe(string, num_merges):\n    indices = list(string.encode())\n"
              "    return indices\n```", line=60, heading="Code", kind="code"),
        chunk("c4", "src_lecture", "images/tokenized-example.png", line=70,
              heading="Tokenized example", kind="image"),
        chunk("c5", "src_notes",
              "字节分词是把字符串映射成 0~255 的字节序列。压缩比是每个 token 覆盖的字节数。",
              line=3, heading="字节分词"),
        chunk("c6", "src_notes",
              "The compression ratio is 4.5 for the sample string; vocabulary size is 256.",
              line=7, heading="字节分词"),
    ]
    return [lecture, notes], chunks


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "mentor.db")
        self.sources, self.chunks = sample_corpus()
        self.notebook = self.store.create_notebook("测试笔记本")
        self.store.attach(self.notebook.id, self.sources, self.chunks)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_notebook_roundtrip(self) -> None:
        notebooks = self.store.list_notebooks()
        self.assertEqual(len(notebooks), 1)
        self.assertEqual(notebooks[0]["source_count"], 2)
        self.assertEqual(notebooks[0]["title"], "测试笔记本")

    def test_sources_and_chunks_persist(self) -> None:
        sources = self.store.notebook_sources(self.notebook.id)
        self.assertEqual({s.path for s in sources}, {"lectures/lecture_01.py", "docs/notes.md"})
        chunks = self.store.notebook_chunks(self.notebook.id)
        self.assertEqual(len(chunks), len(self.chunks))
        self.assertEqual(chunks[0].span.line, 10)

    def test_attach_is_idempotent(self) -> None:
        before = len(self.store.notebook_chunks(self.notebook.id))
        self.store.attach(self.notebook.id, self.sources, self.chunks)
        after = len(self.store.notebook_chunks(self.notebook.id))
        self.assertEqual(before, after)
        self.assertEqual(len(self.store.notebook_sources(self.notebook.id)), 2)

    def test_index_is_cached_and_invalidated(self) -> None:
        first = self.store.index(self.notebook.id)
        self.assertIs(first, self.store.index(self.notebook.id))
        self.store.attach(self.notebook.id, self.sources[:1], self.chunks[:1])
        self.assertIsNot(first, self.store.index(self.notebook.id))

    def test_index_search_works_after_reload(self) -> None:
        hits = self.store.index(self.notebook.id).search("BPE tokenizer")
        self.assertTrue(hits)
        self.assertEqual(hits[0].chunk.source_id, "src_lecture")

    def test_turn_roundtrip(self) -> None:
        tutor = Tutor(self.store, OfflineProvider())
        surface = tutor.ask(self.notebook.id, "BPE 是什么？")
        stored = self.store.get_turn(surface.id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored["question"], "BPE 是什么？")
        self.assertEqual(len(self.store.list_turns(self.notebook.id)), 1)

    def test_stats_report_counts(self) -> None:
        stats = self.store.notebook_stats(self.notebook.id)
        self.assertEqual(stats["sources"], 2)
        self.assertEqual(stats["chunks"], len(self.chunks))


class TutorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "mentor.db")
        self.sources, self.chunks = sample_corpus()
        self.notebook = self.store.create_notebook("测试笔记本")
        self.store.attach(self.notebook.id, self.sources, self.chunks)
        self.tutor = Tutor(self.store, OfflineProvider())

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def collect(self, question: str, mode: str | None = None) -> list[dict]:
        return list(self.tutor.ask_stream(self.notebook.id, question, mode))

    def surface_of(self, events: list[dict]) -> dict:
        finals = [e for e in events if e["event"] == "surface"]
        self.assertEqual(len(finals), 1)
        return finals[0]["data"]["surface"]

    def test_event_order(self) -> None:
        events = self.collect("BPE 是怎么训练的？", "explore")
        names = [e["event"] for e in events]
        self.assertEqual(names[0], "plan")
        self.assertEqual(names[1], "retrieval")
        self.assertEqual(names[-1], "done")
        self.assertIn("surface", names)
        self.assertLess(names.index("surface"), names.index("done"))

    def test_surface_passes_contract_check(self) -> None:
        from mentor.tutor import _surface_from_dict
        for question, mode in [("BPE 是怎么训练的？", "explore"),
                               ("给我一套复习材料", "study"),
                               ("对比字节分词和 BPE", "compare"),
                               ("梳理一下时间线", "brief"),
                               ("随便聊聊", "ask")]:
            surface = _surface_from_dict(self.surface_of(self.collect(question, mode)))
            self.assertEqual(surface_problems(surface), [], f"{question} 违约")

    def test_every_citation_points_at_real_chunk(self) -> None:
        surface = self.surface_of(self.collect("BPE merges tokens", "ask"))
        chunk_ids = {c.id for c in self.store.notebook_chunks(self.notebook.id)}
        self.assertTrue(surface["citations"])
        for cite in surface["citations"]:
            self.assertIn(cite["chunk_id"], chunk_ids)
            self.assertTrue(cite["locator"])
            self.assertTrue(cite["n"] >= 1)

    def test_answer_citations_are_within_range(self) -> None:
        surface = self.surface_of(self.collect("BPE 和字节分词", "explore"))
        valid = {c["n"] for c in surface["citations"]}
        answer = next(w for w in surface["widgets"] if w["type"] == "answer")
        import re
        used = {int(n) for n in re.findall(r"\[(\d+)\]", answer["data"]["markdown"])}
        self.assertTrue(used)
        self.assertTrue(used <= valid, f"越界引用 {used - valid}")

    def test_study_mode_produces_learning_widgets(self) -> None:
        surface = self.surface_of(self.collect("给我一套复习材料", "study"))
        types = {w["type"] for w in surface["widgets"]}
        self.assertIn("answer", types)
        self.assertTrue(types & {"cheatsheet", "flashcards", "quiz"}, types)

    def test_compare_mode_produces_table(self) -> None:
        surface = self.surface_of(self.collect("对比字节分词和 BPE", "compare"))
        types = {w["type"] for w in surface["widgets"]}
        self.assertIn("compare_table", types)

    def test_brief_mode_produces_timeline_when_years_exist(self) -> None:
        surface = self.surface_of(self.collect("梳理这门课的时间线", "brief"))
        types = {w["type"] for w in surface["widgets"]}
        self.assertIn("timeline", types)

    def test_explore_mode_produces_heatmap_with_two_sources(self) -> None:
        # 只有检索同时命中两份材料时，"主题 × 材料"矩阵才有意义。
        surface = self.surface_of(self.collect("BPE 和字节分词的关系", "explore"))
        heat = next((w for w in surface["widgets"] if w["type"] == "heatmap"), None)
        self.assertIsNotNone(heat)
        rows, columns, cells = heat["data"]["rows"], heat["data"]["columns"], heat["data"]["cells"]
        self.assertEqual(len(cells), len(rows))
        self.assertTrue(all(len(row) == len(columns) for row in cells))
        self.assertTrue(all(0.0 <= v <= 1.0 for row in cells for v in row))

    def test_empty_question_reports_error(self) -> None:
        events = self.collect("   ")
        self.assertEqual(events[0]["event"], "error")
        self.assertEqual(events[-1]["event"], "done")

    def test_unknown_notebook_reports_error(self) -> None:
        events = list(self.tutor.ask_stream("nb_missing", "问题"))
        self.assertEqual(events[0]["event"], "error")

    def test_uncovered_question_degrades_honestly(self) -> None:
        events = self.collect("量子纠缠和超导体的关系", "ask")
        surface = self.surface_of(events)
        self.assertTrue(surface["degraded"])
        self.assertFalse(surface["citations"])
        answer = next(w for w in surface["widgets"] if w["type"] == "answer")
        self.assertIn("没有找到", answer["data"]["markdown"])

    def test_followups_are_offered_in_empty_state(self) -> None:
        events = self.collect("量子纠缠和超导体的关系", "ask")
        types = {w["type"] for w in self.surface_of(events)["widgets"]}
        self.assertIn("followups", types)

    def test_quiz_questions_are_answerable(self) -> None:
        surface = self.surface_of(self.collect("给我一套复习材料", "study"))
        quiz = next((w for w in surface["widgets"] if w["type"] == "quiz"), None)
        if quiz is None:
            self.skipTest("这份材料没有生成自测题")
        for q in quiz["data"]["questions"]:
            self.assertGreaterEqual(len(q["choices"]), 2)
            self.assertTrue(0 <= q["answer_index"] < len(q["choices"]))
            self.assertTrue(q["why"])

    def test_provider_failure_falls_back_to_offline(self) -> None:
        from mentor.llm.base import Provider, ProviderError

        class Broken(Provider):
            name = "broken"

            def available(self) -> bool:
                return True

            def generate(self, request):  # type: ignore[override]
                raise ProviderError("故意失败")

            def generate_stream(self, request):  # type: ignore[override]
                raise ProviderError("故意失败")
                yield  # pragma: no cover

        tutor = Tutor(self.store, Broken())
        events = list(tutor.ask_stream(self.notebook.id, "BPE 是什么？", "ask"))
        surface = [e for e in events if e["event"] == "surface"][0]["data"]["surface"]
        self.assertTrue(surface["degraded"])
        self.assertIn("offline", surface["provider"])
        self.assertTrue(any(w["type"] == "answer" for w in surface["widgets"]))
        self.assertTrue(any("故意失败" in n for n in surface["notes"]))


class RenderTest(unittest.TestCase):
    def test_markdown_export_has_answer_and_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "m.db")
            sources, chunks = sample_corpus()
            notebook = store.create_notebook("导出测试")
            store.attach(notebook.id, sources, chunks)
            surface = Tutor(store, OfflineProvider()).ask(notebook.id, "BPE 是什么？", "ask")
            text = surface_to_markdown(surface)
            self.assertIn("# BPE 是什么？", text)
            self.assertIn("## 来源", text)
            self.assertIn("lecture_01.py:", text)
            store.close()


if __name__ == "__main__":
    unittest.main()
