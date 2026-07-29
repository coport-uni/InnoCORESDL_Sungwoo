# cell1 pump bench validation — 2026-07-29 (run `20260729T103557Z-demo_pump_cycle`)

First hardware run of `scenarios/demo_pump_cycle.yaml`: the SY-01B under
L2 YAML control on cell1. **19/19 steps passed**, zero retries, ~53 s
wall clock. Runlog: `runs/20260729T103557Z-demo_pump_cycle/` (gitignored).

## What ran

Operator-gated run from the console (`python -m orchestrator run`, Enter
at the first-motion gate). Pump plunger + valve only; the gantry sat
untouched at X=300 mm the whole run (start and end `/v1/status` agree).

| Step | Result |
|---|---|
| `check_diagnose` | pump present, fw 8.33, sn 32656, 24.0 V, ok |
| `initialize` (force 2) | 6.17 s; valve woke at **'4'**, plunger 0.0 µL |
| cycle 1 unrolled | valve→1 ('1' readback), aspirate 50.0 µL, valve→3 ('3'), dispense→0.0 |
| `remaining_cycles` ×9 | 41.25 s (~4.6 s/cycle), `cycles_done: 9`, `final_valve: '3'` |
| `read_back` witness | `/v1/status` re-read: valve '3', plunger 0.0 µL |

1 unrolled + 9 batched = the 10 requested aspirations, port 1 → port 3.
The verified run used `volume_uL: 50.0`; the working scenario has since
been raised to 100.0 (still inside the 125 µL syringe; dry-run passes).

## Link stability — the flapping is resolved

Earlier the same day the CH340 re-enumerated every ~10-15 s untouched
and died EIO on first query (LearnedPatterns #35). After the bench's
servo-driver power was tidied:

- devnum **017 held** from 19:27 through the whole session — the
  untouched 60 s watch, ~20 min of idle serving, six consecutive
  `/v1/status` valve queries, and this full motion run;
- no EIO, no 500, no re-enumeration during ~53 s of continuous
  command/poll traffic.

Cause was therefore power-side noise from the servo-driver wiring, not
the pump, the cable data lines, or the container's stale-node quirk.

## Observations

- **Init valve position is '4'**, not '1', on this pump's `Z2` homing
  (config `4 way|9600|100K|TSY|high|XLP|AUTO`). The scenario deliberately
  does not assert the post-init valve — only the ports it commands.
- Ports 1↔3 are the same fluid state on the M05 bi-pass valve (180°
  apart); this run validates the control path. A fluid-path validation
  needs the 90° pair (bench: reservoir 2 → tip 1) and eyes on the tubes.
- `pump/cycle` throughput: ~4.6 s per 50 µL cycle at post-init speeds.
