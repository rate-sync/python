"""Per-key limiters that cost no backend connection per key.

``get_keyed_limiter(limiter_id, identifier)`` is the call for any limit that is applied per caller
supplied value (user, client address, e-mail, tenant, hash of a token).

Why it exists
-------------

Creating one registered limiter per identifier (``clone_limiter`` per value, which is what
``get_or_clone_limiter`` did before 0.5.0) gives every identifier its own engine instance, and a
pooled engine (Redis, PostgreSQL) owns a connection pool per instance. Nothing closes those pools
and the registry never forgets a limiter, so open connections grew with the number of distinct
identifiers until the process ran out of file descriptors.

How it works
------------

* The limiter *definition* (the id declared in ``rate-sync.toml`` or ``configure_limiter``) has one
  engine instance and so one pool. It is initialized once, under a lock.
* The identifier is only a key. ``engine.for_key(identifier)`` returns a view that shares the
  engine's pool and addresses the group ``"{limiter_id}:{identifier}"``. The Redis key is the one
  the legacy clone used, so switching neither resets a window nor orphans data.
* Pooled engines cache nothing per identifier. The memory engine keeps its state in the instance,
  so it keeps a bounded LRU of per-key instances (``MemoryRateLimiter.MAX_KEYED_LIMITERS``).
* An engine with no per-key strategy raises :class:`UnsupportedLimiterEngineError` instead of
  silently opening one connection per key.
"""

from __future__ import annotations

import asyncio
import time
import weakref
from dataclasses import dataclass, field

from ratesync.core import RateLimiter
from ratesync.exceptions import RateLimiterAcquisitionError, UnsupportedLimiterEngineError

#: After a failed initialization (backend down), wait this long before trying again. Without it
#: every request would queue behind the lock for a full connect timeout during an outage.
REINITIALIZE_AFTER_SECONDS = 1.0


def _monotonic() -> float:
    return time.monotonic()


@dataclass
class _InitState:
    """Initialization bookkeeping of one engine on one event loop."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    next_attempt: float = float("-inf")
    error: RateLimiterAcquisitionError | None = None


# loop -> engine -> state. Weak on both sides: nothing here keeps a loop or an engine alive, and a
# new engine registered under the same limiter id never inherits the previous one's back-off.
_states: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, weakref.WeakKeyDictionary[RateLimiter, _InitState]
] = weakref.WeakKeyDictionary()


def supports_for_key(limiter: RateLimiter) -> bool:
    """Whether ``limiter``'s engine implements ``for_key`` (a per-key strategy)."""
    return type(limiter).for_key is not RateLimiter.for_key


def _state_of(engine: RateLimiter) -> _InitState:
    per_engine = _states.setdefault(asyncio.get_running_loop(), weakref.WeakKeyDictionary())
    state = per_engine.get(engine)
    if state is None:
        state = per_engine[engine] = _InitState()
    return state


async def _ensure_initialized(engine: RateLimiter) -> None:
    """Initialize the engine once; during an outage retry at most once per interval.

    With ``fail_closed=False`` a failed initialization leaves the engine degraded (fail-open) and
    this returns normally. With ``fail_closed=True`` the engine raises; the same error is raised
    again, without touching the backend, until the retry interval has passed.
    """
    if engine.is_initialized:
        return
    state = _state_of(engine)
    if _monotonic() < state.next_attempt:
        if state.error is not None:
            raise state.error
        return
    async with state.lock:
        if engine.is_initialized:
            return
        if _monotonic() < state.next_attempt:
            if state.error is not None:
                raise state.error
            return
        try:
            await engine.initialize()
        except RateLimiterAcquisitionError as exc:
            state.error = exc
            state.next_attempt = _monotonic() + REINITIALIZE_AFTER_SECONDS
            raise
        state.error = None
        if not engine.is_initialized:
            state.next_attempt = _monotonic() + REINITIALIZE_AFTER_SECONDS


async def get_keyed_limiter(limiter_id: str, identifier: str) -> RateLimiter:
    """The limiter for ``identifier`` under the limiter definition ``limiter_id``.

    Use it for any limit applied per caller-supplied value. The number of backend connections is
    constant (one pool per limiter definition) however many identifiers pass through, and nothing
    is registered or cached per identifier on pooled engines.

    The returned limiter has ``group_id == "{limiter_id}:{identifier}"`` and inherits the
    definition's configuration. For Redis and PostgreSQL its storage key is the one a registered
    clone named ``"{limiter_id}:{identifier}"`` would use, so adopting this function does not reset
    existing windows. It is ready to use: acquire from it directly.

    The returned object may be short-lived: do not keep it as the identifier's state. Its metrics
    (``get_metrics()``) cover that object only; use ``get_state()`` for the backend's truth.
    ``disconnect()`` on it does not close the shared connection.

    Unlike :func:`ratesync.get_or_clone_limiter` this does not look for a limiter that was
    registered explicitly under ``"{limiter_id}:{identifier}"``; it always keys the definition.

    Example:
        >>> limiter = await get_keyed_limiter("login", client_ip)
        >>> if not await limiter.try_acquire(timeout=0):
        ...     raise TooManyRequests()

    Args:
        limiter_id: A limiter declared in ``rate-sync.toml`` or with ``configure_limiter``.
        identifier: The per-caller key (a hash, an address, an organization id). Any string.

    Returns:
        A ready-to-use limiter for the identifier.

    Raises:
        LimiterNotFoundError: ``limiter_id`` is not configured.
        UnsupportedLimiterEngineError: The limiter's engine has no per-key strategy.
        RateLimiterAcquisitionError: The backend is down and the limiter is ``fail_closed``.
    """
    # Imported here: the registry imports this module for get_or_clone_limiter.
    from ratesync.registry import get_limiter

    engine = get_limiter(limiter_id)
    if not supports_for_key(engine):
        raise UnsupportedLimiterEngineError(type(engine).__name__, limiter_id)
    await _ensure_initialized(engine)
    return engine.for_key(identifier)


def reset_keyed_limiters() -> None:
    """Forget initialization back-off state. For tests; never needed by applications."""
    _states.clear()


__all__ = [
    "REINITIALIZE_AFTER_SECONDS",
    "get_keyed_limiter",
    "reset_keyed_limiters",
    "supports_for_key",
]
