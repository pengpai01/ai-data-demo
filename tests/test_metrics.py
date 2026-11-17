"""Deterministic metric tests for the locked 销售回款率口径."""

from decimal import Decimal
from pathlib import Path

from app.constants import BUCKETS, SHARE_MIN
from app.generate import DRAG_IDS, generate
from app.metrics import (
    Scope,
    ar_bridge,
    aging_migration,
    choose_default_view,
    collection_amount,
    contributions,
    customer_contribs,
    decompose,
    dso,
    due_collection_rate,
    month_days,
    net_sales,
    rate_slices,
    select_fastest,
)
from app.store import load_store

ROOT = Path(__file__).resolve().parents[1]


def test_rate_decomposition_identity():
    parts = decompose(Decimal("90"), Decimal("100"), Decimal("78"), Decimal("120"))
    assert parts["rate0"] == Decimal("0.9")
    assert parts["rate1"] == Decimal("78") / Decimal("120")
    assert parts["collection_effect"] == (Decimal("78") - Decimal("90")) / Decimal("100")
    assert parts["sales_effect"] == Decimal("78") / Decimal("120") - Decimal("78") / Decimal("100")
    assert parts["collection_effect"] + parts["sales_effect"] == parts["delta"]


def test_small_share_is_not_eligible_for_fastest():
    slices = [
        {"key": "tiny", "eligible": False, "delta": Decimal("-0.40"), "share": Decimal("0.04")},
        {"key": "main", "eligible": True, "delta": Decimal("-0.10"), "share": Decimal("0.40")},
        {"key": "flat", "eligible": True, "delta": Decimal("-0.01"), "share": Decimal("0.20")},
    ]
    assert select_fastest(slices)["key"] == "main"
    assert SHARE_MIN == Decimal("0.05")


def test_default_view_picks_the_larger_decline_and_ties_go_to_region():
    view, primary, _ = choose_default_view(
        [{"key": "R", "eligible": True, "delta": Decimal("-0.20")}],
        [{"key": "P", "eligible": True, "delta": Decimal("-0.05")}],
    )
    assert view == "region"
    assert primary["key"] == "R"
    view, primary, _ = choose_default_view(
        [{"key": "R", "eligible": True, "delta": Decimal("-0.02")}],
        [{"key": "P", "eligible": True, "delta": Decimal("-0.18")}],
    )
    assert view == "product"
    assert primary["key"] == "P"
    view, _, _ = choose_default_view(
        [{"key": "R", "eligible": True, "delta": Decimal("-0.10")}],
        [{"key": "P", "eligible": True, "delta": Decimal("-0.10")}],
    )
    assert view == "region"


def test_october_story_volume_and_a_tier_deferral():
    store = load_store()
    regions = rate_slices(store, "2026-10", "2026-09", "region")
    lines = rate_slices(store, "2026-10", "2026-09", "product_line")
    assert all(row["share"] >= SHARE_MIN for row in regions)
    assert all(row["share"] >= SHARE_MIN for row in lines)
    view, primary, secondary = choose_default_view(regions, lines)
    assert view == "region"
    assert primary["name"] == "华东"
    assert primary["delta"] < secondary["delta"]
    parts = decompose(primary["prev_collection"], primary["prev_net"], primary["collection"], primary["net"])
    assert parts["collection_effect"] + parts["sales_effect"] == parts["delta"]
    assert parts["collection_effect"] < 0
    assert parts["sales_effect"] < 0
    assert primary["net"] > primary["prev_net"]
    assert primary["collection"] < primary["prev_collection"]
    assert abs(parts["sales_effect"]) >= abs(parts["delta"]) * Decimal("0.25")
    assert abs(parts["collection_effect"]) >= abs(parts["delta"]) * Decimal("0.25")
    # Ranking uses 销售回款率, not the auxiliary 到期回款率.
    scope = Scope(region_id="R-HD")
    assert primary["rate"] == collection_amount(store, "2026-10", scope) / net_sales(store, "2026-10", scope)
    due0 = due_collection_rate(store, "2026-09", scope)
    due1 = due_collection_rate(store, "2026-10", scope)
    assert due1 < due0
    assert (due1 - due0) != parts["delta"]
    agents = customer_contribs(store, "2026-10", "2026-09", scope)[:3]
    assert [row["customer_id"] for row in agents] == list(DRAG_IDS)
    assert all(row["tier"] == "A" for row in agents)
    assert all(row["net"] > row["prev_net"] and row["collection"] < row["prev_collection"] for row in agents)
    cross = contributions(store, "2026-10", "2026-09", scope, "product_line")
    assert sum((row["contrib"] for row in cross), Decimal("0")) == parts["delta"]
    assert cross[0]["name"] == "试剂"
    aging = {row["bucket"]: row for row in aging_migration(store, "2026-10", "2026-09", scope)}
    assert aging["90+"]["amount"] > aging["90+"]["prev_amount"]


def test_dso_formula_and_ar_bridge_identity():
    store = load_store()
    scope = Scope()
    sales = sum((net_sales(store, month, scope) for month in ("2026-08", "2026-09", "2026-10")), Decimal("0"))
    days = sum(month_days(month) for month in ("2026-08", "2026-09", "2026-10"))
    from app.metrics import ar_balance

    assert dso(store, "2026-10", scope) == ar_balance(store, "2026-10", scope) / (sales / Decimal(days))
    bridge = ar_bridge(store, "2026-10", "2026-09", scope)
    assert bridge["other"] == 0
    assert bridge["end_ar"] - bridge["start_ar"] == bridge["added"] - bridge["collected"] + bridge["other"]
    assert dso(store, "2025-11", scope) is None


def test_facts_follow_the_agent_and_stay_fictional():
    store = load_store()
    for customer in store.customers.values():
        assert customer["customer_tier"] in {"A", "B", "C"}
        assert customer["customer_name"].endswith("代理商")
        assert "经销商" not in customer["customer_name"]
    for row in store.shipments + store.collections + store.snapshots:
        assert row["region_id"] == store.customers[row["customer_id"]]["region_id"]
    invoices = {row["invoice_id"]: row for row in store.shipments}
    assert len(invoices) == len(store.shipments)
    for row in store.collections:
        shipment = invoices[row["invoice_id"]]
        assert row["product_id"] == shipment["product_id"]
        assert row["customer_id"] == shipment["customer_id"]
    assert set(row["aging_bucket"] for row in store.snapshots) <= set(BUCKETS)
    header = (ROOT / "data" / "dim_customer.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "customer_type" not in header
    assert "channel" not in header
    blob = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "data").glob("*.csv"))
    for word in ("经销商", "直营", "体检中心", "医院"):
        assert word not in blob


def test_generator_matches_committed_csvs(tmp_path):
    generate(tmp_path)
    for name in (
        "dim_region.csv",
        "dim_product.csv",
        "dim_customer.csv",
        "fact_shipment.csv",
        "fact_collection.csv",
        "fact_ar_snapshot.csv",
    ):
        assert (tmp_path / name).read_bytes() == (ROOT / "data" / name).read_bytes()
