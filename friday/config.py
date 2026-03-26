"""Configuration loader for FRIDAY.

Reads config.toml from the project root (or a path set via the
FRIDAY_CONFIG env-var).  All values have safe defaults so the
assistant starts even with no config file present.
"""

from __future__ import annotations

import os
import pathlib
from typing import Any

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    try:
        import tomli as tomllib  # type: ignore[no-reattr,import]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

_DEFAULTS: dict[str, Any] = {
    "llm": {
        "provider": "ollama",
        "model": "llama3:8b-instruct-q5_K_M",
        "base_url": "http://localhost:11434",
        "timeout_seconds": 120,
    },
    "memory": {
        "db_path": "~/.friday/memory.db",
        "session_window": 20,
    },
    "audit": {
        "db_path": "~/.friday/audit.db",
    },
    "policy": {
        "blocked_directories": [
            "~/.ssh",
            "~/.gnupg",
            "~/.aws",
            "~/.config/friday/secrets",
        ],
        "blocked_extensions": [".key", ".pem", ".pfx", ".p12", ".kdbx"],
        "max_file_size_mb": 100,
        "max_excel_rows": 100_000,
        "require_confirmation_on_delete": True,
        "cloud_enabled": False,
    },
    "sandbox": {
        "fs_whitelist": ["~/Documents", "~/Downloads", "~/Desktop"],
        "max_parallel_tools": 3,
        "tool_timeout_seconds": 30,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: str | None = None) -> dict[str, Any]:
    """Load and return the merged configuration dictionary."""
    if path is None:
        path = os.environ.get(
            "FRIDAY_CONFIG",
            str(pathlib.Path(__file__).parent.parent / "config.toml"),
        )

    if tomllib is None or not pathlib.Path(path).exists():
        return dict(_DEFAULTS)

    with open(path, "rb") as fh:
        user_cfg = tomllib.load(fh)

    return _deep_merge(_DEFAULTS, user_cfg)


# Module-level singleton
_config: dict[str, Any] | None = None


def get_config() -> dict[str, Any]:
    global _config
    if _config is None:
        _config = load_config()
    return _config
