"""Tests for AuditLogger and MemoryStore."""

import time
import pytest

from friday.audit import AuditEntry, AuditLogger
from friday.memory import MemoryStore, TaskRecord


# ---------------------------------------------------------------------------
# AuditLogger
# ---------------------------------------------------------------------------


class TestAuditLogger:
    @pytest.fixture
    def logger(self, tmp_path):
        return AuditLogger(db_path=str(tmp_path / "audit.db"))

    def test_log_and_retrieve(self, logger):
        entry = AuditEntry(
            event_type="tool_call",
            task_id="task-1",
            route="tools",
            tool_name="file_tool",
            input_hash="abc",
            output_hash="def",
            duration_ms=42.0,
        )
        logger.log(entry)
        entries = logger.get_entries(task_id="task-1")
        assert len(entries) == 1
        assert entries[0]["tool_name"] == "file_tool"
        assert entries[0]["duration_ms"] == 42.0

    def test_log_tool_call(self, logger):
        entry = logger.log_tool_call(
            task_id="task-2",
            tool_name="excel_tool",
            inputs={"action": "read_sheet", "path": "/tmp/x.xlsx"},
            outputs={"rows": []},
            duration_ms=100.0,
        )
        assert entry.event_type == "tool_call"
        assert entry.input_hash  # non-empty hash
        assert entry.output_hash

    def test_log_route(self, logger):
        entry = logger.log_route(
            task_id="task-3",
            route="local_llm",
            reason="default",
            sensitivity=0,
        )
        entries = logger.get_entries(task_id="task-3")
        assert entries[0]["event_type"] == "route"
        assert entries[0]["route"] == "local_llm"

    def test_get_entries_limit(self, logger):
        for i in range(5):
            logger.log_route(task_id=f"task-{i}", route="tools")
        entries = logger.get_entries(limit=3)
        assert len(entries) == 3

    def test_input_hash_is_deterministic(self, logger):
        e1 = logger.log_tool_call("t", "file_tool", {"key": "val"}, {}, 1.0)
        e2 = logger.log_tool_call("t", "file_tool", {"key": "val"}, {}, 1.0)
        assert e1.input_hash == e2.input_hash

    def test_different_inputs_different_hash(self, logger):
        e1 = logger.log_tool_call("t", "file_tool", {"key": "val1"}, {}, 1.0)
        e2 = logger.log_tool_call("t", "file_tool", {"key": "val2"}, {}, 1.0)
        assert e1.input_hash != e2.input_hash


# ---------------------------------------------------------------------------
# MemoryStore
# ---------------------------------------------------------------------------


class TestMemoryStore:
    @pytest.fixture
    def memory(self, tmp_path):
        return MemoryStore(
            db_path=str(tmp_path / "memory.db"),
            session_window=5,
        )

    def test_add_and_retrieve_session(self, memory):
        memory.add_turn("user", "hello")
        memory.add_turn("assistant", "hi there")
        ctx = memory.get_session_context()
        assert len(ctx) == 2
        assert ctx[0]["role"] == "user"
        assert ctx[1]["content"] == "hi there"

    def test_session_window_truncates(self, memory):
        for i in range(7):
            memory.add_turn("user", f"msg {i}")
        ctx = memory.get_session_context()
        assert len(ctx) == 5  # window=5

    def test_clear_session(self, memory):
        memory.add_turn("user", "hello")
        memory.clear_session()
        assert memory.get_session_context() == []

    def test_save_and_get_task(self, memory):
        record = TaskRecord(
            task_id="t-1",
            query="list my files",
            route="tools",
            status="pending",
        )
        memory.save_task(record)
        fetched = memory.get_task("t-1")
        assert fetched is not None
        assert fetched["query"] == "list my files"
        assert fetched["status"] == "pending"

    def test_update_task_status(self, memory):
        record = TaskRecord(
            task_id="t-2",
            query="summarise",
            route="local_llm",
            status="running",
        )
        memory.save_task(record)
        memory.update_task("t-2", "done", result="summary text")
        fetched = memory.get_task("t-2")
        assert fetched["status"] == "done"
        assert fetched["result"] == "summary text"

    def test_get_nonexistent_task(self, memory):
        assert memory.get_task("no-such-id") is None

    def test_recent_tasks_ordering(self, memory):
        for i in range(3):
            record = TaskRecord(
                task_id=f"t-{i}",
                query=f"query {i}",
                route="tools",
                status="done",
            )
            memory.save_task(record)
            time.sleep(0.01)
        tasks = memory.recent_tasks(10)
        # Most recent first
        assert tasks[0]["task_id"] == "t-2"
