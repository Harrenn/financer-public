from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Callable

from app import db as db_module
from app.services.settings import (
    SettingsProposalError,
    SettingsValidationError,
    settings_confirm_proposal,
    settings_deny_proposal,
    settings_diff,
    settings_get,
    settings_history,
    settings_list,
    settings_list_proposals,
    settings_reset,
    settings_rollback,
    settings_update,
    settings_validate,
)
from app.v2 import finance
from app.v2.taxonomy import (
    create_taxonomy_alias,
    create_taxonomy_value,
    list_taxonomy_values,
    resolve_taxonomy_term,
    taxonomy_value_to_dict,
)
from agents import MultiAgentOrchestrator


ToolHandler = Callable[[dict[str, Any]], Any]


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler


class GeminiPlannerClient:
    """Placeholder for future role-based LLM planner integration."""

    def __init__(self, api_key: str, *, timeout: int = 30):
        self.api_key = api_key
        self.timeout = timeout



def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    return value


class AgenticFinanceRuntime:
    def __init__(self, llm_client: GeminiPlannerClient | None = None):
        self.llm_client = llm_client
        self.events: list[tuple[str, str]] = []
        self.last_run_id: int | None = None
        self.tools = self._build_tools()

    def _setting(self, key: str, default: Any) -> Any:
        db = db_module.SessionLocal()
        try:
            return settings_get(db, key)["value"]
        except Exception:
            return default
        finally:
            db.close()

    def _normalized_tool_policy(self) -> tuple[set[str], set[str]]:
        allow_raw = self._setting("safety.tool_allowlist", [])
        deny_raw = self._setting("safety.tool_denylist", [])

        allowlist = {
            str(item).strip()
            for item in (allow_raw if isinstance(allow_raw, list) else [])
            if str(item).strip()
        }
        denylist = {
            str(item).strip()
            for item in (deny_raw if isinstance(deny_raw, list) else [])
            if str(item).strip()
        }
        return allowlist, denylist

    def _build_tools(self) -> dict[str, ToolSpec]:
        specs: list[ToolSpec] = [
            ToolSpec("get_dashboard", "Get dashboard metrics.", {"type": "object", "properties": {}}, self._tool_get_dashboard),
            ToolSpec(
                "get_stats",
                "Get stats for day/week/month/year/custom.",
                {
                    "type": "object",
                    "properties": {
                        "period": {"type": "string", "enum": ["day", "week", "month", "year", "custom"]},
                        "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                        "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                    },
                },
                self._tool_get_stats,
            ),
            ToolSpec("list_accounts", "List accounts.", {"type": "object", "properties": {}}, self._tool_list_accounts),
            ToolSpec(
                "create_account",
                "Create or update account.",
                {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "account_type": {"type": ["string", "integer"]},
                        "is_liquid": {"type": "boolean"},
                        "opening_balance": {"type": "number"},
                    },
                    "required": ["name", "account_type"],
                },
                self._tool_create_account,
            ),
            ToolSpec("list_income_sources", "List income sources.", {"type": "object", "properties": {}}, self._tool_list_income_sources),
            ToolSpec(
                "upsert_income_source_with_salary_profile",
                "Create/update income source with salary profile.",
                {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "source_type": {"type": ["string", "integer"]},
                        "payout_method": {"type": ["string", "integer"]},
                        "is_salary": {"type": "boolean"},
                        "default_account_id": {"type": "integer"},
                        "monthly_net_amount": {"type": "number"},
                        "pay_schedule_kind": {"type": ["string", "integer"]},
                        "pay_days": {"type": "array", "items": {"type": "integer"}},
                        "active": {"type": "boolean"},
                    },
                    "required": ["name", "source_type"],
                },
                self._tool_upsert_income_source,
            ),
            ToolSpec(
                "record_salary_payment_from_profile",
                "Create salary income transaction from salary profile.",
                {
                    "type": "object",
                    "properties": {
                        "income_source_id": {"type": "integer"},
                        "date": {"type": "string", "description": "YYYY-MM-DD"},
                        "amount": {"type": "number"},
                        "to_account_id": {"type": "integer"},
                        "memo": {"type": "string"},
                    },
                    "required": ["income_source_id"],
                },
                self._tool_record_salary_payment,
            ),
            ToolSpec(
                "create_transaction",
                "Create transaction using taxonomy kind.",
                {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string"},
                        "kind": {"type": ["string", "integer"]},
                        "amount": {"type": "number"},
                        "from_account_id": {"type": "integer"},
                        "to_account_id": {"type": "integer"},
                        "income_source_id": {"type": "integer"},
                        "category_id": {"type": "integer"},
                        "memo": {"type": "string"},
                    },
                    "required": ["date", "kind", "amount"],
                },
                self._tool_create_transaction,
            ),
            ToolSpec(
                "resolve_taxonomy_term",
                "Resolve term to taxonomy value.",
                {
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string"},
                        "term": {"type": ["string", "integer"]},
                        "semantic_role": {"type": "string"},
                        "autocreate": {"type": "boolean"},
                    },
                    "required": ["domain", "term"],
                },
                self._tool_resolve_taxonomy_term,
            ),
            ToolSpec(
                "create_taxonomy_value",
                "Create taxonomy value.",
                {
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string"},
                        "label": {"type": "string"},
                        "key": {"type": "string"},
                        "semantic_role": {"type": "string"},
                    },
                    "required": ["domain", "label"],
                },
                self._tool_create_taxonomy_value,
            ),
            ToolSpec(
                "create_taxonomy_alias",
                "Create taxonomy alias mapping.",
                {
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string"},
                        "alias": {"type": "string"},
                        "taxonomy_value_id": {"type": "integer"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["domain", "alias", "taxonomy_value_id"],
                },
                self._tool_create_taxonomy_alias,
            ),
            ToolSpec("list_taxonomy_values", "List taxonomy values by domain.", {"type": "object", "properties": {"domain": {"type": "string"}}}, self._tool_list_taxonomy_values),
            ToolSpec(
                "ensure_cash_account",
                "Ensure a cash account exists and return it.",
                {"type": "object", "properties": {"name_hint": {"type": "string"}}},
                self._tool_ensure_cash_account,
            ),
            ToolSpec("settings_list", "List runtime settings.", {"type": "object", "properties": {}}, self._tool_settings_list),
            ToolSpec(
                "settings_get",
                "Read one runtime setting by key.",
                {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
                self._tool_settings_get,
            ),
            ToolSpec(
                "settings_validate",
                "Validate a settings change.",
                {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "new_value": {},
                        "reason": {"type": "string"},
                    },
                    "required": ["key", "new_value"],
                },
                self._tool_settings_validate,
            ),
            ToolSpec(
                "settings_update",
                "Update a setting.",
                {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "new_value": {},
                        "reason": {"type": "string"},
                    },
                    "required": ["key", "new_value"],
                },
                self._tool_settings_update,
            ),
            ToolSpec(
                "settings_reset",
                "Reset one setting to default.",
                {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["key"],
                },
                self._tool_settings_reset,
            ),
            ToolSpec("settings_diff", "List settings diffs.", {"type": "object", "properties": {"key": {"type": "string"}}}, self._tool_settings_diff),
            ToolSpec(
                "settings_history",
                "List settings history.",
                {"type": "object", "properties": {"limit": {"type": "integer"}, "key": {"type": "string"}}},
                self._tool_settings_history,
            ),
            ToolSpec(
                "settings_proposals",
                "List settings proposals.",
                {"type": "object", "properties": {"status": {"type": "string"}, "limit": {"type": "integer"}}},
                self._tool_settings_proposals,
            ),
            ToolSpec(
                "settings_confirm_proposal",
                "Confirm settings proposal.",
                {"type": "object", "properties": {"proposal_id": {"type": "string"}}, "required": ["proposal_id"]},
                self._tool_settings_confirm_proposal,
            ),
            ToolSpec(
                "settings_deny_proposal",
                "Deny settings proposal.",
                {"type": "object", "properties": {"proposal_id": {"type": "string"}}, "required": ["proposal_id"]},
                self._tool_settings_deny_proposal,
            ),
            ToolSpec(
                "settings_rollback",
                "Rollback a setting.",
                {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["key"],
                },
                self._tool_settings_rollback,
            ),
        ]
        return {spec.name: spec for spec in specs}

    def _dynamic_taxonomy_snapshot(self) -> dict[str, list[str]]:
        db = db_module.SessionLocal()
        try:
            out: dict[str, list[str]] = {}
            for domain in [
                "account_type",
                "income_source_type",
                "payout_method",
                "transaction_kind",
                "bucket_type",
                "liability_type",
                "loan_status",
                "pay_schedule_kind",
            ]:
                values = list_taxonomy_values(db, domain)
                out[domain] = [item.key for item in values[:20]]
            return out
        finally:
            db.close()

    def describe_tools(self) -> list[dict[str, Any]]:
        dynamic_snapshot = self._dynamic_taxonomy_snapshot()
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
                "dynamic_options": dynamic_snapshot,
            }
            for spec in self.tools.values()
        ]

    def run_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        spec = self.tools.get(name)
        if not spec:
            return {"ok": False, "error": f"Unknown tool: {name}"}

        allowlist, denylist = self._normalized_tool_policy()
        if name in denylist:
            return {"ok": False, "error": f"Tool blocked by safety.tool_denylist: {name}"}
        if allowlist and name not in allowlist:
            return {"ok": False, "error": f"Tool not in safety.tool_allowlist: {name}"}

        try:
            result = spec.handler(arguments or {})
            return {"ok": True, "result": _jsonable(result)}
        except (SettingsValidationError, SettingsProposalError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def handle_user_message(self, user_text: str) -> str:
        self.events.append(("user", user_text))

        if not bool(self._setting("multi_agent.enabled", True)):
            message = "multi_agent.enabled is false. Enable it via :settings set multi_agent.enabled true"
            self.events.append(("assistant", message))
            return message

        threshold = float(self._setting("policy.large_amount_confirmation_threshold", 1000.0))
        autocreate_threshold = float(self._setting("multi_agent.autocreate_confidence_threshold", 0.85))
        followup_threshold = float(self._setting("multi_agent.followup_confidence_threshold", 0.6))
        user_messages = [text for role, text in self.events if role == "user"]
        orchestrator_input = user_text
        if len(user_messages) >= 2 and len(user_text.split()) <= 14:
            orchestrator_input = " ".join(user_messages[-2:])

        orchestrator = MultiAgentOrchestrator(
            large_amount_threshold=threshold,
            autocreate_threshold=autocreate_threshold,
            followup_threshold=followup_threshold,
        )
        result = orchestrator.run(user_text=orchestrator_input, run_tool=self.run_tool)

        message = str(result.get("assistant_message", "Done."))
        self.last_run_id = result.get("run_id")
        self.events.append(("assistant", message))

        tool_outputs = result.get("tool_outputs") or []
        if tool_outputs:
            self.events.append(("tool", json.dumps(_jsonable(tool_outputs), ensure_ascii=True)))

        return message

    def get_last_trace(self) -> dict[str, Any] | None:
        db = db_module.SessionLocal()
        try:
            return finance.latest_agent_run(db)
        finally:
            db.close()

    # V2 Tool implementations
    def _tool_get_dashboard(self, args: dict[str, Any]) -> Any:
        db = db_module.SessionLocal()
        try:
            return finance.dashboard_metrics(db)
        finally:
            db.close()

    def _tool_get_stats(self, args: dict[str, Any]) -> Any:
        period = str(args.get("period", "month"))
        start_raw = args.get("start_date")
        end_raw = args.get("end_date")
        start_date = date.fromisoformat(start_raw) if start_raw else None
        end_date = date.fromisoformat(end_raw) if end_raw else None

        db = db_module.SessionLocal()
        try:
            return finance.compute_stats(db, period=period, start_date=start_date, end_date=end_date)
        finally:
            db.close()

    def _tool_list_accounts(self, args: dict[str, Any]) -> Any:
        db = db_module.SessionLocal()
        try:
            return finance.list_accounts(db)
        finally:
            db.close()

    def _tool_create_account(self, args: dict[str, Any]) -> Any:
        db = db_module.SessionLocal()
        try:
            payload = finance.create_account(
                db,
                name=str(args["name"]),
                account_type=args["account_type"],
                is_liquid=bool(args.get("is_liquid", True)),
                opening_balance=Decimal(str(args.get("opening_balance", "0.00"))),
            )
            db.commit()
            return payload
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _tool_list_income_sources(self, args: dict[str, Any]) -> Any:
        db = db_module.SessionLocal()
        try:
            return finance.list_income_sources(db)
        finally:
            db.close()

    def _tool_upsert_income_source(self, args: dict[str, Any]) -> Any:
        db = db_module.SessionLocal()
        try:
            payload = finance.upsert_income_source_with_salary_profile(
                db,
                name=str(args["name"]),
                source_type=args["source_type"],
                payout_method=args.get("payout_method"),
                is_salary=bool(args.get("is_salary", False)),
                default_account_id=args.get("default_account_id"),
                monthly_net_amount=Decimal(str(args["monthly_net_amount"])) if args.get("monthly_net_amount") is not None else None,
                pay_schedule_kind=args.get("pay_schedule_kind"),
                pay_days=[int(item) for item in args.get("pay_days", [])],
                active=bool(args.get("active", True)),
            )
            db.commit()
            return payload
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _tool_record_salary_payment(self, args: dict[str, Any]) -> Any:
        db = db_module.SessionLocal()
        try:
            payload = finance.record_salary_payment_from_profile(
                db,
                income_source_id=int(args["income_source_id"]),
                date_value=date.fromisoformat(args["date"]) if args.get("date") else None,
                amount=Decimal(str(args["amount"])) if args.get("amount") is not None else None,
                to_account_id=args.get("to_account_id"),
                memo=str(args.get("memo", "salary payment")),
            )
            db.commit()
            return payload
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _tool_create_transaction(self, args: dict[str, Any]) -> Any:
        db = db_module.SessionLocal()
        try:
            payload = finance.create_transaction(
                db,
                date_value=date.fromisoformat(str(args["date"])),
                kind=args["kind"],
                amount=Decimal(str(args["amount"])),
                from_account_id=args.get("from_account_id"),
                to_account_id=args.get("to_account_id"),
                income_source_id=args.get("income_source_id"),
                category_id=args.get("category_id"),
                memo=str(args.get("memo", "")),
            )
            db.commit()
            return payload
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _tool_resolve_taxonomy_term(self, args: dict[str, Any]) -> Any:
        db = db_module.SessionLocal()
        try:
            result = resolve_taxonomy_term(
                db,
                domain=str(args["domain"]),
                term=args["term"],
                semantic_role=args.get("semantic_role"),
                autocreate=bool(args.get("autocreate", True)),
                created_by="tool",
            )
            db.commit()
            return {
                "resolved": result.resolved,
                "confidence": result.confidence,
                "created": result.created,
                "needs_follow_up": result.needs_follow_up,
                "reason": result.reason,
                "value": taxonomy_value_to_dict(result.value) if result.value else None,
                "candidates": [taxonomy_value_to_dict(item) for item in result.candidates],
            }
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _tool_create_taxonomy_value(self, args: dict[str, Any]) -> Any:
        db = db_module.SessionLocal()
        try:
            value = create_taxonomy_value(
                db,
                domain=str(args["domain"]),
                label=str(args["label"]),
                key=str(args["key"]) if args.get("key") else None,
                semantic_role=str(args["semantic_role"]) if args.get("semantic_role") else None,
                is_system=False,
                metadata={"created_by": "tool"},
            )
            db.commit()
            db.refresh(value)
            return taxonomy_value_to_dict(value)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _tool_create_taxonomy_alias(self, args: dict[str, Any]) -> Any:
        db = db_module.SessionLocal()
        try:
            row = create_taxonomy_alias(
                db,
                domain=str(args["domain"]),
                alias=str(args["alias"]),
                taxonomy_value_id=int(args["taxonomy_value_id"]),
                confidence=float(args.get("confidence", 1.0)),
                created_by="tool",
            )
            db.commit()
            db.refresh(row)
            return {
                "id": row.id,
                "domain": row.domain,
                "alias": row.alias,
                "taxonomy_value_id": row.taxonomy_value_id,
                "confidence": row.confidence,
                "created_by": row.created_by,
                "created_at": row.created_at,
            }
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _tool_list_taxonomy_values(self, args: dict[str, Any]) -> Any:
        db = db_module.SessionLocal()
        try:
            rows = list_taxonomy_values(db, args.get("domain"))
            return [taxonomy_value_to_dict(item) for item in rows]
        finally:
            db.close()

    def _tool_ensure_cash_account(self, args: dict[str, Any]) -> Any:
        db = db_module.SessionLocal()
        try:
            payload = finance.ensure_cash_account(db, name_hint=str(args.get("name_hint", "Cash Wallet")))
            db.commit()
            return payload
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # Settings tools
    def _tool_settings_list(self, args: dict[str, Any]) -> Any:
        db = db_module.SessionLocal()
        try:
            return settings_list(db)
        finally:
            db.close()

    def _tool_settings_get(self, args: dict[str, Any]) -> Any:
        db = db_module.SessionLocal()
        try:
            return settings_get(db, str(args["key"]))
        finally:
            db.close()

    def _tool_settings_validate(self, args: dict[str, Any]) -> Any:
        db = db_module.SessionLocal()
        try:
            return settings_validate(
                db,
                str(args["key"]),
                args.get("new_value"),
            )
        finally:
            db.close()

    def _tool_settings_update(self, args: dict[str, Any]) -> Any:
        db = db_module.SessionLocal()
        try:
            result = settings_update(
                db,
                str(args["key"]),
                args.get("new_value"),
                reason=str(args.get("reason", "user_requested_change")),
            )
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _tool_settings_reset(self, args: dict[str, Any]) -> Any:
        db = db_module.SessionLocal()
        try:
            result = settings_reset(
                db,
                str(args["key"]),
                reason=str(args.get("reason", "reset_to_default")),
            )
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _tool_settings_diff(self, args: dict[str, Any]) -> Any:
        db = db_module.SessionLocal()
        try:
            return settings_diff(db, key=args.get("key"))
        finally:
            db.close()

    def _tool_settings_history(self, args: dict[str, Any]) -> Any:
        db = db_module.SessionLocal()
        try:
            rows = settings_history(
                db,
                limit=int(args.get("limit", 50)),
                key=args.get("key"),
            )
            return [
                {
                    "id": row.id,
                    "key": row.key,
                    "old_value_json": row.old_value_json,
                    "new_value_json": row.new_value_json,
                    "actor": row.actor,
                    "reason": row.reason,
                    "risk_level": row.risk_level,
                    "created_at": row.created_at,
                }
                for row in rows
            ]
        finally:
            db.close()

    def _tool_settings_proposals(self, args: dict[str, Any]) -> Any:
        db = db_module.SessionLocal()
        try:
            rows = settings_list_proposals(
                db,
                status=args.get("status"),
                limit=int(args.get("limit", 50)),
            )
            return [
                {
                    "id": row.id,
                    "key": row.key,
                    "old_value_json": row.old_value_json,
                    "new_value_json": row.new_value_json,
                    "actor": row.actor,
                    "reason": row.reason,
                    "risk_level": row.risk_level,
                    "status": row.status,
                    "created_at": row.created_at,
                    "resolved_at": row.resolved_at,
                }
                for row in rows
            ]
        finally:
            db.close()

    def _tool_settings_confirm_proposal(self, args: dict[str, Any]) -> Any:
        db = db_module.SessionLocal()
        try:
            result = settings_confirm_proposal(db, str(args["proposal_id"]))
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _tool_settings_deny_proposal(self, args: dict[str, Any]) -> Any:
        db = db_module.SessionLocal()
        try:
            result = settings_deny_proposal(db, str(args["proposal_id"]))
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _tool_settings_rollback(self, args: dict[str, Any]) -> Any:
        db = db_module.SessionLocal()
        try:
            result = settings_rollback(
                db,
                key=str(args["key"]),
                reason=str(args.get("reason", "rollback")),
            )
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
