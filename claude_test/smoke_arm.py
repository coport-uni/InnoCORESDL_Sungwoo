#!/usr/bin/env python3
"""T1 bench smoke for cell6 / cell7: joint 1 +10 deg, verified on encoder.

The acceptance test of docs/SPEC_ARM_REPLAY_CELL.md §7 T1. It answers one
question — *does this arm actually move, and can we read that it moved?* —
and it deliberately does **not** go through the replay path: replay proves
lerobot works, and this has to prove the encoder does.

    python claude_test/smoke_arm.py --ip 192.168.0.58 --robot-id fr5_a

THIS MOVES A ROBOT ARM. Per CLAUDE.md Folder-specific rule #3 and spec §8:
the operator stays at the bench with the e-stop in hand and the arm's reach
clear. The script asks for a typed confirmation before the first motion and
there is deliberately no flag to skip it (spec §8.2). If joint 1 at +10 deg
would hit a limit or a fixture, reposition the arm first — the run starts
from wherever it finds the arm.

The cell server owns the controller while it is up, so **stop it first**
(CLAUDE.md Folder-specific rule #2 applies to TCP sessions here for the
same reason it applies to serial ports: the lerobot follower's connect()
takes an exclusive servo session).

Each round records:

* ``q0`` before, ``q1`` after, and the **increment** ``q1-q0`` — the pass
  criterion is the increment, not the endpoint (LearnedPatterns #33: an
  endpoint check passes on an arm that never moved but started there).
* ``q1b``, a **second, independently issued** read. Two readings that
  agree to the last digit are evidence of a cache, not of accuracy
  (LearnedPatterns #34), so a run of exact ties is flagged.
* joints 2–6, which must not have moved.

Writes ``claude_test/smoke_arm_<robot_id>_<UTC>.md``.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: Joint 1 test displacement, degrees (spec T1 J2).
JOG_DEG = 10.0
#: Pass band on the increment and on the return (spec T1 J4 / J7).
INCREMENT_TOLERANCE_DEG = 0.5
#: Other axes must hold to this (spec T1 J6).
COUPLING_TOLERANCE_DEG = 0.5
#: Rounds per arm (spec T1 J8).
ROUNDS = 3
#: How long to let a MoveJ settle before re-reading, seconds. MoveJ with
#: the default block flag returns on acceptance, not on arrival.
SETTLE_S = 2.0
#: Gap between the two independent reads of J3.
INDEPENDENT_READ_GAP_S = 0.5
#: fairino SDK "no error".
SDK_OK = 0
JOINT_COUNT = 6


def read_joints(rpc) -> list[float]:
    """Read the six joint angles in degrees, over XMLRPC.

    NOT ``rpc.GetActualJointPosDegree()``. Measured on this bench
    2026-08-11: that wrapper reads only the port-20004 real-time state
    struct, and cell6's controller does not serve 20004 — it raises
    ``TypeError: '_ctypes.CField' object is not subscriptable`` there.
    It also hard-codes ``return 0, ...``, so it cannot report a failed
    read at all. The XMLRPC call answers on both arms and returns the
    controller's real error code, which is what J1 has to check.

    Args:
        rpc: A connected ``fairino.Robot.RPC``.

    Returns:
        Six degrees, joint 1 first.

    Raises:
        RuntimeError: The controller reported an error, so there is no
            reading. Never a stale value dressed up as one
            (LearnedPatterns #15).
    """
    result = rpc.robot.GetActualJointPosDegree(1)
    if not result or len(result) <= JOINT_COUNT:
        raise RuntimeError(f"joint read returned {result!r}")
    if int(result[0]) != SDK_OK:
        raise RuntimeError(f"joint read failed with SDK error {result[0]}")
    return [float(v) for v in result[1 : JOINT_COUNT + 1]]


def move_joint1(rpc, target: list[float], speed_pct: float) -> None:
    """MoveJ to ``target`` and wait for it to settle.

    NOT ``rpc.MoveJ(...)``. Measured 2026-08-11: that wrapper opens with
    ``while self.reconnect_flag: time.sleep(0.1)`` on a *class* attribute
    the SDK's state thread latches when the port-20004 stream drops, and
    cell6's controller does not feed that stream — the wrapper spins
    forever there. The raw XMLRPC call is bounded by the socket timeout.
    The wrapper's useful step is kept: MoveJ needs a Cartesian desc_pos
    beside the joint target, derived with GetForwardKin (2-3 ms).

    Also worth knowing before you press MOVE: the wrapper's safety check
    reads the same port-20004 struct, so on cell6 it can never see a
    safety stop. The hardware e-stop is the stop that counts.

    Args:
        rpc: A connected ``fairino.Robot.RPC``.
        target: Six joint angles in degrees.
        speed_pct: Velocity percentage; keep it low on a first run.

    Raises:
        RuntimeError: The controller rejected the motion.
    """
    solved = rpc.robot.GetForwardKin(target)
    if int(solved[0]) != SDK_OK:
        raise RuntimeError(f"GetForwardKin failed with SDK error {solved[0]}")
    desc_pos = [float(v) for v in solved[1:7]]
    error = rpc.robot.MoveJ(
        target,
        desc_pos,
        0,
        0,
        float(speed_pct),
        0.0,
        100.0,
        [0.0, 0.0, 0.0, 0.0],
        -1.0,
        0,
        [0.0] * JOINT_COUNT,
    )
    if int(error) != SDK_OK:
        raise RuntimeError(f"MoveJ rejected with SDK error {error}")
    time.sleep(SETTLE_S)


def confirm_first_motion(ip: str, robot_id: str, q0: list[float]) -> bool:
    """Ask the operator, in words, before anything moves (spec §8.2)."""
    print()
    print("=" * 68)
    print(f"  ABOUT TO MOVE ARM {robot_id} AT {ip}")
    print(f"  joint 1: {q0[0]:+.3f} deg  ->  {q0[0] + JOG_DEG:+.3f} deg")
    print(f"  current pose: {['%+.2f' % v for v in q0]}")
    print()
    print("  Confirm: the arm's reach is clear, you are at the bench,")
    print("  and the e-stop is in your hand.")
    print("=" * 68)
    answer = input("  Type MOVE to continue, anything else to abort: ")
    return answer.strip() == "MOVE"


def run_round(rpc, index: int, speed_pct: float) -> dict:
    """One J2–J7 cycle: out +10 deg, verify, come back.

    Args:
        rpc: A connected ``fairino.Robot.RPC``.
        index: 1-based round number, for the report.
        speed_pct: MoveJ velocity percentage.

    Returns:
        A record of the round, including its pass/fail verdict.
    """
    started = time.monotonic()
    q0 = read_joints(rpc)

    target = list(q0)
    target[0] = q0[0] + JOG_DEG
    move_joint1(rpc, target, speed_pct)

    q1 = read_joints(rpc)
    # J3: a second reading, issued separately rather than reused.
    time.sleep(INDEPENDENT_READ_GAP_S)
    q1b = read_joints(rpc)

    increment = q1[0] - q0[0]
    read_gap = abs(q1[0] - q1b[0])
    coupling = [abs(q1[i] - q0[i]) for i in range(1, JOINT_COUNT)]

    move_joint1(rpc, list(q0), speed_pct)
    q2 = read_joints(rpc)
    return_error = q2[0] - q0[0]

    ok_increment = abs(increment - JOG_DEG) <= INCREMENT_TOLERANCE_DEG
    ok_return = abs(return_error) <= INCREMENT_TOLERANCE_DEG
    ok_coupling = all(c <= COUPLING_TOLERANCE_DEG for c in coupling)
    return {
        "round": index,
        "q0": q0,
        "q1": q1,
        "q1b": q1b,
        "q2": q2,
        "increment_deg": increment,
        "increment_error_deg": increment - JOG_DEG,
        "read_gap_deg": read_gap,
        "max_coupling_deg": max(coupling),
        "return_error_deg": return_error,
        "elapsed_s": time.monotonic() - started,
        "passed": ok_increment and ok_return and ok_coupling,
    }


def render_report(
    ip: str, robot_id: str, speed_pct: float, rounds: list[dict]
) -> str:
    """Build the Markdown record of the run (spec T1 "산출")."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    passed = sum(1 for r in rounds if r["passed"])
    ties = sum(1 for r in rounds if r["read_gap_deg"] == 0.0)
    lines = [
        f"# T1 arm smoke — {robot_id} ({ip})",
        "",
        f"- UTC: {stamp}",
        f"- MoveJ velocity: {speed_pct:.1f} %",
        f"- Displacement: joint 1 {JOG_DEG:+.1f} deg, "
        f"tolerance ±{INCREMENT_TOLERANCE_DEG} deg",
        f"- **Result: {passed}/{len(rounds)} passed**",
        "",
        "| # | q0[0] | q1[0] | increment | error | 2nd-read gap | "
        "max other-axis Δ | return error | s | verdict |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rounds:
        lines.append(
            f"| {r['round']} | {r['q0'][0]:+.3f} | {r['q1'][0]:+.3f} | "
            f"{r['increment_deg']:+.3f} | {r['increment_error_deg']:+.3f} | "
            f"{r['read_gap_deg']:.3f} | {r['max_coupling_deg']:.3f} | "
            f"{r['return_error_deg']:+.3f} | {r['elapsed_s']:.1f} | "
            f"{'PASS' if r['passed'] else 'FAIL'} |"
        )
    lines += ["", "## Full poses", ""]
    for r in rounds:
        for label in ("q0", "q1", "q1b", "q2"):
            values = ", ".join(f"{v:+.3f}" for v in r[label])
            lines.append(f"- round {r['round']} `{label}`: [{values}]")
    gaps = [r["read_gap_deg"] for r in rounds]
    lines += [
        "",
        "## Independence of the two reads (J5)",
        "",
        f"- gaps: {['%.4f' % g for g in gaps]}",
        f"- median: {statistics.median(gaps):.4f} deg",
    ]
    if ties == len(rounds):
        lines.append(
            "- **FLAG: every pair of reads was bit-identical.** Two "
            "readings that agree exactly are evidence of a cached value, "
            "not of a precise one (LearnedPatterns #34). Confirm the "
            "second read really re-queried the controller before "
            "accepting this run."
        )
    else:
        lines.append(f"- {len(rounds) - ties}/{len(rounds)} pairs differed.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="smoke_arm",
        description="T1 bench smoke: joint 1 +10 deg on one FR5 arm.",
    )
    parser.add_argument("--ip", required=True, help="Controller IP address.")
    parser.add_argument(
        "--robot-id", required=True, help="Arm label, e.g. fr5_a."
    )
    parser.add_argument(
        "--suite", default="jog10", choices=("jog10",), help="Test suite."
    )
    parser.add_argument(
        "--speed-pct",
        type=float,
        default=10.0,
        help="MoveJ velocity %%; match the cell's jog_speed_pct.",
    )
    parser.add_argument(
        "--rounds", type=int, default=ROUNDS, help="Repeats (spec T1 J8)."
    )
    args = parser.parse_args(argv)

    from external.FR5Controller.fairino.Robot import RPC

    print(f"[J1] connecting to {args.robot_id} at {args.ip} …")
    rpc = RPC(args.ip)
    q0 = read_joints(rpc)
    print(f"[J1] joints: {['%+.3f' % v for v in q0]}")

    if not confirm_first_motion(args.ip, args.robot_id, q0):
        print("aborted by the operator; nothing moved.")
        return 1

    rounds: list[dict] = []
    try:
        for index in range(1, args.rounds + 1):
            print(f"[J2-J7] round {index}/{args.rounds} …")
            record = run_round(rpc, index, args.speed_pct)
            rounds.append(record)
            print(
                f"        increment {record['increment_deg']:+.3f} deg "
                f"(error {record['increment_error_deg']:+.3f}), "
                f"{'PASS' if record['passed'] else 'FAIL'}"
            )
    except (RuntimeError, OSError) as exc:
        # A failed round is still evidence; write what we have and say so.
        print(f"ERROR: {exc}", file=sys.stderr)
    finally:
        try:
            rpc.robot.StopMotion()  # raw: the wrapper can spin forever
        except Exception as exc:  # noqa: BLE001 — best-effort shutdown
            print(f"warning: StopMotion failed: {exc}", file=sys.stderr)

    if not rounds:
        print("no rounds completed; no report written.", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = REPO_ROOT / "claude_test" / f"smoke_arm_{args.robot_id}_{stamp}.md"
    out.write_text(
        render_report(args.ip, args.robot_id, args.speed_pct, rounds),
        encoding="utf-8",
    )
    passed = sum(1 for r in rounds if r["passed"])
    print(f"\n{passed}/{len(rounds)} rounds passed — report: {out}")
    return 0 if passed == args.rounds else 3


if __name__ == "__main__":
    raise SystemExit(main())
