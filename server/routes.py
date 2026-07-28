"""HTTP routes for the InnoCORESDL /v1 API.

Every state-changing handler acquires ``app.state.lock`` for the whole
device interaction (single in-flight, matching the drivers' one-command-at-
a-time contract) and runs blocking cell calls in a worker thread via
``run_in_threadpool`` so the event loop stays responsive. ``GET /v1/health``
is the only lock-free probe.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool

from server.schemas import (
    AmbientRequest,
    AmbientResponse,
    CycleRequest,
    CycleResponse,
    DiagnoseResponse,
    HealthResponse,
    HeaterRequest,
    HeaterResponse,
    HotplateStateResponse,
    InitializeRequest,
    InitializeResponse,
    LampRequest,
    LampResponse,
    PlungerResponse,
    GantryMoveRequest,
    GantryResponse,
    LinearMoveRequest,
    LinearResponse,
    StatusResponse,
    StirSpeedRequest,
    StirSpeedResponse,
    StirrerRequest,
    StirrerResponse,
    StopResponse,
    TemperatureRequest,
    TemperatureResponse,
    ValveRequest,
    ValveResponse,
    VolumeRequest,
    WeightReadResponse,
    WeightResponse,
    ZStageMoveRequest,
    ZStageResponse,
)

router = APIRouter(prefix="/v1")


def _cell(request: Request) -> Any:
    return request.app.state.cell


# ── Discovery ──────────────────────────────────────────────────────────────


@router.get(
    "/health",
    response_model=HealthResponse,
    tags=["Discovery"],
    summary="Liveness probe (lock-free)",
)
async def health(request: Request) -> HealthResponse:
    cell = getattr(request.app.state, "cell", None)
    last = getattr(request.app.state, "last_diagnose", None)

    def _ok(dev: str) -> bool | None:
        if last is None:
            return None
        return bool(last.get(dev, {}).get("ok"))

    return HealthResponse(
        cell_up=cell is not None,
        pump_ok=_ok("pump"),
        balance_ok=_ok("balance"),
        stage_ok=_ok("stage"),
        driver_versions=(last or {}).get("versions", {}),
    )


@router.get(
    "/diagnose",
    response_model=DiagnoseResponse,
    tags=["Discovery"],
    summary="One-shot commissioning probe of all three devices",
)
async def diagnose(request: Request) -> DiagnoseResponse:
    cell = _cell(request)
    async with request.app.state.lock:
        report = await run_in_threadpool(cell.diagnose)
    request.app.state.last_diagnose = report
    return DiagnoseResponse(
        pump=report["pump"],
        balance=report["balance"],
        stage=report["stage"],
        ok_to_initialize=report["ok_to_initialize"],
    )


@router.get(
    "/status",
    response_model=StatusResponse,
    tags=["Discovery"],
    summary="Live readouts (poll ~2 s)",
)
async def status(request: Request) -> StatusResponse:
    cell = _cell(request)
    async with request.app.state.lock:
        s = await run_in_threadpool(cell.status)
    return StatusResponse(**s)


# ── Balance ────────────────────────────────────────────────────────────────


@router.post(
    "/balance/tare",
    response_model=WeightResponse,
    tags=["Balance"],
    summary="Tare the balance",
)
async def tare(request: Request) -> WeightResponse:
    cell = _cell(request)
    async with request.app.state.lock:
        weight_g = await run_in_threadpool(cell.tare)
    return WeightResponse(weight_g=weight_g)


@router.post(
    "/balance/calibrate",
    response_model=WeightResponse,
    tags=["Balance"],
    summary="Internal (isoCAL) calibration — empty pan; commissioning only",
)
async def calibrate(request: Request) -> WeightResponse:
    cell = _cell(request)
    async with request.app.state.lock:
        weight_g = await run_in_threadpool(cell.calibrate)
    return WeightResponse(weight_g=weight_g)


@router.get(
    "/balance/weight",
    response_model=WeightReadResponse,
    tags=["Balance"],
    summary="Settled weight read",
)
async def weight(request: Request) -> WeightReadResponse:
    cell = _cell(request)
    async with request.app.state.lock:
        weight_g, stable = await run_in_threadpool(cell.read_weight)
    return WeightReadResponse(weight_g=weight_g, stable=stable)


@router.post(
    "/balance/ambient",
    response_model=AmbientResponse,
    tags=["Balance"],
    summary="Set the ambient (vibration) filter level",
)
async def ambient(request: Request, body: AmbientRequest) -> AmbientResponse:
    cell = _cell(request)
    async with request.app.state.lock:
        level = await run_in_threadpool(cell.set_ambient, body.level)
    return AmbientResponse(level=level)


# ── Pump ───────────────────────────────────────────────────────────────────


@router.post(
    "/pump/initialize",
    response_model=InitializeResponse,
    tags=["Pump"],
    summary="Home plunger + valve",
)
async def initialize(
    request: Request, body: InitializeRequest
) -> InitializeResponse:
    cell = _cell(request)
    async with request.app.state.lock:
        state = await run_in_threadpool(
            lambda: cell.initialize(force=body.force, ccw=body.ccw)
        )
    return InitializeResponse(**state)


@router.post(
    "/pump/valve",
    response_model=ValveResponse,
    tags=["Pump"],
    summary="Move the valve to a port",
)
async def valve(request: Request, body: ValveRequest) -> ValveResponse:
    cell = _cell(request)
    async with request.app.state.lock:
        pos = await run_in_threadpool(cell.move_valve, body.port)
    return ValveResponse(valve=pos)


@router.post(
    "/pump/aspirate",
    response_model=PlungerResponse,
    tags=["Pump"],
    summary="Aspirate to an absolute contained volume",
)
async def aspirate(request: Request, body: VolumeRequest) -> PlungerResponse:
    cell = _cell(request)
    async with request.app.state.lock:
        plunger_uL = await run_in_threadpool(cell.aspirate, body.target_uL)
    return PlungerResponse(plunger_uL=plunger_uL)


@router.post(
    "/pump/dispense",
    response_model=PlungerResponse,
    tags=["Pump"],
    summary="Dispense to an absolute contained volume (default empty)",
)
async def dispense(request: Request, body: VolumeRequest) -> PlungerResponse:
    cell = _cell(request)
    async with request.app.state.lock:
        plunger_uL = await run_in_threadpool(cell.dispense, body.target_uL)
    return PlungerResponse(plunger_uL=plunger_uL)


@router.post(
    "/pump/cycle",
    response_model=CycleResponse,
    tags=["Pump"],
    summary="Repeated aspirate→dispense (prime / dispense)",
)
async def cycle(request: Request, body: CycleRequest) -> CycleResponse:
    cell = _cell(request)
    async with request.app.state.lock:
        result = await run_in_threadpool(
            lambda: cell.cycle(
                cycles=body.cycles,
                volume_uL=body.volume_uL,
                source_port=body.source_port,
                dispense_port=body.dispense_port,
            )
        )
    return CycleResponse(**result)


# ── Gantry (XZ) — pump+gantry cells ──────────────────────────────────────────


@router.post(
    "/gantry/home",
    response_model=GantryResponse,
    tags=["Gantry"],
    summary="Home the XZ gantry to the origin",
)
async def gantry_home(request: Request) -> GantryResponse:
    cell = _cell(request)
    async with request.app.state.lock:
        x_mm, z_mm = await run_in_threadpool(cell.home_gantry)
    return GantryResponse(x_mm=x_mm, z_mm=z_mm)


@router.post(
    "/gantry/move",
    response_model=GantryResponse,
    tags=["Gantry"],
    summary="Move the XZ gantry (up → X → down)",
)
async def gantry_move(
    request: Request, body: GantryMoveRequest
) -> GantryResponse:
    cell = _cell(request)
    async with request.app.state.lock:
        x_mm, z_mm = await run_in_threadpool(
            lambda: cell.move_gantry(
                body.x_mm,
                body.z_mm,
                speed_pct=body.speed_pct,
                accel_pct=body.accel_pct,
            )
        )
    return GantryResponse(x_mm=x_mm, z_mm=z_mm)


# ── Linear (Y) — balance+linear cells ─────────────────────────────────────────


@router.post(
    "/linear/home",
    response_model=LinearResponse,
    tags=["Linear"],
    summary="Home the linear Y rail to the encoder origin (0 mm)",
)
async def linear_home(request: Request) -> LinearResponse:
    cell = _cell(request)
    async with request.app.state.lock:
        y_mm = await run_in_threadpool(cell.home_linear)
    return LinearResponse(y_mm=y_mm)


@router.post(
    "/linear/move",
    response_model=LinearResponse,
    tags=["Linear"],
    summary="Move the linear Y rail to an absolute mm target",
)
async def linear_move(
    request: Request, body: LinearMoveRequest
) -> LinearResponse:
    cell = _cell(request)
    async with request.app.state.lock:
        y_mm = await run_in_threadpool(lambda: cell.move_linear(body.y_mm))
    return LinearResponse(y_mm=y_mm)


# ── Z stage (single Z) — Cell 5 / cell5 ─────────────────────────────────────


@router.post(
    "/zstage/home",
    response_model=ZStageResponse,
    tags=["ZStage"],
    summary="Home the single Z axis to its origin limit",
)
async def zstage_home(request: Request) -> ZStageResponse:
    cell = _cell(request)
    async with request.app.state.lock:
        z_mm = await run_in_threadpool(cell.home_zstage)
    return ZStageResponse(z_mm=z_mm)


@router.post(
    "/zstage/move",
    response_model=ZStageResponse,
    tags=["ZStage"],
    summary="Move the single Z axis to an absolute mm target",
)
async def zstage_move(
    request: Request, body: ZStageMoveRequest
) -> ZStageResponse:
    cell = _cell(request)
    async with request.app.state.lock:
        z_mm = await run_in_threadpool(
            lambda: cell.move_zstage(
                body.z_mm,
                speed_pct=body.speed_pct,
                accel_pct=body.accel_pct,
            )
        )
    return ZStageResponse(z_mm=z_mm)


# ── Hotplate — Cell 5 / cell5 ───────────────────────────────────────────────


@router.get(
    "/hotplate/state",
    response_model=HotplateStateResponse,
    tags=["Hotplate"],
    summary="Temperatures, stirrer speed, and the commanded on/off state",
)
async def hotplate_state(request: Request) -> HotplateStateResponse:
    cell = _cell(request)
    async with request.app.state.lock:
        state = await run_in_threadpool(cell.read_hotplate)
    return HotplateStateResponse(**state)


@router.post(
    "/hotplate/temperature",
    response_model=TemperatureResponse,
    tags=["Hotplate"],
    summary="Set the target plate temperature (does not start the heater)",
)
async def hotplate_temperature(
    request: Request, body: TemperatureRequest
) -> TemperatureResponse:
    cell = _cell(request)
    async with request.app.state.lock:
        target_c = await run_in_threadpool(
            cell.set_hotplate_temperature, body.celsius
        )
    return TemperatureResponse(target_c=target_c)


@router.post(
    "/hotplate/heater",
    response_model=HeaterResponse,
    tags=["Hotplate"],
    summary="Start or stop heating — never leave the bench while it heats",
)
async def hotplate_heater(
    request: Request, body: HeaterRequest
) -> HeaterResponse:
    cell = _cell(request)
    async with request.app.state.lock:
        state = await run_in_threadpool(
            lambda: cell.set_hotplate_heater(enabled=body.enabled)
        )
    return HeaterResponse(**state)


@router.post(
    "/hotplate/speed",
    response_model=StirSpeedResponse,
    tags=["Hotplate"],
    summary="Set the stirrer target speed (does not start the stirrer)",
)
async def hotplate_speed(
    request: Request, body: StirSpeedRequest
) -> StirSpeedResponse:
    cell = _cell(request)
    async with request.app.state.lock:
        target_rpm = await run_in_threadpool(cell.set_hotplate_speed, body.rpm)
    return StirSpeedResponse(target_rpm=target_rpm)


@router.post(
    "/hotplate/stirrer",
    response_model=StirrerResponse,
    tags=["Hotplate"],
    summary="Start or stop the stirrer",
)
async def hotplate_stirrer(
    request: Request, body: StirrerRequest
) -> StirrerResponse:
    cell = _cell(request)
    async with request.app.state.lock:
        state = await run_in_threadpool(
            lambda: cell.set_hotplate_stirrer(enabled=body.enabled)
        )
    return StirrerResponse(**state)


# ── Lamp (IR lamp on a Tapo plug) — Cell 5 / cell5 ──────────────────────────


@router.get(
    "/lamp/state",
    response_model=LampResponse,
    tags=["Lamp"],
    summary="Read the plug's on/off state over the LAN",
)
async def lamp_state(request: Request) -> LampResponse:
    cell = _cell(request)
    async with request.app.state.lock:
        state = await run_in_threadpool(cell.read_lamp)
    return LampResponse(**state)


@router.post(
    "/lamp/switch",
    response_model=LampResponse,
    tags=["Lamp"],
    summary="Switch the IR lamp on or off",
)
async def lamp_switch(request: Request, body: LampRequest) -> LampResponse:
    cell = _cell(request)
    async with request.app.state.lock:
        state = await run_in_threadpool(
            lambda: cell.set_lamp(enabled=body.enabled)
        )
    return LampResponse(**state)


# ── Safety ─────────────────────────────────────────────────────────────────


@router.post(
    "/stop",
    response_model=StopResponse,
    tags=["Safety"],
    summary="Abort all motion now (Cell 5 also kills heater, stirrer, lamp)",
)
async def stop(request: Request) -> StopResponse:
    cell = _cell(request)
    async with request.app.state.lock:
        await run_in_threadpool(cell.stop)
    return StopResponse(stopped=True)
