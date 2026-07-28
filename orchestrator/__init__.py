"""L2 Orchestrator for InnoCORESDL.

Runs scenario files across the L1 cell servers. Every device access goes
through a cell's HTTP ``/v1`` API — this package never imports a hardware
driver, because a serial port has exactly one owner (CLAUDE.md
Folder-specific rules #2) and that owner is the cell server.

See ``docs/L2_ORCHESTRATOR_SPEC.md`` for the design this implements.
"""

from __future__ import annotations

__version__ = "0.1.0"
