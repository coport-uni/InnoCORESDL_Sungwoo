"""Unit tests for :class:`cell.balance_linear_cell.BalanceLinearCell`.

No hardware: ``BalanceLinearCell.__init__`` takes the two drivers as
arguments, so the fakes below stand in for the amp and the balance.

These cover the one thing the bench proved was missing — ``diagnose()``
answering "healthy" for devices that never replied. On 2026-07-28 the real
cell4 returned ``stage: {model: None, version: None, ok: True}`` and
``ok_to_initialize: true`` while RS485 reads were failing 100%, which is the
last gate before an operator commands a rail that has no software stop
(docs/L1_AUDIT.md GAP-1).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cell.balance_linear_cell import (  # noqa: E402
    BalanceLinearCell,
    BalanceLinearConfig,
)

BALANCE_MODEL = "Model  BCE224I-1SKR"
BALANCE_SERIAL = "SerNo.    0047304196"
LINEAR_MODEL = "MDDLN45SL"
LINEAR_VERSION = "Ver.1.016"


class FakeScale:
    """Balance stand-in; ``None`` means the read got no reply."""

    def __init__(
        self,
        model: str | None = BALANCE_MODEL,
        serial: str | None = BALANCE_SERIAL,
    ) -> None:
        self._model = model
        self._serial = serial

    def get_model_number(self) -> str | None:
        return self._model

    def get_serial_number(self) -> str | None:
        return self._serial


class FakeLinear:
    """Amp stand-in; ``None`` is what the driver returns when every
    retry of an RS485 exchange times out."""

    def __init__(
        self,
        model: str | None = LINEAR_MODEL,
        version: str | None = LINEAR_VERSION,
    ) -> None:
        self._model = model
        self._version = version

    def read_model_name(self) -> str | None:
        return self._model

    def read_software_version(self) -> str | None:
        return self._version


def _cell(scale: FakeScale, lin: FakeLinear) -> BalanceLinearCell:
    return BalanceLinearCell(lin, scale, BalanceLinearConfig())


def test_diagnose_ok_when_both_devices_answer() -> None:
    d = _cell(FakeScale(), FakeLinear()).diagnose()

    assert d["balance"]["ok"] is True
    assert d["stage"]["ok"] is True
    assert d["ok_to_initialize"] is True
    assert d["stage"]["model"] == LINEAR_MODEL
    assert d["versions"]["linear"] == LINEAR_VERSION


def test_diagnose_reports_silent_amp_as_not_ok() -> None:
    """The exact bench failure: amp answers nothing, balance is fine."""
    d = _cell(FakeScale(), FakeLinear(model=None, version=None)).diagnose()

    assert d["stage"]["ok"] is False
    assert d["ok_to_initialize"] is False
    # The balance is genuinely healthy and must not be dragged down with it.
    assert d["balance"]["ok"] is True


@pytest.mark.parametrize(
    "model,version",
    [(None, LINEAR_VERSION), (LINEAR_MODEL, None)],
)
def test_diagnose_needs_both_amp_reads(
    model: str | None, version: str | None
) -> None:
    """Half an answer is not an answer — one None is enough to fail."""
    d = _cell(FakeScale(), FakeLinear(model, version)).diagnose()

    assert d["stage"]["ok"] is False
    assert d["ok_to_initialize"] is False


@pytest.mark.parametrize(
    "model,serial",
    [(None, BALANCE_SERIAL), (BALANCE_MODEL, None), (None, None)],
)
def test_diagnose_reports_silent_balance_as_not_ok(
    model: str | None, serial: str | None
) -> None:
    d = _cell(FakeScale(model, serial), FakeLinear()).diagnose()

    assert d["balance"]["ok"] is False
    assert d["ok_to_initialize"] is False
    assert d["stage"]["ok"] is True


def test_diagnose_absent_pump_does_not_block_initialize() -> None:
    """cell4 has no pump by design, so its absence is not a fault."""
    d = _cell(FakeScale(), FakeLinear()).diagnose()

    assert d["pump"]["present"] is False
    assert d["pump"]["ok"] is True
    assert d["ok_to_initialize"] is True
