"""Configured heating systems must reach controllers and the options form."""
import pytest
from homeassistant.const import UnitOfTemperature
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sat.climate import SatClimate
from custom_components.sat.config_flow import SatOptionsFlowHandler
from custom_components.sat.const import (
    DOMAIN, CONF_HEATING_SYSTEM, CONF_AUTOMATIC_GAINS,
    CONF_HEATING_CURVE_COEFFICIENT, CONF_ROOMS, CONF_CYCLES_PER_HOUR,
    HEATING_SYSTEM_UNDERFLOOR, HEATING_SYSTEM_RADIATORS, HEATING_SYSTEM_HEAT_PUMP,
)
from custom_components.sat.errors import Error
from custom_components.sat.fake import SatFakeCoordinator
from tests.const import DEFAULT_USER_DATA


@pytest.mark.parametrize("heating_system, expected_kp", [
    (HEATING_SYSTEM_UNDERFLOOR, 15.0),
    (HEATING_SYSTEM_RADIATORS, 20.0),
    (HEATING_SYSTEM_HEAT_PUMP, 20.0),
])
@pytest.mark.parametrize("legacy_option", [None, HEATING_SYSTEM_RADIATORS])
async def test_climate_and_area_pid_use_setup_system(hass, heating_system, expected_kp, legacy_option):
    data = {**DEFAULT_USER_DATA, CONF_HEATING_SYSTEM: heating_system, CONF_ROOMS: ["climate.room"]}
    options = {CONF_AUTOMATIC_GAINS: True, CONF_HEATING_CURVE_COEFFICIENT: 2.0}
    if legacy_option is not None:
        options[CONF_HEATING_SYSTEM] = legacy_option
    entry = MockConfigEntry(domain=DOMAIN, data=data, options=options)
    coordinator = SatFakeCoordinator(hass, entry.data, entry.options)
    climate = SatClimate(coordinator, entry, UnitOfTemperature.CELSIUS)

    for pid in [climate.pid, climate.areas.pids.areas[0].pid]:
        pid.update_reset(Error("climate.room", 0), 30)
        assert pid.kp == expected_kp


@pytest.mark.parametrize("heating_system, expected_cycles", [
    (HEATING_SYSTEM_UNDERFLOOR, None),
    (HEATING_SYSTEM_RADIATORS, ["3", "4"]),
    (HEATING_SYSTEM_HEAT_PUMP, ["2", "3"]),
])
@pytest.mark.parametrize("legacy_option", [None, HEATING_SYSTEM_RADIATORS])
async def test_options_cycles_follow_setup_system(hass, heating_system, expected_cycles, legacy_option):
    options = {} if legacy_option is None else {CONF_HEATING_SYSTEM: legacy_option}
    entry = MockConfigEntry(domain=DOMAIN, data={**DEFAULT_USER_DATA, CONF_HEATING_SYSTEM: heating_system}, options=options)
    flow = SatOptionsFlowHandler(entry)
    flow.hass = hass
    result = await flow.async_step_system_configuration()
    cycle_selector = next((value for key, value in result["data_schema"].schema.items() if key.schema == CONF_CYCLES_PER_HOUR), None)

    if expected_cycles is None:
        assert cycle_selector is None
    else:
        assert [item["value"] for item in cycle_selector.config["options"]] == expected_cycles


@pytest.mark.parametrize("saved_cycles, expected_default", [
    (None, "2"), ("2", "2"), (2, "2"), ("3", "3"), (3, "3"),
    ("4", "2"), (4, "2"), (0, "2"),
])
async def test_heat_pump_cycle_default_is_accepted(hass, saved_cycles, expected_default):
    options = {} if saved_cycles is None else {CONF_CYCLES_PER_HOUR: saved_cycles}
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**DEFAULT_USER_DATA, CONF_HEATING_SYSTEM: HEATING_SYSTEM_HEAT_PUMP},
        options=options,
    )
    flow = SatOptionsFlowHandler(entry)
    flow.hass = hass
    result = await flow.async_step_system_configuration()
    cycle_key, cycle_selector = next(
        (key, value) for key, value in result["data_schema"].schema.items()
        if key.schema == CONF_CYCLES_PER_HOUR
    )
    default = cycle_key.default()
    assert default == expected_default
    assert cycle_selector(default) == default
