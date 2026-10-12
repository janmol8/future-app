"""
Unit tests for trading_engine.liquidation_engine
"""
import pytest
from decimal import Decimal
from unittest.mock import MagicMock
from trading_engine.liquidation_engine import LiquidationEngine
from trading_engine.risk_engine import RiskEngine
from trading_engine.models import (
    Position, Wallet, PositionSide, OrderSide, OrderType
)


@pytest.fixture
def mock_price_engine():
    pe = MagicMock()
    pe.get_mark_price.return_value = Decimal("60000")
    pe.get_funding_rate.return_value = Decimal("0.0001")
    pe.calculate_funding_payment.return_value = Decimal("0.6")
    return pe


@pytest.fixture
def mock_account():
    acc = MagicMock()
    acc.wallet = MagicMock()
    acc.wallet.balance = Decimal("10000")
    acc.positions = {}
    acc.hedge_mode = False
    acc.cancel_all_orders = MagicMock()
    acc.place_order = MagicMock(return_value={"success": True})
    acc.funding_history = []
    return acc


@pytest.fixture
def engine(mock_price_engine, mock_account):
    risk = RiskEngine()
    return LiquidationEngine(risk, mock_price_engine, mock_account)


class TestCheckLiquidationConditions:
    def test_safe_position_not_liquidated(self, engine, mock_price_engine):
        pos = Position(symbol="BTCUSDT", side=PositionSide.LONG,
                       entry_price=Decimal("60000"), quantity=Decimal("0.001"),
                       leverage=10, margin_type="ISOLATED", isolated_margin=Decimal("6"))
        mock_price_engine.get_mark_price.return_value = Decimal("60000")
        wallet = Wallet(balance=Decimal("10000"))
        assert engine.check_liquidation_conditions("BTCUSDT", pos, wallet) is False

    def test_losing_long_gets_liquidated(self, engine, mock_price_engine):
        pos = Position(symbol="BTCUSDT", side=PositionSide.LONG,
                       entry_price=Decimal("60000"), quantity=Decimal("0.001"),
                       leverage=10, margin_type="ISOLATED", isolated_margin=Decimal("6"))
        mock_price_engine.get_mark_price.return_value = Decimal("54000")
        wallet = Wallet(balance=Decimal("10000"))
        assert engine.check_liquidation_conditions("BTCUSDT", pos, wallet) is True

    def test_zero_mark_price_not_liquidated(self, engine, mock_price_engine):
        pos = Position(symbol="BTCUSDT", side=PositionSide.LONG,
                       entry_price=Decimal("60000"), quantity=Decimal("0.001"),
                       leverage=10, margin_type="ISOLATED", isolated_margin=Decimal("6"))
        mock_price_engine.get_mark_price.return_value = Decimal("0")
        wallet = Wallet(balance=Decimal("10000"))
        assert engine.check_liquidation_conditions("BTCUSDT", pos, wallet) is False

    def test_losing_short_gets_liquidated(self, engine, mock_price_engine):
        pos = Position(symbol="BTCUSDT", side=PositionSide.SHORT,
                       entry_price=Decimal("60000"), quantity=Decimal("0.001"),
                       leverage=10, margin_type="ISOLATED", isolated_margin=Decimal("6"))
        mock_price_engine.get_mark_price.return_value = Decimal("66000")
        wallet = Wallet(balance=Decimal("10000"))
        assert engine.check_liquidation_conditions("BTCUSDT", pos, wallet) is True


class TestExecuteLiquidationPipeline:
    def test_cancels_orders_and_liquidates(self, engine, mock_account, mock_price_engine):
        # Force recheck to also return True (margin still insufficient after cancel)
        mock_price_engine.get_mark_price.return_value = Decimal("54000")
        pos = Position(symbol="BTCUSDT", side=PositionSide.LONG,
                       entry_price=Decimal("60000"), quantity=Decimal("0.001"),
                       leverage=10, margin_type="ISOLATED", isolated_margin=Decimal("6"))
        result = engine.execute_liquidation_pipeline("BTCUSDT", pos)
        mock_account.cancel_all_orders.assert_called_once_with("BTCUSDT")
        mock_account.place_order.assert_called_once()
        assert result["liquidated"] is True

    def test_long_close_creates_sell_order(self, engine, mock_account, mock_price_engine):
        mock_price_engine.get_mark_price.return_value = Decimal("54000")
        pos = Position(symbol="BTCUSDT", side=PositionSide.LONG,
                       entry_price=Decimal("60000"), quantity=Decimal("0.001"),
                       leverage=10, margin_type="ISOLATED", isolated_margin=Decimal("6"))
        engine.execute_liquidation_pipeline("BTCUSDT", pos)
        order = mock_account.place_order.call_args[0][0]
        assert order.side == OrderSide.SELL
        assert order.order_type == OrderType.MARKET
        assert order.reduce_only is True

    def test_short_close_creates_buy_order(self, engine, mock_account, mock_price_engine):
        mock_price_engine.get_mark_price.return_value = Decimal("66000")
        pos = Position(symbol="BTCUSDT", side=PositionSide.SHORT,
                       entry_price=Decimal("60000"), quantity=Decimal("0.001"),
                       leverage=10, margin_type="ISOLATED", isolated_margin=Decimal("6"))
        engine.execute_liquidation_pipeline("BTCUSDT", pos)
        order = mock_account.place_order.call_args[0][0]
        assert order.side == OrderSide.BUY

    def test_liquidation_event_recorded(self, engine, mock_account, mock_price_engine):
        mock_price_engine.get_mark_price.return_value = Decimal("54000")
        pos = Position(symbol="BTCUSDT", side=PositionSide.LONG,
                       entry_price=Decimal("60000"), quantity=Decimal("0.001"),
                       leverage=10, margin_type="ISOLATED", isolated_margin=Decimal("6"))
        engine.execute_liquidation_pipeline("BTCUSDT", pos)
        assert len(engine.liquidation_events) == 1
        assert engine.liquidation_events[0].symbol == "BTCUSDT"


class TestCalculateADLScore:
    def test_profitable_long(self, engine):
        pos = Position(symbol="BTCUSDT", side=PositionSide.LONG,
                       entry_price=Decimal("60000"), quantity=Decimal("0.001"), leverage=10)
        score = engine.calculate_adl_score(pos, Decimal("66000"))
        assert score == pytest.approx(0.909, abs=0.01)

    def test_losing_long(self, engine):
        pos = Position(symbol="BTCUSDT", side=PositionSide.LONG,
                       entry_price=Decimal("60000"), quantity=Decimal("0.001"), leverage=10)
        score = engine.calculate_adl_score(pos, Decimal("54000"))
        assert score < 0

    def test_zero_notional(self, engine):
        pos = Position(symbol="BTCUSDT", side=PositionSide.LONG,
                       entry_price=Decimal("60000"), quantity=Decimal("0"), leverage=10)
        assert engine.calculate_adl_score(pos, Decimal("60000")) == 0.0


class TestSettleFunding:
    def test_long_pays_funding(self, engine, mock_account, mock_price_engine):
        pos = Position(symbol="BTCUSDT", side=PositionSide.LONG,
                       entry_price=Decimal("60000"), quantity=Decimal("0.001"),
                       leverage=10, margin_type="ISOLATED", isolated_margin=Decimal("6"))
        mock_account.positions = {("BTCUSDT", PositionSide.LONG): pos}
        mock_price_engine.get_funding_rate.return_value = Decimal("0.0001")
        mock_price_engine.get_mark_price.return_value = Decimal("60000")
        mock_price_engine.calculate_funding_payment.return_value = Decimal("0.6")
        initial = mock_account.wallet.balance
        engine.settle_funding()
        assert mock_account.wallet.balance < initial
        assert len(mock_account.funding_history) == 1

    def test_short_receives_funding(self, engine, mock_account, mock_price_engine):
        pos = Position(symbol="BTCUSDT", side=PositionSide.SHORT,
                       entry_price=Decimal("60000"), quantity=Decimal("0.001"),
                       leverage=10, margin_type="ISOLATED", isolated_margin=Decimal("6"))
        mock_account.positions = {("BTCUSDT", PositionSide.SHORT): pos}
        mock_price_engine.get_funding_rate.return_value = Decimal("0.0001")
        mock_price_engine.get_mark_price.return_value = Decimal("60000")
        mock_price_engine.calculate_funding_payment.return_value = Decimal("0.6")
        initial = mock_account.wallet.balance
        engine.settle_funding()
        assert mock_account.wallet.balance > initial

    def test_no_funding_when_rate_zero(self, engine, mock_account, mock_price_engine):
        pos = Position(symbol="BTCUSDT", side=PositionSide.LONG,
                       entry_price=Decimal("60000"), quantity=Decimal("0.001"),
                       leverage=10, margin_type="ISOLATED", isolated_margin=Decimal("6"))
        mock_account.positions = {("BTCUSDT", PositionSide.LONG): pos}
        mock_price_engine.get_funding_rate.return_value = Decimal("0")
        initial = mock_account.wallet.balance
        engine.settle_funding()
        assert mock_account.wallet.balance == initial


class TestCheckAndExecuteCrossLiquidation:
    def _make_cross_position(self, symbol="BTCUSDT", side=PositionSide.LONG,
                             entry_price=Decimal("60000"), quantity=Decimal("0.001"),
                             leverage=10):
        return Position(
            symbol=symbol, side=side, entry_price=entry_price,
            quantity=quantity, leverage=leverage,
            margin_type="CROSS", isolated_margin=Decimal("0")
        )

    def _setup_account(self, mock_account, positions):
        import threading
        mock_account.lock = threading.Lock()
        mock_account.positions = {
            (p.symbol, p.side): p for p in positions
        }
        mock_account.cancel_all_orders = MagicMock()
        mock_account.place_order = MagicMock(return_value={"success": True})

    def test_no_cross_positions_returns_early(self, engine, mock_account, mock_price_engine):
        pos = self._make_cross_position()
        pos.margin_type = "ISOLATED"
        self._setup_account(mock_account, [pos])
        wallet = Wallet(balance=Decimal("10000"))
        engine.check_and_execute_cross_liquidation(wallet)
        mock_account.cancel_all_orders.assert_not_called()

    def test_cross_safe_no_liquidation(self, engine, mock_account, mock_price_engine):
        pos = self._make_cross_position(quantity=Decimal("0.001"))
        self._setup_account(mock_account, [pos])
        mock_price_engine.get_mark_price.return_value = Decimal("60000")
        wallet = Wallet(balance=Decimal("10000"))
        engine.check_and_execute_cross_liquidation(wallet)
        # equity=10000 + upnl(0) = 10000 > mm(~0.3), no liquidation
        mock_account.cancel_all_orders.assert_not_called()

    def test_cross_liquidation_triggered(self, engine, mock_account, mock_price_engine):
        pos = self._make_cross_position(
            entry_price=Decimal("60000"), quantity=Decimal("1"), leverage=10
        )
        self._setup_account(mock_account, [pos])
        # Mark at 54000 → upnl = 1*(54000-60000) = -6000
        # notional = 1*54000 = 54000, mm ≈ 220 (tier 2)
        # equity = wallet(1000) + upnl(-6000) = -5000 < mm(220) → LIQUIDATE
        mock_price_engine.get_mark_price.return_value = Decimal("54000")
        mock_account.wallet = Wallet(balance=Decimal("1000"))
        # Mock check_liquidation_conditions to return True so pipeline proceeds past recheck
        engine.check_liquidation_conditions = MagicMock(return_value=True)
        wallet = mock_account.wallet
        engine.check_and_execute_cross_liquidation(wallet)
        # cancel_all_orders called twice: once in check_and_execute (no args),
        # once in execute_liquidation_pipeline (with symbol)
        assert mock_account.cancel_all_orders.call_count >= 1
        mock_account.place_order.assert_called_once()
        order = mock_account.place_order.call_args[0][0]
        assert order.reduce_only is True
        assert order.side == OrderSide.SELL  # closing LONG

    def test_cross_liquidates_largest_loss(self, engine, mock_account, mock_price_engine):
        pos_a = self._make_cross_position(
            symbol="BTCUSDT", entry_price=Decimal("60000"),
            quantity=Decimal("1"), leverage=10
        )
        pos_b = self._make_cross_position(
            symbol="ETHUSDT", entry_price=Decimal("3000"),
            quantity=Decimal("10"), leverage=10
        )
        self._setup_account(mock_account, [pos_a, pos_b])
        # BTC: upnl = 1*(54000-60000) = -6000
        # ETH: upnl = 10*(2700-3000) = -3000
        # BTC has larger loss → should be liquidated first
        def mark_price_side_effect(symbol):
            if symbol == "BTCUSDT":
                return Decimal("54000")
            return Decimal("2700")
        mock_price_engine.get_mark_price.side_effect = mark_price_side_effect
        mock_account.wallet = Wallet(balance=Decimal("500"))
        # Mock check_liquidation_conditions to return True so pipeline proceeds past recheck
        engine.check_liquidation_conditions = MagicMock(return_value=True)
        wallet = mock_account.wallet
        engine.check_and_execute_cross_liquidation(wallet)
        mock_account.place_order.assert_called_once()
        order = mock_account.place_order.call_args[0][0]
        assert order.symbol == "BTCUSDT"  # largest loss position

    def test_cross_only_closes_one_position(self, engine, mock_account, mock_price_engine):
        pos = self._make_cross_position(
            entry_price=Decimal("60000"), quantity=Decimal("1"), leverage=10
        )
        self._setup_account(mock_account, [pos])
        mock_price_engine.get_mark_price.return_value = Decimal("54000")
        mock_account.wallet = Wallet(balance=Decimal("1000"))
        # Mock check_liquidation_conditions to return True so pipeline proceeds past recheck
        engine.check_liquidation_conditions = MagicMock(return_value=True)
        engine.check_and_execute_cross_liquidation(mock_account.wallet)
        # Only one place_order call (the worst position)
        assert mock_account.place_order.call_count == 1


class TestStop:
    def test_stop_sets_flag(self, engine):
        engine.is_running = True
        engine.stop()
        assert engine.is_running is False
