# Bench bring-up runbook — L1, one cell at a time

The order of operations for the day the hardware is connected. Everything
below is **L1 only**: one cell server, driven over HTTP. L2 comes after
(§6). Results go into [`L1_AUDIT.md`](L1_AUDIT.md).

Companion documents: [`L1_AUDIT.md`](L1_AUDIT.md) is the record and the
gap list; [`L2_ORCHESTRATOR_SPEC.md`](L2_ORCHESTRATOR_SPEC.md) §4.2 is the
policy this implements; `../ADDING_A_CELL.md` is how a cell is built.

---

## 0. Safety rules that override everything here

1. **The physical e-stop is the only stop.** `POST /v1/stop` cannot
   interrupt a command that is already running — it queues behind it
   (`L1_AUDIT.md` GAP-9, measured 4.2 s late on a 5 s move). On cell4 it
   would do nothing even if it arrived (GAP-1). Keep your hand on the
   physical interlock, on every cell, for every motion test.
2. **Nothing moves without an operator present.** `smoke_l1.py` stops and
   waits for you to type `go` before every hazardous action. There is no
   unattended mode and no "yes to all" flag; do not add one.
3. **The gantry (cell1–3) is the highest-consequence subsystem.** Clear the
   frame. If a single Z axis moves alone, or the frame tilts, hit the
   e-stop immediately.
4. **One owner per serial port.** Before starting a cell server, stop the
   L2 orchestrator, any other cell server on the same devices, and the
   hotplate driver's own dashboard (`hotplate_controller/server.py`).
5. **A heater is a hazard like a motor.** Never leave the bench while the
   hotplate or the IR lamp is energized.

---

## 1. Fill in what is still unknown

These are the values no one has collected yet — the actual blocker for
cell2, cell3 and cell5.

```bash
python claude_test/preflight.py        # says exactly which ones are missing
```

It enumerates the attached devices, resolves every address in every
`server/nuc*/*.toml` against them, flags leftover `TBD-…` placeholders,
and tells you if something already listens on a cell's port. It **only
enumerates** — no port is opened, so it is safe to run at any time.

To collect the values it says are missing:

```bash
python -m serial.tools.list_ports -v                             # CH340 pump, Moxa, CDC balance
python -c "from pyftdi.ftdi import Ftdi; Ftdi.show_devices()"    # MKS FTDI serials
```

| Value | Whose | Where it goes |
|---|---|---|
| FTDI serial of cell2's X adapter | Cell B | `server/nuc2/cell2.toml` `[stage] serial_x` |
| FTDI serial of cell3's X adapter | Cell C | `server/nuc2/cell3.toml` `[stage] serial_x` |
| FTDI serial of cell5's single Z | Cell D | `server/nuc2/cell5.toml` `[zstage] serial` |
| Plug name (or IP) of the IR lamp | Cell D | `server/nuc2/cell5.toml` `[lamp] target`, and it must exist in the plug driver's `device_list.md` |
| Plug credentials | Cell D | `external/SmartPlugController/secure.env` — **the operator writes this; Claude Code does not read it** |
| Safe travel per axis | all | the `--step-mm` you pass to `smoke_l1.py`, and later `target_mm` / `lift_mm` in the scenarios |
| `max_celsius` for this bench | Cell D | `server/nuc2/cell5.toml` `[hotplate]` — set it as low as the chemistry allows |
| Real NUC IPs, cell5 port vs ARCHITECTURE.md | L2 | `orchestrator/config.toml` |

Two prerequisites are set on the devices themselves, not in any file:

- **Balance (cell4)**: front panel must be SBI with auto-push —
  `DEVICE → (USB) → DAT.REC = SBI`, `DATA.OUT. → COM. SBI → COM.OUTP =
  AUTO W/`, `STAB.RNG = V.FAST`. A `0x15` (NAK) reply means it is still in
  xBPI mode.
- **Linear rail (cell4)**: MINAS A6 amp at `Pr5.37=0`, `Pr5.30=2`,
  `Pr5.31=1`.

---

## 2. Per cell: start, prove identity, then move

Do this for one cell at a time. Cells that share no device may run
simultaneously, but there is no reason to rush.

```bash
# (a) start the server — a failure here is config or wiring, nothing moved
python -m server --config server/nuc1/cell4.toml

# (b) prove you are talking to the RIGHT devices before anything moves.
#     diagnose returns serial numbers and model names.
curl -s http://127.0.0.1:17060/v1/health   | python -m json.tool
curl -s http://127.0.0.1:17060/v1/diagnose | python -m json.tool

# (c) the gated smoke test
python claude_test/smoke_l1.py --base-url http://127.0.0.1:17060 \
    --suite discovery --suite balance --suite linear \
    --step-mm 10 --out claude_test/smoke_cell4.md
```

Do not skip (b). A config pointing at the *neighbouring* cell's device
still starts cleanly — `diagnose` is what catches it.

| Cell | Port | Suites | Watch for |
|---|---|---|---|
| cell1 / cell2 / cell3 | 17054 / 17056 / 17058 | `discovery pump gantry` | **One Z command must move both Z axes by the same amount.** Any tilt or single-axis motion → e-stop. Note whether the first command after a limit stop is dropped |
| cell4 | 17060 | `discovery balance linear` | Balance settling time (record it). The rail cannot be stopped in software at all |
| cell5 | 17062 | `discovery lamp hotplate zstage pump` | A 409 from `lamp/*` means `secure.env` is not filled in. Watch the plate temperature actually climb. Confirm a setpoint above `max_celsius` is refused with 400 |

Suggested order across the whole bench, lowest risk first:

```
cell4 balance → cell5 lamp → cell5 hotplate → cell4 linear
      → cell5 zstage → pump (any cell) → cell1–3 gantry
```

**Record the durations.** Every `timeout_s` in `scenarios/` is currently a
guess; the numbers from this step are what replace them (A3 / GAP-4).

---

## 3. The two audit probes

These are not device checks, they are *contract* checks — and one of them
is testing a safety path we already know is broken.

```bash
# A7: two overlapping moves. Expect the second to queue, not interleave.
python claude_test/smoke_l1.py --base-url http://127.0.0.1:17060 \
    --suite concurrency --motion linear

# A4: a long move, then /v1/stop mid-flight. EXPECT IT TO FAIL to preempt
#     (GAP-9). Physical e-stop in your hand -- that is the actual stop.
python claude_test/smoke_l1.py --base-url http://127.0.0.1:17060 \
    --suite stop --motion linear
```

`--motion` is `linear` for cell4, `zstage` for cell5, `gantry` for
cell1–3. The probe prints when the stop returned versus when the move
returned and sets `preempted=True/False` for you; paste the row into
`L1_AUDIT.md` S9. Running it on each cell tells us whether GAP-9 behaves
identically everywhere.

---

## 4. Write the results down

`smoke_l1.py --out` produces a Markdown table. Append it under **Result
log** in [`L1_AUDIT.md`](L1_AUDIT.md), then flip the S1–S10 verdicts from
`pending` to `pass` / `gap`. Include what you *saw*, not just the HTTP
status — for the pump, the eye is the only valid instrument
(`../LearnedPatterns.md` #1).

Anything surprising goes into `../LearnedPatterns.md` in
Problem / Cause / Fix / Rule form, same day.

---

## 5. Definition of done for M0

- every S-row in `L1_AUDIT.md` is `pass` or a recorded `gap`;
- durations measured, and the `timeout_s` values in `scenarios/` updated
  from them;
- A4/A7 probes run on each cell;
- gaps that block L2 have `gh` issues.

---

## 6. Then, and only then, L2

```bash
cp orchestrator/config.toml.example orchestrator/config.toml   # real IPs
python -m orchestrator serve &                                 # or Docker
curl -s http://127.0.0.1:17100/v1/cells | python -m json.tool  # all reachable?

# every scenario goes through all three stages, in this order:
python -m orchestrator validate scenarios/demo_linear_move.yaml
python -m orchestrator run      scenarios/demo_linear_move.yaml --step-mode
python -m orchestrator run      scenarios/demo_linear_move.yaml
```

The first motion-bearing step always waits for you. In `--step-mode` every
step does. Cell D's scenario (`demo_cell_d_warmup.yaml`) has no wait step
by design — the thermal hold is yours to time in `--step-mode`.
