"""Map relations from the data-map contract. Every number is computed, never estimated.

「冲量 / 延期」is not produced here. These tables are the calculated facts a later
explanation may be checked against.
"""

from __future__ import annotations

from decimal import Decimal

from app.constants import DATA_START, SHARE_MIN
from app.metrics import (
    Scope,
    aging_migration,
    apply_member,
    ar_bridge,
    choose_default_view,
    collection_amount,
    contributions,
    customer_contribs,
    decompose,
    dimension_members,
    month_timing,
    net_sales,
    rate_slices,
    select_fastest,
    shift_month,
)
from app.store import Store
from app.textfmt import money_text, pct_text, pp_text, slice_title

OPERATIONS = (
    "rate_decompose",
    "cross_contribute",
    "agent_contribute",
    "aging_migration",
    "ar_bridge",
    "month_timing",
    "fastest_decline",
)

OPERATION_NAME = {
    "rate_decompose": "率变分解",
    "cross_contribute": "交叉贡献",
    "agent_contribute": "代理商贡献",
    "aging_migration": "账龄迁移",
    "ar_bridge": "应收月环比拆解",
    "month_timing": "月初月末出货对照",
    "fastest_decline": "下降最快",
}

FORMULA = {
    "rate_decompose": (
        "回款额变化贡献 =（本期回款额 − 上期回款额）÷ 上期净销售额。"
        "净销售额变化贡献 = 本期回款额 ÷ 本期净销售额 − 本期回款额 ÷ 上期净销售额。"
        "两项相加 = 销售回款率环比。展示到 0.01pp 时可能有四舍五入差，未舍入时恒等。"
    ),
    "cross_contribute": (
        "子切片贡献 = 子切片本期回款额 ÷ 父级本期净销售额 − 子切片上期回款额 ÷ 父级上期净销售额。"
        "贡献额 = 贡献 × 父级上期净销售额。各子切片贡献相加 = 父级销售回款率环比。"
        "子维必须是父切片尚未固定的维度。"
    ),
    "agent_contribute": (
        "代理商贡献 = 该代理商本期回款额 ÷ 父级本期净销售额 − 该代理商上期回款额 ÷ 父级上期净销售额。"
        "贡献额 = 贡献 × 父级上期净销售额。拖累按贡献从低到高，贡献最大按从高到低。"
    ),
    "aging_migration": "账龄迁移只比较本月末与上月末快照的金额和占比。账龄不用流水重算。",
    "ar_bridge": (
        "应收余额变动 = 本期新增应收 − 本期回款额 + 其他。"
        "本期新增应收 = 本期净销售额（一笔出库一张发票）。"
        "其他 = 期末应收 − 期初应收 − 新增应收 + 回款额，只作为计算残差，不能解释成未建口径。"
    ),
    "month_timing": "月初 7 日、月末 7 日净销售占当月净销售之比。该月没有出库则本数据无法支持。",
    "fastest_decline": (
        "先按大区视角和产品视角分别计算销售回款率的百分点变化。"
        "净销售占比 = 切片本期净销售额 ÷ 公司本期净销售额。占比低于 5% 的切片不参与下降最快评选，直接查该切片仍然出数。"
        "默认展开下降更快的视角，并列时展开大区视角。"
    ),
}


class RelationError(ValueError):
    pass


def _compare(ym: str) -> str | None:
    previous = shift_month(ym, -1)
    if previous < DATA_START:
        return None
    return previous


def _unsupported_row(label: str) -> list[dict]:
    return [{"项目": label, "结果": "本数据无法支持"}]


def _fixed(scope: Scope) -> set[str]:
    fixed = set()
    if scope.region_id:
        fixed.add("region")
    if scope.product_line or scope.product_category:
        fixed.add("product_line")
    if scope.product_category:
        fixed.add("product")
    if scope.tier:
        fixed.add("tier")
    if scope.cohort:
        fixed.add("cohort")
    if scope.customer_id:
        fixed.add("customer")
    return fixed


def _rate_row(parts: dict | None) -> dict:
    if parts is None:
        return {
            "上期销售回款率": "本数据无法支持",
            "本期销售回款率": "本数据无法支持",
            "销售回款率环比": "本数据无法支持",
            "回款额变化贡献": "本数据无法支持",
            "净销售额变化贡献": "本数据无法支持",
        }
    return {
        "上期销售回款率": pct_text(parts["rate0"]),
        "本期销售回款率": pct_text(parts["rate1"]),
        "销售回款率环比": pp_text(parts["delta"]),
        "回款额变化贡献": pp_text(parts["collection_effect"]),
        "净销售额变化贡献": pp_text(parts["sales_effect"]),
    }


def _decompose(store: Store, ym: str, scope: Scope) -> tuple[list[dict], list[str]]:
    previous = _compare(ym)
    if previous is None:
        return _unsupported_row("率变分解"), ["上期不在经营月内，本数据无法支持。"]
    parts = decompose(
        collection_amount(store, previous, scope),
        net_sales(store, previous, scope),
        collection_amount(store, ym, scope),
        net_sales(store, ym, scope),
    )
    if parts is None:
        return _unsupported_row("率变分解"), ["上期或本期净销售额为 0，本数据无法支持。"]
    return [_rate_row(parts)], []


def _children(store: Store, parent: Scope, dimension: str) -> list[tuple[str, str, Scope]]:
    if dimension == "product":
        seen: list[tuple[str, str, Scope]] = []
        used = set()
        for product in store.products.values():
            if parent.product_line and product["product_line"] != parent.product_line:
                continue
            if parent.product_category and product["product_category"] != parent.product_category:
                continue
            key = product["product_category"]
            if key in used:
                continue
            used.add(key)
            seen.append((key, key, parent.narrow(product_line=product["product_line"], product_category=key)))
        seen.sort(key=lambda item: item[0])
        return seen
    if dimension == "customer":
        rows = []
        for customer in store.customers.values():
            if parent.region_id and customer["region_id"] != parent.region_id:
                continue
            if parent.tier and customer["customer_tier"] != parent.tier:
                continue
            if parent.customer_id and customer["customer_id"] != parent.customer_id:
                continue
            rows.append((customer["customer_id"], customer["customer_name"], parent.narrow(customer_id=customer["customer_id"])))
        rows.sort(key=lambda item: item[1])
        return rows
    return [(key, name, apply_member(parent, dimension, key)) for key, name in dimension_members(dimension)]


def _cross(store: Store, ym: str, scope: Scope, dimension: str | None) -> tuple[list[dict], list[str]]:
    if dimension not in {"region", "product_line", "tier", "cohort", "product", "customer"}:
        raise RelationError("交叉贡献要指定子维：大区、产品线、产品类、等级、新老或代理商。")
    if dimension in _fixed(scope):
        raise RelationError(f"非法组合：父切片已经固定了{dimension}，不能再按这个维度拆贡献。")
    previous = _compare(ym)
    if previous is None:
        return _unsupported_row("交叉贡献"), ["上期不在经营月内，本数据无法支持。"]
    if dimension in {"region", "product_line", "tier", "cohort"}:
        source = contributions(store, ym, previous, scope, dimension)
        rows = [
            {
                "子切片": row["name"],
                "贡献": pp_text(row["contrib"]),
                "贡献额": "本数据无法支持" if row["contrib_amount"] is None else money_text(row["contrib_amount"]),
                "本期净销售额": money_text(row["net"]),
                "上期净销售额": money_text(row["prev_net"]),
                "本期回款额": money_text(row["collection"]),
                "上期回款额": money_text(row["prev_collection"]),
            }
            for row in source
        ]
        return rows, []
    net1 = net_sales(store, ym, scope)
    net0 = net_sales(store, previous, scope)
    rows = []
    for _key, name, child in _children(store, scope, dimension):
        current = collection_amount(store, ym, child)
        before = collection_amount(store, previous, child)
        if net0 == 0 or net1 == 0:
            contrib = None
            amount = None
        else:
            contrib = current / net1 - before / net0
            amount = contrib * net0
        rows.append(
            {
                "子切片": name,
                "贡献": pp_text(contrib),
                "贡献额": "本数据无法支持" if amount is None else money_text(amount),
                "本期净销售额": money_text(net_sales(store, ym, child)),
                "上期净销售额": money_text(net_sales(store, previous, child)),
                "本期回款额": money_text(current),
                "上期回款额": money_text(before),
            }
        )
    notes = []
    if net0 == 0 or net1 == 0:
        notes.append("上期或本期父级净销售额为 0，本数据无法支持。")
    return rows or _unsupported_row("交叉贡献"), notes


def _agents(store: Store, ym: str, scope: Scope, args: dict) -> tuple[list[dict], list[str]]:
    previous = _compare(ym)
    if previous is None:
        return _unsupported_row("代理商贡献"), ["上期不在经营月内，本数据无法支持。"]
    rows = customer_contribs(store, ym, previous, scope)
    rank = args.get("rank") or "drag"
    if rank not in {"drag", "top"}:
        raise RelationError("代理商贡献的 rank 只能是 drag 或 top。")
    if rank == "top":
        known = [row for row in rows if row["contrib"] is not None]
        missing = [row for row in rows if row["contrib"] is None]
        known.sort(key=lambda row: (-row["contrib"], row["customer_id"]))
        rows = known + missing
    try:
        limit = int(args.get("top_n") or 10)
    except (TypeError, ValueError):
        raise RelationError("top_n 要是 1 到 20 的整数。") from None
    limit = max(1, min(limit, 20))
    picked = rows[:limit]
    if not picked:
        return _unsupported_row("代理商贡献"), ["这个切片没有可计算的代理商贡献。"]
    table = []
    for index, row in enumerate(picked, start=1):
        table.append(
            {
                "排名": str(index),
                "代理商": row["customer_name"],
                "等级": f"{row['tier']}级",
                "大区": row["region_name"],
                "贡献": pp_text(row["contrib"]),
                "贡献额": "本数据无法支持" if row["contrib_amount"] is None else money_text(row["contrib_amount"]),
                "本期净销售额": money_text(row["net"]),
                "上期净销售额": money_text(row["prev_net"]),
                "本期回款额": money_text(row["collection"]),
                "上期回款额": money_text(row["prev_collection"]),
            }
        )
    note = "按贡献从低到高，负得越多越靠前。" if rank == "drag" else "按贡献从高到低。"
    if net_sales(store, ym, scope) == 0 or net_sales(store, previous, scope) == 0:
        note += "父级净销售额有一端为 0，贡献本数据无法支持。"
    return table, [note]


def _aging(store: Store, ym: str, scope: Scope) -> tuple[list[dict], list[str]]:
    previous = _compare(ym)
    if previous is None:
        return _unsupported_row("账龄迁移"), ["上月末没有快照，本数据无法支持。"]
    rows = []
    for row in aging_migration(store, ym, previous, scope):
        rows.append(
            {
                "账龄": row["bucket"],
                "上期金额": money_text(row["prev_amount"]),
                "本期金额": money_text(row["amount"]),
                "金额变动": money_text(row["amount_change"]),
                "上期占比": pct_text(row["prev_share"]),
                "本期占比": pct_text(row["share"]),
                "占比变动": pp_text(row["share_delta"]),
            }
        )
    return rows, []


def _bridge(store: Store, ym: str, scope: Scope) -> tuple[list[dict], list[str]]:
    previous = _compare(ym)
    if previous is None:
        return _unsupported_row("应收月环比拆解"), ["没有上月末快照，本数据无法支持。"]
    bridge = ar_bridge(store, ym, previous, scope)
    rows = [
        {"项目": "上期末应收", "金额": money_text(bridge["start_ar"])},
        {"项目": "本期新增应收", "金额": money_text(bridge["added"])},
        {"项目": "本期回款", "金额": money_text(bridge["collected"])},
        {"项目": "其他（计算残差）", "金额": money_text(bridge["other"])},
        {"项目": "本期末应收", "金额": money_text(bridge["end_ar"])},
        {"项目": "变动", "金额": money_text(bridge["change"])},
    ]
    notes = []
    if bridge["other"] != 0:
        notes.append("其他是计算残差，不能解释成未建口径。")
    return rows, notes


def _timing(store: Store, ym: str, scope: Scope) -> tuple[list[dict], list[str]]:
    timing = month_timing(store, ym, scope)
    if timing is None:
        return _unsupported_row("月初月末出货对照"), ["该月没有出库，本数据无法支持。"]
    return [
        {
            "月初7日净销售占比": pct_text(timing["early_share"]),
            "月末7日净销售占比": pct_text(timing["late_share"]),
            "当月净销售": money_text(timing["net"]),
        }
    ], []


def _fastest(store: Store, ym: str, scope: Scope) -> tuple[list[dict], list[str]]:
    previous = _compare(ym)
    if previous is None:
        return _unsupported_row("下降最快"), ["上期不在经营月内，本数据无法支持。"]
    views: list[tuple[str, str, list[dict]]] = []
    if not scope.region_id:
        views.append(("大区视角", "region", rate_slices(store, ym, previous, "region", scope)))
    if not scope.product_line and not scope.product_category:
        views.append(("产品视角", "product_line", rate_slices(store, ym, previous, "product_line", scope)))
    if not views:
        own_rate_1 = net_sales(store, ym, scope)
        own_rate_0 = net_sales(store, previous, scope)
        company = net_sales(store, ym, Scope())
        parts = decompose(
            collection_amount(store, previous, scope),
            own_rate_0,
            collection_amount(store, ym, scope),
            own_rate_1,
        )
        share = (own_rate_1 / company) if company else Decimal(0)
        eligible = parts is not None and share >= SHARE_MIN
        row = _rate_row(parts)
        row.update(
            {
                "视角": "该切片",
                "切片": "父切片",
                "净销售占比": pct_text(share) if company else "本数据无法支持",
                "参评": "是" if eligible else "否",
                "下降最快": "是" if eligible else "否",
            }
        )
        note = "父切片的大区视角和产品视角都已固定。"
        if not eligible:
            note += "没有达到 5% 参评线，本数据无法支持评选；直接查该切片仍然可以出数。"
        return [row], [note]

    winners = {}
    for label, _dimension, slices in views:
        best = select_fastest(slices)
        winners[label] = None if best is None else best["key"]
    expanded = views[0][0]
    if len(views) == 2:
        chosen, _primary, _other = choose_default_view(views[0][2], views[1][2])
        expanded = "大区视角" if chosen == "region" else "产品视角"
    table = []
    for label, dimension, slices in views:
        for row in slices:
            table.append(
                {
                    "视角": label,
                    "切片": slice_title(dimension, row["name"]),
                    "上期销售回款率": pct_text(row["prev_rate"]),
                    "本期销售回款率": pct_text(row["rate"]),
                    "销售回款率环比": pp_text(row["delta"]),
                    "净销售占比": pct_text(row["share"]),
                    "参评": "是" if row["eligible"] else "否",
                    "下降最快": "是" if winners[label] == row["key"] else "否",
                }
            )
    notes = [f"默认展开{expanded}。5% 门槛只用于本次下降最快是否参评，不限制直接查某个切片。"]
    if all(winner is None for winner in winners.values()):
        notes.append("没有净销售占比达到 5% 且能计算环比的切片，本数据无法支持评选。")
    return table, notes


def build_relation(store: Store, operation: str, scope: Scope, ym: str, args: dict) -> dict:
    if operation not in OPERATIONS:
        raise RelationError(f"未知运算：{operation}")
    if operation == "rate_decompose":
        rows, notes = _decompose(store, ym, scope)
    elif operation == "cross_contribute":
        rows, notes = _cross(store, ym, scope, args.get("dimension"))
    elif operation == "agent_contribute":
        rows, notes = _agents(store, ym, scope, args)
    elif operation == "aging_migration":
        rows, notes = _aging(store, ym, scope)
    elif operation == "ar_bridge":
        rows, notes = _bridge(store, ym, scope)
    elif operation == "month_timing":
        rows, notes = _timing(store, ym, scope)
    else:
        rows, notes = _fastest(store, ym, scope)
    return {"rows": rows, "notes": notes, "formula": FORMULA[operation], "op_name": OPERATION_NAME[operation]}
