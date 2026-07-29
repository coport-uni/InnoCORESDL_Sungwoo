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

## 10. Pre-flight passed a bench that could not start: auto-detect and file permissions were never checked

- **Problem**: First cell4 bring-up on NUC1 (2026-07-28). The rail and the
  balance were both physically placed on the bench,
  `claude_test/preflight.py` printed **"Pre-flight clean. Start the
  server"**, and `python -m server --config server/nuc1/cell4.toml` then
  died during startup with
  `SerialException: [Errno 13] could not open port /dev/ttyUSB1`.
  Nothing about the config was wrong.
- **Cause**: Two independent blockers, and pre-flight was blind to both.
  1. **Permissions.** `/dev/ttyUSB1` is `crw-rw---- root:dialout`, and the
     `innocorecontroller` account is in `adm cdrom sudo dip plugdev users
     lpadmin docker` — **not `dialout`**. The address resolved correctly
     (`110A:1150 -> /dev/ttyUSB1`), so pre-flight called it `ok`; resolving
     an address and being allowed to open it are different questions.
  2. **Auto-detection.** `[balance]` deliberately omits `port` so
     `entris_ii.find_port()` can find the Sartorius by VID `0x24BC`.
     Pre-flight reported that as verdict `unset`, "auto-detect at open
     time", and did **not** count it as a blocker — but `find_port()`
     returns `None` here, and `lsusb` shows no `24bc:0010` at all. The
     balance is not on the bus; only the rail (Moxa `110A:1150`), the pump
     (`1A86:7523`) and the three FTDI adapters are. `BalanceLinearCell.open()`
     requires the balance unconditionally, so cell4 cannot start without it
     even for a linear-only scenario like `demo_linear_move.yaml`.
- **Fix**: `claude_test/preflight.py` now answers both. `AUTO_DETECT` holds
  each driver's own detector signature (balance `24BC:*`, IKA RCT
  `0483:5740`), so an unset `port` is resolved against the live bus and
  reports `MISS` when nothing matches instead of a reassuring `auto`. A new
  `_access_problem()` runs `os.access(dev, R_OK|W_OK)` on every port that
  resolved and reports verdict `PERM` with the exact fix
  (`sudo usermod -aG dialout $USER`, then log out and back in). Both new
  verdicts count as blockers, so the exit code is now 1. `os.access` only
  stats the node, so pre-flight is still safe to run against a port a cell
  server already owns.
- **Rule**: "The address resolves" is not "the device will open." A
  pre-flight check that reports readiness must verify *presence*,
  *permission* and *ownership* — and a config field left to auto-detection
  is the easiest of the three to forget, because its config text looks
  perfect. Never let a deferred check print as a pass.

## 11. A USB device that produces *no* kernel log line at all is a cable, not a driver

- **Problem**: The Entris-II balance would not appear on NUC1. `lsusb`
  showed no `24bc:0010`, there was no `ttyACM*`, and
  `PrecisionScaleController.find_port()` returned `None`. The operator
  rewired the bench several times and rebooted; the result was identical
  every time.
- **Cause**: A charge-only USB-C cable. USB attach detection is purely
  electrical — a full-speed device announces itself with a 1.5 kΩ pull-up
  on D+. A cable without the data pair means the host never sees an
  attach, so **nothing is logged at all**. That absence is the diagnostic:
  `journalctl -k --since "-7 days" | grep -iE "24bc|cdc_acm|ttyACM"` was
  completely empty. A device that is present but failing would instead log
  `new full-speed USB device` followed by `device descriptor read/64,
  error -71` or `unable to enumerate`. Sartorius sells the correct lead as
  **`YCC-USB-C-A`**; nothing in any repo doc warned that the cable is an
  ordered accessory rather than an in-box item. With a data cable the
  balance enumerated instantly: `usb 3-2.1: New USB device found,
  idVendor=24bc, idProduct=0010` → `cdc_acm 3-2.1:1.0: ttyACM0`.
- **Fix**: Swap the cable. Before suspecting software, split the question
  with one command — *does the kernel see an attach event?* If not, the
  fault is upstream of every line of code: cable, connector, or power.
  The balance's own front panel can confirm the other direction:
  `DEVICE → USB → DEV.USED` reads `NONE` until it sees a host, and the
  `PC-Connect` indicator appears when it does.
- **Rule**: Never debug a driver for a device that has produced zero
  kernel events. Grep the kernel log for the VID *first*; total silence
  means no electrical connection, and no amount of configuration,
  permissions or menu settings will change it.
- **Corollary, learned the same hour**: rewiring to chase one device broke
  another. `usb 3-7-port1: disabled by hub (EMI?)` began at 16:07:09 and
  repeated 118 times at ~4.5 s intervals — the CH340 pump, which had been
  stable on hub port `3-2.4` for hours, was moved to `3-7.1` and started
  dropping out continuously (`device descriptor read/64, error -32`,
  over-current count 0, so signal integrity rather than power). After any
  rewiring, re-read the kernel log for devices you did **not** touch.

## 12. The cell layer called three driver methods that never existed

- **Problem**: With the balance finally connected and `dialout` granted,
  `python -m server --config server/nuc1/cell4.toml` still died at
  startup: `AttributeError: 'PrecisionScaleController' object has no
  attribute 'set_ambient'`.
- **Cause**: `cell/balance_linear_cell.py` calls `set_ambient()`,
  `tare()` and `flush_pending_reads()`. None of the three has ever
  existed in `external/PrecisionScaleController` — confirmed against the
  full 66-commit history with `git log -S`, not just the current pin, so
  this was never API drift. The cell was written against an imagined
  driver. It survived review because the only tests that exercise cell4
  are the orchestrator's, and those talk to `FakeL1`, an httpx
  `MockTransport` — **32 passing tests that never import a driver**. The
  linear half of the same file was fine, which is why the mismatch looked
  like a working cell.
- **Fix**: Implemented the three methods upstream in the driver
  (`feat/ambient-tare-flush`), sourced from the Technical Note p.4
  command table rather than guessed: ambient is `Esc K/L/M/N`
  (very stable → very unstable) and tare is `Esc T`;
  `flush_pending_reads` drops the AUTO W/ backlog. Verified on the real
  BCE224I-1SKR — `tare()` moved the stable reading from `-0.0048 g` to
  `0.0 g`, which is behavioural proof rather than the absence of an
  exception.
- **Rule**: A mock that you also wrote cannot tell you the real API
  exists. For any cell method that reaches hardware, check the call
  against the driver's actual signature — `dir()` or `git log -S` costs
  seconds — before believing a green test suite.

## 13. `get_serial_number()` returned a weight, because AUTO W/ races every command reply

- **Problem**: `GET /v1/diagnose` on the live cell4 answered with
  `"balance": {"model": "Model  BCE224I-1SKR", "serial_number":
  "G     +   0.0013 g"}`. The model was right; the serial number was a
  weight reading.
- **Cause**: Under `COM.OUTP = AUTO W/` the balance pushes a line on
  every stability event, unprompted. `_send_command` clears the input
  buffer before writing, but that closes only half the window: a stable
  weight pushed *between* the write and the read arrives first, and
  `_read_response` returns the first CR-LF line it sees whatever it is.
  So an ID command's reply is only correct when no stability event
  happens to land in the same few milliseconds — which is why the same
  two calls succeeded when run back-to-back by hand and failed inside
  `diagnose()`.
- **Fix**: Resolved in practice by #14's change — `diagnose()` was asking
  the balance for its model twice per call, and halving the SBI traffic
  shrank the race window enough that the serial number has come back
  correct on every call since (`SerNo.    0047304196`, 8/8). That is a
  mitigation, not a cure: the driver still trusts the first CR-LF line it
  reads, so a stability event landing in the gap would still corrupt the
  reply. The structural fix — make the ID readers skip lines that parse
  as weights, the inverse of what `read_stable_weight` already does with
  `_parse_weight_line` — is still open in `ToDo.md`.
- **Rule**: On a device that talks unprompted, a request/response helper
  is a lie unless it validates that the response it read belongs to the
  request it sent. Match the reply to the command, never to its arrival
  order.

## 14. `/v1/health` 500'd on a perfectly healthy cell, because diagnose read every device twice

- **Problem**: Minutes after cell4 first came up, `GET /v1/health` began
  returning a 500 on every call: `1 validation error for HealthResponse /
  driver_versions.linear / Input should be a valid string, input_value=None`.
  `GET /v1/diagnose` on the same live cell kept answering correctly with
  `"version": "Ver.1.016"`, so the cell was plainly fine — only the
  liveness probe said otherwise.
- **Cause**: Two defects compounding.
  1. `BalanceLinearCell.diagnose()` called `read_software_version()` twice
     per request — once for `stage.version`, once for `versions.linear` —
     and `get_model_number()` twice likewise. On the real MINAS amp the
     **second** read of a back-to-back pair came back `None`, so
     `stage.version` was right while `versions.linear` was `None` *in the
     same response*.
  2. `/v1/health` does not re-query anything; it serves
     `app.state.last_diagnose["versions"]` from cache. One bad read
     therefore poisoned every subsequent health call until the next
     diagnose, turning a transient device hiccup into a permanent 500.
- **Fix**: `diagnose()` now reads each value once into a local and reuses
  it. That alone is not enough — measured after the change,
  `read_software_version()` still returns `None` about **1 call in 5**, so
  `HealthResponse.driver_versions` was widened to `dict[str, str | None]`.
  A liveness probe must report "I could not read this version" as `null`,
  never as a 500: a cell that answers everything else correctly is up.
- **Rule**: Never call a device twice for one value you are going to
  report twice — read once, reuse. And a health/liveness endpoint must be
  the most tolerant schema in the service; if it can 500 on a field that
  is merely informational, it will eventually report a working system as
  dead.

## 15. The cell turned "I don't know where the rail is" into "the rail is exactly where you asked"

- **Problem**: The first operator-gated motion run of
  `demo_linear_move.yaml` reported `home` **ok** in 2.005 s and passed its
  `verify_home` assert (`0.0 <= 0.1`), then failed on the next step with
  `HTTP 500`. Afterwards the rail read **0.39 mm** — the same position it
  held *before* the run. The step that "passed" had not moved the rail at
  all, and the assert written specifically to catch that had confirmed the
  move instead.
- **Cause**: Two faults stacked.
  1. **The transport is genuinely unreliable.** `LinearMotorController`'s
     `_send_and_receive` returns `None` on a failed RS485 handshake and has
     no retry. Sampling `/v1/status` five times in a row returns `None`
     once — about **1 read in 5**. Separately the Moxa adapter dropped off
     USB entirely mid-run (`termios.error: (5, 'Input/output error')` on a
     handle whose device had vanished; it re-enumerated 17 s later on
     another port).
  2. **`cell/balance_linear_cell.py` papered over it.** Three call sites
     substituted a plausible number for `None`: `status()` reported
     `0.0`, `home_linear()` reported `0.0`, and `move_linear()` reported
     **the caller's own requested target**. So a dead link produced
     exactly the value each `verify_*` assert compares against — the
     failure mode was not merely silent, it was self-confirming. The
     runlog shows it plainly: `check_status -> stage_x_mm 0.0` while the
     rail was at 0.39 mm, then `home -> y_mm 0.0`, then `0.0 <= 0.1` ✓.
- **Fix**: A failed read now surfaces. `home_linear`/`move_linear` route
  through `_settled_mm()`, which raises `TransportError` (HTTP 503) with
  "the rail's position is unknown — do not trust the last reported value"
  instead of inventing one. `status()` is a probe rather than a command,
  so it reports `null`; `StatusResponse.stage_x_mm/stage_z_mm` are now
  `float | None`. Verified live: five status calls returned
  `0.39, 0.39, null, 0.39, 0.39` — the null is the read that would
  previously have claimed `0.0`.
- **Rule**: Never substitute a default for a failed measurement. The
  danger is not the missing value, it is that the default you reach for
  is almost always the "everything is fine" value — the origin for a
  home, the setpoint for a move — which is precisely what turns a broken
  sensor into a passing test. `None` means unknown; propagate it or
  raise, and let the caller decide.
- **Corollary**: An assertion can only be as trustworthy as the number it
  reads. `verify_home` was well written and still passed on a rail that
  never moved, because the value it checked was manufactured upstream. A
  verification step proves nothing about hardware unless the path
  producing its input can fail loudly.

## 16. A 10% per-read failure rate is a 100% failure rate inside a closed loop

- **Problem**: After #15 made failed reads honest, the rail's own
  reliability became the blocker. `/v1/status` sampled 20 times returned
  `None` twice — 10% of single RS485 reads failed with the rail healthy,
  the Moxa adapter stable on a direct root port, and no USB events in the
  kernel log. The motion scenario could not complete.
- **Cause**: `LinearMotorController._send_and_receive` gives up after one
  handshake — no EOT, a NAK, a short frame or a bad checksum all return
  `None` immediately. That is survivable for a one-shot read, but
  `move_to_mm` is a *software closed loop*: it calls `read_position_mm`
  once per iteration, so a move doing ~20 reads has only `0.9^20 ≈ 12%`
  chance of completing. The per-call rate looked tolerable; the
  per-operation rate was not.
- **Fix**: `_send_and_receive` gained an `attempts` parameter, with the
  handshake itself split into `_exchange`. Three attempts with a 50 ms
  backoff are applied to the **four read-only** call sites only
  (`read_software_version`, `read_model_name`,
  `read_feedback_pulse_position`, `_read_parameter`). Writes and
  execution-rights commands keep `attempts=1`: they are **not
  idempotent**, and re-sending a parameter write could apply a motion
  twice, which no amount of reliability is worth. Measured after:
  **30 consecutive reads, 0 failures.**
- **Rule**: Judge a transport's error rate at the level of the
  *operation*, not the call. Before adding a retry, split the command set
  by idempotency — reads may be retried freely, and anything that moves
  an axis or writes a parameter may not. A blanket retry on a motion
  protocol is more dangerous than the flakiness it papers over.

## 17. `move_to_mm` reports "gave up here" and "arrived here" with the same value

- **Problem**: The second real motion run got 8 of 9 steps through and
  failed the last assert: `move_back` was commanded to 0.0 mm, returned
  `0.676`, and `verify_back_home` rejected `0.676 <= 0.1`. The outbound
  leg had been accurate (`10.097` for a 10.0 mm target), so the transport
  and the retry fix were plainly fine.
- **Cause**: `move_to_mm`'s software closed loop gives up as soon as the
  residual fails to shrink in one iteration —
  `if abs(error_mm) >= prev_abs_error: return current_mm` — with
  `max_iterations = 5`. A single stalled correction on a servo is common,
  so this aborts on noise. Worse, the function `return current_mm` on
  **all three** exits: converged, residual-stalled, and iteration-cap.
  The caller receives a float either way, so `BalanceLinearCell.move_linear`
  passed a 0.676 mm result up as a successful 200 OK for a move commanded
  to 0.0.
- **Fix**: Both halves, at the operator's request once the run was over.
  `move_to_mm` now returns a frozen `MoveResult(position_mm, converged,
  reason)` from every exit, so acting on the position as the commanded
  one requires checking `converged` first — the position is still there
  for logging and recovery. The stall detector takes `stall_patience=3`
  consecutive non-improving iterations rather than aborting on the
  first, with `max_iterations` 5 → 12 to give that room, and the
  improvement baseline only advances on a real improvement. Downstream,
  `BalanceLinearCell._settled_mm` raises `DeviceFaultError` naming the
  position the rail actually reached. A breaking change, taken
  deliberately: the driver is 0.y.z and the alternative is a silent
  mis-position. Note this is the *same shape* as #15 one layer down —
  #15 stopped the cell inventing a number when the driver returned
  `None`, but a driver returning a real number it never reached slipped
  straight through that guard.
- **Rule**: A positioning call must tell the caller whether it *arrived*,
  not merely where it stopped. When "converged" and "abandoned" share a
  return type, every layer above is forced to assume success — and the
  only thing standing between that and a silent mis-position is an
  assertion someone remembered to write.

## 18. The stop command failed one time in fifteen, and nobody checked

- **Problem**: A 50 mm move oscillated between ~48.5 mm and ~61 mm and
  stalled at 48.592 mm. The per-iteration log showed corrections
  travelling far further than commanded: `move +1.372 mm @ speed 5`
  moved **+9.8 mm**; `move +1.408 mm @ speed 6` moved **+13.0 mm**. The
  distance moved tracked the commanded *speed*, not the commanded
  *distance*.
- **Cause**: `move_relative` ends every move with
  `self._write_parameter(3, 4, 0)` inside a `finally` — and **discarded
  the return value**. `_write_parameter` returns `False` when the RS485
  exchange fails, which at this bench's error rate happens often:
  measured on the real amp, a single-shot zero-speed write succeeded
  **28 times in 30**. So roughly one stop in fifteen never happened, the
  rail kept running at the speed still latched in Pr3.04, and the next
  iteration measured a position far past its target.
- **Fix**: `_stop_motion()` retries the zero-speed write up to
  `stop_attempts` (5) and returns whether the amp acknowledged;
  `move_relative` raises the new `MotionStopError` when it never does,
  rather than returning as though it had stopped. Verified: 30/30, and
  60 zero-speed writes left a stationary rail at 0.175 mm — the write is
  idempotent. `BalanceLinearCell` maps it to `DeviceFaultError` so the
  operator sees "THE RAIL MAY STILL BE MOVING" rather than a bare 500.
- **Rule**: Check the return value of a stop. A command whose failure
  leaves an axis moving is not a fire-and-forget write, and putting it in
  a `finally` block makes it *look* handled while handling nothing.
- **Correction to #16's rule**: that entry said to split a retry policy
  by idempotency, reads retryable and writes not. Correct in general —
  but the stop write is idempotent (writing 0 twice is writing 0), and it
  is exactly the one whose loss is dangerous. Classify by *what
  re-sending actually does*, not by the read/write label; blanket "never
  retry writes" left the single most safety-critical command unprotected
  for a full session. My first hypothesis here was also wrong: I blamed
  the read-retry from #16 for the overshoot, then measured read latency
  with and without it (max 2020 ms vs 2084 ms, 4/40 vs 3/40 slow) and
  found retries made no difference. Measure before blaming your own last
  change.

## 19. Shortening a timeout to "10x the median" cut the success rate in half

- **Problem**: The RS485 read retry (#16) raised reliability without
  bounding latency: three attempts on the port's 2 s timeout is over six
  seconds, and the first scenario run to reach the balance died on its
  **first** step — `cell4 GET status timed out after 5.0s`, preceded by
  three `No EOT response from amplifier`. The obvious fix was to shorten
  each attempt. A whole exchange normally costs ~27 ms, so 300 ms looked
  like ten times the honest cost.
- **Cause**: The obvious fix was wrong, and only measurement showed it.
  Over 60 reads each on the real amp:

  | budget | success | median |
  |---|---|---|
  | 2.0 s | **60/60** | 27 ms |
  | 0.3 s | **28/60** | 1002 ms |

  A 1002 ms median at a 0.3 s budget means nearly every read burned all
  three attempts. Aborting a handshake part-way leaves this half-duplex
  bus mid-transaction, and the next attempt fails on the wreckage of the
  last — so a tight budget is self-reinforcing rather than
  self-correcting. The median said "27 ms is typical"; it said nothing
  about what a *slow but recoverable* exchange costs, which is the only
  number a timeout is actually about.
- **Fix**: Reverted to 2.0 s and recorded the table beside the constant.
  Kept the plumbing — the budget is now explicit, documented and
  tunable, with the port timeout restored in a `finally`. The real
  mismatch was in the scenarios: `status` steps carried `timeout_s: 5.0`,
  too tight for a read that reaches an amp over RS485, so those went to
  15 s. And the six-second case that started all this was a **dying USB
  adapter**, not normal operation; with a healthy one the measured worst
  case is 2078 ms, one slow attempt followed by a good one.
- **Rule**: A timeout is a claim about the tail, so never set one from
  the median. Before tightening one, measure the success rate at the new
  value — and on a protocol with handshakes or shared media, expect an
  aborted attempt to *cause* the next failure rather than merely
  preceding it.
- **On my own reasoning**: this is the second time in one session that
  arithmetic about this driver survived until it met the hardware
  (see #18's correction). Both times the change was defensible on paper
  and wrong on the bench. When a fix cannot be measured yet — the
  adapter was faulted when this one was written — that is worth saying
  out loud rather than shipping the arithmetic and calling it verified.

## 20. Seven "adapter failures" in one day were a USB port and a cable, and the kernel had said so all along

- **Problem**: The Moxa UPort 1150 carrying the rail's RS485 link failed
  seven times in a day. Each failure needed a physical re-plug and
  recurred within minutes, and the symptom was not always the same: for
  most of the day the adapter *vanished*, but the last time it stayed in
  `lsusb` while `serial.Serial(...)` raised `OSError [Errno 71]
  Protocol error` and the kernel logged `ti_interrupt_callback -
  nonzero urb status, -71` at ~336/s. Six hours went into treating this
  as a dying adapter, and the standing recommendation was to replace it.
- **Cause**: Two causes wearing one costume, neither of them the
  adapter.

  The first was the **root port**. The UPort sat on `3-1`, a port
  directly on the controller; the balance, pump and motors all sat
  behind hubs and none of them had ever faulted. Moving the UPort behind
  the same hub as the balance took the urb error rate from 336/s to
  **zero**, and the `Errno 71` open failure disappeared with it.

  The second surfaced only once the first was gone, and is what the
  kernel had been saying all along:

  ```
  usb 3-7-port1: disabled by hub (EMI?), re-enabling...   <- pump, CH340
  usb 3-2-port2: disabled by hub (EMI?), re-enabling...   <- rail, UPort
  ```

  Both ports re-enumerate continuously — the pump 14x/min, the UPort
  every 12-30 s (device number 56 -> 66 -> 73 -> 77 -> 81 inside a
  minute). The balance, **on the same hub as the UPort**, logged zero
  events across the same window. Same hub, same host, same bus: the
  variable is the port and what hangs off it. The two flapping devices
  are the two attached to motor-driven equipment with switching
  supplies; the one steady device is a bench instrument.
- **Fix**: Relocate off the root port — done, and it stands. Then the
  emitter was found by switching things off one at a time. Pulling the
  pump made the UPort **worse** (2 -> 12 re-enumerations per 40 s), so
  it was not the cause. Powering down the **servo amp** produced zero on
  every counter — no re-enumerations, no `disabled by hub`, no urb
  errors — for over a minute, against 18 re-enumerations per minute with
  it on. The pump, by then sitting on the same hub, went quiet too, so
  its 118 dropouts earlier that day were the same amp. Then one more
  measurement decided *how* the amp reaches the USB bus — run it with
  the RS485 cable unplugged from the UPort:

  | amp | RS485 cable | UPort re-enumerations / 40 s |
  |---|---|---|
  | off | connected | **0** |
  | on  | connected | **15** |
  | on  | **disconnected** | **0** |

  The coupling is **conducted, through the RS485 pair and ground** — not
  radiated. Pulling one signal cable silences a running amp, and the
  balance and pump, which have no galvanic path to it, sat at 0 all
  along. That rules out the obvious purchase: a shielded USB cable and
  ferrites on the USB side would have done nothing. The fix is on the
  RS485 side and starts free — amp PE actually landed, shield terminated
  at one end only, signal ground (SG) actually run between the ends —
  then a common-mode choke, and only then an isolated adapter
  (UPort 1150**I**).
- **Rule**: Before condemning a serial adapter, compare it against a
  device that shares its bus. A fault that follows the *port* rather
  than the device is wiring or topology, and `journalctl -k` will
  usually have named it — `disabled by hub (EMI?)` is not a hint, it is
  a diagnosis. Grep the kernel log for the port before buying hardware.
  And when several devices misbehave at once, resist pairing them off as
  cause and effect: here the pump and the rail adapter were both victims
  of a third thing neither was plugged into. The way to find that third
  thing is to switch candidates off and count, one at a time, against a
  device that never fails. Then keep going: "the amp is the emitter" was
  still not enough to buy a part, because it does not say whether the
  noise arrives by air or by wire, and the two fixes share nothing. One
  more cable-pull separated them.
- **On my own reasoning**: I recommended replacing this adapter
  repeatedly across the day, and it was never the adapter. Seven
  failures all produced the same *conclusion* from me because I kept
  reading the same layer — the device — and never asked why every other
  device on the machine was fine. The comparison that solved it took one
  command and was available from the first failure.

## 21. A vanished device node is a permanent outage, because the fd survives the device

- **Problem**: After the adapter re-enumerated, **every** request to the
  cell server returned HTTP 500 in 2 ms — including `GET /v1/status`,
  which had answered correctly minutes earlier. It never recovered on
  its own. The traceback pointed at a line that merely assigns a
  timeout:

  ```
  self.ser.timeout = self.exchange_timeout_s
    -> serial/serialposix.py _reconfigure_port
    -> SerialException: Could not configure port: (5, 'Input/output error')
  ```
- **Cause**: The driver resolves `"110A:1150"` to a device path **once,
  at startup**, and holds the open fd. When the adapter re-enumerated,
  the kernel gave it a new node and deleted the old one; the process
  kept an fd on a node that no longer exists:

  ```
  /proc/<pid>/fd/6 -> /dev/ttyUSB3 (deleted)
  ```

  `tcsetattr` on that fd returns `EIO`, so the failure surfaced at a
  property assignment rather than at a read or a write — which is why it
  read as a driver bug rather than a missing device. The 2 ms response
  time was the tell: the request never reached the wire. A real RS485
  problem costs hundreds of milliseconds and retries; an instant 500 is
  the local end failing.
- **Fix**: Restart the server so it re-resolves the VID:PID. Deliberately
  **not** fixed by auto-reopening on `EIO`: on a link that drops every
  20 s, a 14 s move would lose its port mid-motion, and this rail cannot
  be stopped from software (`docs/L1_AUDIT.md` GAP-1). Reconnect logic
  here would convert a loud failure into a moving rail nobody is talking
  to. The link gets fixed first; resilience is only worth adding once it
  is protecting against the rare case rather than the normal one.
- **Rule**: Resolving a port by VID:PID at startup buys stability against
  *boot-order* renumbering, not against re-enumeration during a run —
  the fd outlives the device it names. When a server that was working
  starts failing instantly and identically on every endpoint, check
  `/proc/<pid>/fd` for `(deleted)` before reading the traceback. And
  before adding automatic reconnect, ask what the hardware is doing while
  the software is reconnecting.

## 22. The documented adapter serial was for an adapter that is not on this bench

- **Problem**: Bringing cell1's XZ gantry up for the first time,
  `server/nuc1/cell1.toml.example`, the `Config` default and
  `CLAUDE.md`'s hardware table all named the X adapter `NTAM63XD`. The
  three FTDI adapters actually plugged in are `NTAMU6TO`, `A10PUO5V`
  and `A10PUO5W`. `MKSMotor.open_xz` checks its `serial_x` against the
  live list and raises `X adapter (serial=NTAM63XD) not connected`, so
  the cell server would have died at startup — before any of the
  gantry code ran.
- **Cause**: The two Z serials in the docs matched the hardware exactly,
  which is what made the X entry credible. Only the X adapter had been
  swapped at some point, and the docs were never re-read against the
  bus. The upstream driver had the right value the whole time:
  `bridge.py` and `CVMeasure.py` both carry `SERIAL_X = 'NTAMU6TO'`,
  and even `open_xz`'s own docstring uses it as its example.
- **Fix**: Take the serial from the bus, not the doc —
  `for d in /sys/bus/usb/devices/*/; do ... cat $d/serial; done` filtered
  to `idVendor=0403`, or the driver's own
  `CAN2USBAdapterDeviceRecognition.py`. Corrected in the example config,
  the `Config` default and `CLAUDE.md`. Identifying X among the three
  needs no guesswork: name X explicitly and `open_xz` assigns whatever
  two remain to Z, and here the odd adapter out is also a different
  model (NTREX USB2CAN vs. two identical FT245R), which corroborates it.
- **Rule**: A hardware address in a doc is a claim about a past bench,
  and it decays silently. Before the first run of any cell, resolve every
  address against the live bus and diff it against the driver's own
  constants — the upstream repo that talks to the device daily is a
  better source than the integration repo's table. Partial agreement is
  not corroboration: two of three serials matching is exactly what a
  single swapped adapter looks like.

## 23. `preflight.py` says "not attached" for an adapter that is plugged in

- **Problem**: With all three USB2CAN adapters visible in `lsusb` and
  enumerated as `/dev/ttyUSB{1,2,5}`, `claude_test/preflight.py` printed
  `attached FTDI adapters (0)` and
  `[MISS] stage.serial_x = NTAMU6TO not attached right now`. Read
  literally, that says the gantry is unplugged. It is not.
- **Cause**: Two different access paths to the same chip. The pre-flight
  resolves FTDI serials through **pyftdi/libusb**, which needs the raw
  `/dev/bus/usb/<bus>/<dev>` node and needs the chip *not* claimed by a
  kernel driver. On a host without the udev rule both fail: `ftdi_sio`
  auto-binds on plug, and the nodes are `crw-rw-r-- root root` while the
  user is merely in `dialout`/`plugdev`. `Ftdi.list_devices()` then
  raises `ValueError: The device has no langid (permission issue, no
  string descriptors supported or device error)` — whose *first* named
  cause is a permission problem, not an absent device. The pump and
  balance resolved fine in the same run because pyserial only needs the
  `ttyUSB`/`ttyACM` node, which `dialout` covers.
- **Fix**: Install the udev rule from
  `external/ESP32S3BOX3MotorController/SETUP_UBUNTU.md` §1 (`MODE="0666"`
  plus a `RUN+=` that unbinds `ftdi_sio`). `release_ftdi_sio()` in the
  driver cannot substitute: writing `/sys/bus/usb/drivers/ftdi_sio/unbind`
  needs root, so from an unprivileged cell server it is a silent no-op.
- **Rule**: "Not attached" from a tool means "I could not see it the way
  I look", which is a different fact from "it is not there". When a
  pre-flight disagrees with `lsusb`, ask what access path the pre-flight
  uses before touching the hardware — and if some devices on the same bus
  resolve and others do not, the split is the clue: here it lands exactly
  on libusb-vs-pyserial, i.e. permissions, not cabling. A checker that
  reports a permission error as a missing device sends you to the wrong
  end of the bench (see #20 for the day that cost).

## 24. The gantry reported the position it was asked for, not the one it reached

- **Problem**: `PumpGantryCell.move_gantry` set `self._stage_x_mm = target`
  right after commanding the move and returned it, and `status()` served
  that same cached number. Every `verify_*` assert in a scenario would
  therefore compare the request against itself: a gantry that never moved
  — unpowered, off the CAN bus, or having dropped the command — still
  answered `200 OK` with `x_mm: 50.0`. `diagnose()` had the matching hole,
  a hardcoded `"stage": {"ok": True}`.
- **Cause**: `MKSMotor.move_to` does not raise when a move fails to start;
  it *prints* `[ERROR] Motor failed to start (status=0x..)` or
  `[ERROR] No response` and returns the status. `MKSMotor.move_sync` then
  discards that return value entirely (it runs `move_to` for its side
  effects inside `_run_group`). So the only signal a caller gets from a
  refused move is a line on stdout. The paired-Z interlock does not cover
  this either — it fires on a raised `ConnectionError`, and a *silently
  dropped* command raises nothing, which is precisely the case where one
  Z lands and its partner does not.
- **Fix**: Confirm by measurement, never by echo. `_confirm()` reads
  `read_position_mm()` back on every axis after each move and raises
  `TransportError` if an axis cannot be read (position unknown ≠ arrived),
  `DeviceFaultError` if it is further than `ARRIVAL_TOLERANCE_MM` from the
  target, and `DeviceFaultError` if the two Z encoders end more than
  `Z_DESYNC_LIMIT_MM` apart. `status()` reports live encoder values and
  `null` for an axis it could not read — never `0.0`, which is the one
  value that would make a `verify_home` assert pass. `diagnose()` derives
  `stage.ok` from a live read of all three motors. Same substitution
  removed from cell4's rail in #15/#17, found here by reading the driver
  rather than by a bench failure.
- **Rule**: When a driver signals failure by printing, its caller has no
  error handling — it has a log. Grep any motion primitive for `print(`
  and `return` before trusting its exceptions, and check what the *group*
  wrapper does with the return value, because a helper that runs a
  function for its side effects throws away the status the single-motor
  path would have given you. The general rule holds: a cell may only
  report a position it has measured. The dead-reckoned value is the
  request, and a check that compares the request to itself always passes.

## 25. The dry run passed, then the first real move was rejected as a 422

- **Problem**: `demo_gantry_step.yaml` validated clean twice — offline
  against the L1 OpenAPI, and again with the real
  `python -m orchestrator validate` against the live cell1 server, both
  reporting `ok (23 steps)`. The first actual `gantry/move` then came
  back **HTTP 422** before touching the hardware:

  ```
  {"type":"greater_than_equal","loc":["body","accel_pct"],
   "msg":"Input should be greater than or equal to 1","input":0}
  ```
- **Cause**: Two independent faults that only met at run time.
  1. `GantryMoveRequest.accel_pct` was declared `ge=1`. But the driver
     maps 0–100 % onto the MKS acceleration byte 0–255, where **0 is a
     real setting** meaning "no acceleration ramp" — and it is the value
     *both* upstream reference scripts use (`bridge.py` and the
     bench-validated `CVMeasure.py` set `MOVE_ACCEL_PCT = 0`). The schema
     forbade the one value the driver's own validated code passes.
  2. The dry run cannot see it. `validate_scenario` resolves each action
     against the cell's OpenAPI and checks the request body's **field
     names**; it never applies the field **constraints**, even though
     they are right there in the same document. So a body with a correct
     name and an out-of-range value validates.
- **Fix**: Two parts, because there were two faults.
  1. Corrected the schema to `ge=0` on `GantryMoveRequest` and, for the
     same reason, `ZStageMoveRequest` (cell5, same driver, identical
     defect waiting). The scenario was left alone: it was right, and
     matching the reference scripts' acceleration was deliberate.
  2. Taught the dry run to read the bounds — `_check_bounds` in
     `orchestrator/scenario.py` now enforces `minimum` / `maximum` /
     `exclusiveMinimum` / `exclusiveMaximum` alongside the type check.
     Fixing only the schema would have left the next scenario to find its
     own out-of-range value the same way, on the bench, with the frame
     powered.

  Writing that check produced a small lesson of its own: the first version
  used `value.__lt__(limit)`, and since an OpenAPI bound is a float while
  the value is usually an int, `(10).__lt__(1.0)` returns `NotImplemented`
  — which is **truthy**, so every valid integer field was reported as
  violating both of its bounds. It was caught only by running the new
  check against a body already known to be good. A validator is worth
  nothing until it has been shown to pass what should pass; testing it
  solely on the bug it was written for proves half of it.
- **Rule**: "The dry run passed" is a statement about the shape of a
  request, not its contents — it answers "does this action exist and are
  these the right field names", and stops there. Value constraints are
  checked for the first time by the real server, on the real bench, with
  the frame powered. When a scenario's numbers come from somewhere
  (a driver's reference script, a spec, an operator), verify them against
  the *schema* as well as the driver before the run. And when a validator
  and a server disagree about the same document, suspect the validator is
  reading less of it than you assumed.
- **On which side was wrong**: the instinct on a 422 is to change the
  request until the server accepts it — here that would have meant
  `accel_pct: 1`, silently moving the gantry with a ramp the reference
  runs never used, and leaving the schema wrong for cell5 too. The
  driver, not the API, is the authority on what a device accepts.

## 26. Made the link survivable for reads, and deliberately left motion fragile

- **Problem**: The rail's USB adapter re-enumerates every few seconds
  (#20: the amp couples noise into its own RS485 link). The wiring fix
  needs bench work that had not happened, and the bench still needed to
  make progress. Two symptoms: the cell server failed to start on **3 of
  4** consecutive attempts, and once running, one drop killed it
  permanently — a 150 s soak returned **0 of 30** positions, every read
  costing the full 6.1 s retry budget.
- **Cause**: Two different things, which is why one fix would not have
  done. Startup failed because `resolve_port()` raises when it looks
  during an enumeration gap — a *transient absence* read as a missing
  device. Reads failed forever because the open fd outlives the deleted
  node (#21), so no amount of retrying on that fd could work.
- **Fix**: `_open_serial()` waits out the gap (20 x 0.5 s) and, if the
  adapter really never returns, raises with the last underlying error
  attached — "absent" and "present but unopenable" send an operator to
  different places. `_exchange()` catches the OS-level error, reopens by
  re-resolving the VID:PID, and reports that attempt as no-reply so the
  existing retry loop absorbs it. Measured on the still-faulted bench,
  150 s of `/v1/status` across 18 kernel re-enumerations:

  | | reads | positions | null | median |
  |---|---|---|---|---|
  | before | 30 | **0** | 30 | 6112 ms |
  | after | 959 | **959** | **0** | **30 ms** |

  14 reconnects happened inside that window. The median is back to the
  healthy-bench figure (27 ms); only the max, 4145 ms, shows a read that
  spanned a reconnect.

  **And then the part that was left fragile on purpose.** `move_to_mm`
  refuses to continue across a reconnect: `link_generation` is sampled
  after the first read and checked after every move and every position
  read, and on a change the rail is stopped and the move *fails* —
  `LinkDroppedError` if the stop landed, `MotionStopError` if it did
  not. So while the link is bad, moves keep aborting. That is the
  intended behaviour, not a shortfall.
- **Rule**: Resilience is a per-operation judgement, not a property of a
  connection. Reopening is right for a read, whose worst case is a stale
  number, and wrong for a move, whose worst case is a rail travelling
  with a latched speed command and nobody able to see it. Decide it by
  what the hardware does during the gap, not by whether reconnecting is
  technically possible.
- **On my own reasoning**: I refused this work twice, on the grounds
  that auto-reconnect would mask a fault, and only built it when asked
  directly. The refusal was too broad. "Do not paper over the fault" was
  right about motion and wrong about reads, and treating the link as one
  thing hid that a 20-line change would have made the bench usable hours
  earlier. The correct answer was never all-or-nothing; it was to ask
  which operations are safe to retry, which is the same question
  `_send_and_receive` already answers for writes a few lines away.

## 27. The rail was homing into its own hard stop, and a passing assert is what hid it

- **Problem**: cell4's first complete weighing run succeeded — 15/15 —
  and its `move_home` step was recorded as `y_mm -1.797`, passing
  `verify_back_home` (`-1.797` within ±2.0 mm). Two runs later every
  move displaced **exactly 0 mm** in any direction, while the amp
  answered serial normally. Diagnosis took three wrong turns before the
  operator read the amp's front panel: **Err16.0**, motor overload.
- **Cause**: `home_linear()` targeted 0.0 mm; the closed loop coasts
  1.5–1.8 mm past its target; and on this bench **0 mm sits on the
  mechanical end stop**. So homing drove the carriage into the stop and
  held it there against the servo until the amp protected itself and
  de-energised. Every later move then did nothing, because an alarmed
  amp still serves parameter reads and writes perfectly.

  The three wrong turns are the instructive part, and all three were
  mine:
  1. **"stalled"** — the driver's word for "commanded, didn't arrive",
     which reads as *the servo fought the load* and sent me to the limit
     switches.
  2. **POT** — the input frame showed `POT(SI2)=0`, active on a
     b-contact. I reported it as the cause and sent the operator to
     check X4 wiring. `Pr5.04 = 1` **disables the over-travel inputs
     entirely**; I had found something that looked wrong and stopped
     before asking whether the amp was configured to care.
  3. **"the amp accepts and does not drive"** — correct but useless,
     because I could not read the alarm. `SRV-ON input = 1` was read as
     "the servo is energised" when it only says a signal is present on
     the wire.
- **Fix**: `home_mm` (default 5.0) is cell config, since how much
  clearance a stop needs is bench wiring, not a driver property. Both
  scenarios follow it — `demo_linear_move.yaml` was still returning to
  0.0 and would have re-tripped the alarm on its next run. `FakeL1`
  homes to the same position, because a fake that homes to 0 lets a
  scenario asserting `y_mm <= tolerance` pass in CI and damage hardware
  on the bench. Verified after: a full 15/15 run with the rail parking
  at 5 mm and never crossing the origin.
- **Rule**: An axis's software zero is not automatically a place the
  carriage may *rest*. Before any scenario returns to a coordinate,
  establish where the mechanical limits are and park clear of them by
  more than the loop's coast distance — a position that is legal to pass
  through can be destructive to hold.
- **On the assert that passed**: `-1.797` inside `±2.0` was written up
  here as "0.2 mm of margin left", a tolerance to tune later. It was not
  a near miss on an assert; it was the rail pushing its stop, logged as
  a success, once per run, until the amp gave out. A tolerance band
  centred on a hazard reports contact with the hazard as compliance. When
  a measured value sits at the edge of its band, ask what is physically
  at the edge before treating it as a number to adjust.
- **On not asking the device**: the amp knew the answer the entire time
  and nothing in this repo could ask it — the driver has no alarm read
  and no alarm clear, which is also why `diagnose()` answered
  `stage.ok: true` on an alarmed amp. That is the same shape as the
  hardcoded `ok: True` fixed in #15's family: a health check that cannot
  fail. Tracked as issue #15; until it lands, the front panel is the
  only alarm indicator on this bench.

## 28. This NUC's Python needs a bootstrap: no `sdl` env, conda blocked by ToS, `python3-venv` missing

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

## 29. The Tapo plug's IP drifts; the fix belongs in the cell, not the submodule

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

## 30. The RCT digital's USB interface wedges under sustained polling — and only power does what reset cannot

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

## 31. The RCT wedge's real chain: USB-powered interface + shared hub; and the recovery that works

- **Problem**: LearnedPatterns #12's pacing/flush/retry did not stop the
  wedges (still 2–6/10 sweeps failing, then bus drop-off). Power-cycling
  the hotplate never revived the interface; neither did quick cable
  replugs, `usb.core` reset (later "Protocol error"), port reopen, or
  disabling ModemManager.
- **Cause**: Two hardware facts. (1) The hotplate's STM32 USB interface
  is powered from USB 5 V, so cycling the hotplate's own power never
  resets it — only cutting USB power does. (2) It sat behind the bench's
  shared USB hub, whose churn (device addresses in the 80s) destabilized
  the CDC firmware until it crashed at the USB protocol level.
- **Fix**: Full-drain recovery: unplug USB → hotplate off → wait 20 s →
  reconnect **directly into a NUC port** → power on. Result: 6-query
  sweeps went 2/10 → 10/10 and stayed clean through a full scenario run.
  ModemManager stays disabled and `/etc/udev/rules.d/99-innocore-usb.rules`
  pins MODE 0666 + ID_MM_DEVICE_IGNORE for 0483:5740 and 0403:6001.
  The hub bounces also silenced the MKS CAN side once — fixed by the
  operator re-powering the motor driver.
- **Rule**: Keep the RCT (and any single-owner serial device) on a
  direct NUC port, never the shared hub. When a USB device ignores its
  host-side resets, cut its *USB* power (unplug), not just the
  appliance's mains — bus-powered interface boards only reset with the
  cable.

## 32. The RCT readback never shows the setpoint — condition on a tolerance, not equality

- **Problem**: The first `cell_d_lamp_heat_40c` run polled
  `plate_c >= 40.0` and hung: the front panel showed 40 while the
  serial readback (`IN_PV_1`) sat at 39.0 for minutes.
- **Cause**: The RCT reports whole degrees over serial and its control
  loop holds just under the setpoint; the panel rounds up. A strict
  `>=` against the setpoint compares against a value the readback may
  never produce.
- **Fix**: The scenario's `until:` uses
  `plate_c >= hot_c - tol_c` with `tol_c: 1.0`; re-run completed in
  74 s with every shutdown verify passing.
- **Rule**: Never gate on exact equality (or strict crossing) of a
  regulated process value read back from the regulator itself — always
  allow the device's own reporting granularity + control band as
  tolerance.

## 33. One measurement set a tolerance four times tighter than reality

- **Problem**: `ARRIVAL_TOLERANCE_MM` — the check that decides whether a
  gantry move actually arrived — was calibrated from a single manual
  50 mm move that came back **0.038 mm** from target. The constant was set
  to 0.5 mm and documented as "~13x the observed worst", which reads like
  a comfortable margin. Four completed scenario runs later, across 20
  waypoints reaching 150 mm, the worst residual was **0.145 mm**. The real
  margin was **3.5x**. Nothing failed — but the number in the comment was
  wrong by a factor of four, and anyone tightening the tolerance on the
  strength of that comment would have started failing good moves.
- **Cause**: One sample cannot show spread, and this axis has spread that
  only appears with repetition. Running the *same* scenario three times
  produced X residuals of 0.043, 0.145 and 0.046 mm at the same waypoint —
  a 3x difference between runs of identical commands. The first
  measurement happened to land near the bottom of that range. Worse, the
  single measurement was taken at 50 mm, and the residual grows with
  travel; the stair to 150 mm was where the larger numbers lived.
- **Fix**: Recalibrated from `runs/*/vars.json` across all four runs and
  corrected the justification in `cell/pump_gantry_cell.py` and both
  scenarios. The *value* did not change — 0.5 mm was already right, by
  luck rather than by evidence. What changed is that it is now defensible.
  Also recorded the more useful of the two statistics: the **increment**
  error (worst 0.094 mm) is tighter and more stable than the **absolute**
  residual (0.145 mm), because a persistent offset cancels when you
  subtract consecutive readings — run 2 carried a +0.145 mm X offset from
  one waypoint to the next while its step-to-step increment stayed within
  0.001 mm of 50.
- **Rule**: A tolerance derived from one measurement is a guess wearing a
  number. Repeat the *same* run at least three times before writing a
  margin into a comment, and take the measurements at the extreme of the
  range you intend to allow, not at the convenient end. When the value
  turns out fine anyway, still fix the reasoning — the next person will
  tighten the constant based on what the comment claims, not on what the
  hardware did. And prefer the statistic that survives a constant offset:
  if what you care about is "did it move 50 mm", measure the difference,
  not the position.

## 34. Two readings that agreed exactly would have been the bug, not the proof

- **Problem**: Each scenario waypoint reads the gantry position twice —
  once from `gantry/move`'s own response, once from a separate
  `GET /v1/status` — and the two disagree by up to **0.145 mm**. That
  looks like an inconsistency to be explained away.
- **Cause**: It is the servo closing its remaining error. `gantry/move`
  reads the encoder the moment `_wait()` returns; `status` reads it a
  fraction of a second later, by which time the loop has settled. A
  measured 50.048 mm followed by 49.9994 mm is two honest reads of a
  moving quantity.
- **Fix**: Nothing — but the disagreement was recognised as *evidence*,
  and kept. Earlier the same day this cell had a bug where `move_gantry`
  returned the commanded target and `status` served the same cached value
  (#24). Under that bug these two reads would have agreed **exactly**, to
  every decimal, at every waypoint. So exact agreement between two
  supposedly independent sources is the thing to be suspicious of; a small
  physical disagreement is what independence looks like.
- **Rule**: When a design deliberately reads the same fact by two paths,
  decide in advance what agreement should look like — and remember that
  *perfect* agreement usually means the two paths are the same path. Two
  sensors of a physical quantity should differ by something on the order
  of the noise; if they never do, look for the shared cache before
  congratulating yourself. Cross-checks only have value where the two
  sides can actually disagree.

## 35. A pump that answers `lsusb` can still be un-openable — watch the devnum, not the presence

- **Problem**: With the SY-01B back on the bench (`lsusb` shows
  `1a86:7523`), restoring cell1's `[pump]` table made the cell open
  cleanly — and then every `/v1/diagnose` and `/v1/status` answered 500
  with `termios.error: (5, 'Input/output error')` on the pump's first
  serial query. Restarting the server reproduced it exactly. The stale
  `/dev` tmpfs node was the obvious suspect (this container's known
  quirk) and was a red herring: the fresh `ttyUSB` node existed with the
  right major:minor every time.
- **Cause**: The CH340 link is physically flapping. Watched for 45 s
  with **nobody holding the port**, the device re-enumerated four times
  (devnums 054 → 055 → 057 → 058, ~10-15 s apart, 2026-07-29). Any fd
  opened on it dies EIO within seconds, and because `status()` queries
  the valve whenever a pump is configured, the dead pump fd took the
  whole cell's probes down — gantry included. Cable, hub port or pump
  USB power; not software, and not the stale-node quirk.
- **Fix**: Reverted `server/nuc1/cell1.toml` to the pumpless shape so
  cell1 serves its gantry cleanly (the config's header now carries the
  measurement and the ready-to-restore `[pump]` block). The L2 pump
  scenario (`scenarios/demo_pump_cycle.yaml`) was validated offline
  against the fake L1 and dry-run against the live cell instead — the
  L1 OpenAPI advertises `pump/*` in every shape, so the dry run does
  not need the pump present.
- **Rule**: `lsusb` proves enumeration, not a link. Before trusting a
  USB serial device that has a flapping history, watch its **device
  number** across a minute of nobody touching it: a devnum that climbs
  is a link that is dropping, and no amount of node-rebuilding or
  reopening will hold a session on it. Fix the physical layer first,
  then confirm the devnum holds still, then wire it into a cell.

## 36. The #35 flapping was the servo-driver power, and the devnum watch proved the fix

- **Problem**: After the bench's servo-driver power wiring was tidied,
  was the SY-01B's CH340 link (re-enumerating every ~10-15 s untouched,
  #35) actually fixed, or just quiet for a moment?
- **Cause**: The flapping had been power-side noise coupled from the
  servo-driver wiring — not the pump, not the cable's data lines, not
  the container's stale-node quirk that #35 already ruled out.
- **Fix**: The same watch that diagnosed #35 verified its resolution:
  devnum 017 held across a 60 s untouched watch, ~20 min of idle
  serving, six consecutive valve queries, and then a full 53 s motion
  run (`20260729T103557Z-demo_pump_cycle`, 19/19 steps, zero EIO).
  `[pump]` is back in the live cell1.toml.
- **Rule**: When a USB serial device flaps, look at what shares its
  power and ground before touching software — and after any physical
  fix, re-run the exact measurement that characterized the fault (the
  devnum watch), then a real traffic run, before declaring it fixed. A
  link that merely *looks* quiet has not earned the `[pump]` table back.

## 37. demo_pump_cycle's remaining_cycles is the desired total MINUS ONE

- **Problem**: An operator wanting 30 pump cycles reads
  `remaining_cycles` as "the number of cycles" and sets it to 30 —
  getting 31, or asserts totals that are off by one. The name invites
  the mistake: nothing at the param site said what it must remain
  *after*.
- **Cause**: The scenario language has no loop construct (deliberately —
  a scenario is data), so the first cycle is unrolled into individually
  asserted steps and only the remainder runs through one `pump/cycle`
  call. Total = 1 + remaining_cycles, an invariant that lived in a
  header comment nobody reads while editing a number.
- **Fix**: The rule now sits directly on the param ("TOTAL MINUS ONE:
  for N total cycles set N - 1; 29 -> 30"), in the header, in
  README.md's scenario tips, and the orchestrator test pins
  `1 + cycles == 30` so an edit that sets the param to the intended
  total fails the suite instead of running an extra cycle on hardware.
- **Rule**: When a scenario splits one logical loop into "unrolled
  first + batched rest", the batch-count param must carry the off-by-one
  in its own comment at the definition site — and a test should pin the
  *total*, not echo the param back at itself.

## 38. A USB link you cannot fix can still be a link you can survive — if the commands are absolute

- **Problem**: With the MINAS amp on, its conducted noise re-enumerates
  the pump's CH340 every ~12-15 s idle and every few seconds under
  active traffic — the same coupling class as the rail's UPort (#20).
  The electrical fix is known but not yet purchased, and any pump
  scenario under the amp died EIO on an early command.
- **Cause**: One fd, one link, no recovery: the first drop killed the
  session, and `status()` querying the valve dragged the whole cell's
  probes down with it.
- **Fix**: The rail's ladder, adapted to the pump's physics
  (`cell/pump_gantry_cell.py`): patient open across the re-enumeration
  gap; guarded commands that reopen by VID:PID and re-issue; **settle
  probes** that check the command's end state after a reconnect and
  skip the re-issue when the MCU finished it alone; an error-15
  busy-wait for the race with a still-executing first issue; and a
  dead link that stays 503, never 409. Verified under the noise:
  `demo_pump_cycle` 19/19, 65/65 drops absorbed, 55 skipped re-issues,
  0 unhandled 500s (claude_test/smoke_cell1_pump_emi_20260729.md).
  Three bench surprises en route: an absent node raises
  `RuntimeError("no serial device matches")`, not `OSError`; a
  re-issued Z2 met error 15 because the MCU kept executing the
  interrupted one; drops arrive several times faster under traffic
  than idle, so retry budgets sized from idle watches are too small.
- **Rule**: Re-issue-on-reconnect is safe only when the device's
  commands are absolute and mechanically bounded, AND the device keeps
  executing through the link loss — so after every reconnect, verify
  the end state before re-issuing, and treat "busy with my own
  previous command" as wait, not failure. The rail earns the opposite
  policy (abort moves) for the same reason stated backwards: its
  motion is not bounded and its position not re-verifiable mid-flight.
