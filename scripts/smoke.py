"""端到端冒烟：真实材料 → 建库 → 提问 → 打印界面结构。

    python scripts/smoke.py [材料目录] [--db 路径]

跑完会打印每个问题的版面、组件清单、引用条数，以及任何降级说明。
用来快速判断"整条链路还通不通"，不替代单元测试。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mentor.ingest import ingest_paths  # noqa: E402
from mentor.llm import build_provider  # noqa: E402
from mentor.store import Store  # noqa: E402
from mentor.targets import expand  # noqa: E402
from mentor.tutor import Tutor  # noqa: E402

QUESTIONS = [
    ("BPE 分词器是怎么训练的？", "explore"),
    ("这门课的五个作业分别做什么？", "brief"),
    ("对比字节分词和 BPE", "compare"),
    ("给我一套复习材料", "study"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=r"D:\CS336")
    parser.add_argument("--db", default=str(Path(__file__).resolve().parents[1] / "data" / "smoke.db"))
    parser.add_argument("--patterns", nargs="*", default=[
        "lectures/lecture_01.py", "lectures/lecture_06.py", "docs/setup-notes.md", "开始.md",
    ])
    args = parser.parse_args()

    root = Path(args.root).resolve()
    db_path = Path(args.db)
    if db_path.exists():
        db_path.unlink()
    store = Store(db_path)

    targets = expand(args.patterns, root=root)
    print(f"材料根：{root}")
    print(f"匹配到 {len(targets)} 个文件：")
    for path in targets:
        print(f"  - {path.relative_to(root)}")
    if not targets:
        print("没有材料，退出")
        return 1

    report = ingest_paths(targets, root=root)
    print(f"\n摄取：{len(report.sources)} 份材料、{len(report.chunks)} 个片段，跳过 {len(report.skipped)} 个")
    for item in report.skipped[:5]:
        print(f"  · 跳过 {item}")

    notebook = store.create_notebook("冒烟笔记本")
    store.attach(notebook.id, report.sources, report.chunks)
    index = store.index(notebook.id)
    print(f"索引：{index.stats()}")
    print("主题词：" + "、".join(index.keyphrases(10)))

    tutor = Tutor(store, build_provider())
    failed = 0
    for question, mode in QUESTIONS:
        surface = tutor.ask(notebook.id, question, mode)
        types = "、".join(w.type for w in surface.widgets)
        print(f"\n=== {question}  [{mode}] ===")
        print(f"版面 {surface.layout}｜后端 {surface.provider}｜组件 {types}")
        print(f"引用 {len(surface.citations)} 条：" + "；".join(
            f"[{c.n}] {c.locator}" for c in surface.citations[:5]))
        if surface.degraded:
            failed += 1
        for note in surface.notes:
            print(f"  ! {note}")
        answer = next((w for w in surface.widgets if w.type == "answer"), None)
        if answer:
            first = str(answer.data.get("markdown", "")).split("\n")
            print("  正文首行：" + (first[0][:110] if first else ""))
        for widget in surface.widgets:
            if widget.type == "sources":
                print("  来源：" + "；".join(
                    f"[{i['n']}] {i['locator']}" for i in widget.data.get("items", [])[:3]))
            if widget.type == "quiz":
                print(f"  自测题 {len(widget.data.get('questions', []))} 道")
            if widget.type == "concept_map":
                print(f"  概念图 {len(widget.data.get('nodes', []))} 节点 / "
                      f"{len(widget.data.get('edges', []))} 条边")
    print(f"\n降级问答：{failed}/{len(QUESTIONS)}")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
