"""Tests for boiler behavior."""

from custom_components.sat.device import DeviceState, DeviceTracker
from custom_components.sat.device.const import BOILER_MODULATION_DELTA_THRESHOLD, BOILER_MODULATION_RELIABILITY_MIN_SAMPLES


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
