"""变异检查（mutation check）：证明某条回归测试**真的抓得住**它要守的 bug。

用法（默认跑"笔记本排序守卫"这一条）：

    python scripts/mutation_check.py
    python scripts/mutation_check.py --file mentor/store.py \
        --old "ORDER BY n.created_at DESC, n.rowid DESC" --new "ORDER BY n.created_at DESC" \
        --test tests.test_notebook_order --label "笔记本同秒排序"

为什么要这个脚本：
- 一条回归测试写着"必须用 rowid 兜底"，但**没人证明过去掉兜底它会红**。没红过的守卫等于没有。
- 2026-10-17 我第一次做这件事时，是**直接在实况树上把代码改坏再还原**——
  另一个会话正在同一棵树上跑验收，那个约 4 秒的窗口差点污染它的证据。
  用户/协作方的要求很具体：**别在实况树上临时破坏**。

做法：把 `mentor/` 与 `tests/` 复制到临时目录，在**副本**里做变异，在副本里跑测试；
主树只读，脚本前后用 SHA256 自证主树未被改动。

判定：变异后测试**必须失败**（= 变异被杀死）。若仍通过，说明守卫是虚的，退出码 1。
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def tree_hash(root: Path) -> str:
    """对 mentor/ 与 tests/ 下所有文件内容取一个汇总哈希（用于自证主树未被改动）。"""
    digest = hashlib.sha256()
    for folder in ("mentor", "tests"):
        base = root / folder
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                digest.update(str(path.relative_to(root)).encode("utf-8"))
                digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="在临时副本上做变异检查，证明回归测试抓得住 bug")
    parser.add_argument("--file", default="mentor/store.py", help="要变异的文件（相对项目根）")
    parser.add_argument("--old", default="ORDER BY n.created_at DESC, n.rowid DESC",
                        help="被替换掉的原文（必须是唯一出现）")
    parser.add_argument("--new", default="ORDER BY n.created_at DESC", help="替换成什么（= 制造 bug）")
    parser.add_argument("--test", default="tests.test_notebook_order", help="要跑的测试模块")
    parser.add_argument("--label", default="笔记本同秒排序", help="这条守卫的名字")
    args = parser.parse_args()

    before = tree_hash(PROJECT)
    target = PROJECT / args.file
    source = target.read_text(encoding="utf-8")
    hits = source.count(args.old)
    if hits != 1:
        print(f"✗ {args.file} 里 {args.old!r} 出现 {hits} 次（要求恰好 1 次），无法做定点变异")
        return 2

    tmpdir = Path(tempfile.mkdtemp(prefix="mentor-mutation-"))
    try:
        for folder in ("mentor", "tests"):
            shutil.copytree(PROJECT / folder, tmpdir / folder,
                            ignore=shutil.ignore_patterns("__pycache__"))
        mutated = tmpdir / args.file
        mutated.write_text(source.replace(args.old, args.new, 1), encoding="utf-8")

        proc = subprocess.run([sys.executable, "-m", "unittest", args.test],
                              cwd=str(tmpdir), capture_output=True, text=True, encoding="utf-8")
        output = (proc.stdout or "") + (proc.stderr or "")
        killed = proc.returncode != 0 and "FAILED" in output
        last = [line for line in output.splitlines() if line.strip()][-1:] or ["(无输出)"]

        print(f"变异：{args.file} 去掉 {args.old!r}")
        print(f"副本跑 {args.test} → 退出码 {proc.returncode}｜{'FAILED' if 'FAILED' in output else '未失败'}")
        print(f"  尾部：{last[0].strip()}")
        print(f"结论：守卫「{args.label}」{'确实抓得住这个 bug ✓' if killed else '抓不住 —— 这条回归测试是虚的 ✗'}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    after = tree_hash(PROJECT)
    print(f"主树未被改动：{'是 ✓' if before == after else '否 ✗（哈希变化，请检查）'}"
          f"  sha256={after[:12]}")
    return 0 if (killed and before == after) else 1


if __name__ == "__main__":
    raise SystemExit(main())
