"""``python -m orchestrator`` — serve, validate, or run a scenario.

Three subcommands:

``serve``
    Run the ``/v1`` API (this is what the Docker unit starts).
``validate``
    Dry-run a scenario file against the registry and the cells' OpenAPI.
    Read-only: the only calls are ``GET /openapi.json``.
``run``
    Execute a scenario from the console. ``--step-mode`` stops after every
    step for an operator check — this is the bench-verification path of
    spec §9 (dry run → real step_mode → real).

The first motion-bearing step always waits for an explicit console
confirmation unless ``confirm_first_motion`` is turned off in the config.
There is deliberately no CLI flag to bypass it.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

import yaml

from orchestrator.client import CellClient
from orchestrator.engine import (
    Engine,
    RunState,
    ScenarioInvalid,
)
from orchestrator.registry import ConfigError, load_config

DEFAULT_CONFIG = Path("orchestrator/config.toml")
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
POLL_S = 0.2


def _parse_params(pairs: list[str] | None) -> dict[str, Any]:
    """Turn ``--param k=v`` pairs into typed values via a YAML scalar load."""
    params: dict[str, Any] = {}
    for pair in pairs or []:
        key, sep, raw = pair.partition("=")
        if not sep:
            raise SystemExit(f"--param needs key=value, got {pair!r}")
        params[key.strip()] = yaml.safe_load(raw)
    return params


def _print_record(record: dict[str, Any]) -> None:
    if record.get("event") == "abort":
        print(f"  [abort] stop broadcast: {record.get('stop_broadcast')}")
        return
    mark = "ok " if record["ok"] else "FAIL"
    where = record.get("cell") or record["kind"]
    action = record.get("action") or record["kind"]
    print(
        f"  [{mark}] {record['id']}  {where}/{action}  {record['duration_s']}s"
    )
    if not record["ok"]:
        print(f"        {record['error']['message']}")


async def _ask(prompt: str) -> str:
    """Read a console line without blocking the event loop."""
    return await asyncio.to_thread(input, prompt)


async def _drive(engine: Engine, run_id: str) -> int:
    """Print progress, handle pause gates, return the process exit code."""
    run = engine.get(run_id)
    shown = 0
    while True:
        while shown < len(run.records):
            _print_record(run.records[shown])
            shown += 1
        if run.state in (RunState.COMPLETED, RunState.FAILED, RunState.ABORTED):
            break
        if run.state is RunState.PAUSED:
            gate = run.pending_confirmation
            if gate:
                print(
                    f"\n  !! next step '{gate}' moves hardware. Clear the "
                    "frame, keep the e-stop in reach."
                )
            answer = await _ask("  [enter]=continue, a=abort > ")
            if answer.strip().lower().startswith("a"):
                await engine.abort(run_id)
            else:
                await engine.resume(run_id)
            continue
        await asyncio.sleep(POLL_S)
    print(f"\nrun {run.run_id}: {run.state.value}")
    if run.log is not None:
        print(f"runlog: {run.log.dir}")
    if run.error:
        print(f"error: {run.error}")
    return EXIT_OK if run.state is RunState.COMPLETED else EXIT_FAILED


async def _validate(args: argparse.Namespace) -> int:
    config, registry = load_config(args.config)
    async with CellClient(
        registry,
        connect_timeout_s=config.connect_timeout_s,
        default_timeout_s=config.step_timeout_s,
    ) as client:
        engine = Engine(config, registry, client)
        text = Path(args.scenario).read_text(encoding="utf-8")
        scenario, issues = await engine.validate(
            text, params=_parse_params(args.param)
        )
    name = scenario.name if scenario else args.scenario
    if not issues:
        print(f"{name}: ok ({len(scenario.steps) if scenario else 0} steps)")
        return EXIT_OK
    print(f"{name}: {len(issues)} issue(s)")
    for issue in issues:
        print(f"  - {issue}")
    return EXIT_FAILED


async def _run(args: argparse.Namespace) -> int:
    config, registry = load_config(args.config)
    async with CellClient(
        registry,
        connect_timeout_s=config.connect_timeout_s,
        default_timeout_s=config.step_timeout_s,
    ) as client:
        engine = Engine(config, registry, client, repo_root=Path.cwd())
        text = Path(args.scenario).read_text(encoding="utf-8")
        try:
            run = await engine.create_run(
                text,
                step_mode=args.step_mode,
                params=_parse_params(args.param),
            )
        except ScenarioInvalid as exc:
            print("scenario did not validate:")
            for issue in exc.issues:
                print(f"  - {issue}")
            return EXIT_FAILED
        print(f"run {run.run_id} started ({len(run.scenario.steps)} steps)")
        code = await _drive(engine, run.run_id)
        await engine.wait(run.run_id)
        return code


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from orchestrator.app import create_app

    config, _registry = load_config(args.config)
    app = create_app(args.config, repo_root=Path.cwd())
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level=args.log_level,
        timeout_keep_alive=int(config.step_timeout_s),
    )
    return EXIT_OK


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="orchestrator")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"orchestrator TOML config (default: {DEFAULT_CONFIG})",
    )
    subs = parser.add_subparsers(dest="command", required=True)

    serve = subs.add_parser("serve", help="run the /v1 API")
    serve.add_argument("--log-level", default="info")

    validate = subs.add_parser(
        "validate", help="dry-run a scenario (no device calls)"
    )
    validate.add_argument("scenario", type=Path)
    validate.add_argument("--param", action="append", metavar="KEY=VALUE")

    run = subs.add_parser("run", help="execute a scenario from the console")
    run.add_argument("scenario", type=Path)
    run.add_argument(
        "--step-mode",
        action="store_true",
        help="pause after every step for an operator check",
    )
    run.add_argument("--param", action="append", metavar="KEY=VALUE")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "serve":
            return _serve(args)
        if args.command == "validate":
            return asyncio.run(_validate(args))
        return asyncio.run(_run(args))
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except FileNotFoundError as exc:
        print(f"file not found: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
