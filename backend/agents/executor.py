from __future__ import annotations

from typing import Callable

from .contracts import ExecutionResult, PlannedAction


EXECUTOR_PROMPT = """You are the deterministic executor agent.
Execute validated actions in order and return concise results.
"""


class ExecutorAgent:
    def execute(
        self,
        *,
        actions: list[PlannedAction],
        run_tool: Callable[[str, dict], dict],
        mapping_notes: list[dict] | None = None,
    ) -> ExecutionResult:
        outputs: list[dict] = []

        for action in actions:
            result = run_tool(action.name, action.arguments)
            outputs.append({"tool": action.name, "arguments": action.arguments, "output": result})
            if not result.get("ok"):
                message = f"Execution failed on {action.name}: {result.get('error', 'unknown error')}"
                return ExecutionResult(assistant_message=message, tool_outputs=outputs)

        mapping_lines: list[str] = []
        for mapping in mapping_notes or []:
            resolved = mapping.get("resolved", {})
            mapping_lines.append(
                f"{mapping.get('field')}='{mapping.get('input_term')}' -> {resolved.get('domain')}:{resolved.get('key')}"
            )

        if outputs:
            message = "Done."
            if mapping_lines:
                message += " Applied mappings: " + "; ".join(mapping_lines)
            return ExecutionResult(assistant_message=message, tool_outputs=outputs)

        return ExecutionResult(assistant_message="No actions were required.", tool_outputs=[])
