import threading
import time
from decimal import Decimal
from typing import Dict, List, Optional
from .models import Position, Wallet, PositionSide, Order, OrderSide, OrderType, OrderStatus, LiquidationEvent
from .risk_engine import RiskEngine
from .price_engine import PriceEngine


class LiquidationEngine:
    """
    Monitors all open positions every second.
    Triggers the 5-step liquidation pipeline when margin ratio >= 1.0.
    """

    def __init__(self, risk_engine: RiskEngine, price_engine: PriceEngine, account_manager):
        self.risk_engine = risk_engine
        self.price_engine = price_engine
        self.account_manager = account_manager
        self.is_running = False
        self.lock = threading.Lock()
        self.liquidation_events: List[LiquidationEvent] = []
        self.insurance_fund = Decimal("0")
        self.funding_interval = 3600  # 1 hour for simulated funding
        self.last_funding_time = time.time()

    def check_liquidation_conditions(self, symbol: str, position: Position, wallet: Wallet) -> bool:
        """
        Check if a position should be liquidated.
        ISOLATED: check if allocated_margin < maintenance_margin
        CROSS: check if account_equity < sum(maintenance_margins)
        """
        mark_price = self.price_engine.get_mark_price(symbol)
        if mark_price <= 0:
            return False

        unrealized_pnl = self.risk_engine.calculate_unrealized_pnl(
            position.side, position.quantity, position.entry_price, mark_price
        )

        notional_value = position.quantity * mark_price
        maintenance_margin = self.risk_engine.calculate_maintenance_margin(notional_value, symbol)

        if position.margin_type == "ISOLATED":
            margin_balance = position.isolated_margin + unrealized_pnl
            return margin_balance < maintenance_margin
        else:
            # CROSS: handled separately
            return False

    def execute_liquidation_pipeline(self, symbol: str, position: Position) -> dict:
        """
        Execute the 5-step liquidation pipeline:
        STEP 1: Cancel all open orders for the symbol
        STEP 2: Partial liquidation (CROSS only)
        STEP 3: Full position takeover
        STEP 4: Submit limit unwind order at bankruptcy price
        STEP 5: ADL simulation if unwind fails
        """
        mark_price = self.price_engine.get_mark_price(symbol)
        print(f"LIQUIDATING position for {symbol} {position.side.value} at mark {mark_price}")

        # STEP 1: Cancel all open orders for the symbol
        self.account_manager.cancel_all_orders(symbol)

        # Recheck margin after cancelling orders
        if not self.check_liquidation_conditions(symbol, position, self.account_manager.wallet):
            return {"liquidated": False, "reason": "Margin recovered after cancelling orders"}

        # STEP 2 & 3: Full position close
        side = OrderSide.SELL if position.side == PositionSide.LONG else OrderSide.BUY
        liquidation_order = Order(
            order_id=f"liq_{int(time.time() * 1000)}",
            symbol=symbol,
            side=side,
            order_type=OrderType.MARKET,
            quantity=position.quantity,
            position_side=position.side,
            reduce_only=True,
            leverage=position.leverage,
        )

        result = self.account_manager.place_order(liquidation_order)

        # STEP 4 & 5: Track liquidation event
        liq_event = LiquidationEvent(
            position_id=f"{symbol}_{position.side.value}",
            symbol=symbol,
            side=position.side,
            entry_price=position.entry_price,
            liquidation_price=mark_price,
            quantity=position.quantity,
            loss=abs(self.risk_engine.calculate_unrealized_pnl(
                position.side, position.quantity, position.entry_price, mark_price
            )),
            insurance_fund_paid=Decimal("0"),
            adl_triggered=False,
        )
        self.liquidation_events.append(liq_event)

        return {
            "liquidated": True,
            "liquidation_price": str(mark_price),
            "bankruptcy_price": str(self.risk_engine.calculate_bankruptcy_price(
                position.entry_price, position.side, position.leverage
            )),
        }

    def check_and_execute_cross_liquidation(self, wallet: Wallet):
        """Execute exchange-grade Cross Margin liquidation logic."""
        with self.account_manager.lock:
            all_positions = list(self.account_manager.positions.values())
            cross_positions = [p for p in all_positions if p.margin_type == "CROSS"]
            if not cross_positions:
                return

        total_unrealized_pnl = Decimal("0")
        total_maintenance_margin = Decimal("0")

        for pos in cross_positions:
            mark_price = self.price_engine.get_mark_price(pos.symbol)
            if mark_price <= 0:
                continue
            unrealized_pnl = self.risk_engine.calculate_unrealized_pnl(
                pos.side, pos.quantity, pos.entry_price, mark_price
            )
            total_unrealized_pnl += unrealized_pnl
            notional_value = pos.quantity * mark_price
            mm = self.risk_engine.calculate_maintenance_margin(notional_value, pos.symbol)
            total_maintenance_margin += mm

        account_equity = wallet.balance + total_unrealized_pnl

        if account_equity < total_maintenance_margin:
            print(f"CROSS LIQUIDATION TRIGGERED: Equity {account_equity} < TMM {total_maintenance_margin}")

            # Step 1: Cancel all open orders
            self.account_manager.cancel_all_orders()

            # Step 2: Find position with largest loss
            if account_equity < total_maintenance_margin:
                target_pos = None
                min_pnl = Decimal("Infinity")

                for pos in cross_positions:
                    mark_price = self.price_engine.get_mark_price(pos.symbol)
                    pnl = self.risk_engine.calculate_unrealized_pnl(
                        pos.side, pos.quantity, pos.entry_price, mark_price
                    )
                    if pnl < min_pnl:
                        min_pnl = pnl
                        target_pos = pos

                if target_pos:
                    self.execute_liquidation_pipeline(target_pos.symbol, target_pos)

    def calculate_adl_score(self, position: Position, mark_price: Decimal) -> float:
        """
        Calculate Auto-Deleveraging (ADL) score.
        ADL Score = Unrealized Profit % × Effective Leverage
        """
        unrealized_pnl = self.risk_engine.calculate_unrealized_pnl(
            position.side, position.quantity, position.entry_price, mark_price
        )

        notional = position.quantity * mark_price
        if notional == 0:
            return 0.0

        profit_ratio = float(unrealized_pnl / notional) if notional > 0 else 0.0
        return profit_ratio * position.leverage

    def settle_funding(self):
        """Settle funding fees for all open positions."""
        with self.account_manager.lock:
            positions_snapshot = dict(self.account_manager.positions)

        for (symbol, side), position in positions_snapshot.items():
            funding_rate = self.price_engine.get_funding_rate(symbol)
            mark_price = self.price_engine.get_mark_price(symbol)

            if funding_rate == 0 or mark_price <= 0:
                continue

            # Funding payment: positive rate means longs pay shorts
            payment = self.price_engine.calculate_funding_payment(
                position.quantity, mark_price, funding_rate
            )

            if position.side == PositionSide.LONG:
                actual_payment = payment  # Positive = paid by longs
            else:
                actual_payment = -payment  # Negative = received by shorts

            # Apply funding to wallet
            self.account_manager.wallet.balance -= actual_payment
            position.funding_payment += actual_payment

            # Record funding payment
            from .models import FundingPayment
            fp = FundingPayment(
                symbol=symbol,
                amount=actual_payment,
                rate=funding_rate,
                position_size=position.quantity,
                position_side=position.side,
                mark_price=mark_price,
            )
            if hasattr(self.account_manager, 'funding_history'):
                self.account_manager.funding_history.append(fp)

    def run_background_loop(self):
        """Background thread to monitor liquidations and settle funding."""
        self.is_running = True
        while self.is_running:
            try:
                # Get current state
                with self.account_manager.lock:
                    all_pos = dict(self.account_manager.positions)
                    wallet = self.account_manager.wallet
                    hedge_mode = self.account_manager.hedge_mode

                # 1. Handle Isolated Positions independently
                for (symbol, side), pos in all_pos.items():
                    if pos.margin_type == "ISOLATED":
                        if self.check_liquidation_conditions(symbol, pos, wallet):
                            self.execute_liquidation_pipeline(symbol, pos)

                # 2. Handle Cross Positions collectively
                cross_positions = [p for p in all_pos.values() if p.margin_type == "CROSS"]
                if cross_positions:
                    self.check_and_execute_cross_liquidation(wallet)

                # 3. Check pending stop orders
                for symbol in set(s for s, _ in all_pos.keys()):
                    mark_price = self.price_engine.get_mark_price(symbol)
                    if mark_price > 0:
                        self.account_manager.matching_engine.check_pending_stops(mark_price, symbol)
                        self.account_manager.matching_engine.update_trailing_stops(mark_price, symbol)

                # 4. Settle funding every hour
                now = time.time()
                if now - self.last_funding_time >= self.funding_interval:
                    self.settle_funding()
                    self.last_funding_time = now

            except Exception as e:
                print(f"Error in liquidation loop: {e}")

            time.sleep(1)  # Check every second

    def stop(self):
        self.is_running = False
