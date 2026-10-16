"""模型 provider 的失败路径：真实部署最先碎在这里。

用假上游（本机 HTTP 服务）把每种坏行为都演一遍。对抗性验证时抓到两个真缺陷：

1. **内容被静默丢掉**：有的 OpenAI 兼容端点忽略 `stream:true`，直接回一个完整 JSON。
   原来的流式读取只认 `data:` 帧，于是模型的回答被扔掉，用户拿到离线兜底结果，
   而 `degraded` 还是 False —— 现在会识别完整响应体。
2. **失败不标记降级**：坏 SSE / 空 SSE / 非 JSON / 空 choices 这些"HTTP 成功但内容不可用"
   的情况，流会安静结束，界面把离线兜底冒充模型输出 —— 现在 provider 显式报错，
   tutor 再补一道"跑完但零组件"的检查。

契约：任何失败都必须在有限时间内返回**合法 surface**、标记 `degraded`、并在 notes 里说明原因。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mentor.ingest import ingest_paths
from mentor.llm import build_provider
from mentor.protocol import surface_problems
from mentor.store import Store
from mentor.tutor import Tutor

WIDGETS = {"widgets": [{"type": "answer", "data": {"markdown": "模型给的回答 [1]"},
                        "cites": [1], "title": "回答"}]}
CONTENT = json.dumps(WIDGETS, ensure_ascii=False)
MARKER = "模型给的回答"


class _Upstream(BaseHTTPRequestHandler):
    """按 mode 演出各种坏行为。"""

    protocol_version = "HTTP/1.1"
    mode = "valid_sse"

    def log_message(self, *args) -> None:  # 静音
        pass

    def _send(self, body: bytes, status: int = 200, ctype: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        mode = type(self).mode
        if mode == "http500":
            return self._send(b'{"error":"boom"}', 500)
        if mode == "notjson":
            return self._send(b"hello, not json at all", 200, "text/plain")
        if mode == "nochoices":
            return self._send(b"{}")
        if mode == "slow":
            time.sleep(3)
            return self._send(json.dumps({"choices": [{"message": {"content": CONTENT}}]}).encode())
        if mode == "valid_json":
            # 忽略 stream:true 的兼容端点：回一整个 JSON
            return self._send(json.dumps({"choices": [{"message": {"content": CONTENT}}]}).encode())
        if mode == "unknownwidget":
            payload = {"widgets": [{"type": "totally_unknown_xyz", "data": {"x": 1}}]}
            return self._send(json.dumps({"choices": [{"message": {"content": json.dumps(payload)}}]}).encode())
        # SSE 三兄弟
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        if mode == "valid_sse":
            for i in range(0, len(CONTENT), 40):
                frame = json.dumps({"choices": [{"delta": {"content": CONTENT[i:i + 40]}}]})
                self.wfile.write(f"data: {frame}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
        elif mode == "badsse":
            self.wfile.write(b"data: {broken json\n\n")
        else:  # emptysse
            self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


class ProviderFailureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
        cls.srv.daemon_threads = True
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        source = root / "m.md"
        source.write_text("字节分词把字符串映射成字节序列。BPE 合并相邻对。\n" * 5, encoding="utf-8")
        cls.store = Store(root / "provider.db")
        report = ingest_paths([source], root=root)
        cls.notebook = cls.store.create_notebook("provider 失败路径")
        cls.store.attach(cls.notebook.id, report.sources, report.chunks)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.store.close()
        cls.tmp.cleanup()

    def ask(self, mode: str, *, timeout: str = "1", endpoint: str | None = None):
        _Upstream.mode = mode
        os.environ.update({
            "MENTOR_LLM_BASE": endpoint or f"http://127.0.0.1:{self.port}/v1",
            "MENTOR_LLM_KEY": "test",
            "MENTOR_LLM_MODEL": "fake",
            "MENTOR_LLM_TIMEOUT": timeout,
        })
        tutor = Tutor(self.store, build_provider("openai"))
        started = time.perf_counter()
        surface = tutor.ask(self.notebook.id, "字节分词是什么？", "explore")
        return surface, time.perf_counter() - started

    def answer_text(self, surface) -> str:
        widget = next((w for w in surface.widgets if w.type == "answer"), None)
        return str(widget.data.get("markdown", "")) if widget else ""

    # ------------------------------------------------------------ 正常路径

    def test_sse_response_uses_model_output(self) -> None:
        surface, elapsed = self.ask("valid_sse")
        self.assertFalse(surface.degraded)
        self.assertIn(MARKER, self.answer_text(surface))
        self.assertLess(elapsed, 5)

    def test_non_streaming_json_endpoint_is_accepted(self) -> None:
        """忽略 stream:true、只回完整 JSON 的端点，内容不能被静默丢掉。"""
        surface, elapsed = self.ask("valid_json")
        self.assertFalse(surface.degraded, "完整 JSON 响应应当被正常使用")
        self.assertIn(MARKER, self.answer_text(surface), "模型的回答不能丢")
        self.assertLess(elapsed, 5)

    # ------------------------------------------------------------ 失败路径

    def _assert_degraded(self, mode: str, expect_hint: str = "") -> None:
        surface, elapsed = self.ask(mode)
        self.assertTrue(surface.degraded, f"{mode} 应当标记为降级")
        self.assertNotIn(MARKER, self.answer_text(surface))
        self.assertTrue(any("offline" in str(surface.provider) for _ in [0]), surface.provider)
        self.assertLess(elapsed, 5, f"{mode} 用了 {elapsed:.2f}s，失败路径必须快速返回")
        self.assertEqual(surface_problems(surface), [], f"{mode} 产出的 surface 不合法")
        if expect_hint:
            self.assertTrue(any(expect_hint in note for note in surface.notes),
                            f"{mode} 的 notes 里应当说明原因：{surface.notes}")

    def test_http_500_degrades(self) -> None:
        self._assert_degraded("http500", "HTTP 500")

    def test_non_json_body_degrades(self) -> None:
        self._assert_degraded("notjson", "上游返回为空")

    def test_empty_choices_degrades(self) -> None:
        self._assert_degraded("nochoices", "响应结构不认识")

    def test_broken_sse_degrades(self) -> None:
        self._assert_degraded("badsse", "上游返回为空")

    def test_empty_sse_degrades(self) -> None:
        self._assert_degraded("emptysse", "上游返回为空")

    def test_unknown_widget_type_degrades(self) -> None:
        self._assert_degraded("unknownwidget", "没有产出任何可用组件")

    def test_timeout_is_bounded(self) -> None:
        surface, elapsed = self.ask("slow", timeout="1")
        self.assertTrue(surface.degraded)
        self.assertLess(elapsed, 4, "超时必须按 MENTOR_LLM_TIMEOUT 快速放弃")

    def test_connection_refused_degrades(self) -> None:
        surface, elapsed = self.ask("valid_sse", endpoint="http://127.0.0.1:9/v1")
        self.assertTrue(surface.degraded)
        self.assertEqual(surface_problems(surface), [])
        self.assertLess(elapsed, 5)

    def test_all_failure_paths_still_render_a_surface(self) -> None:
        """界面永远不能白屏：每种失败都要有组件、有引用、能通过校验。"""
        for mode in ("http500", "notjson", "nochoices", "badsse", "emptysse", "unknownwidget"):
            with self.subTest(mode=mode):
                surface, _ = self.ask(mode)
                self.assertTrue(surface.widgets, f"{mode} 没有任何组件")
                self.assertEqual(surface_problems(surface), [])
                self.assertTrue(any(w.type == "answer" for w in surface.widgets),
                                f"{mode} 至少要有一个 answer 组件")


if __name__ == "__main__":
    unittest.main()
