# Data Sources & URL Inventory

Every external endpoint this app can call, why, and under what controls.
**Nothing on this list is a licensed real-time feed.** All market data here is
delayed and/or unofficial and the app is an **education simulator** — it must
not be used as a trading terminal, and this must be fixed (licensed feed)
before any paid launch. See `docs/prelaunch_checklist.md`.

## Rules that apply to every source

- All outbound traffic goes through the shared HTTP layer
  (`backend/market/http.py`): timeouts, retries **only** for network errors /
  5xx / 429 (with backoff + jitter + Retry-After), 401/403 are **terminal**
  (no reset-and-retry loops), per-provider token-bucket rate limits, daily
  call budgets per IST day, a circuit breaker (opens after 5 consecutive
  failures, half-open probe after 120 s), single-flight dedupe, and
  conditional GET where the server supports it.
- No proxies, headless browsers, CAPTCHA solving, or browser-fingerprint
  spoofing anywhere. UA is the honest `future-app/1.0 (market data client)`.
- Missing data is `null` end-to-end and renders as **N/A** in the UI — never
  0, never a synthesised placeholder. No fake/random prices exist anywhere.
- `ENABLE_NSE_SITE_ADAPTER=0` (env) disables all NSE-website calls in one
  switch without code changes. The app degrades to yfinance + manual CSV.
- If a source blocks plain `requests` traffic, the fallback is manual/CSV
  entry or "N/A" — never evasion.

## URL inventory

### NSE website adapter (`nse_site`) — default ON, env-switchable

| URL | Purpose | Controls |
|---|---|---|
| `https://www.nseindia.com` | Cookie warm-up before JSON APIs (lazy, on first use only — no keep-alive thread) | warm-up budget ≈ 10 % of main, own limiter |
| `https://www.nseindia.com/get-quotes/equity?symbol=SBIN` | Referer for API calls (plain navigation context) | — |
| `https://www.nseindia.com/api/equity-stockIndices?index=…` | Index + constituent quotes (NIFTY 50, BANK, FIN SERVICE, MIDCAP SELECT, 500) | 0.5 req/s, 4000/day, breaker |
| `https://www.nseindia.com/api/allIndices` | Fallback index quotes (incl. SENSEX/VIX as reported by NSE) | same |
| `https://www.nseindia.com/api/quote-equity?symbol=…` | Per-equity quotes (volume/value honestly absent → null) | same |
| `https://www.nseindia.com/api/market-data-pre-open?key=ALL` | Pre-open indicative prices | same |
| `https://www.nseindia.com/api/holiday-master?type=trading` | Trading holiday calendar (CM segment, `%d-%b-%Y`) | same |
| `https://www.nseindia.com/api/marketStatus` | Official market open/closed state | same |
| `https://archives.nseindia.com/content/equities/EQUITY_L.csv` | Instrument master (symbol/ISIN/series/name) for discovery | same client, daily job |

### yfinance (`yfinance`) — secondary/fallback

Yahoo Finance public endpoints via the `yfinance` library (Ticker.history,
Tickers.fast_info). Quotes are **delayed** and occasionally missing fields;
missing fields stay `null`. Supports historical candles for all ranges.
Env: `YF_RATE_PER_SEC` (default 1), `YF_DAILY_BUDGET` (default 20000).
Note: yfinance is an unofficial library against Yahoo endpoints — replace
with a licensed feed before charging users.

### BSE (option chain legacy path, on demand)

| URL | Purpose | Controls |
|---|---|---|
| `https://api.bseindia.com/BseIndiaAPI/api/…` | SENSEX option chain / expiries when the user opens that view | on-demand only, honest UA, 0.8–1 s TTL caches |
| `https://api.bseindia.com/RealTimeBseIndiaAPI/api/…` | Same, realtime path used by existing code | same |
| `https://nsearchives.nseindia.com/content/fo/NSE_FO_contract_YYYYMMDD.csv.gz` | F&O lot sizes (daily contract file, cached 24 h) | on-demand, honest UA |

These calls are the remaining pre-shared-layer paths (documented, honest
headers, no evasion). Migration into the shared client is tracked in
`docs/UPGRADE_PLAN.md`.

### IPO / GMP — manual floor

| Source | Purpose |
|---|---|
| `backend/data/ipos_manual.csv` | IPO list fed by hand (fields documented in `market/providers/manual_ipo.py`). Absent file = empty list, never invented rows. |
| GMP rows | Manual entry only. `GMP_PROVIDER` env is unset by default = feature OFF. GMP is **unofficial** informal-market data and is labelled as such everywhere it renders. |

## Refresh cadence (scheduler, Asia/Kolkata; all jobs lease-guarded)

| Job | Interval | Notes |
|---|---|---|
| `market_status` | 10 s in-session | provider status first, schedule-derived fallback |
| `index_refresh` | 10 s in-session | seeded indices via provider chain |
| `watchlist_refresh` | 15 s | only symbols with live interest (TTL 120 s) — nothing polls when nobody is watching |
| `universe_refresh` | 60 s | NIFTY 500 constituents (staged) |
| `alert_evaluate` | 30 s | price alerts vs stored quotes only |
| `event_drain` | 15 min | domain events → notifications (deduped/throttled) |
| `listing_day_loop` | 5 min | IPO symbol resolution on/after listing day |
| `gmp_refresh` | 15 min | no-op unless GMP provider configured |
| `ipo_list_refresh` | 30 min | manual CSV today; provider chain ready |
| `master_refresh` | daily | EQUITY_L.csv → discovery (companies/listings/aliases) |

History (`/api/market/history`, `/api/ohlc`) is cached in `price_history`:
gaps are fetched once and never re-requested unless inside the most recent
30-minute window; repeated identical upstream calls are deduped by
single-flight; the daily budget caps total provider load.

## What is intentionally NOT here

- No licensed feed yet (Upstox recommended as end-state primary; see
  `docs/PROVIDERS.md`). Kite Connect is disqualified: its terms prohibit use
  in virtual/mock trading applications and public display.
- No fake-data generators, no random-walk simulators, no seeded demo prices.
  An empty market renders as empty + N/A, by design.
