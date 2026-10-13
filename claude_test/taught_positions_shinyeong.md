# Taught positions — synthesis chain

Measured over `/v1`, so every number is an encoder readback the cell confirmed, not a commanded value.
Rail 0 mm is on the mechanical stop; home is 5 mm.

| label | gantry | rail_mm | x_mm | z_mm | UTC |
|---|---|---|---|---|---|
| rail_cell1 | cell1 | 470.772 | 260.000 | 223.654 | 2026-08-13T10:17:51+00:00 |
| cell1_vial | cell1 | 470.771 | 260.000 | 223.654 | 2026-08-13T10:18:03+00:00 |
| rail_cell2 | cell1 | 709.747 | 260.000 | 223.654 | 2026-08-13T10:38:18+00:00 |
| rail_cell3 | cell1 | 960.029 | 260.000 | 223.654 | 2026-08-13T10:42:06+00:00 |
| cell1_vial_0913 | cell1 | 470.211 | 247.985 | 224.997 | 2026-09-13T12:04:00+00:00 |
| cell2_vial_0913 (x/z) | cell2 | 709.008 | 248.004 | 224.998 | 2026-09-13T12:08:34+00:00 |
| cell2_vial_0913 (rail) | cell2 | 714.496 | 248.004 | 224.998 | 2026-09-13T12:12:45+00:00 |
| cell3_vial_0913 | cell3 | 959.352 | 238.004 | 225.000 | 2026-09-13T13:17:32+00:00 | X/Z confirmed by eye at the bench; Z 225 at X 238 NOT reached under power — Z_A stalled at 198/191 before the operator moved the part causing interference |
| cell3_vial_0915 | cell3 | 960.558 | 245.002 | 222.990 | 2026-09-15T14:11:12+00:00 | after Z interference fix; X adjusted +7 by eye at Z 223 |

## cell5 (analysis station) — 2026-10-08

Taught by eye with `claude_test/test_cell5_z_shinyeong.py` (direct drive; no cell5
server — the IKA hotplate is not on the bench, so `PumpZThermalCell.open` refuses).

| axis | value | frame / notes |
|---|---|---|
| Z dispense | **398.0 mm** (encoder readback 398.008) | 0 = carriage at the UPPER end-stop, power-cycled there; `coord_invert = true` (the cell5.toml.example default) gives +mm = DOWN. 20 mm steps all landed within 0.13 mm. |
| Z home | 0 (upper end) | Whether `home_sync(direction=0)` finds the upper switch is NOT yet verified — on 10-08 the upper end was found by driving into it with the frame flipped (stopped at 57.7 mm, "did not arrive"). Verify the homing direction before relying on gantry/home for this axis. |

The driver accepts absolute targets of 0..400 mm only, so a dispense point deeper
than ~400 mm from the top needs a new zero (power-cycle lower down).

Analysis pump: SY-01B serial 30946, NUC2 `/dev/serial/by-path/pci-0000:00:14.0-usb-0:4.3.1:1.0-port0`,
supply on physical port 1 (command 1), nozzle on physical 3 (command 2), 125 µL syringe assumed.
Primed 2026-09-30 (30 cycles) and 2026-10-08 (200 cycles ≈ 20 mL) — long hoses.

### cell5 update — 2026-10-12

- Switch-based homing VERIFIED: `home_sync(direction=0)` drives UP to the upper
  switch (IN_1 = home; the lower end-stop is IN_2 "far") and sets zero there.
  From the bottom the pass took under a minute at 180 RPM.
- Lower end-stop (IN_2) sits ≈422.6 mm below the upper switch (hit at 24.59 mm in
  a frame zeroed at the old 398 mm point). The driver's class default
  `_max_travel_mm = 400` is below that, so the tool raises the INSTANCE value
  (`--max-travel 450`); the int24 coordinate has ample range.
- Dispense Z re-taught by the operator: **421.0 mm** from the switch-based zero
  (1.6 mm above the lower switch; landed 421.03 in one move from home).
  Supersedes the 398.0 of 2026-10-08.
