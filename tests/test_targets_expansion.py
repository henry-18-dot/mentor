"""材料展开的两条硬规则：**别把依赖当材料**、**截断了要说出来**。

对抗性验证时在"脏目录"（真材料 120 份 + node_modules/.venv/cache/__pycache__/.git/site-packages）
上量出来的两件事：

1. `site-packages` 不在忽略表里 → `expand()` 返回 **200** 份，多出的 80 份全是依赖源码；
2. `limit=2000` 是**静默截断**：2500 份材料的目录只返回 2000 份，调用方拿不到任何信号。
   用户会以为"全都导进来了"。
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from mentor.targets import DEFAULT_LIMIT, Expansion, expand, expand_report


class DirtyDirectoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name) / "dirty"
        self.root.mkdir(parents=True)
        for i in range(30):
            folder = self.root / ("notes" if i % 2 else "src")
            folder.mkdir(exist_ok=True)
            (folder / f"real_{i}.md").write_text(f"# 材料 {i}\ntokenizer {i}\n", encoding="utf-8")
        # 各种"不该当材料"的东西
        for sub, count, suffix in (("node_modules/pkg", 40, ".py"),
                                   (".venv/lib", 30, ".py"),
                                   ("cache/uv", 20, ".txt"),
                                   ("__pycache__", 15, ".py"),
                                   (".git/objects", 10, ".py"),
                                   ("site-packages/lib", 25, ".py"),
                                   ("__pypackages__/lib", 10, ".py")):
            folder = self.root / sub
            folder.mkdir(parents=True, exist_ok=True)
            for i in range(count):
                (folder / f"junk_{i}{suffix}").write_text("x", encoding="utf-8")
        for i in range(10):  # 不该当材料的二进制
            (self.root / f"w{i}.bin").write_bytes(b"\x00" * 8)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_only_real_materials_are_collected(self) -> None:
        hits = expand([str(self.root)])
        self.assertEqual(len(hits), 30, f"只该收下 30 份真材料，实际 {len(hits)}")
        self.assertTrue(all("real_" in p.name for p in hits), [p.name for p in hits[:5]])

    def test_dependency_directories_are_ignored(self) -> None:
        hits = expand([str(self.root)])
        joined = " ".join(str(p) for p in hits)
        for junk in ("site-packages", "__pypackages__", "node_modules", ".venv", "__pycache__"):
            self.assertNotIn(junk, joined, f"{junk} 里的文件不该被当材料")

    def test_explicit_file_wins_over_ignore_list(self) -> None:
        """用户点名要的文件就得给，哪怕它躺在 site-packages 里。"""
        target = self.root / "site-packages" / "lib" / "junk_1.py"
        self.assertEqual(expand([str(target)]), [target.resolve()])


class TruncationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name) / "many"
        self.root.mkdir(parents=True)
        for i in range(12):
            (self.root / f"m{i}.md").write_text(f"材料 {i}", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_not_truncated_when_under_the_limit(self) -> None:
        report = expand_report([str(self.root)], limit=20)
        self.assertIsInstance(report, Expansion)
        self.assertEqual(len(report.paths), 12)
        self.assertFalse(report.truncated)
        self.assertEqual(report.warning(), "")

    def test_truncation_is_reported(self) -> None:
        report = expand_report([str(self.root)], limit=5)
        self.assertEqual(len(report.paths), 5)
        self.assertTrue(report.truncated, "超过上限必须如实报告")
        self.assertIn("上限", report.warning())
        self.assertIn("--paths", report.warning())

    def test_exactly_at_the_limit_is_not_truncated(self) -> None:
        """正好等于上限时不该误报（多扫一个文件就能确定）。"""
        report = expand_report([str(self.root)], limit=12)
        self.assertEqual(len(report.paths), 12)
        self.assertFalse(report.truncated, "正好 12 份不该说被截断")

    def test_default_limit_is_documented(self) -> None:
        self.assertEqual(DEFAULT_LIMIT, 2000)

    def test_legacy_expand_still_returns_a_list(self) -> None:
        self.assertIsInstance(expand([str(self.root)]), list)


if __name__ == "__main__":
    unittest.main()
