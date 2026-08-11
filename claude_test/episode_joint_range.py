"""Is this recorded episode replayable on this arm? Read-only.

`lerobot-replay` streams recorded joint angles frame by frame without
asking whether the arm it is driving can reach them. The two FR5 arms do
not have the same joint soft limits (LearnedPatterns #43: cell6's joint
6 is +-175 deg, cell7's +-360), so an episode recorded on one can be
refused partway through a trajectory on the other — with the arm already
moving.

This answers that before anything moves. It scans the episode's whole
action column for its per-joint travel, and optionally reads the target
controller's soft limits over XMLRPC, which is a plain read: nothing is
commanded, nothing is enabled, and it is safe to run while a cell server
holds the arm.

The dataset side needs the ``lerobot`` conda env; the limit read needs
only the stdlib, so a bare env can still answer the second half::

    conda activate lerobot
    python claude_test/episode_joint_range.py \\
        coport-uni/FR5_task3_… 10 --arm-ip 192.168.0.58

Exit status is 1 when a joint leaves the arm's limits, so it can gate a
bench script.
"""

from __future__ import annotations

import argparse
import sys
import xmlrpc.client

#: Joints on an FR5.
JOINT_COUNT = 6

#: Port the controller serves XMLRPC on.
CONTROLLER_RPC_PORT = 20003

#: ``GetJointSoftLimitDeg``'s ``flag``: 1 = read the live values.
SOFT_LIMIT_READ_FLAG = 1

#: The call answers a flat list of ``(negative, positive)`` pairs.
LIMITS_PER_JOINT = 2

#: fairino SDK error code meaning "OK".
SDK_OK = 0

#: How close to a limit still deserves a warning rather than a pass. A
#: trajectory that clears the stop by a tenth of a degree is not a
#: margin, it is a coincidence.
MARGIN_WARN_DEG = 5.0


def read_episode_range(repo_id: str, episode: int) -> dict[str, list[float]]:
    """Per-joint ``[min, max]`` over every frame of one episode.

    Args:
        repo_id: HuggingFace dataset id.
        episode: Episode index within the dataset.

    Returns:
        Joint name -> ``[min_deg, max_deg]``, ordered joint 1 first. Only
        the revolute joints are returned; a gripper channel is dropped.

    Raises:
        SystemExit: lerobot is not importable — wrong conda env.
    """
    try:
        from lerobot.datasets.lerobot_dataset import LeRobotDataset
        from lerobot.utils.constants import ACTION
    except ImportError as exc:
        raise SystemExit(
            f"lerobot is not importable ({exc}); activate the lerobot env"
        ) from exc

    dataset = LeRobotDataset(repo_id, episodes=[episode])
    rows = dataset.hf_dataset.filter(
        lambda row: row["episode_index"] == episode
    )
    names = dataset.features[ACTION]["names"]
    keep = [
        (i, n) for i, n in enumerate(names) if n.lower().startswith("joint")
    ]
    ranges: dict[str, list[float]] = {}
    for index, name in keep:
        values = [float(row[ACTION][index]) for row in rows]
        ranges[name] = [min(values), max(values)]
    return ranges


def read_soft_limits(ip_address: str) -> list[tuple[float, float]]:
    """The controller's joint soft limits, as ``(low, high)`` per joint.

    Raw XMLRPC rather than the SDK wrapper: every wrapper opens with the
    unbounded ``reconnect_flag`` spin (LearnedPatterns #41), and this is
    a read that should either answer or fail.

    Args:
        ip_address: The controller's address.

    Returns:
        Six ``(low_deg, high_deg)`` pairs, joint 1 first.

    Raises:
        SystemExit: The controller did not answer, or reported an error.
    """
    proxy = xmlrpc.client.ServerProxy(
        f"http://{ip_address}:{CONTROLLER_RPC_PORT}/RPC2", allow_none=True
    )
    try:
        result = proxy.GetJointSoftLimitDeg(SOFT_LIMIT_READ_FLAG)
    except (OSError, xmlrpc.client.Fault) as exc:
        raise SystemExit(
            f"cannot read limits from {ip_address}: {exc}"
        ) from exc
    if not result or int(result[0]) != SDK_OK:
        raise SystemExit(f"limit read failed: {result!r}")
    flat = [float(v) for v in result[1 : JOINT_COUNT * LIMITS_PER_JOINT + 1]]
    pairs = [
        flat[i : i + LIMITS_PER_JOINT]
        for i in range(0, len(flat), LIMITS_PER_JOINT)
    ]
    # Ordering of each pair is not worth trusting to documentation.
    return [(min(p), max(p)) for p in pairs]


def report(
    ranges: dict[str, list[float]],
    limits: list[tuple[float, float]] | None,
) -> bool:
    """Print the comparison table.

    Args:
        ranges: Output of `read_episode_range`.
        limits: Output of `read_soft_limits`, or None to skip the check.

    Returns:
        True when every joint stays inside its limits (or none were
        read).
    """
    header = "| joint | episode min | episode max |"
    rule = "|---|---|---|"
    if limits is not None:
        header += " limit low | limit high | margin | verdict |"
        rule += "---|---|---|---|"
    print(header)
    print(rule)

    ok = True
    for index, (name, (low, high)) in enumerate(ranges.items()):
        row = f"| {name} | {low:+.2f} | {high:+.2f} |"
        if limits is not None and index < len(limits):
            lo_limit, hi_limit = limits[index]
            margin = min(low - lo_limit, hi_limit - high)
            if margin < 0:
                verdict = "**OVER LIMIT**"
                ok = False
            elif margin < MARGIN_WARN_DEG:
                verdict = "tight"
            else:
                verdict = "ok"
            row += (
                f" {lo_limit:+.1f} | {hi_limit:+.1f} | "
                f"{margin:+.2f} | {verdict} |"
            )
        print(row)
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_id", help="HuggingFace dataset id")
    parser.add_argument("episode", type=int, help="episode index")
    parser.add_argument(
        "--arm-ip",
        default=None,
        help="controller to check the trajectory against, e.g. 192.168.0.58",
    )
    args = parser.parse_args()

    ranges = read_episode_range(args.repo_id, args.episode)
    limits = read_soft_limits(args.arm_ip) if args.arm_ip else None
    ok = report(ranges, limits)
    if limits is None:
        print("\nNo --arm-ip given: travel only, no reachability verdict.")
        return 0
    print(
        f"\n{args.repo_id} episode {args.episode} on {args.arm_ip}: "
        f"{'within limits' if ok else 'NOT REPLAYABLE'}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
