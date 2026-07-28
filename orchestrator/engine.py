"""Scenario execution engine — the run state machine of spec §6.3–6.5.

One orchestrator process, one active run (spec §6.5). A run walks its
scenario top-level step by top-level step, taking the target cell's lock
around each HTTP call, appending to the runlog as it goes.

Safety properties this file is responsible for:

- ``pause`` stops **between** steps. An in-flight hardware call is always
  allowed to finish; the connection is never dropped mid-motion.
- ``abort`` broadcasts ``POST /v1/stop`` to every registered cell. That is
  the *software* e-stop and does not replace the physical one.
- The first motion-bearing step of a run waits for an operator
  confirmation (``confirm_first_motion``, on by default) — CLAUDE.md
  Folder-specific rules #3: motors never start unattended.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from orchestrator.client import CellCallError, CellClient
from orchestrator.locks import CellLocks
from orchestrator.registry import OrchestratorConfig, Registry
from orchestrator.runlog import RunLog, git_commit, new_run_id, utc_now
from orchestrator.scenario import (
    AssertSyntaxError,
    OnFail,
    PARAMS_ROOT,
    Scenario,
    ScenarioError,
    Step,
    ValidationIssue,
    VariableError,
    eval_assert,
    load_scenario_text,
    resolve,
    validate_scenario,
)


class RunState(str, Enum):
    """States of spec §6.4."""

    VALIDATING = "validating"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


TERMINAL_STATES = frozenset(
    {RunState.COMPLETED, RunState.FAILED, RunState.ABORTED}
)
ACTIVE_STATES = frozenset(
    {RunState.VALIDATING, RunState.READY, RunState.RUNNING, RunState.PAUSED}
)


class RunConflictError(RuntimeError):
    """Raised when a second run is submitted while one is still active."""


class RunNotFoundError(KeyError):
    """Raised when a run id is not known to this orchestrator."""

    def __str__(self) -> str:
        return f"unknown run_id: {self.args[0]}"


class RunStateError(RuntimeError):
    """Raised when pause/resume/abort does not apply in the current state."""


class AssertionFailure(RuntimeError):
    """Raised when an ``assert:`` step evaluates false."""


class ScenarioInvalid(ValueError):
    """Raised when a submitted scenario fails the dry run.

    Attributes:
        issues: The validator's findings, in scenario order.
        run_id: Id of the (failed) run recorded for this submission.
    """

    def __init__(
        self, issues: list[ValidationIssue], run_id: str | None = None
    ) -> None:
        self.issues = issues
        self.run_id = run_id
        super().__init__(
            "scenario did not validate: "
            + "; ".join(str(i) for i in issues[:_ISSUE_PREVIEW])
        )


#: How many issues are inlined into the exception message.
_ISSUE_PREVIEW = 3


@dataclass
class Run:
    """In-memory state of one scenario execution."""

    run_id: str
    scenario: Scenario
    scenario_text: str
    params: dict[str, Any]
    step_mode: bool
    state: RunState = RunState.VALIDATING
    index: int = 0
    current_step: str | None = None
    vars: dict[str, Any] = field(default_factory=dict)
    records: list[dict[str, Any]] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)
    error: str | None = None
    pending_confirmation: str | None = None
    motion_confirmed: bool = False
    stop_broadcast: dict[str, str] | None = None
    created_utc: str = field(default_factory=utc_now)
    started_utc: str | None = None
    finished_utc: str | None = None
    log: RunLog | None = None
    task: asyncio.Task[None] | None = None
    pause_requested: bool = False
    abort_requested: bool = False
    resume_event: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def context(self) -> dict[str, Any]:
        """Variable context for ``${...}`` resolution."""
        return {PARAMS_ROOT: self.params, **self.vars}

    def summary(self) -> dict[str, Any]:
        """Short form used by ``GET /v1/runs``."""
        return {
            "run_id": self.run_id,
            "scenario": self.scenario.name,
            "state": self.state.value,
            "step_mode": self.step_mode,
            "created_utc": self.created_utc,
            "finished_utc": self.finished_utc,
            "steps_done": len(self.records),
            "error": self.error,
        }

    def snapshot(self) -> dict[str, Any]:
        """Full form used by ``GET /v1/runs/{id}``."""
        return {
            **self.summary(),
            "description": self.scenario.description,
            "params": self.params,
            "current_step": self.current_step,
            "step_index": self.index,
            "total_steps": len(self.scenario.steps),
            "pending_confirmation": self.pending_confirmation,
            "vars": self.vars,
            "steps": self.records,
            "issues": [i.as_dict() for i in self.issues],
            "stop_broadcast": self.stop_broadcast,
            "started_utc": self.started_utc,
            "log_dir": str(self.log.dir) if self.log else None,
        }


class Engine:
    """Validates, starts and supervises runs."""

    def __init__(
        self,
        config: OrchestratorConfig,
        registry: Registry,
        client: CellClient,
        *,
        repo_root: Path | None = None,
    ) -> None:
        self._config = config
        self._registry = registry
        self._client = client
        self._repo_root = repo_root
        self._locks = CellLocks()
        self._runs: dict[str, Run] = {}
        self._active: str | None = None

    # ── Introspection ──────────────────────────────────────────────────

    @property
    def config(self) -> OrchestratorConfig:
        return self._config

    @property
    def registry(self) -> Registry:
        return self._registry

    @property
    def client(self) -> CellClient:
        return self._client

    def get(self, run_id: str) -> Run:
        """Look a run up, or raise :class:`RunNotFoundError`."""
        try:
            return self._runs[run_id]
        except KeyError:
            raise RunNotFoundError(run_id) from None

    def runs(self) -> list[Run]:
        """Runs of this process, newest first."""
        return sorted(
            self._runs.values(), key=lambda r: r.created_utc, reverse=True
        )

    def active_run(self) -> Run | None:
        """The run that is not finished yet, if any."""
        if self._active is None:
            return None
        run = self._runs.get(self._active)
        return run if run and run.state in ACTIVE_STATES else None

    # ── Validation ─────────────────────────────────────────────────────

    async def validate(
        self, text: str, *, params: dict[str, Any] | None = None
    ) -> tuple[Scenario | None, list[ValidationIssue]]:
        """Dry-run a scenario (spec §8.2). Touches no device.

        Returns:
            The parsed scenario (None when it did not even parse) and the
            list of issues; empty issues means it is runnable.
        """
        try:
            scenario = load_scenario_text(text)
        except ScenarioError as exc:
            return None, [ValidationIssue("schema", None, str(exc))]
        issues = await validate_scenario(
            scenario, self._registry, self._client, params=params
        )
        return scenario, issues

    # ── Run lifecycle ──────────────────────────────────────────────────

    async def create_run(
        self,
        text: str,
        *,
        step_mode: bool = False,
        params: dict[str, Any] | None = None,
        start: bool = True,
    ) -> Run:
        """Validate a scenario and start it in the background.

        Args:
            text: Scenario YAML.
            step_mode: Pause after every step for an operator check.
            params: Overrides merged over the scenario's own ``params``.
            start: Set False to create the run without launching it (the
                CLI drives it itself).

        Returns:
            The created run, already in ``running`` when ``start``.

        Raises:
            RunConflictError: Another run is still active.
            ScenarioInvalid: The dry run found problems; the run is
                recorded in ``failed`` so it stays visible in the list.
        """
        active = self.active_run()
        if active is not None:
            raise RunConflictError(
                f"run {active.run_id} is {active.state.value}; one "
                "orchestrator runs one scenario at a time"
            )
        scenario, issues = await self.validate(text, params=params)
        if scenario is None or issues:
            run_id = new_run_id(scenario.name if scenario else "invalid")
            if scenario is not None:
                run = self._register(run_id, scenario, text, params, step_mode)
                run.issues = issues
                run.state = RunState.FAILED
                run.finished_utc = utc_now()
                run.error = "scenario did not validate"
            raise ScenarioInvalid(issues, run_id if scenario else None)

        run = self._register(
            new_run_id(scenario.name), scenario, text, params, step_mode
        )
        run.state = RunState.READY
        run.log = RunLog(self._config.log_dir, run.run_id)
        run.log.write_scenario(text)
        run.log.write_meta(self._meta(run))
        self._active = run.run_id
        if start:
            run.task = asyncio.create_task(self._execute(run))
        return run

    def _register(
        self,
        run_id: str,
        scenario: Scenario,
        text: str,
        params: dict[str, Any] | None,
        step_mode: bool,
    ) -> Run:
        run = Run(
            run_id=run_id,
            scenario=scenario,
            scenario_text=text,
            params={**scenario.params, **(params or {})},
            step_mode=step_mode,
        )
        self._runs[run_id] = run
        return run

    def _meta(self, run: Run) -> dict[str, Any]:
        return {
            "run_id": run.run_id,
            "scenario": run.scenario.name,
            "step_mode": run.step_mode,
            "state": run.state.value,
            "params": run.params,
            "git_commit": git_commit(self._repo_root),
            "created_utc": run.created_utc,
            "started_utc": run.started_utc,
            "finished_utc": run.finished_utc,
            "config": {
                "cells": {
                    name: entry.base_url
                    for name, entry in self._registry.cells.items()
                },
                "confirm_first_motion": self._config.confirm_first_motion,
                "stop_on_failure": self._config.stop_on_failure,
                "step_timeout_s": self._config.step_timeout_s,
            },
        }

    async def wait(self, run_id: str) -> Run:
        """Await a run's completion (used by the CLI)."""
        run = self.get(run_id)
        if run.task is not None:
            await asyncio.shield(run.task)
        return run

    # ── Operator controls ──────────────────────────────────────────────

    async def pause(self, run_id: str) -> Run:
        """Request a pause; the current step still finishes (spec §6.4)."""
        run = self.get(run_id)
        if run.state is RunState.PAUSED:
            return run
        if run.state is not RunState.RUNNING:
            raise RunStateError(
                f"run {run_id} is {run.state.value}, cannot pause"
            )
        run.pause_requested = True
        return run

    async def resume(self, run_id: str, from_step: str | None = None) -> Run:
        """Release a paused run, optionally rewinding to ``from_step``."""
        run = self.get(run_id)
        if run.state is not RunState.PAUSED:
            raise RunStateError(
                f"run {run_id} is {run.state.value}, cannot resume"
            )
        if from_step is not None:
            try:
                run.index = run.scenario.index_of(from_step)
            except KeyError:
                raise RunStateError(
                    f"no step {from_step!r} in {run.scenario.name}"
                ) from None
        run.pause_requested = False
        # Flip the state here, not in the gate coroutine: a client that
        # polls right after resuming must not see a stale ``paused``.
        run.state = RunState.RUNNING
        run.resume_event.set()
        return run

    async def abort(self, run_id: str) -> Run:
        """Abort a run and broadcast the software e-stop to every cell.

        **This does not currently stop anything that is already moving.**
        L1 serves ``/v1/stop`` under the same lock the in-flight command
        holds, so the broadcast queues behind the motion it was meant to
        interrupt — measured at 4.2 s late on a 5 s move
        (``docs/L1_AUDIT.md`` GAP-9). On top of that,
        ``BalanceLinearCell.stop()`` is a no-op even when reached (GAP-1).

        Until both are fixed the physical e-stop is the only stop, on every
        cell. What abort *does* reliably do is end the run: no further step
        is dispatched.
        """
        run = self.get(run_id)
        run.abort_requested = True
        run.resume_event.set()
        run.stop_broadcast = await self._client.stop_all()
        if run.log is not None:
            run.log.append_step(
                {
                    "event": "abort",
                    "utc": utc_now(),
                    "stop_broadcast": run.stop_broadcast,
                }
            )
        if run.task is None and run.state not in TERMINAL_STATES:
            run.state = RunState.ABORTED
            run.finished_utc = utc_now()
            self._release(run)
        return run

    async def confirm(self, run_id: str) -> Run:
        """Operator acknowledgement of the first-motion gate."""
        return await self.resume(run_id)

    # ── Execution ──────────────────────────────────────────────────────

    async def _execute(self, run: Run) -> None:
        run.state = RunState.RUNNING
        run.started_utc = utc_now()
        steps = run.scenario.steps
        try:
            while run.index < len(steps):
                index = run.index
                step = steps[index]
                if not await self._gate(run, step):
                    break
                if run.index != index:
                    # resume(from_step=...) rewound us while paused;
                    # re-read the step instead of running the stale one.
                    continue
                if not await self._run_step(run, step):
                    break
                run.index += 1
                if run.step_mode and run.index < len(steps):
                    run.pause_requested = True
        except asyncio.CancelledError:
            run.abort_requested = True
            raise
        finally:
            await self._finish(run)

    async def _gate(self, run: Run, step: Step) -> bool:
        """Handle pause / step-mode / first-motion confirmation.

        Returns:
            False when the run should stop instead of running ``step``.
        """
        if run.abort_requested:
            return False
        needs_confirm = (
            self._config.confirm_first_motion
            and not run.motion_confirmed
            and self._is_gated(step)
        )
        if needs_confirm:
            run.pending_confirmation = step.label(run.index)
            run.pause_requested = True
        if not run.pause_requested:
            return True
        run.state = RunState.PAUSED
        run.resume_event.clear()
        await run.resume_event.wait()
        if run.abort_requested:
            return False
        if needs_confirm:
            run.motion_confirmed = True
        run.pending_confirmation = None
        run.pause_requested = False
        run.state = RunState.RUNNING
        return True

    def _is_gated(self, step: Step) -> bool:
        """True if the step acts on hardware — motion, heat, or power."""
        return any(
            child.action is not None
            and self._config.is_hazardous(child.action, child.method)
            for child in step.children()
        )

    async def _run_step(self, run: Run, step: Step) -> bool:
        """Run one top-level step. Returns False to stop the run."""
        run.current_step = step.label(run.index)
        if step.kind == "parallel":
            outcomes = await asyncio.gather(
                *(self._attempt(run, child) for child in step.children())
            )
            return all(outcomes)
        return await self._attempt(run, step)

    async def _attempt(self, run: Run, step: Step) -> bool:
        """Run one leaf step with its retry/on-fail policy.

        Returns:
            True to keep going (success, or failure with
            ``on_fail: continue``), False to stop the run.
        """
        defaults = run.scenario.defaults
        on_fail = step.on_fail or defaults.on_fail
        retries = defaults.retries if step.retries is None else step.retries
        attempts = 1 + retries if on_fail is OnFail.RETRY else 1
        record: dict[str, Any] = {}
        for attempt in range(1, attempts + 1):
            record = await self._execute_leaf(run, step, attempt, attempts)
            run.records.append(record)
            if run.log is not None:
                run.log.append_step(record)
            if record["ok"]:
                return True
            if attempt < attempts and not run.abort_requested:
                await asyncio.sleep(self._config.retry_delay_s)
        if on_fail is OnFail.CONTINUE:
            return True
        run.error = record.get("error", {}).get("message", "step failed")
        return False

    async def _execute_leaf(
        self, run: Run, step: Step, attempt: int, attempts: int
    ) -> dict[str, Any]:
        started = utc_now()
        clock = time.monotonic()
        record: dict[str, Any] = {
            "id": step.id,
            "kind": step.kind,
            "cell": step.cell,
            "action": step.action,
            "method": step.method if step.kind == "call" else None,
            "attempt": attempt,
            "attempts": attempts,
            "started_utc": started,
        }
        try:
            if step.kind == "assert":
                record["result"] = self._run_assert(run, step)
            else:
                record["result"] = await self._call_cell(run, step)
            record["ok"] = True
            record["error"] = None
        except (
            CellCallError,
            VariableError,
            AssertSyntaxError,
            AssertionFailure,
        ) as exc:
            record["ok"] = False
            record["result"] = None
            record["error"] = (
                exc.as_dict()
                if isinstance(exc, CellCallError)
                else {"kind": type(exc).__name__, "message": str(exc)}
            )
        record["finished_utc"] = utc_now()
        record["duration_s"] = round(time.monotonic() - clock, 3)
        return record

    def _run_assert(self, run: Run, step: Step) -> dict[str, Any]:
        assert step.assert_expr is not None
        expression = resolve(step.assert_expr, run.context)
        if not eval_assert(expression):
            raise AssertionFailure(f"assert failed: {expression}")
        return {"expression": expression, "passed": True}

    async def _call_cell(self, run: Run, step: Step) -> Any:
        assert step.cell is not None and step.action is not None
        body = resolve(step.body, run.context)
        timeout = (
            step.timeout_s
            if step.timeout_s is not None
            else run.scenario.defaults.timeout_s
        )
        async with self._locks.acquire(step.cell):
            result = await self._client.call(
                step.cell,
                step.action,
                method=step.method,
                body=body,
                timeout_s=timeout,
            )
        if step.save_as:
            run.vars[step.save_as] = result
        return result

    async def _finish(self, run: Run) -> None:
        if run.abort_requested:
            run.state = RunState.ABORTED
        elif run.error is not None:
            run.state = RunState.FAILED
        else:
            run.state = RunState.COMPLETED
        run.finished_utc = utc_now()
        run.current_step = None
        if (
            run.state is RunState.FAILED
            and self._config.stop_on_failure
            and run.stop_broadcast is None
        ):
            run.stop_broadcast = await self._client.stop_all()
        if run.log is not None:
            run.log.write_vars(run.vars)
            run.log.write_meta(
                {**self._meta(run), "stop_broadcast": run.stop_broadcast}
            )
        self._release(run)

    def _release(self, run: Run) -> None:
        if self._active == run.run_id:
            self._active = None
