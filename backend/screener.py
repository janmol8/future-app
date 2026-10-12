"""Screener — scans the tracked universe from the shared DB (no 200-row cap).

Universe = all actively tracked listings (discovery keeps it current), so new
stocks are screened automatically. Per-symbol candles come from the history
service (DB cache first). Same filters and result shape as before.
"""
import concurrent.futures
import indicators
import logging

logger = logging.getLogger(__name__)


class Screener:
    """Universe is injected (list of canonical symbols) so the screener never
    hard-codes or caps the stock list. app.py passes the tracked universe."""

    def __init__(self, master_data=None, universe=None, history_lookup=None):
        # master_data: legacy list of {"symbol": ...} dicts (kept for compat)
        # universe: list of canonical symbols (preferred)
        if universe is None:
            universe = [s["symbol"] for s in (master_data or [])]
        self.universe = universe
        self._history_lookup = history_lookup

    def scan(self, filters):
        """
        filters: dict containing scan criteria
        e.g., {"min_price": 100, "max_price": 1000, "rsi_below": 30, "ema_cross": "bullish"}
        """
        symbols_to_scan = self.universe
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            future_to_symbol = {executor.submit(self.evaluate_symbol, sym, filters): sym for sym in symbols_to_scan}
            for future in concurrent.futures.as_completed(future_to_symbol):
                res = future.result()
                if res:
                    results.append(res)

        return results

    def _get_candles(self, symbol):
        """Daily candles via injected lookup (history service) or legacy path."""
        if self._history_lookup is not None:
            try:
                return self._history_lookup(symbol)
            except Exception as exc:  # noqa: BLE001
                logger.debug("history lookup failed for %s: %s", symbol, exc)
                return None
        import stock_provider
        return stock_provider.fetch_ohlc(symbol, interval="1d", range_days=100)

    def evaluate_symbol(self, symbol, filters):
        try:
            # Fetch minimal data for screening
            candles = self._get_candles(symbol)
            if not candles or len(candles) < 30:
                return None

            last_candle = candles[-1]
            price = last_candle["close"]
            prev_price = candles[-2]["close"]
            if not price or not prev_price:
                return None
            change_pct = ((price - prev_price) / prev_price) * 100

            # Price filters
            if "min_price" in filters and price < float(filters["min_price"]): return None
            if "max_price" in filters and price > float(filters["max_price"]): return None

            match = True
            metrics = {
                "symbol": symbol,
                "price": round(price, 2),
                "change": round(change_pct, 2),
                "volume": last_candle["volume"]
            }

            # RSI Filter
            if "rsi_below" in filters or "rsi_above" in filters:
                rsi_data = indicators.compute_indicator("rsi", candles, period=14)
                if rsi_data:
                    rsi_val = rsi_data[-1]["value"]
                    metrics["rsi"] = round(rsi_val, 2)
                    if "rsi_below" in filters and rsi_val > float(filters["rsi_below"]): match = False
                    if "rsi_above" in filters and rsi_val < float(filters["rsi_above"]): match = False
                else:
                    match = False

            # EMA Cross Filter
            if "ema_cross" in filters:
                ema20 = indicators.compute_indicator("ema", candles, period=20)
                ema50 = indicators.compute_indicator("ema", candles, period=50)
                if ema20 and ema50:
                    e20_curr = ema20[-1]["value"]
                    e50_curr = ema50[-1]["value"]
                    e20_prev = ema20[-2]["value"]
                    e50_prev = ema50[-2]["value"]

                    if filters["ema_cross"] == "bullish":
                        if not (e20_prev <= e50_prev and e20_curr > e50_curr): match = False
                    elif filters["ema_cross"] == "bearish":
                        if not (e20_prev >= e50_prev and e20_curr < e50_curr): match = False
                else:
                    match = False

            if match:
                return metrics
        except Exception as exc:
            logger.debug("screening %s failed: %s", symbol, exc)
        return None


def run_screener(filters):
    """Legacy entry: builds a Screener from the tracked DB universe when the
    market runtime is available; falls back to the old master list otherwise."""
    try:
        from market.runtime import get_runtime  # type: ignore
        from database import Database
        runtime = get_runtime(Database())
        universe = runtime.store.universe_symbols(limit=500)
        if universe:
            screener = Screener(universe=universe)
            return screener.scan(filters)
    except Exception as exc:  # noqa: BLE001
        logger.warning("DB universe screener unavailable, legacy path: %s", exc)
    scr = Screener()
    return scr.scan(filters)
