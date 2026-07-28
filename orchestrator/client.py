"""httpx wrapper for talking to the L1 cell servers.

One :class:`httpx.AsyncClient` is shared by every cell; the per-cell base
URL comes from the registry. Tests swap in an ``httpx.MockTransport`` here
instead of running a mock cell server (spec §9).

Deliberately **no automatic retry on state-changing calls**: re-sending a
``gantry/move`` after a timeout would move hardware a second time. Retries
are a scenario-level, author-chosen policy (``on_fail: retry``). Only the
read-only probes (``/openapi.json``, ``/v1/health``) retry internally.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from orchestrator.registry import (
    DEFAULT_CONNECT_TIMEOUT_S,
    DEFAULT_STEP_TIMEOUT_S,
    CellEntry,
    Registry,
)

#: Attempts for the two read-only probes (a cell server that is still
#: booting refuses the first connect).
PROBE_ATTEMPTS = 2
PROBE_BACKOFF_S = 0.5

OPENAPI_PATH = "openapi.json"
HEALTH_ACTION = "health"
STOP_ACTION = "stop"


class CellCallError(RuntimeError):
    """A cell call did not produce a 2xx JSON response.

    Attributes:
        kind: ``timeout`` | ``transport`` | ``http`` | ``decode``.
        cell: Registry name of the cell that was called.
        status: HTTP status code, or None when no response arrived.
        payload: Decoded error body (the L1 ``ErrorResponse`` envelope)
            when the cell answered, else None.
    """

    def __init__(
        self,
        kind: str,
        cell: str,
        message: str,
        *,
        status: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.kind = kind
        self.cell = cell
        self.status = status
        self.payload = payload
        super().__init__(message)

    def as_dict(self) -> dict[str, Any]:
        """Serializable form, used in the runlog and the run detail."""
        return {
            "kind": self.kind,
            "cell": self.cell,
            "status": self.status,
            "message": str(self),
            "payload": self.payload,
        }


class CellClient:
    """Calls ``/v1`` endpoints on the registry's cells."""

    def __init__(
        self,
        registry: Registry,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S,
        default_timeout_s: float = DEFAULT_STEP_TIMEOUT_S,
    ) -> None:
        self._registry = registry
        self._default_timeout_s = default_timeout_s
        self._http = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(default_timeout_s, connect=connect_timeout_s),
        )
        self._openapi: dict[str, dict[str, Any]] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> CellClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    # ── Core call ──────────────────────────────────────────────────────

    async def call(
        self,
        cell: str,
        action: str,
        *,
        method: str = "POST",
        body: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> Any:
        """Invoke one ``/v1`` endpoint and return its decoded JSON.

        Args:
            cell: Registry key of the target cell.
            action: Path below ``/v1``, e.g. ``linear/move``.
            method: ``POST`` (default) or ``GET``.
            body: JSON body for POST calls.
            timeout_s: Per-call timeout; falls back to the config default.

        Returns:
            The decoded response JSON.

        Raises:
            CellCallError: Timeout, transport failure, non-2xx status, or
                an undecodable body.
        """
        entry = self._registry.get(cell)
        url = entry.url(f"v1/{action.lstrip('/')}")
        timeout = self._default_timeout_s if timeout_s is None else timeout_s
        try:
            response = await self._http.request(
                method.upper(),
                url,
                json=body if method.upper() != "GET" else None,
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise CellCallError(
                "timeout",
                cell,
                f"{cell} {method} {action} timed out after {timeout}s",
            ) from exc
        except httpx.HTTPError as exc:
            raise CellCallError(
                "transport", cell, f"{cell} {method} {action}: {exc}"
            ) from exc
        return self._decode(cell, action, method, response)

    @staticmethod
    def _decode(
        cell: str, action: str, method: str, response: httpx.Response
    ) -> Any:
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if response.is_success:
            if payload is None:
                raise CellCallError(
                    "decode",
                    cell,
                    f"{cell} {method} {action}: response was not JSON",
                    status=response.status_code,
                )
            return payload
        message = f"{cell} {method} {action} -> HTTP {response.status_code}"
        if isinstance(payload, dict) and payload.get("message"):
            message = f"{message}: {payload['message']}"
        raise CellCallError(
            "http",
            cell,
            message,
            status=response.status_code,
            payload=payload if isinstance(payload, dict) else None,
        )

    # ── Read-only probes (the only calls a dry run may make) ───────────

    async def _probe(self, entry: CellEntry, path: str) -> Any:
        last: Exception | None = None
        for attempt in range(PROBE_ATTEMPTS):
            try:
                response = await self._http.get(entry.url(path))
                return self._decode(entry.name, path, "GET", response)
            except CellCallError as exc:
                last = exc
                if exc.kind == "http":
                    raise
            except httpx.HTTPError as exc:
                last = CellCallError(
                    "transport", entry.name, f"{entry.name} {path}: {exc}"
                )
            if attempt + 1 < PROBE_ATTEMPTS:
                await asyncio.sleep(PROBE_BACKOFF_S)
        assert last is not None
        raise last

    async def health(self, cell: str) -> Any:
        """``GET /v1/health`` — never touches a device."""
        return await self._probe(self._registry.get(cell), "v1/health")

    async def status(self, cell: str) -> Any:
        """``GET /v1/status`` — live readouts, no motion."""
        return await self._probe(self._registry.get(cell), "v1/status")

    async def openapi(self, cell: str, *, refresh: bool = False) -> Any:
        """``GET /openapi.json``, memoized per cell.

        The validator resolves action paths and body schemas from this, so
        adding an L1 route needs no L2 change (spec §8.1).
        """
        if refresh or cell not in self._openapi:
            document = await self._probe(self._registry.get(cell), OPENAPI_PATH)
            self._openapi[cell] = document
        return self._openapi[cell]

    # ── Safety ─────────────────────────────────────────────────────────

    async def stop_all(
        self, cells: tuple[str, ...] | None = None
    ) -> dict[str, str]:
        """Broadcast ``POST /v1/stop`` to every cell, in parallel.

        This is the *software* e-stop. It does not replace the physical
        e-stop button (spec §6.4).

        Returns:
            Cell name → ``"stopped"`` or the failure message.
        """
        names = cells if cells is not None else self._registry.names()

        async def _one(name: str) -> tuple[str, str]:
            try:
                await self.call(name, STOP_ACTION)
            except CellCallError as exc:
                return name, str(exc)
            except Exception as exc:  # unknown cell, programming error
                return name, repr(exc)
            return name, "stopped"

        results = await asyncio.gather(*(_one(n) for n in names))
        return dict(results)
