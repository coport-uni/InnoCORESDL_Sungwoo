# cell1 pump under active EMI — the link guard verified on hardware (2026-07-29)

With the MINAS amp powered, its conducted noise re-enumerates the pump's
CH340 every ~12–15 s idle and **every few seconds under active serial
traffic**. The pump now absorbs that the way the rail absorbs its RS485
drops (LearnedPatterns #26) — and the full `demo_pump_cycle` scenario
(30 cycles × 100 µL) **completed 19/19 under that noise**, run
`20260729T114000Z-demo_pump_cycle`, ~250 s wall clock.

## The numbers (cell1 server log, one bench session)

| Counter | Count |
|---|---|
| Link drops absorbed (`[pump-link] link dropped`) | **65** |
| Successful reopens | **65 / 65** |
| Interrupted command found already finished in the MCU (re-issue skipped) | **55** |
| Error-15 busy-waits (MCU still executing the first issue) | 2 |
| Unhandled 500s | **0** |

Baseline for contrast: the same scenario on a quiet bench (amp off) ran
~76 s; the ~170 s difference is the cost of riding out ~65 re-enumeration
gaps. Before the guard, the very first serial query died EIO and took
`/v1/status` down with it.

## How it works (cell layer, `cell/pump_gantry_cell.py`)

1. **Patient open** — at startup and reconnect, the open retries across
   the re-enumeration gap (the node is *absent* mid-gap: that raises
   `RuntimeError("no serial device matches …")` from the VID:PID
   resolve, not an `OSError` — learned when the first bench start died
   on exactly that).
2. **Guarded commands** — every pump interaction catches dead-link
   errors (`OSError`/`SerialException`/`termios.error`), reopens by
   VID:PID, and re-issues. Re-issuing is safe **on this device only**
   because every SY-01B command is an absolute target (`A<n>` plunger,
   `I<n>` valve) bounded by the syringe stroke — unlike the rail, whose
   moves must abort on link loss (GAP-1, no software e-stop).
3. **Settle probes** — the MCU keeps executing an interrupted command
   while USB is down, so after a reconnect the guard first checks the
   command's end state (`?6` == port, plunger `?` == target steps,
   init: `?6` no longer `?`) and skips the re-issue when it already
   completed. That path fired 55 times in this run.
4. **Busy-wait** — a re-issue that races the still-executing first
   issue gets the pump's error 15 (CommandOverflow); the guard waits it
   out instead of failing. Fired twice.
5. **Dead ≠ absent** — a pump whose link stays down keeps raising 503
   `TransportError` (retry-able), never the 409 "configured without a
   pump"; `/v1/status` reports the dead link in `error` and keeps the
   gantry half of the answer usable.

## What this does NOT fix

The noise is still there and still the amp's conducted coupling
(`docs/RS485_EMI_evidence_20260729.xlsx`). The guard buys usability, at
~3× wall clock under load; the electrical fix (grounding/termination
ladder, then the isolator purchase) remains the real cure.

## Transient worth knowing

A run started while the *previous* run's interrupted `initialize` was
still homing inside the MCU saw `diagnose` report `ok_to_initialize:
false` and failed its pre-flight assert — correctly. Re-submitting a
minute later passed. The pre-flight is doing its job; don't "fix" it.
