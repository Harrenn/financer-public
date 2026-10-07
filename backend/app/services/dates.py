from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta


def last_day_of_month(year: int, month: int) -> int:
    return monthrange(year, month)[1]


def add_months(dt: date, months: int) -> date:
    month_idx = dt.month - 1 + months
    year = dt.year + month_idx // 12
    month = month_idx % 12 + 1
    day = min(dt.day, last_day_of_month(year, month))
    return date(year, month, day)


def compute_cycle_bounds(start: date, frequency: str) -> tuple[date, date]:
    if frequency == "weekly":
        return start, start + timedelta(days=6)
    if frequency == "bi_weekly":
        return start, start + timedelta(days=13)
    if frequency == "semi_monthly":
        if start.day <= 15:
            return date(start.year, start.month, 1), date(start.year, start.month, 15)
        return date(start.year, start.month, 16), date(start.year, start.month, last_day_of_month(start.year, start.month))

    # monthly defaults to payday-to-day-before-next-month payday
    end = add_months(start, 1) - timedelta(days=1)
    return start, end


def due_date_for_day(reference: date, due_day: int) -> date:
    day = min(due_day, last_day_of_month(reference.year, reference.month))
    return date(reference.year, reference.month, day)
