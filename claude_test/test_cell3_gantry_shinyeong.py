#!/usr/bin/env python3
"""Operator-gated bring-up probe for cell3's XZ gantry on NUC2.

Why this exists instead of ``smoke_l1.py`` against a cell3 server:
``MKSMotor.open_xz`` names only the X adapter and assigns **whichever
two FTDI adapters remain** to Z_A and Z_B. That is correct on NUC1,
where cell1's three adapters are the only ones on the bus. It is wrong
on NUC2, which carries six:

    cell2  NTAFT1KQ, NTA0X8KN          (its X adapter is not attached)
    cell3  NTB3FXCE, NTA4FH8Q, NT9ZVXLU
    cell5  NTB3EP5R                    (claimed by server/nuc2/cell5.toml)

Measured on this bench, ``Ftdi.list_devices()`` returns them in the
order ``NTA0X8KN, NTB3EP5R, NTA4FH8Q, NTB3FXCE, NT9ZVXLU, NTAFT1KQ``, so
``open_xz("NTB3FXCE")`` would drive **cell2's Z and cell5's Z** while
reporting itself as cell3's gantry — silently, because the driver only
prints the serials it picked. This probe therefore names all three
adapters explicitly and never calls ``open_xz``.

It also re-implements the confirm-by-readback guard from
``cell/pump_gantry_cell.py`` (LearnedPatterns #24). NUC2's checkout
predates that commit, and ``MKSMotor.move_to`` *prints* rather than
raises when a move is refused, so without a readback a gantry that never
moved reports the position it was asked for.

Nothing here runs unattended: every hardware action stops and waits for
the operator to type ``go``. There is no "yes to all" flag; do not add
one (``docs/L1_BRINGUP.md`` §0.2).

Usage::

    .venv/bin/python claude_test/test_cell3_gantry_shinyeong.py
    .venv/bin/python claude_test/test_cell3_gantry_shinyeong.py \
        --step-mm 5 --speed-pct 10 --out claude_test/cell3_bringup.md
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from dataclasses import dataclass

from pyftdi.ftdi import Ftdi

from mks_motor import MKSMotor, prepare_usb_nodes, release_ftdi_sio

#: cell3's own adapters, read off NUC2's live bus on 2026-07-29 and
#: cross-checked against the operator's list. Two of cell2's three agreed
#: with that list and its X (`NTB19XKA`) is absent, which is what a single
#: swapped adapter looks like (LearnedPatterns #22) — so only cell3 is
#: driven here, and only by serials this file names.
SERIAL_X = "NTB3FXCE"
SERIAL_Z_A = "NTA4FH8Q"
SERIAL_Z_B = "NT9ZVXLU"

#: Adapters on this bus that belong to other cells. Listed so the probe
#: can tell the operator exactly what it is leaving alone, and so a
#: future rewiring that moves one of these into cell3 is noticed.
FOREIGN_SERIALS = {
    "NTAFT1KQ": "cell2 Z",
    "NTA0X8KN": "cell2 Z",
    "NTB3EP5R": "cell5 Z (server/nuc2/cell5.toml)",
}

#: cell3 is documented as a clone of cell1, so its axis convention is
#: taken from ``PumpGantryCell.Config``: both axes home at the 0x00 end
#: and carry ``coord_invert``, which makes +mm travel into the working
#: envelope on both. UNVERIFIED on cell3's own hardware — per
#: LearnedPatterns #4 a direction config is only proven by an absolute
#: move off the home limit, which is what the X and Z steps below do.
#: If an axis stops instantly at ~0 mm, flip its invert here, do not
#: force the move.
X_COORD_INVERT = True
Z_COORD_INVERT = True
HOME_DIR_X = 0x00
HOME_DIR_Z = 0x00

#: How far an axis may sit from its commanded target and still count as
#: arrived. Mirrors ``cell/pump_gantry_cell.py``: sized to catch a move
#: that was dropped or refused outright, not to grade positioning.
ARRIVAL_TOLERANCE_MM = 0.5

#: Maximum spread between the two Z encoders before the gantry counts as
#: racking. Same value and same reasoning as the cell layer's.
Z_DESYNC_LIMIT_MM = 0.5

#: Conservative defaults for a first run on relocated hardware. The
#: bench's safe travel has not been measured on cell3 yet, so the step
#: stays small until the operator raises it deliberately.
DEFAULT_STEP_MM = 10.0
DEFAULT_SPEED_PCT = 10
DEFAULT_ACCEL_PCT = 0

#: Exit codes. Anything non-zero means no further step should be run.
EXIT_OK = 0
EXIT_ABORTED = 1
EXIT_FAULT = 2


class GantryFault(Exception):
    """A move did not demonstrably do what it was commanded to do."""


@dataclass(frozen=True)
class Motors:
    """The three motors this probe owns, opened by explicit serial."""

    x: MKSMotor
    z_a: MKSMotor
    z_b: MKSMotor

    @property
    def z_pair(self) -> list[MKSMotor]:
        """The paired Z motors, in the order they must always move."""
        return [self.z_a, self.z_b]

    @property
    def all(self) -> list[MKSMotor]:
        """Every motor, for a group emergency stop."""
        return [self.z_a, self.z_b, self.x]


def _bus_serials() -> list[str]:
    """Every FTDI serial libusb can currently see."""
    return [url.sn for url, _ in Ftdi.list_devices()]


def _check_bus() -> None:
    """Refuse to continue unless cell3's three adapters are all present.

    Raises:
        GantryFault: If any of cell3's adapters is missing.
    """
    present = _bus_serials()
    print(f"FTDI adapters on this bus ({len(present)}):")
    for serial in present:
        owner = FOREIGN_SERIALS.get(serial)
        if serial in (SERIAL_X, SERIAL_Z_A, SERIAL_Z_B):
            print(f"  {serial}  <- cell3, this probe drives it")
        elif owner:
            print(f"  {serial}  <- {owner}, NOT touched")
        else:
            print(f"  {serial}  <- UNKNOWN adapter, NOT touched")

    missing = [
        s for s in (SERIAL_X, SERIAL_Z_A, SERIAL_Z_B) if s not in present
    ]
    if missing:
        raise GantryFault(
            f"cell3 adapter(s) not on the bus: {', '.join(missing)}. "
            f"Nothing was opened. Check power and the USB path before "
            f"retrying — an adapter that is plugged in always enumerates."
        )
    print("All three cell3 adapters present.\n")


def _open_motors() -> Motors:
    """Open cell3's three adapters by serial and put them in SR_vFOC.

    Deliberately does not use ``MKSMotor.open_xz``: see the module
    docstring. Every adapter is named, so no motor outside cell3 can be
    picked up no matter what else shares the bus.

    Returns:
        The three opened, configured motors.
    """
    # Both are no-ops when unprivileged and touch only FTDI adapters.
    # NUC2's udev rule grants libusb access without unbinding ftdi_sio,
    # so listing works either way; this only helps if run as root.
    prepare_usb_nodes()
    release_ftdi_sio()

    print(f"Opening X   {SERIAL_X}")
    x = MKSMotor.open(serial=SERIAL_X, coord_invert=X_COORD_INVERT)
    print(f"Opening Z_A {SERIAL_Z_A}")
    z_a = MKSMotor.open(serial=SERIAL_Z_A, coord_invert=Z_COORD_INVERT)
    print(f"Opening Z_B {SERIAL_Z_B}")
    z_b = MKSMotor.open(serial=SERIAL_Z_B, coord_invert=Z_COORD_INVERT)

    # Without setup() the firmware ignores subsequent home/move commands
    # or never replies (mirrors PumpGantryCell.open and bridge.py).
    for motor in (z_a, z_b, x):
        motor.setup()
    print()
    return Motors(x=x, z_a=z_a, z_b=z_b)


def _axis_mm(motor: MKSMotor) -> float | None:
    """One motor's encoder position in mm, or None if it did not answer.

    Both failure shapes collapse to None: a stray CAN frame (the driver
    returns None) and a motor that never replied (it raises). A None is
    "not reached", never a position.
    """
    try:
        return motor.read_position_mm()
    except ConnectionError:
        return None


def _report_positions(motors: Motors, label: str) -> None:
    """Print a live encoder read of all three axes."""
    x_mm = _axis_mm(motors.x)
    z_a_mm = _axis_mm(motors.z_a)
    z_b_mm = _axis_mm(motors.z_b)

    def _fmt(mm: float | None) -> str:
        return "unread" if mm is None else f"{mm:8.3f} mm"

    print(
        f"  {label}: X {_fmt(x_mm)} | Z_A {_fmt(z_a_mm)} | Z_B {_fmt(z_b_mm)}"
    )


def _confirm(motors: Motors, axis: str, target: float) -> float:
    """Read an axis back and fail unless it actually arrived.

    ``MKSMotor.move_to`` prints ``[ERROR] Motor failed to start`` and
    *returns* on a refused or unanswered F5 rather than raising, and
    ``move_sync`` discards that return value. Without this readback a
    gantry that never moved would report the position it was asked for
    (LearnedPatterns #24).

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
            f"{target} mm; the gantry's position is UNKNOWN — do not "
            f"trust the last reported value, and re-home before moving."
        )

    for mm in readings:
        if abs(mm - target) > ARRIVAL_TOLERANCE_MM:
            raise GantryFault(
                f"{axis} did not reach its target: commanded {target} mm, "
                f"encoder reads {mm:.3f} mm. If it stopped at ~0 mm the "
                f"axis was driven into its home limit — that is a "
                f"coord_invert/home_dir problem (LearnedPatterns #4), "
                f"not a hardware fault."
            )

    if axis == "z":
        spread = abs(readings[0] - readings[1])
        if spread > Z_DESYNC_LIMIT_MM:
            raise GantryFault(
                f"paired Z motors ended {spread:.3f} mm apart (limit "
                f"{Z_DESYNC_LIMIT_MM} mm) — the gantry is RACKING. Cut "
                f"power before commanding any further motion."
            )

    return sum(readings) / len(readings)


def _gate(title: str, briefing: str, expect: str) -> bool:
    """Brief the operator and ask for consent.

    Returns:
        True to run the step, False to skip it.

    Raises:
        KeyboardInterrupt: If the operator chooses to quit.
    """
    print(f"\n[{title}]")
    print(f"  !! {briefing}")
    print(f"     expect: {expect}")
    try:
        answer = input("     type 'go' to run, 's' to skip, 'q' to quit > ")
    except EOFError:
        # No terminal means no operator, and no operator means no motion.
        raise KeyboardInterrupt("no operator present") from None
    answer = answer.strip().lower()
    if answer == "go":
        return True
    if answer == "s":
        print("     skipped.")
        return False
    raise KeyboardInterrupt("operator quit")


def _observe(rows: list[dict[str, str]], step: str, result: str) -> None:
    """Record one step and ask the operator what they actually saw."""
    print(f"     -> {result}")
    try:
        seen = input("     what did you observe? > ").strip()
    except EOFError:
        seen = ""
    rows.append(
        {
            "utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "step": step,
            "result": result,
            "observed": seen or "-",
        }
    )


def _step_home(motors: Motors, rows: list[dict[str, str]]) -> None:
    """Home the paired Z, then X, and prove both by readback."""
    if not _gate(
        "home",
        "HIGHEST-RISK SUBSYSTEM. cell3 frame clear, e-stop IN HAND. "
        "Both Z axes must home together — any tilt, hit the e-stop.",
        "X and Z both read ~0.0 mm afterwards",
    ):
        return
    MKSMotor.home_xz(motors.z_pair, motors.x, HOME_DIR_Z, HOME_DIR_X)
    # home() sets the encoder zero on success and only *prints* on
    # failure, so the readback is what tells the two apart.
    z_mm = _confirm(motors, "z", 0.0)
    x_mm = _confirm(motors, "x", 0.0)
    _observe(rows, "home", f"X {x_mm:.3f} mm, Z {z_mm:.3f} mm")


def _step_move(
    motors: Motors,
    rows: list[dict[str, str]],
    axis: str,
    target: float,
    speed_pct: int,
    accel_pct: int,
    briefing: str,
    expect: str,
) -> None:
    """Move one axis to an absolute target and confirm by readback."""
    step = f"{axis}_to_{target:g}mm"
    if not _gate(step, briefing, expect):
        return
    group = motors.z_pair if axis == "z" else [motors.x]
    MKSMotor.move_sync(group, [(target, speed_pct, accel_pct)])
    reached = _confirm(motors, axis, target)
    _observe(
        rows, step, f"{axis} commanded {target:g} mm, reads {reached:.3f} mm"
    )


def _to_markdown(rows: list[dict[str, str]]) -> str:
    """Render the run as a Markdown table for docs/L1_AUDIT.md."""
    head = (
        "### cell3 gantry bring-up (NUC2)\n\n"
        "| UTC | step | result | observed |\n|---|---|---|---|\n"
    )
    body = "".join(
        f"| {r['utc']} | {r['step']} | {r['result']} | {r['observed']} |\n"
        for r in rows
    )
    return head + body


def main(argv: list[str] | None = None) -> int:
    """Run the gated bring-up sequence. Returns a process exit code."""
    parser = argparse.ArgumentParser(
        description="Operator-gated cell3 XZ gantry bring-up (NUC2)."
    )
    parser.add_argument(
        "--step-mm",
        type=float,
        default=DEFAULT_STEP_MM,
        help=f"travel per axis test (default {DEFAULT_STEP_MM})",
    )
    parser.add_argument(
        "--speed-pct",
        type=int,
        default=DEFAULT_SPEED_PCT,
        help=f"move speed percent (default {DEFAULT_SPEED_PCT})",
    )
    parser.add_argument(
        "--accel-pct",
        type=int,
        default=DEFAULT_ACCEL_PCT,
        help=f"move accel percent (default {DEFAULT_ACCEL_PCT})",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="write the Markdown result table to this path",
    )
    args = parser.parse_args(argv)

    print(
        "cell3 XZ gantry bring-up — NUC2.\n"
        "Operator supervision is required; nothing moves until you type "
        "'go'.\n"
        "The physical e-stop is the only stop: POST /v1/stop cannot "
        "interrupt a move in flight (docs/L1_AUDIT.md GAP-9), and this "
        "probe bypasses the server entirely.\n"
    )

    rows: list[dict[str, str]] = []
    motors: Motors | None = None
    code = EXIT_OK
    try:
        _check_bus()
        motors = _open_motors()
        # A motor that is unpowered or off the CAN bus cannot answer an
        # encoder read, so this is the cheapest proof of identity there
        # is — and it runs before anything is allowed to move.
        print("Live encoder read (proves all three motors are reachable):")
        _report_positions(motors, "before")
        step = args.step_mm

        _step_home(motors, rows)
        _step_move(
            motors,
            rows,
            "x",
            step,
            args.speed_pct,
            args.accel_pct,
            f"X travels about {step:g} mm. Confirm the range is clear.",
            f"X reaches {step:g} mm; Z stays home",
        )
        _step_move(
            motors,
            rows,
            "x",
            0.0,
            args.speed_pct,
            args.accel_pct,
            "X returns to the origin.",
            "X back to 0 mm",
        )
        _step_move(
            motors,
            rows,
            "z",
            step,
            args.speed_pct,
            args.accel_pct,
            "ONE command drives BOTH Z motors. WATCH BOTH SIDES — if "
            "only one moves, or the frame tilts, hit the e-stop.",
            f"both Z axes reach {step:g} mm together, no visible tilt",
        )
        _step_move(
            motors,
            rows,
            "z",
            0.0,
            args.speed_pct,
            args.accel_pct,
            "Both Z axes travel back to the origin.",
            "both Z axes back to 0 mm together",
        )

        print("\nFinal live read:")
        _report_positions(motors, "after")

    except KeyboardInterrupt as stop:
        print(f"\n[STOPPED] {stop}")
        code = EXIT_ABORTED
    except GantryFault as fault:
        print(f"\n[FAULT] {fault}")
        if motors is not None:
            print("[SAFETY] firing a hard stop on all three motors...")
            MKSMotor.stop_group_hard(motors.all)
        code = EXIT_FAULT
    except Exception as exc:  # noqa: BLE001 — last resort before motion
        print(f"\n[ERROR] {type(exc).__name__}: {exc}")
        if motors is not None:
            print("[SAFETY] firing a hard stop on all three motors...")
            MKSMotor.stop_group_hard(motors.all)
        code = EXIT_FAULT
    finally:
        if motors is not None:
            for motor in motors.all:
                motor.close()

    if rows:
        table = _to_markdown(rows)
        print("\n" + table)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write(table)
            print(f"written: {args.out}")

    return code


if __name__ == "__main__":
    sys.exit(main())
