import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def make_candles(count=90):
    rows = []
    price = 100.0
    for i in range(count):
        open_ = price
        price += 0.5 if i % 7 else -0.8
        rows.append({
            "time": 1700000000 + i * 86400,
            "open": open_,
            "high": max(open_, price) + 1,
            "low": min(open_, price) - 1,
            "close": price,
            "volume": 1000000 + i,
        })
    return rows


def test_ohlc_endpoint_with_mocked_provider(monkeypatch):
    import app as app_module

    candles = make_candles()
    monkeypatch.setattr(app_module, "get_ohlc_data", lambda symbol, interval, range_days, offset_days=0: candles)
    client = app_module.app.test_client()

    response = client.get("/api/ohlc?symbol=TEST&interval=1d&range=90")
    data = response.get_json()

    assert response.status_code == 200
    assert data["symbol"] == "TEST"
    assert data["count"] == len(candles)
    assert data["candles"][0]["time"] == candles[0]["time"]


def test_quote_endpoint_with_mocked_stock_provider(monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "is_crypto_symbol", lambda symbol: False)
    monkeypatch.setattr(app_module.stock_provider, "get_live_quote", lambda symbol: {
        "symbol": symbol,
        "price": 101.5,
        "change": 1.5,
        "changePercent": 1.5,
        "open": 100,
        "high": 102,
        "low": 99,
        "volume": 1000,
        "previousClose": 100,
        "timestamp": 1700000000,
        "is_open": True,
    })
    client = app_module.app.test_client()

    response = client.get("/api/quote?symbol=TEST")
    data = response.get_json()

    assert response.status_code == 200
    assert data["symbol"] == "TEST"
    assert data["price"] == 101.5


def test_symbols_endpoint_with_mocked_search(monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module.stock_provider, "search_all_symbols", lambda query: [
        {"symbol": "TEST", "name": "Test Stock", "exchange": "NSE", "type": "stock"}
    ])
    client = app_module.app.test_client()

    response = client.get("/api/symbols?query=tes")
    data = response.get_json()

    assert response.status_code == 200
    assert any(row["symbol"] == "TEST" for row in data)


def test_indicator_endpoint_with_mocked_candles(monkeypatch):
    import app as app_module

    candles = make_candles()
    monkeypatch.setattr(app_module, "get_ohlc_data", lambda symbol, interval, range_days, offset_days=0: candles)
    client = app_module.app.test_client()

    response = client.get("/api/indicators?symbol=TEST&interval=1d&type=sma&period=10")

    assert response.status_code == 200
    assert response.get_json() is not None


def test_patterns_endpoint_with_mocked_candles(monkeypatch):
    import app as app_module

    candles = make_candles(120)
    monkeypatch.setattr(app_module, "get_ohlc_data", lambda symbol, interval, range_days, offset_days=0: candles)
    client = app_module.app.test_client()

    response = client.get("/api/patterns?symbol=TEST&interval=1d&max_patterns=10")
    data = response.get_json()

    assert response.status_code == 200
    assert data["symbol"] == "TEST"
    assert "patterns" in data
    assert data["total"] <= 10
