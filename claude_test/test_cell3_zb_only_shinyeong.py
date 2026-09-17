#!/usr/bin/env python3
"""Move cell3's Z_B alone, to prove that motor still follows a command.

DIAGNOSTIC ONLY, AND IT DELIBERATELY BREAKS THE PAIRED-Z INTERLOCK.

cell3's two vertical axes are joined by a crossbeam and are normally
driven only through ``move_sync`` / ``home_xz``, which keep them
together (CLAUDE.md folder rule 3). This tool drives ONE of them. Every
millimetre it travels is a millimetre of rack across that beam, because
Z_A stays where it is — a ball-screw does not back-drive.

It exists for one state, reached on 2026-09-02: Z_A (NTA4FH8Q) stopped
answering the cell server while X and Z_B kept reading 0.000 mm. With
one axis silent the server refuses to serve at all, and
``test_cell3_move_shinyeong.py`` only ever moves the Z pair, so there
was no way left to ask "does Z_B still MOVE, or only still TALK?" —
answering an encoder read proves the CAN link, not the mechanics.

WHY THE DEFAULT IS 3 mm. That is enough travel to be unmistakable on
the encoder and by eye, and small enough that the frame absorbs it: the
one racking event on this bench that caused trouble was 1.40 mm, and it
needed the base plate loosened and the column re-squared afterwards.
``--mm`` will go further, and ``MAX_MM`` caps it, but every extra
millimetre is deflection you have to undo.

RUN IT, THEN PUT IT BACK. The tool prints the starting position and
finishes by offering the exact command to return there. Leaving the
gantry racked is how the next run starts crooked.

The cell3 server must be stopped first — one owner per adapter
(CLAUDE.md folder rule 2). The server refuses to start while Z_A is
silent, so in the state this tool is for, nothing holds the adapters.

    ssh innocore_nuc_2
    cd ~/workspace/InnoCOREServer/InnoCORESDL_shinyeong
    .venv/bin/python claude_test/test_cell3_zb_only_shinyeong.py --mm 3

SAFETY. The operator stays at the bench with the e-stop: a gantry stop
queues behind the move it means to interrupt (docs/L1_AUDIT.md GAP-9),
so the physical switch is the only one that acts at once.
"""

from __future__ import annotations

import argparse
import sys

from pyftdi.ftdi import Ftdi

from mks_motor import MKSMotor, prepare_usb_nodes, release_ftdi_sio

#: cell3's Z_B adapter. Same value as test_cell3_move_shinyeong.py; if
#: the bench is rewired, change it in both files.
SERIAL_Z_B = "NT9ZVXLU"

#: Axis convention inherited from cell1 (``PumpGantryCell.Config``): Z
#: homes at the 0x00 end and carries coord_invert, so +mm travels down
#: into the working envelope. An axis that stops dead at ~0 mm is this
#: setting, not a fault (LearnedPatterns #4).
Z_COORD_INVERT = True

#: Matches the cell layer, and for the same reason: sized to catch a
#: move that was dropped, not to grade positioning accuracy.
ARRIVAL_TOLERANCE_MM = 0.5

DEFAULT_SPEED_PCT = 10
DEFAULT_ACCEL_PCT = 0

#: Small by intent — see the module docstring. This is a diagnostic, not
#: a way to reposition the axis.
DEFAULT_MM = 3.0
MAX_MM = 10.0

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_FAULT = 2


class MoveError(Exception):
    """The bench refused, or the axis did not behave."""


def _read_mm(motor: MKSMotor) -> float | None:
    """Z_B's encoder position in mm, or None if the read failed."""
    try:
        return motor.read_position_mm()
    except ConnectionError:
        return None


def _read_mm_or_raise(motor: MKSMotor) -> float:
    """Read Z_B, or say why the move cannot be judged.

    Raises:
        MoveError: If the axis did not answer.
    """
    mm = _read_mm(motor)
    if mm is None:
        raise MoveError(
            "Z_B did not answer an encoder read. Its position is "
            "unknown, so nothing here can be confirmed — check the CAN "
            "link before driving a racked pair any further."
        )
    return mm


def main(argv: list[str] | None = None) -> int:
    """Move Z_B alone and confirm it arrived. Returns an exit code."""
    parser = argparse.ArgumentParser(
        description="Move cell3's Z_B alone (racks the gantry; diagnostic)."
    )
    parser.add_argument(
        "--mm",
        type=float,
        default=DEFAULT_MM,
        help=f"absolute Z_B target in mm (default {DEFAULT_MM:g}, "
        f"max {MAX_MM:g})",
    )
    parser.add_argument("--speed-pct", type=int, default=DEFAULT_SPEED_PCT)
    parser.add_argument("--accel-pct", type=int, default=DEFAULT_ACCEL_PCT)
    args = parser.parse_args(argv)

    if not 0 <= args.mm <= MAX_MM:
        print(f"[REFUSED] --mm must be in [0, {MAX_MM:g}]")
        return EXIT_REFUSED

    print(
        f"cell3 Z_B ALONE -> {args.mm:g} mm at {args.speed_pct}% speed.\n"
        f"THIS RACKS THE GANTRY. Z_A does not move, so the crossbeam "
        f"takes the whole difference. Hold the e-stop and watch the "
        f"frame, not the terminal.\n"
    )

    present = [url.sn for url, _ in Ftdi.list_devices()]
    if SERIAL_Z_B not in present:
        print(f"[REFUSED] cell3's Z_B adapter {SERIAL_Z_B} is not on the bus.")
        return EXIT_REFUSED

    prepare_usb_nodes()
    release_ftdi_sio()
    try:
        motor = MKSMotor.open(serial=SERIAL_Z_B, coord_invert=Z_COORD_INVERT)
    except Exception as exc:  # noqa: BLE001 — most likely the server
        print(
            f"[REFUSED] could not open {SERIAL_Z_B}: {exc}\n"
            f"  cell3's server is probably holding it. Stop it first:\n"
            f"    pgrep -f 'cell[3]' | xargs -r kill"
        )
        return EXIT_REFUSED

    code = EXIT_OK
    start: float | None = None
    try:
        # Without setup() the firmware ignores the move outright — the
        # same reason the cell server calls it on every motor at open.
        motor.setup()
        start = _read_mm_or_raise(motor)
        print(f"  start: Z_B {start:.3f} mm")

        motor.move_to(
            args.mm, speed_pct=args.speed_pct, accel_pct=args.accel_pct
        )

        # CONFIRM BY READBACK, NOT BY RETURN. move_to PRINTS a refusal
        # rather than raising it (LearnedPatterns #24), so a command the
        # firmware dropped looks exactly like one it obeyed until the
        # encoder is asked.
        reached = _read_mm_or_raise(motor)
        moved = reached - start
        print(f"  after: Z_B {reached:.3f} mm  (moved {moved:+.3f} mm)")

        if abs(reached - args.mm) > ARRIVAL_TOLERANCE_MM:
            raise MoveError(
                f"Z_B did not arrive: commanded {args.mm:g} mm, reads "
                f"{reached:.3f} mm. It talks but does not follow — the "
                f"CAN link is fine and the fault is mechanical, or the "
                f"axis was driven into a limit."
            )
        print(
            f"\n  Z_B FOLLOWS COMMANDS. It moved {moved:+.3f} mm and "
            f"arrived within {ARRIVAL_TOLERANCE_MM:g} mm."
        )

    except MoveError as exc:
        print(f"\n[FAULT] {exc}")
        code = EXIT_FAULT
    except Exception as exc:  # noqa: BLE001 — always stop the axis
        print(f"\n[ERROR] {type(exc).__name__}: {exc}")
        code = EXIT_FAULT
    finally:
        try:
            MKSMotor.stop_group_hard([motor])
        except Exception:  # noqa: BLE001
            print("[SAFETY] could not confirm a stop — CUT POWER.")
        motor.close()

    if start is not None:
        print(
            f"\nTHE GANTRY IS NOW RACKED. Put it back before anything "
            f"else runs on cell3:\n"
            f"    .venv/bin/python claude_test/"
            f"test_cell3_zb_only_shinyeong.py --mm {start:.3f}"
        )
    return code


if __name__ == "__main__":
    sys.exit(main())
