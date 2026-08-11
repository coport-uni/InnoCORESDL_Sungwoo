"""Pydantic request/response models for the InnoCORESDL /v1 API.

Units are in field names (``_g`` grams, ``_uL`` microliters, ``_mm``
millimeters, ``_pct`` percent) to match the SDLClaude UI unit standard.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# ── Discovery ──────────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    cell_up: bool
    pump_ok: bool | None
    balance_ok: bool | None
    stage_ok: bool | None
    # Values are nullable because a driver may fail to report its version:
    # the MINAS amp's read_software_version() intermittently returns None
    # (measured ~1 call in 5 on the bench). This is the *liveness* probe, so
    # an unreadable version must not turn a healthy cell into a 500 — it is
    # reported as null and the cell still says cell_up.
    driver_versions: dict[str, str | None]


class DiagnoseResponse(BaseModel):
    pump: dict = Field(description="Pump diagnostics (version, valve, …).")
    balance: dict = Field(description="Balance model + serial number.")
    stage: dict = Field(description="Stage status (per-axis).")
    ok_to_initialize: bool
    # cell6 / cell7 only; None on cells without an arm.
    arm: dict | None = Field(
        default=None,
        description="Arm identity + reachability (robot_id, ip, gripper).",
    )
    replay: dict | None = Field(
        default=None,
        description="Replay subprocess state and the last episode's summary.",
    )


class StatusResponse(BaseModel):
    weight_g: float
    valve: str = Field(description="Current valve position label, e.g. '1'.")
    plunger_uL: float
    # Nullable: a cell reports null when it could not read the axis rather
    # than inventing a position. On cell4 stage_x_mm carries the linear
    # rail, whose RS485 read can fail (LearnedPatterns #15).
    stage_x_mm: float | None
    stage_z_mm: float | None
    busy: bool
    error: str | None
    # Cell 5 (cell5) only; None on cells without those devices. The
    # heating/stirring flags are the last commanded state — the RCT
    # digital protocol offers no readback.
    hotplate_c: float | None = None
    hotplate_target_c: float | None = None
    heating: bool | None = None
    stirring: bool | None = None
    lamp_on: bool | None = None
    # cell6 / cell7 (arm) only; None on cells without an arm. Degrees,
    # joint 1 first, read from the encoder — except while a replay owns
    # the controller, when it is the last reading taken before the
    # hand-over (the servo session has one owner; see ArmReplayCell).
    joints_deg: list[float] | None = None
    last_replay: dict | None = None


class ErrorResponse(BaseModel):
    error: str
    code: int | None
    command: str | None
    message: str


# ── Balance ────────────────────────────────────────────────────────────────


class WeightResponse(BaseModel):
    weight_g: float


class WeightReadResponse(BaseModel):
    weight_g: float
    stable: bool


class AmbientRequest(BaseModel):
    level: str = Field(
        description="very_stable | stable | unstable | very_unstable"
    )


class AmbientResponse(BaseModel):
    level: str


# ── Pump ───────────────────────────────────────────────────────────────────


class InitializeRequest(BaseModel):
    force: int = Field(
        default=2, description="0/1/2 or 10..40 init force code."
    )
    ccw: bool = False


class InitializeResponse(BaseModel):
    valve: str
    plunger_uL: float


class ValveRequest(BaseModel):
    port: int = Field(ge=1, le=4, description="Valve port (1 or 3 in use).")


class ValveResponse(BaseModel):
    valve: str


class VolumeRequest(BaseModel):
    target_uL: float = Field(
        ge=0, description="Absolute contained-volume target in µL."
    )


class PlungerResponse(BaseModel):
    plunger_uL: float


class CycleRequest(BaseModel):
    cycles: int = Field(ge=1, le=50)
    volume_uL: float = Field(gt=0)
    source_port: int = Field(ge=1, le=4)
    dispense_port: int = Field(ge=1, le=4)


class CycleResponse(BaseModel):
    cycles_done: int
    final_valve: str


# ── Gantry (XZ) ──────────────────────────────────────────────────────────────


class GantryMoveRequest(BaseModel):
    x_mm: float = Field(ge=0)
    z_mm: float = Field(ge=0)
    speed_pct: int = Field(default=20, ge=1, le=100)
    # ge=0, not ge=1: the driver maps 0-100% onto the MKS accel byte 0-255,
    # where 0 means "no acceleration ramp" — a real, supported setting, and
    # the one BOTH upstream reference scripts use (bridge.py and the
    # bench-validated CVMeasure.py run MOVE_ACCEL_PCT = 0). ge=1 rejected it
    # with a 422 on the first real gantry move.
    accel_pct: int = Field(default=10, ge=0, le=100)


class GantryResponse(BaseModel):
    x_mm: float
    z_mm: float


# ── Linear (Y) ───────────────────────────────────────────────────────────────


class LinearMoveRequest(BaseModel):
    y_mm: float = Field(ge=0)


class LinearResponse(BaseModel):
    y_mm: float


# ── Z stage (single Z) — Cell 5 / cell5 ──────────────────────────────────────
# A separate action set from the gantry: no X target, and no paired-Z
# group interlock (one motor).


class ZStageMoveRequest(BaseModel):
    z_mm: float = Field(ge=0)
    speed_pct: int = Field(default=20, ge=1, le=100)
    accel_pct: int = Field(default=10, ge=0, le=100)  # see GantryMoveRequest


class ZStageResponse(BaseModel):
    z_mm: float


# ── Hotplate (IKA RCT digital) — Cell 5 / cell5 ──────────────────────────────


class HotplateStateResponse(BaseModel):
    plate_c: float
    probe_c: float
    target_c: float
    safety_c: float = Field(description="Device safety-circuit limit.")
    rpm: float
    target_rpm: float
    heating: bool = Field(description="Last commanded state, not a readback.")
    stirring: bool = Field(description="Last commanded state, not a readback.")
    max_c: float = Field(description="This cell's configured °C ceiling.")


class TemperatureRequest(BaseModel):
    celsius: float = Field(
        ge=0, description="Target plate temperature; capped by max_c."
    )


class TemperatureResponse(BaseModel):
    target_c: float


class HeaterRequest(BaseModel):
    # NOT `on`: YAML 1.1 resolves a bare `on:` key to the boolean True,
    # so a scenario could never address it (LearnedPatterns #8).
    enabled: bool


class HeaterResponse(BaseModel):
    heating: bool
    target_c: float


class StirSpeedRequest(BaseModel):
    rpm: float = Field(ge=0)


class StirSpeedResponse(BaseModel):
    target_rpm: float


class StirrerRequest(BaseModel):
    enabled: bool


class StirrerResponse(BaseModel):
    stirring: bool
    target_rpm: float


# ── Lamp (IR lamp on a Tapo plug) — Cell 5 / cell5 ───────────────────────────


class LampRequest(BaseModel):
    enabled: bool


class LampResponse(BaseModel):
    is_on: bool | None = Field(
        description="None when the plug's state could not be read."
    )
    target: str = Field(description="Plug name/IP from the driver's list.")
    devices: list[str] = Field(default_factory=list)


# ── Arm (FR5, replay-only) — cell6 / cell7 ───────────────────────────────────
# Not a pose interface on purpose: a request names a recorded dataset
# episode. Prefetch and replay are separate routes so a slow download
# cannot look like a stalled arm (spec D7).


class ArmPrefetchRequest(BaseModel):
    repo_id: str = Field(
        description="HuggingFace dataset id; must match the cell's "
        "allowed_repo_prefixes."
    )
    episode: int = Field(ge=0, description="Episode index in the dataset.")


class ArmPrefetchResponse(BaseModel):
    cached: bool
    frames: int
    fps: int
    duration_s: float


class ArmPrepareResponse(BaseModel):
    """Result of clearing faults + energising the arm.

    ``error_before`` is the point of the response: it records what was
    wrong at the moment the operator chose to clear it, so a runlog does
    not lose that. ``[0, 0]`` means there was nothing latched.
    """

    error_before: list[int] = Field(
        description="Controller fault as [main_code, sub_code] BEFORE reset."
    )
    error_after: list[int]
    error_settled: list[int] = Field(
        description="Re-read a second later, to catch a re-latching fault."
    )
    # NOT `ready`. Measured 2026-08-11: cell6 reported [0, 0] here and
    # still answered `MoveJ ... SDK error 154`. A cleared fault code does
    # not promise the controller will accept motion, so this field claims
    # only what was done.
    fault_cleared: bool
    joints_deg: list[float]


class ArmJogRequest(BaseModel):
    """One bounded, RELATIVE nudge of a single joint.

    Deliberately not a pose: the arm action set stays replay-only (spec
    D8). This exists so the T1 acceptance test (joint 1 +10 deg) can run
    as a scenario, with the orchestrator's operator gate and runlog,
    instead of a standalone script. The cell caps the magnitude again —
    a 422 here and a 400 there.
    """

    joint: int = Field(ge=1, le=6, description="1-based joint index.")
    delta_deg: float = Field(
        ge=-30.0,
        le=30.0,
        description="Relative degrees; the cell caps at 30 as well.",
    )
    speed_pct: float | None = Field(default=None, gt=0, le=30.0)


class ArmJogResponse(BaseModel):
    joint: int
    before_deg: list[float]
    after_deg: list[float]
    target_delta_deg: float
    # MEASURED, not commanded. A scenario asserts on this and on
    # max_other_axis_delta_deg, never on an endpoint (LearnedPatterns #33).
    achieved_delta_deg: float
    max_other_axis_delta_deg: float
    joints_deg: list[float]


class ArmReplayRequest(BaseModel):
    repo_id: str
    episode: int = Field(ge=0)
    # Null adopts the recorded rate. Any other value must equal it: a
    # re-timed replay compresses the ServoJ interval (spec D5).
    fps: int | None = Field(default=None, ge=1)


class ArmReplayResponse(BaseModel):
    completed: bool
    frames: int
    fps: int
    elapsed_s: float
    # Null when the dataset exposed no last frame to compare against —
    # reporting 0.0 there would be a fabricated pass.
    final_joint_error_deg: float | None
    joints_deg: list[float] = Field(
        description="Encoder reading taken after the replay, degrees."
    )


class ArmProgramRequest(BaseModel):
    """Run a ``.lua`` job program the controller already holds.

    Only a bare file name: it is resolved under the cell's
    ``program_dir`` (``/fruser``), and the cell rejects anything with a
    path separator so a request cannot steer ``ProgramLoad`` elsewhere.
    Nothing in this API uploads, edits or deletes a program — authoring
    is teach-pendant / WebApp work (docs/SPEC_ARM_LUA_PROGRAM.md §2).
    """

    name: str = Field(
        min_length=1,
        max_length=128,
        description='Program file name, e.g. "test1.lua".',
    )


class ArmProgramResponse(BaseModel):
    # NOT the same claim as ArmReplayResponse.completed. It means the
    # controller returned to the stopped state with no latched fault and
    # the encoder answered afterwards — not that the arm reached an
    # intended pose. The cell never reads the script, so it has no
    # expected end pose to check (docs/SPEC_ARM_LUA_PROGRAM.md §6.4);
    # judging joints_deg is the caller's job.
    completed: bool
    name: str
    elapsed_s: float
    # Last line the controller reported executing. Null when the
    # controller would not answer GetCurrentLine — progress is
    # informational, so an unreadable line does not fail the run.
    last_line: int | None
    joints_deg: list[float] = Field(
        description="Encoder reading taken after the program, degrees."
    )


# ── Safety ─────────────────────────────────────────────────────────────────


class StopResponse(BaseModel):
    stopped: bool
    # Per-stage outcome from the cells whose stop has independent stages
    # (cell6 / cell7: kill the replay, stop the job program, then stop the
    # controller). None on the cells whose stop() is all-or-nothing.
    detail: dict | None = None
