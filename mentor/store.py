"""SQLite 持久化：笔记本、材料、块、问答存档。

一份材料（Source）全局只存一次，通过 notebook_sources 关联到多本笔记本，
这样同一份讲义被两本笔记本引用时不会重复解析。块检索索引在内存里按需构建
并缓存，材料变更时失效。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Iterable

from .protocol import Chunk, Notebook, Source, Span, Turn, now_iso, stable_id
from .index import Index

SCHEMA = """
CREATE TABLE IF NOT EXISTS notebooks (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, title TEXT NOT NULL,
    text TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{}', added_at TEXT NOT NULL, bytes INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS notebook_sources (
    notebook_id TEXT NOT NULL, source_id TEXT NOT NULL, added_at TEXT NOT NULL,
    PRIMARY KEY (notebook_id, source_id)
);
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, ord INTEGER NOT NULL, kind TEXT NOT NULL,
    heading TEXT NOT NULL DEFAULT '', text TEXT NOT NULL, span TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_by_source ON chunks(source_id, ord);
-- 被覆盖掉的旧片段进档案：历史问答里的引用仍然能查到原文，不会悬空。
CREATE TABLE IF NOT EXISTS chunk_archive (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, ord INTEGER NOT NULL, kind TEXT NOT NULL,
    heading TEXT NOT NULL DEFAULT '', text TEXT NOT NULL, span TEXT NOT NULL,
    archived_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY, notebook_id TEXT NOT NULL, question TEXT NOT NULL,
    surface TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS turns_by_notebook ON turns(notebook_id, created_at);
"""


def default_db_path() -> Path:
    env = os.environ.get("MENTOR_DB")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[1] / "data" / "mentor.db"


class Store:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else default_db_path()
        # 路径是目录时 sqlite 只会回一句 "unable to open database file"，看不出问题在哪
        if self.path.exists() and self.path.is_dir():
            raise ValueError(f"数据库路径是一个目录（{self.path}），请给文件路径")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()
        self._index_cache: dict[str, Index] = {}

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ------------------------------------------------------------ 笔记本
    def create_notebook(self, title: str, meta: dict | None = None) -> Notebook:
        """建笔记本。同一秒、同名也要能建成功（id 撞了就加后缀重试）。"""
        title = (title or "未命名笔记本").strip()
        stamp = now_iso()
        with self._lock:
            for attempt in range(50):
                notebook = Notebook(
                    id=stable_id("nb", title, stamp, "" if attempt == 0 else str(attempt)),
                    title=title, meta=meta or {}, created_at=stamp)
                try:
                    self._db.execute(
                        "INSERT INTO notebooks(id, title, created_at, meta) VALUES(?,?,?,?)",
                        (notebook.id, notebook.title, notebook.created_at,
                         json.dumps(notebook.meta, ensure_ascii=False)),
                    )
                except sqlite3.IntegrityError:
                    continue
                self._db.commit()
                return notebook
        raise RuntimeError("无法创建笔记本：id 连续冲突")

    def list_notebooks(self) -> list[dict]:
        """按创建时间倒序列出笔记本。

        **必须带 rowid 兜底**：`created_at` 只精确到秒，同秒创建的多本（验收脚本就是这么干的）
        在只按 `created_at` 排序时次序**不确定** —— SQLite 可以任意返回。
        实测后果：验收的 PDF 笔记本排在前面时，`ui/self-test.html` 拿不到讲义笔记本，
        `/api/sources?highlight=` 断言随机失败，整个 `accept_ui.py` 退出码 1。
        """
        with self._lock:
            rows = self._db.execute(
                """SELECT n.id, n.title, n.created_at, n.meta,
                          (SELECT COUNT(*) FROM notebook_sources s WHERE s.notebook_id = n.id) AS source_count,
                          (SELECT COUNT(*) FROM turns t WHERE t.notebook_id = n.id) AS turn_count
                   FROM notebooks n ORDER BY n.created_at DESC, n.rowid DESC"""
            ).fetchall()
        return [
            {"id": r["id"], "title": r["title"], "created_at": r["created_at"],
             "meta": json.loads(r["meta"]), "source_count": r["source_count"],
             "turn_count": r["turn_count"]}
            for r in rows
        ]

    def get_notebook(self, notebook_id: str) -> Notebook | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM notebooks WHERE id=?", (notebook_id,)).fetchone()
        if not row:
            return None
        notebook = Notebook(id=row["id"], title=row["title"], created_at=row["created_at"],
                            meta=json.loads(row["meta"]))
        stats = self.notebook_stats(notebook_id)
        notebook.meta.setdefault("stats", {}).update(stats)
        return notebook

    def rename_notebook(self, notebook_id: str, title: str) -> None:
        with self._lock:
            self._db.execute("UPDATE notebooks SET title=? WHERE id=?", (title, notebook_id))
            self._db.commit()

    def delete_notebook(self, notebook_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM notebook_sources WHERE notebook_id=?", (notebook_id,))
            self._db.execute("DELETE FROM turns WHERE notebook_id=?", (notebook_id,))
            self._db.execute("DELETE FROM notebooks WHERE id=?", (notebook_id,))
            self._db.commit()
        self._index_cache.pop(notebook_id, None)

    def notebook_stats(self, notebook_id: str) -> dict:
        with self._lock:
            sources = self._db.execute(
                "SELECT COUNT(*) c FROM notebook_sources WHERE notebook_id=?", (notebook_id,)
            ).fetchone()["c"]
            chunks = self._db.execute(
                """SELECT COUNT(*) c FROM chunks WHERE source_id IN
                   (SELECT source_id FROM notebook_sources WHERE notebook_id=?)""",
                (notebook_id,),
            ).fetchone()["c"]
            turns = self._db.execute(
                "SELECT COUNT(*) c FROM turns WHERE notebook_id=?", (notebook_id,)
            ).fetchone()["c"]
            last = self._db.execute(
                "SELECT created_at FROM turns WHERE notebook_id=? ORDER BY created_at DESC LIMIT 1",
                (notebook_id,),
            ).fetchone()
        return {"sources": sources, "chunks": chunks, "turns": turns,
                "last_asked_at": last["created_at"] if last else ""}

    # -------------------------------------------------------------- 材料
    def attach(self, notebook_id: str, sources: Iterable[Source], chunks: Iterable[Chunk]) -> dict:
        """把材料与块写库并关联到笔记本。重复摄取同一路径会覆盖旧内容。"""
        sources = list(sources)
        by_source: dict[str, list[Chunk]] = {}
        for chunk in chunks:
            by_source.setdefault(chunk.source_id, []).append(chunk)
        added = 0
        touched: list[str] = []
        with self._lock:
            for source in sources:
                mine = by_source.get(source.id, [])
                self._db.execute(
                    """INSERT INTO sources(id, path, kind, title, text, meta, added_at, bytes)
                       VALUES(?,?,?,?,?,?,?,?)
                       ON CONFLICT(path) DO UPDATE SET
                         kind=excluded.kind, title=excluded.title, text=excluded.text,
                         meta=excluded.meta, bytes=excluded.bytes""",
                    (source.id, source.path, source.kind, source.title, source.text,
                     json.dumps(source.meta, ensure_ascii=False), source.added_at,
                     source.bytes or len(source.text.encode("utf-8"))),
                )
                stored_id = self._db.execute(
                    "SELECT id FROM sources WHERE path=?", (source.path,)
                ).fetchone()["id"]
                source.id = stored_id
                # 覆盖前把旧片段存进档案（同一 id 幂等），这样历史回答里的引用
                # 即使指向已被替换的块，也仍然能查到原文。
                self._db.execute(
                    """INSERT OR REPLACE INTO chunk_archive
                       (id, source_id, ord, kind, heading, text, span, archived_at)
                       SELECT id, source_id, ord, kind, heading, text, span, ?
                       FROM chunks WHERE source_id=?""",
                    (now_iso(), stored_id),
                )
                self._db.execute("DELETE FROM chunks WHERE source_id=?", (stored_id,))
                for chunk in mine:
                    chunk.source_id = stored_id
                    chunk.span.source_id = stored_id
                    self._db.execute(
                        "INSERT OR REPLACE INTO chunks(id, source_id, ord, kind, heading, text, span)"
                        " VALUES(?,?,?,?,?,?,?)",
                        (chunk.id, stored_id, chunk.order, chunk.kind, chunk.heading,
                         chunk.text, json.dumps(chunk.span.to_dict(), ensure_ascii=False)),
                    )
                cur = self._db.execute(
                    "INSERT OR IGNORE INTO notebook_sources(notebook_id, source_id, added_at)"
                    " VALUES(?,?,?)",
                    (notebook_id, stored_id, now_iso()),
                )
                added += cur.rowcount if cur.rowcount > 0 else 0
                touched.append(stored_id)
            self._db.commit()
        # 一份材料可以被多本笔记本引用，所以受影响的索引缓存要全部失效
        self._invalidate_indexes(touched)
        return {"added": added, "sources": len(sources),
                "chunks": sum(len(v) for v in by_source.values())}

    def _invalidate_indexes(self, source_ids: list[str]) -> None:
        if not source_ids:
            return
        with self._lock:
            placeholders = ",".join("?" for _ in source_ids)
            rows = self._db.execute(
                f"""SELECT DISTINCT notebook_id FROM notebook_sources
                    WHERE source_id IN ({placeholders})""",
                tuple(source_ids),
            ).fetchall()
        for row in rows:
            self._index_cache.pop(row["notebook_id"], None)

    def notebook_sources(self, notebook_id: str) -> list[Source]:
        with self._lock:
            rows = self._db.execute(
                """SELECT s.* FROM sources s JOIN notebook_sources ns ON ns.source_id = s.id
                   WHERE ns.notebook_id=? ORDER BY ns.added_at, s.path""",
                (notebook_id,),
            ).fetchall()
        return [_source_from_row(r) for r in rows]

    def get_source(self, source_id: str) -> Source | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        return _source_from_row(row) if row else None

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        """按 id 取片段：先查当前块，再查档案（历史引用靠这一步不悬空）。"""
        with self._lock:
            row = self._db.execute("SELECT * FROM chunks WHERE id=?", (chunk_id,)).fetchone()
            if row is None:
                row = self._db.execute(
                    "SELECT * FROM chunk_archive WHERE id=?", (chunk_id,)
                ).fetchone()
        return _chunk_from_row(row) if row else None

    def archived_chunk_count(self, source_id: str = "") -> int:
        with self._lock:
            if source_id:
                row = self._db.execute(
                    "SELECT COUNT(*) c FROM chunk_archive WHERE source_id=?", (source_id,)
                ).fetchone()
            else:
                row = self._db.execute("SELECT COUNT(*) c FROM chunk_archive").fetchone()
        return int(row["c"])

    def source_chunks(self, source_id: str) -> list[Chunk]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM chunks WHERE source_id=? ORDER BY ord", (source_id,)
            ).fetchall()
        return [_chunk_from_row(r) for r in rows]

    def notebook_chunks(self, notebook_id: str) -> list[Chunk]:
        with self._lock:
            rows = self._db.execute(
                """SELECT c.* FROM chunks c JOIN notebook_sources ns ON ns.source_id = c.source_id
                   WHERE ns.notebook_id=? ORDER BY c.source_id, c.ord""",
                (notebook_id,),
            ).fetchall()
        return [_chunk_from_row(r) for r in rows]

    def has_source(self, path: str) -> bool:
        with self._lock:
            row = self._db.execute("SELECT 1 FROM sources WHERE path=?", (path,)).fetchone()
        return row is not None

    # -------------------------------------------------------------- 检索
    def index(self, notebook_id: str) -> Index:
        """按笔记本构建/复用内存索引。"""
        cached = self._index_cache.get(notebook_id)
        if cached is not None:
            return cached
        chunks = self.notebook_chunks(notebook_id)
        sources = {s.id: s for s in self.notebook_sources(notebook_id)}
        built = Index(chunks, sources)
        self._index_cache[notebook_id] = built
        return built

    # -------------------------------------------------------------- 问答
    def save_turn(self, turn: Turn) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO turns(id, notebook_id, question, surface, created_at)"
                " VALUES(?,?,?,?,?)",
                (turn.id, turn.notebook_id, turn.question,
                 json.dumps(turn.surface.to_dict(), ensure_ascii=False), turn.created_at),
            )
            self._db.commit()

    def get_turn(self, turn_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM turns WHERE id=?", (turn_id,)).fetchone()
        if not row:
            return None
        return {"id": row["id"], "notebook_id": row["notebook_id"], "question": row["question"],
                "surface": json.loads(row["surface"]), "created_at": row["created_at"]}

    def list_turns(self, notebook_id: str, limit: int = 30) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, question, created_at FROM turns WHERE notebook_id=?"
                " ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (notebook_id, limit),
            ).fetchall()
        return [{"id": r["id"], "question": r["question"], "created_at": r["created_at"]} for r in rows]

    def list_turn_payloads(self, notebook_id: str, limit: int = 20) -> list[dict]:
        """最近的 N 轮**完整**问答，按时间正序返回（导出成学习指南用）。

        先按时间倒序取最近 N 条（同一秒内用 rowid 兜底），再翻转成正序——
        这样"最近 N 轮"与"阅读顺序"都能满足。
        """
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM turns WHERE notebook_id=?"
                " ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (notebook_id, max(1, limit)),
            ).fetchall()
        payloads = [
            {"id": r["id"], "notebook_id": r["notebook_id"], "question": r["question"],
             "surface": json.loads(r["surface"]), "created_at": r["created_at"]}
            for r in rows
        ]
        payloads.reverse()
        return payloads

    def forget(self) -> None:
        """清空全部数据（材料、块、问答）。用于重新建库。"""
        with self._lock:
            for table in ("turns", "chunks", "chunk_archive", "notebook_sources", "sources", "notebooks"):
                self._db.execute(f"DELETE FROM {table}")
            self._db.commit()
        self._index_cache.clear()


def _source_from_row(row: sqlite3.Row) -> Source:
    return Source(id=row["id"], path=row["path"], kind=row["kind"], title=row["title"],
                  text=row["text"], meta=json.loads(row["meta"]), added_at=row["added_at"],
                  bytes=row["bytes"])


def _chunk_from_row(row: sqlite3.Row) -> Chunk:
    span = json.loads(row["span"])
    return Chunk(id=row["id"], source_id=row["source_id"], text=row["text"],
                 heading=row["heading"], kind=row["kind"], order=row["ord"],
                 span=Span(start=span["start"], end=span["end"], line=span.get("line", 0),
                           section=span.get("section", ""), source_id=span.get("source_id", "")))
