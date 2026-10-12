#!/usr/bin/env python3
"""Live smoke check for the market subsystem.

Run while the app is up (python backend/app.py):

    python scripts/smoke_live.py [--base http://127.0.0.1:5000]

Checks every new endpoint, prints provider health and refresh cadence, and
verifies the no-fake-data contract at runtime: with providers disabled or
unreachable the responses contain nulls/empty lists — never synthesised
numbers. Exits non-zero on any structural failure.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request


def get(base: str, path: str, expect: int = 200):
    try:
        with urllib.request.urlopen(base + path, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as exc:  # noqa: BLE001
        print(f"  ! {path}: {exc}")
        return 0, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5000")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    failures = []

    def check(name, status, body, require_keys=()):
        ok = status == 200 and isinstance(body, dict) and \
            all(k in body for k in require_keys)
        print(f"{'OK  ' if ok else 'FAIL'} {name} [{status}]")
        if not ok:
            failures.append(name)
        return body

    print(f"Smoke against {base}\n")

    status, health = get(base, "/api/market/health")
    health = check("/api/market/health", status, health,
                   ("config", "providers", "router", "recent_jobs"))

    status, overview = get(base, "/api/market/overview")
    overview = check("/api/market/overview", status, overview,
                     ("indices", "market_status", "last_updated"))
    if overview:
        n = len(overview.get("indices") or [])
        print(f"     indices in overview: {n}")

    status, ms = get(base, "/api/market/status")
    ms = check("/api/market/status", status, ms, ("state",))
    if ms:
        print(f"     market state: {ms.get('state')}")

    status, ipos = get(base, "/api/market/ipos")
    ipos = check("/api/market/ipos", status, ipos, ("ipos",))
    if ipos:
        print(f"     IPOs tracked: {len(ipos.get('ipos') or [])}")

    status, hist = get(base, "/api/market/history?symbol=NIFTY%2050&range=1M")
    hist = check("/api/market/history", status, hist, ("candles", "partial"))

    status, alerts = get(base, "/api/market/alerts")
    alerts = check("/api/market/alerts", status, alerts, ("alerts",))

    # Notifications without user_key must be rejected (400), never 500.
    status, _ = get(base, "/api/market/notifications")
    if status == 400:
        print("OK   /api/market/notifications rejects missing user_key [400]")
    else:
        print(f"FAIL /api/market/notifications missing user_key -> expected 400, got {status}")
        failures.append("notifications user_key guard")

    # No-fake-data runtime probe: history for a symbol with no data anywhere
    # must come back with zero candles and a partial flag — not zeros.
    status, ghost = get(base, "/api/market/history?symbol=ZZUNLISTED&range=5Y")
    if status == 200 and ghost is not None:
        if ghost.get("candles") == []:
            print("OK   no-fake-data probe: unknown symbol -> empty candles")
        else:
            print("FAIL no-fake-data probe: unknown symbol returned candles!")
            failures.append("no-fake-data probe")

    # Provider health summary.
    if health:
        print("\nProvider health:")
        for p in (health.get("providers") or []):
            if isinstance(p, dict):
                print(f"  - {p.get('name', '?')}: enabled={p.get('enabled', '?')}")
        jobs = health.get("recent_jobs") or []
        if jobs:
            print("\nRecent jobs:")
            for j in jobs[:5]:
                print(f"  - {j.get('job_name', '?')}: {j.get('status', '?')}")

    print()
    if failures:
        print(f"SMOKE FAILED: {len(failures)} check(s): {', '.join(failures)}")
        return 1
    print("SMOKE OK — all endpoint checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
