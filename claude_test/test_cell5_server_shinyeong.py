#!/usr/bin/env python3
"""Serve cell5 (analysis pump + single ball-screw Z) WITHOUT the hotplate.

WHY. ``PumpZThermalCell.open`` refuses to start unless the IKA RCT digital
is found (cell/pump_z_thermal_cell.py), and on this bench (2026-10-12) the
analysis station has the pump and the Z stage but no hotplate yet. The
synthesis-to-analysis scenario needs cell5's Z and pump over ``/v1``, so
this builds the same ``PumpZThermalCell`` from the same ``cell5.toml``
through the same ``create_app(cell_factory=...)`` hook, with the hotplate
replaced by an object that REFUSES every call. Nothing is faked: diagnose
reports the hotplate as not ok with that refusal, status reports its
temperatures as None, and the heater/stirrer routes answer with the same
error. The pump and Z routes are the repository's own.

Two deliberate deviations from ``open()``:

* the Z instance's ``_max_travel_mm`` is raised from the driver's class
  default (400) to ``--max-travel`` (450). cell5's lower end-stop sits
  ~422.6 mm below the upper (home) switch (measured 2026-10-12), so the
  dispense point at 421 mm is unreachable otherwise. Instance only; the
  driver (a submodule) is not edited.
* the smart plugs are opened exactly as ``open()`` does
  (``SmartPlugController.from_files()``, credentials in
  external/SmartPlugController/secure.env); when that fails the cell
  still serves with ``plug=None`` and the lamp/fan routes answer 409.
  The ``[lamp]`` / ``[fan]`` targets are read from the same cell5.toml
  (2026-10-12: fan on plug2 192.168.0.77 for
  scenarios/demo_cell5_fan_blink.yaml, issue #39).

    cd ~/workspace/InnoCOREServer/InnoCORESDL_Sungwoo          # NUC2
    .venv/bin/python claude_test/test_cell5_server_shinyeong.py \\
        --config server/nuc2/cell5.toml

One owner per device (CLAUDE.md folder rule 2): stop any direct-drive tool
on NTB3EP5R or the pump first. The operator holds the e-stop — the Z has
end-stops but no software stop (GAP-9).
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cell.cell_protocol import DeviceFaultError  # noqa: E402
from cell.pump_z_thermal_cell import PumpZThermalCell, PumpZThermalConfig  # noqa: E402
from external.HotplateController.hotplate_controller import (  # noqa: E402
    RctDigital,
    find_rct_port,
)
from external.SmartPlugController.smartplugcontroller import (  # noqa: E402
    ControllerError,
    SmartPlugController,
)
from mks_motor import MKSMotor, prepare_usb_nodes, release_ftdi_sio  # noqa: E402
from server.app import create_app  # noqa: E402
from sy01b import SyringePumpController  # noqa: E402

DEFAULT_CONFIG = Path("server/nuc2/cell5.toml")


def _load(path: Path) -> tuple[PumpZThermalConfig, dict]:
    """The [pump]/[zstage]/[server] subset of server.__main__._load_pump_z_thermal.

    Re-implemented here because importing ``server.__main__`` drags in
    ``cell.balance_linear_cell``, which needs a LinearMotorController newer
    than NUC2's checkout pins (``LinkDroppedError``); the gantry launcher
    avoids that import for the same reason.
    """
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    pump, zstage = raw.get("pump", {}), raw.get("zstage", {})
    lamp, fan = raw.get("lamp", {}), raw.get("fan", {})
    cfg = PumpZThermalConfig(
        pump_enabled="pump" in raw,
        pump_port=pump.get("port", "1A86:7523"),
        pump_address=int(pump.get("address", 1)),
        pump_baud=int(pump.get("baud", 9600)),
        syringe_uL=int(pump.get("syringe_uL", 125)),
        pump_init_force=int(pump.get("init_force", 2)),
        z_serial=zstage.get("serial"),
        z_coord_invert=bool(zstage.get("coord_invert", True)),
        home_dir_z=int(zstage.get("home_dir", 0)),
        # Same defaults as server.__main__: no [fan] table = no fan
        # (fan routes answer 409); the cell refuses a fan target that
        # is "all" or equals the lamp's.
        hotplate_port=raw.get("hotplate", {}).get("port"),
        max_celsius=float(raw.get("hotplate", {}).get("max_celsius", 60.0)),
        lamp_target=lamp.get("target", "all"),
        fan_target=fan.get("target"),
    )
    return cfg, raw.get("server", {})


class AbsentHotplateError(RuntimeError):
    """Raised by every AbsentHotplate call."""


class AbsentHotplate:
    """Stands in for the IKA RCT digital that is not on the bench.

    Every read or command raises ``AbsentHotplateError``, so nothing
    upstream can mistake it for a measurement; the two stop calls succeed,
    because there is nothing to stop and ``PumpZThermalCell.stop()`` must
    still stop the Z and the pump.
    """

    port = "absent"

    def __getattr__(self, name: str):
        if name in ("stop_heater", "stop_motor"):
            return lambda: None

        def refuse(*_args, **_kwargs):
            raise AbsentHotplateError(
                f"hotplate absent on this bench (cell5 served by "
                f"test_cell5_server_shinyeong.py); {name} refused"
            )

        return refuse


class Cell5WithoutHotplate(PumpZThermalCell):
    """PumpZThermalCell whose hotplate reads report 'absent' instead of 500.

    ``_hotplate_call`` in this checkout turns every driver error into a
    CellError, so a missing hotplate would take ``status`` and ``diagnose``
    down with it. Here the two READ paths answer honestly — ``diagnose``
    gets an absent-by-configuration entry like the balance's, ``status``
    gets None — and every heater/stirrer COMMAND still fails, so no route
    can report a success that did not happen.
    """

    def _hotplate_call(self, command: str, fn):  # noqa: ANN001 — matches the parent
        try:
            return super()._hotplate_call(command, fn)
        except AbsentHotplateError as exc:
            if command == "diagnose":
                return {"present": False, "ok": False, "error": str(exc)}
            if command == "status":
                return None
            raise DeviceFaultError(str(exc), command=command) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Serve cell5 without the hotplate."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--max-travel", type=float, default=450.0)
    args = parser.parse_args(argv)
    if not args.config.exists():
        parser.error(f"config file not found: {args.config}")
    cfg, server = _load(args.config)

    def factory() -> Cell5WithoutHotplate:
        pump = None
        if cfg.pump_enabled:
            pump = SyringePumpController.open(
                SyringePumpController.Config(
                    port=cfg.pump_port,
                    address=cfg.pump_address,
                    baud=cfg.pump_baud,
                    syringe_uL=cfg.syringe_uL,
                    step_mode=SyringePumpController.StepMode.NORMAL,
                )
            )
            print(
                f"cell5 pump: serial {pump.query_serial_number()} at {cfg.pump_port}"
            )
        prepare_usb_nodes()
        release_ftdi_sio()
        z = MKSMotor.open(serial=cfg.z_serial, coord_invert=cfg.z_coord_invert)
        if not z.setup():
            raise SystemExit(
                "cell5 Z motor setup failed; check motor power / CAN"
            )
        z._max_travel_mm = args.max_travel  # noqa: SLF001 — see the module docstring
        mm = z.read_position_mm()
        if mm is None:
            raise SystemExit(
                "cell5 Z did not answer an encoder read; refusing to serve"
            )
        print(
            f"cell5 Z: {cfg.z_serial} reads {mm:.3f} mm, max travel {args.max_travel:g} mm"
        )
        # Plugs as in PumpZThermalCell.open(): missing credentials must
        # not keep the pump and Z down, so fall back to plug=None (409s).
        try:
            plug: SmartPlugController | None = SmartPlugController.from_files()
        except ControllerError as exc:
            print(f"warning: smart plugs unavailable: {exc}", file=sys.stderr)
            plug = None
        else:
            print(
                f"cell5 plugs: lamp={cfg.lamp_target!r} fan={cfg.fan_target!r}"
            )
        # 2026-10-13: use the real IKA RCT when it is on the bench (it is, since
        # 2026-10-12), exactly as PumpZThermalCell.open() does; fall back to the
        # refusing stub only when no hotplate is found. The Z travel override
        # above is the one thing open() cannot give us.
        port = cfg.hotplate_port or find_rct_port()
        if port is not None:
            hotplate = RctDigital(port)
            original = float(hotplate.read_target_temperature())
            print(
                f"cell5 hotplate: IKA RCT at {port}, stored target {original:g} C, cap {cfg.max_celsius:g} C"
            )
            return PumpZThermalCell(pump, z, hotplate, plug, cfg, original)
        print(
            "cell5 hotplate: NOT found — serving with the refusing stub",
            file=sys.stderr,
        )
        return Cell5WithoutHotplate(pump, z, AbsentHotplate(), plug, cfg, 0.0)

    print(
        f"cell5 — pump + Z (hotplate ABSENT), "
        f"serving on port {server.get('port', 17062)}. Keep the e-stop in reach."
    )
    uvicorn.run(
        create_app(cell_factory=factory),
        host=server.get("host", "0.0.0.0"),
        port=int(server.get("port", 17062)),
        log_level=server.get("log_level", "info"),
        timeout_keep_alive=120,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
