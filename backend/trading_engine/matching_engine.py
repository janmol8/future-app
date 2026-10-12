from decimal import Decimal
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import uuid
import time
import threading
from .models import Order, OrderStatus, OrderType, OrderSide, Trade, MAKER_FEE, TAKER_FEE, TimeInForce


class InMemoryOrderBook:
    """
    Maintains a sorted list of Bids and Asks.
    Simulated from real Binance order book via REST API polling.
    """

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.bids: List[Order] = []  # Sorted Descending by price
        self.asks: List[Order] = []  # Sorted Ascending by price
        self.lock = threading.Lock()

    def add_order(self, order: Order):
        """Add a limit order to the book."""
        with self.lock:
            if order.side == OrderSide.BUY:
                self.bids.append(order)
                self.bids.sort(key=lambda x: x.price or Decimal("0"), reverse=True)
            else:
                self.asks.append(order)
                self.asks.sort(key=lambda x: x.price or Decimal("0"))

    def _remove_order_unlocked(self, order_id: str):
        """Remove an order from the book (caller must hold self.lock)."""
        self.bids = [o for o in self.bids if o.order_id != order_id]
        self.asks = [o for o in self.asks if o.order_id != order_id]

    def remove_order(self, order_id: str):
        """Remove an order from the book."""
        with self.lock:
            self._remove_order_unlocked(order_id)

    def get_depth(self, levels: int = 20) -> Dict:
        """Get current book depth."""
        with self.lock:
            return {
                "bids": [[str(o.price), str(o.quantity - o.filled_quantity)] for o in self.bids[:levels]],
                "asks": [[str(o.price), str(o.quantity - o.filled_quantity)] for o in self.asks[:levels]],
            }


class MatchingEngine:
    """
    Single-threaded matching engine processing orders with price-time priority.
    Handles all order types and their execution lifecycles.
    """

    def __init__(self):
        self.order_books: Dict[str, InMemoryOrderBook] = {}
        self.pending_stops: Dict[str, Order] = {}
        self.pending_trailing: Dict[str, Dict] = {}
        self.lock = threading.Lock()

    def get_book(self, symbol: str) -> InMemoryOrderBook:
        if symbol not in self.order_books:
            self.order_books[symbol] = InMemoryOrderBook(symbol)
        return self.order_books[symbol]

    def process_order(self, order: Order, mark_price: Decimal, orderbook_data: Optional[Dict] = None) -> List[Trade]:
        """
        Main entry point for order processing.
        Routes to appropriate handler based on order type.
        """
        if order.order_type == OrderType.MARKET:
            return self._match_market_order(order, mark_price, orderbook_data)
        elif order.order_type == OrderType.LIMIT:
            return self._match_limit_order(order, mark_price)
        elif order.order_type == OrderType.STOP_MARKET:
            return self._process_stop_market(order, mark_price)
        elif order.order_type == OrderType.STOP_LIMIT:
            return self._process_stop_limit(order, mark_price)
        elif order.order_type == OrderType.TRAILING_STOP:
            self._setup_trailing_stop(order)
            return []
        elif order.order_type == OrderType.FOK:
            return self._process_fok(order, mark_price)
        elif order.order_type == OrderType.IOC:
            return self._process_ioc(order, mark_price)
        elif order.order_type == OrderType.TWAP:
            self._setup_twap(order)
            return []
        elif order.order_type == OrderType.ICEBERG:
            self._setup_iceberg(order)
            return []
        return []

    def _match_market_order(
        self,
        order: Order,
        mark_price: Decimal,
        orderbook_data: Optional[Dict] = None
    ) -> List[Trade]:
        """
        Simulate market order execution with slippage.
        Walk the order book consuming liquidity level by level.
        """
        book = self.get_book(order.symbol)
        trades = []
        remaining_qty = order.quantity

        # Try to match against limit orders in the book
        target_list = book.asks if order.side == OrderSide.BUY else book.bids
        to_remove = []

        with book.lock:
            for resting_order in target_list:
                if remaining_qty <= 0:
                    break

                available_qty = resting_order.quantity - resting_order.filled_quantity
                match_qty = min(remaining_qty, available_qty)

                trade = Trade(
                    trade_id=str(uuid.uuid4()),
                    order_id=order.order_id,
                    symbol=order.symbol,
                    side=order.side,
                    quantity=match_qty,
                    price=resting_order.price or mark_price,
                    fee=(match_qty * (resting_order.price or mark_price) * TAKER_FEE).quantize(Decimal("0.00000001")),
                    fee_type="TAKER",
                    is_maker=False,
                    position_side=order.position_side,
                    leverage=order.leverage,
                )
                trades.append(trade)

                resting_order.filled_quantity += match_qty
                if resting_order.filled_quantity >= resting_order.quantity:
                    resting_order.status = OrderStatus.FILLED
                    to_remove.append(resting_order.order_id)
                else:
                    resting_order.status = OrderStatus.PARTIALLY_FILLED

                remaining_qty -= match_qty

            for oid in to_remove:
                book._remove_order_unlocked(oid)

        # If still remaining, apply slippage simulation
        if remaining_qty > 0:
            # Simulate slippage: 0.05% for majors, 0.15% for altcoins
            is_major = any(m in order.symbol for m in ["BTC", "ETH"])
            slippage_pct = Decimal("0.0005") if is_major else Decimal("0.0015")

            if order.side == OrderSide.BUY:
                execution_price = mark_price * (Decimal("1") + slippage_pct)
            else:
                execution_price = mark_price * (Decimal("1") - slippage_pct)

            trade = Trade(
                trade_id=str(uuid.uuid4()),
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                quantity=remaining_qty,
                price=execution_price.quantize(Decimal("0.01")),
                fee=(remaining_qty * execution_price * TAKER_FEE).quantize(Decimal("0.00000001")),
                fee_type="TAKER",
                is_maker=False,
                position_side=order.position_side,
                leverage=order.leverage,
            )
            trades.append(trade)
            remaining_qty = Decimal("0")

        order.status = OrderStatus.FILLED
        order.filled_quantity = order.quantity
        if trades:
            total_qty = sum(t.quantity for t in trades)
            if total_qty > 0:
                order.avg_fill_price = sum(t.price * t.quantity for t in trades) / total_qty

        return trades

    def _match_limit_order(self, order: Order, mark_price: Decimal) -> List[Trade]:
        """Check if limit order can be matched immediately. If not, add to book."""
        if not order.price:
            order.status = OrderStatus.REJECTED
            return []

        # Check for immediate match (Taker)
        if (order.side == OrderSide.BUY and order.price >= mark_price) or \
           (order.side == OrderSide.SELL and order.price <= mark_price):
            # Post-only check
            if order.post_only:
                order.status = OrderStatus.REJECTED
                return []
            return self._match_market_order(order, mark_price)

        # Add to book (Maker)
        book = self.get_book(order.symbol)
        book.add_order(order)
        order.status = OrderStatus.OPEN
        return []

    def _process_stop_market(self, order: Order, current_price: Decimal) -> List[Trade]:
        """Monitor trigger_price against current_price. When triggered, submit as market order."""
        if not order.trigger_price:
            order.status = OrderStatus.REJECTED
            return []

        triggered = False
        if order.side == OrderSide.BUY:
            triggered = current_price >= order.trigger_price
        else:
            triggered = current_price <= order.trigger_price

        if triggered:
            order.status = OrderStatus.TRIGGERED
            return self._match_market_order(order, current_price)
        else:
            with self.lock:
                self.pending_stops[order.order_id] = order
            order.status = OrderStatus.OPEN
            return []

    def _process_stop_limit(self, order: Order, current_price: Decimal) -> List[Trade]:
        """When trigger_price hit, place limit order at order.price."""
        if not order.trigger_price or not order.price:
            order.status = OrderStatus.REJECTED
            return []

        triggered = False
        if order.side == OrderSide.BUY:
            triggered = current_price >= order.trigger_price
        else:
            triggered = current_price <= order.trigger_price

        if triggered:
            order.status = OrderStatus.TRIGGERED
            return self._match_limit_order(order, current_price)
        else:
            with self.lock:
                self.pending_stops[order.order_id] = order
            order.status = OrderStatus.OPEN
            return []

    def _setup_trailing_stop(self, order: Order):
        """Set up a trailing stop order."""
        with self.lock:
            self.pending_trailing[order.order_id] = {
                "order": order,
                "peak": order.trigger_price or Decimal("0"),
                "trough": order.trigger_price or Decimal("999999999"),
            }
        order.status = OrderStatus.OPEN

    def update_trailing_stops(self, current_price: Decimal, symbol: str) -> List[Trade]:
        """Update all trailing stops for a symbol and return any triggered trades."""
        triggered_trades = []
        to_remove = []

        with self.lock:
            for oid, data in self.pending_trailing.items():
                order = data["order"]
                if order.symbol != symbol:
                    continue

                if order.side == OrderSide.LONG:
                    # Track trough
                    if current_price < data["trough"] or data["trough"] == Decimal("999999999"):
                        data["trough"] = current_price
                        trigger = current_price * (Decimal("1") + (order.trailing_callback_pct or Decimal("0.01")))
                        order.trigger_price = trigger

                    if current_price <= (order.trigger_price or Decimal("0")):
                        order.status = OrderStatus.TRIGGERED
                        trades = self._match_market_order(order, current_price)
                        triggered_trades.extend(trades)
                        to_remove.append(oid)
                else:
                    # Track peak
                    if current_price > data["peak"] or data["peak"] == Decimal("0"):
                        data["peak"] = current_price
                        trigger = current_price * (Decimal("1") - (order.trailing_callback_pct or Decimal("0.01")))
                        order.trigger_price = trigger

                    if current_price >= (order.trigger_price or Decimal("999999999")):
                        order.status = OrderStatus.TRIGGERED
                        trades = self._match_market_order(order, current_price)
                        triggered_trades.extend(trades)
                        to_remove.append(oid)

        for oid in to_remove:
            with self.lock:
                self.pending_trailing.pop(oid, None)

        return triggered_trades

    def check_pending_stops(self, current_price: Decimal, symbol: str) -> List[Trade]:
        """Check all pending stop orders and trigger if conditions are met."""
        triggered_trades = []
        to_remove = []

        with self.lock:
            for oid, order in self.pending_stops.items():
                if order.symbol != symbol:
                    continue

                triggered = False
                if order.side == OrderSide.BUY:
                    triggered = current_price >= (order.trigger_price or Decimal("0"))
                else:
                    triggered = current_price <= (order.trigger_price or Decimal("999999999"))

                if triggered:
                    order.status = OrderStatus.TRIGGERED
                    if order.order_type == OrderType.STOP_MARKET:
                        trades = self._match_market_order(order, current_price)
                    else:
                        trades = self._match_limit_order(order, current_price)
                    triggered_trades.extend(trades)
                    to_remove.append(oid)

        for oid in to_remove:
            with self.lock:
                self.pending_stops.pop(oid, None)

        return triggered_trades

    def _process_fok(self, order: Order, mark_price: Decimal) -> List[Trade]:
        """Fill Or Kill: Check full liquidity available. If not, cancel entire order."""
        if not order.price:
            order.status = OrderStatus.REJECTED
            return []

        book = self.get_book(order.symbol)
        target_list = book.asks if order.side == OrderSide.BUY else book.bids

        available_at_price = Decimal("0")
        for resting_order in target_list:
            if order.side == OrderSide.BUY and resting_order.price and resting_order.price <= order.price:
                available_at_price += resting_order.quantity - resting_order.filled_quantity
            elif order.side == OrderSide.SELL and resting_order.price and resting_order.price >= order.price:
                available_at_price += resting_order.quantity - resting_order.filled_quantity

        if available_at_price >= order.quantity:
            return self._match_limit_order(order, mark_price)
        else:
            order.status = OrderStatus.CANCELLED
            return []

    def _process_ioc(self, order: Order, mark_price: Decimal) -> List[Trade]:
        """Immediate Or Cancel: Fill as much as possible immediately, cancel remainder."""
        trades = self._match_market_order(order, mark_price)
        if order.filled_quantity < order.quantity:
            order.status = OrderStatus.CANCELLED if not trades else OrderStatus.PARTIALLY_FILLED
        return trades

    def _setup_twap(self, order: Order):
        """Set up TWAP order for time-weighted execution."""
        with self.lock:
            self.pending_stops[order.order_id] = order
        order.status = OrderStatus.OPEN

    def _setup_iceberg(self, order: Order):
        """Set up iceberg order showing only visible quantity."""
        with self.lock:
            self.pending_stops[order.order_id] = order
        order.status = OrderStatus.OPEN
