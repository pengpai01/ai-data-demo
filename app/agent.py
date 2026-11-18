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
    collection_rate,
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
    "customer_count": ("代理商数量", "count", "有销售数据的代理商：该范围内净销售额不为 0 的家数"),
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
   - 排行时把排序依据的指标放在 metrics 第一个，并直接沿用工具返回的排名，不要自己重排或自己算差值。比谁高用 order=value。只问本月谁最高时不要填 start_month，工具只看 end_month。问「最差 / 最低 / 最少 / 最小」（例如销售额最差、净销售额最低）用 order=low：只看 end_month，按该月数值从小到大取前 top_n 家。这就是最低的几家，直接引用排名和金额，不要说工具只返回从高到低的前 20 名、数据无法支持最低。order=low 是金额最小，不是跌幅，不要改用 order=drop。问了几家，top_n 就填几家。问「近半年 / 某段时间销售额最好、最高、最大的代理商」时必须同时填 start_month 和 end_month，工具按区间合计从高到低排，不是只看结束月。问「下滑最大 / 下降最多 / 跌得最多」用 order=drop；问「增长最大 / 上升最多」用 order=rise。drop 和 rise 的排名看「变动」列（期末减期初；金额是元，不是百分比）。对不上现成报告时只出表格。
   - 问有多少家代理商、某大区或某等级有几家时，metrics 只填 customer_count，不要用净销售额来数人数。公司整体用 group_by=region。家数没有对应的图，页面只展示表格和这句话。
   - 问代理商名单、列表、有哪些代理商时，调用 list_customers。客户维度只有大区、名称、等级。不要查销售额或回款，不要排名，不要图表，也不要补账期、产品线等没问到的字段。
   - 问各大区下每一家代理商的销售、不是问谁最高或前几名时，不要用 top_n 截成前 20，也不要按回款率再排一次名。按大区列出全部代理商的净销售额、出库额、回款额、销售回款率。
   - 没说明排序依据的「排序」，按销售回款率、order=value 排。
3. 需要本数据没有的信息（承诺回款、直营、医院或体检中心作为客户、退货、坏账、代理商库存、预测、目标或预算等），或与经营分析无关时，调用 reject 说明原因。

回答规则：
- 只能引用工具返回的数字，按原样抄写（含千分位、小数、单位、正负号）。不得自己加减乘除、估算或换算单位（不要把元换成万元）。工具没给的合计、占比、差值，写「本数据无法支持」。
- 只写工具结果里能直接读出的事实。说升降时以「环比」列为准（正为升、负为降），不要自己比较两个数的大小，也不要跨指标推论。
- 销售回款率 = 回款额 ÷ 净销售额，是主口径；到期回款率只作辅助对照。
- 一律称代理商，不写经销商。
- 不要输出表格或 Markdown。对得上现成报告时，左侧打开那份报告。对不上报告模板时，左侧只放你这句说明和一张表，不要图。"""

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
                "按代理商分组：order=value 且只给一个月时，只看 end_month，按第一个指标从高到低取前 top_n 家。"
                "order=low 且只给一个月时，同样只看 end_month，按第一个指标从低到高（最小在前）取前 top_n 家。"
                "问最差、最低、最少、最小（销售额最差、净销售额最低）时必须用 order=low，按数值从小到大。问最好、最高、最大时用 order=value，从大到小。家数不固定：最差的 5 家、最差的 10 家、最好的 8 家，top_n 都等于问句里的家数，只返回这么多家。直接引用这几家，不要改用 order=drop，也不要说只返回了前 20 名、无法支持。"
                "order=value 且 start_month 早于 end_month 时，按这段时间的区间合计从高到低取前 top_n 家（金额各月相加，比率按合计重算），不是只看结束月。"
                "order=low 且 start_month 早于 end_month 时，按这段时间的区间合计从低到高取前 top_n 家。"
                "order=drop 或 rise 比较 start_month 与 end_month（省略或与 end 相同则与上月比），"
                "按第一个指标的变动取前 top_n 家：drop 是下滑最大（变动最小），rise 是增长最大。"
                "金额的变动是期末减期初的元；百分比在环比列，不作为 drop/rise 的排序依据。"
                "看某一家代理商或某一个产品的逐月数据时，用 customer 或 product 参数并且不分组。"
                "问有多少家代理商时 metrics 只用 customer_count（净销售额不为 0 的家数），不要按代理商分组，页面只出表格。"
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
                    "order": {
                        "type": "string",
                        "enum": ["value", "low", "drop", "rise"],
                        "description": "value：单月按该月数值从高到低，跨月按区间合计从高到低。low：单月按该月数值从低到高（最小在前），跨月按区间合计从低到高；用于最差、最低、最少、最小，不是跌幅。drop 按变动从小到大（下滑最大在前）；rise 按变动从大到小。low、drop 和 rise 仅用于 group_by=customer。top_n 等于用户要的家数。",
                    },
                    "top_n": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["metrics"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_customers",
            "description": "列出代理商名录。只返回大区、代理商名称、等级，不返回销售额、回款、排名或图表。问名单、列表、有哪些代理商时使用。可按一个大区或一个等级筛选。",
            "parameters": {
                "type": "object",
                "properties": {
                    "region": {"type": "string", "enum": [name for name, _ in REGION_NAMES]},
                    "tier": {"type": "string", "enum": list(TIER_ORDER)},
                },
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


def _agents_with_sales(store: Store, months: list[str], scope: Scope) -> Decimal:
    """Agents whose net sales are not zero in any month of the window."""
    total = 0
    for row in store.customers.values():
        if scope.region_id and row["region_id"] != scope.region_id:
            continue
        if scope.tier and row["customer_tier"] != scope.tier:
            continue
        if scope.customer_id and row["customer_id"] != scope.customer_id:
            continue
        cust = scope.narrow(customer_id=row["customer_id"])
        if any(net_sales(store, month, cust) != 0 for month in months):
            total += 1
    return Decimal(total)


def _metric_value(store: Store, key: str, months: list[str], scope: Scope) -> Decimal | None:
    """One month, or a window total when len(months) > 1 (flow metrics only)."""
    if key == "customer_count":
        return _agents_with_sales(store, months, scope)
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
    if kind == "count":
        return f"{int(value)}家"
    return days_text(value)


def _delta_text(kind: str, prev: Decimal | None, cur: Decimal | None) -> str:
    """Signed change from the window start to the window end. Money is yuan, not a ratio."""
    if prev is None or cur is None:
        return "—"
    delta = cur - prev
    if kind == "count":
        homes = int(delta)
        return f"+{homes}家" if homes > 0 else f"{homes}家"
    if kind == "money":
        text = money_text(delta)
        return f"+{text}" if delta > 0 else text
    if kind == "pct":
        return pp_text(delta)
    return days_signed(delta)


def _change(kind: str, prev: Decimal | None, cur: Decimal | None) -> str:
    """Amounts: relative change; rates: pp; days: day difference."""
    if prev is None or cur is None:
        return "—"
    if kind == "count":
        homes = int(cur - prev)
        return f"+{homes}家" if homes > 0 else f"{homes}家"
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
    if kind == "count":
        return int(value)
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
    order = args.get("order") or "value"
    if order not in {"value", "low", "drop", "rise"}:
        raise ToolError("order 只能是 value、low、drop 或 rise")
    if order != "value" and group_by != "customer":
        raise ToolError("order=low、drop 或 rise 只用于按代理商分组")
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
        if "customer_count" in keys:
            raise ToolError("统计代理商家数时不要按代理商分组，请用 group_by=none、region、product_line 或 tier")
        top_n = max(1, min(int(args.get("top_n") or 10), 20))
        if order in {"value", "low"}:
            if start == end:
                months = [end]
        else:
            anchor = start if start != end else shift_month(end, -1)
            if anchor < DATA_START or anchor > DATA_END:
                raise ToolError(f"{period_label(end)}没有可比较的上一期")
            months = [anchor, end]
        candidates = []
        for row in store.customers.values():
            if base.region_id and row["region_id"] != base.region_id:
                continue
            if base.tier and row["customer_tier"] != base.tier:
                continue
            if base.customer_id and row["customer_id"] != base.customer_id:
                continue
            scope = base.narrow(customer_id=row["customer_id"])
            if order in {"value", "low"}:
                value = _metric_value(store, keys[0], months, scope)
                if value is None:
                    continue
                candidates.append((value, row["customer_id"], row, scope))
            else:
                opened = _metric_value(store, keys[0], [anchor], scope)
                closed = _metric_value(store, keys[0], [end], scope)
                if opened is None or closed is None or (opened == 0 and closed == 0):
                    continue
                candidates.append((closed - opened, row["customer_id"], row, scope))
        if order == "low":
            candidates.sort(key=lambda item: (item[0], item[1]))
        elif order == "drop":
            candidates.sort(key=lambda item: (item[0], item[1]))
        else:
            candidates.sort(key=lambda item: (-item[0], item[1]))
        groups = [(f"{row['customer_name']}（{REGION_LABEL[row['region_id']]}·{row['customer_tier']}级）", scope) for _, _, row, scope in candidates[:top_n]]
        customer_names = {label: row["customer_name"] for (_, _, row, _), (label, _) in zip(candidates[:top_n], groups)}
        metric_name = METRICS[keys[0]][0]
        if order == "value" and len(months) == 1:
            notes.append(f"按代理商分组只看 {period_label(end)}，按{metric_name}从高到低取前 {len(groups)} 家（共 {len(candidates)} 家有数）。表里只有这 {len(groups)} 家。")
        elif order == "low" and len(months) == 1:
            notes.append(
                f"按代理商分组只看 {period_label(end)}，按{metric_name}从低到高取前 {len(groups)} 家"
                f"（最小在前，共 {len(candidates)} 家有数）。这是该月数值最小的代理商，可以直接引用。"
            )
        elif order == "low":
            notes.append(
                f"按代理商分组看 {period_label(months[0])}至{period_label(months[-1])}的区间合计，"
                f"按{metric_name}从低到高取前 {len(groups)} 家（共 {len(candidates)} 家有数）。表里只有这 {len(groups)} 家。"
            )
        elif order == "value":
            notes.append(
                f"按代理商分组看 {period_label(months[0])}至{period_label(months[-1])}的区间合计，"
                f"按{metric_name}从高到低取前 {len(groups)} 家（共 {len(candidates)} 家有数）。"
                "金额是各月相加，比率按合计重算，不是结束月单月的数。"
            )
        else:
            direction = "从小到大（负得越多，下滑越大）" if order == "drop" else "从大到小（增得越多，增长越大）"
            notes.append(
                f"已比较{period_label(anchor)}与{period_label(end)}，按{metric_name}变动{direction}取前 {len(groups)} 家"
                f"（共 {len(candidates)} 家可比较）。变动 = 期末减期初；金额变动的单位是元，环比是相对变化，不决定这个排名。"
            )
    else:
        group_by = "none"
        groups = [(title, base)]
    if "customer_names" not in locals():
        customer_names = {}
    if len(groups) * len(months) * len(keys) > MAX_CELLS:
        raise ToolError("结果太大，请缩小月份或分组")

    if group_by == "customer" and order in {"drop", "rise"}:
        window = f"{period_label(months[0])}与{period_label(months[-1])}相比"
    elif len(months) > 1:
        window = f"{period_label(months[0])}至{period_label(months[-1])}，共 {len(months)} 个月"
    else:
        window = period_label(months[0])
    matrix = {}
    for label, scope in groups:
        matrix[label] = {key: [_metric_value(store, key, [month], scope) for month in months] for key in keys}
    if len(groups) > 1 and group_by != "customer" and "customer_count" not in keys:
        missing = Decimal("-1e30")

        def sort_value(item):
            label, scope = item
            value = matrix[label][keys[0]][0] if len(months) == 1 else _metric_value(store, keys[0], months, scope)
            return missing if value is None else value

        groups.sort(key=sort_value, reverse=True)
        span = "区间合计" if len(months) > 1 else period_label(months[0])
        notes.append(f"已按{span}的{METRICS[keys[0]][0]}从高到低排序。点击表头可改按其他列排序。")
    rows = []
    if group_by == "customer" and order in {"drop", "rise"}:
        for rank, (label, _scope) in enumerate(groups, start=1):
            row = {"排名": str(rank), "范围": label, "期初": period_label(months[0]), "期末": period_label(months[-1])}
            if label in customer_names:
                row["_客户"] = customer_names[label]
            for key in keys:
                kind = METRICS[key][1]
                name = METRICS[key][0]
                opened, closed = matrix[label][key]
                row[f"期初{name}"] = _text(kind, opened)
                row[f"期末{name}"] = _text(kind, closed)
                row[f"{name}变动"] = _delta_text(kind, opened, closed)
                if kind == "money":
                    row[f"{name}环比"] = _change(kind, opened, closed)
            rows.append(row)
    elif group_by == "customer" and len(months) > 1:
        for rank, (label, scope) in enumerate(groups, start=1):
            row = {"排名": str(rank), "范围": label, "期初": period_label(months[0]), "期末": period_label(months[-1])}
            if label in customer_names:
                row["_客户"] = customer_names[label]
            for key in keys:
                kind = METRICS[key][1]
                row[f"{METRICS[key][0]}区间合计"] = _text(kind, _metric_value(store, key, months, scope))
            rows.append(row)
    else:
        for rank, (label, scope) in enumerate(groups, start=1):
            for index, month in enumerate(months):
                show_rank = len(months) == 1 and len(groups) > 1 and "customer_count" not in keys
                row = {"排名": str(rank)} if show_rank else {}
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

    count_only = keys == ["customer_count"]
    if count_only:
        notes.append("代理商数量是净销售额不为 0 的家数，不是销售金额。这个结果只展示表格。")
    if count_only and group_by in {"region", "tier"} and len(groups) > 1 and len(months) == 1:
        total = sum((matrix[label]["customer_count"][0] for label, _scope in groups), Decimal("0"))
        rows.append({"范围": "合计", "月份": period_label(months[0]), "代理商数量": f"{int(total)}家"})

    return {
        "result": {"范围": title, "窗口": window, "分组": group_by, "说明": notes, "数据": rows},
        "keys": keys,
        "months": months,
        "groups": [label for label, _ in groups],
        "matrix": matrix,
        "scope": base,
        "title": title,
        "group_by": group_by,
        "order": order,
        "table_only": True,
        "filters": [f"范围={title}", f"月份={window}", f"分组={group_by}"],
        "formulas": [METRICS[key][2] for key in keys],
    }


def _query_prov(query: dict) -> dict:
    return _prov(
        " ".join(dict.fromkeys(query.get("formulas") or [])),
        query.get("filters") or [],
        ((query.get("result") or {}).get("数据") or [])[:80],
    )


def _charts(query: dict) -> list[dict]:
    if query.get("roster"):
        return [{
            "id": "agent_roster",
            "title": "代理商名单",
            "type": "table",
            "columns": [
                {"key": "大区", "label": "大区"},
                {"key": "代理商", "label": "代理商"},
                {"key": "等级", "label": "等级"},
            ],
            "rows": query["result"]["数据"],
            "provenance": _query_prov(query),
        }]
    keys, months, groups, matrix = query["keys"], query["months"], query["groups"], query["matrix"]
    labels = [period_label(month) for month in months]
    prov = _query_prov(query)
    charts = []
    if query.get("table_only"):
        pass
    elif len(months) > 1 and len(groups) == 1:
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
    count_only = last["keys"] == ["customer_count"]
    roster = bool(last.get("roster"))
    plain = count_only or roster
    query = Query("free", "free_query", question, end, view)
    return {
        "ok": True,
        "supported": True,
        "keep_report": False,
        "question_id": "free_query",
        "question": question,
        "period": end,
        "period_label": "代理商名录" if roster else (period_label(end) if single else f"{period_label(months[0])}至{period_label(end)}"),
        "compare_period": None if plain or not single else compare,
        "compare_label": "" if plain or not (single and compare) else period_label(compare),
        "filters": {"region_id": last["scope"].region_id, "product_line": last["scope"].product_line, "tier": last["scope"].tier, "label": last["title"]},
        "view": query.view,
        "resolved_view": query.view,
        "view_switch": None,
        "page": "sheet",
        "page_title": last.get("page_title") or ("代理商名单" if roster else ("代理商数量" if count_only else question)),
        "conclusion": {"text": text, "sentences": [{"text": text, "provenance": _query_prov(last)}]} if text else None,
        "kpis": [],
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


def _count_reply(result: dict) -> str:
    rows = [row for row in result["result"]["数据"] if row.get("月份") != "区间合计"]
    window = result["result"]["窗口"]
    total = next((row for row in rows if row["范围"] == "合计"), None)
    parts = [f"{row['范围']}{row['代理商数量']}" for row in rows if row["范围"] != "合计"]
    if total:
        return f"{window}有销售数据的代理商共{total['代理商数量']}。其中{'、'.join(parts)}。"
    if len(rows) > 1:
        return f"{window}有销售数据的代理商：{'、'.join(parts)}。"
    only = rows[0]
    scope = only["范围"]
    if scope == "公司合计":
        return f"{window}有销售数据的代理商共{only['代理商数量']}。"
    return f"{window}{scope}有销售数据的代理商共{only['代理商数量']}。"


def _roster_rows(store: Store, region_id: str | None, tier: str | None) -> list[dict]:
    region_index = {rid: index for index, rid in enumerate(REGION_ORDER)}
    tier_index = {name: index for index, name in enumerate(TIER_ORDER)}
    picked = [
        row for row in store.customers.values()
        if (not region_id or row["region_id"] == region_id) and (not tier or row["customer_tier"] == tier)
    ]
    picked.sort(key=lambda row: (region_index[row["region_id"]], tier_index[row["customer_tier"]], row["customer_name"]))
    return [
        {"大区": f"{REGION_LABEL[row['region_id']]}大区", "代理商": row["customer_name"], "等级": f"{row['customer_tier']}级"}
        for row in picked
    ]


def _roster_query(store: Store, period: str, region: str | None, tier: str | None) -> dict:
    if region and region not in REGION_ID:
        raise ToolError(f"未知大区：{region}")
    if tier and tier not in TIER_ORDER:
        raise ToolError(f"未知等级：{tier}")
    region_id = REGION_ID.get(region) if region else None
    if region and tier:
        title = f"{region}大区 · {tier}级"
    elif region:
        title = f"{region}大区"
    elif tier:
        title = f"{tier}级"
    else:
        title = "各大区"
    rows = _roster_rows(store, region_id, tier)
    return {
        "result": {
            "范围": title,
            "窗口": "代理商名录",
            "分组": "customer",
            "说明": ["只取客户维度：大区、代理商名称、等级。不含销售额、回款和排名。"],
            "数据": rows,
        },
        "keys": [],
        "months": [period],
        "groups": [],
        "matrix": {},
        "scope": Scope(region_id=region_id, tier=tier or None),
        "title": title,
        "group_by": "customer",
        "order": "value",
        "table_only": True,
        "roster": True,
        "filters": [f"范围={title}", "来源=客户维度"],
        "formulas": ["客户维度：大区、代理商、等级"],
    }


def _roster_reply(result: dict) -> str:
    rows = result["result"]["数据"]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["大区"]] = counts.get(row["大区"], 0) + 1
    if result["title"] == "各大区":
        parts = "、".join(f"{name}{count}家" for name, count in counts.items())
        return f"各大区代理商共{len(rows)}家。{parts}。"
    return f"{result['title']}代理商共{len(rows)}家。"


def try_agent_list(store: Store, question: str, period: str, view: str) -> dict | None:
    """A roster reads the customer dimension only: region, name, tier."""
    raw = question.strip()
    if not re.search(r"列表|名单|有哪些代理|哪些公司|有哪些公司|代理商有哪些|有哪几家", raw):
        return None
    if re.search(r"销售|回款|出库|折扣|下滑|下降|增长|上升|最高|最低|最好|最差|排名|排行|拖累|欠款|逾期|账龄|DSO|dso|对比|环比|多少|贡献|原因", raw, re.I):
        return None
    chosen = resolve_period(raw, period)
    regions = [name for name, _rid in REGION_NAMES if name in raw]
    tiers = re.findall(r"([ABC])级", raw)
    if len(regions) > 1 or len(tiers) > 1:
        return None
    result = _roster_query(store, chosen, regions[0] if regions else None, tiers[0] if tiers else None)
    return _free_report(store, raw, chosen, view, _roster_reply(result), [result])


def try_region_agent_sales(store: Store, question: str, period: str, view: str) -> dict | None:
    """Every agent's sales under each region. Not a national top-N ranking."""
    raw = question.strip()
    if not re.search(r"各代理商|每个代理商|各个代理商|每家代理商|所有代理商|全部代理商", raw):
        return None
    if not re.search(r"销售|卖了|经营数据|销售情况|销售数据", raw):
        return None
    if re.search(r"最高|最低|最好|最差|排名|排行|下滑|下降|增长|上升|TOP|top|前\s*\d+", raw, re.I):
        return None
    chosen = resolve_period(raw, period)
    regions = [name for name, _rid in REGION_NAMES if name in raw]
    if len(regions) > 1:
        return None
    region_id = REGION_ID[regions[0]] if len(regions) == 1 else None
    region_index = {rid: index for index, rid in enumerate(REGION_ORDER)}
    picked = []
    for row in store.customers.values():
        if region_id and row["region_id"] != region_id:
            continue
        scope = Scope(customer_id=row["customer_id"])
        net = net_sales(store, chosen, scope)
        if net == 0:
            continue
        picked.append((row, net, list_amount(store, chosen, scope), collection_amount(store, chosen, scope), collection_rate(store, chosen, scope)))
    picked.sort(key=lambda item: (region_index[item[0]["region_id"]], -item[1], item[0]["customer_name"]))
    rows = [
        {
            "大区": f"{REGION_LABEL[row['region_id']]}大区",
            "代理商": row["customer_name"],
            "净销售额": money_text(net),
            "出库额": money_text(gross),
            "回款额": money_text(collected),
            "销售回款率": pct_text(rate),
        }
        for row, net, gross, collected, rate in picked
    ]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["大区"]] = counts.get(row["大区"], 0) + 1
    parts = "、".join(f"{name}{count}家" for name, count in counts.items())
    scope_name = f"{regions[0]}大区" if regions else "五大区"
    reply = f"{period_label(chosen)}{scope_name}{len(rows)}家代理商都有销售。{parts}。下表按大区列出每一家的净销售额、出库额、回款额和销售回款率。"
    title = f"{regions[0]}大区" if regions else "各大区"
    result = {
        "result": {
            "范围": title,
            "窗口": period_label(chosen),
            "分组": "customer",
            "说明": ["按大区列出该月全部有销售的代理商，不是全国前 N 名。"],
            "数据": rows,
        },
        "keys": ["net_sales", "list_amount", "collection_amount", "collection_rate"],
        "months": [chosen],
        "groups": [],
        "matrix": {},
        "scope": Scope(region_id=region_id),
        "title": title,
        "group_by": "none",
        "order": "value",
        "table_only": True,
        "page_title": f"{period_label(chosen)}各代理商销售",
        "filters": [f"范围={title}", f"月份={period_label(chosen)}"],
        "formulas": [F_NET, F_SHIP, F_COL, F_RATE],
    }
    return _free_report(store, raw, chosen, view, reply, [result])



def try_agent_extreme(store: Store, question: str, period: str, view: str) -> dict | None:
    """「最差/最好的 N 家」returns exactly N rows, low or high, as a table."""
    raw = question.strip()
    count = re.search(r"(\d+)\s*家", raw)
    if count is None or "代理商" not in raw:
        return None
    low = bool(re.search(r"最差|最低|最少|最小", raw))
    high = bool(re.search(r"最好|最高|最大", raw))
    if low == high or re.search(r"下滑|下降|跌|增长|上升|拖累", raw):
        return None
    number = int(count.group(1))
    if not 1 <= number <= 20:
        return None
    chosen = resolve_period(raw, period)
    metric = "net_sales"
    if "回款率" in raw:
        metric = "collection_rate"
    elif "回款" in raw and "销售" not in raw:
        metric = "collection_amount"
    result = run_query(
        store,
        {"metrics": [metric], "group_by": "customer", "order": "low" if low else "value", "top_n": number, "start_month": chosen, "end_month": chosen},
        chosen,
    )
    rows = result["result"]["数据"]
    label = METRICS[metric][0]
    direction = "最低" if low else "最高"
    result["page_title"] = f"{period_label(chosen)}{label}{direction}的{len(rows)}家"
    reply = f"{period_label(chosen)}{label}{direction}的{len(rows)}家见下表。"
    return _free_report(store, raw, chosen, view, reply, [result])


def try_agent_count(store: Store, question: str, period: str, view: str) -> dict | None:
    """Headcount questions have no chart. The page is the count table and one sentence."""
    raw = question.strip()
    if not re.search(r"多少家|有几家|几家代理|多少个代理|代理商数量|有多少代理|多少代理商", raw):
        return None
    if re.search(r"销售|回款|出库|折扣|下滑|下降|增长|上升|最高|最低|最好|最差|排名|排行|拖累|欠款|逾期|账龄|DSO|dso|对比|环比", raw, re.I):
        return None
    chosen = resolve_period(raw, period)
    regions = [name for name, _rid in REGION_NAMES if name in raw]
    tiers = re.findall(r"([ABC])级", raw)
    lines = [line for line in LINE_ORDER if line in raw]
    by_region = bool(re.search(r"各大区|每个大区|各区|分大区|按大区", raw))
    by_tier = bool(re.search(r"各等级|每个等级|按等级|各级", raw)) or len(tiers) > 1
    by_line = bool(re.search(r"各产品线|每个产品线|按产品线|每条产品线", raw)) or len(lines) > 1
    args: dict = {"metrics": ["customer_count"], "start_month": chosen, "end_month": chosen}
    if len(regions) == 1:
        args["region"] = regions[0]
    if len(tiers) == 1 and not by_tier:
        args["tier"] = tiers[0]
    if len(lines) == 1 and not by_line:
        args["product_line"] = lines[0]
    if by_tier:
        args["group_by"] = "tier"
    elif by_line:
        args["group_by"] = "product_line"
    elif by_region or (not regions and not lines):
        args["group_by"] = "region"
    result = run_query(store, args, chosen)
    return _free_report(store, raw, chosen, view, _count_reply(result), [result])


def answer(store: Store, question: str, period: str, view: str = "auto", history: list[dict] | None = None) -> dict:
    text = question.strip()
    drilled = try_customer_sku(store, text, period, view)
    if drilled is not None:
        return drilled
    listed = try_agent_list(store, text, period, view)
    if listed is not None:
        return listed
    by_region = try_region_agent_sales(store, text, period, view)
    if by_region is not None:
        return by_region
    extreme = try_agent_extreme(store, text, period, view)
    if extreme is not None:
        return extreme
    counted = try_agent_count(store, text, period, view)
    if counted is not None:
        return counted
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
                elif name == "list_customers":
                    try:
                        result = _roster_query(store, chosen, args.get("region") or None, args.get("tier") or None)
                        queries.append(result)
                        content = json.dumps(result["result"], ensure_ascii=False)
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
    sheet = json.dumps([item["result"] for item in queries], ensure_ascii=False)
    if not text or unknown_numbers(text, sheet):
        text = f"已在左侧列出计算结果（{queries[-1]['title']} · {queries[-1]['result']['窗口']}）。模型的文字说明引用了计算结果里没有的数字，已不展示。" if text else \
            f"已在左侧列出计算结果（{queries[-1]['title']} · {queries[-1]['result']['窗口']}）。"
    return _free_report(store, question, period, view, text, queries)
