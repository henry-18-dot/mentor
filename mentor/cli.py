"""mentor 命令行：建库、摄取、提问、导出、起服务。

    python -m mentor.cli doctor
    python -m mentor.cli seed --root D:\\CS336 --title CS336
    python -m mentor.cli ask "BPE 是怎么训练的？" --mode explore
    python -m mentor.cli serve --root D:\\CS336 --open
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .llm import build_provider, load_config, provider_info
from .render import surface_to_markdown
from .server import HOST, PORT, MentorApp, ui_candidates, ui_dir
from .store import Store, default_db_path
from .targets import DEFAULT_LIMIT, default_seed_targets, expand


def _setup_console() -> None:
    """Windows 控制台默认 GBK，讲义里有 emoji 和公式，必须切到 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):  # pragma: no cover - 非交互环境
            pass
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def _print(payload: Any) -> None:
    if isinstance(payload, (dict, list)):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(payload)


def _resolve_notebook(store: Store, wanted: str) -> str:
    """支持用完整 id、id 前缀或标题片段指认笔记本。"""
    if not wanted:
        notebooks = store.list_notebooks()
        if len(notebooks) == 1:
            return notebooks[0]["id"]
        raise SystemExit("有多个笔记本，请用 --notebook 指定（先跑 `notebooks` 看列表）")
    for notebook in store.list_notebooks():
        if notebook["id"] == wanted or notebook["id"].startswith(wanted):
            return notebook["id"]
    low = wanted.lower()
    matches = [n for n in store.list_notebooks() if low in n["title"].lower()]
    if len(matches) == 1:
        return matches[0]["id"]
    if not matches:
        raise SystemExit(f"找不到笔记本 {wanted!r}")
    raise SystemExit("标题匹配到多个笔记本，请用 id：" + ", ".join(m["id"] for m in matches))


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------
def cmd_doctor(args: argparse.Namespace) -> int:
    _setup_console()
    db = Store(args.db)
    report: dict[str, Any] = {
        "version": __version__,
        "python": sys.version.split()[0],
        "db": str(db.path),
        "root": str(Path(args.root).resolve()) if args.root else str(Path.cwd()),
        "provider": provider_info(build_provider(args.provider)),
        "notebooks": len(db.list_notebooks()),
    }
    optional: dict[str, str] = {}
    for module in ("pypdf", "fitz", "pdfminer"):
        try:
            __import__(module)
            optional[module] = "有"
        except ImportError:
            optional[module] = "无"
    report["pdf_libs"] = optional
    try:
        from . import ingest  # noqa: F401
        report["ingest"] = "有"
    except Exception as exc:  # pragma: no cover - 摄取层缺失时也要能诊断
        report["ingest"] = f"缺失：{exc}"
    ui = ui_dir()
    report["ui"] = {"dir": str(ui), "index": (ui / "index.html").exists(),
                    "widgets": (ui / "widgets.js").exists(),
                    "selftest": (ui / "self-test.html").exists(),
                    "candidates": [str(p) for p in ui_candidates()]}
    report["checks"] = _doctor_checks(report)
    _print(report)
    return 0 if not report["checks"] else 1


def _doctor_checks(report: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if sys.version_info < (3, 10):
        problems.append("Python 需要 3.10+（当前 %s）" % report["python"])
    if "缺失" in str(report.get("ingest")):
        problems.append("摄取层不可用，无法导入材料")
    if not report["ui"]["index"]:
        problems.append("界面缺少 index.html，`serve` 只能提供接口")
    if not report["provider"]["available"]:
        problems.append("生成后端为离线抽取模式；要接模型请设置 MENTOR_LLM_* 或 config.json")
    # 配置文件坏了要单独说：否则"离线模式"会被误读成"没配模型"，而实际是配置读不出来
    config_error = (report.get("provider") or {}).get("config_error")
    if config_error:
        problems.append(f"配置文件有问题（已忽略，当前按默认跑）：{config_error}")
    return problems


def cmd_notebooks(args: argparse.Namespace) -> int:
    store = Store(args.db)
    rows = store.list_notebooks()
    if not rows:
        print("还没有笔记本。先跑：python -m mentor.cli seed --root <材料目录>")
        return 0
    for row in rows:
        print(f"{row['id']}  {row['title']}  材料 {row['source_count']} · 问答 {row['turn_count']} · {row['created_at']}")
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    app = MentorApp(Store(args.db), root=args.root or os.getcwd(), provider=build_provider(args.provider))
    patterns = args.paths or default_seed_targets(app.root)
    notebook = app.store.create_notebook(args.title)
    result = app.ingest(notebook.id, patterns, limit=getattr(args, "limit", DEFAULT_LIMIT))
    print(f"笔记本：{notebook.id}  《{notebook.title}》")
    print(f"材料根：{app.root}")
    if result.get("truncated"):
        print(f"⚠ {result.get('warning')}")
    for source in result["sources"]:
        print(f"  + {source['kind']:<12} {source['path']}")
    for item in result["skipped"]:
        print(f"  - 跳过 {item.get('path')}：{item.get('reason')}")
    stats = result.get("stats") or {}
    print(f"共 {stats.get('sources', 0)} 份材料、{stats.get('chunks', 0)} 个片段")
    print(f"\n下一步：python -m mentor.cli ask \"<问题>\" --notebook {notebook.id} --root {app.root}")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    app = MentorApp(Store(args.db), root=args.root or os.getcwd(), provider=build_provider(args.provider))
    notebook_id = _resolve_notebook(app.store, args.notebook)
    result = app.ingest(notebook_id, args.paths, limit=getattr(args, "limit", DEFAULT_LIMIT))
    if result.get("truncated"):
        print(f"⚠ {result.get('warning')}")
    print(f"新增/更新材料 {len(result['sources'])} 份，片段 {(result.get('stats') or {}).get('chunks', 0)} 个")
    for item in result["skipped"]:
        print(f"  - 跳过 {item.get('path')}：{item.get('reason')}")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    _setup_console()
    app = MentorApp(Store(args.db), root=args.root or os.getcwd(), provider=build_provider(args.provider))
    notebook_id = _resolve_notebook(app.store, args.notebook)
    if args.stream:
        for event in app.tutor.ask_stream(notebook_id, args.question, args.mode):
            name, data = event["event"], event["data"]
            if name == "plan":
                print(f"〔版面〕{data['layout']}｜组件：{'、'.join(data['planned'])}｜后端：{data['provider']}")
            elif name == "retrieval":
                print(f"〔检索〕{len(data['hits'])} 段，覆盖 {data['coverage']}")
                for hit in data["hits"]:
                    print(f"   [{hit['n']}] {hit['locator']}  {hit['why']}")
            elif name == "widget":
                print(f"\n〔组件 {data['widget']['type']}〕")
                print(surface_to_markdown({"question": "", "widgets": [data["widget"]],
                                           "citations": []}).strip())
            elif name == "surface":
                surface = data["surface"]
                for note in surface.get("notes") or []:
                    print(f"〔说明〕{note}")
                if args.out:
                    out = Path(args.out)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(surface_to_markdown(surface), encoding="utf-8")
                    print(f"\n已导出：{out}")
                print(f"\n〔完成〕界面 {surface['id']}｜组件 {len(surface['widgets'])} 个｜引用 {len(surface['citations'])} 条")
            elif name == "error":
                print(f"〔错误〕{data.get('message')}", file=sys.stderr)
                return 1
        return 0

    surface = app.tutor.ask(notebook_id, args.question, args.mode)
    text = surface_to_markdown(surface)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"已导出：{out}")
    else:
        print(text)
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    _setup_console()
    app = MentorApp(Store(args.db), root=args.root or os.getcwd())
    notebook_id = _resolve_notebook(app.store, args.notebook)
    result = app.search(notebook_id, args.query, limit=args.limit)
    for hit in result["hits"]:
        print(f"{hit['score']:>6}  {hit['locator']:<28} {hit['why']}")
        print(f"        {hit['snippet']}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    _setup_console()
    store = Store(args.db)
    source = store.get_source(args.source)
    if source is None:
        print("按材料 id 找不到；下面列出所有材料的 id：")
        for notebook in store.list_notebooks():
            print(f"\n《{notebook['title']}》 {notebook['id']}")
            for item in store.notebook_sources(notebook["id"]):
                print(f"  {item.id}  {item.kind:<12} {item.path}")
        return 0
    print(f"{source.path}  [{source.kind}]  {len(source.text)} 字")
    for chunk in store.source_chunks(source.id)[: args.limit]:
        locator = chunk.span.locator(source.path.rsplit('/', 1)[-1])
        print(f"\n--- {locator} ({chunk.kind}) ---")
        print(chunk.text[:400])
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    _setup_console()
    store = Store(args.db)
    turn = store.get_turn(args.turn)
    if turn is None:
        raise SystemExit(f"找不到问答记录 {args.turn}")
    text = surface_to_markdown(turn["surface"])
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"已导出：{out}（{len(text)} 字）")
    return 0


def cmd_export_notebook(args: argparse.Namespace) -> int:
    """把一本笔记本最近的若干轮问答导成一份学习指南（带材料清单与引用索引）。"""
    _setup_console()
    from .render import notebook_to_markdown

    store = Store(args.db)
    try:
        notebook_id = _resolve_notebook(store, args.notebook)
        notebook = store.get_notebook(notebook_id)
        if notebook is None:
            raise SystemExit(f"找不到笔记本 {notebook_id}")
        turns = store.list_turn_payloads(notebook_id, limit=args.limit)
        sources = [s.to_dict() for s in store.notebook_sources(notebook_id)]
    finally:
        # 显式关库：命令行进程退出时也会关，但在测试里同进程调用会占住文件
        store.close()
    text = notebook_to_markdown(notebook.to_dict(), turns, sources)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"已导出：{out}")
    print(f"  《{notebook.title}》：材料 {len(sources)} 份 · 问答 {len(turns)} 轮 · {len(text)} 字")
    if not turns:
        print("  提示：这本笔记本还没有问答记录，指南里只有材料清单。")
    return 0


def cmd_audio(args: argparse.Namespace) -> int:
    _setup_console()
    from . import audio as audio_mod

    app = MentorApp(Store(args.db), root=args.root or os.getcwd(), provider=build_provider(args.provider))
    notebook_id = _resolve_notebook(app.store, args.notebook)
    surface = app.tutor.ask(notebook_id, args.question or "把这份材料讲一遍", "listen")
    widget = next((w for w in surface.widgets if w.type == "audio_script"), None)
    if widget is None:
        raise SystemExit("这次没有生成音频脚本（材料太少或检索没命中）")
    segments = widget.data.get("segments", [])
    script_path = audio_mod.export_script(surface.question, segments, args.out)
    print(f"脚本已导出：{script_path}（{len(segments)} 段）")
    if args.mp3:
        try:
            target = audio_mod.synthesize(segments, Path(args.out).with_suffix(".mp3"),
                                          config=load_config())
            print(f"音频已合成：{target}")
        except Exception as exc:
            print(f"未能合成 mp3：{exc}")
            print("配置 MENTOR_TTS_BASE / MENTOR_TTS_KEY 后重试。")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    _setup_console()
    if not 0 <= args.port <= 65535:
        # 越界端口原来会一路炸到 socketserver 的 OverflowError，用户只看到 traceback
        raise SystemExit(f"端口必须在 0~65535 之间（给的是 {args.port}；0 表示让系统挑一个空闲端口）")
    app = MentorApp(Store(args.db), root=args.root or os.getcwd(), provider=build_provider(args.provider))
    app.serve(args.host, args.port, open_browser=args.open)
    return 0


def cmd_forget(args: argparse.Namespace) -> int:
    store = Store(args.db)
    if not args.yes:
        raise SystemExit("这会清空全部笔记本与材料。确认请加 --yes")
    store.forget()
    print("已清空数据库")
    return 0


# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mentor", description="mentor：资料来源接地的学习工作台")
    parser.add_argument("--version", action="version", version=f"mentor {__version__}")
    parser.add_argument("--db", default=None, help=f"数据库路径（默认 {default_db_path()}）")
    parser.add_argument("--root", default=None, help="材料根目录（默认当前目录）")
    parser.add_argument("--provider", default=None, help="offline / openai / auto（默认 auto）")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="检查环境、依赖与界面文件")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("notebooks", help="列出笔记本")
    p.set_defaults(func=cmd_notebooks)

    p = sub.add_parser("seed", help="用一批材料新建笔记本")
    p.add_argument("paths", nargs="*", help="文件/目录/通配符；留空则用默认示例材料")
    p.add_argument("--title", default="学习笔记本")
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                   help=f"最多导入多少份材料（默认 {DEFAULT_LIMIT}；超了会明确告知）")
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("add", help="往笔记本里追加材料")
    p.add_argument("paths", nargs="+")
    p.add_argument("--notebook", default="")
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                   help=f"最多导入多少份材料（默认 {DEFAULT_LIMIT}；超了会明确告知）")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("ask", help="提问并生成界面")
    p.add_argument("question")
    p.add_argument("--notebook", default="")
    p.add_argument("--mode", default=None,
                   choices=["ask", "brief", "explore", "study", "compare", "listen"])
    p.add_argument("--out", default=None, help="导出为 markdown 文件")
    p.add_argument("--no-stream", dest="stream", action="store_false", help="静默模式，只输出结果")
    p.set_defaults(func=cmd_ask, stream=True)

    p = sub.add_parser("search", help="只做检索，不生成")
    p.add_argument("query")
    p.add_argument("--notebook", default="")
    p.add_argument("--limit", type=int, default=8)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("show", help="查看某份材料的片段与位置")
    p.add_argument("source")
    p.add_argument("--limit", type=int, default=8)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("export", help="把一次问答导出成 markdown")
    p.add_argument("turn")
    p.add_argument("--out", default="mentor-export.md")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("export-notebook", help="把整本笔记本导出成学习指南（含引用索引）")
    p.add_argument("--notebook", default="")
    p.add_argument("--limit", type=int, default=20, help="导出最近多少轮（默认 20）")
    p.add_argument("--out", default="study-guide.md")
    p.set_defaults(func=cmd_export_notebook)

    p = sub.add_parser("audio", help="生成音频概览脚本（可选合成 mp3）")
    p.add_argument("question", nargs="?", default="")
    p.add_argument("--notebook", default="")
    p.add_argument("--out", default="audio-overview.md")
    p.add_argument("--mp3", action="store_true")
    p.set_defaults(func=cmd_audio)

    p = sub.add_parser("serve", help="启动本地服务与界面")
    p.add_argument("--host", default=HOST)
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--open", action="store_true", help="启动后打开浏览器")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("forget", help="清空数据库（危险）")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_forget)

    # 通用选项在子命令后面也要能用：README 与工具自己的提示语写的都是
    # `mentor seed --root D:\CS336 ...` 这种形式，而 argparse 默认只认全局位置，
    # 用户照抄第一条命令就会撞上 "unrecognized arguments: --root"。
    # 用 SUPPRESS 作默认值：子命令没给时不会把全局值覆盖成 None。
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for subparser in action.choices.values():
                subparser.add_argument("--db", default=argparse.SUPPRESS,
                                       help="数据库路径（同全局 --db，可写在子命令后）")
                subparser.add_argument("--root", default=argparse.SUPPRESS,
                                       help="材料根目录（同全局 --root，可写在子命令后）")
                subparser.add_argument("--provider", default=argparse.SUPPRESS,
                                       help="生成后端（同全局 --provider，可写在子命令后）")

    return parser


def main(argv: list[str] | None = None) -> int:
    _setup_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    args.db = args.db or str(default_db_path())
    # `--db` 指向目录是很常见的误用，sqlite 的报错（"unable to open database file"）
    # 完全看不出问题在哪，所以在这里给一句人话。
    db_path = Path(args.db)
    if db_path.exists() and db_path.is_dir():
        print(f"错误：--db 指向的是一个目录（{db_path}），请给它数据库文件路径。", file=sys.stderr)
        return 2
    try:
        return int(args.func(args) or 0)
    except SystemExit as exc:
        raise
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
