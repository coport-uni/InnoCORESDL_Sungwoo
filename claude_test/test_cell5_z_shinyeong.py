#!/usr/bin/env python3
"""Drive cell5's single ball-screw Z (adapter NTB3EP5R) without a cell5 server.

WHY NOT THE SERVER. ``PumpZThermalCell.open`` refuses to start unless the
IKA hotplate is found (cell/pump_z_thermal_cell.py), and on 2026-10-08 the
bench has the pump and the Z motor but no hotplate. This drives the one
motor directly so the analysis station's height can be set.

Commands are given on the command line and run in order, each confirmed
by an encoder readback (LearnedPatterns #24: ``move_to`` prints a refusal
rather than raising it):

    pos            read the encoder, move nothing
    move:<mm>      absolute target in the CURRENT encoder frame
    home:<0|1>     switch-based homing in the given direction (``home_sync``
                   with a one-motor group). Only use once the switch end is
                   known: a missed switch keeps the motor turning for the
                   driver's full _max_wait_sec (cell2, 2026-10-01).

    ssh innocore_nuc_2
    cd ~/workspace/InnoCOREServer/InnoCORESDL_shinyeong
    .venv/bin/python claude_test/test_cell5_z_shinyeong.py pos move:5 pos

THE ENCODER FRAME IS WHEREVER THE MOTOR POWERED UP (or last homed). With
no homing yet, 0 is simply the current position, so the first move should
be small and watched: it tells which way +mm goes on this stage.

SAFETY. Targets are clamped to ``--max-mm`` from 0; speed is low by
default; the motor is sent a hard stop on any exception. The operator
holds the e-stop — a gantry stop queues behind the move it interrupts
(docs/L1_AUDIT.md GAP-9).
"""

from __future__ import annotations

import argparse
import sys

from mks_motor import MKSMotor, prepare_usb_nodes, release_ftdi_sio

SERIAL_Z = "NTB3EP5R"  # server/nuc2/cell5.toml.example [zstage]
Z_COORD_INVERT = True  # +mm moves away from the 0x00 home limit (same example)
ARRIVAL_TOLERANCE_MM = 0.5


def _read(motor: MKSMotor) -> float:
    mm = motor.read_position_mm()
    if mm is None:
        raise ConnectionError("Z did not answer an encoder read")
    return mm


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="cell5 single Z, direct drive."
    )
    parser.add_argument(
        "commands", nargs="+", help="pos | move:<mm> | home:<0|1>"
    )
    parser.add_argument("--max-mm", type=float, default=150.0)
    parser.add_argument("--speed-pct", type=int, default=10)
    parser.add_argument("--accel-pct", type=int, default=0)
    parser.add_argument(
        "--max-travel",
        type=float,
        default=None,
        help="override the driver instance's _max_travel_mm (class default 400). cell5's lower switch sits "
        "~422.6 mm below the upper (home) switch, measured 2026-10-12, so the dispense point needs ~450.",
    )
    parser.add_argument(
        "--down-positive",
        action="store_true",
        help="flip the frame so +mm moves DOWN (measured 2026-10-08: with the default, +mm is up). "
        "Use after a power-cycle at the top, since the driver refuses negative mm.",
    )
    args = parser.parse_args(argv)

    prepare_usb_nodes()
    release_ftdi_sio()
    motor = MKSMotor.open(
        serial=SERIAL_Z,
        coord_invert=not Z_COORD_INVERT
        if args.down_positive
        else Z_COORD_INVERT,
    )
    if args.max_travel is not None:
        motor._max_travel_mm = args.max_travel  # noqa: SLF001 — instance only; the driver is a submodule
    code = 0
    try:
        if not motor.setup():
            print("[REFUSED] setup() failed — check motor power / CAN")
            return 2
        for cmd in args.commands:
            name, _, arg = cmd.partition(":")
            if name == "pos":
                print(f"  pos: Z {_read(motor):.3f} mm")
            elif name == "move":
                target = float(arg)
                if not -args.max_mm <= target <= args.max_mm:
                    print(f"[REFUSED] {target} outside +-{args.max_mm} mm")
                    code = 1
                    break
                start = _read(motor)
                MKSMotor.move_sync(
                    [motor], [(target, args.speed_pct, args.accel_pct)]
                )
                reached = _read(motor)
                print(
                    f"  move: {start:.3f} -> commanded {target:g}, reads {reached:.3f} mm (moved {reached - start:+.3f})"
                )
                if abs(reached - target) > ARRIVAL_TOLERANCE_MM:
                    print(
                        "[FAULT] did not arrive — limit, binding or dropped command"
                    )
                    code = 2
                    break
            elif name == "home":
                direction = int(arg)
                print(f"  home: direction {direction} (switch-based)")
                MKSMotor.home_sync([motor], direction=direction)
                print(f"  home: done, Z reads {_read(motor):.3f} mm")
            else:
                print(f"[REFUSED] unknown command {cmd!r}")
                code = 1
                break
    except Exception as exc:  # noqa: BLE001 — always stop the axis
        print(f"[ERROR] {type(exc).__name__}: {exc}")
        code = 2
    finally:
        try:
            MKSMotor.stop_group_hard([motor])
        except Exception:  # noqa: BLE001
            print("[SAFETY] could not confirm a stop — CUT POWER.")
        motor.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
