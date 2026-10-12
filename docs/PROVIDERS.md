# Provider Architecture

How market data reaches this app, how to configure it, and where it must go
before a real (paid) launch.

## Model

Every external data source implements a small adapter behind three optional
capabilities (`backend/market/providers/base.py`):

- `MarketDataProvider` — quotes, index quotes, historical candles, market
  status, holiday calendar, instrument master
- `IPODataProvider` — the IPO list
- `GMPProvider` — grey-market premium snapshots (unofficial; manual only)

The router (`market/router.py`) walks an ordered provider chain per
capability. A provider that fails, is rate-limited, or has its breaker open
is skipped; the next one serves. If none answer, the last-known-good stored
value may be served **flagged stale** — the router never invents values.
Missing data stays `null` end-to-end and renders as **N/A**.

## Configuration (env; see backend/.env.example)

```
MARKET_DATA_PROVIDER=nse_site,yfinance     # ordered chain, first healthy wins
IPO_DATA_PROVIDER=manual                   # manual CSV floor
GMP_PROVIDER=                              # unset = GMP feature OFF
ENABLE_NSE_SITE_ADAPTER=1                  # single switch for all NSE-site calls
```

Rate/budget knobs: `NSE_SITE_RATE_PER_SEC` (0.5), `NSE_SITE_DAILY_BUDGET`
(4000), `YF_RATE_PER_SEC` (1.0), `YF_DAILY_BUDGET` (20000),
`HTTP_MAX_RETRIES` (3), breaker `BREAKER_FAILURE_THRESHOLD` (5) /
`BREAKER_COOLDOWN_SECONDS` (120).

## Shipped providers

| Provider | Capabilities | Notes |
|---|---|---|
| `nse_site` | quotes, index quotes, market status, holiday calendar, instrument master, pre-open | NSE public website APIs through the shared HTTP layer. ON by default per project decision; env-switchable. Delayed/unofficial. |
| `yfinance` | historical (all ranges), delayed fallback quotes | Unofficial Yahoo endpoints via the `yfinance` library. |
| `manual_ipo` | IPO list from `backend/data/ipos_manual.csv` | Absent file = empty list. Never invents rows. |
| `manual_ipo.ManualGmpProvider` | GMP snapshots (manual entry) | Unofficial by nature; labelled in UI. |

## Licensing reality (read before charging anyone)

- Nothing in the current chain is licensed for redistribution or real-time
  display. All of it is delayed and/or unofficial.
- **Kite Connect (Zerodha) is disqualified** for this product: its terms
  prohibit use in virtual / mock trading applications and public display of
  data. Do not integrate.
- **Upstox** (Upstox API / market data websocket) is the recommended
  end-state primary: free tier with real-time Indian market data, official
  terms that fit an education product. Requires a user account and accepting
  its terms of use; add an `upstox` adapter implementing
  `MarketDataProvider` and put it first in `MARKET_DATA_PROVIDER`.
- **Dhan** offers live market data at ~₹499/month — a paid fallback if
  Upstox does not work out.
- Track this in `docs/prelaunch_checklist.md` ("replace unofficial sources
  with a licensed feed") — it is a launch blocker, not a nice-to-have.

## Adding a provider

1. Subclass the relevant ABC in `backend/market/providers/`; honour the
   missing-data-is-`None` rule and set `provider`/`as_of`/`fetched_at` on
   every record.
2. Route **all** HTTP through `market/http.HttpClient` (rate limit, budget,
   breaker, single-flight). A provider that opens raw sockets/requests
   outside this layer will be rejected in review.
3. Register it in `MarketRuntime._build_providers()` and add its name to the
   `MARKET_DATA_PROVIDER` / `IPO_DATA_PROVIDER` chain as desired.
4. Add capability-gated behaviour only where `supports(capability)` is true.
5. Add tests: normalisation with missing fields, a failover case, and a
   budget/breaker interaction. See `backend/tests/test_market_http.py` for
   the harness patterns (injectable clock/sleep/jitter, offline).
