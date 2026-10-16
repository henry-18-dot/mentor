"""界面与后端的静态契约检查。

界面和后端是并行开发的，最容易出的问题是"后端加了组件、界面没渲染器"或
"界面调了一个不存在的接口"。这些检查不看视觉效果（那要靠无头浏览器截图），
只保证两边的名字对得上。界面文件缺失时整体 skip。
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from mentor.protocol import LAYOUTS, WIDGET_TYPES
from mentor.server import ui_dir

UI = ui_dir()


def _read(name: str) -> str:
    path = UI / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


@unittest.skipUnless((UI / "widgets.js").exists(), "界面还没生成（由界面任务并行产出）")
class WidgetContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.js = _read("widgets.js")
        self.app = _read("app.js")

    def test_every_widget_type_has_a_renderer(self) -> None:
        missing = [name for name in WIDGET_TYPES
                   if not re.search(rf'["\']?{re.escape(name)}["\']?\s*[:(]', self.js)]
        self.assertEqual(missing, [], f"这些组件在 widgets.js 里没有渲染器：{missing}")

    def test_unknown_types_are_handled_gracefully(self) -> None:
        self.assertRegex(self.js, r"未知组件|unknown",
                         "widgets.js 需要给未知组件一个可折叠的兜底渲染")

    def test_exported_api_matches_contract(self) -> None:
        for needed in ("renderWidget", "renderers"):
            self.assertIn(needed, self.js, f"widgets.js 缺少导出 {needed}")

    def test_layouts_are_known_to_the_ui(self) -> None:
        haystack = self.js + self.app
        missing = [layout for layout in LAYOUTS
                   if f'"{layout}"' not in haystack and f"'{layout}'" not in haystack]
        # 版面名允许只作为 CSS class 出现，宽松处理：只在全都找不到时报警
        self.assertLess(len(missing), len(LAYOUTS), f"界面完全不认识这些版面：{missing}")

    def test_modes_cover_the_six_documented_ones(self) -> None:
        missing = [mode for mode in ("ask", "brief", "explore", "study", "compare", "listen")
                   if mode not in self.app]
        self.assertEqual(missing, [], f"模式 chips 缺少：{missing}")

    def test_app_calls_only_documented_endpoints(self) -> None:
        used = set(re.findall(r"/api/[a-zA-Z0-9_\-/{}$.]*", self.app))
        allowed_prefixes = ("/api/health", "/api/notebooks", "/api/sources", "/api/ask",
                            "/api/turns", "/api/search")
        stray = {path for path in used
                 if not any(path.startswith(prefix) for prefix in allowed_prefixes)}
        self.assertEqual(stray, set(), f"app.js 调用了契约外的接口：{stray}")

    def test_app_parses_sse_manually(self) -> None:
        self.assertNotIn("new EventSource", self.app,
                         "POST 不能用 EventSource，必须手动解析流")
        self.assertRegex(self.app, r"getReader|ReadableStream|text/event-stream",
                         "app.js 应当手动解析 SSE 流")


@unittest.skipUnless((UI / "fixtures" / "surface-demo.json").exists(), "预览 fixtures 缺失")
class PreviewFixtureTest(unittest.TestCase):
    def test_fixture_covers_every_widget_type(self) -> None:
        data = json.loads((UI / "fixtures" / "surface-demo.json").read_text(encoding="utf-8"))
        surface = data.get("surface", data)
        present = {widget.get("type") for widget in surface.get("widgets", [])}
        missing = sorted(set(WIDGET_TYPES) - present)
        self.assertEqual(missing, [], f"预览 fixtures 少了这些组件：{missing}")

    def test_fixture_surface_is_well_formed(self) -> None:
        """预览 Surface 本身要合法——但允许刻意放一个未知类型来演示兜底渲染。"""
        from mentor.protocol import Citation, Surface, Widget, surface_problems

        data = json.loads((UI / "fixtures" / "surface-demo.json").read_text(encoding="utf-8"))
        raw = data.get("surface", data)
        widgets = [Widget(type=w["type"], data=w.get("data", {}), title=w.get("title", ""),
                          cites=list(w.get("cites", []))) for w in raw.get("widgets", [])]
        known = [w for w in widgets if w.type in WIDGET_TYPES]
        unknown = [w for w in widgets if w.type not in WIDGET_TYPES]
        from mentor.protocol import Citation as _C
        citations = [_C(n=c["n"], chunk_id=c.get("chunk_id", ""), source_id=c.get("source_id", ""),
                        locator=c.get("locator", ""), title=c.get("title", ""),
                        quote=c.get("quote", ""))
                     for c in raw.get("citations", [])]
        surface = Surface(id=raw.get("id", "fixture"), question=raw.get("question", "预览"),
                          layout=raw.get("layout", "focus"), widgets=known, citations=citations)
        self.assertEqual(surface_problems(surface), [],
                         "预览里合法组件的部分自己不满足后端契约，会掩盖真实问题")
        # 未知组件是刻意的演示样例，不能超过 1 个，且必须走兜底渲染
        self.assertLessEqual(len(unknown), 1, f"未知组件太多：{[w.type for w in unknown]}")

    def test_preview_demos_unknown_widget_fallback(self) -> None:
        data = json.loads((UI / "fixtures" / "surface-demo.json").read_text(encoding="utf-8"))
        raw = data.get("surface", data)
        unknown = [w for w in raw.get("widgets", []) if w.get("type") not in WIDGET_TYPES]
        self.assertEqual(len(unknown), 1,
                         "fixtures 应当保留一个未知类型，用来演示兜底渲染不会崩")
        self.assertIn("unknown", _read("widgets.js"))

    def test_preview_page_loads_without_backend(self) -> None:
        preview = _read("preview.html")
        self.assertTrue(preview, "preview.html 应当存在（离线验收用）")
        self.assertIn("surface-demo.json", preview)
        self.assertNotIn("http://", preview.replace("http://127.0.0.1", ""),
                         "离线预览不应依赖外部站点")


if __name__ == "__main__":
    unittest.main()
