#!/usr/bin/env python3
"""Interactive absolute-position control for cell3's XZ gantry on NUC2.

The companion probe ``test_cell3_gantry_shinyeong.py`` walks a fixed
home/out/back sequence. This one takes typed targets instead, for the
part of bring-up where the operator needs to reach a particular spot —
checking travel limits, finding a work position, or repeating one move
while watching the mechanics.

Same three guarantees as the sequenced probe, for the same reasons:

* **adapters named explicitly** — never ``open_xz``, which would assign
  "whichever two adapters remain" and, on NUC2's shared bus, pick up
  cell2's and cell5's Z motors instead of cell3's;
* **every move confirmed by encoder readback** — ``MKSMotor.move_to``
  prints rather than raises on a refused move, so an unconfirmed move
  reports the position it was *asked* for (LearnedPatterns #24);
* **the Z pair always moves together** — one command, both motors, with
  a racking check afterwards.

Positions are **absolute millimetres from the homed origin**, matching
``/v1/gantry/move``. Home first: without a homing pass the encoder zero
is wherever the motor happened to power up.

Commands at the prompt::

    x 50          move X to 50 mm
    z 20          move Z to 20 mm (both Z motors)
    xz 50 20      move to X 50, Z 20 (retracts Z first, then X, then Z)
    pos           read all three encoders, move nothing
    home          re-home the gantry
    speed 20      change the move speed percent for later moves
    q             quit

Usage::

    .venv/bin/python claude_test/test_cell3_move_shinyeong.py
    .venv/bin/python claude_test/test_cell3_move_shinyeong.py --max-mm 200
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from pyftdi.ftdi import Ftdi

from mks_motor import MKSMotor, prepare_usb_nodes, release_ftdi_sio

#: cell3's adapters. Same values as the sequenced probe; if the bench is
#: rewired, change them in both files.
SERIAL_X = "NTB3FXCE"
SERIAL_Z_A = "NTA4FH8Q"
SERIAL_Z_B = "NT9ZVXLU"

#: Axis convention inherited from cell1 (``PumpGantryCell.Config``):
#: both axes home at the 0x00 end and carry ``coord_invert``, so +mm
#: travels into the working envelope. If an axis stops dead at ~0 mm,
#: that is this setting, not a fault (LearnedPatterns #4).
X_COORD_INVERT = True
Z_COORD_INVERT = True
HOME_DIR_X = 0x00
HOME_DIR_Z = 0x00

#: Same tolerances the cell layer uses, and for the same reason: sized
#: to catch a move that was dropped, not to grade positioning accuracy.
ARRIVAL_TOLERANCE_MM = 0.5
Z_DESYNC_LIMIT_MM = 0.5

#: Soft travel ceiling. cell3's safe envelope has not been measured, so
#: anything past this needs ``--max-mm`` raised deliberately rather than
#: a typo being able to command a 400 mm traverse.
DEFAULT_MAX_MM = 100.0
MIN_MM = 0.0

#: Conservative until the bench is characterised.
DEFAULT_SPEED_PCT = 10
DEFAULT_ACCEL_PCT = 0
MIN_SPEED_PCT = 1
MAX_SPEED_PCT = 100

EXIT_OK = 0
EXIT_FAULT = 2


class GantryFault(Exception):
    """A move did not demonstrably do what it was commanded to do."""


@dataclass(frozen=True)
class Motors:
    """The three motors this tool owns, opened by explicit serial."""

    x: MKSMotor
    z_a: MKSMotor
    z_b: MKSMotor

    @property
    def z_pair(self) -> list[MKSMotor]:
        """The paired Z motors, which only ever move together."""
        return [self.z_a, self.z_b]

    @property
    def all(self) -> list[MKSMotor]:
        """Every motor, for a group emergency stop."""
        return [self.z_a, self.z_b, self.x]


def _check_bus() -> None:
    """Refuse to open anything unless cell3's three adapters are present.

    Raises:
        GantryFault: If any of cell3's adapters is missing.
    """
    present = [url.sn for url, _ in Ftdi.list_devices()]
    wanted = (SERIAL_X, SERIAL_Z_A, SERIAL_Z_B)
    missing = [s for s in wanted if s not in present]
    if missing:
        raise GantryFault(
            f"cell3 adapter(s) not on the bus: {', '.join(missing)}. "
            f"Nothing was opened."
        )
    others = [s for s in present if s not in wanted]
    print(f"cell3 adapters present. Not touched: {', '.join(others) or '-'}")


def _open_motors() -> Motors:
    """Open cell3's three adapters by serial and put them in SR_vFOC."""
    prepare_usb_nodes()
    release_ftdi_sio()
    x = MKSMotor.open(serial=SERIAL_X, coord_invert=X_COORD_INVERT)
    z_a = MKSMotor.open(serial=SERIAL_Z_A, coord_invert=Z_COORD_INVERT)
    z_b = MKSMotor.open(serial=SERIAL_Z_B, coord_invert=Z_COORD_INVERT)
    for motor in (z_a, z_b, x):
        motor.setup()
    return Motors(x=x, z_a=z_a, z_b=z_b)


def _axis_mm(motor: MKSMotor) -> float | None:
    """One motor's encoder position in mm, or None if it did not answer."""
    try:
        return motor.read_position_mm()
    except ConnectionError:
        return None


def _confirm(motors: Motors, axis: str, target: float) -> float:
    """Read an axis back and fail unless it actually arrived.

    Args:
        motors: The opened motor group.
        axis: ``"x"`` or ``"z"``.
        target: The commanded position in mm.

    Returns:
        The mean measured position of the axis, in mm.

    Raises:
        GantryFault: If an axis could not be read, did not arrive, or
            the paired Z motors ended too far apart.
    """
    group = motors.z_pair if axis == "z" else [motors.x]
    readings = [_axis_mm(m) for m in group]

    if any(mm is None for mm in readings):
        raise GantryFault(
            f"{axis} did not answer the position read after a move to "
            f"{target} mm; position UNKNOWN — re-home before moving again."
        )
    for mm in readings:
        if abs(mm - target) > ARRIVAL_TOLERANCE_MM:
            raise GantryFault(
                f"{axis} did not reach its target: commanded {target} mm, "
                f"encoder reads {mm:.3f} mm. Stopping at ~0 mm means the "
                f"axis was driven into its home limit — a coord_invert / "
                f"home_dir problem (LearnedPatterns #4), not a fault."
            )
    if axis == "z":
        spread = abs(readings[0] - readings[1])
        if spread > Z_DESYNC_LIMIT_MM:
            raise GantryFault(
                f"paired Z motors ended {spread:.3f} mm apart (limit "
                f"{Z_DESYNC_LIMIT_MM} mm) — the gantry is RACKING. Cut "
                f"power before commanding further motion."
            )
    return sum(readings) / len(readings)


def _show(motors: Motors) -> None:
    """Print a live encoder read of all three axes."""

    def _fmt(mm: float | None) -> str:
        return "unread" if mm is None else f"{mm:8.3f} mm"

    print(
        f"  X {_fmt(_axis_mm(motors.x))} | "
        f"Z_A {_fmt(_axis_mm(motors.z_a))} | "
        f"Z_B {_fmt(_axis_mm(motors.z_b))}"
    )


def _move_axis(
    motors: Motors, axis: str, target: float, speed: int, accel: int
) -> None:
    """Move one axis to an absolute target and confirm it arrived."""
    group = motors.z_pair if axis == "z" else [motors.x]
    MKSMotor.move_sync(group, [(target, speed, accel)])
    reached = _confirm(motors, axis, target)
    print(f"  {axis} -> commanded {target:g} mm, reads {reached:.3f} mm")


def _in_range(value: float, max_mm: float) -> bool:
    """True if a requested target is inside the soft travel limits."""
    if MIN_MM <= value <= max_mm:
        return True
    print(
        f"  refused: {value:g} mm is outside {MIN_MM:g}..{max_mm:g} mm. "
        f"Raise --max-mm only after measuring the real travel."
    )
    return False


def _handle(
    motors: Motors, parts: list[str], speed: int, accel: int, max_mm: float
) -> int:
    """Run one typed command. Returns the (possibly updated) speed."""
    verb = parts[0]

    if verb == "pos":
        _show(motors)
    elif verb == "home":
        MKSMotor.home_xz(motors.z_pair, motors.x, HOME_DIR_Z, HOME_DIR_X)
        _confirm(motors, "z", 0.0)
        _confirm(motors, "x", 0.0)
        print("  homed; both axes read 0 mm")
    elif verb == "speed" and len(parts) == 2:
        new = int(parts[1])
        if MIN_SPEED_PCT <= new <= MAX_SPEED_PCT:
            print(f"  speed {speed} -> {new} %")
            return new
        print(f"  refused: speed must be {MIN_SPEED_PCT}..{MAX_SPEED_PCT}")
    elif verb in ("x", "z") and len(parts) == 2:
        target = float(parts[1])
        if _in_range(target, max_mm):
            _move_axis(motors, verb, target, speed, accel)
    elif verb == "xz" and len(parts) == 3:
        x_target, z_target = float(parts[1]), float(parts[2])
        if _in_range(x_target, max_mm) and _in_range(z_target, max_mm):
            # Retract Z, traverse X, then lower Z — never diagonal.
            # Same ordering PumpGantryCell.move_gantry uses.
            _move_axis(motors, "z", 0.0, speed, accel)
            _move_axis(motors, "x", x_target, speed, accel)
            _move_axis(motors, "z", z_target, speed, accel)
    else:
        print(
            "  commands: x <mm> | z <mm> | xz <x> <z> | pos | home | "
            "speed <pct> | q"
        )
    return speed


def main(argv: list[str] | None = None) -> int:
    """Open cell3 and run the interactive prompt. Returns an exit code."""
    parser = argparse.ArgumentParser(
        description="Interactive absolute moves for cell3's XZ gantry."
    )
    parser.add_argument(
        "--max-mm",
        type=float,
        default=DEFAULT_MAX_MM,
        help=f"soft travel ceiling per axis (default {DEFAULT_MAX_MM:g})",
    )
    parser.add_argument(
        "--speed-pct",
        type=int,
        default=DEFAULT_SPEED_PCT,
        help=f"initial move speed percent (default {DEFAULT_SPEED_PCT})",
    )
    parser.add_argument(
        "--accel-pct",
        type=int,
        default=DEFAULT_ACCEL_PCT,
        help=f"move accel percent (default {DEFAULT_ACCEL_PCT})",
    )
    args = parser.parse_args(argv)

    print(
        "cell3 XZ gantry — interactive absolute moves (NUC2).\n"
        "Targets are absolute mm from the homed origin. HOME FIRST if "
        "the gantry has not been homed this power cycle.\n"
        "The physical e-stop is the only stop: this tool talks to the "
        "driver directly, so there is no server-side stop at all.\n"
        f"soft limit {MIN_MM:g}..{args.max_mm:g} mm per axis, "
        f"speed {args.speed_pct} %\n"
    )

    motors: Motors | None = None
    speed = args.speed_pct
    code = EXIT_OK
    try:
        _check_bus()
        motors = _open_motors()
        print()
        _show(motors)
        while True:
            try:
                line = input("cell3> ").strip()
            except EOFError:
                break
            if not line:
                continue
            if line.lower() in ("q", "quit", "exit"):
                break
            try:
                speed = _handle(
                    motors, line.split(), speed, args.accel_pct, args.max_mm
                )
            except ValueError:
                print("  refused: could not read that as a number")
    except KeyboardInterrupt:
        print("\n[STOPPED] interrupted")
    except GantryFault as fault:
        print(f"\n[FAULT] {fault}")
        if motors is not None:
            print("[SAFETY] firing a hard stop on all three motors...")
            MKSMotor.stop_group_hard(motors.all)
        code = EXIT_FAULT
    except Exception as exc:  # noqa: BLE001 — last resort around motion
        print(f"\n[ERROR] {type(exc).__name__}: {exc}")
        if motors is not None:
            print("[SAFETY] firing a hard stop on all three motors...")
            MKSMotor.stop_group_hard(motors.all)
        code = EXIT_FAULT
    finally:
        if motors is not None:
            for motor in motors.all:
                motor.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
