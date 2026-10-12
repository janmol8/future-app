from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional

PatternState = Literal["forming", "emerging", "confirmed", "stale", "invalidated"]
PatternBias = Literal["bull", "bear", "neutral"]
PatternType = Literal["reversal", "continuation", "bilateral", "event"]


@dataclass
class Candle:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class Pivot:
    index: int
    kind: Literal["high", "low"]
    price: float
    atr: float
    confirmed_at: int
    prominence_atr: float
    strength: int


@dataclass
class PatternTarget:
    classical: float
    conservative: float
    bulkowski: float


@dataclass
class PatternCandidate:
    id: str
    name: str
    family: Literal["classic", "candlestick", "event", "volatility"]
    type: PatternType
    bias: PatternBias
    state: PatternState
    start_index: int
    end_index: int
    pivots: List[Pivot]
    upper_line: Optional[dict]
    lower_line: Optional[dict]
    breakout_index: Optional[int]
    breakout_price: Optional[float]
    stop_loss: float
    targets: PatternTarget
    confidence: int
    reasons: List[str]
    invalidated_by: Optional[str] = None
    morph_history: List[str] = field(default_factory=list)
    nested_in: Optional[str] = None
    conflicted: bool = False
    event_risk: bool = False
    low_confidence: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    draw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self, candles: Optional[List[Candle]] = None) -> Dict[str, Any]:
        data = asdict(self)
        if candles:
            data["start_time"] = candles[self.start_index].time if self.start_index < len(candles) else None
            data["end_time"] = candles[self.end_index].time if self.end_index < len(candles) else None
        data["stats"] = PATTERN_STATS.get(self.name, {})
        return data


PATTERN_STATS = {
    "Head and Shoulders": {"avg_move": -22, "failure_rate": 4, "target_hit": 74, "pullback": 45},
    "Inverse Head and Shoulders": {"avg_move": 38, "failure_rate": 3, "target_hit": 74, "pullback": 61},
    "Double Top": {"avg_move": -17, "failure_rate": 11, "target_hit": 65, "pullback": 59},
    "Double Bottom": {"avg_move": 41, "failure_rate": 8, "target_hit": 70, "pullback": 64},
    "Triple Top": {"avg_move": -19, "failure_rate": 9, "target_hit": 81, "pullback": None},
    "Triple Bottom": {"avg_move": 37, "failure_rate": 7, "target_hit": 83, "pullback": None},
    "Ascending Triangle": {"avg_move": 35, "failure_rate": 13, "target_hit": 77, "pullback": 57},
    "Descending Triangle": {"avg_move": -19, "failure_rate": 16, "target_hit": 73, "pullback": None},
    "Symmetrical Triangle Up": {"avg_move": 28, "failure_rate": 11, "target_hit": 68, "pullback": None},
    "Symmetrical Triangle Down": {"avg_move": -16, "failure_rate": 16, "target_hit": 68, "pullback": None},
    "Rising Wedge": {"avg_move": -18, "failure_rate": 10, "target_hit": 72, "pullback": None},
    "Falling Wedge": {"avg_move": 38, "failure_rate": 8, "target_hit": 76, "pullback": None},
    "Cup and Handle": {"avg_move": 34, "failure_rate": 6, "target_hit": 74, "pullback": None},
    "Bull Flag": {"avg_move": 23, "failure_rate": 4, "target_hit": 64, "pullback": None},
    "Bear Flag": {"avg_move": -11, "failure_rate": 5, "target_hit": None, "pullback": None},
    "Broadening Top": {"avg_move": -15, "failure_rate": 15, "target_hit": 54, "pullback": None},
    "Diamond Top": {"avg_move": -21, "failure_rate": 6, "target_hit": 76, "pullback": None},
    "Diamond Bottom": {"avg_move": 36, "failure_rate": 4, "target_hit": 81, "pullback": None},
    "BARR Top": {"avg_move": -19, "failure_rate": 14, "target_hit": None, "pullback": None},
    "BARR Bottom": {"avg_move": 55, "failure_rate": 5, "target_hit": None, "pullback": None},
    "Island Reversal Top": {"avg_move": -22, "failure_rate": None, "target_hit": 70, "pullback": None},
    "Three Falling Peaks": {"avg_move": -19, "failure_rate": None, "target_hit": 81, "pullback": None},
    "Three Rising Valleys": {"avg_move": 41, "failure_rate": 6, "target_hit": 83, "pullback": None},
    "Pipe Bottom": {"avg_move": 34, "failure_rate": None, "target_hit": 76, "pullback": None},
    "Pipe Top": {"avg_move": -20, "failure_rate": None, "target_hit": 70, "pullback": None},
    "Horn Bottom": {"avg_move": None, "failure_rate": None, "target_hit": 76, "pullback": None},
    "Horn Top": {"avg_move": None, "failure_rate": None, "target_hit": 70, "pullback": None},
    "Long Island": {"avg_move": 21, "failure_rate": 7, "target_hit": 81, "pullback": None},
    "Diving Board": {"avg_move": 73, "failure_rate": 4, "target_hit": 71, "pullback": None},
    "Dead Cat Bounce": {"avg_move": -18, "failure_rate": 18, "target_hit": None, "pullback": None},
}


CONFIDENCE_CEILINGS = {
    "Head and Shoulders": 95,
    "Inverse Head and Shoulders": 95,
    "Double Top": 90,
    "Double Bottom": 90,
    "Island Reversal Top": 90,
    "Island Reversal Bottom": 90,
    "Triangle": 88,
    "Diamond Top": 80,
    "Diamond Bottom": 80,
    "Dead Cat Bounce": 82,
    "Broadening": 72,
    "Candlestick": 75,
}


def clamp_confidence(name: str, confidence: float) -> int:
    ceiling = 100
    for key, value in CONFIDENCE_CEILINGS.items():
        if key in name:
            ceiling = value
            break
    return max(0, min(ceiling, int(round(confidence))))

