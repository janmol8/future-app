# UPGRADE PLAN — Dynamic Indian Market Data, IPO Lifecycle & Provider Architecture

**Phase 0 deliverable · 2026-09-28 · Read-only audit (only file written: this document)**

Repo snapshot (no git repo): `D:\future_app_backup_20260928\snapshot.tar.gz` (excludes node_modules, caches).

---

## 1. Corrections to the prior review

| # | Prior claim | Verdict |
|---|---|---|
| 1 | No git repo; snapshot instead | ✅ Confirmed — `git status` fails; tar snapshot created at `D:\future_app_backup_20260928\` |
| 2 | `app.py` ~95 KB monolith | ✅ Confirmed — 2,197 lines, 45+ routes, pollers, holidays, option chain, crypto, alerts all inline |
| 3 | `database.py` defines users, subscriptions, payment_events | ✅ Confirmed (71 lines, plain sqlite3, no WAL, no migrations) |
| 4 | `stock_provider._download_nse_master_list()` caches forever; failed download caches 10-symbol fallback permanently | ✅ Confirmed (`_nse_master_loaded` module-global, never invalidated; fallback = `POPULAR_NSE_STOCKS` written to `nse_master_cache.json` on failure) |
| 5 | Copies of `nse_master_cache.json` in two folders | ✅ Confirmed — repo root **and** `backend/` |
| 6 | No BSE master list | ✅ Confirmed |
| 7 | `INDIAN_INDICES` hard-coded with duplicates | ✅ Confirmed — `GC=F`×2, `SI=F`×2, `^CNXIT`×2; no NIFTY 500 |
| 8 | `screener.py` scans first 200 master rows | ✅ Confirmed (`self.master_data[:200]`) |
| 9 | Static 2025-26 holiday tables in two files; go stale in 2027 | ✅ Confirmed — `stock_provider.NSE_HOLIDAYS` + `app._NSE_HOLIDAY_NAMES`; `fetch_nse_holiday_list()` also builds sessions manually with browser headers |
| 10 | Per-symbol poller every 0.5 s behind `/api/stream` | ✅ Confirmed — `_POLL_INTERVAL = 0.5`, one daemon thread per symbol (`_ensure_poller`) |
| 11 | `float(x.get("open", 0))` fake-zero patterns; bare `except: pass` | ✅ Confirmed in `nse_client.get_quote`, `stock_provider.get_live_quote` (e.g. `high/low` default to `lastPrice` when missing), `screener.evaluate_symbol` |
| 12 | Alerts in-memory `USER_ALERTS`; watchlists in localStorage (`fa_watchlist`, `fa_watchlist_baskets`) | ✅ Confirmed |
| 13 | ~20 stray zero-byte files in backend/ | ✅ Confirmed — exactly 20, all 0 bytes, unreferenced (grep clean) |
| 14 | `nextjs_app/` and root `package.json` unused | ✅ Confirmed — 3 TSX files with no build config; no Python file references express/puppeteer |
| 15 | "Reuse the existing scheduler dependency if already used" | ⚠️ Partially wrong — `schedule` is listed in `requirements.txt` but **never imported anywhere**. The `schedule` lib is process-local and has no DB-lease support; **APScheduler + DB leases** is the better fit. Decision below. |
| 16 | Stack claim "Flask 3, SQLite via database.py, pytest, Docker python:3.11-slim" | ✅ Confirmed. Dockerfile CMD is `python backend/app.py` (works because CMD cwd = /app) |

One factual note: `run.bat` → `start.ps1 -SkipDeps`; deps were installed previously (`.startup/requirements.ok` exists).

---

## 2. Architecture map (current)

```
Browser (vanilla JS, no build)
  ├─ /api/quote, /api/ohlc, /api/symbols  ──► stock_provider.py ──► nse_client.py (browser-spoofed session)
  │        │                                    │
  │        │                                    └─► yfinance (fallback + ALL history)
  │        ├─ /api/stream (SSE)  ──► per-symbol 0.5 s poller threads (one per subscribed symbol)
  │        ├─ /api/screener ──► screener.py ──► first 200 master rows, yfinance per symbol
  │        ├─ /api/option-chain ──► option_chain_service.py (NSE/BSE JSON endpoints, BSE API host)
  │        ├─ crypto: crypto_provider.py ──► api.binance.com
  │        ├─ futures/paper trade: backend/engine + trading_engine (local, JSON state)
  │        └─ auth/subscription: auth_service.py + subscription_service.py + Razorpay
  │
  └─ localStorage: fa_watchlist (Yahoo symbols), fa_watchlist_baskets

State: backend/data/*.json (paper trade, futures) + future_app.sqlite3 (users/subs/payments only)
```

**Root causes of "new stocks never appear":**
1. `nse_master_cache.json` loaded once per process, never refreshed, relative path (cwd-dependent).
2. Download failure writes the 10-symbol `POPULAR_NSE_STOCKS` fallback **into the cache file**, making it permanent.
3. No BSE master, no ISIN/company identity, no dedupe, no scheduled refresh.
4. Screener capped at 200 rows; quotes only fetched for symbols a browser requests.

---

## 3. Outbound HTTP call inventory

Every outbound call in backend today (excluding `backend/tests/` and one-off scripts):

| # | URL | Caller | Purpose | Frequency | Permission status |
|---|-----|--------|---------|-----------|-------------------|
| 1 | `https://www.nseindia.com/` + `/get-quotes/equity?symbol=SBIN` | `nse_client` keep-alive thread | Cookie warm-up | every 90 s, forever | ⚠️ **Grey** — public pages, but browser-style headers, `X-Requested-With`, UA spoofing |
| 2 | `https://www.nseindia.com/api/quote-equity?symbol=X` | `nse_client.get_quote` | Equity quote | per-symbol, ≥1 per 2-15 s while a browser watches | ⚠️ Grey (as above) |
| 3 | `https://www.nseindia.com/api/equity-stockIndices?index=X` | `nse_client.get_quote` | Index quotes (NIFTY 50 etc.) | same | ⚠️ Grey |
| 4 | `https://www.nseindia.com/api/allIndices` | `nse_client.get_quote` fallback | All indices | on demand | ⚠️ Grey |
| 5 | `https://www.nseindia.com/api/market-data-pre-open?key=ALL` | `nse_client` + `nse_premarket_service` | Pre-open IEP | pre-open only | ⚠️ Grey |
| 6 | `https://www.nseindia.com/api/option-chain-indices?symbol=X`<br>`/api/option-chain-equities?symbol=X` | `option_chain_service` | Option chains | on demand | ⚠️ Grey (out of scope — leave untouched) |
| 7 | `https://api.bseindia.com/(RealTime)BseIndiaAPI/api/*` | `option_chain_service` | BSE option chain / quotes | on demand | ⚠️ Grey (out of scope) |
| 8 | `https://www.nseindia.com` + holidays page + `/api/holiday-master?type=trading` | `app.fetch_nse_holiday_list` | Holiday calendar | 1×/day | ⚠️ Grey — same browser-spoof pattern, independent of nse_client |
| 9 | `https://archives.nseindia.com/content/equities/EQUITY_L.csv`<br>`https://www.nseindia.com/content/equities/EQUITY_L.csv` | `stock_provider._download_nse_master_list` | Instrument master | once ever (cached forever) | ⚠️ Grey — plain `requests.get` with browser UA; **EQUITY_L.csv on archives is a plain static file** (lowest-risk URL in the app) |
| 10 | Yahoo endpoints via `yfinance` (query1/query2.finance.yahoo.com) | `stock_provider.fetch_ohlc`, `get_live_quote` fallback | History + fallback quotes | heavy — every chart load, every screener symbol | ⚠️ **Unofficial API** — yfinance ToU requires compliance with Yahoo ToU; no redistribution licence |
| 11 | `https://api.binance.com/api/v3/*` (klines, ticker) | `crypto_provider` | Crypto quotes/candles | per request | ✅ Binance public market data is documented as free-to-use with attribution expectation; out of scope anyway |
| 12 | Razorpay APIs | `subscription_service` | Payments | on payment | ✅ Contractual; out of scope |
| 13 | Anthropic API | `ai_engine` | AI commentary | on demand | ✅ Contractual; out of scope |

**NSE approach summary (Rule 2):** current `nse_client.py` warms cookies like a browser and retries on 401/403. It does **not** solve CAPTCHAs, rotate proxies, or fingerprint-spoof beyond a fixed Chrome UA, but the technique is purpose-built to obtain JSON endpoints that NSE does not document as public APIs, and NSE's site terms reserve market data. Per Rule 2 this technique must not extend to any new feature, must move behind the provider interface with rate limits and an env kill-switch, and needs a compliant replacement proposal (§6.2).

---

## 4. Provider evaluation (verified against official pages/ToU on 2026-09-28)

> Verified today via official ToU/docs pages and search. Items marked **[must confirm]** need a live check by you before we rely on them.

### 4.1 Market data — brokers (require a free/cheap account; data licensed from exchanges)

| Provider | Cost | Real-time | Rate limits (published) | Instrument master | History | Redistributing to your users? | Verdict |
|---|---|---|---|---|---|---|---|
| **Zerodha Kite Connect** | Free personal plan; ₹500/mo for data APIs | Yes (paid) | Published, per-endpoint | Yes (`instruments.dump`, daily) | Yes, years of daily/minute | ❌ **Prohibited.** Terms: live market data "cannot be displayed to the public at large"; **"not permitted ... for virtual/mock trading apps, trading related games"** — this app is exactly that. Termination on redistribution. | **Disqualified for server-side display use.** Only viable as opt-in user-connects-their-own-key (each user's own data, no server redistribution) — needs your decision. |
| **Upstox Uplink (API v2/v3)** | API free (broker account needed) | Yes (websocket) | ~25 req/s class per account; published per-endpoint bands | Yes (instruments JSON, daily, NSE+BSE) | Yes, v3 historical candles, 1-min to years | ⚠️ **[must confirm]** — ToU text on redistribution to third-party app users not fully verified today; community threads ask the same question without a clear answer | **Best candidate** for a compliant primary: free, covers NSE+BSE, quotes/candles/master/market-status. Pending ToU confirmation for multi-user display. |
| **Dhan (DhanHQ v2)** | Trading API free; **live data API ₹499+tax/mo**; historical API free tier | Yes (paid tier) | Data APIs 5 req/s; quotes 1 req/s (1000 instruments/req); no rate limit on historical minute/hourly | Yes (via instrument list endpoints) | Yes, up to 5 years | ⚠️ **[must confirm]** — DhanHQ terms are per-account; redistribution stance needs reading their full ToU | Strong secondary; excellent batched quotes (1000/req); weekly-lease access keys exist for platforms **[must confirm current terms]** |
| **Angel One SmartAPI** | Free (account) | Yes | Published per-endpoint | Yes (instrument master JSON) | Yes | ⚠️ **[must confirm]** | Viable fallback candidate |
| **Fyers API v3** | Free (account) | Yes | Published daily limits (e.g. ~100k/day class) | Yes | Yes | ⚠️ **[must confirm]** | Viable fallback candidate |

### 4.2 Market data — official/licensed channels

| Channel | Notes |
|---|---|
| **NSE website/archives** | `archives.nseindia.com` static CSVs (EQUITY_L.csv instrument master, holiday list) are plain files; NSE's **Data Sharing & Usage Policy** governs use — **[must confirm]** whether periodic programmatic download of public master files is permitted without a vendor agreement. NSE sells real-time and EOD data via **NSE Data & Analytics** (paid, licensed). |
| **BSE** | BSE publishes master files and IPO pages; API hosts seen in code (`api.bseindia.com`) are website endpoints, not documented public APIs. **[must confirm]** |
| **Licensed vendors** | NSE authorised data vendors list exists (paid, real-time, redistribution-licensed). Correct route if you ever serve real-time data to many users commercially. |

### 4.3 Non-broker sources

| Source | Cost | Real-time | Notes |
|---|---|---|---|
| **yfinance** | Free | ~15-min delayed for NSE/BSE | Unofficial; Yahoo ToU compliance is on the user; no redistribution licence. Keep as **history/fallback only**, rate-limited, env-flagged. |
| **NSE/BSE public pages for IPOs** | Free | — | IPO calendars, issue details, subscription numbers are published publicly. Permission status: **[must confirm]** — same policy question as instrument masters. |

### 4.4 IPO data providers

| Source | Coverage | Cost | Programmatic access | Verdict |
|---|---|---|---|---|
| **BSE IPO pages / NSE "new listings"** | Official: dates, price band, subscription (BSE shows live bid numbers), allotment/listing | Free | Website JSON/HTML; documented API? **[must confirm]** | Preferred official source if permitted; else manual/CSV |
| **Chittorgarh** | Mainboard + SME IPOs, dates, subscription, GMP, allotment | Free website | No official public API; third-party scrapers exist → **site ToU likely prohibits programmatic access; not verified** | Do **not** build a scraper. Manual/CSV adapter can ingest Chittorgarh-format CSV that you export manually. |
| **IPO preview-type sites (GMP aggregators)** | GMP | Free | None documented | GMP is unofficial by nature — see §F decision below |
| **Manual CSV** | Whatever you enter | — | — | **Floor adapter** (spec E): guaranteed availability |

### 4.5 GMP

Grey-market premium is an **informal OTC market** — no official source exists. Any website GMP is unverified hearsay; scraping GMP sites is both legally grey and unreliable. Recommendation: **GMP adapter ships OFF (`GMP_PROVIDER` unset)**, with (a) a manual-entry admin endpoint (single-admin app, stored with `as_of` + `source` = "manual"), and (b) a documented `GMPProvider` interface so a licensed/partner feed can be plugged in later. UI labels it "unofficial, as of HH:MM IST".

---

## 5. Proposed provider configuration (default chain)

```
MARKET_DATA_PROVIDER = nse_site,yfinance        # Phase 2 launch default (see Q1)
                       # recommended end-state: upstox,yfinance  (once Q1 answered)
IPO_DATA_PROVIDER    = manual                    # CSV/manual floor; add BSE/NSE adapter on Q2 answer
GMP_PROVIDER         = (unset → feature OFF)
```

- `nse_site` adapter = existing technique, wrapped in the new resilient HTTP layer, **rate-limited, env-flagged (`ENABLE_NSE_SITE_ADAPTER=1` default ON to avoid breaking current behaviour — your call), documented in PROVIDERS.md with every URL** — per Rule 2 it stays behind the provider interface and you decide whether it stays enabled.
- `yfinance` = history + fallback quotes, delayed-labelled.
- `manual` IPO adapter = CSV file + admin endpoint → guarantees the IPO module works with zero scraping.

**Compliant replacement proposal (Rule 2):** for an education app the correct long-term primary is a broker API (Upstox → Dhan → Angel One fallback chain), each with the user/admin's own credentials, real-time labelled per the provider, and instrument masters refreshed daily from the broker's official instrument files. No public-display licence needed for a personal/education deployment **[must confirm per-provider]**; commercial multi-user redistribution would need NSE Data & Analytics / authorised vendor agreements.

---

## 6. Plan by phase

### Phase 1 — Foundation
- `backend/market/` package: `config.py` (env parsing, defaults), `http.py` (shared HTTP: timeouts, retry w/ backoff+jitter, Retry-After, token bucket + daily budget, circuit breaker, single-flight, conditional GET), `providers/base.py` (ABCs with capability flags), `router.py` (ordered chain, health/circuit skip, never mixes sources in one record), `cache.py` (TTL cache), `scheduler.py` (APScheduler + DB leases + job_runs), `timeutil.py` (Asia/Kolkata logic, UTC storage).
- `database.py`: `schema_migrations` table, versioned idempotent migrations, `PRAGMA journal_mode=WAL`, `busy_timeout=5000`, threaded connection-per-request as today.
- APScheduler added to requirements; `schedule` removed or left untouched (decide at implementation).

### Phase 2 — Market data & discovery
- Migrations: `companies`, `listings`, `symbol_aliases`, `indices`, `index_constituents`, `quotes_latest`, `price_history`, `history_coverage`, `provider_status`, `job_runs`, `job_leases`, `interest_registry`, `review_queue`, `notification_prefs`, `notifications`.
- NSE EQUITY_L.csv + BSE equity master adapters → discovery job (content-hash diff → candidates → dedupe ISIN → exchange-symbol → normalised name + fuzzy + corroboration → create/review_queue).
- Market status service: provider market-status first, else schedule + `config/market_calendar.json` (holidays/special sessions), covered-year warning; delete both static holiday tables.
- Scheduler jobs: master refresh (daily), index quotes (~10 s in session), NIFTY-500 universe batched (~60 s), interest-registry (~15 s), status (~10 s). Only the scheduler calls providers.
- `/api/stream` rebuilt: reads shared store, interest registry w/ TTL, pushes deltas only. Per-symbol pollers deleted.
- Shims: `/api/quote`, `/api/ohlc`, `/api/symbols` route to new services; `screener.py` reads full universe from DB.

### Phase 3 — IPO module & alerts
- Migrations: `ipos`, `ipo_subscription_snapshots`, `ipo_gmp_snapshots`, `ipo_events`.
- State machine `Upcoming → Open → Closed → Allotment → Listed` (+ `Withdrawn/Postponed`), idempotent transitions logged to `ipo_events`, dates in IST.
- Listing-day loop: from 09:00 IST every 5 min resolve by ISIN → name in instrument masters; on hit: create/attach company+listing, capture listing price, emit `ipo_listed` + `new_stock_added`. Else "Listed – symbol pending" + review_queue + backoff. Never guess symbols.
- IPO providers: `manual` (CSV + admin UI/API) as floor; `bse_site`/`nse_site` adapters only if Q2 permits.
- GMP: manual-entry adapter behind `GMP_PROVIDER`, snapshots with `as_of`/source, staleness, "estimated, unofficial" labels.
- Alerts: notification prefs (opt-in, default OFF), event bus (`new_ipo_announced`, `ipo_open`, `ipo_closing_soon`, `subscription_updated` throttled, `allotment`, `ipo_listed`, `new_stock_added`), notification centre, dedupe via `unique(dedupe_key)`, optional SMTP email w/ backoff. Price alerts: migrate `USER_ALERTS` to DB (fixes restart-loss gap) while keeping API identical.

### Phase 4 — Frontend (vanilla, no build)
- Market overview dashboard, stock detail (reuse chart + ranges), IPO list/detail (status timeline, subscription bars, GMP card), global search, notification bell + prefs, data-health page.
- Global behaviours: last-updated + source + stale badge, "Data temporarily unavailable" empty states, "history starts <date>", N/A (never 0), delayed labels + attribution (docs/DATA_SOURCES.md), pause-on-hidden-tab, colour+sign for direction, responsive.
- Screener note in UI: gainers/losers/most-active computed from NIFTY 500 universe.

### Phase 5 — Tests & docs
- New pytest suites (offline, fixtures only): search, quote normalisation, failover, rate limit/backoff/circuit, market-status matrix, IPO state machine, IPO→listed incl. delayed-symbol case, dedupe/review queue, scheduler leases, caching counts, alerts dedupe/throttle, SSE push on tick, "no hard-coded prices" grep test.
- `compileall`, `node --check` on changed JS, `scripts/smoke_live.py` (optional, real providers once).
- Docs: `DATA_SOURCES.md`, `PROVIDERS.md`, final report.

### Done means
Tests green (old + new) · no static prices (grep-enforced) · new-listing + IPO→listed flows proven by tests · docs written · live behaviour stated exactly (what was and wasn't verified).

---

## 7. Schema diff summary

New tables (all created by versioned migrations): `companies`, `listings`, `symbol_aliases`, `indices`, `index_constituents`, `quotes_latest`, `price_history`, `history_coverage`, `ipos`, `ipo_subscription_snapshots`, `ipo_gmp_snapshots`, `ipo_events`, `provider_status`, `job_runs`, `job_leases`, `interest_registry`, `notification_prefs`, `notifications`, `review_queue`, plus `schema_migrations`.
Changed: `Database.__init__` gains migration runner + WAL/busy_timeout. Existing `users/subscriptions/payment_events` untouched.
Indexes: `listings(exchange,symbol)` UNIQUE, `listings(isin)`, `quotes_latest PK symbol`, `price_history(listing_id,interval,ts)`, `notifications(dedupe_key)` UNIQUE, `job_leases(job_name)`.

---

## 8. API plan

New Blueprint `market_bp` (all existing routes unchanged; shims inside):

| Route | Purpose |
|---|---|
| `GET /api/market/overview` | indices, status, gainers/losers/most-active (NIFTY 500 universe), new listings, IPO summaries, last_updated, sources |
| `GET /api/market/status` | session state, holiday name, next open |
| `GET /api/market/search` | symbol / name / ISIN / BSE code / legacy Yahoo |
| `GET /api/market/stocks/<symbol>` | company + listing + quote + meta |
| `GET /api/market/history` | ranges 1D…5Y from price_history, missing-range backfill |
| `GET /api/market/ipos`, `/api/market/ipos/<id>` | IPO list/detail (status timeline, subscription, GMP) |
| `GET/PUT /api/market/notifications/prefs`, `GET /api/market/notifications` | notification centre |
| `GET /api/market/health` | provider status, last success, errors, circuit states |

---

## 9. Risks

1. **Licensing:** any real-time display to multiple users is exchange-licensed data. Education/personal deployment with broker-API-on-your-own-account is the defensible default; commercial redistribution needs vendor agreements. Documentation and labels are mitigation, not a licence.
2. **NSE site technique:** fragile (session warm-up breaks), legally grey, and per Rule 2 must be capped and switchable. If disabled without a broker key, quotes/history fall back to yfinance-only (delayed) — functional but delayed.
3. **IPO→listed symbol resolution** depends on instrument-master freshness; the 5-min loop + review_queue + "symbol pending" state handles misses without guessing.
4. **SQLite concurrency:** WAL + busy_timeout + short transactions + DB leases make multi-process safe; heavy write load would warrant Postgres later (out of scope).
5. **Windows/Docker parity:** APScheduler and SQLite are fine on both; file-lock behaviour on Windows requires the DB not be opened by two processes without WAL (enabled).
6. **Scope discipline:** option-chain, crypto, futures, paper-trading stay untouched except where they must call the new services.

---

## 10. Questions for you (max 5)

1. **Broker account for the compliant primary?** Upstox Uplink is free, covers NSE+BSE quotes/candles/masters, and is my recommended end-state primary — but it needs your account/API key, and I could not fully verify its terms on redistribution-to-app-users today. Options: (a) start with `nse_site,yfinance` chain now and add Upstox when you have a key; (b) you confirm Upstox ToU for this use and we make it primary immediately; (c) you prefer Dhan (₹499/mo for live data).
2. **Keep the NSE website adapter enabled by default?** Rule 2 says you decide. Default ON keeps today's functionality (and quotes near-real-time); OFF makes yfinance-only (15-min delayed) until a broker key exists.
3. **IPO source:** BSE/NSE IPO pages have the data but the same permission question as masters. OK to default to the **manual/CSV adapter** (you paste a CSV / use admin form) and add exchange-page adapters only if Q2's answer covers them?
4. **GMP:** confirm it ships **OFF with manual-entry only** (no third-party scraping)? (Spec F allows exactly this; my recommendation.)
5. **Scheduler:** `schedule` is in requirements.txt but never used. Confirm **APScheduler** (has intervals, cron, jitter, misfire handling — better fit for leases) and that removing the unused `schedule` line is acceptable.

---

*Awaiting approval to begin Phase 1.*
