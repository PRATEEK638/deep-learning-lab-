"""Audit logger for FRIDAY.

Every tool invocation and routing decision is stored in a local
SQLite database with a unique trace_id.  No raw payloads are
stored – only SHA-256 hashes to protect privacy.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from .config import get_config


@dataclass
class AuditEntry:
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    event_type: str = ""       # "route", "tool_call", "tool_result", "policy_check"
    task_id: str = ""
    route: str = ""            # "tools" | "local_llm" | "cloud_llm"
    tool_name: str = ""
    input_hash: str = ""       # SHA-256 of serialised input
    output_hash: str = ""      # SHA-256 of serialised output
    duration_ms: float = 0.0
    error: str = ""
    metadata: dict = field(default_factory=dict)

    def to_row(self) -> tuple:
        return (
            self.trace_id,
            self.timestamp,
            self.event_type,
            self.task_id,
            self.route,
            self.tool_name,
            self.input_hash,
            self.output_hash,
            self.duration_ms,
            self.error,
            json.dumps(self.metadata),
        )


def _sha256(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


class AuditLogger:
    """Thread-safe SQLite audit logger."""

    _CREATE_TABLE = """
        CREATE TABLE IF NOT EXISTS audit_log (
            trace_id     TEXT PRIMARY KEY,
            timestamp    REAL NOT NULL,
            event_type   TEXT,
            task_id      TEXT,
            route        TEXT,
            tool_name    TEXT,
            input_hash   TEXT,
            output_hash  TEXT,
            duration_ms  REAL,
            error        TEXT,
            metadata     TEXT
        )
    """

    def __init__(self, db_path: str | None = None):
        cfg = get_config()
        _path = db_path or cfg.get("audit", {}).get("db_path", "~/.friday/audit.db")
        resolved = pathlib.Path(_path).expanduser()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = str(resolved)
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(self._CREATE_TABLE)

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

    def log(self, entry: AuditEntry) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO audit_log
                  (trace_id, timestamp, event_type, task_id, route,
                   tool_name, input_hash, output_hash, duration_ms, error, metadata)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                entry.to_row(),
            )

    def log_tool_call(
        self,
        task_id: str,
        tool_name: str,
        inputs: Any,
        outputs: Any,
        duration_ms: float,
        error: str = "",
        route: str = "tools",
    ) -> AuditEntry:
        entry = AuditEntry(
            event_type="tool_call",
            task_id=task_id,
            route=route,
            tool_name=tool_name,
            input_hash=_sha256(inputs),
            output_hash=_sha256(outputs),
            duration_ms=duration_ms,
            error=error,
        )
        self.log(entry)
        return entry

    def log_route(
        self,
        task_id: str,
        route: str,
        reason: str = "",
        sensitivity: int = 0,
    ) -> AuditEntry:
        entry = AuditEntry(
            event_type="route",
            task_id=task_id,
            route=route,
            metadata={"reason": reason, "sensitivity": sensitivity},
        )
        self.log(entry)
        return entry

    def get_entries(self, task_id: str | None = None, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            if task_id:
                rows = conn.execute(
                    "SELECT * FROM audit_log WHERE task_id=? ORDER BY timestamp DESC LIMIT ?",
                    (task_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                ).fetchall()

        cols = [
            "trace_id", "timestamp", "event_type", "task_id", "route",
            "tool_name", "input_hash", "output_hash", "duration_ms", "error", "metadata",
        ]
        result = []
        for row in rows:
            d = dict(zip(cols, row))
            d["metadata"] = json.loads(d["metadata"]) if d["metadata"] else {}
            result.append(d)
        return result
