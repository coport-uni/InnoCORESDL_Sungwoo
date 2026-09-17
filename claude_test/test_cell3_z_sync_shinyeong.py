#!/usr/bin/env python3
"""Measure how far cell3's two Z motors drift apart WHILE THEY MOVE.

The pair reads 0.0000 mm apart at rest after every move, so the standing
desync check says they are perfectly matched. The operator's eye says
otherwise: with the gantry laid horizontal on 2026-09-02 — deliberately,
to take gravity out of the comparison — Z_A visibly lags Z_B. A number
that only exists at the end of a move cannot see that, because both sides
arrive eventually.

So this jogs the pair and samples both encoders during the motion.

WHY IT DOES NOT USE move_to. ``move_sync`` blocks until the move ends and
consumes the motors' response frames while it waits, so there is no safe
moment to read an encoder mid-move. ``jog_start`` returns immediately and
leaves the axes running, which makes the whole motion available for
sampling.

THE SAMPLING BIAS THIS CORRECTS, WHICH IS BIGGER THAN THE EFFECT.
One encoder read costs roughly a quarter of a second. Reading Z_A then
Z_B therefore compares two positions a quarter-second apart, and at even
a slow jog that alone puts them millimetres apart — far more than the lag
being measured, and in a fixed direction, so it would look exactly like a
real result. Every sample here is A, then B, then A again, and the two A
readings are interpolated to B's timestamp before subtracting. What is
left is the difference between the axes rather than between the clocks.

The stationary baseline printed first is the check on that: with nothing
moving, the corrected difference must be ~0. If it is not, the numbers
below it mean nothing.

    ssh innocore_nuc_2
    cd ~/workspace/InnoCOREServer/InnoCORESDL_shinyeong
    .venv/bin/python claude_test/test_cell3_z_sync_shinyeong.py
    .venv/bin/python claude_test/test_cell3_z_sync_shinyeong.py --travel-mm 100

THE SECOND FORM MEASURES WHAT THE ENCODERS CANNOT SEE, and on this fault
that is the whole question. The first run of this tool, on 2026-09-02,
found the two encoders tracking each other to 0.0725 mm over 42 mm of
travel — nothing remotely like the lag the operator can see by eye. Both
encoders sit on their motor shafts, upstream of the coupling and the ball
screw, so a motor that turns correctly while its carriage slips or binds
reads perfectly. The instrument is blind to exactly the failure being
hunted.

``--travel-mm N`` moves the pair a commanded N mm and prints what each
encoder says it travelled. Then measure each carriage against a rule. The
gap between the encoder figure and the physical one is the loss in that
side's drive train, and comparing the two sides says which one loses it.
100 mm is a good N: a round number to read off a rule, and long enough
that a slip stands well clear of how finely anyone can read a scale.

THERE IS NO ORIGIN OF ITS OWN HERE, WHICH MATTERS MORE THAN IT SOUNDS.
Every target below is an absolute coordinate in each motor's OWN encoder
frame, and the two motors carry two separate zeros — each set either by
that motor's last successful homing (at its own switch) or by a power-up,
wherever it happened to be standing. This tool inherits whichever zeros
exist and never checks them.

So the two zeros mean the same physical height only just after a homing
whose switches sit level, or after a hand-alignment and a power cycle.
Let some slip accumulate and a zero stays 0 while the place it points at
moves: commanding both sides to the same number then parks them at
different heights, and the descent looks wrong when it was the reference
that drifted. Measured on 2026-09-02 — both carriages held against the
same end-stop, Z_A reading 20.08 mm and Z_B 1.51 mm.

``--home-first`` re-establishes both zeros before measuring, which is what
makes two runs comparable; without it each run starts from whatever the
last one left behind. It costs a homing pass, and while the binding fault
is active that pass can sit for the driver's full 250 s.

``--speed-pct`` sets the speed of every move here. Lower is not merely
gentler — a stepper's available torque FALLS as speed rises, so low speed
is the knob to reach for when an axis is suspected of binding, and raising
it is the wrong instinct.

The cell3 server must be stopped first — one owner per adapter
(CLAUDE.md folder rule 2):

    pgrep -f 'cell[3]' | xargs -r kill

SAFETY. Both Z motors are driven together through ``jog_start`` so the
pair cannot rack by command, and the run is bounded twice: by ``--seconds``
and by ``--max-mm`` of travel from the start, checked every sample. The
axes are stopped after every burst rather than left running, and the
operator holds the e-stop — a gantry stop queues behind the move it means
to interrupt (docs/L1_AUDIT.md GAP-9).
"""

from __future__ import annotations

import argparse
import sys
import time

from pyftdi.ftdi import Ftdi

from mks_motor import MKSMotor, prepare_usb_nodes, release_ftdi_sio

#: cell3's Z adapters. Same values as test_cell3_move_shinyeong.py.
SERIAL_Z_A = "NTA4FH8Q"
SERIAL_Z_B = "NT9ZVXLU"

#: Axis convention inherited from cell1 (LearnedPatterns #4).
Z_COORD_INVERT = True

#: Slow. A lag is easier to see against less travel per sample, and the
#: gantry is on its side with the fault not yet understood.
DEFAULT_SPEED_RPM = 60
JOG_ACCEL = 0

DEFAULT_SECONDS = 6.0
MAX_SECONDS = 20.0

#: Travel cap, checked every sample. Independent of the time cap so a
#: faster-than-expected axis still stops in a known place.
DEFAULT_MAX_MM = 40.0

#: Samples taken with nothing moving, to prove the interpolation returns
#: ~0 before any moving number is believed.
BASELINE_SAMPLES = 4

#: How much the stationary A-B difference may WANDER between samples
#: before the interpolation is judged unusable.
#:
#: It is the spread that matters here, not the size of the difference.
#: An early version compared |A-B| itself against this, which was wrong
#: twice over: a pair that is genuinely racked reads a large constant
#: difference while the method works perfectly, and that is exactly the
#: state --to-mm exists to repair — so the guard refused the one job it
#: was most needed for. What proves the read-latency correction is doing
#: its work is that repeated stationary samples agree with each other.
BASELINE_SPREAD_MM = 0.05

#: --travel-mm bounds. `_mm_to_coord` clamps an absolute target to
#: [0, 400] mm, so a move that would land outside that is refused here
#: rather than silently truncated into a shorter one — which would make
#: the encoder figure a lie exactly where it is being trusted.
AXIS_MIN_MM = 0.0
AXIS_MAX_MM = 400.0
MAX_TRAVEL_MM = 200.0

DEFAULT_MOVE_SPEED_PCT = 10
DEFAULT_MOVE_ACCEL_PCT = 0

#: Homing direction for Z, matching the cell config (LearnedPatterns #4).
HOME_DIR_Z = 0x00

#: --cycles endpoints. NEITHER IS 0, deliberately. A leg that ends on a
#: limit switch has its error absorbed by the switch instead of carried
#: forward: the axis stops where the switch says rather than where its
#: encoder says, so each cycle would start from a re-established
#: reference and the accumulation this mode exists to build would be
#: wiped out every lap. Both endpoints sit clear of the switches.
DEFAULT_LOW_MM = 10.0
DEFAULT_HIGH_MM = 110.0
MAX_CYCLES = 20

#: Direction probe, as in test_cell2_x_jog_shinyeong.py: `coord_invert`
#: flips F5 absolute moves but NOT the F6 jog bit, so which way "+" goes
#: is a wiring fact that has to be measured rather than assumed.
PROBE_S = 0.3
PROBE_MIN_MM = 0.2

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_FAULT = 2


class SyncError(Exception):
    """The bench refused, or an axis stopped answering."""


def _read(motor: MKSMotor) -> float:
    """One encoder reading in mm.

    Raises:
        SyncError: If the motor did not answer.
    """
    try:
        mm = motor.read_position_mm()
    except ConnectionError as exc:
        raise SyncError(f"an axis stopped answering mid-measurement: {exc}")
    if mm is None:
        raise SyncError("an axis returned a stray frame instead of a position")
    return mm


def _sample(z_a: MKSMotor, z_b: MKSMotor) -> tuple[float, float, float]:
    """Read the pair once, correcting for the gap between the two reads.

    Reads A, then B, then A again, and interpolates the two A readings to
    B's timestamp. See the module docstring: without this the read latency
    dominates the quantity being measured.

    Returns:
        ``(t_b, a_at_t_b, b)`` — B's timestamp, A interpolated to it, and
        B's own reading, all in mm and seconds.
    """
    t0 = time.monotonic()
    a0 = _read(z_a)
    t1 = time.monotonic()
    b = _read(z_b)
    t2 = time.monotonic()
    a1 = _read(z_a)
    t3 = time.monotonic()

    # B's reading is centred at the midpoint of its own transaction, and
    # so are A's; interpolate A between its two midpoints.
    tb = (t1 + t2) / 2
    ta0 = (t0 + t1) / 2
    ta1 = (t2 + t3) / 2
    span = ta1 - ta0
    frac = 0.0 if span <= 0 else (tb - ta0) / span
    return tb, a0 + (a1 - a0) * frac, b


def main(argv: list[str] | None = None) -> int:
    """Measure the pair's mid-motion lag. Returns a process exit code."""
    parser = argparse.ArgumentParser(
        description="Measure cell3's Z_A/Z_B lag during motion."
    )
    parser.add_argument("--seconds", type=float, default=DEFAULT_SECONDS)
    parser.add_argument("--speed-rpm", type=int, default=DEFAULT_SPEED_RPM)
    parser.add_argument("--max-mm", type=float, default=DEFAULT_MAX_MM)
    parser.add_argument(
        "--toward-home",
        action="store_true",
        help="jog toward the home end instead of away from it",
    )
    parser.add_argument(
        "--home-first",
        action="store_true",
        help=(
            "home both Z motors before measuring, so this run starts from "
            "a freshly established zero instead of an inherited one"
        ),
    )
    parser.add_argument(
        "--speed-pct",
        type=int,
        default=DEFAULT_MOVE_SPEED_PCT,
        help=(
            f"speed for every move here, 1-100 (default "
            f"{DEFAULT_MOVE_SPEED_PCT}); lower means more torque"
        ),
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=None,
        help=(
            f"run this many low->high->low laps, accumulating any per-lap "
            f"loss into something visible by eye (max {MAX_CYCLES})"
        ),
    )
    parser.add_argument("--low-mm", type=float, default=DEFAULT_LOW_MM)
    parser.add_argument("--high-mm", type=float, default=DEFAULT_HIGH_MM)
    parser.add_argument(
        "--to-mm",
        type=float,
        default=None,
        help=(
            "send BOTH axes to this one absolute position, which squares "
            "the pair; use it to undo a rack (max "
            f"{AXIS_MAX_MM:g})"
        ),
    )
    parser.add_argument(
        "--travel-mm",
        type=float,
        default=None,
        help=(
            "instead of the jog measurement, move the pair this far and "
            "report each encoder's travel, for comparison against a rule "
            f"(negative goes back; max {MAX_TRAVEL_MM:g})"
        ),
    )
    args = parser.parse_args(argv)

    if not 0 < args.seconds <= MAX_SECONDS:
        print(f"[REFUSED] --seconds must be in (0, {MAX_SECONDS:g}]")
        return EXIT_REFUSED

    if not 1 <= args.speed_pct <= 100:
        print("[REFUSED] --speed-pct must be in [1, 100]")
        return EXIT_REFUSED

    if args.cycles is not None:
        if not 0 < args.cycles <= MAX_CYCLES:
            print(f"[REFUSED] --cycles must be in (0, {MAX_CYCLES}]")
            return EXIT_REFUSED
        for name, value in (
            ("--low-mm", args.low_mm),
            ("--high-mm", args.high_mm),
        ):
            if not AXIS_MIN_MM <= value <= AXIS_MAX_MM:
                print(
                    f"[REFUSED] {name} must be in "
                    f"[{AXIS_MIN_MM:g}, {AXIS_MAX_MM:g}]"
                )
                return EXIT_REFUSED
        if args.high_mm <= args.low_mm:
            print("[REFUSED] --high-mm must be above --low-mm")
            return EXIT_REFUSED

    if args.to_mm is not None and not (
        AXIS_MIN_MM <= args.to_mm <= AXIS_MAX_MM
    ):
        print(
            f"[REFUSED] --to-mm must be in [{AXIS_MIN_MM:g}, {AXIS_MAX_MM:g}]"
        )
        return EXIT_REFUSED

    if args.travel_mm is not None and not (
        0 < abs(args.travel_mm) <= MAX_TRAVEL_MM
    ):
        print(
            f"[REFUSED] --travel-mm must be non-zero and at most "
            f"{MAX_TRAVEL_MM:g} mm in magnitude"
        )
        return EXIT_REFUSED

    present = [url.sn for url, _ in Ftdi.list_devices()]
    missing = [s for s in (SERIAL_Z_A, SERIAL_Z_B) if s not in present]
    if missing:
        print(f"[REFUSED] adapter(s) not on the bus: {', '.join(missing)}")
        return EXIT_REFUSED

    print(
        f"cell3 Z pair — jogging {args.seconds:g} s at {args.speed_rpm} RPM "
        f"and sampling both encoders throughout.\n"
        f"Both motors are driven together, so the pair cannot rack by "
        f"command. Travel is capped at {args.max_mm:g} mm. Hold the "
        f"e-stop.\n"
    )

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

        if args.home_first:
            print(
                "  homing both Z motors first, so this run's zero is its "
                "own and not whatever the last run left behind…\n"
                "  (while the binding fault is active this can sit for up "
                "to 250 s before the driver gives up)"
            )
            MKSMotor.home_sync(pair, direction=HOME_DIR_Z)
            _, a_h, b_h = _sample(z_a, z_b)
            print(f"  homed: Z_A {a_h:.4f}   Z_B {b_h:.4f}\n")

        # ── baseline: repeated stationary samples must AGREE ──────────
        print("stationary baseline (the check on the method itself):")
        diffs = []
        for _ in range(BASELINE_SAMPLES):
            _, a, b = _sample(z_a, z_b)
            diff = a - b
            diffs.append(diff)
            print(f"    Z_A {a:9.4f}   Z_B {b:9.4f}   A-B {diff:+8.4f}")
        spread = max(diffs) - min(diffs)
        standing = sum(diffs) / len(diffs)
        if spread > BASELINE_SPREAD_MM:
            raise SyncError(
                f"the stationary A-B difference wandered by {spread:.4f} mm "
                f"across {BASELINE_SAMPLES} samples, above the "
                f"{BASELINE_SPREAD_MM:g} mm this method needs. Something is "
                f"moving while nothing is commanded, so no figure below "
                f"would mean anything."
            )
        print(
            f"  spread {spread:.4f} mm across {BASELINE_SAMPLES} samples "
            f"— the correction is working."
        )
        if abs(standing) > BASELINE_SPREAD_MM:
            print(
                f"  NOTE: the pair is standing {standing:+.4f} mm apart. "
                f"That is a rack, not a measurement error — the samples "
                f"agree with each other. Mid-motion figures below are "
                f"changes on top of this offset, and --to-mm will square it."
            )
        worst_still = spread
        print()

        _, a_start, b_start = _sample(z_a, z_b)

        if args.cycles is not None:
            low, high = args.low_mm, args.high_mm
            print(
                f"\n  {args.cycles} laps of {low:g} -> {high:g} -> {low:g} mm."
                f"\n  THE ENCODERS ARE NOT THE MEASUREMENT HERE. move_to "
                f"drives each motor until ITS OWN encoder reads the target, "
                f"so both columns below will land on the target every lap "
                f"whatever the carriages do — that is the blind spot, not a "
                f"result. What accumulates is the gap between shaft and "
                f"carriage. Watch the frame; measure it at the end."
            )
            print("\n   lap        Z_A        Z_B       A-B")
            for lap in range(1, args.cycles + 1):
                for leg, target in (("hi", high), ("lo", low)):
                    MKSMotor.move_sync(
                        pair,
                        [
                            (
                                target,
                                args.speed_pct,
                                DEFAULT_MOVE_ACCEL_PCT,
                            )
                        ],
                    )
                    _, a, b = _sample(z_a, z_b)
                    print(
                        f"   {lap:2d} {leg}  {a:9.3f}  {b:9.3f}  {a - b:+8.4f}"
                    )
            _, a_end, b_end = _sample(z_a, z_b)
            print(
                f"\n  back at {low:g} mm after {args.cycles} laps: "
                f"Z_A {a_end:.3f}, Z_B {b_end:.3f}, A-B "
                f"{a_end - b_end:+.4f} mm"
            )
            print(
                f"\n  NOW MEASURE THE TWO CARRIAGES. They started this run "
                f"level. Any step between them now was built up "
                f"{args.cycles} laps at a time, so divide what you measure "
                f"by {args.cycles} to get the loss per lap. Level carriages "
                f"mean the drive trains match and the fault is not here."
            )
            return code

        if args.to_mm is not None:
            # UNRACK. --travel-mm moves both axes by the same DELTA, so it
            # preserves whatever offset the pair already has; sending both
            # to the same ABSOLUTE target is what removes it. Their zeros
            # were set at the same homing, so equal encoder readings are
            # equal carriage positions.
            print(
                f"\n  sending both axes to {args.to_mm:g} mm to square the "
                f"pair (was {a_start - b_start:+.4f} mm apart)…"
            )
            MKSMotor.move_sync(
                pair,
                [(args.to_mm, args.speed_pct, DEFAULT_MOVE_ACCEL_PCT)],
            )
            _, a_end, b_end = _sample(z_a, z_b)
            print("\nafter:")
            print(f"  Z_A {a_end:9.3f} mm")
            print(f"  Z_B {b_end:9.3f} mm")
            print(f"  A-B {a_end - b_end:+.4f} mm")
            if abs(a_end - b_end) > BASELINE_SPREAD_MM:
                print(
                    "\n  STILL APART. One side did not reach the target — "
                    "almost certainly a limit switch stopped it, which is "
                    "the fault itself. Pick a target clear of both limits "
                    "and try again; do not leave the pair racked."
                )
            else:
                print("\n  Square again.")
            return code

        if args.travel_mm is not None:
            target_a = a_start + args.travel_mm
            target_b = b_start + args.travel_mm
            for label, target in (("Z_A", target_a), ("Z_B", target_b)):
                if not AXIS_MIN_MM <= target <= AXIS_MAX_MM:
                    raise SyncError(
                        f"{label} would land at {target:.3f} mm, outside "
                        f"[{AXIS_MIN_MM:g}, {AXIS_MAX_MM:g}]. move_to would "
                        f"clamp it and travel less than asked, so the "
                        f"encoder figure you are about to compare against a "
                        f"rule would be wrong. Reposition first."
                    )
            print(
                f"\n  moving the pair {args.travel_mm:+g} mm together "
                f"(move_sync — the pair cannot rack)…"
            )
            MKSMotor.move_sync(
                pair,
                [(target_a, args.speed_pct, DEFAULT_MOVE_ACCEL_PCT)],
            )
            _, a_end, b_end = _sample(z_a, z_b)
            da = a_end - a_start
            db = b_end - b_start
            print("\nencoder travel:")
            print(f"  Z_A  {a_start:9.3f} -> {a_end:9.3f}   {da:+9.3f} mm")
            print(f"  Z_B  {b_start:9.3f} -> {b_end:9.3f}   {db:+9.3f} mm")
            print(
                f"  commanded {args.travel_mm:+g} mm; "
                f"A-B at rest {a_end - b_end:+.4f} mm"
            )
            print(
                "\n  NOW MEASURE EACH CARRIAGE AGAINST A RULE. These "
                "figures are what the MOTOR SHAFTS turned; they say nothing "
                "about where the carriages went. A side whose carriage "
                "moved less than its number above is losing that much in "
                "its coupling or screw, and that loss is what stops it "
                "reaching a home switch."
            )
            return code

        # WHICH WAY IS "+"? MEASURED. `coord_invert` flips F5 absolute
        # moves but not the F6 jog bit, so the direction is a per-axis
        # wiring fact (same reasoning as test_cell2_x_jog_shinyeong.py).
        cw = not args.toward_home
        MKSMotor.jog_start(
            pair,
            positive=cw,
            invert=False,
            speed_rpm=args.speed_rpm,
            accel=JOG_ACCEL,
        )
        time.sleep(PROBE_S)
        MKSMotor.jog_stop(pair, accel=JOG_ACCEL)
        time.sleep(0.2)
        _, a_probe, _b_probe = _sample(z_a, z_b)
        moved = a_probe - a_start
        want_increase = not args.toward_home
        print(
            f"  direction probe: Z_A {a_start:.3f} -> {a_probe:.3f} "
            f"({moved:+.3f} mm)"
        )
        if abs(moved) < PROBE_MIN_MM:
            print("  probe barely moved — jogging the other way instead.")
            cw = not cw
        elif (moved > 0) != want_increase:
            print("  WRONG WAY — flipping the jog direction.")
            cw = not cw
        else:
            print("  direction confirmed.")

        _, a_start, b_start = _sample(z_a, z_b)
        print("\nrolling (A interpolated to B's timestamp):")
        print("      t      Z_A        Z_B       A-B      travel")

        MKSMotor.jog_start(
            pair,
            positive=cw,
            invert=False,
            speed_rpm=args.speed_rpm,
            accel=JOG_ACCEL,
        )
        t_begin = time.monotonic()
        worst = 0.0
        worst_at = 0.0
        lagger = None
        stopped_by = "time"
        try:
            while time.monotonic() - t_begin < args.seconds:
                t, a, b = _sample(z_a, z_b)
                elapsed = t - t_begin
                diff = a - b
                travel = max(abs(a - a_start), abs(b - b_start))
                print(
                    f"   {elapsed:5.2f}s {a:9.3f} {b:9.3f} {diff:+8.4f} "
                    f"{travel:8.3f}"
                )
                if abs(diff) > abs(worst):
                    worst, worst_at = diff, elapsed
                    lagger = (
                        "Z_A"
                        if (diff > 0) == (cw != args.toward_home)
                        else "Z_B"
                    )
                if travel >= args.max_mm:
                    stopped_by = "travel cap"
                    break
        finally:
            MKSMotor.jog_stop(pair, accel=JOG_ACCEL)

        time.sleep(0.4)
        _, a_end, b_end = _sample(z_a, z_b)
        settled = a_end - b_end

        print(f"\n  stopped by {stopped_by}.")
        print(
            f"  worst mid-motion |A-B|: {abs(worst):.4f} mm "
            f"(signed {worst:+.4f}, at {worst_at:.2f} s)"
        )
        print(f"  after settling:         {settled:+.4f} mm")
        print(f"  baseline noise floor:   {worst_still:.4f} mm\n")
        if abs(worst) <= max(worst_still * 2, BASELINE_SPREAD_MM):
            print(
                "  NO LAG FOUND ABOVE THE NOISE FLOOR. The two axes track "
                "each other within the measurement's own error, so whatever "
                "is visible by eye is not a following error between them — "
                "look at what happens at the ends of travel instead."
            )
        else:
            print(
                f"  {lagger} IS BEHIND during motion by up to "
                f"{abs(worst):.3f} mm, settling to {abs(settled):.4f} mm at "
                f"rest. A lag that disappears when the motion stops is a "
                f"following error, not lost steps: the trailing axis is "
                f"being asked for more torque than it delivers promptly — "
                f"drag on that side, or a current/acceleration setting that "
                f"differs from its partner's."
            )

    except SyncError as exc:
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
