from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlannedAction:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""


@dataclass
class PlanResult:
    assistant_message: str
    actions: list[PlannedAction] = field(default_factory=list)
    needs_follow_up: bool = False
    follow_up_question: str | None = None


@dataclass
class ResolveResult:
    actions: list[PlannedAction] = field(default_factory=list)
    mappings: list[dict[str, Any]] = field(default_factory=list)
    needs_follow_up: bool = False
    follow_up_question: str | None = None


@dataclass
class CriticResult:
    approved: bool
    reason: str = ""
    needs_follow_up: bool = False
    follow_up_question: str | None = None


@dataclass
class ExecutionResult:
    assistant_message: str
    tool_outputs: list[dict[str, Any]] = field(default_factory=list)
