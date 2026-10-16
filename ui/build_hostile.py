# 生成 ui/hostile.html：一个"敌意 surface 载荷"页面，用来验证界面遇到
# 注入、错类型、缺字段、超大字符串、未知组件、海量组件时不会崩、不会执行脚本。
# 用法： python ui/build_hostile.py   （然后 python scripts/accept_ui.py 会顺带验收它）
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

SCRIPT_TAG = "<script>window.__PWNED__ = 1;</script>"
IMG_ONERROR = '<img src=x onerror="window.__PWNED__ = 1">'
JS_URL = "javascript:window.__PWNED__ = 1"
BIDI = "\u202e恶意反转\u202c"
HUGE = "巨串" * 40_000                      # 约 80k 字符
NULLY = "含空字符\u0000的文本"

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>mentor 敌意载荷验证页</title>
<link rel="stylesheet" href="styles.css">
</head>
<body>
<!--
  由 ui/build_hostile.py 生成。载荷全是"模型/上游可能吐出来的坏东西"：
  注入尝试（script / img onerror / javascript: URL）、错类型、缺字段、未知组件类型、
  超长字符串、双向文本覆盖字符、空字符、不可解析的引用。
  判定标准（见 scripts/accept_ui.py 的 check_hostile）：
  脚本一行都不许执行、以文本形式显示、页面不崩、组件数有上限。
-->
<script>
  window.__PWNED__ = undefined;
  window.__ERRORS__ = [];
  window.addEventListener("error", (e) => window.__ERRORS__.push(String(e.message || e)));
  window.addEventListener("unhandledrejection", (e) => window.__ERRORS__.push("rejection: " + String(e.reason)));
</script>
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


INJECT_LINKS = (
    "注入链接：\n\n"
    "- [j1](javascript:window.__PWNED__=1)\n"
    "- [j2](JavaScript:window.__PWNED__=1)\n"
    "- [j3](vbscript:window.__PWNED__=1)\n"
    "- [j4](java\tscript:window.__PWNED__=1)\n"
    "- [j5](data:text/html,x)\n"
    "\n正常链接（正对照，必须仍是真链接）：\n\n"
    "- [ok-relative](./index.html)\n"
    "- [ok-https](https://example.com/x)\n"
    "- [ok-anchor](#canvas)\n"
)


def hostile_widgets() -> list[dict]:
    widgets: list[dict] = [
        {"type": "answer", "data": {"markdown": (f"脚本文本：{SCRIPT_TAG}\n\n图片文本：{IMG_ONERROR}\n\n"
                                                 f"链接：[点我]({JS_URL})\n\n正常加粗 **bold** 与 `code`\n\n"
                                                 f"双向：{BIDI}\n\n空字符：{NULLY}\n\n巨串：{HUGE}")}},
        {"type": "answer", "title": "协议注入", "cites": [1],
         "data": {"markdown": INJECT_LINKS}},
        {"type": "answer", "title": "坏引用", "cites": [1, 999, "x", None],
         "data": {"markdown": "引用齐全性测试 [1] [999] [x]"}},
        {"type": "answer", "data": None},                       # data 是 null
        {"type": "answer", "data": []},                         # data 是数组
        {"type": "answer", "data": "字符串"},                    # data 是字符串
        {"data": {"markdown": "缺 type"}},                       # 缺 type
        {"type": 123, "data": {"markdown": "type 是数字"}},       # type 类型错
        {"type": "totally_unknown_evil", "data": {"markdown": "未知类型"}},
        {"type": "quote", "data": {"text": IMG_ONERROR, "locator": "a.py:1", "title": SCRIPT_TAG}},
        {"type": "quote", "data": {"text": HUGE, "locator": "../../etc/passwd", "title": JS_URL}},
        {"type": "image", "data": {"src": JS_URL, "alt": IMG_ONERROR, "caption": SCRIPT_TAG}},
        {"type": "image", "data": {"src": "", "alt": "", "width": -1, "height": 10 ** 9}},
        {"type": "sources", "data": {"items": [None, 1, "x", {"n": 1, "locator": SCRIPT_TAG,
                                                              "title": IMG_ONERROR, "quote": HUGE}]}},
        {"type": "cheatsheet", "data": {"items": [{"term": SCRIPT_TAG, "definition": IMG_ONERROR},
                                                  {"term": None, "definition": None},
                                                  "字符串条目"]}},
        {"type": "concept_map", "data": {"nodes": [{"id": "a", "label": SCRIPT_TAG},
                                                   {"id": None, "label": None}, "x"],
                                         "edges": [{"source": "a", "target": None, "weight": "重"},
                                                   None]}},
        {"type": "quiz", "data": {"items": [{"question": SCRIPT_TAG, "options": [IMG_ONERROR],
                                             "answer": 99}, None]}},
        {"type": "flashcards", "data": {"items": [{"front": SCRIPT_TAG, "back": NULLY}, {"front": HUGE}]}},
        {"type": "progress", "data": {"items": [{"label": SCRIPT_TAG, "value": 5}, {"label": "x", "value": -3},
                                                {"label": "y", "value": "NaN"}]}},
    ]
    # 海量组件：界面要么有上限，要么能撑住，但不能崩
    for i in range(60):
        widgets.append({"type": "steps", "title": f"批量 {i}",
                        "data": {"items": [{"text": f"步骤 {i}", "detail": SCRIPT_TAG}]}})
    return widgets


def hostile_surface() -> dict:
    return {
        "protocol": "0.1",
        "question": SCRIPT_TAG,
        "intent": "explore",
        "layout": "explore",
        "provider": SCRIPT_TAG,
        "degraded": False,
        "notes": [SCRIPT_TAG, IMG_ONERROR],
        "created_at": "2026-10-16T01:10:00+0800",
        "citations": [
            {"n": 1, "source_id": "s1", "chunk_id": "ck_x", "locator": SCRIPT_TAG,
             "title": IMG_ONERROR, "quote": HUGE},
            {"n": 2, "source_id": None, "chunk_id": None, "locator": None, "title": None, "quote": None},
            "不是对象",
            {"n": "三", "locator": JS_URL},
        ],
        "widgets": hostile_widgets(),
    }


def hostile_sources() -> dict:
    return {"sources": [
        {"id": "s1", "path": SCRIPT_TAG, "kind": SCRIPT_TAG, "title": IMG_ONERROR,
         "text": SCRIPT_TAG + HUGE, "raw": SCRIPT_TAG + "\n" + IMG_ONERROR},
        {"id": "s2", "path": None, "kind": None, "title": None},
        "不是对象",
    ]}


def inline_json(payload) -> str:
    """把 JSON 安全地塞进 <script type="application/json">。

    HTML 解析器只看字面量 `</script`，所以 JSON 里出现它就等于提前闭合脚本块——
    载荷会被当 HTML 解析（实测：注入真的执行了、JSON 被截断、渲染器压根没跑）。
    标准做法是把 `/` 转义成 `\\/`，JSON 语义不变，解析器认不出来。
    """
    return json.dumps(payload, ensure_ascii=False, indent=1).replace("</", "<\\/")


def main() -> int:
    surface = hostile_surface()
    sources = hostile_sources()
    html = (TEMPLATE
            .replace("__SURFACE__", inline_json(surface))
            .replace("__SOURCES__", inline_json(sources)))
    (HERE / "hostile.html").write_text(html, encoding="utf-8")
    print(f"hostile.html 已生成：{len(surface['widgets'])} 个组件"
          f"（含未知类型与海量组件）、{len(sources['sources'])} 份材料")
    print("  载荷里的 </script> 已转义成 <\\/script>，不会提前闭合内联 JSON 块")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
