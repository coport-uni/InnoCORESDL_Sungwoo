"""Per-cell mutual exclusion (spec §6.5).

The lock unit is a **cell**, not a device: coordinating the devices inside
one cell is L1's job (its ``app.state.lock`` already serializes them).
L2 only guarantees that two steps never target the same cell at once.

This is advisory within one orchestrator process. It is not a substitute
for L1's own serialization — which is why the spec's A7 audit item asks
what a cell does when two clients call it anyway.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class CellLocks:
    """Lazily created :class:`asyncio.Lock` per cell name."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def get(self, cell: str) -> asyncio.Lock:
        """The lock for ``cell``, creating it on first use."""
        lock = self._locks.get(cell)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[cell] = lock
        return lock

    def held(self) -> tuple[str, ...]:
        """Cells whose lock is currently taken (for the run detail)."""
        return tuple(n for n, lock in self._locks.items() if lock.locked())

    @asynccontextmanager
    async def acquire(self, cell: str) -> AsyncIterator[None]:
        """Hold ``cell``'s lock for the duration of a step."""
        async with self.get(cell):
            yield
