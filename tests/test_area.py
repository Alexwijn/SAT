"""Tests for the Area class."""

import pytest
from unittest.mock import MagicMock
from homeassistant.components.climate import HVACMode
from homeassistant.const import STATE_UNKNOWN, STATE_UNAVAILABLE
from homeassistant.core import State

from custom_components.sat.area import Area
from custom_components.sat.const import *


def create_config_data(entity_id="climate.room1"):
    return {CONF_ROOMS: [entity_id]}


def create_config_options():
    options = OPTIONS_DEFAULTS.copy()
    return options


def mock_hass_with_climate_state(entity_id, hvac_state, target_temp=21.0, current_temp=20.0):
    """Create a mock hass with a climate entity in the given state."""
    hass = MagicMock()
    state = State(entity_id, hvac_state, {
        "temperature": target_temp,
        "current_temperature": current_temp,
    })
    hass.states.get.return_value = state
    return hass


class TestAreaState:
    def test_state_returns_none_when_off(self):
        area = Area(create_config_data(), create_config_options(), "climate.room1")
        area._hass = mock_hass_with_climate_state("climate.room1", HVACMode.OFF)

        assert area.state is None

    def test_state_returns_none_when_unknown(self):
        area = Area(create_config_data(), create_config_options(), "climate.room1")
        area._hass = mock_hass_with_climate_state("climate.room1", STATE_UNKNOWN)

        assert area.state is None

    def test_state_returns_none_when_unavailable(self):
        area = Area(create_config_data(), create_config_options(), "climate.room1")
        area._hass = mock_hass_with_climate_state("climate.room1", STATE_UNAVAILABLE)

        assert area.state is None

    def test_state_returns_state_when_heating(self):
        area = Area(create_config_data(), create_config_options(), "climate.room1")
        area._hass = mock_hass_with_climate_state("climate.room1", HVACMode.HEAT)

        assert area.state is not None
        assert area.state.state == HVACMode.HEAT


class TestAreaError:
    def test_error_is_none_when_off(self):
        area = Area(create_config_data(), create_config_options(), "climate.room1")
        area._hass = mock_hass_with_climate_state("climate.room1", HVACMode.OFF, target_temp=21.0, current_temp=19.0)

        assert area.error is None

    def test_error_calculated_when_heating(self):
        area = Area(create_config_data(), create_config_options(), "climate.room1")
        area._hass = mock_hass_with_climate_state("climate.room1", HVACMode.HEAT, target_temp=21.0, current_temp=19.0)

        assert area.error is not None
        assert area.error.value == 2.0

    def test_error_is_none_when_unavailable(self):
        area = Area(create_config_data(), create_config_options(), "climate.room1")
        area._hass = mock_hass_with_climate_state("climate.room1", STATE_UNAVAILABLE, target_temp=21.0, current_temp=19.0)

        assert area.error is None


class TestAreaWeight:
    def test_weight_is_none_when_off(self):
        area = Area(create_config_data(), create_config_options(), "climate.room1")
        area._hass = mock_hass_with_climate_state("climate.room1", HVACMode.OFF, target_temp=21.0, current_temp=19.0)

        assert area.weight is None

    def test_weight_calculated_when_heating(self):
        area = Area(create_config_data(), create_config_options(), "climate.room1")
        area._hass = mock_hass_with_climate_state("climate.room1", HVACMode.HEAT, target_temp=21.0, current_temp=19.0)

        assert area.weight is not None
        assert area.weight > 0


class TestAreaTemperatures:
    def test_target_temperature_none_when_off(self):
        area = Area(create_config_data(), create_config_options(), "climate.room1")
        area._hass = mock_hass_with_climate_state("climate.room1", HVACMode.OFF, target_temp=21.0)

        assert area.target_temperature is None

    def test_current_temperature_none_when_off(self):
        area = Area(create_config_data(), create_config_options(), "climate.room1")
        area._hass = mock_hass_with_climate_state("climate.room1", HVACMode.OFF, current_temp=19.0)

        assert area.current_temperature is None

    def test_target_temperature_available_when_heating(self):
        area = Area(create_config_data(), create_config_options(), "climate.room1")
        area._hass = mock_hass_with_climate_state("climate.room1", HVACMode.HEAT, target_temp=21.0)

        assert area.target_temperature == 21.0

    def test_current_temperature_available_when_heating(self):
        area = Area(create_config_data(), create_config_options(), "climate.room1")
        area._hass = mock_hass_with_climate_state("climate.room1", HVACMode.HEAT, current_temp=19.0)

        assert area.current_temperature == 19.0
