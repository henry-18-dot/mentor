"""界面这一层的安全边界：不依赖浏览器的静态守卫 + 载荷页自身的正确性。

**为什么要有它**：真正压到漏洞的是 `scripts/accept_ui.py --case hostile`（真浏览器）。
但那个检查要起 Chrome、跑几十秒；这里用几条毫秒级的静态断言守住最容易回归的地方：

1. `ui/hostile.html` / `ui/preview.html` 的内联 JSON 必须能被解析 —— 载荷里出现字面量
   `</script>` 会提前闭合脚本块（实测：注入真的执行了、JSON 被截断、渲染器压根没跑）；
2. `widgets.js` 里 markdown 链接必须走 `safeUrl()` 白名单，不能再出现 `href: link[2]` 这种直通；
3. 载荷页里确实带着危险协议的用例（否则"没发现 javascript: 链接"只是没测到）。
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "ui"


def inline_json_block(html: str, block_id: str) -> str:
    match = re.search(
        rf'<script type="application/json" id="{block_id}">(.*?)</script>', html, re.S)
    if not match:
        raise AssertionError(f"页面里找不到内联 JSON 块 {block_id}")
    return match.group(1)


class InlineJsonSafetyTest(unittest.TestCase):
    def test_hostile_page_inline_json_parses(self) -> None:
        html = (UI / "hostile.html").read_text(encoding="utf-8")
        surface = json.loads(inline_json_block(html, "demo-surface"))
        self.assertGreater(len(surface["widgets"]), 20)
        # 载荷里必须真的有 </script>，才说明转义这件事被测到了
        self.assertIn("</script>", json.dumps(surface, ensure_ascii=False).replace("<\\/", "</"))
        self.assertNotIn("</script>", inline_json_block(html, "demo-surface"),
                         "内联 JSON 里出现了未转义的 </script>，脚本块会被提前闭合")

    def test_preview_page_matches_fixture(self) -> None:
        html = (UI / "preview.html").read_text(encoding="utf-8")
        inlined = json.loads(inline_json_block(html, "demo-surface"))
        fixture = json.loads((UI / "fixtures" / "surface-demo.json").read_text(encoding="utf-8"))
        self.assertEqual(inlined, fixture, "preview.html 与 fixtures 不同步，重跑 ui/build_preview.py")

    def test_inline_json_escapes_script_close(self) -> None:
        import importlib.util

        for name in ("build_preview.py", "build_hostile.py"):
            spec = importlib.util.spec_from_file_location(name.replace(".py", ""), UI / name)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # type: ignore[union-attr]
            encoded = module.inline_json({"x": "</script><img src=x onerror=1>"})
            self.assertNotIn("</script", encoded, f"{name} 没有转义 </script")
            self.assertEqual(json.loads(encoded)["x"], "</script><img src=x onerror=1>",
                             "转义不能改变 JSON 语义")


class UrlWhitelistTest(unittest.TestCase):
    def setUp(self) -> None:
        self.js = (UI / "widgets.js").read_text(encoding="utf-8")

    def test_markdown_links_go_through_whitelist(self) -> None:
        self.assertIn("export function safeUrl", self.js, "URL 白名单函数不见了")
        self.assertNotIn("href: link[2],", self.js,
                         "markdown 链接又直通 href 了 —— javascript: 会被原样渲染成可点链接")
        self.assertIn("safeUrl(link[2])", self.js)

    def test_image_src_goes_through_whitelist(self) -> None:
        self.assertIn("safeUrl(rawSrc", self.js, "图片 src 必须过白名单")

    def test_dangerous_schemes_are_listed_in_the_payload(self) -> None:
        html = (UI / "hostile.html").read_text(encoding="utf-8")
        for scheme in ("javascript:", "vbscript:", "data:text/html"):
            self.assertIn(scheme, html, f"敌意载荷里没有 {scheme} 用例，等于没测")
        # 正对照：正常链接必须还在，否则"白名单没误伤"也证明不了
        self.assertIn("https://example.com/x", html)


if __name__ == "__main__":
    unittest.main()
