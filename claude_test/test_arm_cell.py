"""T0 unit tests for :class:`cell.arm_cell.ArmCell`.

No hardware: ``ArmCell.__init__`` takes the fairino RPC handle as an
argument, so the fake below stands in for the controller. Only ``open()``
touches the network, and these never call it.

Covers the jog envelope and the latched-fault gate
(docs/SPEC_ARM_REPLAY_CELL.md §7 T1, still current — ``arm/jog_joint``
outlived the replay path), and the Lua job-program action set
(docs/SPEC_ARM_LUA_PROGRAM.md §7).

Two things the fake reproduces on purpose, because a fake that did not is
what let both bugs reach the bench (LearnedPatterns #46):

* ``ProgramRun`` accepts ~160 ms before ``GetProgramState`` says
  "running", so a start confirmation is required and testable.
* ``GetCurrentLine`` resets to 0 as the program ends, so ``last_line``
  has to be a high-water mark.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cell.arm_cell import (  # noqa: E402
    MAX_JOG_DEG,
    MAX_JOG_SPEED_PCT,
    PROGRAM_PAUSED,
    PROGRAM_RUNNING,
    PROGRAM_STOPPED,
    ArmCell,
    ArmConfig,
)
from cell.cell_protocol import (  # noqa: E402
    CellTimeoutError,
    DeviceFaultError,
    InvalidArgError,
    TransportError,
    WrongStateError,
)

#: The pose the fake controller reports unless a test moves it.
START_POSE_DEG = [10.0, -90.0, 90.0, -90.0, -90.0, 0.0]


class FakeProxy:
    """The ``.robot`` XMLRPC proxy on port 20003.

    Joint reads go through here, not the SDK wrapper: the wrapper reads
    the port-20004 state struct, which cell6's controller does not serve
    (measured 2026-08-11). The proxy answers ``[error, j1 … j6]``.
    """

    def __init__(self, owner: FakeRPC) -> None:
        self._owner = owner

    def GetActualJointPosDegree(self, flag: int = 1):  # noqa: N802
        self._owner.calls.append("read")
        return [self._owner.read_error, *self._owner.joints]

    def GetRobotErrorCode(self):  # noqa: N802
        self._owner.calls.append("error_code")
        return [0, *self._owner.fault]

    def ResetAllError(self):  # noqa: N802
        self._owner.calls.append("reset")
        self._owner.fault = [0, 0]
        return 0

    def RobotEnable(self, state):  # noqa: N802
        self._owner.calls.append(("enable", state))
        return 0

    def Mode(self, state):  # noqa: N802
        self._owner.calls.append(("mode", state))
        return 0

    def GetActualTCPNum(self, flag=1):  # noqa: N802
        # Active tool frame. cell6 answers 1, cell7 answers 0 — the whole
        # of "SDK error 154" was hard-coding this to 0.
        self._owner.calls.append("tcp_num")
        return [0, self._owner.tool_num]

    def GetForwardKin(self, joint_pos):  # noqa: N802
        # The controller's kinematics; the cell only forwards the result
        # into MoveJ's desc_pos argument, so a stand-in pose will do.
        return [0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

    def MoveJ(  # noqa: N802
        self,
        joint_pos,
        desc_pos,
        tool,  # recorded so a test can assert the ACTIVE frame was sent
        user,
        vel,
        acc,
        ovl,
        exaxis_pos,
        blendT,  # noqa: N803
        offset_flag,
        offset_pos,
    ):
        self._owner.calls.append(("movej", list(joint_pos), vel, tool))
        self._owner.joints = list(joint_pos)
        return 0

    def StopMotion(self):  # noqa: N802
        self._owner.calls.append("stop_motion")
        return self._owner.stop_error

    # ── Lua job programs ────────────────────────────────────────────────
    def ProgramLoad(self, path):  # noqa: N802
        self._owner.calls.append(("program_load", path))
        if self._owner.load_error == 0:
            self._owner.loaded_name = path
        return self._owner.load_error

    def GetLoadedProgram(self):  # noqa: N802
        self._owner.calls.append("loaded_program")
        return [0, self._owner.loaded_name]

    def ProgramRun(self):  # noqa: N802
        """Accepts, then enters the running state a beat later.

        Measured on cell6 2026-08-11: this returned 0 at t+0.001 s with
        GetProgramState still answering 1, and the state flipped to 2 at
        t+0.160 s. The fake reproduces the lag because a fake that
        flipped instantly is what let the premature-completion bug reach
        the bench.
        """
        self._owner.calls.append("program_run")
        if self._owner.run_error != 0:
            return self._owner.run_error
        if self._owner.never_starts:
            return 0
        self._owner.observed_running = False
        if self._owner.polls_before_running > 0:
            self._owner.pending_start = True
        else:
            self._owner.program_state = PROGRAM_RUNNING
            self._owner.started_by_run = True
        return 0

    def ProgramStop(self):  # noqa: N802
        self._owner.calls.append("program_stop")
        self._owner.program_state = PROGRAM_STOPPED
        self._owner.started_by_run = False
        return self._owner.program_stop_error

    def GetProgramState(self):  # noqa: N802
        """A program this fake started finishes on its own.

        Modelled as "the controller was still running for N more polls,
        then it stopped", rather than a scripted queue of states — a
        queue would also be drained by ``start_program``'s own
        precondition check, which reads this before the program exists.
        A test that wants a program that never ends sets
        ``program_hangs``; one that sets ``program_state`` by hand is
        describing a controller busy with someone else's program, so
        that is left alone.
        """
        self._owner.calls.append("program_state")
        if self._owner.pending_start:
            # The controller has accepted the run but not entered it yet.
            self._owner.polls_before_running -= 1
            if self._owner.polls_before_running <= 0:
                self._owner.pending_start = False
                self._owner.program_state = PROGRAM_RUNNING
                self._owner.started_by_run = True
            return [0, self._owner.program_state]
        running = self._owner.program_state == PROGRAM_RUNNING
        if running and self._owner.started_by_run:
            if not self._owner.observed_running:
                # The real controller does not finish between
                # `start_program`'s confirmation poll and the first
                # `await_program` poll; a fake that did would make the
                # start confirmation impossible to satisfy.
                self._owner.observed_running = True
            elif self._owner.program_run_polls > 0:
                self._owner.program_run_polls -= 1
            elif not self._owner.program_hangs:
                self._owner.program_state = PROGRAM_STOPPED
                self._owner.started_by_run = False
                self._owner.observed_running = False
        return [0, self._owner.program_state]

    def GetCurrentLine(self):  # noqa: N802
        """Line numbers, ending in the controller's reset to 0.

        Measured on cell6 2026-08-11: 4, 9, 11, 12, 14, 15, 18, then
        **0** as the program ended. ``line_script`` replays a shape like
        that so the high-water-mark rule has something to fail against.
        """
        self._owner.calls.append("current_line")
        if self._owner.line_script:
            self._owner.current_line = self._owner.line_script.pop(0)
        else:
            self._owner.current_line += 1
        return [0, self._owner.current_line]


class FakeRPC:
    """Stand-in for ``fairino.Robot.RPC`` — only what the cell calls.

    ``calls`` is the ordered log the stop-sequence test asserts on.
    ``close_raises`` reproduces the real ``CloseRPC()`` AttributeError.
    """

    def __init__(
        self,
        joints: list[float] | None = None,
        *,
        close_raises: bool = False,
    ) -> None:
        self.joints = list(joints or [0.0] * 6)
        self.calls: list[object] = []
        self.stop_error = 0
        self.read_error = 0
        self.closed = False
        self.close_raises = close_raises
        #: Latched controller fault as [main, sub]. The real arm sat at
        #: [1, 1] on 2026-08-11 and refused MoveJ with SDK error 154.
        self.fault = [0, 0]
        #: Active tool frame number. Not always 0: cell6's arm runs tool
        #: 1, and telling MoveJ otherwise is rejected (LearnedPatterns #44).
        self.tool_num = 0
        # ── Lua job-program knobs ───────────────────────────────────────
        #: What GetProgramState answers. Set it by hand to describe a
        #: controller already busy with someone else's program.
        self.program_state = PROGRAM_STOPPED
        #: True once this fake's own ProgramRun started something, which
        #: is what lets GetProgramState finish it again.
        self.started_by_run = False
        #: Polls that still report "running" before the program ends.
        self.program_run_polls = 0
        #: True makes the program never end, for the timeout tests.
        self.program_hangs = False
        #: Polls that still report "stopped" AFTER ProgramRun returned 0,
        #: modelling the 160 ms the real controller takes to enter the
        #: running state.
        self.polls_before_running = 0
        self.pending_start = False
        #: Set once a poll has reported the program running, so the
        #: start confirmation is satisfiable.
        self.observed_running = False
        #: True = ProgramRun answers 0 and nothing ever runs.
        self.never_starts = False
        #: Line numbers to hand out in order; see GetCurrentLine.
        self.line_script: list[int] = []
        self.loaded_name = ""
        self.load_error = 0
        self.run_error = 0
        self.program_stop_error = 0
        self.current_line = 0
        self.robot = FakeProxy(self)

    def MoveJ(self, joint_pos, tool, user, vel=20.0, **kwargs):  # noqa: N802
        self.calls.append(("movej", list(joint_pos), vel))
        self.joints = list(joint_pos)
        return 0

    def StopMotion(self):  # noqa: N802
        self.calls.append("stop_motion")
        return self.stop_error

    def CloseRPC(self):  # noqa: N802
        self.calls.append("close")
        self.closed = True
        if self.close_raises:
            # What the vendored SDK actually does: CloseRPC() reads
            # self.thread, which __init__ never assigns.
            raise AttributeError("'RPC' object has no attribute 'thread'")


def make_cell(
    rpc: FakeRPC | None = None,
    **overrides,
) -> tuple[ArmCell, FakeRPC, None]:
    """Build a cell over a fake controller, at a known pose.

    Returns a 3-tuple whose third element is always None: the callers
    were written against a version that also injected a subprocess
    runner, and unpacking is cheaper to keep than to rewrite everywhere.
    """
    rpc = rpc or FakeRPC(list(START_POSE_DEG))
    cfg = ArmConfig(
        robot_id="fr5_a",
        ip_address="192.168.0.58",
        gripper_enabled=True,
        **overrides,
    )
    # `reconnect` is what the cell calls to rebuild a dropped session.
    # Left at its default it would open a real socket to the bench arm —
    # a unit test must never dial 192.168.0.58.
    cell = ArmCell(rpc, cfg, reconnect=lambda: rpc)
    return cell, rpc, None


def test_movej_declares_the_controllers_active_tool_frame():
    """LearnedPatterns #44: hard-coding tool=0 IS "SDK error 154".

    GetForwardKin answers in the active tool frame, so MoveJ must be told
    that same frame or the joint target and the Cartesian desc_pos
    describe points in different frames and the pair is rejected. cell6
    runs tool 1, cell7 runs tool 0 — identical code, opposite outcomes.
    """
    for tool_num in (0, 1, 2):
        rpc = FakeRPC(list(START_POSE_DEG))
        rpc.tool_num = tool_num
        cell, _, _ = make_cell(rpc=rpc)
        cell.jog_joint(1, 10.0)
        movej = [
            c for c in rpc.calls if isinstance(c, tuple) and c[0] == "movej"
        ]
        assert len(movej) == 1
        assert movej[0][3] == tool_num, (
            f"MoveJ was told tool {movej[0][3]} while the controller's "
            f"active tool is {tool_num}"
        )


# ── The jog envelope: a bound with no test is not a bound ────────────────


def test_jog_accepts_the_cap_exactly():
    """30 deg is the ceiling, so 30 deg itself must go through."""
    rpc = FakeRPC(list(START_POSE_DEG))
    cell, _, _ = make_cell(rpc=rpc)
    out = cell.jog_joint(1, MAX_JOG_DEG)
    assert out["achieved_delta_deg"] == pytest.approx(MAX_JOG_DEG)


@pytest.mark.parametrize(
    "delta", [MAX_JOG_DEG + 0.1, -MAX_JOG_DEG - 0.1, 90.0, -180.0]
)
def test_jog_over_the_cap_is_refused(delta):
    rpc = FakeRPC(list(START_POSE_DEG))
    cell, _, _ = make_cell(rpc=rpc)
    with pytest.raises(InvalidArgError):
        cell.jog_joint(1, delta)
    # Nothing was commanded.
    assert [
        c for c in rpc.calls if isinstance(c, tuple) and c[0] == "movej"
    ] == []


def test_jog_speed_is_capped_not_rejected():
    """A too-fast request is clamped, because the safe answer to "go
    faster than allowed" on a commissioning move is "go at the limit",
    not a 400 that tempts someone to raise the limit."""
    rpc = FakeRPC(list(START_POSE_DEG))
    cell, _, _ = make_cell(rpc=rpc)
    cell.jog_joint(1, 10.0, speed_pct=999.0)
    movej = [c for c in rpc.calls if isinstance(c, tuple) and c[0] == "movej"]
    assert movej[0][2] == pytest.approx(MAX_JOG_SPEED_PCT)


# ── Latched-fault gate (measured: MoveJ answers SDK error 154) ───────────


def test_jog_is_refused_while_a_fault_is_latched():
    rpc = FakeRPC(list(START_POSE_DEG))
    rpc.fault = [1, 1]  # what the bench arm actually reported
    cell, _, _ = make_cell(rpc=rpc)
    with pytest.raises(WrongStateError) as caught:
        cell.jog_joint(1, 10.0)
    assert "latched fault" in str(caught.value)
    # Nothing was commanded.
    assert [c for c in rpc.calls if c and c[0] == "movej"] == []


def test_prepare_arm_clears_and_reports_what_was_wrong():
    rpc = FakeRPC(list(START_POSE_DEG))
    rpc.fault = [1, 1]
    cell, _, _ = make_cell(rpc=rpc)
    out = cell.prepare_arm()
    # The point of the response: the fault is recorded, not just erased.
    assert out["error_before"] == [1, 1]
    assert out["error_after"] == [0, 0]
    assert out["fault_cleared"] is True
    assert out["error_settled"] == [0, 0]
    names = [c if isinstance(c, str) else c[0] for c in rpc.calls]
    assert names.index("reset") < names.index("enable")
    assert names.index("enable") < names.index("mode")
    assert ("mode", 0) in rpc.calls  # 0 = automatic, not manual
    assert ("enable", 1) in rpc.calls


def test_jog_works_once_prepared():
    rpc = FakeRPC(list(START_POSE_DEG))
    rpc.fault = [1, 1]
    cell, _, _ = make_cell(rpc=rpc)
    cell.prepare_arm()
    out = cell.jog_joint(1, 10.0)
    assert out["achieved_delta_deg"] == pytest.approx(10.0)
    assert out["max_other_axis_delta_deg"] == pytest.approx(0.0)


def test_diagnose_reports_a_faulted_arm_as_not_ready():
    """A health check that cannot fail is not a check."""
    rpc = FakeRPC(list(START_POSE_DEG))
    rpc.fault = [1, 1]
    cell, _, _ = make_cell(rpc=rpc)
    report = cell.diagnose()
    assert report["arm"]["ok"] is True  # reachable
    assert report["arm"]["ready"] is False  # but will not move
    assert report["arm"]["fault"] == [1, 1]


# ── T0-1 config parsing ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "port", "ip", "robot_id"),
    [
        ("server/nuc2/cell6.toml.example", 17064, "192.168.0.58", "fr5_a"),
        ("server/nuc1/cell7.toml.example", 17066, "192.168.0.59", "fr5_b"),
    ],
)
def test_example_configs_parse(path, port, ip, robot_id):
    raw = tomllib.loads((REPO_ROOT / path).read_text(encoding="utf-8"))
    assert int(raw["server"]["port"]) == port
    cfg = ArmConfig.from_toml(raw["arm"])
    assert cfg.ip_address == ip
    assert cfg.robot_id == robot_id
    assert isinstance(cfg.gripper_enabled, bool)
    assert isinstance(cfg.jog_speed_pct, float)
    # The Lua job-program keys, same coercion rule: TOML hands back
    # `300` as an int for a float field.
    assert isinstance(cfg.program_dir, str)
    assert not cfg.program_dir.endswith("/")
    assert isinstance(cfg.allowed_programs, tuple)
    assert all(isinstance(p, str) for p in cfg.allowed_programs)
    assert isinstance(cfg.max_program_s, float)
    assert isinstance(cfg.program_poll_s, float)


def test_cell6_and_cell7_have_distinct_addresses():
    """Safety rule §8.3: the two arms must not share an IP or a port."""
    six = tomllib.loads(
        (REPO_ROOT / "server/nuc2/cell6.toml.example").read_text("utf-8")
    )
    seven = tomllib.loads(
        (REPO_ROOT / "server/nuc1/cell7.toml.example").read_text("utf-8")
    )
    assert six["arm"]["ip_address"] != seven["arm"]["ip_address"]
    assert six["server"]["port"] != seven["server"]["port"]
    assert six["arm"]["robot_id"] != seven["arm"]["robot_id"]


# ── T0-9 stop() ─────────────────────────────────────────────────────────


def test_stop_when_idle_still_stops_the_sdk():
    """An e-stop with nothing running still reaches the controller."""
    rpc = FakeRPC()
    cell, _, _ = make_cell(rpc=rpc)
    result = cell.stop()
    assert result["sdk"] == "stopped"
    assert result["program"] == "idle"
    assert "program_stop" not in rpc.calls


def test_stop_records_an_sdk_failure_without_raising():
    rpc = FakeRPC()
    rpc.stop_error = 14  # RobotMotionError
    cell, _, _ = make_cell(rpc=rpc)
    result = cell.stop()
    assert result["sdk"] != "stopped"
    assert "14" in result["sdk"]


def test_status_when_idle_reads_the_encoder():
    rpc = FakeRPC([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    cell, _, _ = make_cell(rpc=rpc)
    state = cell.status()
    assert "read" in rpc.calls
    assert state["busy"] is False
    assert state["joints_deg"] == pytest.approx([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])


def test_unreadable_encoder_is_a_transport_error():
    rpc = FakeRPC()
    rpc.read_error = 14
    cell, _, _ = make_cell(rpc=rpc)
    with pytest.raises(TransportError):
        cell.status()


# ── diagnose + the other action sets ────────────────────────────────────


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.tare(),
        lambda c: c.calibrate(),
        lambda c: c.read_weight(),
        lambda c: c.set_ambient("stable"),
        lambda c: c.initialize(),
        lambda c: c.move_valve(1),
        lambda c: c.aspirate(10.0),
        lambda c: c.dispense(),
        lambda c: c.cycle(
            cycles=1, volume_uL=1.0, source_port=1, dispense_port=2
        ),
        lambda c: c.home_gantry(),
        lambda c: c.move_gantry(0.0, 0.0, speed_pct=10, accel_pct=0),
        lambda c: c.home_linear(),
        lambda c: c.move_linear(0.0),
        lambda c: c.home_zstage(),
        lambda c: c.move_zstage(0.0, speed_pct=10, accel_pct=0),
        lambda c: c.read_hotplate(),
        lambda c: c.set_hotplate_temperature(30.0),
        lambda c: c.set_hotplate_heater(enabled=True),
        lambda c: c.set_hotplate_speed(100.0),
        lambda c: c.set_hotplate_stirrer(enabled=True),
        lambda c: c.read_lamp(),
        lambda c: c.set_lamp(enabled=True),
    ],
)
def test_other_action_sets_are_409(call):
    cell, _, _ = make_cell()
    with pytest.raises(WrongStateError):
        call(cell)


# ── Lua job programs (docs/SPEC_ARM_LUA_PROGRAM.md §7 L0-2 … L0-11) ─────
#
# The second motion path. Everything here drives the raw XMLRPC proxy,
# because the SDK's own Program* wrappers cannot be used at all: they
# spin on the latched `reconnect_flag` (LearnedPatterns #41) and
# `GetProgramState()` is a commented-out stub returning the port-20004
# struct cell6 never serves (#40).


def make_program_cell(rpc=None, **overrides):
    """A cell whose program poll costs nothing, for the wait loop."""
    overrides.setdefault("program_poll_s", 0.0)
    return make_cell(rpc=rpc, **overrides)


@pytest.mark.parametrize(
    "name",
    [
        "test1.txt",  # wrong extension
        "test1",  # no extension
        "",  # empty
        "sub/test1.lua",  # a path, not a name
        "..\\test1.lua",  # a path, the other way
        "../../fruser/test1.lua",  # traversal out of program_dir
    ],
)
def test_program_name_must_be_a_bare_lua_file(name):
    """L0-2: ProgramLoad takes a path, so the request must not."""
    cell, rpc, _ = make_program_cell()
    with pytest.raises(InvalidArgError):
        cell.start_program(name)
    assert not [c for c in rpc.calls if c == "program_run"]


def test_program_outside_the_allow_list_is_refused():
    """L0-3: the same gate allowed_repo_prefixes gives a dataset."""
    cell, _, _ = make_program_cell(allowed_programs=("ok.lua",))
    with pytest.raises(InvalidArgError):
        cell.start_program("other.lua")


def test_empty_allow_list_permits_any_lua_file():
    cell, rpc, _ = make_program_cell()
    out = cell.start_program("anything.lua")
    assert out["path"] == "/fruser/anything.lua"


def test_program_runs_and_reports_the_encoder():
    """L0-11: a 200 means the controller stopped AND the arm was read."""
    cell, rpc, _ = make_program_cell()
    started = cell.start_program("test1.lua")
    assert started["name"] == "test1.lua"
    assert started["timeout_s"] == cell.config.max_program_s

    out = cell.await_program()
    assert out["completed"] is True
    assert out["name"] == "test1.lua"
    assert out["last_line"] is not None
    # Not the cached vector: the encoder was re-read after the program.
    assert "read" in rpc.calls
    assert out["joints_deg"] == list(START_POSE_DEG)
    assert cell.status()["last_program"]["outcome"] == "completed"


def test_mode_auto_precedes_program_run():
    """L0-7: every vendor example puts Mode(0) before ProgramRun."""
    cell, rpc, _ = make_program_cell()
    cell.start_program("test1.lua")
    modes = [
        i
        for i, c in enumerate(rpc.calls)
        if isinstance(c, tuple) and c[0] == "mode" and c[1] == 0
    ]
    loads = [
        i
        for i, c in enumerate(rpc.calls)
        if isinstance(c, tuple) and c[0] == "program_load"
    ]
    runs = [i for i, c in enumerate(rpc.calls) if c == "program_run"]
    assert modes and loads and runs
    assert modes[0] < loads[0] < runs[0]


def test_program_load_is_given_the_configured_directory():
    cell, rpc, _ = make_program_cell(program_dir="/fruser")
    cell.start_program("test1.lua")
    loads = [
        c for c in rpc.calls if isinstance(c, tuple) and c[0] == "program_load"
    ]
    assert loads[0][1] == "/fruser/test1.lua"


def test_program_load_failure_is_a_device_fault():
    cell, rpc, _ = make_program_cell()
    rpc.load_error = 14
    with pytest.raises(DeviceFaultError):
        cell.start_program("test1.lua")
    assert "program_run" not in rpc.calls


def test_loading_a_different_program_than_asked_for_is_refused():
    """L0-6: ProgramLoad answering 0 is not proof of what is loaded."""
    cell, rpc, _ = make_program_cell()

    def load_something_else(path):
        rpc.calls.append(("program_load", path))
        rpc.loaded_name = "/fruser/somethingelse.lua"
        return 0

    rpc.robot.ProgramLoad = load_something_else
    with pytest.raises(DeviceFaultError):
        cell.start_program("test1.lua")
    assert "program_run" not in rpc.calls


def test_an_unreadable_loaded_program_does_not_block_the_run():
    """A getter that will not answer is weaker evidence, not contrary."""
    cell, rpc, _ = make_program_cell()

    def refuse():
        raise OSError("controller will not say")

    rpc.robot.GetLoadedProgram = refuse
    cell.start_program("test1.lua")
    assert "program_run" in rpc.calls


def test_program_start_refused_while_the_controller_is_busy():
    """L0-5: the controller's own state is checked, not just ours."""
    cell, rpc, _ = make_program_cell()
    rpc.program_state = PROGRAM_RUNNING
    with pytest.raises(WrongStateError):
        cell.start_program("test1.lua")
    rpc.program_state = PROGRAM_PAUSED
    with pytest.raises(WrongStateError):
        cell.start_program("test1.lua")


def test_program_start_refused_while_a_fault_is_latched():
    cell, rpc, _ = make_program_cell()
    rpc.fault = [1, 1]
    with pytest.raises(WrongStateError):
        cell.start_program("test1.lua")
    assert "program_run" not in rpc.calls


def test_a_second_program_while_one_runs_is_refused():
    """One job program at a time; the controller has one set of axes."""
    cell, rpc, _ = make_program_cell()
    cell.start_program("test1.lua")
    with pytest.raises(WrongStateError):
        cell.start_program("test1.lua")
    cell.stop()
    # …and it is startable again once the first one is stopped.
    cell.start_program("test1.lua")
    assert cell.diagnose()["program"]["running"] is True


def test_await_without_start_is_409():
    cell, _, _ = make_program_cell()
    with pytest.raises(WrongStateError):
        cell.await_program()


def test_a_program_that_overruns_is_stopped_then_reported():
    """L0-8: the timeout kills it before raising, like the replay one."""
    cell, rpc, _ = make_program_cell(max_program_s=0.0)
    cell.start_program("test1.lua")
    rpc.program_hangs = True
    with pytest.raises(CellTimeoutError):
        cell.await_program()
    assert "program_stop" in rpc.calls
    assert cell.status()["last_program"]["outcome"] == "timeout"


def test_a_paused_program_is_not_treated_as_finished():
    """Paused is state 3, not state 1 — waiting it out is correct."""
    cell, rpc, _ = make_program_cell(max_program_s=0.0)
    cell.start_program("test1.lua")
    rpc.program_state = PROGRAM_PAUSED
    with pytest.raises(CellTimeoutError):
        cell.await_program()


def test_a_fault_latched_by_the_end_fails_the_run():
    """L0-9: the controller stopped, but not because it succeeded."""
    cell, rpc, _ = make_program_cell()
    cell.start_program("test1.lua")
    rpc.fault = [14, 0]
    with pytest.raises(DeviceFaultError):
        cell.await_program()
    assert cell.status()["last_program"]["outcome"] == "fault 14/0"


def test_stop_halts_the_program_before_the_controller():
    """L0-10: kill the thing issuing motion, then StopMotion.

    Reversed, the next Lua line restarts what StopMotion just stopped —
    the same ordering argument as the ServoJ stream.
    """
    cell, rpc, _ = make_program_cell()
    cell.start_program("test1.lua")
    result = cell.stop()
    assert result["program"] == "stopped"
    assert result["sdk"] == "stopped"
    assert rpc.calls.index("program_stop") < rpc.calls.index("stop_motion")
    assert cell.status()["last_program"]["outcome"] == "stopped"


def test_stop_reports_a_failed_program_stop_without_raising():
    cell, rpc, _ = make_program_cell()
    cell.start_program("test1.lua")
    rpc.program_stop_error = 14
    result = cell.stop()
    assert "14" in result["program"]
    # The e-stop still went through to the controller.
    assert result["sdk"] == "stopped"


def test_stop_is_idle_when_no_program_ran():
    cell, _, _ = make_program_cell()
    assert cell.stop()["program"] == "idle"


def test_diagnose_reports_the_program_block():
    cell, rpc, _ = make_program_cell(allowed_programs=("test1.lua",))
    report = cell.diagnose()
    assert report["program"]["running"] is False
    assert report["program"]["dir"] == "/fruser"
    assert report["program"]["allowed"] == ["test1.lua"]

    cell.start_program("test1.lua")
    running = cell.diagnose()
    assert running["program"]["running"] is True
    assert running["program"]["name"] == "test1.lua"
    # Same rule as a replay in flight: do not probe, report.
    assert running["arm"]["detail"] == "job program in flight; not probed"


# ── The two bugs the bench found on 2026-08-11 ──────────────────────────
#
# Both were reported as `completed: true` by a run that had not happened
# yet. They are the reason `arm/program` has a start-confirmation step at
# all, so they get regression tests rather than a comment.


def test_start_waits_for_the_controller_to_actually_enter_running():
    """ProgramRun answers on acceptance; the state flips 160 ms later.

    Measured on cell6 2026-08-11. Polling before that beat saw "stopped",
    concluded the program had finished, and answered 200 in 2.8 ms — then
    the arm swung joint 1 through 91.3 deg. `start_program` must not
    return until the controller says it is running.
    """
    cell, rpc, _ = make_program_cell()
    # Three polls' worth of "not yet", the way the real controller lags.
    rpc.polls_before_running = 3
    cell.start_program("Test1.lua")
    assert rpc.program_state == PROGRAM_RUNNING
    # And the wait really happened rather than being skipped.
    assert rpc.calls.count("program_state") >= 4


def test_a_program_that_never_starts_is_a_fault_not_a_pass():
    """The controller took ProgramRun and stayed stopped. That is a 500.

    Erring the other way is what produced the fabricated success: a cell
    that cannot evidence a run must not report one (LearnedPatterns #15).
    """
    cell, rpc, _ = make_program_cell()
    rpc.never_starts = True
    with pytest.raises(DeviceFaultError):
        cell.start_program("Test1.lua")
    assert cell.diagnose()["program"]["running"] is False


def test_last_line_is_the_high_water_mark_not_the_final_reading():
    """GetCurrentLine resets to 0 as the program ends.

    Measured on cell6 2026-08-11: lines 4, 9, 11, 12, 14, 15, 18, then
    **0**, then state 1. Reporting the last reading says "never executed
    a line" about a program that ran for 23 s — and `last_line > 0` is
    the only evidence a scenario has that the script really executed.
    """
    cell, rpc, _ = make_program_cell()
    rpc.line_script = [4, 9, 18, 0]
    cell.start_program("Test1.lua")
    rpc.program_run_polls = 3
    out = cell.await_program()
    assert out["last_line"] == 18
