"""Unit tests for the PostgreSQL-based rate limiting engine.

Unlike ``tests/engines/test_postgres_specific.py`` (which requires a real
PostgreSQL instance), these tests mock only ``asyncpg.create_pool`` /
``pool.acquire`` so they run without any external infrastructure, while
keeping the real ``asyncpg.exceptions`` classes intact (the engine's except
clauses reference them directly, e.g. ``asyncpg_module.exceptions.
PostgresConnectionError``). This mirrors the mocking approach used in
``tests/test_redis_engine.py``.

Focused on the fail_closed contract: initialize()/acquire()/try_acquire()/
release()/get_state() must all honor fail_closed on backend failure,
consistent with each other.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ratesync.exceptions import RateLimiterAcquisitionError
from ratesync.schemas import PostgresEngineConfig

# Mark all tests in this module as requiring PostgreSQL
pytestmark = pytest.mark.postgres

# Try to import PostgresRateLimiter, skip entire module if not available
try:
    from ratesync.engines.postgres import PostgresRateLimiter

    POSTGRES_AVAILABLE = True
except ImportError:
    POSTGRES_AVAILABLE = False
    PostgresRateLimiter = None


@pytest.fixture(name="config")
def postgres_config_fixture():
    """Create a test PostgreSQL configuration."""
    return PostgresEngineConfig(
        url="postgresql://user:pass@localhost:5432/testdb",
        table_name="test_rate_limiter_state",
        pool_max_size=5,
    )


def _postgres_connection_error():
    """Return the real asyncpg PostgresConnectionError class (not a builtin)."""
    import asyncpg.exceptions as e

    return e.PostgresConnectionError("boom")


@pytest.mark.asyncio
class TestPostgresEngineFailClosed:
    """Test that initialize()/acquire()/release() honor fail_closed on backend failure.

    Regression coverage for a bug where fail_closed was only respected by
    try_acquire()/get_state(): a connection failure in initialize() always
    propagated the raw asyncpg exception (even with fail_closed=False,
    breaking the fail-open promise), and acquire()/release() never caught
    backend failures at all.
    """

    async def test_initialize_fail_closed_true_raises_acquisition_error(self, config):
        """initialize() with fail_closed=True wraps backend failure in RateLimiterAcquisitionError."""
        if not POSTGRES_AVAILABLE:
            pytest.skip("asyncpg library not installed")

        limiter = PostgresRateLimiter.from_config(config, "test", 1.0, fail_closed=True)

        with patch(
            "ratesync.engines.postgres.asyncpg_module.create_pool",
            new=AsyncMock(side_effect=_postgres_connection_error()),
        ):
            with pytest.raises(RateLimiterAcquisitionError):
                await limiter.initialize()

            assert limiter.is_initialized is False

    async def test_initialize_fail_closed_false_does_not_raise(self, config):
        """initialize() with fail_closed=False (default) swallows backend failure."""
        if not POSTGRES_AVAILABLE:
            pytest.skip("asyncpg library not installed")

        limiter = PostgresRateLimiter.from_config(config, "test", 1.0, fail_closed=False)

        with patch(
            "ratesync.engines.postgres.asyncpg_module.create_pool",
            new=AsyncMock(side_effect=_postgres_connection_error()),
        ):
            # Must NOT raise: fail-open on initialize() failure.
            await limiter.initialize()

            assert limiter.is_initialized is False

    async def test_degraded_mode_acquire_does_not_raise(self, config):
        """After a fail-open initialize() failure, acquire() allows the request."""
        if not POSTGRES_AVAILABLE:
            pytest.skip("asyncpg library not installed")

        limiter = PostgresRateLimiter.from_config(config, "test", 1.0, fail_closed=False)

        with patch(
            "ratesync.engines.postgres.asyncpg_module.create_pool",
            new=AsyncMock(side_effect=_postgres_connection_error()),
        ):
            await limiter.initialize()

        # Should return immediately, without touching PostgreSQL, without raising.
        await limiter.acquire()

    async def test_degraded_mode_try_acquire_returns_true(self, config):
        """After a fail-open initialize() failure, try_acquire() returns True."""
        if not POSTGRES_AVAILABLE:
            pytest.skip("asyncpg library not installed")

        limiter = PostgresRateLimiter.from_config(config, "test", 1.0, fail_closed=False)

        with patch(
            "ratesync.engines.postgres.asyncpg_module.create_pool",
            new=AsyncMock(side_effect=_postgres_connection_error()),
        ):
            await limiter.initialize()

        assert await limiter.try_acquire(timeout=0) is True

    async def test_degraded_mode_release_is_noop(self, config):
        """After a fail-open initialize() failure, release() is a no-op."""
        if not POSTGRES_AVAILABLE:
            pytest.skip("asyncpg library not installed")

        limiter = PostgresRateLimiter.from_config(
            config, "test", 1.0, max_concurrent=5, fail_closed=False
        )

        with patch(
            "ratesync.engines.postgres.asyncpg_module.create_pool",
            new=AsyncMock(side_effect=_postgres_connection_error()),
        ):
            await limiter.initialize()

        # Must not raise.
        await limiter.release()

    async def test_degraded_mode_get_state_returns_permissive(self, config):
        """After a fail-open initialize() failure, get_state() returns an allowed state."""
        if not POSTGRES_AVAILABLE:
            pytest.skip("asyncpg library not installed")

        limiter = PostgresRateLimiter.from_config(config, "test", 1.0, fail_closed=False)

        with patch(
            "ratesync.engines.postgres.asyncpg_module.create_pool",
            new=AsyncMock(side_effect=_postgres_connection_error()),
        ):
            await limiter.initialize()

        state = await limiter.get_state()
        assert state.allowed is True

    async def test_acquire_after_successful_init_fail_closed_true_raises(self, config):
        """acquire() with fail_closed=True raises RateLimiterAcquisitionError on backend failure."""
        if not POSTGRES_AVAILABLE:
            pytest.skip("asyncpg library not installed")

        limiter = PostgresRateLimiter.from_config(config, "test", 1.0, fail_closed=True)

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(side_effect=_postgres_connection_error())

        with patch(
            "ratesync.engines.postgres.asyncpg_module.create_pool",
            new=AsyncMock(return_value=mock_pool),
        ):
            await limiter.initialize()
            assert limiter.is_initialized is True

            with pytest.raises(RateLimiterAcquisitionError):
                await limiter.acquire()

    async def test_acquire_after_successful_init_fail_closed_false_allows(self, config):
        """acquire() with fail_closed=False (default) allows the request on backend failure."""
        if not POSTGRES_AVAILABLE:
            pytest.skip("asyncpg library not installed")

        limiter = PostgresRateLimiter.from_config(config, "test", 1.0, fail_closed=False)

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(side_effect=_postgres_connection_error())

        with patch(
            "ratesync.engines.postgres.asyncpg_module.create_pool",
            new=AsyncMock(return_value=mock_pool),
        ):
            await limiter.initialize()
            assert limiter.is_initialized is True

            # Must not raise: fail-open on backend failure after successful init.
            await limiter.acquire()

    async def test_release_after_successful_init_fail_closed_true_raises(self, config):
        """release() with fail_closed=True raises RateLimiterAcquisitionError on backend failure."""
        if not POSTGRES_AVAILABLE:
            pytest.skip("asyncpg library not installed")

        limiter = PostgresRateLimiter.from_config(
            config, "test", 1.0, max_concurrent=5, fail_closed=True
        )

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(side_effect=_postgres_connection_error())

        with patch(
            "ratesync.engines.postgres.asyncpg_module.create_pool",
            new=AsyncMock(return_value=mock_pool),
        ):
            await limiter.initialize()
            assert limiter.is_initialized is True

            with pytest.raises(RateLimiterAcquisitionError):
                await limiter.release()

    async def test_release_after_successful_init_fail_closed_false_allows(self, config):
        """release() with fail_closed=False (default) swallows backend failure."""
        if not POSTGRES_AVAILABLE:
            pytest.skip("asyncpg library not installed")

        limiter = PostgresRateLimiter.from_config(
            config, "test", 1.0, max_concurrent=5, fail_closed=False
        )

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(side_effect=_postgres_connection_error())

        with patch(
            "ratesync.engines.postgres.asyncpg_module.create_pool",
            new=AsyncMock(return_value=mock_pool),
        ):
            await limiter.initialize()
            assert limiter.is_initialized is True

            # Must not raise.
            await limiter.release()
