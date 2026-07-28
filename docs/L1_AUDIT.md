# L1 Adequacy Audit (M0)

Deliverable of spec section 4 (`docs/L2_ORCHESTRATOR_SPEC.md`). M0 is the
prerequisite for M4 and later; M1 and M2 may proceed in parallel.

| | |
|---|---|
| Started | 2026-07-27 |
| Static review basis | L1 source at commit `faadb5d` (`cell/`, and `server/` read from git — it is currently deleted in the working tree) |
| Hardware | **not connected** — every physical check below is `pending` |
| Last updated | 2026-07-28 — **GAP-9 found and measured**: `/v1/stop` cannot preempt an in-flight command on any cell |
| Verdicts | `pass` / `gap` / `blocked` / `pending` |

**Safety rule for everything in section 4.2 below: no hardware command is
issued unless the operator is present, has been told what will move and
how far, and has approved it. If nobody is at the bench, the row stays
`pending`.** (CLAUDE.md Folder-specific rules #3, spec section 12.8.)

---

## A1 — Does every whiteboard device have an L1 cell server?

Verdict: **gap** (one device missing, two out of scope).

| Whiteboard | NUC | Repo mapping | State |
|---|---|---|---|
| Balance + Linear Rail | NUC1 | cell4 `BalanceLinearCell` :17060 | `pass` — implemented |
| Cell A | NUC1 | cell1 `PumpGantryCell` :17054 | `pass` — implemented |
| Cell B / C | NUC2 | cell2 :17056, cell3 :17058 | `pass` — config-only clones of cell1; examples added at `server/nuc2/cell{2,3}.toml.example`. USB identifiers are TBD (GAP-5) |
| Cell 5 | NUC2 | cell5 :17062 — **4 devices** (confirmed 2026-07-27): syringe pump + 1 MKS motor (single Z) + 1 hotplate + 1 Tapo plug | **`pass` (code) / `pending` (bench)** — `PumpZThermalCell` implemented 2026-07-27 with the `zstage` / `hotplate` / `lamp` action sets; config example at `server/nuc2/cell5.toml.example`. Never run against hardware (GAP-2 now narrows to bench bring-up + the TBD identifiers) |
| Synthesis robot | NUC1 | none (confirmed out of scope) | n/a |
| Analysis robot | NUC2 | none (confirmed out of scope) | n/a |

Sub-question from spec section 2 — *does `PumpGantryCell` support X1 + two
synchronized Z axes?* Static answer: **yes**. `PumpGantryCell.open()`
takes one `serial_x` and auto-assigns the two remaining FTDI adapters as
the paired Z; `move_gantry` issues a single Z command that the
`mks_motor` driver executes as a group (`move_sync` / `home_xz` /
`stop_group_hard`, `external/ESP32S3BOX3MotorController/src/mks_motor/mks_motor.py:965`).
So the synchronization is **software** (grouped commands with a desync
interlock), not parallel wiring. Physical confirmation is `pending` — see
the gantry row in 4.2.

## A2 — Do all cells implement health / diagnose / status / stop?

Verdict: **pass** (statically), physical confirmation `pending`.

`server/routes.py` exposes `GET /v1/health` (lock-free), `GET /v1/diagnose`,
`GET /v1/status`, `POST /v1/stop` for every cell shape, and both
`PumpGantryCell` and `BalanceLinearCell` implement all four. Methods that
do not apply to a shape raise `WrongStateError` → HTTP 409 rather than
crashing (`_no_pump()` / `_no_gantry()` in `cell/balance_linear_cell.py`).

Caveat for L2: `GET /v1/status` takes the cell's device lock, so it blocks
while a motion command is in flight — only `GET /v1/health` is safe to
poll during a move. The orchestrator's `GET /v1/cells` therefore defaults
to health only (`with_status=false`).

Two further gaps in the substrate, both L2-relevant (GAP-7):

- **`status.busy` is hardcoded `False`** in both cells
  (`cell/pump_gantry_cell.py:140`, `cell/balance_linear_cell.py:107`), so
  it carries no information. L2 must not use it as a progress signal; the
  only truthful "is it moving" indicator today is that the synchronous
  call has not returned yet.
- **No cell identity on a lock-free endpoint.** `HealthResponse` has
  `cell_up` / `pump_ok` / `balance_ok` / `stage_ok` / `driver_versions`
  but no cell name or shape, so if two `base_url`s are swapped in
  `orchestrator/config.toml`, L2 cannot detect it. Identity does exist in
  `GET /v1/diagnose` (device serial numbers, plus `present: false`
  markers that reveal the shape), but that probes the hardware and takes
  the device lock, so it is a commissioning check, not a poll.

## A3 — Is every endpoint's worst case within 120 s of synchronous HTTP?

Verdict: **pending** — needs the bench.

Static notes: the L1 server sets `timeout_keep_alive=120`, so long
synchronous calls were anticipated. The two unknowns that matter are
`linear/move` (a converging closed loop with a ±0.1 mm tolerance — its
settling time is a measurement, not a constant) and `gantry/home`. Record
measured durations in 4.2 and then replace the placeholder `timeout_s: 60`
values in `scenarios/demo_linear_move.yaml`.

## A4 — Does `POST /v1/stop` actually stop moving hardware?

Verdict: **gap — two independent defects, and the second one is the most
important finding of this audit.**

**(a) GAP-9 — the stop route cannot preempt anything, on any cell.**
`POST /v1/stop` acquires the same `app.state.lock` that the in-flight
command holds for its whole duration (`server/routes.py`), so the stop
request *queues behind the motion it was meant to interrupt*.

Measured off-bench on 2026-07-28, with the real L1 app driving a stub cell
whose move takes 5 s (no hardware involved):

```
move started      t = 0.18 s
POST /v1/stop     t = 1.00 s   <- request sent, mid-move
cell.stop() ran   t = 5.18 s   <- only after the move finished
stop responded    t = 5.18 s   (the HTTP call blocked for 4.2 s)
```

So the gantry's real hard stop (`stop_group_hard`) never gets to run in
time either, and L2's abort broadcast inherits the same defect. **Until
this is fixed the physical e-stop is the only stop, on every cell.**

Fix direction (needs user approval — it is an L1 change): serve `/v1/stop`
**lock-free**, the way `/v1/health` already is, and make each cell's
`stop()` safe to call while another thread is mid-command. That second half
is driver work — firing an MKS `F7` on an FTDI handle another thread is
using needs a driver-level priority path, not just a lock removal here.

**(b) GAP-1 — cell4's `stop()` is a no-op even when it does run.**

| Cell | `stop()` | Effect if it were reached in time |
|---|---|---|
| cell1–3 `PumpGantryCell` | `MKSMotor.stop_group_hard(z + x)` then the pump's halt | real hard stop of the gantry group |
| cell5 `PumpZThermalCell` | motor + pump + heater + stirrer + lamp, all attempted | real stop of motion *and* heat/power |
| cell4 `BalanceLinearCell` | **`pass` (a no-op)** — `cell/balance_linear_cell.py:205` | **nothing** |

Both defects are exercised by `claude_test/smoke_l1.py --suite stop`, which
times the stop against the move and reports `preempted=False` when the stop
queued.

## A5 — Do errors map consistently onto HTTP status codes?

Verdict: **pass**.

`server/errors.py` maps `InvalidArgError`→400, `WrongStateError`→409,
`TransportError`→503, `CellTimeoutError`→504, `DeviceFaultError`→500, bare
`CellError`→500, `ValueError`→400, with the envelope
`{error, code, command, message}`. The orchestrator reuses that exact
envelope (`orchestrator/schemas.py::ErrorResponse`) and preserves the cell's
body inside `CellCallError.payload`, so `on_fail` policies can distinguish
a 409 (wrong state) from a 504 (timeout).

## A6 — Is the OpenAPI schema usable by the L2 validator?

Verdict: **pass with a caveat**.

`GET /openapi.json` is served by FastAPI from the same router for both cell
shapes, so the validator can resolve action paths and request bodies
(`orchestrator/scenario.py` does exactly this; no action table is hardcoded
in L2). **Caveat:** because one router serves both shapes, cell4's OpenAPI
also advertises `gantry/*` and `pump/*`. A scenario that sends `gantry/move`
to cell4 therefore passes validation and fails at run time with a clean 409
(GAP-3). Options: have L1 build a shape-specific router, or have L2 filter
by `GET /v1/diagnose`. Not blocking — the failure is safe and legible.

Also note the field-name asymmetry the demo scenario documents: the linear
rail answers `linear/move` with `y_mm`, but `GET /v1/status` reports its
position as `stage_x_mm` (there is no `y` field in `StatusResponse`).

## A7 — What does L1 do with concurrent requests?

Verdict: **pass (serialize)** — physical confirmation `pending`.

`server/app.py` puts a single `asyncio.Lock` on `app.state`; every
state-changing route holds it for the whole device interaction and runs the
blocking driver call in a worker thread. A second client therefore **waits**
rather than getting a 409. `python -m server` runs a single uvicorn worker
on purpose (multiple workers would each open the serial handles). So even
if the L2 cell lock were bypassed, the hardware is protected — at the cost
of a caller blocking. Confirm at the bench by issuing two overlapping
`linear/move` calls and checking the second one queues (spec A7).

## A8 — Are Cell 5's not-yet-L1 devices' drivers available?

Verdict: **gap** (no `cell5` class), but **every driver Cell 5 needs
exists and its API was read** — the composition is buildable today.

Cell 5 = 4 devices (user-confirmed 2026-07-27). Per-device readiness:

| Device | Driver | API the cell calls | Ready? |
|---|---|---|---|
| Syringe pump | `sy01b` (packaged) | same as cell1–3: `open`, `diagnose`, `initialize`, `move_valve`, `aspirate`, `dispense`, `cycle` | yes — reuse `PumpGantryCell`'s pump code path verbatim |
| Z axis, 1 × MKS motor | `mks_motor` (packaged) | `MKSMotor.open(serial=…, coord_invert=…)` + `setup()`, then the **group helpers with a one-motor group**: `home_sync([z])`, `move_sync([z], …)`, `stop_group_hard([z])` | yes. Correction to an earlier draft of this audit: the group helpers **do** apply — what does not apply is the paired-Z *desync* interlock (there is no partner axis). CLAUDE.md rule #3's real content is "never call `MKSMotor._send`", because only the group helpers run the `_is_at_limit()` pre-send that absorbs the first-command-after-limit drop. Cell 5 follows that |
| Hotplate | `external/HotplateController` (`hotplate_controller.RctDigital`, path-imported, needs `ika>=2.0`) | `find_rct_port()`, `read_target_temperature()`, `set_target_temperature()`, `start_heater()`, `stop_heater()`, `start_motor()`/`stop_motor()` (stirrer), `close()`; range checks + `RctError` hierarchy already in the driver | yes |
| Tapo plug (IR lamp power) | `external/SmartPlugController` (python-kasa) | `SmartPlugController.from_files()`, `resolve_targets(name)`, `switch_many(entries, turn_on=…)` (**async** — the cell must wrap it in `asyncio.run`, as the since-removed `demo_scenario/devices.py` did), status/energy read | yes, once configured |

Notes that shape the implementation:

- The plug is **network-attached**, not serial. The one-owner-per-port rule
  does not bind it, but the cell-boundary rule does: cell5 owns it, and
  nothing else may switch it during a run.
- Plug credentials live in `external/SmartPlugController/secure.env`,
  written by the operator. **Claude Code does not read that file** — the
  `pre-read-env-guard` hook blocks it.
- The hotplate driver ships its own dashboard server
  (`hotplate_controller/server.py`). It must never run at the same time as
  cell5: same serial port, one owner.
- **Cell 5's `stop()` is a real stop**, unlike cell4's (GAP-1): it hard-stops
  the motor, halts the pump, stops the heater and the stirrer, and switches
  the lamp off — attempting all five even if one fails, then reporting what
  did not stop. This is the reference for what GAP-1 should become.
- Robots: `external/FR5Controller` is not packaged; out of scope per
  spec section 2.

---

## Gap list (each becomes a `gh` issue before it is worked)

| id | Gap | Impact | Proposed resolution |
|---|---|---|---|
| GAP-1 | `BalanceLinearCell.stop()` is a no-op | the software e-stop cannot stop the linear rail | extend L1 (`ADDING_A_CELL.md` procedure, user approval): implement a deceleration/servo-off stop on the MINAS A6 driver, or document the rail as physically-interlocked-only in the UI and the run confirmation |
| GAP-2 | ~~Cell 5 (`cell5`) does not exist~~ — **implemented 2026-07-27** | — | `cell/pump_z_thermal_cell.py` + the `zstage`/`hotplate`/`lamp` routes + `server/nuc2/cell5.toml.example`; `[cells.cell5]` is now live in the orchestrator config example. **Remaining: bench bring-up** (S5–S7 below) and the TBD identifiers in GAP-5/6 |
| GAP-9 | **`POST /v1/stop` shares the device lock, so it cannot preempt an in-flight command — on any cell** | the software e-stop only runs after the motion it was meant to interrupt has finished (measured: 4.2 s late on a 5 s move). L2's abort broadcast inherits this | serve `/v1/stop` lock-free like `/v1/health`, and give each driver a priority path for its stop command. Needs user approval + upstream driver work. **Interim: the physical e-stop is the only stop** |
| GAP-8 | The L2 lock is per **cell**; nothing models a shared physical workspace | two different cells in one `parallel` block may occupy the same space — the validator only rejects the *same* cell twice. Matters as soon as a robot arm reaches into another cell's frame | short term: a scenario-authoring rule (never put space-sharing cells in one `parallel` block) plus a note in the spec; longer term: a `workspace` key per cell in `config.toml` that the validator and the engine lock on |
| GAP-3 | OpenAPI is shape-agnostic | dry run cannot reject a wrong-shape action | accept (runtime 409), or filter by `diagnose()` in the L2 validator |
| GAP-4 | Endpoint durations unmeasured | scenario timeouts are guesses | measure in 4.2, then rewrite the `timeout_s` values in `scenarios/` |
| GAP-5 | cell2 / cell3 / cell5 USB identifiers unknown | those cells cannot be started | collect FTDI serials + CH340 paths at the bench; fill in `server/nuc2/*.toml` (udev symlinks if VID:PIDs collide) |
| GAP-6 | NUC IPs, cell5 port, safe `target_mm` unknown | the address book is placeholder-only | operator fills `orchestrator/config.toml`; confirm cell5's port against SDLClaude `ARCHITECTURE.md` |
| GAP-7 | `status.busy` is always `False`, and no lock-free endpoint identifies the cell | L2 cannot poll progress, and a swapped `base_url` goes unnoticed | low priority: have the cells set `busy` from the in-flight command, and add the cell name/shape to `HealthResponse` (an additive `/v1` change) |

---

## 4.2 Real-hardware smoke tests

All rows are **`pending`: no device is connected as of 2026-07-27.**

Run them through a live cell server's HTTP `/v1` API — never by importing a
driver (one owner per serial port). The helper does the prompting,
timing and Markdown formatting:

```bash
# start the cell server first (host, not container)
python -m server --config server/nuc1/cell4.toml

# then, in another shell:
python claude_test/smoke_l1.py --base-url http://127.0.0.1:17060 \
    --suite discovery --suite balance --suite linear \
    --out claude_test/smoke_cell4.md
```

It stops before every moving check and waits for the operator to type
`go`; there is no unattended mode. Paste its output table under the
matching row below and set the verdict.

Recommended order — lowest risk first: **balance → linear → pump → gantry**.
Doing them all in one day is not required.

| # | Device | Check | Pass criterion | Verdict |
|---|---|---|---|---|
| S1 | Balance (cell4) | `balance/tare`, then `balance/weight` empty and with a known mass | reads ~0 after tare; the mass shows up; record the AUTO W/ settling time | `pending` |
| S2 | Linear rail (cell4) | `linear/home`, `linear/move` to ~10 mm, back to 0 | round trip completes, position converges within ±0.1 mm, **durations recorded (feeds A3/GAP-4)** | `pending` |
| S3 | Pump (cell1–3, and D later) | valve to port 2, then one ~5 µL cycle to port 1 | **liquid movement confirmed by eye** — the `?6` reply proves nothing (LearnedPatterns #1) | `pending` |
| S4 | Gantry (cell1–3: X1 + synced Z2) | `gantry/home`, X ~10 mm out and back, then **one** Z command ~10 mm out and back | round trip completes; **both Z axes move together, same distance, no visible tilt**; note any first-command-after-limit drop | `pending` |
| S5 | Z stage (Cell 5, 1 × MKS motor) | `smoke_l1.py --suite zstage`: home, ~10 mm out and back, then `POST /v1/stop` mid-move | round trip completes; no X target in the request model; the stop actually halts it | `pending` |
| S6 | IR lamp via Tapo plug (Cell 5) | `--suite lamp`: read state, on, off | lamp visibly lights and goes out; the plug's `is_on` agrees. A 409 means `secure.env` is not filled in yet | `pending` |
| S7 | Hotplate (Cell 5) | `--suite hotplate`: read, set 30 °C, heater on, hold (operator-timed), heater off | setpoint accepted, temperature trends up, off restores; a setpoint above `max_celsius` is rejected with 400 before the device is touched | `pending`. Never leave the bench while it heats |
| S10 | Cell 5 end-to-end (L2) | `python -m orchestrator run scenarios/demo_cell5_warmup.yaml --step-mode` | all 14 steps pass; the run leaves heater off, lamp off, Z home | `pending` |
| S8 | Concurrency (A7) | `smoke_l1.py --suite concurrency --motion <family>` — two overlapping moves | the second queues behind the first; no interleaved motion | `pending` |
| S9 | Stop (A4) | `smoke_l1.py --suite stop --motion <family>` — a long move, then `POST /v1/stop` mid-flight | **expected to FAIL to preempt** (GAP-9). Record the measured stop-vs-move timing on each cell; on cell4 it would do nothing even if it arrived (GAP-1) | `pending` — physical e-stop in hand, this probe is deliberately testing a broken safety path |

### Result log

Append one block per session — newest at the bottom. Include the request,
the response, the measured duration, and what was physically observed.

```
### <UTC timestamp> — <device> — <operator>
| UTC | check | request | HTTP | s | verdict | observed |
|---|---|---|---|---|---|---|
```

_(no sessions recorded yet — hardware not connected)_
