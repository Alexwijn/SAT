"""Tests for the PWM controller."""

from typing import Optional

import pytest
from homeassistant.core import State

from custom_components.sat.cycles import Cycle, CycleMetrics, CycleShapeMetrics
from custom_components.sat.device import DeviceState
from custom_components.sat.entry_data import PwmConfig
from custom_components.sat.pwm import PWM
from custom_components.sat.types import CycleClassification, HeatingSystem, PWMStatus
from custom_components.sat.types import CycleControlMode, CycleKind, Percentiles


def _make_device_state(
        *,
        flame_active: bool = False,
        hot_water_active: bool = False,
        setpoint: Optional[float] = 40.0,
        flow_temperature: Optional[float] = 50.0,
) -> DeviceState:
    return DeviceState(
        flame_active=flame_active,
        central_heating=True,
        hot_water_active=hot_water_active,
        setpoint=setpoint,
        flow_temperature=flow_temperature,
        return_temperature=40.0 if flow_temperature is not None else None,
        relative_modulation_level=None,
        max_modulation_level=100,
    )


def _make_cycle(classification: CycleClassification, *, control_mode: CycleControlMode = CycleControlMode.PWM) -> Cycle:
    metrics = CycleMetrics(
        requested_setpoint=Percentiles(p50=40.0, p90=40.0),
        control_setpoint=Percentiles(p50=40.0, p90=40.0),
        flow_temperature=Percentiles(p50=40.0, p90=40.0),
        return_temperature=Percentiles(p50=30.0, p90=30.0),
        relative_modulation_level=Percentiles(p50=None, p90=None),
        flow_return_delta=Percentiles(p50=10.0, p90=10.0),
        flow_control_setpoint_error=Percentiles(p50=0.0, p90=0.0),
        flow_requested_setpoint_error=Percentiles(p50=0.0, p90=0.0),
        hot_water_active_fraction=0.0,
    )
    shape = CycleShapeMetrics(
        time_in_band_seconds=180.0,
        time_to_first_overshoot_seconds=None,
        time_to_sustained_overshoot_seconds=None,
        total_overshoot_seconds=0.0,
        max_flow_control_setpoint_error=0.0,
    )
    return Cycle(
        kind=CycleKind.CENTRAL_HEATING,
        control_mode=control_mode,
        tail=metrics,
        metrics=metrics,
        shape=shape,
        classification=classification,
        start=0.0,
        end=180.0,
        sample_count=5,
        min_flow_temperature=35.0,
        max_flow_temperature=45.0,
        fraction_space_heating=1.0,
        fraction_domestic_hot_water=0.0,
    )


@pytest.fixture
def pwm() -> PWM:
    config = PwmConfig(
        cycles_per_hour=4,
        duty_cycle_seconds=4,
        force_pulse_width_modulation=False,
        maximum_relative_modulation=100,
    )
    return PWM(config, HeatingSystem.RADIATORS)


def test_initial_state_and_restore(pwm: PWM):
    assert pwm.enabled is False
    assert pwm.status is PWMStatus.IDLE
    assert pwm.state.duty_cycle is None
    assert pwm.state.last_duty_cycle_percentage is None

    state = State("sensor.pwm", "0", attributes={"pulse_width_modulation_enabled": True})
    pwm.restore(state)
    assert pwm.enabled is True


def test_update_missing_values_sets_idle(pwm: PWM, caplog):
    pwm.enable()
    device_state = _make_device_state(setpoint=None, flow_temperature=None)

    pwm.update(
        device_state=device_state,
        requested_setpoint=40.0,
        timestamp=123.0,
    )

    assert pwm.enabled is True
    assert pwm.status is PWMStatus.IDLE
    assert "PWM turned off due missing values" in caplog.text


@pytest.mark.parametrize(
    "ratio, flame_active, hot_water_active, expected, tolerance",
    [
        (0.05, False, False, (0, 1800), 0),
        (0.05, True, False, (180, 1620), 0),
        (0.15, False, False, (180, 1020), 0),
        (0.50, False, False, (450, 450), 1),
        (0.85, False, False, (1020, 180), 1),
        (0.95, False, False, (1800, 0), 0),
    ],
)
def test_calculate_duty_cycle_ranges(pwm: PWM, ratio, flame_active, hot_water_active, expected, tolerance):
    boiler_temperature = 50.0
    pwm._effective_on_temperature = boiler_temperature

    base_offset = pwm._heating_system.base_offset
    setpoint = base_offset + ratio * (boiler_temperature - base_offset)

    device_state = _make_device_state(flame_active=flame_active, hot_water_active=hot_water_active)
    on_time, off_time = pwm._calculate_duty_cycle(setpoint, device_state)

    if tolerance:
        assert on_time == pytest.approx(expected[0], abs=tolerance)
        assert off_time == pytest.approx(expected[1], abs=tolerance)
    else:
        assert (on_time, off_time) == expected


def test_update_transitions_and_cycle_limit(pwm: PWM):
    pwm.enable()
    base_offset = pwm._heating_system.base_offset
    setpoint = base_offset + 0.5 * (50.0 - base_offset)
    device_state = _make_device_state(flame_active=True, flow_temperature=50.0)

    pwm._last_update = 0.0
    pwm.update(
        device_state=device_state,
        requested_setpoint=setpoint,
        timestamp=0.0,
    )
    assert pwm.status is PWMStatus.ON
    assert pwm._current_cycle == 1

    pwm.update(
        device_state=device_state,
        requested_setpoint=setpoint,
        timestamp=450.0,
    )
    assert pwm.status is PWMStatus.OFF

    pwm._current_cycle = pwm._config.duty_cycle_seconds
    pwm.update(
        device_state=device_state,
        requested_setpoint=setpoint,
        timestamp=900.0,
    )
    assert pwm.status is PWMStatus.OFF
    assert pwm._current_cycle == pwm._config.duty_cycle_seconds


def test_pwm_on_phase_starts_when_flame_ignites(pwm: PWM):
    pwm.enable()
    base_offset = pwm._heating_system.base_offset
    setpoint = base_offset + 0.5 * (50.0 - base_offset)
    pwm._effective_on_temperature = 50.0

    pwm._last_update = 0.0
    pwm.update(
        device_state=_make_device_state(flame_active=False, flow_temperature=50.0),
        requested_setpoint=setpoint,
        timestamp=0.0,
    )

    assert pwm.status is PWMStatus.ON

    pwm.update(
        device_state=_make_device_state(flame_active=False, flow_temperature=50.0),
        requested_setpoint=setpoint,
        timestamp=120.0,
    )

    assert pwm.status is PWMStatus.ON

    pwm.update(
        device_state=_make_device_state(flame_active=True, flow_temperature=50.0),
        requested_setpoint=setpoint,
        timestamp=240.0,
    )

    assert pwm.status is PWMStatus.ON

    pwm.update(
        device_state=_make_device_state(flame_active=True, flow_temperature=50.0),
        requested_setpoint=setpoint,
        timestamp=450.0,
    )

    assert pwm.status is PWMStatus.ON

    pwm.update(
        device_state=_make_device_state(flame_active=True, flow_temperature=50.0),
        requested_setpoint=setpoint,
        timestamp=700.0,
    )

    assert pwm.status is PWMStatus.OFF


def test_cycle_count_resets_after_rolling_hour(pwm: PWM):
    pwm.enable()
    pwm._status = PWMStatus.IDLE
    pwm._current_cycle = 4
    pwm._first_duty_cycle_start = 0.0
    pwm._last_update = 0.0

    base_offset = pwm._heating_system.base_offset
    setpoint = base_offset + 0.05 * (50.0 - base_offset)
    device_state = _make_device_state(flame_active=True, flow_temperature=50.0)

    pwm.update(
        device_state=device_state,
        requested_setpoint=setpoint,
        timestamp=3701.0,
    )

    assert pwm._current_cycle == 1
    assert pwm._first_duty_cycle_start == 3701.0
    assert pwm.status is PWMStatus.ON


def test_effective_temperature_updates_when_flame_stable(pwm: PWM):
    pwm.enable()
    base_offset = pwm._heating_system.base_offset
    setpoint = base_offset + 0.5 * (50.0 - base_offset)

    pwm._status = PWMStatus.ON
    pwm._last_update = 100.0
    pwm._effective_on_temperature = 40.0
    pwm._first_duty_cycle_start = 100.0

    device_state = _make_device_state(
        flame_active=True,
        flow_temperature=50.0,
    )

    pwm.update(
        device_state=device_state,
        requested_setpoint=setpoint,
        timestamp=110.0,
    )

    assert pwm.status is PWMStatus.ON
    assert pwm._effective_on_temperature == pytest.approx(43.0)


def test_disable_resets_state(pwm: PWM):
    pwm._enabled = True
    pwm._status = PWMStatus.ON
    pwm._duty_cycle = (100, 100)
    pwm._last_duty_cycle_percentage = 0.5

    pwm.disable()

    assert pwm.enabled is False
    assert pwm.status is PWMStatus.IDLE
    assert pwm.state.duty_cycle is None
    assert pwm.state.last_duty_cycle_percentage is None


def test_cycle_end_enables_on_overshoot(pwm: PWM):
    cycle = _make_cycle(CycleClassification.OVERSHOOT, control_mode=CycleControlMode.CONTINUOUS)
    pwm.disable()
    pwm.on_cycle_end(cycle)
    assert pwm.enabled is True


def test_cycle_end_pwm_underheat_disables(pwm: PWM):
    cycle = _make_cycle(CycleClassification.UNDERHEAT, control_mode=CycleControlMode.PWM)
    pwm.disable()
    pwm.on_cycle_end(cycle)
    assert pwm.enabled is False
