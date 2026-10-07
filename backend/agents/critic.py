from __future__ import annotations

from decimal import Decimal
from typing import Any

from .contracts import CriticResult, PlannedAction


CRITIC_PROMPT = """You are the critic/safety agent.
Block ambiguous or risky actions and ask concise follow-up questions.
"""


class CriticSafetyAgent:
    def __init__(self, *, large_amount_threshold: float = 1000.0):
        self.large_amount_threshold = Decimal(str(large_amount_threshold))

    def review(
        self,
        *,
        user_text: str,
        actions: list[PlannedAction],
        mappings: list[dict[str, Any]],
    ) -> CriticResult:
        if not actions:
            return CriticResult(approved=True)

        lowered = user_text.lower()
        for action in actions:
            if action.name in {"create_transaction", "record_salary_payment_from_profile"}:
                amount_raw = action.arguments.get("amount")
                if amount_raw is not None:
                    amount = Decimal(str(amount_raw))
                    if amount > self.large_amount_threshold and "confirm" not in lowered:
                        return CriticResult(
                            approved=False,
                            needs_follow_up=True,
                            follow_up_question=(
                                f"This amount ({amount}) is above your confirmation threshold "
                                f"({self.large_amount_threshold}). Reply with 'confirm' to continue."
                            ),
                            reason="high_risk_amount",
                        )

            if action.name == "upsert_income_source_with_salary_profile":
                if action.arguments.get("is_salary"):
                    if "monthly_net_amount" not in action.arguments:
                        return CriticResult(
                            approved=False,
                            needs_follow_up=True,
                            follow_up_question="What is your monthly net salary amount?",
                            reason="missing_monthly_net",
                        )

        return CriticResult(approved=True)
