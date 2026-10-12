import time
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum
from decimal import Decimal
from datetime import datetime
import uuid

# ─── Fee Constants ────────────────────────────────────────────────────────────
MAKER_FEE = Decimal("0.0002")    # 0.02%
TAKER_FEE = Decimal("0.00055")   # 0.055%

# ─── Enums ────────────────────────────────────────────────────────────────────

class OrderStatus(Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    TRIGGERED = "TRIGGERED"

class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"
    STOP_LIMIT = "STOP_LIMIT"
    TRAILING_STOP = "TRAILING_STOP"
    OCO = "OCO"
    TWAP = "TWAP"
    ICEBERG = "ICEBERG"
    FOK = "FOK"
    IOC = "IOC"

class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"

class TimeInForce(Enum):
    GTC = "GTC"   # Good Till Cancelled
    IOC = "IOC"   # Immediate Or Cancel
    FOK = "FOK"   # Fill Or Kill
    GTD = "GTD"   # Good Till Date

class PositionSide(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    BOTH = "BOTH"

class MarginMode(Enum):
    CROSS = "CROSS"
    ISOLATED = "ISOLATED"

class EmotionType(Enum):
    GREED = "GREED"
    FEAR = "FEAR"
    CALM = "CALM"
    IMPATIENCE = "IMPATIENCE"
    REVENGE = "REVENGE"
    FOMO = "FOMO"
    NEUTRAL = "NEUTRAL"

# ─── Risk Tiers ───────────────────────────────────────────────────────────────

@dataclass
class RiskTier:
    tier: int
    bracket_max_value: Decimal
    maintenance_margin_rate: Decimal
    cum_maintenance_margin: Decimal
    max_leverage: int

# BTCUSDT Risk Tiers (Binance-style)
BTCUSDT_RISK_TIERS = [
    RiskTier(1,  Decimal("50000"),     Decimal("0.0040"), Decimal("0"),      125),
    RiskTier(2,  Decimal("250000"),    Decimal("0.0050"), Decimal("50"),     100),
    RiskTier(3,  Decimal("1000000"),   Decimal("0.0100"), Decimal("1300"),   50),
    RiskTier(4,  Decimal("2000000"),   Decimal("0.0250"), Decimal("16300"),  20),
    RiskTier(5,  Decimal("5000000"),   Decimal("0.0500"), Decimal("66300"),  10),
    RiskTier(6,  Decimal("10000000"),  Decimal("0.1000"), Decimal("316300"), 5),
    RiskTier(7,  Decimal("20000000"),  Decimal("0.1250"), Decimal("566300"), 4),
    RiskTier(8,  Decimal("50000000"),  Decimal("0.1500"), Decimal("1066300"),3),
    RiskTier(9,  Decimal("100000000"), Decimal("0.2500"), Decimal("6066300"),2),
    RiskTier(10, Decimal("200000000"), Decimal("0.5000"), Decimal("31066300"),1),
]

# Default altcoin tiers (more conservative)
DEFAULT_RISK_TIERS = [
    RiskTier(1, Decimal("50000"),    Decimal("0.010"), Decimal("0"),     50),
    RiskTier(2, Decimal("250000"),   Decimal("0.015"), Decimal("250"),   25),
    RiskTier(3, Decimal("1000000"),  Decimal("0.025"), Decimal("2750"),  20),
    RiskTier(4, Decimal("5000000"),  Decimal("0.050"), Decimal("27750"), 10),
    RiskTier(5, Decimal("20000000"), Decimal("0.100"), Decimal("277750"),5),
]

# ─── Data Models ──────────────────────────────────────────────────────────────

@dataclass
class Order:
    order_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    symbol: str = ""
    side: OrderSide = OrderSide.BUY
    order_type: OrderType = OrderType.MARKET
    quantity: Decimal = Decimal("0")
    price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    trigger_price: Optional[Decimal] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: Decimal = Decimal("0")
    avg_fill_price: Decimal = Decimal("0")
    time_in_force: TimeInForce = TimeInForce.GTC
    timestamp: datetime = field(default_factory=datetime.now)
    client_order_id: Optional[str] = None
    position_side: Optional[PositionSide] = None
    reduce_only: bool = False
    post_only: bool = False
    trailing_delta: Optional[Decimal] = None
    iceberg_qty: Optional[Decimal] = None
    visible_quantity: Optional[Decimal] = None
    leverage: int = 20
    margin_mode: str = "ISOLATED"
    expire_at: Optional[float] = None
    oco_sibling_id: Optional[str] = None
    twap_config: Optional[Dict] = None
    trailing_callback_pct: Optional[Decimal] = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "quantity": str(self.quantity),
            "price": str(self.price) if self.price else None,
            "stop_price": str(self.stop_price) if self.stop_price else None,
            "trigger_price": str(self.trigger_price) if self.trigger_price else None,
            "status": self.status.value,
            "filled_quantity": str(self.filled_quantity),
            "avg_fill_price": str(self.avg_fill_price),
            "time_in_force": self.time_in_force.value,
            "position_side": self.position_side.value if self.position_side else None,
            "reduce_only": self.reduce_only,
            "post_only": self.post_only,
            "leverage": self.leverage,
            "margin_mode": self.margin_mode,
            "created_at": self.created_at,
        }

@dataclass
class Trade:
    trade_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    order_id: str = ""
    symbol: str = ""
    side: OrderSide = OrderSide.BUY
    quantity: Decimal = Decimal("0")
    price: Decimal = Decimal("0")
    fee: Decimal = Decimal("0")
    fee_type: str = "TAKER"
    timestamp: datetime = field(default_factory=datetime.now)
    is_maker: bool = False
    position_side: Optional[PositionSide] = None
    realized_pnl: Decimal = Decimal("0")
    funding_paid: Decimal = Decimal("0")
    leverage: int = 20

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": str(self.quantity),
            "price": str(self.price),
            "fee": str(self.fee),
            "fee_type": self.fee_type,
            "timestamp": self.timestamp.isoformat(),
            "position_side": self.position_side.value if self.position_side else None,
            "realized_pnl": str(self.realized_pnl),
            "funding_paid": str(self.funding_paid),
            "leverage": self.leverage,
        }

@dataclass
class Position:
    symbol: str = ""
    side: PositionSide = PositionSide.LONG
    entry_price: Decimal = Decimal("0")
    quantity: Decimal = Decimal("0")
    leverage: int = 20
    unrealized_pnl: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    liquidation_price: Decimal = Decimal("0")
    bankruptcy_price: Decimal = Decimal("0")
    margin_type: str = "ISOLATED"
    isolated_margin: Decimal = Decimal("0")
    allocated_margin: Decimal = Decimal("0")
    timestamp: datetime = field(default_factory=datetime.now)
    funding_payment: Decimal = Decimal("0")
    entry_time: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side.value,
            "entry_price": str(self.entry_price),
            "quantity": str(self.quantity),
            "leverage": self.leverage,
            "unrealized_pnl": str(self.unrealized_pnl),
            "realized_pnl": str(self.realized_pnl),
            "liquidation_price": str(self.liquidation_price),
            "bankruptcy_price": str(self.bankruptcy_price),
            "margin_type": self.margin_type,
            "isolated_margin": str(self.isolated_margin),
            "allocated_margin": str(self.allocated_margin),
            "funding_payment": str(self.funding_payment),
            "entry_time": self.entry_time,
        }

@dataclass
class Wallet:
    asset: str = "USDT"
    balance: Decimal = Decimal("10000")
    available_balance: Decimal = Decimal("10000")
    withdrawable_balance: Decimal = Decimal("10000")
    isolated_margin: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset": self.asset,
            "balance": str(self.balance),
            "available_balance": str(self.available_balance),
            "withdrawable_balance": str(self.withdrawable_balance),
            "isolated_margin": str(self.isolated_margin),
            "unrealized_pnl": str(self.unrealized_pnl),
        }

@dataclass
class FundingPayment:
    symbol: str = ""
    amount: Decimal = Decimal("0")
    rate: Decimal = Decimal("0")
    position_size: Decimal = Decimal("0")
    position_side: PositionSide = PositionSide.LONG
    mark_price: Decimal = Decimal("0")
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "amount": str(self.amount),
            "rate": str(self.rate),
            "position_size": str(self.position_size),
            "position_side": self.position_side.value,
            "mark_price": str(self.mark_price),
            "timestamp": self.timestamp.isoformat(),
        }

@dataclass
class JournalEntry:
    entry_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=datetime.now)
    symbol: str = ""
    setup_type: str = "MANUAL"
    emotion: str = "NEUTRAL"
    notes: str = ""
    trade_id: Optional[str] = None
    screenshots: List[str] = field(default_factory=list)
    behavioral_tags: List[str] = field(default_factory=list)
    ai_feedback: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "setup_type": self.setup_type,
            "emotion": self.emotion,
            "notes": self.notes,
            "trade_id": self.trade_id,
            "behavioral_tags": self.behavioral_tags,
            "ai_feedback": self.ai_feedback,
        }

@dataclass
class LiquidationEvent:
    position_id: str = ""
    symbol: str = ""
    side: PositionSide = PositionSide.LONG
    entry_price: Decimal = Decimal("0")
    liquidation_price: Decimal = Decimal("0")
    quantity: Decimal = Decimal("0")
    loss: Decimal = Decimal("0")
    insurance_fund_paid: Decimal = Decimal("0")
    adl_triggered: bool = False
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "position_id": self.position_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "entry_price": str(self.entry_price),
            "liquidation_price": str(self.liquidation_price),
            "quantity": str(self.quantity),
            "loss": str(self.loss),
            "insurance_fund_paid": str(self.insurance_fund_paid),
            "adl_triggered": self.adl_triggered,
            "timestamp": self.timestamp.isoformat(),
        }
