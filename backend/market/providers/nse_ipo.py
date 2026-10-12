"""Automatic IPO provider ('nse_ipo') — detects new / open IPOs from the NSE
website's public JSON feeds and normalises them to IPORecord, including the
price band, lot size and dates.

It reuses the NseSiteProvider's HTTP client (shared rate limit, circuit
breaker, daily budget and cookie warm-up) — it never opens its own
connection. Parsing is deliberately tolerant: NSE field names/formats shift,
so every field is looked up under several candidate keys and anything missing
stays None (rendered as N/A, never guessed).

Feeds used:
  /api/ipo-current-issue                       open / current issues
  /api/all-upcoming-issues?category=ipo        announced, not yet open
The first provider that returns data wins per record; the manual CSV adapter
still works alongside (CSV values only fill gaps via COALESCE in the service).
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime

from .base import IPORecord

logger = logging.getLogger(__name__)

FEEDS = (
    ("/api/ipo-current-issue", None),
    ("/api/all-upcoming-issues", {"category": "ipo"}),
)

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_DATE_FORMATS = ("%d-%b-%Y", "%d-%b-%y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y",
                 "%d %b %Y", "%b %d, %Y", "%Y-%m-%dT%H:%M:%S")


def _first(row: dict, *keys):
    lowered = {str(k).lower(): v for k, v in row.items()}
    for key in keys:
        v = lowered.get(key.lower())
        if v not in (None, "", "-", "NA", "N/A"):
            return v
    return None


def parse_numbers(text) -> list[float]:
    if text is None:
        return []
    if isinstance(text, (int, float)):
        return [float(text)]
    out = []
    for m in _NUM.findall(str(text)):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def parse_price_band(row: dict) -> tuple[float | None, float | None]:
    low = _first(row, "priceBandLow", "price_band_low", "minPrice", "lowerPrice", "floorPrice")
    high = _first(row, "priceBandHigh", "price_band_high", "maxPrice", "upperPrice", "capPrice")
    lo = parse_numbers(low)[:1]
    hi = parse_numbers(high)[:1]
    if lo and hi:
        return min(lo[0], hi[0]), max(lo[0], hi[0])
    nums = parse_numbers(_first(row, "issuePrice", "priceBand", "price_band", "priceRange", "price"))
    nums = [n for n in nums if n > 0]
    if len(nums) >= 2:
        return min(nums), max(nums)
    if len(nums) == 1:
        return nums[0], nums[0]
    return None, None


def parse_date(value) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_issue_size_cr(value) -> float | None:
    """Crore when the text says so; raw rupees (>= 1e6) are converted; anything
    ambiguous is left None rather than guessed."""
    if value in (None, ""):
        return None
    nums = parse_numbers(value)
    if not nums:
        return None
    n = nums[0]
    text = str(value).lower()
    if "cr" in text:
        return round(n, 2)
    if n >= 1_000_000:
        return round(n / 10_000_000, 2)
    return None


def record_from_row(row: dict) -> IPORecord | None:
    name = _first(row, "companyName", "company_name", "name", "company", "issuerName")
    if not name:
        return None
    series = str(_first(row, "series", "issueType", "type", "category") or "").upper()
    ipo_type = "SME" if ("SM" in series or "ST" == series or "SME" in series) else "Mainboard"
    low, high = parse_price_band(row)
    lot = parse_numbers(_first(row, "lotSize", "marketLot", "lot_size", "minBidQuantity"))
    isin = _first(row, "isin")
    return IPORecord(
        name=str(name).strip(),
        ipo_type=ipo_type,
        status=None,  # let IpoService derive Upcoming/Open/Closed from the dates
        open_date=parse_date(_first(row, "issueStartDate", "openDate", "open_date", "biddingStartDate")),
        close_date=parse_date(_first(row, "issueEndDate", "closeDate", "close_date", "biddingEndDate")),
        allotment_date=parse_date(_first(row, "allotmentDate", "allotment_date")),
        listing_date=parse_date(_first(row, "listingDate", "listing_date")),
        price_band_low=low,
        price_band_high=high,
        issue_price=None,
        lot_size=int(lot[0]) if lot and lot[0] > 0 else None,
        issue_size_cr=parse_issue_size_cr(_first(row, "issueSize", "issue_size", "totalIssueSize")),
        exchanges=["NSE"],
        isin=str(isin).strip() if isin else None,
        provider="nse_ipo",
    )


def _rows(payload) -> list[dict]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("data", "issues", "records", "result"):
            inner = payload.get(key)
            if isinstance(inner, list):
                return [r for r in inner if isinstance(r, dict)]
    return []


class NseIpoProvider:
    name = "nse_ipo"
    capabilities = frozenset({"list"})

    def __init__(self, site):
        self.site = site  # NseSiteProvider (shared HTTP layer)

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities and bool(getattr(self.site, "enabled", False))

    def status(self) -> dict:
        return {"name": self.name, "via": getattr(self.site, "name", None)}

    def get_ipo_list(self) -> list[IPORecord]:
        if not self.supports("list"):
            return []
        records: dict[str, IPORecord] = {}
        for path, params in FEEDS:
            try:
                payload = self.site._api_get(path, params=params)
            except Exception as exc:  # noqa: BLE001
                logger.info("nse_ipo: %s failed: %s", path, exc)
                continue
            rows = _rows(payload)
            if not rows:
                logger.info("nse_ipo: no rows from %s", path)
                continue
            for row in rows:
                rec = record_from_row(row)
                if rec:
                    records.setdefault(rec.name.lower(), rec)
        return list(records.values())
