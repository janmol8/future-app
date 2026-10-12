"""Shared resilient HTTP layer for all provider calls.

One place for: timeouts, idempotent-GET retries with exponential backoff +
jitter, Retry-After handling, per-provider token-bucket rate limits and daily
budgets, a circuit breaker (open after N failures, half-open probe), dedupe of
identical in-flight calls, and conditional GET (ETag / If-Modified-Since).

Policy encoded here (per Phase 0 approval):
  - Retries only for idempotent GETs on network errors, 5xx and 429.
  - 401/403 are terminal for the call: recorded as breaker failures, never
    retried in a reset-and-retry loop.
  - Clock, sleep and jitter are injectable so tests can run offline and fast.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field

import requests

from .cache import TTLCache
from .timeutil import ist_now

logger = logging.getLogger(__name__)


class BudgetExhausted(Exception):
    pass


class RateLimited(Exception):
    pass


class TokenBucket:
    """Classic token bucket; blocks until a token is available."""

    def __init__(self, rate_per_sec: float, clock=time.monotonic, sleep=time.sleep):
        self.rate = max(rate_per_sec, 0.01)
        self.capacity = max(1.0, self.rate)
        self._clock = clock
        self._sleep = sleep
        self._tokens = self.capacity
        self._last = clock()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self._clock()
                self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self.rate
            self._sleep(min(wait, 0.25))


class DailyBudget:
    """Count of upstream calls per IST calendar day; refuses when exhausted."""

    def __init__(self, limit: int):
        self.limit = limit
        self._lock = threading.Lock()
        self._day = self._today()
        self._used = 0

    @staticmethod
    def _today() -> str:
        return ist_now().strftime("%Y-%m-%d")

    def _rollover(self) -> None:
        today = self._today()
        if today != self._day:
            self._day = today
            self._used = 0

    def try_consume(self) -> bool:
        with self._lock:
            self._rollover()
            if self._used >= self.limit:
                return False
            self._used += 1
            return True

    def usage(self) -> dict:
        with self._lock:
            self._rollover()
            return {"day": self._day, "used": self._used, "limit": self.limit}


class CircuitBreaker:
    """Opens after N consecutive failures; half-open lets one probe through."""

    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(self, failure_threshold: int = 5, cooldown: float = 120.0, clock=time.monotonic):
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown
        self._clock = clock
        self._lock = threading.Lock()
        self._state = self.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._probe_in_flight = False

    def allow(self) -> bool:
        with self._lock:
            if self._state == self.CLOSED:
                return True
            if self._state == self.OPEN:
                if self._clock() - self._opened_at >= self.cooldown:
                    self._state = self.HALF_OPEN
                    self._probe_in_flight = False
                else:
                    return False
            if self._state == self.HALF_OPEN:
                if self._probe_in_flight:
                    return False
                self._probe_in_flight = True
                return True
            return True

    def record_success(self) -> None:
        with self._lock:
            self._state = self.CLOSED
            self._failures = 0
            self._probe_in_flight = False

    def record_failure(self) -> None:
        with self._lock:
            if self._state == self.HALF_OPEN or self._probe_in_flight:
                self._state = self.OPEN
                self._opened_at = self._clock()
                self._failures = self.failure_threshold
                self._probe_in_flight = False
                return
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._state = self.OPEN
                self._opened_at = self._clock()

    def state(self) -> dict:
        with self._lock:
            return {
                "state": self._state,
                "consecutive_failures": self._failures,
                "seconds_until_probe": max(
                    0.0, self.cooldown - (self._clock() - self._opened_at)
                ) if self._state == self.OPEN else 0.0,
            }


@dataclass
class HttpResponse:
    status_code: int
    body: bytes = b""
    headers: dict = field(default_factory=dict)
    from_cache: bool = False

    def json(self):
        import json as _json
        return _json.loads(self.body.decode("utf-8"))

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class HttpClient:
    """Per-provider HTTP client. All provider adapters must use this."""

    def __init__(
        self,
        name: str,
        rate_per_sec: float | None = None,
        daily_budget: int | None = None,
        breaker: CircuitBreaker | None = None,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        backoff_max: float = 8.0,
        session: requests.Session | None = None,
        sleep=time.sleep,
        clock=time.monotonic,
        jitter=None,
    ):
        self.name = name
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.session = session or requests.Session()
        self._sleep = sleep
        self._clock = clock
        self._jitter = jitter or (lambda d: d * random.uniform(0.5, 1.5))
        self.rate = TokenBucket(rate_per_sec, clock=clock, sleep=sleep) if rate_per_sec else None
        self.budget = DailyBudget(daily_budget) if daily_budget else None
        self.breaker = breaker or CircuitBreaker()
        self._inflight: dict = {}
        self._inflight_lock = threading.Lock()
        self._validators: dict[str, dict] = {}   # url -> {"etag":..., "last_modified":...}
        self._bodies = TTLCache()                 # url -> HttpResponse (for 304 replay)

    # ── single-flight ──
    def _dedupe_key(self, method: str, url: str, params) -> tuple:
        return (method, url, tuple(sorted((params or {}).items())))

    # ── backoff helper ──
    def _delay_for(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return max(0.0, min(retry_after, self.backoff_max * 4))
        base = min(self.backoff_base * (2 ** attempt), self.backoff_max)
        return self._jitter(base)

    # ── conditional GET support ──
    def _conditional_headers(self, url: str) -> dict:
        v = self._validators.get(url)
        if not v:
            return {}
        h = {}
        if v.get("etag"):
            h["If-None-Match"] = v["etag"]
        if v.get("last_modified"):
            h["If-Modified-Since"] = v["last_modified"]
        return h

    def get(self, url: str, params: dict | None = None, headers: dict | None = None,
            timeout: float | None = None) -> HttpResponse | None:
        """Idempotent GET with full resilience policy. Returns None on
        (budget exhausted | breaker open | terminal failure | retries exhausted)."""
        key = self._dedupe_key("GET", url, params)

        with self._inflight_lock:
            entry = self._inflight.get(key)
            if entry is None:
                entry = {"event": threading.Event(), "result": None}
                self._inflight[key] = entry
                leader = True
            else:
                leader = False
        if not leader:
            entry["event"].wait(timeout=self.timeout + self.backoff_max * 4)
            return entry["result"]

        try:
            result = self._do_get(url, params, headers, timeout)
            entry["result"] = result
            return result
        finally:
            with self._inflight_lock:
                self._inflight.pop(key, None)
            entry["event"].set()

    def _do_get(self, url, params, headers, timeout) -> HttpResponse | None:
        if self.budget and not self.budget.try_consume():
            logger.warning("[%s] daily budget exhausted", self.name)
            return None
        if not self.breaker.allow():
            logger.info("[%s] circuit open — skipping call to %s", self.name, url)
            return None
        if self.rate:
            self.rate.acquire()

        merged = dict(headers or {})
        merged.update(self._conditional_headers(url))

        last_error = None
        for attempt in range(self.max_retries):
            try:
                resp = self.session.get(
                    url, params=params, timeout=timeout or self.timeout, headers=merged
                )

                if resp.status_code == 200:
                    self.breaker.record_success()
                    etag = resp.headers.get("ETag")
                    lm = resp.headers.get("Last-Modified")
                    if etag or lm:
                        self._validators[url] = {"etag": etag, "last_modified": lm}
                        self._bodies.set(url, HttpResponse(200, resp.content, dict(resp.headers)))
                    return HttpResponse(resp.status_code, resp.content, dict(resp.headers))

                if resp.status_code == 304:
                    self.breaker.record_success()
                    cached = self._bodies.get(url, ttl=86400 * 7)
                    if cached is not None:
                        return HttpResponse(200, cached.body, cached.headers, from_cache=True)
                    return None

                retry_after = None
                ra = resp.headers.get("Retry-After")
                if ra:
                    try:
                        retry_after = float(ra)
                    except ValueError:
                        retry_after = None

                if resp.status_code in (401, 403):
                    # Terminal: record failure, no reset-and-retry loop.
                    logger.warning("[%s] %s from %s — not retrying", self.name, resp.status_code, url)
                    self.breaker.record_failure()
                    return None

                if resp.status_code == 429 or 500 <= resp.status_code < 600:
                    self.breaker.record_failure()
                    last_error = f"HTTP {resp.status_code}"
                    if attempt + 1 < self.max_retries:
                        self._sleep(self._delay_for(attempt, retry_after))
                        continue
                    return None

                # Other 4xx: terminal, but not a service-health failure.
                logger.info("[%s] HTTP %s from %s", self.name, resp.status_code, url)
                return None

            except requests.RequestException as exc:
                self.breaker.record_failure()
                last_error = str(exc)
                if attempt + 1 < self.max_retries:
                    self._sleep(self._delay_for(attempt, None))
                    continue
                logger.warning("[%s] GET %s failed after %d attempts: %s",
                               self.name, url, attempt + 1, last_error)
                return None

        return None

    def get_json(self, url: str, params: dict | None = None,
                 headers: dict | None = None) -> dict | list | None:
        resp = self.get(url, params=params, headers=headers)
        if resp is None:
            return None
        try:
            return resp.json()
        except ValueError:
            logger.warning("[%s] non-JSON 200 response from %s", self.name, url)
            return None

    def status(self) -> dict:
        return {
            "name": self.name,
            "breaker": self.breaker.state(),
            "budget": self.budget.usage() if self.budget else None,
        }
