"""核心层单元测试：文本工具、检索、接地、组件规划、契约校验。

这些测试不碰 ingest（摄取层由 tests/test_ingest.py 单独覆盖），全部用手工
构造的片段，这样任何失败都能立刻定位到具体模块，而不是"某处解析错了"。
"""

from __future__ import annotations

import unittest

from mentor import surface as surface_mod
from mentor import textutil as tu
from mentor.ground import Grounding
from mentor.index import Index
from mentor.protocol import (Chunk, Citation, Hit, Source, Span, Widget,
                             surface_problems, widget_problems)
from mentor.textutil import keyphrases, split_sentences, tokens_for_index


def make_chunk(chunk_id: str, text: str, *, source_id: str = "src_a", line: int = 1,
               heading: str = "", kind: str = "prose", order: int = 0) -> Chunk:
    return Chunk(id=chunk_id, source_id=source_id, text=text,
                 span=Span(start=0, end=len(text), line=line, section=heading,
                           source_id=source_id),
                 heading=heading, kind=kind, order=order)


def make_hit(chunk: Chunk, score: float = 1.0, matched: list[str] | None = None,
             why: str = "测试") -> Hit:
    return Hit(chunk=chunk, score=score, why=why, matched=matched or [])


class TextUtilTest(unittest.TestCase):
    def test_normalize_collapses_blank_lines_and_keeps_text(self) -> None:
        raw = "a\r\n\r\n\r\n\r\nb\u200b\n"
        self.assertEqual(tu.normalize_text(raw), "a\n\nb")

    def test_split_sentences_offsets_match_source(self) -> None:
        text = "First sentence. Second one!\n\nThird line here."
        sentences = split_sentences(text)
        self.assertEqual([s.text for s in sentences],
                         ["First sentence.", "Second one!", "Third line here."])
        for sent in sentences:
            self.assertEqual(text[sent.start:sent.end], sent.text)
        self.assertEqual([s.line for s in sentences], [1, 1, 3])

    def test_split_sentences_handles_chinese_punctuation(self) -> None:
        text = "第一句。第二句；第三句！"
        sentences = split_sentences(text)
        self.assertEqual([s.text for s in sentences], ["第一句。", "第二句；", "第三句！"])
        self.assertTrue(all(s.line == 1 for s in sentences))

    def test_cjk_bigrams_are_generated(self) -> None:
        tokens = tokens_for_index("分词器 BPE")
        self.assertIn("分词", tokens)
        self.assertIn("词器", tokens)
        self.assertIn("bpe", tokens)

    def test_keyphrases_prefers_marked_terms(self) -> None:
        text = "**Byte-Pair Encoding** is a tokenizer. BPE trains on raw text. BPE is common."
        terms = keyphrases(text)
        self.assertTrue(any("byte-pair encoding" == t.lower() for t in terms), terms)
        self.assertIn("bpe", [t.lower() for t in terms])

    def test_snippet_marks_hit(self) -> None:
        out = tu.snippet("the quick brown fox jumps over the lazy dog", ["brown"])
        self.assertIn("**brown**", out)


class IndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.chunks = [
            make_chunk("c1", "BPE trains a tokenizer by merging the most common pair of tokens.",
                       line=10, heading="Byte Pair Encoding"),
            make_chunk("c2", "Byte tokenization maps a string to bytes between 0 and 255.",
                       line=20, heading="Byte tokenizer"),
            make_chunk("c3", "BPE was adapted to NLP for neural machine translation.",
                       line=30, heading="Byte Pair Encoding"),
            make_chunk("c4", "Scaling laws predict loss at larger compute budgets.",
                       line=40, heading="Scaling", source_id="src_b"),
            make_chunk("c5", "BPE merges the most common pair of adjacent tokens repeatedly, "
                             "and BPE is data driven.", line=50, source_id="src_b"),
        ]
        self.index = Index(self.chunks, {"src_a": Source(id="src_a", path="a.py", title="A",
                                                         kind="lecture-note", text=""),
                                         "src_b": Source(id="src_b", path="b.md", title="B",
                                                         kind="markdown", text="")})

    def test_ranks_relevant_chunk_first(self) -> None:
        hits = self.index.search("how does BPE train a tokenizer")
        self.assertTrue(hits)
        self.assertEqual(hits[0].chunk.id, "c1")
        self.assertIn("bpe", [t.lower() for t in hits[0].matched])

    def test_heading_match_is_reported(self) -> None:
        hits = self.index.search("byte pair encoding")
        self.assertTrue(any("标题命中" in h.why for h in hits), [h.why for h in hits])

    def test_empty_query_returns_nothing(self) -> None:
        self.assertEqual(self.index.search(""), [])
        self.assertEqual(self.index.search("the and of"), [])

    def test_query_without_matches_returns_nothing(self) -> None:
        self.assertEqual(self.index.search("zzzz-nonexistent-qqq"), [])

    def test_phrase_bonus_rewards_exact_phrase(self) -> None:
        """短语命中会被加分：命中"common pair"的片段要出现在最前面，并说明原因。"""
        hits = self.index.search("most common pair of tokens")
        self.assertTrue(hits)
        self.assertIn("common pair", hits[0].chunk.text.lower())
        self.assertTrue(any("整句命中" in h.why for h in hits), [h.why for h in hits])

    def test_per_source_cap_spreads_results(self) -> None:
        hits = self.index.search("bpe tokens", limit=6)
        self.assertLessEqual(sum(1 for h in hits if h.chunk.source_id == "src_b"), 3)
        self.assertEqual(len({h.chunk.id for h in hits}), len(hits))

    def test_chinese_query_matches_chinese_text(self) -> None:
        chunks = [make_chunk("z1", "分词器把字符串映射成整数序列。"), make_chunk("z2", "训练循环")]
        index = Index(chunks)
        hits = index.search("分词器")
        self.assertTrue(hits)
        self.assertEqual(hits[0].chunk.id, "z1")

    def test_noise_bigrams_are_filtered_from_queries(self) -> None:
        tokens = tu.content_tokens("分词器是怎么训练的")
        self.assertIn("分词", tokens)
        self.assertNotIn("器是", tokens)
        self.assertNotIn("是怎", tokens)

    def test_mixed_language_query_prefers_latin_term_match(self) -> None:
        chunks = [
            make_chunk("m1", "| 1 基础 | 分词器 → Transformer → 优化器 → 训练 | 全程训练"),
            make_chunk("m2", "BPE trains a tokenizer by merging the most common pair of tokens.",
                       heading="Byte Pair Encoding"),
        ]
        index = Index(chunks)
        hits = index.search("BPE 分词器是怎么训练的？")
        self.assertEqual(hits[0].chunk.id, "m2")
        self.assertIn("bpe", [t.lower() for t in hits[0].matched])

    def test_stats_and_keyphrases(self) -> None:
        stats = self.index.stats()
        self.assertEqual(stats["chunks"], 5)
        self.assertEqual(stats["sources"], 2)
        self.assertTrue(self.index.keyphrases(5))


class GroundingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.chunks = [
            make_chunk("c1", "BPE merges the most common pair of adjacent tokens.", line=100),
            make_chunk("c2", "The vocabulary of BPE is learned from data.", line=200),
        ]
        self.sources = {"src_a": Source(id="src_a", path="lectures/lecture_01.py",
                                        title="Tokenization", kind="lecture-note", text="")}
        hits = [make_hit(self.chunks[0], matched=["bpe"]), make_hit(self.chunks[1])]
        self.grounding = Grounding(hits, self.sources)

    def test_citations_are_numbered_and_located(self) -> None:
        numbers = [c.n for c in self.grounding.citations]
        self.assertEqual(numbers, [1, 2])
        self.assertEqual(self.grounding.citations[0].locator, "lecture_01.py:100")
        self.assertEqual(self.grounding.citations[0].title, "Tokenization")

    def test_valid_numbers(self) -> None:
        self.assertEqual(self.grounding.valid_numbers(), {1, 2})

    def test_enforce_keeps_valid_and_drops_invalid(self) -> None:
        cleaned, notes = self.grounding.enforce("BPE 合并最高频相邻对 [1]，另有说法 [9]。")
        self.assertIn("[1]", cleaned)
        self.assertNotIn("[9]", cleaned)
        self.assertTrue(any("不存在" in n for n in notes), notes)

    def test_enforce_adds_citation_when_missing(self) -> None:
        cleaned, notes = self.grounding.enforce("BPE 是数据驱动的。")
        self.assertIn("[1]", cleaned)
        self.assertTrue(any("补上" in n for n in notes), notes)

    def test_enforce_does_not_touch_markdown_links(self) -> None:
        cleaned, _ = self.grounding.enforce("见 [讲义](https://example.com) 的第 3 节 [2]。")
        self.assertIn("[讲义](https://example.com)", cleaned)
        self.assertIn("[2]", cleaned)

    def test_unsupported_sentence_is_flagged(self) -> None:
        text = "BPE merges the most common pair of adjacent tokens [1]. 量子纠缠决定宇宙常数。"
        flagged = self.grounding.unsupported_sentences(text)
        self.assertTrue(any("量子" in s for s in flagged), flagged)

    def test_coverage_reports_missing_terms(self) -> None:
        hits = [make_hit(self.chunks[0], matched=["bpe"])]
        grounding = Grounding(hits, self.sources)
        self.assertLess(grounding.coverage("BPE 与强化学习的关系", hits), 1.0)

    def test_context_exposes_source_and_line(self) -> None:
        context = self.grounding.as_context()
        self.assertEqual(context[0]["source_id"], "src_a")
        self.assertEqual(context[0]["line"], 100)


class SurfaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.chunk = make_chunk("c1", "BPE merges the most common pair of adjacent tokens.", line=5)
        self.grounding = Grounding([make_hit(self.chunk)], {})

    def test_detect_intent_from_question_and_mode(self) -> None:
        self.assertEqual(surface_mod.detect_intent("对比 BPE 和字节分词"), "compare")
        self.assertEqual(surface_mod.detect_intent("给我出几道题"), "study")
        self.assertEqual(surface_mod.detect_intent("随便问问", "brief"), "brief")

    def test_plan_widgets_is_subset_of_catalog_and_ordered(self) -> None:
        planned = surface_mod.plan_widgets("explore", "BPE 是什么？", [make_hit(self.chunk)])
        self.assertEqual(planned[0], "answer")
        for name in planned:
            self.assertIn(name, surface_mod.WIDGET_TYPES)

    def test_plan_drops_timeline_without_years(self) -> None:
        planned = surface_mod.plan_widgets("brief", "梳理一下", [make_hit(self.chunk)])
        self.assertNotIn("timeline", planned)

    def test_normalize_rejects_unknown_type(self) -> None:
        widgets, notes = surface_mod.normalize([{"type": "hologram", "data": {}}], self.grounding)
        self.assertEqual(widgets, [])
        self.assertTrue(notes)

    def test_normalize_trims_long_lists_and_fixes_quiz(self) -> None:
        raw = {"type": "quiz", "data": {"questions": [
            {"q": f"q{i}", "choices": ["a", "b"], "answer_index": 99} for i in range(9)]}}
        widgets, _ = surface_mod.normalize([raw], self.grounding)
        questions = widgets[0].data["questions"]
        self.assertEqual(len(questions), surface_mod.MAX_QUESTIONS)
        self.assertEqual(questions[0]["answer_index"], 1)

    def test_normalize_strips_out_of_range_citations(self) -> None:
        raw = {"type": "answer", "cites": [1, 7], "data": {"markdown": "文本 [1]"}}
        widgets, notes = surface_mod.normalize([raw], self.grounding)
        self.assertEqual(widgets[0].cites, [1])
        self.assertTrue(any("越界" in n for n in notes))

    def test_ensure_minimum_adds_answer_and_sources(self) -> None:
        widgets = surface_mod.ensure_minimum([], ["answer"], self.grounding)
        types = [w.type for w in widgets]
        self.assertIn("answer", types)
        self.assertIn("sources", types)

    def test_build_surface_and_problems(self) -> None:
        widgets, _ = surface_mod.normalize([{"type": "answer", "data": {"markdown": "abc [1]"}}],
                                           self.grounding)
        surface = surface_mod.build_surface("q", "ask", "focus", widgets, self.grounding)
        self.assertEqual(surface_problems(surface), [])
        self.assertEqual(surface.citations[0].n, 1)


class ProtocolTest(unittest.TestCase):
    def test_widget_problems_detects_missing_field(self) -> None:
        problems = widget_problems(Widget(type="answer", data={}))
        self.assertTrue(problems)

    def test_widget_problems_accepts_valid_widget(self) -> None:
        self.assertEqual(widget_problems(Widget(type="answer", data={"markdown": "hi"})), [])

    def test_heatmap_is_part_of_the_catalog(self) -> None:
        widget = Widget(type="heatmap", data={"rows": ["a"], "columns": ["b"], "cells": [[0.5]]})
        self.assertEqual(widget_problems(widget), [])

    def test_surface_problems_flags_dangling_citation(self) -> None:
        widget = Widget(type="answer", data={"markdown": "x"}, cites=[3])
        surface = surface_mod.Surface(id="s", question="q", layout="focus", widgets=[widget])
        self.assertTrue(any("不存在" in p for p in surface_problems(surface)))

    def test_surface_problems_flags_unknown_layout(self) -> None:
        widget = Widget(type="answer", data={"markdown": "x"})
        surface = surface_mod.Surface(id="s", question="q", layout="nope", widgets=[widget])
        self.assertTrue(any("版面" in p for p in surface_problems(surface)))

    def test_citation_roundtrip(self) -> None:
        cite = Citation(n=1, chunk_id="c", source_id="s", locator="a.py:1")
        self.assertEqual(cite.to_dict()["n"], 1)

    def test_source_meta_is_light_until_asked(self) -> None:
        raw = "x" * 5000
        source = Source(id="s1", path="a/lecture_01.py", title="T", kind="lecture-note",
                        text="正文", meta={"raw_text": raw, "line_map": [[0, 1]] * 40,
                                           "units": 12})
        light = source.to_dict()
        self.assertNotIn(raw, light["meta"]["raw_text"])
        self.assertEqual(light["meta"]["units"], 12)
        self.assertNotIn("text", light)
        full = source.to_dict(with_text=True)
        self.assertEqual(full["raw"], raw)
        self.assertEqual(full["text"], "正文")
        self.assertEqual(source.raw_text(), raw)

    def test_source_raw_falls_back_to_text(self) -> None:
        source = Source(id="s2", path="b.md", title="B", kind="markdown", text="只有正文")
        self.assertEqual(source.raw_text(), "只有正文")
        self.assertNotIn("raw", source.to_dict(with_text=True))


if __name__ == "__main__":
    unittest.main()
