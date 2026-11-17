"""Free-form path: tool results, keyword fallback, and the number guard. No network."""

import json
from decimal import Decimal

from app import agent
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
    assert reply["conclusion"] is None
    assert reply["chat_reply"] == "西北大区区间合计净销售额 95,852,706.00元。"
    assert any(chart["type"] == "line" for chart in reply["charts"])
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
