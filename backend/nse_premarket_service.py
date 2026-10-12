import time
from datetime import datetime, timezone, timedelta


class NSEPreMarketService:
    CATEGORY_KEYS = {
        "NIFTY 50": "NIFTY",
        "NIFTY50": "NIFTY",
        "NIFTY": "NIFTY",
        "NIFTY BANK": "BANKNIFTY",
        "BANKNIFTY": "BANKNIFTY",
        "NIFTYBANK": "BANKNIFTY",
        "F&O": "FO",
        "FO": "FO",
        "FNO": "FO",
        "ALL": "ALL",
    }

    def __init__(self, nse_client, ttl_seconds=15):
        self.nse_client = nse_client
        self.ttl_seconds = ttl_seconds
        self._cache = {}

    def ist_now(self):
        return datetime.now(timezone(timedelta(hours=5, minutes=30)))

    def session_status(self, now=None):
        now = now or self.ist_now()
        
        # Weekend check
        if now.weekday() >= 5:
            return "MARKET_CLOSED"
            
        mins = now.hour * 60 + now.minute
        if mins < 540:
            return "PREMARKET_NOT_STARTED"
        if 540 <= mins < 555:
            return "PREOPEN_ACTIVE"
        return "MARKET_OPEN"

    def normalize_symbol(self, symbol):
        symbol = (symbol or "").upper().strip()
        return symbol.replace(".NS", "").replace(".BO", "").replace("NSE:", "")

    def normalize_category(self, category):
        key = (category or "ALL").upper().strip()
        return self.CATEGORY_KEYS.get(key, "ALL")

    def get_category(self, category="ALL", force=False):
        key = self.normalize_category(category)
        cache_key = ("category", key)
        cached = self._cache.get(cache_key)
        now = time.time()
        if cached and not force and now - cached["ts"] < self.ttl_seconds:
            return cached["value"]

        raw = self._fetch_category(key)
        status = self.session_status()
        if not raw:
            value = {
                "status": "error",
                "category": key,
                "session_status": status,
                "message": self._fallback_message(status),
                "data": [],
                "timestamp": int(now),
            }
        else:
            rows = [self._parse_row(item) for item in raw.get("data", [])]
            rows = [row for row in rows if row and row.get("symbol")]
            value = {
                "status": "ok",
                "category": key,
                "session_status": status,
                "message": self._fallback_message(status) if not rows else "",
                "data": rows,
                "timestamp": int(now),
            }
        self._cache[cache_key] = {"ts": now, "value": value}
        return value

    def get_symbol(self, symbol, category="ALL"):
        target = self.normalize_symbol(symbol)
        categories = [self.normalize_category(category)]
        for extra in ("ALL", "NIFTY", "BANKNIFTY", "FO"):
            if extra not in categories:
                categories.append(extra)

        for cat in categories:
            payload = self.get_category(cat)
            for row in payload.get("data", []):
                if self.normalize_symbol(row.get("symbol")) == target:
                    return {
                        "status": "ok",
                        "symbol": target,
                        "category": cat,
                        "session_status": payload.get("session_status"),
                        "message": payload.get("message", ""),
                        "data": row,
                        "timestamp": payload.get("timestamp"),
                    }

        status = self.session_status()
        return {
            "status": "error",
            "symbol": target,
            "category": self.normalize_category(category),
            "session_status": status,
            "message": f"No NSE pre-open data found for {target}. {self._fallback_message(status)}".strip(),
            "data": None,
            "timestamp": int(time.time()),
        }

    def _fetch_category(self, key):
        try:
            data = self.nse_client.get("/api/market-data-pre-open", params={"key": key})
            if data and isinstance(data.get("data"), list):
                return data
            print(f"[NSEPreMarket] Empty pre-open response for category={key}")
        except Exception as e:
            print(f"[NSEPreMarket] Fetch failed for category={key}: {e}")
        return None

    def _parse_row(self, item):
        meta = item.get("metadata", {}) if isinstance(item, dict) else {}
        detail = item.get("detail", {}) if isinstance(item, dict) else {}
        pre = detail.get("preOpenMarket", {}) if isinstance(detail, dict) else {}

        symbol = meta.get("symbol") or item.get("symbol")
        prev_close = self._num(meta.get("previousClose") or pre.get("previousClose"))
        iep = self._num(pre.get("IEP") or pre.get("iep") or pre.get("indicativeEquilibriumPrice"))
        final_price = self._num(pre.get("finalPrice") or pre.get("finalOpenPrice") or pre.get("atoPrice"))
        price = final_price or iep
        change = self._num(pre.get("change") or meta.get("change"))
        pct = self._num(pre.get("perChange") or pre.get("pChange") or meta.get("pChange"))

        if change is None and price is not None and prev_close:
            change = price - prev_close
        if pct is None and change is not None and prev_close:
            pct = change / prev_close * 100

        return {
            "symbol": symbol,
            "previousClose": self._round(prev_close),
            "iep": self._round(iep),
            "finalOpeningPrice": self._round(final_price),
            "price": self._round(price),
            "change": self._round(change),
            "percentageChange": self._round(pct),
            "totalBuyQuantity": self._int(pre.get("totalBuyQuantity") or pre.get("totalBuyQty")),
            "totalSellQuantity": self._int(pre.get("totalSellQuantity") or pre.get("totalSellQty")),
            "indicativeQuantity": self._int(pre.get("totalTradedVolume") or pre.get("indicativeQty") or pre.get("quantity")),
            "timestamp": meta.get("lastUpdateTime") or item.get("lastUpdateTime") or int(time.time()),
        }

    def _fallback_message(self, status):
        if status == "PREMARKET_NOT_STARTED":
            return "Pre-market not started. NSE pre-open normally starts around 09:00 IST."
        if status == "MARKET_OPEN":
            return "Pre-open session is over. Use regular market price; opening price line is kept when available."
        return "NSE pre-open data unavailable. NSE may be rate-limiting or blocking the request."

    def _num(self, value):
        try:
            value = float(str(value).replace(",", ""))
            return value if value == value and value not in (float("inf"), float("-inf")) else None
        except Exception:
            return None

    def _int(self, value):
        try:
            return int(float(str(value).replace(",", "")))
        except Exception:
            return None

    def _round(self, value):
        return round(value, 2) if isinstance(value, (int, float)) else None
