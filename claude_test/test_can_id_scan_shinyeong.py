#!/usr/bin/env python3
"""Find which CAN id a silent MKS motor is actually listening on.

``MKSMotor`` addresses every motor at ``can_id=0x01``. A motor whose
front panel shows a healthy ``0.0°/0.00err/0clk`` but which never
answers an encoder read is either not wired to its adapter, or is
listening on a different id — and those two have completely different
fixes. This probe separates them: it sweeps a range of ids on one
adapter and reports any that answer.

Read-only. The only frame sent is ``0x31`` (read cumulative encoder), at
each candidate id in turn. No motion is commanded, no setting is
written, and the adapter is opened once and re-addressed in place rather
than re-opened per id.

Interpretation:

* **an id answers** — the motor is alive and wired; its id is simply not
  1. Either set the motor back to 1 from its panel, or record the real
  id for whatever config drives it.
* **no id answers** — the fault is between the adapter and the motor:
  CAN H/L wiring, a connector, or termination. The id hypothesis is
  excluded, which is the point of running this.

Usage::

    .venv/bin/python claude_test/test_can_id_scan_shinyeong.py
    .venv/bin/python claude_test/test_can_id_scan_shinyeong.py \
        --serial NTA4FH8Q --max-id 32
"""

from __future__ import annotations

import argparse
import sys

from mks_motor import MKSMotor, prepare_usb_nodes, release_ftdi_sio

#: The adapters that opened but whose motor stayed silent in the census
#: of 2026-07-29. Scanned by default so the common case needs no flags.
DEFAULT_SERIALS = ("NTA4FH8Q", "NT9ZVXLU", "NTB19XKA")

#: Ids to sweep. MKS accepts a much wider range, but an id set by hand
#: from the motor's own panel is realistically a small number, and every
#: extra id costs a full no-reply timeout.
DEFAULT_MAX_ID = 16
FIRST_ID = 1

#: The id the driver and every cell config assume.
EXPECTED_ID = 0x01


def _scan(serial: str, max_id: int) -> list[tuple[int, float]]:
    """Sweep CAN ids on one adapter and collect the ones that answer.

    Args:
        serial: FTDI chip serial of the USB2CAN adapter.
        max_id: Highest CAN id to try, inclusive.

    Returns:
        List of (can_id, position_mm) for every id that replied.
    """
    print(f"\n=== {serial} — sweeping CAN id {FIRST_ID}..{max_id} ===")
    try:
        motor = MKSMotor.open(serial=serial)
    except Exception as exc:  # noqa: BLE001 — one bad adapter must not
        print(f"  adapter would not open: {exc}")  # end the whole sweep
        return []

    found: list[tuple[int, float]] = []
    try:
        for can_id in range(FIRST_ID, max_id + 1):
            # Re-address in place: MKSMotor reads self.can_id on every
            # frame it builds, so this costs nothing but the read.
            motor.can_id = can_id
            try:
                mm = motor.read_position_mm()
            except ConnectionError:
                mm = None
            if mm is None:
                print(f"  id {can_id:3d}  -")
                continue
            print(f"  id {can_id:3d}  ANSWERED  {mm:8.3f} mm")
            found.append((can_id, mm))
    finally:
        motor.close()
    return found


def main(argv: list[str] | None = None) -> int:
    """Scan each requested adapter. Returns a process exit code."""
    parser = argparse.ArgumentParser(
        description="Sweep CAN ids on a silent MKS motor (read-only)."
    )
    parser.add_argument(
        "--serial",
        action="append",
        default=None,
        help="adapter serial to scan; repeatable "
        f"(default: {', '.join(DEFAULT_SERIALS)})",
    )
    parser.add_argument(
        "--max-id",
        type=int,
        default=DEFAULT_MAX_ID,
        help=f"highest CAN id to try (default {DEFAULT_MAX_ID})",
    )
    args = parser.parse_args(argv)
    serials = args.serial or list(DEFAULT_SERIALS)

    print(
        "CAN id scan — read-only.\n"
        "No motion is commanded and no motor setting is written.\n"
        f"Every cell config in this repo assumes id {EXPECTED_ID}."
    )
    prepare_usb_nodes()
    release_ftdi_sio()

    results = {serial: _scan(serial, args.max_id) for serial in serials}

    print("\n=== verdict ===")
    for serial, found in results.items():
        if not found:
            print(
                f"  {serial}: no id answered — the id hypothesis is "
                f"EXCLUDED. Look at the CAN wiring between this adapter "
                f"and its motor (H/L swap, connector, termination)."
            )
            continue
        ids = ", ".join(str(can_id) for can_id, _ in found)
        if len(found) == 1 and found[0][0] == EXPECTED_ID:
            print(f"  {serial}: answers on the expected id {EXPECTED_ID}.")
        else:
            print(
                f"  {serial}: ANSWERS ON id {ids} — the motor is alive "
                f"and wired, it is simply not on id {EXPECTED_ID}. Set it "
                f"back from the motor's panel, or record the real id."
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
