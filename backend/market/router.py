"""Provider router: walks the ordered provider chain per capability.

Failover rules (spec B / Rule 1):
  - pick the first provider that supports the capability and answers;
  - skip providers whose circuit breaker is open;
  - never mix fields from different providers inside one record;
  - if every provider fails, return the last good stored value flagged stale,
    or nothing at all — never invented values.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class MarketRouter:
    def __init__(self, market_providers: list, store=None):
        self.providers = [p for p in market_providers if p is not None]
        self.store = store

    def _first_answer(self, capability_call):
        """Call providers in order until one returns a non-empty result."""
        errors = []
        for provider in self.providers:
            try:
                result = capability_call(provider)
            except Exception as exc:  # noqa: BLE001 — one bad provider must not kill the chain
                logger.warning("provider %s raised: %s", provider.name, exc)
                if self.store:
                    self.store.record_provider_failure(provider.name, str(exc))
                errors.append((provider.name, str(exc)))
                continue
            if result:
                if self.store:
                    self.store.record_provider_success(provider.name)
                return result, provider.name
            errors.append((provider.name, "empty"))
        return None, errors

    def get_quotes(self, symbols: list[str]) -> dict[str, dict]:
        """Batched quotes with per-symbol failover and last-good fallback."""
        out: dict[str, dict] = {}
        missing = list(symbols)
        for provider in self.providers:
            if not missing:
                break
            try:
                batch = provider.get_quotes(missing)
            except Exception as exc:  # noqa: BLE001
                logger.warning("provider %s raised: %s", provider.name, exc)
                if self.store:
                    self.store.record_provider_failure(provider.name, str(exc))
                continue
            if self.store:
                self.store.record_provider_success(provider.name)
            for symbol, quote in (batch or {}).items():
                if quote is not None and quote.last is not None:
                    out[symbol] = quote.to_dict()
                    missing.remove(symbol)
        # Last good value for anything no provider could price.
        for symbol in missing:
            if self.store:
                stored = self.store.get_quote(symbol)
                if stored is not None and stored.get("last") is not None:
                    stored["is_stale"] = 1
                    out[symbol] = stored
        return out

    def get_market_status(self):
        result, _ = self._first_answer(lambda p: p.get_market_status())
        return result

    def get_instrument_master(self, exchange: str) -> list:
        result, _ = self._first_answer(lambda p: p.get_instrument_master(exchange))
        return result or []

    def get_historical_candles(self, symbol: str, interval: str, start, end) -> list:
        for provider in self.providers:
            if not provider.supports("historical"):
                continue
            try:
                candles = provider.get_historical_candles(symbol, interval, start, end)
            except Exception as exc:  # noqa: BLE001
                logger.warning("provider %s history raised: %s", provider.name, exc)
                continue
            if candles:
                return candles
        return []

    def get_holiday_calendar(self, year: int) -> dict[str, str]:
        result, _ = self._first_answer(lambda p: p.get_holiday_calendar(year))
        return result or {}

    def status(self) -> dict:
        return {
            "chain": [p.name for p in self.providers],
            "providers": [p.status() for p in self.providers],
        }
