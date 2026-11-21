"""Tests for heating control of heaters that can only be switched on and off."""

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.sat.const import CONF_DEVICE, CONF_MINIMUM_SETPOINT, CONF_MODE, MINIMUM_SETPOINT, OPTIONS_DEFAULTS
from custom_components.sat.coordinator.switch import SatSwitchCoordinator
from custom_components.sat.entry_data import SatConfig, SatMode
from custom_components.sat.heating_control import HeatingDemand, SatHeatingControl
from custom_components.sat.helpers import timestamp
from custom_components.sat.types import CycleControlMode, PWMStatus
from tests.const import DEFAULT_USER_DATA

STEP_SECONDS = 30
HEATER_TEMPERATURE = 50.0


class SwitchHeater:
    """A switch entity that follows the turn on and turn off calls of the coordinator."""

    def __init__(self, hass) -> None:
        self._hass = hass
        self._turn_on_calls = async_mock_service(hass, "switch", "turn_on")
        self._registry_entry = er.async_get(hass).async_get_or_create(domain="switch", platform="test", unique_id="heater", suggested_object_id="heater")

        self._seen_turn_on_calls = 0
        self.starts = 0
        self.steps_on = 0

        async_mock_service(hass, "switch", "turn_off")
        hass.states.async_set(self.entity_id, STATE_OFF)

    @property
    def entity_id(self) -> str:
        return self._registry_entry.entity_id

    @property
    def registry_id(self) -> str:
        return self._registry_entry.id

    @property
    def active(self) -> bool:
        return self._hass.states.get(self.entity_id).state == STATE_ON

    def follow(self) -> None:
        turned_on = len(self._turn_on_calls) > self._seen_turn_on_calls
        self._seen_turn_on_calls = len(self._turn_on_calls)

        if turned_on and not self.active:
            self.starts += 1

        if turned_on:
            self.steps_on += 1

        self._hass.states.async_set(self.entity_id, STATE_ON if turned_on else STATE_OFF)


@pytest.fixture
def heater(hass) -> SwitchHeater:
    return SwitchHeater(hass)


@pytest.fixture
def heating_control(hass, heater) -> SatHeatingControl:
    data = {**DEFAULT_USER_DATA, CONF_MODE: SatMode.SWITCH, CONF_DEVICE: heater.registry_id, CONF_MINIMUM_SETPOINT: HEATER_TEMPERATURE}
    config = SatConfig(entry_id="switch-heating-control", data=data, options={**OPTIONS_DEFAULTS})

    return SatHeatingControl(hass=hass, coordinator=SatSwitchCoordinator(hass, config), config=config)


def _demand(requested_setpoint: float, at: float) -> HeatingDemand:
    return HeatingDemand(timestamp=at, valves_open=True, hvac_mode=HVACMode.HEAT, outside_temperature=10.0, requested_setpoint=requested_setpoint)


async def _run(heating_control: SatHeatingControl, heater: SwitchHeater, requested_setpoint: float, duration_seconds: int) -> None:
    start = timestamp()

    for elapsed in range(0, duration_seconds, STEP_SECONDS):
        await heating_control.update(_demand(requested_setpoint, start + elapsed))
        heater.follow()


async def test_low_demand_cycles_the_heater(heating_control, heater):
    await _run(heating_control, heater, requested_setpoint=32.0, duration_seconds=3600)

    assert heating_control.pwm_state.enabled is True
    assert heating_control.control_mode is CycleControlMode.PWM
    assert 2 <= heater.starts <= OPTIONS_DEFAULTS["cycles_per_hour"]
    assert 0.15 <= heater.steps_on / (3600 / STEP_SECONDS) <= 0.30


async def test_higher_demand_keeps_the_heater_on_for_longer(heating_control, heater):
    await _run(heating_control, heater, requested_setpoint=42.0, duration_seconds=3600)

    assert 0.55 <= heater.steps_on / (3600 / STEP_SECONDS) <= 0.75


async def test_off_phase_switches_the_heater_off(heating_control, heater):
    await _run(heating_control, heater, requested_setpoint=32.0, duration_seconds=600)

    assert heating_control.pwm_state.status is PWMStatus.OFF
    assert heating_control.control_setpoint == MINIMUM_SETPOINT
    assert heater.active is False


async def test_pulse_width_modulation_stays_enabled_at_the_maximum_setpoint(heating_control, heater):
    maximum_setpoint = heating_control._config.limits.maximum_setpoint

    await _run(heating_control, heater, requested_setpoint=maximum_setpoint, duration_seconds=1800)

    assert heating_control.pwm_state.enabled is True
    assert heater.steps_on == 1800 / STEP_SECONDS
