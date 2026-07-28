"""Real cell4 (balance + linear rail): MINAS A6 linear rail (``lmc``) + Entris-II balance.

cell4 carries the Phase's single balance on a linear rail and shuttles it
under cell1–3 to weigh each dispense. It has **no pump**, so the pump methods
of the :class:`cell_protocol.Cell` protocol raise.

The linear (Y) rail runs on the **MINAS standard serial protocol over RS485**
(:class:`external.lmc.LinearMotorController`, amp ``Pr5.37=0``): absolute
positioning (``move_to_mm``) runs a software closed loop whose per-iteration
speed command comes from the driver's ``PIDController`` (P-tuned), converging
to ±0.1 mm and aborting if the residual stops shrinking. Hardware-verified at
the bench, not in CI.
"""

from __future__ import annotations

from dataclasses import dataclass

from entris_ii import PrecisionScaleController

from .cell_protocol import (
    AMBIENT_LEVELS,
    Cell,
    DeviceFaultError,
    InvalidArgError,
    TransportError,
    WrongStateError,
)
from LinearMotorController import (
    LinearMotorController,
    MotionStopError,
    MoveResult,
)


@dataclass(frozen=True, slots=True)
class BalanceLinearConfig:
    """Bench wiring for the weigh cell (loaded from ics.toml)."""

    linear_port: str = "110A:1150"  # MINAS A6 over RS485 via Moxa UPort 1150
    scale_port: str | None = None  # None → auto-detect by Sartorius VID
    ambient: str | None = None


#: Budget for one settled weight read. Measured on this bench: 1.2-3.6 s
#: to converge, so this is roughly eight times the worst observed case.
#: Deliberately below the tightest `balance/weight` step timeout in the
#: scenarios (40 s) so the driver's timeout fires first — its message
#: names the likely cause ("is COM.OUTP set to AUTO.W/O?"), where a step
#: timeout would only say the call was slow.
SETTLE_TIMEOUT_S = 30.0


def _no_pump() -> WrongStateError:
    # Defensive stub (mirror of PumpGantryCell._no_balance): this cell has no
    # pump, but the `Cell` protocol requires every method, so the pump methods
    # raise this instead of crashing. The web greys them out from
    # `diagnose()` pump.present=false; this only fires on a stray call (→ 409).
    # The stage methods ARE implemented here (they drive the linear rail).
    return WrongStateError("balance+linear cell has no pump", command="pump")


def _no_gantry() -> WrongStateError:
    # Defensive stub: motion here is the linear Y rail (linear action set), not
    # an XZ gantry — the gantry methods raise so a misdirected /v1/gantry/*
    # call gets a clean 409 instead of hitting the rail.
    return WrongStateError(
        "balance+linear cell has no gantry", command="gantry"
    )


def _absent(family: str) -> WrongStateError:
    # Defensive stub for the action sets only Cell D (cell5) has: the
    # single Z stage, the hotplate and the IR lamp.
    return WrongStateError(
        f"balance+linear cell has no {family}", command=family
    )


class BalanceLinearCell(Cell):
    """cell4 = MINAS A6 linear rail + Entris-II balance, behind ``Cell``."""

    def __init__(
        self,
        lin: LinearMotorController,
        scale: PrecisionScaleController,
        config: BalanceLinearConfig,
    ) -> None:
        self._lin = lin
        self._scale = scale
        self._cfg = config
        # Last settled weight (updated by read_weight/tare). status() returns
        # this instead of doing a blocking read_stable_weight every poll — a
        # 30 s settle timeout there would hold the cell lock and starve the
        # linear. The operator refreshes it on demand via read_weight.
        self._last_weight_g = 0.0

    @classmethod
    def open(cls, config: BalanceLinearConfig) -> BalanceLinearCell:
        scale_port = config.scale_port or PrecisionScaleController.find_port()
        lin = LinearMotorController(config.linear_port)  # opens RS485 at init
        scale = PrecisionScaleController(port=scale_port)
        scale.__enter__()  # opens the SBI link (context-manager protocol)
        if config.ambient is not None:
            scale.set_ambient(config.ambient)
        return cls(lin, scale, config)

    # ── Discovery ───────────────────────────────────────────────────────
    def diagnose(self) -> dict:
        # Query each device exactly once and reuse the values below. The
        # earlier version asked for the balance model and the amp's software
        # version twice each (once for the per-device block, once for
        # "versions"), and the *second* read of a pair came back None on the
        # real amp -- which then failed HealthResponse validation, since
        # /v1/health serves driver_versions straight out of the cached
        # diagnose. Halving the round-trips also shortens the window in
        # which the balance's AUTO W/ auto-push can race an ID reply
        # (LearnedPatterns #13).
        balance_model = self._scale.get_model_number()
        linear_version = self._lin.read_software_version()
        return {
            # No pump on this cell; ok=True keeps the cell from reading faulted.
            "pump": {"present": False, "ok": True},
            "balance": {
                "model": balance_model,
                "serial_number": self._scale.get_serial_number(),
                "ok": True,
            },
            "stage": {  # the "stage" axis here is the linear rail
                "model": self._lin.read_model_name(),
                "version": linear_version,
                "ok": True,
            },
            "ok_to_initialize": True,
            "versions": {
                "balance": balance_model,
                "linear": linear_version,
            },
        }

    def status(self) -> dict:
        pos = self._lin.read_position_mm()
        return {
            "weight_g": self._last_weight_g,  # cached; refresh via read_weight
            "valve": "-",  # no valve on a weigh cell
            "plunger_uL": 0.0,
            # None (a failed RS485 read) is reported as null, never as 0.0:
            # "I could not read the position" and "the rail is at the origin"
            # are opposite facts, and 0.0 is the one that makes a home assert
            # pass (LearnedPatterns #15). status() is a probe, so unlike the
            # motion methods it reports the gap rather than raising.
            "stage_x_mm": float(pos) if pos is not None else None,
            "stage_z_mm": 0.0,
            "busy": False,
            "error": None,
        }

    # ── Balance ─────────────────────────────────────────────────────────
    def tare(self) -> float:
        # Plain zero/tare (SBI Esc T) — the routine "Tare" button. Adopts the
        # current pan load as the new zero. For commissioning use calibrate().
        self._scale.tare()
        self._last_weight_g = 0.0
        return 0.0

    def calibrate(self) -> float:
        # Internal (isoCAL) calibration: the balance's built-in weight adjusts
        # span + zero. The pan MUST be empty. This is the commissioning path
        # ("Setup all"), not routine zeroing — routine zeroing uses tare().
        # The driver forces ambient to "very_unstable" during the cycle, so
        # restore the configured ambient afterward.
        reading = self._scale.calibrate_internal_very_unstable()
        if self._cfg.ambient is not None:
            self._scale.set_ambient(self._cfg.ambient)
        self._last_weight_g = float(reading.value)
        return self._last_weight_g

    def read_weight(self) -> tuple[float, bool]:
        # Settling is judged here, not by the balance. This bench vibrates
        # enough that the balance's own stability criterion was never met:
        # under COM.OUTP = AUTO W/ it pushes only after it calls itself
        # stable, so it simply went silent and every read timed out having
        # received nothing. The panel is now on AUTO.W/O, which streams
        # regardless, and read_settled_weight accepts a value once
        # consecutive readings agree. Measured on this bench: 1.2-3.6 s to
        # converge, against a 30 s timeout that was previously failing.
        #
        # Flush first: the stream buffers FIFO, so without this the read
        # starts on values captured before the load changed.
        self._scale.flush_pending_reads()
        reading = self._scale.read_settled_weight(timeout=SETTLE_TIMEOUT_S)
        self._last_weight_g = float(reading.value)
        # `stable` means "agreed with its neighbours to within the driver's
        # tolerance", which is the only stability claim anyone can make on
        # this bench -- not the balance's own verdict.
        return self._last_weight_g, True

    def set_ambient(self, level: str) -> str:
        if level not in AMBIENT_LEVELS:
            raise InvalidArgError(
                f"level must be one of {AMBIENT_LEVELS}, got {level!r}"
            )
        self._scale.set_ambient(level)
        return level

    # ── Pump (none on a weigh cell) ─────────────────────────────────────
    def initialize(self, *, force: int = 2, ccw: bool = False) -> dict:
        raise _no_pump()

    def move_valve(self, port: int) -> str:
        raise _no_pump()

    def aspirate(self, target_uL: float) -> float:
        raise _no_pump()

    def dispense(self, target_uL: float = 0.0) -> float:
        raise _no_pump()

    def cycle(
        self,
        *,
        cycles: int,
        volume_uL: float,
        source_port: int,
        dispense_port: int,
    ) -> dict:
        raise _no_pump()

    # ── Linear rail (Y) ─────────────────────────────────────────────────
    def home_linear(self) -> float:
        # No discrete homing on the RS485 driver; the encoder origin is 0 mm,
        # reached by an absolute move via the driver's PID closed loop.
        return self._absolute_move(0.0, "linear/home")

    def move_linear(self, y_mm: float) -> float:
        # Absolute Y target in mm. The RS485 driver's move_to_mm runs a
        # PID-driven software closed loop (P-tuned) to ±0.1 mm; the PID owns the
        # per-iteration speed, so there is no useful per-move speed/accel here.
        return self._absolute_move(y_mm, "linear/move")

    def _absolute_move(self, target_mm: float, command: str) -> float:
        """Drive the rail to an absolute target and confirm it arrived."""
        try:
            result = self._lin.move_to_mm(target_mm)
        except MotionStopError as exc:
            # The amp never acknowledged a stop. The rail may still be
            # moving and no software path can halt it, so this must reach
            # the operator verbatim rather than as a generic 500.
            raise DeviceFaultError(str(exc), command=command) from exc
        return self._settled_mm(result, command)

    @staticmethod
    def _settled_mm(result: MoveResult | None, command: str) -> float:
        """Return the rail's position, or fail loudly if it did not arrive.

        Two distinct failures, neither of which may be reported as a
        successful move (LearnedPatterns #15 and #17):

        * ``None`` — the RS485 exchange failed, so the position is simply
          unknown. This used to substitute a plausible number (0.0 for
          home, the requested target for a move), fabricating exactly the
          value a scenario's ``verify_*`` assert checks against.
        * ``converged=False`` — the amp answered and the position is real,
          but the closed loop gave up short of the target. A 2026-07-28 run
          commanded 0.0 mm, stopped at 0.676 mm, and the cell returned
          ``200 OK`` because the driver reported arrival and surrender with
          the same bare float.
        """
        if result is None:
            raise TransportError(
                "linear amp did not answer the position read; the rail's "
                "position is unknown — do not trust the last reported value",
                command=command,
            )
        if not result.converged:
            raise DeviceFaultError(
                f"linear rail did not reach its target ({result.reason}); "
                f"it stopped at {result.position_mm} mm",
                command=command,
            )
        return float(result.position_mm)

    # ── Gantry (none on a balance+linear cell) ──────────────────────────
    def home_gantry(self) -> tuple[float, float]:
        raise _no_gantry()

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

    def move_gantry(
        self, x_mm: float, z_mm: float, *, speed_pct: int, accel_pct: int
    ) -> tuple[float, float]:
        raise _no_gantry()

    # ── Safety / lifecycle ──────────────────────────────────────────────
    def stop(self) -> None:
        # The MINAS RS485 standard-protocol driver exposes no async halt; a
        # hard stop is handled at the bench-level interlock.
        pass

    def close(self) -> None:
        ser = getattr(self._lin, "ser", None)
        if ser is not None:
            try:
                ser.close()
            except Exception:  # noqa: BLE001 — best-effort shutdown
                pass
        try:
            self._scale.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
