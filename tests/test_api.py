"""API and question-routing checks for the four P0 questions and the P1 list."""

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.constants import MAIN_QUESTION
from app.narrative import unknown_numbers, write_narrative
from app.router import QUESTIONS, classify

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(__import__("app.main", fromlist=["app"]).app)

P0 = [item for item in QUESTIONS if item["priority"] == "P0"]
P1 = [item for item in QUESTIONS if item["priority"] == "P1"]


def test_classifier_covers_the_prototype_questions():
    expected = {
        MAIN_QUESTION: "main",
        "近 6 个月各大区销售回款率怎么走？": "trend",
        "当前逾期应收主要集中在哪些代理商、哪些账龄段？": "overdue",
        "是出货冲量把分母抬高了，还是回款本身在变少？": "volume",
        "折扣/商务政策上升的产品线或大区，回款率是否同步变差？": "discount",
        "哪个大区 DSO 最差、最近两个月变化最大？": "dso",
        "TOP10 欠款代理商及账龄结构": "top_ar",
        "试剂、仪器、服务的回款周期差在哪里？": "cycle",
        "A / B / C 级代理商的回款率差多少？": "tier",
        "新代理商和老代理商回款率对比如何？": "new_old",
        "应收余额月环比主要由什么驱动？": "ar_bridge",
        "90 天以上账龄占比最近有没有恶化？": "aging90",
        "本月回款贡献最大的是哪些代理商、哪个大区？": "top_collection",
        "有没有「出货在增长、回款没跟上」的产品线？": "growth_lag",
        "华东主要是哪些代理商在拖累？": "agents",
        "试剂线是出货冲量还是回款少了？": "volume",
        "只看华东": "slice_focus",
        "只看试剂": "slice_focus",
        "只看 A 级代理商": "slice_focus",
        "承诺回款为什么没兑现？": "unsupported",
        "经销商回款率是多少？": "unsupported",
        "直营医院和代理商比怎么样？": "unsupported",
    }
    for text, intent in expected.items():
        assert classify(text).intent == intent
    huadong = classify("华东主要是哪些代理商在拖累？")
    assert huadong.region_id == "R-HD"
    reagent = classify("试剂线是出货冲量还是回款少了？")
    assert reagent.product_line == "试剂"
    tier = classify("只看 A 级代理商")
    assert tier.tier == "A"


def test_page_shell_and_auto_question_wiring():
    page = client.get("/")
    assert page.status_code == 200
    assert "星澜诊断 · 经营分析助手" in page.text
    script = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "/api/meta" in script
    assert "/api/ask" in script
    assert "meta.main_question" in script
    sheet = script[script.index("function renderSheet"):script.index("function renderAttribution")]
    assert "sourceButton" in sheet
    assert "sentence.provenance" in sheet
    assert "chart.provenance" in sheet or "renderChart" in sheet
    static = script + (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    assert "DEEPSEEK" not in static
    assert re.search(r"sk-[A-Za-z0-9]{8,}", static) is None


def test_main_question_report_shape_and_view_switch():
    response = client.post("/api/ask", json={"question": MAIN_QUESTION, "period": "2026-10", "view": "auto"})
    body = response.json()
    assert body["supported"] is True
    assert body["question_id"] == "p0_main"
    assert body["resolved_view"] == "region"
    assert "华东大区" in body["conclusion"]["text"]
    assert "可切换产品视角" in body["conclusion"]["text"]
    assert body["attribution"]["cross_title"] == "按产品线拆贡献"
    options = {item["label"]: item for item in body["view_switch"]["options"]}
    assert options["大区视角"]["id"] == "region"
    assert options["产品视角"]["id"] == "product"
    assert body["view_switch"]["current"] == "region"
    selected = next(item for item in body["view_switch"]["options"] if item["id"] == body["view_switch"]["current"])
    assert selected["label"] == "大区视角"
    assert "-24.14pp" in body["conclusion"]["text"]
    assert body["conclusion"]["sentences"][0]["provenance"]["formula"]
    assert body["conclusion"]["sentences"][0]["provenance"]["filters"]
    assert body["conclusion"]["sentences"][0]["provenance"]["rows"]
    labels = [kpi["label"] for kpi in body["kpis"]]
    assert labels == ["回款额", "销售回款率", "净销售额", "出库额", "应收余额", "逾期率", "DSO", "到期回款率"]
    assert body["kpis"][-1]["auxiliary"] is True
    assert len(body["charts"]) == 2
    for chart in body["charts"]:
        assert chart["provenance"]["formula"]
        assert chart["provenance"]["filters"]
        assert chart["provenance"]["rows"]
    assert body["attribution"]["decomposition"]["collection_text"] == "-14.92pp"
    assert body["attribution"]["decomposition"]["sales_text"] == "-9.22pp"
    assert body["attribution"]["agents"][0]["name"] == "锦程华东代理商"
    assert "冲量出货抬高分母" in body["attribution"]["signals"]
    assert "大代理商延期" in body["attribution"]["signals"]
    assert body["attribution"]["narrative"]["available"] is False
    assert "未配置" in body["attribution"]["narrative"]["message"]
    product = client.post("/api/ask", json={"question": MAIN_QUESTION, "period": "2026-10", "view": "product"}).json()
    assert product["resolved_view"] == "product"
    assert "试剂产品线" in product["conclusion"]["text"]
    assert "可切换大区视角" in product["conclusion"]["text"]
    assert product["attribution"]["cross_title"] == "按大区拆贡献"
    assert "区域性集中拖累" in product["attribution"]["signals"]
    region = client.post("/api/ask", json={"question": MAIN_QUESTION, "period": "2026-10", "view": "region"}).json()
    assert region["resolved_view"] == "region"


def test_four_p0_questions_return_reports():
    for item in P0:
        body = client.post("/api/ask", json={"question": item["text"], "period": "2026-10"}).json()
        assert body["supported"] is True
        assert body["question_id"] == item["id"]
        assert body["conclusion"]["text"]
        assert body["kpis"]
        assert body["charts"]
        for chart in body["charts"]:
            assert chart["provenance"]["rows"]


def test_p1_questions_are_supported():
    for item in P1:
        body = client.post("/api/ask", json={"question": item["text"], "period": "2026-10"}).json()
        assert body["supported"] is True, item["text"]
        assert body["question_id"] == item["id"]
        assert body["charts"]
        assert body["conclusion"]["sentences"][0]["provenance"]["formula"]


def test_out_of_map_questions_do_not_invent_a_metric():
    for text in ("承诺回款为什么没兑现？", "经销商回款率是多少？", "直营和代理商哪个好？"):
        body = client.post("/api/ask", json={"question": text, "period": "2026-10"}).json()
        assert body["supported"] is False
        assert body["keep_report"] is True
        assert body["chat_reply"].startswith("暂不支持")
    discount = client.post(
        "/api/ask",
        json={"question": "折扣/商务政策上升的产品线或大区，回款率是否同步变差？", "period": "2026-10"},
    ).json()
    assert "折扣率 0.00pp" not in discount["conclusion"]["text"]
    assert "华东大区" in discount["conclusion"]["text"]


def test_followups_change_the_slice():
    body = client.post("/api/ask", json={"question": "只看华东", "period": "2026-10"}).json()
    assert body["question_id"] == "follow_slice"
    assert body["filters"]["region_name"] == "华东"
    agents = client.post("/api/ask", json={"question": "华东主要是哪些代理商在拖累？", "period": "2026-10"}).json()
    assert agents["question_id"] == "follow_agents"
    assert agents["attribution"]["agents"][0]["region"] == "华东"
    reagent = client.post("/api/ask", json={"question": "试剂线是出货冲量还是回款少了？", "period": "2026-10"}).json()
    assert reagent["filters"]["product_line"] == "试剂"
    assert "净销售额变化贡献" in reagent["conclusion"]["text"]


def test_narrative_hides_numbers_that_were_not_computed(monkeypatch):
    fact = "销售回款率环比 -27.28pp。回款额变化贡献 -15.83pp。"
    assert unknown_numbers("环比 -27.28pp，贡献 -15.83pp。", fact) == []
    assert unknown_numbers("大约下降 8.60pp。", fact) == ["8.60"]

    class Fake:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": "华东下降了 8.60pp。第二句。第三句。第四句。"}}]}
            ).encode()

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-not-real")
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: Fake())
    result = write_narrative(fact, ["冲量出货抬高分母"])
    assert result["available"] is False
    assert "无法回引" in result["message"]


def test_self_test_doc_quotes_the_live_figures():
    doc = (ROOT / "docs" / "self-test-p0.md").read_text(encoding="utf-8")
    for token in ("-24.14pp", "-14.92pp", "-9.22pp", "锦程华东代理商", "苏澜华东代理商", "浙澜华东代理商", "通过"):
        assert token in doc
