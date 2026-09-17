#!/usr/bin/env python3
"""Measure how high each cell3 Z home switch sits above the mechanical stop.

WHAT THIS BUYS. cell3's two Z home switches do not trigger at the same
height, and homing gives each motor its own zero AT ITS OWN SWITCH — so
whatever the switches disagree by, the gantry carries as a permanent tilt
out of every homing pass. There is nowhere in software to correct it: the
cell's Config has no per-axis Z offset and the driver's homing payload has
no offset field either, so a single-axis nudge only survives until the next
home. The durable fix is to move one switch, and this says by how much.

THE MEASUREMENT. Both carriages start pressed against their mechanical
stops with the encoders freshly zeroed there (power-cycle at the stops —
see below), which makes encoder 0 the same physical plane on both sides.
The pair is then walked upward in small steps, reading IN_1 on each motor
at every step. The position where a switch lets go is how far above the
stop that switch triggers. The difference between the two is the shim.

WHY BOTH CARRIAGES MUST BE AT THEIR STOPS AND THE POWER CYCLED FIRST.
Encoder zero is not a property of the machine; it is wherever the motor
was when it last homed or last powered up. If the two zeros do not already
mean the same physical height, every number below is measured from two
different references and the difference is meaningless. Pressing both
carriages to their stops by hand and then powering on is the one procedure
that puts both zeros on the same plane without trusting either switch —
which is the thing under test and cannot also be the reference.

    # hand-press both carriages to their stops, power-cycle the motors
    ssh innocore_nuc_2
    cd ~/workspace/InnoCOREServer/InnoCORESDL_shinyeong
    .venv/bin/python claude_test/test_cell3_switch_height_shinyeong.py

It refuses to start unless both switches read closed, because a switch
that is already open means that carriage is not at its stop and the run
would measure from nowhere. Do not read the converse into it: the first
run measured the switches holding closed from the stop up to 0.65 and
0.95 mm, so two closed switches leave a carriage free to sit a couple of
tenths off its stop — comparable to the difference this is measuring.
RUN IT THREE TIMES, hand-pressing and power-cycling before each, and act
on the difference only if it repeats.

The cell3 server must be stopped first — one owner per adapter
(CLAUDE.md folder rule 2):

    pgrep -f 'cell[3]' | xargs -r kill

SAFETY. Travel is away from the home end, in steps of a fraction of a
millimetre, capped by ``--max-mm``, and the pair moves through
``move_sync`` so it cannot rack by command. The operator holds the e-stop:
a gantry stop queues behind the move it means to interrupt
(docs/L1_AUDIT.md GAP-9).
"""

from __future__ import annotations

import argparse
import sys

from pyftdi.ftdi import Ftdi

from mks_motor import MKSMotor, prepare_usb_nodes, release_ftdi_sio

#: cell3's Z adapters. Same values as test_cell3_move_shinyeong.py.
SERIAL_Z_A = "NTA4FH8Q"
SERIAL_Z_B = "NT9ZVXLU"

#: Axis convention inherited from cell1 (LearnedPatterns #4).
Z_COORD_INVERT = True

#: IO bit for the home input. Active low — 0 means CLOSED.
IN_1_HOME = 0x01

#: Step size. Fine enough to place a trigger point well inside the
#: 0.5 mm the cell layer treats as "arrived", coarse enough that the
#: sweep finishes in a couple of minutes.
DEFAULT_STEP_MM = 0.05

#: Cap on the sweep. Both switches were releasing within a fraction of a
#: millimetre of the stop when this was written; anything needing more
#: than a few millimetres is a different fault and should stop the run
#: rather than keep climbing.
DEFAULT_MAX_MM = 5.0
HARD_MAX_MM = 20.0

#: Slow, because torque rises as speed falls and these are short moves
#: where time costs nothing.
MOVE_SPEED_PCT = 1
MOVE_ACCEL_PCT = 0

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_FAULT = 2


class SweepError(Exception):
    """The bench refused, or an axis stopped answering."""


def _home_closed(motor: MKSMotor) -> bool:
    """True if this motor's home switch is closed right now.

    Raises:
        SweepError: If the motor did not answer the IO read.
    """
    status = motor._read_io_status()  # noqa: SLF001 — no public accessor
    if status is None:
        raise SweepError(
            "a motor stopped answering its IO read, so the switch state "
            "is unknown and the sweep cannot be trusted"
        )
    return (status & IN_1_HOME) == 0


def _read_mm(motor: MKSMotor) -> float:
    """One encoder reading in mm.

    Raises:
        SweepError: If the motor did not answer.
    """
    try:
        mm = motor.read_position_mm()
    except ConnectionError as exc:
        raise SweepError(f"an axis stopped answering: {exc}")
    if mm is None:
        raise SweepError("an axis returned a stray frame instead of a position")
    return mm


def main(argv: list[str] | None = None) -> int:
    """Sweep upward and report each switch's height. Returns an exit code."""
    parser = argparse.ArgumentParser(
        description="Measure cell3's Z home switch heights above the stop."
    )
    parser.add_argument("--step-mm", type=float, default=DEFAULT_STEP_MM)
    parser.add_argument("--max-mm", type=float, default=DEFAULT_MAX_MM)
    args = parser.parse_args(argv)

    if not 0 < args.step_mm <= 1.0:
        print("[REFUSED] --step-mm must be in (0, 1.0]")
        return EXIT_REFUSED
    if not 0 < args.max_mm <= HARD_MAX_MM:
        print(f"[REFUSED] --max-mm must be in (0, {HARD_MAX_MM:g}]")
        return EXIT_REFUSED

    present = [url.sn for url, _ in Ftdi.list_devices()]
    missing = [s for s in (SERIAL_Z_A, SERIAL_Z_B) if s not in present]
    if missing:
        print(f"[REFUSED] adapter(s) not on the bus: {', '.join(missing)}")
        return EXIT_REFUSED

    prepare_usb_nodes()
    release_ftdi_sio()
    try:
        z_a = MKSMotor.open(serial=SERIAL_Z_A, coord_invert=Z_COORD_INVERT)
        z_b = MKSMotor.open(serial=SERIAL_Z_B, coord_invert=Z_COORD_INVERT)
    except Exception as exc:  # noqa: BLE001 — most likely the server
        print(
            f"[REFUSED] could not open cell3's Z adapters: {exc}\n"
            f"  the cell3 server is probably holding them. Stop it:\n"
            f"    pgrep -f 'cell[3]' | xargs -r kill"
        )
        return EXIT_REFUSED

    pair = [z_a, z_b]
    code = EXIT_OK
    try:
        for motor in pair:
            motor.setup()

        start_a, start_b = _read_mm(z_a), _read_mm(z_b)
        print(f"\n  start: Z_A {start_a:.4f} mm   Z_B {start_b:.4f} mm")

        # THE PRECONDITION IS THE WHOLE MEASUREMENT, AND THIS CHECK IS
        # WEAKER THAN IT LOOKS. Two closed switches are NECESSARY — an
        # open one proves that carriage is off its stop — but they are not
        # SUFFICIENT. The first run of this tool, 2026-09-02, measured the
        # switches staying closed from the stop up to 0.65 and 0.95 mm, so
        # "closed" is satisfied anywhere inside a band far wider than the
        # ~0.3 mm difference being measured. A carriage resting a couple of
        # tenths off its stop passes this check and puts that error
        # straight into the result.
        #
        # Nothing here can close that gap: the only witness to where a
        # carriage really is, is the switch, and the switch is what is
        # under test. So the operator's hand on the carriage is the
        # reference, and the defence against it is repetition — press,
        # power-cycle and measure three times, and believe the difference
        # only if it repeats.
        closed = {"Z_A": _home_closed(z_a), "Z_B": _home_closed(z_b)}
        if not all(closed.values()):
            open_ones = [k for k, v in closed.items() if not v]
            print(
                f"\n[REFUSED] {', '.join(open_ones)} home switch is already "
                f"OPEN. That carriage is not against its stop, so there is "
                f"no common reference to measure from. Hand-press both "
                f"carriages to their stops, power-cycle the motors so both "
                f"encoders zero there, and run this again."
            )
            return EXIT_REFUSED
        print(
            "  both home switches closed — necessary, but NOT proof that "
            "both carriages are hard against their stops: the switches "
            "stay closed across a band wider than the difference being "
            "measured. Repeat this run (hand-press + power-cycle each "
            "time) and trust the figure only once it repeats."
        )

        print(
            f"\n  stepping up in {args.step_mm:g} mm increments, watching "
            f"for each switch to let go…\n"
        )
        print("      pos      Z_A        Z_B")
        release_a: float | None = None
        release_b: float | None = None
        pos = 0.0
        while pos < args.max_mm:
            pos += args.step_mm
            MKSMotor.move_sync(pair, [(pos, MOVE_SPEED_PCT, MOVE_ACCEL_PCT)])
            a_closed, b_closed = _home_closed(z_a), _home_closed(z_b)
            if release_a is None and not a_closed:
                release_a = _read_mm(z_a)
            if release_b is None and not b_closed:
                release_b = _read_mm(z_b)
            print(
                f"   {pos:7.3f}  {'CLOSED' if a_closed else 'open  '}   "
                f"{'CLOSED' if b_closed else 'open  '}"
            )
            if release_a is not None and release_b is not None:
                break

        print()
        if release_a is None or release_b is None:
            still = [
                n
                for n, r in (("Z_A", release_a), ("Z_B", release_b))
                if r is None
            ]
            raise SweepError(
                f"{', '.join(still)} switch never let go within "
                f"{args.max_mm:g} mm of the stop. That is far more than a "
                f"switch height difference — it is stuck closed, or that "
                f"carriage is not moving. Do not shim anything on this."
            )

        print(f"  Z_A switch releases {release_a:.4f} mm above the stop")
        print(f"  Z_B switch releases {release_b:.4f} mm above the stop")
        gap = release_a - release_b
        print(f"  difference: {abs(gap):.4f} mm")

        higher, lower = ("Z_A", "Z_B") if gap > 0 else ("Z_B", "Z_A")
        if abs(gap) < args.step_mm:
            print(
                f"\n  THE TWO SWITCHES AGREE within one step "
                f"({args.step_mm:g} mm), so homing already leaves the pair "
                f"level and there is nothing to shim. Any tilt you can see "
                f"is coming from somewhere else."
            )
        else:
            print(
                f"\n  {higher}'s SWITCH SITS {abs(gap):.3f} mm HIGHER than "
                f"{lower}'s. Homing zeroes each motor at its own switch, so "
                f"that is exactly the tilt every homing pass leaves behind. "
                f"Move {higher}'s switch {abs(gap):.3f} mm DOWN — toward the "
                f"stop — or {lower}'s the same distance up, and homing will "
                f"land them level from then on.\n"
                f"  Resolution here is one step, {args.step_mm:g} mm; re-run "
                f"with a smaller --step-mm if you need finer than that."
            )

    except SweepError as exc:
        print(f"\n[FAULT] {exc}")
        code = EXIT_FAULT
    except Exception as exc:  # noqa: BLE001 — always stop the axes
        print(f"\n[ERROR] {type(exc).__name__}: {exc}")
        code = EXIT_FAULT
    finally:
        try:
            MKSMotor.stop_group_hard(pair)
        except Exception:  # noqa: BLE001
            print("[SAFETY] could not confirm a stop — CUT POWER.")
        for motor in pair:
            motor.close()

    return code


if __name__ == "__main__":
    sys.exit(main())
