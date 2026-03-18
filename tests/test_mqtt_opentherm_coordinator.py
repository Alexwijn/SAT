"""Unit tests for the OpenTherm MQTT coordinator."""

import json
from unittest.mock import AsyncMock

import pytest

from custom_components.sat.const import CONF_DEVICE, CONF_MODE, CONF_MQTT_TOPIC, OPTIONS_DEFAULTS
from custom_components.sat.coordinator.mqtt.opentherm import (
    DATA_BOILER_CAPACITY,
    DATA_BOILER_TEMPERATURE,
    DATA_CENTRAL_HEATING,
    DATA_CENTRAL_HEATING_WATER_PRESSURE,
    DATA_CONTROL_SETPOINT,
    DATA_DHW_ENABLE,
    DATA_DHW_SETPOINT,
    DATA_DHW_SETPOINT_MAXIMUM,
    DATA_DHW_SETPOINT_MINIMUM,
    DATA_FLAME_ACTIVE,
    DATA_MAXIMUM_CONTROL_SETPOINT,
    DATA_MAX_REL_MOD_LEVEL_SETTING,
    DATA_REL_MIN_MOD_LEVEL,
    DATA_REL_MIN_MOD_LEVEL_LEGACY,
    DATA_REL_MOD_LEVEL,
    DATA_RETURN_TEMPERATURE,
    DATA_SLAVE_MEMBERID,
    SatOpenThermMqttCoordinator,
)
from custom_components.sat.entry_data import SatConfig, SatMode
from custom_components.sat.types import HeaterState
from tests.const import DEFAULT_USER_DATA


def _make_config():
    data = {**DEFAULT_USER_DATA, CONF_MODE: SatMode.MQTT_OPENTHERM, CONF_DEVICE: "otgw-1234", CONF_MQTT_TOPIC: "OTGW"}
    return SatConfig(entry_id="opentherm-test", data=data, options={**OPTIONS_DEFAULTS})


@pytest.fixture
def coordinator(hass, monkeypatch):
    monkeypatch.setattr("custom_components.sat.coordinator.mqtt.Store", lambda *a, **kw: AsyncMock())
    c = SatOpenThermMqttCoordinator(hass, _make_config())
    return c


# ── Identity ──────────────────────────────────────────────────────────────────


def test_type(coordinator):
    assert coordinator.type == "OpenThermGateway (via mqtt)"


def test_id(coordinator):
    assert coordinator.id == "otgw-1234"


# ── State reading from data dict ─────────────────────────────────────────────


def test_active_on(coordinator):
    coordinator.data.update({DATA_CENTRAL_HEATING: "ON"})
    assert coordinator.active is True


def test_active_off(coordinator):
    coordinator.data.update({DATA_CENTRAL_HEATING: "OFF"})
    assert coordinator.active is False


def test_flame_active(coordinator):
    coordinator.data.update({DATA_FLAME_ACTIVE: "ON"})
    assert coordinator.flame_active is True


def test_hot_water_active(coordinator):
    coordinator.data.update({DATA_DHW_ENABLE: "ON"})
    assert coordinator.hot_water_active is True


def test_setpoint(coordinator):
    coordinator.data.update({DATA_CONTROL_SETPOINT: "55.0"})
    assert coordinator.setpoint == 55.0


def test_setpoint_none(coordinator):
    assert coordinator.setpoint is None


def test_hot_water_setpoint(coordinator):
    coordinator.data.update({DATA_DHW_SETPOINT: "48.5"})
    assert coordinator.hot_water_setpoint == 48.5


def test_maximum_setpoint_value(coordinator):
    coordinator.data.update({DATA_MAXIMUM_CONTROL_SETPOINT: "75"})
    assert coordinator.maximum_setpoint_value == 75.0


def test_boiler_temperature(coordinator):
    coordinator.data.update({DATA_BOILER_TEMPERATURE: "45.2"})
    assert coordinator.boiler_temperature == 45.2


def test_return_temperature(coordinator):
    coordinator.data.update({DATA_RETURN_TEMPERATURE: "38.1"})
    assert coordinator.return_temperature == 38.1


def test_boiler_pressure(coordinator):
    coordinator.data.update({DATA_CENTRAL_HEATING_WATER_PRESSURE: "1.5"})
    assert coordinator.boiler_pressure == 1.5


def test_relative_modulation_value(coordinator):
    coordinator.data.update({DATA_REL_MOD_LEVEL: "78"})
    assert coordinator.relative_modulation_value == 78.0


def test_boiler_capacity(coordinator):
    coordinator.data.update({DATA_BOILER_CAPACITY: "24"})
    assert coordinator.boiler_capacity == 24.0


def test_minimum_relative_modulation(coordinator):
    coordinator.data.update({DATA_REL_MIN_MOD_LEVEL: "20"})
    assert coordinator.minimum_relative_modulation_value == 20.0


def test_minimum_relative_modulation_legacy(coordinator):
    coordinator.data.update({DATA_REL_MIN_MOD_LEVEL_LEGACY: "18"})
    assert coordinator.minimum_relative_modulation_value == 18.0


def test_maximum_relative_modulation(coordinator):
    coordinator.data.update({DATA_MAX_REL_MOD_LEVEL_SETTING: "100"})
    assert coordinator.maximum_relative_modulation_value == 100.0


def test_member_id(coordinator):
    coordinator.data.update({DATA_SLAVE_MEMBERID: "95"})
    assert coordinator.member_id == 95


def test_member_id_none(coordinator):
    assert coordinator.member_id is None


def test_dhw_setpoint_bounds(coordinator):
    coordinator.data.update({DATA_DHW_SETPOINT_MINIMUM: "35", DATA_DHW_SETPOINT_MAXIMUM: "65"})
    assert coordinator.minimum_hot_water_setpoint == 35.0
    assert coordinator.maximum_hot_water_setpoint == 65.0


# ── Capabilities ──────────────────────────────────────────────────────────────


def test_capabilities(coordinator):
    assert coordinator.supports_setpoint_management is True
    assert coordinator.supports_hot_water_setpoint_management is True
    assert coordinator.supports_maximum_setpoint_management is True
    assert coordinator.supports_relative_modulation is True


# ── Commands ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_control_setpoint(coordinator, monkeypatch):
    calls = []
    monkeypatch.setattr(coordinator, "_publish_command", AsyncMock(side_effect=lambda *a, **kw: calls.append(a)))

    await coordinator.async_set_control_setpoint(42.5)

    assert ("CS=42.5",) in calls
    assert ("PM=25",) in calls


@pytest.mark.asyncio
async def test_set_hot_water_setpoint(coordinator, monkeypatch):
    calls = []
    monkeypatch.setattr(coordinator, "_publish_command", AsyncMock(side_effect=lambda *a, **kw: calls.append(a)))

    await coordinator.async_set_control_hot_water_setpoint(48.0)

    assert ("SW=48.0",) in calls


@pytest.mark.asyncio
async def test_set_heater_state_on(coordinator, monkeypatch):
    calls = []
    monkeypatch.setattr(coordinator, "_publish_command", AsyncMock(side_effect=lambda *a, **kw: calls.append(a)))

    await coordinator.async_set_heater_state(HeaterState.ON)

    assert ("CH=1",) in calls


@pytest.mark.asyncio
async def test_set_heater_state_off(coordinator, monkeypatch):
    calls = []
    monkeypatch.setattr(coordinator, "_publish_command", AsyncMock(side_effect=lambda *a, **kw: calls.append(a)))

    await coordinator.async_set_heater_state(HeaterState.OFF)

    assert ("CH=0",) in calls


@pytest.mark.asyncio
async def test_set_max_relative_modulation(coordinator, monkeypatch):
    calls = []
    monkeypatch.setattr(coordinator, "_publish_command", AsyncMock(side_effect=lambda *a, **kw: calls.append(a)))

    await coordinator.async_set_control_max_relative_modulation(80)

    assert ("MM=80",) in calls


@pytest.mark.asyncio
async def test_set_max_setpoint(coordinator, monkeypatch):
    calls = []
    monkeypatch.setattr(coordinator, "_publish_command", AsyncMock(side_effect=lambda *a, **kw: calls.append(a)))

    await coordinator.async_set_control_max_setpoint(70.0)

    assert ("SH=70.0",) in calls


@pytest.mark.asyncio
async def test_set_thermostat_setpoint(coordinator, monkeypatch):
    calls = []
    monkeypatch.setattr(coordinator, "_publish_command", AsyncMock(side_effect=lambda *a, **kw: calls.append(a)))

    await coordinator.async_set_control_thermostat_setpoint(21.0)

    assert ("TC=21.0",) in calls


# ── Topic construction ────────────────────────────────────────────────────────


def test_subscription_topic(coordinator):
    assert coordinator._get_topic_for_subscription("flame") == "OTGW/value/otgw-1234/flame"


def test_publishing_topic(coordinator):
    assert coordinator._get_topic_for_publishing() == "OTGW/set/otgw-1234/command"


# ── Message processing ────────────────────────────────────────────────────────


def test_process_message_updates_data(coordinator):
    coordinator._process_message_payload(DATA_BOILER_TEMPERATURE, "52.3")
    assert coordinator.data.get(DATA_BOILER_TEMPERATURE) == 52.3


def test_process_message_json_payload(coordinator):
    coordinator._process_message_payload(DATA_BOILER_TEMPERATURE, json.dumps(45.2))
    assert coordinator.data.get(DATA_BOILER_TEMPERATURE) == 45.2
