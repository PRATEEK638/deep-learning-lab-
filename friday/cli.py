"""FRIDAY CLI – interactive command-line interface.

Usage:
    python -m friday.cli              # interactive REPL
    python -m friday.cli "your query" # single-shot

Commands inside the REPL:
    /history   – show recent tasks
    /audit     – show last 10 audit entries
    /clear     – clear session memory
    /quit      – exit
"""

from __future__ import annotations

import sys

from .orchestrator import Orchestrator


def _print_result(result: dict) -> None:
    status = result.get("status", "?")
    route = result.get("route", "?")
    print(f"\n[{status.upper()} | {route}]")

    if status == "refused":
        print(f"  ⛔  {result.get('message', '')}")
    elif status == "pending_confirmation":
        print(f"  ❓  {result.get('message', '')}")
        tc = result.get("tool_call", {})
        if tc:
            print(f"      Tool: {tc.get('name')}  Args: {tc.get('args')}")
    elif status == "done":
        data = result.get("result")
        if isinstance(data, list):
            for item in data:
                print(f"  {item}")
        elif data is not None:
            print(f"  {data}")
    elif status == "error":
        print(f"  ❌  {result.get('message', '')}")
    else:
        print(f"  {result}")

    ms = result.get("duration_ms")
    if ms is not None:
        print(f"  ({ms:.0f} ms)")


def run_cli(query: str | None = None) -> None:
    orc = Orchestrator()

    if query:
        result = orc.process(query)
        _print_result(result)
        return

    print("FRIDAY  –  Local AI Assistant  (type /quit to exit)")
    print("-" * 50)

    while True:
        try:
            user_input = input("\nYou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not user_input:
            continue

        if user_input.lower() in ("/quit", "/exit", "/q"):
            print("Bye!")
            break

        if user_input.lower() == "/history":
            tasks = orc._memory.recent_tasks(10)
            if not tasks:
                print("  (no tasks yet)")
            for t in tasks:
                print(f"  [{t['status']}] {t['query'][:60]}")
            continue

        if user_input.lower() == "/audit":
            entries = orc._audit.get_entries(limit=10)
            if not entries:
                print("  (no audit entries)")
            for e in entries:
                print(f"  [{e['event_type']}] {e['tool_name'] or e['route']}  "
                      f"task={e['task_id'][:8]}...")
            continue

        if user_input.lower() == "/clear":
            orc._memory.clear_session()
            print("  Session cleared.")
            continue

        result = orc.process(user_input)
        _print_result(result)


def main() -> None:
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else None
    run_cli(query)


if __name__ == "__main__":
    main()
