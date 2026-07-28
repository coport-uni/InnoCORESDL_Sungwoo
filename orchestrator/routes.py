"""HTTP routes for the orchestrator ``/v1`` API (spec §7).

The engine lives on ``app.state.engine``. Handlers stay thin: they load
the scenario text, hand it to the engine, and serialize what comes back.
No handler talks to a cell directly except the read-only ``/v1/cells``
probe.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request

from orchestrator import __version__
from orchestrator.client import CellCallError
from orchestrator.engine import Engine
from orchestrator.registry import CellEntry
from orchestrator.schemas import (
    CellListResponse,
    CellSummary,
    HealthResponse,
    ResumeRequest,
    RunCreatedResponse,
    RunCreateRequest,
    RunDetail,
    RunListResponse,
    ScenarioSource,
    ValidateResponse,
)
from orchestrator.runlog import read_run_meta

router = APIRouter(prefix="/v1")

RUN_ACCEPTED = 202


def _engine(request: Request) -> Engine:
    return request.app.state.engine


def _scenario_text(source: ScenarioSource) -> str:
    """Inline YAML as-is, or the contents of ``scenario_path``.

    Raises:
        ValueError: The path is missing or is not a file.
    """
    if source.scenario_yaml is not None:
        return source.scenario_yaml
    assert source.scenario_path is not None
    path = Path(source.scenario_path)
    if not path.is_file():
        raise ValueError(f"scenario file not found: {path}")
    return path.read_text(encoding="utf-8")


# ── Discovery ──────────────────────────────────────────────────────────────


@router.get(
    "/health",
    response_model=HealthResponse,
    tags=["Discovery"],
    summary="Orchestrator liveness (never touches a cell)",
)
async def health(request: Request) -> HealthResponse:
    engine = _engine(request)
    active = engine.active_run()
    return HealthResponse(
        ok=True,
        version=__version__,
        cells=len(engine.registry),
        active_run=active.run_id if active else None,
    )


@router.get(
    "/cells",
    response_model=CellListResponse,
    tags=["Discovery"],
    summary="Registry plus each cell's health (and optionally status)",
)
async def cells(
    request: Request,
    with_status: bool = Query(
        default=False,
        description=(
            "Also call each cell's GET /v1/status. Read-only, but it "
            "takes the cell's device lock — off by default."
        ),
    ),
) -> CellListResponse:
    engine = _engine(request)

    async def _probe(entry: CellEntry) -> CellSummary:
        summary = CellSummary(
            name=entry.name,
            nuc=entry.nuc,
            base_url=entry.base_url,
            reachable=False,
        )
        try:
            summary.health = await engine.client.health(entry.name)
            summary.reachable = True
        except CellCallError as exc:
            summary.error = str(exc)
            return summary
        if with_status:
            try:
                summary.status = await engine.client.status(entry.name)
            except CellCallError as exc:
                summary.error = str(exc)
        return summary

    probed = await asyncio.gather(
        *(_probe(e) for e in engine.registry.cells.values())
    )
    return CellListResponse(cells=list(probed))


# ── Scenarios ──────────────────────────────────────────────────────────────


@router.post(
    "/scenarios/validate",
    response_model=ValidateResponse,
    tags=["Scenarios"],
    summary="Dry run: schema + registry + OpenAPI check, no device calls",
)
async def validate(request: Request, body: ScenarioSource) -> ValidateResponse:
    engine = _engine(request)
    text = _scenario_text(body)
    scenario, issues = await engine.validate(text, params=body.params)
    return ValidateResponse(
        ok=not issues,
        scenario=scenario.name if scenario else None,
        issues=[i.as_dict() for i in issues],
    )


# ── Runs ───────────────────────────────────────────────────────────────────


@router.post(
    "/runs",
    response_model=RunCreatedResponse,
    status_code=RUN_ACCEPTED,
    tags=["Runs"],
    summary="Validate and start a scenario in the background",
)
async def create_run(
    request: Request, body: RunCreateRequest
) -> RunCreatedResponse:
    engine = _engine(request)
    text = _scenario_text(body)
    run = await engine.create_run(
        text, step_mode=body.step_mode, params=body.params
    )
    return RunCreatedResponse(
        run_id=run.run_id,
        state=run.state.value,
        pending_confirmation=run.pending_confirmation,
    )


@router.get(
    "/runs",
    response_model=RunListResponse,
    tags=["Runs"],
    summary="Runs of this process, plus past runs found in the runlog",
)
async def list_runs(request: Request) -> RunListResponse:
    engine = _engine(request)
    live = [r.summary() for r in engine.runs()]
    live_ids = {r["run_id"] for r in live}
    past = [
        m
        for m in read_run_meta(engine.config.log_dir)
        if m.get("run_id") not in live_ids
    ]
    return RunListResponse(runs=live, past_runs=past)


@router.get(
    "/runs/{run_id}",
    response_model=RunDetail,
    tags=["Runs"],
    summary="State, current step, variables and per-step results",
)
async def get_run(request: Request, run_id: str) -> Any:
    return _engine(request).get(run_id).snapshot()


@router.post(
    "/runs/{run_id}/pause",
    response_model=RunDetail,
    tags=["Runs"],
    summary="Pause after the current step finishes (never mid-motion)",
)
async def pause_run(request: Request, run_id: str) -> Any:
    run = await _engine(request).pause(run_id)
    return run.snapshot()


@router.post(
    "/runs/{run_id}/resume",
    response_model=RunDetail,
    tags=["Runs"],
    summary="Resume a paused run, optionally from a given step",
)
async def resume_run(
    request: Request, run_id: str, body: ResumeRequest | None = None
) -> Any:
    run = await _engine(request).resume(
        run_id, from_step=body.from_step if body else None
    )
    return run.snapshot()


@router.post(
    "/runs/{run_id}/abort",
    response_model=RunDetail,
    tags=["Runs"],
    summary="End the run; broadcast /v1/stop (cannot preempt — GAP-9)",
)
async def abort_run(request: Request, run_id: str) -> Any:
    run = await _engine(request).abort(run_id)
    return run.snapshot()
