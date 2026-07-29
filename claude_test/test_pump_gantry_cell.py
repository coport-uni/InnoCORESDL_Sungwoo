"""Unit tests for :class:`cell.pump_gantry_cell.PumpGantryCell`.

No hardware: ``PumpGantryCell.__init__`` takes the pump and the three
motors as arguments, so the fakes below stand in for all four. Only
``open()`` touches USB, and these never call it.

Two properties are covered, both learned on hardware rather than guessed:

* **The pump is optional.** cell1 runs its gantry while the SY-01B is off
  the bench, so a cell built with ``pump=None`` must serve every gantry
  action and answer 409 — not crash — on every pump action.
* **A move is confirmed by reading the encoder back, never by echoing the
  target.** ``MKSMotor.move_to`` *prints* ``[ERROR] Motor failed to start``
  and returns on a rejected F5, and ``move_sync`` discards that return
  value, so a gantry that never moved would otherwise report ``200 OK``
  with the requested position — the same fabricated-success failure
  LearnedPatterns #15/#17 removed from cell4's rail, on an axis whose
  paired-Z desync damages the mechanism.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cell.cell_protocol import (  # noqa: E402
    DeviceFaultError,
    TransportError,
    WrongStateError,
)
from cell.pump_gantry_cell import (  # noqa: E402
    ARRIVAL_TOLERANCE_MM,
    Z_DESYNC_LIMIT_MM,
    Config,
    PumpGantryCell,
)


class FakeMotor:
    """One MKS motor. ``position`` is what the encoder will report.

    ``lands`` models the firmware behaviour the confirm step exists to
    catch: False means the motor accepts the command and stays put, which
    is what a dropped F5 looks like from the driver's side.
    """

    def __init__(self, position: float = 0.0, *, lands: bool = True) -> None:
        self.position = position
        self.lands = lands
        self.coord_invert = False
        self.silent = False
        self.closed = False
        self.stopped = False

    def move_to(self, mm: float, speed_pct: int = 20, accel_pct: int = 10):
        if self.lands:
            self.position = mm

    def read_position_mm(self) -> float | None:
        if self.silent:
            raise ConnectionError("no reply after retries")
        return self.position

    def emergency_stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


class FakePump:
    """SY-01B stand-in — only what the cell actually calls."""

    def __init__(self) -> None:
        self.initialized = False

    def initialize(self, *, force: int = 2, ccw: bool = False) -> None:
        self.initialized = True

    def query_valve_position(self) -> str:
        return "1"


def _cell(
    *,
    pump: FakePump | None = None,
    x: FakeMotor | None = None,
    za: FakeMotor | None = None,
    zb: FakeMotor | None = None,
) -> PumpGantryCell:
    """A cell wired to fakes. Default: no pump, all three motors at 0 mm."""
    return PumpGantryCell(
        pump,
        za or FakeMotor(),
        zb or FakeMotor(),
        x or FakeMotor(),
        Config(pump_port=None),
    )


def _patch_group(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route MKSMotor's static group helpers at the fakes.

    ``move_sync`` is a staticmethod on the real class, so the cell calls it
    through ``MKSMotor``, not through the injected motors. Substituting a
    loop over ``move_to`` keeps the fakes in charge while preserving the
    call shape the cell uses.
    """
    from cell import pump_gantry_cell as mod

    def move_sync(motors, moves, barrier=None):
        for m in motors:
            for args in moves:
                m.move_to(*args)

    def home_xz(z_motors, x_motor, home_dir_z=0x00, home_dir_x=0x00):
        for m in [*z_motors, x_motor]:
            m.position = 0.0

    def stop_group_hard(motors, attempts=3):
        for m in motors:
            m.emergency_stop()
        return True

    monkeypatch.setattr(mod.MKSMotor, "move_sync", staticmethod(move_sync))
    monkeypatch.setattr(mod.MKSMotor, "home_xz", staticmethod(home_xz))
    monkeypatch.setattr(
        mod.MKSMotor, "stop_group_hard", staticmethod(stop_group_hard)
    )


# ── The pump is optional ────────────────────────────────────────────────


def test_diagnose_reports_an_absent_pump_without_faulting_the_cell() -> None:
    d = _cell().diagnose()

    assert d["pump"]["present"] is False
    assert d["pump"]["ok"] is True
    # The gantry answered, and an absent pump is not a precondition.
    assert d["stage"]["ok"] is True
    assert d["ok_to_initialize"] is True


@pytest.mark.parametrize(
    "action",
    [
        lambda c: c.initialize(),
        lambda c: c.move_valve(1),
        lambda c: c.aspirate(50.0),
        lambda c: c.dispense(0.0),
        lambda c: c.cycle(
            cycles=1, volume_uL=10.0, source_port=2, dispense_port=1
        ),
    ],
)
def test_every_pump_action_409s_when_there_is_no_pump(action) -> None:
    """A stray /v1/pump/* call must be a clean 409, not an AttributeError."""
    with pytest.raises(WrongStateError):
        action(_cell())


def test_status_reports_no_valve_when_there_is_no_pump() -> None:
    s = _cell().status()

    assert s["valve"] == "-"
    assert s["plunger_uL"] == 0.0


def test_gantry_still_moves_without_a_pump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of the pumpless mode: motion is unaffected."""
    _patch_group(monkeypatch)
    cell = _cell()

    assert cell.move_gantry(50.0, 0.0, speed_pct=10, accel_pct=0) == (
        50.0,
        0.0,
    )


def test_close_and_stop_skip_the_absent_pump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_group(monkeypatch)
    x, za, zb = FakeMotor(), FakeMotor(), FakeMotor()
    cell = _cell(x=x, za=za, zb=zb)

    cell.stop()
    cell.close()

    assert all(m.stopped for m in (x, za, zb))
    assert all(m.closed for m in (x, za, zb))


def test_a_configured_pump_is_still_required_to_be_initialized() -> None:
    """Pumpless mode must not weaken the guard on cells that have one."""
    cell = _cell(pump=FakePump())

    with pytest.raises(WrongStateError):
        cell.move_valve(1)


# ── diagnose() derives `ok`, never asserts it ───────────────────────────


@pytest.mark.parametrize("axis", ["x", "za", "zb"])
def test_diagnose_reports_a_silent_motor_as_not_ok(axis: str) -> None:
    """The bug this replaced: `stage.ok` was hardcoded True, so an
    unpowered gantry passed the last check before an operator commands it."""
    motors = {"x": FakeMotor(), "za": FakeMotor(), "zb": FakeMotor()}
    motors[axis].silent = True

    d = _cell(**motors).diagnose()

    assert d["stage"]["ok"] is False
    assert d["ok_to_initialize"] is False


# ── A move is confirmed by readback ─────────────────────────────────────


def test_move_raises_when_the_axis_did_not_arrive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dropped F5 leaves the motor at 0 while the command 'succeeded'."""
    _patch_group(monkeypatch)
    cell = _cell(x=FakeMotor(lands=False))

    with pytest.raises(DeviceFaultError, match="did not reach its target"):
        cell.move_gantry(50.0, 0.0, speed_pct=10, accel_pct=0)


def test_move_raises_when_the_position_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unread axis is unknown, not arrived — the rail must not be
    reported at a position nobody measured."""
    _patch_group(monkeypatch)
    za = FakeMotor()
    za.silent = True
    cell = _cell(za=za)

    with pytest.raises(TransportError, match="position is unknown"):
        cell.move_gantry(0.0, 50.0, speed_pct=10, accel_pct=0)


def test_move_raises_when_the_paired_z_motors_desync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The damage case: one Z lands, its partner does not. move_sync's
    interlock only fires on a CAN fault, so it never sees this."""
    _patch_group(monkeypatch)
    cell = _cell(za=FakeMotor(), zb=FakeMotor(lands=False))

    with pytest.raises(DeviceFaultError) as exc:
        cell.move_gantry(0.0, 50.0, speed_pct=10, accel_pct=0)
    # Whichever check trips first, the operator must be told to stop.
    assert "did not reach its target" in str(exc.value) or "racking" in str(
        exc.value
    )


def test_move_accepts_a_residual_inside_the_tolerance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A servo settles near, not exactly on, the target."""
    _patch_group(monkeypatch)
    residual = ARRIVAL_TOLERANCE_MM / 2

    class Undershoots(FakeMotor):
        def move_to(self, mm, speed_pct=20, accel_pct=10):
            self.position = mm - residual

    cell = _cell(x=Undershoots())
    x_mm, _ = cell.move_gantry(50.0, 0.0, speed_pct=10, accel_pct=0)

    assert x_mm == pytest.approx(50.0 - residual)


def test_home_raises_when_the_gantry_did_not_reach_the_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MKSMotor.home() only *prints* 'Homing FAILED', so the readback is
    what separates a homed gantry from one that never found its switch."""
    _patch_group(monkeypatch)
    from cell import pump_gantry_cell as mod

    monkeypatch.setattr(
        mod.MKSMotor,
        "home_xz",
        staticmethod(lambda z, x, dz=0x00, dx=0x00: None),
    )
    cell = _cell(x=FakeMotor(position=120.0))

    with pytest.raises(DeviceFaultError, match="did not reach its target"):
        cell.home_gantry()


# ── status() is a probe: it reports gaps, it does not raise ─────────────


def test_status_reports_an_unread_axis_as_null_not_zero() -> None:
    """0.0 is the one value that would make a `verify_home` assert pass."""
    x = FakeMotor()
    x.silent = True

    s = _cell(x=x).status()

    assert s["stage_x_mm"] is None
    assert s["error"] is not None


def test_status_flags_a_racking_gantry() -> None:
    spread = Z_DESYNC_LIMIT_MM * 2
    s = _cell(za=FakeMotor(0.0), zb=FakeMotor(spread)).status()

    assert "racking" in s["error"]


def test_status_reports_live_positions_not_the_commanded_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """status() must be able to contradict the last move."""
    _patch_group(monkeypatch)
    x = FakeMotor()
    cell = _cell(x=x)
    cell.move_gantry(50.0, 0.0, speed_pct=10, accel_pct=0)

    x.position = 12.5  # the axis drifted / was pushed

    assert cell.status()["stage_x_mm"] == pytest.approx(12.5)


def test_a_z_only_move_does_not_re_command_x(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cached X is a measured value now, so `==` would never match and
    every Z-only move would pointlessly retract Z and re-drive X."""
    _patch_group(monkeypatch)

    class Undershoots(FakeMotor):
        """Settles just short, the way a real servo does."""

        def move_to(self, mm, speed_pct=20, accel_pct=10):
            self.position = mm - ARRIVAL_TOLERANCE_MM / 2
            self.moves += 1

    x = Undershoots()
    x.moves = 0
    cell = _cell(x=x)
    cell.move_gantry(0.0, 20.0, speed_pct=10, accel_pct=0)
    cell.move_gantry(0.0, 40.0, speed_pct=10, accel_pct=0)

    assert x.moves == 0


# ── EMI-robust pump link: drops are absorbed, absolutes re-issued ───────


class DroppingPump(FakePump):
    """A pump whose link dies for the first ``drops`` interactions.

    OSError(EIO) is what a dead CH340 fd raises through pyserial; every
    method notes its call so tests can assert the re-issue actually
    happened rather than the failure being swallowed.
    """

    def __init__(self, drops: int = 0) -> None:
        super().__init__()
        self.drops = drops
        self.calls: list[str] = []
        self.valve = "?"
        self.contained_uL = 0.0

    def _link(self, name: str) -> None:
        self.calls.append(name)
        if self.drops > 0:
            self.drops -= 1
            raise OSError(5, "Input/output error")

    def initialize(self, *, force: int = 2, ccw: bool = False) -> None:
        self._link("initialize")
        self.initialized = True
        self.valve = "1"

    def move_valve_to_port(self, port: int) -> None:
        self._link(f"valve:{port}")
        self.valve = str(port)

    def aspirate_uL(self, target_uL: float) -> None:
        self._link("aspirate")
        self.contained_uL = float(target_uL)

    def dispense_uL(self, target_uL: float = 0) -> None:
        self._link("dispense")
        self.contained_uL = float(target_uL)

    def query_valve_position(self) -> str:
        self._link("query")
        return self.valve

    def query_plunger_position(self) -> int:
        # Half-steps for the 125 uL syringe in NORMAL mode, like the
        # real ? readback the settle probes compare against.
        self._link("query_plunger")
        return round(self.contained_uL / 125 * 12000)


def _pump_cell(
    pump: DroppingPump, monkeypatch: pytest.MonkeyPatch
) -> PumpGantryCell:
    """A cell whose reconnect path reopens onto the same fake pump."""
    from cell import pump_gantry_cell as mod

    monkeypatch.setattr(mod, "PUMP_REOPEN_DELAY_S", 0.0)
    monkeypatch.setattr(
        mod.SyringePumpController,
        "open",
        staticmethod(lambda cfg: pump),
    )
    return PumpGantryCell(pump, FakeMotor(), FakeMotor(), FakeMotor(), Config())


def test_pump_command_survives_a_link_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One EIO mid-command: reopen, re-issue, converge — no error out."""
    pump = DroppingPump(drops=1)
    cell = _pump_cell(pump, monkeypatch)

    cell.initialize()
    assert cell.aspirate(50.0) == 50.0

    assert pump.contained_uL == 50.0
    # The dropped attempt is present AND its re-issue after reconnect.
    assert pump.calls.count("aspirate") <= 2


def test_cycle_resumes_at_the_interrupted_leg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A drop mid-batch must not fail the whole batch (each leg guarded)."""
    pump = DroppingPump()
    cell = _pump_cell(pump, monkeypatch)
    cell.initialize()
    pump.drops = 1  # the next leg's first attempt dies

    result = cell.cycle(
        cycles=2, volume_uL=10.0, source_port=1, dispense_port=2
    )

    assert result["cycles_done"] == 2
    assert result["final_valve"] == "2"
    assert pump.contained_uL == 0.0


def test_pump_gives_up_when_the_link_never_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A link that stays dead is a 503 TransportError, never a hang."""
    pump = DroppingPump(drops=10_000)
    cell = _pump_cell(pump, monkeypatch)

    with pytest.raises(TransportError, match="pump"):
        cell.initialize()


def test_a_dead_link_is_not_reported_as_a_missing_pump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a failed reconnect the pump is unreachable, not absent —
    the next call must retry the link (503), not answer 409."""
    pump = DroppingPump(drops=10_000)
    cell = _pump_cell(pump, monkeypatch)

    with pytest.raises(TransportError):
        cell.initialize()
    with pytest.raises(TransportError):  # NOT WrongStateError
        cell.initialize()


def test_status_reports_a_dead_pump_link_in_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """status() is a probe: a dead pump link lands in `error`, and the
    gantry half of the answer stays usable."""
    pump = DroppingPump(drops=10_000)
    cell = _pump_cell(pump, monkeypatch)

    s = cell.status()

    assert s["error"] is not None and "pump" in s["error"]
    assert s["stage_x_mm"] == 0.0  # the gantry still answered


def test_reissue_waits_out_a_busy_pump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Error 15 after a reconnect means the interrupted first issue is
    still executing in the MCU — wait, don't fail (bench 2026-07-29)."""
    from cell import pump_gantry_cell as mod

    monkeypatch.setattr(mod, "PUMP_BUSY_DELAY_S", 0.0)

    class BusyThenDone(DroppingPump):
        def __init__(self) -> None:
            super().__init__()
            self.busy_rejections = 2

        def initialize(self, *, force: int = 2, ccw: bool = False) -> None:
            self.calls.append("initialize")
            if self.busy_rejections > 0:
                self.busy_rejections -= 1
                raise mod.SyringePumpController.CommandOverflowError(
                    mod.SyringePumpController.ErrorCode.COMMAND_OVERFLOW,
                    "Z2",
                    b"",
                )
            self.initialized = True
            self.valve = "1"

    pump = BusyThenDone()
    cell = _pump_cell(pump, monkeypatch)

    result = cell.initialize()

    assert result["plunger_uL"] == 0.0
    assert pump.initialized is True
    assert pump.calls.count("initialize") == 3  # 2 rejected + 1 accepted


def test_a_command_that_finished_in_the_mcu_is_not_reissued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The link can die AFTER the MCU executed the command; the settle
    probe must detect the end state and skip the re-issue."""

    class DiesAfterExecuting(DroppingPump):
        def move_valve_to_port(self, port: int) -> None:
            self.calls.append(f"valve:{port}")
            self.valve = str(port)  # executed...
            if self.drops > 0:
                self.drops -= 1
                raise OSError(5, "Input/output error")  # ...then link died

    pump = DiesAfterExecuting()
    cell = _pump_cell(pump, monkeypatch)
    cell.initialize()
    pump.drops = 1

    assert cell.move_valve(2) == "2"
    # One issue only: the settle probe saw valve=2 after the reconnect.
    assert pump.calls.count("valve:2") == 1
