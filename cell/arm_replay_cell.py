"""Real cell6 / cell7: one FR5 robot arm, on two motion paths.

Two identical cells, one shape, config apart (spec D1): cell6 is the
**synthesis-stage** arm at 192.168.0.58, cell7 the **analysis-stage** arm
at 192.168.0.59. One arm per cell process, because the L2 lock is per
cell and an arm is exactly the unit that must be locked.

============  =============================================================
Motion (a)    ``lerobot-replay`` on a recorded HuggingFace dataset episode
Motion (b)    a ``.lua`` job program already on the controller
Reads         the fairino XMLRPC SDK (``external/FR5Controller/fairino``)
============  =============================================================

The two paths are mutually exclusive and differ in who is doing the work.
A **replay** keeps this machine in the loop: lerobot streams the episode's
frames as ``ServoJ`` at 20 Hz, so the PC and the network are load-bearing
for the whole episode, and only motions someone recorded exist. A **job
program** is executed by the controller's own interpreter and planner —
``Mode(0)`` → ``ProgramLoad`` → ``ProgramRun`` and this process is done
talking; the firmware does the interpolation and blending (see
``docs/SPEC_ARM_LUA_PROGRAM.md``). The cost is feedback: a replay can be
checked against the episode's last frame, while a job program is a script
this cell never reads, so ``await_program`` can only report that it ended
cleanly — not that it ended anywhere in particular.

The arm action set is deliberately *not* a pose interface (ADDING_A_CELL.md
"a robot arm is just another action family"): a request names a dataset
repo and an episode number, or a program file name. Nothing streams a
pose from L2.

Four properties of this cell that are not obvious from the interface:

- **The replay runs in another conda env, as a subprocess** (spec D3).
  lerobot drags in torch; the SDL venv must not. The subprocess is also
  what makes ``stop()`` real — killing a process stops the ServoJ stream
  in a way no in-process flag could.
- **The SDK session is handed over, not shared** (spec Q1). The lerobot
  follower's ``connect()`` runs ``RobotEnable(0) → ResetAllError →
  RobotEnable(1) → Mode(0) → ServoMoveStart``, an exclusive control
  session. So the cell drops its own RPC before spawning the subprocess
  and rebuilds it afterwards, and ``status()`` answers from the last
  reading — never the wire — while a replay is in flight.
- **Replay is two calls, not one** (``start_replay`` + ``await_replay``).
  The server holds one lock per command; a single blocking call would
  hold it for the whole episode and ``POST /v1/stop`` would queue behind
  the motion it is meant to abort. That is GAP-9 (LearnedPatterns #9),
  and spec §6.1 says reproducing it here is a defect. The route takes the
  lock only for the launch and waits outside it.
- **A 200 means the encoder was read.** ``await_replay`` re-reads the
  joints from the SDK after the subprocess exits and reports the error
  against the episode's last recorded frame. LearnedPatterns #24: the
  gantry once reported the position it was asked for, not the one it
  reached.

Hardware-verified at the bench, not in CI. The unit tests in
``claude_test/test_arm_replay_cell.py`` drive fakes.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
import xmlrpc.client
from dataclasses import dataclass, field
from pathlib import Path

from .cell_protocol import (
    Cell,
    CellTimeoutError,
    DeviceFaultError,
    InvalidArgError,
    TransportError,
    WrongStateError,
)

#: Joints on an FR5. The dataset's action vector may carry a gripper
#: channel after these; only the revolute joints are compared.
JOINT_COUNT = 6

#: How far past ``final_pose_tolerance_deg`` the post-replay pose may sit
#: before the cell calls it a fault rather than a miss. The tolerance
#: itself is a *scenario* judgement (spec §5) — the response carries the
#: number and the scenario asserts on it. This is the separate, much
#: looser "the arm is not where the episode ended at all" line.
POSE_RUNAWAY_FACTOR = 10.0

#: Grace period between SIGTERM and SIGKILL on the replay subprocess
#: (spec §6.1). Long enough for lerobot's ``finally: robot.disconnect()``
#: to run, short enough that ``POST /v1/stop`` answers inside its 2 s
#: acceptance bound.
TERMINATE_GRACE_S = 2.0

#: Timeout for the dataset metadata probe. It may download, so this is
#: generous; it is deliberately NOT part of the replay timeout (spec D7
#: splits prefetch from motion so a slow network cannot look like a
#: stalled arm).
PROBE_TIMEOUT_S = 1800.0

#: fairino SDK error code meaning "OK".
_SDK_OK = 0

#: ``flag`` for the controller's joint read: 1 = non-blocking.
_JOINT_READ_NONBLOCKING = 1

#: How far the arm may be from an episode's first frame and still have
#: the cell drive it there itself (spec §6.2 step 4). Beyond this the
#: request is refused instead, because the "approach" would be a large
#: simultaneous multi-joint swing that nobody asked for by name.
#:
#: Measured 2026-08-11, and this is why the cap exists: with the arm
#: parked where the bench left it, the gap to episode 10 of
#: FR5_task3_turn_the_sliver_air_valve… was **97.2 deg** — joint 6 at
#: -98.1 against a first frame of -1.0. A `POST /v1/arm/replay` would
#: have answered by rotating the wrist through a right angle and more
#: before the episode began. Same lesson as LearnedPatterns #39 on the
#: linear rail: bound the *commanded travel*, and make the operator
#: place the mechanism near its start.
MAX_START_APPROACH_DEG = 30.0

#: Hard ceiling on one ``arm/jog_joint`` step, degrees. The spec's arm
#: action set is replay-only (D8) and the +10 deg acceptance test was
#: meant to stay a bench script; running it from a scenario instead
#: needs a route, so this is the narrowest one that does the job — ONE
#: joint, a RELATIVE step, and a cap no request can raise. It is not a
#: pose interface and must not grow into one: a caller cannot express
#: "go to this configuration", only "nudge this axis a little".
#:
#: Raised from 15 to 30 on 2026-08-11 at the operator's request, after T1
#: passed 3/3 on both arms at 10 deg with a worst increment error of
#: 0.0013 deg. Still a real cap, deliberately: a bound that a request can
#: lift is not a bound. 30 deg of joint 1 is a substantial base sweep —
#: the operator gate and a clear frame do the work here, not this number.
MAX_JOG_DEG = 30.0

#: Speed ceiling for a jog, percent. A commissioning move is slow.
MAX_JOG_SPEED_PCT = 30.0

#: How long ``prepare_arm`` waits before re-reading the fault, to catch
#: one that re-latches. Measured 2026-08-11: cell6 read (0, 0) right
#: after ResetAllError, refused the next MoveJ, and was back at (1, 1)
#: seconds later.
FAULT_RELATCH_WATCH_S = 1.0

#: Pause between the steps of ``prepare_arm``. The follower's own
#: enable sequence sleeps 0.2-0.5 s between calls; the controller does
#: not finish enabling before it answers the RPC.
ENABLE_STEP_SETTLE_S = 0.5

#: ``Mode()`` argument for automatic mode. Every vendor example puts
#: ``Mode(0)`` in front of ``ProgramRun`` (fairino SDK manual, "WebAPP
#: program use"; ``fairino/example/TestWebAppCommand.py``).
_MODE_AUTO = 0

#: ``GetProgramState()`` encoding, per the SDK docstring at
#: ``Robot.py`` ~4915 and the WebAPP manual.
PROGRAM_STOPPED = 1
PROGRAM_RUNNING = 2
PROGRAM_PAUSED = 3

#: The controller path Lua job programs live under. The SDK manual calls
#: ``/fruser/`` a fixed path, so this is a config key only so that a
#: future firmware can move it — not an invitation to point elsewhere.
DEFAULT_PROGRAM_DIR = "/fruser"

#: Extension a job program must have for ``arm/program`` to accept it.
PROGRAM_SUFFIX = ".lua"

#: Seconds to let a jog settle before re-reading. MoveJ answers on
#: acceptance, not on arrival, so a read taken immediately after it
#: reports the pose the arm is leaving (LearnedPatterns #24's shape).
JOG_SETTLE_S = 2.0

#: Reads a lerobot dataset's metadata without importing lerobot into this
#: process (spec D3). Runs under the lerobot conda env; prints one JSON
#: object on stdout. argv: repo_id, episode, root ("" for the HF cache),
#: offline ("1" forbids a download).
META_PROBE_PY = r"""
import json
import sys
from pathlib import Path

repo_id, episode, root, offline = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]

from lerobot.utils.constants import HF_LEROBOT_HOME

local = Path(root) if root else HF_LEROBOT_HOME / repo_id
cached = (local / "meta").is_dir()
if offline == "1" and not cached:
    print(json.dumps({"cached": False}))
    raise SystemExit(0)

from lerobot.datasets.dataset_metadata import LeRobotDatasetMetadata
from lerobot.datasets.lerobot_dataset import LeRobotDataset
from lerobot.utils.constants import ACTION

meta = LeRobotDatasetMetadata(repo_id, root=root or None)
total = int(meta.total_episodes)
if not 0 <= episode < total:
    print(json.dumps({"cached": True, "total_episodes": total}))
    raise SystemExit(0)

ds = LeRobotDataset(repo_id, root=root or None, episodes=[episode])
rows = ds.hf_dataset.filter(lambda x: x["episode_index"] == episode)
names = ds.features[ACTION]["names"]
keep = [i for i, n in enumerate(names) if n.lower().startswith("joint")]
first = [float(rows[0][ACTION][i]) for i in keep]
last = [float(rows[len(rows) - 1][ACTION][i]) for i in keep]
print(json.dumps({
    "cached": True,
    "total_episodes": total,
    "frames": int(len(rows)),
    "fps": int(ds.fps),
    "first_action_deg": first,
    "last_action_deg": last,
}))
"""


@dataclass(frozen=True, slots=True)
class ArmReplayConfig:
    """Bench wiring for one arm (loaded from the cell6/cell7 TOML)."""

    #: Identifier written into logs, runlogs and ``--robot.id``.
    robot_id: str = "fr5_a"
    #: The controller's address. Fixed network asset, so no VID:PID rule.
    #: cell6 and cell7 MUST differ (spec §8.3): teach-pendant work, not
    #: something a second process can detect.
    ip_address: str = "192.168.0.58"
    gripper_enabled: bool = True
    # ── the lerobot side (spec D3: its own env, never installed here) ──
    conda_sh: str = "/home/inno-controller/anaconda3/etc/profile.d/conda.sh"
    conda_env: str = "lerobot"
    lerobot_root: str = "external/FR5ControllerVLA"
    #: ``--dataset.root``. Empty means the HuggingFace cache, which is
    #: what an orchestrated run should use; ``6__replay.sh`` pins a local
    #: directory instead and this reproduces that when a bench needs it.
    dataset_root: str = ""
    # ── what a request is allowed to ask for ──────────────────────────
    #: Spec D6. A dataset outside these prefixes is not replayed: an
    #: arbitrary episode off the internet is physical motion here.
    allowed_repo_prefixes: tuple[str, ...] = ("coport-uni/",)
    #: ``HF_HOME`` for the subprocesses; empty leaves the default.
    cache_dir: str = ""
    #: Refuse to replay anything not already downloaded.
    offline_only: bool = False
    # ── motion envelope ───────────────────────────────────────────────
    max_replay_s: float = 300.0
    replay_timeout_factor: float = 1.5
    start_pose_tolerance_deg: float = 2.0
    final_pose_tolerance_deg: float = 1.0
    #: MoveJ velocity for the start-pose approach and for smoke_arm.py.
    jog_speed_pct: float = 10.0
    # ── the Lua job-program side (the second motion path) ─────────────
    #: Where the controller keeps its ``.lua`` job programs.
    program_dir: str = DEFAULT_PROGRAM_DIR
    #: Same gate as ``allowed_repo_prefixes``, for programs. Empty means
    #: any ``*.lua`` on the controller; naming them here narrows a
    #: motion-bearing request to a reviewed list.
    allowed_programs: tuple[str, ...] = ()
    #: Timeout for one program run. Unlike a replay this cannot be
    #: derived — the cell does not read the script, so it has no frames
    #: and no fps to compute from.
    max_program_s: float = 300.0
    #: How often ``await_program`` asks the controller whether it is
    #: still running.
    program_poll_s: float = 0.5

    @classmethod
    def from_toml(cls, table: dict) -> ArmReplayConfig:
        """Build a config from the ``[arm]`` table of a cell TOML.

        Args:
            table: The parsed ``[arm]`` table.

        Returns:
            The config, with every field coerced to its declared type —
            TOML gives ``300`` for a float field as an int otherwise, and
            T0-1 asserts on the types.
        """
        prefixes = table.get("allowed_repo_prefixes", ("coport-uni/",))
        return cls(
            robot_id=str(table.get("robot_id", "fr5_a")),
            ip_address=str(table.get("ip_address", "192.168.0.58")),
            gripper_enabled=bool(table.get("gripper_enabled", True)),
            conda_sh=str(table.get("conda_sh", cls.conda_sh)),
            conda_env=str(table.get("conda_env", "lerobot")),
            lerobot_root=str(
                table.get("lerobot_root", "external/FR5ControllerVLA")
            ),
            dataset_root=str(table.get("dataset_root", "")),
            allowed_repo_prefixes=tuple(str(p) for p in prefixes),
            cache_dir=str(table.get("cache_dir", "")),
            offline_only=bool(table.get("offline_only", False)),
            max_replay_s=float(table.get("max_replay_s", 300.0)),
            replay_timeout_factor=float(
                table.get("replay_timeout_factor", 1.5)
            ),
            start_pose_tolerance_deg=float(
                table.get("start_pose_tolerance_deg", 2.0)
            ),
            final_pose_tolerance_deg=float(
                table.get("final_pose_tolerance_deg", 1.0)
            ),
            jog_speed_pct=float(table.get("jog_speed_pct", 10.0)),
            program_dir=str(
                table.get("program_dir", DEFAULT_PROGRAM_DIR)
            ).rstrip("/"),
            allowed_programs=tuple(
                str(p) for p in table.get("allowed_programs", ())
            ),
            max_program_s=float(table.get("max_program_s", 300.0)),
            program_poll_s=float(table.get("program_poll_s", 0.5)),
        )


def replay_timeout_s(
    frames: int, fps: int, *, factor: float, ceiling: float
) -> float:
    """Seconds to allow the replay subprocess (spec §6.2 step 3).

    Args:
        frames: Frames in the episode.
        fps: Frames per second the episode was recorded at.
        factor: Slack multiplier over the nominal duration.
        ceiling: ``max_replay_s`` — the absolute upper bound.

    Returns:
        ``min(frames / fps * factor, ceiling)``.

    Raises:
        InvalidArgError: ``fps`` is not positive.
    """
    if fps <= 0:
        raise InvalidArgError(f"fps must be positive, got {fps}", command="arm")
    return min(frames / fps * factor, ceiling)


def build_replay_argv(
    config: ArmReplayConfig, *, repo_id: str, episode: int, fps: int
) -> list[str]:
    """Assemble the ``lerobot-replay`` argv (spec §6.2 step 5).

    Same argument series as ``FR5ControllerVLA/6__replay.sh``, which is
    the only invocation known to have replayed on this hardware.

    Args:
        config: This cell's arm config.
        repo_id: HuggingFace dataset id.
        episode: Episode index within the dataset.
        fps: Replay rate; always the recorded rate (spec D5).

    Returns:
        The argv, ``lerobot-replay`` first.
    """
    argv = [
        "lerobot-replay",
        "--robot.type=fairino_follower",
        f"--robot.ip_address={config.ip_address}",
        # Lower-case: draccus parses the literal, and "False" is not it.
        f"--robot.gripper_enabled={str(config.gripper_enabled).lower()}",
        f"--robot.id={config.robot_id}",
    ]
    if config.dataset_root:
        argv.append(f"--dataset.root={config.dataset_root}")
    argv += [
        f"--dataset.repo_id={repo_id}",
        f"--dataset.episode={episode}",
        f"--dataset.fps={fps}",
    ]
    return argv


def build_conda_command(config: ArmReplayConfig, argv: list[str]) -> list[str]:
    """Wrap ``argv`` in the lerobot conda env (spec D3).

    ``exec`` matters: without it the process the cell holds is bash, and
    a SIGTERM would kill the shell while the replay kept streaming.

    Args:
        config: This cell's arm config.
        argv: The command to run inside the env.

    Returns:
        A ``bash -lc`` argv.
    """
    inner = " ".join(shlex.quote(a) for a in argv)
    script = (
        f"source {shlex.quote(config.conda_sh)} && "
        f"conda activate {shlex.quote(config.conda_env)} && "
        f"exec {inner}"
    )
    return ["bash", "-lc", script]


class LerobotRunner:
    """Runs lerobot in its own conda env, as a child process group.

    Split out of the cell so the unit tests can replace it wholesale:
    everything that needs conda, the network, or a real PID lives here.
    """

    def __init__(self, config: ArmReplayConfig) -> None:
        self._cfg = config

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self._cfg.cache_dir:
            env["HF_HOME"] = self._cfg.cache_dir
        if self._cfg.offline_only:
            env["HF_HUB_OFFLINE"] = "1"
        return env

    def probe(self, repo_id: str, episode: int) -> dict:
        """Read the dataset's metadata, downloading it if allowed.

        Args:
            repo_id: HuggingFace dataset id.
            episode: Episode index to describe.

        Returns:
            ``cached``, and — when the episode exists — ``total_episodes``,
            ``frames``, ``fps``, ``first_action_deg``, ``last_action_deg``.

        Raises:
            TransportError: The probe failed (network, HF auth, a broken
                lerobot env). Prefetch is the network step, so a failure
                here is a 503, never a 500.
            CellTimeoutError: The probe outlived ``PROBE_TIMEOUT_S``.
        """
        argv = [
            "python",
            "-c",
            META_PROBE_PY,
            repo_id,
            str(episode),
            self._cfg.dataset_root,
            "1" if self._cfg.offline_only else "0",
        ]
        try:
            done = subprocess.run(
                build_conda_command(self._cfg, argv),
                cwd=self._cfg.lerobot_root,
                env=self._env(),
                capture_output=True,
                text=True,
                timeout=PROBE_TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CellTimeoutError(
                f"dataset probe timed out after {PROBE_TIMEOUT_S:.0f} s",
                command="arm/prefetch",
            ) from exc
        except OSError as exc:
            raise TransportError(str(exc), command="arm/prefetch") from exc
        if done.returncode != _SDK_OK:
            raise TransportError(
                f"dataset probe failed (exit {done.returncode}): "
                f"{done.stderr.strip()[-400:]}",
                command="arm/prefetch",
            )
        # lerobot logs to stdout as well; the probe's JSON is the last
        # line it prints.
        for line in reversed(done.stdout.strip().splitlines()):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        raise TransportError(
            f"dataset probe printed no JSON: {done.stdout.strip()[-400:]}",
            command="arm/prefetch",
        )

    def spawn(self, argv: list[str]) -> subprocess.Popen:
        """Start the replay in its own session so the group can be killed."""
        return subprocess.Popen(  # noqa: S603 — argv built from config
            build_conda_command(self._cfg, argv),
            cwd=self._cfg.lerobot_root,
            env=self._env(),
            start_new_session=True,
        )

    def terminate(self, proc: subprocess.Popen) -> None:
        self._signal(proc, signal.SIGTERM)

    def kill(self, proc: subprocess.Popen) -> None:
        self._signal(proc, signal.SIGKILL)

    @staticmethod
    def _signal(proc: subprocess.Popen, sig: int) -> None:
        """Signal the whole process group, falling back to the child.

        ``conda activate`` can leave helpers around the exec'd replay;
        signalling only the child would orphan them and, worse, leave
        something that can still write to the controller.
        """
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.send_signal(sig)
            except (ProcessLookupError, OSError):
                pass


def _no_pump() -> WrongStateError:
    # Defensive stub: an arm cell has no fluidics.
    return WrongStateError("arm cell has no pump", command="pump")


def _no_balance() -> WrongStateError:
    # The Phase's single balance lives on cell4.
    return WrongStateError("arm cell has no balance", command="balance")


def _no_stage() -> WrongStateError:
    # Motion here is the arm action set — gantry/linear/zstage all carry
    # a Cartesian target this cell cannot honour.
    return WrongStateError(
        "arm cell has no gantry/linear/Z stage; use the arm actions",
        command="stage",
    )


def _no_thermal() -> WrongStateError:
    # Hotplate and lamp belong to Cell 5.
    return WrongStateError(
        "arm cell has no hotplate or lamp", command="thermal"
    )


@dataclass
class _Pending:
    """What a launched replay expects to have happened when it ends."""

    repo_id: str
    episode: int
    frames: int
    fps: int
    timeout_s: float
    last_action_deg: list[float] = field(default_factory=list)
    started_at: float = 0.0


@dataclass
class _ProgramPending:
    """What a launched Lua job program is being waited on for.

    Deliberately thinner than :class:`_Pending`: the cell never reads the
    script, so there is no expected end pose to compare against — see
    ``await_program``.
    """

    name: str
    timeout_s: float
    started_at: float = 0.0
    last_line: int | None = None


class ArmReplayCell(Cell):
    """cell6 / cell7 = one FR5 arm replaying episodes, behind ``Cell``."""

    def __init__(
        self,
        rpc,
        config: ArmReplayConfig,
        *,
        runner=None,
        reconnect=None,
    ) -> None:
        self._rpc = rpc
        self._cfg = config
        self._runner = runner if runner is not None else LerobotRunner(config)
        # How to rebuild the SDK session after the replay subprocess
        # released it. Injectable because it is a real TCP connect: a
        # test that fell through to it would dial the bench arm.
        self._reconnect = reconnect or (
            lambda: self._connect(self._cfg.ip_address)
        )
        self._proc = None
        self._pending: _Pending | None = None
        self._last_replay: dict | None = None
        #: The Lua job program this cell launched and has not collected.
        self._program: _ProgramPending | None = None
        self._last_program: dict | None = None
        #: Guards the *launch*, not the run. ``stop()`` never takes it —
        #: it reaches ``self._proc`` directly (spec §6.1).
        self._replay_lock = threading.Lock()
        #: Last joints read from the SDK. ``status()`` serves this while
        #: the subprocess owns the controller (spec Q1).
        self._joints_deg: list[float] = [0.0] * JOINT_COUNT

    @property
    def config(self) -> ArmReplayConfig:
        return self._cfg

    @classmethod
    def open(cls, config: ArmReplayConfig) -> ArmReplayCell:
        """Connect to the controller and check the lerobot side exists.

        Args:
            config: This cell's arm config.

        Returns:
            The opened cell.

        Raises:
            TransportError: The controller did not answer a joint read,
                or the configured conda/lerobot paths do not exist. Any
                of those means the cell cannot do its one job, so the
                server must fail to start rather than 503 later.
        """
        for label, path in (
            ("conda_sh", Path(config.conda_sh)),
            ("lerobot_root", Path(config.lerobot_root)),
        ):
            if not path.exists():
                raise TransportError(
                    f"[arm] {label} does not exist: {path}", command="arm"
                )
        cell = cls(cls._connect(config.ip_address), config)
        # One real read, so a server that started is a server that can
        # see the arm — not one that will find out on the first request.
        cell._joints_deg = cell._read_joints()
        return cell

    @staticmethod
    def _connect(ip_address: str):
        """Open an XMLRPC session to the controller."""
        from external.FR5Controller.fairino.Robot import RPC

        try:
            return RPC(ip_address)
        except OSError as exc:
            raise TransportError(
                f"cannot reach the FR5 controller at {ip_address}: {exc}",
                command="arm",
            ) from exc

    # ── SDK session hand-over (spec Q1) ─────────────────────────────────
    def _release_rpc(self) -> None:
        """Drop this process's SDK session before the subprocess starts.

        Best effort on purpose: the vendored ``CloseRPC()`` reads
        ``self.thread``, which its ``__init__`` never assigns, so it
        raises ``AttributeError`` every time. Letting that propagate
        would turn every replay launch into a 500.
        """
        rpc, self._rpc = self._rpc, None
        if rpc is None:
            return
        try:
            rpc.CloseRPC()
        except Exception as exc:  # noqa: BLE001 — see the docstring
            print(f"warning: CloseRPC failed: {exc}", file=sys.stderr)

    def _ensure_rpc(self):
        """Rebuild the SDK session after the subprocess released it."""
        if self._rpc is None:
            self._rpc = self._reconnect()
        return self._rpc

    def _read_joints(self) -> list[float]:
        """Read the six joint angles in degrees, from the encoder.

        Goes through the controller's XMLRPC call on port 20003, not the
        SDK's ``GetActualJointPosDegree()`` wrapper. Measured on this
        bench 2026-08-11: the wrapper reads *only* the port-20004
        real-time state struct (its XMLRPC branch is commented out
        upstream), and cell6's controller does not serve 20004 — so the
        wrapper raises ``TypeError: '_ctypes.CField' object is not
        subscriptable`` there while the XMLRPC call answers on both arms.
        The wrapper also hard-codes ``return 0, ...``, so it can never
        report a failed read as an error code; the XMLRPC call returns
        the controller's real one. Same path lerobot's follower falls
        back to (``_use_xmlrpc_reads``).

        Returns:
            Six degrees, joint 1 first.

        Raises:
            TransportError: The controller did not answer, or answered
                with an error code. Never a cached value dressed up as a
                reading (LearnedPatterns #15).
        """
        rpc = self._ensure_rpc()
        try:
            result = rpc.robot.GetActualJointPosDegree(_JOINT_READ_NONBLOCKING)
        except (
            OSError,
            AttributeError,
            TypeError,
            xmlrpc.client.Fault,
        ) as exc:
            raise TransportError(str(exc), command="arm") from exc
        if not result or len(result) <= JOINT_COUNT:
            raise TransportError(
                f"joint read returned {result!r}", command="arm"
            )
        if int(result[0]) != _SDK_OK:
            raise TransportError(
                f"joint read failed with SDK error {result[0]}",
                command="arm",
            )
        self._joints_deg = [float(v) for v in result[1 : JOINT_COUNT + 1]]
        return list(self._joints_deg)

    def _replay_running(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    # ── Discovery ───────────────────────────────────────────────────────
    def diagnose(self) -> dict:
        reachable = True
        detail: str | None = None
        fault: list[int] | None = None
        if self._replay_running():
            # Do not probe the controller while the subprocess owns the
            # servo session; report what the last read said.
            detail = "replay in flight; not probed"
        elif self._program_running():
            detail = "job program in flight; not probed"
        else:
            try:
                self._read_joints()
                # Reachable is not the same question as ready. Measured
                # 2026-08-11: this arm answered every read while holding
                # main=1 sub=1 and refusing MoveJ with SDK error 154, so
                # a diagnose that reported only reachability said
                # "healthy" about an arm that could not move.
                fault = list(self._robot_error())
            except TransportError as exc:
                reachable = False
                detail = str(exc)
        return {
            "arm": {
                "ok": reachable,
                "robot_id": self._cfg.robot_id,
                "ip": self._cfg.ip_address,
                "reachable": reachable,
                "gripper": self._cfg.gripper_enabled,
                "joints_deg": list(self._joints_deg),
                # `ok` stays "can we talk to it" so /v1/health keeps one
                # meaning; `ready` is "will it accept a move".
                "fault": fault,
                "ready": reachable and fault == [0, 0],
                "detail": detail,
            },
            "replay": {
                "running": self._replay_running(),
                "last": self._last_replay,
                "env": self._cfg.conda_env,
                "allowed_repo_prefixes": list(self._cfg.allowed_repo_prefixes),
            },
            "program": {
                "running": self._program_running(),
                "name": self._program.name if self._program else None,
                "last": self._last_program,
                "dir": self._cfg.program_dir,
                "allowed": list(self._cfg.allowed_programs),
            },
            # Absent by design, not faulted — same convention as the
            # balance on Cell 5, so the UI greys these out.
            "pump": {"present": False, "ok": True},
            "balance": {"present": False, "ok": True},
            "stage": {"present": False, "ok": True},
            "ok_to_initialize": True,
            "versions": {},
        }

    def status(self) -> dict:
        replaying = self._replay_running()
        # A Lua program runs *on the controller*, so unlike a replay it
        # does not hold this process's SDK session — the encoder can be
        # read right through it, and that is the more useful answer.
        joints = list(self._joints_deg) if replaying else self._read_joints()
        return {
            "weight_g": 0.0,  # no balance on an arm cell
            "valve": "-",  # no pump
            "plunger_uL": 0.0,
            "stage_x_mm": None,  # no Cartesian stage; the arm is joints
            "stage_z_mm": None,
            "busy": replaying or self._program_running(),
            "error": None,
            "joints_deg": joints,
            "last_replay": self._last_replay,
            "last_program": self._last_program,
        }

    # ── Arm action set ──────────────────────────────────────────────────
    def _check_repo(self, repo_id: str) -> None:
        """Spec §6.2 step 1 / D6: the allow-list gate."""
        if not repo_id.startswith(tuple(self._cfg.allowed_repo_prefixes)):
            allowed = ", ".join(self._cfg.allowed_repo_prefixes)
            raise InvalidArgError(
                f"repo_id {repo_id!r} is outside the allowed prefixes "
                f"({allowed})",
                command="arm",
            )

    def _meta(self, repo_id: str, episode: int) -> dict:
        """Validate the request against the dataset (spec §6.2 steps 1–2).

        Args:
            repo_id: HuggingFace dataset id.
            episode: Episode index.

        Returns:
            The probe's metadata dict.

        Raises:
            InvalidArgError: Prefix not allowed, or episode out of range.
            WrongStateError: ``offline_only`` and the dataset is not
                cached — a 409, not a 400: the request is well-formed,
                the bench is just not allowed to fetch it.
        """
        self._check_repo(repo_id)
        meta = self._runner.probe(repo_id, episode)
        if not meta.get("cached", False):
            raise WrongStateError(
                f"{repo_id} is not cached and offline_only is set",
                command="arm",
            )
        total = int(meta.get("total_episodes", 0))
        # Checked here as well as in the probe: the probe answers "no
        # frames" for an episode it could not describe, but that is one
        # bit of information for two different failures, and only the
        # explicit range check can say which.
        if not 0 <= episode < total or "frames" not in meta:
            raise InvalidArgError(
                f"episode {episode} is out of range; {repo_id} has "
                f"{total} episode(s)",
                command="arm",
            )
        return meta

    def _robot_error(self) -> tuple[int, int]:
        """The controller's latched fault, as ``(main_code, sub_code)``.

        Raw XMLRPC: the SDK's ``GetRobotErrorCode()`` reads the
        port-20004 struct that cell6 does not serve (LearnedPatterns
        #40), so it cannot answer there at all.

        Returns:
            ``(0, 0)`` when the controller reports no fault.

        Raises:
            TransportError: The controller could not be reached.
        """
        rpc = self._ensure_rpc()
        try:
            result = rpc.robot.GetRobotErrorCode()
        except (OSError, AttributeError, xmlrpc.client.Fault) as exc:
            raise TransportError(str(exc), command="arm") from exc
        if not result or len(result) < 3:
            raise TransportError(
                f"error-code read returned {result!r}", command="arm"
            )
        return int(result[1]), int(result[2])

    def _require_ready(self) -> None:
        """Refuse to command motion while a fault is latched.

        Measured 2026-08-11: the first real ``arm/jog_joint`` came back
        ``MoveJ rejected with SDK error 154`` while
        ``GetRobotErrorCode()`` reported ``main=1 sub=1``. The arm was
        faulted and de-energised, and the SDK's own error number says
        nothing about that. So the check is made here, where the answer
        can name the cause and the cure.

        Deliberately NOT a silent reset. lerobot's follower clears and
        re-enables inside ``connect()``, which is fine for a teleop
        session and wrong for this cell: if the fault is a joint that hit
        a limit, clearing it and re-issuing the same move repeats the
        crash. ``prepare_arm`` (``POST /v1/arm/enable``) is the operator's
        explicit "yes, clear it".

        Raises:
            WrongStateError: A fault is latched.
        """
        main, sub = self._robot_error()
        if main != 0 or sub != 0:
            raise WrongStateError(
                f"the controller has a latched fault (main={main}, "
                f"sub={sub}) and will refuse to move — MoveJ answers SDK "
                "error 154. Check why it faulted, then clear it "
                "deliberately with POST /v1/arm/enable.",
                command="arm",
            )

    def prepare_arm(self) -> dict:
        """Clear faults, energise the servos, select automatic mode.

        The sequence lerobot's follower runs at ``connect()``, minus the
        servo session it needs and this cell does not:
        ``ResetAllError → RobotEnable(1) → Mode(0)``. ``Mode(0)`` is
        automatic mode, ``RobotEnable(1)`` energises.

        Its own action rather than something ``open()`` or ``jog_joint``
        does quietly, because it is the step that discards evidence: the
        response therefore reports the fault codes seen **before** the
        reset, so a run's log records what was wrong.

        This energises a robot arm. Nothing moves, but holding torque
        comes on and the next motion command will be accepted.

        Args:
            None.

        Returns:
            ``error_before``, ``error_after``, ``ready``, ``joints_deg``.

        Raises:
            DeviceFaultError: The controller refused a step, or a fault
                survived the reset.
            TransportError: The controller could not be reached.
        """
        if self._replay_running():
            raise WrongStateError("a replay owns the controller", command="arm")
        before = self._robot_error()
        rpc = self._ensure_rpc()
        # Raw XMLRPC throughout: every one of these wrappers opens with
        # the unbounded reconnect_flag spin (LearnedPatterns #41).
        for label, call, arg in (
            ("ResetAllError", "ResetAllError", None),
            ("RobotEnable", "RobotEnable", 1),
            ("Mode", "Mode", 0),  # 0 = automatic, 1 = manual
        ):
            try:
                method = getattr(rpc.robot, call)
                code = method() if arg is None else method(arg)
            except (OSError, xmlrpc.client.Fault) as exc:
                raise TransportError(str(exc), command="arm") from exc
            if int(code) != _SDK_OK:
                raise DeviceFaultError(
                    f"{label} failed with SDK error {code}", command="arm"
                )
            time.sleep(ENABLE_STEP_SETTLE_S)
        after = self._robot_error()
        if after != (0, 0):
            raise DeviceFaultError(
                f"a fault survived the reset (main={after[0]}, "
                f"sub={after[1]}); this needs the teach pendant",
                command="arm",
            )
        # Read it again after a settle. Measured 2026-08-11: on cell6 the
        # fault re-latches, and a single read taken straight after
        # ResetAllError saw [0, 0] on an arm that then refused MoveJ and
        # was back at [1, 1] moments later. One read is a snapshot of a
        # value that moves.
        time.sleep(FAULT_RELATCH_WATCH_S)
        settled = self._robot_error()
        if settled != (0, 0):
            raise DeviceFaultError(
                f"the fault came back {FAULT_RELATCH_WATCH_S:.1f} s after "
                f"the reset (main={settled[0]}, sub={settled[1]}) — the "
                "controller is not merely latched, it is faulting. Teach "
                "pendant.",
                command="arm",
            )
        return {
            "error_before": list(before),
            "error_after": list(after),
            "error_settled": list(settled),
            # NOT "ready". Measured 2026-08-11: cell6 reported (0, 0)
            # here and still answered `MoveJ ... SDK error 154`, so a
            # cleared main/sub code does NOT mean the controller will
            # accept motion. This field says only what was done.
            "fault_cleared": True,
            "joints_deg": self._read_joints(),
        }

    def jog_joint(
        self, joint: int, delta_deg: float, *, speed_pct: float | None = None
    ) -> dict:
        """Nudge ONE joint by a RELATIVE amount, and verify on the encoder.

        Exists so the spec's T1 acceptance test (joint 1 +10 deg, three
        rounds) can be a scenario instead of a standalone script. It is a
        deliberate narrowing of spec D8, not a pose interface: a request
        names one axis and a bounded increment, never a configuration.

        The response carries ``achieved_delta_deg`` — measured, not
        commanded — and ``max_other_axis_delta_deg``, so a scenario
        asserts the *increment* and the *coupling* rather than an
        endpoint. LearnedPatterns #33: an endpoint assert passes on an arm
        that never moved but happened to start there.

        Args:
            joint: 1-based joint index, 1..6.
            delta_deg: Relative degrees, bounded by ``MAX_JOG_DEG``.
            speed_pct: MoveJ velocity; defaults to the config's
                ``jog_speed_pct`` and is capped at ``MAX_JOG_SPEED_PCT``.

        Returns:
            ``joint``, ``before_deg``, ``after_deg``,
            ``target_delta_deg``, ``achieved_delta_deg``,
            ``max_other_axis_delta_deg``, ``joints_deg``.

        Raises:
            InvalidArgError: Joint out of range, or the step exceeds the
                cap.
            WrongStateError: A replay owns the controller.
            DeviceFaultError: The controller rejected the motion.
            TransportError: The encoder could not be read before or after.
        """
        if not 1 <= joint <= JOINT_COUNT:
            raise InvalidArgError(
                f"joint must be 1..{JOINT_COUNT}, got {joint}", command="arm"
            )
        if abs(delta_deg) > MAX_JOG_DEG:
            raise InvalidArgError(
                f"|delta_deg| {abs(delta_deg):.1f} exceeds this cell's jog "
                f"cap of {MAX_JOG_DEG:.1f} deg",
                command="arm",
            )
        if self._replay_running():
            raise WrongStateError(
                "a replay owns the controller; POST /v1/stop first",
                command="arm",
            )
        speed = min(
            float(speed_pct or self._cfg.jog_speed_pct), MAX_JOG_SPEED_PCT
        )
        self._require_ready()
        before = self._read_joints()
        target = list(before)
        target[joint - 1] = before[joint - 1] + float(delta_deg)
        self._move_j(target, speed_pct=speed)
        # MoveJ answers on acceptance, so read the arrival, not the start.
        time.sleep(JOG_SETTLE_S)
        after = self._read_joints()
        achieved = after[joint - 1] - before[joint - 1]
        others = [
            abs(after[i] - before[i])
            for i in range(JOINT_COUNT)
            if i != joint - 1
        ]
        return {
            "joint": joint,
            "before_deg": before,
            "after_deg": after,
            "target_delta_deg": float(delta_deg),
            "achieved_delta_deg": achieved,
            "max_other_axis_delta_deg": max(others) if others else 0.0,
            "joints_deg": after,
        }

    def prefetch_episode(self, repo_id: str, episode: int) -> dict:
        """Download + describe an episode without moving anything (D7).

        Args:
            repo_id: HuggingFace dataset id.
            episode: Episode index.

        Returns:
            ``cached``, ``frames``, ``fps``, ``duration_s``.
        """
        meta = self._meta(repo_id, episode)
        frames, fps = int(meta["frames"]), int(meta["fps"])
        return {
            "cached": True,
            "frames": frames,
            "fps": fps,
            "duration_s": frames / fps if fps else 0.0,
        }

    def start_replay(
        self, repo_id: str, episode: int, fps: int | None = None
    ) -> dict:
        """Validate, approach the first frame, and launch the replay.

        Returns quickly — the episode plays out under ``await_replay``,
        which the server calls *without* the command lock so a concurrent
        ``POST /v1/stop`` is not queued behind the motion (spec §6.1).

        Args:
            repo_id: HuggingFace dataset id.
            episode: Episode index.
            fps: Replay rate. ``None`` adopts the recorded rate; any
                other value must equal it (spec D5).

        Returns:
            ``frames``, ``fps``, ``timeout_s``.

        Raises:
            InvalidArgError: Bad prefix, episode, or fps.
            WrongStateError: A replay is already running.
            DeviceFaultError: The start-pose approach was rejected.
        """
        if not self._replay_lock.acquire(blocking=False):
            raise WrongStateError("a replay is already starting", command="arm")
        try:
            if self._replay_running():
                raise WrongStateError(
                    "a replay is already running; POST /v1/stop first",
                    command="arm",
                )
            if self._program is not None:
                # A servo session and a job program are two different
                # owners of the same axes; never both at once.
                raise WrongStateError(
                    f"job program {self._program.name} is running; "
                    "POST /v1/stop first",
                    command="arm",
                )
            meta = self._meta(repo_id, episode)
            frames, recorded_fps = int(meta["frames"]), int(meta["fps"])
            if fps is not None and int(fps) != recorded_fps:
                raise InvalidArgError(
                    f"fps {fps} does not match the recorded "
                    f"{recorded_fps}; re-timed replay compresses the "
                    "ServoJ interval and is refused",
                    command="arm",
                )
            timeout_s = replay_timeout_s(
                frames,
                recorded_fps,
                factor=self._cfg.replay_timeout_factor,
                ceiling=self._cfg.max_replay_s,
            )
            self._approach_start(meta.get("first_action_deg") or [])
            # The follower's connect() takes an exclusive servo session,
            # so this process must be off the controller first (Q1).
            self._release_rpc()
            argv = build_replay_argv(
                self._cfg,
                repo_id=repo_id,
                episode=episode,
                fps=recorded_fps,
            )
            self._proc = self._runner.spawn(argv)
            self._pending = _Pending(
                repo_id=repo_id,
                episode=episode,
                frames=frames,
                fps=recorded_fps,
                timeout_s=timeout_s,
                last_action_deg=[
                    float(v) for v in (meta.get("last_action_deg") or [])
                ],
                started_at=time.monotonic(),
            )
        finally:
            self._replay_lock.release()
        return {"frames": frames, "fps": recorded_fps, "timeout_s": timeout_s}

    def _approach_start(self, first_frame: list[float]) -> None:
        """MoveJ to the episode's first pose when we are far from it.

        Spec §6.2 step 4. The fork's follower ramps toward each ServoJ
        target at ``max_servo_speed`` (90 deg/s) rather than refusing a
        jump, so a replay started from the wrong pose does not fail — it
        lunges. Hence the tolerance check here.

        Args:
            first_frame: The episode's first recorded joint vector.
        """
        if len(first_frame) < JOINT_COUNT:
            return
        target = [float(v) for v in first_frame[:JOINT_COUNT]]
        self._require_ready()
        here = self._read_joints()
        drift = max(abs(a - b) for a, b in zip(here, target))
        if drift <= self._cfg.start_pose_tolerance_deg:
            return
        if drift > MAX_START_APPROACH_DEG:
            # Refuse rather than swing there. See MAX_START_APPROACH_DEG:
            # the alternative is a replay request answering with a large
            # unnamed multi-joint move.
            worst = max(
                range(JOINT_COUNT), key=lambda i: abs(here[i] - target[i])
            )
            raise WrongStateError(
                f"the arm is {drift:.1f} deg from this episode's first "
                f"frame (worst: joint {worst + 1}, {here[worst]:+.1f} vs "
                f"{target[worst]:+.1f}), over the "
                f"{MAX_START_APPROACH_DEG:.0f} deg approach limit. Jog it "
                f"near {[round(v, 1) for v in target]} first — a replay "
                "must not begin with a large unrequested move.",
                command="arm",
            )
        self._move_j(target)
        self._joints_deg = list(target)

    def _move_j(
        self, target: list[float], *, speed_pct: float | None = None
    ) -> None:
        """MoveJ over XMLRPC, bypassing the SDK wrapper.

        NOT ``rpc.MoveJ(...)``. Measured 2026-08-11: the wrapper opens
        with ``while self.reconnect_flag: time.sleep(0.1)``, and
        ``reconnect_flag`` is a *class* attribute the SDK's state thread
        sets when the port-20004 stream drops. cell6's controller does
        not feed that stream, so the flag latches and the wrapper spins
        forever — a ``POST /v1/stop`` against this cell hung indefinitely
        that way before this change. ``StopMotion`` and ``ResetAllError``
        carry the same spin; the raw XMLRPC calls are bounded by the
        socket timeout and answered in ~17 ms on both arms.

        The wrapper's one useful step is reproduced: MoveJ wants a
        Cartesian ``desc_pos`` alongside the joint target, and the
        wrapper derives it with ``GetForwardKin`` when the caller passes
        zeros (measured 2–3 ms on both controllers).

        Args:
            target: Six joint angles in degrees.

        Raises:
            DeviceFaultError: The controller rejected the motion.
            TransportError: The controller could not be reached.
        """
        rpc = self._ensure_rpc()
        vel = min(
            float(self._cfg.jog_speed_pct if speed_pct is None else speed_pct),
            MAX_JOG_SPEED_PCT,
        )
        try:
            # The tool frame MoveJ is told about must be the one
            # GetForwardKin answered in, or the joint target and the
            # Cartesian desc_pos describe points in different frames and
            # the controller rejects the pair. Measured 2026-08-11: this
            # is the whole of "SDK error 154". cell6's active tool is 1
            # and cell7's is 0, so a hard-coded 0 worked on one arm and
            # failed on the other with an identical code path — which is
            # also why the bench's older FR5Controller.py hard-codes
            # tool=1 (it was written for cell6).
            tool = rpc.robot.GetActualTCPNum(1)
            if int(tool[0]) != _SDK_OK:
                raise DeviceFaultError(
                    f"cannot read the active tool number (SDK error "
                    f"{tool[0]}); refusing to guess it",
                    command="arm",
                )
            tool_num = int(tool[1])
            solved = rpc.robot.GetForwardKin(target)
            if int(solved[0]) != _SDK_OK:
                raise DeviceFaultError(
                    f"GetForwardKin failed with SDK error {solved[0]}",
                    command="arm",
                )
            desc_pos = [float(v) for v in solved[1:7]]
            error = rpc.robot.MoveJ(
                target,
                desc_pos,
                tool_num,  # the ACTIVE tool, never a hard-coded 0
                0,  # user frame; GetActualWObjNum reads 0 on both arms
                vel,
                0.0,  # acc
                100.0,  # ovl
                [0.0, 0.0, 0.0, 0.0],  # exaxis_pos
                -1.0,  # blendT
                0,  # offset_flag
                [0.0] * JOINT_COUNT,  # offset_pos
            )
        except (OSError, xmlrpc.client.Fault) as exc:
            raise TransportError(str(exc), command="arm") from exc
        if int(error) != _SDK_OK:
            raise DeviceFaultError(
                f"MoveJ rejected with SDK error {error}", command="arm"
            )

    def await_replay(self) -> dict:
        """Wait out the launched replay and verify it on the encoder.

        Args:
            None.

        Returns:
            ``completed``, ``frames``, ``elapsed_s``,
            ``final_joint_error_deg``, ``joints_deg``.

        Raises:
            WrongStateError: Nothing was launched.
            CellTimeoutError: The subprocess outlived its timeout; it is
                killed before this is raised.
            DeviceFaultError: Non-zero exit, or the arm ended nowhere
                near the episode's last frame.
            TransportError: The encoder could not be re-read afterwards —
                which means the run is unverified, so it is not a 200.
        """
        proc, pending = self._proc, self._pending
        if proc is None or pending is None:
            raise WrongStateError("no replay in flight", command="arm")
        try:
            code = proc.wait(timeout=pending.timeout_s)
        except subprocess.TimeoutExpired as exc:
            self._halt_process(proc)
            self._finish(pending, "timeout")
            raise CellTimeoutError(
                f"replay of {pending.repo_id} episode {pending.episode} "
                f"exceeded {pending.timeout_s:.1f} s",
                command="arm",
            ) from exc
        elapsed = time.monotonic() - pending.started_at
        self._proc = None
        if code != _SDK_OK:
            self._finish(pending, f"exit {code}")
            raise DeviceFaultError(
                f"lerobot-replay exited with code {code}", command="arm"
            )
        # Spec §6.2 step 7 / LearnedPatterns #24: a 200 has to mean the
        # encoder was read, not that the command was accepted.
        joints = self._read_joints()
        error_deg = self._pose_error(joints, pending.last_action_deg)
        runaway = POSE_RUNAWAY_FACTOR * self._cfg.final_pose_tolerance_deg
        if error_deg is not None and error_deg > runaway:
            self._finish(pending, f"pose error {error_deg:.2f} deg")
            raise DeviceFaultError(
                f"replay ended {error_deg:.2f} deg from the episode's last "
                f"frame (runaway limit {runaway:.2f} deg)",
                command="arm",
            )
        result = {
            "completed": True,
            "frames": pending.frames,
            "fps": pending.fps,
            "elapsed_s": elapsed,
            "final_joint_error_deg": error_deg,
            "joints_deg": joints,
        }
        self._finish(pending, "completed", elapsed=elapsed)
        return result

    @staticmethod
    def _pose_error(
        joints: list[float], last_frame: list[float]
    ) -> float | None:
        """Largest per-axis gap to the episode's last recorded frame.

        Args:
            joints: Encoder reading, degrees.
            last_frame: The episode's last recorded joint vector.

        Returns:
            The max absolute difference, or None when the dataset did not
            expose a last frame — reporting 0.0 there would be a
            fabricated pass.
        """
        if len(last_frame) < JOINT_COUNT or len(joints) < JOINT_COUNT:
            return None
        return max(
            abs(a - b)
            for a, b in zip(joints[:JOINT_COUNT], last_frame[:JOINT_COUNT])
        )

    def _finish(
        self, pending: _Pending, outcome: str, *, elapsed: float | None = None
    ) -> None:
        self._proc = None
        self._pending = None
        self._last_replay = {
            "repo_id": pending.repo_id,
            "episode": pending.episode,
            "frames": pending.frames,
            "fps": pending.fps,
            "outcome": outcome,
            "elapsed_s": elapsed,
        }

    def _halt_process(self, proc) -> str:
        """SIGTERM, then SIGKILL after the grace period (spec §6.1).

        Args:
            proc: The replay subprocess.

        Returns:
            ``"terminated"``, ``"killed"``, or ``"idle"``.
        """
        if proc is None or proc.poll() is not None:
            return "idle"
        self._runner.terminate(proc)
        try:
            proc.wait(timeout=TERMINATE_GRACE_S)
            return "terminated"
        except subprocess.TimeoutExpired:
            self._runner.kill(proc)
            try:
                proc.wait(timeout=TERMINATE_GRACE_S)
            except subprocess.TimeoutExpired:
                pass
            return "killed"

    # ── Lua job programs: the controller-side motion path ───────────────
    #
    # Everything below talks to the controller through the *raw* XMLRPC
    # proxy, never the SDK's Program* wrappers. Two reasons, both read
    # out of the vendored SDK rather than guessed:
    #
    # * ``ProgramLoad``/``ProgramRun``/``ProgramStop``/``GetCurrentLine``/
    #   ``GetLoadedProgram`` all open with
    #   ``while self.reconnect_flag: time.sleep(0.1)`` on a *class*
    #   attribute the state thread latches when the port-20004 stream
    #   drops — the same unbounded spin that hung ``POST /v1/stop``
    #   (LearnedPatterns #41). ``ProgramRun`` additionally gates on
    #   ``GetSafetyCode()``, which reads that same dead struct.
    # * ``GetProgramState()`` is not implemented at all: its XMLRPC body
    #   is commented out upstream and it returns
    #   ``self.robot_state_pkg.robot_state`` — the port-20004 struct
    #   cell6 never serves (LearnedPatterns #40).
    def _check_program(self, name: str) -> str:
        """Validate a requested program name and return it.

        Args:
            name: The bare file name, e.g. ``"test1.lua"``.

        Returns:
            The name, unchanged.

        Raises:
            InvalidArgError: Wrong extension, a path separator (a request
                must not be able to steer ``ProgramLoad`` out of
                ``program_dir``), or outside ``allowed_programs``.
        """
        if not name or not name.endswith(PROGRAM_SUFFIX):
            raise InvalidArgError(
                f"program name must end in {PROGRAM_SUFFIX}: {name!r}",
                command="arm",
            )
        if any(token in name for token in ("/", "\\", "..")):
            raise InvalidArgError(
                f"program name must be a bare file name, not a path: {name!r}",
                command="arm",
            )
        allowed = self._cfg.allowed_programs
        if allowed and name not in allowed:
            raise InvalidArgError(
                f"{name!r} is not in allowed_programs ({', '.join(allowed)})",
                command="arm",
            )
        return name

    def _program_state(self) -> int:
        """The controller's job-program state (1 / 2 / 3).

        Returns:
            ``PROGRAM_STOPPED``, ``PROGRAM_RUNNING`` or
            ``PROGRAM_PAUSED``.

        Raises:
            TransportError: The controller did not answer, or answered
                with an error code or an unreadable shape.
        """
        rpc = self._ensure_rpc()
        try:
            result = rpc.robot.GetProgramState()
        except (
            OSError,
            AttributeError,
            TypeError,
            xmlrpc.client.Fault,
        ) as exc:
            raise TransportError(str(exc), command="arm") from exc
        if not isinstance(result, (list, tuple)) or len(result) < 2:
            raise TransportError(
                f"program-state read returned {result!r}", command="arm"
            )
        if int(result[0]) != _SDK_OK:
            raise TransportError(
                f"program-state read failed with SDK error {result[0]}",
                command="arm",
            )
        return int(result[1])

    def _current_line(self) -> int | None:
        """The line the job program is executing, or None if unreadable.

        Progress is the *only* feedback a running Lua program offers, and
        it is strictly informational here — a failed read must not fail
        the run, so this swallows rather than raises.
        """
        try:
            rpc = self._ensure_rpc()
            result = rpc.robot.GetCurrentLine()
        except Exception:  # noqa: BLE001 — progress is not a verdict
            return None
        if not isinstance(result, (list, tuple)) or len(result) < 2:
            return None
        if int(result[0]) != _SDK_OK:
            return None
        return int(result[1])

    def _program_running(self) -> bool:
        return self._program is not None

    def start_program(self, name: str) -> dict:
        """Load a Lua job program on the controller and start it.

        The counterpart of ``start_replay`` for the *other* motion path:
        here the controller plans and executes, and this process only
        says "go". Returns as soon as the program is running; collect it
        with ``await_program``.

        **This moves the arm, and the cell cannot bound where.** A replay
        is checked against its first recorded frame
        (``MAX_START_APPROACH_DEG``); a Lua program's first ``PTP``/
        ``MoveJ`` goes from wherever the arm is to a taught point this
        process never reads, at whatever speed the script asks for. The
        operator gate and a clear frame are the guard, not this code.

        Args:
            name: Bare program file name on the controller, e.g.
                ``"test1.lua"``. Resolved under ``program_dir``.

        Returns:
            ``name``, the resolved controller ``path``, and ``timeout_s``.

        Raises:
            InvalidArgError: The name failed ``_check_program``.
            WrongStateError: A replay or a program is already running, or
                the controller has a latched fault.
            DeviceFaultError: The controller refused the load or the run,
                or loaded something other than what was asked for.
            TransportError: The controller could not be reached.
        """
        if not self._replay_lock.acquire(blocking=False):
            raise WrongStateError(
                "a motion command is already starting", command="arm"
            )
        try:
            if self._replay_running():
                raise WrongStateError(
                    "a replay is running; POST /v1/stop first", command="arm"
                )
            if self._program is not None:
                raise WrongStateError(
                    f"program {self._program.name} is already running; "
                    "POST /v1/stop first",
                    command="arm",
                )
            name = self._check_program(name)
            state = self._program_state()
            if state != PROGRAM_STOPPED:
                raise WrongStateError(
                    f"the controller already has a job program in state "
                    f"{state} (2=running, 3=paused); POST /v1/stop first",
                    command="arm",
                )
            self._require_ready()
            path = f"{self._cfg.program_dir}/{name}"
            rpc = self._ensure_rpc()
            self._run_sdk(rpc.robot.Mode, _MODE_AUTO, what="Mode(0)")
            self._run_sdk(rpc.robot.ProgramLoad, path, what="ProgramLoad")
            self._require_loaded(name, path)
            self._run_sdk(rpc.robot.ProgramRun, what="ProgramRun")
            self._program = _ProgramPending(
                name=name,
                timeout_s=float(self._cfg.max_program_s),
                started_at=time.monotonic(),
            )
        finally:
            self._replay_lock.release()
        return {
            "name": name,
            "path": path,
            "timeout_s": self._cfg.max_program_s,
        }

    @staticmethod
    def _run_sdk(call, *args, what: str) -> None:
        """Issue one raw SDK call that answers with a bare error code.

        Args:
            call: The bound raw XMLRPC method.
            *args: Its arguments.
            what: Name used in the error message.

        Raises:
            DeviceFaultError: The controller returned a non-zero code.
            TransportError: The call did not reach the controller.
        """
        try:
            error = call(*args)
        except (
            OSError,
            AttributeError,
            TypeError,
            xmlrpc.client.Fault,
        ) as exc:
            raise TransportError(str(exc), command="arm") from exc
        # Some of these answer with a bare int, others with a 1-tuple.
        if isinstance(error, (list, tuple)):
            error = error[0] if error else _SDK_OK
        if int(error) != _SDK_OK:
            raise DeviceFaultError(
                f"{what} failed with SDK error {error}", command="arm"
            )

    def _require_loaded(self, name: str, path: str) -> None:
        """Confirm the controller loaded the program that was asked for.

        ``ProgramLoad`` answering 0 says the call was accepted, not that
        this file is what is now loaded — the same distinction
        LearnedPatterns #24 draws for motion. If the controller will not
        say what it loaded, that is not treated as a failure; an
        unanswered getter is weaker evidence, not contrary evidence.

        Args:
            name: The requested bare file name.
            path: The full controller path that was loaded.

        Raises:
            DeviceFaultError: The controller reports a different program.
        """
        try:
            rpc = self._ensure_rpc()
            result = rpc.robot.GetLoadedProgram()
        except Exception:  # noqa: BLE001 — see the docstring
            return
        if not isinstance(result, (list, tuple)) or len(result) < 2:
            return
        if int(result[0]) != _SDK_OK:
            return
        loaded = str(result[1])
        if loaded and name not in loaded and path not in loaded:
            raise DeviceFaultError(
                f"asked the controller to load {path} but it reports "
                f"{loaded!r} loaded; refusing to run it",
                command="arm",
            )

    def await_program(self) -> dict:
        """Wait out the running job program and read the arm afterwards.

        What a 200 from here does and does not mean is worth stating,
        because it is weaker than ``await_replay``'s. It means: the
        controller went back to state 1, no fault was latched when it
        did, and the encoder answered afterwards. It does **not** mean
        the arm reached an intended pose — the cell never read the
        script, so it has no expected end pose to compare against.
        Judging the final ``joints_deg`` is the caller's job.

        A program parked in ``PROGRAM_PAUSED`` is not finished; it is
        waited on until the timeout, then stopped.

        Args:
            None.

        Returns:
            ``completed``, ``name``, ``elapsed_s``, ``last_line``,
            ``joints_deg``.

        Raises:
            WrongStateError: Nothing was launched.
            CellTimeoutError: The program outlived ``max_program_s``; it
                is stopped before this is raised.
            DeviceFaultError: The controller latched a fault by the end.
            TransportError: The encoder could not be re-read, which means
                the run is unverified — so it is not a 200.
        """
        pending = self._program
        if pending is None:
            raise WrongStateError("no program in flight", command="arm")
        while True:
            state = self._program_state()
            pending.last_line = self._current_line()
            if state == PROGRAM_STOPPED:
                break
            if time.monotonic() - pending.started_at > pending.timeout_s:
                self._halt_program()
                self._finish_program(pending, "timeout")
                raise CellTimeoutError(
                    f"program {pending.name} exceeded "
                    f"{pending.timeout_s:.1f} s",
                    command="arm",
                )
            time.sleep(self._cfg.program_poll_s)
        elapsed = time.monotonic() - pending.started_at
        main, sub = self._robot_error()
        if main != 0 or sub != 0:
            self._finish_program(pending, f"fault {main}/{sub}")
            raise DeviceFaultError(
                f"program {pending.name} ended with a latched fault "
                f"(main={main}, sub={sub})",
                command="arm",
            )
        # LearnedPatterns #24: a 200 has to mean the encoder was read.
        joints = self._read_joints()
        result = {
            "completed": True,
            "name": pending.name,
            "elapsed_s": elapsed,
            "last_line": pending.last_line,
            "joints_deg": joints,
        }
        self._finish_program(pending, "completed", elapsed=elapsed)
        return result

    def _finish_program(
        self,
        pending: _ProgramPending,
        outcome: str,
        *,
        elapsed: float | None = None,
    ) -> None:
        self._program = None
        self._last_program = {
            "name": pending.name,
            "outcome": outcome,
            "last_line": pending.last_line,
            "elapsed_s": elapsed,
        }

    def _halt_program(self) -> str:
        """Tell the controller to terminate the running job program.

        Returns:
            ``"stopped"``, ``"idle"``, or a short failure description.
            Never raises: this is reached from ``stop()``.
        """
        try:
            rpc = self._ensure_rpc()
            error = rpc.robot.ProgramStop()
        except Exception as exc:  # noqa: BLE001 — an e-stop never raises
            return f"failed: {exc}"
        if isinstance(error, (list, tuple)):
            error = error[0] if error else _SDK_OK
        if int(error) != _SDK_OK:
            return f"ProgramStop returned SDK error {error}"
        return "stopped"

    # ── Balance / pump / stage / thermal: not on an arm cell ────────────
    def tare(self) -> float:
        raise _no_balance()

    def calibrate(self) -> float:
        raise _no_balance()

    def read_weight(self) -> tuple[float, bool]:
        raise _no_balance()

    def set_ambient(self, level: str) -> str:
        raise _no_balance()

    def initialize(self, *, force: int = 2, ccw: bool = False) -> dict:
        raise _no_pump()

    def move_valve(self, port: int) -> str:
        raise _no_pump()

    def aspirate(self, target_uL: float) -> float:  # noqa: N803
        raise _no_pump()

    def dispense(self, target_uL: float = 0.0) -> float:  # noqa: N803
        raise _no_pump()

    def cycle(
        self,
        *,
        cycles: int,
        volume_uL: float,  # noqa: N803
        source_port: int,
        dispense_port: int,
    ) -> dict:
        raise _no_pump()

    def home_gantry(self) -> tuple[float, float]:
        raise _no_stage()

    def move_gantry(
        self, x_mm: float, z_mm: float, *, speed_pct: int, accel_pct: int
    ) -> tuple[float, float]:
        raise _no_stage()

    def home_linear(self) -> float:
        raise _no_stage()

    def move_linear(self, y_mm: float) -> float:
        raise _no_stage()

    def home_zstage(self) -> float:
        raise _no_stage()

    def move_zstage(
        self, z_mm: float, *, speed_pct: int, accel_pct: int
    ) -> float:
        raise _no_stage()

    def read_hotplate(self) -> dict:
        raise _no_thermal()

    def set_hotplate_temperature(self, celsius: float) -> float:
        raise _no_thermal()

    def set_hotplate_heater(self, *, enabled: bool) -> dict:
        raise _no_thermal()

    def set_hotplate_speed(self, rpm: float) -> float:
        raise _no_thermal()

    def set_hotplate_stirrer(self, *, enabled: bool) -> dict:
        raise _no_thermal()

    def read_lamp(self) -> dict:
        raise _no_thermal()

    def set_lamp(self, *, enabled: bool) -> dict:
        raise _no_thermal()

    # ── Safety / lifecycle ──────────────────────────────────────────────
    def stop(self) -> dict:
        """Kill both motion paths, then tell the controller to stop.

        Reaches ``self._proc`` directly and takes no lock, so it works
        while a replay is in flight — this cell is meant to be the
        counter-example to GAP-9, not another instance of it.

        Every step is attempted whatever the others do, and none raises:
        a partial stop is reported, because the caller of an e-stop needs
        the answer more than it needs an exception.

        Args:
            None.

        Returns:
            ``{"subprocess": ..., "program": ..., "sdk": ...}``, each a
            short outcome string.
        """
        result: dict[str, str] = {}
        proc = self._proc
        try:
            result["subprocess"] = self._halt_process(proc)
        except Exception as exc:  # noqa: BLE001 — an e-stop never raises
            result["subprocess"] = f"failed: {exc}"
        if self._pending is not None:
            self._finish(self._pending, "stopped")
        self._proc = None
        # Order matters, and for the same reason in both halves: whatever
        # is issuing motion has to be dead before StopMotion, or the next
        # ServoJ frame — or the next Lua line — restarts it.
        program = self._program
        result["program"] = (
            self._halt_program() if program is not None else "idle"
        )
        if program is not None:
            self._finish_program(program, "stopped")
        try:
            rpc = self._ensure_rpc()
            # Raw XMLRPC, not rpc.StopMotion(): the wrapper spins on the
            # latched reconnect_flag and never returns — see _move_j.
            error = rpc.robot.StopMotion()
            result["sdk"] = (
                "stopped"
                if int(error) == _SDK_OK
                else f"StopMotion returned SDK error {error}"
            )
        except Exception as exc:  # noqa: BLE001 — an e-stop never raises
            result["sdk"] = f"failed: {exc}"
        return result

    def close(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                self._halt_process(proc)
            except Exception:  # noqa: BLE001 — best-effort shutdown
                print("warning: replay subprocess kill failed", file=sys.stderr)
        self._proc = None
        if self._program is not None:
            # A job program outlives this process otherwise: it runs on
            # the controller, so shutting the server down does not end it.
            print(
                f"warning: stopping job program {self._program.name}: "
                f"{self._halt_program()}",
                file=sys.stderr,
            )
            self._program = None
        self._release_rpc()
