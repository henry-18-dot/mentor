"""静态文件服务的安全边界：路径穿越、NUL 字节、越界绝对路径。

这块以前没人测过。对抗性验证的结论：
- 各种 `..`（含 `%2e%2e`、`%5c` 反斜杠变体）与 `C:/Windows/...` 都被 `relative_to` 挡住 → 403；
- **但** `/%00`、`/index.html%00.png` 这种含 NUL 的路径曾经返回 **500**（`ValueError: embedded null
  character` 被兜底 except 当成内部故障）——那是非法请求，应当是 400。
"""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from mentor.llm import OfflineProvider
from mentor.server import MentorApp
from mentor.store import Store


class StaticSafetyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.store = Store(root / "static.db")
        cls.app = MentorApp(store=cls.store, root=root, provider=OfflineProvider())
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

    def get(self, raw_path: str) -> tuple[int, bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", raw_path)
            resp = conn.getresponse()
            return resp.status, resp.read(400)
        finally:
            conn.close()

    # ---------------------------------------------------------------- 正常路径

    def test_index_and_assets_are_served(self) -> None:
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"<!DOCTYPE html>", body)
        status, body = self.get("/app.js")
        self.assertEqual(status, 200)
        self.assertTrue(body)

    def test_extensionless_entry_falls_back_to_html(self) -> None:
        status, _ = self.get("/self-test")
        self.assertEqual(status, 200)

    def test_unknown_page_is_404(self) -> None:
        status, body = self.get("/definitely-not-here")
        self.assertEqual(status, 404)
        self.assertIn("error", json.loads(body.decode("utf-8")))

    # ---------------------------------------------------------------- 越界

    def test_parent_traversal_is_forbidden(self) -> None:
        for path in ("/../pyproject.toml", "/../../README.md", "/../mentor/server.py",
                     "/..%2f..%2fpyproject.toml", "/%2e%2e/%2e%2e/README.md",
                     "/..%5c..%5cREADME.md", "/%2e%2e%5c%2e%2e%5cREADME.md"):
            with self.subTest(path=path):
                status, body = self.get(path)
                self.assertEqual(status, 403, f"{path} 应当越界被拒")
                self.assertIn("越界", json.loads(body.decode("utf-8"))["error"])

    def test_absolute_windows_path_is_forbidden(self) -> None:
        for path in ("/C:/Windows/win.ini", "/C:%5cWindows%5cwin.ini"):
            with self.subTest(path=path):
                status, _ = self.get(path)
                self.assertEqual(status, 403)

    # ---------------------------------------------------------------- 非法输入

    def test_null_byte_path_is_a_client_error(self) -> None:
        """含 NUL 的路径是非法请求（400），不能变成 500 内部故障。"""
        for path in ("/%00", "/index.html%00.png", "/a%00b.js"):
            with self.subTest(path=path):
                status, body = self.get(path)
                self.assertEqual(status, 400, f"{path} 应当是 400 而不是 {status}")
                self.assertNotIn("Traceback", body.decode("utf-8", "replace"))

    def test_server_still_healthy_after_bad_requests(self) -> None:
        for path in ("/%00", "/../pyproject.toml", "/definitely-not-here"):
            self.get(path)
        status, _ = self.get("/api/health")
        self.assertEqual(status, 200, "被非法请求打过之后服务仍应正常")


if __name__ == "__main__":
    unittest.main()
