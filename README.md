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

**Cell D (cell5) is bench-verified end to end** — L1 and L2 against the
real hardware on NUC2 (2026-07-28). The other cells (cell1–4) remain
code-level only: nothing on NUC1 has run against a device yet.

| Layer | Built | Verified | Not yet |
|---|---|---|---|
| L1 cells | all three shapes | **cell5 on real hardware** (Z + hotplate + lamp; pump-less config); others: imports, OpenAPI, shape inference, error mapping | cell1–4 physical behaviour |
| L1 `/v1` server | 26 routes | **cell5 live on NUC2:17062** — diagnose, 10/10 hotplate/state stress, lamp over LAN | cell1–4 bench bring-up |
| L2 orchestrator | registry, client, validator, engine (`wait_s`, `until:`), runlog, `/v1`, CLI | 45 tests; **three real Cell D runs completed** (see below) | multi-cell / cross-NUC runs |
| Deployment | systemd template + Docker Compose | — | never deployed as a service (bench runs used the venv directly) |

### Cell D bench verification (NUC2, 2026-07-28)

How it was verified, in order — the spec §9 ladder (dry run → gated real):

1. **Device identity** — `udevadm`/`lsusb`: Z = NTREX USB2CAN FTDI
   `NTB3EP5R`, hotplate = STM32 VCP `0483:5740` (auto-detected), lamp =
   Tapo P110M at `192.168.0.237` (bare-IP entry synthesised by the cell).
2. **L1 probes** — `GET /v1/diagnose` (pump reported absent-ok),
   `hotplate/state` ×10 = **10/10** (after the direct-USB-port fix,
   LearnedPatterns #13), `lamp/state` over the LAN.
3. **L2 dry runs** — `python -m orchestrator validate` on every scenario:
   0 issues.
4. **Gated real runs** through the orchestrator `/v1` API, operator
   confirming each first-hazard gate; every run left the cell safe
   (heater off, lamp off, Z at home) and wrote its runlog under `runs/`:

| Run | Result | What it proved |
|---|---|---|
| `cell_d_lamp_heat_40c` | 13/13 steps | lamp switch, 40 °C setpoint, `until:` poll to temperature (1 °C readback tolerance, LP #14), verified shutdown; an earlier abort mid-poll killed heater+lamp immediately |
| `cell_d_z_cycles` (50 mm) | 17/17 steps | homing (15.5 s), three 0↔50 mm strokes (~3.6 s each), re-home |
| `cell_d_final` | 21/21 steps | home → 400 mm → home, then lamp+heater to 40 °C, **2-minute dwell at temperature**, verified all-off |

Bench IPs (measured): **NUC1 = 192.168.0.126, NUC2 = 192.168.0.120**;
the orchestrator ran on NUC2 with cell5 at `127.0.0.1:17062`.

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
| [`scenarios/`](scenarios/) | scenario files — `demo_linear_move.yaml`, `demo_cell_d_warmup.yaml`, and the bench-verified Cell D set: `demo_cell_d_lamp_blink.yaml`, `demo_cell_d_hotplate_30c.yaml`, `demo_cell_d_z_cycles.yaml`, `demo_cell_d_lamp_heat_40c.yaml`, `demo_cell_d_final.yaml` |
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

Beyond cell calls, asserts and `parallel`, scenario steps can hold and
poll (both added for the Cell D bench work, spec §8.1):

- `wait_s: 10.0` — local timed hold, sliced 0.2 s so an abort cuts it
  short; used for "hold the heater for N seconds".
- `until: "${result.plate_c} >= 39.0"` on a GET — repeat the read every
  `poll_s` until the condition holds, `timeout_s` bounding the whole
  poll; used for "wait until the plate reaches temperature". GET-only,
  and the cell lock is held per read so an abort's stop broadcast is
  never queued behind the poll.

---

## Quick start

```bash
conda activate sdl                      # Python >= 3.12; on NUC2 use
                                        # the repo-local .venv instead
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

**cell5 is done** (see Status above); this section now applies to the
remaining cells — cell2/cell3 on NUC2, cell1/cell4 on NUC1 — plus
cell5's missing syringe pump (add the `[pump]` table back when it
arrives). The runbook is [`docs/L1_BRINGUP.md`](docs/L1_BRINGUP.md).
In short:

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

Hard-won bench rules from the 2026-07-28 bring-up (details in
[`LearnedPatterns.md`](LearnedPatterns.md) #11–#14):

- **The hotplate's USB stays on a direct NUC port, never the shared
  hub.** Behind the hub its STM32 CDC interface wedged repeatedly and
  eventually dropped off the bus. Recovery when it wedges: unplug USB →
  hotplate off → wait 20 s → reconnect on the direct port → power on
  (the interface board is USB-powered — cycling the hotplate alone
  never resets it).
- ModemManager is disabled on NUC2 and
  `/etc/udev/rules.d/99-innocore-usb.rules` pins node permissions +
  `ID_MM_DEVICE_IGNORE` for the FTDI and STM32 VCP ids.
- **Temperature conditions need tolerance**: the RCT reports whole
  degrees and regulates just under the setpoint (panel shows 40 while
  serial says 39.0), so scenarios gate on `>= target - 1.0`.
- The `[pump]` table is optional in the cell5 config: without it the
  cell serves Z + hotplate + lamp and answers 409 on pump routes.
- A lamp `target` may be a bare IP unknown to the submodule's
  `device_list.md` — the cell synthesises the entry, so a re-DHCPed
  plug never needs an edit under `external/`.

### Field names must survive YAML

L2 scenarios are YAML, and YAML 1.1 resolves a bare `on:` **key** to a
boolean — so a field named `on` is unreachable from a scenario. The
heater/lamp routes take `enabled` instead.
See [`LearnedPatterns.md`](LearnedPatterns.md) #8.

---

## Dependencies

Python ≥ 3.12. The documented shared conda env **`sdl`** does not exist
on NUC2 — the bench runs there use a repo-local **`.venv`** instead
(created with `python3.12 -m venv --without-pip .venv` + get-pip.py,
because `python3-venv` is not installed and conda is blocked on a ToS
prompt; LearnedPatterns #10). The drivers come from the `external/`
submodules as editable installs, so a fresh checkout needs both:

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
