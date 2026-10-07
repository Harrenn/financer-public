#!/usr/bin/env python3
from __future__ import annotations

import json
import shlex
from typing import Any

import app.db as db_module
from app.seed import seed_defaults
from app.v2.seed import seed_defaults_v2

from agentic_runtime import AgenticFinanceRuntime


HELP_TEXT = """\
Financer natural-language terminal mode.

Type normal language requests such as:
- show my dashboard
- add expense 42 for groceries today from account 1
- create bank account named Payroll with opening balance 5000
- show monthly stats
- set daemon.daily_time to 07:30
- confirm proposal scp_...

Commands:
- :help   show this help
- :quit   exit
- :tools  list available tool names
- :capabilities  show what the agent can do
- :clear  clear in-memory conversation context
- :settings
- :settings show [key]
- :settings set <key> <value>
- :settings reset <key>
- :settings history [limit]
- :settings proposals [pending|applied|denied]
- :confirm <proposal_id>
- :deny <proposal_id>
- :trace last
"""


def _parse_scalar(raw: str) -> Any:
    stripped = raw.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return stripped


def _tool(runtime: AgenticFinanceRuntime, name: str, arguments: dict[str, Any]) -> Any:
    result = runtime.run_tool(name, arguments)
    if result.get("ok"):
        return result.get("result")
    raise ValueError(str(result.get("error", "tool failed")))


def _print_setting_row(row: dict[str, Any]) -> None:
    marker = "default" if row.get("is_default") else "custom"
    print(f"{row.get('key')} = {row.get('value')} ({marker})")


def _print_capabilities(runtime: AgenticFinanceRuntime) -> None:
    tools = {spec["name"] for spec in runtime.describe_tools()}
    groups = [
        ("Dashboard and stats", ["get_dashboard", "get_stats"]),
        ("Core finance data", ["list_accounts", "create_account", "list_income_sources", "upsert_income_source_with_salary_profile"]),
        ("Transactions", ["create_transaction", "record_salary_payment_from_profile"]),
        ("Taxonomy and ontology", ["resolve_taxonomy_term", "create_taxonomy_value", "create_taxonomy_alias", "list_taxonomy_values"]),
        ("Settings autonomy", ["settings_list", "settings_update", "settings_proposals", "settings_confirm_proposal"]),
    ]

    print("Agent capabilities:")
    for label, examples in groups:
        available = [name for name in examples if name in tools]
        if available:
            print(f"- {label}: {', '.join(available)}")

    allowlist = runtime._setting("safety.tool_allowlist", [])  # noqa: SLF001
    denylist = runtime._setting("safety.tool_denylist", [])  # noqa: SLF001
    print(f"- Active tool allowlist: {allowlist if allowlist else 'none (all tools allowed)'}")
    print(f"- Active tool denylist: {denylist if denylist else 'none'}")
    print("- Full raw tool list: use :tools")


def _cmd_trace(runtime: AgenticFinanceRuntime, line: str) -> None:
    tokens = shlex.split(line)
    if len(tokens) != 2 or tokens[1] != "last":
        raise ValueError("usage: :trace last")
    trace = runtime.get_last_trace()
    if not trace:
        print("no agent trace available")
        return

    print(f"run_id={trace.get('id')} status={trace.get('status')} created_at={trace.get('created_at')}")
    print(f"user: {trace.get('user_input')}")
    print(f"assistant: {trace.get('assistant_output')}")
    for step in trace.get("steps", []):
        print(
            f"step={step.get('step_order')} agent={step.get('agent_name')} "
            f"status={step.get('status')} error={step.get('error')}"
        )


def _cmd_settings(runtime: AgenticFinanceRuntime, line: str) -> None:
    tokens = shlex.split(line)
    if len(tokens) == 1:
        rows = _tool(runtime, "settings_list", {})
        for row in rows:
            _print_setting_row(row)
        return

    sub = tokens[1]
    if sub == "show":
        if len(tokens) == 2:
            rows = _tool(runtime, "settings_list", {})
            for row in rows:
                _print_setting_row(row)
            return
        if len(tokens) != 3:
            raise ValueError("usage: :settings show [key]")
        row = _tool(runtime, "settings_get", {"key": tokens[2]})
        _print_setting_row(row)
        return

    if sub == "set":
        if len(tokens) < 4:
            raise ValueError("usage: :settings set <key> <value>")
        key = tokens[2]
        raw_value = " ".join(tokens[3:])
        new_value = _parse_scalar(raw_value)
        result = _tool(
            runtime,
            "settings_update",
            {
                "key": key,
                "new_value": new_value,
                "reason": "cli_settings_set",
            },
        )
        if result.get("applied"):
            print(f"applied: {result.get('key')} -> {result.get('new_value')} [{result.get('risk_level')}]")
        else:
            print(
                f"pending confirmation: proposal_id={result.get('proposal_id')} "
                f"key={result.get('key')} new_value={result.get('new_value')}"
            )
        return

    if sub == "reset":
        if len(tokens) != 3:
            raise ValueError("usage: :settings reset <key>")
        result = _tool(
            runtime,
            "settings_reset",
            {
                "key": tokens[2],
                "reason": "cli_settings_reset",
            },
        )
        if result.get("applied"):
            print(f"reset applied: {result.get('key')} -> {result.get('new_value')} [{result.get('risk_level')}]")
        else:
            print(f"reset pending confirmation: proposal_id={result.get('proposal_id')} key={result.get('key')}")
        return

    if sub == "history":
        if len(tokens) > 3:
            raise ValueError("usage: :settings history [limit]")
        limit = int(tokens[2]) if len(tokens) == 3 else 20
        rows = _tool(runtime, "settings_history", {"limit": limit})
        if not rows:
            print("no settings history")
            return
        for row in rows:
            print(
                f"{row.get('created_at')} id={row.get('id')} key={row.get('key')} "
                f"risk={row.get('risk_level')} reason={row.get('reason')}"
            )
        return

    if sub == "proposals":
        if len(tokens) > 3:
            raise ValueError("usage: :settings proposals [status]")
        status = tokens[2] if len(tokens) == 3 else "pending"
        rows = _tool(runtime, "settings_proposals", {"status": status, "limit": 50})
        if not rows:
            print(f"no proposals with status={status}")
            return
        for row in rows:
            print(
                f"{row.get('id')} key={row.get('key')} status={row.get('status')} "
                f"risk={row.get('risk_level')} new={row.get('new_value_json')}"
            )
        return

    raise ValueError(f"unknown :settings subcommand: {sub}")


def main() -> int:
    db_module.init_db()
    db = db_module.SessionLocal()
    try:
        seed_defaults(db)
        seed_defaults_v2(db)
    finally:
        db.close()

    runtime = AgenticFinanceRuntime()
    print("Financer agent is ready. Type :help for help, :quit to exit.")

    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not line:
            continue

        if line in {":quit", ":exit"}:
            return 0
        if line == ":help":
            print(HELP_TEXT)
            continue
        if line == ":tools":
            for spec in runtime.describe_tools():
                print(spec["name"])
            continue
        if line == ":capabilities":
            _print_capabilities(runtime)
            continue
        if line == ":clear":
            runtime.events.clear()
            print("conversation context cleared")
            continue

        try:
            if line.startswith(":settings"):
                _cmd_settings(runtime, line)
                continue
            if line.startswith(":confirm "):
                parts = shlex.split(line)
                if len(parts) != 2:
                    print("usage: :confirm <proposal_id>")
                    continue
                result = _tool(runtime, "settings_confirm_proposal", {"proposal_id": parts[1]})
                print(f"proposal applied: {result.get('proposal_id')} key={result.get('key')}")
                continue
            if line.startswith(":deny "):
                parts = shlex.split(line)
                if len(parts) != 2:
                    print("usage: :deny <proposal_id>")
                    continue
                result = _tool(runtime, "settings_deny_proposal", {"proposal_id": parts[1]})
                print(f"proposal denied: {result.get('proposal_id')} status={result.get('status')}")
                continue
            if line.startswith(":trace"):
                _cmd_trace(runtime, line)
                continue

            response = runtime.handle_user_message(line)
            print(response)
        except ValueError as exc:
            print(f"error: {exc}")
        except Exception as exc:  # noqa: BLE001
            print(f"agent error: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
