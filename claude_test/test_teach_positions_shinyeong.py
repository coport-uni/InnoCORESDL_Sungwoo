#!/usr/bin/env python3
"""Teach the rail and gantry positions the synthesis chain needs.

The synthesis run moves the balance (on cell4's rail) under each gantry
in turn, lowers that gantry's head over the vial, and dispenses. None of
those positions exist yet: every scenario in this repository uses round
numbers chosen to *measure travel*, not taught points, and the rail has
only ever been driven between 5 mm and 50 mm. This tool is how the real
numbers get found.

It drives **over HTTP `/v1`**, never the drivers directly, so it can run
while the cell servers are up — the one-owner-per-port rule is not
violated and the moves go through the same `_confirm` encoder readback
the scenarios rely on (LearnedPatterns #24).

Two guards exist because the hardware has no software stop:

* **The rail cannot be sent into unmeasured travel in one jump.** Each
  `rail` command may advance at most ``--rail-step`` mm past the furthest
  point reached so far in this session, and may move at most
  ``--rail-max-move`` mm in one call. The rail cannot be stopped from
  software at all (GAP-1/GAP-9), so the only real protection is that no
  single command can run far.
* **Z is capped** by ``--z-max``. Descending onto the balance is the one
  move that can break something expensive: the head must stop *above*
  the vial, not touch it. Teach it in shrinking increments and stop
  while there is still a visible gap.

Rail note: 0 mm sits on the mechanical stop, so home is 5 mm, matching
`home_mm` in the existing scenarios (README "Bench notes").

EMI: with cell4's amp powered the RS485 link drops periodically
(LearnedPatterns #20). A rail move that straddles a drop aborts by
design — that is a correct abort, not damage. Re-issue it; every target
here is absolute, so a repeat is harmless.

Commands::

    rail 60          move the rail to 60 mm (absolute)
    x 120            move the gantry X to 120 mm (Z retracts first)
    z 40             lower the gantry Z to 40 mm
    xz 120 40        X then Z, in the cell's own up-X-down order
    pos              read the rail and the gantry, move nothing
    weight           read the balance (no motion)
    home rail        home the rail, then park it at 5 mm
    home gantry      home the gantry
    record <label>   append the live positions to the notes file
    q                quit

Usage::

    .venv/bin/python claude_test/test_teach_positions_shinyeong.py \\
        --gantry-url http://127.0.0.1:17054 --gantry-name cell1
    .venv/bin/python claude_test/test_teach_positions_shinyeong.py \\
        --gantry-url http://192.168.0.120:17056 --gantry-name cell2
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.error
import urllib.request

#: Where the taught numbers accumulate. Appended to, never rewritten, so
#: an aborted session still leaves its measurements behind.
DEFAULT_NOTES = "claude_test/taught_positions_shinyeong.md"

DEFAULT_RAIL_URL = "http://127.0.0.1:17060"
DEFAULT_GANTRY_URL = "http://127.0.0.1:17054"

#: 0 mm is on the mechanical stop; the scenarios park at 5 mm.
RAIL_HOME_MM = 5.0

#: How far past the furthest point reached this session one command may
#: go. The rail's travel beyond 50 mm has never been driven.
DEFAULT_RAIL_STEP_MM = 20.0

#: Cap on a single rail command's travel, whatever the target.
DEFAULT_RAIL_MAX_MOVE_MM = 50.0

#: The rail has been driven to 50 mm before, so that much is not new
#: ground and seeds the "furthest reached" watermark.
RAIL_KNOWN_MM = 50.0

#: Ceiling for the gantry Z. Descending onto the balance is where the
#: damage would be; raise it deliberately, one step at a time.
DEFAULT_Z_MAX_MM = 120.0

#: Gantry X ceiling. cell1/2/3 have run to 100 mm.
DEFAULT_X_MAX_MM = 200.0

DEFAULT_SPEED_PCT = 10
DEFAULT_ACCEL_PCT = 0

#: Rail moves are slow (~14 s for 50 mm) and retry inside the driver.
RAIL_TIMEOUT_S = 120.0
GANTRY_TIMEOUT_S = 120.0
READ_TIMEOUT_S = 30.0
BALANCE_TIMEOUT_S = 90.0

EXIT_OK = 0
EXIT_ERROR = 1


class BenchError(Exception):
    """A request the bench refused, or a guard this tool refused."""


def _call(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    body: dict | None = None,
    timeout_s: float = READ_TIMEOUT_S,
) -> dict:
    """One `/v1` call. Returns the decoded body.

    Raises:
        BenchError: On any HTTP or transport failure, with the server's
            own message when it sent one — a 409/400/503 from the cell
            says more than the status code alone.
    """
    url = f"{base_url.rstrip('/')}/v1/{path.lstrip('/')}"
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:400]
        raise BenchError(f"{method} {path} -> HTTP {exc.code}: {detail}")
    except Exception as exc:  # noqa: BLE001 — transport, timeout, decode
        raise BenchError(f"{method} {path} -> {type(exc).__name__}: {exc}")


class Bench:
    """The rail and one gantry, addressed over HTTP."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.rail_url = args.rail_url
        self.gantry_url = args.gantry_url
        self.gantry_name = args.gantry_name
        self.notes = args.notes
        self.rail_step = args.rail_step
        self.rail_max_move = args.rail_max_move
        self.z_max = args.z_max
        self.x_max = args.x_max
        self.speed = args.speed_pct
        self.accel = args.accel_pct
        #: Furthest rail position proven so far. Seeded here with the
        #: travel the bench had already driven before this tool existed,
        #: and raised to the live position by ``seed_watermark`` — see
        #: there for why that matters.
        self.rail_watermark = RAIL_KNOWN_MM

    def seed_watermark(self) -> None:
        """Raise the watermark to wherever the rail already is.

        The guard exists to stop the rail running into travel nobody has
        driven. A position the carriage is *currently sitting at* is by
        definition travel that has been driven, so starting every session
        at ``RAIL_KNOWN_MM`` is wrong: restarting this tool (to raise a
        cap, say) would otherwise refuse to advance a rail parked far out,
        which is exactly what happened teaching cell1 on 2026-08-13 — the
        rail was at 430 mm and the guard still believed 50 mm.

        A failed read leaves the conservative seed in place; the operator
        can re-run ``pos`` and try again.
        """
        try:
            here = self.rail_mm()
        except BenchError as exc:
            print(
                f"  rail position unread ({exc}); watermark stays at "
                f"{self.rail_watermark:g} mm"
            )
            return
        if here is not None and here > self.rail_watermark:
            self.rail_watermark = here
            print(f"  watermark seeded from the live rail: {here:.3f} mm")

    # ── reads ──────────────────────────────────────────────────────────
    def rail_mm(self) -> float | None:
        """The rail's position, or None if the RS485 read failed."""
        return _call(self.rail_url, "status")["stage_x_mm"]

    def gantry_xz(self) -> tuple[float | None, float | None]:
        """The gantry's (x, z), either of which may be None."""
        st = _call(self.gantry_url, "status")
        return st["stage_x_mm"], st["stage_z_mm"]

    def show(self) -> None:
        """Print both cells' live positions."""

        def _f(mm: float | None) -> str:
            return "unread" if mm is None else f"{mm:8.3f} mm"

        try:
            rail = _f(self.rail_mm())
        except BenchError as exc:
            rail = f"ERROR ({exc})"
        try:
            x, z = self.gantry_xz()
            gantry = f"X {_f(x)} | Z {_f(z)}"
        except BenchError as exc:
            gantry = f"ERROR ({exc})"
        print(f"  rail {rail}")
        print(f"  {self.gantry_name} {gantry}")

    # ── motion ─────────────────────────────────────────────────────────
    def move_rail(self, target: float) -> None:
        """Move the rail to an absolute target, both guards applied.

        Raises:
            BenchError: If the target breaks a guard, or the move fails.
        """
        if target < 0:
            raise BenchError("rail targets are absolute and start at 0")
        here = self.rail_mm()
        if here is None:
            raise BenchError(
                "the rail did not answer a position read, so this tool "
                "cannot tell how far the move would be — retry the read "
                "(EMI drops one read in ten; the driver reconnects)"
            )
        if abs(target - here) > self.rail_max_move:
            raise BenchError(
                f"refused: {here:.1f} -> {target:.1f} mm is "
                f"{abs(target - here):.1f} mm in one command, over the "
                f"{self.rail_max_move:g} mm cap. Step there instead."
            )
        if target > self.rail_watermark + self.rail_step:
            raise BenchError(
                f"refused: {target:.1f} mm is more than "
                f"{self.rail_step:g} mm past the furthest point reached "
                f"({self.rail_watermark:.1f} mm). Advance in steps so a "
                f"rail that cannot be stopped never runs into unmeasured "
                f"travel."
            )
        out = _call(
            self.rail_url,
            "linear/move",
            method="POST",
            body={"y_mm": target},
            timeout_s=RAIL_TIMEOUT_S,
        )
        reached = out.get("y_mm")
        print(f"  rail -> commanded {target:g} mm, reads {reached} mm")
        if reached is not None:
            self.rail_watermark = max(self.rail_watermark, reached)

    def move_gantry(self, x: float, z: float) -> None:
        """Move the gantry to (x, z); the cell orders it up-X-down."""
        if not 0 <= x <= self.x_max:
            raise BenchError(f"refused: X {x:g} outside 0..{self.x_max:g}")
        if not 0 <= z <= self.z_max:
            raise BenchError(
                f"refused: Z {z:g} outside 0..{self.z_max:g}. Raise "
                f"--z-max only after looking at the gap to the vial."
            )
        out = _call(
            self.gantry_url,
            "gantry/move",
            method="POST",
            body={
                "x_mm": x,
                "z_mm": z,
                "speed_pct": self.speed,
                "accel_pct": self.accel,
            },
            timeout_s=GANTRY_TIMEOUT_S,
        )
        print(
            f"  {self.gantry_name} -> commanded X {x:g} Z {z:g}, "
            f"reads X {out.get('x_mm')} Z {out.get('z_mm')}"
        )

    # ── notes ──────────────────────────────────────────────────────────
    def record(self, label: str) -> None:
        """Append the live positions under ``label`` to the notes file."""
        rail = self.rail_mm()
        x, z = self.gantry_xz()
        stamp = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        line = (
            f"| {label} | {self.gantry_name} | "
            f"{'?' if rail is None else f'{rail:.3f}'} | "
            f"{'?' if x is None else f'{x:.3f}'} | "
            f"{'?' if z is None else f'{z:.3f}'} | {stamp} |\n"
        )
        try:
            with open(self.notes, encoding="utf-8") as handle:
                fresh = "| label |" not in handle.read()
        except FileNotFoundError:
            fresh = True
        with open(self.notes, "a", encoding="utf-8") as handle:
            if fresh:
                handle.write(
                    "# Taught positions — synthesis chain\n\n"
                    "Measured over `/v1`, so every number is an encoder "
                    "readback the cell confirmed, not a commanded value.\n"
                    "Rail 0 mm is on the mechanical stop; home is 5 mm.\n\n"
                    "| label | gantry | rail_mm | x_mm | z_mm | UTC |\n"
                    "|---|---|---|---|---|---|\n"
                )
            handle.write(line)
        print(f"  recorded '{label}' -> {self.notes}")


def _handle(bench: Bench, parts: list[str]) -> None:
    """Run one typed command."""
    verb = parts[0]
    if verb == "pos":
        bench.show()
    elif verb == "weight":
        out = _call(
            bench.rail_url, "balance/weight", timeout_s=BALANCE_TIMEOUT_S
        )
        print(f"  weight {out.get('weight_g')} g  stable={out.get('stable')}")
    elif verb == "rail" and len(parts) == 2:
        bench.move_rail(float(parts[1]))
    elif verb == "x" and len(parts) == 2:
        _, z = bench.gantry_xz()
        bench.move_gantry(float(parts[1]), z if z is not None else 0.0)
    elif verb == "z" and len(parts) == 2:
        x, _ = bench.gantry_xz()
        bench.move_gantry(x if x is not None else 0.0, float(parts[1]))
    elif verb == "xz" and len(parts) == 3:
        bench.move_gantry(float(parts[1]), float(parts[2]))
    elif verb == "home" and len(parts) == 2 and parts[1] == "rail":
        _call(
            bench.rail_url,
            "linear/home",
            method="POST",
            timeout_s=RAIL_TIMEOUT_S,
        )
        bench.move_rail(RAIL_HOME_MM)
    elif verb == "home" and len(parts) == 2 and parts[1] == "gantry":
        out = _call(
            bench.gantry_url,
            "gantry/home",
            method="POST",
            timeout_s=GANTRY_TIMEOUT_S,
        )
        print(f"  homed: X {out.get('x_mm')} Z {out.get('z_mm')}")
    elif verb == "record" and len(parts) == 2:
        bench.record(parts[1])
    else:
        print(
            "  commands: rail <mm> | x <mm> | z <mm> | xz <x> <z> | pos | "
            "weight | home rail | home gantry | record <label> | q"
        )


def main(argv: list[str] | None = None) -> int:
    """Run the teaching prompt. Returns a process exit code."""
    parser = argparse.ArgumentParser(
        description="Teach rail + gantry positions over /v1 (interactive)."
    )
    parser.add_argument("--rail-url", default=DEFAULT_RAIL_URL)
    parser.add_argument("--gantry-url", default=DEFAULT_GANTRY_URL)
    parser.add_argument(
        "--gantry-name", default="cell1", help="label used in the notes"
    )
    parser.add_argument("--notes", default=DEFAULT_NOTES)
    parser.add_argument(
        "--rail-step",
        type=float,
        default=DEFAULT_RAIL_STEP_MM,
        help=f"max advance past the furthest point reached "
        f"(default {DEFAULT_RAIL_STEP_MM:g})",
    )
    parser.add_argument(
        "--rail-max-move",
        type=float,
        default=DEFAULT_RAIL_MAX_MOVE_MM,
        help=f"max travel in one rail command "
        f"(default {DEFAULT_RAIL_MAX_MOVE_MM:g})",
    )
    parser.add_argument("--z-max", type=float, default=DEFAULT_Z_MAX_MM)
    parser.add_argument("--x-max", type=float, default=DEFAULT_X_MAX_MM)
    parser.add_argument("--speed-pct", type=int, default=DEFAULT_SPEED_PCT)
    parser.add_argument("--accel-pct", type=int, default=DEFAULT_ACCEL_PCT)
    args = parser.parse_args(argv)

    bench = Bench(args)
    print(
        f"Teaching positions — rail {args.rail_url}, "
        f"{args.gantry_name} {args.gantry_url}\n"
        f"Every move is real motion and NOTHING here can be stopped from "
        f"software: the rail has no software stop at all (GAP-1) and a "
        f"gantry stop queues behind the move it means to interrupt "
        f"(GAP-9). Keep the physical e-stop in hand.\n"
        f"Guards: rail advances at most {args.rail_step:g} mm past the "
        f"furthest point reached ({RAIL_KNOWN_MM:g} mm to start) and "
        f"{args.rail_max_move:g} mm per command; Z is capped at "
        f"{args.z_max:g} mm.\n"
        f"Lower onto the vial in SHRINKING steps and stop with a visible "
        f"gap — the head must hover, never touch the pan.\n"
    )
    bench.show()
    bench.seed_watermark()

    while True:
        try:
            line = input("teach> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.lower() in ("q", "quit", "exit"):
            break
        try:
            _handle(bench, line.split())
        except BenchError as exc:
            print(f"  {exc}")
        except ValueError:
            print("  refused: could not read that as a number")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
