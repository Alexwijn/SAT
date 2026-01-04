import pytest

from custom_components.sat.const import CONF_MAXIMUM_SETPOINT, HeatingSystem
from custom_components.sat.fake import SatFakeCoordinator
from custom_components.sat.overshoot_protection import (
    MINIMUM_WARMUP_RISE,
    STABILITY_MINIMUM_SAMPLES,
    STABILITY_TEMPERATURE_RANGE,
    STABILITY_WINDOW_DURATION_SECONDS,
    OvershootProtection,
)
from tests.const import DEFAULT_USER_DATA, make_config


def _build_coordinator(hass, *, maximum_setpoint_value=None):
    options = {}
    if maximum_setpoint_value is not None:
        options[CONF_MAXIMUM_SETPOINT] = maximum_setpoint_value
    config = make_config(data=DEFAULT_USER_DATA.copy(), options=options)
    return SatFakeCoordinator(hass, config)


def test_invalid_heating_system_raises(hass):
    coordinator = _build_coordinator(hass)
    with pytest.raises(ValueError):
        OvershootProtection(coordinator, "invalid")


def test_setpoint_uses_default_when_maximum_missing(hass):
    coordinator = _build_coordinator(hass)
    protection = OvershootProtection(coordinator, HeatingSystem.RADIATORS)
    assert protection._setpoint == float(coordinator.maximum_setpoint_value)


def test_setpoint_is_clamped_by_maximum(hass):
    coordinator = _build_coordinator(hass, maximum_setpoint_value=50)
    protection = OvershootProtection(coordinator, HeatingSystem.RADIATORS)
    assert protection._setpoint == 50.0


def test_record_sample_prunes_old_values(hass):
    coordinator = _build_coordinator(hass)
    protection = OvershootProtection(coordinator, HeatingSystem.RADIATORS)
    protection._record_sample(0.0, 40.0, None)
    protection._record_sample(STABILITY_WINDOW_DURATION_SECONDS + 1, 41.0, None)
    assert len(protection._samples) == 1
    assert protection._samples[0][1] == 41.0


def test_sample_stats_requires_min_samples(hass):
    coordinator = _build_coordinator(hass)
    protection = OvershootProtection(coordinator, HeatingSystem.RADIATORS)
    for i in range(STABILITY_MINIMUM_SAMPLES - 1):
        protection._record_sample(float(i), 40.0, None)
    assert protection._sample_statistics() is None


def test_sample_stats_computes_values(hass):
    coordinator = _build_coordinator(hass)
    protection = OvershootProtection(coordinator, HeatingSystem.RADIATORS)
    start = 0.0
    for i in range(STABILITY_MINIMUM_SAMPLES):
        protection._record_sample(start + (i * 60.0), 40.6, 25.0)
    stats = protection._sample_statistics()
    assert stats is not None
    assert stats.duration == (STABILITY_MINIMUM_SAMPLES - 1) * 60.0
    assert stats.temperature_range == 0.0
    assert stats.average_temperature == 40.6
    assert stats.average_modulation == 25.0


def test_is_stable_true_for_flat_window(hass):
    coordinator = _build_coordinator(hass)
    protection = OvershootProtection(coordinator, HeatingSystem.RADIATORS)
    start = 0.0
    for i in range(STABILITY_MINIMUM_SAMPLES):
        protection._record_sample(start + (i * 60.0), 40.0 + MINIMUM_WARMUP_RISE + 0.1, 20.0)
    stats = protection._sample_statistics()
    assert stats is not None
    assert protection._is_stable(stats, starting_temperature=40.0)


def test_is_stable_false_for_large_range(hass):
    coordinator = _build_coordinator(hass)
    protection = OvershootProtection(coordinator, HeatingSystem.RADIATORS)
    start = 0.0
    temperatures = [40.0, 40.0 + STABILITY_TEMPERATURE_RANGE + 0.2] * (STABILITY_MINIMUM_SAMPLES // 2)
    for i, temperature in enumerate(temperatures):
        protection._record_sample(start + (i * 60.0), temperature, None)
    stats = protection._sample_statistics()
    assert stats is not None
    assert not protection._is_stable(stats, starting_temperature=40.0)


def test_calculate_overshoot_value_no_modulation(hass):
    coordinator = _build_coordinator(hass)
    protection = OvershootProtection(coordinator, HeatingSystem.RADIATORS)
    protection._stable_temperature = 42.0
    coordinator._relative_modulation_value = None
    assert protection._calculate_overshoot_value() == 42.0


def test_calculate_overshoot_value_with_stable_modulation(hass):
    coordinator = _build_coordinator(hass)
    protection = OvershootProtection(coordinator, HeatingSystem.RADIATORS)
    protection._stable_temperature = 42.0
    protection._stable_modulation = 40.0
    protection._setpoint = 60.0
    assert protection._calculate_overshoot_value() == 36.0


@pytest.mark.asyncio
async def test_get_setpoint_falls_back_without_boiler_temperature(hass):
    coordinator = _build_coordinator(hass)
    coordinator._boiler_temperature = None
    coordinator._relative_modulation_value = None
    protection = OvershootProtection(coordinator, HeatingSystem.RADIATORS)
    assert await protection._get_setpoint(is_ready=True) == protection._setpoint
