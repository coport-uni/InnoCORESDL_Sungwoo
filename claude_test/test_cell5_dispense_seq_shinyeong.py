"""cell5 filmed sequence: Z top -> 398 mm, pump 25 uL (command 1 aspirate, command 2 dispense), Z -> top."""

import sys
import time
from sy01b import SyringePumpController as S

sys.path.insert(0, "claude_test")
from test_cell5_z_shinyeong import main as zmain  # noqa: E402

Z_DISPENSE, Z_TOP, UL = 398.0, 2.0, 25.0
PUMP = "/dev/serial/by-path/pci-0000:00:14.0-usb-0:4.3.1:1.0-port0"

t0 = time.monotonic()
if zmain(["--max-mm", "400", f"move:{Z_DISPENSE}", "pos"]) != 0:
    sys.exit("Z descent failed — pump not run")
print(f"  Z at dispense height ({time.monotonic() - t0:.1f}s)", flush=True)
p = S.open(S.Config(port=PUMP, step_mode=S.StepMode.NORMAL, syringe_uL=125))
assert p.query_serial_number() == "30946"
p.move_valve_to_port(1)
p.aspirate_uL(UL)
a = p.query_plunger_position()
p.move_valve_to_port(2)
p.dispense_uL(0)
d = p.query_plunger_position()
st = p.query_status()
print(
    f"  pump: aspirated {a} steps ({UL:g} uL) -> dispensed to {d}, error {st.error.name} ({time.monotonic() - t0:.1f}s)",
    flush=True,
)
p._transport.close()
rc = zmain(["--max-mm", "400", f"move:{Z_TOP}", "pos"])
print(f"  Z back at top, rc {rc} ({time.monotonic() - t0:.1f}s)")
