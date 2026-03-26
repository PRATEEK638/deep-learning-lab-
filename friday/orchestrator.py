"""FRIDAY orchestrator – ties together router, planner, tools, memory, audit."""

from __future__ import annotations

import time
import uuid
from typing import Any

from .audit import AuditLogger
from .config import get_config
from .memory import MemoryStore, TaskRecord
from .planner import Planner
from .policy import PolicyEngine
from .router import Router
from .tools import ToolRegistry


class Orchestrator:
    """Main async-ready orchestrator for FRIDAY task execution."""

    def __init__(self, config: dict | None = None):
        self._cfg = config or get_config()
        self._policy = PolicyEngine(self._cfg)
        self._audit = AuditLogger()
        self._memory = MemoryStore()
        self._router = Router(self._policy, self._audit, self._cfg)
        self._planner = Planner(self._cfg)
        self._registry = ToolRegistry()
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        from .tools.excel_tool import ExcelTool
        from .tools.file_tool import FileTool
        from .tools.system_tool import SystemTool

        self._registry.register(FileTool(self._policy, self._cfg))
        self._registry.register(SystemTool())
        try:
            self._registry.register(ExcelTool(self._policy, self._cfg))
        except ImportError:
            pass  # openpyxl not installed – skip excel_tool

    def process(self, query: str, task_id: str | None = None) -> dict[str, Any]:
        """Process a user query end-to-end and return a result dict."""
        task_id = task_id or str(uuid.uuid4())
        start = time.monotonic()

        # Add user turn to session
        self._memory.add_turn("user", query)

        # Route
        routing = self._router.route(query, task_id)

        record = TaskRecord(
            task_id=task_id,
            query=query,
            route=routing.route,
            status="running",
        )
        self._memory.save_task(record)

        if routing.route == "refuse":
            result = {
                "task_id": task_id,
                "route": "refuse",
                "status": "refused",
                "message": routing.reason,
            }
            self._memory.update_task(task_id, "refused", error=routing.reason)
            self._memory.add_turn("assistant", routing.reason)
            return result

        # If tool hint is set, try direct tool dispatch via planner
        tool_schemas = self._registry.list_schemas()
        session_ctx = self._memory.get_session_context()

        if routing.route == "tools" and routing.tool_hint:
            tool = self._registry.get(routing.tool_hint)
            if tool:
                # Ask LLM to produce the correct tool args
                plan = self._planner.plan(query, tool_schemas, session_ctx)

                if "error" in plan and plan.get("name") == "error":
                    # LLM unavailable; tell the user
                    result = {
                        "task_id": task_id,
                        "route": routing.route,
                        "status": "error",
                        "message": plan["error"],
                    }
                    self._memory.update_task(task_id, "failed", error=plan["error"])
                    return result

                tool_name = plan.get("name", routing.tool_hint)
                tool_args = plan.get("args", {})

                # Respect confirmation requirement
                if routing.requires_confirmation and not tool_args.get("confirmed"):
                    result = {
                        "task_id": task_id,
                        "route": routing.route,
                        "status": "pending_confirmation",
                        "message": f"Please confirm the operation: {query}",
                        "tool_call": {"name": tool_name, "args": tool_args},
                    }
                    self._memory.update_task(
                        task_id, "pending_confirmation",
                        error="Awaiting user confirmation"
                    )
                    return result

                actual_tool = self._registry.get(tool_name) or tool
                t_start = time.monotonic()
                tool_result = actual_tool.run(**tool_args)
                duration_ms = (time.monotonic() - t_start) * 1000

                self._audit.log_tool_call(
                    task_id, actual_tool.schema.name,
                    tool_args, tool_result.data,
                    duration_ms, tool_result.error,
                )

                if tool_result.success:
                    answer = str(tool_result.data)
                    self._memory.update_task(task_id, "done", result=answer)
                    self._memory.add_turn("assistant", answer)
                    return {
                        "task_id": task_id,
                        "route": routing.route,
                        "status": "done",
                        "result": tool_result.data,
                        "duration_ms": duration_ms,
                    }
                else:
                    self._memory.update_task(task_id, "failed", error=tool_result.error)
                    return {
                        "task_id": task_id,
                        "route": routing.route,
                        "status": "error",
                        "message": tool_result.error,
                        "duration_ms": duration_ms,
                    }

        # LLM-only route
        if routing.route in ("local_llm", "cloud_llm"):
            plan = self._planner.plan(query, tool_schemas, session_ctx)
            answer_text = plan.get("args", {}).get("text", str(plan))
            self._memory.update_task(task_id, "done", result=answer_text)
            self._memory.add_turn("assistant", answer_text)
            return {
                "task_id": task_id,
                "route": routing.route,
                "status": "done",
                "result": answer_text,
                "duration_ms": (time.monotonic() - start) * 1000,
            }

        # Fallback
        self._memory.update_task(task_id, "failed", error="Unknown route")
        return {
            "task_id": task_id,
            "route": routing.route,
            "status": "error",
            "message": "Unknown routing outcome",
        }
