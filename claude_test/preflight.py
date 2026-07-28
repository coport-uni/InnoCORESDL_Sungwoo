"""Bench pre-flight: can these cell configs actually start? (no motion)

Run this BEFORE `python -m server` on bench day. It answers the five
questions that otherwise cost an hour of confusion:

1. Which devices are actually plugged in right now?
2. Does every address in every `server/**/ *.toml` resolve to one of them,
   or is it still a `TBD-…` placeholder?
3. For a device left to auto-detect (`port` unset), is one actually on the
   bus for the driver's own detector to find?
4. May this user open the ports it resolved, or is it missing `dialout`?
5. Is anything already holding a port a cell server needs?

Questions 3 and 4 exist because their absence let a bench pass pre-flight
that could not start at all: the balance was unplugged (reported only as
"auto-detect at open time") and the account was not in `dialout`, so even
the rail's own port raised ``EACCES`` (LearnedPatterns.md #10).

It **enumerates only** — it never opens a serial handle and never sends a
command, so it is safe to run while a cell server is up (one owner per
port, CLAUDE.md Folder-specific rules #2). Nothing here moves hardware.
``os.access`` stats the device node; it does not open it.

With no hardware attached it still runs and reports everything as
`missing`, which is the useful answer when you are preparing off-bench.

Usage:
    python claude_test/preflight.py                 # every config found
    python claude_test/preflight.py server/nuc2/cell5.toml
"""

from __future__ import annotations

import argparse
import grp
import os
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

#: Auto-detect signatures for a ``serial`` field left unset, as ``(VID,
#: PID)``. Each entry mirrors that driver's own detector, so pre-flight can
#: tell "the driver will find it at open time" from "there is nothing on
#: the bus to find". ``None`` for the PID matches any product from that
#: vendor, which is what ``entris_ii.find_port`` does.
AUTO_DETECT: dict[str, tuple[str, str | None, str]] = {
    "balance": ("24BC", None, "Sartorius balance, entris_ii.find_port"),
    "hotplate": ("0483", "5740", "IKA RCT, hotplate_controller.find_rct_port"),
}


@dataclass(frozen=True)
class Finding:
    """One resolved (or unresolved) address."""

    config: str
    table: str
    key: str
    value: str
    verdict: str  # ok | missing | tbd | unset | denied
    detail: str

    def line(self) -> str:
        mark = {
            "ok": "ok  ",
            "missing": "MISS",
            "tbd": "TBD ",
            "unset": "auto",
            "denied": "PERM",
        }
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


def _resolve_serial(
    value: str, ports: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Match a config value against the attached serial ports.

    Accepts a device path (``/dev/ttyUSB0``) or a ``VID:PID`` string, and
    returns the matching port record so the caller can also check whether
    this user may open it.
    """
    wanted = value.strip().upper()
    for port in ports:
        if port["device"] and port["device"].upper() == wanted:
            return port
        if port["vidpid"] and port["vidpid"] == wanted:
            return port
    return None


def _resolve_auto(
    table: str, ports: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, str | None]:
    """Would this table's driver find its device by itself right now?

    Returns ``(port, label)``. ``label`` is ``None`` when the table has no
    known auto-detect signature, which means "cannot answer" rather than
    "not attached" — the caller keeps the old ``unset`` verdict for that.
    """
    signature = AUTO_DETECT.get(table)
    if signature is None:
        return None, None
    vid, pid, label = signature
    for port in ports:
        if not port["vidpid"]:
            continue
        got_vid, got_pid = port["vidpid"].split(":")
        if got_vid == vid and (pid is None or got_pid == pid):
            return port, label
    return None, label


def _access_problem(device: str) -> str | None:
    """Why this user could not open ``device``, or ``None`` if they can.

    ``os.access`` stats the node — it never opens a handle, so this is
    still safe against a port a cell server already owns. A resolved port
    the account cannot open is the same blocker as an unplugged one: the
    server dies at startup with ``EACCES``.
    """
    if not device or not os.path.exists(device):
        return f"{device} is no longer in /dev (stale enumeration?)"
    if os.access(device, os.R_OK | os.W_OK):
        return None
    try:
        group = grp.getgrgid(os.stat(device).st_gid).gr_name
    except (KeyError, OSError):
        group = "its owning group"
    return (
        f"{device} exists but this user cannot open it -- "
        f"`sudo usermod -aG {group} $USER`, then log out and back in"
    )


def _serial_finding(
    name: str, table: str, key: str, value: str, port: dict[str, Any]
) -> Finding:
    """Verdict for a serial port that resolved: attached, but openable?"""
    problem = _access_problem(port["device"])
    if problem:
        return Finding(name, table, key, value, "denied", problem)
    return Finding(
        name,
        table,
        key,
        value,
        "ok",
        f"-> {port['device']} ({port['desc']})",
    )


def _auto_finding(
    name: str, table: str, key: str, ports: list[dict[str, Any]]
) -> Finding:
    """Verdict for a serial device left to the driver's auto-detection."""
    port, label = _resolve_auto(table, ports)
    if label is None:  # no known signature -- cannot answer, as before
        return Finding(
            name, table, key, "", "unset", "auto-detect at open time"
        )
    if port is None:
        return Finding(
            name,
            table,
            key,
            "",
            "missing",
            f"auto-detect finds nothing -- no {label} on this bus",
        )
    finding = _serial_finding(name, table, key, "", port)
    if finding.verdict != "ok":
        return finding
    return Finding(
        name, table, key, "", "ok", f"auto-detect -> {port['device']} ({label})"
    )


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
            if kind == "serial":
                findings.append(_auto_finding(name, table, key, ports))
                continue
            detail = {
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
            port = _resolve_serial(text, ports)
            if port is None:
                findings.append(
                    Finding(
                        name, table, key, text, "missing", "not attached now"
                    )
                )
            else:
                findings.append(_serial_finding(name, table, key, text, port))
            continue
        if kind == "ftdi":
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

    blocked = [f for f in findings if f.verdict in ("tbd", "missing", "denied")]
    print(
        f"\n{len(findings) - len(blocked)}/{len(findings)} addresses resolve."
    )
    if blocked:
        print("Not ready to start:")
        for row in blocked:
            print(f"  - {row.config}: {row.table}.{row.key} ({row.verdict})")
            print(f"      {row.detail}")
        return EXIT_INCOMPLETE
    print("Pre-flight clean. Start the server, then GET /v1/diagnose.")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
