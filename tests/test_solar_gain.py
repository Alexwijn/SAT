"""Unit tests for solar gain detection logic."""

from custom_components.sat.const import (
    CONF_SOLAR_GAIN_COMPENSATION,
    CONF_SOLAR_GAIN_FREEZE_INTEGRAL,
    CONF_SOLAR_GAIN_MIN_ELEVATION,
    CONF_SOLAR_GAIN_MIN_RISE_PER_HOUR,
    CONF_SOLAR_GAIN_SETPOINT_OFFSET_CELSIUS,
    OPTIONS_DEFAULTS,
)
from custom_components.sat.entry_data import SolarGainConfig
from custom_components.sat.solar_gain import SolarGainController, SolarGainSample, SolarGainSignals


def _config(**overrides) -> SolarGainConfig:
    values = {
        "enabled": OPTIONS_DEFAULTS[CONF_SOLAR_GAIN_COMPENSATION],
        "freeze_integral": OPTIONS_DEFAULTS[CONF_SOLAR_GAIN_FREEZE_INTEGRAL],
        "minimum_elevation": OPTIONS_DEFAULTS[CONF_SOLAR_GAIN_MIN_ELEVATION],
        "minimum_rise_per_hour": OPTIONS_DEFAULTS[CONF_SOLAR_GAIN_MIN_RISE_PER_HOUR],
        "setpoint_offset_celsius": OPTIONS_DEFAULTS[CONF_SOLAR_GAIN_SETPOINT_OFFSET_CELSIUS],
    }
    values.update(overrides)
    return SolarGainConfig(**values)


def test_detects_solar_gain_when_signals_match():
    controller = SolarGainController(_config(enabled=True, minimum_elevation=10.0, minimum_rise_per_hour=0.5))

    controller.update(SolarGainSignals(
        sample=SolarGainSample(temperature=20.0, timestamp=0.0),
        valves_open=True,
        sun_elevation=20.0,
        flame_active=True,
        is_heating_mode=True,
        relative_modulation=10.0,
    ))

    snapshot = controller.update(SolarGainSignals(
        sample=SolarGainSample(temperature=20.2, timestamp=600.0),
        valves_open=True,
        sun_elevation=20.0,
        flame_active=True,
        is_heating_mode=True,
        relative_modulation=10.0,
    ))

    assert snapshot.active is True
    assert snapshot.rise_per_hour is not None
    assert snapshot.rise_per_hour > 0.5


def test_does_not_detect_when_sun_is_below_threshold():
    controller = SolarGainController(_config(enabled=True, minimum_elevation=15.0, minimum_rise_per_hour=0.5))

    controller.update(SolarGainSignals(
        sample=SolarGainSample(temperature=20.0, timestamp=0.0),
        valves_open=True,
        sun_elevation=5.0,
        flame_active=True,
        is_heating_mode=True,
        relative_modulation=5.0,
    ))

    snapshot = controller.update(SolarGainSignals(
        sample=SolarGainSample(temperature=20.2, timestamp=600.0),
        valves_open=True,
        sun_elevation=5.0,
        flame_active=True,
        is_heating_mode=True,
        relative_modulation=5.0,
    ))

    assert snapshot.active is False


def test_does_not_detect_when_modulation_unknown_and_flame_active():
    controller = SolarGainController(_config(enabled=True, minimum_elevation=10.0, minimum_rise_per_hour=0.5))

    controller.update(SolarGainSignals(
        sample=SolarGainSample(temperature=20.0, timestamp=0.0),
        valves_open=True,
        sun_elevation=20.0,
        flame_active=True,
        is_heating_mode=True,
        relative_modulation=None,
    ))

    snapshot = controller.update(SolarGainSignals(
        sample=SolarGainSample(temperature=20.2, timestamp=600.0),
        valves_open=True,
        sun_elevation=20.0,
        flame_active=True,
        is_heating_mode=True,
        relative_modulation=None,
    ))

    assert snapshot.active is False
