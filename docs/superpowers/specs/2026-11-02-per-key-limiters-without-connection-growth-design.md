# Per-key limiters without connection growth: Design Spec

Date: 2026-11-02
Status: Approved by the package owner (delegated run). Implements Python `0.5.0` and the TypeScript parity addition.
Scope: `python/` (primary) and `typescript/` (parity).

## 1. Problem

`ratesync.get_or_clone_limiter(base_id, identifier)` is the documented pattern for per-user,
per-IP and per-tenant limits. Every call with an identifier not seen before runs `clone_limiter`,
which registers a new limiter in the process-wide registry under `"{base_id}:{identifier}"`.

For the pooled engines (`redis`, `postgres`), each engine instance owns its own connection pool
(`ConnectionPool` plus `Redis` client, or an `asyncpg` pool), created by `initialize()` and
released only by an explicit `disconnect()`. The registry has no eviction, no TTL and no
unregister. So:

1. Open connections grow with the number of distinct identifiers ever seen (one pool per
   identifier, each holding at least one socket after the first `PING`).
2. The registry dictionary itself grows without bound (memory, on every engine including memory).
3. The library's own integrations repeat the pattern: `@rate_limited("api:{user_id}")`,
   `RateLimitDependency`, `RateLimitMiddleware` and `CompositeRateLimiter.check` all clone per
   identifier.

When the identifier is caller-controlled (a hash of a bearer token that is never validated at that
layer, a client address, an e-mail), a scanner or any client behind a rotating address mints a new
identifier per request. Observed in production on 2026-11-02 in a service that uses `rate-sync`
0.4.2 through `get_or_clone_limiter`: 1024 of 1024 file descriptors in use, 1008 of them TCP
connections to Redis, after about five days. `accept()` failed and the service was down for about
70 minutes. The consuming application worked around it with a local shim; this spec moves the
capability into the library.

## 2. Findings that shape the design

- The Redis key of a limiter is derived from `group_id` alone
  (`{key_prefix}:{group_id}:window`, and `:rate`, `:concurrent` for the token bucket). The
  PostgreSQL engine keys rows on `group_id`. So the identifier is only a key; it never needs its
  own connection.
- A shallow copy of an initialized pooled engine, with `group_id` replaced, shares the `Redis`
  client, the pool and the registered Lua scripts (they are references), and addresses the
  identifier's key. Nothing needs to be cached per identifier, so nothing can grow.
- The Redis token-bucket and PostgreSQL engines apply `group_id.strip()` in `__init__`. The legacy
  clone therefore stored a stripped `"{base}:{identifier}"`. A copy must apply the same strip or an
  identifier with trailing whitespace would address a different key than before (a deploy would
  reset that window). The sliding-window engine does not strip; it keeps the raw value.
- The memory engine holds all state in the instance (timestamps, lock, semaphore). It cannot share
  anything, so it needs one instance per identifier. It has no connection to leak, but it needs a
  bound.
- `RateLimiterMetrics` is per instance. A copy needs its own metrics object; sharing the base's
  would mix every identifier's counters.
- `disconnect()` on a limiter closes its pool. Called on a copy it would close the pool every other
  copy uses. Copies must never close the shared backend.
- The TypeScript package already shares one `RateLimitStore` (one client) per `configureStore`, and
  a `RateLimiter` is a light object that holds a reference to it. There is no connection per
  identifier. See section 7.

## 3. Options considered

A. Bounded expiring cache of the library's clones that closes the backend on eviction.
Rejected. The registry has no unregister, so it needs writes into private state. Each retained
clone still costs a pool, so the bound must be small to be safe, and then an attacker minting
identifiers forces a connect, a `PING` and a close on every request. An eviction racing an
in-flight acquire closes a pool under it.

B. One pool per limiter definition, identifier is only a key (chosen).
A first-class per-key capability on the engines, plus a registry-level function that initializes
the definition's engine once and returns a per-key view. The connection count is one pool per
limiter definition regardless of the number of identifiers.

C. One pool per store, shared by every limiter on it.
Also fixes explicit `clone_limiter` growth in connections, but changes the meaning of
`pool_max_size` (per limiter today, per store after), adds an ownership problem (who closes the
shared pool) and touches every engine constructor and the registry. Larger blast radius than the
incident needs. Not chosen; can be revisited.

D. Document the problem and tell users to raise `ulimit -n`.
Rejected as the fix: it moves the date of the outage.

## 4. Decisions

D1. Public name and signature (stable, chosen so that an application shim of the same name becomes
a one-line import change):

```python
async def get_keyed_limiter(limiter_id: str, identifier: str) -> RateLimiter
```

Exported as `ratesync.get_keyed_limiter` (module `ratesync.keyed`). It returns a limiter whose
`group_id` is `"{limiter_id}:{identifier}"`, whose Redis or PostgreSQL key is byte-identical to the
one the legacy clone used, and that is ready to use. It does not register anything in the registry.
Raises `LimiterNotFoundError` for an unknown `limiter_id` and `UnsupportedLimiterEngineError` for an
engine with no per-key strategy. It does not look up explicitly registered limiters named
`"{limiter_id}:{identifier}"` (it is a pure keying function; see D5 for the legacy function that
does).

D2. Engine level capability: `RateLimiter.for_key(identifier: str) -> RateLimiter`, a concrete
(non-abstract) method on the base class that raises `UnsupportedLimiterEngineError` by default, so
third-party engines written against the 0.4 base class keep importing and working, and opt in by
overriding it. Contract: the returned limiter addresses `"{self.group_id}:{identifier}"`, and shares
the backend with `self`.

- Redis token bucket, Redis sliding window, PostgreSQL: a shallow copy of the root engine with
  `_group_id` replaced (stripped where the constructor strips), a fresh `RateLimiterMetrics`, and a
  link to the root. A copy of a copy resolves to the same root. The copy shares pool, client and
  scripts. Its `initialize()` delegates to the root and adopts the backend references (so a copy
  created before the root finished initializing heals itself instead of opening a second pool).
  Its `disconnect()` only detaches the copy and never closes the shared backend.
- Memory: the instance keeps a bounded LRU of independent clones built from its public
  properties, at most `MemoryRateLimiter.MAX_KEYED_LIMITERS` (10,000) per base, oldest evicted.
  An evicted window restarts, harmless for an engine that is single-process by definition. The
  clones are initialized on creation (the memory `initialize()` is only a flag).
- `for_key` is synchronous and does no I/O.

D3. Initialization and outage behaviour of `get_keyed_limiter` (the registry-level function):

- The root engine is initialized at most once, under a per-limiter, per-event-loop lock (two
  concurrent first calls open one pool).
- When the backend is down and `fail_closed=False`, `initialize()` returns without raising and the
  engine is degraded (fail-open). Retries are limited to one per second per limiter
  (`REINITIALIZE_AFTER_SECONDS`), so an outage does not queue a connect timeout per request behind
  the lock. Callers get a degraded view that allows requests, as before.
- When `fail_closed=True`, the `RateLimiterAcquisitionError` from `initialize()` is raised to the
  caller, and during the retry interval the same error is re-raised without touching the backend.
  This matches what each legacy `get_or_clone_limiter` call did (it re-ran `initialize()` and
  raised), without paying a connect timeout per request.

D4. Unsupported engines fail loudly. `get_keyed_limiter` on an engine whose class does not
override `for_key` raises `UnsupportedLimiterEngineError` (subclass of `RateLimiterError` and
`TypeError`) before any connection is made. It never falls back to the leaking path silently.

D5. `get_or_clone_limiter(base_id, unique_id)` keeps its signature and return type and stops
leaking for every engine that supports `for_key`:

1. If a limiter named `"{base_id}:{unique_id}"` was explicitly registered (by configuration or by
   `clone_limiter` with overrides), it is returned as before (initialized if needed). This keeps
   per-identifier overrides, such as a premium tenant with a higher limit, working.
2. Otherwise it returns `get_keyed_limiter(base_id, unique_id)`. Nothing is registered.
3. For an engine without `for_key` (a custom engine) it falls back to the legacy
   clone-and-register path and logs a warning once per limiter id, so nothing that works today
   breaks.

Observable differences, all documented in the changelog: after `get_or_clone_limiter`, the dynamic
id is no longer in `list_limiters()` / `has_limiter()` / `get_limiter()` (it was never documented
as a registration API); on pooled engines the returned object is transient and its metrics cover
only that object; on the memory engine an identifier idle long enough to be evicted from the LRU
starts a fresh window.

D6. `clone_limiter(source, new_id, **overrides)` keeps registering. It is the explicit API to
create a named variant of a limiter (for example `payments` -> `payments-premium` with a different
rate), the registry entry is the point, and the number of such variants is chosen by the developer,
not by callers. Its docstring and the docs now say: never call it with a caller-supplied value; use
`get_keyed_limiter` for that. Not changed: making registered clones share the source's pool would
need overrides on per-engine private fields and a lifecycle for the shared pool (option C).

D7. The library's own integrations move onto the leak-free path through `get_or_clone_limiter`:
the `@rate_limited("api:{user_id}")` template resolver, `RateLimitDependency`, `RateLimitMiddleware`
and `CompositeRateLimiter.check`. Because a view's metrics are per object, `remaining` in those
integrations is computed from `get_state()` (the backend's truth, not a process-local counter) and
falls back to the previous estimate only when the engine has no state.

D8. Public error: `UnsupportedLimiterEngineError(RateLimiterError, TypeError)` in
`ratesync.exceptions`, exported from `ratesync`.

D9. Version: minor bump, `0.4.2 -> 0.5.0`. New public API and a changed side effect of
`get_or_clone_limiter` (no registration) in a `0.x` line where minor versions may carry that.

## 5. Out of scope

- Changing any rate, window, key format or default.
- Pool sharing between registered limiters of the same store (option C).
- A public `close_keyed_limiters()` (nothing is cached for pooled engines; the memory LRU is
  per-engine and dies with it).
- Changing `fail_closed` semantics.

## 6. Acceptance criteria (Python)

| ID | Criterion | Test |
|----|-----------|------|
| AC1 | 5,000 distinct identifiers on the real Redis token-bucket engine class create exactly one `ConnectionPool`; the count is flat between 100 and 5,000 identifiers | `tests/test_keyed_limiters.py::test_pool_count_stays_flat_token_bucket` |
| AC2 | The same for the Redis sliding-window engine class and for the PostgreSQL engine class | `::test_pool_count_stays_flat_sliding_window`, `::test_pool_count_stays_flat_postgres` |
| AC3 | `get_or_clone_limiter` with thousands of identifiers creates one pool and registers nothing | `::test_get_or_clone_does_not_grow_pools_or_registry` |
| AC4 | Each identifier has its own budget on the shared connection | `::test_each_identifier_has_its_own_budget` |
| AC5 | The key a view addresses equals the key the legacy clone addressed, for every engine class, including an identifier with trailing whitespace | `::test_key_is_byte_identical_to_legacy_clone` |
| AC6 | Real Redis: a view and a legacy clone of the same identifier consume the same key | `tests/integration/test_keyed_limiters_redis.py` |
| AC7 | Concurrent first use opens one pool; an outage retries at most once per interval; `fail_closed` re-raises during the interval | `::test_concurrent_first_use_opens_one_pool`, `::test_outage_*` |
| AC8 | `disconnect()` on a view never closes the shared backend; a view created before the root initialized heals itself | `::test_view_disconnect_is_a_noop_on_the_backend`, `::test_view_created_early_adopts_the_backend` |
| AC9 | The memory LRU is bounded, evicts the oldest entry and keeps one instance per identifier | `::test_memory_lru_is_bounded` |
| AC10 | An engine without `for_key` raises `UnsupportedLimiterEngineError`, and `get_or_clone_limiter` falls back to the legacy path for it | `::test_unsupported_engine_*` |
| AC11 | `get_or_clone_limiter` honors an explicitly registered `"{base}:{id}"` limiter | `::test_get_or_clone_honors_registered_override` |
| AC12 | Integrations (`@rate_limited` template, dependency, middleware, composite) register no limiter per identifier and keep their behaviour | existing suites plus `::test_integrations_do_not_register_per_identifier_limiters` |
| AC13 | All pre-existing unit tests still pass | `poetry run pytest -m "not integration"` |

## 7. TypeScript parity decision

Checked `typescript/src/core/registry.ts`, `rate-limiter.ts` and the stores. `RateLimiter` receives
the `RateLimitStore` that `configureStore` created; `cloneLimiter` and `getOrCloneLimiter` build a
new `RateLimiter` over the same store. Redis and PostgreSQL clients belong to the store, so the
number of connections is already independent of the number of identifiers. The Python incident
cannot happen there.

What does exist in TypeScript is the registry growth (`this.limiters.set(newId, cloned)` per
identifier, never removed): memory only, one small object per identifier.

Decision: add `getKeyedLimiter(baseId, identifier)` to the TypeScript package with the same name
and meaning as Python (a limiter with id `"{baseId}:{identifier}"` over the shared store, nothing
registered, initialized), and document it as the call for caller-supplied identifiers.
`cloneLimiter` and `getOrCloneLimiter` keep their contract (they register, and `getOrCloneLimiter`
is synchronous); their TSDoc and the README point to `getKeyedLimiter`. No behaviour change, so a
minor bump. The package has never been published to npm (`npm view rate-sync` answers 404) and has
no release tag; publication is a separate owner decision (section 8).

## 8. Release

- Python: `0.5.0` through the tag workflow in `RELEASING.md` (PyPI Trusted Publishing).
- TypeScript: `package.json` and `CHANGELOG.md` prepared as `0.2.0`; not tagged or published here,
  because the `rate-sync` name on npm is unclaimed and the repository has no CI publish workflow or
  recorded npm owner. Left as an owner decision.

## 9. Traceability

Consumer incident record and workaround: the consuming application's connection-leak spec of the
same date. Library files touched: `core.py`, `exceptions.py`, `keyed.py` (new), `registry.py`,
`engines/{redis,redis_sliding_window,postgres,memory}.py`, `decorators.py`,
`composite.py`, `contrib/fastapi/{dependencies,middleware}.py`, `__init__.py`, docs.
