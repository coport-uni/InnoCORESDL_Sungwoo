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

**cell4 runs on real hardware.** On 2026-07-28 the balance + linear-rail
cell completed `demo_weigh_at_position.yaml` end to end through L2 —
15/15 steps, run `20260728T111725Z-demo_weigh_at_position` — zeroing the
balance, carrying it 50 mm, weighing a vial at 25.7424 g and returning.
The other cell shapes have not been through the same bring-up.

| Layer | State |
|---|---|
| L1 cell4 (balance + linear) | **bench-verified**: identity, status, tare, weigh, home, move |
| L1 cell1–3 / cell5 | code complete; cell1's gantry is mid bring-up (LearnedPatterns #22–#25), cell5 untouched by hardware |
| L1 `/v1` server | 26 routes, serving cell4 against real devices |
| L2 orchestrator | registry, client, validator, engine, runlog, `/v1`, CLI — 71 tests, plus real runs against cell4 |
| Deployment | systemd template + Docker Compose, **never deployed to a NUC** |

What the first real run measured, and what it settled:

| Question | Answer |
|---|---|
| Can cell4 weigh somewhere other than where it settles? | **Yes.** Carrying the balance 50 mm and back shifted a 25.7 g reading by **0.0039 g** |
| How fast is a settled weight read? | ~1.2 s (stream 2.6 lines/s, consecutive-3 spread median 0.0005 g) |
| How fast is a 50 mm move? | ~6 s |
| Is the RS485 link reliable? | **No** — see the EMI note below. Reads survive it; moves abort on it, deliberately |

Every number above is a bench measurement. The 71 tests touch no
hardware, which is the point: they cannot tell you any of this.

### Safety gaps you must know before touching hardware

| | |
|---|---|
| **GAP-9** | `POST /v1/stop` **cannot interrupt a command already in flight** — on any cell. It waits for the same lock the move holds; measured 4.2 s late on a 5 s move. L2's abort inherits this. |
| **GAP-1** | cell4's `stop()` is a no-op even when it does run. |
| **No alarm visibility** | the MINAS driver cannot read or clear an amp alarm, so `diagnose()` reports `stage.ok: true` on an amp that has tripped and de-energised its servo. The front panel is the only alarm indicator ([#15](https://github.com/coport-uni/InnoCORESDL_Sungwoo/issues/15)). |

**Consequence: the physical e-stop is the only stop.** Both gaps, with their
measurements and proposed fixes, are in [`docs/L1_AUDIT.md`](docs/L1_AUDIT.md).

### Two known faults deliberately left open

Both are understood, reproduced and documented; neither is being fixed
yet, and running the bench with them open is a decision rather than an
oversight.

**[#13](https://github.com/coport-uni/InnoCORESDL_Sungwoo/issues/13) — the
amp jams its own RS485 link.** Deferred to a later bench session, because
the fix is electrical (grounding and termination, see the note below) and
not something software can close. Meanwhile the link is *usable*, not
fixed: reads are absorbed by the driver's reconnect, and a move that
straddles a drop is stopped and failed rather than resumed. So a run can
still abort partway on a link drop. Re-run it; that is the abort rule
working.

**[#15](https://github.com/coport-uni/InnoCORESDL_Sungwoo/issues/15) — the
driver cannot read an amp alarm.** Left open on purpose: developing the
alarm read means *reproducing* an alarm, and the only alarm this bench
knows how to raise is Err16.0 — driving the rail into its mechanical stop
until the motor overloads. That is not a thing to do repeatedly to a servo
for the sake of a nicer error message.

The operator carries this one instead. **If moves stop having any effect
while the amp still answers `/v1/diagnose` normally, read the amp's front
panel** — that combination means an alarm, and software will keep
reporting `stage.ok: true` throughout. Recovery is in the bench note
below. If the alarm read is implemented later, decode the response against
the manual rather than by provoking the amp.

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
| [`scenarios/`](scenarios/) | scenario files — `demo_linear_move.yaml`, `demo_weigh_at_position.yaml` (cell4, bench-verified), `demo_cell_d_warmup.yaml` |
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
python -m orchestrator run      scenarios/demo_linear_move.yaml

# checks that need no hardware
pytest claude_test
ruff check cell/ server/ orchestrator/ claude_test/
```

Runs stop where a human is needed and nowhere else: once before the first
motion step (`confirm_first_motion`, CLAUDE.md folder rule #3) and once at
any step carrying a `pause:` — `demo_weigh_at_position.yaml` uses one to
have the vial loaded. `--step-mode` still exists and stops after *every*
step; prefer it only when debugging, since thirteen prompts of which one
matters is how an operator stops reading them.

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
| M0 — L1 adequacy audit | code review done; cell4's physical checks done, other cells pending; 9 gaps recorded |
| M1 / M2 — registry + dry-run validator | done |
| M4 / M5 — engine, runlog, failure policies, pause/resume/abort | done; exercised by real cell4 runs |
| M6 — systemd + Docker + real `demo_linear_move` | **`demo_linear_move` and `demo_weigh_at_position` both run on cell4**; systemd/Compose artifacts still never deployed |
| M7 — web scenario tab | not started; `web/` was removed with the other pre-L2 work and lives in git history |

Open questions that shape the next phase, all recorded as gaps:

- **GAP-9 / GAP-1** — make the software stop actually stop something. Needs
  an L1 change plus driver work; user approval required.
- **#13 (RS485/EMI)** — deferred to a later bench session; the fix is
  electrical, and the software already limits the damage (reads reconnect,
  moves abort).
- **#15 (amp alarm read)** — deferred deliberately, because building it
  means repeatedly overloading a servo to reproduce the alarm. Documented
  as an operator check instead.
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

`DAT.REC = SBI`, `COM.OUTP = AUTO.W/O`, `STAB.RNG = V.FAST`; USB-C SBI
defaults 9600 / odd / 8 / 1. (`AUTO W/` — *with* stability — only speaks
once the balance calls itself settled, which on this bench it never did:
20 s of listening captured zero lines. `AUTO.W/O` streams unconditionally
and the driver judges settling, as above.) The PC lead is an ordered
accessory, `YCC-USB-C-A`; a charge-only cable produces **no kernel events
at all**, which is its own diagnosis (LearnedPatterns #11). A `0x15` (NAK) reply means the balance is in
xBPI mode — wrong interface menu. The ambient filter comes from the cell
config, not the panel.

### Cell D specifics

The hotplate has a cell-level `max_celsius` ceiling in its config, checked
before the driver is called. The IR lamp's plug credentials live in
`external/SmartPlugController/secure.env`, written by the operator — Claude
Code does not read that file. Never run the hotplate driver's own dashboard
(`hotplate_controller/server.py`) while cell5 is up: one owner per port.

### The rail must not park on 0 mm (critical)

cell4's 0 mm encoder origin sits **on the mechanical end stop**, and the
closed loop coasts 1.5–1.8 mm past its target. Homing to 0 therefore
drives the carriage into the stop and holds it there until the amp trips
**Err16.0** (motor overload) and de-energises the servo — after which every
move displaces 0 mm while the amp still answers serial normally.

This is the alarm referred to under "known faults left open": while it is
latched, `/v1/diagnose` keeps answering `stage.ok: true` and every move
reports a stall, so the front panel is the only place the truth appears.

The rail parks at `home_mm` (default **5.0 mm**) in the cell config, and
both scenarios use a matching `home_mm` param. Keep the two in step, and
keep the clearance well above the coast distance. Recovery is: pull the
rail clear of the stop **by hand first** (the servo is off, so it moves
freely), *then* clear the alarm — clearing while still pressed re-trips it.
See [`LearnedPatterns.md`](LearnedPatterns.md) #27 and issue
[#15](https://github.com/coport-uni/InnoCORESDL_Sungwoo/issues/15).

### The servo amp jams its own RS485 link

The MINAS amp couples conducted noise back through the RS485 pair, which
knocks the Moxa UPort off USB every few seconds. Measured: amp off → 0
re-enumerations per 40 s; amp on → 15; amp on with the RS485 cable
unplugged → 0. It is **conducted, not radiated**, so a shielded USB cable
and ferrites on the USB side are the wrong purchase — the fix is on the
RS485 side (SG run between the ends, 120 Ω termination once per end,
shield grounded at one end only), and only then an isolated adapter.

The driver absorbs this for **reads** — it reopens the port and 959/959
position reads landed across 18 re-enumerations. It deliberately does
**not** absorb it for moves: a move that straddles a reconnect is stopped
and failed, because the alternative is a rail travelling on stale position
data with no software stop. So while the link is bad, moves keep aborting;
that is the design, not a regression.
See [`LearnedPatterns.md`](LearnedPatterns.md) #20, #26 and issue
[#13](https://github.com/coport-uni/InnoCORESDL_Sungwoo/issues/13).

### Balance settling is judged in software

The bench runs `COM.OUTP = AUTO.W/O` — the balance streams whether or not
it calls itself stable — and the driver accepts a value once three
consecutive readings agree within 0.002 g. That tolerance is measured, not
guessed: consecutive-3 spread runs median 0.0005 g, p90 0.0009 g. A
healthy settle costs ~1.2 s against a 60 s budget, so **hitting the
timeout means the stream went silent, not that the balance was slow.**

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
