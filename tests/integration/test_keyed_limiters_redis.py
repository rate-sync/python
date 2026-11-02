"""Integration tests for per-key limiters against a real Redis server.

Requires Redis (``REDIS_URL``, default ``redis://localhost:6379/0``). They check what the
pool-counting unit tests cannot: the connections the SERVER sees stay flat as identifiers
multiply, and a per-key limiter consumes the same Redis key the legacy registered clone did.

Spec: docs/superpowers/specs/2026-11-02-per-key-limiters-without-connection-growth-design.md
"""

import os
import uuid

import pytest

from ratesync import (
    clone_limiter,
    configure_limiter,
    configure_store,
    get_keyed_limiter,
    get_limiter,
    get_or_clone_limiter,
)
from ratesync import keyed
from ratesync.registry import _registry

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

try:
    from redis import asyncio as redis_asyncio

    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

pytestmark = [
    pytest.mark.integration,
    pytest.mark.redis,
    pytest.mark.skipif(not REDIS_AVAILABLE, reason="Redis library not installed"),
]


@pytest.fixture
async def redis_client():
    client = redis_asyncio.from_url(REDIS_URL)
    try:
        await client.ping()
    except Exception:
        await client.aclose()
        pytest.skip("Redis not available")
    yield client
    await client.aclose()


@pytest.fixture
async def prefix(redis_client):
    value = f"keyed_{uuid.uuid4().hex[:8]}"
    saved = (dict(_registry._limiters), dict(_registry._backends))
    _registry._limiters.clear()
    _registry._backends.clear()
    yield value
    keys = [key async for key in redis_client.scan_iter(match=f"{value}:*")]
    if keys:
        await redis_client.delete(*keys)
    keyed.reset_keyed_limiters()
    _registry._limiters.clear()
    _registry._backends.clear()
    _registry._limiters.update(saved[0])
    _registry._backends.update(saved[1])


async def _connected_clients(client) -> int:
    return int((await client.info("clients"))["connected_clients"])


@pytest.mark.parametrize("algorithm", ["sliding_window", "token_bucket"])
async def test_server_side_connections_stay_flat(redis_client, prefix, algorithm):
    configure_store("redis", strategy="redis", url=REDIS_URL, key_prefix=prefix)
    if algorithm == "sliding_window":
        configure_limiter(
            "login", store_id="redis", algorithm="sliding_window", limit=5, window_seconds=60
        )
    else:
        configure_limiter("login", store_id="redis", rate_per_second=1000.0)

    baseline = await _connected_clients(redis_client)
    for i in range(200):
        limiter = await get_keyed_limiter("login", f"id-{i}")
        await limiter.try_acquire(timeout=0)
    after_200 = await _connected_clients(redis_client)

    for i in range(200, 3000):
        limiter = await get_or_clone_limiter("login", f"id-{i}")
        await limiter.try_acquire(timeout=0)
    after_3000 = await _connected_clients(redis_client)

    # One pool for the definition; the number of connections it holds does not depend on how many
    # identifiers passed through. (The legacy path held one or more per identifier.)
    assert after_3000 == after_200
    assert after_200 - baseline <= 10

    await get_limiter("login").disconnect()


async def test_a_view_and_a_legacy_clone_consume_the_same_redis_key(redis_client, prefix):
    configure_store("redis", strategy="redis", url=REDIS_URL, key_prefix=prefix)
    configure_limiter(
        "login", store_id="redis", algorithm="sliding_window", limit=2, window_seconds=60
    )

    view = await get_keyed_limiter("login", "1.2.3.4")
    assert await view.try_acquire(timeout=0) is True

    clone_limiter("login", "login:1.2.3.4")  # what get_or_clone_limiter registered before 0.5.0
    legacy = get_limiter("login:1.2.3.4")
    await legacy.initialize()
    assert await legacy.try_acquire(timeout=0) is True

    # Both consumed the same window: the budget of 2 is now spent for either.
    assert await view.try_acquire(timeout=0) is False
    assert await legacy.try_acquire(timeout=0) is False
    assert await redis_client.exists(f"{prefix}:login:1.2.3.4:window") == 1

    await legacy.disconnect()
    await get_limiter("login").disconnect()


async def test_each_identifier_has_its_own_budget(redis_client, prefix):
    configure_store("redis", strategy="redis", url=REDIS_URL, key_prefix=prefix)
    configure_limiter(
        "login", store_id="redis", algorithm="sliding_window", limit=1, window_seconds=60
    )

    a = await get_keyed_limiter("login", "a")
    b = await get_keyed_limiter("login", "b")

    assert await a.try_acquire(timeout=0) is True
    assert await a.try_acquire(timeout=0) is False
    assert await b.try_acquire(timeout=0) is True
    state = await (await get_keyed_limiter("login", "a")).get_state()
    assert state.remaining == 0

    await get_limiter("login").disconnect()


async def test_token_bucket_key_is_unchanged(redis_client, prefix):
    configure_store("redis", strategy="redis", url=REDIS_URL, key_prefix=prefix)
    configure_limiter("api", store_id="redis", rate_per_second=1.0)

    view = await get_keyed_limiter("api", "alice")
    assert await view.try_acquire(timeout=0) is True
    assert await view.try_acquire(timeout=0) is False

    assert await redis_client.exists(f"{prefix}:api:alice:rate") == 1
    await get_limiter("api").disconnect()
