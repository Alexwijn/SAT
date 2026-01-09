"""Tests for boiler behavior."""

from custom_components.sat.boiler import Boiler, BoilerState
from custom_components.sat.boiler.const import BOILER_MODULATION_DELTA_THRESHOLD, BOILER_MODULATION_RELIABILITY_MIN_SAMPLES


def _boiler_state(modulation: float) -> BoilerState:
    return BoilerState(
        flame_active=True,
        central_heating=True,
        hot_water_active=False,
        modulation_reliable=False,
        flame_on_since=None,
        flame_off_since=None,
        setpoint=50.0,
        flow_temperature=40.0,
        return_temperature=35.0,
        max_modulation_level=100,
        relative_modulation_level=modulation,
    )


def test_modulation_reliability_recovers():
    boiler = Boiler()
    min_samples = BOILER_MODULATION_RELIABILITY_MIN_SAMPLES
    high = BOILER_MODULATION_DELTA_THRESHOLD + 1.0

    for _ in range(min_samples):
        boiler.update(_boiler_state(0.0), None)

    assert boiler.modulation_reliable is False

    for _ in range(min_samples):
        boiler.update(_boiler_state(0.5), None)

    assert boiler.modulation_reliable is False

    for value in (high, 0.0, 0.0, 0.0, 0.0, 0.0, high, 0.0):
        boiler.update(_boiler_state(value), None)

    assert boiler.modulation_reliable is False

    for value in (high, 0.0, high, 0.0, high, 0.0, 0.0, 0.0):
        boiler.update(_boiler_state(value), None)

    assert boiler.modulation_reliable is True
