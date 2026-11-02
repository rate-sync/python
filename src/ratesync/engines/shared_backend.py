"""Per-key views that share one backend connection pool.

Pooled engines (Redis, PostgreSQL) derive every storage key from ``group_id`` and own a single
connection pool. A *view* is a shallow copy of the engine whose ``group_id`` names one key: it
shares the pool (and the Redis client and registered scripts) with the engine it was made from,
so the number of connections does not depend on the number of keys.

See ``docs/superpowers/specs/2026-11-02-per-key-limiters-without-connection-growth-design.md``.
"""

from __future__ import annotations

import copy
from typing import Any, ClassVar, Self

from ratesync.core import RateLimiterMetrics


class SharedBackendKeyMixin:
    """``for_key`` for engines that own one connection pool and key everything on ``group_id``.

    Subclasses declare:

    * ``_BACKEND_ATTRS``: the instance attributes that hold the live backend (pool, client,
      scripts, readiness flags). A view copies them from its root on ``initialize()``.
    * ``_STRIP_GROUP_ID``: whether the constructor strips ``group_id``. The legacy per-identifier
      clone went through that constructor, so a view must strip too or an identifier with trailing
      whitespace would address a different key than it used to.
    """

    _BACKEND_ATTRS: ClassVar[tuple[str, ...]] = ()
    _STRIP_GROUP_ID: ClassVar[bool] = False

    #: The engine this view was made from, or None for a root engine.
    _root: Any = None

    # Provided by the engine.
    _group_id: str
    _metrics: RateLimiterMetrics

    @property
    def is_view(self) -> bool:
        """True for a per-key view, False for the engine registered under a limiter id."""
        return self._root is not None

    def for_key(self, identifier: str) -> Self:
        """Return a view of this engine that addresses ``"{group_id}:{identifier}"``.

        The view shares this engine's backend. It has its own metrics. Its ``disconnect()`` never
        closes the shared backend. A view of a view resolves to the same root.
        """
        root = self._root if self._root is not None else self
        group_id = f"{self._group_id}:{identifier}"
        if self._STRIP_GROUP_ID:
            group_id = group_id.strip()

        view = copy.copy(root)
        view._group_id = group_id
        view._root = root
        view._metrics = RateLimiterMetrics()
        return view

    async def _initialize_view(self) -> None:
        """Initialize a view: initialize the root, then adopt its backend references."""
        root = self._root
        await root.initialize()
        for name in self._BACKEND_ATTRS:
            setattr(self, name, getattr(root, name))

    def _detach_view(self) -> None:
        """Forget the shared backend without closing it (a view does not own it)."""
        for name in self._BACKEND_ATTRS:
            setattr(self, name, False if isinstance(getattr(self, name), bool) else None)
