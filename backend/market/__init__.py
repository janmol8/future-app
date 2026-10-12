"""backend/market — dynamic Indian market data subsystem.

Layers:
  config      env-driven configuration (single source of truth for intervals/limits)
  timeutil    Asia/Kolkata logic, UTC storage helpers
  cache       thread-safe TTL cache with single-flight dedupe
  http        shared resilient HTTP layer (rate limit, budget, breaker, retries)
  providers   provider ABCs and adapters
  router      ordered provider chain with health failover
  scheduler   APScheduler jobs guarded by DB leases
"""
