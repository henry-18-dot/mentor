"""审美决策页的正确性：变体必须真的不同、CSS 不许越界、页面不许引外部资源。

**为什么这事值得单独测**：决策页是"用户据此拍板"的输入。如果几个变体在浏览器里渲染成同一个样子，
用户就是在对着无效信息做决定 —— 那不是观感问题，是正确性问题。

真踩过的两个坑：
1. `variant()` 拼 CSS 时少一个 `{`，后一条规则变成**全局选择器**，把 B2 的"去条纹"泄漏给了所有变体
   （看页面文字完全看不出来，只有量计算样式才发现）；
2. 决策页的窗口比容器宽时，grid 项的 `min-width: auto` 会把整个页面撑出横向滚动。

浏览器侧的行为由 `scripts/accept_ui.py --case decisions` 验；这里守的是**生成器出口**。
"""

from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
DECISIONS = PROJECT / "docs" / "decisions"
PAGES = ("index.html", "a-typography.html", "b-visual-language.html", "c-structure.html")


def load_builder():
    spec = importlib.util.spec_from_file_location("build_decisions", PROJECT / "scripts" / "build_decisions.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


class DecisionsGeneratorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.builder = load_builder()
        cls.html = {name: (DECISIONS / name).read_text(encoding="utf-8") for name in PAGES}

    def test_all_pages_exist_and_are_substantial(self) -> None:
        for name, text in self.html.items():
            with self.subTest(page=name):
                self.assertGreater(len(text), 2000, f"{name} 太短，内容可能没生成全")

    def test_generation_is_deterministic(self) -> None:
        """同一个生成器跑两次必须得到同样的字节，否则改动无法对比。"""
        again = {
            "index.html": self.builder.build_index(),
            "a-typography.html": self.builder.build_a(),
            "b-visual-language.html": self.builder.build_b(),
            "c-structure.html": self.builder.build_c(),
        }
        for name, text in again.items():
            with self.subTest(page=name):
                self.assertEqual(text, self.html[name], f"{name} 的生成结果不稳定")

    def test_each_decision_page_has_variants_and_a_tradeoff_table(self) -> None:
        for name in PAGES[1:]:
            text = self.html[name]
            with self.subTest(page=name):
                variants = re.findall(r'class="variant v-([A-Z]\d)"', text)
                self.assertGreaterEqual(len(set(variants)), 2, f"{name} 的候选方案少于 2 个")
                self.assertIn("取舍与代价", text, f"{name} 缺少取舍表")
                self.assertIn("我的建议", text, f"{name} 缺少建议")
                self.assertIn("怎么选", text, f"{name} 缺少「怎么选」的说明")

    def test_variant_css_is_scoped(self) -> None:
        """变体内的每条 CSS 规则都必须挂在 `.v-<code>` 下 —— 这条守的是上面第 1 个坑。

        只扫**变体内部**的 `<style>` 块；页面级样式（`.d-rail`、`@media` 那些）本来就该是全局的。
        """
        checked = 0
        for name in PAGES[1:]:
            text = self.html[name]
            for match in re.finditer(r'class="variant v-([A-Z]\d)">\s*<style>(.*?)</style>', text, re.S):
                code, block = match.group(1), match.group(2)
                checked += 1
                for selector_group in re.findall(r"([^{}]+)\{", block):
                    for selector in selector_group.split(","):
                        selector = selector.strip()
                        if not selector:
                            continue
                        with self.subTest(page=name, variant=code, selector=selector):
                            self.assertTrue(selector.startswith(f".v-{code}"),
                                            f"{name}/{code} 里有没加作用域的规则：{selector!r}（会泄漏到其它变体）")
        self.assertGreater(checked, 4, "没有扫到变体样式块，这条断言等于没测")

    def test_pages_use_mentor_tokens(self) -> None:
        """变体必须用真实令牌渲染，否则用户是在对示意图做决定。"""
        self.assertIn("var(--maxw-read)", self.html["a-typography.html"],
                      "A 页没有用 --maxw-read，列宽对比就不是真实渲染")
        for name in PAGES[1:]:
            text = self.html[name]
            with self.subTest(page=name):
                self.assertGreater(text.count("var(--"), 5, f"{name} 用的 mentor 令牌太少")
                self.assertIn("widget", text, f"{name} 没有用 mentor 的组件类")

    def test_no_external_resources(self) -> None:
        """决策页必须能离线打开：不许**加载**外部资源（出处引用链接不算，反而应该保留）。"""
        loader = re.compile(
            r'<(?:link|script|img|iframe|source)\b[^>]*?(?:href|src)="((?:https?:)?//[^"]+)"', re.I)
        import_rule = re.compile(r"@import\s+url\(\s*['\"]?https?://", re.I)
        for name, text in self.html.items():
            with self.subTest(page=name):
                self.assertEqual(loader.findall(text), [], f"{name} 加载了外部资源，离线打不开")
                self.assertEqual(import_rule.findall(text), [], f"{name} 用 @import 拉了外部样式")
                self.assertIn('href="../../ui/styles.css"', text,
                              f"{name} 没有引用 mentor 的真实样式表")

    def test_external_citations_are_labelled(self) -> None:
        """出处链接应在新标签打开且带 noopener，避免本页被替换掉。"""
        for match in re.finditer(r'<a\s+href="https?://[^"]+"([^>]*)>', self.html["index.html"]):
            attrs = match.group(1)
            with self.subTest(attrs=attrs[:60]):
                self.assertIn('target="_blank"', attrs)
                self.assertIn("noopener", attrs)

    def test_index_links_every_decision(self) -> None:
        index = self.html["index.html"]
        for name in PAGES[1:]:
            with self.subTest(page=name):
                self.assertIn(name, index, f"索引页没有链接到 {name}")


if __name__ == "__main__":
    unittest.main()
