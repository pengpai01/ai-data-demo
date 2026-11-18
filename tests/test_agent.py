"""Free-form path: tool results, keyword fallback, and the number guard. No network."""

import json
from decimal import Decimal

from app import agent
from app.metrics import Scope, net_sales
from app.store import load_store

STORE = load_store()


def _tool_call(name, args, call_id="c1"):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}},
    ]}


def _scripted(monkeypatch, replies):
    queue = list(replies)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    monkeypatch.setattr(agent, "_call_model", lambda messages: queue.pop(0))


def test_each_line_has_distinct_products_and_can_be_grouped():
    by_line: dict[str, set[str]] = {}
    for row in STORE.products.values():
        by_line.setdefault(row["product_line"], set()).add(row["product_category"])
    assert set(by_line) == {"试剂", "仪器", "服务"}
    assert all(len(names) >= 10 for names in by_line.values())
    result = agent.run_query(STORE, {"metrics": ["net_sales"], "product_line": "试剂", "group_by": "product"}, "2026-10")
    assert {row["范围"] for row in result["result"]["数据"]} == by_line["试剂"]
    one = agent.run_query(STORE, {"metrics": ["net_sales"], "product": "肝功试剂"}, "2026-10")
    assert one["title"] == "肝功试剂"


def test_sku_rows_for_one_agent_sort_by_either_amount():
    def amounts(metric):
        result = agent.run_query(
            STORE,
            {"metrics": [metric, "collection_rate"], "customer": "锦程华东代理商", "group_by": "product"},
            "2026-10",
        )
        label = "净销售额" if metric == "net_sales" else "回款额"
        values = []
        for row in result["result"]["数据"]:
            text = row[label].replace(",", "").removesuffix("元")
            values.append(Decimal("-1") if text == "—" else Decimal(text))
        return values

    for metric in ("net_sales", "collection_amount"):
        values = amounts(metric)
        assert values == sorted(values, reverse=True)
        assert values[0] > 0


def test_ranked_agent_uses_the_row_month_when_drilling():
    result = agent.run_query(STORE, {"metrics": ["collection_amount", "net_sales"], "group_by": "customer", "top_n": 5}, "2026-10")
    row = result["result"]["数据"][0]
    assert row["_客户"].endswith("代理商")
    assert "（" not in row["_客户"]
    reply = agent.answer(STORE, f"按SKU展示{row['_客户']}在2026年10月的销售和回款", "2026-09")
    assert reply["question_id"] == "customer_sku"
    assert reply["period"] == "2026-10"
    table = next(chart for chart in reply["charts"] if chart["type"] == "table")
    assert table["columns"][0]["label"] == "产品"


def test_customer_drop_is_a_table_of_the_largest_net_sales_decline():
    result = agent.run_query(
        STORE,
        {"metrics": ["net_sales"], "group_by": "customer", "order": "drop", "start_month": "2026-09", "end_month": "2026-10", "top_n": 3},
        "2026-10",
    )
    rows = result["result"]["数据"]
    assert [row["排名"] for row in rows] == ["1", "2", "3"]
    assert rows[0]["期初"] == "2026年9月" and rows[0]["期末"] == "2026年10月"

    def yuan(text):
        return Decimal(text.replace(",", "").replace("+", "").removesuffix("元"))

    changes = [yuan(row["净销售额变动"]) for row in rows]
    assert changes == sorted(changes)
    assert changes[0] < 0
    for row in rows:
        assert yuan(row["净销售额变动"]) == yuan(row["期末净销售额"]) - yuan(row["期初净销售额"])

    worst_name = None
    worst = None
    worst_id = ""
    for customer in STORE.customers.values():
        scope = Scope(customer_id=customer["customer_id"])
        delta = net_sales(STORE, "2026-10", scope) - net_sales(STORE, "2026-09", scope)
        if worst is None or (delta, customer["customer_id"]) < (worst, worst_id):
            worst, worst_id, worst_name = delta, customer["customer_id"], customer["customer_name"]
    assert rows[0]["_客户"] == worst_name
    assert [chart["type"] for chart in agent._charts(result)] == ["table"]

    level = agent.run_query(STORE, {"metrics": ["net_sales"], "group_by": "customer", "top_n": 5}, "2026-10")
    assert [chart["type"] for chart in agent._charts(level)] == ["table"]


def test_half_year_agent_ranking_uses_the_window_total_and_only_a_table():
    result = agent.run_query(
        STORE,
        {"metrics": ["net_sales"], "region": "华北", "group_by": "customer", "start_month": "2026-05", "end_month": "2026-10", "top_n": 3},
        "2026-10",
    )
    rows = result["result"]["数据"]
    assert [row["排名"] for row in rows] == ["1", "2", "3"]
    assert rows[0]["期初"] == "2026年5月" and rows[0]["期末"] == "2026年10月"
    assert "月份" not in rows[0]

    def yuan(text):
        return Decimal(text.replace(",", "").removesuffix("元"))

    totals = [yuan(row["净销售额区间合计"]) for row in rows]
    assert totals == sorted(totals, reverse=True)
    months = ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]
    best_name = None
    best = None
    best_id = ""
    for customer in STORE.customers.values():
        if customer["region_id"] != "R-HB":
            continue
        scope = Scope(customer_id=customer["customer_id"])
        total = sum((net_sales(STORE, month, scope) for month in months), Decimal("0"))
        if best is None or (-total, customer["customer_id"]) < (-best, best_id):
            best, best_id, best_name = total, customer["customer_id"], customer["customer_name"]
    assert rows[0]["_客户"] == best_name
    assert yuan(rows[0]["净销售额区间合计"]) == best
    assert [chart["type"] for chart in agent._charts(result)] == ["table"]


def test_clicking_an_agent_opens_a_sku_breakdown():
    reply = agent.answer(STORE, "按SKU展示锦程华东代理商的销售和回款", "2026-10")
    assert reply["question_id"] == "customer_sku"
    table = next(chart for chart in reply["charts"] if chart["type"] == "table")
    assert [column["label"] for column in table["columns"]] == ["产品", "产品线", "净销售额", "出库额", "回款额", "销售回款率"]
    assert table["rows"]
    assert table["rows"][0]["产品线"] in {"试剂", "仪器", "服务"}
    nets = [Decimal(row["净销售额"].split("→")[-1].replace(",", "").removesuffix("元")) for row in table["rows"]]
    assert nets == sorted(nets, reverse=True)
    assert nets[0] > 0


def test_region_window_has_monthly_rows_changes_and_total():
    result = agent.run_query(STORE, {"metrics": ["net_sales", "collection_rate"], "start_month": "2026-05", "end_month": "2026-10", "region": "西北"}, "2026-10")
    rows = result["result"]["数据"]
    assert [row["月份"] for row in rows] == ["2026年5月", "2026年6月", "2026年7月", "2026年8月", "2026年9月", "2026年10月", "区间合计"]
    assert rows[0]["净销售额环比"] == "—"
    assert rows[-1]["净销售额"] == "95,852,706.00元"
    assert rows[-1]["销售回款率"] == "98.22%"
    assert rows[5]["销售回款率环比"] == "-0.09pp"


def test_group_ranking_is_sorted_by_first_metric():
    result = agent.run_query(STORE, {"metrics": ["collection_rate"], "group_by": "product_line"}, "2026-10")
    rows = result["result"]["数据"]
    assert [row["范围"] for row in rows] == ["仪器产品线", "服务产品线", "试剂产品线"]
    assert [row["排名"] for row in rows] == ["1", "2", "3"]


def test_single_customer_ignores_customer_grouping():
    result = agent.run_query(STORE, {"metrics": ["collection_amount"], "customer": "锦程华东代理商", "group_by": "customer", "start_month": "2026-05", "end_month": "2026-10"}, "2026-10")
    assert len(result["months"]) == 6
    assert result["title"] == "锦程华东代理商"


def test_agent_count_is_a_table_and_a_sentence_without_a_chart():
    result = agent.run_query(STORE, {"metrics": ["customer_count"], "group_by": "region"}, "2026-10")
    rows = result["result"]["数据"]
    assert [row["范围"] for row in rows] == ["华东大区", "华南大区", "华北大区", "西南大区", "西北大区", "合计"]
    assert [row["代理商数量"] for row in rows] == ["26家", "24家", "24家", "23家", "23家", "120家"]
    assert [chart["type"] for chart in agent._charts(result)] == ["table"]

    reply = agent.answer(STORE, "公司有多少家代理商", "2026-10")
    assert reply["kpis"] == []
    assert [chart["type"] for chart in reply["charts"]] == ["table"]
    assert reply["compare_label"] == ""
    assert "共120家" in reply["chat_reply"]
    assert reply["conclusion"]["sentences"][0]["text"] == reply["chat_reply"]
    assert "净销售额" not in reply["chat_reply"]


def test_agents_per_region_is_a_count_table():
    reply = agent.answer(STORE, "每个大区有多少家代理商", "2026-10")
    assert reply["supported"] is True
    assert "暂不支持" not in reply["chat_reply"]
    assert "华东大区26家" in reply["chat_reply"]
    assert "共120家" in reply["chat_reply"]
    assert [chart["type"] for chart in reply["charts"]] == ["table"]
    _assert_sheet_provenance(reply)
    assert reply["charts"][0]["rows"][-1] == {"范围": "合计", "月份": "2026年10月", "代理商数量": "120家"}

    by_line = agent.answer(STORE, "每个产品线有多少家代理商", "2026-10")
    assert [row["范围"] for row in by_line["charts"][0]["rows"]] == ["试剂产品线", "仪器产品线", "服务产品线"]
    assert "暂不支持" not in by_line["chat_reply"]



def _assert_sheet_provenance(reply):
    assert reply["page"] == "sheet"
    sentence = reply["conclusion"]["sentences"][0]
    assert sentence["provenance"]["formula"]
    assert sentence["provenance"]["filters"]
    assert sentence["provenance"]["rows"]
    for chart in reply["charts"]:
        assert chart["type"] == "table"
        assert chart["provenance"]["formula"]
        assert chart["provenance"]["filters"]
        assert chart["provenance"]["rows"]


def test_agent_roster_is_names_by_region_without_rank_or_chart():
    reply = agent.answer(STORE, "公司每个大区的代理商的列表", "2026-10")
    assert reply["supported"] is True
    assert reply["kpis"] == []
    assert reply["compare_label"] == ""
    assert reply["page"] == "sheet"
    assert reply["page_title"] == "代理商名单"
    _assert_sheet_provenance(reply)
    assert "各大大区" not in reply["chat_reply"]
    assert "各大区代理商共120家" in reply["chat_reply"]
    assert reply["period_label"] == "代理商名录"
    assert [chart["type"] for chart in reply["charts"]] == ["table"]
    table = reply["charts"][0]
    assert [column["label"] for column in table["columns"]] == ["大区", "代理商", "等级"]
    assert len(table["rows"]) == 120
    assert "排名" not in table["rows"][0]
    assert "净销售额" not in table["rows"][0]
    order = ["华东大区", "华南大区", "华北大区", "西南大区", "西北大区"]
    names = [row["大区"] for row in table["rows"]]
    assert names == sorted(names, key=order.index)
    assert names.count("华东大区") == 26
    assert "共120家" in reply["chat_reply"]
    assert "排名" not in reply["chat_reply"]

    blocked = agent.answer(STORE, "华东主要是哪些代理商在拖累？", "2026-10")
    assert blocked["question_id"] == "follow_agents"


def test_each_agent_sales_by_region_is_the_full_october_table():
    reply = agent.answer(STORE, "显示一下全国各大区下各代理商10月的销售情况", "2026-10")
    assert reply["page"] == "sheet"
    assert reply["page_title"] == "2026年10月各代理商销售"
    _assert_sheet_provenance(reply)
    assert "各大大区" not in reply["chat_reply"]
    assert "前20" not in reply["chat_reply"]
    assert "前 20" not in reply["chat_reply"]
    table = reply["charts"][0]
    assert [column["label"] for column in table["columns"]] == ["大区", "代理商", "净销售额", "出库额", "回款额", "销售回款率"]
    assert len(table["rows"]) == 120
    assert "排名" not in table["rows"][0]
    east = [row for row in table["rows"] if row["大区"] == "华东大区"]
    assert len(east) == 26
    assert east[0]["代理商"] == "锦程华东代理商"
    assert east[0]["净销售额"] == "5,236,560.00元"
    assert east[0]["销售回款率"] == "49.57%"
    shantou = next(row for row in table["rows"] if row["代理商"] == "潮汕华南代理商")
    assert shantou["销售回款率"] == "131.98%"
    assert agent.try_region_agent_sales(STORE, "净销售额最高的代理商", "2026-10", "auto") is None


def test_without_key_the_keyword_router_answers():
    reply = agent.answer(STORE, "华东主要是哪些代理商在拖累？", "2026-10")
    assert reply["question_id"] == "follow_agents"


def test_hard_block_runs_before_the_model(monkeypatch):
    _scripted(monkeypatch, [])
    reply = agent.answer(STORE, "华东经销商回款", "2026-10")
    assert reply["supported"] is False
    assert "经销商" in reply["chat_reply"]


def test_model_can_open_an_existing_report(monkeypatch):
    _scripted(monkeypatch, [_tool_call("open_report", {"analysis": "agents", "region": "华东"})])
    reply = agent.answer(STORE, "华东哪几家拖后腿", "2026-10")
    assert reply["question_id"] == "follow_agents"
    assert reply["filters"]["region_id"] == "R-HD"


def test_reply_with_traceable_numbers_is_kept(monkeypatch):
    args = {"metrics": ["net_sales"], "start_month": "2026-05", "end_month": "2026-10", "region": "西北"}
    _scripted(monkeypatch, [_tool_call("query_metrics", args), {"role": "assistant", "content": "西北大区区间合计净销售额 95,852,706.00元。"}])
    reply = agent.answer(STORE, "展示西北大区近半年的销售数据", "2026-10")
    assert reply["question_id"] == "free_query"
    assert reply["page"] == "sheet"
    _assert_sheet_provenance(reply)
    assert reply["kpis"] == []
    assert reply["chat_reply"] == "西北大区区间合计净销售额 95,852,706.00元。"
    assert reply["conclusion"]["sentences"][0]["text"] == reply["chat_reply"]
    assert [chart["type"] for chart in reply["charts"]] == ["table"]
    assert reply["period_label"] == "2026年5月至2026年10月"


def test_reply_with_invented_numbers_is_dropped(monkeypatch):
    args = {"metrics": ["net_sales"], "region": "西北"}
    _scripted(monkeypatch, [_tool_call("query_metrics", args), {"role": "assistant", "content": "西北大区净销售额约 1600 万元。"}])
    reply = agent.answer(STORE, "西北这个月卖了多少", "2026-10")
    assert "1600" not in reply["chat_reply"]
    assert "已不展示" in reply["chat_reply"]
    assert reply["charts"][-1]["type"] == "table"

def test_question_numbers_are_not_treated_as_cited(monkeypatch):
    args = {"metrics": ["net_sales"], "region": "西北"}
    _scripted(monkeypatch, [_tool_call("query_metrics", args), {"role": "assistant", "content": "约 1600 万元"}])
    reply = agent.answer(STORE, "是不是卖了 1600 万", "2026-10")
    assert "1600" not in reply["chat_reply"]
    assert "已不展示" in reply["chat_reply"]
    assert reply["charts"][-1]["type"] == "table"


def test_model_reject_is_unsupported(monkeypatch):
    _scripted(monkeypatch, [_tool_call("reject", {"reason": "没有预测数据。"})])
    reply = agent.answer(STORE, "明年华东能卖多少", "2026-10")
    assert reply["supported"] is False
    assert reply["chat_reply"] == "暂不支持。没有预测数据。"
