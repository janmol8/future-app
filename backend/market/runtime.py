"""Runtime wiring for the market subsystem: singletons for db, providers,
router, services and the scheduler. app.py imports `get_runtime` and starts it.
"""
from __future__ import annotations

import logging
import threading

from .calendar_service import refresh_calendar
from .config import GMP_PROVIDER, IPO_DATA_PROVIDERS, MARKET_DATA_PROVIDERS, describe
from .discovery import DiscoveryService
from .history_service import HistoryService
from .ipo_service import IpoService
from .router import MarketRouter
from .scheduler import MarketScheduler, register_job
from .store import MarketStore
from .alerts_service import AlertNotificationService

logger = logging.getLogger(__name__)


class MarketRuntime:
    def __init__(self, db):
        self.db = db
        self.store = MarketStore(db)
        self.market_providers = []
        self.ipo_providers = []
        self.gmp_provider = None
        self._build_providers()
        self.router = MarketRouter(self.market_providers, store=self.store)
        self.discovery = DiscoveryService(db, router=self.router)
        self.history = HistoryService(db, self.router)
        self.ipo_service = IpoService(db, self.ipo_providers,
                                      discovery=self.discovery,
                                      gmp_provider=self.gmp_provider)
        self.scheduler = MarketScheduler(db)
        self.alerts = AlertNotificationService(db, store=self.store)
        self.holidays: dict = {}
        self.on_quotes = None          # callback(list[dict]) for SSE push bridge

    def _push_quotes(self, quotes: dict) -> None:
        if self.on_quotes is None or not quotes:
            return
        try:
            payload = [q.to_dict() if hasattr(q, "to_dict") else q
                       for q in quotes.values()]
            self.on_quotes(payload)
        except Exception:  # noqa: BLE001 — push failures must not kill jobs
            logger.exception("on_quotes callback failed")

    def _build_providers(self) -> None:
        from .providers.nse_site import NseSiteProvider
        from .providers.yfinance_provider import YfinanceProvider

        for name in MARKET_DATA_PROVIDERS:
            try:
                if name == "nse_site":
                    provider = NseSiteProvider()
                elif name == "yfinance":
                    provider = YfinanceProvider()
                else:
                    logger.warning("unknown market data provider '%s' — skipped", name)
                    continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("provider %s failed to initialise: %s", name, exc)
                continue
            self.market_providers.append(provider)

        for name in IPO_DATA_PROVIDERS:
            if name == "manual":
                from .providers.manual_ipo import ManualIpoProvider
                self.ipo_providers.append(ManualIpoProvider())
            elif name == "nse_ipo":
                from .providers.nse_ipo import NseIpoProvider
                from .providers.nse_site import NseSiteProvider
                site = next((p for p in self.market_providers if isinstance(p, NseSiteProvider)), None)
                self.ipo_providers.append(NseIpoProvider(site or NseSiteProvider()))
            else:
                logger.warning("unknown IPO data provider '%s' — skipped", name)
        if GMP_PROVIDER:
            # GMP ships with no third-party adapters; manual entry only.
            from .providers.manual_ipo import ManualGmpProvider
            self.gmp_provider = ManualGmpProvider()

    # ── scheduler job bodies ──
    def _job_market_status(self) -> None:
        self.holidays = refresh_calendar(self.router)
        status = self.router.get_market_status()
        if status is None:
            from .calendar_service import compute_status, load_special_sessions
            status = compute_status(self.holidays, special_sessions=load_special_sessions())
        self.store.upsert_status(status)

    def _job_index_refresh(self) -> None:
        symbols = self.store.index_symbols()
        quotes = self.router.get_quotes(symbols)
        for quote in quotes.values():
            self.store.upsert_quote(quote)
        self._push_quotes(quotes)

    def _job_watchlist_refresh(self) -> None:
        symbols = self.store.active_interest()
        if not symbols:
            return
        quotes = self.router.get_quotes(symbols)
        for quote in quotes.values():
            self.store.upsert_quote(quote)
        self._push_quotes(quotes)

    def _job_universe_refresh(self) -> None:
        symbols = self.store.universe_symbols()
        if not symbols:
            return
        quotes = self.router.get_quotes(symbols)
        for quote in quotes.values():
            self.store.upsert_quote(quote)
        self._push_quotes(quotes)

    def _job_master_refresh(self) -> None:
        from .discovery import content_hash
        for provider in self.market_providers:
            if not provider.supports("instrument_master"):
                continue
            for exchange in ("NSE",):
                instruments = provider.get_instrument_master(exchange)
                if instruments:
                    stats = self.discovery.sync_master(
                        exchange, instruments, content_hash(instruments)
                    )
                    logger.info("master sync %s: %s", exchange, stats)

    def _job_ipo_list_refresh(self) -> None:
        if self.ipo_service is not None:
            self.ipo_service.refresh_from_providers()

    def _job_gmp_refresh(self) -> None:
        if self.ipo_service is not None:
            self.ipo_service.refresh_gmp()

    def _job_listing_day_loop(self) -> None:
        if self.ipo_service is not None:
            self.ipo_service.run_listing_day_loop()

    def _job_alert_evaluate(self) -> None:
        fired = self.alerts.evaluate_price_alerts()
        if fired:
            logger.info("price alerts fired: %d", len(fired))

    def _job_event_drain(self) -> None:
        # Domain events from discovery/IPO services fan out to notifications.
        # Also runs the IPO closing-soon check alongside the drain.
        events = list(self.discovery.events) + list(self.ipo_service.events)
        if events:
            self.alerts.drain_service_events(events)
            self.discovery.events.clear()
            self.ipo_service.events.clear()

    def start(self) -> None:
        register_job("market_status", self._job_market_status)
        register_job("index_refresh", self._job_index_refresh)
        register_job("watchlist_refresh", self._job_watchlist_refresh)
        register_job("universe_refresh", self._job_universe_refresh)
        register_job("master_refresh", self._job_master_refresh)
        register_job("ipo_list_refresh", self._job_ipo_list_refresh)
        register_job("gmp_refresh", self._job_gmp_refresh)
        register_job("listing_day_loop", self._job_listing_day_loop)
        register_job("alert_evaluate", self._job_alert_evaluate)
        register_job("event_drain", self._job_event_drain)
        # Seed calendar immediately so status is correct before first tick.
        try:
            self.holidays = refresh_calendar(self.router)
        except Exception as exc:  # noqa: BLE001
            logger.warning("initial calendar refresh failed: %s", exc)
        if self.scheduler.start():
            logger.info("market runtime started: %s", describe())


_runtime: MarketRuntime | None = None
_runtime_lock = threading.Lock()


def get_runtime(db) -> MarketRuntime:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = MarketRuntime(db)
        return _runtime
