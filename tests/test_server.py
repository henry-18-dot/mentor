"""HTTP 层测试：真的起一个本地服务，用 http.client 走接口。

不碰摄取层（材料直接写进 Store），这样测试只验证"接口与流"这一层。
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

from .test_pipeline import sample_corpus


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "materials").mkdir()
        cls.app = MentorApp(store=Store(root / "http-test.db"), root=root,
                            provider=OfflineProvider())
        cls.sources, cls.chunks = sample_corpus()
        cls.notebook = cls.app.store.create_notebook("HTTP 测试")
        cls.app.store.attach(cls.notebook.id, cls.sources, cls.chunks)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), cls.app.handler())
        cls.httpd.daemon_threads = True
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.app.store.close()
        cls.tmp.cleanup()

    def _conn(self) -> http.client.HTTPConnection:
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)

    def _get(self, path: str) -> tuple[int, dict]:
        conn = self._conn()
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        conn.close()
        return resp.status, json.loads(body) if body else {}

    def _post(self, path: str, payload: dict) -> tuple[int, dict]:
        conn = self._conn()
        conn.request("POST", path, body=json.dumps(payload), headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        conn.close()
        return resp.status, json.loads(body) if body else {}

    def test_health(self) -> None:
        status, data = self._get("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["provider"]["name"], "offline")

    def test_list_notebooks(self) -> None:
        status, data = self._get("/api/notebooks")
        self.assertEqual(status, 200)
        self.assertTrue(any(n["id"] == self.notebook.id for n in data["notebooks"]))

    def test_notebook_detail(self) -> None:
        status, data = self._get(f"/api/notebooks/{self.notebook.id}")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["sources"]), 2)
        self.assertEqual(data["stats"]["chunks"], len(self.chunks))
        self.assertNotIn("text", data["sources"][0])  # 列表不带全文
        for key, value in data["sources"][0]["meta"].items():
            # 列表页的元数据也要轻：超长字段被折叠成 <N 字符>
            self.assertLessEqual(len(str(value)), 420, f"{key} 太重了")

    def test_source_detail_has_text_and_chunks(self) -> None:
        source_id = self.app.store.notebook_sources(self.notebook.id)[0].id
        status, data = self._get(f"/api/sources/{source_id}")
        self.assertEqual(status, 200)
        self.assertIn("path", data["source"])
        self.assertTrue(data["chunks"])

    def test_search(self) -> None:
        status, data = self._get(f"/api/search?notebook_id={self.notebook.id}&q=BPE")
        self.assertEqual(status, 200)
        self.assertTrue(data["hits"])
        self.assertIn("locator", data["hits"][0])

    def test_ask_non_stream_returns_surface(self) -> None:
        status, data = self._post("/api/ask?stream=0",
                                  {"notebook_id": self.notebook.id, "question": "BPE 是什么？"})
        self.assertEqual(status, 200)
        surface = data["surface"]
        self.assertTrue(surface["widgets"])
        self.assertTrue(surface["citations"])

    def test_ask_stream_emits_sse_frames(self) -> None:
        conn = self._conn()
        conn.request("POST", "/api/ask",
                     body=json.dumps({"notebook_id": self.notebook.id,
                                      "question": "BPE 是怎么训练的？", "mode": "explore"}),
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertIn("text/event-stream", resp.getheader("Content-Type", ""))
        names: list[str] = []
        data_lines: list[str] = []
        for _ in range(400):
            line = resp.readline().decode("utf-8")
            if not line:
                break
            if line.startswith("event: "):
                names.append(line[7:].strip())
            elif line.startswith("data: "):
                data_lines.append(line[6:].strip())
            if names and names[-1] == "done":
                break
        conn.close()
        self.assertEqual(names[0], "plan")
        self.assertEqual(names[1], "retrieval")
        self.assertEqual(names[-1], "done")
        self.assertIn("widget", names)
        self.assertIn("surface", names)
        surface = json.loads(data_lines[names.index("surface")])["surface"]
        self.assertTrue(surface["id"])

    def test_turn_can_be_fetched_after_ask(self) -> None:
        _, data = self._post("/api/ask?stream=0",
                             {"notebook_id": self.notebook.id, "question": "字节分词是什么？"})
        turn_id = data["surface"]["id"]
        status, turn = self._get(f"/api/turns/{turn_id}")
        self.assertEqual(status, 200)
        self.assertEqual(turn["turn"]["question"], "字节分词是什么？")

    def test_unknown_paths_return_404(self) -> None:
        status, _ = self._get("/api/nope")
        self.assertEqual(status, 404)
        status, _ = self._get("/api/sources/src_missing")
        self.assertEqual(status, 404)

    def test_missing_notebook_returns_404(self) -> None:
        status, _ = self._get("/api/notebooks/nb_missing")
        self.assertEqual(status, 404)

    def test_static_traversal_blocked(self) -> None:
        status, _ = self._get("/../mentor/server.py")
        self.assertIn(status, (403, 404))

    def test_index_served_when_present(self) -> None:
        if not (self.app.ui / "index.html").exists():
            self.skipTest("界面还没生成（由界面任务并行产出）")
        conn = self._conn()
        conn.request("GET", "/")
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertIn("<", body)


if __name__ == "__main__":
    unittest.main()
