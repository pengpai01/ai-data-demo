"""Local single-page server. Metrics stay in Python; the page only renders them."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.constants import DATA_END, DATA_START, DEFAULT_PERIOD, MAIN_QUESTION
from app.metrics import iter_months
from app.reports import answer
from app.router import QUESTIONS
from app.store import load_store

STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="星澜诊断 · 经营分析助手")
STORE = load_store()


class AskBody(BaseModel):
    question: str = Field(min_length=1)
    period: str = DEFAULT_PERIOD
    view: str = "auto"


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "model_configured": bool(os.environ.get("DEEPSEEK_API_KEY", "").strip())}


@app.get("/api/meta")
def meta():
    return {
        "company": "星澜诊断",
        "main_question": MAIN_QUESTION,
        "default_period": DEFAULT_PERIOD,
        "periods": iter_months(DATA_START, DATA_END),
        "questions": QUESTIONS,
    }


@app.post("/api/ask")
def ask_post(body: AskBody):
    return answer(STORE, body.question, body.period, body.view)


@app.get("/api/ask")
def ask_get(question: str, period: str = DEFAULT_PERIOD, view: str = "auto"):
    return answer(STORE, question, period, view)


app.mount("/static", StaticFiles(directory=STATIC), name="static")
