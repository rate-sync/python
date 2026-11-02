"""How many requests a limiter has left, for response headers."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def remaining_after(limiter: Any, allowed: bool, limit: int) -> int:
    """Requests left in the window after an acquire attempt, for ``X-RateLimit-Remaining``.

    A per-key limiter (``get_or_clone_limiter``) can be a short-lived object, so a counter kept on
    the object (``get_metrics().total_acquisitions``) says nothing about the window. For a sliding
    window the backend knows the truth, so it is read with ``get_state()``. A token bucket has no
    window, so the previous best-effort estimate is kept.

    Args:
        limiter: The limiter that was just acquired from.
        allowed: Whether the acquire succeeded.
        limit: The limit advertised in the headers.

    Returns:
        The remaining count, never negative; 0 when the request was refused.
    """
    if not allowed:
        return 0

    if getattr(limiter, "limit", None) is not None and getattr(limiter, "window_seconds", None):
        try:
            state = await limiter.get_state()
            return max(0, min(int(state.remaining), limit)) if limit > 0 else int(state.remaining)
        except (NotImplementedError, AttributeError):
            pass
        except Exception as exc:  # backend trouble must not break the request path
            logger.warning("Could not read limiter state for headers: %s", exc)

    metrics = limiter.get_metrics()
    return max(0, limit - (metrics.total_acquisitions % limit) if limit > 0 else 999)
