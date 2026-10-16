"""把 Surface 导出成人能读的文件（Markdown）。

界面上是卡片，导出时是文档；两者必须说同一件事——所以引用编号、来源位置、
"材料未覆盖"的提示都要带出去，不能导出成一段没有出处的漂亮话。
"""

from __future__ import annotations

import time
from typing import Any

from .protocol import Surface


def _as_dict(item: Any) -> dict[str, Any]:
    return item.to_dict() if hasattr(item, "to_dict") else dict(item)


def notebook_to_markdown(notebook: dict, turns: list[dict], sources: list[dict],
                         *, generated_at: str = "") -> str:
    """把一本笔记本最近的若干轮问答拼成一份可离线阅读的学习指南。

    顺序：材料清单 → 问题目录 → 逐轮正文（含每轮的引用与出处）→ 引用总索引。
    引用总索引按出现次数排序，方便回头核对"哪些原文被反复引用"。
    """
    title = notebook.get("title") or notebook.get("id") or "笔记本"
    stamp = generated_at or time.strftime("%Y-%m-%d %H:%M")
    lines: list[str] = [f"# {title} · 学习指南", ""]
    lines.append(f"> 导出时间：{stamp} · 材料 {len(sources)} 份 · 问答 {len(turns)} 轮")
    lines.append("")

    if sources:
        lines.append("## 材料")
        for source in sources:
            kind = source.get("kind", "")
            path = source.get("path", source.get("id", ""))
            lines.append(f"- `{path}`（{kind}）")
        lines.append("")

    if turns:
        lines.append("## 目录")
        for index, turn in enumerate(turns, start=1):
            question = str(turn.get("question") or "").strip()
            lines.append(f"{index}. {question}")
        lines.append("")
    else:
        lines.append("这本笔记本还没有问答记录；先在界面或命令行里提问，再导出一份指南。")
        lines.append("")

    cited: dict[str, dict] = {}
    for index, turn in enumerate(turns, start=1):
        surface = turn.get("surface") or {}
        question = str(turn.get("question") or "").strip()
        lines.append(f"## {index}. {question}")
        lines.append("")
        lines.append(surface_to_markdown(surface).split("\n", 1)[-1].strip())
        lines.append("")
        for cite in surface.get("citations") or []:
            locator = str(cite.get("locator") or "")
            if not locator:
                continue
            entry = cited.setdefault(locator, {"locator": locator, "title": cite.get("title", ""),
                                               "count": 0, "questions": []})
            entry["count"] += 1
            if question not in entry["questions"]:
                entry["questions"].append(question)

    if cited:
        lines.append("## 引用总索引")
        lines.append("")
        for entry in sorted(cited.values(), key=lambda e: (-e["count"], e["locator"])):
            title = f" · {entry['title']}" if entry["title"] else ""
            asked = "；".join(entry["questions"][:3])
            lines.append(f"- `{entry['locator']}`{title} —— 被 {entry['count']} 轮引用（{asked}）")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _bar(value: Any) -> str:
    """把 0~1 的热度画成纯文本方块，导出的 markdown 里也能看出深浅。"""
    try:
        ratio = max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return str(value)
    filled = int(round(ratio * 5))
    return "█" * filled + "·" * (5 - filled) + f" {ratio:.2f}"


def surface_to_markdown(surface: Surface | dict[str, Any]) -> str:
    data = _as_dict(surface)
    lines: list[str] = [f"# {data.get('question', '问答')}", ""]
    meta = [f"版面：{data.get('layout', '')}", f"意图：{data.get('intent', '')}",
            f"生成后端：{data.get('provider', '')}", f"时间：{data.get('created_at', '')}"]
    if data.get("degraded"):
        meta.append("**降级**：部分内容由离线抽取补全")
    lines.append("> " + " · ".join(meta))
    lines.append("")
    for widget in data.get("widgets", []):
        block = widget_to_markdown(widget)
        if block:
            lines.append(block)
            lines.append("")
    notes = data.get("notes") or []
    if notes:
        lines.append("## 生成说明")
        lines.extend(f"- {note}" for note in notes)
        lines.append("")
    citations = data.get("citations") or []
    has_sources_widget = any(w.get("type") == "sources" for w in data.get("widgets", []))
    if citations and not has_sources_widget:
        # 有 sources 组件时它已经把出处列全了，这里再列一遍就是重复
        lines.append("## 来源")
        for cite in citations:
            lines.append(f"- [{cite.get('n')}] {cite.get('locator', '')} · {cite.get('title', '')}")
    return "\n".join(lines).strip() + "\n"


def widget_to_markdown(widget: dict[str, Any]) -> str:
    wtype = widget.get("type", "")
    data = widget.get("data") or {}
    title = widget.get("title") or wtype
    cites = "".join(f"[{n}]" for n in widget.get("cites") or [])
    out: list[str] = []

    if wtype == "answer":
        conf = data.get("confidence", "")
        out.append(f"## {title}" + (f"（置信度 {conf}）" if conf else ""))
        out.append(str(data.get("markdown", "")))
    elif wtype == "quote":
        out.append(f"## {title}")
        out.append(f"> {data.get('text', '')}")
        out.append(f"> — {data.get('locator', '')}")
    elif wtype == "sources":
        out.append(f"## {title}")
        for item in data.get("items", []):
            out.append(f"- [{item.get('n')}] `{item.get('locator', '')}` {item.get('title', '')}"
                       f"{'：' + str(item.get('snippet', '')) if item.get('snippet') else ''}")
    elif wtype == "concept_map":
        out.append(f"## {title}")
        for node in data.get("nodes", []):
            out.append(f"- **{node.get('label')}**（{node.get('group', '')}，权重 {node.get('weight', '')}）")
        if data.get("edges"):
            out.append("")
            out.append("关系：" + "；".join(
                f"{e.get('from')} → {e.get('to')}（{e.get('label', '')}）" for e in data["edges"]))
    elif wtype == "timeline":
        out.append(f"## {title}")
        for item in data.get("items", []):
            out.append(f"- **{item.get('at', '')}** {item.get('label', '')} — {item.get('detail', '')}")
    elif wtype == "steps":
        out.append(f"## {title}")
        for i, item in enumerate(data.get("items", []), start=1):
            out.append(f"{i}. **{item.get('title', '')}** {item.get('detail', '')}")
    elif wtype == "compare_table":
        out.append(f"## {title}")
        columns = data.get("columns", [])
        if columns:
            out.append("| " + " | ".join(str(c) for c in columns) + " |")
            out.append("|" + "---|" * len(columns))
            for row in data.get("rows", []):
                out.append("| " + " | ".join(str(c) for c in row) + " |")
        if data.get("caption"):
            out.append(f"\n*{data['caption']}*")
    elif wtype == "cheatsheet":
        out.append(f"## {title}")
        for item in data.get("items", []):
            out.append(f"- **{item.get('term', '')}**：{item.get('definition', '')}")
    elif wtype == "flashcards":
        out.append(f"## {title}")
        for i, card in enumerate(data.get("cards", []), start=1):
            out.append(f"{i}. **{card.get('front', '')}** → {card.get('back', '')}")
    elif wtype == "quiz":
        out.append(f"## {title}")
        for i, q in enumerate(data.get("questions", []), start=1):
            out.append(f"{i}. {q.get('q', '')}")
            for j, choice in enumerate(q.get("choices", [])):
                mark = "✅" if j == q.get("answer_index") else "　"
                out.append(f"   - {mark} {choice}")
            if q.get("why"):
                out.append(f"   - 依据：{q['why']}")
    elif wtype == "code":
        out.append(f"## {title}")
        out.append(f"```{data.get('language', '')}\n{data.get('code', '')}\n```")
        if data.get("note"):
            out.append(f"*{data['note']}*")
    elif wtype == "formula":
        out.append(f"## {title}")
        out.append(f"```\n{data.get('plain', '')}\n```")
        if data.get("note"):
            out.append(f"*{data['note']}*")
    elif wtype == "image":
        out.append(f"## {title}")
        out.append(f"![{data.get('caption', '')}]({data.get('src', '')})")
    elif wtype == "followups":
        out.append(f"## {title}")
        out.extend(f"- {item}" for item in data.get("items", []))
    elif wtype == "audio_script":
        out.append(f"## {title}")
        for seg in data.get("segments", []):
            out.append(f"**{seg.get('speaker', '')}**：{seg.get('text', '')}")
    elif wtype == "progress":
        out.append(f"## {title}")
        for item in data.get("items", []):
            out.append(f"- {item.get('label', '')}：{item.get('value', '')} {item.get('note', '')}")
    elif wtype == "heatmap":
        out.append(f"## {title}")
        columns = data.get("columns", [])
        rows = data.get("rows", [])
        cells = data.get("cells", [])
        if columns and rows:
            out.append("| 主题 | " + " | ".join(str(c) for c in columns) + " |")
            out.append("|" + "---|" * (len(columns) + 1))
            for i, row_label in enumerate(rows):
                values = cells[i] if i < len(cells) else []
                out.append("| " + str(row_label) + " | " +
                           " | ".join(_bar(v) for v in values) + " |")
        if data.get("caption"):
            out.append(f"\n*{data['caption']}*")
    else:  # 未知组件也要导出，不能静默丢内容
        out.append(f"## {title}")
        out.append(f"```json\n{data}\n```")

    if cites and wtype not in ("sources",):
        out.append(f"\n来源：{cites}")
    return "\n".join(part for part in out if part is not None).strip()
