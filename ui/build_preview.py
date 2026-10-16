# 由 ui/fixtures/*.json 生成 ui/preview.html 的内联数据块，
# 让 preview.html 双击（file://）也能渲染，不需要 fetch 本地文件。
# 用法： python mentor/ui/build_preview.py
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>mentor 离线预览 · 17 种组件</title>
<link rel="stylesheet" href="styles.css">
</head>
<body>
<!--
  离线预览页：不连后端，直接渲染 fixtures/surface-demo.json（协议里全部 16 种组件 + 1 个刻意未知的类型）。
  下面两个 application/json 块由 build_preview.py 依据 fixtures/ 生成，内容与之逐字一致；
  之所以内联，是因为浏览器不允许 file:// 页面 fetch 本地 JSON。
  fixtures/surface-demo.json 仍是唯一真源：改完 fixtures 后重跑 build_preview.py 即可。
-->
<script type="application/json" id="demo-surface">
__SURFACE__
</script>
<script type="application/json" id="demo-sources">
__SOURCES__
</script>
<div class="app" id="app"></div>
<script>window.__MENTOR_PREVIEW__ = true;</script>
<script type="module" src="./app.js"></script>
</body>
</html>
"""


def inline_json(payload) -> str:
    """把 JSON 安全地塞进 <script type="application/json">。

    HTML 解析器只看字面量 `</script`：fixture 里只要出现它，脚本块就会提前闭合、
    页面被当成 HTML 解析。转义成 `<\\/script` 即可（JSON 语义不变）。
    """
    return json.dumps(payload, ensure_ascii=False, indent=2).replace("</", "<\\/")


def main() -> int:
    surface = json.loads((HERE / "fixtures" / "surface-demo.json").read_text(encoding="utf-8"))
    sources = json.loads((HERE / "fixtures" / "sources-demo.json").read_text(encoding="utf-8"))
    html = (TEMPLATE
            .replace("__SURFACE__", inline_json(surface))
            .replace("__SOURCES__", inline_json(sources)))
    (HERE / "preview.html").write_text(html, encoding="utf-8")
    print(f"preview.html 已生成：{len(surface['widgets'])} 个组件、{len(sources['sources'])} 份材料")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
