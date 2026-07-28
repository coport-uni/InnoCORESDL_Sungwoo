"""``python -m server`` entry point for the InnoCORESDL cell.

Reads a TOML config (device tables — ``[pump]`` / ``[stage]`` / ``[balance]``
/ ``[linear]`` / ``[zstage]`` / ``[hotplate]`` / ``[lamp]`` — plus
``[server]`` for host/port/log level), opens the cell once, and hands the
live app to uvicorn. The tables present select the cell shape, so
``--config`` alone is enough. Single worker — multiple workers would each
try to open the same serial handles and fight for them.
"""

from __future__ import annotations

import argparse
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

import uvicorn

from cell.balance_linear_cell import BalanceLinearCell, BalanceLinearConfig
from cell.pump_gantry_cell import Config, PumpGantryCell
from cell.pump_z_thermal_cell import (
    DEFAULT_MAX_CELSIUS,
    PumpZThermalCell,
    PumpZThermalConfig,
)
from server.app import create_app


@dataclass(frozen=True, slots=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 17054
    log_level: str = "info"


def _load(path: Path) -> tuple[Config, ServerConfig]:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    pump = raw.get("pump", {})
    stage = raw.get("stage", {})
    server = raw.get("server", {})
    cell_cfg = Config(
        # No [pump] table → the cell serves its gantry alone and the pump
        # action set 409s. The default only applies when the table exists
        # but omits `port`; absence of the table is a deliberate "no pump",
        # not a request for the default one.
        pump_port=pump.get("port", "1A86:7523") if pump else None,
        pump_address=int(pump.get("address", 1)),
        pump_baud=int(pump.get("baud", 9600)),
        syringe_uL=int(pump.get("syringe_uL", 125)),
        pump_init_force=int(pump.get("init_force", 2)),
        motor_serial_x=stage.get("serial_x", "NTAMU6TO"),
        z_coord_invert=bool(stage.get("z_coord_invert", True)),
        x_coord_invert=bool(stage.get("x_coord_invert", True)),
        home_dir_z=int(stage.get("home_dir_z", 0)),
        home_dir_x=int(stage.get("home_dir_x", 0)),
    )
    server_cfg = ServerConfig(
        host=server.get("host", "0.0.0.0"),
        port=int(server.get("port", 17054)),
        log_level=server.get("log_level", "info"),
    )
    return cell_cfg, server_cfg


def _load_balance_linear(
    path: Path,
) -> tuple[BalanceLinearConfig, ServerConfig]:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    linear = raw.get("linear", {})
    balance = raw.get("balance", {})
    server = raw.get("server", {})
    bl_cfg = BalanceLinearConfig(
        linear_port=linear.get("port", "110A:1150"),
        scale_port=balance.get("port"),
        ambient=balance.get("ambient"),
    )
    server_cfg = ServerConfig(
        host=server.get("host", "0.0.0.0"),
        port=int(server.get("port", 17060)),  # cell4 default
        log_level=server.get("log_level", "info"),
    )
    return bl_cfg, server_cfg


def _load_pump_z_thermal(
    path: Path,
) -> tuple[PumpZThermalConfig, ServerConfig]:
    """Parse a Cell 5 (cell5) config: pump + Z + hotplate + lamp."""
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    pump = raw.get("pump", {})
    zstage = raw.get("zstage", {})
    hotplate = raw.get("hotplate", {})
    lamp = raw.get("lamp", {})
    server = raw.get("server", {})
    cell_cfg = PumpZThermalConfig(
        # No [pump] table = no pump on this bench yet; the cell then
        # serves Z + hotplate + lamp and answers 409 on pump routes.
        pump_enabled="pump" in raw,
        pump_port=pump.get("port", "1A86:7523"),
        pump_address=int(pump.get("address", 1)),
        pump_baud=int(pump.get("baud", 9600)),
        syringe_uL=int(pump.get("syringe_uL", 125)),
        pump_init_force=int(pump.get("init_force", 2)),
        z_serial=zstage.get("serial"),
        z_coord_invert=bool(zstage.get("coord_invert", True)),
        home_dir_z=int(zstage.get("home_dir", 0)),
        hotplate_port=hotplate.get("port"),
        max_celsius=float(hotplate.get("max_celsius", DEFAULT_MAX_CELSIUS)),
        lamp_target=lamp.get("target", "all"),
    )
    server_cfg = ServerConfig(
        host=server.get("host", "0.0.0.0"),
        port=int(server.get("port", 17062)),  # cell5 default
        log_level=server.get("log_level", "info"),
    )
    return cell_cfg, server_cfg


def _infer_cell(path: Path) -> str:
    """Pick the cell shape from the config's tables so `--config` alone selects
    it. A ``[zstage]`` / ``[hotplate]`` / ``[lamp]`` table → ``pump_z_thermal``
    (cell5, Cell 5 — checked first because it also has a ``[pump]`` table); a
    ``[linear]`` (or ``[balance]``) table → ``balance_linear`` (cell4);
    otherwise ``pump_gantry`` (cell1–3). ``--cell`` overrides this."""
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    if any(table in raw for table in ("zstage", "hotplate", "lamp")):
        return "pump_z_thermal"
    has_bl = "linear" in raw or "balance" in raw
    return "balance_linear" if has_bl else "pump_gantry"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ics-server")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=(
            "TOML config path. Default: $ICS_SERVER_CONFIG env var, "
            "otherwise ./server/ics.toml."
        ),
    )
    parser.add_argument(
        "--cell",
        choices=("pump_gantry", "balance_linear", "pump_z_thermal"),
        default=None,
        help=(
            "Cell shape to serve. Omit to auto-detect from the config: a "
            "[zstage]/[hotplate]/[lamp] table → 'pump_z_thermal' (cell5), a "
            "[linear] table → 'balance_linear' (cell4), otherwise "
            "'pump_gantry' (cell1–3). Pass explicitly only to override the "
            "inference."
        ),
    )
    args = parser.parse_args(argv)

    if args.config is not None:
        cfg_path = args.config
    elif env := os.environ.get("ICS_SERVER_CONFIG"):
        cfg_path = Path(env)
    else:
        cfg_path = Path("server/ics.toml")

    if not cfg_path.exists():
        parser.error(f"config file not found: {cfg_path}")

    cell_kind = args.cell or _infer_cell(cfg_path)
    if cell_kind == "balance_linear":
        bl_cfg, server_cfg = _load_balance_linear(cfg_path)
        factory = lambda: BalanceLinearCell.open(bl_cfg)  # noqa: E731
    elif cell_kind == "pump_z_thermal":
        pzt_cfg, server_cfg = _load_pump_z_thermal(cfg_path)
        factory = lambda: PumpZThermalCell.open(pzt_cfg)  # noqa: E731
    else:
        cell_cfg, server_cfg = _load(cfg_path)
        factory = lambda: PumpGantryCell.open(cell_cfg)  # noqa: E731

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
    raise SystemExit(main())
