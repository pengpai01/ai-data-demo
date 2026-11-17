"""Map a Chinese question onto the business map. Out-of-map questions stay unsupported."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.constants import DATA_END, DATA_START, DEFAULT_PERIOD, MAIN_QUESTION

REGION_NAMES = (
    ("华东", "R-HD"),
    ("华南", "R-HN"),
    ("华北", "R-HB"),
    ("西南", "R-XN"),
    ("西北", "R-XB"),
)
LINES = ("试剂", "仪器", "服务")

QUESTIONS = [
    {"id": "p0_main", "priority": "P0", "text": MAIN_QUESTION},
    {"id": "p0_trend", "priority": "P0", "text": "近 6 个月各大区销售回款率怎么走？"},
    {"id": "p0_overdue", "priority": "P0", "text": "当前逾期应收主要集中在哪些代理商、哪些账龄段？"},
    {"id": "p0_volume", "priority": "P0", "text": "是出货冲量把分母抬高了，还是回款本身在变少？"},
    {"id": "p1_discount", "priority": "P1", "text": "折扣/商务政策上升的产品线或大区，回款率是否同步变差？"},
    {"id": "p1_dso", "priority": "P1", "text": "哪个大区 DSO 最差、最近两个月变化最大？"},
    {"id": "p1_top_ar", "priority": "P1", "text": "TOP10 欠款代理商及账龄结构"},
    {"id": "p1_cycle", "priority": "P1", "text": "试剂、仪器、服务的回款周期差在哪里？"},
    {"id": "p1_tier", "priority": "P1", "text": "A / B / C 级代理商的回款率差多少？"},
    {"id": "p1_new_old", "priority": "P1", "text": "新代理商和老代理商回款率对比如何？"},
    {"id": "p1_ar_bridge", "priority": "P1", "text": "应收余额月环比主要由什么驱动？"},
    {"id": "p1_aging90", "priority": "P1", "text": "90 天以上账龄占比最近有没有恶化？"},
    {"id": "p1_top_collection", "priority": "P1", "text": "本月回款贡献最大的是哪些代理商、哪个大区？"},
    {"id": "p1_growth_lag", "priority": "P1", "text": "有没有「出货在增长、回款没跟上」的产品线？"},
    {"id": "follow_agents", "priority": "追问", "text": "华东主要是哪些代理商在拖累？"},
    {"id": "follow_volume", "priority": "追问", "text": "试剂线是出货冲量还是回款少了？"},
]

INTENT_ID = {
    "main": "p0_main",
    "trend": "p0_trend",
    "overdue": "p0_overdue",
    "volume": "p0_volume",
    "discount": "p1_discount",
    "dso": "p1_dso",
    "top_ar": "p1_top_ar",
    "cycle": "p1_cycle",
    "tier": "p1_tier",
    "new_old": "p1_new_old",
    "ar_bridge": "p1_ar_bridge",
    "aging90": "p1_aging90",
    "top_collection": "p1_top_collection",
    "growth_lag": "p1_growth_lag",
    "slice_focus": "follow_slice",
    "agents": "follow_agents",
    "unsupported": "unsupported",
}

UNSUPPORTED = (
    ("承诺回款", "暂不支持承诺回款。本演示没有承诺表，不能按承诺口径计算。"),
    ("经销商", "暂不支持。本演示销售对象全部是代理商，没有「经销商」口径。"),
    ("直营", "暂不支持直营对比。销售全部通过代理商，不设直营客户。"),
    ("客户类型", "暂不支持客户类型对比。客户全部是代理商，只按 A/B/C 等级区分。"),
    ("体检中心", "暂不支持。体检中心不是本演示的销售客户，不进入发货、回款或应收。"),
    ("医院", "暂不支持把医院当作销售客户。医院只可能是终端背景，不进入发货、回款或应收。"),
    ("退货", "暂不支持退货。本版不计算退货。"),
    ("坏账", "暂不支持坏账。本版不计算坏账核销。"),
    ("实盘", "暂不支持代理商实盘库存。冲量只对照公司侧出库和净销售。"),
    ("库存", "暂不支持代理商库存。冲量只对照公司侧出库和净销售。"),
)


@dataclass
class Query:
    intent: str
    question_id: str
    text: str
    period: str
    view: str
    region_id: str | None = None
    product_line: str | None = None
    tier: str | None = None
    message: str | None = None


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _has(compact: str, *words: str) -> bool:
    return any(word.lower() in compact for word in words)


def _period_in_text(text: str) -> str | None:
    match = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月", text)
    if match:
        return f"{match.group(1)}-{int(match.group(2)):02d}"
    match = re.search(r"(20\d{2})-(0[1-9]|1[0-2])", text)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    return None


PERIOD_BLOCK = "暂不支持该期间。演示数据的经营月份是 2025年11月 至 2026年10月。"


def resolve_period(text: str, period: str = DEFAULT_PERIOD) -> str:
    return _period_in_text(text.strip()) or period or DEFAULT_PERIOD


def hard_block(text: str, period: str) -> str | None:
    """Out-of-map words and out-of-range months are refused before any routing."""
    raw = text.strip()
    compact = _compact(raw)
    for word, message in UNSUPPORTED:
        if word in raw or word in compact:
            return message
    if period < DATA_START or period > DATA_END:
        return PERIOD_BLOCK
    return None


def classify(text: str, period: str = DEFAULT_PERIOD, view: str = "auto") -> Query:
    raw = text.strip()
    compact = _compact(raw)
    chosen = resolve_period(raw, period)
    if view not in {"auto", "region", "product"}:
        view = "auto"

    blocked = hard_block(raw, chosen)
    if blocked:
        return Query("unsupported", "unsupported", raw, chosen, view, message=blocked)

    regions = [rid for name, rid in REGION_NAMES if name in raw]
    region_id = regions[0] if len(regions) == 1 else None
    lines = [line for line in LINES if line in raw]
    product_line = lines[0] if len(lines) == 1 else None
    tiers = re.findall(r"([ABC])\s*级", raw)
    if re.search(r"A\s*/\s*B\s*/\s*C", raw, re.I):
        tiers = ["A", "B", "C"]
    tier = tiers[0] if len(tiers) == 1 else None

    if _has(compact, "折扣", "商务政策"):
        intent = "discount"
    elif _has(compact, "近6个月", "近六个月", "怎么走"):
        intent = "trend"
    elif "逾期" in compact:
        intent = "overdue"
    elif _has(compact, "冲量", "分母", "回款本身", "回款少"):
        intent = "volume"
    elif "dso" in compact:
        intent = "dso"
    elif _has(compact, "top10", "欠款"):
        intent = "top_ar"
    elif _has(compact, "回款周期", "周期差"):
        intent = "cycle"
    elif _has(compact, "新代理商", "老代理商"):
        intent = "new_old"
    elif "驱动" in compact and "应收" in compact:
        intent = "ar_bridge"
    elif "90" in compact and ("账龄" in compact or "天" in compact):
        intent = "aging90"
    elif "回款贡献最大" in compact or ("贡献最大" in compact and "回款" in compact):
        intent = "top_collection"
    elif _has(compact, "没跟上", "出货在增长"):
        intent = "growth_lag"
    elif len(tiers) >= 2 or ("级代理商" in compact and _has(compact, "差多少", "对比")):
        intent = "tier"
    elif _has(compact, "哪些代理商", "代理商在拖"):
        intent = "agents"
    elif _has(compact, "下降最快", "哪个大区", "哪类产品", "原因是什么"):
        intent = "main"
    elif region_id or product_line or tier:
        intent = "slice_focus"
    else:
        return Query(
            "unsupported",
            "unsupported",
            raw,
            chosen,
            view,
            message="暂不支持。这个问题超出当前业务地图（销售回款率、大区、产品线、代理商等级、账龄）。",
        )

    return Query(
        intent,
        INTENT_ID[intent],
        raw,
        chosen,
        view,
        region_id=region_id,
        product_line=product_line,
        tier=tier,
    )
