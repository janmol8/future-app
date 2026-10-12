"""
Unit tests for trading_engine.risk_engine
"""
import pytest
from decimal import Decimal
from trading_engine.risk_engine import RiskEngine
from trading_engine.models import (
    Order, Position, PositionSide, OrderSide, OrderType,
    BTCUSDT_RISK_TIERS, DEFAULT_RISK_TIERS
)


@pytest.fixture
def engine():
    return RiskEngine()


# ── get_risk_tier ──────────────────────────────────────────────

class TestGetRiskTier:
    def test_btc_tier1(self, engine):
        tier = engine.get_risk_tier("BTCUSDT", Decimal("10000"))
        assert tier.tier == 1
        assert tier.max_leverage == 125

    def test_btc_tier5(self, engine):
        tier = engine.get_risk_tier("BTCUSDT", Decimal("3000000"))
        assert tier.tier == 5
        assert tier.max_leverage == 10

    def test_btc_highest_tier(self, engine):
        tier = engine.get_risk_tier("BTCUSDT", Decimal("500000000"))
        assert tier.tier == 10
        assert tier.max_leverage == 1

    def test_altcoin_uses_default_tiers(self, engine):
        tier = engine.get_risk_tier("ETHUSDT", Decimal("10000"))
        assert tier.tier == 1

    def test_altcoin_high_tier(self, engine):
        tier = engine.get_risk_tier("DOGEUSDT", Decimal("25000000"))
        assert tier.tier == 5


# ── calculate_initial_margin ───────────────────────────────────

class TestCalculateInitialMargin:
    def test_basic(self, engine):
        im = engine.calculate_initial_margin(Decimal("1000"), 10)
        assert im == Decimal("100")

    def test_leverage_1(self, engine):
        im = engine.calculate_initial_margin(Decimal("500"), 1)
        assert im == Decimal("500")

    def test_leverage_125(self, engine):
        im = engine.calculate_initial_margin(Decimal("10000"), 125)
        assert im == Decimal("80")

    def test_zero_leverage_returns_full(self, engine):
        im = engine.calculate_initial_margin(Decimal("1000"), 0)
        assert im == Decimal("1000")

    def test_negative_leverage_returns_full(self, engine):
        im = engine.calculate_initial_margin(Decimal("1000"), -5)
        assert im == Decimal("1000")


# ── calculate_maintenance_margin ───────────────────────────────

class TestCalculateMaintenanceMargin:
    def test_tier1_btc(self, engine):
        mm = engine.calculate_maintenance_margin(Decimal("100000"), "BTCUSDT")
        # Tier 1: bracket_max=50000, mmr=0.004, cum=0
        # notional 100000 > 50000 → tier 2 (mmr=0.005, cum=50)
        # MM = 100000*0.005 - 50 = 450
        assert mm == Decimal("450")

    def test_tier2_btc(self, engine):
        mm = engine.calculate_maintenance_margin(Decimal("300000"), "BTCUSDT")
        # 300000 > 250000 → tier 3 (mmr=0.010, cum=1300)
        # MM = 300000*0.010 - 1300 = 1700
        assert mm == Decimal("1700")

    def test_tier4_btc(self, engine):
        mm = engine.calculate_maintenance_margin(Decimal("4000000"), "BTCUSDT")
        # 4000000 > 2000000 → tier 5 (mmr=0.050, cum=66300)
        # MM = 4000000*0.050 - 66300 = 133700
        assert mm == Decimal("133700")

    def test_mm_never_negative(self, engine):
        mm = engine.calculate_maintenance_margin(Decimal("10"), "BTCUSDT")
        assert mm >= Decimal("0")


# ── calculate_liquidation_price ────────────────────────────────

class TestCalculateLiquidationPrice:
    def test_long_isolated_10x(self, engine):
        liq = engine.calculate_liquidation_price(
            entry_price=Decimal("100"),
            side=PositionSide.LONG,
            leverage=10,
            maintenance_margin_rate=Decimal("0.005"),
            quantity=Decimal("1"),
            margin_mode="ISOLATED",
        )
        # im=0.1, mmr=0.005 → liq = 100 * (1 - 0.1 + 0.005) = 100 * 0.905 = 90.50
        assert liq == Decimal("90.50")

    def test_short_isolated_10x(self, engine):
        liq = engine.calculate_liquidation_price(
            entry_price=Decimal("100"),
            side=PositionSide.SHORT,
            leverage=10,
            maintenance_margin_rate=Decimal("0.005"),
            quantity=Decimal("1"),
            margin_mode="ISOLATED",
        )
        # im=0.1, mmr=0.005 → liq = 100 * (1 + 0.1 - 0.005) = 100 * 1.095 = 109.50
        assert liq == Decimal("109.50")

    def test_long_1x_never_liquidated(self, engine):
        liq = engine.calculate_liquidation_price(
            entry_price=Decimal("100"),
            side=PositionSide.LONG,
            leverage=1,
            maintenance_margin_rate=Decimal("0.005"),
            quantity=Decimal("1"),
            margin_mode="ISOLATED",
        )
        # im=1.0, mmr=0.005 → liq = 100 * (1 - 1 + 0.005) = 0.50
        assert liq == Decimal("0.50")

    def test_zero_quantity_returns_zero(self, engine):
        liq = engine.calculate_liquidation_price(
            entry_price=Decimal("100"),
            side=PositionSide.LONG,
            leverage=10,
            maintenance_margin_rate=Decimal("0.005"),
            quantity=Decimal("0"),
        )
        assert liq == Decimal("0")

    def test_zero_price_returns_zero(self, engine):
        liq = engine.calculate_liquidation_price(
            entry_price=Decimal("0"),
            side=PositionSide.LONG,
            leverage=10,
            maintenance_margin_rate=Decimal("0.005"),
            quantity=Decimal("1"),
        )
        assert liq == Decimal("0")


# ── calculate_bankruptcy_price ─────────────────────────────────

class TestCalculateBankruptcyPrice:
    def test_long(self, engine):
        bk = engine.calculate_bankruptcy_price(Decimal("100"), PositionSide.LONG, 10)
        assert bk == Decimal("90.00")

    def test_short(self, engine):
        bk = engine.calculate_bankruptcy_price(Decimal("100"), PositionSide.SHORT, 10)
        assert bk == Decimal("110.00")

    def test_1x_long(self, engine):
        bk = engine.calculate_bankruptcy_price(Decimal("200"), PositionSide.LONG, 1)
        assert bk == Decimal("0.00")

    def test_1x_short(self, engine):
        bk = engine.calculate_bankruptcy_price(Decimal("200"), PositionSide.SHORT, 1)
        assert bk == Decimal("400.00")


# ── calculate_unrealized_pnl ───────────────────────────────────

class TestCalculateUnrealizedPnl:
    def test_long_profit(self, engine):
        upnl = engine.calculate_unrealized_pnl(
            PositionSide.LONG, Decimal("1"), Decimal("100"), Decimal("110")
        )
        assert upnl == Decimal("10")

    def test_long_loss(self, engine):
        upnl = engine.calculate_unrealized_pnl(
            PositionSide.LONG, Decimal("1"), Decimal("100"), Decimal("90")
        )
        assert upnl == Decimal("-10")

    def test_short_profit(self, engine):
        upnl = engine.calculate_unrealized_pnl(
            PositionSide.SHORT, Decimal("1"), Decimal("100"), Decimal("90")
        )
        assert upnl == Decimal("10")

    def test_short_loss(self, engine):
        upnl = engine.calculate_unrealized_pnl(
            PositionSide.SHORT, Decimal("1"), Decimal("100"), Decimal("110")
        )
        assert upnl == Decimal("-10")

    def test_fractional_quantity(self, engine):
        upnl = engine.calculate_unrealized_pnl(
            PositionSide.LONG, Decimal("0.5"), Decimal("100"), Decimal("120")
        )
        assert upnl == Decimal("10")

    def test_no_change(self, engine):
        upnl = engine.calculate_unrealized_pnl(
            PositionSide.LONG, Decimal("1"), Decimal("100"), Decimal("100")
        )
        assert upnl == Decimal("0")


# ── calculate_roi ──────────────────────────────────────────────

class TestCalculateROI:
    def test_positive_roi(self, engine):
        roi = engine.calculate_roi(Decimal("50"), Decimal("100"))
        assert roi == Decimal("50.00")

    def test_negative_roi(self, engine):
        roi = engine.calculate_roi(Decimal("-25"), Decimal("100"))
        assert roi == Decimal("-25.00")

    def test_zero_margin(self, engine):
        roi = engine.calculate_roi(Decimal("50"), Decimal("0"))
        assert roi == Decimal("0")


# ── validate_order ─────────────────────────────────────────────

class TestValidateOrder:
    def _make_order(self, **kwargs):
        defaults = {
            "symbol": "BTCUSDT",
            "side": OrderSide.BUY,
            "order_type": OrderType.MARKET,
            "quantity": Decimal("0.001"),
            "leverage": 10,
        }
        defaults.update(kwargs)
        return Order(**defaults)

    def test_valid_market_order(self, engine):
        order = self._make_order()
        ok, err = engine.validate_order(
            order, Decimal("10000"), None,
            mark_price=Decimal("60000"),
            available_balance=Decimal("10000"),
        )
        assert ok is True

    def test_zero_quantity_rejected(self, engine):
        order = self._make_order(quantity=Decimal("0"))
        ok, err = engine.validate_order(
            order, Decimal("10000"), None, mark_price=Decimal("60000"),
            available_balance=Decimal("10000"),
        )
        assert ok is False
        assert "positive" in err.lower()

    def test_insufficient_balance(self, engine):
        order = self._make_order(quantity=Decimal("1"), leverage=1)
        ok, err = engine.validate_order(
            order, Decimal("100"), None, mark_price=Decimal("60000"),
            available_balance=Decimal("100"),
        )
        assert ok is False
        assert "margin" in err.lower() or "insufficient" in err.lower()

    def test_reduce_only_no_position_rejected(self, engine):
        order = self._make_order(reduce_only=True)
        ok, err = engine.validate_order(
            order, Decimal("10000"), None, mark_price=Decimal("60000"),
            available_balance=Decimal("10000"),
        )
        assert ok is False
        assert "position" in err.lower()

    def test_price_deviation_rejected(self, engine):
        order = self._make_order(order_type=OrderType.LIMIT, price=Decimal("100000"))
        ok, err = engine.validate_order(
            order, Decimal("10000"), None, mark_price=Decimal("60000"),
            available_balance=Decimal("10000"),
        )
        assert ok is False
        assert "10%" in err or "deviation" in err.lower()

    def test_post_only_crossing_spread_rejected(self, engine):
        order = self._make_order(
            order_type=OrderType.LIMIT, price=Decimal("61000"),
            post_only=True
        )
        ok, err = engine.validate_order(
            order, Decimal("10000"), None, mark_price=Decimal("60000"),
            available_balance=Decimal("10000"),
        )
        assert ok is False
        assert "post-only" in err.lower() or "spread" in err.lower()


# ── calculate_margin_ratio ────────────────────────────────────

class TestCalculateMarginRatio:
    def test_basic_ratio(self, engine):
        ratio = engine.calculate_margin_ratio(Decimal("500"), Decimal("10000"))
        assert ratio == Decimal("0.05")

    def test_ratio_1(self, engine):
        ratio = engine.calculate_margin_ratio(Decimal("1000"), Decimal("1000"))
        assert ratio == Decimal("1")

    def test_ratio_above_1(self, engine):
        ratio = engine.calculate_margin_ratio(Decimal("5000"), Decimal("1000"))
        assert ratio == Decimal("5")

    def test_zero_equity_returns_999(self, engine):
        ratio = engine.calculate_margin_ratio(Decimal("500"), Decimal("0"))
        assert ratio == Decimal("999")

    def test_negative_equity_returns_999(self, engine):
        ratio = engine.calculate_margin_ratio(Decimal("500"), Decimal("-1000"))
        assert ratio == Decimal("999")

    def test_zero_margin_ratio(self, engine):
        ratio = engine.calculate_margin_ratio(Decimal("0"), Decimal("10000"))
        assert ratio == Decimal("0")

    def test_small_margin_large_equity(self, engine):
        ratio = engine.calculate_margin_ratio(Decimal("1"), Decimal("1000000"))
        assert ratio == Decimal("0.000001")


# ── calculate_effective_leverage ───────────────────────────────

class TestCalculateEffectiveLeverage:
    def test_basic(self, engine):
        lev = engine.calculate_effective_leverage(Decimal("60000"), Decimal("6000"))
        assert lev == Decimal("10.00")

    def test_1x(self, engine):
        lev = engine.calculate_effective_leverage(Decimal("10000"), Decimal("10000"))
        assert lev == Decimal("1.00")

    def test_high_leverage(self, engine):
        lev = engine.calculate_effective_leverage(Decimal("125000"), Decimal("1000"))
        assert lev == Decimal("125.00")

    def test_zero_equity_returns_zero(self, engine):
        lev = engine.calculate_effective_leverage(Decimal("60000"), Decimal("0"))
        assert lev == Decimal("0")

    def test_negative_equity_returns_zero(self, engine):
        lev = engine.calculate_effective_leverage(Decimal("60000"), Decimal("-5000"))
        assert lev == Decimal("0")

    def test_small_notional(self, engine):
        lev = engine.calculate_effective_leverage(Decimal("100"), Decimal("10000"))
        assert lev == Decimal("0.01")

    def test_large_notional_large_equity(self, engine):
        lev = engine.calculate_effective_leverage(Decimal("500000"), Decimal("100000"))
        assert lev == Decimal("5.00")
