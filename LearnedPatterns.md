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
