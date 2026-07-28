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


# ── Safety ─────────────────────────────────────────────────────────────────


class StopResponse(BaseModel):
    stopped: bool
