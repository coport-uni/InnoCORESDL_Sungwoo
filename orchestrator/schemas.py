"""Pydantic request/response models for the orchestrator ``/v1`` API.

The error envelope mirrors the L1 ``server/schemas.py::ErrorResponse``
(``error`` / ``code`` / ``command`` / ``message``) so a client parses L1
and L2 failures the same way (spec §7); ``issues`` is the L2-only
addition carrying dry-run findings.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ErrorResponse(BaseModel):
    """L1-compatible error envelope."""

    error: str
    code: int | None = None
    command: str | None = None
    message: str
    issues: list[dict[str, Any]] | None = None
    run_id: str | None = None


class HealthResponse(BaseModel):
    ok: bool
    version: str
    cells: int
    active_run: str | None = None


class CellSummary(BaseModel):
    """One registry entry plus its live reachability."""

    name: str
    nuc: str
    base_url: str
    reachable: bool
    health: dict[str, Any] | None = None
    status: dict[str, Any] | None = None
    error: str | None = None


class CellListResponse(BaseModel):
    cells: list[CellSummary]


class ScenarioSource(BaseModel):
    """Either a repo-relative path or inline YAML — exactly one."""

    model_config = ConfigDict(extra="forbid")

    scenario_path: str | None = None
    scenario_yaml: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _one_source(self) -> ScenarioSource:
        given = [self.scenario_path, self.scenario_yaml]
        if sum(x is not None for x in given) != 1:
            raise ValueError(
                "provide exactly one of 'scenario_path' or 'scenario_yaml'"
            )
        return self


class ValidateResponse(BaseModel):
    ok: bool
    scenario: str | None = None
    issues: list[dict[str, Any]] = Field(default_factory=list)


class RunCreateRequest(ScenarioSource):
    """``POST /v1/runs`` body."""

    step_mode: bool = Field(
        default=False,
        description=(
            "Pause after every step so the operator can check the "
            "hardware before resuming (spec §9)."
        ),
    )


class RunCreatedResponse(BaseModel):
    run_id: str
    state: str
    pending_confirmation: str | None = None


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_step: str | None = Field(
        default=None,
        description="Rewind to this step id instead of continuing.",
    )


class RunSummary(BaseModel):
    run_id: str
    scenario: str
    state: str
    step_mode: bool
    created_utc: str
    finished_utc: str | None = None
    steps_done: int
    error: str | None = None


class RunDetail(RunSummary):
    """Full run state (spec §7 ``GET /v1/runs/{id}``)."""

    description: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    current_step: str | None = None
    step_index: int = 0
    total_steps: int = 0
    pending_confirmation: str | None = None
    vars: dict[str, Any] = Field(default_factory=dict)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    issues: list[dict[str, Any]] = Field(default_factory=list)
    stop_broadcast: dict[str, str] | None = None
    started_utc: str | None = None
    log_dir: str | None = None


class RunListResponse(BaseModel):
    runs: list[RunSummary]
    past_runs: list[dict[str, Any]] = Field(
        default_factory=list,
        description="meta.json of runs found under the runlog root.",
    )
