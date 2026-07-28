"""Shared fixtures for the orchestrator tests.

There is no mock cell server in this repo (spec section 9). The stand-in
lives here instead, at test level: :class:`FakeL1` answers the same
``/openapi.json`` shape and the same ``/v1`` routes the real L1 serves
(checked against ``server/routes.py`` + ``server/schemas.py``), and is
mounted through ``httpx.MockTransport``.

Keeping it here -- not in ``orchestrator/`` -- is deliberate: it must
never become a deployable artifact that someone mistakes for a cell.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator.client import CellClient  # noqa: E402
from orchestrator.engine import Engine  # noqa: E402
from orchestrator.registry import (  # noqa: E402
    CellEntry,
    OrchestratorConfig,
    Registry,
)

HTTP_OK = 200
HTTP_SERVER_ERROR = 500

#: Simulated travel of the linear rail per move, in mm/s of wall clock.
FAKE_MOVE_DELAY_S = 0.0


def _ref(name: str) -> dict[str, Any]:
    return {"$ref": f"#/components/schemas/{name}"}


def _post(operation_id: str, request: str | None = None) -> dict[str, Any]:
    op: dict[str, Any] = {
        "operationId": operation_id,
        "responses": {"200": {"description": "ok"}},
    }
    if request is not None:
        op["requestBody"] = {
            "required": True,
            "content": {"application/json": {"schema": _ref(request)}},
        }
    return {"post": op}


def _get(operation_id: str) -> dict[str, Any]:
    return {
        "get": {
            "operationId": operation_id,
            "responses": {"200": {"description": "ok"}},
        }
    }


#: Mirrors the request models of ``server/schemas.py``.
_SCHEMAS: dict[str, Any] = {
    "LinearMoveRequest": {
        "type": "object",
        "title": "LinearMoveRequest",
        "properties": {"y_mm": {"type": "number", "minimum": 0}},
        "required": ["y_mm"],
    },
    "GantryMoveRequest": {
        "type": "object",
        "title": "GantryMoveRequest",
        "properties": {
            "x_mm": {"type": "number", "minimum": 0},
            "z_mm": {"type": "number", "minimum": 0},
            "speed_pct": {"type": "integer", "default": 20},
            "accel_pct": {"type": "integer", "default": 10},
        },
        "required": ["x_mm", "z_mm"],
    },
    "VolumeRequest": {
        "type": "object",
        "title": "VolumeRequest",
        "properties": {"target_uL": {"type": "number", "minimum": 0}},
        "required": ["target_uL"],
    },
    "ValveRequest": {
        "type": "object",
        "title": "ValveRequest",
        "properties": {"port": {"type": "integer"}},
        "required": ["port"],
    },
    "AmbientRequest": {
        "type": "object",
        "title": "AmbientRequest",
        "properties": {"level": {"type": "string"}},
        "required": ["level"],
    },
    "ZStageMoveRequest": {
        "type": "object",
        "title": "ZStageMoveRequest",
        "properties": {
            "z_mm": {"type": "number", "minimum": 0},
            "speed_pct": {"type": "integer", "default": 20},
            "accel_pct": {"type": "integer", "default": 10},
        },
        "required": ["z_mm"],
    },
    "TemperatureRequest": {
        "type": "object",
        "title": "TemperatureRequest",
        "properties": {"celsius": {"type": "number", "minimum": 0}},
        "required": ["celsius"],
    },
    "HeaterRequest": {
        "type": "object",
        "title": "HeaterRequest",
        "properties": {"enabled": {"type": "boolean"}},
        "required": ["enabled"],
    },
    "StirSpeedRequest": {
        "type": "object",
        "title": "StirSpeedRequest",
        "properties": {"rpm": {"type": "number", "minimum": 0}},
        "required": ["rpm"],
    },
    "StirrerRequest": {
        "type": "object",
        "title": "StirrerRequest",
        "properties": {"enabled": {"type": "boolean"}},
        "required": ["enabled"],
    },
    "LampRequest": {
        "type": "object",
        "title": "LampRequest",
        "properties": {"enabled": {"type": "boolean"}},
        "required": ["enabled"],
    },
}

_COMMON_PATHS: dict[str, Any] = {
    "/v1/health": _get("health"),
    "/v1/diagnose": _get("diagnose"),
    "/v1/status": _get("status"),
    "/v1/stop": _post("stop"),
}

_LINEAR_PATHS: dict[str, Any] = {
    "/v1/linear/home": _post("linear_home"),
    "/v1/linear/move": _post("linear_move", "LinearMoveRequest"),
    "/v1/balance/tare": _post("tare"),
    "/v1/balance/weight": _get("weight"),
    "/v1/balance/ambient": _post("ambient", "AmbientRequest"),
}

_PUMP_PATHS: dict[str, Any] = {
    "/v1/pump/valve": _post("valve", "ValveRequest"),
    "/v1/pump/aspirate": _post("aspirate", "VolumeRequest"),
    "/v1/pump/dispense": _post("dispense", "VolumeRequest"),
}

_GANTRY_PATHS: dict[str, Any] = {
    "/v1/gantry/home": _post("gantry_home"),
    "/v1/gantry/move": _post("gantry_move", "GantryMoveRequest"),
    **_PUMP_PATHS,
}

#: Cell D (cell5): single Z + hotplate + IR lamp, on top of the pump.
_THERMAL_PATHS: dict[str, Any] = {
    "/v1/zstage/home": _post("zstage_home"),
    "/v1/zstage/move": _post("zstage_move", "ZStageMoveRequest"),
    "/v1/hotplate/state": _get("hotplate_state"),
    "/v1/hotplate/temperature": _post(
        "hotplate_temperature", "TemperatureRequest"
    ),
    "/v1/hotplate/heater": _post("hotplate_heater", "HeaterRequest"),
    "/v1/hotplate/speed": _post("hotplate_speed", "StirSpeedRequest"),
    "/v1/hotplate/stirrer": _post("hotplate_stirrer", "StirrerRequest"),
    "/v1/lamp/state": _get("lamp_state"),
    "/v1/lamp/switch": _post("lamp_switch", "LampRequest"),
    **_PUMP_PATHS,
}

_SHAPE_PATHS: dict[str, dict[str, Any]] = {
    "linear": _LINEAR_PATHS,
    "gantry": _GANTRY_PATHS,
    "thermal": _THERMAL_PATHS,
}

#: Which action families each shape actually implements. The OpenAPI does
#: NOT encode this (see below) — the cell's defensive stubs do, at run
#: time, with a 409.
_SHAPE_FAMILIES: dict[str, set[str]] = {
    "linear": {"linear", "balance"},
    "gantry": {"gantry", "pump"},
    "thermal": {"zstage", "hotplate", "lamp", "pump"},
}

#: Families that belong to some shape; anything else is a real 404.
_ALL_FAMILIES = set().union(*_SHAPE_FAMILIES.values())


def openapi_document(_shape: str | None = None) -> dict[str, Any]:
    """The L1 OpenAPI document — deliberately shape-AGNOSTIC.

    The real server mounts one router for every cell shape, so cell4's
    ``/openapi.json`` also advertises ``gantry/*`` and cell5's advertises
    ``linear/*`` (docs/L1_AUDIT.md GAP-3). The fake reproduces that: a
    narrower fake would let tests "catch" wrong-shape actions that the real
    dry run cannot, which is worse than useless. Wrong-shape calls fail at
    run time with the cell's 409, and a test asserts exactly that.
    """
    paths = dict(_COMMON_PATHS)
    for shape_paths in _SHAPE_PATHS.values():
        paths.update(shape_paths)
    return {
        "openapi": "3.1.0",
        "info": {"title": "ics-server", "version": "0.1.0"},
        "paths": paths,
        "components": {"schemas": _SCHEMAS},
    }


class FakeL1:
    """Minimal in-memory stand-in for the L1 cell servers."""

    def __init__(self, shapes: dict[str, str]) -> None:
        self.shapes = shapes
        self.calls: list[tuple[str, str, str, Any]] = []
        self.position_mm: dict[str, float] = dict.fromkeys(shapes, 0.0)
        self.z_mm: dict[str, float] = dict.fromkeys(shapes, 0.0)
        self.target_c: dict[str, float] = dict.fromkeys(shapes, 20.0)
        self.heating: dict[str, bool] = dict.fromkeys(shapes, False)
        self.lamp_on: dict[str, bool] = dict.fromkeys(shapes, False)
        #: What the pan is carrying, in grams. Default is an empty,
        #: freshly tared pan. A test that exercises a scenario with an
        #: operator step sets this *while the run is paused* -- which is
        #: what the operator physically does at that pause.
        self.pan_g = 0.005
        self.failures: dict[tuple[str, str], list[Callable[[], Any]]] = {}

    # ── failure injection ──────────────────────────────────────────────

    def fail_next(
        self,
        cell: str,
        action: str,
        *,
        times: int = 1,
        status: int = HTTP_SERVER_ERROR,
        message: str = "injected fault",
    ) -> None:
        """Make the next ``times`` calls answer an L1 error envelope."""

        def _responder() -> httpx.Response:
            return httpx.Response(
                status,
                json={
                    "error": "DeviceFaultError",
                    "code": 42,
                    "command": action,
                    "message": message,
                },
            )

        self.failures.setdefault((cell, action), []).extend(
            [_responder] * times
        )

    def timeout_next(self, cell: str, action: str, *, times: int = 1) -> None:
        """Make the next ``times`` calls time out at the transport."""

        def _responder() -> httpx.Response:
            raise httpx.ReadTimeout("injected timeout")

        self.failures.setdefault((cell, action), []).extend(
            [_responder] * times
        )

    # ── transport ──────────────────────────────────────────────────────

    def handle(self, request: httpx.Request) -> httpx.Response:
        cell = request.url.host.split(".")[0]
        path = request.url.path
        body = None
        if request.content:
            import json

            body = json.loads(request.content)
        self.calls.append((cell, request.method, path, body))
        if cell not in self.shapes:
            return httpx.Response(404, json={"message": "no such cell"})
        action = path.removeprefix("/v1/").lstrip("/")
        queued = self.failures.get((cell, action))
        if queued:
            return queued.pop(0)()
        if path == "/openapi.json":
            return httpx.Response(HTTP_OK, json=openapi_document())
        family = action.split("/")[0]
        implemented = _SHAPE_FAMILIES[self.shapes[cell]]
        if family in _ALL_FAMILIES and family not in implemented:
            # What the real cell's defensive stub does: WrongStateError.
            return httpx.Response(
                409,
                json={
                    "error": "WrongStateError",
                    "code": None,
                    "command": family,
                    "message": f"this cell has no {family}",
                },
            )
        return self._respond(cell, action, body)

    def _respond(self, cell: str, action: str, body: Any) -> httpx.Response:
        if action == "health":
            return httpx.Response(
                HTTP_OK,
                json={
                    "cell_up": True,
                    "pump_ok": None,
                    "balance_ok": None,
                    "stage_ok": None,
                    "driver_versions": {},
                },
            )
        if action == "status":
            return httpx.Response(
                HTTP_OK,
                json={
                    "weight_g": 0.0,
                    "valve": "-",
                    "plunger_uL": 0.0,
                    "stage_x_mm": self.position_mm[cell],
                    "stage_z_mm": 0.0,
                    "busy": False,
                    "error": None,
                },
            )
        if action == "stop":
            return httpx.Response(HTTP_OK, json={"stopped": True})
        if action == "linear/home":
            self.position_mm[cell] = 0.0
            return httpx.Response(HTTP_OK, json={"y_mm": 0.0})
        if action == "linear/move":
            self.position_mm[cell] = float(body["y_mm"])
            return httpx.Response(
                HTTP_OK, json={"y_mm": self.position_mm[cell]}
            )
        if action == "gantry/home":
            return httpx.Response(HTTP_OK, json={"x_mm": 0.0, "z_mm": 0.0})
        if action == "gantry/move":
            return httpx.Response(
                HTTP_OK,
                json={"x_mm": body["x_mm"], "z_mm": body["z_mm"]},
            )
        if action == "balance/tare":
            return httpx.Response(HTTP_OK, json={"weight_g": 0.0})
        if action == "balance/weight":
            return httpx.Response(
                HTTP_OK, json={"weight_g": self.pan_g, "stable": True}
            )
        if action.startswith("pump/"):
            return httpx.Response(HTTP_OK, json={"plunger_uL": 0.0})
        if action == "zstage/home":
            self.z_mm[cell] = 0.0
            return httpx.Response(HTTP_OK, json={"z_mm": 0.0})
        if action == "zstage/move":
            self.z_mm[cell] = float(body["z_mm"])
            return httpx.Response(HTTP_OK, json={"z_mm": self.z_mm[cell]})
        if action == "hotplate/state":
            return httpx.Response(
                HTTP_OK,
                json={
                    "plate_c": 21.5,
                    "probe_c": 21.0,
                    "target_c": self.target_c[cell],
                    "safety_c": 300.0,
                    "rpm": 0.0,
                    "target_rpm": 0.0,
                    "heating": self.heating[cell],
                    "stirring": False,
                    "max_c": 150.0,
                },
            )
        if action == "hotplate/temperature":
            self.target_c[cell] = float(body["celsius"])
            return httpx.Response(
                HTTP_OK, json={"target_c": self.target_c[cell]}
            )
        if action == "hotplate/heater":
            self.heating[cell] = bool(body["enabled"])
            return httpx.Response(
                HTTP_OK,
                json={
                    "heating": self.heating[cell],
                    "target_c": self.target_c[cell],
                },
            )
        if action == "hotplate/speed":
            return httpx.Response(HTTP_OK, json={"target_rpm": body["rpm"]})
        if action == "hotplate/stirrer":
            return httpx.Response(
                HTTP_OK, json={"stirring": body["enabled"], "target_rpm": 0.0}
            )
        if action in ("lamp/state", "lamp/switch"):
            if action == "lamp/switch":
                self.lamp_on[cell] = bool(body["enabled"])
            return httpx.Response(
                HTTP_OK,
                json={
                    "is_on": self.lamp_on[cell],
                    "target": "ir_lamp",
                    "devices": ["ir_lamp"],
                },
            )
        return httpx.Response(404, json={"message": f"no route {action}"})


@pytest.fixture
def shapes() -> dict[str, str]:
    """The three cell shapes: cell1–3, cell4, and Cell D (cell5)."""
    return {"cell1": "gantry", "cell4": "linear", "cell5": "thermal"}


@pytest.fixture
def fake_l1(shapes: dict[str, str]) -> FakeL1:
    return FakeL1(shapes)


@pytest.fixture
def registry(shapes: dict[str, str]) -> Registry:
    return Registry(
        cells={
            name: CellEntry(
                name=name,
                base_url=f"http://{name}.test",
                nuc="nuc1" if name in ("cell1", "cell4") else "nuc2",
            )
            for name in shapes
        }
    )


@pytest.fixture
def config(tmp_path: Path) -> OrchestratorConfig:
    """Test defaults: no operator gate, no retry sleep."""
    return OrchestratorConfig(
        log_dir=tmp_path / "runs",
        retry_delay_s=0.0,
        confirm_first_motion=False,
    )


@pytest.fixture
def transport(fake_l1: FakeL1) -> httpx.MockTransport:
    return httpx.MockTransport(fake_l1.handle)


@pytest.fixture
async def client(registry: Registry, transport: httpx.MockTransport) -> Any:
    cell_client = CellClient(registry, transport=transport)
    try:
        yield cell_client
    finally:
        await cell_client.aclose()


@pytest.fixture
def engine(
    config: OrchestratorConfig, registry: Registry, client: CellClient
) -> Engine:
    return Engine(config, registry, client)
