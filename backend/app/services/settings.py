from __future__ import annotations

import json
import uuid
from calendar import monthrange
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import DEFAULT_RUNTIME_SETTINGS
from ..models import RuntimeSetting, SettingsChangeProposal, SettingsHistory


class SettingsValidationError(ValueError):
    pass


class SettingsProposalError(ValueError):
    pass


@dataclass(frozen=True)
class SettingRule:
    kind: str
    choices: set[str] | None = None
    minimum: float | None = None
    maximum: float | None = None


SENSITIVE_MARKERS = {"secret", "token", "password", "private_key", "api_key", "credential"}


SETTINGS_RULES: dict[str, SettingRule] = {
    "daemon.daily_time": SettingRule(kind="time"),
    "daemon.weekly_day": SettingRule(kind="day_of_week"),
    "daemon.weekly_time": SettingRule(kind="time"),
    "daemon.monthly_day": SettingRule(kind="int", minimum=1, maximum=31),
    "daemon.monthly_time": SettingRule(kind="time"),
    "daemon.annual_month": SettingRule(kind="int", minimum=1, maximum=12),
    "daemon.annual_day": SettingRule(kind="int", minimum=1, maximum=31),
    "daemon.annual_time": SettingRule(kind="time"),
    "policy.large_amount_confirmation_threshold": SettingRule(kind="float", minimum=0),
    "policy.large_amount_confirmation_hard_cap": SettingRule(kind="float", minimum=1),
    "alerts.channel": SettingRule(
        kind="enum",
        choices={"terminal_inbox", "terminal_stdout", "inbox_os_notify"},
    ),
    "stats.default_format": SettingRule(kind="enum", choices={"table", "json", "csv"}),
    "stats.default_scope": SettingRule(kind="enum", choices={"day", "week", "month", "year", "custom"}),
    "analytics.forecast_sensitivity": SettingRule(kind="enum", choices={"low", "medium", "high"}),
    "analytics.anomaly_sensitivity": SettingRule(kind="enum", choices={"low", "medium", "high"}),
    "model.name": SettingRule(kind="string"),
    "model.temperature": SettingRule(kind="float", minimum=0, maximum=2),
    "model.top_p": SettingRule(kind="float", minimum=0, maximum=1),
    "model.retry_limit": SettingRule(kind="int", minimum=0, maximum=10),
    "model.timeout_seconds": SettingRule(kind="int", minimum=5, maximum=300),
    "model.planner.name": SettingRule(kind="string"),
    "model.planner.temperature": SettingRule(kind="float", minimum=0, maximum=2),
    "model.planner.top_p": SettingRule(kind="float", minimum=0, maximum=1),
    "model.planner.retry_limit": SettingRule(kind="int", minimum=0, maximum=10),
    "model.planner.timeout_seconds": SettingRule(kind="int", minimum=5, maximum=300),
    "model.ontology.name": SettingRule(kind="string"),
    "model.ontology.temperature": SettingRule(kind="float", minimum=0, maximum=2),
    "model.ontology.top_p": SettingRule(kind="float", minimum=0, maximum=1),
    "model.ontology.retry_limit": SettingRule(kind="int", minimum=0, maximum=10),
    "model.ontology.timeout_seconds": SettingRule(kind="int", minimum=5, maximum=300),
    "model.critic.name": SettingRule(kind="string"),
    "model.critic.temperature": SettingRule(kind="float", minimum=0, maximum=2),
    "model.critic.top_p": SettingRule(kind="float", minimum=0, maximum=1),
    "model.critic.retry_limit": SettingRule(kind="int", minimum=0, maximum=10),
    "model.critic.timeout_seconds": SettingRule(kind="int", minimum=5, maximum=300),
    "model.executor.name": SettingRule(kind="string"),
    "model.executor.temperature": SettingRule(kind="float", minimum=0, maximum=2),
    "model.executor.top_p": SettingRule(kind="float", minimum=0, maximum=1),
    "model.executor.retry_limit": SettingRule(kind="int", minimum=0, maximum=10),
    "model.executor.timeout_seconds": SettingRule(kind="int", minimum=5, maximum=300),
    "multi_agent.autocreate_confidence_threshold": SettingRule(kind="float", minimum=0, maximum=1),
    "multi_agent.followup_confidence_threshold": SettingRule(kind="float", minimum=0, maximum=1),
    "multi_agent.enabled": SettingRule(kind="bool"),
    "safety.tool_allowlist": SettingRule(kind="string_list"),
    "safety.tool_denylist": SettingRule(kind="string_list"),
    "safety.max_actions_per_turn": SettingRule(kind="int", minimum=1, maximum=50),
    "safety.autonomous_depth_limit": SettingRule(kind="int", minimum=1, maximum=20),
}


def _now() -> datetime:
    return datetime.utcnow()


def _is_secret_like_key(key: str) -> bool:
    key_lower = key.lower()
    return any(marker in key_lower for marker in SENSITIVE_MARKERS)


def _ensure_allowed_key(key: str) -> None:
    if _is_secret_like_key(key):
        raise SettingsValidationError("Secret-bearing settings are not editable through agent settings tools.")
    if key not in SETTINGS_RULES:
        raise SettingsValidationError(f"Unknown setting key: {key}")


def _serialize(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _deserialize(raw: str) -> Any:
    return json.loads(raw)


def _normalize_day_of_week(value: str) -> str:
    normalized = value.strip().lower()
    valid = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
    if normalized not in valid:
        raise SettingsValidationError("Weekly day must be one of monday..sunday")
    return normalized


def _normalize_time(value: str) -> str:
    raw = value.strip()
    try:
        parsed = datetime.strptime(raw, "%H:%M")
    except ValueError as exc:
        raise SettingsValidationError("Time must use 24-hour HH:MM format") from exc
    return parsed.strftime("%H:%M")


def _normalize_list(value: Any) -> list[str]:
    if isinstance(value, list):
        items = [str(item).strip() for item in value]
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise SettingsValidationError("List settings must be valid JSON array or comma-separated string") from exc
            if not isinstance(parsed, list):
                raise SettingsValidationError("List settings JSON must be an array")
            items = [str(item).strip() for item in parsed]
        elif not stripped:
            items = []
        else:
            items = [part.strip() for part in stripped.split(",")]
    else:
        raise SettingsValidationError("List settings require a JSON list or comma-separated string")

    output: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not item:
            continue
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def _normalize_value(rule: SettingRule, value: Any) -> Any:
    if isinstance(value, Decimal):
        value = float(value)

    if rule.kind == "string":
        normalized = str(value).strip()
        if not normalized:
            raise SettingsValidationError("String setting cannot be empty")
        return normalized

    if rule.kind == "bool":
        if isinstance(value, bool):
            return value
        lowered = str(value).strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
        raise SettingsValidationError("Expected boolean value")

    if rule.kind == "enum":
        normalized = str(value).strip().lower()
        if not rule.choices or normalized not in rule.choices:
            choices = ", ".join(sorted(rule.choices or set()))
            raise SettingsValidationError(f"Invalid value; expected one of: {choices}")
        return normalized

    if rule.kind == "time":
        return _normalize_time(str(value))

    if rule.kind == "day_of_week":
        return _normalize_day_of_week(str(value))

    if rule.kind == "string_list":
        return _normalize_list(value)

    if rule.kind == "int":
        try:
            normalized_int = int(str(value).strip())
        except ValueError as exc:
            raise SettingsValidationError("Expected integer value") from exc
        if rule.minimum is not None and normalized_int < int(rule.minimum):
            raise SettingsValidationError(f"Value must be >= {int(rule.minimum)}")
        if rule.maximum is not None and normalized_int > int(rule.maximum):
            raise SettingsValidationError(f"Value must be <= {int(rule.maximum)}")
        return normalized_int

    if rule.kind == "float":
        try:
            normalized_float = float(str(value).strip())
        except ValueError as exc:
            raise SettingsValidationError("Expected numeric value") from exc
        if rule.minimum is not None and normalized_float < rule.minimum:
            raise SettingsValidationError(f"Value must be >= {rule.minimum}")
        if rule.maximum is not None and normalized_float > rule.maximum:
            raise SettingsValidationError(f"Value must be <= {rule.maximum}")
        return normalized_float

    raise SettingsValidationError(f"Unsupported setting rule type: {rule.kind}")


def _validate_cross_constraints(db: Session, key: str, normalized_value: Any) -> None:
    if key == "policy.large_amount_confirmation_threshold":
        hard_cap = float(settings_get(db, "policy.large_amount_confirmation_hard_cap")["value"])
        if float(normalized_value) > hard_cap:
            raise SettingsValidationError(
                f"Threshold cannot exceed hard cap ({hard_cap:g}). Increase the hard cap first."
            )
    elif key == "policy.large_amount_confirmation_hard_cap":
        threshold = float(settings_get(db, "policy.large_amount_confirmation_threshold")["value"])
        if float(normalized_value) < threshold:
            raise SettingsValidationError(
                f"Hard cap cannot be below current threshold ({threshold:g}). Lower threshold first."
            )
    elif key == "daemon.annual_month":
        day = int(settings_get(db, "daemon.annual_day")["value"])
        max_day = monthrange(2024, int(normalized_value))[1]
        if day > max_day:
            raise SettingsValidationError(
                f"annual_day={day} is invalid for month={int(normalized_value)}; max day is {max_day}"
            )
    elif key == "daemon.annual_day":
        month = int(settings_get(db, "daemon.annual_month")["value"])
        max_day = monthrange(2024, month)[1]
        if int(normalized_value) > max_day:
            raise SettingsValidationError(f"annual_day exceeds max day {max_day} for month={month}")


def _read_runtime_rows(db: Session) -> dict[str, RuntimeSetting]:
    return {row.key: row for row in db.scalars(select(RuntimeSetting)).all()}


def _current_value_from_rows(rows: dict[str, RuntimeSetting], key: str) -> Any:
    row = rows.get(key)
    if row:
        return _deserialize(row.value_json)
    return DEFAULT_RUNTIME_SETTINGS[key]


def _classify_risk(db: Session, key: str, old_value: Any, new_value: Any) -> str:
    if key.startswith("model."):
        return "critical"
    if key == "policy.large_amount_confirmation_hard_cap":
        return "critical"
    if key == "policy.large_amount_confirmation_threshold":
        return "critical" if float(new_value) > float(old_value) else "low"
    if key == "safety.max_actions_per_turn":
        return "critical" if int(new_value) > int(old_value) else "low"
    if key == "safety.autonomous_depth_limit":
        return "critical" if int(new_value) > int(old_value) else "low"
    if key == "safety.tool_allowlist":
        old_set = set(old_value or [])
        new_set = set(new_value or [])
        return "critical" if bool(new_set - old_set) else "low"
    if key == "safety.tool_denylist":
        old_set = set(old_value or [])
        new_set = set(new_value or [])
        return "critical" if bool(old_set - new_set) else "low"
    if key.startswith("safety.") and old_value != new_value:
        return "critical"
    return "low"


def settings_list(db: Session) -> list[dict[str, Any]]:
    rows = _read_runtime_rows(db)
    output: list[dict[str, Any]] = []
    for key in sorted(SETTINGS_RULES.keys()):
        value = _current_value_from_rows(rows, key)
        row = rows.get(key)
        output.append(
            {
                "key": key,
                "value": value,
                "default_value": DEFAULT_RUNTIME_SETTINGS[key],
                "is_default": value == DEFAULT_RUNTIME_SETTINGS[key],
                "updated_by": row.updated_by if row else "system_default",
                "updated_at": row.updated_at if row else None,
            }
        )
    return output


def settings_get(db: Session, key: str) -> dict[str, Any]:
    _ensure_allowed_key(key)
    rows = _read_runtime_rows(db)
    value = _current_value_from_rows(rows, key)
    row = rows.get(key)
    return {
        "key": key,
        "value": value,
        "default_value": DEFAULT_RUNTIME_SETTINGS[key],
        "is_default": value == DEFAULT_RUNTIME_SETTINGS[key],
        "updated_by": row.updated_by if row else "system_default",
        "updated_at": row.updated_at if row else None,
    }


def settings_validate(db: Session, key: str, new_value: Any) -> dict[str, Any]:
    _ensure_allowed_key(key)
    rule = SETTINGS_RULES[key]
    normalized = _normalize_value(rule, new_value)
    _validate_cross_constraints(db, key, normalized)

    current = settings_get(db, key)["value"]
    risk_level = _classify_risk(db, key, current, normalized)

    return {
        "key": key,
        "normalized_value": normalized,
        "old_value": current,
        "risk_level": risk_level,
    }


def _upsert_setting_row(db: Session, key: str, value: Any, actor: str) -> None:
    row = db.get(RuntimeSetting, key)
    if not row:
        row = RuntimeSetting(key=key, value_json=_serialize(value), updated_by=actor, updated_at=_now())
        db.add(row)
        return
    row.value_json = _serialize(value)
    row.updated_by = actor
    row.updated_at = _now()


def _append_history(
    db: Session,
    *,
    key: str,
    old_value: Any,
    new_value: Any,
    actor: str,
    reason: str,
    risk_level: str,
) -> SettingsHistory:
    row = SettingsHistory(
        key=key,
        old_value_json=_serialize(old_value),
        new_value_json=_serialize(new_value),
        actor=actor,
        reason=reason,
        risk_level=risk_level,
    )
    db.add(row)
    return row


def settings_update(
    db: Session,
    key: str,
    new_value: Any,
    *,
    reason: str,
    actor: str = "user_request_via_agent",
    auto_apply_critical: bool = False,
) -> dict[str, Any]:
    validation = settings_validate(db, key, new_value)
    normalized_value = validation["normalized_value"]
    old_value = validation["old_value"]
    risk_level = validation["risk_level"]

    if old_value == normalized_value:
        return {
            "key": key,
            "old_value": old_value,
            "new_value": normalized_value,
            "risk_level": risk_level,
            "applied": True,
            "proposal_id": None,
        }

    if risk_level == "critical" and not auto_apply_critical:
        proposal_id = f"scp_{uuid.uuid4().hex[:16]}"
        proposal = SettingsChangeProposal(
            id=proposal_id,
            key=key,
            old_value_json=_serialize(old_value),
            new_value_json=_serialize(normalized_value),
            actor=actor,
            reason=reason,
            risk_level=risk_level,
            status="pending",
        )
        db.add(proposal)
        return {
            "key": key,
            "old_value": old_value,
            "new_value": normalized_value,
            "risk_level": risk_level,
            "applied": False,
            "proposal_id": proposal_id,
        }

    _upsert_setting_row(db, key, normalized_value, actor)
    _append_history(
        db,
        key=key,
        old_value=old_value,
        new_value=normalized_value,
        actor=actor,
        reason=reason,
        risk_level=risk_level,
    )
    return {
        "key": key,
        "old_value": old_value,
        "new_value": normalized_value,
        "risk_level": risk_level,
        "applied": True,
        "proposal_id": None,
    }


def settings_reset(
    db: Session,
    key: str,
    *,
    reason: str = "reset_to_default",
    actor: str = "user_request_via_agent",
    auto_apply_critical: bool = False,
) -> dict[str, Any]:
    _ensure_allowed_key(key)
    return settings_update(
        db,
        key,
        DEFAULT_RUNTIME_SETTINGS[key],
        reason=reason,
        actor=actor,
        auto_apply_critical=auto_apply_critical,
    )


def settings_diff(db: Session, key: str | None = None) -> list[dict[str, Any]]:
    keys = [key] if key else sorted(SETTINGS_RULES.keys())
    output: list[dict[str, Any]] = []
    for item in keys:
        _ensure_allowed_key(item)
        current = settings_get(db, item)["value"]
        default_value = DEFAULT_RUNTIME_SETTINGS[item]
        output.append(
            {
                "key": item,
                "current_value": current,
                "default_value": default_value,
                "changed": current != default_value,
            }
        )
    return output


def settings_history(db: Session, *, limit: int = 50, key: str | None = None) -> list[SettingsHistory]:
    query = select(SettingsHistory)
    if key:
        _ensure_allowed_key(key)
        query = query.where(SettingsHistory.key == key)
    return db.scalars(query.order_by(SettingsHistory.created_at.desc(), SettingsHistory.id.desc()).limit(limit)).all()


def settings_list_proposals(db: Session, *, status: str | None = None, limit: int = 50) -> list[SettingsChangeProposal]:
    query = select(SettingsChangeProposal)
    if status:
        query = query.where(SettingsChangeProposal.status == status)
    return db.scalars(query.order_by(SettingsChangeProposal.created_at.desc()).limit(limit)).all()


def settings_confirm_proposal(
    db: Session,
    proposal_id: str,
    *,
    actor: str = "user_request_via_agent",
) -> dict[str, Any]:
    proposal = db.get(SettingsChangeProposal, proposal_id)
    if not proposal:
        raise SettingsProposalError("Settings proposal not found")
    if proposal.status != "pending":
        raise SettingsProposalError(f"Settings proposal is not pending (status={proposal.status})")

    old_value = _deserialize(proposal.old_value_json)
    new_value = _deserialize(proposal.new_value_json)

    _upsert_setting_row(db, proposal.key, new_value, actor)
    _append_history(
        db,
        key=proposal.key,
        old_value=old_value,
        new_value=new_value,
        actor=actor,
        reason=proposal.reason,
        risk_level=proposal.risk_level,
    )

    proposal.status = "applied"
    proposal.resolved_at = _now()
    proposal.resolved_by = actor

    return {
        "key": proposal.key,
        "old_value": old_value,
        "new_value": new_value,
        "risk_level": proposal.risk_level,
        "applied": True,
        "proposal_id": proposal.id,
    }


def settings_deny_proposal(
    db: Session,
    proposal_id: str,
    *,
    actor: str = "user_request_via_agent",
) -> dict[str, Any]:
    proposal = db.get(SettingsChangeProposal, proposal_id)
    if not proposal:
        raise SettingsProposalError("Settings proposal not found")
    if proposal.status != "pending":
        raise SettingsProposalError(f"Settings proposal is not pending (status={proposal.status})")

    proposal.status = "denied"
    proposal.resolved_at = _now()
    proposal.resolved_by = actor
    return {"proposal_id": proposal.id, "status": proposal.status}


def settings_rollback(
    db: Session,
    key: str,
    *,
    reason: str = "rollback",
    actor: str = "user_request_via_agent",
    auto_apply_critical: bool = False,
) -> dict[str, Any]:
    _ensure_allowed_key(key)
    latest = db.scalar(
        select(SettingsHistory)
        .where(SettingsHistory.key == key)
        .order_by(SettingsHistory.created_at.desc(), SettingsHistory.id.desc())
    )
    if not latest:
        raise SettingsProposalError("No setting history found to roll back")
    return settings_update(
        db,
        key,
        _deserialize(latest.old_value_json),
        reason=reason,
        actor=actor,
        auto_apply_critical=auto_apply_critical,
    )
