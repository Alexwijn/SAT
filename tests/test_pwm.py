"""Tests for the PWM controller."""

from typing import Optional

import pytest
from homeassistant.core import State

from custom_components.sat.cycles import Cycle, CycleMetrics, CycleShapeMetrics
from custom_components.sat.device import DeviceState
from custom_components.sat.entry_data import PwmConfig
from custom_components.sat.const import HEATER_STARTUP_TIMEFRAME, PWM_IGNITION_TIMEOUT_SECONDS
from custom_components.sat.helpers import timestamp as wall_clock
from custom_components.sat.pwm import PWM
from custom_components.sat.types import CycleClassification, HeatingSystem, PWMStatus
from custom_components.sat.types import CycleControlMode, CycleKind, Percentiles


def _make_device_state(
        *,
        flame_active: bool = False,
        central_heating: bool = True,
        hot_water_active: bool = False,
        setpoint: Optional[float] = 40.0,
        flow_temperature: Optional[float] = 50.0,
) -> DeviceState:
    return DeviceState(
        flame_active=flame_active,
        central_heating=central_heating,
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
        duty_cycle_seconds=780,
        maximum_relative_modulation=100,
        force_pulse_width_modulation=False,
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


def test_low_duty_residual_dhw_flame_does_not_start_cycle(pwm: PWM):
    """A flame left over from a hot water pulse must not grant the minimum on-time."""
    boiler_temperature = 50.0
    pwm._effective_on_temperature = boiler_temperature

    base_offset = pwm._heating_system.base_offset
    setpoint = base_offset + 0.05 * (boiler_temperature - base_offset)

    device_state = _make_device_state(flame_active=True, central_heating=False)
    on_time, off_time = pwm._calculate_duty_cycle(setpoint, device_state)

    assert (on_time, off_time) == (0, 1800)


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
    assert pwm.cycles_this_hour == 1
    assert pwm.state.floor_known is False

    pwm.update(
        device_state=device_state,
        requested_setpoint=setpoint,
        timestamp=450.0,
    )
    assert pwm.status is PWMStatus.OFF
    assert pwm.state.effective_on_temperature == 50.0

    pwm._cycle_starts = [800.0] * pwm._config.cycles_per_hour
    pwm.update(
        device_state=device_state,
        requested_setpoint=setpoint,
        timestamp=900.0,
    )
    assert pwm.status is PWMStatus.OFF
    assert pwm.cycles_this_hour == pwm._config.cycles_per_hour


def test_minimum_flame_on_hold_prevents_early_off_when_recalculated_on_time_drops(pwm: PWM):
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
    assert pwm._waiting_for_flame_on is True

    pwm.update(
        device_state=_make_device_state(flame_active=True, flow_temperature=50.0),
        requested_setpoint=setpoint,
        timestamp=60.0,
    )
    assert pwm.status is PWMStatus.ON
    assert pwm._waiting_for_flame_on is False

    pwm.update(
        device_state=_make_device_state(flame_active=True, flow_temperature=90.0),
        requested_setpoint=setpoint,
        timestamp=120.0,
    )
    assert pwm.status is PWMStatus.ON

    pwm.update(
        device_state=_make_device_state(flame_active=True, flow_temperature=90.0),
        requested_setpoint=setpoint,
        timestamp=200.0,
    )
    assert pwm.status is PWMStatus.ON
    assert pwm.state.effective_on_temperature == 50.0

    pwm.update(
        device_state=_make_device_state(flame_active=True, flow_temperature=90.0),
        requested_setpoint=setpoint,
        timestamp=240.0,
    )
    assert pwm.status is PWMStatus.OFF
    assert pwm.state.effective_on_temperature == 90.0


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
    assert pwm.state.floor_observed is False
    assert pwm.state.effective_on_temperature == 50.0

    pwm.update(
        device_state=_make_device_state(flame_active=True, flow_temperature=50.0),
        requested_setpoint=setpoint,
        timestamp=450.0,
    )

    assert pwm.status is PWMStatus.ON
    assert pwm.state.floor_observed is True

    pwm.update(
        device_state=_make_device_state(flame_active=True, flow_temperature=50.0),
        requested_setpoint=setpoint,
        timestamp=700.0,
    )

    assert pwm.status is PWMStatus.OFF


def test_cycle_count_resets_after_rolling_hour(pwm: PWM):
    pwm.enable()
    pwm._status = PWMStatus.IDLE
    pwm._cycle_starts = [0.0, 25.0, 50.0, 75.0]
    pwm._last_update = 0.0

    base_offset = pwm._heating_system.base_offset
    setpoint = base_offset + 0.05 * (50.0 - base_offset)
    device_state = _make_device_state(flame_active=True, flow_temperature=50.0)

    pwm.update(
        device_state=device_state,
        requested_setpoint=setpoint,
        timestamp=3701.0,
    )

    assert pwm.cycles_this_hour == 1
    assert pwm._cycle_starts == [3701.0]
    assert pwm.status is PWMStatus.ON


def test_floor_is_observed_once_the_burn_is_established(pwm: PWM):
    pwm.enable()
    base_offset = pwm._heating_system.base_offset
    setpoint = base_offset + 0.5 * (50.0 - base_offset)

    pwm._status = PWMStatus.ON
    pwm._last_update = 100.0
    pwm._flame_on_at = 100.0
    pwm._effective_on_temperature = 40.0

    pwm.update(device_state=_make_device_state(flame_active=True, flow_temperature=50.0), requested_setpoint=setpoint, timestamp=110.0)
    assert pwm.state.effective_on_temperature == 40.0
    assert pwm.state.floor_observed is False

    pwm.update(device_state=_make_device_state(flame_active=True, flow_temperature=50.0), requested_setpoint=setpoint, timestamp=100.0 + HEATER_STARTUP_TIMEFRAME)
    assert pwm.status is PWMStatus.ON
    assert pwm.state.effective_on_temperature == 50.0
    assert pwm.state.floor_observed is True

    pwm.update(device_state=_make_device_state(flame_active=True, flow_temperature=52.0), requested_setpoint=setpoint, timestamp=130.0 + HEATER_STARTUP_TIMEFRAME)
    assert pwm.state.effective_on_temperature == pytest.approx(50.6)


def test_reset_keeps_enabled(pwm: PWM):
    pwm._enabled = True
    pwm._status = PWMStatus.ON
    pwm._duty_cycle = (100, 100)

    pwm.reset()

    assert pwm.enabled is True
    assert pwm.status is PWMStatus.IDLE
    assert pwm.state.duty_cycle is None


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


def test_pwm_on_phase_waits_for_late_ignition(pwm: PWM):
    pwm.enable()
    base_offset = pwm._heating_system.base_offset
    setpoint = base_offset + 0.5 * (50.0 - base_offset)
    pwm._effective_on_temperature = 50.0
    pwm._last_update = 0.0

    for moment in (0.0, HEATER_STARTUP_TIMEFRAME + 60.0, PWM_IGNITION_TIMEOUT_SECONDS - 60.0):
        pwm.update(
            device_state=_make_device_state(flame_active=False, flow_temperature=50.0),
            requested_setpoint=setpoint,
            timestamp=moment,
        )

        assert pwm.status is PWMStatus.ON

    ignition = PWM_IGNITION_TIMEOUT_SECONDS - 30.0
    on_time = pwm.state.on_time_seconds

    pwm.update(
        device_state=_make_device_state(flame_active=True, flow_temperature=50.0),
        requested_setpoint=setpoint,
        timestamp=ignition,
    )

    pwm.update(
        device_state=_make_device_state(flame_active=True, flow_temperature=50.0),
        requested_setpoint=setpoint,
        timestamp=ignition + on_time - 10.0,
    )

    assert pwm.status is PWMStatus.ON

    pwm.update(
        device_state=_make_device_state(flame_active=True, flow_temperature=50.0),
        requested_setpoint=setpoint,
        timestamp=ignition + on_time + 10.0,
    )

    assert pwm.status is PWMStatus.OFF


def test_pwm_on_phase_gives_up_waiting_after_ignition_timeout(pwm: PWM):
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

    pwm.update(
        device_state=_make_device_state(flame_active=False, flow_temperature=50.0),
        requested_setpoint=setpoint,
        timestamp=PWM_IGNITION_TIMEOUT_SECONDS + 1.0,
    )

    assert pwm.status is PWMStatus.OFF


def test_restore_resumes_in_off_phase_after_interrupted_burn(pwm: PWM):
    now = wall_clock()
    switched_at = now - 120.0
    pwm.restore(State("climate.home", "heat", attributes={
        "pulse_width_modulation_enabled": True,
        "pulse_width_modulation_state": PWMStatus.ON.name,
        "pulse_width_modulation_switched_at": switched_at,
    }))

    assert pwm.status is PWMStatus.OFF
    assert pwm.cycles_this_hour == 1
    assert pwm.state.last_switched_at == switched_at

    pwm._effective_on_temperature = 50.0
    base_offset = pwm._heating_system.base_offset
    setpoint = base_offset + 0.5 * (50.0 - base_offset)
    device_state = _make_device_state(flame_active=False, flow_temperature=45.0)

    pwm.update(device_state=device_state, requested_setpoint=setpoint, timestamp=now)
    assert pwm.status is PWMStatus.OFF

    pwm.update(device_state=device_state, requested_setpoint=setpoint, timestamp=now + 500.0)
    assert pwm.status is PWMStatus.ON


def test_restore_resumes_off_phase_from_its_own_switch(pwm: PWM):
    now = wall_clock()
    pwm.restore(State("climate.home", "heat", attributes={
        "pulse_width_modulation_enabled": True,
        "pulse_width_modulation_state": PWMStatus.OFF.name,
        "pulse_width_modulation_switched_at": now - 60.0,
    }))

    assert pwm.status is PWMStatus.OFF
    assert pwm.cycles_this_hour == 0
    assert pwm._last_update == now - 60.0


@pytest.mark.parametrize(
    "attributes",
    [
        {"pulse_width_modulation_enabled": True},
        {"pulse_width_modulation_enabled": True, "pulse_width_modulation_state": PWMStatus.IDLE.name, "pulse_width_modulation_switched_at": 1.0},
        {"pulse_width_modulation_enabled": True, "pulse_width_modulation_state": PWMStatus.ON.name},
        {"pulse_width_modulation_enabled": True, "pulse_width_modulation_state": PWMStatus.ON.name, "pulse_width_modulation_switched_at": "later"},
        {"pulse_width_modulation_enabled": True, "pulse_width_modulation_state": PWMStatus.ON.name, "pulse_width_modulation_switched_at": wall_clock() + 3600.0},
    ],
)
def test_restore_without_usable_phase_stays_idle(pwm: PWM, attributes):
    pwm.restore(State("climate.home", "heat", attributes=attributes))

    assert pwm.enabled is True
    assert pwm.status is PWMStatus.IDLE
    assert pwm.state.last_switched_at is None


def test_suspend_keeps_off_phase_timing_and_cycle_count(pwm: PWM):
    pwm.enable()
    pwm._status = PWMStatus.OFF
    pwm._last_update = 100.0
    pwm._cycle_starts = [100.0]

    pwm.suspend(250.0)

    assert pwm.status is PWMStatus.OFF
    assert pwm._last_update == 100.0
    assert pwm.cycles_this_hour == 1


def test_suspend_ends_an_interrupted_burn_at_the_suspend_time(pwm: PWM):
    pwm.enable()
    pwm._status = PWMStatus.ON
    pwm._last_update = 100.0
    pwm._flame_on_at = 110.0
    pwm._waiting_for_flame_on = True
    pwm._cycle_starts = [100.0]

    pwm.suspend(250.0)

    assert pwm.status is PWMStatus.OFF
    assert pwm._last_update == 250.0
    assert pwm._flame_on_at is None
    assert pwm._waiting_for_flame_on is False
    assert pwm.cycles_this_hour == 1


def test_suspend_leaves_idle_untouched(pwm: PWM):
    pwm.enable()
    pwm.suspend(250.0)

    assert pwm.status is PWMStatus.IDLE
    assert pwm.state.last_switched_at is None


def test_restore_keeps_learned_boiler_temperature_for_duty_cycle(pwm: PWM):
    pwm.restore(State("climate.home", "heat", attributes={
        "pulse_width_modulation_enabled": True,
        "pulse_width_modulation_effective_on_temperature": 54.0,
    }))

    assert pwm.state.effective_on_temperature == 54.0

    base_offset = pwm._heating_system.base_offset
    setpoint = base_offset + 0.5 * (54.0 - base_offset)
    on_time, off_time = pwm._calculate_duty_cycle(setpoint, _make_device_state(flame_active=False, flow_temperature=38.0))

    assert abs(on_time - off_time) <= 1


@pytest.mark.parametrize("value", [None, "warm", 0.0, -5.0])
def test_restore_ignores_unusable_boiler_temperature(pwm: PWM, value):
    pwm.restore(State("climate.home", "heat", attributes={
        "pulse_width_modulation_enabled": True,
        "pulse_width_modulation_effective_on_temperature": value,
    }))

    assert pwm.state.effective_on_temperature is None


def test_flame_dropout_reopens_warm_up_window_on_reignition(pwm: PWM):
    pwm.enable()
    pwm._status = PWMStatus.ON
    pwm._last_update = 0.0
    pwm._flame_on_at = 0.0
    pwm._effective_on_temperature = 40.0
    setpoint = pwm._heating_system.base_offset + 10.0

    pwm.update(device_state=_make_device_state(flame_active=False, flow_temperature=38.0), requested_setpoint=setpoint, timestamp=30.0)
    assert pwm._flame_on_at is None
    assert pwm.status is PWMStatus.ON
    assert pwm.state.effective_on_temperature == 40.0

    pwm.update(device_state=_make_device_state(flame_active=True, flow_temperature=50.0), requested_setpoint=setpoint, timestamp=400.0)
    assert pwm._flame_on_at == 400.0
    assert pwm.state.effective_on_temperature == 40.0
    assert pwm.state.floor_observed is False

    pwm.update(device_state=_make_device_state(flame_active=True, flow_temperature=50.0), requested_setpoint=setpoint, timestamp=400.0 + HEATER_STARTUP_TIMEFRAME)
    assert pwm.state.effective_on_temperature == 50.0


def test_maximum_cycle_covers_the_longest_duty_cycle(pwm: PWM):
    on_time, off_time = pwm._calculate_duty_cycle(pwm._heating_system.base_offset, _make_device_state(flame_active=True))

    assert pwm.maximum_cycle_seconds == on_time + off_time


def test_disable_keeps_learned_floor(pwm: PWM):
    pwm._enabled = True
    pwm._effective_on_temperature = 44.0
    pwm._floor_observed_at = 10.0

    pwm.disable()

    assert pwm.state.effective_on_temperature == 44.0
    assert pwm.state.floor_known is True
    assert pwm.state.floor_observed is False


def test_first_burn_learns_floor_after_startup_timeframe(pwm: PWM):
    pwm.enable()
    setpoint = pwm._heating_system.base_offset + 5.0
    pwm._last_update = 0.0

    pwm.update(device_state=_make_device_state(flame_active=True, flow_temperature=27.0), requested_setpoint=setpoint, timestamp=0.0)
    assert pwm.status is PWMStatus.ON
    assert pwm.state.floor_known is False

    pwm.update(device_state=_make_device_state(flame_active=True, flow_temperature=35.0), requested_setpoint=setpoint, timestamp=60.0)
    assert pwm.state.floor_known is False

    pwm.update(device_state=_make_device_state(flame_active=True, flow_temperature=38.0), requested_setpoint=setpoint, timestamp=float(HEATER_STARTUP_TIMEFRAME))
    assert pwm.status is PWMStatus.ON
    assert pwm.state.effective_on_temperature == 38.0
    assert pwm.state.floor_observed is True


def test_floor_follows_a_changed_heat_sink(pwm: PWM):
    pwm.enable()
    setpoint = pwm._heating_system.base_offset + 5.0
    pwm._effective_on_temperature = 44.0
    pwm._last_update = 0.0

    pwm.update(device_state=_make_device_state(flame_active=True, flow_temperature=27.0), requested_setpoint=setpoint, timestamp=0.0)
    assert pwm.status is PWMStatus.ON
    assert pwm.state.effective_on_temperature == 44.0

    pwm.update(device_state=_make_device_state(flame_active=True, flow_temperature=38.0), requested_setpoint=setpoint, timestamp=float(HEATER_STARTUP_TIMEFRAME))
    assert pwm.state.effective_on_temperature == 38.0


def test_floor_observation_resets_when_the_on_phase_ends(pwm: PWM):
    pwm.enable()
    base_offset = pwm._heating_system.base_offset
    setpoint = base_offset + 0.5 * (50.0 - base_offset)
    device_state = _make_device_state(flame_active=True, flow_temperature=50.0)
    pwm._last_update = 0.0

    pwm.update(device_state=device_state, requested_setpoint=setpoint, timestamp=0.0)
    pwm.update(device_state=device_state, requested_setpoint=setpoint, timestamp=float(HEATER_STARTUP_TIMEFRAME))
    assert pwm.state.floor_observed is True

    pwm.update(device_state=device_state, requested_setpoint=setpoint, timestamp=450.0)
    assert pwm.status is PWMStatus.OFF
    assert pwm.state.floor_observed is False
    assert pwm.state.effective_on_temperature == 50.0


def test_floor_is_not_learned_from_a_hot_water_burn(pwm: PWM):
    pwm.enable()
    pwm._status = PWMStatus.ON
    pwm._last_update = 100.0
    pwm._flame_on_at = 100.0
    pwm._effective_on_temperature = 40.0

    established = 100.0 + HEATER_STARTUP_TIMEFRAME
    setpoint = pwm._heating_system.base_offset + 15.0

    pwm.update(device_state=_make_device_state(flame_active=True, hot_water_active=True, flow_temperature=70.0), requested_setpoint=setpoint, timestamp=established)
    assert pwm.state.effective_on_temperature == 40.0

    pwm.update(device_state=_make_device_state(flame_active=True, flow_temperature=65.0), requested_setpoint=setpoint, timestamp=established + 5.0)
    assert pwm.state.effective_on_temperature == 40.0


def test_hourly_cap_survives_disable_and_enable(pwm: PWM):
    pwm.enable()
    pwm._cycle_starts = [800.0, 820.0, 840.0, 860.0]

    pwm.disable()
    pwm.enable()
    pwm.update(device_state=_make_device_state(flame_active=True), requested_setpoint=pwm._heating_system.base_offset + 15.0, timestamp=900.0)

    assert pwm.cycles_this_hour == 4
    assert pwm.status is PWMStatus.OFF
