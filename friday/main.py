"""FRIDAY FastAPI service entry point.

Runs the orchestrator behind a lightweight HTTP API so other
processes (tray app, VS Code extension, etc.) can send queries
and receive JSON responses.

Endpoints
---------
POST /query          – process a user query
GET  /health         – liveness probe
GET  /tasks          – list recent tasks
GET  /audit          – list recent audit entries
GET  /tools          – list registered tools (schemas)
"""

from __future__ import annotations

from typing import Any

try:
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel
    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False

from .orchestrator import Orchestrator

_orc: Orchestrator | None = None


def _get_orchestrator() -> Orchestrator:
    global _orc
    if _orc is None:
        _orc = Orchestrator()
    return _orc


if _FASTAPI_AVAILABLE:
    app = FastAPI(title="FRIDAY AI Assistant", version="0.1.0")

    class QueryRequest(BaseModel):
        query: str
        task_id: str | None = None

    @app.post("/query")
    def process_query(req: QueryRequest) -> dict[str, Any]:
        orc = _get_orchestrator()
        return orc.process(req.query, req.task_id)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/tasks")
    def list_tasks(limit: int = 20) -> list[dict]:
        orc = _get_orchestrator()
        return orc._memory.recent_tasks(limit)

    @app.get("/audit")
    def list_audit(limit: int = 20) -> list[dict]:
        orc = _get_orchestrator()
        return orc._audit.get_entries(limit=limit)

    @app.get("/tools")
    def list_tools() -> list[dict]:
        orc = _get_orchestrator()
        return orc._registry.list_schemas()


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:  # pragma: no cover
    """Start the FastAPI server with uvicorn."""
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit(
            "uvicorn is required to run the server. "
            "Install it with: pip install uvicorn"
        ) from exc
    uvicorn.run("friday.main:app", host=host, port=port, reload=False)
