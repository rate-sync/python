"""Tests for per-key limiters that cost no backend connection per key.

Spec: docs/superpowers/specs/2026-11-02-per-key-limiters-without-connection-growth-design.md

``get_or_clone_limiter`` used to register one limiter per identifier, and every pooled engine owns
its own connection pool, so connections grew with the number of distinct identifiers. These tests
pin the replacement: one pool per limiter DEFINITION, the identifier is only a key.

The pool-counting tests run the REAL engine classes (``RedisRateLimiter``,
``RedisSlidingWindowRateLimiter``, ``PostgresRateLimiter``); only the driver entry points that
open a pool are replaced by counting fakes, so a regression that creates a pool per identifier
fails here without needing a server.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

import ratesync
from ratesync import (
    clone_limiter,
    configure_limiter,
    configure_store,
    get_keyed_limiter,
    get_limiter,
    get_or_clone_limiter,
)
from ratesync import keyed
from ratesync.core import RateLimiter
from ratesync.engines.memory import MemoryRateLimiter
from ratesync.engines.redis import RedisRateLimiter
from ratesync.engines.redis_sliding_window import RedisSlidingWindowRateLimiter
from ratesync.exceptions import (
    LimiterNotFoundError,
    RateLimiterAcquisitionError,
    RateLimiterError,
    UnsupportedLimiterEngineError,
)
from ratesync.registry import _registry

try:
    from ratesync.engines.postgres import PostgresRateLimiter
except ImportError:  # pragma: no cover
    PostgresRateLimiter = None  # type: ignore[assignment,misc]


# =============================================================================
# FAKES: count how many pools the real engines open
# =============================================================================


class FakePool:
    """Stands in for ``redis.asyncio.ConnectionPool``."""

    created = 0
    disconnected = 0

    @classmethod
    def from_url(cls, url: str, **kwargs):
        cls.created += 1
        return cls()

    async def disconnect(self) -> None:
        type(self).disconnected += 1


class FakeRedis:
    """Stands in for ``redis.asyncio.Redis``."""

    ping_fails = False

    def __init__(self, connection_pool):
        self.connection_pool = connection_pool

    async def ping(self) -> bool:
        if type(self).ping_fails:
            raise ConnectionError("redis is down")
        return True

    def register_script(self, script: str):
        # token bucket: [success, wait_ms, concurrent]; sliding window: [allowed, remaining, reset]
        return AsyncMock(return_value=[1, 0, 1])

    async def aclose(self) -> None:
        return None


class FakePgPool:
    """Stands in for an ``asyncpg`` pool."""

    created = 0
    closed = 0

    async def close(self) -> None:
        type(self).closed += 1


async def _fake_create_pool(*args, **kwargs):
    FakePgPool.created += 1
    return FakePgPool()


@pytest.fixture(autouse=True)
def reset_state():
    FakePool.created = FakePool.disconnected = 0
    FakeRedis.ping_fails = False
    FakePgPool.created = FakePgPool.closed = 0
    keyed.reset_keyed_limiters()
    saved = (dict(_registry._limiters), dict(_registry._backends))
    _registry._limiters.clear()
    _registry._backends.clear()
    yield
    keyed.reset_keyed_limiters()
    _registry._limiters.clear()
    _registry._backends.clear()
    _registry._limiters.update(saved[0])
    _registry._backends.update(saved[1])


@pytest.fixture
def fake_redis():
    with (
        patch("ratesync.engines.redis.ConnectionPool", FakePool),
        patch("ratesync.engines.redis.redis_asyncio.Redis", FakeRedis),
        patch("ratesync.engines.redis_sliding_window.ConnectionPool", FakePool),
        patch("ratesync.engines.redis_sliding_window.redis_asyncio.Redis", FakeRedis),
    ):
        yield


@pytest.fixture
def fake_postgres():
    with patch("ratesync.engines.postgres.asyncpg_module.create_pool", _fake_create_pool):
        yield


def _redis_limiter(group_id: str = "login", **kwargs) -> RedisRateLimiter:
    kwargs.setdefault("rate_per_second", 100.0)
    return RedisRateLimiter(url="redis://fake:6379/0", group_id=group_id, **kwargs)


def _sliding_limiter(group_id: str = "login", **kwargs) -> RedisSlidingWindowRateLimiter:
    kwargs.setdefault("limit", 5)
    kwargs.setdefault("window_seconds", 60)
    return RedisSlidingWindowRateLimiter(url="redis://fake:6379/0", group_id=group_id, **kwargs)


def _postgres_limiter(group_id: str = "login", **kwargs) -> PostgresRateLimiter:
    kwargs.setdefault("rate_per_second", 100.0)
    return PostgresRateLimiter(
        connection_url="postgresql://x:y@fake/db", group_id=group_id, auto_create=False, **kwargs
    )


def _register(limiter: RateLimiter, limiter_id: str = "login") -> None:
    """Install a ready-made engine as the registry's limiter for ``limiter_id``."""
    configure_store("mem", strategy="memory")
    configure_limiter(limiter_id, store_id="mem", rate_per_second=1.0)
    store_id, rate, conc, timeout, _, algo, limit, window, fail_closed = (
        _registry.get_limiter_config(limiter_id)
    )
    _registry._limiters[limiter_id] = (
        store_id,
        rate,
        conc,
        timeout,
        limiter,
        algo,
        limit,
        window,
        fail_closed,
    )


# =============================================================================
# AC1, AC2: the number of pools is flat, on the real engine classes
# =============================================================================


class TestPoolCountStaysFlat:
    async def test_the_counter_sees_a_pool_per_engine_instance(self, fake_redis):
        """Canary: the old pattern (one engine instance per identifier) is visible to the fakes."""
        for i in range(50):
            await _redis_limiter(group_id=f"login:legacy-{i}").initialize()

        assert FakePool.created == 50

    async def test_redis_token_bucket(self, fake_redis):
        _register(_redis_limiter())

        for i in range(100):
            limiter = await get_keyed_limiter("login", f"id-{i}")
            assert await limiter.try_acquire(timeout=0) is True
        pools_at_100 = FakePool.created

        for i in range(100, 5000):
            limiter = await get_keyed_limiter("login", f"id-{i}")
            assert await limiter.try_acquire(timeout=0) is True

        assert pools_at_100 == 1
        assert FakePool.created == 1
        assert FakePool.disconnected == 0

    async def test_redis_sliding_window(self, fake_redis):
        _register(_sliding_limiter())

        for i in range(5000):
            limiter = await get_keyed_limiter("login", f"id-{i}")
            assert await limiter.try_acquire(timeout=0) is True

        assert FakePool.created == 1

    @pytest.mark.skipif(PostgresRateLimiter is None, reason="asyncpg not installed")
    async def test_postgres(self, fake_postgres):
        pg = _postgres_limiter()
        _register(pg)
        await pg.initialize()  # opens the pool once; try_acquire itself needs a server

        for i in range(5000):
            limiter = await get_keyed_limiter("login", f"id-{i}")
            assert limiter.is_initialized
            assert limiter.group_id == f"login:id-{i}"

        assert FakePgPool.created == 1
        assert FakePgPool.closed == 0

    async def test_get_or_clone_does_not_grow_pools_or_registry(self, fake_redis):
        _register(_redis_limiter())
        registered_before = set(_registry._limiters)

        for i in range(3000):
            limiter = await get_or_clone_limiter("login", f"id-{i}")
            assert limiter.group_id == f"login:id-{i}"
            assert limiter.is_initialized

        assert FakePool.created == 1
        assert set(_registry._limiters) == registered_before


# =============================================================================
# AC4, AC9: budgets are independent; memory engine is bounded
# =============================================================================


class TestMemoryEngine:
    @pytest.fixture(autouse=True)
    def memory_base(self):
        configure_store("mem", strategy="memory")
        configure_limiter(
            "login", store_id="mem", limit=2, window_seconds=60, algorithm="sliding_window"
        )

    async def test_each_identifier_has_its_own_budget(self):
        a = await get_keyed_limiter("login", "a")
        b = await get_keyed_limiter("login", "b")

        assert await a.try_acquire(timeout=0) is True
        assert await a.try_acquire(timeout=0) is True
        assert await a.try_acquire(timeout=0) is False
        assert await b.try_acquire(timeout=0) is True

    async def test_same_identifier_is_the_same_instance(self):
        first = await get_keyed_limiter("login", "a")
        again = await get_keyed_limiter("login", "a")

        assert first is again
        assert first.group_id == "login:a"
        assert first.is_initialized

    async def test_lru_is_bounded_and_evicts_the_oldest(self):
        base = get_limiter("login")
        with patch.object(MemoryRateLimiter, "MAX_KEYED_LIMITERS", 3):
            first = await get_keyed_limiter("login", "0")
            for i in range(1, 4):
                await get_keyed_limiter("login", str(i))
            assert len(base._keyed) == 3

            # "0" was evicted: a new instance with a fresh window
            assert await get_keyed_limiter("login", "0") is not first

            # recently used entries survive
            kept = await get_keyed_limiter("login", "3")
            await get_keyed_limiter("login", "4")
            assert await get_keyed_limiter("login", "3") is kept

    async def test_clone_inherits_configuration(self):
        limiter = await get_keyed_limiter("login", "a")

        assert limiter.limit == 2
        assert limiter.window_seconds == 60
        assert limiter.rate_per_second is None
        assert limiter.fail_closed is False


# =============================================================================
# AC5: byte-identical keys
# =============================================================================


class TestKeyIsByteIdenticalToLegacyClone:
    """The legacy clone passed ``"{base}:{identifier}"`` to the engine constructor."""

    IDENTIFIERS = ["1.2.3.4", "user@example.com", "hash:abc123", "trailing ", "a:b:c", "x" * 200]

    async def test_redis_token_bucket(self, fake_redis):
        _register(_redis_limiter(key_prefix="pfx"))
        for identifier in self.IDENTIFIERS:
            legacy = RedisRateLimiter(
                url="redis://fake:6379/0",
                group_id=f"login:{identifier}",
                rate_per_second=100.0,
                key_prefix="pfx",
            )
            view = await get_keyed_limiter("login", identifier)

            assert view.group_id == legacy.group_id
            assert (view._rate_key, view._window_key, view._concurrent_key) == (
                legacy._rate_key,
                legacy._window_key,
                legacy._concurrent_key,
            )

    async def test_redis_sliding_window(self, fake_redis):
        _register(_sliding_limiter(key_prefix="pfx"))
        for identifier in self.IDENTIFIERS:
            legacy = RedisSlidingWindowRateLimiter(
                url="redis://fake:6379/0",
                group_id=f"login:{identifier}",
                limit=5,
                window_seconds=60,
                key_prefix="pfx",
            )
            view = await get_keyed_limiter("login", identifier)

            assert view._key == legacy._key

    @pytest.mark.skipif(PostgresRateLimiter is None, reason="asyncpg not installed")
    async def test_postgres(self, fake_postgres):
        _register(_postgres_limiter())
        for identifier in self.IDENTIFIERS:
            legacy = _postgres_limiter(group_id=f"login:{identifier}")
            view = await get_keyed_limiter("login", identifier)

            assert view.group_id == legacy.group_id

    async def test_the_key_is_prefix_limiter_identifier_window(self, fake_redis):
        _register(_sliding_limiter(key_prefix="rate_limit"))

        view = await get_keyed_limiter("login", "1.2.3.4")

        assert view._key == "rate_limit:login:1.2.3.4:window"


# =============================================================================
# The view is a faithful copy that does not share mutable bookkeeping
# =============================================================================


class TestView:
    async def test_metrics_are_per_view(self, fake_redis):
        _register(_redis_limiter())
        a = await get_keyed_limiter("login", "a")
        b = await get_keyed_limiter("login", "b")

        await a.try_acquire(timeout=0)

        assert a.get_metrics().total_acquisitions == 1
        assert b.get_metrics().total_acquisitions == 0
        assert get_limiter("login").get_metrics().total_acquisitions == 0

    async def test_view_inherits_configuration(self, fake_redis):
        _register(_redis_limiter(rate_per_second=7.0, max_concurrent=3, default_timeout=2.0))

        view = await get_keyed_limiter("login", "a")

        assert view.rate_per_second == 7.0
        assert view.max_concurrent == 3
        assert view.default_timeout == 2.0
        assert type(view) is RedisRateLimiter

    async def test_a_view_of_a_view_resolves_to_the_same_backend(self, fake_redis):
        _register(_redis_limiter())
        view = await get_keyed_limiter("login", "a")

        nested = view.for_key("b")

        assert nested.group_id == "login:a:b"
        assert nested._pool is get_limiter("login")._pool

    async def test_view_disconnect_never_closes_the_shared_backend(self, fake_redis):
        _register(_redis_limiter())
        view = await get_keyed_limiter("login", "a")

        await view.disconnect()

        assert FakePool.disconnected == 0
        other = await get_keyed_limiter("login", "b")
        assert await other.try_acquire(timeout=0) is True

    async def test_view_created_before_the_root_was_initialized_adopts_the_backend(
        self, fake_redis
    ):
        root = _redis_limiter()
        _register(root)
        early = root.for_key("a")  # root not initialized yet
        assert early.is_initialized is False

        await early.initialize()

        assert early.is_initialized is True
        assert root.is_initialized is True
        assert FakePool.created == 1
        assert await early.try_acquire(timeout=0) is True

    async def test_root_disconnect_still_closes_the_pool(self, fake_redis):
        root = _redis_limiter()
        _register(root)
        await get_keyed_limiter("login", "a")

        await root.disconnect()

        assert FakePool.disconnected == 1


# =============================================================================
# AC7: initialization is single-flight and outage-safe
# =============================================================================


class TestInitialization:
    async def test_concurrent_first_use_opens_one_pool(self, fake_redis):
        _register(_redis_limiter())

        views = await asyncio.gather(*(get_keyed_limiter("login", f"id-{i}") for i in range(50)))

        assert FakePool.created == 1
        assert all(v.is_initialized for v in views)

    async def test_an_outage_is_retried_at_most_once_per_interval(self, fake_redis):
        _register(_redis_limiter())
        FakeRedis.ping_fails = True
        now = [1000.0]

        with patch.object(keyed, "_monotonic", lambda: now[0]):
            for i in range(20):
                view = await get_keyed_limiter("login", f"id-{i}")
                # fail-open: a degraded view lets the request through
                assert await view.try_acquire(timeout=0) is True
            attempts_during_interval = FakePool.created

            now[0] += keyed.REINITIALIZE_AFTER_SECONDS + 0.1
            await get_keyed_limiter("login", "later")

        assert attempts_during_interval == 1
        assert FakePool.created == 2

    async def test_recovery_after_an_outage(self, fake_redis):
        _register(_redis_limiter())
        FakeRedis.ping_fails = True
        now = [1000.0]

        with patch.object(keyed, "_monotonic", lambda: now[0]):
            await get_keyed_limiter("login", "a")
            FakeRedis.ping_fails = False
            now[0] += keyed.REINITIALIZE_AFTER_SECONDS + 0.1
            view = await get_keyed_limiter("login", "b")

        assert get_limiter("login").is_initialized
        assert view.is_initialized

    async def test_fail_closed_raises_and_keeps_raising_without_hammering_the_backend(
        self, fake_redis
    ):
        _register(_redis_limiter(fail_closed=True))
        FakeRedis.ping_fails = True
        now = [1000.0]

        with patch.object(keyed, "_monotonic", lambda: now[0]):
            for i in range(10):
                with pytest.raises(RateLimiterAcquisitionError):
                    await get_keyed_limiter("login", f"id-{i}")

        assert FakePool.created == 1


# =============================================================================
# AC10, AC11: contract of the registry-level functions
# =============================================================================


class TestContract:
    async def test_unknown_limiter(self):
        with pytest.raises(LimiterNotFoundError):
            await get_keyed_limiter("nope", "a")

    async def test_unsupported_engine_raises_before_any_connection(self, fake_redis):
        class CustomEngine(MemoryRateLimiter):
            for_key = RateLimiter.for_key  # an engine that did not opt in

        _register(CustomEngine(group_id="login", rate_per_second=1.0))

        with pytest.raises(UnsupportedLimiterEngineError) as raised:
            await get_keyed_limiter("login", "a")

        assert "CustomEngine" in str(raised.value)
        assert isinstance(raised.value, RateLimiterError)
        assert isinstance(raised.value, TypeError)
        assert get_limiter("login").is_initialized is False

    async def test_base_class_default_is_unsupported(self):
        with pytest.raises(UnsupportedLimiterEngineError):
            RateLimiter.for_key(MemoryRateLimiter("x", rate_per_second=1.0), "a")

    async def test_get_or_clone_falls_back_to_the_legacy_path_for_unsupported_engines(self):
        class CustomEngine(MemoryRateLimiter):
            for_key = RateLimiter.for_key

        configure_store("mem", strategy="memory")
        configure_limiter("login", store_id="mem", rate_per_second=1.0)
        _registry._limiters["login"] = (
            *_registry.get_limiter_config("login")[:4],
            CustomEngine(group_id="login", rate_per_second=1.0),
            *_registry.get_limiter_config("login")[5:],
        )

        limiter = await get_or_clone_limiter("login", "a")

        assert limiter.group_id == "login:a"
        assert limiter.is_initialized
        assert _registry.has_limiter("login:a")  # registered, as before

    async def test_get_or_clone_honors_a_registered_override(self):
        configure_store("mem", strategy="memory")
        configure_limiter("api", store_id="mem", rate_per_second=1.0)
        clone_limiter("api", "api:vip", rate_per_second=50.0)

        limiter = await get_or_clone_limiter("api", "vip")

        assert limiter.rate_per_second == 50.0
        assert limiter is get_limiter("api:vip")
        assert (await get_or_clone_limiter("api", "other")).rate_per_second == 1.0

    async def test_get_keyed_limiter_ignores_registered_ids(self):
        configure_store("mem", strategy="memory")
        configure_limiter("api", store_id="mem", rate_per_second=1.0)
        clone_limiter("api", "api:vip", rate_per_second=50.0)

        assert (await get_keyed_limiter("api", "vip")).rate_per_second == 1.0

    async def test_get_or_clone_unknown_base(self):
        with pytest.raises(LimiterNotFoundError):
            await get_or_clone_limiter("nope", "a")

    def test_public_names(self):
        assert ratesync.get_keyed_limiter is keyed.get_keyed_limiter
        assert ratesync.UnsupportedLimiterEngineError is UnsupportedLimiterEngineError


# =============================================================================
# AC12: the library's own integrations no longer register a limiter per identifier
# =============================================================================


class TestIntegrationsDoNotRegisterPerIdentifierLimiters:
    @pytest.fixture(autouse=True)
    def base(self, fake_redis):
        _register(_sliding_limiter(limit=3, window_seconds=60), "api")

    async def test_rate_limited_template(self):
        from ratesync import rate_limited

        @rate_limited("api:{user_id}", timeout=0, mode="try")
        async def call(user_id: str) -> str:
            return user_id

        before = set(_registry._limiters)
        for i in range(500):
            assert await call(f"user-{i}") == f"user-{i}"

        assert set(_registry._limiters) == before
        assert FakePool.created == 1

    async def test_rate_limited_template_unknown_base(self):
        from ratesync import rate_limited

        @rate_limited("nope:{user_id}")
        async def call(user_id: str) -> str:
            return user_id

        with pytest.raises(LimiterNotFoundError, match="Base limiter 'nope' not found"):
            await call("a")

    async def test_dependency(self):
        from ratesync.contrib.fastapi.dependencies import RateLimitDependency

        dependency = RateLimitDependency("api", identifier_extractor=lambda request: request.k)
        before = set(_registry._limiters)

        for i in range(500):
            result = await dependency._check_limit(f"client-{i}")
            assert result.allowed is True

        assert set(_registry._limiters) == before
        assert FakePool.created == 1

    async def test_middleware(self):
        from ratesync.contrib.fastapi.middleware import RateLimitMiddleware

        async def app(scope, receive, send):  # pragma: no cover - never called
            raise AssertionError

        middleware = RateLimitMiddleware(app, limiter_id="api")
        before = set(_registry._limiters)

        for i in range(500):
            assert (await middleware._check_limit(f"client-{i}")).allowed is True

        assert set(_registry._limiters) == before
        assert FakePool.created == 1

    async def test_composite(self):
        from ratesync import CompositeRateLimiter

        composite = CompositeRateLimiter(limiters={"by_user": "api"})
        before = set(_registry._limiters)

        for i in range(200):
            result = await composite.check(identifiers={"by_user": f"user-{i}"})
            assert result.allowed is True

        assert set(_registry._limiters) == before
        assert FakePool.created == 1

    async def test_check_limiter_resolves_a_suffixed_id_without_registering_it(self):
        from ratesync import check_limiter

        before = set(_registry._limiters)

        result = await check_limiter("api:alice")

        assert result.allowed is True
        assert set(_registry._limiters) == before


class TestMemoryRemainingFromTheBackend:
    async def test_sliding_window_remaining_counts_down(self):
        from ratesync.contrib.fastapi.dependencies import RateLimitDependency

        configure_store("mem", strategy="memory")
        configure_limiter(
            "api", store_id="mem", algorithm="sliding_window", limit=3, window_seconds=60
        )
        dependency = RateLimitDependency("api")

        remaining = [(await dependency._check_limit("a")).remaining for _ in range(4)]

        assert remaining == [2, 1, 0, 0]
