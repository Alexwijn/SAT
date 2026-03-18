"""Unit tests for the EMS MQTT coordinator."""

import json
from unittest.mock import AsyncMock

import pytest

from custom_components.sat.const import CONF_DEVICE, CONF_MODE, CONF_MQTT_TOPIC, OPTIONS_DEFAULTS
from custom_components.sat.coordinator.mqtt.ems import (
    DATA_BOILER_CAPACITY,
    DATA_BOILER_DATA,
    DATA_BOILER_TEMPERATURE,
    DATA_CENTRAL_HEATING,
    DATA_CONTROL_SETPOINT,
    DATA_DHW_ENABLE,
    DATA_DHW_SETPOINT,
    DATA_FLAME_ACTIVE,
    DATA_MAX_REL_MOD_LEVEL_SETTING,
    DATA_REL_MIN_MOD_LEVEL,
    DATA_REL_MOD_LEVEL,
    DATA_RETURN_TEMPERATURE,
    SatEmsMqttCoordinator,
)
from custom_components.sat.entry_data import SatConfig, SatMode
from custom_components.sat.types import HeaterState
from tests.const import DEFAULT_USER_DATA


def _make_config():
    data = {**DEFAULT_USER_DATA, CONF_MODE: SatMode.MQTT_EMS, CONF_DEVICE: "ems-esp", CONF_MQTT_TOPIC: "ems-esp"}
    return SatConfig(entry_id="ems-test", data=data, options={**OPTIONS_DEFAULTS})


@pytest.fixture
def coordinator(hass, monkeypatch):
    monkeypatch.setattr("custom_components.sat.coordinator.mqtt.Store", lambda *a, **kw: AsyncMock())
    return SatEmsMqttCoordinator(hass, _make_config())


# ── Identity ──────────────────────────────────────────────────────────────────


def test_type(coordinator):
    assert coordinator.type == "Energy Management System (via mqtt)"


def test_id(coordinator):
    assert coordinator.id == "ems-esp"


# ── State reading ─────────────────────────────────────────────────────────────


def test_active(coordinator):
    coordinator.data.update({DATA_CENTRAL_HEATING: "on"})
    assert coordinator.active is True
    coordinator.data.update({DATA_CENTRAL_HEATING: "off"})
    assert coordinator.active is False


def test_flame_active(coordinator):
    coordinator.data.update({DATA_FLAME_ACTIVE: "on"})
    assert coordinator.flame_active is True


def test_hot_water_active(coordinator):
    coordinator.data.update({DATA_DHW_ENABLE: "on"})
    assert coordinator.hot_water_active is True


def test_setpoint(coordinator):
    coordinator.data.update({DATA_CONTROL_SETPOINT: 55.0})
    assert coordinator.setpoint == 55.0


def test_hot_water_setpoint(coordinator):
    coordinator.data.update({DATA_DHW_SETPOINT: 48.5})
    assert coordinator.hot_water_setpoint == 48.5


def test_boiler_temperature(coordinator):
    coordinator.data.update({DATA_BOILER_TEMPERATURE: 45.2})
    assert coordinator.boiler_temperature == 45.2


def test_return_temperature(coordinator):
    coordinator.data.update({DATA_RETURN_TEMPERATURE: 38.1})
    assert coordinator.return_temperature == 38.1


def test_relative_modulation(coordinator):
    coordinator.data.update({DATA_REL_MOD_LEVEL: 78})
    assert coordinator.relative_modulation_value == 78.0


def test_boiler_capacity(coordinator):
    coordinator.data.update({DATA_BOILER_CAPACITY: 24})
    assert coordinator.boiler_capacity == 24.0


def test_min_max_modulation(coordinator):
    coordinator.data.update({DATA_REL_MIN_MOD_LEVEL: 20, DATA_MAX_REL_MOD_LEVEL_SETTING: 100})
    assert coordinator.minimum_relative_modulation_value == 20.0
    assert coordinator.maximum_relative_modulation_value == 100.0


def test_member_id_not_supported(coordinator):
    assert coordinator.member_id is None


# ── Capabilities ──────────────────────────────────────────────────────────────


def test_capabilities(coordinator):
    assert coordinator.supports_setpoint_management is True
    assert coordinator.supports_hot_water_setpoint_management is True
    assert coordinator.supports_maximum_setpoint_management is True


# ── Commands ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_control_setpoint(coordinator, monkeypatch):
    calls = []
    monkeypatch.setattr(coordinator, "_publish_command", AsyncMock(side_effect=lambda *a, **kw: calls.append(a)))

    await coordinator.async_set_control_setpoint(42.5)

    assert len(calls) == 1
    payload = json.loads(calls[0][0])
    assert payload == {"cmd": "selflowtemp", "value": 42.5}


@pytest.mark.asyncio
async def test_set_control_setpoint_minimum_sends_zero(coordinator, monkeypatch):
    """When setpoint is 10 (minimum), EMS sends value 0 to disable."""
    calls = []
    monkeypatch.setattr(coordinator, "_publish_command", AsyncMock(side_effect=lambda *a, **kw: calls.append(a)))

    await coordinator.async_set_control_setpoint(10)

    payload = json.loads(calls[0][0])
    assert payload["value"] == 0


@pytest.mark.asyncio
async def test_set_hot_water_setpoint(coordinator, monkeypatch):
    calls = []
    monkeypatch.setattr(coordinator, "_publish_command", AsyncMock(side_effect=lambda *a, **kw: calls.append(a)))

    await coordinator.async_set_control_hot_water_setpoint(48.0)

    payload = json.loads(calls[0][0])
    assert payload == {"cmd": "dhw/seltemp", "value": 48.0}


@pytest.mark.asyncio
async def test_set_heater_state_on(coordinator, monkeypatch):
    calls = []
    monkeypatch.setattr(coordinator, "_publish_command", AsyncMock(side_effect=lambda *a, **kw: calls.append(a)))

    await coordinator.async_set_heater_state(HeaterState.ON)

    payload = json.loads(calls[0][0])
    assert payload == {"cmd": "heatingactivated", "value": "on"}


@pytest.mark.asyncio
async def test_set_heater_state_off(coordinator, monkeypatch):
    calls = []
    monkeypatch.setattr(coordinator, "_publish_command", AsyncMock(side_effect=lambda *a, **kw: calls.append(a)))

    await coordinator.async_set_heater_state(HeaterState.OFF)

    payload = json.loads(calls[0][0])
    assert payload == {"cmd": "heatingactivated", "value": "off"}


@pytest.mark.asyncio
async def test_set_max_relative_modulation(coordinator, monkeypatch):
    calls = []
    monkeypatch.setattr(coordinator, "_publish_command", AsyncMock(side_effect=lambda *a, **kw: calls.append(a)))

    await coordinator.async_set_control_max_relative_modulation(80)

    payload = json.loads(calls[0][0])
    assert payload == {"cmd": "burnmaxpower", "value": 80}


@pytest.mark.asyncio
async def test_set_max_relative_modulation_floor(coordinator, monkeypatch):
    """EMS enforces a minimum of 20 for max modulation."""
    calls = []
    monkeypatch.setattr(coordinator, "_publish_command", AsyncMock(side_effect=lambda *a, **kw: calls.append(a)))

    await coordinator.async_set_control_max_relative_modulation(10)

    payload = json.loads(calls[0][0])
    assert payload["value"] == 20


@pytest.mark.asyncio
async def test_set_max_setpoint(coordinator, monkeypatch):
    calls = []
    monkeypatch.setattr(coordinator, "_publish_command", AsyncMock(side_effect=lambda *a, **kw: calls.append(a)))

    await coordinator.async_set_control_max_setpoint(70.0)

    payload = json.loads(calls[0][0])
    assert payload == {"cmd": "heatingtemp", "value": 70.0}


# ── Topic construction ────────────────────────────────────────────────────────


def test_subscription_topic(coordinator):
    assert coordinator._get_topic_for_subscription("boiler_data") == "ems-esp/boiler_data"


def test_publishing_topic(coordinator):
    assert coordinator._get_topic_for_publishing() == "ems-esp/boiler"


# ── Payload normalization ─────────────────────────────────────────────────────


def test_normalize_dict_payload(coordinator):
    payload = {"curflowtemp": 45.2, "burngas": "on"}
    result = coordinator._normalize_payload("boiler_data", payload)
    assert result == payload


def test_normalize_scalar_payload(coordinator):
    result = coordinator._normalize_payload("some_key", "value")
    assert result == {"some_key": "value"}
