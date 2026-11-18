"""Section 8 of the locked data-map contract: metrics, slices, relations, typed refusals.

These paths are not open NL-to-SQL. A question must land on an enumerated
metric, dimension, or relation; otherwise it is refused with a 5.1 type.
"""

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from app import agent
from app.constants import BUCKETS, SHARE_MIN
from app.metrics import (
    Scope,
    aging_amounts,
    ar_balance,
    avg_collection_days,
    collection_amount,
    contributions,
    decompose,
    due_amount,
    net_sales,
    shipments,
    top_ar_customers,
)
from app.textfmt import pct_text, pp_text
from app.store import load_store

STORE = load_store()
YM = "2026-10"
PREV = "2026-09"


def _plain(text: str) -> str:
    return text.replace(",", "").replace("+", "").replace("元", "").replace("%", "").replace("pp", "").strip()


def _money(text: str) -> Decimal:
    return Decimal(_plain(text).removesuffix("天"))


def _table(result: dict) -> list[dict]:
    return result["result"]["数据"]


def _prov_table(result: dict) -> dict:
    charts = agent._charts(result)
    assert charts and charts[-1]["type"] == "table"
    provenance = charts[-1]["provenance"]
    assert provenance["formula"]
    assert provenance["filters"]
    assert provenance["rows"]
    return charts[-1]


def test_new_flow_metrics_match_the_shipment_and_invoice_rows():
    scope = Scope(product_line="仪器", region_id="R-HD")
    result = agent.run_query(
        STORE,
        {"metrics": ["shipment_qty", "due_amount", "new_ar"], "region": "华东", "product_line": "仪器"},
        YM,
    )
    row = _table(result)[0]
    qty = sum(line["qty"] for line in shipments(STORE, YM, scope))
    assert row["出库量"] == f"{qty:,}台"
    assert _money(row["本期到期应收"]) == due_amount(STORE, YM, scope)
    assert _money(row["本期新增应收"]) == net_sales(STORE, YM, scope)
    assert "出库" in result["formulas"][0]
    _prov_table(result)


def test_shipment_qty_refuses_cross_line_and_mixed_units():
    with pytest.raises(agent.ToolError, match="出库量"):
        agent.run_query(STORE, {"metrics": ["shipment_qty"]}, YM)
    with pytest.raises(agent.ToolError, match="出库量"):
        agent.run_query(STORE, {"metrics": ["shipment_qty"], "product_line": "试剂"}, YM)
    boxed = agent.run_query(STORE, {"metrics": ["shipment_qty"], "product_line": "试剂", "unit": "盒"}, YM)
    assert boxed["result"]["数据"][0]["出库量"].endswith("盒")
    by_product = agent.run_query(
        STORE, {"metrics": ["shipment_qty"], "product_line": "仪器", "group_by": "product"}, YM
    )
    assert by_product["result"]["数据"]
    assert all(row["出库量"].endswith("台") for row in by_product["result"]["数据"])


def test_aging_metrics_filter_and_group_only_on_snapshot_buckets():
    scope = Scope(region_id="R-HD")
    amounts = aging_amounts(STORE, YM, scope)
    balance = ar_balance(STORE, YM, scope)
    one = agent.run_query(
        STORE,
        {"metrics": ["aging_amount", "aging_share"], "region": "华东", "aging_bucket": "90+"},
        YM,
    )
    row = _table(one)[0]
    assert _money(row["账龄金额"]) == amounts["90+"]
    assert row["账龄占比"] == pct_text(amounts["90+"] / balance)

    grouped = agent.run_query(STORE, {"metrics": ["aging_amount"], "region": "华东", "group_by": "aging_bucket"}, YM)
    rows = _table(grouped)
    assert {row["范围"] for row in rows} == set(BUCKETS)
    assert sum((_money(row["账龄金额"]) for row in rows), Decimal("0")) == balance
    _prov_table(grouped)

    with pytest.raises(agent.ToolError, match="账龄"):
        agent.run_query(STORE, {"metrics": ["net_sales"], "group_by": "aging_bucket"}, YM)
    with pytest.raises(agent.ToolError, match="账龄"):
        agent.run_query(STORE, {"metrics": ["collection_amount"], "aging_bucket": "1–30"}, YM)


def test_cohort_filter_and_group_follow_first_shipment():
    parent = Scope(region_id="R-XB")
    fresh = net_sales(STORE, YM, parent.narrow(cohort="new"))
    aged = net_sales(STORE, YM, parent.narrow(cohort="old"))
    assert fresh > 0 and aged > 0
    assert fresh + aged <= net_sales(STORE, YM, parent)

    filtered = agent.run_query(STORE, {"metrics": ["net_sales", "collection_rate"], "region": "西北", "cohort": "new"}, YM)
    assert _money(_table(filtered)[0]["净销售额"]) == fresh
    assert "新代理商" in filtered["title"]

    grouped = agent.run_query(STORE, {"metrics": ["collection_rate"], "region": "西北", "group_by": "cohort"}, YM)
    assert {row["范围"] for row in _table(grouped)} == {"新代理商", "老代理商"}
    _prov_table(grouped)


def test_headcount_and_concentration_are_distinct_from_customer_count():
    scope = Scope(region_id="R-HN")
    active = agent.run_query(STORE, {"metrics": ["active_agent_count"], "region": "华南"}, YM)
    collecting = agent.run_query(STORE, {"metrics": ["collecting_agent_count"], "region": "华南"}, YM)
    top5 = agent.run_query(STORE, {"metrics": ["top5_ar_concentration"], "region": "华南"}, YM)

    active_ids = set()
    collecting_ids = set()
    for customer in STORE.customers.values():
        if customer["region_id"] != "R-HN":
            continue
        child = Scope(region_id="R-HN", customer_id=customer["customer_id"])
        if shipments(STORE, YM, child) or ar_balance(STORE, YM, child) != 0:
            active_ids.add(customer["customer_id"])
        if collection_amount(STORE, YM, child) != 0:
            collecting_ids.add(customer["customer_id"])
    assert _table(active)[0]["活跃代理商数"] == f"{len(active_ids)}家"
    assert _table(collecting)[0]["回款代理商数"] == f"{len(collecting_ids)}家"

    balance = ar_balance(STORE, YM, scope)
    leaders = top_ar_customers(STORE, YM, scope, limit=5)
    expected = sum((row["outstanding"] for row in leaders), Decimal("0")) / balance
    assert _table(top5)[0]["前5家应收集中度"] == pct_text(expected)
    days = agent.run_query(STORE, {"metrics": ["avg_collection_days"], "region": "华南"}, YM)
    assert _table(days)[0]["平均回款天数"] == agent._text("days", avg_collection_days(STORE, YM, scope, 6))


def test_early_window_days_are_unsupported_not_invented():
    early = agent.run_query(STORE, {"metrics": ["dso", "avg_collection_days"], "end_month": "2025-11"}, "2025-11")
    row = _table(early)[0]
    assert row["DSO"] == "本数据无法支持"
    assert row["平均回款天数"] == "本数据无法支持"
    reply = agent.answer(STORE, "2025年11月的DSO", YM)
    assert reply["supported"] is True
    assert "本数据无法支持" in reply["chat_reply"]
    assert reply["charts"][-1]["provenance"]["formula"]


PARENTS = [
    {},
    {"region": "华东"},
    {"product_line": "试剂"},
    {"tier": "A"},
    {"cohort": "new"},
    {"product": "肝功试剂"},
    {"region": "华南", "product_line": "仪器"},
]


def _relation_dimension(operation: str, parent: dict) -> str | None:
    if operation != "cross_contribute":
        return None
    if "region" not in parent:
        return "region"
    if "product_line" not in parent and "product" not in parent:
        return "product_line"
    if "tier" not in parent:
        return "tier"
    return "cohort"


@pytest.mark.parametrize("parent", PARENTS)
@pytest.mark.parametrize(
    "operation",
    [
        "rate_decompose",
        "cross_contribute",
        "agent_contribute",
        "aging_migration",
        "ar_bridge",
        "month_timing",
        "fastest_decline",
    ],
)
def test_relations_answer_any_legal_parent_with_a_table(parent, operation):
    args = {"operation": operation, "end_month": YM, **parent}
    dimension = _relation_dimension(operation, parent)
    if dimension:
        args["dimension"] = dimension
    result = agent.run_relation(STORE, args, YM)
    table = _prov_table(result)
    assert table["rows"]
    blob = json.dumps(result["result"], ensure_ascii=False)
    assert "经销商" not in blob
    if operation == "ar_bridge":
        assert "退货" not in blob and "坏账" not in blob


def test_rate_decompose_and_cross_contribution_identities():
    result = agent.run_relation(STORE, {"operation": "rate_decompose", "region": "华东"}, YM)
    row = _table(result)[0]
    scope = Scope(region_id="R-HD")
    parts = decompose(
        collection_amount(STORE, PREV, scope),
        net_sales(STORE, PREV, scope),
        collection_amount(STORE, YM, scope),
        net_sales(STORE, YM, scope),
    )
    assert row["回款额变化贡献"] == pp_text(parts["collection_effect"])
    assert row["净销售额变化贡献"] == pp_text(parts["sales_effect"])
    assert row["销售回款率环比"] == pp_text(parts["delta"])

    cross = agent.run_relation(
        STORE, {"operation": "cross_contribute", "region": "华东", "dimension": "product_line"}, YM
    )
    expected = contributions(STORE, YM, PREV, Scope(region_id="R-HD"), "product_line")
    assert [item["子切片"] for item in _table(cross)] == [item["name"] for item in expected]
    assert [item["贡献"] for item in _table(cross)] == [pp_text(item["contrib"]) for item in expected]

    early = agent.run_relation(STORE, {"operation": "rate_decompose", "end_month": "2025-11"}, "2025-11")
    assert "本数据无法支持" in json.dumps(early["result"], ensure_ascii=False)
    reply = agent.answer(STORE, "2025年11月华东销售回款率的率变分解", YM)
    assert reply["supported"] is True
    assert "本数据无法支持" in reply["chat_reply"]


def test_fastest_decline_keeps_the_five_percent_gate_but_direct_lookup_does_not():
    company = agent.run_relation(STORE, {"operation": "fastest_decline"}, YM)
    rows = _table(company)
    winners = [row for row in rows if row["下降最快"] == "是"]
    assert winners
    assert all(row["参评"] == "是" for row in winners)
    for row in rows:
        share = Decimal(_plain(row["净销售占比"])) / Decimal(100)
        if row["参评"] == "是":
            assert share >= SHARE_MIN
        if row["参评"] == "否" and row["净销售占比"] != "本数据无法支持":
            assert share < SHARE_MIN
    assert any("大区视角" == row["视角"] for row in rows)
    assert any("产品视角" == row["视角"] for row in rows)

    small = None
    for product in STORE.products.values():
        scope = Scope(product_category=product["product_category"])
        share = net_sales(STORE, YM, scope) / net_sales(STORE, YM, Scope())
        if 0 < share < SHARE_MIN:
            small = product["product_category"]
            break
    assert small
    direct = agent.run_query(STORE, {"metrics": ["collection_rate"], "product": small}, YM)
    assert _table(direct)[0]["销售回款率"] != "本数据无法支持"
    focused = agent.run_relation(STORE, {"operation": "fastest_decline", "product_line": "试剂"}, YM)
    assert _table(focused)
    assert all(row["视角"] == "大区视角" for row in _table(focused))


def test_questions_outside_the_fourteen_still_map_when_they_are_on_the_map():
    by_product = agent.answer(STORE, "按产品类看销售回款率", YM)
    assert by_product["supported"] is True
    assert by_product["question_id"] == "free_query"
    table = next(chart for chart in by_product["charts"] if chart["type"] == "table")
    assert len(table["rows"]) >= 10
    assert table["provenance"]["formula"]
    assert by_product["conclusion"]["sentences"][0]["provenance"]["rows"]
    assert "肝功试剂" in {row["范围"] for row in table["rows"]}

    cohort = agent.answer(STORE, "华东新代理商的净销售额", YM)
    assert cohort["supported"] is True
    assert "新代理商" in cohort["chat_reply"] or "新代理商" in json.dumps(cohort["charts"], ensure_ascii=False)
    assert "经销商" not in cohort["chat_reply"]

    qty = agent.answer(STORE, "仪器出库量是多少", YM)
    assert qty["supported"] is True
    assert qty["charts"][-1]["rows"][0]["出库量"].endswith("台")

    aging = agent.answer(STORE, "华东90+的账龄金额", YM)
    assert aging["supported"] is True
    assert "90+" in json.dumps(aging["charts"], ensure_ascii=False)


def test_refusals_are_typed_and_province_drill_is_refused():
    cases = [
        ("承诺回款为什么没兑现？", "缺指标", "承诺回款"),
        ("毛利是多少", "缺指标", "毛利"),
        ("按省区下钻华东的回款率", "缺维度", "省区"),
        ("江苏省的净销售额", "缺维度", "省"),
        ("各省销售回款率", "缺维度", "省"),
        ("按医院拆回款", "缺关联", "医院"),
        ("按承诺拆回款", "缺关联", "承诺"),
        ("经销商回款率是多少？", "缺维度", "经销商"),
        ("直营医院和代理商比怎么样？", "缺维度", "直营"),
        ("2024年10月的净销售额", "缺时间窗", "2025年11月"),
        ("公司出库量合计是多少", "非法组合", "出库量"),
        ("按账龄桶看净销售额", "非法组合", "账龄"),
        ("华中大区的回款率", "缺维度", "华东"),
        ("今天天气怎么样", "无法映射", "销售回款率"),
    ]
    for text, kind, token in cases:
        reply = agent.answer(STORE, text, YM)
        assert reply["supported"] is False, text
        assert reply["refusal_type"] == kind, text
        assert reply["chat_reply"].startswith("暂不支持"), text
        assert kind in reply["chat_reply"], text
        assert token in reply["chat_reply"], text
        assert reply["charts"] == []
        assert "冲量出货抬高分母" not in reply["chat_reply"]


def test_preset_main_report_and_static_version_stay_put():
    from app.constants import MAIN_QUESTION

    reply = agent.answer(STORE, MAIN_QUESTION, YM)
    assert reply["question_id"] == "p0_main"
    assert reply["resolved_view"] == "region"
    assert "华东大区" in reply["conclusion"]["text"]
    assert "可切换产品视角" not in reply["conclusion"]["text"]
    assert "东华" not in reply["conclusion"]["text"]
    assert reply["view_switch"] is None
    html = (Path(__file__).resolve().parents[1] / "app" / "static" / "index.html").read_text(encoding="utf-8")
    versions = [int(number) for number in re.findall(r"app\.(?:js|css)\?v=(\d+)", html)]
    assert versions
    assert min(versions) >= 18
    assert "app.js?v=19" in html
    assert "app.css?v=19" in html
