"""笔记本列表的顺序必须**确定**（同秒创建也不能随机）。

由来（另一个会话实测报过来的）：验收脚本在同一秒里建两本笔记本（讲义 + PDF），
而 `list_notebooks` 原本只有 `ORDER BY created_at DESC` —— `created_at` 只精确到秒，
同秒的两本次序**不确定**，SQLite 可以任意返回。后果：`ui/self-test.html` 拿 `notebooks[0]`
当讲义笔记本时，PDF 排前面就取不到 `lecture_01.py`，`/api/sources?highlight=` 断言失败，
整个 `accept_ui.py` 退出码 1（**同一份代码两次运行，一次过、一次红**）。

那一边修的是自检不再依赖次序；这里补的是**根因**：列表本身要有确定的次序，
否则任何"按列表顺序取第一条"的调用方都踩同一个坑。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mentor.store import Store


class NotebookOrderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Path(self.tmp.name) / "order.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_newest_first_even_within_the_same_second(self) -> None:
        """连建 5 本（必然同秒），次序必须是"最后建的排最前"。"""
        store = Store(self.db)
        try:
            created = [store.create_notebook(f"笔记本 {i}").id for i in range(5)]
            rows = store.list_notebooks()
            self.assertEqual([r["id"] for r in rows], list(reversed(created)),
                             "同秒创建的笔记本次序不确定 —— 必须用 rowid 兜底排序")
        finally:
            store.close()

    def test_the_test_is_meaningful_ties_really_exist(self) -> None:
        """确认这些笔记本的 created_at 真的相同，否则上一条测的就不是"同秒"场景。"""
        store = Store(self.db)
        try:
            for i in range(4):
                store.create_notebook(f"t{i}")
            stamps = {r["created_at"] for r in store.list_notebooks()}
            self.assertEqual(len(stamps), 1, f"没造出同秒场景（{stamps}），这条测试失去意义")
        finally:
            store.close()

    def test_order_is_stable_across_repeated_reads(self) -> None:
        """同一次进程内反复读，次序不能变。"""
        store = Store(self.db)
        try:
            for i in range(3):
                store.create_notebook(f"s{i}")
            first = [r["id"] for r in store.list_notebooks()]
            for _ in range(5):
                self.assertEqual([r["id"] for r in store.list_notebooks()], first)
        finally:
            store.close()

    def test_a_fresh_store_on_the_same_file_sees_the_same_order(self) -> None:
        """换个连接重开也要一致（顺序由 ORDER BY 决定，不该依赖连接状态）。"""
        store = Store(self.db)
        try:
            for i in range(3):
                store.create_notebook(f"r{i}")
            before = [r["id"] for r in store.list_notebooks()]
        finally:
            store.close()
        again = Store(self.db)
        try:
            self.assertEqual([r["id"] for r in again.list_notebooks()], before)
        finally:
            again.close()


if __name__ == "__main__":
    unittest.main()
