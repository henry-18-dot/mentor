"""搜索命中定位：`/api/sources/<id>?highlight=<term>` 要告诉我们首个命中在第几行。

为什么需要它：搜到东西之后点左栏的来源卡，右栏应该直接跳到命中的那一行，
而不是让用户自己在几百行里找。行号必须与界面显示的行号一致（原始文件行号）。
"""

from __future__ import annotations

import unittest

from mentor.protocol import Source
from mentor.server import _locate_term

LECTURE = """\
import torch

def train_bpe(text):
    # 用 BPE 合并最常见的相邻 token 对
    merges = {}
    return merges

class Tokenizer:
    pass
"""

NOTES = """\
# 笔记

字节分词把字符串映射成 0~255 的字节序列，BPE 则合并相邻对。
"""


def source(sid: str, path: str, text: str) -> Source:
    return Source(id=sid, path=path, kind="note", title=path, text=text,
                  meta={"raw": text})


class LocateTermTest(unittest.TestCase):
    def setUp(self) -> None:
        self.lecture = source("s1", "lecture_01.py", LECTURE)
        self.notes = source("s2", "notes.md", NOTES)

    def test_finds_first_line_with_latin_term(self) -> None:
        found = _locate_term(self.lecture, "merges")
        self.assertTrue(found["found"])
        self.assertEqual(found["line"], 5)
        self.assertEqual(found["term"], "merges")

    def test_word_boundary_keeps_false_positives_out(self) -> None:
        # "BPE" 在第 4 行出现；"bpe" 作为单独词也应命中同一行
        self.assertEqual(_locate_term(self.lecture, "bpe")["line"], 4)
        # "rc" 只是 "torch" 的一部分，不该被当成命中
        self.assertFalse(_locate_term(self.lecture, "rc")["found"])

    def test_finds_chinese_term(self) -> None:
        self.assertEqual(_locate_term(self.notes, "字节分词")["line"], 3)

    def test_missing_term_reports_not_found(self) -> None:
        found = _locate_term(self.lecture, "这份材料里没有的词")
        self.assertFalse(found["found"])
        self.assertEqual(found["line"], 0, "找不到就老实说 0，不要随便跳一行")

    def test_blank_term_is_not_found(self) -> None:
        self.assertFalse(_locate_term(self.lecture, " ")["found"])

    def test_crlf_line_endings_do_not_shift_lines(self) -> None:
        windows = source("s3", "win.py", LECTURE.replace("\n", "\r\n"))
        self.assertEqual(_locate_term(windows, "Tokenizer")["line"], 8)


if __name__ == "__main__":
    unittest.main()
