"""Real cell6 / cell7: one FR5 robot arm, behind the ``Cell`` interface.

Two identical cells, one shape, config apart: cell6 is the
**synthesis-stage** arm at 192.168.0.58, cell7 the **analysis-stage** arm
at 192.168.0.59. One arm per cell process, because the L2 lock is per
cell and an arm is exactly the unit that must be locked.

============  =============================================================
Motion        a ``.lua`` job program already on the controller
Reads         the fairino XMLRPC SDK (``external/FR5Controller/fairino``)
============  =============================================================

The arm action set is deliberately *not* a pose interface (ADDING_A_CELL.md
"a robot arm is just another action family"): a request names a program
file the controller already holds, and the controller's own interpreter
and planner execute it — ``Mode(0)`` → ``ProgramLoad`` → ``ProgramRun``,
and this process is done talking. Nothing streams a pose from L2. Design:
``docs/SPEC_ARM_LUA_PROGRAM.md``.

There was a second motion path here until 2026-08-11: ``lerobot-replay``
streaming a recorded dataset episode as ``ServoJ`` frames at 20 Hz. It
worked and was removed anyway — it kept this machine inside the arm's
real-time loop and could only express motions somebody had recorded.
``LearnedPatterns.md`` #47 has the full reasoning and, more usefully,
what it was *better* at; ``docs/SPEC_ARM_REPLAY_CELL.md`` keeps the
design. Both matter if VLA policy rollouts come back into scope, because
a learned policy emits poses continuously and cannot be pre-loaded onto a
controller.

Three properties of this cell that are not obvious from the interface:

- **A job program is two calls, not one** (``start_program`` +
  ``await_program``). The server holds one lock per command; a single
  blocking call would hold it for the whole program and ``POST /v1/stop``
  would queue behind the motion it is meant to abort. That is GAP-9
  (LearnedPatterns #9). The route takes the lock only for the launch and
  waits outside it.
- **Starting is not the same event as being started.** ``ProgramRun``
  answers on acceptance; the controller enters the running state ~160 ms
  later. Polling inside that window reads the pre-start idle as
  "finished" — which it once did, answering a 200 in 2.8 ms for a motion
  that then swung joint 1 through 91 degrees (LearnedPatterns #46).
- **A 200 means the encoder was read**, and no more than that. The cell
  never reads the script, so it has no expected end pose and does not
  claim the arm arrived anywhere (spec §6.4).

Hardware-verified at the bench, not in CI. The unit tests in
``claude_test/test_arm_cell.py`` drive fakes.
"""

from __future__ import annotations

import sys
import threading
import time
import xmlrpc.client
from dataclasses import dataclass

from .cell_protocol import (
    Cell,
    CellTimeoutError,
    DeviceFaultError,
    InvalidArgError,
    TransportError,
    WrongStateError,
)

#: Revolute joints on an FR5. The gripper is a separate channel and is
#: not one of these.
JOINT_COUNT = 6

#: fairino SDK error code meaning "OK".
_SDK_OK = 0

#: ``flag`` for the controller's joint read: 1 = non-blocking.
_JOINT_READ_NONBLOCKING = 1

#: Hard ceiling on one ``arm/jog_joint`` step, degrees. The spec's arm
#: action set is program-only and the +10 deg acceptance test was meant
#: to stay a bench script; running it from a scenario instead needs a
#: route, so this is the narrowest one that does the job — ONE
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

#: How long ``start_program`` waits for the controller to actually enter
#: the running state before calling the start a failure.
#:
#: ``ProgramRun()`` answers on acceptance, not on entry — the same
#: distinction as MoveJ (``JOG_SETTLE_S``). Measured on cell6
#: 2026-08-11 with ``/fruser/Test1.lua``: the call returned 0 at t+0.001 s
#: with the state still 1, and the state flipped to 2 at **t+0.160 s**.
#: Without this wait the first poll saw "stopped", concluded the program
#: had finished, and answered 200 in 2.8 ms — while the arm then swung
#: joint 1 through 91.3 degrees. That is LearnedPatterns #24 exactly: a
#: success reported for something that had not happened yet. 5 s is ~30x
#: the measured latency.
PROGRAM_START_GRACE_S = 5.0

#: Poll interval while waiting for the start, kept well under the
#: measured 160 ms so the transition is not stepped over.
PROGRAM_START_POLL_S = 0.05

#: Seconds to let a jog settle before re-reading. MoveJ answers on
#: acceptance, not on arrival, so a read taken immediately after it
#: reports the pose the arm is leaving (LearnedPatterns #24's shape).
JOG_SETTLE_S = 2.0


@dataclass(frozen=True, slots=True)
class ArmConfig:
    """Bench wiring for one arm (loaded from the cell6/cell7 TOML)."""

    #: Identifier written into logs and runlogs.
    robot_id: str = "fr5_a"
    #: The controller's address. Fixed network asset, so no VID:PID rule.
    #: cell6 and cell7 MUST differ: teach-pendant work, not something a
    #: second process can detect.
    ip_address: str = "192.168.0.58"
    gripper_enabled: bool = True
    # ── motion envelope ───────────────────────────────────────────────
    #: MoveJ velocity for a commissioning jog and for smoke_arm.py.
    jog_speed_pct: float = 10.0
    # ── the Lua job-program path ──────────────────────────────────────
    #: Where the controller keeps its ``.lua`` job programs.
    program_dir: str = DEFAULT_PROGRAM_DIR
    #: Same gate as ``allowed_repo_prefixes``, for programs. Empty means
    #: any ``*.lua`` on the controller; naming them here narrows a
    #: motion-bearing request to a reviewed list.
    allowed_programs: tuple[str, ...] = ()
    #: Timeout for one program run. It cannot be derived — the cell
    #: does not read the script, so there is nothing to compute from.
    max_program_s: float = 300.0
    #: How often ``await_program`` asks the controller whether it is
    #: still running.
    program_poll_s: float = 0.5

    @classmethod
    def from_toml(cls, table: dict) -> ArmConfig:
        """Build a config from the ``[arm]`` table of a cell TOML.

        Unknown keys are ignored rather than rejected, which matters on a
        bench mid-migration: a cell6.toml still carrying the removed
        replay keys (``conda_env``, ``max_replay_s``, …) starts instead of
        refusing to.

        Args:
            table: The parsed ``[arm]`` table.

        Returns:
            The config, with every field coerced to its declared type —
            TOML gives ``300`` for a float field as an int otherwise, and
            the config test asserts on the types.
        """
        return cls(
            robot_id=str(table.get("robot_id", "fr5_a")),
            ip_address=str(table.get("ip_address", "192.168.0.58")),
            gripper_enabled=bool(table.get("gripper_enabled", True)),
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
class _ProgramPending:
    """What a launched Lua job program is being waited on for.

    Deliberately thinner than :class:`_Pending`: the cell never reads the
    script, so there is no expected end pose to compare against — see
    ``await_program``.
    """

    name: str
    timeout_s: float
    started_at: float = 0.0
    #: The HIGHEST line seen, not the most recent one. The controller
    #: resets GetCurrentLine to 0 as the program ends (measured on cell6
    #: 2026-08-11: … 15, 18, then 0), so the last reading is always 0 and
    #: reporting it would say "never executed a line" about a program
    #: that ran for 23 s.
    last_line: int | None = None


class ArmCell(Cell):
    """cell6 / cell7 = one FR5 arm, behind the ``Cell`` interface."""

    def __init__(
        self,
        rpc,
        config: ArmConfig,
        *,
        reconnect=None,
    ) -> None:
        self._rpc = rpc
        self._cfg = config
        # How to rebuild the SDK session if it is ever dropped.
        # Injectable because it is a real TCP connect: a test that fell
        # through to it would dial the bench arm.
        self._reconnect = reconnect or (
            lambda: self._connect(self._cfg.ip_address)
        )
        #: The Lua job program this cell launched and has not collected.
        self._program: _ProgramPending | None = None
        self._last_program: dict | None = None
        #: Guards the *launch*, not the run. ``stop()`` never takes it —
        #: it reaches ``self._program`` directly, so an e-stop is not
        #: queued behind the motion it exists to abort (GAP-9).
        self._launch_lock = threading.Lock()
        #: Last joints read from the SDK.
        self._joints_deg: list[float] = [0.0] * JOINT_COUNT

    @property
    def config(self) -> ArmConfig:
        return self._cfg

    @classmethod
    def open(cls, config: ArmConfig) -> ArmCell:
        """Connect to the controller and prove it answers.

        Args:
            config: This cell's arm config.

        Returns:
            The opened cell.

        Raises:
            TransportError: The controller did not answer a joint read,
                which means the cell cannot do its one job — so the
                server must fail to start rather than 503 later.
        """
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
        would turn every session rebuild into a 500.
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

    # ── Discovery ───────────────────────────────────────────────────────
    def diagnose(self) -> dict:
        reachable = True
        detail: str | None = None
        fault: list[int] | None = None
        if self._program_running():
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
        # A Lua job program runs *on the controller* and does not hold
        # this process's SDK session, so the encoder can be read right
        # through one — joints_deg here is always a live reading.
        return {
            "weight_g": 0.0,  # no balance on an arm cell
            "valve": "-",  # no pump
            "plunger_uL": 0.0,
            "stage_x_mm": None,  # no Cartesian stage; the arm is joints
            "stage_z_mm": None,
            "busy": self._program_running(),
            "error": None,
            "joints_deg": self._read_joints(),
            "last_program": self._last_program,
        }

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
        if self._program_running():
            raise WrongStateError(
                "a job program owns the controller", command="arm"
            )
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
            WrongStateError: A job program owns the controller.
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
        if self._program_running():
            raise WrongStateError(
                "a job program owns the controller; POST /v1/stop first",
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

        The controller plans and executes; this process only says "go".
        Returns once the controller has actually entered the running
        state — not when it accepted the command, which is a different
        moment and the source of a real bug (see ``_confirm_started``).
        Collect it with ``await_program``.

        **This moves the arm, and the cell cannot bound where.** The
        program's first ``PTP``/``MoveJ`` goes from wherever the arm is
        to a taught point this process never reads, at whatever speed the
        script asks for. Nothing here can check that path, because
        nothing here parses the script. The operator gate and a clear
        frame are the guard, not this code.

        Args:
            name: Bare program file name on the controller, e.g.
                ``"test1.lua"``. Resolved under ``program_dir``.

        Returns:
            ``name``, the resolved controller ``path``, and ``timeout_s``.

        Raises:
            InvalidArgError: The name failed ``_check_program``.
            WrongStateError: A program is already running, or the
                controller has a latched fault.
            DeviceFaultError: The controller refused the load or the run,
                or loaded something other than what was asked for.
            TransportError: The controller could not be reached.
        """
        if not self._launch_lock.acquire(blocking=False):
            raise WrongStateError(
                "a motion command is already starting", command="arm"
            )
        try:
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
            started_at = time.monotonic()
            self._confirm_started(name)
            self._program = _ProgramPending(
                name=name,
                timeout_s=float(self._cfg.max_program_s),
                started_at=started_at,
            )
        finally:
            self._launch_lock.release()
        return {
            "name": name,
            "path": path,
            "timeout_s": self._cfg.max_program_s,
        }

    def _confirm_started(self, name: str) -> None:
        """Block until the controller reports the program running.

        ``ProgramRun()`` returns 0 on acceptance, and the state stays at
        ``PROGRAM_STOPPED`` for a beat afterwards — 160 ms on cell6,
        measured. Returning before that beat is what let ``arm/program``
        answer ``completed: true`` in 2.8 ms and then move the arm 91
        degrees (see ``PROGRAM_START_GRACE_S``).

        A program shorter than this wait cannot be told apart from one
        that never started: the controller resets both the state and the
        line when it finishes, leaving no trace to read afterwards. The
        cell errs toward reporting a fault rather than a success it
        cannot evidence (LearnedPatterns #15).

        Args:
            name: The program, for the error message.

        Raises:
            DeviceFaultError: The controller accepted ``ProgramRun`` but
                never left the stopped state.
            TransportError: The state could not be read.
        """
        deadline = time.monotonic() + PROGRAM_START_GRACE_S
        while time.monotonic() < deadline:
            if self._program_state() != PROGRAM_STOPPED:
                return
            time.sleep(PROGRAM_START_POLL_S)
        raise DeviceFaultError(
            f"the controller accepted ProgramRun for {name} but was still "
            f"stopped {PROGRAM_START_GRACE_S:.1f} s later; it did not run "
            "(or it finished faster than this cell can observe)",
            command="arm",
        )

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
        because it is weaker than it looks. It means: the
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
            line = self._current_line()
            if line is not None:
                # Keep the high-water mark, not the reading — see
                # _ProgramPending.last_line.
                pending.last_line = max(pending.last_line or 0, line)
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
        """Terminate the job program, then tell the controller to stop.

        Reaches ``self._program`` directly and takes no lock, so it works
        while a program is in flight — this cell is meant to be the
        counter-example to GAP-9, not another instance of it.

        Both steps are attempted whatever the other does, and neither
        raises: a partial stop is reported, because the caller of an
        e-stop needs the answer more than it needs an exception.

        Args:
            None.

        Returns:
            ``{"program": ..., "sdk": ...}``, each a short outcome string.
        """
        result: dict[str, str] = {}
        # Order matters: whatever is issuing motion has to be dead before
        # StopMotion, or the next Lua line restarts what it just stopped.
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
