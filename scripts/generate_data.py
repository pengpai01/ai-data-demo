#!/usr/bin/env python3
"""Write the six fictional CSVs. Seed is the code, not a random draw."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.generate import generate  # noqa: E402
from app.metrics import (  # noqa: E402
    Scope,
    aging_migration,
    choose_default_view,
    collection_amount,
    contributions,
    customer_contribs,
    decompose,
    discount_rate,
    due_collection_rate,
    net_sales,
    rate_slices,
)
from app.store import load_store  # noqa: E402
from decimal import Decimal  # noqa: E402


def _pp(value) -> str:
    if value is None:
        return "n/a"
    return f"{(value * Decimal(100)):.2f}pp"


def _pct(value) -> str:
    if value is None:
        return "n/a"
    return f"{(value * Decimal(100)):.2f}%"


def summarize(folder: Path) -> None:
    store = load_store(folder)
    period, compare = "2026-10", "2026-09"
    regions = rate_slices(store, period, compare, "region")
    lines = rate_slices(store, period, compare, "product_line")
    view, primary, secondary = choose_default_view(regions, lines)
    print(f"default_view={view} primary={None if primary is None else primary['name']} {_pp(None if primary is None else primary['delta'])}")
    print(f"secondary={None if secondary is None else secondary['name']} {_pp(None if secondary is None else secondary['delta'])}")
    print("--- regions ---")
    for row in regions:
        print(
            f"{row['name']} share={_pct(row['share'])} rate={_pct(row['rate'])} prev={_pct(row['prev_rate'])} "
            f"delta={_pp(row['delta'])} eligible={row['eligible']} net={row['net']} col={row['collection']}"
        )
    print("--- lines ---")
    for row in lines:
        print(
            f"{row['name']} share={_pct(row['share'])} rate={_pct(row['rate'])} prev={_pct(row['prev_rate'])} "
            f"delta={_pp(row['delta'])} eligible={row['eligible']}"
        )
    if primary is None:
        return
    dimension = "region" if view == "region" else "product_line"
    scope = Scope(region_id=primary["key"]) if view == "region" else Scope(product_line=primary["key"])
    parts = decompose(primary["prev_collection"], primary["prev_net"], primary["collection"], primary["net"])
    print("--- decompose primary ---")
    if parts:
        print(
            f"delta={_pp(parts['delta'])} collection={_pp(parts['collection_effect'])} sales={_pp(parts['sales_effect'])} "
            f"sum={_pp(parts['collection_effect'] + parts['sales_effect'])}"
        )
        print(f"net {primary['prev_net']} -> {primary['net']}  col {primary['prev_collection']} -> {primary['collection']}")
    cross_dim = "product_line" if view == "region" else "region"
    print("--- cross ---")
    for row in contributions(store, period, compare, scope, cross_dim):
        print(f"{row['name']} contrib={_pp(row['contrib'])} coll={_pp(row['collection_effect'])} sales={_pp(row['sales_effect'])}")
    print("--- top agents ---")
    for row in customer_contribs(store, period, compare, scope)[:5]:
        print(
            f"{row['customer_id']} {row['customer_name']} {row['tier']} contrib={_pp(row['contrib'])} "
            f"net {row['prev_net']}->{row['net']} col {row['prev_collection']}->{row['collection']}"
        )
    print("--- aging ---")
    for row in aging_migration(store, period, compare, scope):
        print(f"{row['bucket']} {row['prev_amount']} -> {row['amount']} share_delta={row['share_delta']}")
    print("discount", discount_rate(store, compare, scope), "->", discount_rate(store, period, scope))
    print("due rate", due_collection_rate(store, compare, scope), "->", due_collection_rate(store, period, scope))
    print("company net", net_sales(store, period), "col", collection_amount(store, period))


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data"
    generate(out)
    print(f"wrote {out}")
    summarize(out)


if __name__ == "__main__":
    main()
