"""Seeded fictional CSV generator for 星澜诊断.

Every customer is an 代理商. The October 2026 story is built in, not sampled:
three large A-tier 华东 agents both ship a month-end surge and pay only part
of what comes due. Other agents keep paying on the due date.

A 100 yuan shipment on 2024-11-15, collected the same day, marks each 老代理商
so that 「近 12 个月首次出库」 can separate 新 and 老 inside a file whose
operating months are 2025-11 through 2026-10. That marker is outside the
operating window and is fully settled, so it does not enter 2026-09/10 rates
or any month-end AR snapshot.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from app.constants import BUCKETS
from app.store import DATA_DIR

CENT = Decimal("0.01")
MARKER_DATE = date(2024, 11, 15)
MARKER_NET = Decimal("100.00")
MONTHS = [
    "2025-11",
    "2025-12",
    "2026-01",
    "2026-02",
    "2026-03",
    "2026-04",
    "2026-05",
    "2026-06",
    "2026-07",
    "2026-08",
    "2026-09",
    "2026-10",
]
MONTH_FACTOR = {
    "2025-11": Decimal("0.97"),
    "2025-12": Decimal("1.05"),
    "2026-01": Decimal("0.92"),
    "2026-02": Decimal("0.86"),
    "2026-03": Decimal("0.98"),
    "2026-04": Decimal("1.02"),
    "2026-05": Decimal("1.04"),
    "2026-06": Decimal("1.05"),
    "2026-07": Decimal("1.02"),
    "2026-08": Decimal("1.03"),
    "2026-09": Decimal("1.00"),
    "2026-10": Decimal("1.00"),
}
LINE_SHARE = {
    "试剂": Decimal("0.72"),
    "仪器": Decimal("0.18"),
    "服务": Decimal("0.10"),
}
# October volume multipliers on each line's baseline (not stacked on MONTH_FACTOR).
DRAG_OCT_LINE = {
    "试剂": Decimal("1.32"),
    "仪器": Decimal("1.08"),
    "服务": Decimal("1.02"),
}
HD_OTHER_OCT = Decimal("1.05")
OTHER_OCT = Decimal("1.02")
# Fraction of the original invoice paid on the due date. Residual is never caught up.
DRAG_PAY_EARLY = Decimal("1")
DRAG_PAY_MID = Decimal("0.84")  # 2026-07 .. 2026-09
DRAG_PAY_OCT = Decimal("0.60")
NORMAL_DISCOUNT = Decimal("0.08")
DRAG_OCT_DISCOUNT = {"试剂": Decimal("0.14"), "仪器": Decimal("0.10"), "服务": Decimal("0.09")}
NORMAL_DAYS = (6, 13, 20, 27)
NORMAL_WEIGHTS = (Decimal("0.22"), Decimal("0.26"), Decimal("0.28"), Decimal("0.24"))
SURGE_DAYS = (7, 14, 26, 30)
SURGE_WEIGHTS = (Decimal("0.08"), Decimal("0.12"), Decimal("0.30"), Decimal("0.50"))
UNIT_PRICE = {"试剂": Decimal("860"), "仪器": Decimal("160000"), "服务": Decimal("4200")}

REGIONS = [
    ("R-HD", "华东", "上海/江苏/浙江/安徽"),
    ("R-HN", "华南", "广东/广西/福建/海南"),
    ("R-HB", "华北", "北京/天津/河北/山东"),
    ("R-XN", "西南", "四川/重庆/云南/贵州"),
    ("R-XB", "西北", "陕西/甘肃/宁夏/新疆"),
]
PRODUCTS = [
    ("P-R-BIO", "试剂", "生化试剂", "XL-生化试剂盒-阿尔法", "盒"),
    ("P-R-IMU", "试剂", "免疫试剂", "XL-免疫试剂-贝塔", "人份"),
    ("P-R-MOL", "试剂", "分子试剂", "XL-核酸试剂-伽马", "人份"),
    ("P-I-ANA", "仪器", "生化分析仪", "XL-2000全自动生化分析仪", "台"),
    ("P-I-LUM", "仪器", "化学发光仪", "XL-流明化学发光分析仪", "台"),
    ("P-I-HEM", "仪器", "血球分析仪", "XL-血细胞分析仪", "台"),
    ("P-S-INS", "服务", "装机服务", "XL-装机调试服务", "次"),
    ("P-S-MNT", "服务", "维保服务", "XL-年度维保服务", "次"),
    ("P-S-TRN", "服务", "应用培训", "XL-应用培训服务", "次"),
]
# id, name, tier, region, terms, monthly base net, role, first operating month
CUSTOMERS = [
    ("C-HD-A01", "锦程华东代理商", "A", "R-HD", 60, "4200000", "drag", "2025-11"),
    ("C-HD-A02", "苏澜华东代理商", "A", "R-HD", 60, "3800000", "drag", "2025-11"),
    ("C-HD-A03", "浙澜华东代理商", "A", "R-HD", 60, "3600000", "drag", "2025-11"),
    ("C-HD-A04", "皖江华东代理商", "A", "R-HD", 60, "2000000", "normal", "2025-11"),
    ("C-HD-B01", "浦江华东代理商", "B", "R-HD", 45, "1500000", "normal", "2025-11"),
    ("C-HD-B02", "甬城华东代理商", "B", "R-HD", 45, "1300000", "normal", "2025-11"),
    ("C-HD-C01", "嘉禾华东代理商", "C", "R-HD", 30, "900000", "normal", "2026-04"),
    ("C-HD-C02", "吴中华东代理商", "C", "R-HD", 30, "500000", "normal", "2025-11"),
    ("C-HN-A01", "岭南华南代理商", "A", "R-HN", 60, "3200000", "normal", "2025-11"),
    ("C-HN-A02", "珠海华南代理商", "A", "R-HN", 60, "2800000", "normal", "2025-11"),
    ("C-HN-B01", "潮汕华南代理商", "B", "R-HN", 45, "1800000", "normal", "2025-11"),
    ("C-HN-B02", "桂林华南代理商", "B", "R-HN", 45, "1400000", "normal", "2025-11"),
    ("C-HN-C01", "闽江华南代理商", "C", "R-HN", 30, "700000", "normal", "2025-11"),
    ("C-HN-C02", "邕江华南代理商", "C", "R-HN", 30, "800000", "normal", "2026-04"),
    ("C-HB-A01", "渤海华北代理商", "A", "R-HB", 60, "2800000", "normal", "2025-11"),
    ("C-HB-A02", "燕赵华北代理商", "A", "R-HB", 60, "2400000", "normal", "2025-11"),
    ("C-HB-B01", "齐鲁华北代理商", "B", "R-HB", 45, "1500000", "normal", "2025-11"),
    ("C-HB-B02", "津门华北代理商", "B", "R-HB", 45, "1200000", "normal", "2025-11"),
    ("C-HB-C01", "太行华北代理商", "C", "R-HB", 30, "600000", "normal", "2025-11"),
    ("C-HB-C02", "坝上华北代理商", "C", "R-HB", 30, "700000", "normal", "2026-04"),
    ("C-XN-A01", "锦官西南代理商", "A", "R-XN", 60, "2200000", "normal", "2025-11"),
    ("C-XN-B01", "渝州西南代理商", "B", "R-XN", 45, "1200000", "normal", "2025-11"),
    ("C-XN-B02", "滇池西南代理商", "B", "R-XN", 45, "1000000", "normal", "2025-11"),
    ("C-XN-C01", "筑城西南代理商", "C", "R-XN", 30, "500000", "normal", "2025-11"),
    ("C-XN-C02", "金沙西南代理商", "C", "R-XN", 30, "650000", "normal", "2026-04"),
    ("C-XB-A01", "秦川西北代理商", "A", "R-XB", 60, "1600000", "normal", "2025-11"),
    ("C-XB-B01", "陇右西北代理商", "B", "R-XB", 45, "900000", "normal", "2025-11"),
    ("C-XB-B02", "河西西北代理商", "B", "R-XB", 45, "700000", "normal", "2025-11"),
    ("C-XB-C01", "朔方西北代理商", "C", "R-XB", 30, "350000", "normal", "2025-11"),
    ("C-XB-C02", "天山西北代理商", "C", "R-XB", 30, "500000", "normal", "2026-04"),
]
DRAG_IDS = ("C-HD-A01", "C-HD-A02", "C-HD-A03")

# Designed cast. Tests check behavior, not a hardcoded pp.
STORY = {
    "period": "2026-10",
    "compare": "2026-09",
    "drag_ids": DRAG_IDS,
    "default_region_name": "华东",
}


def q2(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def split_money(total: Decimal, weights: tuple[Decimal, ...]) -> list[Decimal]:
    total = q2(total)
    parts: list[Decimal] = []
    acc = Decimal("0.00")
    for index, weight in enumerate(weights):
        if index == len(weights) - 1:
            parts.append(q2(total - acc))
        else:
            part = q2(total * weight)
            parts.append(part)
            acc += part
    if sum(parts, Decimal("0")) != total:
        raise RuntimeError("split did not reconcile")
    if any(part < 0 for part in parts):
        raise RuntimeError("negative split")
    return parts


def line_factor(customer: dict, ym: str, line: str) -> Decimal:
    if ym == "2026-10":
        if customer["role"] == "drag":
            return DRAG_OCT_LINE[line]
        if customer["region_id"] == "R-HD":
            return HD_OTHER_OCT
        return OTHER_OCT
    return MONTH_FACTOR[ym]


def pay_ratio(customer: dict, ym: str) -> Decimal:
    if customer["role"] != "drag":
        return Decimal("1")
    if ym <= "2026-06":
        return DRAG_PAY_EARLY
    if ym <= "2026-09":
        return DRAG_PAY_MID
    return DRAG_PAY_OCT


def discount_rate(customer: dict, ym: str, line: str) -> Decimal:
    if customer["role"] == "drag" and ym == "2026-10":
        return DRAG_OCT_DISCOUNT[line]
    return NORMAL_DISCOUNT


def aging_bucket(due: date, month_end: date) -> str:
    days = (month_end - due).days
    if days <= 0:
        return "未到期"
    if days <= 30:
        return "1–30"
    if days <= 60:
        return "31–60"
    if days <= 90:
        return "61–90"
    return "90+"


def month_end(ym: str) -> date:
    year, month = map(int, ym.split("-"))
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _customer_records() -> list[dict]:
    rows = []
    for cid, name, tier, region, terms, base, role, start in CUSTOMERS:
        rows.append(
            {
                "customer_id": cid,
                "customer_name": name,
                "customer_tier": tier,
                "region_id": region,
                "payment_terms_days": terms,
                "base": Decimal(base),
                "role": role,
                "start": start,
            }
        )
    return rows


def _skus_by_line() -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for product_id, line, _cat, _sku, _unit in PRODUCTS:
        grouped.setdefault(line, []).append(product_id)
    return grouped


def generate(out_dir: Path | None = None) -> Path:
    folder = Path(out_dir) if out_dir else DATA_DIR
    folder.mkdir(parents=True, exist_ok=True)
    customers = _customer_records()
    by_id = {row["customer_id"]: row for row in customers}
    skus = _skus_by_line()

    shipments: list[dict] = []
    collections: list[dict] = []
    snapshots: list[dict] = []
    open_invoices: list[dict] = []
    ship_n = 1
    col_n = 1

    def add_shipment(cust, ship_date, product_id, net, disc_rate, due):
        nonlocal ship_n
        net = q2(net)
        if net <= 0:
            return
        list_amt = q2(net / (Decimal("1") - disc_rate))
        discount = list_amt - net
        unit = UNIT_PRICE[_line_of(product_id)]
        qty = max(1, int((list_amt / unit).quantize(Decimal("1"), rounding=ROUND_HALF_UP)))
        sid = f"SHP{ship_n:06d}"
        ship_n += 1
        invoice_id = f"INV{sid[3:]}"
        shipments.append(
            {
                "shipment_id": sid,
                "shipment_date": ship_date.isoformat(),
                "customer_id": cust["customer_id"],
                "product_id": product_id,
                "region_id": cust["region_id"],
                "qty": qty,
                "list_amount": f"{list_amt:.2f}",
                "discount_amount": f"{discount:.2f}",
                "net_amount": f"{net:.2f}",
                "invoice_id": invoice_id,
            }
        )
        open_invoices.append(
            {
                "invoice_id": invoice_id,
                "invoice_date": ship_date,
                "due": due,
                "customer": cust,
                "product_id": product_id,
                "original": net,
                "outstanding": net,
            }
        )

    for cust in customers:
        if cust["start"] != "2025-11":
            continue
        product_id = skus["试剂"][0]
        list_amt = q2(MARKER_NET / (Decimal("1") - NORMAL_DISCOUNT))
        discount = list_amt - MARKER_NET
        sid = f"SHP{ship_n:06d}"
        ship_n += 1
        invoice_id = f"INV{sid[3:]}"
        shipments.append(
            {
                "shipment_id": sid,
                "shipment_date": MARKER_DATE.isoformat(),
                "customer_id": cust["customer_id"],
                "product_id": product_id,
                "region_id": cust["region_id"],
                "qty": 1,
                "list_amount": f"{list_amt:.2f}",
                "discount_amount": f"{discount:.2f}",
                "net_amount": f"{MARKER_NET:.2f}",
                "invoice_id": invoice_id,
            }
        )
        cid = f"COL{col_n:06d}"
        col_n += 1
        collections.append(
            {
                "collection_id": cid,
                "collection_date": MARKER_DATE.isoformat(),
                "customer_id": cust["customer_id"],
                "region_id": cust["region_id"],
                "invoice_id": invoice_id,
                "product_id": product_id,
                "amount": f"{MARKER_NET:.2f}",
            }
        )

    for ym in MONTHS:
        year, month = map(int, ym.split("-"))
        for cust in customers:
            if ym < cust["start"]:
                continue
            surge = cust["role"] == "drag" and ym == "2026-10"
            days = SURGE_DAYS if surge else NORMAL_DAYS
            weights = SURGE_WEIGHTS if surge else NORMAL_WEIGHTS
            for line, share in LINE_SHARE.items():
                target = q2(cust["base"] * line_factor(cust, ym, line) * share)
                parts = split_money(target, weights)
                sku_list = skus[line]
                for index, (day, net) in enumerate(zip(days, parts)):
                    ship_date = date(year, month, day)
                    due = ship_date + timedelta(days=cust["payment_terms_days"])
                    add_shipment(
                        cust,
                        ship_date,
                        sku_list[index % len(sku_list)],
                        net,
                        discount_rate(cust, ym, line),
                        due,
                    )
        for inv in open_invoices:
            if inv["outstanding"] <= 0:
                continue
            if inv["due"].year != year or inv["due"].month != month:
                continue
            ratio = pay_ratio(inv["customer"], ym)
            pay = q2(inv["original"] * ratio)
            if pay > inv["outstanding"]:
                pay = inv["outstanding"]
            if pay <= 0:
                continue
            cid = f"COL{col_n:06d}"
            col_n += 1
            collections.append(
                {
                    "collection_id": cid,
                    "collection_date": inv["due"].isoformat(),
                    "customer_id": inv["customer"]["customer_id"],
                    "region_id": inv["customer"]["region_id"],
                    "invoice_id": inv["invoice_id"],
                    "product_id": inv["product_id"],
                    "amount": f"{pay:.2f}",
                }
            )
            inv["outstanding"] = q2(inv["outstanding"] - pay)

        # Snapshot after this month's invoices and collections, before later months pay.
        end = month_end(ym)
        for inv in open_invoices:
            if inv["outstanding"] <= 0:
                continue
            if inv["invoice_date"] > end:
                continue
            snapshots.append(
                {
                    "snapshot_month": ym,
                    "invoice_id": inv["invoice_id"],
                    "invoice_date": inv["invoice_date"].isoformat(),
                    "due_date": inv["due"].isoformat(),
                    "customer_id": inv["customer"]["customer_id"],
                    "product_id": inv["product_id"],
                    "region_id": inv["customer"]["region_id"],
                    "original_amount": f"{inv['original']:.2f}",
                    "outstanding_amount": f"{inv['outstanding']:.2f}",
                    "aging_bucket": aging_bucket(inv["due"], end),
                }
            )

    _write_dims(folder, customers)
    _write(
        folder / "fact_shipment.csv",
        ["shipment_id", "shipment_date", "customer_id", "product_id", "region_id", "qty", "list_amount", "discount_amount", "net_amount", "invoice_id"],
        shipments,
    )
    _write(
        folder / "fact_collection.csv",
        ["collection_id", "collection_date", "customer_id", "region_id", "invoice_id", "product_id", "amount"],
        collections,
    )
    _write(
        folder / "fact_ar_snapshot.csv",
        ["snapshot_month", "invoice_id", "invoice_date", "due_date", "customer_id", "product_id", "region_id", "original_amount", "outstanding_amount", "aging_bucket"],
        snapshots,
    )
    _reject_forbidden(folder)
    if any(bucket not in BUCKETS for bucket in {row["aging_bucket"] for row in snapshots}):
        raise RuntimeError("aging bucket outside the locked list")
    # Touch by_id so a typo in DRAG_IDS fails here.
    for drag_id in DRAG_IDS:
        if by_id[drag_id]["role"] != "drag" or by_id[drag_id]["customer_tier"] != "A":
            raise RuntimeError("drag cast must stay A-tier 华东 agents")
    return folder


def _line_of(product_id: str) -> str:
    for pid, line, *_rest in PRODUCTS:
        if pid == product_id:
            return line
    raise KeyError(product_id)


def _write_dims(folder: Path, customers: list[dict]) -> None:
    _write(
        folder / "dim_region.csv",
        ["region_id", "region_name", "province"],
        [{"region_id": a, "region_name": b, "province": c} for a, b, c in REGIONS],
    )
    _write(
        folder / "dim_product.csv",
        ["product_id", "product_line", "product_category", "sku", "unit"],
        [
            {
                "product_id": a,
                "product_line": b,
                "product_category": c,
                "sku": d,
                "unit": e,
            }
            for a, b, c, d, e in PRODUCTS
        ],
    )
    _write(
        folder / "dim_customer.csv",
        ["customer_id", "customer_name", "customer_tier", "region_id", "payment_terms_days"],
        [
            {
                "customer_id": row["customer_id"],
                "customer_name": row["customer_name"],
                "customer_tier": row["customer_tier"],
                "region_id": row["region_id"],
                "payment_terms_days": row["payment_terms_days"],
            }
            for row in customers
        ],
    )


def _write(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row[key] for key in fields})


def _reject_forbidden(folder: Path) -> None:
    banned = ("经销商", "直营", "体检中心", "customer_type", "channel", "医院")
    for path in folder.glob("*.csv"):
        text = path.read_text(encoding="utf-8")
        for word in banned:
            if word in text:
                raise RuntimeError(f"{path.name} contains forbidden token {word}")


def default_data_dir() -> Path:
    return DATA_DIR
