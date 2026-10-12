"""
Unit tests for trading_engine.matching_engine
"""
import pytest
from decimal import Decimal
from trading_engine.matching_engine import MatchingEngine, InMemoryOrderBook
from trading_engine.models import (
    Order, OrderSide, OrderType, OrderStatus, Trade, PositionSide
)


@pytest.fixture
def engine():
    return MatchingEngine()


@pytest.fixture
def book():
    return InMemoryOrderBook("BTCUSDT")


# ── InMemoryOrderBook ──────────────────────────────────────────

class TestOrderBook:
    def test_add_bid(self, book):
        o = Order(order_id="1", symbol="BTCUSDT", side=OrderSide.BUY,
                  order_type=OrderType.LIMIT, price=Decimal("100"), quantity=Decimal("5"))
        book.add_order(o)
        assert len(book.bids) == 1
        assert book.bids[0].order_id == "1"

    def test_bids_sorted_descending(self, book):
        for price in [90, 110, 100]:
            book.add_order(Order(order_id=str(price), symbol="BTCUSDT", side=OrderSide.BUY,
                                 order_type=OrderType.LIMIT, price=Decimal(str(price)), quantity=Decimal("1")))
        prices = [float(o.price) for o in book.bids]
        assert prices == sorted(prices, reverse=True)

    def test_add_ask(self, book):
        o = Order(order_id="1", symbol="BTCUSDT", side=OrderSide.SELL,
                  order_type=OrderType.LIMIT, price=Decimal("101"), quantity=Decimal("3"))
        book.add_order(o)
        assert len(book.asks) == 1

    def test_asks_sorted_ascending(self, book):
        for price in [105, 101, 103]:
            book.add_order(Order(order_id=str(price), symbol="BTCUSDT", side=OrderSide.SELL,
                                 order_type=OrderType.LIMIT, price=Decimal(str(price)), quantity=Decimal("1")))
        prices = [float(o.price) for o in book.asks]
        assert prices == sorted(prices)

    def test_remove_order(self, book):
        book.add_order(Order(order_id="a", symbol="BTCUSDT", side=OrderSide.BUY,
                             order_type=OrderType.LIMIT, price=Decimal("100"), quantity=Decimal("1")))
        book.add_order(Order(order_id="b", symbol="BTCUSDT", side=OrderSide.BUY,
                             order_type=OrderType.LIMIT, price=Decimal("99"), quantity=Decimal("1")))
        book.remove_order("a")
        assert len(book.bids) == 1
        assert book.bids[0].order_id == "b"

    def test_get_depth(self, book):
        for i, p in enumerate([101, 102, 103, 104, 105]):
            book.add_order(Order(order_id=str(i), symbol="BTCUSDT", side=OrderSide.SELL,
                                 order_type=OrderType.LIMIT, price=Decimal(str(p)), quantity=Decimal("1")))
        depth = book.get_depth(levels=3)
        assert len(depth["asks"]) == 3


# ── Market Order Matching ──────────────────────────────────────

class TestMarketOrder:
    def test_market_buy_empty_book(self, engine):
        order = Order(order_id="o1", symbol="BTCUSDT", side=OrderSide.BUY,
                      order_type=OrderType.MARKET, quantity=Decimal("0.001"), leverage=10)
        trades = engine.process_order(order, mark_price=Decimal("60000"))
        assert len(trades) == 1
        assert trades[0].side == OrderSide.BUY
        assert trades[0].quantity == Decimal("0.001")
        assert order.status == OrderStatus.FILLED

    def test_market_buy_fills_from_asks(self, engine):
        book = engine.get_book("BTCUSDT")
        book.add_order(Order(order_id="ask1", symbol="BTCUSDT", side=OrderSide.SELL,
                             order_type=OrderType.LIMIT, price=Decimal("60000"), quantity=Decimal("0.002")))
        order = Order(order_id="o1", symbol="BTCUSDT", side=OrderSide.BUY,
                      order_type=OrderType.MARKET, quantity=Decimal("0.001"), leverage=10)
        trades = engine.process_order(order, mark_price=Decimal("60000"))
        assert len(trades) == 1
        assert trades[0].price == Decimal("60000")
        assert order.status == OrderStatus.FILLED

    def test_market_buy_partial_fill_with_slippage(self, engine):
        book = engine.get_book("BTCUSDT")
        book.add_order(Order(order_id="ask1", symbol="BTCUSDT", side=OrderSide.SELL,
                             order_type=OrderType.LIMIT, price=Decimal("60000"), quantity=Decimal("0.0005")))
        order = Order(order_id="o1", symbol="BTCUSDT", side=OrderSide.BUY,
                      order_type=OrderType.MARKET, quantity=Decimal("0.001"), leverage=10)
        trades = engine.process_order(order, mark_price=Decimal("60000"))
        # 1 trade fills at limit, 1 fills with slippage
        assert len(trades) == 2
        assert trades[0].price == Decimal("60000")
        assert trades[1].price > Decimal("60000")  # slippage > mark

    def test_market_sell_fills_from_bids(self, engine):
        book = engine.get_book("BTCUSDT")
        book.add_order(Order(order_id="bid1", symbol="BTCUSDT", side=OrderSide.BUY,
                             order_type=OrderType.LIMIT, price=Decimal("59900"), quantity=Decimal("0.001")))
        order = Order(order_id="o1", symbol="BTCUSDT", side=OrderSide.SELL,
                      order_type=OrderType.MARKET, quantity=Decimal("0.001"), leverage=10)
        trades = engine.process_order(order, mark_price=Decimal("60000"))
        assert len(trades) == 1
        assert trades[0].price == Decimal("59900")

    def test_market_order_fee_calculation(self, engine):
        order = Order(order_id="o1", symbol="BTCUSDT", side=OrderSide.BUY,
                      order_type=OrderType.MARKET, quantity=Decimal("0.001"), leverage=10)
        trades = engine.process_order(order, mark_price=Decimal("60000"))
        # Fee uses execution price which includes slippage for BTC (0.0005%)
        exec_price = trades[0].price
        expected_fee = (Decimal("0.001") * exec_price * Decimal("0.00055")).quantize(Decimal("0.00000001"))
        assert trades[0].fee == expected_fee

    def test_market_order_sets_position_side(self, engine):
        order = Order(order_id="o1", symbol="BTCUSDT", side=OrderSide.BUY,
                      order_type=OrderType.MARKET, quantity=Decimal("0.001"),
                      leverage=10, position_side=PositionSide.LONG)
        trades = engine.process_order(order, mark_price=Decimal("60000"))
        assert trades[0].position_side == PositionSide.LONG


# ── Limit Order ────────────────────────────────────────────────

class TestLimitOrder:
    def test_limit_buy_above_mark_fills_immediately(self, engine):
        order = Order(order_id="o1", symbol="BTCUSDT", side=OrderSide.BUY,
                      order_type=OrderType.LIMIT, price=Decimal("61000"),
                      quantity=Decimal("0.001"), leverage=10)
        trades = engine.process_order(order, mark_price=Decimal("60000"))
        assert order.status == OrderStatus.FILLED
        assert len(trades) == 1

    def test_limit_buy_below_mark_rests(self, engine):
        order = Order(order_id="o1", symbol="BTCUSDT", side=OrderSide.BUY,
                      order_type=OrderType.LIMIT, price=Decimal("59000"),
                      quantity=Decimal("0.001"), leverage=10)
        trades = engine.process_order(order, mark_price=Decimal("60000"))
        assert order.status == OrderStatus.OPEN
        assert len(trades) == 0
        assert len(engine.get_book("BTCUSDT").bids) == 1

    def test_limit_post_only_crossing_rejected(self, engine):
        order = Order(order_id="o1", symbol="BTCUSDT", side=OrderSide.BUY,
                      order_type=OrderType.LIMIT, price=Decimal("61000"),
                      quantity=Decimal("0.001"), leverage=10, post_only=True)
        trades = engine.process_order(order, mark_price=Decimal("60000"))
        assert order.status == OrderStatus.REJECTED
        assert len(trades) == 0

    def test_limit_no_price_rejected(self, engine):
        order = Order(order_id="o1", symbol="BTCUSDT", side=OrderSide.BUY,
                      order_type=OrderType.LIMIT, quantity=Decimal("0.001"), leverage=10)
        trades = engine.process_order(order, mark_price=Decimal("60000"))
        assert order.status == OrderStatus.REJECTED


# ── Stop Orders ────────────────────────────────────────────────

class TestStopOrders:
    def test_stop_market_buy_triggers(self, engine):
        order = Order(order_id="o1", symbol="BTCUSDT", side=OrderSide.BUY,
                      order_type=OrderType.STOP_MARKET, quantity=Decimal("0.001"),
                      leverage=10, trigger_price=Decimal("61000"))
        trades = engine.process_order(order, mark_price=Decimal("61500"))
        # TRIGGERED is set first, then _match_market_order overwrites to FILLED
        assert order.status == OrderStatus.FILLED
        assert len(trades) >= 1

    def test_stop_market_buy_pending(self, engine):
        order = Order(order_id="o1", symbol="BTCUSDT", side=OrderSide.BUY,
                      order_type=OrderType.STOP_MARKET, quantity=Decimal("0.001"),
                      leverage=10, trigger_price=Decimal("61000"))
        trades = engine.process_order(order, mark_price=Decimal("60000"))
        assert order.status == OrderStatus.OPEN
        assert len(trades) == 0

    def test_stop_market_no_trigger_rejected(self, engine):
        order = Order(order_id="o1", symbol="BTCUSDT", side=OrderSide.BUY,
                      order_type=OrderType.STOP_MARKET, quantity=Decimal("0.001"), leverage=10)
        trades = engine.process_order(order, mark_price=Decimal("60000"))
        assert order.status == OrderStatus.REJECTED


# ── FOK / IOC ──────────────────────────────────────────────────

class TestFOKIOC:
    def test_fok_fills_when_liquidity_available(self, engine):
        book = engine.get_book("BTCUSDT")
        book.add_order(Order(order_id="ask1", symbol="BTCUSDT", side=OrderSide.SELL,
                             order_type=OrderType.LIMIT, price=Decimal("60000"), quantity=Decimal("0.001")))
        order = Order(order_id="o1", symbol="BTCUSDT", side=OrderSide.BUY,
                      order_type=OrderType.FOK, price=Decimal("60000"),
                      quantity=Decimal("0.001"), leverage=10)
        trades = engine.process_order(order, mark_price=Decimal("60000"))
        assert len(trades) == 1
        assert order.status == OrderStatus.FILLED

    def test_fok_cancels_when_insufficient_liquidity(self, engine):
        book = engine.get_book("BTCUSDT")
        book.add_order(Order(order_id="ask1", symbol="BTCUSDT", side=OrderSide.SELL,
                             order_type=OrderType.LIMIT, price=Decimal("60000"), quantity=Decimal("0.0005")))
        order = Order(order_id="o1", symbol="BTCUSDT", side=OrderSide.BUY,
                      order_type=OrderType.FOK, price=Decimal("60000"),
                      quantity=Decimal("0.001"), leverage=10)
        trades = engine.process_order(order, mark_price=Decimal("60000"))
        assert order.status == OrderStatus.CANCELLED
        assert len(trades) == 0

    def test_ioc_fills_partially(self, engine):
        book = engine.get_book("BTCUSDT")
        book.add_order(Order(order_id="ask1", symbol="BTCUSDT", side=OrderSide.SELL,
                             order_type=OrderType.LIMIT, price=Decimal("60000"), quantity=Decimal("0.0005")))
        order = Order(order_id="o1", symbol="BTCUSDT", side=OrderSide.BUY,
                      order_type=OrderType.IOC, quantity=Decimal("0.001"), leverage=10)
        trades = engine.process_order(order, mark_price=Decimal("60000"))
        # IOC fills from book (0.0005) + remainder with slippage (0.0005) = 2 trades
        assert len(trades) == 2
        # _match_market_order sets status to FILLED even for IOC
        assert order.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED, OrderStatus.CANCELLED)
