from decimal import Decimal, ROUND_DOWN
from typing import List, Optional, Tuple
from .models import RiskTier, BTCUSDT_RISK_TIERS, DEFAULT_RISK_TIERS, Position, Order, OrderSide, PositionSide


class RiskEngine:
    """
    Handles risk limits, margin calculations, and pre-trade validation.
    Implements Binance Futures-style risk management.
    """

    def __init__(self, risk_tiers: List[RiskTier] = BTCUSDT_RISK_TIERS):
        self.risk_tiers = risk_tiers
        self.min_order_value = Decimal("10")

    def get_risk_tier(self, symbol: str, notional_value: Decimal) -> RiskTier:
        tiers = BTCUSDT_RISK_TIERS if "BTC" in symbol else DEFAULT_RISK_TIERS
        for tier in tiers:
            if notional_value <= tier.bracket_max_value:
                return tier
        return tiers[-1]

    def calculate_initial_margin(self, notional_value: Decimal, leverage: int) -> Decimal:
        if leverage <= 0:
            return notional_value
        return (notional_value / Decimal(str(leverage))).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)

    def calculate_maintenance_margin(self, notional_value: Decimal, symbol: str = "BTCUSDT") -> Decimal:
        tier = self.get_risk_tier(symbol, notional_value)
        mm = (notional_value * tier.maintenance_margin_rate) - tier.cum_maintenance_margin
        return max(mm, Decimal("0"))

    def calculate_liquidation_price(
        self,
        entry_price: Decimal,
        side: PositionSide,
        leverage: int,
        maintenance_margin_rate: Decimal,
        wallet_balance: Decimal = Decimal("0"),
        other_pnl: Decimal = Decimal("0"),
        deduction: Decimal = Decimal("0"),
        quantity: Decimal = Decimal("0"),
        margin_mode: str = "ISOLATED"
    ) -> Decimal:
        if quantity <= 0 or entry_price <= 0:
            return Decimal("0")

        im = Decimal("1") / Decimal(str(leverage))
        mmr = maintenance_margin_rate

        if margin_mode == "ISOLATED":
            if side == PositionSide.LONG:
                liq = entry_price * (Decimal("1") - im + mmr) - deduction / quantity
            else:
                liq = entry_price * (Decimal("1") + im - mmr) + deduction / quantity
        else:
            if side == PositionSide.LONG:
                numerator = wallet_balance + other_pnl + deduction - quantity * entry_price
                denominator = quantity * (mmr - Decimal("1"))
                liq = numerator / denominator if denominator != 0 else entry_price * (Decimal("1") - im + mmr)
            else:
                numerator = wallet_balance + other_pnl + deduction + quantity * entry_price
                denominator = quantity * (mmr + Decimal("1"))
                liq = numerator / denominator if denominator != 0 else entry_price * (Decimal("1") + im - mmr)

        return max(liq, Decimal("0")).quantize(Decimal("0.01"))

    def calculate_bankruptcy_price(self, entry_price: Decimal, side: PositionSide, leverage: int) -> Decimal:
        im = Decimal("1") / Decimal(str(leverage))
        if side == PositionSide.LONG:
            return (entry_price * (Decimal("1") - im)).quantize(Decimal("0.01"))
        else:
            return (entry_price * (Decimal("1") + im)).quantize(Decimal("0.01"))

    def calculate_margin_ratio(self, maintenance_margin: Decimal, account_equity: Decimal) -> Decimal:
        if account_equity <= 0:
            return Decimal("999")
        return maintenance_margin / account_equity

    def calculate_unrealized_pnl(
        self,
        side: PositionSide,
        quantity: Decimal,
        entry_price: Decimal,
        mark_price: Decimal
    ) -> Decimal:
        if side == PositionSide.LONG:
            return (quantity * (mark_price - entry_price)).quantize(Decimal("0.00000001"))
        else:
            return (quantity * (entry_price - mark_price)).quantize(Decimal("0.00000001"))

    def calculate_roi(self, unrealized_pnl: Decimal, initial_margin: Decimal) -> Decimal:
        if initial_margin == 0:
            return Decimal("0")
        return ((unrealized_pnl / initial_margin) * Decimal("100")).quantize(Decimal("0.01"))

    def calculate_effective_leverage(self, position_notional: Decimal, account_equity: Decimal) -> Decimal:
        if account_equity <= 0:
            return Decimal("0")
        return (position_notional / account_equity).quantize(Decimal("0.01"))

    def validate_order(
        self,
        order: Order,
        wallet_balance: Decimal,
        current_position: Optional[Position],
        mark_price: Decimal = Decimal("0"),
        available_balance: Decimal = Decimal("0")
    ) -> Tuple[bool, str]:
        if order.quantity <= 0:
            return False, "Quantity must be positive"

        price = order.price if order.price else mark_price
        if price <= 0:
            return False, "Invalid price for notional calculation"

        notional_value = order.quantity * price

        if notional_value < self.min_order_value:
            return False, f"Order value ${notional_value:.2f} below minimum ${self.min_order_value}"

        if order.leverage <= 0:
            return False, "Leverage must be positive"

        initial_margin = self.calculate_initial_margin(notional_value, order.leverage)
        required = initial_margin

        print(f"[FuturesRisk] order={order.order_id} symbol={order.symbol} notional={notional_value} required_margin={required} available={available_balance}")
        if not order.reduce_only and required > available_balance:
            return False, f"Insufficient margin. Need ${required:.2f}, have ${available_balance:.2f}"

        if order.reduce_only:
            if not current_position:
                return False, "Reduce-only order requires an existing position"
            if order.side == OrderSide.BUY and current_position.side != PositionSide.SHORT:
                return False, "Cannot BUY to reduce a LONG position"
            elif order.side == OrderSide.SELL and current_position.side != PositionSide.LONG:
                return False, "Cannot SELL to reduce a SHORT position"
            if order.quantity > current_position.quantity:
                return False, "Reduce-only quantity exceeds position size"

        if mark_price > 0 and order.price:
            deviation = abs(order.price - mark_price) / mark_price
            if deviation > Decimal("0.10"):
                return False, f"Price {order.price} is >10% from mark price {mark_price}"

        if order.post_only and mark_price > 0 and order.price:
            if order.side == OrderSide.BUY and order.price >= mark_price:
                return False, "Post-only buy order would cross the spread"
            elif order.side == OrderSide.SELL and order.price <= mark_price:
                return False, "Post-only sell order would cross the spread"

        return True, ""
