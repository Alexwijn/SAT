"""Unit tests for the Switch coordinator."""

import pytest
from homeassistant.const import STATE_ON, STATE_OFF
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.sat.const import CONF_DEVICE, CONF_MODE, CONF_SIMULATION, OPTIONS_DEFAULTS
from custom_components.sat.coordinator.switch import SatSwitchCoordinator
from custom_components.sat.entry_data import SatConfig, SatMode
from custom_components.sat.types import HeaterState
from tests.const import DEFAULT_USER_DATA


@pytest.fixture
def coordinator(hass):
    """Create a SatSwitchCoordinator with a registered switch entity."""
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get_or_create(
        domain="switch",
        platform="test",
        unique_id="boiler_heater_unique",
        suggested_object_id="boiler_heater",
    )

    data = {**DEFAULT_USER_DATA, CONF_MODE: SatMode.SWITCH, CONF_DEVICE: entry.id}
    config = SatConfig(entry_id="switch-test", data=data, options={**OPTIONS_DEFAULTS})

    return SatSwitchCoordinator(hass, config)


# ── Identity ──────────────────────────────────────────────────────────────────


def test_type(coordinator):
    assert coordinator.type == "Switch"


def test_member_id(coordinator):
    assert coordinator.member_id == -1


# ── State reading ─────────────────────────────────────────────────────────────


def test_active_on(coordinator, hass):
    hass.states.async_set(coordinator._entity.entity_id, STATE_ON)
    assert coordinator.active is True


def test_active_off(coordinator, hass):
    hass.states.async_set(coordinator._entity.entity_id, STATE_OFF)
    assert coordinator.active is False


def test_active_no_state(coordinator):
    assert coordinator.active is False


def test_setpoint_returns_minimum(coordinator):
    assert coordinator.setpoint == coordinator.minimum_setpoint


def test_maximum_setpoint_returns_minimum(coordinator):
    assert coordinator.maximum_setpoint == coordinator.minimum_setpoint


# ── Capabilities ──────────────────────────────────────────────────────────────


def test_no_setpoint_management(coordinator):
    assert coordinator.supports_setpoint_management is False
    assert coordinator.supports_hot_water_setpoint_management is False
    assert coordinator.supports_maximum_setpoint_management is False


# ── Commands ──────────────────────────────────────────────────────────────────


@pytest.fixture
def sim_coordinator(hass):
    """Create a SatSwitchCoordinator with simulation enabled so service calls are skipped."""
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get_or_create(
        domain="switch",
        platform="test",
        unique_id="boiler_heater_sim",
        suggested_object_id="boiler_heater_sim",
    )

    data = {**DEFAULT_USER_DATA, CONF_MODE: SatMode.SWITCH, CONF_DEVICE: entry.id}
    options = {**OPTIONS_DEFAULTS, CONF_SIMULATION: True}
    config = SatConfig(entry_id="switch-sim", data=data, options=options)

    return SatSwitchCoordinator(hass, config)


@pytest.mark.asyncio
async def test_set_heater_state_on(sim_coordinator):
    """With simulation enabled, the service call is skipped but super() is still invoked."""
    await sim_coordinator.async_set_heater_state(HeaterState.ON)


@pytest.mark.asyncio
async def test_set_heater_state_off(sim_coordinator):
    await sim_coordinator.async_set_heater_state(HeaterState.OFF)


def _coordinator_for(hass, domain: str) -> SatSwitchCoordinator:
    entry = er.async_get(hass).async_get_or_create(domain=domain, platform="test", unique_id=f"heater_{domain}", suggested_object_id="heater")

    data = {**DEFAULT_USER_DATA, CONF_MODE: SatMode.SWITCH, CONF_DEVICE: entry.id}
    config = SatConfig(entry_id=f"{domain}-test", data=data, options={**OPTIONS_DEFAULTS})

    return SatSwitchCoordinator(hass, config)


@pytest.mark.parametrize(("domain", "turn_on_service", "turn_off_service"), [
    ("switch", "turn_on", "turn_off"),
    ("input_boolean", "turn_on", "turn_off"),
    ("valve", "open_valve", "close_valve"),
])
async def test_set_heater_state_calls_the_services_of_the_domain(hass, domain, turn_on_service, turn_off_service):
    coordinator = _coordinator_for(hass, domain)

    turn_on_calls = async_mock_service(hass, domain, turn_on_service)
    turn_off_calls = async_mock_service(hass, domain, turn_off_service)

    await coordinator.async_set_heater_state(HeaterState.ON)

    assert len(turn_off_calls) == 0
    assert len(turn_on_calls) == 1
    assert turn_on_calls[0].data["entity_id"] == f"{domain}.heater"

    await coordinator.async_set_heater_state(HeaterState.OFF)

    assert len(turn_on_calls) == 1
    assert len(turn_off_calls) == 1
    assert turn_off_calls[0].data["entity_id"] == f"{domain}.heater"


@pytest.mark.parametrize(("state", "active"), [
    ("open", True),
    ("opening", False),
    ("closing", False),
    ("closed", False),
])
def test_valve_is_active_while_open(hass, state, active):
    coordinator = _coordinator_for(hass, "valve")
    hass.states.async_set("valve.heater", state)

    assert coordinator.active is active


@pytest.mark.parametrize(("domain", "service"), [("switch", "turn_off"), ("input_boolean", "turn_off"), ("valve", "close_valve")])
async def test_release_calls_the_off_service(hass, domain, service):
    coordinator = _coordinator_for(hass, domain)
    calls = async_mock_service(hass, domain, service)

    await coordinator.async_release()

    assert len(calls) == 1
