"""性能基线：在真实材料上量索引构建、检索与端到端问答的耗时。

为什么需要它：mentor 的卖点是"本地优先"，那就得说清楚"本地到底多快"。
这个脚本给出一组可复现的数字（同一批材料、同一批问题、固定重复次数），
跑完写 `docs/performance.md` 与 `runs/bench.md`，以后改检索或加组件时对比着看。

用法：
    python scripts/bench.py                      # 默认用 CS336 讲义 + docs
    python scripts/bench.py --repeat 30 --out runs/bench.md
    python scripts/bench.py --root D:\\CS336 --files lectures/lecture_01.py lectures/lecture_06.py
"""

from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from mentor.ingest import ingest_paths  # noqa: E402
from mentor.llm import OfflineProvider  # noqa: E402
from mentor.store import Store  # noqa: E402
from mentor.targets import expand  # noqa: E402
from mentor.tutor import Tutor  # noqa: E402

DEFAULT_FILES = [
    "lectures/lecture_01.py",
    "lectures/lecture_06.py",
    "docs/setup-notes.md",
]
QUESTIONS = [
    ("BPE 分词器是怎么训练的？", "explore"),
    ("这门课的五个作业分别做什么？", "brief"),
    ("对比字节分词和 BPE", "compare"),
    ("给我一套复习材料", "study"),
    ("Kernel 和并行是怎么讲的？", "ask"),
    ("注意力机制的代价是什么？", "ask"),
    ("给我一份音频概览脚本", "listen"),
    ("这门课为什么存在？", "ask"),
]


def stats_ms(samples: list[float]) -> dict:
    ordered = sorted(samples)
    return {
        "n": len(samples),
        "median": round(statistics.median(samples) * 1000, 1),
        "mean": round(statistics.fmean(samples) * 1000, 1),
        "p95": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] * 1000, 1),
        "max": round(max(samples) * 1000, 1),
    }


def _setup_console() -> None:
    """Windows 控制台默认 GBK：脚本里有中文和 ¥，不切 UTF-8 会直接崩在 print 上。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def main() -> int:
    _setup_console()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=r"D:\CS336")
    parser.add_argument("--files", nargs="*", default=DEFAULT_FILES)
    parser.add_argument("--repeat", type=int, default=15, help="每个查询重复次数")
    parser.add_argument("--out", default=str(PROJECT / "runs" / "bench.md"))
    args = parser.parse_args()

    root = Path(args.root).resolve()
    targets = expand(args.files, root=root)
    if not targets:
        print(f"在 {root} 下找不到这些材料：{args.files}")
        return 2

    results: dict[str, object] = {"root": str(root), "files": [str(p.relative_to(root)) for p in targets]}
    tmp = tempfile.TemporaryDirectory()
    store = Store(Path(tmp.name) / "bench.db")
    try:
        # 1) 摄取
        started = time.perf_counter()
        report = ingest_paths(targets, root=root)
        ingest_seconds = time.perf_counter() - started
        notebook = store.create_notebook("性能基线")
        store.attach(notebook.id, report.sources, report.chunks)

        # 2) 索引构建（冷）+ 命中缓存（热）
        tracemalloc.start()
        started = time.perf_counter()
        index = store.index(notebook.id)
        cold_seconds = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        started = time.perf_counter()
        store.index(notebook.id)
        warm_seconds = time.perf_counter() - started

        stats = index.stats()
        results.update({
            "ingest_seconds": round(ingest_seconds, 3),
            "sources": len(report.sources),
            "chunks": stats["chunks"],
            "terms": stats["terms"],
            "avg_chunk_tokens": stats["avg_chunk_tokens"],
            "index_cold_ms": round(cold_seconds * 1000, 1),
            "index_warm_ms": round(warm_seconds * 1000, 3),
            "index_peak_mb": round(peak / 1024 / 1024, 1),
        })

        # 3) 检索（每次提问都会做一次）
        per_query: dict[str, dict] = {}
        all_samples: list[float] = []
        for question, _mode in QUESTIONS:
            samples = []
            for _ in range(max(1, args.repeat)):
                started = time.perf_counter()
                hits = index.search(question, limit=8)
                samples.append(time.perf_counter() - started)
            per_query[question] = stats_ms(samples)
            all_samples.extend(samples)
        results["search_overall"] = stats_ms(all_samples)
        results["search_per_query"] = per_query

        # 4) 端到端问答（离线 provider：检索 + 组装组件 + 接地校验 + 存档）
        tutor = Tutor(store, OfflineProvider())
        ask_samples: list[float] = []
        ask_detail: list[dict] = []
        for question, mode in QUESTIONS:
            started = time.perf_counter()
            surface = tutor.ask(notebook.id, question, mode)
            elapsed = time.perf_counter() - started
            ask_samples.append(elapsed)
            ask_detail.append({
                "question": question, "mode": mode, "ms": round(elapsed * 1000, 1),
                "widgets": len(surface.widgets),
                "citations": len(surface.citations),
                "degraded": surface.degraded,
            })
        results["ask_overall"] = stats_ms(ask_samples)
        results["ask_detail"] = ask_detail
    finally:
        store.close()
        tmp.cleanup()

    # 输出
    search = results["search_overall"]
    ask = results["ask_overall"]
    lines = [
        "# mentor 性能基线",
        "",
        f"- 材料根：`{results['root']}`",
        f"- 材料：{len(results['files'])} 份 → {results['sources']} 个源、{results['chunks']} 个片段、"
        f"{results['terms']} 个索引词（平均 {results['avg_chunk_tokens']} token/片段）",
        f"- 摄取：{results['ingest_seconds']} s",
        f"- 索引构建：冷 {results['index_cold_ms']} ms（峰值内存 {results['index_peak_mb']} MB）"
        f"／热（命中缓存）{results['index_warm_ms']} ms",
        f"- 检索（{search['n']} 次）：中位 {search['median']} ms，均值 {search['mean']} ms，"
        f"p95 {search['p95']} ms，最慢 {search['max']} ms",
        f"- 端到端问答（{ask['n']} 次，离线 provider）：中位 {ask['median']} ms，均值 {ask['mean']} ms，"
        f"p95 {ask['p95']} ms，最慢 {ask['max']} ms",
        "",
        "## 每条查询的检索耗时（中位 / p95，毫秒）",
        "",
        "| 查询 | 中位 | p95 | 命中 |",
        "|---|---:|---:|---:|",
    ]
    for question, _mode in QUESTIONS:
        row = results["search_per_query"][question]
        lines.append(f"| {question} | {row['median']} | {row['p95']} | {row['n']} |")
    lines += ["", "## 每次问答的端到端耗时与产出", "",
              "| 问题 | 模式 | 耗时(ms) | 组件 | 引用 | 降级 |", "|---|---|---:|---:|---:|:--:|"]
    for item in results["ask_detail"]:
        lines.append(f"| {item['question']} | {item['mode']} | {item['ms']} | {item['widgets']} | "
                     f"{item['citations']} | {'是' if item['degraded'] else '否'} |")

    text = "\n".join(lines) + "\n"
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
