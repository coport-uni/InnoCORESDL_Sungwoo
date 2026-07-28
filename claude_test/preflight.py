"""Bench pre-flight: can these cell configs actually start? (no motion)

Run this BEFORE `python -m server` on bench day. It answers the three
questions that otherwise cost an hour of confusion:

1. Which devices are actually plugged in right now?
2. Does every address in every `server/**/ *.toml` resolve to one of them,
   or is it still a `TBD-…` placeholder?
3. Is anything already holding a port a cell server needs?

It **enumerates only** — it never opens a serial handle and never sends a
command, so it is safe to run while a cell server is up (one owner per
port, CLAUDE.md Folder-specific rules #2). Nothing here moves hardware.

With no hardware attached it still runs and reports everything as
`missing`, which is the useful answer when you are preparing off-bench.

Usage:
    python claude_test/preflight.py                 # every config found
    python claude_test/preflight.py server/nuc2/cell5.toml
"""

from __future__ import annotations

import argparse
import socket
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_GLOB = "server/nuc*/*.toml"
TBD_MARKER = "TBD"
EXIT_OK = 0
EXIT_INCOMPLETE = 1

#: Device address fields per config table: (table, key, kind).
#: ``kind`` picks which pool of attached devices can satisfy it.
ADDRESS_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("pump", "port", "serial"),
    ("linear", "port", "serial"),
    ("balance", "port", "serial"),
    ("hotplate", "port", "serial"),
    ("stage", "serial_x", "ftdi"),
    ("zstage", "serial", "ftdi"),
    ("lamp", "target", "plug"),
)


@dataclass(frozen=True)
class Finding:
    """One resolved (or unresolved) address."""

    config: str
    table: str
    key: str
    value: str
    verdict: str  # ok | missing | tbd | unset
    detail: str

    def line(self) -> str:
        mark = {"ok": "ok  ", "missing": "MISS", "tbd": "TBD ", "unset": "auto"}
        return (
            f"  [{mark[self.verdict]}] {self.table}.{self.key} = "
            f"{self.value or '(unset)'}  {self.detail}"
        )


def attached_serial() -> list[dict[str, Any]]:
    """Every serial port the OS currently sees (never opened)."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    found = []
    for port in list_ports.comports():
        if port.vid is None:
            continue  # built-in ttyS*/COM ports: never one of our devices
        found.append(
            {
                "device": port.device,
                "vidpid": (
                    f"{port.vid:04X}:{port.pid:04X}"
                    if port.vid is not None
                    else None
                ),
                "serial": port.serial_number,
                "desc": port.description,
            }
        )
    return found


def attached_ftdi() -> list[str]:
    """FTDI adapter serial numbers, for the MKS motors.

    Enumeration reads USB descriptors; it does not claim the interface.
    Returns an empty list when pyftdi is missing or the bus is empty.
    """
    try:
        from pyftdi.ftdi import Ftdi
    except ImportError:
        return []
    try:
        devices = Ftdi.list_devices()
    except Exception:  # noqa: BLE001 — a bus we cannot read is "none found"
        return []
    serials = []
    for descriptor, _interfaces in devices:
        serial = getattr(descriptor, "sn", None)
        if serial:
            serials.append(str(serial))
    return serials


def _resolve_serial(value: str, ports: list[dict[str, Any]]) -> str | None:
    """Match a config value against the attached serial ports.

    Accepts a device path (``/dev/ttyUSB0``) or a ``VID:PID`` string.
    """
    wanted = value.strip().upper()
    for port in ports:
        if port["device"] and port["device"].upper() == wanted:
            return f"-> {port['device']} ({port['desc']})"
        if port["vidpid"] and port["vidpid"] == wanted:
            return f"-> {port['device']} ({port['desc']})"
    return None


def _label(path: Path) -> str:
    """Repo-relative name when possible, else the path as given."""
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def check_config(
    path: Path, ports: list[dict[str, Any]], ftdi: list[str]
) -> list[Finding]:
    """Resolve every device address declared in one cell config."""
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    name = _label(path)
    findings: list[Finding] = []
    for table, key, kind in ADDRESS_FIELDS:
        if table not in raw:
            continue
        value = raw[table].get(key)
        if value is None:
            detail = {
                "serial": "auto-detect at open time",
                "ftdi": "opens the FIRST FTDI adapter -- only safe if alone",
                "plug": "no plug named",
            }[kind]
            findings.append(Finding(name, table, key, "", "unset", detail))
            continue
        text = str(value)
        if TBD_MARKER in text.upper():
            findings.append(
                Finding(
                    name,
                    table,
                    key,
                    text,
                    "tbd",
                    "placeholder -- fill this in before the bench run",
                )
            )
            continue
        if kind == "serial":
            hit = _resolve_serial(text, ports)
        elif kind == "ftdi":
            hit = f"-> FTDI {text}" if text in ftdi else None
        else:  # plug: a LAN device, nothing to enumerate locally
            findings.append(
                Finding(
                    name,
                    table,
                    key,
                    text,
                    "ok",
                    "LAN device -- must appear in the plug driver's "
                    "device_list.md, with credentials in secure.env",
                )
            )
            continue
        findings.append(
            Finding(
                name,
                table,
                key,
                text,
                "ok" if hit else "missing",
                hit or "not attached right now",
            )
        )
    return findings


def port_in_use(port: int) -> bool:
    """True if something already listens on this TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def server_port(path: Path) -> int | None:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    port = raw.get("server", {}).get("port")
    return int(port) if port is not None else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bench pre-flight for the cell configs (read-only)."
    )
    parser.add_argument(
        "configs",
        nargs="*",
        type=Path,
        help=f"Cell TOMLs. Default: every {CONFIG_GLOB} in the repo.",
    )
    args = parser.parse_args(argv)

    configs = args.configs or sorted(REPO_ROOT.glob(CONFIG_GLOB))
    if not configs:
        print(
            "no cell config found. Copy the examples first, e.g.\n"
            "  cp server/nuc1/cell4.toml.example server/nuc1/cell4.toml",
            file=sys.stderr,
        )
        return EXIT_INCOMPLETE

    ports = attached_serial()
    ftdi = attached_ftdi()
    print(f"attached USB serial ports ({len(ports)}):")
    for port in ports:
        print(
            f"  {port['device']}  {port['vidpid'] or '-'}  "
            f"sn={port['serial'] or '-'}  {port['desc']}"
        )
    print(f"attached FTDI adapters ({len(ftdi)}): {', '.join(ftdi) or '-'}")
    if not ports and not ftdi:
        print("  (nothing attached -- expected when preparing off-bench)")

    findings: list[Finding] = []
    for config in configs:
        print(f"\n{config}:")
        rows = check_config(config, ports, ftdi)
        findings.extend(rows)
        for row in rows:
            print(row.line())
        port = server_port(config)
        if port is not None:
            busy = port_in_use(port)
            print(
                f"  [{'BUSY' if busy else 'free'}] server.port = {port}"
                + ("  <- something already listens here" if busy else "")
            )

    blocked = [f for f in findings if f.verdict in ("tbd", "missing")]
    print(
        f"\n{len(findings) - len(blocked)}/{len(findings)} addresses resolve."
    )
    if blocked:
        print("Not ready to start:")
        for row in blocked:
            print(f"  - {row.config}: {row.table}.{row.key} ({row.verdict})")
        return EXIT_INCOMPLETE
    print("Pre-flight clean. Start the server, then GET /v1/diagnose.")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
