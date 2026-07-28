"""Real pump+gantry cell (cell1): pump (``sy01b``) + XZ gantry (ESP32 ``mks_motor``).

A dispensing cell has **no balance** — the Phase's single balance lives on
cell4 (see ``balance_linear_cell.py``), so the balance methods of the :class:`cell_protocol.Cell`
protocol raise here. The XZ gantry is the three MKS SERVO57D motors driven by
the ESP32 ``mks_motor`` driver (paired-Z safety interlock, pyftdi), addressed
by FTDI serial — vendored from **ESP32S3BOX3MotorController** (the sole XZ
gantry reference; see ``external/VENDORED.md``).

Gantry calls mirror that repo's ``bridge.py`` (``open_xz`` + ``move_sync`` +
``home_xz``). Motion order is up → X → down (never diagonal). Hardware-verified
at the bench, not in CI.

The pump is **optional**: leave ``pump_port`` unset (omit the config's
``[pump]`` table) and the cell serves the gantry alone, with the pump action
set raising like any other absent device. That is not a degraded mode — it is
how cell1 runs while its pump is off the bench (its USB link flaps
independently of this cell; see ToDo.md), and it keeps the gantry usable
instead of blocking the whole cell on a device no step needs.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from mks_motor import MKSMotor, prepare_usb_nodes, release_ftdi_sio
from sy01b import SyringePumpController

from .cell_protocol import (
    Cell,
    DeviceFaultError,
    TransportError,
    WrongStateError,
)


@dataclass(frozen=True, slots=True)
class Config:
    """Bench wiring for a dispense cell (loaded from ics.toml)."""

    # None → this cell has no pump (gantry only); the pump action set 409s.
    pump_port: str | None = "1A86:7523"
    pump_address: int = 1
    pump_baud: int = 9600
    syringe_uL: int = 125
    pump_init_force: int = 2
    # XZ gantry: FTDI serial of the X adapter; the other two adapters are the
    # paired Z (order doesn't matter — they always move together). The default
    # is this bench's X adapter, the same serial bridge.py and CVMeasure.py
    # name; run CAN2USBAdapterDeviceRecognition.py to read it off a new one.
    motor_serial_x: str = "NTAMU6TO"
    # Both axes home at the 0x00 end and move +mm into the working travel via
    # coord_invert (their encoder-positive points into the home limit). This
    # is the uniform convention; the legacy CVMeasure.py instead homed X the
    # opposite way (home_dir_x=0x01, no invert) — equivalent, but asymmetric.
    z_coord_invert: bool = True
    x_coord_invert: bool = True
    home_dir_z: int = 0x00
    home_dir_x: int = 0x00


def _no_balance() -> WrongStateError:
    # Defensive stub: this cell has no balance, but the `Cell` protocol
    # requires every method, so the balance methods raise this instead of
    # crashing. In normal use the web reads `diagnose()` balance.present=false
    # and greys those controls out — this only fires on a stray/buggy call
    # (→ HTTP 409). The stage methods, by contrast, ARE implemented (they
    # drive the gantry); only the balance group is absent here.
    return WrongStateError("pump+gantry cell has no balance", command="balance")


def _no_linear() -> WrongStateError:
    # Defensive stub: motion here is the XZ gantry (gantry action set), not a
    # linear rail — the linear methods raise so a misdirected /v1/linear/* call
    # gets a clean 409 instead of hitting the gantry.
    return WrongStateError(
        "pump+gantry cell has no linear rail", command="linear"
    )


#: How far the two paired Z motors may disagree before the gantry is
#: considered racking. They are mechanically coupled, so any real divergence
#: is the failure `move_sync`'s interlock exists to prevent — but the
#: interlock only fires on a CAN fault, and a motor that silently drops a
#: motion command desyncs without raising. Reading both encoders is the only
#: check that catches that case. Measured on this bench 2026-07-28 over
#: home + four 50 mm moves: the pair's worst disagreement was 0.020 mm
#: (0.000 mm at 50 mm depth), so this is ~25x the observed worst.
Z_DESYNC_LIMIT_MM = 0.5

#: How far an axis may sit from its commanded target and still count as
#: arrived. These are closed-loop FOC servos on a ball screw, so the settled
#: error is far below this; the tolerance exists to catch a move that was
#: dropped or refused outright, not to grade positioning accuracy — a
#: dropped 50 mm move misses by 50 mm, not by half of one.
#: Measured on this bench 2026-07-28 across four completed scenario runs
#: (20 waypoints, reaching 150 mm): worst residual **0.145 mm**, read
#: immediately after the move returned — the servo then closes the rest, so
#: a follow-up /v1/status read of the same waypoint lands within 0.001 mm
#: of target. That leaves ~3.5x margin here.
#: An earlier note claimed 0.038 mm and ~13x; that came from a single
#: manual 50 mm move and understated the spread by about four times. One
#: measurement is not a distribution — the number only became trustworthy
#: once three repeats of the same scenario disagreed with each other.
ARRIVAL_TOLERANCE_MM = 0.5


def _no_pump() -> WrongStateError:
    # Defensive stub used when `pump_port` is unset — cell1 running its
    # gantry with the pump off the bench. Mirror of BalanceLinearCell's
    # `_no_pump`: the web greys the pump controls out from `diagnose()`
    # pump.present=false, so this only fires on a stray call (→ 409).
    return WrongStateError(
        "this pump+gantry cell was configured without a pump", command="pump"
    )


def _absent(family: str) -> WrongStateError:
    # Defensive stub for the action sets only Cell 5 (cell5) has: the
    # single Z stage, the hotplate and the IR lamp. Same contract as the
    # stubs above — a stray call gets 409, not an AttributeError.
    return WrongStateError(f"pump+gantry cell has no {family}", command=family)


class PumpGantryCell(Cell):
    """cell1 = syringe pump + XZ gantry, behind :class:`cell_protocol.Cell`."""

    def __init__(
        self,
        pump: SyringePumpController | None,
        za: MKSMotor,
        zb: MKSMotor,
        x: MKSMotor,
        config: Config,
    ) -> None:
        self._pump = pump
        self._x = x
        self._z_motors = [za, zb]
        self._cfg = config
        self._plunger_uL = 0.0
        self._stage_x_mm = 0.0
        self._stage_z_mm = 0.0
        self._initialized = False

    @classmethod
    def open(cls, config: Config) -> PumpGantryCell:
        pump = None
        if config.pump_port:
            pump_cfg = SyringePumpController.Config(
                port=config.pump_port,
                address=config.pump_address,
                baud=config.pump_baud,
                syringe_uL=config.syringe_uL,
                step_mode=SyringePumpController.StepMode.NORMAL,
                reply_timeout_s=2.0,
            )
            pump = SyringePumpController.open(pump_cfg)
        # Docker /dev is a private tmpfs and ftdi_sio re-claims the FTDI
        # adapters on every enumeration; rebuild the USB nodes and detach
        # ftdi_sio so pyftdi can open the USB2CAN adapters (mirrors bridge.py).
        # Both are no-ops on non-Linux and touch only FTDI adapters — no motion.
        prepare_usb_nodes()
        release_ftdi_sio()
        # Opens all three USB2CAN adapters by serial (X explicit, two Z auto).
        za, zb, x = MKSMotor.open_xz(
            config.motor_serial_x, z_coord_invert=config.z_coord_invert
        )
        # open_xz inverts only the Z pair; X uses the same convention as Z
        # (+mm away from the 0x00-home end), so set its invert here too.
        x.coord_invert = config.x_coord_invert
        # Put every motor into SR_vFOC + active-response before any motion
        # (mirrors bridge.py). open_xz only opens the adapters; without this
        # the firmware ignores subsequent home/move commands or never replies.
        for motor in (za, zb, x):
            motor.setup()
        return cls(pump, za, zb, x, config)

    # ── Discovery ───────────────────────────────────────────────────────
    @staticmethod
    def _axis_mm(motor: MKSMotor) -> float | None:
        """One axis' encoder position in mm, or None if it did not answer.

        Both failure shapes of ``read_position_mm`` collapse to None here —
        a stray/mismatched CAN frame (returns None) and a motor that never
        replied (raises ConnectionError). Callers must treat None as "not
        reached", never as a position.
        """
        try:
            return motor.read_position_mm()
        except ConnectionError:
            return None

    def _read_axes(self) -> tuple[float | None, float | None, float | None]:
        """Live (x, z_a, z_b) encoder positions in mm; None where unread."""
        return (
            self._axis_mm(self._x),
            self._axis_mm(self._z_motors[0]),
            self._axis_mm(self._z_motors[1]),
        )

    def diagnose(self) -> dict:
        # `ok` is derived from a live read of every motor, never asserted.
        # This block used to be a hardcoded `"ok": True`, which made
        # diagnose() answer "healthy" for a gantry whose adapters were open
        # but whose motors were unpowered or off the CAN bus — the same
        # fabricated-success bug removed from cell4's diagnose() and from
        # the motion path (LearnedPatterns #15). Reading the encoder is the
        # cheapest question that only a reachable motor can answer, and this
        # is the last gate before an operator commands a paired-Z gantry.
        x_mm, za_mm, zb_mm = self._read_axes()
        stage_ok = None not in (x_mm, za_mm, zb_mm)
        pump_block: dict = {"present": False, "ok": True}
        pump_version = None
        pump_ok = False
        if self._pump is not None:
            report = self._pump.diagnose()
            pump_version = report.software_version
            pump_ok = report.ok_to_initialize
            pump_block = {
                "present": True,
                "software_version": report.software_version,
                "serial_number": report.serial_number,
                "config": report.config,
                "supply_volts": report.supply_volts,
                "valve": report.valve_position,
                "ok": report.ok_to_initialize,
            }
        return {
            "pump": pump_block,
            # No balance on a dispense cell; ok=True so the cell isn't faulted.
            "balance": {"present": False, "ok": True},
            "stage": {  # the XZ gantry (3 MKS motors)
                "serial_x": self._cfg.motor_serial_x,
                "x_mm": x_mm,
                "z_a_mm": za_mm,
                "z_b_mm": zb_mm,
                "ok": stage_ok,
            },
            # An absent pump is not a precondition (mirrors cell4). A present
            # one is: a cell that has a pump must be able to talk to it.
            "ok_to_initialize": (
                stage_ok and (pump_ok if self._pump is not None else True)
            ),
            "versions": {"pump": pump_version},
        }

    def status(self) -> dict:
        # Live encoder reads, not the commanded target. move_gantry's cached
        # `_stage_*_mm` is what was *asked for*; MKSMotor.move_to logs and
        # returns on a failed start rather than raising, so the cache can say
        # 50 mm for a gantry that never moved. Reporting the cache here would
        # make every scenario's verify step check the request against itself.
        x_mm, za_mm, zb_mm = self._read_axes()
        error = None
        z_mm: float | None = None
        if za_mm is None or zb_mm is None:
            # Half an answer is not a position: with one Z unread the pair
            # could be racking and this is exactly where that shows up.
            error = "a Z motor did not report its position"
        else:
            z_mm = (za_mm + zb_mm) / 2
            spread = abs(za_mm - zb_mm)
            if spread > Z_DESYNC_LIMIT_MM:
                error = (
                    f"paired Z motors disagree by {spread:.2f} mm "
                    f"(limit {Z_DESYNC_LIMIT_MM} mm) — the gantry may be "
                    f"racking; do not command further motion"
                )
        if x_mm is None and error is None:
            error = "the X motor did not report its position"
        return {
            "weight_g": 0.0,  # no balance on this cell
            "valve": (
                self._pump.query_valve_position()
                if self._pump is not None
                else "-"  # no valve to report on a pumpless cell
            ),
            "plunger_uL": self._plunger_uL,
            # None (an unread axis) is reported as null, never as 0.0: "I
            # could not read the axis" and "the axis is at the origin" are
            # opposite facts, and 0.0 is the one that makes a home assert
            # pass. status() is a probe, so it reports the gap rather than
            # raising (same contract as cell4's status).
            "stage_x_mm": x_mm,
            "stage_z_mm": z_mm,
            "busy": False,
            "error": error,
        }

    # ── Balance (none on a dispense cell) ───────────────────────────────
    def tare(self) -> float:
        raise _no_balance()

    def calibrate(self) -> float:
        raise _no_balance()

    def read_weight(self) -> tuple[float, bool]:
        raise _no_balance()

    def set_ambient(self, level: str) -> str:
        raise _no_balance()

    # ── Pump (optional — absent when `pump_port` is unset) ──────────────
    def initialize(self, *, force: int = 2, ccw: bool = False) -> dict:
        pump = self._require_pump()
        pump.initialize(force=force, ccw=ccw)
        self._initialized = True
        self._plunger_uL = 0.0
        return {"valve": pump.query_valve_position(), "plunger_uL": 0.0}

    def _require_pump(self) -> SyringePumpController:
        if self._pump is None:
            raise _no_pump()
        return self._pump

    def _require_init(self) -> SyringePumpController:
        pump = self._require_pump()
        if not self._initialized:
            raise WrongStateError("pump not initialized", command="initialize")
        return pump

    def move_valve(self, port: int) -> str:
        pump = self._require_init()
        pump.move_valve_to_port(port)
        return pump.query_valve_position()

    def aspirate(self, target_uL: float) -> float:
        pump = self._require_init()
        pump.aspirate_uL(target_uL)
        self._plunger_uL = float(target_uL)
        return self._plunger_uL

    def dispense(self, target_uL: float = 0.0) -> float:
        pump = self._require_init()
        pump.dispense_uL(target_uL)
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
        pump = self._require_init()
        for _ in range(cycles):
            pump.move_valve_to_port(source_port)
            pump.aspirate_uL(volume_uL)
            pump.move_valve_to_port(dispense_port)
            pump.dispense_uL(0)
        self._plunger_uL = 0.0
        return {
            "cycles_done": cycles,
            "final_valve": pump.query_valve_position(),
        }

    # ── Stage = XZ gantry ───────────────────────────────────────────────
    # All gantry motion goes through the driver's high-level API
    # (move_sync / home_xz / stop_group_hard) and NEVER MKSMotor._send
    # directly. This is critical: those entry points run the driver's
    # _is_at_limit() check and pre-send a sacrificial command to absorb the
    # MKS-firmware quirk that drops the first motion command issued while a
    # limit switch is closed. Bypassing them would reproduce that bug.
    def _confirm(self, axis: str, target: float, command: str) -> float:
        """Read an axis back and fail loudly unless it actually arrived.

        ``MKSMotor.move_to`` prints ``[ERROR] Motor failed to start`` and
        *returns* on a rejected or unanswered F5 — it does not raise — and
        ``move_sync`` discards its return value, so without this the cell
        would report a clean 200 with the requested target for a gantry that
        never moved. Same fabricated-success failure cell4's ``_settled_mm``
        removes from the linear rail (LearnedPatterns #15/#17), and the
        stakes here are higher: this axis is a paired Z.
        """
        motors = self._z_motors if axis == "z" else [self._x]
        readings = [self._axis_mm(m) for m in motors]
        if any(mm is None for mm in readings):
            raise TransportError(
                f"{axis} motor did not answer the position read after a move "
                f"to {target} mm; the gantry's position is unknown — do not "
                f"trust the last reported value",
                command=command,
            )
        for mm in readings:
            if abs(mm - target) > ARRIVAL_TOLERANCE_MM:
                raise DeviceFaultError(
                    f"{axis} axis did not reach its target: commanded "
                    f"{target} mm, encoder reads {mm:.2f} mm",
                    command=command,
                )
        if axis == "z" and abs(readings[0] - readings[1]) > Z_DESYNC_LIMIT_MM:
            raise DeviceFaultError(
                f"paired Z motors ended {abs(readings[0] - readings[1]):.2f} "
                f"mm apart — the gantry is racking; cut power before "
                f"commanding further motion",
                command=command,
            )
        return sum(readings) / len(readings)

    def _move_z(self, target: float, sp: int, ac: int, command: str) -> None:
        MKSMotor.move_sync(self._z_motors, [(target, sp, ac)])
        self._stage_z_mm = self._confirm("z", target, command)

    def _move_x(self, target: float, sp: int, ac: int, command: str) -> None:
        MKSMotor.move_sync([self._x], [(target, sp, ac)])
        self._stage_x_mm = self._confirm("x", target, command)

    def home_gantry(self) -> tuple[float, float]:
        MKSMotor.home_xz(
            self._z_motors, self._x, self._cfg.home_dir_z, self._cfg.home_dir_x
        )
        # home() sets the encoder zero on success and only *prints* "Homing
        # FAILED" on failure, so the readback is what distinguishes them.
        self._stage_z_mm = self._confirm("z", 0.0, "gantry/home")
        self._stage_x_mm = self._confirm("x", 0.0, "gantry/home")
        return (self._stage_x_mm, self._stage_z_mm)

    def move_gantry(
        self, x_mm: float, z_mm: float, *, speed_pct: int, accel_pct: int
    ) -> tuple[float, float]:
        cmd = "gantry/move"
        # up → X → down (never diagonal). If X is unchanged, drop Z straight.
        # Compared with a tolerance, not `==`: `_stage_x_mm` now holds a
        # *measured* encoder value, which lands on 0.02 rather than 0.0, so
        # an exact compare would never match and every Z-only move would
        # pointlessly retract Z and re-command X. The threshold is the same
        # one that decides whether an axis arrived — inside it, the gantry
        # is by definition already at that X.
        if abs(x_mm - self._stage_x_mm) <= ARRIVAL_TOLERANCE_MM:
            self._move_z(z_mm, speed_pct, accel_pct, cmd)
        else:
            self._move_z(0.0, speed_pct, accel_pct, cmd)  # up
            self._move_x(x_mm, speed_pct, accel_pct, cmd)  # X
            self._move_z(z_mm, speed_pct, accel_pct, cmd)  # down
        return (self._stage_x_mm, self._stage_z_mm)

    # ── Linear rail (none on a pump+gantry cell) ────────────────────────
    def home_linear(self) -> float:
        raise _no_linear()

    def move_linear(self, y_mm: float) -> float:
        raise _no_linear()

    # ── Cell 5 action sets (none on a pump+gantry cell) ─────────────────
    def home_zstage(self) -> float:
        raise _absent("zstage")

    def move_zstage(
        self, z_mm: float, *, speed_pct: int, accel_pct: int
    ) -> float:
        raise _absent("zstage")

    def read_hotplate(self) -> dict:
        raise _absent("hotplate")

    def set_hotplate_temperature(self, celsius: float) -> float:
        raise _absent("hotplate")

    def set_hotplate_heater(self, *, enabled: bool) -> dict:
        raise _absent("hotplate")

    def set_hotplate_speed(self, rpm: float) -> float:
        raise _absent("hotplate")

    def set_hotplate_stirrer(self, *, enabled: bool) -> dict:
        raise _absent("hotplate")

    def read_lamp(self) -> dict:
        raise _absent("lamp")

    def set_lamp(self, *, enabled: bool) -> dict:
        raise _absent("lamp")

    # ── Safety / lifecycle ──────────────────────────────────────────────
    def stop(self) -> None:
        # Hard-stop the whole gantry group; halt the pump if it exposes one.
        try:
            MKSMotor.stop_group_hard(self._z_motors + [self._x])
        except Exception as e:  # noqa: BLE001 — surface as a device fault
            raise DeviceFaultError(f"gantry stop failed: {e}", command="stop")
        if self._pump is None:
            return
        halt = getattr(self._pump, "halt", None) or getattr(
            self._pump, "stop", None
        )
        if callable(halt):
            halt()

    def close(self) -> None:
        devices = [self._x, *self._z_motors]
        if self._pump is not None:
            devices.append(self._pump)
        for dev in devices:
            fn = getattr(dev, "close", None)
            if callable(fn):
                try:
                    fn()
                except Exception:  # noqa: BLE001 — best-effort shutdown
                    print(
                        f"warning: {type(dev).__name__}.close failed",
                        file=sys.stderr,
                    )
