# Upgrade Final Report — Dynamic Indian Market Subsystem

Delivers the approved spec: dynamic NSE-side data, IPO lifecycle automation,
provider architecture, no fake data anywhere, education positioning, backward
compatibility, and security controls. Phases 0–5 complete; **180 tests pass**
(68 new + 112 pre-existing), `compileall` clean, live smoke green.

## What changed (by phase)

**Phase 0 — plan & hygiene** (`docs/UPGRADE_PLAN.md`, `docs/prelaunch_checklist.md`)
Verified all prior-review claims, removed 20 stray zero-byte files, wrote the
upgrade plan and the licensing prelaunch item.

**Phase 1 — foundation** (`backend/market/` new package)
`config.py` (env-driven knobs), `timeutil.py` (IST logic / UTC storage),
`cache.py` (TTL + single-flight), `http.py` (shared HTTP layer: token bucket,
daily budget per IST day, circuit breaker, terminal 401/403, retries only for
network/5xx/429 with backoff+jitter+Retry-After, conditional GET),
`providers/base.py` (capability ABCs + dataclasses), `router.py` (ordered
failover, last-good-stale, never invents), `scheduler.py` (APScheduler +
DB leases + `job_runs`), `store.py`, `runtime.py`, and `database.py`
rewritten with migrations v1–v5 (24 tables, WAL).

**Phase 2 — dynamic market data** (`providers/nse_site.py`,
`providers/yfinance_provider.py`, `calendar_service.py`, `discovery.py`,
`history_service.py`, `ipo_service.py`, `providers/manual_ipo.py`,
`blueprint.py`, app.py shims, screener.py rewrite)
- Discovery: ISIN → symbol → normalised-name+fuzzy dedupe (mid-confidence
  goes to `review_queue` **and still creates** its own row — ambiguity never
  swallows a listing), symbol-rename-in-place with old symbol kept as alias,
  series filter, new-listing events, daily master diff via content hash.
- History: gap-math cache with an attempted-window ledger (a fetched-and-empty
  range is never re-requested; recent windows refresh at most every 30 min).
- Calendar: dynamic holiday/special-session status replaces both hard-coded
  holiday tables.
- NSE adapter: honest UA, lazy warm-up, all calls through the shared layer.
- Legacy `nse_client.py` rewritten as a compliant facade over the shared
  layer: **keep-alive thread deleted, browser headers deleted,
  401/403-reset-retry deleted**; same method surface so app.py,
  option_chain_service, premarket service and stock_provider keep working.

**Phase 3 — alerts & notifications** (`market/alerts_service.py`, runtime
jobs, blueprint + legacy endpoints)
Event fan-out with `UNIQUE(user_key, dedupe_key)` + per-subject throttle +
opt-in prefs (default OFF; price alerts bypass prefs). Durable price alerts
(`user_price_alerts`) evaluated every 30 s **against stored quotes only** —
no quote ⇒ no evaluation, never a guessed trigger. Legacy `/api/alerts`
routes now write to the durable store; the restart-lossy in-memory list is
gone. `import_legacy_alerts()` exists for one-off imports (idempotent via
`legacy_id`).

**Phase 4 — frontend** (`frontend/js/market_ui.js`, `frontend/css/market_ui.css`,
index.html, app.js hooks)
Market Overview tab (indices + status + new listings + source note), IPO
Tracker tab (status chips, dates, GMP labelled unofficial), notification bell
+ inbox + per-event prefs, N/A-first formatting, stale badges, and
polling only while a panel is actually open. Education disclaimer on every
panel: *delayed, unofficial, for education only*.

**Phase 5 — docs & guards** (`docs/DATA_SOURCES.md`, `docs/PROVIDERS.md`,
`scripts/smoke_live.py`, `backend/tests/test_no_fake_data.py`)
Complete URL inventory, refresh-cadence table, provider architecture +
licensing guidance (Upstox end-state, Kite disqualified, Dhan fallback),
opt-in live smoke script, and static+behavioural no-fake-data guard tests.

## Refresh cadence (also in docs/DATA_SOURCES.md)

| Job | Interval |
|---|---|
| market_status / index_refresh | 10 s in-session |
| watchlist_refresh | 15 s, only symbols with live interest |
| alert_evaluate | 30 s |
| listing_day_loop | 5 min |
| universe_refresh | 60 s |
| event_drain / gmp_refresh | 15 min |
| ipo_list_refresh | 30 min |
| master_refresh (discovery) | daily |
| history recent-window refetch | ≤ once / 30 min per (symbol, interval) |

## Migration & rollback

- **Database:** first start runs migrations v1–v5 idempotently; no manual
  steps. `user_price_alerts` is created lazily by the alerts service. Old
  DBs keep working; `legacy_id` is added via ALTER + unique index.
- **Config:** no env changes required to run as before. New optional vars are
  documented in `backend/.env.example`; `ENABLE_NSE_SITE_ADAPTER=0` and/or
  `MARKET_DATA_PROVIDER=yfinance` degrade gracefully.
- **Rollback:** restore the Phase 0 snapshot
  (`D:\future_app_backup_20260928\snapshot.tar.gz`) and restart. The new
  tables are additive; the old app simply ignores them.

## Verification

- `python -m pytest backend/tests` → **180 passed** (foundation 23, http 12,
  phase2 23, phase3 17, no-fake-data 5, pre-existing 100+).
- `python -m compileall backend` clean; `node --check` on changed JS clean.
- Live smoke (providers disabled on purpose) with `scripts/smoke_live.py`:
  all endpoints 200, market state honestly `UNKNOWN`, unknown symbol returns
  **empty candles**, zero fabricated numbers.

## Files changed / added

**New — market subsystem** (`backend/market/`): `__init__.py`, `config.py`,
`timeutil.py`, `cache.py`, `http.py`, `router.py`, `scheduler.py`,
`store.py`, `runtime.py`, `calendar_service.py`, `discovery.py`,
`history_service.py`, `ipo_service.py`, `alerts_service.py`,
`blueprint.py`; `backend/market/providers/`: `base.py`, `nse_site.py`,
`yfinance_provider.py`, `manual_ipo.py`.

**New — tests**: `backend/tests/test_market_foundation.py`,
`test_market_http.py`, `test_market_phase2.py`, `test_market_phase3.py`,
`test_no_fake_data.py` (68 tests total across the five).

**New — docs & tooling**: `docs/UPGRADE_PLAN.md`, `docs/DATA_SOURCES.md`,
`docs/PROVIDERS.md`, `docs/UPGRADE_REPORT.md`, `scripts/smoke_live.py`,
`backend/.env.example` (market vars), `config/market_calendar.json`
(created on first calendar refresh).

**Modified**: `backend/database.py` (rewritten, migrations v1–v5),
`backend/app.py` (poller/holiday removal, shims, runtime wiring, durable
alert endpoints), `backend/nse_client.py` (rewritten as compliant facade),
`backend/stock_provider.py` (static holiday table removed),
`backend/option_chain_service.py` (honest BSE headers, shared-layer
historical OI), `backend/screener.py` (rewritten, DB universe),
`backend/requirements.txt` (APScheduler), `backend/nse_premarket_service.py`
(unchanged API; now served by the facade), `frontend/index.html` (tabs,
panels, bell, includes), `frontend/js/app.js` (tab + close hooks, init),
`docs/prelaunch_checklist.md` (licensing item).

**New — frontend**: `frontend/js/market_ui.js`, `frontend/css/market_ui.css`.

**Deleted**: 20 stray zero-byte files in `backend/` (Phase 0), the
in-memory `USER_ALERTS` list, `stock_provider.NSE_HOLIDAYS`,
`app._NSE_HOLIDAY_NAMES`, the nse_client keep-alive thread and browser
fingerprint headers.

## Known limitations

1. BSE option-chain calls in `option_chain_service` are still legacy
   on-demand paths (documented, honest headers); migrating them into the
   shared layer is future work.
2. Index constituents for NIFTY 500 are staged (`get_index_constituents`
   returns `[]`) until the master job wires the official archive files.
3. yfinance fallback depends on an unofficial library; treat as best-effort.
4. GMP is manual-entry only by design; no scraping source is approved.
5. Single-user assumption: notifications use one local user key per browser.
