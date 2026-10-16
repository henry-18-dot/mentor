"""SSE 流必须能被"读到底"：这是浏览器之外的所有客户端的前提。

踩过的坑：`/api/ask` 原本发 `Connection: keep-alive` 又不给 Content-Length，
浏览器用 fetch+ReadableStream 感觉正常，但 curl / urllib / 脚本会一直阻塞到超时——
**流永远不结束**。协议里明明有终止事件 `done`，那就该推完关连接。
"""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from mentor.llm import OfflineProvider
from mentor.server import MentorApp
from mentor.store import Store
from tests.test_pipeline import sample_corpus  # 复用同一套夹具


class StreamTerminationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        root.mkdir(exist_ok=True)
        cls.store = Store(root / "sse.db")
        cls.app = MentorApp(store=cls.store, root=root, provider=OfflineProvider())
        sources, chunks = sample_corpus()
        cls.notebook = cls.store.create_notebook("SSE 测试")
        cls.store.attach(cls.notebook.id, sources, chunks)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), cls.app.handler())
        cls.httpd.daemon_threads = True
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.store.close()
        cls.tmp.cleanup()

    def read_stream(self, timeout: float = 10.0) -> tuple[float, str, dict]:
        """像普通脚本那样读 SSE：必须在 timeout 内读到 EOF，否则算失败。"""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        body = json.dumps({"notebook_id": self.notebook.id, "question": "BPE 是什么？"})
        started = time.perf_counter()
        try:
            conn.request("POST", "/api/ask", body=body,
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            headers = {k.lower(): v for k, v in resp.getheaders()}
            text = resp.read().decode("utf-8")
            return time.perf_counter() - started, text, headers
        finally:
            conn.close()

    def test_stream_reaches_eof_and_ends_with_done(self) -> None:
        elapsed, text, _ = self.read_stream()
        self.assertLess(elapsed, 10.0, "读 SSE 超时：流没有结束（这就是修复前的症状）")
        self.assertIn("event: plan", text)
        self.assertIn("event: surface", text)
        events = [line[7:] for line in text.splitlines() if line.startswith("event: ")]
        self.assertEqual(events[-1], "done", f"最后一个事件应当是 done，实际 {events[-3:]}")
        self.assertIn("done", events)

    def test_stream_uses_connection_close(self) -> None:
        _, _, headers = self.read_stream()
        self.assertEqual(headers.get("connection", "").lower(), "close",
                         "一次性流必须声明 Connection: close，否则客户端读不到结束")
        self.assertIn("text/event-stream", headers.get("content-type", ""))

    def test_stream_events_carry_json_payloads(self) -> None:
        _, text, _ = self.read_stream()
        data_lines = [line[6:] for line in text.splitlines() if line.startswith("data: ")]
        self.assertGreater(len(data_lines), 3)
        for raw in data_lines:
            json.loads(raw)  # 每帧都必须是合法 JSON，否则前端解析会炸

    def test_percent_encoded_path_is_a_clean_404(self) -> None:
        # 非 ASCII 的 id 走百分号编码（浏览器就是这么发的）：应当得到 404，
        # 而不是让服务器抛异常断连。（顺带说明：http.client 拒绝在请求行里放原始非 ASCII 字符，
        # 那是客户端限制，不是服务端缺陷。）
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", "/api/notebooks/%E4%B8%8D%E5%AD%98%E5%9C%A8%E7%9A%84id")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 404)
            payload = json.loads(resp.read().decode("utf-8"))
            self.assertIn("error", payload)
        finally:
            conn.close()

    def test_client_disconnect_does_not_leak_threads(self) -> None:
        """客户端提前断开（关标签页 / Esc 中断）不许留下线程或句柄。

        对抗性验证时量过：50 次强制 RST 之后线程数回到基线、内存峰值 3.39 MB。
        这里留一个更小的常驻护栏，防止以后把 `Connection: close` 或异常处理改回去。
        """
        import gc
        import socket
        import threading

        def one_disconnect() -> None:
            sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
            body = json.dumps({"notebook_id": self.notebook.id,
                               "question": "BPE 是什么？"}).encode()
            sock.sendall(b"POST /api/ask HTTP/1.1\r\nHost: x\r\n"
                         b"Content-Type: application/json\r\nContent-Length: "
                         + str(len(body)).encode() + b"\r\n\r\n" + body)
            sock.recv(32)  # 只读一点就掐断
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                            b"\x01\x00\x00\x00\x00\x00\x00\x00")  # 强制 RST
            sock.close()

        baseline = threading.active_count()
        for _ in range(8):
            one_disconnect()
        deadline = time.time() + 10
        while time.time() < deadline and threading.active_count() > baseline:
            time.sleep(0.2)
        gc.collect()
        self.assertLessEqual(threading.active_count(), baseline + 1,
                             f"断连后有线程没退出（{threading.active_count()} vs 基线 {baseline}）")
        # 服务仍然健康
        status, _, _ = self.read_stream(timeout=10)
        self.assertLess(status, 10)


if __name__ == "__main__":
    unittest.main()
