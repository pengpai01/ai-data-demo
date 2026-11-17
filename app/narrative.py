"""Turn computed facts into 4–6 sentences. Never invents a number.

If DEEPSEEK_API_KEY is missing or the reply quotes a number that is not in the
fact sheet, the caller gets the structured facts only.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

SYSTEM = """你是星澜诊断的经营分析说明员。只根据用户给出的已计算事实写原因说明。
规则：
1. 写 4 到 6 句中文。
2. 不得出现事实中没有的数字。需要数字时，按事实里的写法原样抄写（包括正负号、小数和单位）。
3. 不得估算，不得改口径。事实没有覆盖的判断，写「本数据无法支持」。
4. 只使用事实里「计算信号」已列出的标签：冲量出货抬高分母、大代理商延期、账龄恶化、促销折扣升但回款未跟、区域性集中拖累。没有列出的标签不要用。
5. 销售回款率是主口径。到期回款率只作辅助对照，不能拿它替换主结论。
6. 一律称代理商。不要写经销商，不要把医院或体检中心当成销售客户。
7. 不要写关账日，不要写承诺回款、退货、坏账或代理商实盘库存。"""


def unknown_numbers(text: str, fact_sheet: str) -> list[str]:
    """Digits in the reply that do not occur in the fact sheet."""
    missing = []
    for token in re.findall(r"\d+(?:\.\d+)?", text):
        if token not in fact_sheet:
            missing.append(token)
    return missing


def write_narrative(fact_sheet: str, signals: list[str]) -> dict:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        return {
            "available": False,
            "text": "",
            "signals": signals,
            "message": "模型未配置（缺少环境变量 DEEPSEEK_API_KEY）。上面的结构化结果都来自计算；未生成原因说明，也没有编造数字。",
        }
    base = os.environ.get("DEEPSEEK_API_BASE_URL", "https://api.deepseek.com").strip().rstrip("/")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat").strip() or "deepseek-chat"
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": fact_sheet},
        ],
    }
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
        text = body["choices"][0]["message"]["content"].strip()
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError, ValueError):
        return {
            "available": False,
            "text": "",
            "signals": signals,
            "message": "模型调用失败，未生成原因说明。结构化计算结果仍然有效。",
        }
    missing = unknown_numbers(text, fact_sheet)
    if missing:
        return {
            "available": False,
            "text": "",
            "signals": signals,
            "message": "模型输出含无法回引到计算结果的数字，已不展示该说明。",
        }
    return {"available": True, "text": text, "signals": signals, "message": ""}
