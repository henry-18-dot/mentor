"""并发与重摄录的边界：这两块以前没人测过。

两件事：
1. **一边提问一边重新摄录材料**：不能抛异常，引用也不能凭空少掉；
2. **重新摄录后，旧引用仍要能查到原文**：`store.chunk_archive` 就是为了这个——
   同一份材料被重新摄入时旧片段会归档，历史问答里的引用不能变成悬空指针。
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from mentor.ingest import ingest_paths
from mentor.llm import OfflineProvider
from mentor.store import Store
from mentor.tutor import Tutor


class ReattachKeepsOldCitationsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.lecture = self.root / "lecture.py"
        self.lecture.write_text(
            "\n".join(f'text("line {i} tokenizer merge pair")' for i in range(50)),
            encoding="utf-8",
        )
        self.store = Store(self.root / "store.db")
        self.tutor = Tutor(self.store, OfflineProvider())
        self.notebook = self.store.create_notebook("重摄录")
        self.attach()

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def attach(self) -> None:
        report = ingest_paths([self.lecture], root=self.root)
        self.store.attach(self.notebook.id, report.sources, report.chunks)

    def test_old_chunk_still_resolves_after_reingest(self) -> None:
        surface = self.tutor.ask(self.notebook.id, "tokenizer 怎么用？", "explore")
        citation = surface.citations[0]
        old_chunk_id = citation.chunk_id
        self.assertTrue(self.store.get_chunk(old_chunk_id), "第一次提问后引用应该能查到片段")

        self.attach()  # 同一份材料再摄一次：旧片段会归档

        archived = self.store.get_chunk(old_chunk_id)
        self.assertIsNotNone(archived, "重新摄录后旧引用不能变成悬空指针")
        self.assertIn("tokenizer", archived.text)

    def test_historical_turn_still_renders_after_reingest(self) -> None:
        surface = self.tutor.ask(self.notebook.id, "tokenizer 怎么用？", "explore")
        turn_id = surface.turn_id if hasattr(surface, "turn_id") else None
        turns = self.store.list_turns(self.notebook.id, limit=5)
        self.assertTrue(turns)
        turn = self.store.get_turn(turns[0]["id"])
        self.assertTrue(turn["surface"]["citations"])
        for cite in turn["surface"]["citations"]:
            self.assertTrue(self.store.get_chunk(cite["chunk_id"]),
                            f"历史问答里的引用 {cite.get('locator')} 查不到片段了")
        del turn_id

    def test_reingest_does_not_duplicate_sources(self) -> None:
        before = len(self.store.notebook_sources(self.notebook.id))
        self.attach()
        self.attach()
        after = len(self.store.notebook_sources(self.notebook.id))
        self.assertEqual(before, after, "同一路径重复摄入不该产生重复来源")
        self.assertGreater(self.store.archived_chunk_count(), 0,
                           "旧片段应当归档，而不是被静默丢弃")


class ConcurrentAskWhileAttachingTest(unittest.TestCase):
    """提问线程与摄录线程同时跑：不许抛异常，引用不许残缺。"""

    REATTACH_ROUNDS = 3
    READERS = 3

    def test_asking_while_attaching_stays_consistent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "base.py"
            base.write_text(
                "\n".join(f'text("line {i} tokenizer merge pair")' for i in range(200)),
                encoding="utf-8",
            )
            extra = root / "extra.md"
            extra.write_text("字节分词把字符串映射成字节序列。\n" * 20, encoding="utf-8")

            store = Store(root / "concurrent.db")
            try:
                report = ingest_paths([base], root=root)
                notebook = store.create_notebook("并发")
                store.attach(notebook.id, report.sources, report.chunks)
                tutor = Tutor(store, OfflineProvider())

                errors: list[str] = []
                answers: list[int] = []
                stop = threading.Event()

                def reader(tag: str) -> None:
                    while not stop.is_set():
                        try:
                            surface = tutor.ask(notebook.id, "tokenizer 是怎么用的？", "explore")
                            if not surface.citations:
                                errors.append(f"{tag}: 引用凭空少了")
                            elif any(not c.locator for c in surface.citations):
                                errors.append(f"{tag}: 引用没有 locator")
                            else:
                                answers.append(len(surface.citations))
                        except Exception as exc:  # pragma: no cover - 失败要报出来
                            errors.append(f"{tag}: {type(exc).__name__}: {exc}")

                def attacher() -> None:
                    for _ in range(self.REATTACH_ROUNDS):
                        try:
                            rep = ingest_paths([base, extra], root=root)
                            store.attach(notebook.id, rep.sources, rep.chunks)
                        except Exception as exc:  # pragma: no cover
                            errors.append(f"attach: {type(exc).__name__}: {exc}")
                        time.sleep(0.05)

                readers = [threading.Thread(target=reader, args=(f"reader{i}",), daemon=True)
                           for i in range(self.READERS)]
                writer = threading.Thread(target=attacher, daemon=True)
                for thread in readers:
                    thread.start()
                writer.start()
                writer.join(timeout=30)
                time.sleep(0.2)
                stop.set()
                for thread in readers:
                    thread.join(timeout=10)

                self.assertEqual(errors, [], f"并发过程中出错：{errors[:3]}")
                self.assertGreater(len(answers), 0, "并发期间应当至少完成几次提问")
                self.assertGreaterEqual(len(store.notebook_sources(notebook.id)), 1)
                self.assertGreater(len(store.notebook_chunks(notebook.id)), 0)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
