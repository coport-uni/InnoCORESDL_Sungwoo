#!/usr/bin/env python3
"""Serve cell2 / cell3's L1 ``/v1`` API on NUC2, adapters named explicitly.

``python -m server --config server/nuc2/cell2.toml`` cannot be used on this
bench. It reaches ``PumpGantryCell.open``, which calls
``MKSMotor.open_xz(serial_x)`` — that names only the X adapter and assigns
**whichever two FTDI adapters remain** to Z_A and Z_B
(``external/ESP32S3BOX3MotorController/src/mks_motor/mks_motor.py``
``open_xz``). NUC2 carries seven adapters across three cells, so:

* starting cell2 that way would pick up cell3's and cell5's Z motors;
* starting cell2 **and** cell3 together is impossible — whichever came
  second would take motors the first is already driving.

Since running both at once is exactly what the L2 orchestration test needs,
this launcher opens all three adapters by serial and hands the finished cell
to ``server.app.create_app``, which takes a factory and constructs nothing
itself. No existing file is modified: the cell class, the routes, the error
mapping and the schemas are the repository's own.

Refuses to serve unless every one of the three motors answers an encoder
read. A cell whose motor is unpowered or off the CAN bus still starts
happily otherwise, and the first thing it would do is report positions it
cannot measure (LearnedPatterns #24). Serving is not motion, so this check
costs nothing and runs before uvicorn binds.

Usage::

    .venv/bin/python claude_test/test_gantry_server_shinyeong.py --cell cell2
    .venv/bin/python claude_test/test_gantry_server_shinyeong.py --cell cell3

Ports follow SDLClaude's table and ``orchestrator/config.toml``:
cell2 = 17056, cell3 = 17058.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

# The repository root, so `cell` and `server` import when this file is run
# as a script rather than through `python -m`.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import uvicorn  # noqa: E402 — must follow the sys.path insert above
from pyftdi.ftdi import Ftdi  # noqa: E402

from cell.pump_gantry_cell import Config, PumpGantryCell  # noqa: E402
from mks_motor import (  # noqa: E402
    MKSMotor,
    prepare_usb_nodes,
    release_ftdi_sio,
)
from server.app import create_app  # noqa: E402


@dataclass(frozen=True, slots=True)
class CellWiring:
    """Which adapters belong to one cell, and where it listens."""

    serial_x: str
    serial_z_a: str
    serial_z_b: str
    port: int


#: Read off NUC2's live bus on 2026-07-29 and cross-checked against the
#: operator's wiring list. cell2's X was `NTB19XKA`; the list's `NTAF1KQ`
#: is really `NTAFT1KQ` (a dropped character), and `NTB3EP5R` belongs to
#: cell5, not cell2 — `server/nuc2/cell5.toml` claims it as its zstage.
#: Two of three serials agreeing is exactly what a swapped adapter looks
#: like (LearnedPatterns #22), which is why all three are named here.
WIRING = {
    "cell2": CellWiring(
        serial_x="NTB19XKA",
        serial_z_a="NTAFT1KQ",
        serial_z_b="NTA0X8KN",
        port=17056,
    ),
    "cell3": CellWiring(
        serial_x="NTB3FXCE",
        serial_z_a="NTA4FH8Q",
        serial_z_b="NT9ZVXLU",
        port=17058,
    ),
}

#: Adapters on this bus that belong elsewhere. Printed at startup so the
#: operator can see what the launcher is leaving alone, and so a rewiring
#: that moves one of them into a gantry cell is noticed rather than
#: silently absorbed.
FOREIGN = {
    "NTB3EP5R": "cell5 zstage (server/nuc2/cell5.toml)",
}

#: Neither gantry cell on NUC2 has a syringe pump on the bench, so the
#: pump action set answers 409. `Config.pump_port = None` is the
#: repository's own way of saying that (`server/__main__.py` `_load`
#: does the same when a config has no `[pump]` table).
NO_PUMP: str | None = None

#: Both axes home at the 0x00 end and travel +mm into the working envelope
#: via coord_invert. This is `Config`'s own default and cell1's verified
#: convention; cell2/cell3 are documented as clones of cell1. UNVERIFIED
#: on their hardware until an absolute move off the home limit succeeds —
#: an axis that stops dead at ~0 mm means this is wrong for that axis, not
#: that the motor is broken (LearnedPatterns #4).
COORD_INVERT = True
HOME_DIR = 0x00

DEFAULT_HOST = "0.0.0.0"
DEFAULT_LOG_LEVEL = "info"

#: uvicorn's default keep-alive is shorter than a homing run, and the
#: orchestrator holds one connection across a step. Matches the value
#: `server/__main__.py` passes.
KEEP_ALIVE_S = 120

EXIT_OK = 0
EXIT_REFUSED = 1


class BenchRefusal(Exception):
    """The bench is not in a state where serving this cell is safe."""


def _check_bus(wiring: CellWiring, cell_name: str) -> None:
    """Refuse unless this cell's three adapters are all on the bus.

    Args:
        wiring: The adapters this cell owns.
        cell_name: Cell being started, for the message.

    Raises:
        BenchRefusal: If any of the three adapters is absent.
    """
    present = [url.sn for url, _ in Ftdi.list_devices()]
    mine = (wiring.serial_x, wiring.serial_z_a, wiring.serial_z_b)

    print(f"FTDI adapters on this bus ({len(present)}):")
    for serial in sorted(present):
        if serial == wiring.serial_x:
            note = f"{cell_name} X — this server drives it"
        elif serial in (wiring.serial_z_a, wiring.serial_z_b):
            note = f"{cell_name} Z — this server drives it"
        else:
            note = f"{FOREIGN.get(serial, 'another cell')} — NOT touched"
        print(f"  {serial}  <- {note}")

    missing = [s for s in mine if s not in present]
    if missing:
        raise BenchRefusal(
            f"{cell_name} adapter(s) not on the bus: {', '.join(missing)}. "
            f"Nothing was opened. An adapter that is plugged in always "
            f"enumerates, so check power and the USB path first."
        )
    print(f"All three {cell_name} adapters present.\n")


def _open_motors(wiring: CellWiring) -> tuple[MKSMotor, MKSMotor, MKSMotor]:
    """Open this cell's three adapters by serial, in SR_vFOC.

    Args:
        wiring: The adapters this cell owns.

    Returns:
        Tuple ``(z_a, z_b, x)``, matching ``PumpGantryCell.__init__``'s
        argument order.
    """
    # Both are no-ops unprivileged and touch only FTDI adapters; mirrors
    # what `PumpGantryCell.open` does before opening the gantry.
    prepare_usb_nodes()
    release_ftdi_sio()

    print(f"Opening X   {wiring.serial_x}")
    x = MKSMotor.open(serial=wiring.serial_x, coord_invert=COORD_INVERT)
    print(f"Opening Z_A {wiring.serial_z_a}")
    z_a = MKSMotor.open(serial=wiring.serial_z_a, coord_invert=COORD_INVERT)
    print(f"Opening Z_B {wiring.serial_z_b}")
    z_b = MKSMotor.open(serial=wiring.serial_z_b, coord_invert=COORD_INVERT)

    # Without setup() the firmware ignores later home/move commands or
    # never replies. `PumpGantryCell.open` does the same, in this order.
    for motor in (z_a, z_b, x):
        motor.setup()
    return z_a, z_b, x


def _prove_reachable(
    z_a: MKSMotor, z_b: MKSMotor, x: MKSMotor, cell_name: str
) -> None:
    """Refuse to serve unless all three motors answer an encoder read.

    Reading the encoder is the cheapest question only a powered, wired
    motor can answer, and it commands no motion.

    Raises:
        BenchRefusal: If any axis does not answer.
    """
    axes = (("X", x), ("Z_A", z_a), ("Z_B", z_b))
    readings: dict[str, float | None] = {}
    for label, motor in axes:
        try:
            readings[label] = motor.read_position_mm()
        except ConnectionError:
            readings[label] = None

    shown = "  ".join(
        f"{label} {'unread' if mm is None else f'{mm:.3f} mm'}"
        for label, mm in readings.items()
    )
    print(f"Live encoder read: {shown}")

    silent = [label for label, mm in readings.items() if mm is None]
    if silent:
        raise BenchRefusal(
            f"{cell_name} axis/axes {', '.join(silent)} did not answer an "
            f"encoder read. Refusing to serve: this cell would report "
            f"positions it cannot measure. Check motor power and the CAN "
            f"wiring, then re-run "
            f"claude_test/test_nuc2_motor_census_shinyeong.py."
        )
    print(f"{cell_name} is reachable on all three axes.\n")


def main(argv: list[str] | None = None) -> int:
    """Open one gantry cell and serve it. Returns a process exit code."""
    parser = argparse.ArgumentParser(
        description="Serve cell2/cell3's /v1 API with named adapters."
    )
    parser.add_argument(
        "--cell",
        required=True,
        choices=sorted(WIRING),
        help="which cell to serve",
    )
    parser.add_argument(
        "--host", default=DEFAULT_HOST, help=f"bind host ({DEFAULT_HOST})"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="bind port (default: cell2=17056, cell3=17058)",
    )
    parser.add_argument(
        "--log-level", default=DEFAULT_LOG_LEVEL, help="uvicorn log level"
    )
    args = parser.parse_args(argv)

    wiring = WIRING[args.cell]
    port = args.port if args.port is not None else wiring.port

    print(
        f"{args.cell} L1 server — NUC2, adapters named explicitly.\n"
        f"Starting the server commands no motion. The physical e-stop "
        f"remains the only stop once a scenario runs: POST /v1/stop queues "
        f"behind the move it means to interrupt (docs/L1_AUDIT.md GAP-9).\n"
    )

    try:
        _check_bus(wiring, args.cell)
        z_a, z_b, x = _open_motors(wiring)
        _prove_reachable(z_a, z_b, x, args.cell)
    except BenchRefusal as refusal:
        print(f"\n[REFUSED] {refusal}")
        return EXIT_REFUSED

    config = Config(
        pump_port=NO_PUMP,
        motor_serial_x=wiring.serial_x,
        z_coord_invert=COORD_INVERT,
        x_coord_invert=COORD_INVERT,
        home_dir_z=HOME_DIR,
        home_dir_x=HOME_DIR,
    )
    cell = PumpGantryCell(None, z_a, z_b, x, config)

    # create_app takes a factory and builds no cell of its own, so the
    # already-opened cell goes in untouched. Its lifespan closes the cell
    # on shutdown.
    app = create_app(cell_factory=lambda: cell)
    print(f"serving {args.cell} on http://{args.host}:{port}\n")
    uvicorn.run(
        app,
        host=args.host,
        port=port,
        log_level=args.log_level,
        timeout_keep_alive=KEEP_ALIVE_S,
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
