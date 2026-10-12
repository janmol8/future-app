"""Offline tests for the shared HTTP layer. No live network — a scripted fake
session stands in for requests.Session. Covers: retry/backoff on 5xx+429 with
Retry-After, terminal 401/403 (no reset-and-retry), circuit breaker open/half-open,
token bucket pacing, daily budget, single-flight dedupe, conditional GET."""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from market.http import HttpClient, CircuitBreaker, DailyBudget, TokenBucket, HttpResponse


class FakeResponse:
    def __init__(self, status_code=200, body=b"{}", headers=None):
        self.status_code = status_code
        self.content = body
        self.text = body.decode("utf-8", errors="replace")
        self.headers = headers or {}

    def json(self):
        import json
        return json.loads(self.content.decode("utf-8"))


class FakeSession:
    """Scripted session: pops one response per call; records calls."""
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        if not self.script:
            return FakeResponse(200)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClock:
    """Controllable monotonic clock with recorded sleeps."""
    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def time(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds


@pytest.fixture()
def clock():
    return FakeClock()


def make_client(script, clock, **kwargs):
    defaults = dict(
        name="test",
        rate_per_sec=1000.0,        # effectively no pacing in most tests
        daily_budget=100,
        timeout=1.0,
        max_retries=3,
        backoff_base=0.5,
        backoff_max=8.0,
        session=FakeSession(script),
        sleep=clock.sleep,
        clock=clock.time,
        jitter=lambda d: d,         # deterministic: no jitter in tests
    )
    defaults.update(kwargs)
    return HttpClient(**defaults)


def test_retries_on_500_then_succeeds(clock):
    client = make_client(
        [FakeResponse(500), FakeResponse(200, b'{"ok": true}')], clock
    )
    data = client.get_json("http://x/y")
    assert data == {"ok": True}
    assert len(client.session.calls) == 2
    assert clock.sleeps and clock.sleeps[0] == pytest.approx(0.5)


def test_backoff_is_exponential_and_capped(clock):
    client = make_client(
        [FakeResponse(500), FakeResponse(500), FakeResponse(500)], clock,
        max_retries=3, backoff_base=0.5, backoff_max=1.0,
    )
    assert client.get("http://x/y") is None
    assert clock.sleeps == [pytest.approx(0.5), pytest.approx(1.0)]


def test_honours_retry_after(clock):
    client = make_client(
        [FakeResponse(429, headers={"Retry-After": "3"}), FakeResponse(200, b"{}")], clock,
    )
    assert client.get("http://x/y") is not None
    assert clock.sleeps[0] == pytest.approx(3.0)


def test_401_is_terminal_no_retry(clock):
    client = make_client([FakeResponse(401), FakeResponse(200, b"{}")], clock)
    assert client.get("http://x/y") is None
    assert len(client.session.calls) == 1          # no second attempt
    assert client.breaker.state()["consecutive_failures"] == 1


def test_403_is_terminal_no_retry(clock):
    client = make_client([FakeResponse(403)], clock)
    assert client.get("http://x/y") is None
    assert len(client.session.calls) == 1


def test_404_is_terminal_but_not_breaker_failure(clock):
    client = make_client([FakeResponse(404)], clock)
    assert client.get("http://x/y") is None
    assert client.breaker.state()["consecutive_failures"] == 0


def test_network_errors_are_retried(clock):
    import requests
    client = make_client(
        [requests.ConnectionError("boom"), FakeResponse(200, b"{}")], clock
    )
    assert client.get("http://x/y") is not None
    assert len(client.session.calls) == 2


def test_circuit_opens_after_threshold_and_half_open_probes(clock):
    breaker = CircuitBreaker(failure_threshold=3, cooldown=120.0, clock=clock.time)
    client = make_client([FakeResponse(500)] * 3, clock, breaker=breaker)
    for _ in range(3):
        client.get("http://x/y")
    assert breaker.state()["state"] == "open"

    # While open: calls are skipped without hitting the session.
    client2_script = FakeSession([FakeResponse(200, b"{}")])
    client.session = client2_script
    assert client.get("http://x/y") is None
    assert len(client2_script.calls) == 0

    # After cooldown, one half-open probe is allowed.
    clock.t += 121
    client.session = FakeSession([FakeResponse(200, b"{}")])
    result = client.get("http://x/y")
    assert result is not None and result.status_code == 200
    assert breaker.state()["state"] == "closed"


def test_daily_budget_exhausted(clock):
    client = make_client([FakeResponse(200, b"{}")] * 5, clock, daily_budget=2)
    assert client.get("http://a/1") is not None
    assert client.get("http://a/2") is not None
    assert client.get("http://a/3") is None        # budget gone
    assert len(client.session.calls) == 2


def test_budget_resets_next_day(clock):
    from market import http as http_mod
    client = make_client([FakeResponse(200, b"{}")] * 4, clock, daily_budget=1)
    assert client.get("http://a/1") is not None
    assert client.get("http://a/2") is None
    # Roll the IST day forward.
    real_today = DailyBudget._today
    DailyBudget._today = staticmethod(lambda: "2000-01-02")
    try:
        assert client.get("http://a/3") is not None
    finally:
        DailyBudget._today = staticmethod(real_today)


def test_token_bucket_paces(clock):
    bucket = TokenBucket(rate_per_sec=2.0, clock=clock.time, sleep=clock.sleep)
    bucket.acquire()                       # capacity token, free
    bucket.acquire()                       # capacity token, free
    t0 = clock.t
    bucket.acquire()                       # must wait ~0.5 s for a new token
    assert clock.t - t0 == pytest.approx(0.5, abs=0.01)


def test_single_flight_dedupes_inflight_calls(clock):
    """Deterministic: a follower arrives while a leader is in flight, must
    adopt the leader's result without making its own upstream call."""
    from market.http import HttpResponse

    client = make_client([], clock)
    key = client._dedupe_key("GET", "http://x/slow", None)
    entry = {
        "event": threading.Event(),
        "result": HttpResponse(200, b'{"n": 1}'),
    }
    with client._inflight_lock:
        client._inflight[key] = entry      # simulate an in-flight leader call

    results = []
    t = threading.Thread(target=lambda: results.append(client.get_json("http://x/slow")))
    t.start()
    t.join(timeout=0.3)                    # follower must block on the leader
    assert t.is_alive()                    # still waiting — no second upstream call
    assert len(client.session.calls) == 0  # follower never hit the session

    entry["event"].set()                   # leader completes
    t.join(timeout=2)
    assert results == [{"n": 1}]           # follower adopted the leader's result


def test_conditional_get_replays_304(clock):
    client = make_client(
        [
            FakeResponse(200, b'{"v": 1}', headers={"ETag": '"abc"'}),
            FakeResponse(304, headers={}),
        ],
        clock,
    )
    first = client.get("http://x/y")
    assert first.status_code == 200
    second = client.get("http://x/y")
    assert second is not None and second.from_cache is True
    assert second.json() == {"v": 1}
    # Second request carried If-None-Match.
    assert client.session.calls[1]["headers"].get("If-None-Match") == '"abc"'
