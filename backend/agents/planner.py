from __future__ import annotations

import re
from decimal import Decimal

from .contracts import PlanResult, PlannedAction


PLANNER_PROMPT = """You are the planner agent for Financer V2.
Return a short actionable plan with concrete tool actions.
Prioritize setup actions that reduce user friction.
"""


def _extract_monthly_net(user_text: str) -> Decimal | None:
    pattern = re.compile(r"(?:salary\s*(?:is|=)?\s*|net\s*)(\d+(?:\.\d+)?)", re.IGNORECASE)
    match = pattern.search(user_text)
    if not match:
        # fallback: first standalone large number
        nums = re.findall(r"\b\d{3,}(?:\.\d+)?\b", user_text)
        if not nums:
            return None
        return Decimal(nums[0])
    return Decimal(match.group(1))


def _extract_pay_days(user_text: str) -> list[int]:
    matches = re.findall(r"\b([12]?\d|3[01])(?:st|nd|rd|th)?\b", user_text)
    days = sorted({int(item) for item in matches if 1 <= int(item) <= 31})
    return days[:4]


def _infer_schedule_kind(user_text: str, pay_days: list[int]) -> str | None:
    lowered = user_text.lower()
    if "semi" in lowered and "month" in lowered:
        return "semi_monthly"
    if "bi-month" in lowered:
        return "semi_monthly"
    if "bi week" in lowered or "bi-week" in lowered or "biweekly" in lowered:
        return "bi_weekly"
    if "weekly" in lowered:
        return "weekly"
    if "monthly" in lowered:
        return "monthly"
    if len(pay_days) >= 2:
        return "semi_monthly"
    return None


def _infer_source_type(user_text: str) -> str:
    lowered = user_text.lower()
    if "freelance" in lowered or "contract" in lowered:
        return "freelance"
    if "business" in lowered:
        return "business"
    if "investment" in lowered:
        return "investment"
    return "employment"


def _infer_payout_method(user_text: str) -> str | None:
    lowered = user_text.lower()
    if "cash" in lowered:
        return "cash"
    if "bank" in lowered or "transfer" in lowered:
        return "bank_transfer"
    if "gcash" in lowered or "maya" in lowered or "wallet" in lowered or "e-wallet" in lowered:
        return "digital_wallet"
    return None


class PlannerAgent:
    def plan(self, user_text: str) -> PlanResult:
        lowered = user_text.lower().strip()

        if not lowered:
            return PlanResult("Tell me what you want to do with your finances.")

        if any(phrase in lowered for phrase in ["show dashboard", "dashboard", "net worth"]):
            return PlanResult(
                assistant_message="I will fetch your dashboard metrics.",
                actions=[PlannedAction(name="get_dashboard", arguments={})],
            )

        if "stats" in lowered or "report" in lowered:
            period = "month"
            for candidate in ["day", "week", "month", "year"]:
                if candidate in lowered:
                    period = candidate
                    break
            return PlanResult(
                assistant_message=f"I will fetch your {period} stats.",
                actions=[PlannedAction(name="get_stats", arguments={"period": period})],
            )

        if "salary" in lowered or "income source" in lowered:
            monthly_net = _extract_monthly_net(user_text)
            pay_days = _extract_pay_days(user_text)
            schedule_kind = _infer_schedule_kind(user_text, pay_days)
            source_type = _infer_source_type(user_text)
            payout_method = _infer_payout_method(user_text)

            action_args = {
                "name": "Primary Salary",
                "source_type": source_type,
                "payout_method": payout_method,
                "is_salary": True,
                "monthly_net_amount": monthly_net,
                "pay_schedule_kind": schedule_kind,
                "pay_days": pay_days,
                "active": True,
            }
            filtered_args = {k: v for k, v in action_args.items() if v is not None}

            message = "I will set up your salary profile with dynamic taxonomy mapping."
            if monthly_net is None:
                return PlanResult(
                    assistant_message="I can set this up, but I need your monthly net salary amount.",
                    needs_follow_up=True,
                    follow_up_question="What is your monthly net salary?",
                )

            return PlanResult(
                assistant_message=message,
                actions=[PlannedAction(name="upsert_income_source_with_salary_profile", arguments=filtered_args)],
            )

        if lowered.startswith("create account") or "account" in lowered:
            account_type = "cash" if "cash" in lowered else "bank"
            name_match = re.search(r"named\s+([\w\s-]+)", user_text, flags=re.IGNORECASE)
            name = name_match.group(1).strip() if name_match else "Primary Account"
            opening_match = re.search(r"(?:opening\s+balance|balance)\s*(\d+(?:\.\d+)?)", user_text, flags=re.IGNORECASE)
            opening = Decimal(opening_match.group(1)) if opening_match else Decimal("0")
            return PlanResult(
                assistant_message="I will create that account.",
                actions=[
                    PlannedAction(
                        name="create_account",
                        arguments={
                            "name": name,
                            "account_type": account_type,
                            "is_liquid": True,
                            "opening_balance": opening,
                        },
                    )
                ],
            )

        return PlanResult(
            assistant_message=(
                "I can help with setup, salary profiles, accounts, transactions, dashboard, and stats. "
                "Tell me the exact action and I will execute it."
            ),
            actions=[],
        )
