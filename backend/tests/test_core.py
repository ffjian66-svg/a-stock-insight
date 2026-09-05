from datetime import date, datetime

from app.main import app
from app.providers.mock import MockProvider
from app.scheduler import is_trading_session
from app.services.indicators import calculate_indicators
from app.services.scoring import score_stock
from fastapi.testclient import TestClient


def test_mock_provider_contract() -> None:
    provider = MockProvider()
    assert len(provider.stocks()) >= 5
    assert provider.daily(date.today())[0].ts_code.endswith((".SH", ".SZ"))
    assert provider.quotes(["600519.SH"])[0].price > 0


def test_scoring_is_explainable() -> None:
    rows = [{"close": 100 + index * 0.5} for index in range(80)]
    indicators = calculate_indicators(rows)
    result = score_stock(indicators, {"pe_ttm": 20, "roe": 16, "profit_growth": 22}, 0.4)
    assert result.total is not None
    assert result.coverage == 1
    assert len(result.explanations) == 4


def test_score_requires_coverage() -> None:
    result = score_stock({}, {}, None)
    assert result.total is None
    assert result.coverage == 0


def test_trading_session_excludes_lunch_break() -> None:
    assert is_trading_session(datetime(2026, 9, 1, 10, 0))
    assert not is_trading_session(datetime(2026, 9, 1, 12, 0))


def test_spa_fallback_cannot_read_parent_files() -> None:
    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/%2e%2e/%2e%2e/pyproject.toml")
        assert response.status_code == 404
        assert "a-stock-insight" not in response.text


def test_unknown_api_returns_json_404() -> None:
    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/api/v1/does-not-exist")
        assert response.status_code == 404
        assert response.json()["detail"] == "接口不存在"
