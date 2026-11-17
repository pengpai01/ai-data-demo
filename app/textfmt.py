"""Display formatters. Rounded text is what the page and the model are allowed to quote."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")
PP = Decimal("0.01")


def q2(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def money_text(value: Decimal) -> str:
    amount = q2(value)
    sign = "-" if amount < 0 else ""
    return f"{sign}{abs(amount):,.2f}元"


def pct_text(value: Decimal | None) -> str:
    if value is None:
        return "本数据无法支持"
    amount = (value * Decimal(100)).quantize(PP, rounding=ROUND_HALF_UP)
    return f"{amount}%"


def pct_signed(value: Decimal | None) -> str:
    if value is None:
        return "本数据无法支持"
    amount = (value * Decimal(100)).quantize(PP, rounding=ROUND_HALF_UP)
    if amount > 0:
        return f"+{amount}%"
    return f"{amount}%"


def pp_text(value: Decimal | None) -> str:
    if value is None:
        return "本数据无法支持"
    amount = (value * Decimal(100)).quantize(PP, rounding=ROUND_HALF_UP)
    if amount > 0:
        return f"+{amount}pp"
    return f"{amount}pp"


def days_text(value: Decimal | None) -> str:
    if value is None:
        return "本数据无法支持"
    amount = value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if amount > 0:
        return f"{amount}天"
    return f"{amount}天"


def days_signed(value: Decimal | None) -> str:
    if value is None:
        return "本数据无法支持"
    amount = value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if amount > 0:
        return f"+{amount}天"
    return f"{amount}天"


def pct_num(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float((value * Decimal(100)).quantize(PP, rounding=ROUND_HALF_UP))


def pp_num(value: Decimal | None) -> float | None:
    return pct_num(value)


def money_num(value: Decimal) -> float:
    return float(q2(value))


def days_num(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def period_label(ym: str) -> str:
    year, month = ym.split("-")
    return f"{year}年{int(month)}月"


def tone_of(delta: Decimal | None, better: str) -> str:
    if delta is None or delta == 0:
        return "flat"
    improved = delta > 0 if better == "up" else delta < 0
    return "good" if improved else "bad"


def slice_title(dimension: str, name: str) -> str:
    if dimension == "region":
        return f"{name}大区"
    if dimension == "product_line":
        return f"{name}产品线"
    if dimension == "tier":
        return f"{name}代理商" if name.endswith("级") else f"{name}级代理商"
    return name
