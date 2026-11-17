"""Build report payloads from the metrics engine. No estimated numbers."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from app.constants import (
    BUCKETS,
    BUCKET_UNDUE,
    DATA_START,
    DISCOUNT_TAG_PP,
    LARGE_INVOICE,
    MAJOR_SHARE,
    TAG_AGENT,
    TAG_AGING,
    TAG_DISCOUNT,
    TAG_REGION,
    TAG_VOLUME,
)
from app.metrics import (
    Scope,
    aging_migration,
    apply_member,
    ar_balance,
    ar_bridge,
    avg_collection_days,
    collection_amount,
    contributions,
    customer_contribs,
    decompose,
    dimension_members,
    discount_rate,
    dso,
    due_collection_rate,
    filter_lines,
    large_due_invoices,
    list_amount as gross_amount,
    month_timing,
    net_sales,
    overdue_balance,
    overdue_rate,
    rate_slices,
    select_fastest,
    shift_month,
    top_ar_customers,
    weighted_age_days,
)
from app.narrative import write_narrative
from app.router import Query, classify
from app.store import Store
from app.textfmt import (
    days_num,
    days_signed,
    days_text,
    money_num,
    money_text,
    pct_num,
    pct_signed,
    pct_text,
    period_label,
    pp_num,
    pp_text,
    slice_title,
    tone_of,
)

F_RATE = "销售回款率 = 统计期内实际回款额 ÷ 统计期内净销售额。环比 = 本期销售回款率 − 上期销售回款率，单位 pp。"
F_DECOMP = (
    "回款额变化贡献 =（本期回款额 − 上期回款额）÷ 上期净销售额。"
    "净销售额变化贡献 = 本期回款额 ÷ 本期净销售额 − 本期回款额 ÷ 上期净销售额。"
    "两项相加 = 销售回款率环比。展示到 0.01pp 时可能有四舍五入差，未舍入时恒等。"
)
F_CONTRIB = (
    "子切片贡献 = 子切片本期回款额 ÷ 父级本期净销售额 − 子切片上期回款额 ÷ 父级上期净销售额。"
    "贡献额 = 贡献 × 父级上期净销售额。各子切片贡献相加 = 父级销售回款率环比。"
)
F_DUE = "到期回款率 = 统计期内实际回款额 ÷ 统计期内到期应收额。到期应收额 = 到期日落在统计期内的发票原净额（含已回款）。辅助口径，不作主结论。"
F_OVERDUE = "逾期率 = 逾期余额 ÷ 应收余额。逾期余额 = 月末快照里账龄不是「未到期」的未清金额。余额只取月末快照。"
F_DSO = "DSO = 期末应收余额 ÷（近 3 个自然月净销售额合计 ÷ 这 3 个月的日历天数）。不足 3 个完整月时本数据无法支持。"
F_DISCOUNT = "折扣率 = 商务折扣额 ÷ 码洋。码洋即出库额（list_amount）。净销售额 = 码洋 − 折扣额。"
F_NET = "净销售额 = 统计期内对代理商的销售净额（码洋 − 商务折扣），按出库日归属。销售对象全部是代理商。"
F_COL = "回款额 = 统计期内到账金额，按回款日归属，并挂到对应发票。"
F_SHIP = "出库额 = 统计期内码洋（list_amount）合计，按出库日归属。"
F_AR = "应收余额 = 该月末快照的未清金额合计。账龄只读快照，不用流水重算。"
F_BRIDGE = "应收余额变动 = 本期新增应收 − 本期回款额 + 其他。本期新增应收 = 本期净销售额（一笔出库一张发票）。其他 = 期末应收 − 期初应收 − 新增应收 + 回款额。"
F_NEW = "新代理商 = 首次出库日落在统计月往前 12 个自然月的窗口内（含窗口起始月 1 日）。老代理商 = 首次出库早于该窗口。尚未出库的不归入任何一组。"
F_AGE_DAYS = "加权平均账龄（天）= Σ 未清金额 ×（月末 − 发票日）的天数 ÷ Σ 未清金额。只用来描述快照，不替代 DSO。"
F_COLLECT_DAYS = "平均回款天数 = 近 6 个月已回款金额加权的（回款日 − 出库日）。未回款发票不进入这个平均。"
F_GATE = "净销售占比 = 切片本期净销售额 ÷ 公司本期净销售额。占比低于 5% 的切片不参与「下降最快」评选，仍可在追问里查看。"


def scope_title(store: Store, scope: Scope) -> str:
    parts = []
    if scope.region_id:
        parts.append(f"{store.region_name[scope.region_id]}大区")
    if scope.product_line:
        parts.append(f"{scope.product_line}产品线")
    if scope.tier:
        parts.append(f"{scope.tier}级代理商")
    if scope.cohort == "new":
        parts.append("新代理商")
    elif scope.cohort == "old":
        parts.append("老代理商")
    if scope.customer_id:
        parts.append(store.customers[scope.customer_id]["customer_name"])
    return "、".join(parts) if parts else "公司合计"


def _prov(formula: str, filters: list[str], rows: list[dict], notes: list[str] | None = None) -> dict:
    return {"formula": formula, "filters": filters, "notes": notes or [], "rows": rows}


def _filters(store: Store, ym: str, compare: str | None, scope: Scope) -> list[str]:
    return filter_lines(store, ym, compare, scope)


def _mom_amount(cur: Decimal, prev: Decimal) -> Decimal | None:
    if prev == 0:
        return None
    return (cur - prev) / prev


def _kpi(label, value_text, mom_text, tone, provenance, auxiliary=False, hint=""):
    return {
        "label": label,
        "value_text": value_text,
        "mom_text": mom_text,
        "tone": tone,
        "auxiliary": auxiliary,
        "hint": hint,
        "provenance": provenance,
    }


def build_kpis(store: Store, ym: str, compare: str, scope: Scope) -> list[dict]:
    filters = _filters(store, ym, compare, scope)
    net1, net0 = net_sales(store, ym, scope), net_sales(store, compare, scope)
    col1, col0 = collection_amount(store, ym, scope), collection_amount(store, compare, scope)
    gross1, gross0 = gross_amount(store, ym, scope), gross_amount(store, compare, scope)
    rate1 = None if net1 == 0 else col1 / net1
    rate0 = None if net0 == 0 else col0 / net0
    ar1, ar0 = ar_balance(store, ym, scope), ar_balance(store, compare, scope)
    od1 = overdue_rate(store, ym, scope)
    od0 = overdue_rate(store, compare, scope)
    dso1, dso0 = dso(store, ym, scope), dso(store, compare, scope)
    due1, due0 = due_collection_rate(store, ym, scope), due_collection_rate(store, compare, scope)
    rate_delta = None if rate1 is None or rate0 is None else rate1 - rate0
    od_delta = None if od1 is None or od0 is None else od1 - od0
    due_delta = None if due1 is None or due0 is None else due1 - due0
    dso_delta = None if dso1 is None or dso0 is None else dso1 - dso0

    def money_pair(label, cur, prev, formula, better):
        delta = _mom_amount(cur, prev)
        return _kpi(
            label,
            money_text(cur),
            "—" if delta is None else pct_signed(delta),
            tone_of(delta, better),
            _prov(formula, filters, [
                {"项目": label, "本期": money_text(cur), "上期": money_text(prev), "环比": "—" if delta is None else pct_signed(delta)},
            ]),
        )

    return [
        money_pair("回款额", col1, col0, F_COL, "up"),
        _kpi(
            "销售回款率",
            pct_text(rate1),
            pp_text(rate_delta),
            tone_of(rate_delta, "up"),
            _prov(F_RATE, filters, [
                {"项目": "销售回款率", "本期回款额": money_text(col1), "本期净销售额": money_text(net1), "本期": pct_text(rate1), "上期": pct_text(rate0), "环比": pp_text(rate_delta)},
            ]),
        ),
        money_pair("净销售额", net1, net0, F_NET, "up"),
        money_pair("出库额", gross1, gross0, F_SHIP, "up"),
        money_pair("应收余额", ar1, ar0, F_AR, "down"),
        _kpi(
            "逾期率",
            pct_text(od1),
            pp_text(od_delta),
            tone_of(od_delta, "down"),
            _prov(F_OVERDUE, filters, [
                {"项目": "逾期率", "本期逾期余额": money_text(overdue_balance(store, ym, scope)), "本期应收余额": money_text(ar1), "本期": pct_text(od1), "上期": pct_text(od0), "环比": pp_text(od_delta)},
            ]),
        ),
        _kpi(
            "DSO",
            days_text(dso1),
            days_signed(dso_delta),
            tone_of(dso_delta, "down"),
            _prov(F_DSO, filters, [{"项目": "DSO", "本期": days_text(dso1), "上期": days_text(dso0), "变化": days_signed(dso_delta)}]),
            hint="期末应收 / 近3个月日均净销售",
        ),
        _kpi(
            "到期回款率",
            pct_text(due1),
            pp_text(due_delta),
            tone_of(due_delta, "up"),
            _prov(F_DUE, filters, [{"项目": "到期回款率", "本期": pct_text(due1), "上期": pct_text(due0), "环比": pp_text(due_delta)}]),
            auxiliary=True,
            hint="辅助，不作主结论",
        ),
    ]


def _rate_rows(slices: list[dict]) -> list[dict]:
    rows = []
    for row in slices:
        rows.append({
            "切片": row["name"],
            "本期净销售额": money_text(row["net"]),
            "上期净销售额": money_text(row["prev_net"]),
            "本期回款额": money_text(row["collection"]),
            "上期回款额": money_text(row["prev_collection"]),
            "本期销售回款率": pct_text(row["rate"]),
            "上期销售回款率": pct_text(row["prev_rate"]),
            "环比": pp_text(row["delta"]),
            "净销售占比": pct_text(row["share"]),
            "参评": "是" if row["eligible"] else "否（占比<5%）",
        })
    return rows


def rate_chart(slices, title, chart_id, ym, compare, store, scope, drill) -> dict:
    best = select_fastest(slices)
    return {
        "id": chart_id,
        "title": title,
        "type": "grouped_bar",
        "y_format": "pct",
        "drill": drill,
        "highlight": [] if best is None else [best["name"]],
        "muted": [row["name"] for row in slices if not row["eligible"]],
        "categories": [row["name"] for row in slices],
        "series": [
            {"name": period_label(compare), "values": [pct_num(row["prev_rate"]) for row in slices]},
            {"name": period_label(ym), "values": [pct_num(row["rate"]) for row in slices]},
        ],
        "footnote": "灰色切片净销售占比低于 5%，不参与下降最快评选。" if any(not row["eligible"] for row in slices) else "",
        "provenance": _prov(F_RATE + F_GATE, _filters(store, ym, compare, scope), _rate_rows(slices), [F_GATE]),
    }


def _signals(parts, net0, net1, agents, aging, disc0, disc1, cross_rows, cross_dimension) -> list[str]:
    tags = []
    delta = parts["delta"]
    if (
        delta < 0
        and parts["sales_effect"] < 0
        and net1 > net0
        and abs(parts["sales_effect"]) >= abs(delta) * MAJOR_SHARE
    ):
        tags.append(TAG_VOLUME)
    top3 = [row for row in agents if row["contrib"] is not None][:3]
    if (
        len(top3) >= 3
        and all(row["tier"] == "A" for row in top3)
        and all(row["net"] > row["prev_net"] and row["collection"] < row["prev_collection"] for row in top3)
    ):
        tags.append(TAG_AGENT)
    plus = next(row for row in aging if row["bucket"] == "90+")
    overdue_now = sum((row["amount"] for row in aging if row["bucket"] != BUCKET_UNDUE), Decimal("0"))
    overdue_prev = sum((row["prev_amount"] for row in aging if row["bucket"] != BUCKET_UNDUE), Decimal("0"))
    if plus["amount"] > plus["prev_amount"] or overdue_now > overdue_prev:
        tags.append(TAG_AGING)
    if disc0 is not None and disc1 is not None and (disc1 - disc0) >= DISCOUNT_TAG_PP and delta < 0:
        tags.append(TAG_DISCOUNT)
    valid = [row for row in cross_rows if row["contrib"] is not None]
    if cross_dimension == "region" and delta < 0 and valid:
        worst = min(valid, key=lambda row: row["contrib"])
        if worst["contrib"] < 0 and worst["contrib"] / delta >= Decimal("0.5"):
            tags.append(TAG_REGION)
    return tags


def build_attribution(store: Store, ym: str, compare: str, scope: Scope, cross_dimension: str, title: str) -> dict | None:
    net1, net0 = net_sales(store, ym, scope), net_sales(store, compare, scope)
    col1, col0 = collection_amount(store, ym, scope), collection_amount(store, compare, scope)
    parts = decompose(col0, net0, col1, net1)
    if parts is None:
        return None
    cross = contributions(store, ym, compare, scope, cross_dimension)
    agents = customer_contribs(store, ym, compare, scope)
    aging = aging_migration(store, ym, compare, scope)
    disc1, disc0 = discount_rate(store, ym, scope), discount_rate(store, compare, scope)
    timing1, timing0 = month_timing(store, ym, scope), month_timing(store, compare, scope)
    due1, due0 = due_collection_rate(store, ym, scope), due_collection_rate(store, compare, scope)
    invoices = large_due_invoices(store, ym, scope, limit=None)
    big = [row for row in invoices if row["original"] >= LARGE_INVOICE]
    signals = _signals(parts, net0, net1, agents, aging, disc0, disc1, cross, cross_dimension)
    cross_label = {"region": "大区", "product_line": "产品线", "tier": "代理商等级", "cohort": "新老代理商"}[cross_dimension]

    notes = []
    if timing0 and timing1:
        notes.append(
            f"月末7日净销售占比 {pct_text(timing0['late_share'])} → {pct_text(timing1['late_share'])}；"
            f"月初7日净销售占比 {pct_text(timing0['early_share'])} → {pct_text(timing1['early_share'])}。"
        )
        if timing1["late_share"] > timing0["late_share"]:
            notes.append("净销售更集中在月末 7 日，不是月初前移。")
    if disc0 is not None and disc1 is not None:
        notes.append(f"折扣率 {pct_text(disc0)} → {pct_text(disc1)}。")
    if due0 is not None and due1 is not None:
        notes.append(f"到期回款率（辅助口径） {pct_text(due0)} → {pct_text(due1)}。")
    notes.append(
        f"本月到期发票 {len(invoices)} 张，其中原净额达到 {money_text(LARGE_INVOICE)} 的 {len(big)} 张。"
    )

    fact_lines = [
        f"切片：{title}",
        f"期间：{period_label(ym)}（{ym}）",
        f"对比期间：{period_label(compare)}（{compare}）",
        "参评门槛：净销售占比 5%。",
        f"计算信号：{'、'.join(signals) if signals else '无'}",
        f"销售回款率：{pct_text(parts['rate0'])} → {pct_text(parts['rate1'])}，环比 {pp_text(parts['delta'])}",
        f"回款额：{money_text(col0)} → {money_text(col1)}，回款额变化贡献 {pp_text(parts['collection_effect'])}",
        f"净销售额：{money_text(net0)} → {money_text(net1)}，净销售额变化贡献 {pp_text(parts['sales_effect'])}",
        f"交叉维度：{cross_label}",
    ]
    if TAG_AGENT in signals:
        fact_lines.append("A级拖累代理商家数：3")
    for row in cross:
        fact_lines.append(
            f"交叉 {row['name']} 贡献 {pp_text(row['contrib'])}，贡献额 {money_text(row['contrib_amount']) if row['contrib_amount'] is not None else '本数据无法支持'}，"
            f"回款额变化贡献 {pp_text(row['collection_effect'])}，净销售额变化贡献 {pp_text(row['sales_effect'])}"
        )
    fact_lines.append("拖累代理商按贡献从低到高：")
    for row in agents[:5]:
        fact_lines.append(
            f"{row['customer_name']}（{row['tier']}级，{row['region_name']}）贡献 {pp_text(row['contrib'])}，"
            f"净销售 {money_text(row['prev_net'])} → {money_text(row['net'])}，回款额 {money_text(row['prev_collection'])} → {money_text(row['collection'])}"
        )
    for row in aging:
        fact_lines.append(
            f"账龄 {row['bucket']} {money_text(row['prev_amount'])} → {money_text(row['amount'])}，"
            f"占比 {pct_text(row['prev_share'])} → {pct_text(row['share'])}"
        )
    fact_lines.extend(notes)
    for row in big[:5]:
        fact_lines.append(
            f"大额到期发票 {row['invoice_id']} {row['customer_name']} 原净额 {money_text(row['original'])}，"
            f"月末仍未收回 {money_text(row['outstanding'])}，到期日 {row['due_date']}"
        )
    fact_sheet = "\n".join(fact_lines)
    narrative = write_narrative(fact_sheet, signals)

    cross_rows = []
    for row in cross:
        cross_rows.append({
            "name": row["name"],
            "pp_text": pp_text(row["contrib"]),
            "amount_text": money_text(row["contrib_amount"]) if row["contrib_amount"] is not None else "本数据无法支持",
            "pp_num": pp_num(row["contrib"]),
            "collection_text": pp_text(row["collection_effect"]),
            "sales_text": pp_text(row["sales_effect"]),
        })
    agent_rows = []
    for row in agents[:8]:
        agent_rows.append({
            "name": row["customer_name"],
            "tier": f"{row['tier']}级",
            "region": row["region_name"],
            "pp_text": pp_text(row["contrib"]),
            "pp_num": pp_num(row["contrib"]),
            "net_text": f"{money_text(row['prev_net'])} → {money_text(row['net'])}",
            "collection_text": f"{money_text(row['prev_collection'])} → {money_text(row['collection'])}",
        })
    aging_rows = []
    for row in aging:
        aging_rows.append({
            "bucket": row["bucket"],
            "prev": money_text(row["prev_amount"]),
            "curr": money_text(row["amount"]),
            "share_prev": pct_text(row["prev_share"]),
            "share_curr": pct_text(row["share"]),
            "share_delta": pp_text(row["share_delta"]),
        })
    invoice_rows = []
    for row in invoices[:5]:
        invoice_rows.append({
            "invoice_id": row["invoice_id"],
            "customer": row["customer_name"],
            "tier": f"{row['tier']}级",
            "line": row["product_line"],
            "due": row["due_date"],
            "original": money_text(row["original"]),
            "outstanding": money_text(row["outstanding"]),
        })
    prov_rows = [{"部分": "率变分解", "项目": "回款额变化贡献", "数值": pp_text(parts["collection_effect"])}]
    prov_rows.append({"部分": "率变分解", "项目": "净销售额变化贡献", "数值": pp_text(parts["sales_effect"])})
    for row in cross_rows:
        prov_rows.append({"部分": "交叉贡献", "项目": row["name"], "数值": row["pp_text"], "贡献额": row["amount_text"]})
    for row in agent_rows:
        prov_rows.append({"部分": "代理商", "项目": row["name"], "数值": row["pp_text"]})
    for row in aging_rows:
        prov_rows.append({"部分": "账龄", "项目": row["bucket"], "数值": f"{row['prev']} → {row['curr']}"})
    return {
        "title": f"归因 · {title}",
        "decomposition": {
            "collection_text": pp_text(parts["collection_effect"]),
            "sales_text": pp_text(parts["sales_effect"]),
            "delta_text": pp_text(parts["delta"]),
            "detail": (
                f"回款额 {money_text(col0)} → {money_text(col1)}，贡献 {pp_text(parts['collection_effect'])}。"
                f"净销售额 {money_text(net0)} → {money_text(net1)}，贡献 {pp_text(parts['sales_effect'])}。"
            ),
        },
        "cross_title": f"按{cross_label}拆贡献",
        "cross_rows": cross_rows,
        "agents": agent_rows,
        "aging": aging_rows,
        "auxiliary_notes": notes,
        "large_invoices": invoice_rows,
        "signals": signals,
        "narrative": narrative,
        "provenance": _prov(
            F_DECOMP + F_CONTRIB + F_DUE + F_AR,
            _filters(store, ym, compare, scope),
            prov_rows,
            notes,
        ),
    }


def _trailing(ym: str, count: int = 6) -> list[str]:
    months = []
    for offset in range(count - 1, -1, -1):
        month = shift_month(ym, -offset)
        if month >= DATA_START:
            months.append(month)
    return months


def _pack(query, store, compare, scope, conclusion, kpis, charts, attribution, chat_reply, view_switch=None, resolved_view=None):
    return {
        "ok": True,
        "supported": True,
        "keep_report": False,
        "question_id": query.question_id,
        "question": query.text,
        "period": query.period,
        "period_label": period_label(query.period),
        "compare_period": compare,
        "compare_label": period_label(compare),
        "filters": {
            "region_id": scope.region_id,
            "region_name": store.region_name.get(scope.region_id) if scope.region_id else None,
            "product_line": scope.product_line,
            "tier": scope.tier,
            "label": scope_title(store, scope),
        },
        "view": query.view,
        "resolved_view": resolved_view or query.view,
        "view_switch": view_switch,
        "conclusion": conclusion,
        "kpis": kpis,
        "charts": charts,
        "attribution": attribution,
        "chat_reply": chat_reply,
    }


def _say(text, provenance):
    return {"text": text, "sentences": [{"text": text, "provenance": provenance}]}


def _blocked(query: Query, message: str) -> dict:
    return {
        "ok": True,
        "supported": False,
        "keep_report": True,
        "question_id": "unsupported",
        "question": query.text,
        "chat_reply": message,
        "conclusion": None,
        "kpis": [],
        "charts": [],
        "attribution": None,
        "view_switch": None,
    }


def build_main(store, query, compare, scope):
    ym = query.period
    region_rows = rate_slices(store, ym, compare, "region", scope)
    product_rows = rate_slices(store, ym, compare, "product_line", scope)
    region_best = select_fastest(region_rows)
    product_best = select_fastest(product_rows)
    if region_best is None and product_best is None:
        return _blocked(query, "本数据无法支持：没有净销售占比达到 5% 且能计算环比的切片。")
    if query.view == "product" and product_best is not None:
        resolved = "product"
    elif query.view == "region" and region_best is not None:
        resolved = "region"
    elif region_best is None:
        resolved = "product"
    elif product_best is None:
        resolved = "region"
    elif region_best["delta"] <= product_best["delta"]:
        resolved = "region"
    else:
        resolved = "product"
    primary = region_best if resolved == "region" else product_best
    other = product_best if resolved == "region" else region_best
    dimension = "region" if resolved == "region" else "product_line"
    cross_dimension = "product_line" if resolved == "region" else "region"
    focus = apply_member(scope, dimension, primary["key"])
    title = slice_title(dimension, primary["name"])
    other_text = ""
    if other is not None:
        other_dim = "product_line" if resolved == "region" else "region"
        other_text = f"可切换{('产品线' if resolved == 'region' else '大区')}视角（{slice_title(other_dim, other['name'])} {pp_text(other['delta'])}）。"
    parts = decompose(primary["prev_collection"], primary["prev_net"], primary["collection"], primary["net"])
    text = (
        f"{period_label(ym)}销售回款率下降最快：{title}（{pp_text(primary['delta'])}，净销售占比 {pct_text(primary['share'])}）。"
        f"回款额变化贡献 {pp_text(parts['collection_effect'])}，净销售额变化贡献 {pp_text(parts['sales_effect'])}。"
        f"{other_text}"
    )
    conclusion = _say(text, _prov(F_RATE + F_DECOMP + F_GATE, _filters(store, ym, compare, Scope()), _rate_rows(region_rows) + _rate_rows(product_rows), [F_GATE]))
    attribution = build_attribution(store, ym, compare, focus, cross_dimension, title)
    charts = [
        rate_chart(region_rows, "各大区销售回款率：本月 vs 上月", "region_rate", ym, compare, store, scope, "focus"),
        rate_chart(product_rows, "各产品线销售回款率：本月 vs 上月", "line_rate", ym, compare, store, scope, "focus"),
    ]
    view_switch = {
        "current": resolved,
        "options": [
            {
                "id": "region",
                "label": "大区视角",
                "slice": "—" if region_best is None else f"{region_best['name']}大区",
                "pp_text": "—" if region_best is None else pp_text(region_best["delta"]),
            },
            {
                "id": "product",
                "label": "产品线视角",
                "slice": "—" if product_best is None else product_best["name"],
                "pp_text": "—" if product_best is None else pp_text(product_best["delta"]),
            },
        ],
    }
    reply = f"已生成{period_label(ym)}主问报告。下降最快的是{title}（{pp_text(primary['delta'])}）。左侧是结论、指标、对比图和归因。"
    return _pack(query, store, compare, scope, conclusion, build_kpis(store, ym, compare, scope), charts, attribution, reply, view_switch, resolved)


def build_slice(store, query, compare, scope):
    ym = query.period
    title = scope_title(store, scope)
    net1, net0 = net_sales(store, ym, scope), net_sales(store, compare, scope)
    col1, col0 = collection_amount(store, ym, scope), collection_amount(store, compare, scope)
    parts = decompose(col0, net0, col1, net1)
    if parts is None:
        return _blocked(query, f"本数据无法支持：{title}在{period_label(ym)}没有可计算的销售回款率环比。")
    if scope.region_id and not scope.product_line:
        chart_dim, cross_dim = "product_line", "product_line"
    elif scope.product_line and not scope.region_id:
        chart_dim, cross_dim = "region", "region"
    else:
        chart_dim, cross_dim = "tier", "tier"
    charts = []
    if chart_dim:
        rows = rate_slices(store, ym, compare, chart_dim, scope)
        charts.append(rate_chart(rows, f"{title}内部销售回款率：本月 vs 上月", "slice_rate", ym, compare, store, scope, "focus"))
    attribution = build_attribution(store, ym, compare, scope, cross_dim, title)
    text = (
        f"当前切片 {title}。{period_label(ym)}销售回款率 {pct_text(parts['rate1'])}，环比 {pp_text(parts['delta'])}。"
        f"回款额变化贡献 {pp_text(parts['collection_effect'])}，净销售额变化贡献 {pp_text(parts['sales_effect'])}。"
    )
    conclusion = _say(text, _prov(F_RATE + F_DECOMP, _filters(store, ym, compare, scope), [
        {"切片": title, "本期销售回款率": pct_text(parts["rate1"]), "上期销售回款率": pct_text(parts["rate0"]), "环比": pp_text(parts["delta"]),
         "回款额变化贡献": pp_text(parts["collection_effect"]), "净销售额变化贡献": pp_text(parts["sales_effect"])},
    ]))
    reply = f"已把左侧切到{title}。销售回款率 {pct_text(parts['rate1'])}，环比 {pp_text(parts['delta'])}。"
    return _pack(query, store, compare, scope, conclusion, build_kpis(store, ym, compare, scope), charts, attribution, reply)


def build_trend(store, query, compare, scope):
    ym = query.period
    months = _trailing(ym, 6)
    members = dimension_members("region")
    if scope.region_id:
        members = [item for item in members if item[0] == scope.region_id]
    series = []
    table_rows = []
    for key, name in members:
        child = apply_member(scope, "region", key)
        values = []
        for month in months:
            values.append(pct_num(None if net_sales(store, month, child) == 0 else collection_amount(store, month, child) / net_sales(store, month, child)))
        series.append({"name": name, "values": values})
        first, last = values[0], values[-1]
        delta = None if first is None or last is None else (Decimal(str(last)) - Decimal(str(first))) / Decimal(100)
        table_rows.append({"大区": name, "窗口首月": "—" if first is None else f"{first:.2f}%", "最后一个月": "—" if last is None else f"{last:.2f}%", "窗口变动": pp_text(delta)})
    # Rank the latest point and the window change on unrounded ratios.
    scored = []
    for key, name in members:
        child = apply_member(scope, "region", key)
        start_m, end_m = months[0], months[-1]
        r0 = None if net_sales(store, start_m, child) == 0 else collection_amount(store, start_m, child) / net_sales(store, start_m, child)
        r1 = None if net_sales(store, end_m, child) == 0 else collection_amount(store, end_m, child) / net_sales(store, end_m, child)
        scored.append((name, r1, None if r0 is None or r1 is None else r1 - r0))
    scored = [item for item in scored if item[1] is not None]
    lowest = min(scored, key=lambda item: item[1])
    moved = min(scored, key=lambda item: item[2] if item[2] is not None else Decimal("0"))
    text = (
        f"近 {len(months)} 个月（{period_label(months[0])}至{period_label(months[-1])}）"
        f"销售回款率最低的是{lowest[0]}大区（{pct_text(lowest[1])}）。"
        f"窗口内下降最多的是{moved[0]}大区（{pp_text(moved[2])}，为窗口末月减首月）。"
    )
    chart = {
        "id": "region_trend",
        "title": "各大区销售回款率趋势",
        "type": "line",
        "y_format": "pct",
        "drill": "trend",
        "categories": [period_label(month) for month in months],
        "series": series,
        "footnote": "点大区名称可只看该大区。",
        "provenance": _prov(F_RATE, _filters(store, ym, compare, scope) + [f"趋势窗口={months[0]}至{months[-1]}"], table_rows),
    }
    conclusion = _say(text, chart["provenance"])
    reply = f"已在左侧更新近 {len(months)} 个月销售回款率趋势。"
    return _pack(query, store, compare, scope, conclusion, build_kpis(store, ym, compare, scope), [chart], None, reply)


def build_overdue(store, query, compare, scope):
    ym = query.period
    rows = top_ar_customers(store, ym, scope, limit=50)
    overdue_rows = sorted(rows, key=lambda row: (-row["overdue"], row["customer_id"]))
    top = [row for row in overdue_rows if row["overdue"] > 0][:10]
    aging = aging_migration(store, ym, compare, scope)
    overdue_buckets = [row for row in aging if row["bucket"] != BUCKET_UNDUE]
    worst_bucket = max(overdue_buckets, key=lambda row: row["amount"]) if overdue_buckets else None
    total_ar = ar_balance(store, ym, scope)
    top5_ar = sum((row["outstanding"] for row in sorted(rows, key=lambda row: -row["outstanding"])[:5]), Decimal("0"))
    concentration = None if total_ar == 0 else top5_ar / total_ar
    age_days = weighted_age_days(store, ym, scope)
    names = "、".join(row["customer_name"] for row in top[:3]) if top else "无"
    bucket_text = "本数据无法支持" if worst_bucket is None else f"{worst_bucket['bucket']}（{money_text(worst_bucket['amount'])}）"
    text = (
        f"{period_label(ym)}逾期应收靠前的代理商是{names}。"
        f"逾期金额最大的账龄段是{bucket_text}。"
        f"前5家代理商应收集中度 {pct_text(concentration)}。加权平均账龄 {days_text(age_days)}。"
    )
    table = {
        "id": "overdue_table",
        "title": "逾期应收靠前的代理商",
        "type": "table",
        "columns": [
            {"key": "name", "label": "代理商"},
            {"key": "tier", "label": "等级"},
            {"key": "region", "label": "大区"},
            {"key": "overdue", "label": "逾期余额"},
            {"key": "outstanding", "label": "应收余额"},
        ],
        "rows": [
            {
                "name": row["customer_name"],
                "tier": f"{row['tier']}级",
                "region": row["region_name"],
                "overdue": money_text(row["overdue"]),
                "outstanding": money_text(row["outstanding"]),
            }
            for row in top
        ],
        "provenance": _prov(F_OVERDUE + F_AR, _filters(store, ym, None, scope), [
            {"代理商": row["customer_name"], "逾期余额": money_text(row["overdue"]), "应收余额": money_text(row["outstanding"])}
            for row in top
        ], [f"前5家应收集中度 {pct_text(concentration)}", f"加权平均账龄 {days_text(age_days)}。{F_AGE_DAYS}"]),
    }
    stacked = {
        "id": "overdue_stack",
        "title": "逾期靠前代理商的账龄结构",
        "type": "stacked",
        "y_format": "money",
        "segments": list(BUCKETS),
        "categories": [row["customer_name"] for row in top[:8]],
        "rows": [
            {"name": row["customer_name"], "values": [money_num(row["buckets"][bucket]) for bucket in BUCKETS]}
            for row in top[:8]
        ],
        "provenance": table["provenance"],
    }
    conclusion = _say(text, table["provenance"])
    reply = "已在左侧列出逾期应收靠前的代理商和账龄结构。"
    return _pack(query, store, compare, scope, conclusion, build_kpis(store, ym, compare, scope), [stacked, table], None, reply)


def build_volume(store, query, compare, scope):
    ym = query.period
    title = scope_title(store, scope)
    net1, net0 = net_sales(store, ym, scope), net_sales(store, compare, scope)
    col1, col0 = collection_amount(store, ym, scope), collection_amount(store, compare, scope)
    parts = decompose(col0, net0, col1, net1)
    if parts is None:
        return _blocked(query, "本数据无法支持这段期间的回款与净销售分解。")
    larger = "净销售额变化" if abs(parts["sales_effect"]) >= abs(parts["collection_effect"]) else "回款额变化"
    both = parts["sales_effect"] < 0 and parts["collection_effect"] < 0
    text = (
        f"{title}：回款额变化贡献 {pp_text(parts['collection_effect'])}，净销售额变化贡献 {pp_text(parts['sales_effect'])}。"
        f"按绝对值，{larger}的拉动更大。"
        + ("两边都在拉低销售回款率。" if both else "")
        + f"净销售额 {money_text(net0)} → {money_text(net1)}；回款额 {money_text(col0)} → {money_text(col1)}。"
    )
    months = _trailing(ym, 6)
    net_vals, col_vals, rate_vals = [], [], []
    for month in months:
        net_v = net_sales(store, month, scope)
        col_v = collection_amount(store, month, scope)
        net_vals.append(money_num(net_v))
        col_vals.append(money_num(col_v))
        rate_vals.append(pct_num(None if net_v == 0 else col_v / net_v))
    dual = {
        "id": "volume_dual",
        "title": f"{title}：净销售额、回款额与销售回款率",
        "type": "dual",
        "categories": [period_label(month) for month in months],
        "bars": [
            {"name": "净销售额", "values": net_vals},
            {"name": "回款额", "values": col_vals},
        ],
        "line": {"name": "销售回款率", "values": rate_vals, "format": "pct"},
        "provenance": _prov(F_DECOMP + F_RATE, _filters(store, ym, compare, scope), [
            {"项目": "回款额变化贡献", "数值": pp_text(parts["collection_effect"])},
            {"项目": "净销售额变化贡献", "数值": pp_text(parts["sales_effect"])},
            {"项目": "净销售额", "上期": money_text(net0), "本期": money_text(net1)},
            {"项目": "回款额", "上期": money_text(col0), "本期": money_text(col1)},
        ]),
    }
    cross_dim = "product_line" if scope.region_id and not scope.product_line else "region"
    cross = contributions(store, ym, compare, scope, cross_dim)
    effect_chart = {
        "id": "volume_effects",
        "title": "各切片的回款贡献与净销售贡献",
        "type": "grouped_bar",
        "y_format": "pp",
        "drill": "focus",
        "highlight": [],
        "muted": [],
        "categories": [row["name"] for row in cross],
        "series": [
            {"name": "回款额变化贡献", "values": [pp_num(row["collection_effect"]) for row in cross]},
            {"name": "净销售额变化贡献", "values": [pp_num(row["sales_effect"]) for row in cross]},
        ],
        "provenance": dual["provenance"],
    }
    attribution = build_attribution(store, ym, compare, scope, cross_dim, title)
    conclusion = _say(text, dual["provenance"])
    reply = f"已在左侧拆开{title}的回款与净销售贡献。"
    return _pack(query, store, compare, scope, conclusion, build_kpis(store, ym, compare, scope), [dual, effect_chart], attribution, reply)


def build_discount(store, query, compare, scope):
    ym = query.period
    blocks = []
    hit = []
    for dimension, label in (("region", "大区"), ("product_line", "产品线")):
        rows = []
        for key, name in dimension_members(dimension):
            child = apply_member(scope, dimension, key)
            d1, d0 = discount_rate(store, ym, child), discount_rate(store, compare, child)
            r1 = None if net_sales(store, ym, child) == 0 else collection_amount(store, ym, child) / net_sales(store, ym, child)
            r0 = None if net_sales(store, compare, child) == 0 else collection_amount(store, compare, child) / net_sales(store, compare, child)
            d_delta = None if d1 is None or d0 is None else d1 - d0
            r_delta = None if r1 is None or r0 is None else r1 - r0
            # Compare the same 0.01pp rounding the page shows, so a 0.00pp remainder is not called a rise.
            worse = (
                d_delta is not None
                and r_delta is not None
                and (d_delta * Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) > 0
                and (r_delta * Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) < 0
            )
            if worse:
                hit.append(f"{name}{'大区' if dimension == 'region' else ''}（折扣率 {pp_text(d_delta)}，销售回款率 {pp_text(r_delta)}）")
            rows.append({"name": name, "discount_pp": pp_num(d_delta), "rate_pp": pp_num(r_delta), "worse": worse,
                         "discount_text": pp_text(d_delta), "rate_text": pp_text(r_delta)})
        blocks.append((label, rows))
    if hit:
        text = f"{period_label(ym)}折扣率上升且销售回款率下降的切片：{'；'.join(hit)}。"
    else:
        text = f"{period_label(ym)}没有同时出现折扣率上升和销售回款率下降的大区或产品线。"
    charts = []
    prov_rows = []
    for label, rows in blocks:
        charts.append({
            "id": f"discount_{label}",
            "title": f"各{label}：折扣率变动 vs 销售回款率变动",
            "type": "grouped_bar",
            "y_format": "pp",
            "drill": "focus",
            "highlight": [row["name"] for row in rows if row["worse"]],
            "muted": [],
            "categories": [row["name"] for row in rows],
            "series": [
                {"name": "折扣率变动", "values": [row["discount_pp"] for row in rows]},
                {"name": "销售回款率变动", "values": [row["rate_pp"] for row in rows]},
            ],
            "provenance": None,
        })
        for row in rows:
            prov_rows.append({"分组": label, "切片": row["name"], "折扣率变动": row["discount_text"], "销售回款率变动": row["rate_text"]})
    provenance = _prov(F_DISCOUNT + F_RATE, _filters(store, ym, compare, scope), prov_rows)
    for chart in charts:
        chart["provenance"] = provenance
    conclusion = _say(text, provenance)
    reply = "已在左侧对比折扣率变动和销售回款率变动。"
    return _pack(query, store, compare, scope, conclusion, build_kpis(store, ym, compare, scope), charts, None, reply)


def build_dso(store, query, compare, scope):
    ym = query.period
    rows = []
    for key, name in dimension_members("region"):
        if scope.region_id and scope.region_id != key:
            continue
        child = apply_member(scope, "region", key)
        cur, prev = dso(store, ym, child), dso(store, compare, child)
        delta = None if cur is None or prev is None else cur - prev
        rows.append({"name": name, "dso": cur, "prev": prev, "delta": delta})
    usable = [row for row in rows if row["dso"] is not None]
    if not usable:
        return _blocked(query, "本数据无法支持 DSO：近 3 个月净销售不完整。")
    worst = max(usable, key=lambda row: row["dso"])
    movers = [row for row in usable if row["delta"] is not None]
    mover = max(movers, key=lambda row: row["delta"]) if movers else None
    text = f"{period_label(ym)} DSO 最高的是{worst['name']}大区（{days_text(worst['dso'])}）。"
    if mover is not None:
        text += f"最近两个月上升最多的是{mover['name']}大区（{days_signed(mover['delta'])}）。"
    table_rows = [
        {"大区": row["name"], "本期DSO": days_text(row["dso"]), "上期DSO": days_text(row["prev"]), "变化": days_signed(row["delta"])}
        for row in rows
    ]
    provenance = _prov(F_DSO, _filters(store, ym, compare, scope), table_rows)
    chart = {
        "id": "dso_region",
        "title": "各大区 DSO",
        "type": "grouped_bar",
        "y_format": "days",
        "drill": "focus",
        "highlight": [worst["name"]],
        "muted": [],
        "categories": [row["name"] for row in rows],
        "series": [
            {"name": period_label(compare), "values": [days_num(row["prev"]) for row in rows]},
            {"name": period_label(ym), "values": [days_num(row["dso"]) for row in rows]},
        ],
        "provenance": provenance,
    }
    conclusion = _say(text, provenance)
    reply = f"已在左侧比较各大区 DSO。最高的是{worst['name']}大区（{days_text(worst['dso'])}）。"
    return _pack(query, store, compare, scope, conclusion, build_kpis(store, ym, compare, scope), [chart], None, reply)


def build_top_ar(store, query, compare, scope):
    ym = query.period
    rows = top_ar_customers(store, ym, scope, limit=10)
    if not rows:
        return _blocked(query, "本数据无法支持：当前切片没有应收余额。")
    text = f"{period_label(ym)}应收余额最高的是{rows[0]['customer_name']}（{money_text(rows[0]['outstanding'])}，{rows[0]['region_name']}，{rows[0]['tier']}级）。"
    table = {
        "id": "top_ar_table",
        "title": "TOP10 欠款代理商",
        "type": "table",
        "columns": [
            {"key": "rank", "label": "序"},
            {"key": "name", "label": "代理商"},
            {"key": "tier", "label": "等级"},
            {"key": "region", "label": "大区"},
            {"key": "outstanding", "label": "应收余额"},
            {"key": "overdue", "label": "其中逾期"},
        ],
        "rows": [
            {"rank": str(index + 1), "name": row["customer_name"], "tier": f"{row['tier']}级", "region": row["region_name"],
             "outstanding": money_text(row["outstanding"]), "overdue": money_text(row["overdue"])}
            for index, row in enumerate(rows)
        ],
        "provenance": _prov(F_AR, _filters(store, ym, None, scope), [
            {"代理商": row["customer_name"], "应收余额": money_text(row["outstanding"]), "逾期": money_text(row["overdue"])}
            for row in rows
        ]),
    }
    stacked = {
        "id": "top_ar_stack",
        "title": "TOP10 欠款代理商账龄结构",
        "type": "stacked",
        "y_format": "money",
        "segments": list(BUCKETS),
        "categories": [row["customer_name"] for row in rows],
        "rows": [{"name": row["customer_name"], "values": [money_num(row["buckets"][bucket]) for bucket in BUCKETS]} for row in rows],
        "provenance": table["provenance"],
    }
    conclusion = _say(text, table["provenance"])
    reply = "已在左侧列出应收余额前 10 的代理商和账龄。"
    return _pack(query, store, compare, scope, conclusion, build_kpis(store, ym, compare, scope), [stacked, table], None, reply)


def build_cycle(store, query, compare, scope):
    ym = query.period
    rows = []
    for line in ("试剂", "仪器", "服务"):
        if scope.product_line and scope.product_line != line:
            continue
        child = scope.narrow(product_line=line)
        rows.append({
            "name": line,
            "dso": dso(store, ym, child),
            "days": avg_collection_days(store, ym, child, 6),
        })
    usable = [row for row in rows if row["dso"] is not None]
    if not usable:
        return _blocked(query, "本数据无法支持各产品线的回款周期。")
    slowest = max(usable, key=lambda row: row["dso"])
    detail = "；".join(f"{row['name']} DSO {days_text(row['dso'])}，已回款平均 {days_text(row['days'])}" for row in rows)
    text = f"{period_label(ym)} DSO 最长的是{slowest['name']}（{days_text(slowest['dso'])}）。{detail}。平均回款天数只统计已回款，未回款不计入。"
    provenance = _prov(F_DSO + F_COLLECT_DAYS, _filters(store, ym, compare, scope), [
        {"产品线": row["name"], "DSO": days_text(row["dso"]), "已回款平均天数": days_text(row["days"])} for row in rows
    ], [F_COLLECT_DAYS])
    chart = {
        "id": "cycle_lines",
        "title": "各产品线回款周期",
        "type": "grouped_bar",
        "y_format": "days",
        "drill": "focus",
        "highlight": [slowest["name"]],
        "muted": [],
        "categories": [row["name"] for row in rows],
        "series": [
            {"name": "DSO", "values": [days_num(row["dso"]) for row in rows]},
            {"name": "已回款平均天数", "values": [days_num(row["days"]) for row in rows]},
        ],
        "provenance": provenance,
    }
    conclusion = _say(text, provenance)
    reply = f"已在左侧比较产品线回款周期。DSO 最长的是{slowest['name']}。"
    return _pack(query, store, compare, scope, conclusion, build_kpis(store, ym, compare, scope), [chart], None, reply)


def build_tier(store, query, compare, scope):
    ym = query.period
    rows = rate_slices(store, ym, compare, "tier", scope)
    bits = [f"{row['name']} {pct_text(row['rate'])}（环比 {pp_text(row['delta'])}，占比 {pct_text(row['share'])}）" for row in rows]
    text = f"{period_label(ym)}代理商等级销售回款率：{'；'.join(bits)}。"
    chart = rate_chart(rows, "A/B/C 级代理商销售回款率：本月 vs 上月", "tier_rate", ym, compare, store, scope, None)
    conclusion = _say(text, chart["provenance"])
    reply = "已在左侧对比 A/B/C 级代理商的销售回款率。"
    return _pack(query, store, compare, scope, conclusion, build_kpis(store, ym, compare, scope), [chart], None, reply)


def build_new_old(store, query, compare, scope):
    ym = query.period
    rows = []
    for key, name in (("new", "新代理商"), ("old", "老代理商")):
        child = scope.narrow(cohort=key)
        net1, net0 = net_sales(store, ym, child), net_sales(store, compare, child)
        col1, col0 = collection_amount(store, ym, child), collection_amount(store, compare, child)
        parts = decompose(col0, net0, col1, net1)
        rows.append({"name": name, "parts": parts, "net": net1, "prev_net": net0})
    if any(row["parts"] is None for row in rows):
        missing = "、".join(row["name"] for row in rows if row["parts"] is None)
        text = f"本数据无法支持完整的新老对比（{missing}在对比月或本月没有净销售）。"
        bits = []
    else:
        bits = [f"{row['name']} {pct_text(row['parts']['rate1'])}（环比 {pp_text(row['parts']['delta'])}）" for row in rows]
        text = f"{period_label(ym)}新老代理商销售回款率：{'；'.join(bits)}。新代理商指首次出库落在近 12 个月。"
    provenance = _prov(F_RATE + F_NEW, _filters(store, ym, compare, scope), [
        {
            "分组": row["name"],
            "本期净销售额": money_text(row["net"]),
            "本期销售回款率": pct_text(None if row["parts"] is None else row["parts"]["rate1"]),
            "环比": pp_text(None if row["parts"] is None else row["parts"]["delta"]),
        }
        for row in rows
    ], [F_NEW])
    chart = {
        "id": "new_old",
        "title": "新代理商与老代理商销售回款率",
        "type": "grouped_bar",
        "y_format": "pct",
        "drill": None,
        "highlight": [],
        "muted": [],
        "categories": [row["name"] for row in rows],
        "series": [
            {"name": period_label(compare), "values": [pct_num(None if row["parts"] is None else row["parts"]["rate0"]) for row in rows]},
            {"name": period_label(ym), "values": [pct_num(None if row["parts"] is None else row["parts"]["rate1"]) for row in rows]},
        ],
        "provenance": provenance,
    }
    conclusion = _say(text, provenance)
    reply = "已在左侧对比新代理商和老代理商的销售回款率。"
    return _pack(query, store, compare, scope, conclusion, build_kpis(store, ym, compare, scope), [chart], None, reply)


def build_ar_bridge(store, query, compare, scope):
    ym = query.period
    bridge = ar_bridge(store, ym, compare, scope)
    text = (
        f"{scope_title(store, scope)}应收余额 {money_text(bridge['start_ar'])} → {money_text(bridge['end_ar'])}，变动 {money_text(bridge['change'])}。"
        f"本期新增应收 {money_text(bridge['added'])}，本期回款 {money_text(bridge['collected'])}，其他 {money_text(bridge['other'])}。"
    )
    provenance = _prov(F_BRIDGE, _filters(store, ym, compare, scope), [
        {"项目": "上期末应收", "金额": money_text(bridge["start_ar"])},
        {"项目": "本期新增应收", "金额": money_text(bridge["added"])},
        {"项目": "本期回款", "金额": money_text(bridge["collected"])},
        {"项目": "其他", "金额": money_text(bridge["other"])},
        {"项目": "本期末应收", "金额": money_text(bridge["end_ar"])},
    ])
    chart = {
        "id": "ar_bridge",
        "title": "应收余额月环比拆解",
        "type": "hbar",
        "rows": [
            {"name": "本期新增应收", "value": money_num(bridge["added"]), "text": money_text(bridge["added"])},
            {"name": "本期回款（减少应收）", "value": money_num(-bridge["collected"]), "text": money_text(-bridge["collected"])},
            {"name": "其他", "value": money_num(bridge["other"]), "text": money_text(bridge["other"])},
            {"name": "应收余额变动", "value": money_num(bridge["change"]), "text": money_text(bridge["change"])},
        ],
        "provenance": provenance,
    }
    conclusion = _say(text, provenance)
    reply = "已在左侧拆开应收余额的月环比。"
    return _pack(query, store, compare, scope, conclusion, build_kpis(store, ym, compare, scope), [chart], None, reply)


def _bucket_share(store, month, scope, bucket):
    from app.metrics import aging_amounts

    amounts_map = aging_amounts(store, month, scope)
    total = sum(amounts_map.values(), Decimal("0"))
    if total == 0:
        return None, amounts_map[bucket]
    return amounts_map[bucket] / total, amounts_map[bucket]


def build_aging90(store, query, compare, scope):
    ym = query.period
    months = _trailing(ym, 6)
    shares, amounts = [], []
    table = []
    share_by_month = {}
    for month in months:
        share, amount = _bucket_share(store, month, scope, "90+")
        share_by_month[month] = share
        shares.append(pct_num(share))
        amounts.append(money_num(amount))
        table.append({"月份": period_label(month), "90+金额": money_text(amount), "90+占比": pct_text(share)})
    prev_share = share_by_month.get(months[-2]) if len(months) >= 2 else None
    curr_share = share_by_month.get(months[-1]) if months else None
    if prev_share is None or curr_share is None:
        text = "本数据无法支持 90 天以上账龄占比的最近变动。"
    else:
        direction = "上升" if curr_share > prev_share else ("持平" if curr_share == prev_share else "下降")
        text = f"90 天以上账龄占比最近一个月{direction}：{pct_text(prev_share)} → {pct_text(curr_share)}（{pp_text(curr_share - prev_share)}）。"
    provenance = _prov(F_AR + "90+占比 = 账龄为 90+ 的未清金额 ÷ 应收余额。", _filters(store, ym, compare, scope), table)
    chart = {
        "id": "aging90",
        "title": "90 天以上账龄占比",
        "type": "line",
        "y_format": "pct",
        "drill": None,
        "categories": [period_label(month) for month in months],
        "series": [{"name": "90+占比", "values": shares}],
        "provenance": provenance,
    }
    amount_chart = {
        "id": "aging90_amount",
        "title": "90 天以上账龄金额",
        "type": "grouped_bar",
        "y_format": "money",
        "drill": None,
        "highlight": [],
        "muted": [],
        "categories": [period_label(month) for month in months],
        "series": [{"name": "90+金额", "values": amounts}],
        "provenance": provenance,
    }
    conclusion = _say(text, provenance)
    reply = "已在左侧更新 90 天以上账龄的占比和金额。"
    return _pack(query, store, compare, scope, conclusion, build_kpis(store, ym, compare, scope), [chart, amount_chart], None, reply)


def build_top_collection(store, query, compare, scope):
    ym = query.period
    total = collection_amount(store, ym, scope)
    agents = []
    ids = set()
    for row in store.collections_by_month.get(ym, []):
        if scope.region_id and row["region_id"] != scope.region_id:
            continue
        if scope.product_line:
            product = row.get("product_id") or ""
            if not product or store.products[product]["product_line"] != scope.product_line:
                continue
        if scope.tier and store.customers[row["customer_id"]]["customer_tier"] != scope.tier:
            continue
        ids.add(row["customer_id"])
    for customer_id in ids:
        child = scope.narrow(customer_id=customer_id)
        amount = collection_amount(store, ym, child)
        customer = store.customers[customer_id]
        agents.append({
            "name": customer["customer_name"],
            "tier": customer["customer_tier"],
            "region": store.region_name[customer["region_id"]],
            "amount": amount,
            "share": None if total == 0 else amount / total,
        })
    agents.sort(key=lambda row: (-row["amount"], row["name"]))
    region_rows = []
    for key, name in dimension_members("region"):
        if scope.region_id and scope.region_id != key:
            continue
        amount = collection_amount(store, ym, apply_member(scope, "region", key))
        region_rows.append({"name": name, "amount": amount, "share": None if total == 0 else amount / total})
    region_rows.sort(key=lambda row: -row["amount"])
    if not agents:
        return _blocked(query, "本数据无法支持：当前切片这个月没有回款。")
    top_region = region_rows[0]
    text = (
        f"{period_label(ym)}回款最高的代理商是{agents[0]['name']}（{money_text(agents[0]['amount'])}，占 {pct_text(agents[0]['share'])}）。"
        f"回款最高的大区是{top_region['name']}（{money_text(top_region['amount'])}，占 {pct_text(top_region['share'])}）。"
    )
    provenance = _prov(F_COL, _filters(store, ym, None, scope), [
        {"代理商": row["name"], "大区": row["region"], "回款额": money_text(row["amount"]), "占比": pct_text(row["share"])}
        for row in agents[:10]
    ])
    chart = {
        "id": "top_collection",
        "title": "本月回款贡献靠前的代理商",
        "type": "hbar",
        "rows": [
            {"name": row["name"], "value": money_num(row["amount"]), "text": f"{money_text(row['amount'])} · {pct_text(row['share'])}"}
            for row in agents[:10]
        ],
        "provenance": provenance,
    }
    region_chart = {
        "id": "collection_region",
        "title": "本月各大区回款额",
        "type": "grouped_bar",
        "y_format": "money",
        "drill": "focus",
        "highlight": [top_region["name"]],
        "muted": [],
        "categories": [row["name"] for row in region_rows],
        "series": [{"name": period_label(ym), "values": [money_num(row["amount"]) for row in region_rows]}],
        "provenance": provenance,
    }
    conclusion = _say(text, provenance)
    reply = "已在左侧列出本月回款贡献靠前的代理商和大区。"
    return _pack(query, store, compare, scope, conclusion, build_kpis(store, ym, compare, scope), [chart, region_chart], None, reply)


def build_growth(store, query, compare, scope):
    ym = query.period
    rows = []
    for line in ("试剂", "仪器", "服务"):
        if scope.product_line and scope.product_line != line:
            continue
        child = scope.narrow(product_line=line)
        g1, g0 = gross_amount(store, ym, child), gross_amount(store, compare, child)
        c1, c0 = collection_amount(store, ym, child), collection_amount(store, compare, child)
        n1, n0 = net_sales(store, ym, child), net_sales(store, compare, child)
        ship_g = None if g0 == 0 else (g1 - g0) / g0
        col_g = None if c0 == 0 else (c1 - c0) / c0
        net_g = None if n0 == 0 else (n1 - n0) / n0
        lag = ship_g is not None and col_g is not None and ship_g > 0 and ship_g > col_g
        rows.append({"name": line, "ship_g": ship_g, "col_g": col_g, "net_g": net_g, "lag": lag})
    lagged = [row for row in rows if row["lag"]]
    if lagged:
        detail = "；".join(f"{row['name']}出库额 {pct_signed(row['ship_g'])}，回款额 {pct_signed(row['col_g'])}" for row in lagged)
        text = f"出货在增长、回款没跟上的产品线：{detail}。"
    else:
        text = f"{period_label(ym)}没有「出库额正增长且增速高于回款额」的产品线。"
    provenance = _prov(F_SHIP + F_COL + "对照列净销售额增速 = 净销售额环比，不替代出库额增速。", _filters(store, ym, compare, scope), [
        {"产品线": row["name"], "出库额增速": pct_signed(row["ship_g"]), "回款额增速": pct_signed(row["col_g"]), "净销售额增速": pct_signed(row["net_g"]), "出货增长且回款没跟上": "是" if row["lag"] else "否"}
        for row in rows
    ])
    chart = {
        "id": "growth_lag",
        "title": "出库额增速 vs 回款额增速",
        "type": "grouped_bar",
        "y_format": "pct",
        "drill": "focus",
        "highlight": [row["name"] for row in lagged],
        "muted": [],
        "categories": [row["name"] for row in rows],
        "series": [
            {"name": "出库额增速", "values": [pct_num(row["ship_g"]) for row in rows]},
            {"name": "回款额增速", "values": [pct_num(row["col_g"]) for row in rows]},
        ],
        "provenance": provenance,
    }
    conclusion = _say(text, provenance)
    reply = "已在左侧对照各产品线的出库增速和回款增速。"
    return _pack(query, store, compare, scope, conclusion, build_kpis(store, ym, compare, scope), [chart], None, reply)


def build_agents(store, query, compare, scope):
    ym = query.period
    title = scope_title(store, scope)
    agents = [row for row in customer_contribs(store, ym, compare, scope) if row["contrib"] is not None][:8]
    if not agents:
        return _blocked(query, "本数据无法支持：这个切片算不出代理商贡献。")
    names = "、".join(f"{row['customer_name']}（{pp_text(row['contrib'])}）" for row in agents[:3])
    text = f"{title}对销售回款率拖累最大的代理商是{names}。"
    provenance = _prov(F_CONTRIB, _filters(store, ym, compare, scope), [
        {"代理商": row["customer_name"], "等级": f"{row['tier']}级", "大区": row["region_name"], "贡献": pp_text(row["contrib"]),
         "净销售": f"{money_text(row['prev_net'])} → {money_text(row['net'])}", "回款额": f"{money_text(row['prev_collection'])} → {money_text(row['collection'])}"}
        for row in agents
    ])
    chart = {
        "id": "agent_drag",
        "title": f"{title}：拖累销售回款率的代理商",
        "type": "hbar",
        "rows": [
            {"name": f"{row['customer_name']}（{row['tier']}级）", "value": pp_num(row["contrib"]), "text": pp_text(row["contrib"])}
            for row in agents
        ],
        "provenance": provenance,
    }
    cross_dim = "product_line" if scope.region_id else "region"
    attribution = build_attribution(store, ym, compare, scope, cross_dim, title)
    conclusion = _say(text, provenance)
    reply = f"已在左侧列出{title}里拖累销售回款率的代理商。"
    return _pack(query, store, compare, scope, conclusion, build_kpis(store, ym, compare, scope), [chart], attribution, reply)


BUILDERS = {
    "main": build_main,
    "slice_focus": build_slice,
    "trend": build_trend,
    "overdue": build_overdue,
    "volume": build_volume,
    "discount": build_discount,
    "dso": build_dso,
    "top_ar": build_top_ar,
    "cycle": build_cycle,
    "tier": build_tier,
    "new_old": build_new_old,
    "ar_bridge": build_ar_bridge,
    "aging90": build_aging90,
    "top_collection": build_top_collection,
    "growth_lag": build_growth,
    "agents": build_agents,
}


def comparable(store: Store, ym: str) -> tuple[str | None, str | None]:
    compare = shift_month(ym, -1)
    if net_sales(store, ym, Scope()) == 0 or net_sales(store, compare, Scope()) == 0:
        return None, f"{period_label(ym)}没有完整的上月经营数据，不能计算环比。演示默认期间是 2026年10月。"
    return compare, None


def answer(store: Store, question: str, period: str, view: str = "auto") -> dict:
    query = classify(question, period, view)
    if query.intent == "unsupported":
        return _blocked(query, query.message or "暂不支持。")
    compare, problem = comparable(store, query.period)
    if problem:
        return _blocked(query, problem)
    scope = Scope(region_id=query.region_id, product_line=query.product_line, tier=query.tier)
    return BUILDERS[query.intent](store, query, compare, scope)
