from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import TaxonomyAlias, TaxonomyValue


@dataclass
class TaxonomyResolution:
    resolved: bool
    confidence: float
    created: bool
    needs_follow_up: bool
    reason: str
    value: TaxonomyValue | None
    candidates: list[TaxonomyValue]


def _normalize_domain(domain: str) -> str:
    return domain.strip().lower()


def _normalize_term(term: str) -> str:
    normalized = term.strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower()).strip("_")
    return slug or "item"


def taxonomy_value_to_dict(value: TaxonomyValue) -> dict[str, Any]:
    try:
        metadata = json.loads(value.metadata_json or "{}")
    except json.JSONDecodeError:
        metadata = {}
    return {
        "id": value.id,
        "domain": value.domain,
        "key": value.key,
        "label": value.label,
        "semantic_role": value.semantic_role,
        "is_system": value.is_system,
        "active": value.active,
        "metadata": metadata,
    }


def _ensure_unique_key(db: Session, domain: str, base_key: str) -> str:
    candidate = base_key
    idx = 2
    while db.scalar(select(TaxonomyValue).where(TaxonomyValue.domain == domain, TaxonomyValue.key == candidate)):
        candidate = f"{base_key}_{idx}"
        idx += 1
    return candidate


def list_taxonomy_values(db: Session, domain: str | None = None) -> list[TaxonomyValue]:
    query = select(TaxonomyValue).where(TaxonomyValue.active.is_(True)).order_by(TaxonomyValue.domain.asc(), TaxonomyValue.label.asc())
    if domain:
        query = query.where(TaxonomyValue.domain == _normalize_domain(domain))
    return db.scalars(query).all()


def create_taxonomy_value(
    db: Session,
    *,
    domain: str,
    label: str,
    key: str | None = None,
    semantic_role: str | None = None,
    is_system: bool = False,
    metadata: dict[str, Any] | None = None,
) -> TaxonomyValue:
    normalized_domain = _normalize_domain(domain)
    base_key = _slug(key or label)
    unique_key = _ensure_unique_key(db, normalized_domain, base_key)

    value = TaxonomyValue(
        domain=normalized_domain,
        key=unique_key,
        label=label.strip(),
        semantic_role=semantic_role,
        is_system=is_system,
        active=True,
        metadata_json=json.dumps(metadata or {}, separators=(",", ":"), sort_keys=True),
    )
    db.add(value)
    db.flush()
    return value


def create_taxonomy_alias(
    db: Session,
    *,
    domain: str,
    alias: str,
    taxonomy_value_id: int,
    confidence: float = 1.0,
    created_by: str = "agent",
) -> TaxonomyAlias:
    normalized_domain = _normalize_domain(domain)
    normalized_alias = _normalize_term(alias)

    existing = db.scalar(
        select(TaxonomyAlias).where(
            TaxonomyAlias.domain == normalized_domain,
            TaxonomyAlias.alias == normalized_alias,
        )
    )
    if existing:
        existing.taxonomy_value_id = taxonomy_value_id
        existing.confidence = confidence
        existing.created_by = created_by
        db.flush()
        return existing

    row = TaxonomyAlias(
        domain=normalized_domain,
        alias=normalized_alias,
        taxonomy_value_id=taxonomy_value_id,
        confidence=confidence,
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    return row


def _find_exact_match(db: Session, domain: str, term: str) -> TaxonomyValue | None:
    normalized = _normalize_term(term)

    alias = db.scalar(
        select(TaxonomyAlias).where(
            TaxonomyAlias.domain == domain,
            TaxonomyAlias.alias == normalized,
        )
    )
    if alias:
        return db.get(TaxonomyValue, alias.taxonomy_value_id)

    by_key = db.scalar(
        select(TaxonomyValue).where(
            TaxonomyValue.domain == domain,
            TaxonomyValue.key == _slug(normalized),
            TaxonomyValue.active.is_(True),
        )
    )
    if by_key:
        return by_key

    by_label = db.scalar(
        select(TaxonomyValue).where(
            TaxonomyValue.domain == domain,
            func.lower(TaxonomyValue.label) == normalized,
            TaxonomyValue.active.is_(True),
        )
    )
    if by_label:
        return by_label

    return None


def _fuzzy_candidates(db: Session, domain: str, term: str) -> list[tuple[TaxonomyValue, float]]:
    normalized = _normalize_term(term)
    values = list_taxonomy_values(db, domain)
    score_by_id: dict[int, float] = {}

    aliases = db.scalars(select(TaxonomyAlias).where(TaxonomyAlias.domain == domain)).all()
    alias_map: dict[int, list[str]] = {}
    for alias in aliases:
        alias_map.setdefault(alias.taxonomy_value_id, []).append(alias.alias)

    for value in values:
        texts = {value.key, value.label.lower(), _normalize_term(value.label)}
        texts.update(alias_map.get(value.id, []))
        best = max(SequenceMatcher(None, normalized, text).ratio() for text in texts)
        score_by_id[value.id] = best

    ranked = sorted(((v, score_by_id[v.id]) for v in values), key=lambda item: item[1], reverse=True)
    return ranked


def resolve_taxonomy_term(
    db: Session,
    *,
    domain: str,
    term: str | int,
    semantic_role: str | None = None,
    autocreate: bool = True,
    created_by: str = "agent",
) -> TaxonomyResolution:
    normalized_domain = _normalize_domain(domain)

    if isinstance(term, int) or (isinstance(term, str) and term.strip().isdigit()):
        item = db.get(TaxonomyValue, int(term))
        if item and item.domain == normalized_domain and item.active:
            return TaxonomyResolution(True, 1.0, False, False, "resolved_by_id", item, [])

    term_text = str(term).strip()
    if not term_text:
        return TaxonomyResolution(False, 0.0, False, True, "empty_term", None, [])

    exact = _find_exact_match(db, normalized_domain, term_text)
    if exact:
        return TaxonomyResolution(True, 1.0, False, False, "exact_match", exact, [])

    ranked = _fuzzy_candidates(db, normalized_domain, term_text)
    top_candidates = [item[0] for item in ranked[:3] if item[1] >= 0.6]

    if ranked and ranked[0][1] >= 0.92:
        best_value, score = ranked[0]
        return TaxonomyResolution(True, float(score), False, False, "fuzzy_high_confidence", best_value, top_candidates)

    if ranked and ranked[0][1] >= 0.7:
        reason = "ambiguous_term"
        return TaxonomyResolution(False, float(ranked[0][1]), False, True, reason, None, top_candidates)

    if autocreate:
        created = create_taxonomy_value(
            db,
            domain=normalized_domain,
            label=term_text,
            key=None,
            semantic_role=semantic_role,
            is_system=False,
            metadata={"autocreated": True},
        )
        create_taxonomy_alias(
            db,
            domain=normalized_domain,
            alias=term_text,
            taxonomy_value_id=created.id,
            confidence=0.86,
            created_by=created_by,
        )
        return TaxonomyResolution(True, 0.86, True, False, "autocreated", created, [])

    reason = "no_match"
    return TaxonomyResolution(False, 0.0, False, True, reason, None, top_candidates)
