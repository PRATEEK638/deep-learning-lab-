"""Task router for FRIDAY.

Rule-first routing: deterministic keyword / pattern rules are checked
before any model inference.  The router emits one of three routes:

  "tools"      – handled directly by registered tools (no LLM needed)
  "local_llm"  – forward to the local Ollama LLM
  "cloud_llm"  – forward to cloud model (only when enabled & data ≤ L1)

Routing table (in priority order)
----------------------------------
1. Contains local path OR mentions "my file / desktop / documents"
   → local tools; no cloud.
2. Action is delete / move
   → local tools; require confirmation; no cloud.
3. excel / spreadsheet / csv + local file
   → local tool + local LLM.
4. research / latest news / web search
   → cloud_llm IFF cloud_enabled AND data ≤ L1 after sanitisation.
5. Summarise pasted text < 2k tokens, no PII
   → local_llm; if long/complex and user opts-in → cloud.
6. Default
   → local_llm.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from .audit import AuditLogger
from .config import get_config
from .policy import PolicyEngine, Sensitivity, _PATH_RE_CORE


@dataclass
class RoutingDecision:
    route: str              # "tools" | "local_llm" | "cloud_llm"
    task_id: str
    reason: str
    tool_hint: str = ""     # suggested tool name (if route == "tools")
    requires_confirmation: bool = False
    sanitized_query: str = ""


# ---------------------------------------------------------------------------
# Pattern matching helpers
# ---------------------------------------------------------------------------

_LOCAL_FILE_PATTERN = re.compile(
    r"(?:^|[\s\"'(])(?:"
    + _PATH_RE_CORE
    + r"|(?:my\s+)?(?:file|desktop|documents?|downloads?|folder))",
    re.IGNORECASE | re.MULTILINE,
)

_DESTRUCTIVE_PATTERN = re.compile(
    r"\b(?:delete|remove|rm|erase|trash|move|mv|rename)\b",
    re.IGNORECASE,
)

_EXCEL_PATTERN = re.compile(
    r"\b(?:excel|spreadsheet|csv|xlsx|xls|openpyxl|rows?|columns?|sheet)\b",
    re.IGNORECASE,
)

_FILE_OP_PATTERN = re.compile(
    r"\b(?:list|show|open|read|copy|create|write|find|search)\b.{0,40}\b(?:file|folder|dir|document)\b",
    re.IGNORECASE | re.DOTALL,
)

_WEB_PATTERN = re.compile(
    r"\b(?:research|latest news|current|web search|google|search online"
    r"|look up|wikipedia|browse)\b",
    re.IGNORECASE,
)

_APP_PATTERN = re.compile(
    r"\b(?:open|launch|start|close|kill|quit|terminate)\s+\w+\s*(?:app|application|program|process)?\b",
    re.IGNORECASE,
)


class Router:
    """Deterministic rule-first task router."""

    def __init__(
        self,
        policy: PolicyEngine | None = None,
        audit: AuditLogger | None = None,
        config: dict | None = None,
    ):
        cfg = config or get_config()
        self._policy = policy or PolicyEngine(cfg)
        self._audit = audit or AuditLogger()
        self._cloud_enabled: bool = cfg.get("policy", {}).get("cloud_enabled", False)

    def route(self, query: str, task_id: str | None = None) -> RoutingDecision:
        """Apply routing rules to *query* and return a RoutingDecision."""
        task_id = task_id or str(uuid.uuid4())
        sensitivity = self._policy.classify_text(query)

        # --- Rule 1: L3 secrets → refuse
        if sensitivity == Sensitivity.L3_SECRET:
            decision = RoutingDecision(
                route="refuse",
                task_id=task_id,
                reason="Query contains or references secret/credential data (L3).",
            )
            self._audit.log_route(task_id, "refuse", decision.reason, int(sensitivity))
            return decision

        # --- Rule 2: destructive file ops → tools + confirmation
        if _DESTRUCTIVE_PATTERN.search(query) and _LOCAL_FILE_PATTERN.search(query):
            decision = RoutingDecision(
                route="tools",
                task_id=task_id,
                reason="Destructive file operation detected – routing to file_tool with confirmation gate.",
                tool_hint="file_tool",
                requires_confirmation=True,
            )
            self._audit.log_route(task_id, "tools", decision.reason, int(sensitivity))
            return decision

        # --- Rule 3: excel/spreadsheet with local file → tools + local LLM
        if _EXCEL_PATTERN.search(query) and _LOCAL_FILE_PATTERN.search(query):
            decision = RoutingDecision(
                route="tools",
                task_id=task_id,
                reason="Excel/spreadsheet operation on local file.",
                tool_hint="excel_tool",
            )
            self._audit.log_route(task_id, "tools", decision.reason, int(sensitivity))
            return decision

        # --- Rule 4: local path / file mentions → tools (no cloud)
        if _LOCAL_FILE_PATTERN.search(query):
            hint = "file_tool"
            if _APP_PATTERN.search(query):
                hint = "system_tool"
            decision = RoutingDecision(
                route="tools",
                task_id=task_id,
                reason="Local path or file reference detected.",
                tool_hint=hint,
            )
            self._audit.log_route(task_id, "tools", decision.reason, int(sensitivity))
            return decision

        # --- Rule 5: app open/close → system_tool
        if _APP_PATTERN.search(query):
            decision = RoutingDecision(
                route="tools",
                task_id=task_id,
                reason="App open/close operation.",
                tool_hint="system_tool",
            )
            self._audit.log_route(task_id, "tools", decision.reason, int(sensitivity))
            return decision

        # --- Rule 6: web / research → cloud if allowed
        if _WEB_PATTERN.search(query):
            if self._cloud_enabled and sensitivity <= Sensitivity.L1_PERSONAL_ABSTRACT:
                sanitized = self._policy.redact(query)
                decision = RoutingDecision(
                    route="cloud_llm",
                    task_id=task_id,
                    reason="Web/research query with cloud enabled and data ≤ L1.",
                    sanitized_query=sanitized,
                )
            else:
                reason = (
                    "Web/research query but cloud is disabled."
                    if not self._cloud_enabled
                    else "Web/research query blocked: data sensitivity > L1."
                )
                decision = RoutingDecision(
                    route="local_llm",
                    task_id=task_id,
                    reason=reason,
                )
            self._audit.log_route(task_id, decision.route, decision.reason, int(sensitivity))
            return decision

        # --- Default: local LLM
        decision = RoutingDecision(
            route="local_llm",
            task_id=task_id,
            reason="Default route – local LLM.",
        )
        self._audit.log_route(task_id, "local_llm", decision.reason, int(sensitivity))
        return decision
