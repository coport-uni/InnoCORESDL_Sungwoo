#!/usr/bin/env python3
"""Read cell3's Z home switches directly, so a base adjustment can be
judged in seconds instead of by running a homing pass.

THE LOOP THIS EXISTS FOR. cell3's Z_A does not rise far enough: something
interferes on the way up, the carriage stops short of its home switch, and
the switch never closes. Fixing that is a matter of shifting Z_A's base and
trying again, and the cost of "trying again" is what this tool attacks.

Homing is a terrible way to ask the question. When Z_A misses its switch
the motor simply keeps turning for the driver's whole ``_max_wait_sec``
(250 s) — measured twice on 2026-09-01, encoder ending at -7657 mm and
-7593 mm, which is 180 RPM x 250 s of screw with the carriage going
nowhere. Then the cell holds its lock until that expires and the server
has to be restarted. Four minutes and a restart per attempt.

But the switch state is readable outright. ``CMD_READ_IO`` (0x34) returns
IN_1 and IN_2 per motor, and with this bench's wiring (homeTrig=0, active
low) a bit value of 0 means CLOSED. So the question "does Z_A's switch
close at the top now?" is one CAN frame per motor, and no motion at all.

    ssh innocore_nuc_2
    cd ~/workspace/InnoCOREServer/InnoCORESDL_shinyeong
    .venv/bin/python claude_test/test_cell3_home_switch_shinyeong.py
    .venv/bin/python claude_test/test_cell3_home_switch_shinyeong.py --to-zero

Default is READ-ONLY: it reports where the axes are and which switches are
closed, and commands nothing. ``--to-zero`` first drives the Z pair up to
encoder 0 — the top of the addressable range — and reads there.

``--descend-mm N`` goes down to N first, THEN back up to 0, and that is
usually the run you want. Reading the switches where the carriage already
sits proves little: after a manual re-home both switches are closed simply
because someone put them there. The fault is that Z_A cannot RETURN to its
switch under its own power, so the measurement has to include a descent
and a climb. One command, about half a minute, per base adjustment.

WHY --to-zero CANNOT PROVE A GOOD ADJUSTMENT ON ITS OWN. ``move_to``
clamps its target to [0, 400] mm, so 0 is as high as an absolute move can
ask for. If Z_A's switch sits ABOVE encoder 0, no move here will reach it
and only a homing pass could — this tool will report the switch open and
cannot tell you how much further it needed to go. That is still the useful
answer: switch closed at 0 means the base is now right, switch open at 0
means it is not, and you get it in seconds.

The Z pair moves through ``move_sync``, so both sides travel together and
the gantry does not rack. The cell3 server must be stopped first — one
owner per adapter (CLAUDE.md folder rule 2):

    pgrep -f 'cell[3]' | xargs -r kill

SAFETY. Motion here is bounded by an absolute target and the operator
holds the e-stop; a gantry stop queues behind the move it interrupts
(docs/L1_AUDIT.md GAP-9), so the physical switch is the only immediate one.
"""

from __future__ import annotations

import argparse
import sys

from pyftdi.ftdi import Ftdi

from mks_motor import MKSMotor, prepare_usb_nodes, release_ftdi_sio

#: cell3's adapters. Same values as test_cell3_move_shinyeong.py; if the
#: bench is rewired, change them in both files.
SERIAL_X = "NTB3FXCE"
SERIAL_Z_A = "NTA4FH8Q"
SERIAL_Z_B = "NT9ZVXLU"

#: Axis convention inherited from cell1 (LearnedPatterns #4).
Z_COORD_INVERT = True

#: IO bits from CMD_READ_IO. Active low: a 0 bit means the switch is
#: closed, which is why every test below is `== 0` and not truthiness.
IN_1_HOME = 0x01
IN_2_FAR = 0x02

DEFAULT_SPEED_PCT = 10
DEFAULT_ACCEL_PCT = 0

#: The top of what an absolute move can ask for — `_mm_to_coord` clamps
#: to [0, 400] mm. See the docstring on why that matters here.
TOP_MM = 0.0

#: Cap on --descend-mm. The measurement only needs enough travel for the
#: carriage to leave its switch and come back; 30 mm is what the failures
#: were reproduced from, and a deep descent just adds time and risk.
MAX_DESCEND_MM = 60.0

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_FAULT = 2


def _switch_text(status: int | None) -> str:
    """Describe one motor's switch inputs, or say the read failed."""
    if status is None:
        return "IO unread (motor did not answer)"
    home = "CLOSED" if (status & IN_1_HOME) == 0 else "open  "
    far = "CLOSED" if (status & IN_2_FAR) == 0 else "open  "
    return f"IN_1(home) {home}   IN_2(far) {far}"


def _report(label: str, motor: MKSMotor) -> bool:
    """Print one axis's position and switch state. True if home is closed."""
    try:
        mm = motor.read_position_mm()
    except ConnectionError:
        mm = None
    status = motor._read_io_status()  # noqa: SLF001 — no public accessor
    pos = "unread " if mm is None else f"{mm:8.3f}"
    print(f"  {label:4s} {pos} mm   {_switch_text(status)}")
    return status is not None and (status & IN_1_HOME) == 0


def main(argv: list[str] | None = None) -> int:
    """Read cell3's home switches. Returns a process exit code."""
    parser = argparse.ArgumentParser(
        description="Read cell3's Z home switches (base-adjustment probe)."
    )
    parser.add_argument(
        "--to-zero",
        action="store_true",
        help="drive the Z pair up to encoder 0 before reading",
    )
    parser.add_argument(
        "--descend-mm",
        type=float,
        default=None,
        help=(
            "go down to this depth first, then climb back to 0 and read "
            f"(implies --to-zero; max {MAX_DESCEND_MM:g})"
        ),
    )
    parser.add_argument("--speed-pct", type=int, default=DEFAULT_SPEED_PCT)
    parser.add_argument("--accel-pct", type=int, default=DEFAULT_ACCEL_PCT)
    args = parser.parse_args(argv)

    if args.descend_mm is not None:
        if not 0 < args.descend_mm <= MAX_DESCEND_MM:
            print(f"[REFUSED] --descend-mm must be in (0, {MAX_DESCEND_MM:g}]")
            return EXIT_REFUSED
        # Descending and then not climbing back would leave the carriage
        # off its switch with nothing measured, which is the one outcome
        # this tool must never produce.
        args.to_zero = True

    present = [url.sn for url, _ in Ftdi.list_devices()]
    missing = [
        s for s in (SERIAL_X, SERIAL_Z_A, SERIAL_Z_B) if s not in present
    ]
    if missing:
        print(
            f"[REFUSED] cell3 adapter(s) not on the bus: {', '.join(missing)}"
        )
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

        print("\nbefore:")
        _report("Z_A", z_a)
        _report("Z_B", z_b)

        if args.descend_mm is not None:
            print(
                f"\n  descending the Z pair to {args.descend_mm:g} mm, so "
                f"the climb back is a real test and not a reading of where "
                f"someone left the carriage…"
            )
            MKSMotor.move_sync(
                pair,
                [(args.descend_mm, args.speed_pct, args.accel_pct)],
            )
            print("\nat depth:")
            _report("Z_A", z_a)
            _report("Z_B", z_b)

        if args.to_zero:
            print(
                f"\n  driving the Z pair to {TOP_MM:g} mm together "
                f"(move_sync — the pair cannot rack)…"
            )
            MKSMotor.move_sync(
                pair,
                [(TOP_MM, args.speed_pct, args.accel_pct)],
            )
            print("\nat the top:")
            a_closed = _report("Z_A", z_a)
            b_closed = _report("Z_B", z_b)

            print()
            if a_closed and b_closed:
                print(
                    "  BOTH HOME SWITCHES CLOSED. This base position "
                    "reaches the switch on each side — homing has a "
                    "stop condition now. Try a real gantry/home next."
                )
            elif b_closed and not a_closed:
                print(
                    "  Z_B CLOSED, Z_A OPEN — the known fault, unchanged. "
                    "Z_A still stops short of its switch at the top of "
                    "the addressable range. Adjust Z_A's base and run "
                    "this again; do not spend 250 s on a homing pass to "
                    "learn the same thing."
                )
            elif a_closed and not b_closed:
                print(
                    "  Z_A CLOSED, Z_B OPEN — the fault has MOVED to the "
                    "other side. If you just shifted Z_A's base, it has "
                    "gone past what Z_B can match; back it off."
                )
            else:
                print(
                    "  NEITHER SWITCH CLOSED at the top of travel. Both "
                    "sides stop short, so this is not a Z_A-only problem "
                    "— look at what limits the pair's upward travel "
                    "before adjusting either base."
                )

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
