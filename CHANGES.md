# future_app update – paper trading, IPO detection, dashboard

Copy these files over your project (same paths), then restart `python app.py`
and hard-refresh the browser (Ctrl+Shift+R). Nothing in your data/ folder, .env
or paper_trade_data.json is touched.

## 1. Paper trading (fixed)
- Market orders were always rejected: the API dropped `current_price`
  (and stop-loss / target / display name). Fixed; the server also fetches the
  live price itself when none is sent.
- Target exits never fired when a stop-loss was set (`elif` bug). Fixed.
- Prices never refreshed server-side, so SL/target/limit orders never
  triggered. Positions/account reads now refresh prices (throttled, 3 s).
- Reversing a position (e.g. long 10 -> sell 15) checked cash before the closing
  proceeds were released and hard-coded the account. Fixed.
- `profit_factor` was `Infinity` (invalid JSON, broke the stats panel) -> null.
- Added: STOP orders, immediate fill of marketable limits, input validation,
  thread locking, atomic saves, realised-today / unrealised P&L in the account.
- Spot mode now shows Rs. with Indian number grouping; real error text on reject.

## 2. IPO detection + price band
- New `market/providers/nse_ipo.py`: reads NSE's current + upcoming IPO feeds
  through the existing rate-limited NSE client. Default providers are now
  `nse_ipo,manual`; refresh every 10 min and 8 s after startup.
- Fixed a bug where updating an already-tracked IPO crashed (so a price band
  announced later never appeared).
- "New IPO" and "IPO open" notifications are ON by default.
- IPO tab redesigned: price band, lot size, minimum investment, issue size, all
  dates, countdown, NEW badge, status filters, working refresh button.

## 3. Dashboard
- It always showed "Analyzing market data..." because nothing passed it data.
  It now shows market status + indices, your paper account (P&L, cash, win
  rate), open positions and live IPOs, auto-refreshing. Symbol trend / S&R
  appear when data is available; the crypto-futures widgets are collapsed.

## Not verified
The NSE IPO feed could not be called from my environment, so field parsing is
tested against sample data only. If the IPO list stays empty, check
http://localhost:5000/api/market/health and the log for "nse_ipo", or use
backend/data/ipos_manual.csv as a fallback.
