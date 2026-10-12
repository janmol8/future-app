import requests
import time
import threading
from decimal import Decimal
from typing import Dict, Optional, List
from datetime import datetime


class PriceEngine:
    """
    Provides real-time pricing triad:
    - Index Price: fetched from Binance spot API or fallback
    - Mark Price: Index Price + smoothed funding premium
    - Last Traded Price: last matched trade on local engine
    - Funding Rate: calculated per the premium index formula
    """

    def __init__(self, cache_ttl: int = 1, funding_cache_ttl: int = 60):
        self.cache: Dict[str, Dict] = {}
        self.cache_ttl = cache_ttl
        self.funding_cache_ttl = funding_cache_ttl
        self.lock = threading.Lock()
        self._fetch_lock = threading.Lock()
        self.base_url = "https://fapi.binance.com"
        self.spot_url = "https://api.binance.com"
        self.orderbook_cache: Dict[str, Dict] = {}
        self.orderbook_ttl = 0.5

    def _is_indian_symbol(self, symbol: str) -> bool:
        s = symbol.upper()
        return s.endswith(".NS") or s.endswith(".BO") or s.startswith("^")

    def _fetch_from_api(self, symbol: str) -> Optional[Dict]:
        """Fetch comprehensive market data from Binance Futures API."""
        if self._is_indian_symbol(symbol):
            return {}

        try:
            data = {}
            try:
                resp = requests.get(f"{self.base_url}/fapi/v1/premiumIndex", params={"symbol": symbol}, timeout=5)
                if resp.status_code == 200:
                    data.update(resp.json())
            except Exception:
                pass
            try:
                resp = requests.get(f"{self.base_url}/fapi/v1/openInterest", params={"symbol": symbol}, timeout=2)
                if resp.status_code == 200:
                    data.update(resp.json())
            except Exception:
                pass
            try:
                resp = requests.get(f"{self.base_url}/futures/data/globalLongShortAccountRatio", params={"symbol": symbol, "period": "5m", "limit": 1}, timeout=2)
                if resp.status_code == 200:
                    ls_data = resp.json()
                    if ls_data:
                        data['longShortRatio'] = ls_data[0].get('longShortRatio')
                        data['longAccount'] = ls_data[0].get('longAccount')
                        data['shortAccount'] = ls_data[0].get('shortAccount')
            except Exception:
                pass
            try:
                resp = requests.get(f"{self.base_url}/futures/data/openInterestHist", params={"symbol": symbol, "period": "5m", "limit": 2}, timeout=2)
                if resp.status_code == 200:
                    hist = resp.json()
                    if len(hist) >= 2:
                        current_oi = Decimal(str(hist[-1]['sumOpenInterest']))
                        prev_oi = Decimal(str(hist[-2]['sumOpenInterest']))
                        data['oiDelta'] = current_oi - prev_oi
                        data['oiDeltaPercent'] = float((data['oiDelta'] / prev_oi * 100)) if prev_oi != 0 else 0
            except Exception:
                pass
            return data
        except Exception as e:
            print(f"Error fetching market data for {symbol}: {e}")
            return None

    def get_symbol_data(self, symbol: str) -> Optional[Dict]:
        """Get symbol data from cache or API (non-blocking)."""
        if self._is_indian_symbol(symbol):
            return {}

        with self.lock:
            now = time.time()
            if symbol in self.cache:
                cached = self.cache[symbol]
                if 'last_updated' in cached and now - cached['last_updated'] < self.cache_ttl:
                    return cached.get('data')
            # Check if another thread is already fetching
            if '_fetching' in self.cache.get(symbol, {}):
                return self.cache.get(symbol, {}).get('data')
            self.cache.setdefault(symbol, {})['_fetching'] = True

        # Release lock before API call to avoid blocking other threads
        data = self._fetch_from_api(symbol)

        with self.lock:
            now = time.time()
            if data:
                self.cache[symbol] = {'data': data, 'last_updated': now}
                return data
            if symbol in self.cache:
                self.cache[symbol].pop('_fetching', None)
                return self.cache[symbol].get('data')
        return None

    def get_index_price(self, symbol: str) -> Decimal:
        if not self._is_indian_symbol(symbol):
            data = self.get_symbol_data(symbol)
            if data and data.get('indexPrice'):
                return Decimal(str(data['indexPrice'])).quantize(Decimal("0.01"))
            try:
                resp = requests.get(f"{self.spot_url}/api/v3/ticker/price", params={"symbol": symbol}, timeout=2)
                if resp.status_code == 200:
                    return Decimal(resp.json().get('price', '0')).quantize(Decimal("0.01"))
            except Exception:
                pass
            
        # Fallback for Indian stocks/indices or when Binance fails
        try:
            import stock_provider
            quote = stock_provider.get_live_quote(symbol)
            if quote and quote.get('price'):
                return Decimal(str(quote['price'])).quantize(Decimal("0.01"))
        except Exception as e:
            if not self._is_indian_symbol(symbol):
                print(f"Fallback quote failed for {symbol}: {e}")
            
        return Decimal("0")

    def get_mark_price(self, symbol: str) -> Decimal:
        if self._is_indian_symbol(symbol):
            return self.get_index_price(symbol)

        data = self.get_symbol_data(symbol)
        if data:
            try:
                index_price = Decimal(str(data.get('indexPrice', '0')))
                funding_rate = Decimal(str(data.get('lastFundingRate', '0')))
                next_funding_time = int(data.get('nextFundingTime', 0))
                if index_price > 0 and next_funding_time > 0:
                    now_ms = int(time.time() * 1000)
                    time_until_funding = max(0, next_funding_time - now_ms)
                    funding_interval_ms = 8 * 60 * 60 * 1000
                    funding_basis = funding_rate * (Decimal(time_until_funding) / Decimal(funding_interval_ms))
                    mark_price = index_price * (Decimal("1") + funding_basis)
                    return mark_price.quantize(Decimal("0.01"))
            except Exception:
                pass
            try:
                mark = Decimal(str(data.get('markPrice', '0')))
                if mark > 0: return mark.quantize(Decimal("0.01"))
            except Exception:
                pass
        
        # Fallback to index price if mark price data unavailable
        return self.get_index_price(symbol)

    def get_funding_rate(self, symbol: str) -> Decimal:
        data = self.get_symbol_data(symbol)
        if data:
            try:
                return Decimal(str(data.get('lastFundingRate', '0')))
            except Exception:
                pass
        return Decimal("0")

    def get_next_funding_time(self, symbol: str) -> int:
        data = self.get_symbol_data(symbol)
        if data:
            return int(data.get('nextFundingTime', 0))
        return 0

    def get_open_interest(self, symbol: str) -> Decimal:
        data = self.get_symbol_data(symbol)
        if data:
            try:
                return Decimal(str(data.get('openInterest', '0')))
            except Exception:
                pass
        return Decimal("0")

    def get_oi_delta(self, symbol: str) -> Dict:
        data = self.get_symbol_data(symbol)
        if data:
            return {"delta": str(data.get('oiDelta', '0')), "percent": str(data.get('oiDeltaPercent', '0'))}
        return {"delta": "0", "percent": "0"}

    def get_long_short_ratio(self, symbol: str) -> Dict:
        data = self.get_symbol_data(symbol)
        if data:
            return {
                "ratio": str(data.get('longShortRatio', '0')),
                "long_pct": str(data.get('longAccount', '0')),
                "short_pct": str(data.get('shortAccount', '0'))
            }
        return {"ratio": "0", "long_pct": "0", "short_pct": "0"}

    def get_orderbook(self, symbol: str, limit: int = 20) -> Dict:
        if self._is_indian_symbol(symbol):
            return {"bids": [], "asks": []}

        now = time.time()
        if symbol in self.orderbook_cache:
            if now - self.orderbook_cache[symbol]['last_updated'] < self.orderbook_ttl:
                return self.orderbook_cache[symbol]['data']
        try:
            resp = requests.get(f"{self.spot_url}/api/v3/depth", params={"symbol": symbol, "limit": limit}, timeout=3)
            if resp.status_code == 200:
                data = resp.json()
                book = {
                    "bids": [[b[0], b[1]] for b in data.get('bids', [])],
                    "asks": [[a[0], a[1]] for a in data.get('asks', [])],
                }
                self.orderbook_cache[symbol] = {'data': book, 'last_updated': now}
                return book
        except Exception:
            pass
        return self.orderbook_cache.get(symbol, {}).get('data', {"bids": [], "asks": []})

    def calculate_funding_payment(self, position_size: Decimal, mark_price: Decimal, funding_rate: Decimal) -> Decimal:
        return position_size * mark_price * funding_rate

    def get_volatility_24h(self, symbol: str) -> float:
        if self._is_indian_symbol(symbol):
            return 0.02
            
        try:
            resp = requests.get(f"{self.spot_url}/api/v3/ticker/24hr", params={"symbol": symbol}, timeout=3)
            if resp.status_code == 200:
                data = resp.json()
                high = float(data.get('highPrice', 0))
                low = float(data.get('lowPrice', 0))
                if low > 0:
                    return (high - low) / low
        except Exception:
            pass
        return 0.03
