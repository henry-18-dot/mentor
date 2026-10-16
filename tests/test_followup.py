"""多轮追问上下文：把上一轮引用过的片段带进本轮。

"展开第 2 点""继续"这类追问和材料没有词面重合，纯检索会得到空上下文；
`context_turns>0` 时把最近几轮引用过的块并进上下文，追问才接得上。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mentor.llm import OfflineProvider
from mentor.store import Store
from mentor.tutor import Tutor

from .test_pipeline import sample_corpus


class FollowUpContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "followup.db")
        sources, chunks = sample_corpus()
        self.notebook = self.store.create_notebook("追问测试")
        self.store.attach(self.notebook.id, sources, chunks)
        self.tutor = Tutor(self.store, OfflineProvider())

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_follow_up_without_lexical_overlap_uses_prior_context(self) -> None:
        first = self.tutor.ask(self.notebook.id, "BPE 是怎么训练的？", "explore")
        self.assertTrue(first.citations, "第一轮应当命中材料")
        first_chunks = {c.chunk_id for c in first.citations}

        # 这句和语料没有任何词面重合：没有上下文时只能走"材料里没有"的空状态
        follow = self.tutor.ask(self.notebook.id, "继续展开讲讲", "ask")
        follow_chunks = {c.chunk_id for c in follow.citations}
        self.assertTrue(follow.citations, "带上下文时追问不该是空的")
        self.assertTrue(follow_chunks & first_chunks,
                        "追问应当复用上一轮引用过的片段")
        self.assertFalse(follow.degraded)
        self.assertTrue(any("上一轮" in note for note in follow.notes), follow.notes)

    def test_follow_up_without_context_is_honest_empty_state(self) -> None:
        self.tutor.ask(self.notebook.id, "BPE 是怎么训练的？", "explore")
        events = list(self.tutor.ask_stream(self.notebook.id, "继续展开讲讲", "ask",
                                            context_turns=0))
        surface = [e for e in events if e["event"] == "surface"][0]["data"]["surface"]
        self.assertFalse(surface["citations"], "关掉上下文后不该凭空引用")
        self.assertTrue(surface["degraded"])

    def test_prior_context_is_capped_and_deduped(self) -> None:
        for question in ("BPE 是怎么训练的？", "字节分词和 BPE 有什么区别？"):
            self.tutor.ask(self.notebook.id, question, "explore")
        events = list(self.tutor.ask_stream(self.notebook.id, "继续", "ask"))
        retrieval = [e for e in events if e["event"] == "retrieval"][0]["data"]
        ids = [hit["chunk_id"] for hit in retrieval["hits"]]
        self.assertEqual(len(ids), len(set(ids)), "上下文片段不能重复")
        self.assertLessEqual(len(ids), 8)

    def test_history_questions_are_passed_to_provider(self) -> None:
        from mentor.llm.base import GenerateRequest, Provider

        captured: dict[str, GenerateRequest] = {}

        class Spy(Provider):
            name = "spy"

            def available(self) -> bool:
                return True

            def generate(self, request):  # type: ignore[override]
                captured["req"] = request
                return [{"type": "answer", "data": {"markdown": "ok [1]"}, "cites": [1]}]

        tutor = Tutor(self.store, Spy())
        tutor.ask(self.notebook.id, "BPE 是怎么训练的？", "ask")
        tutor.ask(self.notebook.id, "那字节分词呢？", "ask")
        self.assertIn("BPE 是怎么训练的？", captured["req"].history,
                      "追问时应当把上一轮的问题带给模型")

    def test_context_turns_zero_skips_history(self) -> None:
        from mentor.llm.base import GenerateRequest, Provider

        seen: list[list[str]] = []

        class Spy(Provider):
            name = "spy"

            def available(self) -> bool:
                return True

            def generate(self, request):  # type: ignore[override]
                seen.append(list(request.history))
                return [{"type": "answer", "data": {"markdown": "ok [1]"}, "cites": [1]}]

        tutor = Tutor(self.store, Spy())
        tutor.ask(self.notebook.id, "BPE 是怎么训练的？", "ask")
        # 用一个能词面命中的问题，确保这轮真的会调用 provider
        list(tutor.ask_stream(self.notebook.id, "BPE 的直觉是什么？", "ask",
                              context_turns=0))
        self.assertGreaterEqual(len(seen), 2, "第二轮应当调到 provider")
        self.assertEqual(seen[1], [], "context_turns=0 时不应带历史")


if __name__ == "__main__":
    unittest.main()
