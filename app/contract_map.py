"""Map a question onto the locked metric / dimension / relation enums.

This is not open NL-to-SQL. A question that does not land on those enums is
refused with a section 5.1 type. Preset reports stay on their own path.
"""

from __future__ import annotations

import re

from app.constants import DATA_END, DATA_START
from app.metrics import shift_month
from app.router import (
    PERIOD_BLOCK,
    REGION_NAMES,
    UNMAPPED_MESSAGE,
    hard_block_detail,
    resolve_period,
)
from app.store import Store

REGION_LABELS = {name for name, _rid in REGION_NAMES}
LINES = ("试剂", "仪器", "服务")
NEW_METRICS = {
    "shipment_qty",
    "due_amount",
    "new_ar",
    "aging_amount",
    "aging_share",
    "avg_collection_days",
    "active_agent_count",
    "collecting_agent_count",
    "top5_ar_concentration",
}
ALIASES = (
    ("到期回款率", "due_collection_rate"),
    ("销售回款率", "collection_rate"),
    ("平均回款天数", "avg_collection_days"),
    ("回款天数", "avg_collection_days"),
    ("加权平均账龄", "weighted_age_days"),
    ("前5家应收集中度", "top5_ar_concentration"),
    ("前五家应收集中度", "top5_ar_concentration"),
    ("应收集中度", "top5_ar_concentration"),
    ("活跃代理商数", "active_agent_count"),
    ("活跃代理商", "active_agent_count"),
    ("回款代理商数", "collecting_agent_count"),
    ("回款代理商", "collecting_agent_count"),
    ("有销售代理商", "customer_count"),
    ("代理商数量", "customer_count"),
    ("本期到期应收", "due_amount"),
    ("到期应收", "due_amount"),
    ("本期新增应收", "new_ar"),
    ("新增应收", "new_ar"),
    ("逾期余额", "overdue_balance"),
    ("逾期率", "overdue_rate"),
    ("应收余额", "ar_balance"),
    ("账龄占比", "aging_share"),
    ("账龄金额", "aging_amount"),
    ("折扣率", "discount_rate"),
    ("折扣额", "discount_amount"),
    ("出库量", "shipment_qty"),
    ("出库额", "list_amount"),
    ("净销售额", "net_sales"),
    ("净销售", "net_sales"),
    ("销售额", "net_sales"),
    ("回款额", "collection_amount"),
    ("回款率", "collection_rate"),
    ("DSO", "dso"),
    ("dso", "dso"),
)
RELATIONS = (
    ("率变分解", "rate_decompose"),
    ("回款率分解", "rate_decompose"),
    ("交叉贡献", "cross_contribute"),
    ("账龄迁移", "aging_migration"),
    ("应收月环比", "ar_bridge"),
    ("应收余额月环比", "ar_bridge"),
    ("应收拆解", "ar_bridge"),
    ("月初7", "month_timing"),
    ("月初七", "month_timing"),
    ("月末7", "month_timing"),
    ("月末七", "month_timing"),
    ("下降最快", "fastest_decline"),
    ("代理商贡献", "agent_contribute"),
)
BUCKET_PATTERNS = (
    (r"未到期", "未到期"),
    (r"90\s*\+|90天以上", "90+"),
    (r"61\s*[–\-]\s*90", "61–90"),
    (r"31\s*[–\-]\s*60", "31–60"),
    (r"1\s*[–\-]\s*30", "1–30"),
)


def find_metrics(text: str) -> list[str]:
    occupied = [False] * len(text)
    found = []
    for alias, key in sorted(ALIASES, key=lambda item: len(item[0]), reverse=True):
        if alias.isascii():
            hay, needle = text.lower(), alias.lower()
        else:
            hay, needle = text, alias
        start = 0
        while True:
            index = hay.find(needle, start)
            if index < 0:
                break
            end = index + len(needle)
            if not any(occupied[index:end]):
                for cursor in range(index, end):
                    occupied[cursor] = True
                found.append((index, key))
            start = end
    found.sort()
    keys = []
    for _index, key in found:
        if key not in keys:
            keys.append(key)
    return keys


def primary_metric(text: str) -> str | None:
    found = find_metrics(text)
    return found[0] if found else None


def _refuse(kind: str, detail: str) -> dict:
    if detail.startswith("暂不支持"):
        message = detail if kind in detail else f"暂不支持。{kind}：{detail.removeprefix('暂不支持').lstrip('。')}"
    else:
        message = f"暂不支持。{kind}：{detail}"
    return {"action": "refuse", "refusal_type": kind, "message": message}


def _window(text: str, end: str) -> tuple[str, str] | None:
    compact = re.sub(r"\s+", "", text)
    if re.search(r"近半年|近六个月|近6个月", compact):
        return shift_month(end, -5), end
    if re.search(r"近三个月|近3个月", compact):
        return shift_month(end, -2), end
    counted = re.search(r"近(\d+)个月", compact)
    if counted:
        width = int(counted.group(1))
        if width < 1 or width > 12:
            return None
        if width == 1:
            return end, end
        return shift_month(end, 1 - width), end
    span = re.search(
        r"(20\d{2})?年?(\d{1,2})月(?:至|到|–|—|-)"
        r"(?:(20\d{2})年)?(\d{1,2})月",
        compact,
    )
    if span:
        year_end = span.group(3) or span.group(1) or end[:4]
        year_start = span.group(1) or year_end
        start = f"{year_start}-{int(span.group(2)):02d}"
        close = f"{year_end}-{int(span.group(4)):02d}"
        if start > close:
            start, close = close, start
        return start, close
    iso = re.search(r"(20\d{2})-(0[1-9]|1[0-2])(?:至|到|–|—)(20\d{2})-(0[1-9]|1[0-2])", compact)
    if iso:
        start, close = f"{iso.group(1)}-{iso.group(2)}", f"{iso.group(3)}-{iso.group(4)}"
        if start > close:
            start, close = close, start
        return start, close
    return end, end


def _unknown_member(store: Store, text: str) -> dict | None:
    for match in re.finditer(r"([\u4e00-\u9fff]{2})大区", text):
        name = match.group(1)
        if name not in REGION_LABELS:
            return _refuse(
                "缺维度",
                f"没有找到名为「{name}」的大区。可选的大区是：华东、华南、华北、西南、西北。不会按猜测计算。",
            )
    known = set()
    for product in store.products.values():
        known.add(product["product_category"])
        known.add(product["sku"])
    for match in re.finditer(r"[\u4e00-\u9fff]{0,8}试剂", text):
        token = match.group(0)
        core = re.sub(r"^(?:按|各|该|每|看|的|和|与|及|只|条)+", "", token)
        if core in known or core in {"试剂", ""}:
            continue
        if "产品线" in text[match.start(): match.end() + 4]:
            continue
        options = "、".join(sorted(product["product_category"] for product in store.products.values() if product["product_line"] == "试剂")[:8])
        return _refuse("缺维度", f"没有找到名为「{core}」的产品。可选的试剂产品类包括：{options}。不会按猜测计算。")
    return None


def _relation(text: str) -> str | None:
    compact = re.sub(r"\s+", "", text)
    for needle, operation in RELATIONS:
        if needle in compact:
            return operation
    return None


def _groups(text: str) -> list[str]:
    compact = re.sub(r"\s+", "", text)
    found = []
    if re.search(r"各大区|按大区|分大区|每个大区", compact):
        found.append("region")
    if re.search(r"各产品线|按产品线|分产品线|每条产品线", compact):
        found.append("product_line")
    if re.search(r"各产品类|按产品类|按产品(?!线)|各产品(?!线)|分产品(?!线)", compact):
        found.append("product")
    if re.search(r"各等级|按等级|每个等级", compact) or re.search(r"A\s*/\s*B\s*/\s*C", text):
        found.append("tier")
    if re.search(r"按新老|新老对比|新代理商和老|老代理商和新", compact) or ("新代理商" in text and "老代理商" in text):
        found.append("cohort")
    if re.search(r"各账龄|按账龄|账龄桶|账龄段", compact):
        found.append("aging_bucket")
    if re.search(r"按代理商|各代理商", compact) and "cohort" not in found:
        found.append("customer")
    return found


def _buckets(text: str) -> list[str]:
    found = []
    for pattern, bucket in BUCKET_PATTERNS:
        if re.search(pattern, text) and bucket not in found:
            found.append(bucket)
    return found


def _cross_dimension(text: str, args: dict) -> str | None:
    compact = re.sub(r"\s+", "", text)
    if re.search(r"按产品类|各产品类", compact):
        return "product"
    if re.search(r"按产品线|各产品线", compact):
        return "product_line"
    if re.search(r"按大区|各大区", compact):
        return "region"
    if re.search(r"按等级|各等级", compact):
        return "tier"
    if "新老" in compact or ("新代理商" in text and "老代理商" in text):
        return "cohort"
    if re.search(r"按代理商|各代理商", compact):
        return "customer"
    fixed = set()
    if args.get("region"):
        fixed.add("region")
    if args.get("product_line") or args.get("product"):
        fixed.add("product_line")
    if args.get("product"):
        fixed.add("product")
    if args.get("tier"):
        fixed.add("tier")
    if args.get("cohort"):
        fixed.add("cohort")
    if args.get("customer"):
        fixed.add("customer")
    for dimension in ("product_line", "region", "tier", "cohort", "product", "customer"):
        if dimension not in fixed:
            return dimension
    return None


def _filters(store: Store, text: str) -> dict:
    regions = [name for name in REGION_LABELS if name in text]
    grouped_region = bool(re.search(r"各大区|每个大区|按大区|分大区", text))
    if len(regions) > 1 and not grouped_region:
        return {"error": _refuse("无法映射", "问题里有多个大区，不能唯一对应到一个切片。请改问一个大区，或说各大区。")}
    lines = [line for line in LINES if line in text]
    grouped_line = bool(re.search(r"各产品线|每条产品线|按产品线|分产品线", text))
    if len(lines) > 1 and not grouped_line:
        return {"error": _refuse("无法映射", "问题里有多条产品线，不能唯一对应到一个切片。请改问一条产品线，或说各产品线。")}
    names = [row["customer_name"] for row in store.customers.values() if row["customer_name"] in text]
    if len(set(names)) > 1:
        return {"error": _refuse("无法映射", "问题里有多家代理商，不能唯一对应。请改问一家，或说按代理商。")}
    product_hits = []
    for product in store.products.values():
        if product["product_category"] in text or product["sku"] in text:
            product_hits.append(product["product_category"])
    product_hits = list(dict.fromkeys(product_hits))
    grouped_product = bool(re.search(r"各产品类|按产品类|各产品(?!线)|按产品(?!线)", text))
    if len(product_hits) > 1 and not grouped_product:
        return {"error": _refuse("无法映射", "问题里有多个产品类，不能唯一对应。请改问一个产品类，或说按产品类。")}
    tiers = re.findall(r"([ABC])级", text)
    args: dict = {}
    if len(regions) == 1 and not grouped_region:
        args["region"] = regions[0]
    if len(lines) == 1 and not grouped_line:
        args["product_line"] = lines[0]
    if len(product_hits) == 1 and not grouped_product:
        args["product"] = product_hits[0]
    if len(set(names)) == 1:
        args["customer"] = names[0]
    if len(tiers) == 1 and not re.search(r"各等级|按等级|A\s*/\s*B\s*/\s*C", text):
        args["tier"] = tiers[0]
    if "新代理商" in text and "老代理商" not in text and "新老" not in text:
        args["cohort"] = "new"
    elif "老代理商" in text and "新代理商" not in text and "新老" not in text:
        args["cohort"] = "old"
    buckets = _buckets(text)
    grouped_aging = bool(re.search(r"各账龄|按账龄|账龄桶|账龄段", text))
    if len(buckets) > 1 and not grouped_aging:
        return {"error": _refuse("无法映射", "问题里有多个账龄桶，请改问一个桶，或说按账龄桶。")}
    if len(buckets) == 1:
        args["aging_bucket"] = buckets[0]
    if "出库量" in text:
        for unit in ("人份", "盒", "瓶", "台", "次"):
            if re.search(rf"(?:按|单位|以){unit}|{unit}装|{unit}的出库", text):
                args["unit"] = unit
                break
    return {"args": args}


def _new_surface(metrics: list[str], args: dict, group: str | None) -> bool:
    if set(metrics) & NEW_METRICS:
        return True
    if args.get("cohort") or args.get("aging_bucket") or args.get("product") or args.get("unit"):
        return True
    if group in {"product", "aging_bucket"}:
        return True
    if group == "cohort" and not set(metrics) <= {"collection_rate"}:
        return True
    return False


def _parent(args: dict) -> bool:
    return any(args.get(key) for key in ("region", "product_line", "product", "tier", "cohort", "customer"))


def parse_question(store: Store, text: str, period: str) -> dict | None:
    """Return a query/relation/refuse plan, or None when the question names no enum."""
    raw = text.strip()
    blocked = hard_block_detail(raw, resolve_period(raw, period))
    if blocked:
        kind, message = blocked
        return _refuse(kind, message)
    unknown = _unknown_member(store, raw)
    if unknown:
        return unknown
    relation = _relation(raw)
    metrics = find_metrics(raw)
    compact = re.sub(r"\s+", "", raw)
    if not metrics and re.search(r"销售数据|经营数据", compact) and relation is None:
        metrics = ["net_sales", "list_amount", "collection_amount", "collection_rate"]
    groups = _groups(raw)
    if len(set(groups)) > 1 and relation is None:
        return _refuse("无法映射", "问题里有多个分组方式，不能唯一对应。请只保留一个分组。")
    group = groups[0] if groups else None
    if group == "aging_bucket" and "overdue_balance" not in metrics and "overdue_rate" not in metrics and "逾期" in raw:
        metrics = ["overdue_balance", *metrics]
    filters = _filters(store, raw)
    if filters.get("error"):
        return filters["error"]
    args = dict(filters["args"])
    end = resolve_period(raw, period)
    window = _window(raw, end)
    if window is None:
        return _refuse("缺时间窗", PERIOD_BLOCK.removeprefix("暂不支持。缺时间窗："))
    start, close = window
    if start < DATA_START or close > DATA_END or start > DATA_END or close < DATA_START:
        return _refuse("缺时间窗", PERIOD_BLOCK.removeprefix("暂不支持。缺时间窗："))
    if relation is None and not metrics:
        return None
    plan = {
        "new_surface": _new_surface(metrics, args, group) or relation is not None,
        "parent": _parent(args),
        "period_is_start": close == DATA_START,
        "metrics": metrics,
        "operation": relation,
    }
    if relation:
        query = {"operation": relation, "start_month": start, "end_month": close, **args}
        if relation == "cross_contribute":
            dimension = _cross_dimension(raw, args)
            if not dimension:
                return _refuse("无法映射", "请说明交叉贡献按大区、产品线、产品类、等级、新老或代理商拆开。")
            query["dimension"] = dimension
        if relation == "agent_contribute" and re.search(r"贡献最大|最高", raw):
            query["rank"] = "top"
        elif relation == "agent_contribute":
            query["rank"] = "drag"
        plan["action"] = "relation"
        plan["args"] = query
        if relation == "fastest_decline" and not plan["parent"]:
            plan["new_surface"] = False
        if relation == "ar_bridge" and not plan["parent"]:
            plan["new_surface"] = False
        return plan
    plan["action"] = "query"
    plan["new_surface"] = _new_surface(metrics, args, group)
    plan["args"] = {"metrics": metrics, "start_month": start, "end_month": close, "group_by": group or "none", **args}
    return plan


def should_direct(intent: str, plan: dict) -> bool:
    """Use the enum plan instead of a preset report."""
    if plan.get("action") == "relation":
        if plan.get("operation") == "fastest_decline" and intent == "main" and not plan.get("parent"):
            return False
        if plan.get("operation") == "ar_bridge" and intent == "ar_bridge" and not plan.get("parent"):
            return False
        return True
    if plan.get("new_surface") or plan.get("period_is_start"):
        return True
    if intent in {"unsupported", "slice_focus"}:
        return True
    implied = {
        "trend": {"collection_rate"},
        "main": {"collection_rate"},
        "tier": {"collection_rate"},
        "new_old": {"collection_rate"},
        "volume": {"collection_rate", "net_sales", "collection_amount"},
        "discount": {"discount_rate", "collection_rate"},
        "dso": {"dso"},
        "overdue": {"overdue_balance", "ar_balance", "aging_amount", "aging_share"},
        "top_ar": {"ar_balance"},
        "cycle": {"dso", "avg_collection_days"},
        "aging90": {"aging_share", "aging_amount"},
        "top_collection": {"collection_amount"},
        "growth_lag": {"net_sales", "collection_amount"},
        "agents": {"collection_rate"},
    }.get(intent)
    metrics = set(plan.get("metrics") or [])
    if implied is not None and metrics and not metrics <= implied:
        return True
    return False


UNMAPPED = _refuse("无法映射", UNMAPPED_MESSAGE.removeprefix("暂不支持。无法映射："))
