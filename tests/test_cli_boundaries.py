"""CLI 边界：用户误用要给一句人话，而不是 traceback。

对抗性验证时抓到的两个：
- `--db` 指向一个**目录** → sqlite 只回 "unable to open database file"，完全看不出问题在哪；
- `serve --port 99999` → 一路炸到 socketserver 的 `OverflowError: bind(): port must be 0-65535`。

约定：用户输入错误走 `SystemExit("中文说明")`（argparse 那套），真正的内部错误照旧 traceback
——不为了"好看"把所有异常都吞掉。
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from mentor import cli
from mentor.store import Store


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv)
    except SystemExit as exc:  # 命令里的 raise SystemExit("…") 会走到这里
        code = exc.code if isinstance(exc.code, int) else 1
        if isinstance(exc.code, str):
            err.write(exc.code)
    return code, out.getvalue(), err.getvalue()


class StorePathTest(unittest.TestCase):
    def test_store_refuses_a_directory_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as ctx:
                Store(Path(tmp))
            self.assertIn("目录", str(ctx.exception))

    def test_store_creates_missing_parent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "deep" / "nested" / "m.db")
            try:
                self.assertTrue(store.path.exists())
            finally:
                store.close()


class CliBoundaryTest(unittest.TestCase):
    def setUp(self) -> None:
        # CLI 命令会在进程内保留下 SQLite 连接（真实使用时进程退出即释放），
        # 所以临时目录的清理要允许失败，否则测试会因为文件被占而报错。
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.db = self.root / "cli.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_db_pointing_at_a_directory_is_a_clean_error(self) -> None:
        code, out, err = run_cli(["--db", str(self.root), "notebooks"])
        self.assertEqual(code, 2)
        self.assertIn("目录", err + out)
        self.assertNotIn("Traceback", err + out)
        self.assertNotIn("unable to open database file", err + out)

    def test_invalid_port_is_a_clean_error(self) -> None:
        code, out, err = run_cli(["--db", str(self.db), "serve", "--port", "99999"])
        self.assertEqual(code, 1)
        message = err + out
        self.assertIn("端口", message)
        self.assertNotIn("Traceback", message)
        self.assertNotIn("OverflowError", message)

    def test_negative_port_is_rejected_too(self) -> None:
        code, out, err = run_cli(["--db", str(self.db), "serve", "--port", "-1"])
        self.assertEqual(code, 1)
        self.assertIn("端口", err + out)

    def test_forget_requires_confirmation(self) -> None:
        run_cli(["--db", str(self.db), "notebooks"])  # 先把库建出来
        code, out, err = run_cli(["--db", str(self.db), "forget"])
        self.assertEqual(code, 1)
        self.assertIn("--yes", err + out)

    def test_unknown_notebook_is_a_clean_error(self) -> None:
        code, out, err = run_cli(["--db", str(self.db), "ask", "--notebook", "nope", "问题"])
        self.assertEqual(code, 1)
        self.assertIn("找不到笔记本", err + out)
        self.assertNotIn("Traceback", err + out)

    def test_empty_database_reports_helpfully(self) -> None:
        code, out, err = run_cli(["--db", str(self.db), "notebooks"])
        self.assertEqual(code, 0)
        self.assertIn("还没有笔记本", out)

    def test_broken_config_is_visible_from_doctor(self) -> None:
        import os

        bad = self.root / "bad.json"
        bad.write_text('{"provider": "openai",}', encoding="utf-8")
        saved = os.environ.get("MENTOR_CONFIG")
        os.environ["MENTOR_CONFIG"] = str(bad)
        try:
            code, out, err = run_cli(["--db", str(self.db), "doctor"])
        finally:
            if saved is None:
                os.environ.pop("MENTOR_CONFIG", None)
            else:
                os.environ["MENTOR_CONFIG"] = saved
        message = out + err
        self.assertIn("配置文件有问题", message, message[:400])
        self.assertEqual(code, 1, "doctor 发现配置问题应当返回非零")


class SeedOptionOrderTest(unittest.TestCase):
    """通用选项写在子命令后面也要能用 —— README 与工具提示语都是那个写法。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.materials = self.root / "materials"
        self.materials.mkdir()
        for i in range(12):
            (self.materials / f"m{i}.md").write_text(f"# 材料 {i}\ntokenizer {i}\n", encoding="utf-8")
        self.db = self.root / "seed.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_options_after_subcommand_work(self) -> None:
        """`seed <paths> --db … --root … --title …`：README 就是这么写的。"""
        code, out, err = run_cli([
            "seed", str(self.materials / "m0.md"), "--db", str(self.db),
            "--root", str(self.root), "--title", "顺序测试",
        ])
        self.assertEqual(code, 0, out + err)
        self.assertIn("顺序测试", out)
        self.assertNotIn("unrecognized arguments", err + out)

    def test_options_before_subcommand_still_work(self) -> None:
        code, out, err = run_cli([
            "--db", str(self.db), "--root", str(self.root),
            "seed", str(self.materials / "m0.md"), "--title", "前置测试",
        ])
        self.assertEqual(code, 0, out + err)
        self.assertIn("前置测试", out)

    def test_subcommand_does_not_clobber_global_root(self) -> None:
        from mentor.cli import build_parser

        args = build_parser().parse_args(["--root", "X", "seed", "p"])
        self.assertEqual(args.root, "X", "子命令没给 --root 时不能把全局值冲成 None")

    def test_truncation_is_announced_by_the_cli(self) -> None:
        code, out, err = run_cli([
            "seed", str(self.materials), "--db", str(self.db),
            "--root", str(self.root), "--title", "截断测试", "--limit", "5",
        ])
        message = out + err
        self.assertEqual(code, 0, message)
        self.assertIn("⚠", message, "被上限截断必须显式告知用户")
        self.assertIn("上限", message)

    def test_limit_flag_is_accepted(self) -> None:
        from mentor.cli import build_parser

        args = build_parser().parse_args(["seed", "p", "--limit", "7"])
        self.assertEqual(args.limit, 7)


if __name__ == "__main__":
    unittest.main()
