"""Free-form questions. DeepSeek either opens an existing analysis or calls query_metrics.

The model never computes: every number it may quote comes back from a tool call,
and a reply that cites anything else is dropped in favour of the computed table.
Without DEEPSEEK_API_KEY (or when the call fails) the keyword router answers.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from decimal import Decimal

from app.constants import DATA_END, DATA_START, LINE_ORDER, REGION_ORDER, TIER_ORDER
from app.metrics import (
    Scope,
    ar_balance,
    collection_amount,
    discount_amount,
    dso,
    due_amount,
    iter_months,
    list_amount,
    net_sales,
    overdue_balance,
    shift_month,
    weighted_age_days,
)
from app.narrative import unknown_numbers
from app.reports import (
    BUILDERS,
    F_AGE_DAYS,
    F_AR,
    F_COL,
    F_DISCOUNT,
    F_DSO,
    F_DUE,
    F_NET,
    F_OVERDUE,
    F_RATE,
    F_SHIP,
    _blocked,
    _prov,
    answer as keyword_answer,
    try_customer_sku,
    build_kpis,
    comparable,
)
from app.router import INTENT_ID, QUESTIONS, REGION_NAMES, Query, hard_block, resolve_period
from app.store import Store
from app.textfmt import days_num, days_signed, days_text, money_num, money_text, pct_num, pct_signed, pct_text, period_label, pp_text

MAX_ROUNDS = 6
MAX_MONTHS = 12
MAX_CELLS = 600
MAX_CHARTS = 4
HISTORY_TURNS = 6
PRESET_TEXTS = {item["text"] for item in QUESTIONS}
REGION_ID = dict(REGION_NAMES)
REGION_LABEL = {rid: name for name, rid in REGION_NAMES}

# (label, kind, formula). Flow metrics sum over a window; balances do not.
METRICS = {
    "net_sales": ("净销售额", "money", F_NET),
    "list_amount": ("出库额", "money", F_SHIP),
    "discount_amount": ("折扣额", "money", F_DISCOUNT),
    "discount_rate": ("折扣率", "pct", F_DISCOUNT),
    "collection_amount": ("回款额", "money", F_COL),
    "collection_rate": ("销售回款率", "pct", F_RATE),
    "due_collection_rate": ("到期回款率（辅助）", "pct", F_DUE),
    "ar_balance": ("应收余额", "money", F_AR),
    "overdue_balance": ("逾期余额", "money", F_OVERDUE),
    "overdue_rate": ("逾期率", "pct", F_OVERDUE),
    "dso": ("DSO", "days", F_DSO),
    "weighted_age_days": ("加权平均账龄", "days", F_AGE_DAYS),
}
FLOW = {"net_sales": net_sales, "list_amount": list_amount, "discount_amount": discount_amount, "collection_amount": collection_amount}

INTENT_GUIDE = {
    "main": "上个月哪个大区/哪类产品的销售回款率下降最快，原因是什么（主问）",
    "trend": "近 6 个月各大区销售回款率走势（可只看一个大区）",
    "overdue": "逾期应收集中在哪些代理商、哪些账龄段",
    "volume": "回款率下降是出货冲量抬高分母，还是回款本身变少（可按大区/产品线）",
    "discount": "折扣/商务政策上升的切片，回款率是否同步变差",
    "dso": "各大区 DSO 及近两个月变化",
    "top_ar": "TOP10 欠款代理商及账龄结构",
    "cycle": "试剂、仪器、服务的回款周期差异",
    "tier": "A/B/C 级代理商销售回款率对比",
    "new_old": "新代理商与老代理商销售回款率对比",
    "ar_bridge": "应收余额月环比由什么驱动",
    "aging90": "90 天以上账龄占比是否恶化",
    "top_collection": "本月回款贡献最大的代理商和大区",
    "growth_lag": "出货在增长、回款没跟上的产品线",
    "agents": "某个大区/产品线里拖累销售回款率的代理商",
    "slice_focus": "聚焦某个大区/产品线/等级，看单月销售回款率环比和归因",
}

SYSTEM = """你是星澜诊断的经营分析助手，回答代理商销售与回款相关的问题。数据是虚构的演示数据，经营月份为 2025年11月 至 2026年10月。销售对象全部是代理商（A/B/C 三级），按大区（华东、华南、华北、西南、西北）和产品线（试剂、仪器、服务）划分。每条产品线下有多个具体产品，问「有哪些产品」或某个产品时用 query_metrics 的 product、group_by=product。

当前页面期间：{label}（{ym}）。「本月」「上个月」「最近」都指这个期间；「近半年」「近 6 个月」指 {start6} 至 {ym}；「近 3 个月」指 {start3} 至 {ym}。

做法：
1. 问题与下列现成分析之一一致时，调用 open_report：
{guide}
2. 其他取数问题（某个范围的逐月数据、排行、对比、合计）调用 query_metrics，可多次调用；拿到结果后用中文回答 2 到 4 句。
   - 只查用户问到的指标。用户只说「销售数据」「经营数据」时，查净销售额、出库额、回款额、销售回款率。
   - 排行或排序时，把排序依据的指标放在 metrics 第一个；工具已按它从高到低排好，直接沿用工具给的顺序和排名。
   - 没说明排序依据的「排序」，按销售回款率排。
3. 需要本数据没有的信息（承诺回款、直营、医院或体检中心作为客户、退货、坏账、代理商库存、预测、目标或预算等），或与经营分析无关时，调用 reject 说明原因。

回答规则：
- 只能引用工具返回的数字，按原样抄写（含千分位、小数、单位、正负号）。不得自己加减乘除、估算或换算单位（不要把元换成万元）。工具没给的合计、占比、差值，写「本数据无法支持」。
- 只写工具结果里能直接读出的事实。说升降时以「环比」列为准（正为升、负为降），不要自己比较两个数的大小，也不要跨指标推论。
- 销售回款率 = 回款额 ÷ 净销售额，是主口径；到期回款率只作辅助对照。
- 一律称代理商，不写经销商。
- 不要输出表格或 Markdown，左侧页面会展示计算表和图。"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "open_report",
            "description": "打开一个现成分析报告（左侧整页刷新）。问题与现成分析一致时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "analysis": {"type": "string", "enum": list(INTENT_GUIDE)},
                    "region": {"type": "string", "enum": [name for name, _ in REGION_NAMES]},
                    "product_line": {"type": "string", "enum": list(LINE_ORDER)},
                    "tier": {"type": "string", "enum": list(TIER_ORDER)},
                    "period": {"type": "string", "description": "YYYY-MM，默认当前页面期间"},
                },
                "required": ["analysis"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_metrics",
            "description": (
                "按月计算指标。可按大区、产品线、具体产品、等级或代理商分组。多个月时每组附「区间合计」"
                "（金额类求和，回款率/折扣率按合计重算；余额、DSO、账龄类没有合计）。"
                "按产品分组看一条产品线下的各个产品，建议同时给 product_line。"
                "按代理商分组用于代理商排行，只看 end_month 一个月，按第一个指标从高到低取前 top_n 家。"
                "看某一家代理商或某一个产品的逐月数据时，用 customer 或 product 参数并且不分组。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "metrics": {"type": "array", "items": {"type": "string", "enum": list(METRICS)}, "minItems": 1},
                    "start_month": {"type": "string", "description": "YYYY-MM"},
                    "end_month": {"type": "string", "description": "YYYY-MM"},
                    "region": {"type": "string", "enum": [name for name, _ in REGION_NAMES]},
                    "product_line": {"type": "string", "enum": list(LINE_ORDER)},
                    "product": {"type": "string", "description": "具体产品名称，例如肝功试剂、全自动生化分析仪、年度维保"},
                    "tier": {"type": "string", "enum": list(TIER_ORDER)},
                    "customer": {"type": "string", "description": "代理商名称"},
                    "group_by": {"type": "string", "enum": ["none", "region", "product_line", "product", "tier", "customer"]},
                    "top_n": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["metrics"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reject",
            "description": "问题超出本数据或与经营分析无关时使用。",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
        },
    },
]


class ToolError(ValueError):
    pass


def _month_arg(value, fallback: str) -> str:
    text = str(value or "").strip() or fallback
    if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", text):
        raise ToolError(f"月份格式应为 YYYY-MM：{text}")
    if text < DATA_START or text > DATA_END:
        raise ToolError(f"{text} 超出数据范围（{DATA_START} 至 {DATA_END}）")
    return text


def _find_customer(store: Store, name: str) -> dict:
    name = name.strip()
    exact = [row for row in store.customers.values() if row["customer_name"] == name]
    if exact:
        return exact[0]
    loose = [row for row in store.customers.values() if name in row["customer_name"]]
    if len(loose) == 1:
        return loose[0]
    if not loose:
        raise ToolError(f"没有找到代理商：{name}")
    raise ToolError(f"代理商名称不唯一：{name}，可选 " + "、".join(row["customer_name"] for row in loose[:8]))


def _metric_value(store: Store, key: str, months: list[str], scope: Scope) -> Decimal | None:
    """One month, or a window total when len(months) > 1 (flow metrics only)."""
    if len(months) > 1:
        if key in FLOW:
            return sum((FLOW[key](store, m, scope) for m in months), Decimal("0"))
        if key == "collection_rate":
            net = sum((net_sales(store, m, scope) for m in months), Decimal("0"))
            return None if net == 0 else sum((collection_amount(store, m, scope) for m in months), Decimal("0")) / net
        if key == "discount_rate":
            gross = sum((list_amount(store, m, scope) for m in months), Decimal("0"))
            return None if gross == 0 else sum((discount_amount(store, m, scope) for m in months), Decimal("0")) / gross
        if key == "due_collection_rate":
            due = sum((due_amount(store, m, scope) for m in months), Decimal("0"))
            return None if due == 0 else sum((collection_amount(store, m, scope) for m in months), Decimal("0")) / due
        return None
    month = months[0]
    if key in FLOW:
        return FLOW[key](store, month, scope)
    if key == "collection_rate":
        net = net_sales(store, month, scope)
        return None if net == 0 else collection_amount(store, month, scope) / net
    if key == "discount_rate":
        gross = list_amount(store, month, scope)
        return None if gross == 0 else discount_amount(store, month, scope) / gross
    if key == "due_collection_rate":
        due = due_amount(store, month, scope)
        return None if due == 0 else collection_amount(store, month, scope) / due
    if key == "ar_balance":
        return ar_balance(store, month, scope)
    if key == "overdue_balance":
        return overdue_balance(store, month, scope)
    if key == "overdue_rate":
        balance = ar_balance(store, month, scope)
        return None if balance == 0 else overdue_balance(store, month, scope) / balance
    if key == "dso":
        return dso(store, month, scope)
    if key == "weighted_age_days":
        return weighted_age_days(store, month, scope)
    raise ToolError(f"未知指标：{key}")


def _text(kind: str, value: Decimal | None) -> str:
    if value is None:
        return "—"
    if kind == "money":
        return money_text(value)
    if kind == "pct":
        return pct_text(value)
    return days_text(value)


def _change(kind: str, prev: Decimal | None, cur: Decimal | None) -> str:
    """Amounts: relative change; rates: pp; days: day difference."""
    if prev is None or cur is None:
        return "—"
    if kind == "days" and abs(cur - prev) < Decimal("0.05"):
        return "0.0天"
    if kind == "pct" and abs(cur - prev) < Decimal("0.00005"):
        return "0.00pp"
    if kind == "money":
        return "—" if prev == 0 else pct_signed((cur - prev) / prev)
    if kind == "pct":
        return pp_text(cur - prev)
    return days_signed(cur - prev)


def _num(kind: str, value: Decimal | None):
    if value is None:
        return None
    if kind == "money":
        return money_num(value)
    if kind == "pct":
        return pct_num(value)
    return days_num(value)


def _find_product(store: Store, name: str) -> dict:
    name = name.strip()
    rows = list(store.products.values())
    exact = [row for row in rows if name in {row["product_category"], row["sku"]}]
    if len(exact) == 1:
        return exact[0]
    loose = [row for row in rows if name in row["product_category"] or name in row["sku"]]
    if len(loose) == 1:
        return loose[0]
    if not loose:
        raise ToolError(f"没有找到产品：{name}")
    raise ToolError(f"产品名称不唯一：{name}，可选 " + "、".join(row["product_category"] for row in loose[:8]))


def _scope_label(store: Store, scope: Scope, customer: dict | None) -> str:
    parts = []
    if customer:
        parts.append(customer["customer_name"])
    if scope.product_category:
        parts.append(scope.product_category)
    if scope.region_id:
        parts.append(f"{REGION_LABEL[scope.region_id]}大区")
    if scope.product_line and not scope.product_category:
        parts.append(f"{scope.product_line}产品线")
    if scope.tier:
        parts.append(f"{scope.tier}级代理商")
    return " · ".join(parts) or "公司合计"


def run_query(store: Store, args: dict, period: str) -> dict:
    keys = [key for key in args.get("metrics") or [] if key in METRICS]
    if not keys:
        raise ToolError("metrics 至少给一个有效指标")
    end = _month_arg(args.get("end_month"), period)
    start = _month_arg(args.get("start_month"), end)
    if start > end:
        start, end = end, start
    months = iter_months(start, end)
    if len(months) > MAX_MONTHS:
        raise ToolError(f"一次最多查 {MAX_MONTHS} 个月")

    region = args.get("region")
    if region and region not in REGION_ID:
        raise ToolError(f"未知大区：{region}")
    line = args.get("product_line")
    if line and line not in LINE_ORDER:
        raise ToolError(f"未知产品线：{line}")
    tier = args.get("tier")
    if tier and tier not in TIER_ORDER:
        raise ToolError(f"未知等级：{tier}")
    customer = _find_customer(store, args["customer"]) if args.get("customer") else None
    product = _find_product(store, args["product"]) if args.get("product") else None
    if product and line and product["product_line"] != line:
        raise ToolError(f"{product['product_category']}属于{product['product_line']}产品线，不属于{line}")
    base = Scope(
        region_id=REGION_ID.get(region) if region else None,
        product_line=(product["product_line"] if product else line) or None,
        product_category=product["product_category"] if product else None,
        tier=tier or None,
        customer_id=customer["customer_id"] if customer else None,
    )
    title = _scope_label(store, base, customer)

    group_by = args.get("group_by") or "none"
    if customer and group_by == "customer":
        group_by = "none"
    notes = []
    if group_by == "region":
        groups = [(f"{REGION_LABEL[rid]}大区", base.narrow(region_id=rid)) for rid in REGION_ORDER if not base.region_id or base.region_id == rid]
    elif group_by == "product_line":
        groups = [(f"{name}产品线", base.narrow(product_line=name)) for name in LINE_ORDER if not base.product_line or base.product_line == name]
    elif group_by == "product":
        groups = [
            (row["product_category"], base.narrow(product_line=row["product_line"], product_category=row["product_category"]))
            for row in store.products.values()
            if (not base.product_line or row["product_line"] == base.product_line)
            and (not base.product_category or row["product_category"] == base.product_category)
        ]
        groups.sort(key=lambda item: item[0])
    elif group_by == "tier":
        groups = [(f"{name}级代理商", base.narrow(tier=name)) for name in TIER_ORDER if not base.tier or base.tier == name]
    elif group_by == "customer":
        months = [end]
        top_n = max(1, min(int(args.get("top_n") or 10), 20))
        candidates = []
        for row in store.customers.values():
            if base.region_id and row["region_id"] != base.region_id:
                continue
            if base.tier and row["customer_tier"] != base.tier:
                continue
            if base.customer_id and row["customer_id"] != base.customer_id:
                continue
            scope = base.narrow(customer_id=row["customer_id"])
            value = _metric_value(store, keys[0], months, scope)
            if value is not None:
                candidates.append((value, row, scope))
        candidates.sort(key=lambda item: item[0], reverse=True)
        groups = [(f"{row['customer_name']}（{REGION_LABEL[row['region_id']]}·{row['customer_tier']}级）", scope) for _, row, scope in candidates[:top_n]]
        customer_names = {label: row["customer_name"] for (_, row, _), (label, _) in zip(candidates[:top_n], groups)}
        notes.append(f"按代理商分组只看 {period_label(end)}，按{METRICS[keys[0]][0]}从高到低取前 {len(groups)} 家（共 {len(candidates)} 家有数）。")
    else:
        group_by = "none"
        groups = [(title, base)]
    if "customer_names" not in locals():
        customer_names = {}
    if len(groups) * len(months) * len(keys) > MAX_CELLS:
        raise ToolError("结果太大，请缩小月份或分组")

    window = f"{period_label(months[0])}至{period_label(months[-1])}，共 {len(months)} 个月" if len(months) > 1 else period_label(months[0])
    matrix = {}
    for label, scope in groups:
        matrix[label] = {key: [_metric_value(store, key, [month], scope) for month in months] for key in keys}
    if len(groups) > 1 and group_by != "customer":
        missing = Decimal("-1e30")

        def sort_value(item):
            label, scope = item
            value = matrix[label][keys[0]][0] if len(months) == 1 else _metric_value(store, keys[0], months, scope)
            return missing if value is None else value

        groups.sort(key=sort_value, reverse=True)
        span = "区间合计" if len(months) > 1 else period_label(months[0])
        notes.append(f"已按{span}的{METRICS[keys[0]][0]}从高到低排序。点击表头可改按其他列排序。")
    rows = []
    for rank, (label, scope) in enumerate(groups, start=1):
        for index, month in enumerate(months):
            row = {"排名": str(rank)} if len(months) == 1 and len(groups) > 1 else {}
            row.update({"范围": label, "月份": period_label(month)})
            if label in customer_names:
                row["_客户"] = customer_names[label]
            for key in keys:
                kind = METRICS[key][1]
                series = matrix[label][key]
                row[METRICS[key][0]] = _text(kind, series[index])
                if len(months) > 1:
                    row[f"{METRICS[key][0]}环比"] = "—" if index == 0 else _change(kind, series[index - 1], series[index])
            rows.append(row)
        if len(months) > 1:
            row = {"范围": label, "月份": "区间合计"}
            for key in keys:
                row[METRICS[key][0]] = _text(METRICS[key][1], _metric_value(store, key, months, scope))
                row[f"{METRICS[key][0]}环比"] = "—"
            rows.append(row)

    return {
        "result": {"范围": title, "窗口": window, "分组": group_by, "说明": notes, "数据": rows},
        "keys": keys,
        "months": months,
        "groups": [label for label, _ in groups],
        "matrix": matrix,
        "scope": base,
        "title": title,
        "group_by": group_by,
        "filters": [f"范围={title}", f"月份={window}", f"分组={group_by}"],
        "formulas": [METRICS[key][2] for key in keys],
    }


def _charts(query: dict) -> list[dict]:
    keys, months, groups, matrix = query["keys"], query["months"], query["groups"], query["matrix"]
    labels = [period_label(month) for month in months]
    prov = _prov(" ".join(dict.fromkeys(query["formulas"])), query["filters"], query["result"]["数据"][:80])
    charts = []
    if len(months) > 1 and len(groups) == 1:
        for kind in ("money", "pct", "days"):
            picked = [key for key in keys if METRICS[key][1] == kind]
            if not picked:
                continue
            charts.append({
                "id": f"free_{kind}",
                "title": f"{query['title']}：" + "、".join(METRICS[key][0] for key in picked) + "逐月走势",
                "type": "line",
                "y_format": kind,
                "categories": labels,
                "series": [{"name": METRICS[key][0], "values": [_num(kind, v) for v in matrix[groups[0]][key]]} for key in picked],
                "provenance": prov,
            })
    elif len(months) > 1:
        for key in keys[:2]:
            kind = METRICS[key][1]
            charts.append({
                "id": f"free_{key}",
                "title": f"{METRICS[key][0]}逐月走势（按{_group_name(query['group_by'])}）",
                "type": "line",
                "y_format": kind,
                "categories": labels,
                "series": [{"name": label, "values": [_num(kind, v) for v in matrix[label][key]]} for label in groups],
                "provenance": prov,
            })
    elif len(groups) > 1:
        key = keys[0]
        kind = METRICS[key][1]
        if query["group_by"] == "customer":
            charts.append({
                "id": f"free_{key}",
                "title": f"{period_label(months[0])}{METRICS[key][0]}：代理商排行",
                "type": "hbar",
                "rows": [{"name": label, "value": _num(kind, matrix[label][key][0]) or 0, "text": _text(kind, matrix[label][key][0])} for label in groups],
                "provenance": prov,
            })
        else:
            charts.append({
                "id": f"free_{key}",
                "title": f"{period_label(months[0])}{METRICS[key][0]}（按{_group_name(query['group_by'])}）",
                "type": "grouped_bar",
                "y_format": kind,
                "categories": groups,
                "series": [{"name": METRICS[key][0], "values": [_num(kind, matrix[label][key][0]) for label in groups]}],
                "provenance": prov,
            })
    columns = []
    for name in (query["result"]["数据"][0] if query["result"]["数据"] else {}):
        if str(name).startswith("_"):
            continue
        column = {"key": name, "label": name}
        if name == "范围" and query["group_by"] == "customer":
            column["drill"] = "customer"
        columns.append(column)
    charts.append({
        "id": "free_table",
        "title": f"计算结果：{query['title']} · {query['result']['窗口']}",
        "type": "table",
        "columns": columns,
        "rows": query["result"]["数据"],
        "provenance": prov,
    })
    return charts


def _group_name(group_by: str) -> str:
    return {"region": "大区", "product_line": "产品线", "product": "产品", "tier": "等级", "customer": "代理商"}.get(group_by, "范围")


def _call_model(messages: list[dict]) -> dict:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    base = os.environ.get("DEEPSEEK_API_BASE_URL", "https://api.deepseek.com").strip().rstrip("/")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat").strip() or "deepseek-chat"
    payload = {"model": model, "temperature": 0, "messages": messages, "tools": TOOLS, "tool_choice": "auto"}
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        body = json.loads(response.read().decode("utf-8"))
    return body["choices"][0]["message"]


def _open_report(store: Store, question: str, args: dict, period: str, view: str) -> dict:
    intent = args.get("analysis")
    if intent not in INTENT_GUIDE:
        raise ToolError(f"未知分析：{intent}")
    ym = _month_arg(args.get("period"), period)
    region = args.get("region")
    query = Query(
        intent,
        INTENT_ID[intent],
        question,
        ym,
        view,
        region_id=REGION_ID.get(region) if region else None,
        product_line=args.get("product_line") or None,
        tier=args.get("tier") or None,
    )
    compare, problem = comparable(store, ym)
    if problem:
        return _blocked(query, problem)
    scope = Scope(region_id=query.region_id, product_line=query.product_line, tier=query.tier)
    return BUILDERS[intent](store, query, compare, scope)


def _free_report(store: Store, question: str, period: str, view: str, text: str, queries: list[dict]) -> dict:
    last = queries[-1]
    months = last["months"]
    charts = []
    for item in queries:
        charts.extend(_charts(item))
    charts = [chart for chart in charts if chart["type"] != "table"][: MAX_CHARTS - 1] + [chart for chart in charts if chart["type"] == "table"][:2]
    end = months[-1]
    compare, _ = comparable(store, end)
    single = len(months) == 1
    query = Query("free", "free_query", question, end, view)
    return {
        "ok": True,
        "supported": True,
        "keep_report": False,
        "question_id": "free_query",
        "question": question,
        "period": end,
        "period_label": period_label(end) if single else f"{period_label(months[0])}至{period_label(end)}",
        "compare_period": compare if single else None,
        "compare_label": period_label(compare) if single and compare else "",
        "filters": {"region_id": last["scope"].region_id, "product_line": last["scope"].product_line, "tier": last["scope"].tier, "label": last["title"]},
        "view": query.view,
        "resolved_view": query.view,
        "view_switch": None,
        "conclusion": None,
        "kpis": build_kpis(store, end, compare, last["scope"]) if single and compare and not last["scope"].customer_id else [],
        "charts": charts,
        "attribution": None,
        "chat_reply": text,
    }


def _history(items: list[dict] | None) -> list[dict]:
    out = []
    for item in (items or [])[-HISTORY_TURNS:]:
        role = item.get("role")
        text = str(item.get("text") or "").strip()
        if role in {"user", "assistant"} and text:
            out.append({"role": role, "content": text[:600]})
    return out


def answer(store: Store, question: str, period: str, view: str = "auto", history: list[dict] | None = None) -> dict:
    text = question.strip()
    drilled = try_customer_sku(store, text, period, view)
    if drilled is not None:
        return drilled
    if text in PRESET_TEXTS or not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        return keyword_answer(store, text, period, view)
    chosen = resolve_period(text, period)
    blocked = hard_block(text, chosen)
    if blocked:
        return _blocked(Query("unsupported", "unsupported", text, chosen, view), blocked)

    system = SYSTEM.format(
        label=period_label(chosen),
        ym=chosen,
        start6=shift_month(chosen, -5),
        start3=shift_month(chosen, -2),
        guide="\n".join(f"   - {key}：{desc}" for key, desc in INTENT_GUIDE.items()),
    )
    messages = [{"role": "system", "content": system}, *_history(history), {"role": "user", "content": text}]
    queries: list[dict] = []
    try:
        for _ in range(MAX_ROUNDS):
            reply = _call_model(messages)
            calls = reply.get("tool_calls") or []
            if not calls:
                return _finish(store, text, chosen, view, (reply.get("content") or "").strip(), queries)
            messages.append({"role": "assistant", "content": reply.get("content") or "", "tool_calls": calls})
            for call in calls:
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                if name == "reject":
                    reason = str(args.get("reason") or "").strip() or "这个问题超出当前业务地图。"
                    return _blocked(Query("unsupported", "unsupported", text, chosen, view), f"暂不支持。{reason}")
                if name == "open_report":
                    try:
                        return _open_report(store, text, args, chosen, view)
                    except ToolError as error:
                        content = json.dumps({"error": str(error)}, ensure_ascii=False)
                elif name == "query_metrics":
                    try:
                        result = run_query(store, args, chosen)
                        queries.append(result)
                        content = json.dumps(result["result"], ensure_ascii=False)
                    except ToolError as error:
                        content = json.dumps({"error": str(error)}, ensure_ascii=False)
                else:
                    content = json.dumps({"error": f"未知工具：{name}"}, ensure_ascii=False)
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})
    except (urllib.error.URLError, KeyError, IndexError, json.JSONDecodeError, TimeoutError, ValueError):
        return keyword_answer(store, text, period, view)
    return _finish(store, text, chosen, view, "", queries)


def _finish(store: Store, question: str, period: str, view: str, text: str, queries: list[dict]) -> dict:
    if not queries:
        if text and not re.search(r"\d", text):
            return {**_blocked(Query("free", "free_chat", question, period, view), text), "supported": True}
        return _blocked(Query("unsupported", "unsupported", question, period, view),
                        "暂不支持。这个问题超出当前业务地图（销售回款率、大区、产品线、代理商等级、账龄）。")
    sheet = json.dumps([item["result"] for item in queries], ensure_ascii=False) + question
    if not text or unknown_numbers(text, sheet):
        text = f"已在左侧列出计算结果（{queries[-1]['title']} · {queries[-1]['result']['窗口']}）。模型的文字说明引用了计算结果里没有的数字，已不展示。" if text else \
            f"已在左侧列出计算结果（{queries[-1]['title']} · {queries[-1]['result']['窗口']}）。"
    return _free_report(store, question, period, view, text, queries)
