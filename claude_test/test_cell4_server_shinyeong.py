#!/usr/bin/env python3
"""Serve cell4 (rail + balance) so a station move runs in one go.

WHY THIS EXISTS. The synthesis run drives the rail to a station with one
``/v1/linear/move``, yet on the bench the carriage visibly stops part-way
and starts again. The cell4 server log shows why, on the 2026-09-15 return
from the end position:

    move_to_mm: target=5.0 mm, start=969.346 mm
      iter 1: now 718.495 mm
      iter 2: now 467.236 mm
      iter 3: now 215.924 mm
      iter 4: now 5.754 mm

``LinearMotorController.move_to_mm`` breaks every move into steps, each
capped by ``timeout_per_step`` (default 10 s). At the 25 r/min the PID
settles on for a long move that is about 250 mm, so the rail stops at the
cap, sits through a fixed 2 s settle inside ``move_relative_mm``, re-reads
its position and sets off again — once on the way to cell1 (465 mm), three
times on the way home (965 mm). The operator asked for those stops to go.

WHY A LAUNCHER AND NOT AN EDIT. ``BalanceLinearCell`` calls ``move_to_mm``
with the driver's defaults, and both it and the driver are existing code
that others are working in (and the driver is a submodule). This launcher
builds the same cell from the same ``server/nuc1/cell4.toml`` through the
same ``create_app(cell_factory=...)`` hook ``python -m server`` uses, and
only changes the step cap on that one call. Nothing on disk outside this
file is modified.

WHAT IS GIVEN UP. The step cap was also a checkpoint: every ~250 mm the
loop stopped, measured, and corrected. With one long step the rail is
measured and corrected only at the end, so a position read corrupted
mid-move (this bench's hub resets, LearnedPatterns #20) is acted on later.
What stays: the poll loop still stops the rail as soon as it is within
tolerance, a failed read still breaks the loop and sends the stop write,
and a link drop still aborts the move. The rail still cannot be stopped
from software (GAP-1) — the physical e-stop is the stop.

    cd ~/workspace/InnoCOREServer/InnoCORESDL_Sungwoo
    .venv/bin/python claude_test/test_cell4_server_shinyeong.py \\
        --config server/nuc1/cell4.toml

Stop the standard cell4 server first — one owner per port (CLAUDE.md
folder rule 2).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

# Run as a file from claude_test/, Python puts claude_test/ on the path, not
# the repo root — so `cell` and `server` are not importable the way they are
# under `python -m server`. Add the root explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cell.balance_linear_cell import BalanceLinearCell  # noqa: E402
from server.__main__ import _load_balance_linear  # noqa: E402
from server.app import create_app  # noqa: E402

#: Per-step cap handed to ``move_to_mm``. Long enough that the longest
#: move on this bench — home to the 970 mm end, 965 mm at the ~25 mm/s the
#: 2026-09-15 log implies (250 mm per 10 s step) — fits in one step with
#: margin, so no station move is split. Still finite, so a step that never
#: reaches tolerance is abandoned and the rail is sent its stop write.
STEP_TIMEOUT_S = 50.0

DEFAULT_CONFIG = Path("server/nuc1/cell4.toml")


def _one_step_rail(cell: BalanceLinearCell) -> BalanceLinearCell:
    """Make this cell's rail moves run as a single step.

    Wraps the driver instance's ``move_to_mm`` so every call from the cell
    carries ``timeout_per_step=STEP_TIMEOUT_S`` unless the caller already
    set one. Only this instance is affected; the class is untouched.
    """
    lin = cell._lin  # noqa: SLF001 — the cell exposes no hook for this
    original = lin.move_to_mm

    def move_to_mm(target_mm: float, **kwargs):
        kwargs.setdefault("timeout_per_step", STEP_TIMEOUT_S)
        return original(target_mm, **kwargs)

    lin.move_to_mm = move_to_mm
    return cell


def main(argv: list[str] | None = None) -> int:
    """Serve cell4 with single-step rail moves. Returns an exit code."""
    parser = argparse.ArgumentParser(
        description="Serve cell4 with station moves in one step."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)

    if not args.config.exists():
        parser.error(f"config file not found: {args.config}")

    bl_cfg, server_cfg = _load_balance_linear(args.config)
    print(
        f"cell4 — rail moves in one step (timeout_per_step "
        f"{STEP_TIMEOUT_S:g} s), serving on port {server_cfg.port}. The "
        f"rail has no software stop; keep the e-stop in reach."
    )

    def factory() -> BalanceLinearCell:
        return _one_step_rail(BalanceLinearCell.open(bl_cfg))

    app = create_app(cell_factory=factory)
    uvicorn.run(
        app,
        host=server_cfg.host,
        port=server_cfg.port,
        log_level=server_cfg.log_level,
        timeout_keep_alive=120,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
