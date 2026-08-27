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

Each cell may also carry a syringe pump. The two SY-01B units on this
bench are CH340-backed, and every CH340 reports the same ``1a86:7523``
with **no USB serial number**, so ``_resolve_port``'s ``VID:PID`` and
``VID:PID:SERIAL`` forms cannot tell them apart — the first would match
both and raise, the second has no serial to match on. They are pinned by
``/dev/serial/by-path/`` instead, which names the physical socket
(controller PCI address plus the chain of hub ports) rather than the
device. See ``CellWiring.pump_port``.

Usage::

    .venv/bin/python claude_test/test_gantry_server_shinyeong.py --cell cell2
    .venv/bin/python claude_test/test_gantry_server_shinyeong.py --cell cell3

    # gantry only, as before the pumps arrived
    .venv/bin/python claude_test/test_gantry_server_shinyeong.py \
        --cell cell2 --no-pump

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
from sy01b import SyringePumpController  # noqa: E402


@dataclass(frozen=True, slots=True)
class CellWiring:
    """Which adapters belong to one cell, and where it listens."""

    serial_x: str
    serial_z_a: str
    serial_z_b: str
    port: int
    #: This cell's SY-01B, as a `/dev/serial/by-path/` device path, or
    #: None for a cell with no pump on the bench. NOT a `VID:PID` spec:
    #: both pumps are CH340s reporting `1a86:7523` with no USB serial, so
    #: `VID:PID` matches both (`_resolve_port` raises) and
    #: `VID:PID:SERIAL` has nothing to match. by-path names the SOCKET,
    #: not the device — move the cable and the config now points at
    #: whatever is in that socket, so label the sockets. Survives the
    #: `ttyUSB*` renumbering and the EMI re-enumerations, because neither
    #: changes where the plug is.
    pump_port: str | None


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
        # USB 3-2.1.1 — root port 2, then two hubs. Read off NUC2
        # 2026-08-27; it was /dev/ttyUSB2 that day, which is exactly the
        # number this path exists not to depend on.
        pump_port=(
            "/dev/serial/by-path/pci-0000:00:14.0-usb-0:2.1.1:1.0-port0"
        ),
    ),
    "cell3": CellWiring(
        serial_x="NTB3FXCE",
        serial_z_a="NTA4FH8Q",
        serial_z_b="NT9ZVXLU",
        port=17058,
        # USB 3-7.3.1 — root port 7, then two hubs. Same session; it was
        # /dev/ttyUSB0 that day.
        pump_port=(
            "/dev/serial/by-path/pci-0000:00:14.0-usb-0:7.3.1:1.0-port0"
        ),
    ),
}

#: Adapters on this bus that belong elsewhere. Printed at startup so the
#: operator can see what the launcher is leaving alone, and so a rewiring
#: that moves one of them into a gantry cell is noticed rather than
#: silently absorbed.
FOREIGN = {
    "NTB3EP5R": "cell5 zstage (server/nuc2/cell5.toml)",
}

#: What `--no-pump` resolves to. `Config.pump_port = None` is the
#: repository's own way of saying "this cell has no pump": the pump
#: action set then answers 409, exactly as `server/__main__.py` `_load`
#: arranges when a config has no `[pump]` table. Use it when a pump is
#: off the bench — it is not a degraded mode, it is how these cells ran
#: before the pumps arrived.
NO_PUMP: str | None = None

#: The SY-01B's DT address, set on the pump's own rotary switch (switch
#: position N answers as address N+1; F is the self-test, not an
#: address). Give each pump on a bench a DIFFERENT one even though each
#: has its own USB link: a config that points at the wrong by-path then
#: times out instead of quietly dispensing from the other pump.
DEFAULT_PUMP_ADDRESS = 1

#: 125 uL barrels, matching cell1. `init_force` 2 is the one-third-force
#: homing code for 50-125 uL syringes and must be changed with the
#: barrel, not independently of it.
DEFAULT_SYRINGE_UL = 125
DEFAULT_INIT_FORCE = 2

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


def _open_pump(config: Config, cell_name: str) -> SyringePumpController:
    """Open this cell's pump and refuse to serve one that cannot talk.

    Goes through `PumpGantryCell`'s own opener rather than
    `SyringePumpController.open` so the pump gets the same USB
    re-enumeration tolerance cell1 has: the amp's conducted noise drops
    the CH340 mid-open, and `_open_pump_patiently` waits the gap out
    instead of failing the launch (LearnedPatterns #35).

    Then `diagnose()`, for the same reason `_prove_reachable` reads the
    encoders: a pump that enumerates but does not answer would otherwise
    be discovered by the first scenario step that needs it. It also
    prints the pump's OWN serial number, which is the only way to
    confirm that this by-path really leads to the pump you think it
    does — the socket cannot tell you that.

    Raises:
        BenchRefusal: The pump did not open, or opened and cannot talk.
    """
    assert config.pump_port is not None
    print(f"Opening pump {config.pump_port}")
    try:
        pump = PumpGantryCell._open_pump_patiently(
            PumpGantryCell._pump_config(config), "open"
        )
    except Exception as exc:  # noqa: BLE001 — surfaced as a refusal below
        raise BenchRefusal(
            f"{cell_name}'s pump did not open at {config.pump_port}: "
            f"{exc}. Check that the pump is powered and that this "
            f"by-path still exists (`ls -l /dev/serial/by-path/`) — the "
            f"path names a socket, so an unplugged or re-socketed pump "
            f"makes it vanish."
        ) from exc

    try:
        report = pump.diagnose()
    except Exception as exc:  # noqa: BLE001 — surfaced as a refusal below
        pump.close()
        raise BenchRefusal(
            f"{cell_name}'s pump opened at {config.pump_port} but did "
            f"not answer diagnose(): {exc}"
        ) from exc

    print(
        f"Pump answers: fw {report.software_version}  "
        f"serial {report.serial_number}  {report.supply_volts} V  "
        f"address {config.pump_address}"
    )
    if not report.ok_to_initialize:
        pump.close()
        raise BenchRefusal(
            f"{cell_name}'s pump reports it is not ready to initialize "
            f"({report}). Refusing to serve."
        )
    print(f"{cell_name}'s pump is reachable.\n")
    return pump


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
    parser.add_argument(
        "--pump-port",
        default=None,
        help="override this cell's pump device path. Prefer a "
        "/dev/serial/by-path/ entry; a bare /dev/ttyUSBn works but is "
        "renumbered by the next re-enumeration.",
    )
    parser.add_argument(
        "--no-pump",
        action="store_true",
        help="serve the gantry alone, as before the pumps arrived. "
        "/v1/pump/* then answers 409.",
    )
    parser.add_argument(
        "--pump-address",
        type=int,
        default=DEFAULT_PUMP_ADDRESS,
        help=f"DT address set on the pump's rotary switch "
        f"(default {DEFAULT_PUMP_ADDRESS})",
    )
    parser.add_argument(
        "--syringe-ul",
        type=int,
        default=DEFAULT_SYRINGE_UL,
        help=f"barrel size in uL (default {DEFAULT_SYRINGE_UL})",
    )
    parser.add_argument(
        "--init-force",
        type=int,
        default=DEFAULT_INIT_FORCE,
        help=f"Z<force> homing code; {DEFAULT_INIT_FORCE} is "
        f"one-third force, for 50-125 uL barrels",
    )
    args = parser.parse_args(argv)
    if args.no_pump and args.pump_port:
        parser.error("--no-pump and --pump-port contradict each other")

    wiring = WIRING[args.cell]
    port = args.port if args.port is not None else wiring.port
    # --no-pump wins, then an explicit --pump-port, then the bench value
    # recorded in WIRING. A cell whose WIRING carries no pump_port and
    # that got no --pump-port simply has no pump, as before.
    if args.no_pump:
        pump_port = NO_PUMP
    elif args.pump_port:
        pump_port = args.pump_port
    else:
        pump_port = wiring.pump_port

    print(
        f"{args.cell} L1 server — NUC2, adapters named explicitly.\n"
        f"Starting the server commands no motion. The physical e-stop "
        f"remains the only stop once a scenario runs: POST /v1/stop queues "
        f"behind the move it means to interrupt (docs/L1_AUDIT.md GAP-9).\n"
    )

    config = Config(
        pump_port=pump_port,
        pump_address=args.pump_address,
        syringe_uL=args.syringe_ul,
        pump_init_force=args.init_force,
        motor_serial_x=wiring.serial_x,
        z_coord_invert=COORD_INVERT,
        x_coord_invert=COORD_INVERT,
        home_dir_z=HOME_DIR,
        home_dir_x=HOME_DIR,
    )

    pump: SyringePumpController | None = None
    try:
        _check_bus(wiring, args.cell)
        z_a, z_b, x = _open_motors(wiring)
        _prove_reachable(z_a, z_b, x, args.cell)
        # After the gantry, not before: a bench that is refusing on its
        # motors should not have had its pump opened first.
        if pump_port is not None:
            pump = _open_pump(config, args.cell)
        else:
            print(f"{args.cell}: no pump — /v1/pump/* will answer 409.\n")
    except BenchRefusal as refusal:
        print(f"\n[REFUSED] {refusal}")
        return EXIT_REFUSED

    cell = PumpGantryCell(pump, z_a, z_b, x, config)

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
