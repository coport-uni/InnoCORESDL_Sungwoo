#!/usr/bin/env python3
"""Phase-0 reconnaissance for the FR5 Lua job-program path. READ ONLY.

Before any of `arm/program` can be designed against the real controllers,
three things have to be measured rather than assumed:

1. **What Lua programs are actually on this controller.** They were taught
   through the WebApp, so the repo has no record of their names.
2. **What raw `GetProgramState()` returns.** The vendored SDK wrapper is a
   stub — its XMLRPC body is commented out and it hard-returns the
   port-20004 state struct (`external/FR5Controller/fairino/Robot.py`
   ~4915), the same dead struct behind LearnedPatterns #40. The
   commented-out original implies raw XMLRPC gives `(error, state)`, with
   state 1=stopped, 2=running, 3=paused. That has to be confirmed on the
   wire before a polling loop is built on it.
3. **The shape of `GetLuaList()`'s return.** The wrapper splits field 2 on
   `;`; the raw tuple is what the cell would parse.

THIS SCRIPT DOES NOT MOVE THE ARM. It issues getters only — no `Mode()`,
no `ProgramLoad`, no `ProgramRun`, no `MoveJ`. Every probe is wrapped, so
one failing call still leaves the rest of the reconnaissance on the page.

    python claude_test/probe_arm_lua.py --ip 192.168.0.59 --robot-id fr5_b

The cell server owns the controller while it is up, so **stop it first**
(CLAUDE.md Folder-specific rule #2: one owner per device, and here the
session is a TCP one).

Writes ``claude_test/probe_arm_lua_<robot_id>_<UTC>.md``.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: fairino SDK "no error".
SDK_OK = 0
#: Revolute joints on an FR5.
JOINT_COUNT = 6
#: ``flag`` argument to GetActualJointPosDegree: 1 = non-blocking.
JOINT_READ_NONBLOCKING = 1
#: How ``GetProgramState`` encodes itself, per the SDK docstring and the
#: WebAPP manual. Recorded here so the probe report is self-explaining.
PROGRAM_STATES = {1: "stopped / no program", 2: "running", 3: "paused"}


def _probe(label: str, note: str, call) -> dict:
    """Run one getter and record what came back, error or not.

    Args:
        label: Short name of the call, used as the report row key.
        note: Why this call is being made, for the report.
        call: Zero-argument callable issuing the getter.

    Returns:
        ``label``, ``note``, and either ``value`` (the repr of the raw
        return) or ``error`` (the repr of the exception). Never both.
    """
    try:
        value = call()
    except Exception as exc:  # noqa: BLE001 — recording it *is* the job
        return {
            "label": label,
            "note": note,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {"label": label, "note": note, "value": repr(value)}


def collect(rpc) -> list[dict]:
    """Issue every read-only probe against a connected controller.

    Args:
        rpc: A connected ``fairino.Robot.RPC``.

    Returns:
        One record per probe, in the order they were issued.
    """
    return [
        _probe(
            "raw GetLuaList()",
            "which Lua programs exist, and in what return shape",
            rpc.robot.GetLuaList,
        ),
        _probe(
            "raw GetLoadedProgram()",
            "the program the controller currently has loaded, if any",
            rpc.robot.GetLoadedProgram,
        ),
        _probe(
            "raw GetProgramState()",
            "the state code a polling loop would read (1/2/3)",
            rpc.robot.GetProgramState,
        ),
        _probe(
            "wrapper GetProgramState()",
            "expected to fail or to return the dead 20004 struct "
            "(LearnedPatterns #40); recorded as the counter-evidence",
            rpc.GetProgramState,
        ),
        _probe(
            "raw GetCurrentLine()",
            "the progress channel — the only feedback a running Lua "
            "program offers",
            rpc.robot.GetCurrentLine,
        ),
        _probe(
            "raw GetRobotErrorCode()",
            "main/sub fault codes; a latched fault blocks everything",
            rpc.robot.GetRobotErrorCode,
        ),
        _probe(
            "raw GetActualJointPosDegree(1)",
            "proves the session is live and the encoder answers",
            lambda: rpc.robot.GetActualJointPosDegree(JOINT_READ_NONBLOCKING),
        ),
    ]


def interpret_lua_list(record: dict) -> list[str]:
    """Pull program names out of the GetLuaList probe, if it succeeded.

    The wrapper splits field 2 of the raw tuple on ``;``; this repeats
    that against the raw value so the report can list names plainly.

    Args:
        record: The ``raw GetLuaList()`` record from :func:`collect`.

    Returns:
        The program names, or an empty list when the call failed or the
        return did not have the documented shape.
    """
    if "value" not in record:
        return []
    try:
        raw = eval(record["value"])  # noqa: S307 — our own repr, one hop
    except Exception:  # noqa: BLE001 — a weird repr just means no names
        return []
    minimum_fields = 3
    if not isinstance(raw, (list, tuple)) or len(raw) < minimum_fields:
        return []
    if int(raw[0]) != SDK_OK:
        return []
    return [name for name in str(raw[2]).split(";") if name]


def render_report(ip: str, robot_id: str, records: list[dict]) -> str:
    """Build the Markdown record of the reconnaissance.

    Args:
        ip: Controller IP address.
        robot_id: Arm label, e.g. ``fr5_b``.
        records: Probe records from :func:`collect`.

    Returns:
        The report body.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    failed = sum(1 for r in records if "error" in r)
    lines = [
        f"# Phase-0 Lua probe — {robot_id} ({ip})",
        "",
        f"- UTC: {stamp}",
        "- Read-only: no `Mode()`, no `ProgramLoad`, no `ProgramRun`, "
        "no motion.",
        f"- **{len(records) - failed}/{len(records)} probes answered.**",
        "",
        "## Probes",
        "",
    ]
    for record in records:
        lines.append(f"### {record['label']}")
        lines.append("")
        lines.append(f"*{record['note']}*")
        lines.append("")
        if "error" in record:
            lines.append(f"```\nFAILED: {record['error']}\n```")
        else:
            lines.append(f"```\n{record['value']}\n```")
        lines.append("")
    names = interpret_lua_list(records[0])
    lines += ["## Lua programs on this controller", ""]
    if names:
        lines += [f"- `/fruser/{name}`" for name in names]
    else:
        lines.append(
            "- none parsed. Either the controller holds no Lua program, "
            "or `GetLuaList()` does not return the "
            "`(error, count, 'a;b;c')` shape the SDK wrapper assumes — "
            "read the raw value above before designing against it."
        )
    lines += [
        "",
        "## Program-state encoding (for reference)",
        "",
    ]
    lines += [f"- `{code}` — {text}" for code, text in PROGRAM_STATES.items()]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="probe_arm_lua",
        description=(
            "Read-only reconnaissance of the FR5 Lua job-program API. "
            "Issues getters only; nothing moves."
        ),
    )
    parser.add_argument("--ip", required=True, help="Controller IP address.")
    parser.add_argument(
        "--robot-id", required=True, help="Arm label, e.g. fr5_b."
    )
    args = parser.parse_args(argv)

    from external.FR5Controller.fairino.Robot import RPC

    print(f"connecting to {args.robot_id} at {args.ip} …")
    print("read-only: nothing on this path moves the arm.")
    try:
        rpc = RPC(args.ip)
    except OSError as exc:
        print(f"ERROR: cannot reach {args.ip}: {exc}", file=sys.stderr)
        return 2

    records = collect(rpc)
    for record in records:
        outcome = record.get("error", record.get("value", ""))
        print(f"  {record['label']:<32} {outcome}")

    try:
        rpc.CloseRPC()
    except Exception:  # noqa: BLE001 — vendored CloseRPC always raises
        pass

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = (
        REPO_ROOT / "claude_test" / f"probe_arm_lua_{args.robot_id}_{stamp}.md"
    )
    out.write_text(
        render_report(args.ip, args.robot_id, records), encoding="utf-8"
    )
    failed = sum(1 for r in records if "error" in r)
    print(f"\n{len(records) - failed}/{len(records)} answered — report: {out}")
    return 0 if failed == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
