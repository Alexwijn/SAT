"""Unit tests for the OTthing MQTT coordinator."""

from unittest.mock import AsyncMock

import pytest

from custom_components.sat.const import CONF_DEVICE, CONF_MODE, CONF_MQTT_TOPIC, OPTIONS_DEFAULTS
from custom_components.sat.coordinator.mqtt.otthing import (
    COMMAND_PAYLOAD_HEAT,
    COMMAND_PAYLOAD_OFF,
    COMMAND_SUFFIX_CH_MODE,
    COMMAND_SUFFIX_CH_SETPOINT,
    COMMAND_SUFFIX_DHW_SETPOINT,
    SatOtthingMqttCoordinator,
)
from custom_components.sat.entry_data import SatConfig, SatMode
from custom_components.sat.types import HeaterState
from tests.const import DEFAULT_USER_DATA


def _make_config():
    data = {**DEFAULT_USER_DATA, CONF_MODE: SatMode.MQTT_OTTHING, CONF_DEVICE: "otthing-01", CONF_MQTT_TOPIC: "otthing/otthing-01"}
    return SatConfig(entry_id="otthing-test", data=data, options={**OPTIONS_DEFAULTS})


@pytest.fixture
def coordinator(hass, monkeypatch):
    monkeypatch.setattr("custom_components.sat.coordinator.mqtt.Store", lambda *a, **kw: AsyncMock())
    return SatOtthingMqttCoordinator(hass, _make_config())


# Realistic OTthing state payload
OTTHING_STATE = {
    "slave": {
        "status": {"flame": True, "ch_mode": True, "dhw_mode": False},
        "flow_t": 45.2,
        "return_t": 38.1,
        "rel_mod": 78.5,
        "max_capacity": 24.0,
        "min_modulation": 20.0,
        "maxModulation": 100.0,
        "ch_set_t": 55.0,
        "dhw_set_t": 50.0,
        "memberId": 95,
        "dhwMin": 35.0,
        "dhwMax": 65.0,
    },
    "thermostat": {
        "status": {"ch_enable": True, "dhw_enable": False},
        "ch_set_t": 55.0,
        "dhw_set_t": 50.0,
    },
}


def _load_state(coordinator):
    """Simulate receiving an OTthing state payload."""
    normalized = coordinator._normalize_payload("state", OTTHING_STATE)
    coordinator.async_set_updated_data(normalized)


# ── Identity ──────────────────────────────────────────────────────────────────


def test_type(coordinator):
    assert coordinator.type == "OTthing (via mqtt)"


def test_id(coordinator):
    assert coordinator.id == "otthing-01"


# ── State reading ─────────────────────────────────────────────────────────────


def test_active_from_thermostat_status(coordinator):
    _load_state(coordinator)
    assert coordinator.active is True


def test_flame_active(coordinator):
    _load_state(coordinator)
    assert coordinator.flame_active is True


def test_hot_water_active(coordinator):
    _load_state(coordinator)
    # thermostat.status.dhw_enable is False, slave.status.dhw_mode is False
    assert coordinator.hot_water_active is False


def test_setpoint(coordinator):
    _load_state(coordinator)
    assert coordinator.setpoint == 55.0


def test_hot_water_setpoint(coordinator):
    _load_state(coordinator)
    assert coordinator.hot_water_setpoint == 50.0


def test_boiler_temperature(coordinator):
    _load_state(coordinator)
    assert coordinator.boiler_temperature == 45.2


def test_return_temperature(coordinator):
    _load_state(coordinator)
    assert coordinator.return_temperature == 38.1


def test_relative_modulation(coordinator):
    _load_state(coordinator)
    assert coordinator.relative_modulation_value == 78.5


def test_boiler_capacity(coordinator):
    _load_state(coordinator)
    assert coordinator.boiler_capacity == 24.0


def test_member_id(coordinator):
    _load_state(coordinator)
    assert coordinator.member_id == 95


def test_min_max_modulation(coordinator):
    _load_state(coordinator)
    assert coordinator.minimum_relative_modulation_value == 100.0
    assert coordinator.maximum_relative_modulation_value == 20.0


def test_dhw_setpoint_bounds(coordinator):
    _load_state(coordinator)
    assert coordinator.minimum_hot_water_setpoint == 35.0
    assert coordinator.maximum_hot_water_setpoint == 65.0


def test_empty_state_returns_defaults(coordinator):
    assert coordinator.setpoint is None
    assert coordinator.boiler_temperature is None
    assert coordinator.active is False
    assert coordinator.flame_active is False
    assert coordinator.member_id is None


# ── Capabilities ──────────────────────────────────────────────────────────────


def test_capabilities(coordinator):
    assert coordinator.supports_setpoint_management is True
    assert coordinator.supports_hot_water_setpoint_management is True
    assert coordinator.supports_relative_modulation_management is False


# ── Commands ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_control_setpoint(coordinator, monkeypatch):
    calls = []
    monkeypatch.setattr(coordinator, "_publish_command", AsyncMock(side_effect=lambda *a, **kw: calls.append((a, kw))))

    await coordinator.async_set_control_setpoint(42.5)

    assert len(calls) == 2
    assert calls[0] == ((COMMAND_PAYLOAD_HEAT,), {"suffix": COMMAND_SUFFIX_CH_MODE})
    assert calls[1] == (("42.5",), {"suffix": COMMAND_SUFFIX_CH_SETPOINT})


@pytest.mark.asyncio
async def test_set_hot_water_setpoint(coordinator, monkeypatch):
    calls = []
    monkeypatch.setattr(coordinator, "_publish_command", AsyncMock(side_effect=lambda *a, **kw: calls.append((a, kw))))

    await coordinator.async_set_control_hot_water_setpoint(48.0)

    assert calls[0] == (("48.0",), {"suffix": COMMAND_SUFFIX_DHW_SETPOINT})


@pytest.mark.asyncio
async def test_set_heater_state_on(coordinator, monkeypatch):
    calls = []
    monkeypatch.setattr(coordinator, "_publish_command", AsyncMock(side_effect=lambda *a, **kw: calls.append((a, kw))))

    await coordinator.async_set_heater_state(HeaterState.ON)

    assert calls[0] == ((COMMAND_PAYLOAD_HEAT,), {"suffix": COMMAND_SUFFIX_CH_MODE})


@pytest.mark.asyncio
async def test_set_heater_state_off(coordinator, monkeypatch):
    calls = []
    monkeypatch.setattr(coordinator, "_publish_command", AsyncMock(side_effect=lambda *a, **kw: calls.append((a, kw))))

    await coordinator.async_set_heater_state(HeaterState.OFF)

    assert calls[0] == ((COMMAND_PAYLOAD_OFF,), {"suffix": COMMAND_SUFFIX_CH_MODE})


# ── Topic construction ────────────────────────────────────────────────────────


def test_subscription_topic(coordinator):
    assert coordinator._get_topic_for_subscription("state") == "otthing/otthing-01/state"


def test_publishing_topic(coordinator):
    assert coordinator._get_topic_for_publishing() == "otthing/otthing-01"


def test_publishing_topic_with_suffix(coordinator):
    assert coordinator._build_publish_topic("chMode1") == "otthing/otthing-01/chMode1"


# ── Payload normalization ─────────────────────────────────────────────────────


def test_normalize_dict_payload_adds_defaults(coordinator):
    payload = {"slave": {"flow_t": 45.0}}
    result = coordinator._normalize_payload("state", payload)
    assert "thermostat" in result
    assert result["state"] == payload


def test_normalize_scalar_payload(coordinator):
    result = coordinator._normalize_payload("key", "value")
    assert result == {"key": "value"}
