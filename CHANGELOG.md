# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-11-02

### Added
- `get_keyed_limiter(limiter_id, identifier)`: the limiter for one caller-supplied value (user,
  client address, e-mail, tenant, token hash) that costs **no backend connection per identifier**.
  The limiter definition keeps a single engine and connection pool; the identifier is only a key
  that shares it. The group, and so the Redis key (`{key_prefix}:{limiter_id}:{identifier}:window`,
  `:rate`, `:concurrent`) and the PostgreSQL row, are exactly what a registered clone named
  `"{limiter_id}:{identifier}"` used, so adopting it resets no window and orphans no data.
- `RateLimiter.for_key(identifier)`, the engine-level capability behind it. Implemented by the
  Redis token bucket, Redis sliding window and PostgreSQL engines (a view that shares the pool,
  with its own metrics; its `disconnect()` never closes the shared pool) and by the memory engine
  (a bounded cache of per-key instances, see the next item).
- The memory engine's per-key cache never drops live state. A plain LRU would let a limited caller
  refresh its own budget by minting `cap` distinct keys, so: the cap is `max_keyed_limiters`
  (default 10,000, a `MemoryRateLimiter` argument and a memory store setting); at the cap, only
  entries with no live state (window expired, token interval elapsed, no held concurrency slot) are
  reclaimed, scanned at most once per second (`MemoryRateLimiter.is_idle`); when nothing is
  reclaimable, keys that are not cached share one overflow limiter with the base configuration
  (`group_id` `"{group_id}:*overflow*"`), so a flood of keys can neither raise the budget nor touch
  an older key.
  The default on the base class raises `UnsupportedLimiterEngineError`, so existing third-party
  engines keep working and opt in by overriding it.
- `UnsupportedLimiterEngineError` (a `RateLimiterError` and a `TypeError`): an engine with no
  per-key strategy fails loudly instead of silently opening one connection per key.
- Initialization of the shared engine is single-flight (concurrent first calls open one pool). When
  the backend is down it is retried at most once per second instead of once per request, and a
  `fail_closed` limiter keeps raising `RateLimiterAcquisitionError`.

### Fixed
- Open Redis and PostgreSQL connections, and the registry itself, grew without bound with the
  number of distinct identifiers passed to `get_or_clone_limiter`, `@rate_limited("api:{user_id}")`,
  `RateLimitDependency`, `RateLimitMiddleware` and `CompositeRateLimiter.check`: each new identifier
  registered a limiter that owned its own connection pool and nothing ever closed it. With
  caller-controlled identifiers (client addresses, hashes of unvalidated tokens) a long-running
  service ran out of file descriptors. They now go through `get_keyed_limiter`: one connection pool
  per limiter definition regardless of the number of identifiers.

### Changed
- `get_or_clone_limiter(base_id, unique_id)` keeps its signature and return type but no longer
  registers a limiter per identifier for the Redis, PostgreSQL and memory engines. It still
  returns a limiter registered explicitly as `"{base_id}:{unique_id}"` (configuration, or
  `clone_limiter` with overrides). For an engine without `for_key` it falls back to the previous
  behavior and logs a warning once. Observable differences: after the call the dynamic id is no
  longer returned by `get_limiter()`, `has_limiter()` or `list_limiters()` (`check_limiter("api:x")`
  and `acquire("api:x")` still resolve it); on Redis and PostgreSQL the returned object can be
  short-lived and its `get_metrics()` covers that object only; on the memory engine, at the cap with
  every entry live, new keys share one overflow budget.
- `clone_limiter` keeps registering (it creates a named variant, with overrides). Its documentation
  now says to use it for a small fixed set of variants and never with caller-supplied values.
- `X-RateLimit-Remaining` from `RateLimitDependency` and `RateLimitMiddleware` is read from the
  limiter state for sliding windows (it was a process-local counter on the limiter object).
- `MemoryRateLimiter.reset()` also resets the per-key limiters created with `for_key`
  (`reset_all_limiters()` keeps resetting limiters obtained through `get_or_clone_limiter`).
  On Redis and PostgreSQL it resets the definition only; use `reset_backend_store()` to clear
  per-identifier data in tests.

## [0.4.2] - 2026-08-11

### Fixed
- `tests/compliance/test_state.py::TestStateIntrospection` parametrized 6 tests over every
  engine that supports `get_state()`, including `redis` and `postgres`, without the
  `redis`/`postgres`/`integration` markers the rest of the compliance suite uses to keep
  infrastructure-requiring cases out of the `unit` CI job. This was masked as long as a
  backend connection failure during `initialize()` always raised, which the engine factory
  fixtures caught and turned into `pytest.skip(...)`. The 0.4.1 `fail_closed` fix corrected
  `initialize()` to degrade to a permissive fail-open state instead of raising (its intended,
  documented behavior for the default `fail_closed=False`), which stopped tripping that catch
  and let the `unit` job silently run these cases against no real backend. 4 of the 6 tests'
  assertions were strict enough to fail outright against the resulting fake permissive state;
  the other 2 happened to still pass by coincidence. Engine parametrization for this class now
  goes through a new `get_engine_params()` helper that attaches each engine's markers (already
  declared, but previously unused, on `ENGINE_CAPABILITIES` in `tests/compliance/utils.py`), so
  `redis`/`postgres` cases are properly deselected from `unit` and only run under the
  `integration-redis`/`integration-postgres` CI jobs, against real backends. No production code
  changed — the 0.4.1 `fail_closed` fix was correct; only this test's categorization was wrong.

## [0.4.1] - 2026-08-11

### Fixed
- `fail_closed` was silently ignored by `initialize()` on both the Redis and PostgreSQL
  engines, and was never respected at all by the blocking `acquire()` and `release()`
  methods — only `try_acquire()` and `get_state()` honored it. A backend connection
  failure during `initialize()` always propagated the raw driver exception regardless
  of `fail_closed`, breaking the fail-open promise (`fail_closed=False`, the default)
  right at startup. All five methods (`initialize()`, `acquire()`, `try_acquire()`,
  `release()`, `get_state()`) now consistently raise `RateLimiterAcquisitionError` when
  `fail_closed=True`, or log a warning and return a permissive result when
  `fail_closed=False`.
- The `except` clauses implementing `fail_closed` never actually matched real
  `redis-py`/`asyncpg` exceptions — their exception hierarchies don't inherit from
  Python's builtin `OSError`/`ConnectionError`/etc. — so the `fail_closed` contract
  was effectively unenforceable against a real backend outage even where it looked
  implemented. Both engines now also catch the driver-specific connection exception
  types.
- `ratesync.__version__` still reported `0.3.0` after the `0.4.0` release (drift
  between `pyproject.toml` and `__init__.py`).

## [0.4.0] - 2026-03-20

### Changed
- Widen redis dependency range from `^5.0.0` to `>=5.0.0,<8.0.0` to support redis-py 6.x and 7.x
- Update dev dependency to redis `^7.3.0` for testing with latest client

## [0.3.0] - 2025-02-03

### Added
- **Template strings in `@rate_limited` decorator**: Use `@rate_limited("api:{user_id}")` to resolve placeholders from function arguments at call time. Limiters are auto-cloned from the base config on first use.
- FastAPI optional extra: `pip install rate-sync[fastapi]` now installs FastAPI and Starlette as optional dependencies, consistent with engine extras (`redis`, `postgres`)
- `all` extra now includes FastAPI/Starlette alongside engine dependencies

### Changed
- Rename GitHub repository from `rate-sync/rate-sync` to `rate-sync/python` to support multi-language implementations
- Update all project URLs, badges, documentation links, and clone instructions to new repo path
- RuntimeError messages in `ratesync.contrib.fastapi` modules now reference `pip install rate-sync[fastapi]` instead of `pip install fastapi`
- Updated installation docs across README, docs, and examples to reflect the new `fastapi` extra

## [0.2.2] - 2025-01-27

### Changed
- Migrate repository from `zyc/rate-sync` to `rate-sync/python` organization
- Update all project URLs, badges, and documentation links to new org
- Add project logo (PNG, SVG) and social preview banner
- Add "What It Solves" section to README with real-world use case scenarios

## [0.2.1] - 2025-01-27

### Changed
- README: add "What It Solves" section with real-world problem scenarios and deep-dive links
- README: add missing pattern doc links (Abuse Prevention, Monitoring, Testing)

## [0.2.0] - 2025-01-27

### Added
- PostgreSQL engine for persistent rate limiting
- Redis engine with Lua scripts for atomic operations
- Sliding window algorithm (Redis and Memory engines)
- FastAPI integration: `RateLimitDependency`, `RateLimitMiddleware`, rate limit headers
- `clone_limiter()` for per-user/per-tenant limiting
- `fail_closed` option for strict mode
- Composite rate limiting with `CompositeRateLimiter` (strategies: `most_restrictive`, `all_must_pass`, `any_must_pass`)
- Public API: `get_or_clone_limiter()`, `hash_identifier()`, `combine_identifiers()`
- Configuration introspection: `get_config()`, `get_state()`, `list_limiters()`
- Testing utilities in `ratesync.testing` module
- Domain layer with `domain/value_objects/` structure
- Concurrency limiting via `max_concurrent` parameter
- `release()` method and automatic slot release in `acquire_context()`
- Standardized compliance test suite for multi-engine testing
- Tag-based automated releases with Trusted Publishing
- Pattern guides for authentication, API tiering, testing, monitoring, and more

### Changed
- Refactored API for improved type safety
- Separated store and limiter configuration
- Project metadata updated for open source release
- `hash_identifier()` and `combine_identifiers()` moved to `domain/value_objects/identifier.py`
- `rate_per_second` now optional (requires at least one of `rate_per_second` or `max_concurrent`)
- Memory engine accepts `algorithm` parameter (`token_bucket` or `sliding_window`)
- All project documentation translated to English

### Removed
- NATS engine (replaced by Redis and PostgreSQL engines)

### Fixed
- Memory engine sliding window implementation for deterministic testing
- Registry configuration parsing for memory + sliding window

## [0.1.0] - 2025-10-15

### Added
- Initial release
- Memory engine for single-process apps
- NATS engine for distributed limiting via JetStream KV
- Token bucket algorithm
- Full async/await support
- Auto-loading from `rate-sync.toml`
- `@rate_limited` decorator
- Built-in metrics (`RateLimiterMetrics`)
- Environment variable expansion in TOML (`${VAR}` syntax)
