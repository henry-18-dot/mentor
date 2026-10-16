"""独立审查（supervisor，deepseek-v4-pro/max）发现问题的回归测试。

每条测试对应报告里的一个编号，命名保留编号，方便对照复现：
M1 代码块被引用清洗破坏 / M2 数字文本链接误伤 / M3 跨笔记本块替换与缓存 /
M4 Grounding.cite 键类型 / S1 单字中文查询 / S3 子串误判 / S5 顿号引用 /
S6 空正文补引用 / S7 同秒同名笔记本 / S8 正文引用越界未被校验。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mentor import ground as ground_mod
from mentor.ground import Grounding, contains_term
from mentor.index import Index
from mentor.protocol import Chunk, Hit, Source, Span, Widget, surface_problems
from mentor.store import Store


def chunk(cid: str, text: str, *, source_id: str = "src_a", line: int = 1) -> Chunk:
    return Chunk(id=cid, source_id=source_id, text=text,
                 span=Span(start=0, end=len(text), line=line, section="", source_id=source_id))


def grounding_with(*chunks: Chunk) -> Grounding:
    hits = [Hit(chunk=c, score=1.0, why="测试", matched=[]) for c in chunks]
    return Grounding(hits, {"src_a": Source(id="src_a", path="a.py", title="A",
                                            kind="lecture-note", text="")})


class CodeSafetyTest(unittest.TestCase):
    """M1 / M2：引用清洗不得动代码与链接。"""

    def setUp(self) -> None:
        self.g = grounding_with(chunk("c1", "BPE merges the most common pair of adjacent tokens."))

    def test_fenced_code_is_untouched(self) -> None:
        code = 'x = [1, 2, 3]\ny = [1, 99]\nz = x[0]\n\ndef f():\n        return z'
        markdown = f"要点如下 [9]：\n\n```python\n{code}\n```\n\n材料里说 BPE 合并最高频相邻对 [1]。"
        cleaned, _ = self.g.enforce(markdown)
        self.assertIn(code, cleaned, "代码块内容被改写了")
        self.assertNotIn("[9]", cleaned.split("```")[0], "正文里的越界引用应当被清掉")

    def test_indentation_is_preserved(self) -> None:
        markdown = "```\nif x:\n        deep = 1\n```"
        cleaned, _ = self.g.enforce(markdown)
        self.assertIn("        deep = 1", cleaned)

    def test_inline_code_is_untouched(self) -> None:
        cleaned, _ = self.g.enforce("数组下标写作 `x[0]`，而引用写作 [1]。")
        self.assertIn("`x[0]`", cleaned)
        self.assertIn("[1]", cleaned)

    def test_numeric_markdown_link_is_not_mangled(self) -> None:
        cleaned, notes = self.g.enforce("参考 [99](https://example.com) 这份外部资料。")
        self.assertIn("[99](https://example.com)", cleaned)
        self.assertNotIn("(https://example.com)", cleaned.replace("[99](https://example.com)", ""))
        # 链接不是引用，所以正文此时确实还没有引用 → 补一条并说明，这是预期行为
        self.assertTrue(any("补上" in note for note in notes), notes)

    def test_empty_answer_is_not_given_a_citation(self) -> None:
        cleaned, notes = self.g.enforce("")
        self.assertEqual(cleaned, "")
        self.assertEqual(notes, [])
        cleaned, _ = self.g.enforce("   \n  ")
        self.assertEqual(cleaned, "")

    def test_ideographic_comma_citations_are_normalised(self) -> None:
        second = chunk("c2", "Byte tokenization maps strings to bytes.")
        g = grounding_with(chunk("c1", "BPE merges adjacent tokens."), second)
        cleaned, _ = g.enforce("两条来源都支持这个说法 [1、2]。越界的 [1、7] 应当被清掉。")
        self.assertIn("[1,2]", cleaned)
        self.assertNotIn("[7]", cleaned)

    def test_cite_lookup_by_number(self) -> None:
        """M4：cite(n) 曾用整数查以 chunk_id 为键的字典，恒为 None。"""
        g = grounding_with(chunk("ck_alpha", "BPE merges adjacent tokens."))
        self.assertIsNotNone(g.cite(1))
        self.assertEqual(g.cite(1).chunk_id, "ck_alpha")
        self.assertIsNone(g.cite(99))


class SentenceSplitTest(unittest.TestCase):
    """S4：缩写与列表编号不该被切成独立句子。"""

    def test_abbreviation_is_not_split(self) -> None:
        from mentor.textutil import split_sentences
        sentences = [s.text for s in split_sentences("Born in the U.S.A. He later moved.")]
        self.assertEqual(sentences, ["Born in the U.S.A.", "He later moved."])

    def test_list_marker_is_merged_back(self) -> None:
        from mentor.textutil import split_sentences
        text = "1. Internal: guide model development. 2. External: measure real quality."
        sentences = [s.text.strip() for s in split_sentences(text)]
        self.assertTrue(sentences[0].startswith("1. Internal"), sentences)
        self.assertEqual(len(sentences), 2, sentences)

    def test_offsets_still_match_after_merging(self) -> None:
        from mentor.textutil import split_sentences
        text = "1. Internal: guide development. 2. External: measure quality."
        for sent in split_sentences(text):
            self.assertEqual(text[sent.start:sent.end], sent.text)


class SubstringSafetyTest(unittest.TestCase):
    """S3：拉丁词的子串误判。"""

    def test_word_boundary_containment(self) -> None:
        self.assertFalse(contains_term("engine", "en"))
        self.assertFalse(contains_term("token", "en"))
        self.assertTrue(contains_term("encoding", "encoding"))
        self.assertTrue(contains_term("the encoder here", "encoder"))

    def test_chinese_still_matches_by_substring(self) -> None:
        self.assertTrue(contains_term("字节分词训练", "分词"))
        self.assertFalse(contains_term("字节切分", "分词"))

    def test_near_ignores_substring_hits(self) -> None:
        from mentor.index import _near
        self.assertTrue(_near("the engine code", ["engine"], window=10))
        # "en" 若按子串判断会命中 "engine" 内部，这里是词边界判断 → 找不到位置
        self.assertFalse(_near("engine only", ["en"], window=10))


class ChineseQueryTest(unittest.TestCase):
    """S1 / S2：单字查询与中文短语命中。"""

    def setUp(self) -> None:
        self.chunks = [
            chunk("z1", "分词器把字符串映射成整数序列，训练时要合并最高频相邻对。"),
            chunk("z2", "注意力把任意两个位置直接连起来。"),
        ]
        self.index = Index(self.chunks)

    def test_single_character_query_still_finds_something(self) -> None:
        hits = self.index.search("分")
        self.assertTrue(hits, "单字中文查询不该零结果")
        self.assertEqual(hits[0].chunk.id, "z1")

    def test_chinese_phrase_gets_phrase_bonus(self) -> None:
        hits = self.index.search("分词器训练")
        self.assertTrue(hits)
        self.assertTrue(any("整句命中" in h.why for h in hits),
                        f"中文短语没有拿到命中加成：{[h.why for h in hits]}")


class StoreIsolationTest(unittest.TestCase):
    """M3 / S7：跨笔记本共享材料、覆盖后的历史引用、同秒同名。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "m.db")

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def _source_and_chunks(self, text: str) -> tuple[Source, list[Chunk]]:
        source = Source(id="src_shared", path="shared/notes.md", title="共享材料",
                        kind="markdown", text=text)
        chunks = [chunk("ck_v1", text, source_id="src_shared", line=1)]
        return source, chunks

    def test_shared_source_reingest_keeps_history_resolvable(self) -> None:
        nb1 = self.store.create_notebook("第一本")
        nb2 = self.store.create_notebook("第二本")
        source, chunks = self._source_and_chunks("第一版内容，讲 BPE 合并。")
        self.store.attach(nb1.id, [source], chunks)
        self.store.attach(nb2.id, [source], chunks)
        self.assertIsNotNone(self.store.get_chunk("ck_v1"))

        # 第二本先建索引，然后第一本重摄取改了内容
        before = self.store.index(nb2.id)
        new_source = Source(id="src_shared", path="shared/notes.md", title="共享材料",
                           kind="markdown", text="第二版内容，讲字节分词。")
        new_chunk = chunk("ck_v2", "第二版内容，讲字节分词。", source_id="src_shared", line=1)
        self.store.attach(nb1.id, [new_source], [new_chunk])

        # 1) 旧片段进了档案，历史引用仍能查到原文
        archived = self.store.get_chunk("ck_v1")
        self.assertIsNotNone(archived, "旧片段应当被归档而不是被删掉")
        self.assertIn("BPE", archived.text)
        # 2) 另一本笔记本的索引缓存必须失效，重建后看到的是新内容
        after = self.store.index(nb2.id)
        self.assertIsNot(after, before, "共享材料的笔记本索引缓存没有失效")
        self.assertTrue(after.search("字节分词"), "重建后的索引应当包含新内容")

    def test_same_title_same_second_can_be_created_twice(self) -> None:
        first = self.store.create_notebook("同名")
        second = self.store.create_notebook("同名")
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(len(self.store.list_notebooks()), 2)

    def test_archive_count_reports_replaced_chunks(self) -> None:
        nb = self.store.create_notebook("归档")
        source, chunks = self._source_and_chunks("第一版。")
        self.store.attach(nb.id, [source], chunks)
        self.assertEqual(self.store.archived_chunk_count(), 0)
        source2, chunks2 = self._source_and_chunks("第二版。")
        chunks2[0].id = "ck_v2"
        self.store.attach(nb.id, [source2], chunks2)
        self.assertEqual(self.store.archived_chunk_count("src_shared"), 1)


class SurfaceCitationGuardTest(unittest.TestCase):
    """S8：正文里的越界引用必须被契约校验挡住，而不是靠 enforce 自觉。"""

    def test_out_of_range_citation_in_markdown_is_flagged(self) -> None:
        from mentor.protocol import Citation, Surface
        widget = Widget(type="answer", data={"markdown": "结论是 A [1]，另一处 [7]。"}, cites=[1])
        surface = Surface(id="s", question="q", layout="focus", widgets=[widget],
                          citations=[Citation(n=1, chunk_id="c1", source_id="s1",
                                              locator="a.py:1")])
        problems = surface_problems(surface)
        self.assertTrue(any("[7]" in p for p in problems), problems)

    def test_numeric_link_is_not_reported_as_citation(self) -> None:
        from mentor.protocol import Citation, Surface
        widget = Widget(type="answer", data={"markdown": "见 [9](https://x.test) 与 [1]。"})
        surface = Surface(id="s", question="q", layout="focus", widgets=[widget],
                          citations=[Citation(n=1, chunk_id="c1", source_id="s1",
                                              locator="a.py:1")])
        self.assertEqual(surface_problems(surface), [])


class SecondRoundTest(unittest.TestCase):
    """二轮复核新发现的问题（编号沿用报告）：①②③④⑤⑥。"""

    def setUp(self) -> None:
        self.g = grounding_with(chunk("c1", "BPE merges the most common pair of adjacent tokens."))

    def test_code_in_answer_is_not_reported_as_citation(self) -> None:
        """① surface_problems 的引用校验必须也屏蔽代码区，否则含代码的回答被误标降级。"""
        from mentor.protocol import Citation, Surface
        widget = Widget(type="answer", data={"markdown": "示例：\n\n```python\ny = a[0]\n```\n\n正文 [1]。"})
        surface = Surface(id="s", question="q", layout="focus", widgets=[widget],
                          citations=[Citation(n=1, chunk_id="c1", source_id="s1",
                                              locator="a.py:1")])
        self.assertEqual(surface_problems(surface), [])

    def test_unclosed_fence_is_protected(self) -> None:
        """② 只有开口没有闭合的围栏（模型被截断）也必须整段保护。"""
        markdown = "```python\nx = [1, 2, 3]\ny = a[0]\nprint(x[99])\n"
        cleaned, _ = self.g.enforce(markdown)
        self.assertIn("x = [1, 2, 3]", cleaned)
        self.assertIn("y = a[0]", cleaned)
        self.assertIn("print(x[99])", cleaned)

    def test_sentences_inside_code_are_not_flagged(self) -> None:
        """③ 代码区不该被算作"未支撑断言"。"""
        markdown = ("材料里说 BPE 合并最高频相邻对 [1]。\n\n"
                    "```python\n# 这一段是代码，不该被逐句判定\nx = a[0]\nreturn x\n```\n")
        flagged = self.g.unsupported_sentences(markdown)
        self.assertEqual(flagged, [], flagged)

    def test_placeholder_collision_is_harmless(self) -> None:
        """④ 正文里出现形似占位符的字面量，也不能被替换成代码块内容。"""
        text = ("正文提到占位符字样 \x00mentor-code-0\x00 结束 [1]。\n\n"
                "```\nreal code\n```\n")
        cleaned, _ = self.g.enforce(text)
        self.assertIn("real code", cleaned)
        self.assertIn("占位符字样", cleaned)
        self.assertNotIn("\x00mentor-code-0\x00 real code", cleaned)

    def test_spaced_citation_is_recognised(self) -> None:
        """⑥ `[ 1 ]` 也要认，否则会重复补一条引用。"""
        cleaned, notes = self.g.enforce("结论 [ 1 ] 正确。")
        self.assertIn("[1]", cleaned)
        self.assertNotIn("[ 1 ]", cleaned)
        self.assertEqual(notes, [])

    def test_three_digit_list_marker_merges(self) -> None:
        from mentor.textutil import split_sentences
        text = "100. 第一百项说明开始。 101. 下一项说明继续。"
        sentences = [s.text.strip() for s in split_sentences(text)]
        self.assertTrue(sentences[0].startswith("100."), sentences)


class ChunkIdentityTest(unittest.TestCase):
    """⑤ chunk id 必须由全文决定：只哈希前缀会让历史引用指向新内容。"""

    def test_chunk_id_changes_when_tail_changes(self) -> None:
        from mentor.ingest import _make_chunk
        from mentor.protocol import Source

        source = Source(id="src_x", path="a.md", title="A", kind="markdown", text="")
        head = "前缀" * 60  # 超过 80 字符
        first = _make_chunk(source, head + "第一版结尾", 0, 10, "", "prose", 0)
        second = _make_chunk(source, head + "第二版结尾", 0, 10, "", "prose", 0)
        self.assertNotEqual(first.id, second.id,
                            "只改第 80 字符之后的内容也必须换 id")


if __name__ == "__main__":
    unittest.main()
