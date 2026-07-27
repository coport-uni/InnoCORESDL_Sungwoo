"""Device plumbing for the demo scenario.

Every device used by ``main.py`` gets a context manager here that opens
it, applies safety-relevant defaults, and guarantees cleanup (heater
off, motor stopped and closed, pump port released) even when the
scenario aborts mid-run. The scenario itself stays in ``main.py`` and
only calls high-level driver methods on the yielded objects.

All drivers are the vendored copies under ``external/`` — no new device
code lives here.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager, suppress
from typing import Iterator

from external.HotplateController.hotplate_controller import (
    RctDigital,
    find_rct_port,
)
from mks_motor import MKSMotor, prepare_usb_nodes, release_ftdi_sio
from external.SmartPlugController.smartplugcontroller import (
    ControllerError,
    SmartPlugController,
)
from sy01b import SyringePumpController


@contextmanager
def hotplate(port: str | None = None) -> Iterator[RctDigital]:
    """Yield a connected IKA RCT digital; always safe on exit.

    On exit — normal or exception — the heater is stopped, the original
    temperature setpoint is restored, and the serial port is closed.

    Args:
        port: Serial port path. ``None`` auto-detects by USB VID:PID
            via ``find_rct_port``.

    Raises:
        RuntimeError: No RCT digital found on the USB bus.
    """
    resolved = port or find_rct_port()
    if resolved is None:
        raise RuntimeError(
            "RCT digital not found by USB VID:PID; pass the port explicitly"
        )
    rct = RctDigital(resolved)
    original_setpoint = rct.read_target_temperature()
    try:
        yield rct
    finally:
        try:
            rct.stop_heater()
            rct.set_target_temperature(original_setpoint)
        finally:
            rct.close()


@contextmanager
def motor(
    serial: str | None = None, coord_invert: bool = False
) -> Iterator[MKSMotor]:
    """Yield a single configured MKS SERVO57D (standalone Z axis).

    Rebuilds the FTDI /dev nodes first (Docker private /dev goes stale
    after USB re-enumeration) and detaches ``ftdi_sio``. On an exception
    the motor is emergency-stopped before the device is closed.

    Args:
        serial: FTDI adapter serial number. ``None`` opens the first
            enumerated adapter — fine on the demo bench where this is
            the only FTDI device.
        coord_invert: Forwarded to ``MKSMotor.open`` for axes whose
            encoder positive direction points into the closed limit.

    Raises:
        RuntimeError: Motor setup (mode / slave-response) failed.
    """
    prepare_usb_nodes()
    release_ftdi_sio()
    if serial is not None:
        mks = MKSMotor.open(serial=serial, coord_invert=coord_invert)
    else:
        mks = MKSMotor.open(coord_invert=coord_invert)
    try:
        if not mks.setup():
            raise RuntimeError("MKS motor setup failed; check CAN wiring")
        yield mks
    except BaseException:
        with suppress(Exception):
            mks.emergency_stop()
        raise
    finally:
        mks.close()


@contextmanager
def syringe_pump(
    port: str, *, syringe_uL: int = 125
) -> Iterator[SyringePumpController]:
    """Yield an opened, diagnosed SY-01B syringe pump.

    Runs ``diagnose()`` after opening and refuses to yield a pump whose
    pre-init status forbids initialization. The port is closed on exit.

    Args:
        port: Device path or ``"VID:PID"`` string (resolved by the
            driver), e.g. ``"1A86:7523"``.
        syringe_uL: Installed syringe volume in µL.

    Raises:
        RuntimeError: The pump reports a state that blocks
            initialization.
    """
    cfg = SyringePumpController.Config(port=port, syringe_uL=syringe_uL)
    pump = SyringePumpController.open(cfg)
    try:
        report = pump.diagnose()
        if not report.ok_to_initialize:
            raise RuntimeError(
                f"pump not ready to initialize: {report.pre_init_status.error.name}"
            )
        yield pump
    finally:
        pump.close()


def plug_controller() -> SmartPlugController | None:
    """Build the Tapo plug controller, or ``None`` when unavailable.

    Credentials come from ``KASA_USERNAME``/``KASA_PASSWORD`` or the
    ``secure.env`` beside the vendored module; the device list is the
    vendored ``device_list.md``. Missing configuration is not fatal to
    the scenario — the plug steps are skipped with a notice.
    """
    try:
        return SmartPlugController.from_files()
    except ControllerError as exc:
        print(f"[plug] unavailable, skipping plug steps: {exc}")
        return None


def plug_switch(
    ctrl: SmartPlugController | None, target: str, *, on: bool
) -> None:
    """Switch the plug(s) matching ``target`` on or off (blocking).

    Sync façade over the async kasa API so ``main.py`` stays
    synchronous. A ``None`` controller (missing credentials) is a
    no-op.

    Args:
        ctrl: Controller from :func:`plug_controller`, or ``None``.
        target: ``"all"``, a device name, or an IP from the list.
        on: ``True`` to turn on, ``False`` to turn off.

    Raises:
        RuntimeError: No device matches, or a switch failed.
    """
    if ctrl is None:
        return
    entries = ctrl.resolve_targets(target)
    if not entries:
        raise RuntimeError(f"no plug matches {target!r} in device_list.md")
    results = asyncio.run(ctrl.switch_many(entries, turn_on=on))
    failed = [r for r in results if not r.ok]
    if failed:
        raise RuntimeError(
            "plug switch failed: "
            + "; ".join(f"{r.entry.name}: {r.error}" for r in failed)
        )
    for result in results:
        state = "ON" if result.after else "OFF"
        print(f"[plug] {result.entry.name} -> {state}")
