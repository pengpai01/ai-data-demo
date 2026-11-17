"""Seeded fictional CSV generator for 星澜诊断.

Every customer is an 代理商. The October 2026 story is built in, not sampled:
three large A-tier 华东 agents both ship a month-end surge and pay only part
of what comes due. The other agents, volumes, shipment days and payment habits
(on time, a little late, or split into two payments) come from a fixed seed, so
the files are reproducible.

A 100 yuan shipment on 2024-11-15, collected the same day, marks each 老代理商
so that 「近 12 个月首次出库」 can separate 新 and 老 inside a file whose
operating months are 2025-11 through 2026-10. That marker is outside the
operating window and is fully settled, so it does not enter 2026-09/10 rates
or any month-end AR snapshot.
"""

from __future__ import annotations

import csv
import random
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
DRAG_DAYS = (6, 13, 20, 27)
DRAG_WEIGHTS = (Decimal("0.22"), Decimal("0.26"), Decimal("0.28"), Decimal("0.24"))
SURGE_DAYS = (7, 14, 26, 30)
SURGE_WEIGHTS = (Decimal("0.08"), Decimal("0.12"), Decimal("0.30"), Decimal("0.50"))
SEED = 20261063
# Each agent keeps habitual order days per line; a month moves each day by at most this much.
SHIPMENTS_PER_MONTH = {"试剂": (3, 6), "仪器": (1, 3), "服务": (2, 4)}
DAY_JITTER = 2
# Per-agent payment habit: share of agents, max days paid after due date.
PAY_HABITS = (("准时", 70, 2), ("偏慢", 24, 10), ("拖延", 6, 20))
PARTIAL_PROB = Decimal("0.08")

REGIONS = [
    ("R-HD", "华东", "上海/江苏/浙江/安徽"),
    ("R-HN", "华南", "广东/广西/福建/海南"),
    ("R-HB", "华北", "北京/天津/河北/山东"),
    ("R-XN", "西南", "四川/重庆/云南/贵州"),
    ("R-XB", "西北", "陕西/甘肃/宁夏/新疆"),
]
PRODUCTS = [
    ("P-R-LIV", "试剂", "肝功试剂", "XL-肝功生化试剂盒", "盒"),
    ("P-R-REN", "试剂", "肾功试剂", "XL-肾功生化试剂盒", "盒"),
    ("P-R-LIP", "试剂", "血脂试剂", "XL-血脂生化试剂盒", "盒"),
    ("P-R-CLIA", "试剂", "化学发光试剂", "XL-化学发光免疫试剂", "人份"),
    ("P-R-ELISA", "试剂", "酶联免疫试剂", "XL-酶联免疫试剂", "人份"),
    ("P-R-NA", "试剂", "核酸试剂", "XL-核酸检测试剂", "人份"),
    ("P-R-COAG", "试剂", "凝血试剂", "XL-凝血功能试剂", "盒"),
    ("P-R-HEM", "试剂", "血球试剂", "XL-血球稀释试剂", "瓶"),
    ("P-R-POCT", "试剂", "快检试剂", "XL-胶体金快检试剂", "人份"),
    ("P-R-URI", "试剂", "尿液试剂", "XL-尿液分析试剂", "人份"),
    ("P-I-ANA", "仪器", "全自动生化分析仪", "XL-2000全自动生化分析仪", "台"),
    ("P-I-SEMI", "仪器", "半自动生化分析仪", "XL-800半自动生化分析仪", "台"),
    ("P-I-CLIA", "仪器", "化学发光分析仪", "XL-流明化学发光分析仪", "台"),
    ("P-I-ELISA", "仪器", "酶联免疫分析仪", "XL-酶联免疫分析仪", "台"),
    ("P-I-HEM", "仪器", "血细胞分析仪", "XL-五分类血细胞分析仪", "台"),
    ("P-I-COAG", "仪器", "凝血分析仪", "XL-全自动凝血分析仪", "台"),
    ("P-I-PCR", "仪器", "核酸扩增仪", "XL-实时荧光定量扩增仪", "台"),
    ("P-I-POCT", "仪器", "快检读数仪", "XL-便携快检读数仪", "台"),
    ("P-I-URI", "仪器", "尿液分析仪", "XL-尿液分析仪", "台"),
    ("P-I-ELEC", "仪器", "电解质分析仪", "XL-电解质分析仪", "台"),
    ("P-S-INST", "服务", "装机调试", "XL-装机调试服务", "次"),
    ("P-S-MAINT", "服务", "年度维保", "XL-年度维保服务", "次"),
    ("P-S-TRAIN", "服务", "应用培训", "XL-应用培训服务", "次"),
    ("P-S-CAL", "服务", "定期校准", "XL-定期校准服务", "次"),
    ("P-S-EQA", "服务", "室间质评", "XL-室间质评支持服务", "次"),
    ("P-S-UPG", "服务", "软件升级", "XL-软件升级服务", "次"),
    ("P-S-MOVE", "服务", "移机服务", "XL-仪器移机服务", "次"),
    ("P-S-REPAIR", "服务", "应急维修", "XL-应急维修服务", "次"),
    ("P-S-VALID", "服务", "试剂验证", "XL-试剂性能验证服务", "次"),
    ("P-S-PATROL", "服务", "巡检服务", "XL-定期巡检服务", "次"),
]
UNIT_PRICE = {
    "P-R-LIV": Decimal("920"),
    "P-R-REN": Decimal("780"),
    "P-R-LIP": Decimal("640"),
    "P-R-CLIA": Decimal("68"),
    "P-R-ELISA": Decimal("36"),
    "P-R-NA": Decimal("110"),
    "P-R-COAG": Decimal("540"),
    "P-R-HEM": Decimal("320"),
    "P-R-POCT": Decimal("22"),
    "P-R-URI": Decimal("18"),
    "P-I-ANA": Decimal("186000"),
    "P-I-SEMI": Decimal("46000"),
    "P-I-CLIA": Decimal("428000"),
    "P-I-ELISA": Decimal("62000"),
    "P-I-HEM": Decimal("96000"),
    "P-I-COAG": Decimal("142000"),
    "P-I-PCR": Decimal("268000"),
    "P-I-POCT": Decimal("16000"),
    "P-I-URI": Decimal("28000"),
    "P-I-ELEC": Decimal("35000"),
    "P-S-INST": Decimal("4800"),
    "P-S-MAINT": Decimal("26000"),
    "P-S-TRAIN": Decimal("3600"),
    "P-S-CAL": Decimal("2400"),
    "P-S-EQA": Decimal("1800"),
    "P-S-UPG": Decimal("6500"),
    "P-S-MOVE": Decimal("5200"),
    "P-S-REPAIR": Decimal("3000"),
    "P-S-VALID": Decimal("4200"),
    "P-S-PATROL": Decimal("1600"),
}
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
# Seeded extra agents per region: 2 A, 6 B, 10 C. Names are fictional place-style words.
EXTRA_NAMES = {
    "R-HD": ("钱塘", "姑苏", "金陵", "维扬", "会稽", "东瓯", "新安", "庐州", "淮扬",
             "沪滨", "虞山", "鹿城", "锡山", "澄江", "海陵", "婺州", "天目", "云间"),
    "R-HN": ("羊城", "鹏城", "南粤", "榕城", "鹭岛", "琼州", "莞城", "禅城", "端州",
             "惠阳", "韶州", "梧州", "柳江", "北海", "湛江", "泉州", "漳州", "莆阳"),
    "R-HB": ("京华", "燕京", "蓟门", "冀中", "沧州", "泰山", "泉城", "琴岛", "烟台",
             "潍坊", "保定", "唐山", "廊坊", "德州", "临沂", "邯郸", "承德", "威海"),
    "R-XN": ("蓉城", "巴渝", "春城", "黔中", "峨眉", "青城", "嘉州", "绵州", "宜宾",
             "泸州", "遵义", "大理", "丽江", "曲靖", "南充", "涪陵", "万州", "达州"),
    "R-XB": ("长安", "咸阳", "宝鸡", "金城", "敦煌", "酒泉", "张掖", "银川", "塞上",
             "昆仑", "伊犁", "哈密", "延安", "汉中", "榆林", "天水", "喀什", "石河"),
}
EXTRA_TIERS = ("A",) * 2 + ("B",) * 6 + ("C",) * 10
EXTRA_BASE = {"A": (1400000, 2400000), "B": (500000, 1200000), "C": (150000, 500000)}
EXTRA_TERMS = {"A": (60, 60, 90), "B": (45, 45, 60), "C": (30, 30, 45)}
LATE_STARTS = ("2026-01", "2026-02", "2026-03", "2026-05", "2026-06", "2026-07", "2026-08")
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


def random_weights(rng: random.Random, count: int) -> tuple[Decimal, ...]:
    raw = [rng.randint(50, 150) for _ in range(count)]
    total = Decimal(sum(raw))
    weights = [(Decimal(value) / total).quantize(Decimal("0.0001")) for value in raw[:-1]]
    weights.append(Decimal("1") - sum(weights, Decimal("0")))
    return tuple(weights)


def line_factor(customer: dict, ym: str, line: str) -> Decimal:
    if ym == "2026-10":
        if customer["role"] == "drag":
            return DRAG_OCT_LINE[line]
        base = HD_OTHER_OCT if customer["region_id"] == "R-HD" else OTHER_OCT
    else:
        base = MONTH_FACTOR[ym]
    return base * customer["noise"].get(ym, Decimal("1"))


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
    return customer["discount"]


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


def _customer_records(rng: random.Random) -> list[dict]:
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
    region_names = {region_id: name for region_id, name, _ in REGIONS}
    for region, names in EXTRA_NAMES.items():
        for word, tier in zip(names, EXTRA_TIERS):
            seq = sum(1 for row in rows if row["region_id"] == region and row["customer_tier"] == tier) + 1
            low, high = EXTRA_BASE[tier]
            rows.append(
                {
                    "customer_id": f"C-{region[2:]}-{tier}{seq:02d}",
                    "customer_name": f"{word}{region_names[region]}代理商",
                    "customer_tier": tier,
                    "region_id": region,
                    "payment_terms_days": rng.choice(EXTRA_TERMS[tier]),
                    "base": Decimal(rng.randrange(low, high + 1, 10000)),
                    "role": "normal",
                    "start": rng.choice(LATE_STARTS) if rng.random() < 0.15 else "2025-11",
                }
            )
    habit_weights = [share for _name, share, _days in PAY_HABITS]
    for row in rows:
        if row["role"] == "drag":
            row["noise"] = {}
            row["discount"] = NORMAL_DISCOUNT
            row["max_delay"] = 0
            row["order_days"] = {line: DRAG_DAYS for line in LINE_SHARE}
            continue
        row["order_days"] = {
            line: tuple(sorted(rng.sample(range(3, 27), rng.randint(*SHIPMENTS_PER_MONTH[line]))))
            for line in LINE_SHARE
        }
        row["noise"] = {ym: Decimal(rng.randint(94, 106)) / Decimal(100) for ym in MONTHS}
        row["discount"] = Decimal(rng.randrange(60, 105, 5)) / Decimal(1000)
        row["max_delay"] = rng.choices(PAY_HABITS, weights=habit_weights)[0][2]
    return rows


def _skus_by_line() -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for product_id, line, _cat, _sku, _unit in PRODUCTS:
        grouped.setdefault(line, []).append(product_id)
    return grouped


def generate(out_dir: Path | None = None) -> Path:
    folder = Path(out_dir) if out_dir else DATA_DIR
    folder.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    side = random.Random(SEED + 17)
    customers = _customer_records(rng)
    by_id = {row["customer_id"]: row for row in customers}
    skus = _skus_by_line()

    shipments: list[dict] = []
    collections: list[dict] = []
    snapshots: list[dict] = []
    open_invoices: list[dict] = []
    payments_by_month: dict[str, list] = {}
    ship_n = 1
    col_n = 1
    pay_seq = 0

    def schedule(inv):
        nonlocal pay_seq
        cust = inv["customer"]
        if cust["role"] == "drag":
            plan = [(inv["due"], q2(inv["original"] * pay_ratio(cust, inv["due"].strftime("%Y-%m"))))]
        else:
            first_date = inv["due"] + timedelta(days=rng.randint(0, cust["max_delay"]))
            if rng.random() < float(PARTIAL_PROB):
                first = q2(inv["original"] * Decimal(rng.randint(60, 90)) / Decimal(100))
                plan = [
                    (first_date, first),
                    (first_date + timedelta(days=rng.randint(15, 40)), inv["original"] - first),
                ]
            else:
                plan = [(first_date, inv["original"])]
        for pay_date, amount in plan:
            pay_seq += 1
            payments_by_month.setdefault(pay_date.strftime("%Y-%m"), []).append((pay_date, pay_seq, inv, amount))

    def add_shipment(cust, ship_date, product_id, net, disc_rate, due):
        nonlocal ship_n
        net = q2(net)
        if net <= 0:
            return
        list_amt = q2(net / (Decimal("1") - disc_rate))
        discount = list_amt - net
        unit = UNIT_PRICE[product_id]
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
        inv = {
            "invoice_id": invoice_id,
            "invoice_date": ship_date,
            "due": due,
            "customer": cust,
            "product_id": product_id,
            "original": net,
            "outstanding": net,
        }
        open_invoices.append(inv)
        schedule(inv)

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
            for line, share in LINE_SHARE.items():
                if surge:
                    days, weights = SURGE_DAYS, SURGE_WEIGHTS
                elif cust["role"] == "drag":
                    days, weights = DRAG_DAYS, DRAG_WEIGHTS
                else:
                    habit = cust["order_days"][line]
                    moved = sorted({min(28, max(1, day + rng.randint(-DAY_JITTER, DAY_JITTER))) for day in habit})
                    days = tuple(moved) if len(moved) == len(habit) else habit
                    weights = random_weights(rng, len(days))
                target = q2(cust["base"] * line_factor(cust, ym, line) * share)
                parts = split_money(target, weights)
                for day, net in zip(days, parts):
                    ship_date = date(year, month, day)
                    due = ship_date + timedelta(days=cust["payment_terms_days"])
                    # Length 6 matches the catalog the locked story was drawn with.
                    # _randbelow retries, so a longer list would move every later payment.
                    rng.choice(range(6))
                    add_shipment(
                        cust,
                        ship_date,
                        side.choice(skus[line]),
                        net,
                        discount_rate(cust, ym, line),
                        due,
                    )
        for pay_date, _seq, inv, amount in sorted(payments_by_month.pop(ym, []), key=lambda item: (item[0], item[1])):
            pay = min(q2(amount), inv["outstanding"])
            if pay <= 0:
                continue
            cid = f"COL{col_n:06d}"
            col_n += 1
            collections.append(
                {
                    "collection_id": cid,
                    "collection_date": pay_date.isoformat(),
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
        open_invoices[:] = [inv for inv in open_invoices if inv["outstanding"] > 0]
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
