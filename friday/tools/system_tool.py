"""system_tool – OS-level operations for FRIDAY.

Supported actions:
  open_app    – launch an application by name / path
  close_app   – terminate a process by name or PID
  foreground  – get the name/PID of the currently focused window

Safety: no shell=True; uses subprocess.Popen with explicit arg lists.
Process names are checked against an allowlist to prevent abuse.
"""

from __future__ import annotations

import os
import pathlib
import platform
import subprocess
import sys
from typing import Any

from ..tools import BaseTool, SafetyLevel, SideEffect, ToolSchema


_ALLOWED_APPS: set[str] = {
    # common productivity apps (lower-case names)
    "notepad", "notepad.exe",
    "calc", "calc.exe",
    "explorer", "explorer.exe",
    "code", "code.exe",  # VS Code
    "firefox", "firefox.exe",
    "chrome", "google-chrome",
    "evince", "gedit", "libreoffice",
    "excel", "winword", "powerpnt",  # Office
    "python", "python3",
}


class SystemTool(BaseTool):
    """Safe OS interaction: open/close apps, query foreground window."""

    _SCHEMA = ToolSchema(
        name="system_tool",
        description=(
            "Open or close an application, or get the foreground window info. "
            "open_app and close_app require an allowed app name."
        ),
        side_effect=SideEffect.SYSTEM,
        safety_level=SafetyLevel.LOCAL_ONLY,
        timeout_seconds=15,
        retries=0,
        requires_confirmation=False,
        dry_run=False,
        idempotent=False,
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["open_app", "close_app", "foreground"],
                },
                "app": {"type": "string", "description": "App name or path"},
                "pid": {"type": "integer", "description": "PID for close_app"},
            },
            "required": ["action"],
        },
        output_schema={
            "type": "object",
            "properties": {
                "result": {},
                "message": {"type": "string"},
            },
        },
    )

    def __init__(self, allowed_apps: set[str] | None = None):
        self._allowed = allowed_apps if allowed_apps is not None else _ALLOWED_APPS

    @property
    def schema(self) -> ToolSchema:
        return self._SCHEMA

    def _check_app(self, app: str) -> None:
        name = pathlib.Path(app).name.lower()
        if name not in self._allowed:
            raise PermissionError(
                f"App '{name}' is not in the allowed list.  "
                f"Allowed: {sorted(self._allowed)}"
            )

    def _open_app(self, app: str) -> dict:
        self._check_app(app)
        if platform.system() == "Windows":
            proc = subprocess.Popen([app], shell=False)
        else:
            proc = subprocess.Popen([app], shell=False,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        return {"pid": proc.pid, "message": f"Started '{app}' (PID {proc.pid})"}

    def _close_app(self, app: str = "", pid: int | None = None) -> dict:
        if pid:
            import signal
            try:
                os.kill(pid, signal.SIGTERM)
                return {"message": f"Sent SIGTERM to PID {pid}"}
            except ProcessLookupError:
                return {"message": f"No process with PID {pid}"}

        if not app:
            raise ValueError("Either 'app' or 'pid' must be provided for close_app")

        self._check_app(app)
        killed: list[int] = []
        try:
            import psutil  # optional dependency
            for proc in psutil.process_iter(["pid", "name"]):
                if proc.info["name"] and app.lower() in proc.info["name"].lower():
                    proc.terminate()
                    killed.append(proc.info["pid"])
        except ImportError:
            # Fallback: pkill-style on Unix
            if platform.system() != "Windows":
                result = subprocess.run(
                    ["pkill", "-f", app], capture_output=True
                )
                return {
                    "message": (
                        f"pkill '{app}' exited {result.returncode}"
                    )
                }
        return {"message": f"Terminated PIDs: {killed}" if killed else f"No running '{app}' found"}

    def _foreground(self) -> dict:
        system = platform.system()
        if system == "Windows":
            try:
                import ctypes
                hwnd = ctypes.windll.user32.GetForegroundWindow()
                length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                return {"title": buf.value, "hwnd": hwnd}
            except Exception as exc:
                return {"error": str(exc)}
        elif system == "Linux":
            try:
                result = subprocess.run(
                    ["xdotool", "getactivewindow", "getwindowname"],
                    capture_output=True, text=True, timeout=5
                )
                return {"title": result.stdout.strip()}
            except Exception as exc:
                return {"error": str(exc)}
        else:
            return {"title": "unknown", "platform": system}

    def _execute(self, action: str, app: str = "", pid: int | None = None, **_) -> Any:
        if action == "open_app":
            if not app:
                raise ValueError("'app' is required for open_app")
            return self._open_app(app)
        if action == "close_app":
            return self._close_app(app=app, pid=pid)
        if action == "foreground":
            return self._foreground()
        raise ValueError(f"Unknown action: {action!r}")
