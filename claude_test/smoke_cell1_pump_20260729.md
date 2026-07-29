# cell1 pump bench validation — 2026-07-29

Two hardware runs of `scenarios/demo_pump_cycle.yaml` (SY-01B under L2
YAML control on cell1), both 19/19 steps, and together they retell
LearnedPatterns #1 with the current plumbing:

| Run | Valve commands | Software | Liquid |
|---|---|---|---|
| `20260729T103557Z` | 1 → 3 (180°, same state) | 19/19 ✓ | **oscillated at one line only** — no transfer |
| `20260729T104717Z` | 1 → 2 (90°, real switch) | 19/19 ✓ | **transferred, operator-confirmed by eye** |

The first run is the cautionary half: every wire-level assert can pass
while the fluid path never changes. Runlogs under `runs/` (gitignored).

## The control-path run — `20260729T103557Z`, commands 1 → 3, 50 µL

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

1 unrolled + 9 batched = the 10 requested aspirations (commands 1 → 3,
50.0 µL). All 19 steps passed — and the operator then reported the water
only moving back and forth in one line. See "The fluid-path run" below.

## The fluid-path run — `20260729T104717Z`, commands 1 → 2, 100 µL

The bench plumbing has the **reservoir on physical port 1** and the
**tip on physical port 3**. Per the HIL mapping (driver docstring +
LearnedPatterns #1): command 1 (=3) → state `C-1/2-3` = syringe↔physical
1; command 2 (=4) → state `C-3/1-2` = syringe↔physical 3. So the 1 → 3
command pair of the first run held the syringe on the reservoir line the
whole time — rotor turning, path constant — exactly what the operator
saw. The corrected pair is **command 1 (aspirate, reservoir) → command 2
(dispense, tip)**, 90° apart.

| Step | Result |
|---|---|
| `initialize` (force 2) | 6.17 s; valve woke at '4', plunger 0.0 µL |
| cycle 1 unrolled | cmd 1 ('1' readback), aspirate 100.0 µL, cmd 2 ('2'), dispense→0.0 |
| `remaining_cycles` ×9 | 62.96 s (~7.0 s/cycle at 100 µL), `cycles_done: 9`, `final_valve: '2'` |
| `read_back` witness | valve '2', plunger 0.0 µL, gantry untouched at X=300 mm |

**Liquid transfer confirmed by the operator's eye**: reservoir drawn
down, liquid expelled at the tip — ~1 mL moved across the 10 cycles.
This is the assert no scenario step can make (the `?6` readback proves
rotor position only), and the pair of runs is the proof of why it must
stay a human check.

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
