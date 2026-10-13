"""T0 unit tests for cell5's smart plugs (lamp + fan).

No hardware: ``PumpZThermalCell.__init__`` takes its drivers as
arguments, so the fakes below stand in for the plug driver, the Z motor
and the hotplate. Only ``open()`` touches a device, and the one test
that calls it fails before any port is opened.

Covers the ``fan`` action set added on 2026-09-30 (issue #39): the fan
and the lamp are two plugs that must never be confused, the fan is
optional, and ``stop()`` switches it off with everything else. The lamp
is re-tested through the same paths because both now share the plug
helpers.

These do **not** verify the hardware; the bench does (CLAUDE.md rule 4).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cell import pump_z_thermal_cell  # noqa: E402
from cell.cell_protocol import (  # noqa: E402
    DeviceFaultError,
    InvalidArgError,
    TransportError,
    WrongStateError,
)
from cell.pump_z_thermal_cell import (  # noqa: E402
    PumpZThermalCell,
    PumpZThermalConfig,
)
from external.SmartPlugController.smartplugcontroller import (  # noqa: E402
    DeviceEntry,
    DeviceReport,
    SwitchResult,
)

#: The bench addresses: the lamp's plug is in the driver's list, the
#: fan's is reached by a bare IP the list does not know (LP #29).
LAMP_IP = "192.168.0.237"
FAN_IP = "192.168.0.77"
ORIGINAL_SETPOINT_C = 25.0


class FakePlug:
    """The plug driver: one listed plug, every switch recorded by IP."""

    def __init__(self) -> None:
        self._entries = [
            DeviceEntry(
                device_type="tapo p110m", name="plug1", mac="", ip=LAMP_IP
            )
        ]
        self.is_on: dict[str, bool] = {LAMP_IP: False, FAN_IP: False}
        self.switched: list[tuple[str, bool]] = []
        #: IPs whose next read/switch reports a failure.
        self.broken: set[str] = set()

    def resolve_targets(self, target: str) -> list[DeviceEntry]:
        if target.lower() == "all":
            return list(self._entries)
        return [e for e in self._entries if target in (e.name, e.ip)]

    async def read(self, entry: DeviceEntry) -> DeviceReport:
        if entry.ip in self.broken:
            return DeviceReport(entry=entry, error="TimeoutError: no reply")
        return DeviceReport(entry=entry, is_on=self.is_on[entry.ip])

    async def switch_many(
        self, entries: list[DeviceEntry], *, turn_on: bool
    ) -> list[SwitchResult]:
        results = []
        for entry in entries:
            if entry.ip in self.broken:
                results.append(
                    SwitchResult(entry=entry, error="TimeoutError: no reply")
                )
                continue
            before = self.is_on[entry.ip]
            self.is_on[entry.ip] = turn_on
            self.switched.append((entry.ip, turn_on))
            results.append(
                SwitchResult(entry=entry, before=before, after=turn_on)
            )
        return results


class FakeHotplate:
    """Just enough of ``RctDigital`` for ``stop()`` and ``status()``."""

    port = "/dev/null"

    def __init__(self) -> None:
        self.heater_stops = 0
        self.motor_stops = 0

    def stop_heater(self) -> None:
        self.heater_stops += 1

    def stop_motor(self) -> None:
        self.motor_stops += 1

    def read_plate_temperature(self) -> float:
        return 21.5

    def read_target_temperature(self) -> float:
        return ORIGINAL_SETPOINT_C

    def read_safety_temperature(self) -> float:
        return 50.0

    def read_probe_temperature(self) -> float:
        return 22.0

    def read_speed(self) -> float:
        return 0.0

    def read_target_speed(self) -> float:
        return 200.0

    def read_name(self) -> str:
        return "RCT digital"


def make_cell(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fan_target: str | None = FAN_IP,
    plug: FakePlug | None | str = "default",
) -> tuple[PumpZThermalCell, FakePlug | None, FakeHotplate]:
    """Build a pumpless cell5 over fakes; the Z stop is a no-op."""
    monkeypatch.setattr(
        pump_z_thermal_cell.MKSMotor,
        "stop_group_hard",
        staticmethod(lambda motors: None),
    )
    fake_plug = FakePlug() if plug == "default" else plug
    hotplate = FakeHotplate()
    config = PumpZThermalConfig(
        pump_enabled=False, lamp_target=LAMP_IP, fan_target=fan_target
    )
    cell = PumpZThermalCell(
        None, object(), hotplate, fake_plug, config, ORIGINAL_SETPOINT_C
    )
    return cell, fake_plug, hotplate


def test_fan_switches_its_own_plug_and_not_the_lamps(monkeypatch) -> None:
    cell, plug, _ = make_cell(monkeypatch)
    state = cell.set_fan(enabled=True)
    assert state == {"is_on": True, "target": FAN_IP, "devices": [FAN_IP]}
    assert plug.switched == [(FAN_IP, True)]
    assert plug.is_on[LAMP_IP] is False
    assert cell.status()["fan_on"] is True
    assert cell.status()["lamp_on"] is None  # never commanded, never read


def test_lamp_switches_its_own_plug_and_not_the_fans(monkeypatch) -> None:
    cell, plug, _ = make_cell(monkeypatch)
    state = cell.set_lamp(enabled=True)
    # The lamp's plug is in the driver's list, so it answers by name.
    assert state == {"is_on": True, "target": LAMP_IP, "devices": ["plug1"]}
    assert plug.switched == [(LAMP_IP, True)]
    assert plug.is_on[FAN_IP] is False
    assert cell.status()["fan_on"] is None


def test_read_fan_reports_the_plug_not_the_last_command(monkeypatch) -> None:
    cell, plug, _ = make_cell(monkeypatch)
    cell.set_fan(enabled=True)
    plug.is_on[FAN_IP] = False  # switched off at the wall
    assert cell.read_fan()["is_on"] is False
    assert cell.status()["fan_on"] is False


def test_no_fan_table_answers_409_and_diagnoses_clean(monkeypatch) -> None:
    cell, plug, _ = make_cell(monkeypatch, fan_target=None)
    with pytest.raises(WrongStateError, match="no fan configured"):
        cell.read_fan()
    with pytest.raises(WrongStateError, match="no fan configured"):
        cell.set_fan(enabled=True)
    assert plug.switched == []
    # Absent by configuration is not a fault.
    assert cell._fan_diagnosis() == {"present": False, "ok": True}
    assert cell.status()["fan_on"] is None


def test_missing_credentials_answer_409_for_both_plugs(monkeypatch) -> None:
    cell, _, _ = make_cell(monkeypatch, plug=None)
    with pytest.raises(WrongStateError, match="fan plug is not configured"):
        cell.set_fan(enabled=True)
    with pytest.raises(WrongStateError, match="lamp plug is not configured"):
        cell.set_lamp(enabled=True)
    assert cell._fan_diagnosis()["ok"] is False


def test_fan_diagnosis_names_the_target(monkeypatch) -> None:
    cell, _, _ = make_cell(monkeypatch)
    assert cell._fan_diagnosis() == {
        "present": True,
        "target": FAN_IP,
        "devices": [FAN_IP],
        "ok": True,
    }


def test_unreachable_fan_is_a_transport_error_on_read(monkeypatch) -> None:
    cell, plug, _ = make_cell(monkeypatch)
    plug.broken.add(FAN_IP)
    with pytest.raises(TransportError) as excinfo:
        cell.read_fan()
    assert excinfo.value.command == "fan"


def test_failed_fan_switch_is_a_device_fault(monkeypatch) -> None:
    cell, plug, _ = make_cell(monkeypatch)
    plug.broken.add(FAN_IP)
    with pytest.raises(DeviceFaultError, match="fan switch failed"):
        cell.set_fan(enabled=True)
    assert cell.status()["fan_on"] is None


def test_stop_switches_the_fan_off_with_everything_else(monkeypatch) -> None:
    cell, plug, hotplate = make_cell(monkeypatch)
    cell.set_lamp(enabled=True)
    cell.set_fan(enabled=True)
    plug.switched.clear()
    cell.stop()
    assert hotplate.heater_stops == 1
    assert hotplate.motor_stops == 1
    assert plug.switched == [(LAMP_IP, False), (FAN_IP, False)]
    assert plug.is_on == {LAMP_IP: False, FAN_IP: False}
    assert cell.status()["fan_on"] is False


def test_stop_without_a_fan_does_not_touch_a_second_plug(monkeypatch) -> None:
    cell, plug, _ = make_cell(monkeypatch, fan_target=None)
    cell.stop()
    assert plug.switched == [(LAMP_IP, False)]


def test_stop_reports_a_fan_that_did_not_switch_off(monkeypatch) -> None:
    cell, plug, hotplate = make_cell(monkeypatch)
    plug.broken.add(FAN_IP)
    with pytest.raises(DeviceFaultError, match="stop incomplete: fan:"):
        cell.stop()
    # The fan failing must not have kept the heat sources on.
    assert hotplate.heater_stops == 1
    assert plug.switched == [(LAMP_IP, False)]


@pytest.mark.parametrize("fan_target", ["all", "ALL", LAMP_IP])
def test_open_refuses_a_fan_target_that_would_switch_the_lamp(
    fan_target: str,
) -> None:
    """`fan/switch` must never be able to light the IR lamp.

    The check runs before any port is opened, so this needs no hardware.
    """
    config = PumpZThermalConfig(
        pump_enabled=False, lamp_target=LAMP_IP, fan_target=fan_target
    )
    with pytest.raises(InvalidArgError, match="must name one plug of its own"):
        PumpZThermalCell.open(config)


# ── Hotplate link loss (issue #41) ─────────────────────────────────────────


class DeadHotplate(FakeHotplate):
    """A handle on a node the kernel has already deleted.

    Every read raises what the real driver raised on the bench after the
    IKA re-enumerated: ``termios.error(5, 'Input/output error')``.
    """

    def __init__(self, exc: BaseException) -> None:
        super().__init__()
        self.exc = exc
        self.closes = 0

    def read_plate_temperature(self) -> float:
        raise self.exc

    def close(self) -> None:
        self.closes += 1


def _patch_reopen(
    monkeypatch: pytest.MonkeyPatch, port: str | None, replacement
) -> list[str]:
    """Make ``find_rct_port`` answer ``port`` and ``RctDigital`` build
    ``replacement``; returns the ports opened."""
    opened: list[str] = []

    def fake_rct(p: str):
        opened.append(p)
        if isinstance(replacement, BaseException):
            raise replacement
        return replacement

    monkeypatch.setattr(pump_z_thermal_cell, "find_rct_port", lambda: port)
    monkeypatch.setattr(pump_z_thermal_cell, "RctDigital", fake_rct)
    return opened


@pytest.mark.parametrize(
    "link_error",
    [
        pump_z_thermal_cell.termios.error(5, "Input/output error"),
        OSError(5, "Input/output error"),
    ],
    ids=["termios.error", "OSError"],
)
def test_dead_hotplate_handle_is_reopened_once_and_the_read_retried(
    monkeypatch, link_error
) -> None:
    cell, _, _ = make_cell(monkeypatch)
    dead = DeadHotplate(link_error)
    cell._hotplate = dead  # noqa: SLF001 — the handle under test
    fresh = FakeHotplate()
    opened = _patch_reopen(monkeypatch, "/dev/ttyACM0", fresh)

    state = cell.read_hotplate()

    assert state["plate_c"] == 21.5
    assert opened == ["/dev/ttyACM0"]
    assert dead.closes == 1
    assert cell._hotplate is fresh  # noqa: SLF001


def test_reopen_without_an_rct_on_the_bus_is_a_transport_error(
    monkeypatch,
) -> None:
    cell, _, _ = make_cell(monkeypatch)
    cell._hotplate = DeadHotplate(OSError(5, "Input/output error"))  # noqa: SLF001
    opened = _patch_reopen(monkeypatch, None, FakeHotplate())

    with pytest.raises(TransportError, match="no IKA RCT found"):
        cell.read_hotplate()
    assert opened == []


def test_second_link_failure_after_reopen_is_a_transport_error(
    monkeypatch,
) -> None:
    cell, _, _ = make_cell(monkeypatch)
    cell._hotplate = DeadHotplate(OSError(5, "Input/output error"))  # noqa: SLF001
    still_dead = DeadHotplate(OSError(5, "Input/output error"))
    opened = _patch_reopen(monkeypatch, "/dev/ttyACM0", still_dead)

    with pytest.raises(TransportError, match="failed again after reopen"):
        cell.read_hotplate()
    # One reopen, not a loop.
    assert opened == ["/dev/ttyACM0"]


def test_reopen_that_cannot_open_the_port_is_a_transport_error(
    monkeypatch,
) -> None:
    cell, _, _ = make_cell(monkeypatch)
    cell._hotplate = DeadHotplate(OSError(5, "Input/output error"))  # noqa: SLF001
    _patch_reopen(monkeypatch, "/dev/ttyACM0", OSError(13, "Permission denied"))

    with pytest.raises(TransportError, match="reopen on /dev/ttyACM0 failed"):
        cell.read_hotplate()
