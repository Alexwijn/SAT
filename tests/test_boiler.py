"""Tests for boiler behavior."""

from custom_components.sat.boiler import Boiler, BoilerState


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
        relative_modulation_level=modulation,
    )


def test_modulation_reliability_recovers():
    boiler = Boiler(modulation_reliability_min_samples=3)

    for _ in range(3):
        boiler.update(_boiler_state(0.0), None)

    assert boiler.modulation_reliable is False

    for value in (0.5, 0.7, 0.4):
        boiler.update(_boiler_state(value), None)

    assert boiler.modulation_reliable is False

    for value in (37.0, 0.0, 0.0):
        boiler.update(_boiler_state(value), None)

    assert boiler.modulation_reliable is False

    for value in (10.0, 15.0, 12.0):
        boiler.update(_boiler_state(value), None)

    assert boiler.modulation_reliable is True
