"""Tests for the PID controller."""

import pytest

from custom_components.sat.const import DEADBAND, HEATING_SYSTEM_RADIATORS, HEATING_SYSTEM_UNDERFLOOR
from custom_components.sat.pid import (
    DERIVATIVE_ALPHA1,
    DERIVATIVE_ALPHA2,
    DERIVATIVE_DECAY,
    DERIVATIVE_ERROR_ALPHA,
    DERIVATIVE_RAW_CAP,
    ERROR_EPSILON,
    PID,
)


class TimestampSequence:
    """Return deterministic timestamp values, repeating the last one."""

    def __init__(self, values):
        self._values = iter(values)
        self._last = None

    def __call__(self):
        try:
            self._last = next(self._values)
        except StopIteration:
            if self._last is None:
                raise
        return self._last


def _patch_timestamp(monkeypatch, values):
    timestamp = TimestampSequence(values)
    monkeypatch.setattr("custom_components.sat.pid.timestamp", timestamp)
    return timestamp


def test_initial_state_and_availability(monkeypatch):
    _patch_timestamp(monkeypatch, [100.0])

    pid = PID(
        entity_id="climate.test",
        heating_system=HEATING_SYSTEM_RADIATORS,
        automatic_gain_value=2.0,
        heating_curve_coefficient=2.0,
        kp=1.0,
        ki=0.5,
        kd=0.1,
    )

    assert pid.available is False
    assert pid.integral == 0.0
    assert pid.raw_derivative == 0.0
    assert pid.derivative == 0.0
    assert pid.output == 0.0


def test_manual_gains_output_and_availability(monkeypatch):
    _patch_timestamp(monkeypatch, [0.0, 0.0, 10.0])

    pid = PID(
        entity_id="climate.test",
        heating_system=HEATING_SYSTEM_RADIATORS,
        automatic_gain_value=2.0,
        heating_curve_coefficient=2.0,
        kp=2.0,
        ki=1.0,
        kd=0.5,
        automatic_gains=False,
    )

    pid.update(0.05, 10.0, heating_curve=30.0)

    assert pid.available is True
    assert pid.kp == 2.0
    assert pid.ki == 1.0
    assert pid.kd == 0.5
    assert pid.proportional == 0.1
    assert pid.integral == 0.5
    assert pid.derivative == 0.0
    assert pid.output == 30.6


def test_automatic_gains_calculation(monkeypatch):
    _patch_timestamp(monkeypatch, [0.0, 0.0, 5.0])

    pid = PID(
        entity_id="climate.test",
        heating_system=HEATING_SYSTEM_UNDERFLOOR,
        automatic_gain_value=2.0,
        heating_curve_coefficient=2.0,
        kp=1.0,
        ki=1.0,
        kd=1.0,
        automatic_gains=True,
    )

    assert pid.kp == 0.0

    pid.update(0.05, 5.0, heating_curve=40.0)

    assert pid.kp == 20.0
    assert pid.ki == round(20.0 / 8400, 6)
    assert pid.kd == round(0.07 * 8400 * 20.0, 6)


def test_integral_timebase_reset_and_accumulation(monkeypatch):
    _patch_timestamp(monkeypatch, [0.0, 0.0, 10.0, 20.0, 30.0])

    pid = PID(
        entity_id="climate.test",
        heating_system=HEATING_SYSTEM_RADIATORS,
        automatic_gain_value=2.0,
        heating_curve_coefficient=2.0,
        kp=1.0,
        ki=1.0,
        kd=0.0,
    )

    pid.update(DEADBAND + 0.4, 10.0, heating_curve=10.0)
    assert pid.integral == 0.0

    pid.update(DEADBAND / 2, 20.0, heating_curve=10.0)
    assert pid.integral == 0.0

    pid.update(DEADBAND / 2, 30.0, heating_curve=10.0)
    assert pid.integral == 0.5


def test_integral_clamped_to_heating_curve(monkeypatch):
    _patch_timestamp(monkeypatch, [0.0, 0.0, 10.0])

    pid = PID(
        entity_id="climate.test",
        heating_system=HEATING_SYSTEM_RADIATORS,
        automatic_gain_value=2.0,
        heating_curve_coefficient=2.0,
        kp=1.0,
        ki=1.0,
        kd=0.0,
    )

    pid.update(DEADBAND, 10.0, heating_curve=0.5)

    assert pid.integral == 0.5


def test_derivative_filtering_and_cap(monkeypatch):
    _patch_timestamp(monkeypatch, [0.0, 0.0, 10.0, 11.0, 12.0])

    pid = PID(
        entity_id="climate.test",
        heating_system=HEATING_SYSTEM_RADIATORS,
        automatic_gain_value=2.0,
        heating_curve_coefficient=2.0,
        kp=0.0,
        ki=0.0,
        kd=1.0,
    )

    pid.update(1.0, 10.0, heating_curve=10.0)
    pid.update(11.0, 11.0, heating_curve=10.0)

    filtered_error = DERIVATIVE_ERROR_ALPHA * 11.0 + (1 - DERIVATIVE_ERROR_ALPHA) * 1.0
    derivative = (filtered_error - 1.0) / 11.0
    expected_filtered = DERIVATIVE_ALPHA1 * derivative
    expected_raw = DERIVATIVE_ALPHA2 * expected_filtered

    assert pid.raw_derivative == pytest.approx(round(expected_raw, 3), rel=1e-3)
    assert pid.derivative == pytest.approx(expected_raw, rel=1e-3)

    pid.update(1000.0, 12.0, heating_curve=10.0)
    assert pid.raw_derivative == DERIVATIVE_RAW_CAP


def test_derivative_decay_when_error_unchanged(monkeypatch):
    _patch_timestamp(monkeypatch, [0.0, 0.0, 10.0, 20.0])

    pid = PID(
        entity_id="climate.test",
        heating_system=HEATING_SYSTEM_RADIATORS,
        automatic_gain_value=2.0,
        heating_curve_coefficient=2.0,
        kp=0.0,
        ki=0.0,
        kd=1.0,
    )

    pid.update(1.0, 10.0, heating_curve=10.0)
    pid._raw_derivative = 4.0

    pid.update(1.0, 20.0, heating_curve=10.0)

    assert pid.raw_derivative == pytest.approx(4.0, rel=1e-3)


@pytest.mark.parametrize(
    ("delta", "should_update"),
    [
        (ERROR_EPSILON / 2, False),
        (ERROR_EPSILON, True),
        (0.1, True),
    ],
)
def test_derivative_update_thresholds(monkeypatch, delta, should_update):
    _patch_timestamp(monkeypatch, [0.0, 0.0, 10.0, 20.0])

    pid = PID(
        entity_id="climate.test",
        heating_system=HEATING_SYSTEM_RADIATORS,
        automatic_gain_value=2.0,
        heating_curve_coefficient=2.0,
        kp=0.0,
        ki=0.0,
        kd=1.0,
    )

    pid.update(1.0, 10.0, heating_curve=10.0)
    pid._raw_derivative = 2.0

    pid.update(1.0 + delta, 20.0, heating_curve=10.0)

    if not should_update:
        assert pid.raw_derivative == pytest.approx(2.0, rel=1e-3)
        return

    filtered_error = DERIVATIVE_ERROR_ALPHA * (1.0 + delta) + (1 - DERIVATIVE_ERROR_ALPHA) * 1.0
    derivative = (filtered_error - 1.0) / 10.0
    expected_filtered = DERIVATIVE_ALPHA1 * derivative + (1 - DERIVATIVE_ALPHA1) * 2.0
    expected_raw = DERIVATIVE_ALPHA2 * expected_filtered + (1 - DERIVATIVE_ALPHA2) * 2.0

    assert pid.raw_derivative == pytest.approx(expected_raw, rel=1e-3)


def test_derivative_freeze_in_deadband(monkeypatch):
    _patch_timestamp(monkeypatch, [0.0, 0.0, 10.0, 20.0])

    pid = PID(
        entity_id="climate.test",
        heating_system=HEATING_SYSTEM_RADIATORS,
        automatic_gain_value=2.0,
        heating_curve_coefficient=2.0,
        kp=0.0,
        ki=0.0,
        kd=1.0,
    )

    pid.update(1.0, 10.0, heating_curve=10.0)
    pid._raw_derivative = 3.0

    pid.update(DEADBAND / 2, 20.0, heating_curve=10.0)

    assert pid.raw_derivative == pytest.approx(3.0 * DERIVATIVE_DECAY, rel=1e-3)


def test_derivative_uses_internal_timing(monkeypatch):
    _patch_timestamp(monkeypatch, [0.0, 0.0, 100.0, 200.0])

    pid = PID(
        entity_id="climate.test",
        heating_system=HEATING_SYSTEM_RADIATORS,
        automatic_gain_value=2.0,
        heating_curve_coefficient=2.0,
        kp=0.0,
        ki=0.0,
        kd=1.0,
    )

    pid.update(1.0, 100.0, heating_curve=10.0)
    pid.update(2.0, 200.0, heating_curve=10.0)

    filtered_error = DERIVATIVE_ERROR_ALPHA * 2.0 + (1 - DERIVATIVE_ERROR_ALPHA) * 1.0
    derivative = (filtered_error - 1.0) / 200.0
    expected_filtered = DERIVATIVE_ALPHA1 * derivative
    expected_raw = DERIVATIVE_ALPHA2 * expected_filtered

    assert pid.raw_derivative == pytest.approx(round(expected_raw, 3), rel=1e-3)
