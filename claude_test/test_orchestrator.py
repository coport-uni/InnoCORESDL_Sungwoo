"""Orchestrator engine + validator tests over httpx MockTransport.

Covers the acceptance criteria of spec section 11: M2 (the six validator
errors, demo scenario validates), M4 (the demo runs to completion and
leaves the four runlog files), M5 (the three on_fail policies, abort's
stop broadcast, resume --from-step).

No hardware and no cell server are involved -- ``FakeL1`` in conftest.py
answers over MockTransport.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from conftest import FakeL1, HOME_MM, REPO_ROOT, openapi_document
from orchestrator.app import create_app
from orchestrator.engine import (
    Engine,
    RunState,
    ScenarioInvalid,
    RunConflictError,
    TERMINAL_STATES,
)
from orchestrator.registry import OrchestratorConfig, Registry
from orchestrator.runlog import META_FILE, SCENARIO_FILE, STEPS_FILE, VARS_FILE
from orchestrator.scenario import (
    AssertSyntaxError,
    ScenarioError,
    check_body,
    eval_assert,
    load_scenario_text,
    operation,
    request_schema,
)

DEMO = REPO_ROOT / "scenarios" / "demo_linear_move.yaml"
WAIT_TIMEOUT_S = 5.0
POLL_S = 0.01


async def wait_for(check: Callable[[], bool]) -> None:
    """Spin until ``check`` holds, or fail the test."""
    waited = 0.0
    while not check():
        await asyncio.sleep(POLL_S)
        waited += POLL_S
        if waited > WAIT_TIMEOUT_S:
            raise AssertionError("condition not reached in time")


def scenario_yaml(steps: str, *, name: str = "t", extra: str = "") -> str:
    return f"name: {name}\n{extra}steps:\n{steps}"


# ── M2: validation ─────────────────────────────────────────────────────────


async def test_demo_scenario_validates(engine: Engine) -> None:
    scenario, issues = await engine.validate(DEMO.read_text(encoding="utf-8"))
    assert [str(i) for i in issues] == []
    assert scenario is not None
    assert scenario.name == "demo_linear_move"


BAD_SCENARIOS: list[tuple[str, str]] = [
    (
        "schema",
        "name: Not Snake Case\nsteps:\n"
        "  - id: a\n    cell: cell4\n    action: status\n    method: GET\n",
    ),
    (
        "unknown_cell",
        scenario_yaml(
            "  - id: a\n    cell: cell9\n    action: status\n    method: GET\n"
        ),
    ),
    (
        "unknown_action",
        scenario_yaml("  - id: a\n    cell: cell4\n    action: linear/fly\n"),
    ),
    (
        "body_mismatch",
        scenario_yaml(
            "  - id: a\n    cell: cell4\n    action: linear/move\n"
            "    body:\n      y: 10.0\n"
        ),
    ),
    (
        "var_order",
        scenario_yaml(
            "  - id: a\n    cell: cell4\n    action: linear/move\n"
            '    body:\n      y_mm: "${later.y_mm}"\n'
            "  - id: later\n    cell: cell4\n    action: linear/home\n"
            "    save_as: later\n"
        ),
    ),
    (
        "parallel_duplicate_cell",
        scenario_yaml(
            "  - parallel:\n"
            "      - id: a\n        cell: cell4\n        action: linear/home\n"
            "      - id: b\n        cell: cell4\n        action: balance/tare\n"
        ),
    ),
]


@pytest.mark.parametrize(
    ("code", "text"), BAD_SCENARIOS, ids=[c for c, _ in BAD_SCENARIOS]
)
async def test_validator_reports_each_error(
    engine: Engine, code: str, text: str
) -> None:
    _scenario, issues = await engine.validate(text)
    assert code in {i.code for i in issues}, [str(i) for i in issues]
    assert all(i.message for i in issues)


async def test_validator_flags_wrong_method(engine: Engine) -> None:
    _s, issues = await engine.validate(
        scenario_yaml(
            "  - id: a\n    cell: cell4\n    action: linear/move\n"
            "    method: GET\n"
        )
    )
    assert {i.code for i in issues} == {"unknown_action"}
    assert "not as GET" in issues[0].message


async def test_validate_makes_no_state_changing_call(
    engine: Engine, fake_l1: FakeL1
) -> None:
    await engine.validate(DEMO.read_text(encoding="utf-8"))
    assert {c[1] for c in fake_l1.calls} == {"GET"}
    assert {c[2] for c in fake_l1.calls} == {"/openapi.json"}


async def test_unreachable_cell_is_reported(
    engine: Engine, fake_l1: FakeL1
) -> None:
    fake_l1.timeout_next("cell4", "openapi.json", times=4)
    _s, issues = await engine.validate(
        scenario_yaml("  - id: a\n    cell: cell4\n    action: linear/home\n")
    )
    assert {i.code for i in issues} == {"cell_unreachable"}


def test_assert_rejects_non_comparisons() -> None:
    assert eval_assert("1.5 > 1.0") is True
    assert eval_assert("0.5 >= 1.0 - 0.1") is False
    # Booleans interpolate as Python literals; the JSON spellings also work,
    # because scenario authors write YAML, not Python.
    assert eval_assert("True == True") is True
    assert eval_assert("True == true") is True
    assert eval_assert("False == false") is True
    assert eval_assert("None == null") is True
    for hostile in (
        "__import__('os').system('id')",
        "open('x')",
        "a.b",
        "banana > 1",
    ):
        with pytest.raises(AssertSyntaxError):
            eval_assert(hostile)


# ── M4: execution + runlog ─────────────────────────────────────────────────


async def test_demo_run_completes_and_writes_runlog(
    engine: Engine, fake_l1: FakeL1
) -> None:
    run = await engine.create_run(DEMO.read_text(encoding="utf-8"))
    await engine.wait(run.run_id)

    assert run.state is RunState.COMPLETED
    assert run.error is None
    assert len(run.records) == len(run.scenario.steps)
    assert all(r["ok"] for r in run.records)
    # The rail actually went out and came back on the simulated cell.
    # The rail parks at the safe home, not on the 0 mm stop.
    assert fake_l1.position_mm["cell4"] == HOME_MM
    assert run.vars["at_target"]["y_mm"] == run.params["target_mm"]

    log_dir = Path(run.log.dir)
    for name in (SCENARIO_FILE, STEPS_FILE, VARS_FILE, META_FILE):
        assert (log_dir / name).is_file(), name
    lines = (log_dir / STEPS_FILE).read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(run.records)
    assert json.loads(lines[0])["id"] == "check_status"
    meta = json.loads((log_dir / META_FILE).read_text(encoding="utf-8"))
    assert meta["run_id"] == run.run_id
    assert meta["state"] == "completed"


async def test_params_override_reaches_the_body(
    engine: Engine, fake_l1: FakeL1
) -> None:
    run = await engine.create_run(
        DEMO.read_text(encoding="utf-8"), params={"target_mm": 12.5}
    )
    await engine.wait(run.run_id)
    assert run.state is RunState.COMPLETED
    moves = [
        body["y_mm"]
        for _c, _m, path, body in fake_l1.calls
        if path == "/v1/linear/move"
    ]
    assert moves == [12.5, HOME_MM]


async def test_parallel_block_uses_distinct_cells(engine: Engine) -> None:
    text = scenario_yaml(
        "  - parallel:\n"
        "      - id: a\n        cell: cell4\n        action: linear/home\n"
        "      - id: b\n        cell: cell1\n        action: gantry/home\n"
    )
    run = await engine.create_run(text)
    await engine.wait(run.run_id)
    assert run.state is RunState.COMPLETED
    assert {r["id"] for r in run.records} == {"a", "b"}


async def test_assert_failure_fails_the_run(engine: Engine) -> None:
    text = scenario_yaml(
        "  - id: home\n    cell: cell4\n    action: linear/home\n"
        "    save_as: h\n"
        # Compared against a position the rail cannot reach, so this
        # tests the assert machinery and not the fake's home value.
        '  - id: check\n    assert: "${h.y_mm} > 1000.0"\n'
    )
    run = await engine.create_run(text)
    await engine.wait(run.run_id)
    assert run.state is RunState.FAILED
    assert "assert failed" in run.records[-1]["error"]["message"]


# ── M5: failure policies, abort, resume ────────────────────────────────────


async def test_on_fail_abort_stops_and_broadcasts_stop(
    engine: Engine, fake_l1: FakeL1
) -> None:
    fake_l1.fail_next("cell4", "linear/home")
    text = scenario_yaml(
        "  - id: home\n    cell: cell4\n    action: linear/home\n"
        "  - id: after\n    cell: cell4\n    action: balance/tare\n"
    )
    run = await engine.create_run(text)
    await engine.wait(run.run_id)
    assert run.state is RunState.FAILED
    assert len(run.records) == 1
    assert run.records[0]["error"]["status"] == 500
    assert run.stop_broadcast == {
        "cell1": "stopped",
        "cell4": "stopped",
        "cell5": "stopped",
    }


async def test_on_fail_continue_keeps_going(
    engine: Engine, fake_l1: FakeL1
) -> None:
    fake_l1.fail_next("cell4", "linear/home")
    text = scenario_yaml(
        "  - id: home\n    cell: cell4\n    action: linear/home\n"
        "    on_fail: continue\n"
        "  - id: after\n    cell: cell4\n    action: balance/tare\n"
    )
    run = await engine.create_run(text)
    await engine.wait(run.run_id)
    assert run.state is RunState.COMPLETED
    assert [r["ok"] for r in run.records] == [False, True]


async def test_on_fail_retry_recovers(engine: Engine, fake_l1: FakeL1) -> None:
    fake_l1.timeout_next("cell4", "linear/home", times=2)
    text = scenario_yaml(
        "  - id: home\n    cell: cell4\n    action: linear/home\n"
        "    on_fail: retry\n    retries: 2\n"
    )
    run = await engine.create_run(text)
    await engine.wait(run.run_id)
    assert run.state is RunState.COMPLETED
    assert [r["attempt"] for r in run.records] == [1, 2, 3]
    assert [r["ok"] for r in run.records] == [False, False, True]
    assert run.records[0]["error"]["kind"] == "timeout"


async def test_on_fail_retry_exhausted_fails(
    engine: Engine, fake_l1: FakeL1
) -> None:
    fake_l1.fail_next("cell4", "linear/home", times=5)
    text = scenario_yaml(
        "  - id: home\n    cell: cell4\n    action: linear/home\n"
        "    on_fail: retry\n    retries: 1\n"
    )
    run = await engine.create_run(text)
    await engine.wait(run.run_id)
    assert run.state is RunState.FAILED
    assert len(run.records) == 2


async def test_step_mode_pauses_and_resumes(engine: Engine) -> None:
    run = await engine.create_run(
        DEMO.read_text(encoding="utf-8"), step_mode=True
    )
    await wait_for(lambda: run.state is RunState.PAUSED)
    assert len(run.records) == 1
    await engine.resume(run.run_id)
    await wait_for(lambda: run.state is RunState.PAUSED)
    assert len(run.records) == 2
    while run.state is RunState.PAUSED:
        await engine.resume(run.run_id)
        await wait_for(lambda: run.state is not RunState.RUNNING)
    await engine.wait(run.run_id)
    assert run.state is RunState.COMPLETED


async def test_resume_from_step_rewinds(engine: Engine) -> None:
    text = scenario_yaml(
        "  - id: one\n    cell: cell4\n    action: linear/home\n"
        "  - id: two\n    cell: cell4\n    action: balance/tare\n"
    )
    run = await engine.create_run(text, step_mode=True)
    await wait_for(lambda: run.state is RunState.PAUSED)
    await engine.resume(run.run_id, from_step="one")
    await wait_for(lambda: run.state is RunState.PAUSED)
    assert [r["id"] for r in run.records] == ["one", "one"]
    await engine.abort(run.run_id)
    await engine.wait(run.run_id)
    assert run.state is RunState.ABORTED


async def test_abort_broadcasts_stop_to_every_cell(
    engine: Engine, fake_l1: FakeL1
) -> None:
    run = await engine.create_run(
        DEMO.read_text(encoding="utf-8"), step_mode=True
    )
    await wait_for(lambda: run.state is RunState.PAUSED)
    await engine.abort(run.run_id)
    await engine.wait(run.run_id)
    assert run.state is RunState.ABORTED
    stopped = [c for c in fake_l1.calls if c[2] == "/v1/stop"]
    assert {c[0] for c in stopped} == {"cell1", "cell4", "cell5"}
    aborted = [
        json.loads(line)
        for line in (Path(run.log.dir) / STEPS_FILE)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert aborted[-1]["event"] == "abort"


async def test_one_active_run_at_a_time(engine: Engine) -> None:
    text = DEMO.read_text(encoding="utf-8")
    first = await engine.create_run(text, step_mode=True)
    await wait_for(lambda: first.state is RunState.PAUSED)
    with pytest.raises(RunConflictError):
        await engine.create_run(text)
    await engine.abort(first.run_id)
    await engine.wait(first.run_id)
    second = await engine.create_run(text)
    await engine.wait(second.run_id)
    assert second.state is RunState.COMPLETED


async def test_invalid_scenario_is_rejected_before_any_call(
    engine: Engine, fake_l1: FakeL1
) -> None:
    with pytest.raises(ScenarioInvalid) as caught:
        await engine.create_run(
            scenario_yaml(
                "  - id: a\n    cell: cell9\n    action: linear/home\n"
            )
        )
    assert caught.value.issues[0].code == "unknown_cell"
    assert not [c for c in fake_l1.calls if c[2].startswith("/v1/")]


# ── Motion safety gate ─────────────────────────────────────────────────────


async def test_first_motion_step_waits_for_confirmation(
    config: OrchestratorConfig,
    registry: Registry,
    client: Any,
    fake_l1: FakeL1,
) -> None:
    guarded = OrchestratorConfig(
        log_dir=config.log_dir,
        retry_delay_s=0.0,
        confirm_first_motion=True,
    )
    engine = Engine(guarded, registry, client)
    run = await engine.create_run(DEMO.read_text(encoding="utf-8"))
    await wait_for(lambda: run.state is RunState.PAUSED)
    # The read-only status step ran; the first motion step is gated.
    assert run.pending_confirmation == "home"
    assert [r["id"] for r in run.records] == ["check_status"]
    assert not [c for c in fake_l1.calls if c[2] == "/v1/linear/home"]
    await engine.confirm(run.run_id)
    await engine.wait(run.run_id)
    assert run.state is RunState.COMPLETED
    assert run.pending_confirmation is None


# ── Scenario-declared operator pause ───────────────────────────────────────

WEIGH = REPO_ROOT / "scenarios" / "demo_weigh_at_position.yaml"

#: The step demo_weigh_at_position.yaml marks for the operator.
PAUSE_STEP = "weigh"

#: A vial inside the scenario's min_vial_g..max_vial_g bracket. Loading
#: it is what the operator does at the pause, so the tests do it there.
VIAL_G = 25.7424


async def test_pause_step_holds_the_run_without_step_mode(
    engine: Engine, fake_l1: FakeL1
) -> None:
    """The point of `pause:`: one prompt, not one per step."""
    run = await engine.create_run(WEIGH.read_text(encoding="utf-8"))
    await wait_for(lambda: run.state is RunState.PAUSED)

    assert run.pending_pause is not None
    assert "vial" in run.pending_pause
    # It held *before* the marked step, not after it.
    assert PAUSE_STEP not in [r["id"] for r in run.records]
    # And it is the only stop: everything up to it already ran.
    assert "move_to_weigh" in [r["id"] for r in run.records]

    fake_l1.pan_g = VIAL_G  # the operator loads the vial, here
    await engine.resume(run.run_id)
    await engine.wait(run.run_id)
    assert run.state is RunState.COMPLETED
    assert run.pending_pause is None


async def test_pause_is_the_only_stop_in_the_whole_run(
    engine: Engine, fake_l1: FakeL1
) -> None:
    """Without step mode the run must pause exactly once."""
    run = await engine.create_run(WEIGH.read_text(encoding="utf-8"))
    pauses = 0
    while run.state not in TERMINAL_STATES:
        if run.state is RunState.PAUSED:
            pauses += 1
            fake_l1.pan_g = VIAL_G
            await engine.resume(run.run_id)
        await asyncio.sleep(0)
    assert run.state is RunState.COMPLETED
    assert pauses == 1


async def test_pause_and_motion_gate_are_reported_separately(
    config: OrchestratorConfig,
    registry: Registry,
    client: Any,
    fake_l1: FakeL1,
) -> None:
    """They say opposite things -- "it is about to move" versus "reach
    into it" -- so one field must not stand in for the other."""
    guarded = OrchestratorConfig(
        log_dir=config.log_dir,
        retry_delay_s=0.0,
        confirm_first_motion=True,
    )
    engine = Engine(guarded, registry, client)
    run = await engine.create_run(WEIGH.read_text(encoding="utf-8"))

    # First stop: the motion gate, with no operator instruction.
    await wait_for(lambda: run.state is RunState.PAUSED)
    assert run.pending_confirmation == "home"
    assert run.pending_pause is None
    await engine.confirm(run.run_id)

    # Second stop: the operator instruction, with no motion warning.
    await wait_for(lambda: run.state is RunState.PAUSED)
    assert run.pending_pause is not None
    assert run.pending_confirmation is None
    fake_l1.pan_g = VIAL_G
    await engine.resume(run.run_id)
    await engine.wait(run.run_id)
    assert run.state is RunState.COMPLETED


def test_pause_on_a_parallel_child_is_rejected() -> None:
    """The gate runs before the block, so a child pause would never
    fire. Refusing it at load time beats skipping the one place an
    operator was meant to intervene."""
    text = """
name: bad_pause
steps:
  - id: block
    parallel:
      - id: a
        cell: cell4
        action: status
        method: GET
        pause: reach into the machine here
"""
    with pytest.raises(ScenarioError):
        load_scenario_text(text)


# ── Cell D (cell5): pump + single Z + hotplate + IR lamp ───────────────────

CELL_D_DEMO = REPO_ROOT / "scenarios" / "demo_cell_d_warmup.yaml"


async def test_cell_d_scenario_validates_and_runs(
    engine: Engine, fake_l1: FakeL1
) -> None:
    text = CELL_D_DEMO.read_text(encoding="utf-8")
    _s, issues = await engine.validate(text)
    assert [str(i) for i in issues] == []

    run = await engine.create_run(text)
    await engine.wait(run.run_id)
    assert run.state is RunState.COMPLETED
    assert fake_l1.target_c["cell5"] == 30.0
    # The run must leave the cell safe: heater off, lamp off, stage home.
    assert fake_l1.heating["cell5"] is False
    assert fake_l1.lamp_on["cell5"] is False
    assert fake_l1.z_mm["cell5"] == 0.0


async def test_cell_d_body_typo_is_caught_before_anything_moves(
    engine: Engine, fake_l1: FakeL1
) -> None:
    # `celsius` is the field; `temperature` is not. The dry run must catch
    # it -- finding out mid-sequence would mean the Z axis had already moved.
    _s, issues = await engine.validate(
        scenario_yaml(
            "  - id: warm\n    cell: cell5\n    action: hotplate/temperature\n"
            "    body:\n      temperature: 30.0\n"
        )
    )
    codes = {i.code for i in issues}
    assert codes == {"body_mismatch"}
    assert not [c for c in fake_l1.calls if c[2].startswith("/v1/hotplate")]


async def test_heater_and_lamp_are_gated_but_reads_are_not(
    config: OrchestratorConfig,
    registry: Registry,
    client: Any,
    fake_l1: FakeL1,
) -> None:
    """A heater or lamp switching on unattended is gated like motion.

    The read-only GETs under the same prefixes are not — the gate keys on
    the method, because every L1 GET is a probe.
    """
    guarded = OrchestratorConfig(
        log_dir=config.log_dir,
        retry_delay_s=0.0,
        confirm_first_motion=True,
    )
    assert guarded.is_hazardous("hotplate/heater") is True
    assert guarded.is_hazardous("lamp/switch") is True
    assert guarded.is_hazardous("zstage/move") is True
    assert guarded.is_hazardous("hotplate/state", "GET") is False
    assert guarded.is_hazardous("lamp/state", "GET") is False
    assert guarded.is_hazardous("status", "GET") is False

    engine = Engine(guarded, registry, client)
    text = scenario_yaml(
        "  - id: read\n    cell: cell5\n    action: hotplate/state\n"
        "    method: GET\n    save_as: hp\n"
        "  - id: heat_on\n    cell: cell5\n    action: hotplate/heater\n"
        "    body:\n      enabled: true\n",
        name="gate_check",
    )
    run = await engine.create_run(text)
    await wait_for(lambda: run.state is RunState.PAUSED)
    # The read ran unattended; the heater is waiting for the operator.
    assert [r["id"] for r in run.records] == ["read"]
    assert run.pending_confirmation == "heat_on"
    assert fake_l1.heating["cell5"] is False
    await engine.abort(run.run_id)
    await engine.wait(run.run_id)
    assert fake_l1.heating["cell5"] is False


async def test_wrong_shape_action_fails_cleanly_at_runtime(
    engine: Engine,
) -> None:
    """GAP-3: the dry run cannot catch a wrong-shape action on the real L1.

    cell5 has no gantry, but the real server advertises `gantry/*` on every
    cell, so validation passes and the call fails at run time — with the
    cell's defensive 409, not a crash.
    """
    run = await engine.create_run(
        scenario_yaml(
            "  - id: nope\n    cell: cell5\n    action: gantry/home\n"
        )
    )
    await engine.wait(run.run_id)
    assert run.state is RunState.FAILED
    error = run.records[0]["error"]
    assert error["status"] == 409
    assert error["payload"]["error"] == "WrongStateError"


def test_fake_l1_does_not_drift_from_the_real_openapi() -> None:
    """Guard: the fake must match the real L1's routes AND body fields.

    The fake is hand-written (spec §9 forbids a deployable mock server), so
    it can drift — and a fake with the wrong field name would make every
    validator test agree with itself and disagree with the bench. Uses the
    validator's own OpenAPI helpers, so it checks what the validator reads.
    Skipped when `server/` is absent from the working tree.
    """
    server_app = pytest.importorskip("server.app")
    real = server_app.create_app().openapi()
    fake = openapi_document()
    for path, item in fake["paths"].items():
        action = path.removeprefix("/v1/")
        for method in item:
            real_op = operation(real, action, method)
            assert real_op is not None, f"{method.upper()} {path} not on L1"
            fake_body = request_schema(fake, operation(fake, action, method))
            if fake_body is None:
                continue
            real_body = request_schema(real, real_op)
            assert real_body is not None, f"{path}: L1 takes no body"
            assert set(fake_body["properties"]) <= set(
                real_body["properties"]
            ), f"{path}: fake has fields L1 does not"
            assert set(fake_body.get("required", ())) == set(
                real_body.get("required", ())
            ), f"{path}: required fields differ from L1"


# ── M1: the HTTP surface ───────────────────────────────────────────────────


def test_api_health_cells_and_validate(
    config: OrchestratorConfig,
    registry: Registry,
    transport: Any,
) -> None:
    app = create_app(config=config, registry=registry, transport=transport)
    with TestClient(app) as http:
        health = http.get("/v1/health").json()
        assert health["ok"] is True
        assert health["cells"] == len(registry)

        cells = http.get("/v1/cells").json()["cells"]
        assert {c["name"] for c in cells} == {"cell1", "cell4", "cell5"}
        assert all(c["reachable"] for c in cells)

        ok = http.post(
            "/v1/scenarios/validate",
            json={"scenario_path": str(DEMO)},
        ).json()
        assert ok == {"ok": True, "scenario": "demo_linear_move", "issues": []}

        bad = http.post(
            "/v1/scenarios/validate",
            json={"scenario_yaml": "name: x\nsteps: []\n"},
        ).json()
        assert bad["ok"] is False
        assert bad["issues"][0]["code"] == "schema"


def test_api_run_lifecycle(
    config: OrchestratorConfig,
    registry: Registry,
    transport: Any,
) -> None:
    app = create_app(config=config, registry=registry, transport=transport)
    with TestClient(app) as http:
        created = http.post(
            "/v1/runs", json={"scenario_path": str(DEMO), "step_mode": True}
        )
        assert created.status_code == 202
        run_id = created.json()["run_id"]

        conflict = http.post("/v1/runs", json={"scenario_path": str(DEMO)})
        assert conflict.status_code == 409
        assert conflict.json()["error"] == "RunConflictError"

        assert http.get(f"/v1/runs/{run_id}").json()["run_id"] == run_id
        assert http.get("/v1/runs/nope").status_code == 404

        aborted = http.post(f"/v1/runs/{run_id}/abort").json()
        assert aborted["stop_broadcast"] == {
            "cell1": "stopped",
            "cell4": "stopped",
            "cell5": "stopped",
        }

        invalid = http.post(
            "/v1/runs", json={"scenario_yaml": "name: x\nsteps: []\n"}
        )
        assert invalid.status_code == 400
        assert invalid.json()["issues"][0]["code"] == "schema"


# ── Dry-run bounds checking (LearnedPatterns #25) ───────────────────────


def _gantry_move_schema() -> tuple[dict, dict]:
    """The real L1 OpenAPI and its GantryMoveRequest schema.

    Imported the same way as the drift guard above: the L1 package pulls in
    the hardware drivers, so a checkout without them skips rather than errors.
    """
    server_app = pytest.importorskip("server.app")
    document = server_app.create_app().openapi()
    return document, document["components"]["schemas"]["GantryMoveRequest"]


def test_dry_run_rejects_a_body_outside_the_schema_bounds() -> None:
    """The bench regression: a scenario validated clean twice, then its
    first real move came back 422 for `accel_pct: 0` against `minimum: 1`.
    The bound was in the document the validator had already parsed."""
    document, schema = _gantry_move_schema()
    stale = {
        **schema,
        "properties": {
            **schema["properties"],
            "accel_pct": {**schema["properties"]["accel_pct"], "minimum": 1.0},
        },
    }
    body = {"x_mm": 50.0, "z_mm": 0.0, "speed_pct": 10, "accel_pct": 0}

    problems = check_body(document, stale, body)

    assert problems and "at least 1.0" in problems[0]


def test_dry_run_accepts_a_body_inside_the_bounds() -> None:
    """Guards the NotImplemented trap: an OpenAPI bound is a float and the
    value is often an int, and `(10).__lt__(1.0)` is NotImplemented, which
    is truthy — so a naive check flags every valid integer field."""
    document, schema = _gantry_move_schema()

    for speed in (1, 10, 100):  # both boundaries and a middle value
        body = {"x_mm": 50.0, "z_mm": 0.0, "speed_pct": speed, "accel_pct": 0}
        assert check_body(document, schema, body) == []


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("speed_pct", 0, "at least"),
        ("speed_pct", 200, "at most"),
        ("x_mm", -5.0, "at least"),
    ],
)
def test_dry_run_bounds_cover_both_ends(
    field: str, value: float, expected: str
) -> None:
    document, schema = _gantry_move_schema()
    body = {"x_mm": 50.0, "z_mm": 0.0, "speed_pct": 10, "accel_pct": 0}

    problems = check_body(document, schema, {**body, field: value})

    assert any(field in p and expected in p for p in problems)
