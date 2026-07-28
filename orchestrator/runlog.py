"""Append-only run records (spec §10).

Every run writes ``runs/{run_id}/`` with four files:

===============  ==========================================================
scenario.yaml    the scenario exactly as submitted (before params merge)
run.jsonl        one line per step: ids, UTC timestamps, result, error
vars.json        the variable snapshot at the end of the run
meta.json        run_id, step_mode, git commit, config summary
===============  ==========================================================

The directory doubles as the experiment record, so nothing is rewritten
in place — ``run.jsonl`` is only ever appended to.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RUN_ID_TIME_FORMAT = "%Y%m%dT%H%M%SZ"
SCENARIO_FILE = "scenario.yaml"
STEPS_FILE = "run.jsonl"
VARS_FILE = "vars.json"
META_FILE = "meta.json"
GIT_COMMIT_TIMEOUT_S = 5
_UNSAFE_RUN_ID = re.compile(r"[^A-Za-z0-9_.-]+")


def utc_now() -> str:
    """Current UTC time as an ISO-8601 string with a ``+00:00`` offset."""
    return datetime.now(timezone.utc).isoformat()


def new_run_id(scenario_name: str) -> str:
    """Sortable ``<utc>-<scenario>`` identifier, safe as a directory name."""
    stamp = datetime.now(timezone.utc).strftime(RUN_ID_TIME_FORMAT)
    slug = _UNSAFE_RUN_ID.sub("-", scenario_name).strip("-")
    return f"{stamp}-{slug}"


def git_commit(repo: Path | None = None) -> str | None:
    """HEAD of the working tree, or None outside a git checkout."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo) if repo else None,
            capture_output=True,
            text=True,
            timeout=GIT_COMMIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


class RunLog:
    """Writer for one run's directory."""

    def __init__(self, root: Path, run_id: str) -> None:
        self.dir = Path(root) / run_id
        self.dir.mkdir(parents=True, exist_ok=True)

    def write_scenario(self, text: str) -> None:
        """Store the submitted scenario verbatim."""
        (self.dir / SCENARIO_FILE).write_text(text, encoding="utf-8")

    def append_step(self, record: dict[str, Any]) -> None:
        """Append one JSON line to ``run.jsonl``."""
        line = json.dumps(record, ensure_ascii=False, default=str)
        with (self.dir / STEPS_FILE).open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def write_vars(self, variables: dict[str, Any]) -> None:
        """Snapshot the variables (overwritten as the run progresses)."""
        self._dump(VARS_FILE, variables)

    def write_meta(self, meta: dict[str, Any]) -> None:
        """Write/refresh ``meta.json``."""
        self._dump(META_FILE, meta)

    def _dump(self, name: str, payload: dict[str, Any]) -> None:
        (self.dir / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str)
            + "\n",
            encoding="utf-8",
        )


def read_run_meta(root: Path) -> list[dict[str, Any]]:
    """Every past run's ``meta.json`` under ``root``, newest first."""
    runs: list[dict[str, Any]] = []
    if not root.exists():
        return runs
    for path in sorted(root.iterdir(), reverse=True):
        meta = path / META_FILE
        if not meta.is_file():
            continue
        try:
            runs.append(json.loads(meta.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return runs
