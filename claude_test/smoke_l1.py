"""Operator-supervised L1 smoke test over HTTP /v1 (spec section 4.2).

Purpose: when the hardware is finally connected, this script walks the M0
smoke tests one endpoint at a time and prints a Markdown table to paste
into ``docs/L1_AUDIT.md`` -- request, response, measured duration, and the
physical behaviour the operator observed.

Rules this script enforces, from CLAUDE.md and the spec:

- Every hardware-moving check STOPS and waits for the operator to type
  ``go``. There is no unattended mode and no "yes to all" flag.
- Calls go through a running cell server's ``/v1`` API, never through a
  driver: one owner per serial port (CLAUDE.md Folder-specific rules #2).
- The pump check drives the valve 90 degrees apart (source 2 -> tip 1) and
  asks for an EYE confirmation, because the `?6` reply cannot prove the
  fluid path moved (LearnedPatterns.md #1).
- The gantry check asks whether BOTH Z axes moved together; a single-axis
  move means stop immediately.

- The hotplate and lamp checks are gated exactly like motion: a heater or
  an IR lamp coming on unattended is the same class of hazard.

Usage:
    # cell4 (balance + linear rail)
    python claude_test/smoke_l1.py --base-url http://127.0.0.1:17060 \
        --suite discovery --suite balance --suite linear \
        --out claude_test/smoke_cell4.md

    # cell5 / Cell 5 (pump + single Z + hotplate + IR lamp)
    python claude_test/smoke_l1.py --base-url http://127.0.0.1:17062 \
        --suite discovery --suite lamp --suite hotplate --suite zstage \
        --suite pump --out claude_test/smoke_cell5.md
"""

from __future__ import annotations

import argparse
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

DEFAULT_STEP_MM = 10.0
DEFAULT_VOLUME_UL = 5.0
DEFAULT_TIMEOUT_S = 120.0
SOURCE_PORT = 2  # reservoir; 90 degrees from the tip on the M05 valve
TIP_PORT = 1

#: How far the A4/A7 probes travel, and how long the stop probe waits
#: before firing. Long enough that the axis is provably mid-move.
PROBE_TRAVEL_MM = 40.0
STOP_AFTER_S = 1.5

#: Move action per motion family, and how to build its request body. Used
#: by the `stop` (A4) and `concurrency` (A7) probes, which need a move
#: that is long enough to interrupt. The family cannot be inferred from
#: the OpenAPI -- one router serves every shape (L1_AUDIT GAP-3) -- so the
#: operator names it with --motion.
MOTION_FAMILIES: dict[str, tuple[str, Any]] = {
    "linear": ("linear/move", lambda mm: {"y_mm": mm}),
    "zstage": ("zstage/move", lambda mm: {"z_mm": mm}),
    "gantry": ("gantry/move", lambda mm: {"x_mm": 0.0, "z_mm": mm}),
}


@dataclass(frozen=True)
class Check:
    """One smoke-test call."""

    key: str
    title: str
    action: str
    method: str = "POST"
    body: dict[str, Any] = field(default_factory=dict)
    expect: str = ""
    briefing: str | None = None  # set => hardware moves => operator gate


def _suites(step_mm: float, volume_uL: float) -> dict[str, tuple[Check, ...]]:
    """Build the suites with the bench-chosen step size and volume."""
    return {
        "discovery": (
            Check(
                "health",
                "Liveness probe",
                "health",
                "GET",
                expect="cell_up true; A2 evidence",
            ),
            Check(
                "diagnose",
                "Commissioning probe of every device",
                "diagnose",
                "GET",
                expect="per-device ok flags, ok_to_initialize",
            ),
            Check(
                "status",
                "Live readouts",
                "status",
                "GET",
                expect="positions/weight readable, busy false",
            ),
        ),
        "balance": (
            Check(
                "tare",
                "Tare the balance",
                "balance/tare",
                expect="weight_g ~ 0",
                briefing=(
                    "The balance zeroes on its current pan load. Pan should "
                    "be empty and settled."
                ),
            ),
            Check(
                "weight_empty",
                "Settled read after tare",
                "balance/weight",
                "GET",
                expect="weight_g near 0, stable true",
            ),
            Check(
                "weight_loaded",
                "Read again with a known mass on the pan",
                "balance/weight",
                "GET",
                expect="value changes by the known mass; note settling time",
                briefing=(
                    "Place a known weight (or a small object) on the pan "
                    "now, then let it settle."
                ),
            ),
        ),
        "linear": (
            Check(
                "linear_home",
                "Home the Y rail",
                "linear/home",
                expect="y_mm 0.0; record the duration for A3",
                briefing=(
                    "The MINAS A6 rail homes to the encoder origin. Clear "
                    "the travel path; keep the e-stop in reach."
                ),
            ),
            Check(
                "linear_out",
                f"Move Y to {step_mm} mm",
                "linear/move",
                body={"y_mm": step_mm},
                expect="y_mm within +-0.1 mm of target (closed loop)",
                briefing=(
                    f"The rail travels about {step_mm} mm away from home. "
                    "Confirm the range is clear."
                ),
            ),
            Check(
                "linear_back",
                "Move Y back to 0 mm",
                "linear/move",
                body={"y_mm": 0.0},
                expect="returns to 0 +-0.1 mm; note the settling time",
                briefing="The rail travels back to the origin.",
            ),
        ),
        "pump": (
            Check(
                "pump_init",
                "Home plunger and valve",
                "pump/initialize",
                body={"force": 2, "ccw": False},
                expect="valve/plunger report a defined home state",
                briefing=(
                    "The SY-01B homes its plunger and valve. Check the "
                    "tubing is connected and the reservoir has liquid."
                ),
            ),
            Check(
                "valve_source",
                f"Valve to the reservoir (port {SOURCE_PORT})",
                "pump/valve",
                body={"port": SOURCE_PORT},
                expect=(
                    "valve reports the port -- this alone does NOT prove "
                    "the fluid path moved (LearnedPatterns #1)"
                ),
                briefing="The valve rotor turns to the reservoir port.",
            ),
            Check(
                "cycle",
                f"One {volume_uL} uL aspirate/dispense cycle",
                "pump/cycle",
                body={
                    "cycles": 1,
                    "volume_uL": volume_uL,
                    "source_port": SOURCE_PORT,
                    "dispense_port": TIP_PORT,
                },
                expect="liquid visibly moves reservoir -> tip",
                briefing=(
                    f"Pump draws {volume_uL} uL from port {SOURCE_PORT} and "
                    f"dispenses to port {TIP_PORT}. WATCH THE TUBING -- the "
                    "eye is the only valid check here."
                ),
            ),
        ),
        "zstage": (
            Check(
                "zstage_home",
                "Home Cell 5's single Z axis",
                "zstage/home",
                expect="z_mm 0.0; record the duration for A3",
                briefing=(
                    "One MKS motor drives this axis -- there is no paired-Z "
                    "partner. Clear the travel, keep the e-stop in reach."
                ),
            ),
            Check(
                "zstage_out",
                f"Z to {step_mm} mm",
                "zstage/move",
                body={"z_mm": step_mm},
                expect="reaches the target; no X action exists on this cell",
                briefing=f"The Z axis travels about {step_mm} mm.",
            ),
            Check(
                "zstage_back",
                "Z back to 0 mm",
                "zstage/move",
                body={"z_mm": 0.0},
                expect="returns to the origin",
                briefing="The Z axis travels back to the origin.",
            ),
        ),
        "hotplate": (
            Check(
                "hotplate_read",
                "Read plate/probe temperature and setpoint",
                "hotplate/state",
                "GET",
                expect="plausible room temperature; max_c is the cell ceiling",
            ),
            Check(
                "hotplate_setpoint",
                "Set the target to 30 °C",
                "hotplate/temperature",
                body={"celsius": 30.0},
                expect="target_c 30.0; above max_c would be a 400",
                briefing=(
                    "Sets the setpoint only -- the heater does not start "
                    "yet. Check nothing flammable is on the plate."
                ),
            ),
            Check(
                "heater_on",
                "Start heating",
                "hotplate/heater",
                body={"enabled": True},
                expect="temperature starts trending up",
                briefing=(
                    "THE PLATE WILL HEAT. Do not leave the bench. Watch the "
                    "plate temperature climb toward 30 °C."
                ),
            ),
            Check(
                "heater_off",
                "Stop heating",
                "hotplate/heater",
                body={"enabled": False},
                expect="heating false; the plate begins to cool",
                briefing="Turns the heater off.",
            ),
        ),
        "lamp": (
            Check(
                "lamp_read",
                "Read the plug state over the LAN",
                "lamp/state",
                "GET",
                expect=(
                    "is_on matches the lamp; a 409 here means the plug "
                    "credentials are not configured yet"
                ),
            ),
            Check(
                "lamp_on",
                "Switch the IR lamp on",
                "lamp/switch",
                body={"enabled": True},
                expect="the lamp visibly lights; is_on true",
                briefing=(
                    "THE IR LAMP WILL LIGHT. Clear anything flammable "
                    "around it first, and do not leave it lit unattended."
                ),
            ),
            Check(
                "lamp_off",
                "Switch the IR lamp off",
                "lamp/switch",
                body={"enabled": False},
                expect="the lamp visibly goes out; is_on false",
                briefing="Turns the lamp off.",
            ),
        ),
        "gantry": (
            Check(
                "gantry_home",
                "Home the XZ gantry",
                "gantry/home",
                expect="x_mm/z_mm 0.0; note any first-command drop",
                briefing=(
                    "HIGHEST-RISK SUBSYSTEM. Frame clear, e-stop in hand. "
                    "Both Z axes must home together."
                ),
            ),
            Check(
                "gantry_x_out",
                f"X to {step_mm} mm (Z stays home)",
                "gantry/move",
                body={"x_mm": step_mm, "z_mm": 0.0},
                expect="X moves, Z does not",
                briefing=f"X travels about {step_mm} mm.",
            ),
            Check(
                "gantry_x_back",
                "X back to 0 mm",
                "gantry/move",
                body={"x_mm": 0.0, "z_mm": 0.0},
                expect="returns to origin",
                briefing="X returns to the origin.",
            ),
            Check(
                "gantry_z_out",
                f"Z to {step_mm} mm -- ONE command, TWO axes",
                "gantry/move",
                body={"x_mm": 0.0, "z_mm": step_mm},
                expect=(
                    "BOTH Z axes move together, same distance, no visible "
                    "tilt (the paired-Z interlock)"
                ),
                briefing=(
                    "One Z command drives both Z motors. WATCH BOTH SIDES. "
                    "If only one moves, or the frame tilts, hit the e-stop."
                ),
            ),
            Check(
                "gantry_z_back",
                "Z back to 0 mm",
                "gantry/move",
                body={"x_mm": 0.0, "z_mm": 0.0},
                expect="both Z axes return together",
                briefing="Both Z axes travel back to the origin.",
            ),
        ),
    }


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return "q"


def _gate(briefing: str, expect: str) -> str:
    """Brief the operator and get consent. Returns go | skip | quit."""
    print(f"\n  !! {briefing}")
    print(f"     expect: {expect}")
    answer = _ask("     type 'go' to run, 's' to skip, 'q' to quit > ")
    if answer.lower() == "go":
        return "go"
    return "quit" if answer.lower().startswith("q") else "skip"


def _call(
    http: httpx.Client, check: Check, timeout_s: float
) -> tuple[int | None, Any, float]:
    started = time.monotonic()
    try:
        response = http.request(
            check.method,
            f"/v1/{check.action}",
            json=check.body if check.method != "GET" else None,
            timeout=timeout_s,
        )
    except httpx.HTTPError as exc:
        return None, {"transport_error": str(exc)}, time.monotonic() - started
    elapsed = time.monotonic() - started
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text[:200]}
    return response.status_code, payload, elapsed


def run_suite(
    http: httpx.Client, checks: tuple[Check, ...], timeout_s: float
) -> tuple[list[dict[str, Any]], bool]:
    """Run one suite. Returns the rows and whether the operator quit."""
    rows: list[dict[str, Any]] = []
    for check in checks:
        print(f"\n[{check.key}] {check.title}")
        if check.briefing is not None:
            decision = _gate(check.briefing, check.expect)
            if decision == "quit":
                return rows, True
            if decision == "skip":
                rows.append(
                    {
                        "key": check.key,
                        "status": "pending",
                        "note": "skipped by operator",
                    }
                )
                continue
        status, payload, elapsed = _call(http, check, timeout_s)
        ok = status is not None and 200 <= status < 300
        print(f"     -> HTTP {status} in {elapsed:.2f}s: {payload}")
        note = ""
        if check.briefing is not None:
            note = _ask("     what did you observe? > ")
        rows.append(
            {
                "key": check.key,
                "title": check.title,
                "request": f"{check.method} /v1/{check.action} {check.body}",
                "status": "pass" if ok else "gap",
                "http": status,
                "duration_s": round(elapsed, 2),
                "response": payload,
                "expect": check.expect,
                "note": note,
                "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )
        if not ok:
            print("     !! non-2xx -- record this as a gap in L1_AUDIT.md")
    return rows, False


def _row(
    key: str,
    title: str,
    request: str,
    status: str,
    *,
    duration_s: float | None = None,
    http: int | None = None,
    note: str = "",
    detail: Any = None,
) -> dict[str, Any]:
    """One result row, in the shape :func:`to_markdown` expects."""
    return {
        "key": key,
        "title": title,
        "request": request,
        "status": status,
        "http": http,
        "duration_s": None if duration_s is None else round(duration_s, 2),
        "response": detail,
        "expect": "",
        "note": note,
        "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def run_stop_probe(
    http: httpx.Client, motion: str, timeout_s: float
) -> tuple[list[dict[str, Any]], bool]:
    """A4: does ``POST /v1/stop`` interrupt a move, or queue behind it?

    Starts a long move in a worker thread, fires ``/v1/stop`` while it is
    provably in flight, and times both. If the stop only returns when the
    move does, it did not preempt anything — see ``docs/L1_AUDIT.md``
    GAP-9, which is exactly what this probe measured off the bench.
    """
    action, body_of = MOTION_FAMILIES[motion]
    briefing = (
        f"A LONG MOVE STARTS ({action}, ~{PROBE_TRAVEL_MM} mm), then "
        f"POST /v1/stop is fired {STOP_AFTER_S}s into it. Clear the full "
        "travel and keep the PHYSICAL e-stop in your hand -- the software "
        "stop is what is under test, so assume it will not work."
    )
    expect = (
        "the axis halts within a fraction of a second of the stop; if it "
        "instead runs to the target, the stop queued behind the move"
    )
    decision = _gate(briefing, expect)
    if decision != "go":
        return (
            [_row("stop_probe", "A4 stop-during-move", "-", "pending")],
            decision == "quit",
        )

    move = Check(
        "probe_move", "long move", action, body=body_of(PROBE_TRAVEL_MM)
    )
    started = time.monotonic()
    result: dict[str, Any] = {}

    def _run_move() -> None:
        result["move"] = _call(http, move, timeout_s)

    worker = threading.Thread(target=_run_move, daemon=True)
    worker.start()
    time.sleep(STOP_AFTER_S)
    print(f"     [{time.monotonic() - started:.2f}s] firing POST /v1/stop")
    stop_status, stop_payload, _ = _call(
        http, Check("stop", "stop", "stop"), timeout_s
    )
    stop_at = time.monotonic() - started
    worker.join(timeout=timeout_s)
    move_at = time.monotonic() - started
    print(
        f"     stop returned at {stop_at:.2f}s (HTTP {stop_status}); "
        f"move returned at {move_at:.2f}s"
    )
    # The tell: a stop that could not preempt returns when the move does.
    preempted = stop_at < move_at - 0.5
    note = _ask("     did the axis actually stop early? > ")
    verdict = "pass" if preempted else "gap"
    return (
        [
            _row(
                "stop_probe",
                "A4 stop-during-move",
                f"POST /v1/{action} then POST /v1/stop",
                verdict,
                duration_s=stop_at,
                http=stop_status,
                note=(
                    f"stop@{stop_at:.2f}s move@{move_at:.2f}s "
                    f"preempted={preempted}; operator: {note or '-'}"
                ),
                detail=stop_payload,
            )
        ],
        False,
    )


def run_concurrency_probe(
    http: httpx.Client, motion: str, timeout_s: float
) -> tuple[list[dict[str, Any]], bool]:
    """A7: two overlapping moves — does L1 serialize them or interleave?

    The expected (and code-predicted) answer is serialization: L1 holds
    one ``asyncio.Lock`` for the whole device interaction, so the second
    caller waits rather than getting a 409.
    """
    action, body_of = MOTION_FAMILIES[motion]
    briefing = (
        f"TWO {action} requests are sent at once (to {PROBE_TRAVEL_MM} mm "
        "and back to 0). If L1 serializes them the axis makes two clean "
        "moves; if it does not, the motion is undefined. Physical e-stop "
        "in hand."
    )
    expect = "the second call waits for the first; no interleaved motion"
    decision = _gate(briefing, expect)
    if decision != "go":
        return (
            [_row("concurrency", "A7 overlapping calls", "-", "pending")],
            decision == "quit",
        )

    started = time.monotonic()
    outcomes: dict[str, tuple[float, Any]] = {}

    def _fire(name: str, mm: float) -> None:
        status, payload, _ = _call(
            http,
            Check(name, "concurrent move", action, body=body_of(mm)),
            timeout_s,
        )
        outcomes[name] = (time.monotonic() - started, (status, payload))

    threads = [
        threading.Thread(target=_fire, args=("first", PROBE_TRAVEL_MM)),
        threading.Thread(target=_fire, args=("second", 0.0)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=timeout_s)

    finishes = sorted(at for at, _ in outcomes.values())
    serialized = len(finishes) == 2 and finishes[1] - finishes[0] > 0.5
    for name, (at, (status, _payload)) in sorted(outcomes.items()):
        print(f"     {name}: HTTP {status} at {at:.2f}s")
    note = _ask("     did the axis make two clean, separate moves? > ")
    return (
        [
            _row(
                "concurrency",
                "A7 overlapping calls",
                f"2x POST /v1/{action}",
                "pass" if serialized else "gap",
                duration_s=finishes[-1] if finishes else None,
                note=(
                    f"finished at {[round(f, 2) for f in finishes]}; "
                    f"serialized={serialized}; operator: {note or '-'}"
                ),
                detail={k: v[1][0] for k, v in outcomes.items()},
            )
        ],
        False,
    )


PROBES = {"stop": run_stop_probe, "concurrency": run_concurrency_probe}


def to_markdown(base_url: str, rows: list[dict[str, Any]]) -> str:
    """Render the rows as a table for docs/L1_AUDIT.md."""
    head = (
        f"### Smoke test — {base_url}\n\n"
        "| UTC | check | request | HTTP | s | verdict | observed |\n"
        "|---|---|---|---|---|---|---|\n"
    )
    lines = []
    for row in rows:
        lines.append(
            "| {utc} | {key} | `{request}` | {http} | {duration_s} | "
            "{status} | {note} |".format(
                utc=row.get("utc", "-"),
                key=row.get("key", "-"),
                request=row.get("request", "-"),
                http=row.get("http", "-"),
                duration_s=row.get("duration_s", "-"),
                status=row.get("status", "-"),
                note=row.get("note", "") or "-",
            )
        )
    return head + "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Operator-supervised L1 smoke test (spec section 4.2)."
    )
    parser.add_argument(
        "--base-url",
        required=True,
        help="Cell server root, e.g. http://127.0.0.1:17060",
    )
    parser.add_argument(
        "--suite",
        action="append",
        required=True,
        choices=sorted([*_suites(DEFAULT_STEP_MM, DEFAULT_VOLUME_UL), *PROBES]),
        help=(
            "Repeatable. Recommended order, lowest risk first: discovery, "
            "balance, lamp, hotplate, linear, zstage, pump, gantry. The "
            "'stop' (A4) and 'concurrency' (A7) probes need --motion."
        ),
    )
    parser.add_argument(
        "--motion",
        choices=sorted(MOTION_FAMILIES),
        help=(
            "Which move action this cell has, for the stop/concurrency "
            "probes: linear (cell4), zstage (cell5), gantry (cell1-3)."
        ),
    )
    parser.add_argument("--step-mm", type=float, default=DEFAULT_STEP_MM)
    parser.add_argument("--volume-uL", type=float, default=DEFAULT_VOLUME_UL)
    parser.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument(
        "--out", type=Path, help="Write the Markdown table here as well."
    )
    args = parser.parse_args(argv)

    suites = _suites(args.step_mm, args.volume_uL)
    print(
        "Operator supervision is required. Nothing moves until you type "
        "'go'.\nKeep the physical e-stop within reach (CLAUDE.md #3)."
    )
    if any(s in PROBES for s in args.suite) and args.motion is None:
        parser.error("the stop/concurrency probes need --motion")

    rows: list[dict[str, Any]] = []
    with httpx.Client(base_url=args.base_url.rstrip("/")) as http:
        for name in args.suite:
            print(f"\n=== suite: {name} ===")
            if name in PROBES:
                suite_rows, quit_now = PROBES[name](
                    http, args.motion, args.timeout_s
                )
            else:
                suite_rows, quit_now = run_suite(
                    http, suites[name], args.timeout_s
                )
            rows.extend(suite_rows)
            if quit_now:
                print("operator quit; remaining checks stay pending")
                break

    table = to_markdown(args.base_url, rows)
    print("\n" + table)
    if args.out:
        args.out.write_text(table, encoding="utf-8")
        print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
