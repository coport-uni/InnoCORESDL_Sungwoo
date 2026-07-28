# InnoCORESDL

A self-driving-lab cell project: hardware drivers composed behind a `Cell`
interface, each cell served over its own FastAPI `/v1` API (**L1**), with an
orchestrator above them that runs scenario files across cells (**L2**).

Three cell shapes exist, the SDLClaude reference implementations:

| Shape | Cells | Devices |
|---|---|---|
| pump + gantry | cell1–3 (Cell A/B/C) | syringe pump (`sy01b`) + XZ gantry — one X and **two synchronized Z** MKS motors |
| balance + linear | cell4 | MINAS A6 linear rail (`lmc`) + the Phase's single Entris-II balance |
| pump + Z + thermal | cell5 (Cell D) | pump + **one** MKS motor as a standalone Z + IKA hotplate + IR lamp on a Tapo plug |

All hardware drivers are git submodules under `external/` — see
[`external/SUBMODULES.md`](external/SUBMODULES.md).

---

## Status

**Nothing in this repository has ever run against real hardware.** Every
device-facing claim below is code-level, or was verified against the real
FastAPI app driving a stub cell.

| Layer | Built | Verified without hardware | Not yet |
|---|---|---|---|
| L1 cells | all three shapes | imports, OpenAPI, config/shape inference, error mapping | every physical behaviour |
| L1 `/v1` server | 26 routes | live app + stub cell, OpenAPI generation | bench bring-up |
| L2 orchestrator | registry, client, validator, engine, runlog, `/v1`, CLI | 32 tests; both demo scenarios end-to-end over real HTTP against a stub | anything touching a device |
| Deployment | systemd template + Docker Compose | — | never deployed to a NUC |

### Safety gaps you must know before touching hardware

| | |
|---|---|
| **GAP-9** | `POST /v1/stop` **cannot interrupt a command already in flight** — on any cell. It waits for the same lock the move holds; measured 4.2 s late on a 5 s move. L2's abort inherits this. |
| **GAP-1** | cell4's `stop()` is a no-op even when it does run. |

**Consequence: the physical e-stop is the only stop.** Both gaps, with their
measurements and proposed fixes, are in [`docs/L1_AUDIT.md`](docs/L1_AUDIT.md).

---

## Layout

```
scenario YAML ─▶ orchestrator/ :17100 ──HTTP /v1──▶ cell servers ──▶ Cell ──▶ drivers ──▶ hardware
```

| Path | What |
|---|---|
| [`cell/`](cell/) | the cell layer: [`cell_protocol.py`](cell/cell_protocol.py) (interface + `CellError` hierarchy), [`pump_gantry_cell.py`](cell/pump_gantry_cell.py), [`balance_linear_cell.py`](cell/balance_linear_cell.py), [`pump_z_thermal_cell.py`](cell/pump_z_thermal_cell.py) |
| [`server/`](server/) | the L1 `/v1` server — `create_app` + routes + schemas + error mapping. `nuc1/`, `nuc2/` hold the per-NUC config examples |
| [`orchestrator/`](orchestrator/) | the L2 orchestrator: registry, cell client, scenario loader + dry-run validator, run engine, runlog, `/v1` API, CLI |
| [`scenarios/`](scenarios/) | scenario files — `demo_linear_move.yaml`, `demo_cell_d_warmup.yaml` |
| [`deploy/`](deploy/) | systemd template unit for the cells, Compose for the orchestrator, NUC setup guide |
| [`claude_test/`](claude_test/) | tests + the two bench tools (`preflight.py`, `smoke_l1.py`) |
| [`docs/`](docs/) | the L2 spec, the M0 audit, the bring-up runbook |
| [`external/`](external/) | every driver as a submodule |

### The `/v1` action sets

A cell implements the sets its hardware has and answers 409 for the rest, so
a misdirected call is legible instead of a crash.

| Set | Routes | Cells |
|---|---|---|
| Discovery | `health`, `diagnose`, `status` | all |
| Balance | `balance/tare`, `balance/calibrate`, `balance/weight`, `balance/ambient` | cell4 |
| Pump | `pump/initialize`, `pump/valve`, `pump/aspirate`, `pump/dispense`, `pump/cycle` | cell1–3, cell5 |
| Gantry | `gantry/home`, `gantry/move` | cell1–3 |
| Linear | `linear/home`, `linear/move` | cell4 |
| ZStage | `zstage/home`, `zstage/move` | cell5 |
| Hotplate | `hotplate/state`, `hotplate/temperature`, `hotplate/heater`, `hotplate/speed`, `hotplate/stirrer` | cell5 |
| Lamp | `lamp/state`, `lamp/switch` | cell5 |
| Safety | `stop` | all (see GAP-9) |

L2 never hardcodes these — it reads each cell's `GET /openapi.json`, which is
why adding Cell D's nine routes needed **zero** orchestrator changes.

---

## Quick start

```bash
conda activate sdl                      # Python >= 3.12
git submodule update --init --recursive
pip install -r requirements.txt

# L1 — one cell server (real hardware; shape auto-detected from the config)
cp server/nuc1/cell4.toml.example server/nuc1/cell4.toml
python -m server --config server/nuc1/cell4.toml         # :17060

# L2 — the orchestrator
cp orchestrator/config.toml.example orchestrator/config.toml
python -m orchestrator serve                             # :17100
python -m orchestrator validate scenarios/demo_linear_move.yaml   # no devices
python -m orchestrator run      scenarios/demo_linear_move.yaml --step-mode

# checks that need no hardware
pytest claude_test
ruff check cell/ server/ orchestrator/ claude_test/
```

Ports are per cell (SDLClaude `ARCHITECTURE.md`): cell1=17054, cell2=17056,
cell3=17058, cell4=17060, cell5=17062, orchestrator=17100.

---

## Next: connecting the real hardware

The runbook is [`docs/L1_BRINGUP.md`](docs/L1_BRINGUP.md). In short:

1. **Collect what is still unknown** — `python claude_test/preflight.py`
   lists exactly which addresses are still `TBD`: cell2/cell3 X-adapter FTDI
   serials, cell5's Z serial, the IR lamp's plug name, the real NUC IPs, and
   the safe travel per axis.
2. **Bring up one cell at a time** — start its server, prove identity with
   `GET /v1/diagnose`, then run the gated smoke test
   (`python claude_test/smoke_l1.py --base-url … --suite …`). It stops before
   every hazardous action and waits for the operator to type `go`.
3. **Run the two contract probes** — `--suite concurrency` (A7) and
   `--suite stop` (A4). The second is expected to fail: it measures GAP-9.
4. **Record everything in [`docs/L1_AUDIT.md`](docs/L1_AUDIT.md)**, and
   replace the guessed `timeout_s` values in `scenarios/` with the measured
   durations.
5. **Then L2**: dry run → `--step-mode` → automatic.

### Roadmap

| Milestone | State |
|---|---|
| M0 — L1 adequacy audit | code review done; **every physical check pending**; 9 gaps recorded |
| M1 / M2 — registry + dry-run validator | done |
| M4 / M5 — engine, runlog, failure policies, pause/resume/abort | done (tests only) |
| M6 — systemd + Docker + real `demo_linear_move` | artifacts written, **never deployed** |
| M7 — web scenario tab | not started; `web/` was removed with the other pre-L2 work and lives in git history |

Open questions that shape the next phase, all recorded as gaps:

- **GAP-9 / GAP-1** — make the software stop actually stop something. Needs
  an L1 change plus driver work; user approval required.
- **GAP-8** — the L2 lock is per cell, so two cells sharing one physical
  workspace can still collide inside a `parallel` block. This must be solved
  before a robot arm reaches into another cell's frame.
- **Robots** — the two arms have no L1 cell yet. `ADDING_A_CELL.md` says they
  fit the cell format as an `arm` action set of discrete named trajectories;
  the blocker is that `external/FR5Controller` is not packaged.

Milestone detail is in
[`docs/L2_ORCHESTRATOR_SPEC.md`](docs/L2_ORCHESTRATOR_SPEC.md) §11.

---

## Bench notes

### Valve port gotcha (critical)

The pump's valve is a Runze M05 **Bi-pass** valve with only two fluid states
90° apart. Firmware ports 1 & 3 land on the *same* state (and 2 & 4 on the
other), so source and sink must be **90° apart, not 180°** — on this bench
the reservoir is port 2 and the tip is port 1. Verify with the eye (which
tube moves liquid), not the `?6` digit.
See [`LearnedPatterns.md`](LearnedPatterns.md) #1.

### Balance prerequisites (front panel, menu-only)

`DAT.REC = SBI`, `COM.OUTP = AUTO W/`, `STAB.RNG = V.FAST`; USB-C SBI
defaults 9600 / odd / 8 / 1. A `0x15` (NAK) reply means the balance is in
xBPI mode — wrong interface menu. The ambient filter comes from the cell
config, not the panel.

### Cell D specifics

The hotplate has a cell-level `max_celsius` ceiling in its config, checked
before the driver is called. The IR lamp's plug credentials live in
`external/SmartPlugController/secure.env`, written by the operator — Claude
Code does not read that file. Never run the hotplate driver's own dashboard
(`hotplate_controller/server.py`) while cell5 is up: one owner per port.

### Field names must survive YAML

L2 scenarios are YAML, and YAML 1.1 resolves a bare `on:` **key** to a
boolean — so a field named `on` is unreachable from a scenario. The
heater/lamp routes take `enabled` instead.
See [`LearnedPatterns.md`](LearnedPatterns.md) #8.

---

## Dependencies

Python ≥ 3.12 in the shared conda env **`sdl`**. The drivers come from the
`external/` submodules as editable installs, so a fresh checkout needs both:

```bash
git submodule update --init --recursive
pip install -r requirements.txt
```

The four packaged drivers import under their *package* names, not their repo
names: `sy01b`, `entris_ii`, `mks_motor`, `LinearMotorController`. The
hotplate and smart plug are still path-imported from `external/`.

## See also

- **SDLClaude `ARCHITECTURE.md`** — the SDL-wide architecture (Levels,
  Phases, the cell boundary rule, the port table). This repo is one Phase
  within it.
- [`docs/L2_ORCHESTRATOR_SPEC.md`](docs/L2_ORCHESTRATOR_SPEC.md) — the L2 design.
- [`docs/L1_AUDIT.md`](docs/L1_AUDIT.md) — the M0 audit, gap list, smoke-test record.
- [`docs/L1_BRINGUP.md`](docs/L1_BRINGUP.md) — the bench runbook.
- [`ADDING_A_CELL.md`](ADDING_A_CELL.md) — how to add hardware as a cell.
- [`LearnedPatterns.md`](LearnedPatterns.md) — every non-obvious problem hit
  here, with the rule it produced. Read it before debugging.
- [`CLAUDE.md`](CLAUDE.md) — conventions and environment for working here.
