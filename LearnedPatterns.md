# LearnedPatterns — InnoCORESDL

Running log of gotchas hit while building the combined SY-01B pump +
Entris-II balance scripts in this folder. Append a new entry whenever a
non-obvious problem is solved, using the **Problem / Cause / Fix / Rule**
format below. Newest entries at the bottom.

---

## 1. Bi-pass (M05) valve: firmware ports 1↔3 are the SAME fluid state

- **Problem**: With the dispense routine set to aspirate from valve port 3
  and dispense from port 1 (`SOURCE_PORT=3, DISPENSE_PORT=1`), the syringe
  drew and expelled at the **same** physical tube (the tip on port 1) and
  the reservoir was never drawn from — "nothing comes out." The valve and
  plunger moved correctly at the wire level (`/1I3R`, `/1A4800R`, `/1I1R`,
  `/1A0R` all sent; `?6` stepped 1→2→3 then back to 1), which masked the
  real issue for a long time.
- **Cause**: The bench valve is a Runze **M05 Bi-pass Flow Path** valve
  (the "MCC-4"), which has only **two** physical fluid states 90° apart:
  `C-1/2-3` (syringe↔physical port 1) and `C-3/1-2` (syringe↔physical
  port 3). The firmware is configured as a 4-way distribution valve, so it
  maps port digits to rotor angles 90° apart. Because the rotor pattern
  repeats every 180°, **firmware ports 1 and 3 land on the same fluid
  state** (and 2 and 4 on the other). Commanding 1↔3 is a 180° rotation
  that returns to the *same* connection — `?6` reports a different digit
  but the fluid path is identical. So aspirate@3 and dispense@1 were both
  the `C-1/2-3` (tip) state.
- **Fix**: Use a **90°-apart** port pair so the two commands hit different
  fluid states. Empirically confirmed on this bench: aspirate from
  **firmware port 2** (`C-3/1-2` → reservoir) and dispense from **firmware
  port 1** (`C-1/2-3` → tip). Firmware port 4 is interchangeable with 2,
  and firmware port 3 with 1.
- **Rule**: On a Bi-pass / dual-selection valve driven as a distribution
  valve, never assume `move_valve_to_port(n)` changing the `?6` digit means
  the fluid path changed. The two real states are 90° apart — pick source
  and sink ports that differ by 90° (e.g. 1 & 2), not 180° (1 & 3). Verify
  by watching which physical tube actually moves liquid, not by `?6`.

> Note: the SyringePumpController server's `/v1/prime` defaults
> (`source_port=3, sink_port=1`) have the **same** 180° bug; the ESP32
> path only works when the operator manually selects a 90°-apart pair.

---

## 2. Balance `read_stable_weight` returns too early after a dispense

- **Problem**: Reading the post-dispense mass with a single
  `read_stable_weight()` grabbed a value before the liquid had finished
  settling, so masses were recorded low/inconsistent and the script moved
  on without the weight truly confirmed.
- **Cause**: Under `COM.OUTP = AUTO W/` the balance auto-pushes a value on
  each stability event, and the *first* event after a dispense can fire
  early (droplet still spreading / line relaxing). Taking that first pushed
  value trusts the balance's loose, momentary stability call. A value
  buffered during the dispense could also be returned immediately.
- **Fix**: Added `read_settled_weight()` — it flushes the dispense
  transient, then waits until `SETTLE_AGREEMENT_READS` (default 3)
  consecutive stable readings agree within `SETTLE_TOLERANCE_G` (default
  0.001 g, ≈ the BCE224I's ~1 mg auto-push jitter) before accepting,
  bounded by a generous `SETTLE_TIMEOUT_S` (30 s). A per-read timeout means
  the pan went quiet → settled.
- **Rule**: Never trust a single auto-pushed "stable" reading for a value
  that changes right before the read. Require N consecutive in-tolerance
  readings (settling by agreement), and set the tolerance no tighter than
  the balance's own jitter or it will never converge.

---

## 3. `read_stable_weight` times out — balance only streams `Stat`

- **Problem**: At `confirm_zero`, `read_stable_weight()` raised
  `TimeoutError: no stable reading within 30.0s under AUTO W/`. Raw
  listening showed the balance *was* pushing (AUTO W/ on), but every line
  was `Stat` (its unstable indicator) — it never reported a numeric weight,
  so the read never returned.
- **Cause**: The pan never reached the balance's stability criterion — a
  noisy/disturbed setup (dispense-tube tension on the vial, vibration,
  draft) and/or too-strict ambient/STAB.RNG filtering. `Stat` carries no
  digits, so no value can be parsed regardless of timeout or
  `TARE_TOLERANCE_G` (which is only checked *after* a numeric read).
- **Fix**: Set the ambient filter looser over SBI — `scale.set_ambient(
  "very_unstable")` (Esc N) at startup (config `BALANCE_AMBIENT`). Heavier
  filtering lets the balance declare stability in a noisy environment;
  confirmed live (read returned −0.0036 g instead of timing out). Best
  paired with physically steadying the pan (remove tube tension/drafts) for
  accuracy; `STAB.RNG = V.FAST` is the menu-only complement.
- **Rule**: A `read_stable_weight` timeout with the balance streaming
  `Stat` is an instability problem, not a code or tare-tolerance one. Loosen
  ambient (`Esc K/L/M/N`) and steady the pan; raising `TARE_TOLERANCE_G`
  does nothing (it runs after the read).

---

## 4. PumpGantryCell X axis: `home_dir_x=0x00` without invert drives X into its home limit

- **Problem**: First bench bring-up of the XZ gantry through `PumpGantryCell`.
  Homing succeeded (Z re-squared from a racked state, all three motors
  landed on IN_1). Z `move_to(100)` worked (Z_A/Z_B = 100.02 mm), but X
  `move_to(100)` printed `[LIMIT] Motor stopped by limit switch` immediately
  and X never left 0 mm.
- **Cause**: `Config.home_dir_x` was copied from `bridge.py` (`0x00`), which
  is a **jog-only** UI and never does absolute `move_to` from home. X's
  encoder-positive direction points *into* its home limit, so with no
  `coord_invert`, `move_to(+mm)` emits `+coord` and drives X straight back
  into the IN_1 limit it's sitting on. Z only worked because it already had
  `z_coord_invert=True` (its limit wires were swapped), which flips +mm to
  `-coord` (away from home). The legacy `CVMeasure.py` sidestepped this
  asymmetrically with `HOME_DIR_X=0x01` (home X at the opposite end, no
  invert) — equivalent but inconsistent with Z.
- **Fix**: Treat X exactly like Z — add `x_coord_invert: bool = True` to
  `Config` and apply it in `open()` (`x.coord_invert = config.x_coord_invert`,
  since `open_xz` only exposes the Z pair's invert). Both axes now home at
  the `0x00` end and `move_to(+mm)` travels into the work via `-coord`.
  Verified live: X `move_to(100)` → coord `0x-28000`, X = 100.02 mm; back to
  0 stopped on the limit at 0.08 mm. No re-home needed — the encoder zero is
  unchanged, only the coord sign flips.
- **Rule**: `home_dir`/`coord_invert` are per-axis and must be validated with
  an actual absolute `move_to` off the home limit, not assumed from the jog
  bridge. A motor commanded toward the limit it already rests on stops
  instantly at 0 — that's a direction-config bug, not a hardware fault.
  Keep all gantry axes on one convention (home at `0x00` + `coord_invert`)
  so +mm always means "into the working travel."

## 5. The L2 spec's example scenario used field names L1 does not have

- **Problem**: Building the L2 orchestrator from
  `docs/L2_ORCHESTRATOR_SPEC.md`, the section 8.1 examples call
  `pump/dispense` with `{volume_uL: …}`, `gantry/move` with `{x: …, z: …}`,
  and read a mass as `${measured.grams}`. Copied verbatim into a scenario,
  every one of those steps would have failed at the bench — after the
  hardware had already moved through the earlier steps.
- **Cause**: The spec is a design document written ahead of the code. L1's
  actual contract is `VolumeRequest.target_uL`, `GantryMoveRequest.x_mm` /
  `z_mm`, and `WeightReadResponse.weight_g` (`server/schemas.py`). A design
  doc's example payloads are illustrations, not an interface.
- **Fix**: The scenario validator resolves every action path and body field
  against the cell's live `GET /openapi.json` instead of a table baked into
  L2 (`orchestrator/scenario.py`), so a wrong field name is a dry-run error
  before anything moves; `scenarios/demo_linear_move.yaml` was written from
  `server/routes.py` + `server/schemas.py`, and the spec was corrected to
  match (v0.9), not the other way round.
- **Rule**: The code is the interface; the spec is intent. When they
  disagree, fix the spec. Never take endpoint paths, field names, or units
  from a design document — read the route and the request model, and let
  the dry run catch what you miss.

## 6. cell4 reports the linear rail's position as `stage_x_mm`, not `y_mm`

- **Problem**: A scenario that moves the rail with `linear/move` (which
  answers `{y_mm: …}`) and then verifies the position with `GET /v1/status`
  found no `y_mm` field anywhere in the status body.
- **Cause**: `StatusResponse` is shared by both cell shapes and only has
  `stage_x_mm` / `stage_z_mm`. `BalanceLinearCell.status()` therefore
  reports the rail's mm position in **`stage_x_mm`**
  (`cell/balance_linear_cell.py:113`) — the same physical axis is `y_mm` on
  the way in and `stage_x_mm` on the way out.
- **Fix**: Assert on the `linear/move` response (`y_mm`) for the move
  itself, and on `stage_x_mm` when cross-checking through `status`.
  `scenarios/demo_linear_move.yaml` does both, and says why inline.
- **Rule**: A shared response model can rename an axis across cell shapes.
  Check the field a *given cell* fills in, not the one the request used.

## 7. `POST /v1/stop` does not stop the linear rail (software e-stop gap)

- **Problem**: The L2 abort path broadcasts `POST /v1/stop` to every cell as
  a software e-stop. On cell4 the rail keeps moving.
- **Cause**: `BalanceLinearCell.stop()` is an intentional no-op
  (`cell/balance_linear_cell.py:205`) — the MINAS RS485 standard protocol
  driver exposes no asynchronous halt, so stopping was left to the bench
  interlock. The gantry cells do the opposite: `stop()` calls
  `MKSMotor.stop_group_hard(...)`, a real hard stop.
- **Fix**: Recorded as GAP-1 in `docs/L1_AUDIT.md` (M0/A4) and stated at
  every place that promises a stop: the engine's `abort` docstring, the
  config comment, and the demo scenario header. Closing it means extending
  L1 through the `ADDING_A_CELL.md` procedure with user approval.
- **Rule**: "Broadcast stop to all cells" is only as strong as each cell's
  `stop()`. Before trusting a software e-stop, read every implementation —
  and never let a cell whose stop is a no-op run unattended.

## 8. A JSON field named `on` is unreachable from a YAML scenario

- **Problem**: Cell D's heater and lamp routes were written as
  `POST /v1/hotplate/heater {"on": true}` — the obvious spelling. The
  scenario step
  ```yaml
      body:
        on: true
  ```
  failed dry-run validation with `steps.6.body.1.[key]: Input should be a
  valid string`, and the JSON itself explained nothing.
- **Cause**: YAML 1.1 (what PyYAML implements) resolves the bare scalars
  `on`/`off`/`yes`/`no`/`y`/`n` to booleans — **including when they are
  mapping keys**. `yaml.safe_load("on: true")` returns `{True: True}`, so
  the step body arrived with a boolean key and never matched the schema.
  Quoting (`"on": true`) works, but a field whose only correct spelling is
  the quoted one is a trap for whoever writes the next scenario.
- **Fix**: Renamed the field to `enabled` in `HeaterRequest`,
  `StirrerRequest` and `LampRequest` (`server/schemas.py`) and in the cell
  methods, so the natural YAML spelling is the correct one. A related trap
  in the same area: `assert` expressions are a Python subset, so an
  interpolated boolean must render as `True`/`False`, not `true`/`false` —
  `orchestrator/scenario.py` now emits Python literals *and* accepts
  `true`/`false`/`null` as names, so either spelling works in an assert.
- **Rule**: The scenario language is YAML, so an API field name must
  survive YAML's scalar resolution. Avoid `on`, `off`, `yes`, `no`, `y`,
  `n`, `true`, `false`, `null` as field names — and remember the rule
  applies to keys, not just values.

## 9. `POST /v1/stop` waits for the lock the move is holding — so it never interrupts anything

- **Problem**: The L2 abort path broadcasts `POST /v1/stop` to every cell
  and calls it a software e-stop. Writing the bench probe for it (A4), the
  question came up: does that request actually reach the driver while an
  axis is moving?
- **Cause**: It does not. Every state-changing route in `server/routes.py`
  holds `app.state.lock` for the whole device interaction — **including
  `/v1/stop`**. An `asyncio.Lock` is FIFO, so the stop request simply
  queues behind the move it was meant to interrupt. Measured with the real
  L1 app over a stub cell whose move takes 5 s (no hardware): move ran
  0.18→5.18 s, the stop was requested at 1.00 s, and `cell.stop()` executed
  at **5.18 s** — after the motion had already completed on its own. The
  HTTP call itself blocked for 4.2 s.
  This is independent of, and worse than, LearnedPatterns #7: even the
  cells whose `stop()` is a genuine hard stop (`stop_group_hard` on the
  gantry) never get to run it in time.
- **Fix**: Recorded as GAP-9 in `docs/L1_AUDIT.md` with the measurement,
  and `claude_test/smoke_l1.py --suite stop` now times the stop against the
  move and reports `preempted=False` automatically. The real fix is an L1
  change needing user approval: serve `/v1/stop` lock-free (as
  `/v1/health` already is) **and** give each driver a priority path for its
  stop command — firing an MKS `F7` on an FTDI handle another thread is
  mid-command on is not safe just because the HTTP layer stopped waiting.
  Until then the physical e-stop is the only stop, on every cell.
- **Rule**: A safety endpoint must not share the mutex with the operation
  it aborts. When you add a "stop"/"abort"/"cancel" route, check what it
  waits on before you believe it — and prove it with a timestamped probe
  against a deliberately slow operation, which costs nothing and needs no
  hardware.

## 10. This NUC's Python needs a bootstrap: no `sdl` env, conda blocked by ToS, `python3-venv` missing

- **Problem**: `pip install -r requirements.txt` had nowhere to go on
  NUC2: no `sdl` conda env exists, `conda create` fails until Anaconda's
  channel Terms of Service are accepted (a licensing decision, not a
  technical one), the Debian `python3` is PEP 668 externally-managed, and
  `python3 -m venv` fails because the `python3-venv` apt package (bundled
  `ensurepip`) is not installed — and installing it needs sudo.
- **Cause**: Bare-metal NUC image with stock Debian Python plus a
  personal Anaconda install; none of the documented paths (`conda
  activate sdl`) exist on this machine.
- **Fix**: `python3.12 -m venv --without-pip .venv`, then bootstrap pip
  with `curl -sSL https://bootstrap.pypa.io/get-pip.py |
  .venv/bin/python -`, then `.venv/bin/pip install -r requirements.txt`.
  `.venv/` is already gitignored.
- **Rule**: `--without-pip` + get-pip.py turns a venv-less Debian Python
  into a working venv with no sudo and no conda. Check `.venv/bin/python
  -V` matches the project's floor (3.12) before installing.

## 11. The Tapo plug's IP drifts; the fix belongs in the cell, not the submodule

- **Problem**: Cell D's IR-lamp plug answers at 192.168.0.237, but the
  driver's `device_list.md` (inside the `SmartPlugController` submodule)
  still says 192.168.1.239 — `resolve_targets()` matched nothing and the
  lamp was unreachable by name or by its real IP.
- **Cause**: The plug is on DHCP and the bench moved to the 192.168.0.x
  subnet. The device list lives *inside the submodule*, and CLAUDE.md
  forbids local-only edits under `external/` — so the obvious one-line
  fix was the wrong one.
- **Fix**: `PumpZThermalCell._resolve_lamp()`: when `lamp_target` parses
  as an IP address and matches no list entry, the cell synthesises a
  `DeviceEntry` for it. The bench config (`server/nuc2/cell5.toml`) sets
  `target = "192.168.0.237"`; the submodule stays untouched.
- **Rule**: Bench-local facts (IPs, serials, ports) go in the gitignored
  cell config, never into a submodule's tracked files. When a driver's
  registry file can go stale, give the cell a config-driven escape hatch
  and record the current value in the `.example` as a comment.

## 12. The RCT digital's USB interface wedges under sustained polling — and only power does what reset cannot

- **Problem**: During Cell D bring-up, `hotplate/state` (six serial
  queries per call) failed 8/10 under a tight loop; continued probing
  then silenced the device completely — even raw `IN_NAME` at correct
  7E1 framing got nothing — and finally `0483:5740` vanished from the
  USB bus. `usb.core`'s device reset and replugging the cable did not
  bring it back.
- **Cause**: The `ika` package sends with a fixed 0.1 s post-write
  sleep, never flushes the input buffer, and never retries. One late
  reply desyncs every following exchange, and continued pressure
  crashes the RCT's USB CDC firmware outright. A crashed CDC interface
  does not respond to a USB bus reset; it needs the device's own power
  to cycle.
- **Fix**: Upstream `HotplateController` PR #8 (`fix/serial-robustness`,
  pinned here at 2f3b8d6): 0.25 s minimum gap between exchanges, input
  flush before each, one retry after 0.3 s. Recovery procedure: hotplate
  power off → replug USB → power on; then re-apply device-node
  permissions (a replug resets them).
- **Rule**: Never poll the RCT back-to-back — keep ≥0.25 s between
  serial exchanges, and treat "device stopped answering" as a firmware
  wedge: stop querying immediately and power-cycle the device instead
  of retrying harder.
