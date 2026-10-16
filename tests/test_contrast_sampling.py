"""对比度审计的采样口径守卫：必须"取所有匹配里最差的那个"。

由来（2026-10-17，与协作会话互相复现不出对方数字才定位到）：
`accept_ui.py` 的对比度审计原本用 `document.querySelector(sel)` 取**首个**匹配元素，
于是 `右栏行号` 量到的是命中行还是普通行，取决于当时右栏第一个 `.line__n` 属于哪种状态 ——
同一份代码两次运行得到 **7.29:1** 与 **8.86:1**，双方各自都能复现自己那个、复现不出对方那个，
而**两个数都是"正确"的测量**，只是测了不同元素。审计的断言是"最低 X:1"，
就必须是**所有匹配元素的下界**，否则这个数跨次不可比、也无法作为验收依据。

这条测试是源码级契约守卫：把 `querySelectorAll` + `nodes`/`state` 两个事实钉住，
一旦有人退回"取首个匹配"的写法，这里立刻红（浏览器侧的真实行为由 `accept_ui.py` 覆盖）。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = (PROJECT / "scripts" / "accept_ui.py").read_text(encoding="utf-8")


class ContrastSamplingTest(unittest.TestCase):
    def test_audit_samples_every_match_not_just_the_first(self) -> None:
        js = re.search(r'CONTRAST_JS = """(.*?)"""', SOURCE, re.S)
        self.assertIsNotNone(js, "找不到 CONTRAST_JS")
        body = js.group(1)
        self.assertIn("querySelectorAll", body, "对比度审计必须遍历所有匹配元素")
        self.assertNotRegex(body, r"document\.querySelector\(sel\)",
                            "不要退回'取首个匹配'：那个值会随 DOM 状态漂移")
        self.assertIn("worst", body, "必须保留'取最差'的判定")

    def test_pairs_report_how_many_matches_and_which_state(self) -> None:
        js = re.search(r'CONTRAST_JS = """(.*?)"""', SOURCE, re.S).group(1)
        for field in ("nodes", "state"):
            with self.subTest(field=field):
                self.assertIn(f"{field}:", js, f"对比度结果要带 {field}，否则数字无法复现")

    def test_log_line_prints_the_state_signature(self) -> None:
        audit = re.search(r"def contrast_audit\(.*?\n(.*?)\n\n", SOURCE, re.S)
        self.assertIsNotNone(audit)
        self.assertIn("取最差", audit.group(1), "日志里要说明这个数取自哪个状态")

    def test_docs_quote_the_reproducible_minimum(self) -> None:
        """handover 里写的必须是可以复现的那个下界（浅色 5.22 / 深色 6.5），而不是旧口径的 8.11。"""
        handover = (PROJECT / "docs" / "handover.md").read_text(encoding="utf-8")
        self.assertIn("5.22", handover, "handover 没写实测的最低值")
        self.assertNotIn("最低 8.11", handover, "旧的'最低 8.11'是取首个匹配得出的高估值，不该还在")

    # --- 同一类缺陷的第三处：点击目标曾经"用一个样本支撑全称断言" -----------------

    def test_target_audit_enumerates_every_control(self) -> None:
        js = re.search(r"const targets = \[\];(.*?)const inlineTight", SOURCE, re.S)
        self.assertIsNotNone(js, "找不到点击目标审计的取样段")
        body = js.group(1)
        for sel in ("button", "a[href]", "input", "select", "textarea"):
            with self.subTest(selector=sel):
                self.assertIn(sel, body, f"点击目标审计没覆盖 {sel}")
        self.assertIn("controls.reduce", SOURCE, "必须取所有控件里的最小值，不能抽样一个")

    def test_target_audit_separates_inline_links(self) -> None:
        """WCAG 2.5.8 对正文内联链接有例外，必须单独统计而不是混进下界。"""
        js = re.search(r"const targets = \[\];(.*?)const inlineTight", SOURCE, re.S).group(1)
        self.assertIn("inline", js)
        self.assertIn(".md p, .md li", js)

    def test_api_doc_no_longer_overclaims_target_size(self) -> None:
        api = (PROJECT / "docs" / "api.md").read_text(encoding="utf-8")
        self.assertNotIn("点击目标 ≥ 32px", api,
                         "全称的 ≥32px 已被证伪（实测最小 24×24，20+ 个控件 <32px），不许再出现")
        self.assertIn("24", api, "api.md 要写实测的下界")


if __name__ == "__main__":
    unittest.main()
