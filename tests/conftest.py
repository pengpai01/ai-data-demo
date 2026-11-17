import pytest


@pytest.fixture(autouse=True)
def no_llm_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
