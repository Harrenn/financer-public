from __future__ import annotations

from dataclasses import replace
from typing import Any

from sqlalchemy.orm import Session

from app.v2.taxonomy import resolve_taxonomy_term, taxonomy_value_to_dict

from .contracts import PlannedAction, ResolveResult


ONTOLOGY_PROMPT = """You are the ontology resolver agent.
Map user terms into taxonomy values with confidence and aliases.
Ask follow-up only when mapping is ambiguous.
"""


ACTION_FIELD_DOMAIN_MAP: dict[str, dict[str, str]] = {
    "create_account": {
        "account_type": "account_type",
    },
    "upsert_income_source_with_salary_profile": {
        "source_type": "income_source_type",
        "payout_method": "payout_method",
        "pay_schedule_kind": "pay_schedule_kind",
    },
    "create_transaction": {
        "kind": "transaction_kind",
    },
    "record_salary_payment_from_profile": {
        "kind": "transaction_kind",
    },
    "resolve_taxonomy_term": {
        "domain": "taxonomy_domain",
    },
}


class OntologyResolverAgent:
    def __init__(
        self,
        *,
        autocreate_threshold: float = 0.85,
        followup_threshold: float = 0.6,
    ):
        self.autocreate_threshold = autocreate_threshold
        self.followup_threshold = followup_threshold

    def resolve(self, db: Session, actions: list[PlannedAction]) -> ResolveResult:
        mapped_actions: list[PlannedAction] = []
        mappings: list[dict[str, Any]] = []

        for action in actions:
            field_map = ACTION_FIELD_DOMAIN_MAP.get(action.name, {})
            updated = dict(action.arguments)

            for field, domain in field_map.items():
                if field not in updated or updated[field] is None:
                    continue

                term = updated[field]
                resolution = resolve_taxonomy_term(
                    db,
                    domain=domain,
                    term=term,
                    autocreate=True,
                    created_by="ontology_agent",
                )

                if not resolution.resolved:
                    if resolution.needs_follow_up:
                        question = f"I found an ambiguous {domain} value for '{term}'. Please clarify."
                    else:
                        question = f"I could not map {domain} term '{term}'."
                    return ResolveResult(
                        actions=mapped_actions,
                        mappings=mappings,
                        needs_follow_up=True,
                        follow_up_question=question,
                    )

                if resolution.confidence < self.followup_threshold:
                    return ResolveResult(
                        actions=mapped_actions,
                        mappings=mappings,
                        needs_follow_up=True,
                        follow_up_question=f"I am not confident about {domain} '{term}'. Please confirm.",
                    )

                if resolution.confidence < self.autocreate_threshold and resolution.needs_follow_up:
                    return ResolveResult(
                        actions=mapped_actions,
                        mappings=mappings,
                        needs_follow_up=True,
                        follow_up_question=f"I found close matches for {domain} '{term}'. Which one should I use?",
                    )

                if resolution.value:
                    updated[field] = resolution.value.key
                    mappings.append(
                        {
                            "action": action.name,
                            "field": field,
                            "input_term": str(term),
                            "domain": domain,
                            "resolved": taxonomy_value_to_dict(resolution.value),
                            "confidence": resolution.confidence,
                            "created": resolution.created,
                        }
                    )

            mapped_actions.append(replace(action, arguments=updated))

        return ResolveResult(actions=mapped_actions, mappings=mappings)
