import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paper_trade import PaperTradeEngine  # noqa: E402


@pytest.fixture
def prices():
    return {"RELIANCE": 100.0, "TCS": 200.0}


@pytest.fixture
def eng(tmp_path, prices):
    return PaperTradeEngine(data_file=str(tmp_path / "pt.json"),
                            price_fetcher=lambda s: prices.get(s))


def test_market_order_uses_server_price_when_none_supplied(eng):
    r = eng.place_order("RELIANCE", "MARKET", "BUY", 10)
    assert r["success"], r
    assert eng.get_account()["free_cash"] == 1000000 - 1000


def test_market_order_without_any_price_fails_cleanly(eng):
    r = eng.place_order("UNKNOWN", "MARKET", "BUY", 1)
    assert not r["success"] and "unavailable" in r["error"]


def test_target_triggers_even_when_stop_loss_is_set(eng):
    eng.place_order("RELIANCE", "MARKET", "BUY", 10, stop_loss=90, target=110)
    out = eng.update_prices({"RELIANCE": 111})
    assert len(out["triggered_target"]) == 1
    assert not eng.accounts["default"]["positions"]
    assert eng.get_account()["realized_pnl"] == pytest.approx(110)


def test_stop_loss_triggers(eng):
    eng.place_order("RELIANCE", "MARKET", "BUY", 10, stop_loss=90, target=110)
    out = eng.update_prices({"RELIANCE": 89})
    assert len(out["triggered_sl"]) == 1
    assert eng.get_account()["realized_pnl"] == pytest.approx(-110)


def test_short_position_pnl_and_cash(eng, prices):
    eng.place_order("RELIANCE", "MARKET", "SELL", 10)
    eng.update_prices({"RELIANCE": 90})
    acc = eng.get_account()
    assert acc["unrealized_pnl"] == pytest.approx(100)
    res = eng.close_position("RELIANCE", current_price=90)
    assert res["realized_pnl"] == pytest.approx(100)
    assert eng.get_account()["free_cash"] == pytest.approx(1000100)


def test_reverse_position_releases_cash_first(tmp_path):
    e = PaperTradeEngine(data_file=str(tmp_path / "a.json"), price_fetcher=lambda s: 100.0)
    e.reset_account(1000)
    assert e.place_order("X", "MARKET", "BUY", 10)["success"]      # uses all cash
    r = e.place_order("X", "MARKET", "SELL", 15)                   # close 10, short 5
    assert r["success"], r
    pos = e.accounts["default"]["positions"]["X"]
    assert pos["side"] == "SELL" and pos["quantity"] == 5


def test_limit_order_fills_on_price_cross_and_cancel(eng):
    r = eng.place_order("RELIANCE", "LIMIT", "BUY", 5, price=95)
    assert r["order"]["status"] == "PENDING"
    eng.update_prices({"RELIANCE": 96})
    assert eng.get_orders()[0]["status"] == "PENDING"
    eng.update_prices({"RELIANCE": 94})
    assert eng.get_orders()[0]["status"] == "FILLED"
    r2 = eng.place_order("TCS", "LIMIT", "BUY", 1, price=150)
    assert eng.cancel_order(r2["order_id"])["success"]


def test_marketable_limit_fills_immediately(eng):
    r = eng.place_order("RELIANCE", "LIMIT", "BUY", 5, price=105)
    assert r["order"]["status"] == "FILLED"


def test_insufficient_balance_rejected(eng):
    r = eng.place_order("RELIANCE", "MARKET", "BUY", 1_000_000)
    assert not r["success"] and "Insufficient" in r["error"]


def test_validation(eng):
    assert not eng.place_order("RELIANCE", "MARKET", "HOLD", 1)["success"]
    assert not eng.place_order("RELIANCE", "MARKET", "BUY", -1)["success"]
    assert not eng.place_order("RELIANCE", "MARKET", "BUY", "abc")["success"]
    assert not eng.place_order("RELIANCE", "LIMIT", "BUY", 1)["success"]


def test_performance_is_json_safe_with_no_losses(eng):
    eng.place_order("RELIANCE", "MARKET", "BUY", 10)
    eng.close_position("RELIANCE", current_price=110)
    stats = eng.get_performance_stats()
    json.dumps(stats, allow_nan=False)   # would raise on Infinity
    assert stats["profit_factor"] is None and stats["win_rate"] == 100


def test_partial_close_and_persistence(eng, tmp_path, prices):
    eng.place_order("RELIANCE", "MARKET", "BUY", 10)
    eng.close_position("RELIANCE", quantity=4, current_price=110)
    assert eng.accounts["default"]["positions"]["RELIANCE"]["quantity"] == 6
    again = PaperTradeEngine(data_file=eng.data_file, price_fetcher=lambda s: None)
    assert again.accounts["default"]["positions"]["RELIANCE"]["quantity"] == 6


def test_accounts_do_not_share_state(tmp_path):
    a = PaperTradeEngine(data_file=str(tmp_path / "1.json"), price_fetcher=lambda s: 10.0)
    b = PaperTradeEngine(data_file=str(tmp_path / "2.json"), price_fetcher=lambda s: 10.0)
    a.place_order("X", "MARKET", "BUY", 1)
    assert not b.accounts["default"]["positions"]
