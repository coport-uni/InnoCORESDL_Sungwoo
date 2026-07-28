"""Real Cell D (cell5): pump + single Z stage + hotplate + IR lamp.

Composition confirmed with the user on 2026-07-27 — four devices in one
cell, one server, one owner:

======================  ==========================================
Syringe pump            ``sy01b`` (same driver + code path as cell1–3)
Z stage, 1 motor        ``mks_motor``, ONE MKS SERVO57D over FTDI/CAN
Hotplate                IKA RCT digital (``external/HotplateController``)
IR lamp                 Tapo plug (``external/SmartPlugController``)
======================  ==========================================

Differences from the other cells that matter:

- **One Z axis, no X.** The gantry action set is not reused — its
  signature carries an X target and its motion runs through the paired-Z
  desync interlock, neither of which exists here. The motion still goes
  through the driver's *group* helpers (``home_sync`` / ``move_sync`` /
  ``stop_group_hard``) with a one-motor group, because those are what run
  the ``_is_at_limit()`` pre-send that absorbs the MKS firmware's "first
  motion command after a limit stop is dropped" quirk (CLAUDE.md
  Folder-specific rules #3).
- **This cell heats.** ``stop()`` therefore stops the motor *and* the
  heater *and* the lamp — an e-stop that left a heater running would be
  worse than useless. Every one of the four devices is attempted even if
  an earlier one fails.
- **The lamp is network-attached** (python-kasa over the LAN), not
  serial. Its credentials live in
  ``external/SmartPlugController/secure.env``, written by the operator.
  When they are absent the cell still opens — pump, Z and hotplate work
  and the lamp methods raise 409.
- ``heating`` / ``stirring`` are the **last commanded state**: the RCT
  digital protocol has no readback for either.

Hardware-verified at the bench, not in CI.
"""

from __future__ import annotations

import asyncio
import ipaddress
import sys
from dataclasses import dataclass

from mks_motor import MKSMotor, prepare_usb_nodes, release_ftdi_sio
from sy01b import SyringePumpController

from external.HotplateController.hotplate_controller import (
    RctCommError,
    RctDigital,
    RctError,
    RctRangeError,
    find_rct_port,
)
from external.SmartPlugController.smartplugcontroller import (
    ControllerError,
    DeviceEntry,
    SmartPlugController,
)

from .cell_protocol import (
    Cell,
    DeviceFaultError,
    InvalidArgError,
    TransportError,
    WrongStateError,
)

#: Bench ceiling for the hotplate setpoint. The driver enforces the
#: device's own limits; this is the stricter, cell-level one an
#: orchestrated run must not exceed. Override per bench in the config.
DEFAULT_MAX_CELSIUS = 150.0


@dataclass(frozen=True, slots=True)
class PumpZThermalConfig:
    """Bench wiring for Cell D (loaded from the cell5 TOML)."""

    # Pump — identical to a dispense cell. `pump_enabled = False` (an
    # omitted [pump] table) builds the cell without one: the bench ran
    # Cell D's Z + hotplate + lamp before its syringe pump arrived, and a
    # missing pump must not keep the other three devices down. The pump
    # endpoints then answer 409 exactly like the absent balance does.
    pump_enabled: bool = True
    pump_port: str = "1A86:7523"
    pump_address: int = 1
    pump_baud: int = 9600
    syringe_uL: int = 125
    pump_init_force: int = 2
    # Z stage: FTDI serial of the single adapter. None opens the first
    # enumerated FTDI device — only safe when it is the sole adapter.
    z_serial: str | None = None
    # +mm moves away from the 0x00 home limit, as on the gantry cells.
    z_coord_invert: bool = True
    home_dir_z: int = 0x00
    # Hotplate: None auto-detects the RCT digital by USB VID:PID.
    hotplate_port: str | None = None
    max_celsius: float = DEFAULT_MAX_CELSIUS
    # Lamp: device name or IP as written in the plug driver's
    # device_list.md. "all" would switch every plug on the bench.
    # A bare IP that the list does not mention is still addressable: the
    # cell synthesises the entry (see _resolve_lamp). That keeps a bench
    # re-IP out of external/, which is a submodule and must not carry
    # local-only edits (CLAUDE.md Overview).
    lamp_target: str = "all"


def _no_balance() -> WrongStateError:
    # Defensive stub: Cell D has no balance (the Phase's single balance
    # lives on cell4), so a stray /v1/balance/* call gets a clean 409.
    return WrongStateError("Cell D has no balance", command="balance")


def _no_pump() -> WrongStateError:
    # Not "broken" but "not fitted": this cell was configured with
    # pump_enabled = False, so every pump route answers 409 rather than
    # failing later against a port that was never opened.
    return WrongStateError("Cell D has no pump configured", command="pump")


def _no_gantry() -> WrongStateError:
    # Defensive stub: motion here is one Z axis (the zstage action set).
    # A /v1/gantry/* call carries an X target this cell cannot honour.
    return WrongStateError(
        "Cell D has no XZ gantry; use the zstage actions", command="gantry"
    )


def _no_linear() -> WrongStateError:
    # Defensive stub: the linear Y rail belongs to cell4.
    return WrongStateError("Cell D has no linear rail", command="linear")


def _no_lamp() -> WrongStateError:
    # Not "absent hardware" but "unconfigured": the plug needs
    # credentials in the driver's secure.env, written by the operator.
    return WrongStateError(
        "lamp plug is not configured (see SmartPlugController/secure.env)",
        command="lamp",
    )


class PumpZThermalCell(Cell):
    """cell5 = pump + single Z + hotplate + IR lamp, behind ``Cell``."""

    def __init__(
        self,
        pump: SyringePumpController | None,
        z: MKSMotor,
        hotplate: RctDigital,
        plug: SmartPlugController | None,
        config: PumpZThermalConfig,
        original_setpoint_c: float,
    ) -> None:
        self._pump = pump
        self._z = z
        self._hotplate = hotplate
        self._plug = plug
        self._cfg = config
        self._original_setpoint_c = original_setpoint_c
        self._plunger_uL = 0.0
        self._z_mm = 0.0
        self._initialized = False
        # Last commanded state — the RCT protocol has no readback.
        self._heating = False
        self._stirring = False
        self._lamp_on: bool | None = None

    @classmethod
    def open(cls, config: PumpZThermalConfig) -> PumpZThermalCell:
        pump: SyringePumpController | None = None
        if config.pump_enabled:
            pump_cfg = SyringePumpController.Config(
                port=config.pump_port,
                address=config.pump_address,
                baud=config.pump_baud,
                syringe_uL=config.syringe_uL,
                step_mode=SyringePumpController.StepMode.NORMAL,
                reply_timeout_s=2.0,
            )
            pump = SyringePumpController.open(pump_cfg)
        # Docker's /dev is a private tmpfs and ftdi_sio re-claims the FTDI
        # adapter on every enumeration; rebuild the nodes and detach it so
        # pyftdi can open the USB2CAN adapter. No motion, FTDI only.
        prepare_usb_nodes()
        release_ftdi_sio()
        if config.z_serial is not None:
            z = MKSMotor.open(
                serial=config.z_serial, coord_invert=config.z_coord_invert
            )
        else:
            z = MKSMotor.open(coord_invert=config.z_coord_invert)
        # SR_vFOC + active-response before any motion; without it the
        # firmware ignores later home/move commands or never replies.
        if not z.setup():
            raise DeviceFaultError(
                "Z motor setup failed; check the CAN wiring", command="zstage"
            )
        port = config.hotplate_port or find_rct_port()
        if port is None:
            raise TransportError(
                "IKA RCT digital not found by USB VID:PID; set "
                "[hotplate] port explicitly",
                command="hotplate",
            )
        hotplate = RctDigital(port)
        original = float(hotplate.read_target_temperature())
        # A missing plug config must not keep the whole cell down: the
        # lamp methods raise 409 and diagnose reports it as absent.
        try:
            plug: SmartPlugController | None = SmartPlugController.from_files()
        except ControllerError as exc:
            print(f"warning: lamp plug unavailable: {exc}", file=sys.stderr)
            plug = None
        return cls(pump, z, hotplate, plug, config, original)

    # ── Discovery ───────────────────────────────────────────────────────
    def diagnose(self) -> dict:
        if self._pump is not None:
            report = self._pump.diagnose()
            pump = {
                "software_version": report.software_version,
                "serial_number": report.serial_number,
                "config": report.config,
                "supply_volts": report.supply_volts,
                "valve": report.valve_position,
                "ok": report.ok_to_initialize,
            }
            ok_to_initialize = report.ok_to_initialize
            versions = {"pump": report.software_version}
        else:
            # Absent by configuration, like the balance: ok=True so the
            # cell is not reported faulted for hardware it never had.
            pump = {"present": False, "ok": True}
            ok_to_initialize = True
            versions = {}
        hotplate = self._hotplate_call(
            "diagnose",
            lambda: {
                "name": self._hotplate.read_name(),
                "port": self._hotplate.port,
                "target_c": self._hotplate.read_target_temperature(),
                "safety_c": self._hotplate.read_safety_temperature(),
                "max_c": self._cfg.max_celsius,
                "ok": True,
            },
        )
        return {
            "pump": pump,
            # No balance on Cell D; ok=True so the cell isn't faulted.
            "balance": {"present": False, "ok": True},
            "stage": {  # the single Z axis
                "axes": 1,
                "serial": self._cfg.z_serial,
                "ok": True,
            },
            "hotplate": hotplate,
            "lamp": self._lamp_diagnosis(),
            "ok_to_initialize": ok_to_initialize,
            "versions": versions,
        }

    def _lamp_diagnosis(self) -> dict:
        if self._plug is None:
            return {"present": False, "ok": False, "error": "not configured"}
        entries = self._resolve_lamp()
        return {
            "present": True,
            "target": self._cfg.lamp_target,
            "devices": [e.name for e in entries],
            "ok": bool(entries),
        }

    def status(self) -> dict:
        return {
            "weight_g": 0.0,  # no balance on this cell
            "valve": (
                self._pump.query_valve_position()
                if self._pump is not None
                else "-"  # no pump fitted
            ),
            "plunger_uL": self._plunger_uL,
            "stage_x_mm": 0.0,  # no X axis on Cell D
            "stage_z_mm": self._z_mm,
            "busy": False,
            "error": None,
            "hotplate_c": self._hotplate_call(
                "status", lambda: float(self._hotplate.read_plate_temperature())
            ),
            "hotplate_target_c": self._hotplate_call(
                "status",
                lambda: float(self._hotplate.read_target_temperature()),
            ),
            "heating": self._heating,
            "stirring": self._stirring,
            "lamp_on": self._lamp_on,
        }

    # ── Balance (none on Cell D) ────────────────────────────────────────
    def tare(self) -> float:
        raise _no_balance()

    def calibrate(self) -> float:
        raise _no_balance()

    def read_weight(self) -> tuple[float, bool]:
        raise _no_balance()

    def set_ambient(self, level: str) -> str:
        raise _no_balance()

    # ── Pump (same contract as cell1–3) ─────────────────────────────────
    def initialize(self, *, force: int = 2, ccw: bool = False) -> dict:
        if self._pump is None:
            raise _no_pump()
        self._pump.initialize(force=force, ccw=ccw)
        self._initialized = True
        self._plunger_uL = 0.0
        return {"valve": self._pump.query_valve_position(), "plunger_uL": 0.0}

    def _require_init(self) -> None:
        if self._pump is None:
            raise _no_pump()
        if not self._initialized:
            raise WrongStateError("pump not initialized", command="initialize")

    def move_valve(self, port: int) -> str:
        self._require_init()
        self._pump.move_valve_to_port(port)
        return self._pump.query_valve_position()

    def aspirate(self, target_uL: float) -> float:
        self._require_init()
        self._pump.aspirate_uL(target_uL)
        self._plunger_uL = float(target_uL)
        return self._plunger_uL

    def dispense(self, target_uL: float = 0.0) -> float:
        self._require_init()
        self._pump.dispense_uL(target_uL)
        self._plunger_uL = float(target_uL)
        return self._plunger_uL

    def cycle(
        self,
        *,
        cycles: int,
        volume_uL: float,
        source_port: int,
        dispense_port: int,
    ) -> dict:
        self._require_init()
        for _ in range(cycles):
            self._pump.move_valve_to_port(source_port)
            self._pump.aspirate_uL(volume_uL)
            self._pump.move_valve_to_port(dispense_port)
            self._pump.dispense_uL(0)
        self._plunger_uL = 0.0
        return {
            "cycles_done": cycles,
            "final_valve": self._pump.query_valve_position(),
        }

    # ── Gantry / linear (neither exists on Cell D) ──────────────────────
    def home_gantry(self) -> tuple[float, float]:
        raise _no_gantry()

    def move_gantry(
        self, x_mm: float, z_mm: float, *, speed_pct: int, accel_pct: int
    ) -> tuple[float, float]:
        raise _no_gantry()

    def home_linear(self) -> float:
        raise _no_linear()

    def move_linear(self, y_mm: float) -> float:
        raise _no_linear()

    # ── Z stage (one motor) ─────────────────────────────────────────────
    # Motion goes through the driver's group helpers with a one-motor
    # group, never MKSMotor._send: only those run the _is_at_limit()
    # pre-send that absorbs the firmware's first-command-after-limit drop.
    def home_zstage(self) -> float:
        MKSMotor.home_sync([self._z], direction=self._cfg.home_dir_z)
        self._z_mm = 0.0
        return self._z_mm

    def move_zstage(
        self, z_mm: float, *, speed_pct: int, accel_pct: int
    ) -> float:
        MKSMotor.move_sync([self._z], [(z_mm, speed_pct, accel_pct)])
        self._z_mm = float(z_mm)
        return self._z_mm

    # ── Hotplate ────────────────────────────────────────────────────────
    def _hotplate_call(self, command: str, fn):
        """Run a driver call, translating ``RctError`` to a CellError.

        Args:
            command: Name reported in the error envelope.
            fn: Zero-argument driver call.

        Returns:
            Whatever ``fn`` returns.

        Raises:
            InvalidArgError: Value outside the device's accepted range.
            TransportError: The serial link failed.
            DeviceFaultError: Any other hotplate fault.
        """
        try:
            return fn()
        except RctRangeError as exc:
            raise InvalidArgError(str(exc), command=command) from exc
        except RctCommError as exc:
            raise TransportError(str(exc), command=command) from exc
        except RctError as exc:
            raise DeviceFaultError(str(exc), command=command) from exc

    def read_hotplate(self) -> dict:
        return self._hotplate_call(
            "hotplate",
            lambda: {
                "plate_c": float(self._hotplate.read_plate_temperature()),
                "probe_c": float(self._hotplate.read_probe_temperature()),
                "target_c": float(self._hotplate.read_target_temperature()),
                "safety_c": float(self._hotplate.read_safety_temperature()),
                "rpm": float(self._hotplate.read_speed()),
                "target_rpm": float(self._hotplate.read_target_speed()),
                "heating": self._heating,
                "stirring": self._stirring,
                "max_c": self._cfg.max_celsius,
            },
        )

    def set_hotplate_temperature(self, celsius: float) -> float:
        if celsius > self._cfg.max_celsius:
            raise InvalidArgError(
                f"{celsius} °C exceeds this cell's ceiling of "
                f"{self._cfg.max_celsius} °C",
                command="hotplate",
            )
        self._hotplate_call(
            "hotplate", lambda: self._hotplate.set_target_temperature(celsius)
        )
        return float(celsius)

    def set_hotplate_heater(self, *, enabled: bool) -> dict:
        self._hotplate_call(
            "hotplate",
            lambda: (
                self._hotplate.start_heater()
                if enabled
                else self._hotplate.stop_heater()
            ),
        )
        self._heating = enabled
        return {
            "heating": self._heating,
            "target_c": self._hotplate_call(
                "hotplate",
                lambda: float(self._hotplate.read_target_temperature()),
            ),
        }

    def set_hotplate_speed(self, rpm: float) -> float:
        self._hotplate_call(
            "hotplate", lambda: self._hotplate.set_target_speed(rpm)
        )
        return float(rpm)

    def set_hotplate_stirrer(self, *, enabled: bool) -> dict:
        self._hotplate_call(
            "hotplate",
            lambda: (
                self._hotplate.start_motor()
                if enabled
                else self._hotplate.stop_motor()
            ),
        )
        self._stirring = enabled
        return {
            "stirring": self._stirring,
            "target_rpm": self._hotplate_call(
                "hotplate", lambda: float(self._hotplate.read_target_speed())
            ),
        }

    # ── Lamp (IR lamp on a Tapo plug) ───────────────────────────────────
    # python-kasa is async; the server already runs these methods in a
    # worker thread, so that thread has no running loop and asyncio.run
    # is safe here (same pattern the old demo_scenario used).
    def _resolve_lamp(self) -> list[DeviceEntry]:
        """Match ``lamp_target`` against the list, or synthesise by IP.

        The driver's ``device_list.md`` lives inside the submodule, which
        must carry no local-only edits, so a bench whose plug got a new
        DHCP address configures the bare IP as ``lamp_target`` and the
        cell builds the entry itself.

        Returns:
            Matching entries; empty when the target names nothing.
        """
        assert self._plug is not None
        entries = self._plug.resolve_targets(self._cfg.lamp_target)
        if entries:
            return entries
        try:
            ipaddress.ip_address(self._cfg.lamp_target)
        except ValueError:
            return []
        return [
            DeviceEntry(
                device_type="tapo",
                name=self._cfg.lamp_target,
                mac="",
                ip=self._cfg.lamp_target,
            )
        ]

    def _lamp_entries(self):
        if self._plug is None:
            raise _no_lamp()
        entries = self._resolve_lamp()
        if not entries:
            raise WrongStateError(
                f"no plug matches {self._cfg.lamp_target!r} in device_list.md",
                command="lamp",
            )
        return entries

    def read_lamp(self) -> dict:
        entries = self._lamp_entries()

        async def _read_targets():
            # Only this cell's plug(s) — read_all() would poll every
            # device in the driver's list, including other benches'.
            return await asyncio.gather(
                *(self._plug.read(entry) for entry in entries)
            )

        try:
            mine = asyncio.run(_read_targets())
        except (ControllerError, OSError) as exc:
            raise TransportError(str(exc), command="lamp") from exc
        failed = [r for r in mine if not r.ok]
        if failed:
            raise TransportError(
                "; ".join(f"{r.entry.name}: {r.error}" for r in failed),
                command="lamp",
            )
        self._lamp_on = bool(mine and all(r.is_on for r in mine))
        return {
            "is_on": self._lamp_on,
            "target": self._cfg.lamp_target,
            "devices": [r.entry.name for r in mine],
        }

    def set_lamp(self, *, enabled: bool) -> dict:
        entries = self._lamp_entries()
        try:
            results = asyncio.run(
                self._plug.switch_many(entries, turn_on=enabled)
            )
        except (ControllerError, OSError) as exc:
            raise TransportError(str(exc), command="lamp") from exc
        failed = [r for r in results if not r.ok]
        if failed:
            raise DeviceFaultError(
                "lamp switch failed: "
                + "; ".join(f"{r.entry.name}: {r.error}" for r in failed),
                command="lamp",
            )
        self._lamp_on = enabled
        return {
            "is_on": self._lamp_on,
            "target": self._cfg.lamp_target,
            "devices": [r.entry.name for r in results],
        }

    # ── Safety / lifecycle ──────────────────────────────────────────────
    def stop(self) -> None:
        """Stop everything: Z motor, pump, heater, lamp.

        Every device is attempted even when an earlier one fails — a stop
        that gave up halfway could leave the heater running. The first
        failure is reported afterwards.

        Raises:
            DeviceFaultError: One or more devices did not stop.
        """
        failures: list[str] = []
        try:
            MKSMotor.stop_group_hard([self._z])
        except Exception as exc:  # noqa: BLE001 — surface as a device fault
            failures.append(f"zstage: {exc}")
        halt = getattr(self._pump, "halt", None) or getattr(
            self._pump, "stop", None
        )
        if callable(halt):
            try:
                halt()
            except Exception as exc:  # noqa: BLE001
                failures.append(f"pump: {exc}")
        try:
            self._hotplate.stop_heater()
            self._heating = False
        except Exception as exc:  # noqa: BLE001
            failures.append(f"hotplate: {exc}")
        try:
            self._hotplate.stop_motor()
            self._stirring = False
        except Exception as exc:  # noqa: BLE001
            failures.append(f"stirrer: {exc}")
        if self._plug is not None:
            try:
                self.set_lamp(enabled=False)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"lamp: {exc}")
        if failures:
            raise DeviceFaultError(
                "stop incomplete: " + "; ".join(failures), command="stop"
            )

    def close(self) -> None:
        # Heater off and the operator's setpoint restored before the port
        # is released — the cell must not leave the plate hot or retuned.
        try:
            self._hotplate.stop_heater()
            self._hotplate.stop_motor()
            self._hotplate.set_target_temperature(self._original_setpoint_c)
        except Exception:  # noqa: BLE001 — best-effort shutdown
            print("warning: hotplate shutdown failed", file=sys.stderr)
        for dev in (self._z, self._pump, self._hotplate):
            fn = getattr(dev, "close", None)
            if callable(fn):
                try:
                    fn()
                except Exception:  # noqa: BLE001 — best-effort shutdown
                    print(
                        f"warning: {type(dev).__name__}.close failed",
                        file=sys.stderr,
                    )
