"""Load the six fictional CSVs into memory. Amounts stay as Decimal."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from app.constants import BUCKETS, REGION_ORDER

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def parse_money(value: str) -> Decimal:
    return Decimal(value)


@dataclass
class Store:
    regions: dict[str, dict]
    products: dict[str, dict]
    customers: dict[str, dict]
    shipments: list[dict]
    collections: list[dict]
    snapshots: list[dict]
    shipments_by_month: dict[str, list] = field(default_factory=dict)
    collections_by_month: dict[str, list] = field(default_factory=dict)
    snapshots_by_month: dict[str, list] = field(default_factory=dict)
    collections_by_invoice: dict[str, list] = field(default_factory=dict)
    first_shipment: dict[str, date] = field(default_factory=dict)
    region_name: dict[str, str] = field(default_factory=dict)


def _read(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_store(data_dir: Path | None = None) -> Store:
    folder = Path(data_dir) if data_dir else DATA_DIR
    regions = {}
    for row in _read(folder / "dim_region.csv"):
        regions[row["region_id"]] = row
    products = {}
    for row in _read(folder / "dim_product.csv"):
        products[row["product_id"]] = row
    customers = {}
    for row in _read(folder / "dim_customer.csv"):
        customers[row["customer_id"]] = {
            "customer_id": row["customer_id"],
            "customer_name": row["customer_name"],
            "customer_tier": row["customer_tier"],
            "region_id": row["region_id"],
            "payment_terms_days": int(row["payment_terms_days"]),
        }

    shipments = []
    for row in _read(folder / "fact_shipment.csv"):
        ship_date = parse_date(row["shipment_date"])
        shipments.append(
            {
                "shipment_id": row["shipment_id"],
                "shipment_date": ship_date,
                "month": ship_date.strftime("%Y-%m"),
                "customer_id": row["customer_id"],
                "product_id": row["product_id"],
                "region_id": row["region_id"],
                "qty": int(row["qty"]),
                "list_amount": parse_money(row["list_amount"]),
                "discount_amount": parse_money(row["discount_amount"]),
                "net_amount": parse_money(row["net_amount"]),
                "invoice_id": row["invoice_id"],
            }
        )

    collections = []
    for row in _read(folder / "fact_collection.csv"):
        col_date = parse_date(row["collection_date"])
        collections.append(
            {
                "collection_id": row["collection_id"],
                "collection_date": col_date,
                "month": col_date.strftime("%Y-%m"),
                "customer_id": row["customer_id"],
                "region_id": row["region_id"],
                "invoice_id": row["invoice_id"],
                "product_id": row["product_id"],
                "amount": parse_money(row["amount"]),
            }
        )

    snapshots = []
    for row in _read(folder / "fact_ar_snapshot.csv"):
        if row["aging_bucket"] not in BUCKETS:
            raise ValueError(f"unknown aging bucket {row['aging_bucket']}")
        snapshots.append(
            {
                "snapshot_month": row["snapshot_month"],
                "invoice_id": row["invoice_id"],
                "invoice_date": parse_date(row["invoice_date"]),
                "due_date": parse_date(row["due_date"]),
                "customer_id": row["customer_id"],
                "product_id": row["product_id"],
                "region_id": row["region_id"],
                "original_amount": parse_money(row["original_amount"]),
                "outstanding_amount": parse_money(row["outstanding_amount"]),
                "aging_bucket": row["aging_bucket"],
            }
        )

    store = Store(
        regions=regions,
        products=products,
        customers=customers,
        shipments=shipments,
        collections=collections,
        snapshots=snapshots,
    )
    for row in shipments:
        store.shipments_by_month.setdefault(row["month"], []).append(row)
        prev = store.first_shipment.get(row["customer_id"])
        if prev is None or row["shipment_date"] < prev:
            store.first_shipment[row["customer_id"]] = row["shipment_date"]
    for row in collections:
        store.collections_by_month.setdefault(row["month"], []).append(row)
        store.collections_by_invoice.setdefault(row["invoice_id"], []).append(row)
    for row in snapshots:
        store.snapshots_by_month.setdefault(row["snapshot_month"], []).append(row)
    store.region_name = {rid: regions[rid]["region_name"] for rid in REGION_ORDER if rid in regions}
    return store
