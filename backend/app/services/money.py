from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


TWOPLACES = Decimal("0.01")


def d(value: object) -> Decimal:
    return Decimal(str(value))


def money(value: Decimal | int | float | str) -> Decimal:
    return d(value).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
