"""file_tool – safe file-system operations for FRIDAY.

Supported actions: list, read_text, copy, move, rename, delete.

Guardrails
----------
- All paths must be inside the configured fs_whitelist.
- Blocked directories / extensions enforced via PolicyEngine.
- delete / move trigger the destructive-op confirmation gate.
- Max file size enforced on read.
- No shell execution; uses Python's shutil / pathlib only.
"""

from __future__ import annotations

import os
import pathlib
import shutil
from typing import Any

from ..config import get_config
from ..policy import PolicyEngine, Sensitivity
from ..tools import BaseTool, SafetyLevel, SideEffect, ToolSchema


class FileTool(BaseTool):
    """CRUD-style file tool with safety guardrails."""

    _SCHEMA = ToolSchema(
        name="file_tool",
        description=(
            "Perform safe file-system operations: list, read_text, copy, "
            "move, rename, delete.  Destructive ops require confirmation."
        ),
        side_effect=SideEffect.WRITE,
        safety_level=SafetyLevel.LOCAL_ONLY,
        timeout_seconds=30,
        retries=1,
        requires_confirmation=False,  # set per-action at runtime
        dry_run=False,
        idempotent=False,
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "read_text", "copy", "move", "rename", "delete"],
                },
                "path": {"type": "string"},
                "destination": {"type": "string"},
                "confirmed": {"type": "boolean", "default": False},
            },
            "required": ["action", "path"],
        },
        output_schema={
            "type": "object",
            "properties": {
                "result": {},
                "requires_confirmation": {"type": "boolean"},
                "message": {"type": "string"},
            },
        },
    )

    def __init__(self, policy: PolicyEngine | None = None, config: dict | None = None):
        cfg = config or get_config()
        self._policy = policy or PolicyEngine(cfg)
        sandbox_cfg = cfg.get("sandbox", {})
        self._whitelist: list[pathlib.Path] = [
            pathlib.Path(p).expanduser()
            for p in sandbox_cfg.get("fs_whitelist", [])
        ]
        self._max_file_bytes: int = (
            cfg.get("policy", {}).get("max_file_size_mb", 100) * 1024 * 1024
        )

    @property
    def schema(self) -> ToolSchema:
        return self._SCHEMA

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _assert_in_whitelist(self, path: pathlib.Path) -> None:
        """Raise ValueError if path is not inside any whitelisted directory."""
        if not self._whitelist:
            return  # no whitelist configured → allow all (not recommended)
        resolved = path.expanduser().resolve()
        for allowed in self._whitelist:
            try:
                resolved.relative_to(allowed.resolve())
                return
            except ValueError:
                continue
        raise ValueError(
            f"Path '{resolved}' is not inside any allowed directory: "
            + ", ".join(str(w) for w in self._whitelist)
        )

    def _check_policy(self, path: pathlib.Path) -> None:
        decision = self._policy.check_path(path)
        if not decision.allowed:
            raise PermissionError(decision.reason)

    def _resolve(self, raw: str) -> pathlib.Path:
        p = pathlib.Path(raw).expanduser()
        self._assert_in_whitelist(p)
        self._check_policy(p)
        return p

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _action_list(self, path: str) -> list[dict]:
        p = self._resolve(path)
        if not p.exists():
            raise FileNotFoundError(f"'{p}' does not exist")
        if p.is_file():
            stat = p.stat()
            return [{"name": p.name, "type": "file", "size": stat.st_size}]
        entries = []
        for child in sorted(p.iterdir()):
            entries.append(
                {
                    "name": child.name,
                    "type": "dir" if child.is_dir() else "file",
                    "size": child.stat().st_size if child.is_file() else None,
                }
            )
        return entries

    def _action_read_text(self, path: str) -> str:
        p = self._resolve(path)
        if not p.is_file():
            raise FileNotFoundError(f"'{p}' is not a file")
        size = p.stat().st_size
        if size > self._max_file_bytes:
            raise ValueError(
                f"File size {size} bytes exceeds limit {self._max_file_bytes} bytes"
            )
        return p.read_text(errors="replace")

    def _action_copy(self, path: str, destination: str) -> str:
        src = self._resolve(path)
        dst = self._resolve(destination)
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        return str(dst)

    def _action_move(self, path: str, destination: str, confirmed: bool) -> Any:
        if not confirmed:
            return {
                "requires_confirmation": True,
                "message": f"Confirm moving '{path}' → '{destination}'?",
            }
        src = self._resolve(path)
        dst = self._resolve(destination)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return str(dst)

    def _action_rename(self, path: str, destination: str) -> str:
        src = self._resolve(path)
        dst = src.parent / pathlib.Path(destination).name
        self._assert_in_whitelist(dst)
        self._check_policy(dst)
        src.rename(dst)
        return str(dst)

    def _action_delete(self, path: str, confirmed: bool) -> Any:
        if not confirmed:
            return {
                "requires_confirmation": True,
                "message": f"Confirm deleting '{path}'?",
            }
        p = self._resolve(path)
        if not p.exists():
            raise FileNotFoundError(f"'{p}' does not exist")
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return f"Deleted '{p}'"

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _execute(self, action: str, path: str, destination: str = "", confirmed: bool = False) -> Any:
        if action == "list":
            return self._action_list(path)
        if action == "read_text":
            return self._action_read_text(path)
        if action == "copy":
            if not destination:
                raise ValueError("'destination' is required for copy")
            return self._action_copy(path, destination)
        if action == "move":
            if not destination:
                raise ValueError("'destination' is required for move")
            return self._action_move(path, destination, confirmed)
        if action == "rename":
            if not destination:
                raise ValueError("'destination' is required for rename")
            return self._action_rename(path, destination)
        if action == "delete":
            return self._action_delete(path, confirmed)
        raise ValueError(f"Unknown action: {action!r}")
