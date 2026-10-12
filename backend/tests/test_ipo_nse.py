import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from database import Database
from market.ipo_service import IpoService
from market.providers.nse_ipo import NseIpoProvider, parse_price_band, record_from_row


@pytest.fixture()
def db(tmp_path):
    return Database(data_dir=str(tmp_path))


class FakeSite:
    name = "nse_site"
    enabled = True

    def __init__(self, feeds):
        self.feeds = feeds

    def _api_get(self, path, params=None):
        return self.feeds.get(path)


def test_price_band_parsing_variants():
    assert parse_price_band({"issuePrice": "Rs.95 to Rs.100"}) == (95.0, 100.0)
    assert parse_price_band({"issuePrice": "₹ 1,050 - ₹ 1,100"}) == (1050.0, 1100.0)
    assert parse_price_band({"priceBandLow": "10", "priceBandHigh": 12}) == (10.0, 12.0)
    assert parse_price_band({"issuePrice": "250"}) == (250.0, 250.0)
    assert parse_price_band({}) == (None, None)


def test_record_from_row_fields():
    rec = record_from_row({
        "companyName": "Acme Ltd", "series": "EQ", "issueStartDate": "13-Oct-2026",
        "issueEndDate": "15-Oct-2026", "issuePrice": "Rs.95 to Rs.100", "lotSize": "150",
        "issueSize": "Rs. 500 Cr",
    })
    assert rec.name == "Acme Ltd" and rec.ipo_type == "Mainboard"
    assert rec.open_date == date(2026, 10, 13) and rec.close_date == date(2026, 10, 15)
    assert (rec.price_band_low, rec.price_band_high, rec.lot_size) == (95.0, 100.0, 150)
    assert rec.issue_size_cr == 500.0
    assert record_from_row({"series": "SM", "companyName": "Tiny"}).ipo_type == "SME"
    assert record_from_row({"foo": 1}) is None


def test_provider_merges_feeds_and_survives_bad_payloads():
    site = FakeSite({
        "/api/ipo-current-issue": [{"companyName": "A Ltd", "issuePrice": "10 to 12"}],
        "/api/all-upcoming-issues": {"data": [{"companyName": "B Ltd"}, {"companyName": "A Ltd"}]},
    })
    names = sorted(r.name for r in NseIpoProvider(site).get_ipo_list())
    assert names == ["A Ltd", "B Ltd"]
    assert NseIpoProvider(FakeSite({})).get_ipo_list() == []


def test_new_ipo_detected_then_price_band_added_later(db):
    """Regression: updating an existing IPO used to crash (excluded.exchanges)."""
    row_v1 = {"companyName": "Acme Ltd", "series": "EQ",
              "issueStartDate": "13-Oct-2026", "issueEndDate": "15-Oct-2026"}
    site = FakeSite({"/api/all-upcoming-issues": [row_v1]})
    svc = IpoService(db, [NseIpoProvider(site)])
    assert svc.refresh_from_providers()["inserted"] == 1
    assert any(e["type"] == "new_ipo_announced" for e in svc.events)

    site.feeds["/api/all-upcoming-issues"] = [dict(row_v1, issuePrice="Rs.95 to Rs.100", lotSize=150)]
    stats = svc.refresh_from_providers()
    assert stats["updated"] == 1 and stats["inserted"] == 0
    with db.connect() as conn:
        row = dict(conn.execute("SELECT * FROM ipos").fetchone())
    assert (row["price_band_low"], row["price_band_high"], row["lot_size"]) == (95.0, 100.0, 150)
