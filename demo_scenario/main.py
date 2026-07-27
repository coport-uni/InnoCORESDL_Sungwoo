"""Demo scenario: motor + hotplate/plug + syringe dispense.

Sequence (operator-confirmed before the first motion):

1. Motor homes, then moves down ``MOTOR_DOWN_MM``.
2. Smart plug and hotplate start together; the plate holds
   ``HOTPLATE_TARGET_C`` for ``HEAT_HOLD_S`` while the temperature is
   printed.
3. The syringe pump dispenses ``DISPENSE_UL`` through the tip.
4. Motor returns to the origin.
5. Heater and smart plug stop together.

Run from the repo root (so ``external.*`` imports resolve)::

    python -m demo_scenario.main

Prerequisites:
    - Operator present with the motion path clear; keep an e-stop handy.
    - Plug credentials in ``external/SmartPlugController/secure.env`` (or
      ``KASA_USERNAME``/``KASA_PASSWORD``); without them the plug steps
      are skipped with a notice.
    - No other process (cell server, standalone tools) holding the
      pump, hotplate, or motor ports.
"""

import os
import time

from demo_scenario.devices import (
    hotplate,
    motor,
    plug_controller,
    plug_switch,
    syringe_pump,
)

# ── Scenario parameters ────────────────────────────────────────────────
MOTOR_SERIAL = os.environ.get("DEMO_MOTOR_SERIAL")  # None → first adapter
MOTOR_DOWN_MM = 20.0  # downward travel from the origin
MOTOR_SPEED_PCT = 20
MOTOR_ACCEL_PCT = 10

HOTPLATE_TARGET_C = 30.0
HEAT_HOLD_S = 60.0  # heat for one minute
TEMP_POLL_S = 5.0

PUMP_PORT = "1A86:7523"  # CH340 USB-serial, resolved by VID:PID
PUMP_INIT_FORCE = 2  # one-third force (50/100 µL syringes)
DISPENSE_UL = 5.0
# M05 Bi-pass valve: source and sink must be 90° apart, not 180°
# (firmware ports 1 & 3 share a fluid state) — see LearnedPatterns #1.
VALVE_RESERVOIR_PORT = 2
VALVE_TIP_PORT = 1

PLUG_TARGET = "plug1"


def confirm_motion() -> None:
    """Gate the first motor move on an explicit operator go-ahead."""
    input("Clear the motor path, e-stop ready — press Enter to start... ")


def move_down(mks) -> None:
    """Home the motor, then travel down to ``MOTOR_DOWN_MM``."""
    print(f"[motor] homing, then down to {MOTOR_DOWN_MM} mm")
    mks.home()
    mks.move_to(
        MOTOR_DOWN_MM, speed_pct=MOTOR_SPEED_PCT, accel_pct=MOTOR_ACCEL_PCT
    )
    print(f"[motor] at {mks.read_position_mm():.2f} mm")


def move_home(mks) -> None:
    """Return the motor to the origin."""
    print("[motor] returning to origin")
    mks.move_to(0, speed_pct=MOTOR_SPEED_PCT, accel_pct=MOTOR_ACCEL_PCT)
    print(f"[motor] at {mks.read_position_mm():.2f} mm")


def heat_and_hold(rct) -> None:
    """Heat to ``HOTPLATE_TARGET_C`` and hold for ``HEAT_HOLD_S``."""
    print(
        f"[hotplate] plate {rct.read_plate_temperature():.1f} °C, "
        f"heating to {HOTPLATE_TARGET_C:.0f} °C for {HEAT_HOLD_S:.0f} s"
    )
    rct.set_target_temperature(HOTPLATE_TARGET_C)
    rct.start_heater()
    deadline = time.monotonic() + HEAT_HOLD_S
    while time.monotonic() < deadline:
        time.sleep(TEMP_POLL_S)
        print(f"[hotplate] plate {rct.read_plate_temperature():.1f} °C")


def dispense(pump) -> None:
    """Draw ``DISPENSE_UL`` from the reservoir and dispense at the tip."""
    print(f"[pump] initializing, then dispensing {DISPENSE_UL} µL")
    pump.initialize(force=PUMP_INIT_FORCE)
    pump.move_valve_to_port(VALVE_RESERVOIR_PORT)
    pump.aspirate_uL(DISPENSE_UL)
    pump.move_valve_to_port(VALVE_TIP_PORT)
    pump.dispense_uL(0)
    print("[pump] dispense complete")


def main() -> None:
    plug = plug_controller()
    with (
        motor(MOTOR_SERIAL) as mks,
        hotplate() as rct,
        syringe_pump(PUMP_PORT) as pump,
    ):
        confirm_motion()
        move_down(mks)  # 1. origin → down MOTOR_DOWN_MM
        plug_switch(plug, PLUG_TARGET, on=True)  # 2. plug + heater on
        heat_and_hold(rct)  # 30 °C for one minute
        dispense(pump)  # 3. 5 µL dispense
        move_home(mks)  # 4. back to origin
        rct.stop_heater()  # 5. heater + plug off
        plug_switch(plug, PLUG_TARGET, on=False)
    print("Scenario complete.")


if __name__ == "__main__":
    main()
