"""Cell facade + error hierarchy for the InnoCORESDL /v1 server.

The L1 server is a thin HTTP bridge over a **Cell** — a composition of
devices behind one interface. Three implementations satisfy the
:class:`Cell` protocol, each owning a different set of devices:

* :class:`PumpGantryCell` (cell1–3) — pump + XZ gantry.
* :class:`BalanceLinearCell` (cell4) — balance + linear Y rail.
* :class:`PumpZThermalCell` (cell5, Cell 5) — pump + single Z stage +
  hotplate + IR lamp on a Tapo plug.

All three drive real drivers opened at the bench; verification is
hardware-in-the-loop and there is no in-memory fake.

The protocol lists **every** action set. A cell implements the ones its
hardware has and raises :class:`WrongStateError` from the rest, so a
misdirected ``/v1`` call gets a clean 409 instead of an AttributeError —
see the ``_no_*()`` stubs in each implementation.

Every device fault surfaces as a :class:`CellError` subclass so the server
maps it to a stable HTTP status + JSON envelope (see ``server/errors.py``).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class CellError(Exception):
    """Base for all cell faults.

    Carries the optional originating ``command`` and a device ``code`` so the
    server can serialize a stable error envelope without leaking tracebacks.
    """

    def __init__(
        self,
        message: str,
        *,
        command: str | None = None,
        code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.command = command
        self.code = code


class InvalidArgError(CellError):
    """A request argument is out of range or malformed (HTTP 400)."""


class WrongStateError(CellError):
    """Operation not allowed in the current state, e.g. not initialized,
    plunger overflow (HTTP 409)."""


class DeviceFaultError(CellError):
    """A device reported a hardware fault — overload, init failure (HTTP 500)."""


class TransportError(CellError):
    """The serial/USB transport is closed or unreachable (HTTP 503)."""


class CellTimeoutError(CellError):
    """A device did not respond within its timeout (HTTP 504)."""


# Ambient-filter levels accepted by the balance (entris_ii.set_ambient).
AMBIENT_LEVELS = ("very_stable", "stable", "unstable", "very_unstable")


@runtime_checkable
class Cell(Protocol):
    """Interface the /v1 routes call. Implemented by PumpGantryCell and
    BalanceLinearCell.

    All methods are synchronous and blocking; the server runs them in a
    worker thread under a single ``asyncio.Lock`` (one command in flight).
    """

    def diagnose(self) -> dict: ...
    def status(self) -> dict: ...
    # Balance
    def tare(self) -> float: ...
    def calibrate(self) -> float: ...
    def read_weight(self) -> tuple[float, bool]: ...
    def set_ambient(self, level: str) -> str: ...
    # Pump
    def initialize(self, *, force: int = 2, ccw: bool = False) -> dict: ...
    def move_valve(self, port: int) -> str: ...
    def aspirate(self, target_uL: float) -> float: ...
    def dispense(self, target_uL: float = 0.0) -> float: ...
    def cycle(
        self,
        *,
        cycles: int,
        volume_uL: float,
        source_port: int,
        dispense_port: int,
    ) -> dict: ...
    # Gantry actions (XZ, 2 axes + speed/accel) — pump+gantry cells
    def home_gantry(self) -> tuple[float, float]: ...
    def move_gantry(
        self, x_mm: float, z_mm: float, *, speed_pct: int, accel_pct: int
    ) -> tuple[float, float]: ...
    # Linear actions (Y rail, 1 axis; the driver owns the speed profile) —
    # balance+linear cells. A separate action set from the gantry on purpose:
    # different motor, axis, and wire protocol (see ARCHITECTURE.md).
    def home_linear(self) -> float: ...
    def move_linear(self, y_mm: float) -> float: ...
    # Z-stage actions (a SINGLE Z axis, no X) — Cell 5 / cell5. Its own set
    # rather than a reuse of the gantry: the gantry signature carries an X
    # target and its motion goes through the paired-Z group interlock, and
    # this cell has neither. Same driver family (mks_motor over CAN), one
    # motor, no group.
    def home_zstage(self) -> float: ...
    def move_zstage(
        self, z_mm: float, *, speed_pct: int, accel_pct: int
    ) -> float: ...
    # Hotplate actions (IKA RCT digital) — Cell 5 / cell5. ``heating`` and
    # ``stirring`` are the last commanded state: the RCT protocol offers no
    # readback for either.
    def read_hotplate(self) -> dict: ...
    def set_hotplate_temperature(self, celsius: float) -> float: ...
    def set_hotplate_heater(self, *, enabled: bool) -> dict: ...
    def set_hotplate_speed(self, rpm: float) -> float: ...
    def set_hotplate_stirrer(self, *, enabled: bool) -> dict: ...
    # Lamp actions (IR lamp on a Tapo plug) — Cell 5 / cell5. The only
    # network-attached device in any cell; still owned by exactly one cell.
    def read_lamp(self) -> dict: ...
    def set_lamp(self, *, enabled: bool) -> dict: ...
    # Safety / lifecycle
    def stop(self) -> None: ...
    def close(self) -> None: ...
