"""Main-report bar click changes the attribution parent. v1.5 acceptance."""

import json
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from app.constants import MAIN_QUESTION
from app.metrics import (
    Scope,
    collection_amount,
    contributions,
    customer_contribs,
    decompose,
    net_sales,
    rate_slices,
)
from app.reports import click_hint, resolve_chart_focus
from app.store import load_store
from app.textfmt import pct_text, pp_text

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(__import__("app.main", fromlist=["app"]).app)
STORE = load_store()
YM = "2026-10"
PREV = "2026-09"
REGIONS = ["华东", "华南", "华北", "西南", "西北"]
LINES = ["试剂", "仪器", "服务"]


def _ask(focus=None, view="auto"):
    payload = {"question": MAIN_QUESTION, "period": YM, "view": view}
    if focus is not None:
        payload["focus"] = focus
    return client.post("/api/ask", json=payload).json()


def _rate(label, kpis):
    return next(item for item in kpis if item["label"] == label)


def _expected_parts(scope):
    return decompose(
        collection_amount(STORE, PREV, scope),
        net_sales(STORE, PREV, scope),
        collection_amount(STORE, YM, scope),
        net_sales(STORE, YM, scope),
    )


def test_default_lands_on_the_faster_region_and_cross_splits_inside_it():
    body = _ask()
    assert body["question"] == MAIN_QUESTION
    assert body["question_id"] == "p0_main"
    assert body["view_switch"] is None
    assert body["resolved_view"] == "region"
    assert body["attribution"]["title"] == "归因-华东大区"
    assert body["attribution"]["cross_title"] == "按产品线拆贡献"
    assert [row["name"] for row in body["attribution"]["cross_rows"]] == LINES
    assert body["attribution_target"]["label"] == "华东大区"
    assert body["attribution_target"]["is_default"] is True
    assert body["attribution_target"]["dimension"] == "region"
    assert "下降最快：华东大区" in body["conclusion"]["text"]
    assert "可切换产品视角" not in body["conclusion"]["text"]
    assert "可点击右侧产品线柱查看其归因" in body["conclusion"]["text"]
    assert "试剂产品线下降 -8.88pp" in body["conclusion"]["text"]
    assert "当前查看" not in body["conclusion"]["text"]
    assert "东华" not in body["conclusion"]["text"]
    assert "经销商" not in json.dumps(body, ensure_ascii=False)
    assert body["charts"][0]["categories"] == REGIONS
    assert body["charts"][1]["categories"] == LINES
    assert body["charts"][0]["highlight"] == ["华东"]
    assert body["charts"][1]["highlight"] == []
    assert body["charts"][0]["drill"] == "attribution"
    assert body["charts"][1]["drill"] == "attribution"
    assert "省" not in "".join(body["charts"][0]["categories"] + body["charts"][1]["categories"])
    rate = _rate("销售回款率", body["kpis"])
    assert rate["value_text"] == "70.91%"
    assert rate["value_text"] != "92.32%"


def test_clicking_a_region_bar_moves_the_whole_attribution_block():
    for name, key in (("华南", "R-HN"), ("华北", "R-HB")):
        body = _ask({"dimension": "region", "key": key})
        scope = Scope(region_id=key)
        parts = _expected_parts(scope)
        agents = customer_contribs(STORE, YM, PREV, scope)
        assert body["question"] == MAIN_QUESTION
        assert body["question_id"] == "p0_main"
        assert body["attribution"]["title"] == f"归因-{name}大区"
        assert body["attribution"]["title"] != "归因-华东大区"
        assert body["attribution"]["cross_title"] == "按产品线拆贡献"
        assert [row["name"] for row in body["attribution"]["cross_rows"]] == LINES
        assert body["attribution"]["decomposition"]["collection_text"] == pp_text(parts["collection_effect"])
        assert body["attribution"]["decomposition"]["sales_text"] == pp_text(parts["sales_effect"])
        assert body["attribution"]["decomposition"]["collection_text"] != "-14.92pp"
        assert [row["name"] for row in body["attribution"]["agents"][:3]] == [row["customer_name"] for row in agents[:3]]
        assert {row["region"] for row in body["attribution"]["agents"]} == {name}
        assert body["attribution_target"] == {
            "dimension": "region",
            "key": key,
            "name": name,
            "label": f"{name}大区",
            "is_default": False,
        }
        assert body["period_label"] == "2026年10月"
        assert body["compare_label"] == "2026年9月"
        assert "下降最快：华东大区" in body["conclusion"]["text"]
        assert f"下降最快：{name}大区" not in body["conclusion"]["text"]
        assert f"当前查看：{name}大区" in body["conclusion"]["text"]
        assert body["charts"][0]["categories"] == REGIONS
        assert body["charts"][1]["categories"] == LINES
        assert body["charts"][0]["highlight"] == [name]
        assert body["charts"][1]["highlight"] == []
        assert _rate("销售回款率", body["kpis"])["value_text"] == pct_text(parts["rate1"])


def test_clicking_a_product_bar_cross_splits_by_region():
    for name in ("仪器", "服务"):
        body = _ask({"dimension": "product_line", "key": name})
        scope = Scope(product_line=name)
        parts = _expected_parts(scope)
        cross = contributions(STORE, YM, PREV, scope, "region")
        agents = customer_contribs(STORE, YM, PREV, scope)
        assert body["attribution"]["title"] == f"归因-{name}产品线"
        assert body["resolved_view"] == "product"
        assert body["attribution"]["cross_title"] == "按大区拆贡献"
        assert [row["name"] for row in body["attribution"]["cross_rows"]] == [row["name"] for row in cross]
        assert [row["name"] for row in body["attribution"]["cross_rows"]] == REGIONS
        assert body["attribution"]["decomposition"]["collection_text"] == pp_text(parts["collection_effect"])
        assert body["attribution"]["decomposition"]["sales_text"] == pp_text(parts["sales_effect"])
        assert [row["name"] for row in body["attribution"]["agents"][:3]] == [row["customer_name"] for row in agents[:3]]
        assert body["attribution_target"]["label"] == f"{name}产品线"
        assert body["attribution_target"]["is_default"] is False
        assert body["attribution_target"]["dimension"] == "product_line"
        assert "下降最快：华东大区" in body["conclusion"]["text"]
        assert f"当前查看：{name}产品线" in body["conclusion"]["text"]
        assert "下降最快：{0}产品线".format(name) not in body["conclusion"]["text"]
        assert body["charts"][0]["categories"] == REGIONS
        assert body["charts"][1]["categories"] == LINES
        assert body["charts"][0]["highlight"] == []
        assert body["charts"][1]["highlight"] == [name]
        assert len(body["charts"][0]["categories"]) == 5
        assert len(body["charts"][1]["categories"]) == 3
        assert _rate("销售回款率", body["kpis"])["value_text"] == pct_text(parts["rate1"])
    reagent = _ask({"dimension": "product_line", "key": "试剂"})
    assert "区域性集中拖累" in reagent["attribution"]["signals"]
    # Last click replaces the parent. A region lock does not stay beside a product lock.
    assert reagent["attribution_target"]["dimension"] == "product_line"
    assert reagent["attribution"]["title"] != "归因-华东大区"


def test_share_below_five_percent_can_still_be_the_attribution_parent(monkeypatch):
    real = rate_slices

    def wrapped(store, ym, compare, dimension, parent=None):
        rows = real(store, ym, compare, dimension, parent)
        if dimension == "region":
            for row in rows:
                if row["key"] == "R-XB":
                    row["share"] = Decimal("0.04")
                    row["eligible"] = False
        return rows

    monkeypatch.setattr("app.reports.rate_slices", wrapped)
    body = _ask({"dimension": "region", "key": "R-XB"})
    assert "西北" in body["charts"][0]["categories"]
    assert "西北" in body["charts"][0]["muted"]
    assert body["charts"][0]["highlight"] == ["西北"]
    assert body["charts"][1]["highlight"] == []
    assert body["charts"][0]["categories"] == REGIONS
    assert body["attribution"]["title"] == "归因-西北大区"
    assert body["attribution"]["cross_title"] == "按产品线拆贡献"
    assert body["attribution"]["decomposition"]["collection_text"]
    assert body["attribution_target"]["is_default"] is False
    assert "当前查看：西北大区" in body["conclusion"]["text"]
    assert "下降最快：华东大区" in body["conclusion"]["text"]
    default = _ask()
    assert default["attribution"]["title"] == "归因-华东大区"
    assert "西北" in default["charts"][0]["muted"]
    assert default["charts"][0]["highlight"] == ["华东"]
    assert "当前查看" not in default["conclusion"]["text"]


def test_return_default_and_repeat_of_the_default_bar_stay_on_the_default():
    moved = _ask({"dimension": "region", "key": "R-HN"})
    assert moved["attribution_target"]["is_default"] is False
    restored = _ask(None)
    assert restored["attribution"]["title"] == "归因-华东大区"
    assert restored["attribution"]["cross_title"] == "按产品线拆贡献"
    assert restored["charts"][0]["highlight"] == ["华东"]
    assert restored["charts"][1]["highlight"] == []
    assert restored["attribution_target"]["is_default"] is True
    assert "当前查看" not in restored["conclusion"]["text"]
    assert _rate("销售回款率", restored["kpis"])["value_text"] == "70.91%"
    again = _ask({"dimension": "region", "key": "R-HD"})
    assert again["attribution"]["title"] == "归因-华东大区"
    assert again["attribution_target"]["is_default"] is True
    assert "当前查看" not in again["conclusion"]["text"]
    by_name = _ask({"dimension": "region", "name": "华南"})
    assert by_name["attribution"]["title"] == "归因-华南大区"


def test_focus_resolver_keeps_cross_split_and_ignores_the_five_percent_gate():
    regions = [
        {"key": "R-HD", "name": "华东", "eligible": True, "delta": Decimal("-0.20"), "share": Decimal("0.30")},
        {"key": "R-XB", "name": "西北", "eligible": False, "delta": Decimal("-0.50"), "share": Decimal("0.01")},
    ]
    lines = [
        {"key": "试剂", "name": "试剂", "eligible": True, "delta": Decimal("-0.05"), "share": Decimal("0.40")},
    ]
    default = resolve_chart_focus(regions, lines, None)
    assert default["dimension"] == "region"
    assert default["row"]["key"] == "R-HD"
    assert default["cross_dimension"] == "product_line"
    assert default["is_default"] is True
    small = resolve_chart_focus(regions, lines, {"dimension": "region", "key": "R-XB"})
    assert small["row"]["key"] == "R-XB"
    assert small["is_default"] is False
    assert small["cross_dimension"] == "product_line"
    product_faster = resolve_chart_focus(
        [{"key": "R-HD", "name": "华东", "eligible": True, "delta": Decimal("-0.02"), "share": Decimal("0.20")}],
        [{"key": "试剂", "name": "试剂", "eligible": True, "delta": Decimal("-0.18"), "share": Decimal("0.40")}],
        None,
    )
    assert product_faster["dimension"] == "product_line"
    assert product_faster["cross_dimension"] == "region"
    assert product_faster["is_default"] is True
    tie = resolve_chart_focus(
        [{"key": "R-HD", "name": "华东", "eligible": True, "delta": Decimal("-0.10"), "share": Decimal("0.20")}],
        [{"key": "试剂", "name": "试剂", "eligible": True, "delta": Decimal("-0.10"), "share": Decimal("0.40")}],
        None,
    )
    assert tie["dimension"] == "region"
    assert click_hint("product_line", {"name": "试剂", "delta": Decimal("-0.0888")}) == (
        "同期试剂产品线下降 -8.88pp，可点击右侧产品线柱查看其归因。"
    )
    assert "可点击左侧大区柱查看其归因" in click_hint("region", {"name": "华东", "delta": Decimal("-0.2414")})
    risen = click_hint("region", {"name": "华南", "delta": Decimal("0.02")})
    assert risen.startswith("同期华南大区变化 +")
    assert "下降" not in risen
    assert click_hint("region", None) == ""


def test_page_drops_the_view_switch_and_can_return_to_the_default_bar():
    script = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    assert "view_switch" not in script
    assert "大区视角" not in script
    assert "产品视角" not in script
    assert "可切换产品视角" not in script + html
    assert "当前归因：" in script
    assert "返回默认" in script
    assert 'drill === "attribution"' in script
    assert "focus: same ? null : { dimension, key }" in script
    assert "app.js?v=19" in html
    assert "app.css?v=19" in html
