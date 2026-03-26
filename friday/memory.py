"""Session memory and task log for FRIDAY.

Two storage layers:
  1. In-RAM episodic window (recent turns, truncated to session_window).
  2. SQLite task log for persistence / crash recovery.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import get_config


@dataclass
class Turn:
    role: str           # "user" | "assistant" | "tool"
    content: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TaskRecord:
    task_id: str
    query: str
    route: str
    status: str          # "pending" | "running" | "done" | "failed"
    result: str = ""
    error: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)


class MemoryStore:
    """Combined in-RAM session window + persistent SQLite task log."""

    _CREATE_TASKS = """
        CREATE TABLE IF NOT EXISTS tasks (
            task_id    TEXT PRIMARY KEY,
            query      TEXT,
            route      TEXT,
            status     TEXT,
            result     TEXT,
            error      TEXT,
            created_at REAL,
            updated_at REAL,
            metadata   TEXT
        )
    """

    def __init__(self, db_path: str | None = None, session_window: int | None = None):
        cfg = get_config()
        mem_cfg = cfg.get("memory", {})
        _path = db_path or mem_cfg.get("db_path", "~/.friday/memory.db")
        resolved = pathlib.Path(_path).expanduser()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = str(resolved)
        self._window = session_window or mem_cfg.get("session_window", 20)
        self._session: deque[Turn] = deque(maxlen=self._window)
        self._init_db()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self._db_path, timeout=10, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(self._CREATE_TASKS)

    # ------------------------------------------------------------------
    # Session window (in-RAM)
    # ------------------------------------------------------------------

    def add_turn(self, role: str, content: str, metadata: dict | None = None) -> Turn:
        turn = Turn(role=role, content=content, metadata=metadata or {})
        self._session.append(turn)
        return turn

    def get_session_context(self) -> list[dict]:
        return [t.to_dict() for t in self._session]

    def clear_session(self) -> None:
        self._session.clear()

    # ------------------------------------------------------------------
    # Task log (SQLite)
    # ------------------------------------------------------------------

    def save_task(self, record: TaskRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO tasks
                  (task_id, query, route, status, result, error,
                   created_at, updated_at, metadata)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.task_id,
                    record.query,
                    record.route,
                    record.status,
                    record.result,
                    record.error,
                    record.created_at,
                    record.updated_at,
                    json.dumps(record.metadata),
                ),
            )

    def update_task(
        self,
        task_id: str,
        status: str,
        result: str = "",
        error: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE tasks
                   SET status=?, result=?, error=?, updated_at=?
                 WHERE task_id=?
                """,
                (status, result, error, time.time(), task_id),
            )

    def get_task(self, task_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE task_id=?", (task_id,)
            ).fetchone()
        if row is None:
            return None
        cols = [
            "task_id", "query", "route", "status", "result", "error",
            "created_at", "updated_at", "metadata",
        ]
        d = dict(zip(cols, row))
        d["metadata"] = json.loads(d["metadata"]) if d["metadata"] else {}
        return d

    def recent_tasks(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        cols = [
            "task_id", "query", "route", "status", "result", "error",
            "created_at", "updated_at", "metadata",
        ]
        result = []
        for row in rows:
            d = dict(zip(cols, row))
            d["metadata"] = json.loads(d["metadata"]) if d["metadata"] else {}
            result.append(d)
        return result
