# 星澜诊断 · 经营分析助手（演示）

虚构 IVD 公司「星澜诊断」的经营分析演示：左侧报告，右侧对话。数据全部虚构，不含任何真实回款或客户信息。销售全部通过代理商。

实现依据是仓库里的 `prototype-v1.3.md`。口径、场景和用词以这一版为准。

## 本地启动

```bash
chmod +x scripts/run.sh
./scripts/run.sh
```

浏览器打开 http://127.0.0.1:8000 。页面会自动计算主问：「上个月哪个大区/哪类产品的回款率下降最快，原因是什么？」默认期间是 2026年10月，对比 2026年9月。

分步做同一件事：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/generate_data.py
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`python scripts/generate_data.py` 用代码里的种子重写 `data/` 下六张 CSV。已提交的 CSV 与脚本输出一致。

## 环境变量

复制 `.env.example` 为 `.env`，不要提交 `.env`，也不要把密钥写进代码。

| 变量 | 作用 |
|------|------|
| `DEEPSEEK_API_KEY` | 用于「原因说明」和对话里的自由提问。没有密钥时，计算、图表和结构化归因照常显示，对话退回关键词匹配 |
| `DEEPSEEK_API_BASE_URL` | 可选，默认 `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 可选，默认 `deepseek-chat` |

接口是 OpenAI 兼容的 chat completions。密钥只读环境变量，不进入前端、`VITE_` 或 localStorage。

本地若要自测那 4–6 句原因说明，可以使用 weekly-report-ppt 已经在用的 `DEEPSEEK_API_KEY`。P0 自测不依赖这把密钥。没有密钥不会让整次交付失败。

## 自测

```bash
source .venv/bin/activate
pytest -q
```

结果和本次算出的数字在 `docs/self-test-p0.md`。口径和归因公式在 `docs/business-map.md`。

## 里面有什么

- `data/`：六张虚构 CSV，含 120 家代理商、30 个产品（试剂、仪器、服务各 10 个）、约 1.25 万笔出库和 1.16 万笔回款。经营月是 2025-11 至 2026-10。老代理商另有一笔 2024-11-15、100 元、当天结清的标记出库，只为区分「近 12 个月首次出库」。
- `app/`：FastAPI 计算指标，并提供单页。指标是确定性代码，不是模型。
- 对话里的 P0 四问和 P1 十问都会刷新左侧报告。超出地图的问题（承诺回款、经销商、直营对比等）回答「暂不支持」。
- 配了密钥时可以自由提问（`app/agent.py`）：模型把问题对到现成分析，或调用 `query_metrics` 按月、按大区/产品线/等级/代理商取数，左侧画出走势和计算表。模型回答里的数字必须能在取数结果里找到，否则只展示计算表。

主问的数据故事是：华东净销售被月末出货抬高，同时三家 A 级代理商回款下降。页面上的 pp 以当次计算为准。
