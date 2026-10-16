"""把用户给的目标（文件 / 目录 / 通配符）展开成一份要摄取的路径清单。

为什么单独一个模块：CLI、HTTP 接口、示例脚本都要同一套规则，而且默认
忽略列表必须一致——否则一次"导入整个仓库"会把 cache、模型权重、虚拟环境
全吃进去。
"""

from __future__ import annotations

import fnmatch
import glob
from pathlib import Path
from typing import Iterable, NamedTuple

# 这些目录一律不扫：构建产物、缓存、依赖、模型权重、课程数据。
DEFAULT_IGNORES = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", "envs",
    "cache", "caches", "runs", "logs", "models", "data", ".ipynb_checkpoints",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build", ".next",
    # 依赖源码：实测过不带 site-packages 时，脏目录里 80 个依赖文件会被当课程材料吃进来
    "site-packages", "__pypackages__", ".eggs", "bower_components",
    # 工具缓存 / 构建目录
    ".tox", ".nox", ".cache", ".sass-cache", ".terraform", ".gradle", ".m2",
}

# 值得当材料看的后缀。其余后缀即便显式给出也会被 ingest 层记成 skipped。
TEXT_SUFFIXES = {
    ".py", ".md", ".markdown", ".txt", ".rst", ".ipynb", ".html", ".htm",
    ".json", ".yaml", ".yml", ".toml", ".tex", ".csv", ".sh", ".ps1", ".c", ".h",
    ".cpp", ".cu", ".rs", ".go", ".java", ".js", ".ts", ".css", ".sql", ".mmd",
}
BINARY_SUFFIXES = {".pdf", ".docx", ".pptx"}
ALLOWED_SUFFIXES = TEXT_SUFFIXES | BINARY_SUFFIXES

MAX_FILE_BYTES = 20 * 1024 * 1024
DEFAULT_LIMIT = 2000


class Expansion(NamedTuple):
    """展开结果 + 是否被上限截断。

    `truncated` 必须能被调用方看到：以前 `expand()` 直接 `return found[:limit]`，
    5000 份材料的目录会安静地只导入 2000 份，用户永远不知道少了什么。
    """

    paths: list[Path]
    limit: int
    truncated: bool

    def warning(self) -> str:
        if not self.truncated:
            return ""
        return (f"材料数量超过上限 {self.limit}，只取前 {self.limit} 份；"
                f"请用 --paths 缩小范围，或提高上限后重试")


def expand_report(entries: Iterable[str], *, root: str | Path | None = None,
                  ignores: set[str] | None = None, limit: int = DEFAULT_LIMIT) -> Expansion:
    """展开目标，并如实报告有没有被上限截断。

    目录递归、通配符支持 `**`、相对路径相对 root 解析。
    显式给出的**文件**不受忽略表影响（用户点名要的就得给），只有目录递归才应用忽略表。
    """
    base = Path(root).resolve() if root else Path.cwd()
    skip = set(ignores or DEFAULT_IGNORES)
    found: list[Path] = []
    seen: set[Path] = set()
    state = {"truncated": False}

    def add(path: Path) -> None:
        resolved = path.resolve()
        if resolved in seen or not resolved.is_file():
            return
        if len(found) >= limit:
            # 已经满了还遇到合条件的文件：这就是"被截断"的确证（不多扫，见到第一个就定论）
            state["truncated"] = True
            return
        seen.add(resolved)
        found.append(resolved)

    for entry in entries:
        raw = str(entry).strip()
        if not raw:
            continue
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = base / candidate
        text = str(candidate)
        if any(ch in text for ch in "*?["):
            for match in sorted(glob.glob(text, recursive=True)):
                _collect(Path(match), skip, add, found, limit, state)
                if state["truncated"]:
                    break
        elif candidate.is_dir():
            _collect(candidate, skip, add, found, limit, state)
        else:
            add(candidate)
        if state["truncated"]:
            break
    return Expansion(found, limit, state["truncated"])


def expand(entries: Iterable[str], *, root: str | Path | None = None,
           ignores: set[str] | None = None, limit: int = DEFAULT_LIMIT) -> list[Path]:
    """兼容入口：只要路径清单。需要"是否被截断"就用 `expand_report()`。"""
    return expand_report(entries, root=root, ignores=ignores, limit=limit).paths


def _collect(candidate: Path, skip: set[str], add, found: list[Path], limit: int,
             state: dict[str, bool]) -> None:
    # 一旦确证"还有更多材料"，就不用再扫了
    if state["truncated"]:
        return
    if candidate.is_dir():
        if candidate.name in skip or candidate.name.startswith("."):
            return
        try:
            children = sorted(candidate.iterdir())
        except OSError:
            return
        for child in children:
            _collect(child, skip, add, found, limit, state)
            if state["truncated"]:
                return
        return
    if candidate.suffix.lower() in ALLOWED_SUFFIXES:
        add(candidate)


def default_seed_targets(root: str | Path) -> list[str]:
    """示例笔记本的默认材料：课程讲义 + 中文说明。"""
    base = Path(root)
    patterns = [
        "lectures/lecture_0[1-4].py",
        "lectures/lecture_*.pdf",
        "docs/*.md",
        "开始.md",
    ]
    out: list[str] = []
    for pattern in patterns:
        hits = sorted(glob.glob(str(base / pattern)))
        out.extend(hits)
    return out or [str(base)]


def is_too_big(path: Path) -> bool:
    try:
        return path.stat().st_size > MAX_FILE_BYTES
    except OSError:
        return True


def matches_any(name: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)
