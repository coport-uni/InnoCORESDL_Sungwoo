"""T0 unit tests for :class:`cell.arm_replay_cell.ArmReplayCell`.

No hardware and no lerobot: ``ArmReplayCell.__init__`` takes the fairino
RPC handle and the lerobot *runner* as arguments, so the fakes below stand
in for both. Only ``open()`` touches the network, and these never call it.

Covers docs/SPEC_ARM_REPLAY_CELL.md §7 T0-1 … T0-10, plus two properties
that the spec's list implies but does not enumerate, both learned from the
code rather than guessed:

* **The SDK session is handed over, not shared.** The lerobot follower's
  ``connect()`` runs ``RobotEnable(0) → ResetAllError → RobotEnable(1) →
  Mode(0) → ServoMoveStart`` (spec Q1), which is an exclusive control
  session. The cell must therefore drop its own RPC before the subprocess
  starts and rebuild it afterwards, and ``status()`` must not touch the
  SDK while a replay is running.
* **``CloseRPC()`` raises on this SDK.** ``fairino/Robot.py`` closes over
  a local ``thread`` but reads ``self.thread``, so every ``CloseRPC()``
  ends in ``AttributeError``. A release path that let that propagate
  would turn every replay launch into a 500.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cell.arm_replay_cell import (  # noqa: E402
    MAX_JOG_DEG,
    MAX_JOG_SPEED_PCT,
    MAX_START_APPROACH_DEG,
    POSE_RUNAWAY_FACTOR,
    PROGRAM_PAUSED,
    PROGRAM_RUNNING,
    PROGRAM_STOPPED,
    ArmReplayCell,
    ArmReplayConfig,
    build_replay_argv,
    replay_timeout_s,
)
from cell.cell_protocol import (  # noqa: E402
    CellTimeoutError,
    DeviceFaultError,
    InvalidArgError,
    TransportError,
    WrongStateError,
)

#: A pose the fake dataset's first frame sits at.
FIRST_FRAME_DEG = [10.0, -90.0, 90.0, -90.0, -90.0, 0.0]
#: …and its last frame.
LAST_FRAME_DEG = [20.0, -80.0, 95.0, -90.0, -90.0, 5.0]

RECORDED_FPS = 20
EPISODE_FRAMES = 200
TOTAL_EPISODES = 3


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
        self._owner.calls.append("program_run")
        if self._owner.run_error == 0:
            self._owner.program_state = PROGRAM_RUNNING
        return self._owner.run_error

    def ProgramStop(self):  # noqa: N802
        self._owner.calls.append("program_stop")
        self._owner.program_state = PROGRAM_STOPPED
        return self._owner.program_stop_error

    def GetProgramState(self):  # noqa: N802
        self._owner.calls.append("program_state")
        if self._owner.program_state_seq:
            self._owner.program_state = self._owner.program_state_seq.pop(0)
        return [0, self._owner.program_state]

    def GetCurrentLine(self):  # noqa: N802
        self._owner.calls.append("current_line")
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
        #: What GetProgramState answers when the queue below is empty.
        self.program_state = PROGRAM_STOPPED
        #: States handed out one per poll, so a test can script "running,
        #: running, stopped" without a clock.
        self.program_state_seq: list[int] = []
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


class FakeProc:
    """Popen stand-in. ``exits_after`` waits before reporting a code."""

    def __init__(
        self,
        returncode: int = 0,
        *,
        hangs: bool = False,
    ) -> None:
        self.pid = 4242
        self._returncode = returncode
        self._hangs = hangs
        self._done = False

    def poll(self) -> int | None:
        return None if not self._done else self._returncode

    def wait(self, timeout: float | None = None) -> int:
        if self._hangs and not self._done:
            raise subprocess.TimeoutExpired(
                cmd="lerobot-replay", timeout=timeout
            )
        self._done = True
        return self._returncode


class FakeRunner:
    """Stand-in for the conda/lerobot subprocess runner.

    ``meta`` is what the dataset probe reports; ``spawned`` records the
    argv the cell assembled; ``signals`` records the stop escalation.
    """

    def __init__(
        self,
        proc: FakeProc | None = None,
        *,
        meta: dict | None = None,
        probe_error: Exception | None = None,
    ) -> None:
        self.proc = proc or FakeProc()
        self.meta = meta or {
            "total_episodes": TOTAL_EPISODES,
            "frames": EPISODE_FRAMES,
            "fps": RECORDED_FPS,
            "cached": True,
            "first_action_deg": list(FIRST_FRAME_DEG),
            "last_action_deg": list(LAST_FRAME_DEG),
        }
        self.probe_error = probe_error
        self.spawned: list[list[str]] = []
        self.signals: list[str] = []

    def probe(self, repo_id: str, episode: int) -> dict:
        if self.probe_error is not None:
            raise self.probe_error
        out = dict(self.meta)
        if not out.get("cached", True):
            # The probe stops at the cache check under offline_only.
            return {"cached": False}
        if not 0 <= episode < int(out["total_episodes"]):
            # What the real probe prints for an episode it cannot
            # describe: the count, and nothing about frames.
            return {"cached": True, "total_episodes": out["total_episodes"]}
        return out

    def spawn(self, argv: list[str]):
        self.spawned.append(list(argv))
        return self.proc

    def terminate(self, proc) -> None:
        self.signals.append("SIGTERM")

    def kill(self, proc) -> None:
        self.signals.append("SIGKILL")
        proc._done = True


def make_cell(
    rpc: FakeRPC | None = None,
    runner: FakeRunner | None = None,
    **overrides,
) -> tuple[ArmReplayCell, FakeRPC, FakeRunner]:
    """Build a cell over fakes, at the first frame unless told otherwise."""
    rpc = rpc or FakeRPC(list(FIRST_FRAME_DEG))
    runner = runner or FakeRunner()
    cfg = ArmReplayConfig(
        robot_id="fr5_a",
        ip_address="192.168.0.58",
        gripper_enabled=True,
        **overrides,
    )
    # `reconnect` is what the cell calls after handing the servo session
    # to the subprocess. Left at its default it would open a real socket
    # to the bench arm — a unit test must never dial 192.168.0.58.
    cell = ArmReplayCell(rpc, cfg, runner=runner, reconnect=lambda: rpc)
    return cell, rpc, runner


def test_movej_declares_the_controllers_active_tool_frame():
    """LearnedPatterns #44: hard-coding tool=0 IS "SDK error 154".

    GetForwardKin answers in the active tool frame, so MoveJ must be told
    that same frame or the joint target and the Cartesian desc_pos
    describe points in different frames and the pair is rejected. cell6
    runs tool 1, cell7 runs tool 0 — identical code, opposite outcomes.
    """
    for tool_num in (0, 1, 2):
        rpc = FakeRPC(list(FIRST_FRAME_DEG))
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
    rpc = FakeRPC(list(FIRST_FRAME_DEG))
    cell, _, _ = make_cell(rpc=rpc)
    out = cell.jog_joint(1, MAX_JOG_DEG)
    assert out["achieved_delta_deg"] == pytest.approx(MAX_JOG_DEG)


@pytest.mark.parametrize(
    "delta", [MAX_JOG_DEG + 0.1, -MAX_JOG_DEG - 0.1, 90.0, -180.0]
)
def test_jog_over_the_cap_is_refused(delta):
    rpc = FakeRPC(list(FIRST_FRAME_DEG))
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
    rpc = FakeRPC(list(FIRST_FRAME_DEG))
    cell, _, _ = make_cell(rpc=rpc)
    cell.jog_joint(1, 10.0, speed_pct=999.0)
    movej = [c for c in rpc.calls if isinstance(c, tuple) and c[0] == "movej"]
    assert movej[0][2] == pytest.approx(MAX_JOG_SPEED_PCT)


# ── Latched-fault gate (measured: MoveJ answers SDK error 154) ───────────


def test_jog_is_refused_while_a_fault_is_latched():
    rpc = FakeRPC(list(FIRST_FRAME_DEG))
    rpc.fault = [1, 1]  # what the bench arm actually reported
    cell, _, _ = make_cell(rpc=rpc)
    with pytest.raises(WrongStateError) as caught:
        cell.jog_joint(1, 10.0)
    assert "latched fault" in str(caught.value)
    # Nothing was commanded.
    assert [c for c in rpc.calls if c and c[0] == "movej"] == []


def test_replay_is_refused_while_a_fault_is_latched():
    rpc = FakeRPC(list(FIRST_FRAME_DEG))
    rpc.fault = [1, 1]
    cell, _, runner = make_cell(rpc=rpc)
    with pytest.raises(WrongStateError):
        cell.start_replay("coport-uni/x", 0, fps=None)
    assert runner.spawned == []


def test_prepare_arm_clears_and_reports_what_was_wrong():
    rpc = FakeRPC(list(FIRST_FRAME_DEG))
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
    rpc = FakeRPC(list(FIRST_FRAME_DEG))
    rpc.fault = [1, 1]
    cell, _, _ = make_cell(rpc=rpc)
    cell.prepare_arm()
    out = cell.jog_joint(1, 10.0)
    assert out["achieved_delta_deg"] == pytest.approx(10.0)
    assert out["max_other_axis_delta_deg"] == pytest.approx(0.0)


def test_diagnose_reports_a_faulted_arm_as_not_ready():
    """A health check that cannot fail is not a check."""
    rpc = FakeRPC(list(FIRST_FRAME_DEG))
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
    cfg = ArmReplayConfig.from_toml(raw["arm"])
    assert cfg.ip_address == ip
    assert cfg.robot_id == robot_id
    assert isinstance(cfg.gripper_enabled, bool)
    assert isinstance(cfg.allowed_repo_prefixes, tuple)
    assert all(isinstance(p, str) for p in cfg.allowed_repo_prefixes)
    assert isinstance(cfg.max_replay_s, float)
    assert isinstance(cfg.replay_timeout_factor, float)
    assert isinstance(cfg.start_pose_tolerance_deg, float)
    assert isinstance(cfg.final_pose_tolerance_deg, float)
    assert isinstance(cfg.jog_speed_pct, float)
    assert isinstance(cfg.offline_only, bool)
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


# ── T0-2 repo_id prefix allow-list ──────────────────────────────────────


def test_allowed_repo_prefix_passes():
    cell, _, runner = make_cell()
    out = cell.prefetch_episode("coport-uni/x", 0)
    assert out["cached"] is True
    assert out["frames"] == EPISODE_FRAMES
    assert out["fps"] == RECORDED_FPS


def test_disallowed_repo_prefix_rejected():
    cell, _, runner = make_cell()
    with pytest.raises(InvalidArgError):
        cell.prefetch_episode("evil/x", 0)
    # Rejected before anything was downloaded or spawned.
    assert runner.spawned == []


def test_disallowed_repo_prefix_rejected_on_replay_too():
    cell, _, runner = make_cell()
    with pytest.raises(InvalidArgError):
        cell.start_replay("evil/x", 0, fps=None)
    assert runner.spawned == []


# ── T0-3 episode range ──────────────────────────────────────────────────


@pytest.mark.parametrize("episode", [TOTAL_EPISODES, TOTAL_EPISODES + 5, -1])
def test_episode_out_of_range_rejected(episode):
    cell, _, _ = make_cell()
    with pytest.raises(InvalidArgError):
        cell.start_replay("coport-uni/x", episode, fps=None)


def test_last_episode_in_range_accepted():
    cell, _, runner = make_cell()
    cell.start_replay("coport-uni/x", TOTAL_EPISODES - 1, fps=None)
    assert len(runner.spawned) == 1


# ── T0-4 fps ────────────────────────────────────────────────────────────


def test_fps_null_adopts_recorded_fps():
    cell, _, runner = make_cell()
    started = cell.start_replay("coport-uni/x", 0, fps=None)
    assert started["fps"] == RECORDED_FPS
    assert f"--dataset.fps={RECORDED_FPS}" in runner.spawned[0]


def test_fps_matching_recorded_passes():
    cell, _, runner = make_cell()
    started = cell.start_replay("coport-uni/x", 0, fps=RECORDED_FPS)
    assert started["fps"] == RECORDED_FPS


def test_fps_mismatch_rejected():
    """D5: fps-accelerated replay compresses the ServoJ interval."""
    cell, _, runner = make_cell()
    with pytest.raises(InvalidArgError):
        cell.start_replay("coport-uni/x", 0, fps=RECORDED_FPS * 2)
    assert runner.spawned == []


# ── T0-5 timeout arithmetic ─────────────────────────────────────────────


def test_timeout_from_frames_and_fps():
    assert replay_timeout_s(2400, 20, factor=1.5, ceiling=300.0) == 180.0


def test_timeout_is_capped_by_max_replay_s():
    assert replay_timeout_s(2400, 20, factor=1.5, ceiling=100.0) == 100.0


def test_timeout_rejects_nonpositive_fps():
    with pytest.raises(InvalidArgError):
        replay_timeout_s(2400, 0, factor=1.5, ceiling=300.0)


# ── T0-6 CLI assembly ───────────────────────────────────────────────────


def test_replay_argv_matches_the_reference_script():
    """Same argument series as FR5ControllerVLA's ``6__replay.sh``."""
    cfg = ArmReplayConfig(
        robot_id="fr5_follower",
        ip_address="192.168.58.2",
        gripper_enabled=True,
        dataset_root="/data/FR5_pick_red_colored_marker_to_box",
    )
    argv = build_replay_argv(
        cfg,
        repo_id="coport-uni/FR5_pick_red_colored_marker_to_box",
        episode=0,
        fps=20,
    )
    assert argv == [
        "lerobot-replay",
        "--robot.type=fairino_follower",
        "--robot.ip_address=192.168.58.2",
        "--robot.gripper_enabled=true",
        "--robot.id=fr5_follower",
        "--dataset.root=/data/FR5_pick_red_colored_marker_to_box",
        "--dataset.repo_id=coport-uni/FR5_pick_red_colored_marker_to_box",
        "--dataset.episode=0",
        "--dataset.fps=20",
    ]


def test_replay_argv_omits_dataset_root_when_unset():
    cfg = ArmReplayConfig(robot_id="fr5_b", ip_address="192.168.0.59")
    argv = build_replay_argv(cfg, repo_id="coport-uni/x", episode=2, fps=30)
    assert not any(a.startswith("--dataset.root=") for a in argv)
    assert "--robot.gripper_enabled=true" in argv
    assert "--dataset.episode=2" in argv


def test_replay_argv_lowercases_the_gripper_flag():
    """``--robot.gripper_enabled=False`` is not what draccus parses."""
    cfg = ArmReplayConfig(gripper_enabled=False)
    argv = build_replay_argv(cfg, repo_id="coport-uni/x", episode=0, fps=20)
    assert "--robot.gripper_enabled=false" in argv


# ── T0-7 one replay at a time ───────────────────────────────────────────


def test_second_replay_while_running_is_rejected():
    cell, _, runner = make_cell(runner=FakeRunner(FakeProc(hangs=True)))
    cell.start_replay("coport-uni/x", 0, fps=None)
    with pytest.raises(WrongStateError):
        cell.start_replay("coport-uni/x", 1, fps=None)
    assert len(runner.spawned) == 1


def test_await_without_a_start_is_rejected():
    cell, _, _ = make_cell()
    with pytest.raises(WrongStateError):
        cell.await_replay()


# ── T0-8 subprocess exit codes ──────────────────────────────────────────


def test_nonzero_exit_is_a_device_fault():
    cell, _, _ = make_cell(runner=FakeRunner(FakeProc(returncode=1)))
    cell.start_replay("coport-uni/x", 0, fps=None)
    with pytest.raises(DeviceFaultError):
        cell.await_replay()


def test_clean_exit_reports_the_encoder_error():
    """§6.2 step 7: 200 OK means the encoder was re-read after the run."""
    rpc = FakeRPC(list(FIRST_FRAME_DEG))
    cell, _, _ = make_cell(rpc=rpc, runner=FakeRunner(FakeProc(0)))
    cell.start_replay("coport-uni/x", 0, fps=None)
    # The arm ends where the subprocess left it — 0.4 deg off the last
    # recorded frame on joint 3.
    landed = list(LAST_FRAME_DEG)
    landed[2] += 0.4
    rpc.joints = landed
    out = cell.await_replay()
    assert out["completed"] is True
    assert out["frames"] == EPISODE_FRAMES
    assert out["joints_deg"] == pytest.approx(landed)
    assert out["final_joint_error_deg"] == pytest.approx(0.4)
    assert out["elapsed_s"] >= 0.0


def test_runaway_final_pose_is_a_device_fault():
    rpc = FakeRPC(list(FIRST_FRAME_DEG))
    cell, _, _ = make_cell(rpc=rpc, runner=FakeRunner(FakeProc(0)))
    cell.start_replay("coport-uni/x", 0, fps=None)
    runaway = list(LAST_FRAME_DEG)
    runaway[0] += POSE_RUNAWAY_FACTOR * cell.config.final_pose_tolerance_deg
    runaway[0] += 1.0
    rpc.joints = runaway
    with pytest.raises(DeviceFaultError):
        cell.await_replay()


def test_replay_overrun_times_out_and_kills_the_subprocess():
    runner = FakeRunner(FakeProc(hangs=True))
    cell, _, _ = make_cell(runner=runner, max_replay_s=0.01)
    cell.start_replay("coport-uni/x", 0, fps=None)
    with pytest.raises(CellTimeoutError):
        cell.await_replay()
    assert runner.signals == ["SIGTERM", "SIGKILL"]


# ── T0-9 stop() ─────────────────────────────────────────────────────────


def test_stop_terminates_the_subprocess_then_stops_the_sdk():
    rpc = FakeRPC(list(FIRST_FRAME_DEG))
    runner = FakeRunner(FakeProc(hangs=True))
    cell, _, _ = make_cell(rpc=rpc, runner=runner)
    cell.start_replay("coport-uni/x", 0, fps=None)
    result = cell.stop()
    # Order matters: the streaming process dies before the controller is
    # told to stop, or the next ServoJ frame restarts the motion.
    assert runner.signals[0] == "SIGTERM"
    assert "stop_motion" in rpc.calls
    assert rpc.calls.index("stop_motion") > 0
    assert result["subprocess"] in ("terminated", "killed")
    assert result["sdk"] == "stopped"


def test_stop_when_idle_still_stops_the_sdk():
    rpc = FakeRPC()
    cell, _, runner = make_cell(rpc=rpc)
    result = cell.stop()
    assert result["subprocess"] == "idle"
    assert result["sdk"] == "stopped"
    assert runner.signals == []


def test_stop_records_an_sdk_failure_without_raising():
    rpc = FakeRPC()
    rpc.stop_error = 14  # RobotMotionError
    cell, _, _ = make_cell(rpc=rpc)
    result = cell.stop()
    assert result["subprocess"] == "idle"
    assert result["sdk"] != "stopped"
    assert "14" in result["sdk"]


def test_stop_does_not_wait_for_the_replay_lock():
    """§6.1: stop reaches the process handle directly.

    The guard that makes a second ``start_replay`` a 409 must not be the
    thing ``stop()`` blocks on — that is GAP-9, and this cell is meant to
    be the counter-example.
    """
    runner = FakeRunner(FakeProc(hangs=True))
    cell, _, _ = make_cell(runner=runner)
    cell.start_replay("coport-uni/x", 0, fps=None)
    assert cell._replay_lock.locked() is False
    result = cell.stop()
    assert result["subprocess"] in ("terminated", "killed")


# ── T0-10 start-pose tolerance ──────────────────────────────────────────


def test_far_from_the_first_frame_moves_there_first():
    far = [v + 10.0 for v in FIRST_FRAME_DEG]
    rpc = FakeRPC(far)
    cell, _, runner = make_cell(rpc=rpc, start_pose_tolerance_deg=2.0)
    cell.start_replay("coport-uni/x", 0, fps=None)
    movej = [c for c in rpc.calls if isinstance(c, tuple)]
    assert len(movej) == 1
    assert movej[0][1] == pytest.approx(FIRST_FRAME_DEG)
    # …at the configured jog speed, not the SDK default.
    assert movej[0][2] == pytest.approx(cell.config.jog_speed_pct)


def test_too_far_from_the_first_frame_is_refused():
    """Measured on the bench: 97.2 deg from episode 10's first frame.

    A replay must not answer by swinging the wrist through a right
    angle, so beyond MAX_START_APPROACH_DEG the cell refuses and names
    the pose to jog to.
    """
    far = list(FIRST_FRAME_DEG)
    far[5] += POSE_RUNAWAY_FACTOR * 0 + MAX_START_APPROACH_DEG + 60.0
    rpc = FakeRPC(far)
    cell, _, runner = make_cell(rpc=rpc, start_pose_tolerance_deg=2.0)
    with pytest.raises(WrongStateError) as caught:
        cell.start_replay("coport-uni/x", 0, fps=None)
    assert "approach limit" in str(caught.value)
    # Nothing moved and nothing was spawned.
    assert [c for c in rpc.calls if isinstance(c, tuple)] == []
    assert runner.spawned == []


def test_within_tolerance_does_not_move_first():
    near = [v + 1.0 for v in FIRST_FRAME_DEG]
    rpc = FakeRPC(near)
    cell, _, runner = make_cell(rpc=rpc, start_pose_tolerance_deg=2.0)
    cell.start_replay("coport-uni/x", 0, fps=None)
    assert [c for c in rpc.calls if isinstance(c, tuple)] == []
    assert len(runner.spawned) == 1


# ── Session hand-over (spec Q1) ─────────────────────────────────────────


def test_rpc_is_released_before_the_subprocess_starts():
    """The follower's connect() enables the arm and opens a servo
    session; two owners of that session is the failure Q1 asks about."""
    rpc = FakeRPC(list(FIRST_FRAME_DEG))
    cell, _, runner = make_cell(rpc=rpc)
    cell.start_replay("coport-uni/x", 0, fps=None)
    assert rpc.closed is True
    assert cell._rpc is None


def test_release_survives_the_sdk_close_bug():
    rpc = FakeRPC(list(FIRST_FRAME_DEG), close_raises=True)
    cell, _, runner = make_cell(rpc=rpc)
    cell.start_replay("coport-uni/x", 0, fps=None)  # must not raise
    assert len(runner.spawned) == 1


def test_status_during_replay_does_not_touch_the_sdk():
    rpc = FakeRPC(list(FIRST_FRAME_DEG))
    cell, _, _ = make_cell(rpc=rpc, runner=FakeRunner(FakeProc(hangs=True)))
    cell.start_replay("coport-uni/x", 0, fps=None)
    before = list(rpc.calls)
    state = cell.status()
    assert rpc.calls == before
    assert state["busy"] is True
    assert state["joints_deg"] == pytest.approx(FIRST_FRAME_DEG)


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


# ── offline_only (§6.2 step 2) ──────────────────────────────────────────


def test_offline_only_rejects_an_uncached_dataset():
    runner = FakeRunner()
    runner.meta["cached"] = False
    cell, _, _ = make_cell(runner=runner, offline_only=True)
    with pytest.raises(WrongStateError):
        cell.start_replay("coport-uni/x", 0, fps=None)


def test_offline_only_accepts_a_cached_dataset():
    runner = FakeRunner()
    runner.meta["cached"] = True
    cell, _, _ = make_cell(runner=runner, offline_only=True)
    cell.start_replay("coport-uni/x", 0, fps=None)
    assert len(runner.spawned) == 1


# ── diagnose + the other action sets ────────────────────────────────────


def test_diagnose_reports_the_arm_and_the_replay_state():
    cell, _, _ = make_cell()
    report = cell.diagnose()
    assert report["arm"]["ok"] is True
    assert report["arm"]["robot_id"] == "fr5_a"
    assert report["arm"]["ip"] == "192.168.0.58"
    assert report["arm"]["reachable"] is True
    assert report["arm"]["gripper"] is True
    assert report["replay"]["running"] is False
    assert report["replay"]["last"] is None
    # The families this cell does not have must still be reported, so the
    # UI can grey them out rather than probing and getting a 409.
    for absent in ("pump", "balance", "stage"):
        assert report[absent]["present"] is False


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
