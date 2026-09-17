#!/usr/bin/env python3
"""Read-only census of every MKS motor reachable from NUC2.

Answers one question per adapter: *is there a motor behind it that
answers?* Nothing moves, and nothing is written to a motor — the only
CAN frame sent is ``0x31`` (read cumulative encoder), which is the same
question ``diagnose()`` asks and the cheapest one only a powered,
correctly wired motor can answer.

Deliberately does **not** call ``MKSMotor.setup()``. That would write
SR_vFOC mode and the slave-response flag into motors belonging to cells
this probe has no business configuring. The trade-off is stated in the
output: a motor with active-response disabled would also read as no
reply, so a silent adapter means "not answering", not "broken".

Adapter ownership is taken from the configs and the operator's wiring
list, not guessed:

    cell2  NTAFT1KQ, NTA0X8KN     (X adapter NTB19XKA is not attached)
    cell3  NTB3FXCE, NTA4FH8Q, NT9ZVXLU
    cell5  NTB3EP5R               (server/nuc2/cell5.toml [zstage])

Usage::

    .venv/bin/python claude_test/test_nuc2_motor_census_shinyeong.py
"""

from __future__ import annotations

import sys

from pyftdi.ftdi import Ftdi

from mks_motor import MKSMotor, prepare_usb_nodes, release_ftdi_sio

#: serial -> (cell, axis). Every adapter the wiring list names. An entry
#: that is not on the bus is reported as absent rather than omitted, so
#: "I did not look" never reads as "it is fine". NTB19XKA was absent on
#: the first census of 2026-07-29 and appeared after a re-plug, which is
#: why it lives here rather than in a separate absent-list.
EXPECTED = {
    "NTAFT1KQ": ("cell2", "Z_A"),
    "NTA0X8KN": ("cell2", "Z_B"),
    "NTB19XKA": ("cell2", "X"),
    "NTB3FXCE": ("cell3", "X"),
    "NTA4FH8Q": ("cell3", "Z_A"),
    "NT9ZVXLU": ("cell3", "Z_B"),
    "NTB3EP5R": ("cell5", "Z"),
}

#: How many encoder reads to try per motor before calling it silent. The
#: driver already retries the CAN response internally; this covers the
#: firmware's habit of dropping the first command after a fresh link.
READ_ATTEMPTS = 3


def _probe(serial: str) -> tuple[bool, float | None, str]:
    """Open one adapter and ask the motor behind it for its position.

    Args:
        serial: FTDI chip serial of the USB2CAN adapter.

    Returns:
        Tuple of (adapter_opened, position_mm_or_None, note).
    """
    motor = None
    try:
        motor = MKSMotor.open(serial=serial)
    except Exception as exc:  # noqa: BLE001 — report, never abort the census
        return (False, None, f"adapter would not open: {exc}")

    try:
        for _ in range(READ_ATTEMPTS):
            try:
                mm = motor.read_position_mm()
            except ConnectionError:
                mm = None
            if mm is not None:
                return (True, mm, "motor answered")
        return (True, None, "adapter open, motor did NOT answer")
    finally:
        motor.close()


def main() -> int:
    """Probe every adapter and print the census. Returns an exit code."""
    print(
        "NUC2 motor census — read-only.\n"
        "No motion is commanded and no motor setting is written; the "
        "only frame sent is an encoder read.\n"
    )
    prepare_usb_nodes()
    release_ftdi_sio()

    present = [url.sn for url, _ in Ftdi.list_devices()]
    print(
        f"FTDI adapters on the bus ({len(present)}): "
        f"{', '.join(sorted(present))}\n"
    )

    rows: list[tuple[str, str, str, str]] = []
    for serial in sorted(present):
        cell, axis = EXPECTED.get(serial, ("UNKNOWN", "?"))
        opened, mm, note = _probe(serial)
        if not opened:
            verdict = "ADAPTER FAIL"
        elif mm is None:
            verdict = "NO REPLY"
        else:
            verdict = f"ok  {mm:8.3f} mm"
        rows.append((cell, axis, serial, verdict))
        print(f"  {cell:6s} {axis:4s} {serial}  {verdict}   ({note})")

    for serial, (cell, axis) in EXPECTED.items():
        if serial not in present:
            rows.append((cell, axis, serial, "NOT ON BUS"))
            print(
                f"  {cell:6s} {axis:4s} {serial}  NOT ON BUS   "
                f"(did not enumerate; check power and the USB path)"
            )

    print("\n=== summary by cell ===")
    for cell in sorted({r[0] for r in rows}):
        entries = [r for r in rows if r[0] == cell]
        bad = [r for r in entries if not r[3].startswith("ok")]
        state = (
            "ALL OK"
            if not bad
            else (
                f"{len(bad)} of {len(entries)} not answering: "
                f"{', '.join(f'{r[1]}({r[2]})' for r in bad)}"
            )
        )
        print(f"  {cell}: {state}")

    print(
        "\nA silent motor is power, CAN wiring, or a non-default CAN id — "
        "in that order of likelihood. An adapter that opens proves the "
        "USB half is fine, so the fault is downstream of it."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
