"""Tool registry and base class for FRIDAY tools.

Each tool is described by a JSON Schema (inputs/outputs) and a set
of metadata fields that the router and policy engine consult.
"""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SideEffect(str, Enum):
    READ = "read"
    WRITE = "write"
    SYSTEM = "system"
    NET = "net"


class SafetyLevel(str, Enum):
    LOCAL_ONLY = "local-only"
    CLOUD_SAFE = "cloud-safe"


@dataclass
class ToolSchema:
    """Metadata + JSON-Schema description for one tool."""

    name: str
    description: str
    side_effect: SideEffect
    safety_level: SafetyLevel
    timeout_seconds: int = 30
    retries: int = 0
    requires_confirmation: bool = False
    dry_run: bool = False
    idempotent: bool = True

    # JSON Schema fragments for inputs / outputs
    input_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "side_effect": self.side_effect.value,
            "safety_level": self.safety_level.value,
            "timeout_seconds": self.timeout_seconds,
            "retries": self.retries,
            "requires_confirmation": self.requires_confirmation,
            "dry_run": self.dry_run,
            "idempotent": self.idempotent,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
        }


@dataclass
class ToolResult:
    success: bool
    data: Any = None
    error: str = ""
    duration_ms: float = 0.0


class BaseTool(abc.ABC):
    """Abstract base class every FRIDAY tool must extend."""

    @property
    @abc.abstractmethod
    def schema(self) -> ToolSchema:
        ...

    @abc.abstractmethod
    def _execute(self, **kwargs) -> Any:
        ...

    def run(self, **kwargs) -> ToolResult:
        start = time.monotonic()
        try:
            data = self._execute(**kwargs)
            return ToolResult(
                success=True,
                data=data,
                duration_ms=(time.monotonic() - start) * 1000,
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=str(exc),
                duration_ms=(time.monotonic() - start) * 1000,
            )


class ToolRegistry:
    """Central registry mapping tool names to BaseTool instances."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.schema.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def list_schemas(self) -> list[dict]:
        return [t.schema.to_dict() for t in self._tools.values()]

    def __contains__(self, name: str) -> bool:
        return name in self._tools
