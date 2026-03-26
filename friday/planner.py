"""LLM planner interface for FRIDAY.

Wraps the Ollama HTTP API.  Returns structured JSON tool calls that
the orchestrator dispatches.  Falls back gracefully if Ollama is
not running (useful in offline / test environments).

System prompt enforces:
  - Use ONLY registered tools.
  - Never send personal / local data to cloud.
  - Destructive actions must request confirmation.
  - Output JSON matching the tool input schemas.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .config import get_config


_SYSTEM_PROMPT = """You are the FRIDAY Local Planner. Follow these rules strictly:
1. Use ONLY the registered tools listed below. Do NOT invent tool names.
2. If the data is personal or comes from local files/paths, NEVER call a cloud tool.
3. For destructive actions (delete, move, overwrite), always set "confirmed": false
   so the user is asked first.
4. Output ONLY a JSON object with the key "tool_call" containing:
   {"name": "<tool_name>", "args": {<tool_args>}}
   or {"name": "answer", "args": {"text": "<direct answer>"}} if no tool is needed.
5. Keep reasoning short. Do not include explanations outside the JSON.
"""


class Planner:
    """Thin async wrapper around the Ollama completion API."""

    def __init__(self, config: dict | None = None):
        cfg = config or get_config()
        llm_cfg = cfg.get("llm", {})
        self._base_url: str = llm_cfg.get("base_url", "http://localhost:11434")
        self._model: str = llm_cfg.get("model", "llama3:8b-instruct-q5_K_M")
        self._timeout: int = llm_cfg.get("timeout_seconds", 120)

    def _build_prompt(
        self,
        query: str,
        tool_schemas: list[dict],
        session_context: list[dict],
    ) -> str:
        tools_json = json.dumps(tool_schemas, indent=2)
        history = ""
        for turn in session_context[-6:]:  # last 6 turns
            role = turn.get("role", "user")
            content = turn.get("content", "")
            history += f"{role.upper()}: {content}\n"

        return (
            f"{_SYSTEM_PROMPT}\n\n"
            f"AVAILABLE TOOLS:\n{tools_json}\n\n"
            f"CONVERSATION HISTORY:\n{history}\n"
            f"USER: {query}\n\n"
            f"ASSISTANT (JSON only):"
        )

    def plan(
        self,
        query: str,
        tool_schemas: list[dict],
        session_context: list[dict] | None = None,
    ) -> dict[str, Any]:
        """Call Ollama and parse the returned JSON tool call.

        Returns a dict of the form:
            {"name": "<tool_name>", "args": {...}}

        If Ollama is unreachable, returns an error dict.
        """
        try:
            import urllib.request
            prompt = self._build_prompt(query, tool_schemas, session_context or [])
            payload = json.dumps({
                "model": self._model,
                "prompt": prompt,
                "stream": False,
            }).encode()

            req = urllib.request.Request(
                f"{self._base_url}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = json.loads(resp.read().decode())
                text = body.get("response", "")
        except Exception as exc:
            return {"error": f"LLM unavailable: {exc}", "name": "error", "args": {}}

        return self._parse_response(text)

    @staticmethod
    def _parse_response(text: str) -> dict[str, Any]:
        """Extract the first JSON object from *text*."""
        # Try to find a JSON object in the response
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {"name": "answer", "args": {"text": text.strip()}}
        try:
            parsed = json.loads(match.group())
            # Accept both {"tool_call": {...}} and flat {"name": ..., "args": ...}
            if "tool_call" in parsed:
                return parsed["tool_call"]
            if "name" in parsed:
                return parsed
            return {"name": "answer", "args": {"text": text.strip()}}
        except json.JSONDecodeError:
            return {"name": "answer", "args": {"text": text.strip()}}
