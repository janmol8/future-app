from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .primitives import PatternCandidate


@dataclass
class RiskMetrics:
    pattern_name: str
    bias: str
    entry_price: float
    stop_loss: float
    target_classical: float
    target_conservative: float
    target_bulkowski: float
    risk_amount: Optional[float]
    risk_pct: Optional[float]
    reward_classical: Optional[float]
    rr_classical: Optional[float]
    rr_bulkowski: Optional[float]
    position_size_units: Optional[float]
    position_size_value: Optional[float]
    max_loss_inr: Optional[float]
    lot_size: Optional[int]
    lots: Optional[float]


class RiskEngine:
    @staticmethod
    def compute(pattern: PatternCandidate, entry_price: float, account_size: float = 100000, risk_pct_per_trade: float = 1.0, lot_size: int = 1) -> RiskMetrics:
        stop = float(pattern.stop_loss)
        entry = float(entry_price)
        risk_per_unit = abs(entry - stop)
        target_risk_amount = max(0.0, (risk_pct_per_trade / 100.0) * account_size)
        if risk_per_unit <= 0 or entry <= 0:
            units = lots = position_value = risk_pct = rr_classical = rr_bulkowski = reward_classical = None
        else:
            units = target_risk_amount / risk_per_unit
            lots = (units / lot_size) if lot_size and lot_size > 0 else None
            position_value = units * entry
            risk_pct = (risk_per_unit / entry) * 100
            reward_classical = abs(pattern.targets.classical - entry)
            rr_classical = reward_classical / risk_per_unit
            rr_bulkowski = abs(pattern.targets.bulkowski - entry) / risk_per_unit
        return RiskMetrics(
            pattern_name=pattern.name,
            bias=pattern.bias,
            entry_price=round(entry, 4),
            stop_loss=round(stop, 4),
            target_classical=round(pattern.targets.classical, 4),
            target_conservative=round(pattern.targets.conservative, 4),
            target_bulkowski=round(pattern.targets.bulkowski, 4),
            risk_amount=round(risk_per_unit, 4) if risk_per_unit > 0 else None,
            risk_pct=round(risk_pct, 2) if risk_pct is not None else None,
            reward_classical=round(reward_classical, 4) if reward_classical is not None else None,
            rr_classical=round(rr_classical, 2) if rr_classical is not None else None,
            rr_bulkowski=round(rr_bulkowski, 2) if rr_bulkowski is not None else None,
            position_size_units=round(units, 2) if units is not None else None,
            position_size_value=round(position_value, 2) if position_value is not None else None,
            max_loss_inr=round(target_risk_amount, 2),
            lot_size=lot_size if lot_size and lot_size > 0 else None,
            lots=round(lots, 2) if lots is not None else None,
        )

