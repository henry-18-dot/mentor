"""跨文字体系提问：要说清"为什么一个词都没命中"。

离线检索是**按字面**匹配的。中文提问配英文材料（CS336 的作业 PDF 全是英文）时，
结果必然是空的 —— 这是工具的**已知限制**，不是材料的错。以前界面只回一句
"材料里没有找到与这个问题直接相关的内容"，用户只会以为"这工具搜不到东西"。
现在会把原因讲出来，并给出可行的替代（换英文关键词）。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mentor import textutil as tu
from mentor.ingest import ingest_paths
from mentor.llm import OfflineProvider
from mentor.store import Store
from mentor.tutor import Tutor

ENGLISH_MATERIAL = """\
# Building a Transformer Language Model

The tokenizer uses byte-pair encoding to merge the most frequent adjacent token pairs.
Training the tokenizer on raw text yields a vocabulary tailored to the corpus.
The attention mechanism computes a weighted sum of value vectors.
"""

CHINESE_MATERIAL = """\
# 讲义笔记

字节分词把字符串映射成 0~255 的字节序列，BPE 则反复合并最常见的相邻对。
注意力的复杂度在序列长度上是平方级，这是长上下文的核心瓶颈。
"""


class ScriptOfTest(unittest.TestCase):
    def test_english_is_latin(self) -> None:
        self.assertEqual(tu.script_of("BPE tokenizer merges pairs"), "latin")

    def test_chinese_is_cjk(self) -> None:
        self.assertEqual(tu.script_of("字节分词把字符串映射成字节序列"), "cjk")

    def test_chinese_with_latin_terms_is_still_cjk(self) -> None:
        self.assertEqual(tu.script_of("BPE 分词器是怎么训练的？"), "cjk")

    def test_empty_is_unknown(self) -> None:
        self.assertEqual(tu.script_of(""), "unknown")
        self.assertEqual(tu.script_of("123 456 ——"), "unknown")

    def test_heavy_mix_is_mixed(self) -> None:
        self.assertEqual(tu.script_of("注意力 attention 是核心 mechanism 之一"), "mixed")

    def test_boundary_a_quarter_minority_stays_dominant(self) -> None:
        """少数派文字不到三成时算"主导"：技术中文里夹几个英文词很常见。"""
        self.assertEqual(tu.script_of("BPE 分词器是怎么训练的"), "cjk")
        self.assertEqual(tu.script_of("tokenizer 分词注意力"), "mixed")


class CrossScriptHintTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "hint.db")
        self.tutor = Tutor(self.store, OfflineProvider())

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def notebook_with(self, name: str, text: str) -> str:
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        report = ingest_paths([path], root=self.root)
        notebook = self.store.create_notebook(name)
        self.store.attach(notebook.id, report.sources, report.chunks)
        return notebook.id

    def answer_of(self, notebook_id: str, question: str):
        events = list(self.tutor.ask_stream(notebook_id, question, "ask"))
        surface = [e for e in events if e["event"] == "surface"][0]["data"]["surface"]
        answer = next((w for w in surface["widgets"] if w["type"] == "answer"), None)
        return surface, (answer or {}).get("data", {}).get("markdown", "")

    def test_chinese_question_over_english_material_explains_why(self) -> None:
        notebook = self.notebook_with("english.md", ENGLISH_MATERIAL)
        surface, markdown = self.answer_of(notebook, "这份材料讲了什么优化技巧？")
        self.assertEqual(surface["citations"], [], "字面检索确实命中不了，别造假")
        self.assertIn("英文", markdown, "答复里要说明材料是英文")
        self.assertTrue(any("英文" in note for note in surface["notes"]), surface["notes"])
        self.assertLessEqual(len(markdown), 400)

    def test_english_question_over_english_material_has_no_hint(self) -> None:
        notebook = self.notebook_with("english.md", ENGLISH_MATERIAL)
        surface, markdown = self.answer_of(notebook, "What does the tokenizer merge?")
        self.assertNotIn("注意：材料正文主要是", markdown)
        self.assertTrue(surface["citations"], "同语言提问应当能命中")

    def test_chinese_question_over_chinese_material_has_no_hint(self) -> None:
        notebook = self.notebook_with("chinese.md", CHINESE_MATERIAL)
        _surface, markdown = self.answer_of(notebook, "字节分词和 BPE 的关系是什么？")
        self.assertNotIn("注意：材料正文主要是", markdown)

    def test_english_question_over_chinese_material_explains_why(self) -> None:
        notebook = self.notebook_with("chinese.md", CHINESE_MATERIAL)
        surface, markdown = self.answer_of(notebook, "How does byte pair encoding work?")
        self.assertIn("中文", markdown)
        self.assertTrue(any("中文" in note for note in surface["notes"]), surface["notes"])

    def test_hint_does_not_leak_into_matching_questions(self) -> None:
        """提示只在"没命中"时出现；有命中时不要往答复里塞这段。"""
        notebook = self.notebook_with("english.md", ENGLISH_MATERIAL)
        for question in ("What is byte-pair encoding?", "How is the tokenizer trained?"):
            _surface, markdown = self.answer_of(notebook, question)
            self.assertNotIn("离线检索按字面匹配", markdown, question)


if __name__ == "__main__":
    unittest.main()
