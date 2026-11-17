"""Deterministic 销售回款 metrics. The model never computes these."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.constants import (
    BUCKET_UNDUE,
    BUCKETS,
    DATA_END,
    DATA_START,
    LINE_ORDER,
    REGION_ORDER,
    SHARE_MIN,
    TIER_ORDER,
)
from app.store import Store

ZERO = Decimal("0")


@dataclass(frozen=True)
class Scope:
    region_id: str | None = None
    product_line: str | None = None
    tier: str | None = None
    customer_id: str | None = None
    cohort: str | None = None

    def narrow(self, **kwargs: str | None) -> "Scope":
        data = {
            "region_id": self.region_id,
            "product_line": self.product_line,
            "tier": self.tier,
            "customer_id": self.customer_id,
            "cohort": self.cohort,
        }
        data.update(kwargs)
        return Scope(**data)


def shift_month(ym: str, delta: int) -> str:
    year, month = map(int, ym.split("-"))
    month += delta
    while month <= 0:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    return f"{year:04d}-{month:02d}"


def month_start(ym: str) -> date:
    year, month = map(int, ym.split("-"))
    return date(year, month, 1)


def month_end(ym: str) -> date:
    year, month = map(int, ym.split("-"))
    return date(year, month, calendar.monthrange(year, month)[1])


def month_days(ym: str) -> int:
    year, month = map(int, ym.split("-"))
    return calendar.monthrange(year, month)[1]


def iter_months(start: str, end: str) -> list[str]:
    out = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur = shift_month(cur, 1)
    return out


def window_start(ym: str) -> date:
    """First day of the 12 calendar months ending in ym."""
    return month_start(shift_month(ym, -11))


def cohort_of(store: Store, customer_id: str, ym: str) -> str | None:
    """新 = first shipment inside the trailing 12 months; 老 = earlier.

    Customers whose first shipment is after this month are not in either group.
    """
    first = store.first_shipment.get(customer_id)
    if first is None or first > month_end(ym):
        return None
    if first >= window_start(ym):
        return "new"
    return "old"


def match_row(store: Store, row: dict, scope: Scope, ym: str) -> bool:
    if scope.region_id and row["region_id"] != scope.region_id:
        return False
    if scope.customer_id and row["customer_id"] != scope.customer_id:
        return False
    if scope.product_line:
        product_id = row.get("product_id") or ""
        if not product_id:
            return False
        if store.products[product_id]["product_line"] != scope.product_line:
            return False
    customer = store.customers[row["customer_id"]]
    if scope.tier and customer["customer_tier"] != scope.tier:
        return False
    if scope.cohort and cohort_of(store, row["customer_id"], ym) != scope.cohort:
        return False
    return True


def _sum(rows: list[dict], key: str) -> Decimal:
    total = ZERO
    for row in rows:
        total += row[key]
    return total


def shipments(store: Store, ym: str, scope: Scope) -> list[dict]:
    return [row for row in store.shipments_by_month.get(ym, []) if match_row(store, row, scope, ym)]


def collection_rows(store: Store, ym: str, scope: Scope) -> list[dict]:
    return [row for row in store.collections_by_month.get(ym, []) if match_row(store, row, scope, ym)]


def snapshot_rows(store: Store, ym: str, scope: Scope) -> list[dict]:
    return [row for row in store.snapshots_by_month.get(ym, []) if match_row(store, row, scope, ym)]


def net_sales(store: Store, ym: str, scope: Scope = Scope()) -> Decimal:
    return _sum(shipments(store, ym, scope), "net_amount")


def list_amount(store: Store, ym: str, scope: Scope = Scope()) -> Decimal:
    return _sum(shipments(store, ym, scope), "list_amount")


def discount_amount(store: Store, ym: str, scope: Scope = Scope()) -> Decimal:
    return _sum(shipments(store, ym, scope), "discount_amount")


def collection_amount(store: Store, ym: str, scope: Scope = Scope()) -> Decimal:
    return _sum(collection_rows(store, ym, scope), "amount")


def collection_rate(store: Store, ym: str, scope: Scope = Scope()) -> Decimal | None:
    sales = net_sales(store, ym, scope)
    if sales == 0:
        return None
    return collection_amount(store, ym, scope) / sales


def discount_rate(store: Store, ym: str, scope: Scope = Scope()) -> Decimal | None:
    gross = list_amount(store, ym, scope)
    if gross == 0:
        return None
    return discount_amount(store, ym, scope) / gross


def aging_amounts(store: Store, ym: str, scope: Scope = Scope()) -> dict[str, Decimal]:
    totals = {bucket: ZERO for bucket in BUCKETS}
    for row in snapshot_rows(store, ym, scope):
        totals[row["aging_bucket"]] += row["outstanding_amount"]
    return totals


def ar_balance(store: Store, ym: str, scope: Scope = Scope()) -> Decimal:
    return sum(aging_amounts(store, ym, scope).values(), ZERO)


def overdue_balance(store: Store, ym: str, scope: Scope = Scope()) -> Decimal:
    amounts = aging_amounts(store, ym, scope)
    return sum((amt for bucket, amt in amounts.items() if bucket != BUCKET_UNDUE), ZERO)


def overdue_rate(store: Store, ym: str, scope: Scope = Scope()) -> Decimal | None:
    balance = ar_balance(store, ym, scope)
    if balance == 0:
        return None
    return overdue_balance(store, ym, scope) / balance


def due_amount(store: Store, ym: str, scope: Scope = Scope()) -> Decimal:
    """Original net of invoices whose due date falls in the month.

    Due date = shipment date + the agent's payment_terms_days.
    Paid and unpaid invoices both count. This is 到期应收额.
    """
    year, month = map(int, ym.split("-"))
    total = ZERO
    for row in store.shipments:
        customer = store.customers[row["customer_id"]]
        due = row["shipment_date"] + timedelta(days=customer["payment_terms_days"])
        if due.year != year or due.month != month:
            continue
        if match_row(store, row, scope, ym):
            total += row["net_amount"]
    return total


def due_collection_rate(store: Store, ym: str, scope: Scope = Scope()) -> Decimal | None:
    due = due_amount(store, ym, scope)
    if due == 0:
        return None
    return collection_amount(store, ym, scope) / due


def dso(store: Store, ym: str, scope: Scope = Scope()) -> Decimal | None:
    """Ending AR / trailing-3-month average daily net sales.

    Returns None when any of the three months is outside the demo span.
    """
    months = [shift_month(ym, -2), shift_month(ym, -1), ym]
    if any(month < DATA_START or month > DATA_END for month in months):
        return None
    sales = ZERO
    days = 0
    for month in months:
        sales += net_sales(store, month, scope)
        days += month_days(month)
    if sales == 0:
        return None
    return ar_balance(store, ym, scope) / (sales / Decimal(days))


def weighted_age_days(store: Store, ym: str, scope: Scope = Scope()) -> Decimal | None:
    """Outstanding-weighted days since invoice date, from the month-end snapshot."""
    rows = snapshot_rows(store, ym, scope)
    end = month_end(ym)
    weight = ZERO
    acc = ZERO
    for row in rows:
        weight += row["outstanding_amount"]
        acc += row["outstanding_amount"] * Decimal((end - row["invoice_date"]).days)
    if weight == 0:
        return None
    return acc / weight


def decompose(c0: Decimal, s0: Decimal, c1: Decimal, s1: Decimal) -> dict | None:
    """Split Δ(C/S) into a collection effect and a net-sales effect.

    回款额变化贡献 = (C1 - C0) / S0
    净销售额变化贡献 = C1 / S1 - C1 / S0
    The two effects add up to r1 - r0 exactly (Decimal arithmetic).
    """
    if s0 == 0 or s1 == 0:
        return None
    rate0 = c0 / s0
    rate1 = c1 / s1
    collection_effect = (c1 - c0) / s0
    sales_effect = (c1 / s1) - (c1 / s0)
    return {
        "rate0": rate0,
        "rate1": rate1,
        "delta": rate1 - rate0,
        "collection_effect": collection_effect,
        "sales_effect": sales_effect,
    }


def dimension_members(dimension: str) -> list[tuple[str, str]]:
    if dimension == "region":
        return [(rid, _region_label(rid)) for rid in REGION_ORDER]
    if dimension == "product_line":
        return [(line, line) for line in LINE_ORDER]
    if dimension == "tier":
        return [(tier, f"{tier}级") for tier in TIER_ORDER]
    if dimension == "cohort":
        return [("new", "新代理商"), ("old", "老代理商")]
    raise KeyError(dimension)


def _region_label(region_id: str) -> str:
    return {
        "R-HD": "华东",
        "R-HN": "华南",
        "R-HB": "华北",
        "R-XN": "西南",
        "R-XB": "西北",
    }[region_id]


def apply_member(scope: Scope, dimension: str, key: str) -> Scope:
    if dimension == "region":
        return scope.narrow(region_id=key)
    if dimension == "product_line":
        return scope.narrow(product_line=key)
    if dimension == "tier":
        return scope.narrow(tier=key)
    if dimension == "cohort":
        return scope.narrow(cohort=key)
    raise KeyError(dimension)


def rate_slices(store: Store, ym: str, compare: str, dimension: str, parent: Scope | None = None) -> list[dict]:
    """Own-rate comparison for each member. Share is versus the whole company."""
    parent = parent or Scope()
    company_net = net_sales(store, ym, Scope())
    rows = []
    for key, name in dimension_members(dimension):
        child = apply_member(parent, dimension, key)
        net1 = net_sales(store, ym, child)
        net0 = net_sales(store, compare, child)
        col1 = collection_amount(store, ym, child)
        col0 = collection_amount(store, compare, child)
        parts = decompose(col0, net0, col1, net1)
        share = (net1 / company_net) if company_net else ZERO
        rows.append(
            {
                "key": key,
                "name": name,
                "net": net1,
                "prev_net": net0,
                "collection": col1,
                "prev_collection": col0,
                "list_amount": list_amount(store, ym, child),
                "prev_list": list_amount(store, compare, child),
                "discount": discount_amount(store, ym, child),
                "prev_discount": discount_amount(store, compare, child),
                "rate": None if parts is None else parts["rate1"],
                "prev_rate": None if parts is None else parts["rate0"],
                "delta": None if parts is None else parts["delta"],
                "share": share,
                "eligible": parts is not None and share >= SHARE_MIN,
            }
        )
    return rows


def select_fastest(slices: list[dict]) -> dict | None:
    """Lowest (most negative) pp change among slices with share >= 5%."""
    eligible = [row for row in slices if row["eligible"] and row["delta"] is not None]
    if not eligible:
        return None
    return min(eligible, key=lambda row: (row["delta"], row["key"]))


def choose_default_view(region_slices: list[dict], product_slices: list[dict]) -> tuple[str, dict | None, dict | None]:
    """Default-expand the eligible slice with the larger decline. Tie → 大区."""
    region_best = select_fastest(region_slices)
    product_best = select_fastest(product_slices)
    if region_best is None and product_best is None:
        return "region", None, None
    if product_best is None:
        return "region", region_best, None
    if region_best is None:
        return "product", product_best, None
    if region_best["delta"] <= product_best["delta"]:
        return "region", region_best, product_best
    return "product", product_best, region_best


def contributions(store: Store, ym: str, compare: str, parent: Scope, dimension: str) -> list[dict]:
    """Cross-dimension contribution to the parent rate change.

    贡献 = C1_i / S1_parent - C0_i / S0_parent
    Sum across i equals the parent's Δ销售回款率.
    贡献额 = 贡献 × 上期父级净销售额.
    """
    net1 = net_sales(store, ym, parent)
    net0 = net_sales(store, compare, parent)
    rows = []
    for key, name in dimension_members(dimension):
        child = apply_member(parent, dimension, key)
        c1 = collection_amount(store, ym, child)
        c0 = collection_amount(store, compare, child)
        s1 = net_sales(store, ym, child)
        s0 = net_sales(store, compare, child)
        if net0 == 0 or net1 == 0:
            contrib = coll_e = sales_e = None
            amount = None
        else:
            contrib = c1 / net1 - c0 / net0
            coll_e = (c1 - c0) / net0
            sales_e = c1 / net1 - c1 / net0
            amount = contrib * net0
        rows.append(
            {
                "key": key,
                "name": name,
                "net": s1,
                "prev_net": s0,
                "collection": c1,
                "prev_collection": c0,
                "contrib": contrib,
                "collection_effect": coll_e,
                "sales_effect": sales_e,
                "contrib_amount": amount,
                "rate": None if s1 == 0 else c1 / s1,
                "prev_rate": None if s0 == 0 else c0 / s0,
            }
        )
    return rows


def customer_contribs(store: Store, ym: str, compare: str, scope: Scope) -> list[dict]:
    """Agents ranked by contribution to the scope's 销售回款率 (most negative first)."""
    net1 = net_sales(store, ym, scope)
    net0 = net_sales(store, compare, scope)
    ids: set[str] = set()
    for month in (ym, compare):
        for row in shipments(store, month, scope):
            ids.add(row["customer_id"])
        for row in collection_rows(store, month, scope):
            ids.add(row["customer_id"])
    rows = []
    for customer_id in ids:
        child = scope.narrow(customer_id=customer_id)
        c1 = collection_amount(store, ym, child)
        c0 = collection_amount(store, compare, child)
        s1 = net_sales(store, ym, child)
        s0 = net_sales(store, compare, child)
        customer = store.customers[customer_id]
        if net0 == 0 or net1 == 0:
            contrib = None
            amount = None
        else:
            contrib = c1 / net1 - c0 / net0
            amount = contrib * net0
        rows.append(
            {
                "customer_id": customer_id,
                "customer_name": customer["customer_name"],
                "tier": customer["customer_tier"],
                "region_id": customer["region_id"],
                "region_name": store.region_name[customer["region_id"]],
                "net": s1,
                "prev_net": s0,
                "collection": c1,
                "prev_collection": c0,
                "contrib": contrib,
                "contrib_amount": amount,
            }
        )
    rows.sort(key=lambda row: ((row["contrib"] is None), row["contrib"] if row["contrib"] is not None else ZERO, row["customer_id"]))
    return rows


def aging_migration(store: Store, ym: str, compare: str, scope: Scope) -> list[dict]:
    curr = aging_amounts(store, ym, scope)
    prev = aging_amounts(store, compare, scope)
    curr_total = sum(curr.values(), ZERO)
    prev_total = sum(prev.values(), ZERO)
    rows = []
    for bucket in BUCKETS:
        curr_share = (curr[bucket] / curr_total) if curr_total else None
        prev_share = (prev[bucket] / prev_total) if prev_total else None
        share_delta = None
        if curr_share is not None and prev_share is not None:
            share_delta = curr_share - prev_share
        rows.append(
            {
                "bucket": bucket,
                "amount": curr[bucket],
                "prev_amount": prev[bucket],
                "amount_change": curr[bucket] - prev[bucket],
                "share": curr_share,
                "prev_share": prev_share,
                "share_delta": share_delta,
            }
        )
    return rows


def month_timing(store: Store, ym: str, scope: Scope) -> dict | None:
    rows = shipments(store, ym, scope)
    total = _sum(rows, "net_amount")
    if total == 0:
        return None
    end = month_end(ym)
    late_from = end - timedelta(days=6)
    early_to = month_start(ym) + timedelta(days=6)
    late = ZERO
    early = ZERO
    for row in rows:
        if row["shipment_date"] >= late_from:
            late += row["net_amount"]
        if row["shipment_date"] <= early_to:
            early += row["net_amount"]
    return {"late_share": late / total, "early_share": early / total, "net": total}


def large_due_invoices(store: Store, ym: str, scope: Scope, limit: int | None = 5) -> list[dict]:
    year, month = map(int, ym.split("-"))
    end = month_end(ym)
    found = []
    for row in store.shipments:
        customer = store.customers[row["customer_id"]]
        due = row["shipment_date"] + timedelta(days=customer["payment_terms_days"])
        if due.year != year or due.month != month:
            continue
        if not match_row(store, row, scope, ym):
            continue
        paid = ZERO
        for col in store.collections_by_invoice.get(row["invoice_id"], []):
            if col["collection_date"] <= end:
                paid += col["amount"]
        outstanding = row["net_amount"] - paid
        found.append(
            {
                "invoice_id": row["invoice_id"],
                "customer_id": row["customer_id"],
                "customer_name": customer["customer_name"],
                "tier": customer["customer_tier"],
                "product_line": store.products[row["product_id"]]["product_line"],
                "due_date": due.isoformat(),
                "original": row["net_amount"],
                "outstanding": outstanding,
            }
        )
    found.sort(key=lambda item: (-item["original"], item["invoice_id"]))
    if limit is None:
        return found
    return found[:limit]


def avg_collection_days(store: Store, ym: str, scope: Scope, months: int = 6) -> Decimal | None:
    """Amount-weighted days from invoice date to collection date, trailing window."""
    acc = ZERO
    weight = ZERO
    for offset in range(months - 1, -1, -1):
        month = shift_month(ym, -offset)
        if month < DATA_START:
            continue
        for row in collection_rows(store, month, scope):
            shipment = _invoice_shipment(store, row["invoice_id"])
            if shipment is None:
                continue
            days = (row["collection_date"] - shipment["shipment_date"]).days
            acc += row["amount"] * Decimal(days)
            weight += row["amount"]
    if weight == 0:
        return None
    return acc / weight


def _invoice_shipment(store: Store, invoice_id: str) -> dict | None:
    # Linear scan is fine at demo size; cache on first use via attribute.
    index = getattr(store, "_invoice_index", None)
    if index is None:
        index = {row["invoice_id"]: row for row in store.shipments}
        setattr(store, "_invoice_index", index)
    return index.get(invoice_id)


def top_ar_customers(store: Store, ym: str, scope: Scope, limit: int = 10) -> list[dict]:
    buckets: dict[str, dict[str, Decimal]] = {}
    for row in snapshot_rows(store, ym, scope):
        entry = buckets.setdefault(row["customer_id"], {bucket: ZERO for bucket in BUCKETS})
        entry[row["aging_bucket"]] += row["outstanding_amount"]
    rows = []
    for customer_id, amounts in buckets.items():
        customer = store.customers[customer_id]
        total = sum(amounts.values(), ZERO)
        overdue = sum((amt for bucket, amt in amounts.items() if bucket != BUCKET_UNDUE), ZERO)
        rows.append(
            {
                "customer_id": customer_id,
                "customer_name": customer["customer_name"],
                "tier": customer["customer_tier"],
                "region_id": customer["region_id"],
                "region_name": store.region_name[customer["region_id"]],
                "outstanding": total,
                "overdue": overdue,
                "buckets": amounts,
            }
        )
    rows.sort(key=lambda row: (-row["outstanding"], row["customer_id"]))
    return rows[:limit]


def ar_bridge(store: Store, ym: str, compare: str, scope: Scope) -> dict:
    """ΔAR = 本期新增应收 − 本期回款 + 其他.

    新增应收 is period net sales because each shipment is one invoice.
    其他 is the residual. On this dataset it is 0 (no write-off, no unlinked cash).
    """
    start_ar = ar_balance(store, compare, scope)
    end_ar = ar_balance(store, ym, scope)
    added = net_sales(store, ym, scope)
    collected = collection_amount(store, ym, scope)
    other = end_ar - start_ar - added + collected
    return {
        "start_ar": start_ar,
        "added": added,
        "collected": collected,
        "other": other,
        "end_ar": end_ar,
        "change": end_ar - start_ar,
    }


def scope_from_slice(dimension: str, key: str, parent: Scope | None = None) -> Scope:
    return apply_member(parent or Scope(), dimension, key)


def filter_lines(store: Store, ym: str, compare: str | None, scope: Scope) -> list[str]:
    lines = [f"期间={ym}"]
    if compare:
        lines.append(f"对比期间={compare}")
    if scope.region_id:
        lines.append(f"大区={store.region_name.get(scope.region_id, scope.region_id)}")
    else:
        lines.append("大区=全部")
    lines.append(f"产品线={scope.product_line or '全部'}")
    lines.append(f"代理商等级={scope.tier or '全部'}")
    if scope.customer_id:
        lines.append(f"代理商={store.customers[scope.customer_id]['customer_name']}")
    if scope.cohort == "new":
        lines.append("代理商分组=新代理商")
    elif scope.cohort == "old":
        lines.append("代理商分组=老代理商")
    else:
        lines.append("代理商分组=全部")
    lines.append("客户范围=全部为代理商")
    return lines
