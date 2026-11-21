"""Tests for boiler behavior."""

from custom_components.sat.device import DeviceState, DeviceTracker
from custom_components.sat.device.const import BOILER_MODULATION_DELTA_THRESHOLD, BOILER_MODULATION_RELIABILITY_MIN_SAMPLES
from custom_components.sat.types import BoilerStatus


def _boiler_state(modulation: float) -> DeviceState:
    return DeviceState(
        flame_active=True,
        central_heating=True,
        hot_water_active=False,
        setpoint=50.0,
        flow_temperature=40.0,
        return_temperature=35.0,
        max_modulation_level=100,
        relative_modulation_level=modulation,
    )


def test_modulation_reliability_recovers():
    boiler = DeviceTracker()
    min_samples = BOILER_MODULATION_RELIABILITY_MIN_SAMPLES
    high = BOILER_MODULATION_DELTA_THRESHOLD + 1.0

    timestamp = 0.0
    for _ in range(min_samples):
        boiler.update(_boiler_state(0.0), None, timestamp)
        timestamp += 1.0

    assert boiler.modulation_reliable is False

    for _ in range(min_samples):
        boiler.update(_boiler_state(0.5), None, timestamp)
        timestamp += 1.0

    assert boiler.modulation_reliable is False

    for value in (high, 0.0, 0.0, 0.0, 0.0, 0.0, high, 0.0):
        boiler.update(_boiler_state(value), None, timestamp)
        timestamp += 1.0

    assert boiler.modulation_reliable is False

    for value in (high, 0.0, high, 0.0, high, 0.0, 0.0, 0.0):
        boiler.update(_boiler_state(value), None, timestamp)
        timestamp += 1.0

    assert boiler.modulation_reliable is True


def _heating_state(*, flow: float, setpoint: float, flame_active: bool) -> DeviceState:
    return DeviceState(
        flame_active=flame_active,
        central_heating=True,
        hot_water_active=False,
        setpoint=setpoint,
        flow_temperature=flow,
        return_temperature=flow - 5.0,
        max_modulation_level=100,
        relative_modulation_level=0.0,
    )


def test_stall_is_counted_from_demand_start_after_pwm_off_phase():
    tracker = DeviceTracker()
    tracker.update(_heating_state(flow=50.0, setpoint=52.0, flame_active=True), None, 0.0)
    tracker.update(_heating_state(flow=50.0, setpoint=10.0, flame_active=False), None, 60.0)
    tracker.update(_heating_state(flow=30.0, setpoint=10.0, flame_active=False), None, 1500.0)

    tracker.update(_heating_state(flow=30.0, setpoint=52.0, flame_active=False), None, 1560.0)
    assert tracker.status is BoilerStatus.WAITING_FOR_FLAME

    tracker.update(_heating_state(flow=30.0, setpoint=52.0, flame_active=False), None, 1800.0)
    assert tracker.status is BoilerStatus.WAITING_FOR_FLAME

    tracker.update(_heating_state(flow=30.0, setpoint=52.0, flame_active=False), None, 2200.0)
    assert tracker.status is BoilerStatus.STALLED_IGNITION
