"""FastAPI application factory for the L2 orchestrator.

Mirrors the L1 ``server/app.py`` pattern: a lifespan builds the long-lived
objects and tears them down on shutdown. ``app.state`` holds

- ``config``: the ``[orchestrator]`` settings,
- ``registry``: the cell address book,
- ``client``: the shared httpx client,
- ``engine``: the run state machine.

Exception handlers translate the engine's errors into the L1-compatible
``ErrorResponse`` envelope so one client parser covers both levels.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from orchestrator import __version__
from orchestrator.client import CellCallError, CellClient
from orchestrator.engine import (
    Engine,
    RunConflictError,
    RunNotFoundError,
    RunStateError,
    ScenarioInvalid,
)
from orchestrator.registry import (
    ConfigError,
    OrchestratorConfig,
    Registry,
    UnknownCellError,
    load_config,
)

HTTP_BAD_REQUEST = 400
HTTP_NOT_FOUND = 404
HTTP_CONFLICT = 409
HTTP_INTERNAL = 500
HTTP_BAD_GATEWAY = 502

DESCRIPTION = (
    "L2 Orchestrator — runs scenario files across the InnoCORESDL L1 "
    "cell servers over HTTP /v1 only. One process, one active run. "
    "POST /v1/runs/{id}/abort ends the run and broadcasts POST /v1/stop, "
    "but that broadcast cannot preempt motion already in flight "
    "(docs/L1_AUDIT.md GAP-9) -- the physical e-stop is the only stop."
)

TAGS = [
    {
        "name": "Discovery",
        "description": (
            "Read-only: orchestrator health and the cell registry. Never "
            "moves a device."
        ),
    },
    {
        "name": "Scenarios",
        "description": (
            "Dry-run validation against each cell's OpenAPI. Sends no "
            "state-changing request."
        ),
    },
    {
        "name": "Runs",
        "description": (
            "Create, inspect, pause, resume and abort scenario runs."
        ),
    },
]


def _error(
    status: int,
    exc: Exception,
    *,
    issues: list[dict[str, object]] | None = None,
    run_id: str | None = None,
) -> JSONResponse:
    body: dict[str, object] = {
        "error": type(exc).__name__,
        "code": None,
        "command": None,
        "message": str(exc),
    }
    if issues is not None:
        body["issues"] = issues
    if run_id is not None:
        body["run_id"] = run_id
    return JSONResponse(status_code=status, content=body)


def register_exception_handlers(app: FastAPI) -> None:
    """Map engine/registry errors onto the shared error envelope."""

    @app.exception_handler(ScenarioInvalid)
    async def _invalid(_r: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, ScenarioInvalid)
        return _error(
            HTTP_BAD_REQUEST,
            exc,
            issues=[i.as_dict() for i in exc.issues],
            run_id=exc.run_id,
        )

    @app.exception_handler(RunNotFoundError)
    async def _missing(_r: Request, exc: Exception) -> JSONResponse:
        return _error(HTTP_NOT_FOUND, exc)

    @app.exception_handler(RunConflictError)
    async def _conflict(_r: Request, exc: Exception) -> JSONResponse:
        return _error(HTTP_CONFLICT, exc)

    @app.exception_handler(RunStateError)
    async def _state(_r: Request, exc: Exception) -> JSONResponse:
        return _error(HTTP_CONFLICT, exc)

    @app.exception_handler(UnknownCellError)
    async def _cell(_r: Request, exc: Exception) -> JSONResponse:
        return _error(HTTP_BAD_REQUEST, exc)

    @app.exception_handler(CellCallError)
    async def _downstream(_r: Request, exc: Exception) -> JSONResponse:
        # A cell failed, not the orchestrator -- 502, and the cell's own
        # message is preserved in the envelope.
        return _error(HTTP_BAD_GATEWAY, exc)

    @app.exception_handler(ValueError)
    async def _value(_r: Request, exc: Exception) -> JSONResponse:
        return _error(HTTP_BAD_REQUEST, exc)

    @app.exception_handler(ConfigError)
    async def _config(_r: Request, exc: Exception) -> JSONResponse:
        return _error(HTTP_INTERNAL, exc)


def create_app(
    config_path: Path | None = None,
    *,
    config: OrchestratorConfig | None = None,
    registry: Registry | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    repo_root: Path | None = None,
) -> FastAPI:
    """Build the orchestrator app.

    Args:
        config_path: ``config.toml`` to load. Ignored when ``config`` and
            ``registry`` are passed directly (tests do that).
        config: Pre-built settings.
        registry: Pre-built cell address book.
        transport: httpx transport override — tests pass a
            ``MockTransport`` here instead of running mock cell servers.
        repo_root: Where ``git rev-parse HEAD`` runs for the runlog meta.

    Raises:
        ConfigError: Neither a usable config path nor explicit objects.
    """
    if config is None or registry is None:
        if config_path is None:
            raise ConfigError("create_app needs config_path or config+registry")
        config, registry = load_config(config_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = CellClient(
            registry,
            transport=transport,
            connect_timeout_s=config.connect_timeout_s,
            default_timeout_s=config.step_timeout_s,
        )
        app.state.config = config
        app.state.registry = registry
        app.state.client = client
        app.state.engine = Engine(config, registry, client, repo_root=repo_root)
        try:
            yield
        finally:
            await client.aclose()

    app = FastAPI(
        title="innocoresdl-orchestrator",
        version=__version__,
        description=DESCRIPTION,
        openapi_tags=TAGS,
        lifespan=lifespan,
    )
    # Imported here so the module stays importable without the routes'
    # transitive deps during config-only use (e.g. the validate CLI).
    from orchestrator.routes import router

    app.include_router(router)
    register_exception_handlers(app)
    return app
