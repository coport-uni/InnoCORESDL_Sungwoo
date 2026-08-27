#!/usr/bin/env python3
"""Jog cell2's X axis a RELATIVE distance, when absolute moves cannot.

Recovery tool for one specific state, reached on 2026-08-13:

* cell2's X end-stop switch came off its mount, so homing drove past
  where the switch should have been, never saw it, and reported
  ``Motor not responding`` mid-motion. X's encoder zero was therefore
  never established.
* The axis now reads **-784 mm**. That number is not a position on this
  rail; it is a count from an origin that was set wherever the carriage
  happened to sit when the amp last powered up.
* The carriage has to move about +100 mm to free the mount so the switch
  can be re-attached.

``/v1/gantry/move`` cannot express that move. ``MKSMotor._mm_to_coord``
**clamps its argument to [0, 400] mm**, and ``GantryMoveRequest.x_mm``
requires ``ge=0``, so the smallest legal command from -784 mm is a
784 mm traverse. An absolute interface is useless when the coordinate
frame is meaningless — the move has to be relative.

So this jogs in speed mode (F6) and closes the loop on the encoder
itself: short bursts, read the delta after each, stop when the target
distance is covered. Nothing here depends on a jog lasting exactly as
long as it was asked to, which is the failure mode a timed jog has.

WHY IT TALKS TO THE DRIVER AND NOT THE SERVER: the cell layer exposes
no relative move, by design. So cell2's server MUST BE STOPPED before
running this — one owner per adapter. The tool refuses to start if
something already holds cell2's X adapter.

    ssh innocore_nuc_2
    pkill -f 'shinyeong.py --cell cell2'
    cd ~/workspace/InnoCOREServer/InnoCORESDL_shinyeong
    .venv/bin/python claude_test/test_cell2_x_jog_shinyeong.py --mm 100

Afterwards: re-attach the switch, restart cell2's server, then home the
gantry so X finally gets a real origin.

SAFETY. This drives an axis whose limit switch is DETACHED, so the one
protection that would normally stop an over-travel is absent. Hence:
bursts are short, the total is capped by ``--mm``, the jog is stopped
after every burst rather than left running, and the operator holds the
e-stop. Direction follows the cell's own convention (+mm travels away
from the home end, cell1's verified sense); if the carriage starts
moving the wrong way, e-stop and pass ``--negative``.
"""

from __future__ import annotations

import argparse
import sys
import time

from pyftdi.ftdi import Ftdi

from mks_motor import MKSMotor, prepare_usb_nodes, release_ftdi_sio

#: cell2's X adapter. The axis whose switch fell off; its two Z motors
#: homed cleanly on 2026-08-13 and are not touched here.
SERIAL_X = "NTB19XKA"

#: Matches the cell config, so "+mm" here means what "+mm" means to
#: /v1/gantry/move: away from the home end.
X_COORD_INVERT = True

#: Slow. This axis is being driven with no working end-stop.
DEFAULT_SPEED_RPM = 60
JOG_ACCEL = 0

#: One burst. Short enough that a burst at DEFAULT_SPEED_RPM covers a
#: few millimetres, so overshoot past the target stays small.
BURST_S = 0.5

#: Give up rather than jog forever if the encoder never advances — a
#: dropped CAN frame or a mechanically stuck axis both land here.
MAX_BURSTS = 200
STALL_BURSTS = 6

#: How close to the requested distance counts as done.
ARRIVE_MM = 2.0

#: Direction probe. Short enough that going the wrong way costs a couple
#: of millimetres on an axis with no end-stop, long enough that the
#: encoder change is unambiguous.
PROBE_S = 0.25

#: Below this, the probe is treated as "did not move" rather than as a
#: direction reading — a jammed axis and a mis-set direction look the
#: same at a fraction of a millimetre.
PROBE_MIN_MM = 0.3

DEFAULT_MM = 100.0
MAX_MM = 200.0

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_FAULT = 2


class JogError(Exception):
    """The bench refused, or the axis did not behave."""


def _read_mm(motor: MKSMotor) -> float | None:
    """X's encoder position in mm, or None if the read failed."""
    try:
        return motor.read_position_mm()
    except ConnectionError:
        return None


def _read_mm_retry(motor: MKSMotor, tries: int = 4) -> float:
    """Read X, retrying — a lost frame must not look like no motion.

    Raises:
        JogError: If every attempt failed.
    """
    for _ in range(tries):
        mm = _read_mm(motor)
        if mm is not None:
            return mm
        time.sleep(0.1)
    raise JogError(
        "X did not answer an encoder read. Its position is unknown, so "
        "the jog cannot be closed against it — stop and check the CAN "
        "link before driving an axis with no end-stop."
    )


def main(argv: list[str] | None = None) -> int:
    """Jog X the requested distance. Returns a process exit code."""
    parser = argparse.ArgumentParser(
        description="Relative jog of cell2's X axis (recovery tool)."
    )
    parser.add_argument(
        "--mm",
        type=float,
        default=DEFAULT_MM,
        help=f"distance to travel (default {DEFAULT_MM:g}, max {MAX_MM:g})",
    )
    parser.add_argument(
        "--negative",
        action="store_true",
        help="jog toward the home end instead of away from it",
    )
    parser.add_argument("--speed-rpm", type=int, default=DEFAULT_SPEED_RPM)
    args = parser.parse_args(argv)

    if not 0 < args.mm <= MAX_MM:
        print(f"[REFUSED] --mm must be in (0, {MAX_MM:g}]")
        return EXIT_REFUSED

    direction = "toward home" if args.negative else "away from home"
    print(
        f"cell2 X relative jog — {args.mm:g} mm {direction}, "
        f"{args.speed_rpm} RPM in {BURST_S:g} s bursts.\n"
        f"THE END-STOP SWITCH ON THIS AXIS IS DETACHED. Nothing will stop "
        f"an over-travel except you: hold the e-stop, watch the carriage, "
        f"and cut power if it heads the wrong way.\n"
    )

    present = [url.sn for url, _ in Ftdi.list_devices()]
    if SERIAL_X not in present:
        print(f"[REFUSED] cell2's X adapter {SERIAL_X} is not on the bus.")
        return EXIT_REFUSED

    prepare_usb_nodes()
    release_ftdi_sio()
    try:
        motor = MKSMotor.open(serial=SERIAL_X, coord_invert=X_COORD_INVERT)
    except Exception as exc:  # noqa: BLE001 — most likely the server
        print(
            f"[REFUSED] could not open {SERIAL_X}: {exc}\n"
            f"  cell2's server is probably still running and holding it. "
            f"Stop it first: pkill -f 'shinyeong.py --cell cell2'"
        )
        return EXIT_REFUSED

    code = EXIT_OK
    try:
        motor.setup()
        start = _read_mm_retry(motor)
        print(f"  start: {start:.3f} mm (an arbitrary origin — see above)")
        target = args.mm
        travelled = 0.0
        stalled = 0

        # WHICH WAY IS "+"? MEASURED, NOT ASSUMED.
        #
        # `coord_invert` flips F5 absolute moves so that move_to(+mm)
        # travels away from the home limit. It does NOT touch F6 jog —
        # MKSMotor.__init__ says so outright ("F6 jog is NOT affected").
        # So the CW/CCW bit that corresponds to "+mm" cannot be derived
        # from the config; `jog_start` takes a separate `invert` flag
        # precisely because it is a per-axis wiring fact.
        #
        # Guessing it is unacceptable here: this axis has NO END-STOP, so
        # a wrong guess drives it further into the stop it is already
        # jammed against. Instead, probe: one short burst, then read.
        # `read_position_mm` applies coord_invert to what it returns, so
        # the reported mm is in the same sense as move_to's argument —
        # away from home therefore reads as an INCREASE.
        cw = not args.negative
        probe_from = start
        MKSMotor.jog_start(
            [motor],
            positive=cw,
            invert=False,
            speed_rpm=args.speed_rpm,
            accel=JOG_ACCEL,
        )
        time.sleep(PROBE_S)
        MKSMotor.jog_stop([motor], accel=JOG_ACCEL)
        time.sleep(0.15)
        probe_to = _read_mm_retry(motor)
        delta = probe_to - probe_from
        want_increase = not args.negative
        print(
            f"  probe: {probe_from:.3f} -> {probe_to:.3f} mm "
            f"(delta {delta:+.3f})"
        )

        if abs(delta) < PROBE_MIN_MM:
            print(
                "  probe moved almost nothing — the axis may already be "
                "against a hard stop in this direction. Flipping and "
                "probing the other way."
            )
            cw = not cw
        elif (delta > 0) != want_increase:
            print(
                f"  WRONG WAY: reported mm went {'up' if delta > 0 else 'down'} "
                f"when it should have gone {'up' if want_increase else 'down'}. "
                f"Flipping the jog direction."
            )
            cw = not cw
        else:
            print("  direction confirmed.")
        # The probe itself may have moved a little; measure from here on
        # against the position after it, so `travelled` counts only
        # motion in the direction finally chosen.
        start = _read_mm_retry(motor)

        for burst in range(1, MAX_BURSTS + 1):
            MKSMotor.jog_start(
                [motor],
                positive=cw,
                invert=False,
                speed_rpm=args.speed_rpm,
                accel=JOG_ACCEL,
            )
            time.sleep(BURST_S)
            MKSMotor.jog_stop([motor], accel=JOG_ACCEL)
            time.sleep(0.15)

            here = _read_mm_retry(motor)
            moved = abs(here - start)
            step = moved - travelled
            travelled = moved
            print(
                f"  burst {burst:3d}: {here:9.3f} mm  "
                f"travelled {travelled:7.3f} / {target:g} mm"
            )

            if target - travelled <= ARRIVE_MM:
                print(f"\n  arrived: {travelled:.3f} mm travelled")
                break
            # A burst that moved nothing means the axis is not following.
            # Six in a row is not noise, and continuing would keep
            # commanding an axis that cannot move.
            stalled = stalled + 1 if step < 0.05 else 0
            if stalled >= STALL_BURSTS:
                raise JogError(
                    f"X stopped advancing after {travelled:.3f} mm "
                    f"({STALL_BURSTS} bursts with no motion). It is "
                    f"mechanically blocked, at a hard stop, or its CAN "
                    f"link is dropping under load — do not keep driving."
                )
        else:
            raise JogError(
                f"gave up after {MAX_BURSTS} bursts having travelled "
                f"{travelled:.3f} mm of {target:g} mm"
            )

    except JogError as exc:
        print(f"\n[FAULT] {exc}")
        code = EXIT_FAULT
    except Exception as exc:  # noqa: BLE001 — always stop the axis
        print(f"\n[ERROR] {type(exc).__name__}: {exc}")
        code = EXIT_FAULT
    finally:
        # Belt and braces: the loop stops after every burst, but an
        # exception mid-burst would otherwise leave the axis running.
        try:
            MKSMotor.stop_group_hard([motor])
        except Exception:  # noqa: BLE001
            print("[SAFETY] could not confirm a stop — CUT POWER.")
        motor.close()

    print(
        "\nNext: re-attach the end-stop switch, restart cell2's server, "
        "then POST /v1/gantry/home so X gets a real origin. Until it is "
        "homed, every X coordinate on cell2 is still meaningless."
    )
    return code


if __name__ == "__main__":
    sys.exit(main())
