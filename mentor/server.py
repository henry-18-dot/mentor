"""HTTP 服务：静态界面 + JSON 接口 + SSE 问答流。

只用标准库（`http.server` / `ThreadingHTTPServer`）。问答走 SSE，因为
"智能界面"的价值一半在过程里——骨架先到、组件逐个到，用户不用对着
转圈等一整段文本。`?stream=0` 时退化成一次性返回，方便脚本和测试。
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__
from .llm import build_provider, provider_info
from .protocol import PROTOCOL_VERSION, Source
from .store import Store
from .targets import DEFAULT_LIMIT, default_seed_targets, expand, expand_report
from .tutor import Tutor

log = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 8477
MAX_BODY = 8 * 1024 * 1024


def project_root() -> Path:
    """默认的路径解析根：MENTOR_ROOT，否则当前工作目录。

    材料在哪就以哪为根，所以默认不猜，用运行目录；CLI 的 --root 可以覆盖。
    """
    env = os.environ.get("MENTOR_ROOT")
    if env:
        return Path(env).resolve()
    return Path.cwd().resolve()


def _locate_term(source: Source, term: str) -> dict[str, Any]:
    """在原文里找 term 的首个命中行（搜索后直接打开原文时用）。

    行号以**界面会显示的那份文本**为准（`display_text()`）：PDF 的 `raw_text` 是二进制载荷，
    拿它搜索只会搜到乱码。找不到就返回 line=0，让界面老实说"没定位到"，而不是随便跳一行。
    """
    from . import textutil as tu

    term = (term or "").strip()
    if not term:
        return {"term": "", "line": 0, "found": False}
    text = source.display_text()
    for index, line in enumerate(text.replace("\r\n", "\n").split("\n"), start=1):
        if tu.find_term_spans(line, term, limit=1):
            return {"term": term, "line": index, "found": True}
    return {"term": term, "line": 0, "found": False}


class _Server(ThreadingHTTPServer):
    """关掉浏览器提前断开时的 traceback 噪音。

    SSE 是"推完就关"的一次性流，客户端（关标签页、切页面、Esc 中断）提前断开是**正常事件**，
    但 socketserver 默认会把它当未处理异常打印一整段 traceback，看起来像服务坏了。
    真正的内部错误照旧交给默认处理（会打印出来，不该被静音）。
    """

    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:  # pragma: no cover
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError,
                            BrokenPipeError, TimeoutError)):
            log.debug("客户端提前断开：%s（%s）", client_address, type(exc).__name__)
            return
        super().handle_error(request, client_address)


def ui_candidates() -> list[Path]:
    """界面文件可能在哪：MENTOR_UI → 项目根 `ui/` → 包内 `mentor/ui/`。

    界面是独立于包开发和部署的，硬编码一个位置会让 `serve` 起得来却打不开页面，
    所以两处都找，谁先有 index.html 用谁。
    """
    here = Path(__file__).resolve()
    out: list[Path] = []
    env = os.environ.get("MENTOR_UI")
    if env:
        out.append(Path(env))
    out.append(here.parents[1] / "ui")   # 仓库里的 mentor/ui 之外的那份 ui/
    out.append(here.parent / "ui")       # 包内 mentor/ui/
    return out


def ui_dir() -> Path:
    for candidate in ui_candidates():
        if (candidate / "index.html").exists():
            return candidate.resolve()
    return ui_candidates()[0].resolve()


class MentorApp:
    """把 store / provider / tutor 装配在一起，并生成请求处理器。"""

    def __init__(self, store: Store | None = None, *, root: str | Path | None = None,
                 provider=None, ui: str | Path | None = None) -> None:
        self.store = store or Store()
        self.root = Path(root).resolve() if root else project_root()
        self.provider = provider or build_provider()
        self.tutor = Tutor(self.store, self.provider)
        self.ui = Path(ui).resolve() if ui else ui_dir()
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ 接口
    def health(self) -> dict[str, Any]:
        return {"ok": True, "version": __version__, "protocol": PROTOCOL_VERSION,
                "root": str(self.root),
                "provider": provider_info(self.provider), "ui": str(self.ui),
                "ui_candidates": [str(p) for p in ui_candidates()],
                "ui_ready": (self.ui / "index.html").exists()}

    def create_notebook(self, title: str, entries: list[str]) -> dict[str, Any]:
        notebook = self.store.create_notebook(title or "未命名笔记本")
        return self.ingest(notebook.id, entries)

    def ingest(self, notebook_id: str, entries: list[str], *,
               limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        if self.store.get_notebook(notebook_id) is None:
            raise KeyError(f"找不到笔记本 {notebook_id}")
        patterns = [e for e in entries if str(e).strip()] or default_seed_targets(self.root)
        expansion = expand_report(patterns, root=self.root, limit=limit)
        targets = expansion.paths
        result: dict[str, Any] = {"notebook": None, "sources": [], "skipped": [],
                                 "targets": [str(p) for p in targets],
                                 # 被上限截断时要说出来，否则"导入了 2000 份"会被当成"全都导入了"
                                 "truncated": expansion.truncated, "limit": expansion.limit}
        if expansion.truncated:
            result["warning"] = expansion.warning()
        if not targets:
            result["skipped"] = [{"path": p, "reason": "没有匹配到可摄取的文件"}
                                 for p in patterns]
            notebook = self.store.get_notebook(notebook_id)
            result["notebook"] = notebook.to_dict() if notebook else None
            return result
        from .ingest import ingest_paths  # 延迟导入：摄取是可选项，缺了界面也能开

        report = ingest_paths(targets, root=self.root)
        stats = self.store.attach(notebook_id, report.sources, report.chunks)
        notebook = self.store.get_notebook(notebook_id)
        result["notebook"] = notebook.to_dict() if notebook else None
        result["sources"] = [s.to_dict() for s in self.store.notebook_sources(notebook_id)]
        result["skipped"] = [dict(item) for item in report.skipped]
        result["stats"] = stats
        return result

    def notebook_payload(self, notebook_id: str) -> dict[str, Any]:
        notebook = self.store.get_notebook(notebook_id)
        if notebook is None:
            raise KeyError(notebook_id)
        return {"notebook": notebook.to_dict(),
                "sources": [s.to_dict() for s in self.store.notebook_sources(notebook_id)],
                "stats": self.store.notebook_stats(notebook_id),
                "turns": self.store.list_turns(notebook_id, limit=20)}

    def search(self, notebook_id: str, query: str, limit: int = 8) -> dict[str, Any]:
        from . import textutil as tu

        index = self.store.index(notebook_id)
        sources = {s.id: s for s in self.store.notebook_sources(notebook_id)}
        hits = []
        for hit in index.search(query, limit=limit):
            chunk = hit.chunk
            source = sources.get(chunk.source_id)
            name = source.path.rsplit("/", 1)[-1] if source else chunk.source_id
            hits.append({
                "chunk_id": chunk.id, "source_id": chunk.source_id,
                "locator": chunk.span.locator(name),
                "title": chunk.heading or (source.title if source else ""),
                "snippet": tu.strip_markdown(tu.snippet(chunk.text, hit.matched, width=220)),
                "score": round(hit.score, 3), "why": hit.why, "line": chunk.span.line,
                # 命中词 + 它在片段里的偏移：界面据此做行内高亮
                "matched": hit.matched, "matches": hit.matches,
            })
        return {"hits": hits, "terms": index.query_terms(query)}

    # ------------------------------------------------------------------ 服务
    def handler(self) -> type[BaseHTTPRequestHandler]:
        app = self

        class Handler(BaseHTTPRequestHandler):
            server_version = f"mentor/{__version__}"
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args: Any) -> None:  # 静音默认访问日志
                log.debug("%s - %s", self.address_string(), fmt % args)

            # -------------------------------------------------------- 工具
            def _json(self, payload: Any, status: int = 200) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _error(self, message: str, status: int = 400) -> None:
                self._json({"error": message}, status=status)

            def _read_json(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0:
                    return {}
                if length > MAX_BODY:
                    raise ValueError("请求体过大")
                raw = self.rfile.read(length)
                try:
                    data = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"请求体不是 JSON：{exc}") from exc
                return data if isinstance(data, dict) else {}

            def _static(self, url_path: str) -> bool:
                rel = unquote(url_path).lstrip("/") or "index.html"
                # 客户端可以在路径里塞 NUL（`/%00`）之类的东西：这是**非法请求**，
                # 不是服务器故障。不先拦掉的话，Path.exists() 会抛
                # "ValueError: embedded null character"，被兜底 except 变成 500。
                if "\x00" in rel:
                    self._error("路径包含非法字符", 400)
                    return True
                try:
                    target = (app.ui / rel).resolve()
                    inside = True
                    try:
                        target.relative_to(app.ui)
                    except ValueError:
                        inside = False
                except (ValueError, OSError) as exc:
                    self._error(f"路径无法解析：{exc}", 400)
                    return True
                if not inside:
                    self._error("越界路径", 403)
                    return True
                try:
                    is_dir = target.is_dir()
                except OSError:
                    self._error("找不到路径", 404)
                    return True
                if is_dir:
                    target = target / "index.html"
                if (not target.exists() or not target.is_file()) and not target.suffix:
                    # 无扩展名的路径退化成 .html，方便 /selftest 这类入口
                    candidate = target.with_suffix(".html")
                    if candidate.exists():
                        target = candidate
                if not target.exists() or not target.is_file():
                    return False
                ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                    ctype += "; charset=utf-8"
                body = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(body)
                return True

            # --------------------------------------------------------- GET
            def do_GET(self) -> None:  # noqa: N802
                parts = urlsplit(self.path)
                path = parts.path
                query = parse_qs(parts.query)
                try:
                    if path == "/api/health":
                        self._json(app.health())
                        return
                    if path == "/api/notebooks":
                        self._json({"notebooks": app.store.list_notebooks()})
                        return
                    if path.startswith("/api/notebooks/"):
                        notebook_id = path.split("/", 3)[3]
                        self._json(app.notebook_payload(notebook_id))
                        return
                    if path.startswith("/api/sources/"):
                        source_id = path.split("/", 3)[3]
                        source = app.store.get_source(source_id)
                        if source is None:
                            self._error("找不到材料", 404)
                            return
                        payload: dict[str, Any] = {
                            "source": source.to_dict(with_text=True),
                            "chunks": [c.to_dict() for c in app.store.source_chunks(source_id)],
                        }
                        highlight = (query.get("highlight") or [""])[0].strip()
                        if highlight:
                            # 给"搜索后直接打开原文"用：告诉界面首个命中在第几行
                            payload["highlight"] = _locate_term(source, highlight)
                        self._json(payload)
                        return
                    if path.startswith("/api/turns/"):
                        turn_id = path.split("/", 3)[3]
                        turn = app.store.get_turn(turn_id)
                        if turn is None:
                            self._error("找不到问答记录", 404)
                            return
                        self._json({"turn": turn})
                        return
                    if path == "/api/turns":
                        notebook_id = (query.get("notebook_id") or [""])[0]
                        self._json({"turns": app.store.list_turns(notebook_id)})
                        return
                    if path == "/api/search":
                        notebook_id = (query.get("notebook_id") or [""])[0]
                        self._json(app.search(notebook_id, (query.get("q") or [""])[0]))
                        return
                    if path == "/favicon.ico":
                        self.send_response(204)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    if self._static(path):
                        return
                    self._error("找不到路径", 404)
                except KeyError as exc:
                    self._error(f"找不到对象：{exc}", 404)
                except Exception as exc:  # pragma: no cover - 兜底
                    log.exception("GET %s 失败", path)
                    self._error(str(exc), 500)

            # -------------------------------------------------------- POST
            def do_POST(self) -> None:  # noqa: N802
                parts = urlsplit(self.path)
                path = parts.path
                query = parse_qs(parts.query)
                try:
                    body = self._read_json()
                except ValueError as exc:
                    self._error(str(exc), 400)
                    return
                try:
                    if path == "/api/notebooks":
                        entries = [str(e) for e in (body.get("paths") or [])]
                        self._json(app.create_notebook(str(body.get("title") or "学习笔记本"), entries))
                        return
                    if path.startswith("/api/notebooks/") and path.endswith("/ingest"):
                        notebook_id = path.split("/")[3]
                        entries = [str(e) for e in (body.get("paths") or [])]
                        self._json(app.ingest(notebook_id, entries))
                        return
                    if path == "/api/ask":
                        self._ask(body, query)
                        return
                    self._error("找不到路径", 404)
                except KeyError as exc:
                    self._error(f"找不到对象：{exc}", 404)
                except Exception as exc:  # pragma: no cover - 兜底
                    log.exception("POST %s 失败", path)
                    self._error(str(exc), 500)

            def do_DELETE(self) -> None:  # noqa: N802
                path = urlsplit(self.path).path
                if path.startswith("/api/notebooks/"):
                    notebook_id = path.split("/", 3)[3]
                    app.store.delete_notebook(notebook_id)
                    self._json({"deleted": notebook_id})
                    return
                self._error("找不到路径", 404)

            # --------------------------------------------------------- SSE
            def _ask(self, body: dict[str, Any], query: dict[str, list[str]]) -> None:
                notebook_id = str(body.get("notebook_id") or "")
                question = str(body.get("question") or "")
                mode = body.get("mode") or None
                # context_turns>0：把最近几轮引用过的片段带进本轮，追问才接得上
                try:
                    context_turns = int(body.get("context_turns", 2))
                except (TypeError, ValueError):
                    context_turns = 2
                stream_flag = (query.get("stream") or ["1"])[0]
                if stream_flag in ("0", "false", "no"):
                    surface: dict[str, Any] | None = None
                    for event in app.tutor.ask_stream(notebook_id, question, mode,
                                                      context_turns=context_turns):
                        if event["event"] == "surface":
                            surface = event["data"]["surface"]
                        if event["event"] == "error":
                            self._error(event["data"].get("message", "生成失败"), 400)
                            return
                    self._json({"surface": surface})
                    return

                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, no-transform")
                # 这是一次性流：协议里有终止事件 done，推完就关连接。
                # 用 keep-alive 且不给 Content-Length，会让非浏览器客户端（curl/urllib/脚本）
                # 永远读不到"流结束"（实测 30 秒超时）；前端的 fetch+ReadableStream 反而需要这个 EOF。
                self.send_header("Connection", "close")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                self.close_connection = True
                try:
                    for event in app.tutor.ask_stream(notebook_id, question, mode,
                                                      context_turns=context_turns):
                        self._sse(event["event"], event["data"])
                except (BrokenPipeError, ConnectionResetError):  # pragma: no cover
                    log.info("客户端断开，停止推送")
                except Exception as exc:  # pragma: no cover
                    log.exception("问答流失败")
                    try:
                        self._sse("error", {"message": str(exc)})
                    except OSError:
                        pass

            def _sse(self, name: str, data: dict[str, Any]) -> None:
                payload = json.dumps(data, ensure_ascii=False)
                frame = f"event: {name}\ndata: {payload}\n\n".encode("utf-8")
                self.wfile.write(frame)
                self.wfile.flush()

        return Handler

    # ------------------------------------------------------------------ 启动
    def serve(self, host: str = HOST, port: int = PORT, *, open_browser: bool = False,
              quiet: bool = False) -> None:
        handler = self.handler()
        httpd = _Server((host, port), handler)
        httpd.daemon_threads = True
        url = f"http://{host}:{port}/"
        if not quiet:
            print(f"mentor 已启动：{url}")
            print(f"  材料根目录：{self.root}")
            print(f"  界面文件：{self.ui}（{'已就绪' if (self.ui / 'index.html').exists() else '缺失 index.html'}）")
            print(f"  生成后端：{provider_info(self.provider)}")
            print("  Ctrl+C 停止")
        if open_browser:
            threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            if not quiet:
                print("\n已停止")
        finally:
            httpd.server_close()


def serve(host: str = HOST, port: int = PORT, **kwargs: Any) -> None:
    MentorApp().serve(host, port, **kwargs)
