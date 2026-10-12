"""Manual IPO provider ('manual') — the guaranteed floor adapter.

Data source: a CSV file maintained by the admin (backend/data/ipos_manual.csv)
and/or rows inserted through the admin API. Format (header row required):

name,ipo_type,status,open_date,close_date,allotment_date,listing_date,
price_band_low,price_band_high,issue_price,lot_size,issue_size_cr,
fresh_issue_cr,ofs_cr,exchanges,isin

Dates ISO (YYYY-MM-DD); empty cells become N/A (None). This adapter never
touches the network, so the IPO module works with zero scraping (approved plan).
"""
from __future__ import annotations

import csv
import logging
import os
from datetime import date, datetime

from ..timeutil import iso_utc
from .base import GmpSnapshot, IPORecord, IpoSubscription

logger = logging.getLogger(__name__)

DEFAULT_CSV = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "ipos_manual.csv",
)

FIELDS = [
    "name", "ipo_type", "status", "open_date", "close_date", "allotment_date",
    "listing_date", "price_band_low", "price_band_high", "issue_price",
    "lot_size", "issue_size_cr", "fresh_issue_cr", "ofs_cr", "exchanges", "isin",
]


def _date(value):
    if not value or not str(value).strip():
        return None
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _float(value):
    try:
        text = str(value).strip()
        return float(text) if text else None
    except (TypeError, ValueError):
        return None


def _int(value):
    f = _float(value)
    return int(f) if f is not None else None


class ManualIpoProvider:
    name = "manual"
    capabilities = frozenset({"list", "subscription_manual"})

    def __init__(self, csv_path: str | None = None):
        self.csv_path = csv_path or os.environ.get("IPO_MANUAL_CSV", DEFAULT_CSV)

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def status(self) -> dict:
        return {"name": self.name, "csv": self.csv_path,
                "present": os.path.exists(self.csv_path)}

    def _read_rows(self) -> list[dict]:
        if not os.path.exists(self.csv_path):
            return []
        rows = []
        try:
            with open(self.csv_path, "r", encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    if (row.get("name") or "").strip():
                        rows.append(row)
        except OSError as exc:
            logger.warning("manual IPO CSV unreadable: %s", exc)
        return rows

    def get_ipo_list(self) -> list[IPORecord]:
        records = []
        for row in self._read_rows():
            exchanges = [e.strip().upper() for e in (row.get("exchanges") or "").split(";")
                         if e.strip()]
            records.append(IPORecord(
                name=(row.get("name") or "").strip(),
                ipo_type=(row.get("ipo_type") or "").strip() or None,
                status=(row.get("status") or "").strip() or None,
                open_date=_date(row.get("open_date")),
                close_date=_date(row.get("close_date")),
                allotment_date=_date(row.get("allotment_date")),
                listing_date=_date(row.get("listing_date")),
                price_band_low=_float(row.get("price_band_low")),
                price_band_high=_float(row.get("price_band_high")),
                issue_price=_float(row.get("issue_price")),
                lot_size=_int(row.get("lot_size")),
                issue_size_cr=_float(row.get("issue_size_cr")),
                fresh_issue_cr=_float(row.get("fresh_issue_cr")),
                ofs_cr=_float(row.get("ofs_cr")),
                exchanges=exchanges,
                isin=(row.get("isin") or "").strip() or None,
                provider=self.name,
            ))
        return records

    def get_subscription(self, ipo: IPORecord) -> IpoSubscription | None:
        return None  # subscription numbers arrive via admin API snapshots


class ManualGmpProvider:
    """GMP from admin manual entry (DB-backed via ipo_gmp_snapshots); the
    provider interface exists so a licensed feed can be plugged in later."""
    name = "manual_gmp"

    def status(self) -> dict:
        return {"name": self.name, "mode": "manual entry only"}
