"""Tests for the Router."""

import pytest
from friday.audit import AuditLogger
from friday.policy import PolicyEngine
from friday.router import Router


@pytest.fixture
def router(tmp_path):
    """Router with cloud disabled (default) and in-memory audit DB."""
    cfg = {
        "policy": {
            "cloud_enabled": False,
            "blocked_directories": [],
            "blocked_extensions": [],
            "require_confirmation_on_delete": True,
        },
        "audit": {"db_path": str(tmp_path / "audit.db")},
    }
    policy = PolicyEngine(cfg)
    audit = AuditLogger(db_path=str(tmp_path / "audit.db"))
    return Router(policy=policy, audit=audit, config=cfg)


@pytest.fixture
def cloud_router(tmp_path):
    """Router with cloud enabled."""
    cfg = {
        "policy": {
            "cloud_enabled": True,
            "blocked_directories": [],
            "blocked_extensions": [],
            "require_confirmation_on_delete": True,
        },
        "audit": {"db_path": str(tmp_path / "audit2.db")},
    }
    policy = PolicyEngine(cfg)
    audit = AuditLogger(db_path=str(tmp_path / "audit2.db"))
    return Router(policy=policy, audit=audit, config=cfg)


class TestRouterRules:
    def test_local_path_routes_to_tools(self, router):
        decision = router.route("Show me the files in ~/Documents")
        assert decision.route == "tools"
        assert decision.tool_hint == "file_tool"

    def test_delete_requires_confirmation(self, router):
        decision = router.route("delete ~/Downloads/old_file.txt")
        assert decision.route == "tools"
        assert decision.requires_confirmation

    def test_move_requires_confirmation(self, router):
        decision = router.route("move ~/Desktop/notes.txt to ~/Documents")
        assert decision.route == "tools"
        assert decision.requires_confirmation

    def test_excel_routes_to_tools(self, router):
        decision = router.route("summarize the excel file at ~/report.xlsx")
        assert decision.route == "tools"
        assert decision.tool_hint == "excel_tool"

    def test_app_open_routes_to_system_tool(self, router):
        decision = router.route("open notepad app")
        assert decision.route == "tools"
        assert decision.tool_hint == "system_tool"

    def test_web_research_cloud_disabled(self, router):
        decision = router.route("research latest AI news")
        assert decision.route == "local_llm"

    def test_web_research_cloud_enabled_l0(self, cloud_router):
        decision = cloud_router.route("research the latest breakthroughs in fusion energy")
        assert decision.route == "cloud_llm"

    def test_secret_query_refused(self, router):
        decision = router.route("password=hunter2 please store this")
        assert decision.route == "refuse"

    def test_default_route_local_llm(self, router):
        decision = router.route("What is the capital of France?")
        assert decision.route == "local_llm"

    def test_task_id_generated_if_missing(self, router):
        decision = router.route("hello")
        assert len(decision.task_id) > 0

    def test_task_id_preserved(self, router):
        decision = router.route("hello", task_id="my-custom-id")
        assert decision.task_id == "my-custom-id"

    def test_audit_entry_written(self, tmp_path):
        cfg = {
            "policy": {
                "cloud_enabled": False,
                "blocked_directories": [],
                "blocked_extensions": [],
            },
            "audit": {"db_path": str(tmp_path / "audit3.db")},
        }
        policy = PolicyEngine(cfg)
        audit = AuditLogger(db_path=str(tmp_path / "audit3.db"))
        r = Router(policy=policy, audit=audit, config=cfg)
        decision = r.route("hello world", task_id="test-123")
        entries = audit.get_entries(task_id="test-123")
        assert len(entries) >= 1
        assert entries[0]["event_type"] == "route"
