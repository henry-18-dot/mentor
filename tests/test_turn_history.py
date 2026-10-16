"""问答历史不许静默丢轮次。

**这次的 bug**：`Surface.id` 由 `stable_id(..., question, created_at, provider)` 生成，而
`created_at` 只精确到秒。同一个问题在同一秒内问两次 → **同一个 id** → `save_turn` 的
`INSERT OR REPLACE` 把上一轮覆盖。实测连问 200 次只留下 26 轮（13%）。

用户能碰到它的场景：连点两次发送、回车按两下、脚本/批处理里循环提问、
或者两个进程共用一个库。界面上看起来有两轮，重新加载后少了一轮。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mentor.ingest import ingest_paths
from mentor.llm import OfflineProvider
from mentor.store import Store
from mentor.tutor import Tutor


class TurnHistoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "turns.db")
        report = ingest_paths([Path(r"D:\CS336\lectures\lecture_01.py")], root=Path(r"D:\CS336"))
        self.notebook = self.store.create_notebook("历史")
        self.store.attach(self.notebook.id, report.sources, report.chunks)
        self.tutor = Tutor(self.store, OfflineProvider())

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_same_question_in_the_same_second_keeps_every_turn(self) -> None:
        surfaces = [self.tutor.ask(self.notebook.id, "BPE 是怎么训练的？", "ask") for _ in range(6)]
        ids = [s.id for s in surfaces]
        self.assertEqual(len(set(ids)), 6, "同一秒内的多次提问必须拿到不同的 surface id")
        turns = self.store.list_turns(self.notebook.id, limit=50)
        self.assertEqual(len(turns), 6, f"6 次提问必须留下 6 轮，实际 {len(turns)}")
        for surface in surfaces:
            self.assertIsNotNone(self.store.get_turn(surface.id), f"{surface.id} 查不到")

    def test_repeated_batch_keeps_linear_growth(self) -> None:
        """批处理场景：反复问同样的问题，轮次数必须等于提问次数。"""
        asked = 12
        for _ in range(asked):
            self.tutor.ask(self.notebook.id, "字节分词是什么？", "ask")
        turns = self.store.list_turns(self.notebook.id, limit=100)
        self.assertEqual(len(turns), asked)
        self.assertEqual([t["question"] for t in turns], ["字节分词是什么？"] * asked)

    def test_identical_questions_with_different_modes_are_separate(self) -> None:
        for mode in ("ask", "explore", "brief"):
            self.tutor.ask(self.notebook.id, "注意力复杂度是多少？", mode)
        self.assertEqual(len(self.store.list_turns(self.notebook.id, limit=20)), 3)

    def test_turn_payloads_are_all_retrievable(self) -> None:
        for i in range(5):
            self.tutor.ask(self.notebook.id, f"第 {i} 个问题：BPE 怎么训练？", "ask")
        payloads = self.store.list_turn_payloads(self.notebook.id, limit=10)
        self.assertEqual(len(payloads), 5)
        for payload in payloads:
            self.assertTrue(payload["surface"]["widgets"], "存下来的 surface 不该是空的")
            self.assertTrue(self.store.get_turn(payload["id"]))


if __name__ == "__main__":
    unittest.main()
