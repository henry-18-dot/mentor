"""整本导出：把最近的问答拼成一份带出处的学习指南。

重点不是"把 JSON 打印成 markdown"，而是导出的东西能当资料用：
问题按时间正序、每轮正文与出处都在、末尾有一张"哪些原文被反复引用"的总索引。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mentor.llm import OfflineProvider
from mentor.render import notebook_to_markdown
from mentor.store import Store
from mentor.tutor import Tutor

from .test_pipeline import sample_corpus


class NotebookExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "export.db")
        sources, chunks = sample_corpus()
        self.notebook = self.store.create_notebook("导出测试笔记本")
        self.store.attach(self.notebook.id, sources, chunks)
        self.tutor = Tutor(self.store, OfflineProvider())
        self.questions = ["BPE 是怎么训练的？", "字节分词和 BPE 有什么区别？"]
        for question in self.questions:
            self.tutor.ask(self.notebook.id, question, "explore")

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def build(self, limit: int = 20) -> str:
        turns = self.store.list_turn_payloads(self.notebook.id, limit=limit)
        notebook = self.store.get_notebook(self.notebook.id)
        sources = [s.to_dict() for s in self.store.notebook_sources(self.notebook.id)]
        return notebook_to_markdown(notebook.to_dict(), turns, sources,
                                    generated_at="2026-10-15 23:30")

    def test_guide_has_title_sources_and_turns_in_time_order(self) -> None:
        text = self.build()
        self.assertIn("# 导出测试笔记本 · 学习指南", text)
        self.assertIn("2026-10-15 23:30", text)
        self.assertIn("## 材料", text)
        self.assertIn("lectures/lecture_01.py", text)
        self.assertIn("docs/notes.md", text)
        first = text.index(self.questions[0])
        second = text.index(self.questions[1])
        self.assertLess(first, second, "目录与正文都要按时间正序")
        self.assertIn("## 目录", text)

    def test_guide_keeps_citations_and_builds_index(self) -> None:
        text = self.build()
        self.assertIn("## 引用总索引", text)
        self.assertRegex(text, r"-\s+`[a-z0-9_]+\.py:\d+`")
        self.assertIn("被", text)
        # 每轮的出处必须跟正文一起带走，不能只留"漂亮结论"
        self.assertIn("## 来源", text)

    def test_limit_keeps_the_most_recent_turns(self) -> None:
        text = self.build(limit=1)
        self.assertIn(self.questions[1], text)
        self.assertNotIn(self.questions[0], text,
                         "limit=1 时应导出最近一轮，而不是最早一轮")

    def test_empty_notebook_still_produces_a_usable_file(self) -> None:
        empty = self.store.create_notebook("空笔记本")
        notebook = self.store.get_notebook(empty.id)
        text = notebook_to_markdown(notebook.to_dict(), [], [], generated_at="now")
        self.assertIn("# 空笔记本 · 学习指南", text)
        self.assertIn("还没有问答记录", text)

    def test_cli_command_writes_the_file(self) -> None:
        from mentor import cli

        out = Path(self.tmp.name) / "guide.md"
        code = cli.main(["--db", str(self.store.path), "export-notebook",
                         "--notebook", self.notebook.id, "--out", str(out)])
        self.assertEqual(code, 0)
        self.assertTrue(out.exists())
        written = out.read_text(encoding="utf-8")
        self.assertIn(self.questions[0], written)
        self.assertIn(self.questions[1], written)


if __name__ == "__main__":
    unittest.main()
