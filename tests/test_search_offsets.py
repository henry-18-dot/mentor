"""命中位置：检索结果要能说清"命中了哪几个字、在片段的哪个位置"。

只标"这条片段命中了"信息量不够——界面需要行内高亮，用户需要一眼看到命中词。
"""

from __future__ import annotations

import unittest

from mentor import textutil as tu
from mentor.index import Index
from mentor.protocol import Chunk, Span


def make_chunk(cid: str, text: str) -> Chunk:
    return Chunk(id=cid, source_id="src_a", text=text,
                 span=Span(start=0, end=len(text), line=1, section="", source_id="src_a"))


class FindTermSpansTest(unittest.TestCase):
    def test_latin_is_case_insensitive(self) -> None:
        text = "BPE merges tokens. bpe again."
        spans = tu.find_term_spans(text, "bpe")
        self.assertEqual([text[a:b] for a, b in spans], ["BPE", "bpe"])

    def test_latin_respects_word_boundaries(self) -> None:
        self.assertEqual(tu.find_term_spans("the engine runs", "en"), [])
        self.assertEqual(tu.find_term_spans("en and engine", "en"), [(0, 2)])

    def test_limit_is_respected(self) -> None:
        text = "token token token token"
        self.assertEqual(len(tu.find_term_spans(text, "token", limit=2)), 2)

    def test_chinese_offsets_are_exact(self) -> None:
        text = "字节分词把字符串映射成字节序列"
        spans = tu.find_term_spans(text, "字节")
        self.assertEqual(len(spans), 2)
        for start, end in spans:
            self.assertEqual(text[start:end], "字节")

    def test_empty_inputs(self) -> None:
        self.assertEqual(tu.find_term_spans("", "x"), [])
        self.assertEqual(tu.find_term_spans("abc", ""), [])

    def test_merge_spans_joins_overlaps(self) -> None:
        self.assertEqual(tu.merge_spans([(0, 3), (2, 6), (9, 11)]), [(0, 6), (9, 11)])
        self.assertEqual(tu.merge_spans([]), [])


class HitMatchesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.chunks = [
            make_chunk("c1", "BPE merges the most common pair of adjacent tokens. BPE is data driven."),
            make_chunk("c2", "字节分词把字符串映射成 256 个字节取值。"),
        ]
        self.index = Index(self.chunks)

    def test_hits_carry_offsets_that_point_at_the_term(self) -> None:
        hits = self.index.search("BPE tokenizer")
        self.assertTrue(hits)
        hit = hits[0]
        self.assertTrue(hit.matches, "命中结果必须带偏移")
        text = hit.chunk.text
        for span in hit.matches:
            self.assertEqual(text[span["start"]:span["end"]].lower(), span["term"].lower())
            self.assertIn(span["term"], hit.matched)

    def test_offsets_are_inside_the_chunk(self) -> None:
        for hit in self.index.search("字节分词"):
            for span in hit.matches:
                self.assertGreaterEqual(span["start"], 0)
                self.assertLessEqual(span["end"], len(hit.chunk.text))
                self.assertLess(span["start"], span["end"])

    def test_offsets_survive_serialization(self) -> None:
        hits = self.index.search("BPE")
        payload = hits[0].to_dict()
        self.assertIn("matches", payload)
        self.assertEqual(payload["matches"][0]["term"], "bpe")

    def test_at_most_two_occurrences_per_term(self) -> None:
        chunk = make_chunk("c3", "token token token token token")
        index = Index([chunk])
        hits = index.search("token")
        self.assertTrue(hits)
        occurrences = [s for s in hits[0].matches if s["term"] == "token"]
        self.assertLessEqual(len(occurrences), 2)


if __name__ == "__main__":
    unittest.main()
