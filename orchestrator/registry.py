"""Config-driven address book of the L1 cell servers.

``config.toml`` is the only place a cell's host, port and owning NUC are
written down; no address is hardcoded in code (spec §2). Both NUCs clone
the same repository and differ only by which config they load (spec §3).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 17100
DEFAULT_LOG_DIR = "runs"
DEFAULT_STATUS_POLL_S = 1.0
DEFAULT_STEP_TIMEOUT_S = 30.0
DEFAULT_CONNECT_TIMEOUT_S = 5.0
DEFAULT_RETRY_DELAY_S = 1.0

#: Action prefixes treated as "this acts on hardware": it moves something
#: (gantry, linear rail, Z stage, pump) or it heats/energizes something
#: (hotplate, IR lamp). The first such step of a run needs an operator
#: confirmation when ``confirm_first_motion`` is on (CLAUDE.md
#: Folder-specific rules #3, spec §6.4). A heater coming on unattended is
#: the same class of hazard as a motor starting unattended.
DEFAULT_HAZARD_PREFIXES = (
    "gantry/",
    "linear/",
    "zstage/",
    "stage/",
    "pump/",
    "hotplate/",
    "lamp/",
)

#: The L1 convention: every state-changing route is a POST, every GET is a
#: read-only probe. So a GET is never gated, even under a hazard prefix
#: (``hotplate/state``, ``lamp/state``).
READ_ONLY_METHOD = "GET"


class ConfigError(ValueError):
    """Raised when ``config.toml`` is missing or malformed."""


class UnknownCellError(KeyError):
    """Raised when a scenario names a cell that is not in the registry."""

    def __init__(self, name: str, known: tuple[str, ...]) -> None:
        self.name = name
        self.known = known
        super().__init__(
            f"unknown cell {name!r}; registry has: {', '.join(known) or '-'}"
        )

    def __str__(self) -> str:  # KeyError repr()s its arg otherwise
        return self.args[0]


@dataclass(frozen=True, slots=True)
class CellEntry:
    """One L1 cell server: where it lives and which NUC hosts it."""

    name: str
    base_url: str
    nuc: str

    def url(self, path: str) -> str:
        """Absolute URL for a ``/v1`` sub-path, e.g. ``linear/move``."""
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"


@dataclass(frozen=True, slots=True)
class OrchestratorConfig:
    """The ``[orchestrator]`` table of ``config.toml``."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    log_dir: Path = Path(DEFAULT_LOG_DIR)
    status_poll_s: float = DEFAULT_STATUS_POLL_S
    step_timeout_s: float = DEFAULT_STEP_TIMEOUT_S
    connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S
    retry_delay_s: float = DEFAULT_RETRY_DELAY_S
    confirm_first_motion: bool = True
    stop_on_failure: bool = True
    hazard_prefixes: tuple[str, ...] = DEFAULT_HAZARD_PREFIXES

    def is_hazardous(self, action: str, method: str = "POST") -> bool:
        """True if this call acts on hardware (moves, heats, energizes).

        Args:
            action: Path below ``/v1``, e.g. ``hotplate/heater``.
            method: HTTP method. A ``GET`` is read-only by the L1
                convention and is never treated as hazardous.
        """
        if method.upper() == READ_ONLY_METHOD:
            return False
        return action.lstrip("/").startswith(self.hazard_prefixes)


@dataclass(frozen=True, slots=True)
class Registry:
    """Immutable name → :class:`CellEntry` map."""

    cells: dict[str, CellEntry] = field(default_factory=dict)

    def __contains__(self, name: object) -> bool:
        return name in self.cells

    def __len__(self) -> int:
        return len(self.cells)

    def names(self) -> tuple[str, ...]:
        return tuple(self.cells)

    def get(self, name: str) -> CellEntry:
        """Look a cell up, or raise :class:`UnknownCellError`."""
        try:
            return self.cells[name]
        except KeyError:
            raise UnknownCellError(name, self.names()) from None

    def by_nuc(self, nuc: str) -> tuple[CellEntry, ...]:
        return tuple(c for c in self.cells.values() if c.nuc == nuc)


def _orchestrator_config(raw: dict[str, Any]) -> OrchestratorConfig:
    table = raw.get("orchestrator", {})
    if not isinstance(table, dict):
        raise ConfigError("[orchestrator] must be a table")
    prefixes = table.get("hazard_prefixes", DEFAULT_HAZARD_PREFIXES)
    return OrchestratorConfig(
        host=str(table.get("host", DEFAULT_HOST)),
        port=int(table.get("port", DEFAULT_PORT)),
        log_dir=Path(str(table.get("log_dir", DEFAULT_LOG_DIR))),
        status_poll_s=float(table.get("status_poll_s", DEFAULT_STATUS_POLL_S)),
        step_timeout_s=float(
            table.get("step_timeout_s", DEFAULT_STEP_TIMEOUT_S)
        ),
        connect_timeout_s=float(
            table.get("connect_timeout_s", DEFAULT_CONNECT_TIMEOUT_S)
        ),
        retry_delay_s=float(table.get("retry_delay_s", DEFAULT_RETRY_DELAY_S)),
        confirm_first_motion=bool(table.get("confirm_first_motion", True)),
        stop_on_failure=bool(table.get("stop_on_failure", True)),
        hazard_prefixes=tuple(str(p) for p in prefixes),
    )


def _registry(raw: dict[str, Any]) -> Registry:
    table = raw.get("cells", {})
    if not isinstance(table, dict):
        raise ConfigError("[cells] must be a table of cell tables")
    cells: dict[str, CellEntry] = {}
    for name, entry in table.items():
        if not isinstance(entry, dict):
            raise ConfigError(f"[cells.{name}] must be a table")
        base_url = entry.get("base_url")
        if not base_url:
            raise ConfigError(f"[cells.{name}] needs a base_url")
        cells[name] = CellEntry(
            name=name,
            base_url=str(base_url),
            nuc=str(entry.get("nuc", "")),
        )
    return Registry(cells=cells)


def load_config(path: Path) -> tuple[OrchestratorConfig, Registry]:
    """Parse ``config.toml`` into its two halves.

    Args:
        path: Path to the orchestrator TOML config.

    Returns:
        The ``[orchestrator]`` settings and the cell registry.

    Raises:
        ConfigError: The file is missing, unparseable, or a cell entry
            lacks ``base_url``.
    """
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    return _orchestrator_config(raw), _registry(raw)
