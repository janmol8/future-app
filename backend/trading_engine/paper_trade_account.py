import json
import os
import threading
import uuid
import time
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from .models import (
    Order, Position, Wallet, Trade, OrderStatus, OrderSide, OrderType,
    PositionSide, TimeInForce, FundingPayment, JournalEntry, LiquidationEvent,
    MAKER_FEE, TAKER_FEE
)
from .price_engine import PriceEngine
from .risk_engine import RiskEngine
from .matching_engine import MatchingEngine
from .liquidation_engine import LiquidationEngine
from .analytics_engine import AnalyticsEngine
from .journal_engine import JournalEngine


class PaperTradeAccount:
    """
    Master Account Manager. Coordinates all sub-engines and persists state.
    
    Wallet accounting model (Binance-style):
      - wallet.balance = TOTAL account equity (initial_balance + realized PnL)
      - wallet.available_balance = balance - total_margin_used - total_unrealized_pnl_if_negative
      - wallet.isolated_margin = sum of all isolated margins locked in positions
    """

    def __init__(self, initial_balance: Decimal = Decimal("10000"), data_file: str = "futures_trade_data.json"):
        self.data_file = os.path.join(os.path.dirname(__file__), '..', data_file)
        self.lock = threading.Lock()

        self.price_engine = PriceEngine()
        self.risk_engine = RiskEngine()
        self.matching_engine = MatchingEngine()
        self.analytics_engine = AnalyticsEngine()
        self.journal_engine = JournalEngine()
        self.liquidation_engine = LiquidationEngine(self.risk_engine, self.price_engine, self)

        self.wallet = Wallet(
            asset="USDT",
            balance=initial_balance,
            available_balance=initial_balance,
            withdrawable_balance=initial_balance,
        )
        self.positions: Dict[Tuple[str, PositionSide], Position] = {}
        self.orders: Dict[str, Order] = {}
        self.trades: List[Trade] = []
        self.funding_history: List[FundingPayment] = []
        self.equity_curve: List[Dict] = []
        self.initial_balance = initial_balance
        self.hedge_mode: bool = False
        self.leverage_settings: Dict[str, int] = {}
        self.margin_mode_settings: Dict[str, str] = {}
        self.liquidation_events: List[LiquidationEvent] = []
        self._total_margin_used = Decimal("0")

        self._load_state()
        self._recalc_available_balance()
        self._record_equity_snapshot()

        self.liq_thread = threading.Thread(target=self.liquidation_engine.run_background_loop, daemon=True)
        self.liq_thread.start()
        self.equity_thread = threading.Thread(target=self._equity_snapshot_loop, daemon=True)
        self.equity_thread.start()
        self.twap_thread = threading.Thread(target=self._twap_execution_loop, daemon=True)
        self.twap_thread.start()

    def _equity_snapshot_loop(self):
        while True:
            try:
                time.sleep(60)
                self._record_equity_snapshot()
                self._save_state()
            except Exception as e:
                print(f"Error in equity snapshot loop: {e}")

    # ─── TWAP EXECUTION LOOP ───────────────────────────────────────────
    def _twap_execution_loop(self):
        """Background loop that finds pending TWAP orders and executes child slices."""
        # Track state: {order_id: {next_slice_at, slice_count, total_slices, slice_size, duration_sec, last_exec_at}}
        twap_state: Dict[str, Dict] = {}
        while True:
            try:
                time.sleep(1)
                now = time.time()
                with self.lock:
                    for oid, order in list(self.orders.items()):
                        if order.order_type != OrderType.TWAP:
                            continue
                        if order.status not in (OrderStatus.OPEN,):
                            continue
                        if not order.twap_config:
                            continue

                        # Initialize TWAP state on first encounter
                        if oid not in twap_state:
                            cfg = order.twap_config
                            total_slices = cfg.get("slices", 10)
                            duration_sec = cfg.get("duration_sec", 600)
                            randomize = cfg.get("randomize", False)
                            slice_size = order.quantity / Decimal(str(total_slices))
                            interval = duration_sec / total_slices
                            twap_state[oid] = {
                                "total_slices": total_slices,
                                "slice_size": slice_size,
                                "interval": interval,
                                "randomize": randomize,
                                "slice_count": 0,
                                "next_slice_at": now + interval,
                                "remaining_qty": order.quantity,
                                "total_filled": Decimal("0"),
                                "total_cost": Decimal("0"),
                                "start_time": now,
                            }

                        state = twap_state[oid]
                        if now < state["next_slice_at"]:
                            continue

                        # Time for next slice
                        remaining = state["remaining_qty"]
                        if remaining <= 0:
                            order.status = OrderStatus.FILLED
                            order.filled_quantity = order.quantity
                            order.avg_fill_price = (state["total_cost"] / state["total_filled"]).quantize(Decimal("0.01")) if state["total_filled"] > 0 else Decimal("0")
                            del twap_state[oid]
                            continue

                        # Calculate this slice size
                        slices_left = state["total_slices"] - state["slice_count"]
                        if slices_left <= 0:
                            order.status = OrderStatus.FILLED
                            order.filled_quantity = order.quantity
                            order.avg_fill_price = (state["total_cost"] / state["total_filled"]).quantize(Decimal("0.01")) if state["total_filled"] > 0 else Decimal("0")
                            del twap_state[oid]
                            continue

                        slice_qty = remaining / Decimal(str(slices_left))
                        if state["randomize"]:
                            import random
                            variation = Decimal(str(random.uniform(0.8, 1.2)))
                            slice_qty = (slice_qty * variation).quantize(Decimal("0.00000001"))
                            slice_qty = min(slice_qty, remaining)

                        # Execute slice as market order
                        mark_price = self.price_engine.get_mark_price(order.symbol)
                        orderbook_data = self.price_engine.get_orderbook(order.symbol)

                        child_order = Order(
                            order_id=str(uuid.uuid4()),
                            symbol=order.symbol,
                            side=order.side,
                            order_type=OrderType.MARKET,
                            quantity=slice_qty,
                            leverage=order.leverage,
                            position_side=order.position_side,
                        )
                        new_trades = self.matching_engine.process_order(child_order, mark_price, orderbook_data)

                        for trade in new_trades:
                            self._apply_trade(trade)
                            state["total_filled"] += trade.quantity
                            state["total_cost"] += trade.price * trade.quantity

                        state["remaining_qty"] -= slice_qty
                        state["slice_count"] += 1

                        # Update parent order fill progress
                        order.filled_quantity = order.quantity - state["remaining_qty"]
                        if state["remaining_qty"] > 0:
                            order.status = OrderStatus.PARTIALLY_FILLED
                        else:
                            order.status = OrderStatus.FILLED
                            order.avg_fill_price = (state["total_cost"] / state["total_filled"]).quantize(Decimal("0.01")) if state["total_filled"] > 0 else Decimal("0")
                            del twap_state[oid]

                        # Schedule next slice
                        state["next_slice_at"] = now + state["interval"]

                    # Clean up TWAP state for cancelled orders
                    for oid in list(twap_state.keys()):
                        if oid not in self.orders or self.orders[oid].status == OrderStatus.CANCELLED:
                            del twap_state[oid]

                if twap_state:
                    self._recalc_available_balance()
                    self._save_state()
            except Exception as e:
                print(f"Error in TWAP execution loop: {e}")

    def _record_equity_snapshot(self):
        equity = self._calculate_total_equity()
        self.equity_curve.append({"timestamp": int(time.time()), "equity": float(equity)})
        if len(self.equity_curve) > 1440:
            self.equity_curve = self.equity_curve[-1440:]

    def _calculate_total_equity(self) -> Decimal:
        """Total equity = wallet.balance (which tracks realized PnL) + total unrealized PnL."""
        total_upnl = Decimal("0")
        for (symbol, side), pos in self.positions.items():
            mark_price = self.price_engine.get_mark_price(symbol)
            if mark_price > 0:
                upnl = self.risk_engine.calculate_unrealized_pnl(pos.side, pos.quantity, pos.entry_price, mark_price)
                total_upnl += upnl
        return self.wallet.balance + total_upnl

    def _recalc_available_balance(self):
        """Recalculate available balance from wallet.balance and margin used."""
        total_upnl = Decimal("0")
        total_margin = Decimal("0")
        for (symbol, side), pos in self.positions.items():
            total_margin += pos.isolated_margin
            mark_price = self.price_engine.get_mark_price(symbol)
            if mark_price > 0:
                upnl = self.risk_engine.calculate_unrealized_pnl(pos.side, pos.quantity, pos.entry_price, mark_price)
                total_upnl += upnl
        self._total_margin_used = total_margin
        self.wallet.isolated_margin = total_margin
        # App rule: available funds are wallet balance minus locked position margin.
        # Unrealized P&L changes portfolio value, not the amount of margin already locked.
        self.wallet.available_balance = self.wallet.balance - total_margin
        if self.wallet.available_balance < 0:
            self.wallet.available_balance = Decimal("0")

    def set_hedge_mode(self, enabled: bool) -> Dict:
        with self.lock:
            if self.positions:
                return {"success": False, "error": "Cannot change mode with open positions"}
            self.hedge_mode = enabled
            self._save_state()
            return {"success": True, "hedge_mode": enabled}

    def set_leverage(self, symbol: str, leverage: int) -> Dict:
        leverage = max(1, min(leverage, 125))
        with self.lock:
            self.leverage_settings[symbol] = leverage
            for (s, side), pos in self.positions.items():
                if s == symbol:
                    pos.leverage = leverage
                    notional = pos.quantity * pos.entry_price
                    pos.isolated_margin = notional / leverage
            self._recalc_available_balance()
            self._save_state()
        return {"success": True, "leverage": leverage}

    def set_margin_mode(self, symbol: str, mode: str) -> Dict:
        if mode not in ("CROSS", "ISOLATED"):
            return {"success": False, "error": "Invalid margin mode"}
        with self.lock:
            self.margin_mode_settings[symbol] = mode
            for (s, side), pos in self.positions.items():
                if s == symbol:
                    pos.margin_type = mode
            self._save_state()
        return {"success": True, "margin_mode": mode}

    def adjust_isolated_margin(self, symbol: str, side: str, amount: Decimal) -> Dict:
        amount = Decimal(str(amount))
        pos_side = PositionSide(side)
        key = (symbol, pos_side)
        with self.lock:
            if key not in self.positions:
                return {"success": False, "error": "Position not found"}
            pos = self.positions[key]
            if pos.margin_type != "ISOLATED":
                return {"success": False, "error": "Can only adjust margin on ISOLATED positions"}
            if amount > 0:
                if self.wallet.available_balance < amount:
                    return {"success": False, "error": "Insufficient balance"}
                pos.isolated_margin += amount
                pos.allocated_margin += amount
                self.wallet.available_balance -= amount
            else:
                abs_amount = abs(amount)
                if pos.isolated_margin < abs_amount:
                    return {"success": False, "error": "Insufficient margin to remove"}
                pos.isolated_margin -= abs_amount
                pos.allocated_margin = max(Decimal("0"), pos.allocated_margin - abs_amount)
                self.wallet.available_balance += abs_amount
            self._recalc_available_balance()
            self._save_state()
        return {"success": True}

    def place_order(self, order: Order) -> Dict:
        with self.lock:
            target_pos = None
            if self.hedge_mode:
                if order.position_side:
                    target_pos = self.positions.get((order.symbol, order.position_side))
            else:
                for (s, side), pos in self.positions.items():
                    if s == order.symbol:
                        target_pos = pos
                        break

            mark_price = self.price_engine.get_mark_price(order.symbol)
            self._recalc_available_balance()
            is_valid, error = self.risk_engine.validate_order(
                order, self.wallet.available_balance, target_pos,
                mark_price=mark_price, available_balance=self.wallet.available_balance
            )
            if not is_valid:
                return {"success": False, "error": error}

            self.orders[order.order_id] = order
            orderbook_data = self.price_engine.get_orderbook(order.symbol)
            new_trades = self.matching_engine.process_order(order, mark_price, orderbook_data)

            for trade in new_trades:
                self._apply_trade(trade)
                current_pos = None
                if self.hedge_mode:
                    current_pos = self.positions.get((trade.symbol, trade.position_side or PositionSide.BOTH))
                else:
                    for (s, side), pos in self.positions.items():
                        if s == trade.symbol:
                            current_pos = pos
                            break
                self.journal_engine.auto_capture_trade_metadata(trade, current_pos)

            self._recalc_available_balance()
            self._record_equity_snapshot()
            self._save_state()
            print(f"[FuturesOrder] placed={order.order_id} status={order.status.value} trades={len(new_trades)} available={self.wallet.available_balance}")
            return {
                "success": True,
                "order_id": order.order_id,
                "status": order.status.value,
                "trades": [t.to_dict() for t in new_trades],
            }

    def close_position(self, symbol: str, side: Optional[str] = None, pct: float = 100.0) -> Dict:
        """Close a position by symbol and optional side. pct=100 means full close."""
        with self.lock:
            target_key = None
            if side:
                pos_side = PositionSide(side)
                if (symbol, pos_side) in self.positions:
                    target_key = (symbol, pos_side)
            else:
                for (s, s_val), pos in self.positions.items():
                    if s == symbol:
                        target_key = (s, s_val)
                        break

            if not target_key:
                return {"success": False, "error": "No position found"}

            pos = self.positions[target_key]
            close_qty = pos.quantity * Decimal(str(pct)) / Decimal("100")
            close_qty = close_qty.quantize(Decimal("0.00000001"))
            if close_qty > pos.quantity:
                close_qty = pos.quantity

            close_side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
            order = Order(
                order_id=str(uuid.uuid4()),
                symbol=symbol,
                side=close_side,
                order_type=OrderType.MARKET,
                quantity=close_qty,
                position_side=pos.side,
                reduce_only=True,
                leverage=pos.leverage,
            )

        result = self.place_order(order)
        return result

    def _apply_trade(self, trade: Trade):
        symbol = trade.symbol

        if self.hedge_mode:
            side = trade.position_side or (PositionSide.LONG if trade.side == OrderSide.BUY else PositionSide.SHORT)
            pos_key = (symbol, side)
            self._apply_trade_to_position(pos_key, trade)
        else:
            existing_pos = None
            pos_key = None
            for (s, side), pos in self.positions.items():
                if s == symbol:
                    existing_pos = pos
                    pos_key = (s, side)
                    break

            if not existing_pos:
                side = PositionSide.LONG if trade.side == OrderSide.BUY else PositionSide.SHORT
                pos_key = (symbol, side)
                self._open_new_position(pos_key, trade)
            else:
                is_increase = (
                    (existing_pos.side == PositionSide.LONG and trade.side == OrderSide.BUY) or
                    (existing_pos.side == PositionSide.SHORT and trade.side == OrderSide.SELL)
                )
                if is_increase:
                    self._increase_position(pos_key, existing_pos, trade)
                else:
                    self._decrease_or_flip_position(pos_key, existing_pos, trade)

        self.trades.append(trade)

    def _open_new_position(self, pos_key: Tuple[str, PositionSide], trade: Trade):
        leverage = self.leverage_settings.get(trade.symbol, trade.leverage or 20)
        margin_mode = self.margin_mode_settings.get(trade.symbol, "ISOLATED")
        notional = trade.quantity * trade.price
        initial_margin = notional / leverage

        pos = Position(
            symbol=trade.symbol,
            side=pos_key[1],
            entry_price=trade.price,
            quantity=trade.quantity,
            leverage=leverage,
            margin_type=margin_mode,
            isolated_margin=initial_margin,
            allocated_margin=initial_margin,
            entry_time=time.time(),
        )

        tier = self.risk_engine.get_risk_tier(trade.symbol, notional)
        pos.liquidation_price = self.risk_engine.calculate_liquidation_price(
            trade.price, pos.side, leverage, tier.maintenance_margin_rate,
            quantity=trade.quantity, margin_mode=margin_mode
        )
        pos.bankruptcy_price = self.risk_engine.calculate_bankruptcy_price(trade.price, pos.side, leverage)

        self.positions[pos_key] = pos
        print(f"[FuturesPosition] created symbol={trade.symbol} side={pos.side.value} qty={pos.quantity} margin={pos.isolated_margin}")

    def _increase_position(self, pos_key: Tuple[str, PositionSide], pos: Position, trade: Trade):
        new_total_qty = pos.quantity + trade.quantity
        pos.entry_price = (pos.entry_price * pos.quantity + trade.price * trade.quantity) / new_total_qty
        pos.quantity = new_total_qty
        notional = pos.quantity * pos.entry_price
        pos.isolated_margin = notional / pos.leverage
        pos.allocated_margin = pos.isolated_margin

        tier = self.risk_engine.get_risk_tier(pos.symbol, notional)
        pos.liquidation_price = self.risk_engine.calculate_liquidation_price(
            pos.entry_price, pos.side, pos.leverage, tier.maintenance_margin_rate,
            quantity=pos.quantity, margin_mode=pos.margin_type
        )
        pos.bankruptcy_price = self.risk_engine.calculate_bankruptcy_price(pos.entry_price, pos.side, pos.leverage)

    def _decrease_or_flip_position(self, pos_key: Tuple[str, PositionSide], pos: Position, trade: Trade):
        if trade.quantity > pos.quantity:
            # Flip: close existing + open new in opposite direction
            remaining_qty = trade.quantity - pos.quantity
            realized_pnl = self.risk_engine.calculate_unrealized_pnl(pos.side, pos.quantity, pos.entry_price, trade.price)
            trade.realized_pnl = realized_pnl

            # Delete old position
            del self.positions[pos_key]

            # Open flipped
            new_side = PositionSide.SHORT if pos.side == PositionSide.LONG else PositionSide.LONG
            new_key = (trade.symbol, new_side)
            flip_trade = Trade(
                trade_id=str(uuid.uuid4()),
                order_id=trade.order_id,
                symbol=trade.symbol,
                side=trade.side,
                quantity=remaining_qty,
                price=trade.price,
                fee=trade.fee,
                fee_type=trade.fee_type,
                position_side=new_side,
                leverage=trade.leverage,
            )
            self._open_new_position(new_key, flip_trade)
            self.trades.append(flip_trade)

        elif trade.quantity == pos.quantity:
            # Full close
            realized_pnl = self.risk_engine.calculate_unrealized_pnl(pos.side, pos.quantity, pos.entry_price, trade.price)
            trade.realized_pnl = realized_pnl
            self.wallet.balance += realized_pnl
            del self.positions[pos_key]
        else:
            # Partial close
            realized_pnl = self.risk_engine.calculate_unrealized_pnl(pos.side, trade.quantity, pos.entry_price, trade.price)
            trade.realized_pnl = realized_pnl
            self.wallet.balance += realized_pnl

            old_qty = pos.quantity
            pos.quantity -= trade.quantity
            margin_released = pos.isolated_margin * (trade.quantity / old_qty)
            pos.isolated_margin -= margin_released
            pos.allocated_margin = max(Decimal("0"), pos.allocated_margin - margin_released)

            notional = pos.quantity * pos.entry_price
            tier = self.risk_engine.get_risk_tier(pos.symbol, notional)
            pos.liquidation_price = self.risk_engine.calculate_liquidation_price(
                pos.entry_price, pos.side, pos.leverage, tier.maintenance_margin_rate,
                quantity=pos.quantity, margin_mode=pos.margin_type
            )

    def _apply_trade_to_position(self, pos_key: Tuple[str, PositionSide], trade: Trade):
        if pos_key not in self.positions:
            self._open_new_position(pos_key, trade)
        else:
            pos = self.positions[pos_key]
            is_increase = (
                (pos.side == PositionSide.LONG and trade.side == OrderSide.BUY) or
                (pos.side == PositionSide.SHORT and trade.side == OrderSide.SELL)
            )
            if is_increase:
                self._increase_position(pos_key, pos, trade)
            else:
                self._decrease_or_flip_position(pos_key, pos, trade)

    def cancel_order(self, order_id: str) -> bool:
        with self.lock:
            if order_id in self.orders:
                order = self.orders[order_id]
                if order.status in [OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED, OrderStatus.PENDING]:
                    order.status = OrderStatus.CANCELLED
                    self.matching_engine.get_book(order.symbol).remove_order(order_id)
                    self._save_state()
                    return True
            return False

    def cancel_all_orders(self, symbol: Optional[str] = None):
        with self.lock:
            for oid, order in list(self.orders.items()):
                if symbol and order.symbol != symbol:
                    continue
                if order.status in [OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED, OrderStatus.PENDING]:
                    order.status = OrderStatus.CANCELLED
                    self.matching_engine.get_book(order.symbol).remove_order(oid)

    def get_account_summary(self) -> Dict:
        self._recalc_available_balance()
        total_upnl = Decimal("0")
        total_notional = Decimal("0")
        total_mm = Decimal("0")

        for (symbol, side), pos in self.positions.items():
            mark_price = self.price_engine.get_mark_price(symbol)
            if mark_price > 0:
                upnl = self.risk_engine.calculate_unrealized_pnl(pos.side, pos.quantity, pos.entry_price, mark_price)
                total_upnl += upnl
                notional = pos.quantity * mark_price
                total_notional += notional
                mm = self.risk_engine.calculate_maintenance_margin(notional, symbol)
                total_mm += mm

        account_equity = self.wallet.balance + total_upnl
        margin_ratio = 0.0
        if account_equity > 0:
            margin_ratio = float(total_mm / account_equity * 100)

        return {
            "wallet_balance": str(self.wallet.balance),
            "available_balance": str(self.wallet.available_balance),
            "unrealized_pnl": str(total_upnl),
            "margin_balance": str(account_equity),
            "total_notional": str(total_notional),
            "total_maintenance_margin": str(total_mm),
            "margin_ratio": round(margin_ratio, 2),
            "positions_count": len(self.positions),
            "open_orders_count": len([o for o in self.orders.values() if o.status in (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED)]),
            "hedge_mode": self.hedge_mode,
            "initial_balance": str(self.initial_balance),
            "total_exposure": str(total_notional),
        }

    def get_all_positions(self) -> List[Dict]:
        result = []
        for (symbol, side), pos in self.positions.items():
            mark_price = self.price_engine.get_mark_price(symbol)
            upnl = Decimal("0")
            upnl_pct = Decimal("0")
            if mark_price > 0:
                upnl = self.risk_engine.calculate_unrealized_pnl(pos.side, pos.quantity, pos.entry_price, mark_price)
                notional = pos.quantity * pos.entry_price
                if notional > 0:
                    upnl_pct = (upnl / notional * 100).quantize(Decimal("0.01"))

            notional = pos.quantity * mark_price if mark_price > 0 else pos.quantity * pos.entry_price
            mm = self.risk_engine.calculate_maintenance_margin(notional, pos.symbol)
            margin_balance = pos.isolated_margin + upnl
            margin_ratio = 0.0
            if margin_balance > 0:
                margin_ratio = float(mm / margin_balance * 100)

            result.append({
                "symbol": pos.symbol,
                "side": pos.side.value,
                "entry_price": str(pos.entry_price),
                "mark_price": str(mark_price),
                "quantity": str(pos.quantity),
                "leverage": pos.leverage,
                "unrealized_pnl": str(upnl),
                "unrealized_pnl_pct": float(upnl_pct),
                "liquidation_price": str(pos.liquidation_price),
                "bankruptcy_price": str(pos.bankruptcy_price),
                "margin_type": pos.margin_type,
                "isolated_margin": str(pos.isolated_margin),
                "margin_ratio": round(margin_ratio, 2),
                "notional": str(notional),
                "funding_payment": str(pos.funding_payment),
                "entry_time": pos.entry_time,
            })
        return result

    def _save_state(self):
        try:
            data = {
                "version": 2,
                "accounts": {
                    "default": {
                        "user_id": "default",
                        "wallet": self.wallet.to_dict(),
                        "positions": {f"{k[0]}|{k[1].value}": v.to_dict() for k, v in self.positions.items()},
                        "open_orders": [o.to_dict() for o in self.orders.values() if o.status in (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED)],
                        "trade_history": [t.to_dict() for t in self.trades[-500:]],
                        "funding_history": [f.to_dict() for f in self.funding_history[-200:]],
                        "journal_entries": [e.to_dict() for e in self.journal_engine.entries[-200:]],
                        "equity_curve": self.equity_curve[-1440:],
                        "initial_balance": str(self.initial_balance),
                        "leverage_settings": self.leverage_settings,
                        "margin_mode_settings": self.margin_mode_settings,
                        "hedge_mode": self.hedge_mode,
                        "created_at": time.time(),
                    }
                }
            }
            with open(self.data_file, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f"Error saving state: {e}")

    def _load_state(self):
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, 'r') as f:
                    data = json.load(f)

                if data.get("version") == 2:
                    account = data.get("accounts", {}).get("default", {})
                else:
                    account = data

                wallet_data = account.get("wallet", {})
                self.wallet.balance = Decimal(str(wallet_data.get("balance", self.initial_balance)))
                self.wallet.available_balance = Decimal(str(wallet_data.get("available_balance", self.initial_balance)))
                self.wallet.isolated_margin = Decimal(str(wallet_data.get("isolated_margin", "0")))

                self.hedge_mode = account.get("hedge_mode", False)
                self.leverage_settings = account.get("leverage_settings", {})
                self.margin_mode_settings = account.get("margin_mode_settings", {})
                self.initial_balance = Decimal(str(account.get("initial_balance", self.initial_balance)))
                self.equity_curve = account.get("equity_curve", [])

                # Load positions
                for pos_key_str, pos_data in account.get("positions", {}).items():
                    parts = pos_key_str.split("|")
                    if len(parts) == 2:
                        sym, side_val = parts
                        pos_side = PositionSide(side_val)
                        pos = Position(
                            symbol=pos_data.get("symbol", sym),
                            side=pos_side,
                            entry_price=Decimal(str(pos_data.get("entry_price", 0))),
                            quantity=Decimal(str(pos_data.get("quantity", 0))),
                            leverage=pos_data.get("leverage", 20),
                            margin_type=pos_data.get("margin_type", "ISOLATED"),
                            isolated_margin=Decimal(str(pos_data.get("isolated_margin", 0))),
                            allocated_margin=Decimal(str(pos_data.get("allocated_margin", 0))),
                            funding_payment=Decimal(str(pos_data.get("funding_payment", 0))),
                            entry_time=pos_data.get("entry_time", time.time()),
                        )
                        self.positions[(sym, pos_side)] = pos

                for entry_data in account.get("journal_entries", []):
                    entry = JournalEntry(
                        entry_id=entry_data.get("entry_id", str(uuid.uuid4())),
                        timestamp=datetime.fromisoformat(entry_data.get("timestamp", datetime.now().isoformat())),
                        symbol=entry_data.get("symbol", ""),
                        setup_type=entry_data.get("setup_type", "MANUAL"),
                        emotion=entry_data.get("emotion", "NEUTRAL"),
                        notes=entry_data.get("notes", ""),
                        trade_id=entry_data.get("trade_id"),
                        behavioral_tags=entry_data.get("behavioral_tags", []),
                        ai_feedback=entry_data.get("ai_feedback", ""),
                    )
                    self.journal_engine.entries.append(entry)

                for trade_data in account.get("trade_history", []):
                    trade = Trade(
                        trade_id=trade_data.get("trade_id", str(uuid.uuid4())),
                        order_id=trade_data.get("order_id", ""),
                        symbol=trade_data.get("symbol", ""),
                        side=OrderSide(trade_data.get("side", "BUY")),
                        quantity=Decimal(str(trade_data.get("quantity", 0))),
                        price=Decimal(str(trade_data.get("price", 0))),
                        fee=Decimal(str(trade_data.get("fee", 0))),
                        fee_type=trade_data.get("fee_type", "TAKER"),
                        position_side=PositionSide(trade_data["position_side"]) if trade_data.get("position_side") else None,
                        realized_pnl=Decimal(str(trade_data.get("realized_pnl", "0"))),
                        funding_paid=Decimal(str(trade_data.get("funding_paid", "0"))),
                        leverage=trade_data.get("leverage", 20),
                    )
                    self.trades.append(trade)

                for fp_data in account.get("funding_history", []):
                    fp = FundingPayment(
                        symbol=fp_data.get("symbol", ""),
                        amount=Decimal(str(fp_data.get("amount", 0))),
                        rate=Decimal(str(fp_data.get("rate", 0))),
                        position_size=Decimal(str(fp_data.get("position_size", 0))),
                        position_side=PositionSide(fp_data["position_side"]) if fp_data.get("position_side") else PositionSide.LONG,
                        mark_price=Decimal(str(fp_data.get("mark_price", 0))),
                    )
                    self.funding_history.append(fp)

                print(f"Loaded state: balance={self.wallet.balance}, trades={len(self.trades)}, positions={len(self.positions)}")
            except Exception as e:
                print(f"Error loading state: {e}")

    def reset_account(self, initial_balance: Decimal = Decimal("10000")) -> Dict:
        with self.lock:
            self.wallet = Wallet(asset="USDT", balance=initial_balance, available_balance=initial_balance, withdrawable_balance=initial_balance)
            self.positions.clear()
            self.orders.clear()
            self.trades.clear()
            self.funding_history.clear()
            self.equity_curve.clear()
            self.initial_balance = initial_balance
            self.journal_engine.entries.clear()
            self.leverage_settings.clear()
            self.margin_mode_settings.clear()
            self._total_margin_used = Decimal("0")
            self._record_equity_snapshot()
            self._save_state()
        return {"success": True, "balance": str(initial_balance)}
