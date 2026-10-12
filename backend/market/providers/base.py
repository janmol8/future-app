"""Provider abstract base classes and canonical data shapes.

Rules enforced by these types:
  - A Quote never mixes fields from two providers or two timestamps.
  - Missing data is None, never 0 (Rule 1).
  - Every record carries provider, as_of (provider time if known) and
    fetched_at (our fetch time, UTC ISO).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date

from ..timeutil import iso_utc


@dataclass
class Quote:
    symbol: str
    exchange: str | None = None
    last: float | None = None            # LTP; None when unknown (never 0)
    prev_close: float | None = None
    change: float | None = None
    change_pct: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: int | None = None
    value: float | None = None           # traded value if the source gives it
    as_of: str | None = None             # provider timestamp (ISO)
    fetched_at: str = field(default_factory=iso_utc)
    provider: str = ""
    price_kind: str = "ltp"              # ltp | indicative (pre-open IEP) | close
    session: str | None = None           # PRE_OPEN / REGULAR / POST_MARKET / CLOSED / HOLIDAY
    is_stale: bool = False

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "last": self.last,
            "prev_close": self.prev_close,
            "change": self.change,
            "change_pct": self.change_pct,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "volume": self.volume,
            "value": self.value,
            "as_of": self.as_of,
            "fetched_at": self.fetched_at,
            "provider": self.provider,
            "price_kind": self.price_kind,
            "session": self.session,
            "is_stale": self.is_stale,
        }


@dataclass
class MarketStatus:
    state: str                           # PRE_OPEN | REGULAR | POST_MARKET | CLOSED | HOLIDAY
                                       # (+ SPECIAL_PRE_OPEN / SPECIAL_SESSION when exposed)
    holiday_name: str | None = None
    next_open: str | None = None         # ISO IST instant
    as_of: str = field(default_factory=iso_utc)
    provider: str = ""


@dataclass
class Instrument:
    """One row of an exchange instrument master."""
    symbol: str
    name: str
    exchange: str                        # NSE | BSE
    isin: str | None = None
    series: str | None = None
    listing_date: date | None = None
    bse_code: str | None = None          # BSE numeric scrip code, when exchange == BSE
    face_value: float | None = None
    provider: str = ""


@dataclass
class Candle:
    time: int                            # epoch seconds (UTC)
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


@dataclass
class IndexDefinition:
    symbol: str
    name: str
    exchange: str


@dataclass
class IPORecord:
    """IPO fields; anything the source lacks stays None and renders as N/A."""
    name: str
    ipo_type: str | None = None          # Mainboard | SME
    status: str | None = None            # Upcoming | Open | Closed | Allotment | Listed | Withdrawn | Postponed
    open_date: date | None = None
    close_date: date | None = None
    allotment_date: date | None = None
    listing_date: date | None = None
    price_band_low: float | None = None
    price_band_high: float | None = None
    issue_price: float | None = None
    lot_size: int | None = None
    issue_size_cr: float | None = None
    fresh_issue_cr: float | None = None
    ofs_cr: float | None = None
    exchanges: list[str] = field(default_factory=list)
    isin: str | None = None
    provider: str = ""
    fetched_at: str = field(default_factory=iso_utc)


@dataclass
class IpoSubscription:
    ipo_name: str
    as_of: str
    total_x: float | None = None         # overall subscription (times)
    qib_x: float | None = None
    nii_x: float | None = None
    retail_x: float | None = None
    source: str = ""


@dataclass
class GmpSnapshot:
    ipo_name: str
    gmp_rupees: float
    as_of: str
    source: str
    gmp_pct: float | None = None         # vs price-band midpoint/issue price
    estimated_listing_price: float | None = None


class MarketDataProvider(ABC):
    """Capability-flagged market data provider interface."""

    name: str = "base"
    capabilities: frozenset[str] = frozenset()

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    @abstractmethod
    def get_market_status(self) -> MarketStatus | None:
        ...

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """Batched quotes. Returns only symbols the provider could price;
        absent keys mean 'not available from this provider' (never fake)."""
        return {}

    def get_index_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        return self.get_quotes(symbols)

    def get_index_constituents(self, index_symbol: str) -> list[str]:
        return []

    def get_instrument_master(self, exchange: str) -> list[Instrument]:
        return []

    def get_historical_candles(self, symbol: str, interval: str,
                               start: date, end: date) -> list[Candle]:
        return []

    def get_holiday_calendar(self, year: int) -> dict[str, str]:
        """{"YYYY-MM-DD": name} for the given year; empty if unsupported."""
        return {}

    def status(self) -> dict:
        return {"name": self.name}


class IPODataProvider(ABC):
    name: str = "base"
    capabilities: frozenset[str] = frozenset()   # list, detail, subscription, listing_info

    @abstractmethod
    def get_ipo_list(self) -> list[IPORecord]:
        ...

    def get_subscription(self, ipo: IPORecord) -> IpoSubscription | None:
        return None


class GMPProvider(ABC):
    name: str = "base"

    @abstractmethod
    def get_gmp(self, ipo_name: str) -> GmpSnapshot | None:
        ...
