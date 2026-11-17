"""Locked demo constants from prototype v1.3. Do not relax the 口径."""

from decimal import Decimal

DATA_START = "2025-11"
DATA_END = "2026-10"
DEFAULT_PERIOD = "2026-10"
SHARE_MIN = Decimal("0.05")

# Aging labels use the prototype's en dash.
BUCKETS = ("未到期", "1–30", "31–60", "61–90", "90+")
BUCKET_UNDUE = "未到期"

REGION_ORDER = ("R-HD", "R-HN", "R-HB", "R-XN", "R-XB")
LINE_ORDER = ("试剂", "仪器", "服务")
TIER_ORDER = ("A", "B", "C")

MAIN_QUESTION = "上个月哪个大区/哪类产品的回款率下降最快，原因是什么？"

# Preset reason tags. Applied only when the computed tests below are true.
TAG_VOLUME = "冲量出货抬高分母"
TAG_AGENT = "大代理商延期"
TAG_AGING = "账龄恶化"
TAG_DISCOUNT = "促销折扣升但回款未跟"
TAG_REGION = "区域性集中拖累"
PRESET_TAGS = (TAG_VOLUME, TAG_AGENT, TAG_AGING, TAG_DISCOUNT, TAG_REGION)

# A slice effect is "major" when it explains at least this share of Δrate.
MAJOR_SHARE = Decimal("0.25")

# Discount-rate increase (ratio, 1pp = 0.01) that can raise the promo tag.
DISCOUNT_TAG_PP = Decimal("0.01")

# Invoices at or above this original net amount are called 大额 in auxiliary facts.
LARGE_INVOICE = Decimal("500000")
