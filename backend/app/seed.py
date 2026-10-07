from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import DEFAULT_BUDGET_PROFILE, DEFAULT_CATEGORIES, DEFAULT_RUNTIME_SETTINGS, DEFAULT_SAVINGS_PRIORITY
from .models import BudgetProfile, Category, RuntimeSetting, SavingsPriorityConfig


def seed_defaults(db: Session) -> None:
    default_profile = db.scalar(select(BudgetProfile).where(BudgetProfile.is_default.is_(True)))
    if not default_profile:
        default_profile = BudgetProfile(**DEFAULT_BUDGET_PROFILE, is_default=True, active=True)
        db.add(default_profile)

    existing_categories = {name for name, in db.execute(select(Category.name)).all()}
    for category in DEFAULT_CATEGORIES:
        if category["name"] not in existing_categories:
            db.add(Category(**category))

    savings_config = db.get(SavingsPriorityConfig, 1)
    if not savings_config:
        db.add(SavingsPriorityConfig(id=1, **DEFAULT_SAVINGS_PRIORITY))

    existing_settings = {row.key for row in db.scalars(select(RuntimeSetting)).all()}
    for key, value in DEFAULT_RUNTIME_SETTINGS.items():
        if key not in existing_settings:
            db.add(RuntimeSetting(key=key, value_json=json.dumps(value), updated_by="system_seed"))

    db.commit()
