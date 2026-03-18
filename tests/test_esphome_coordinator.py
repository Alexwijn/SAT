"""Unit tests for the ESPHome coordinator."""

from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from aioesphomeapi import (
    BinarySensorInfo,
    BinarySensorState,
    DeviceInfo,
    NumberInfo,
    NumberState,
    SensorInfo,
    SensorState,
    SwitchInfo,
    SwitchState,
)

from custom_components.sat.const import CONF_DEVICE, CONF_MODE, OPTIONS_DEFAULTS
from custom_components.sat.coordinator.esphome import SatEspHomeCoordinator, EspHomeEntityKeys
from custom_components.sat.entry_data import SatConfig, SatMode
from custom_components.sat.types import HeaterState
from tests.const import DEFAULT_USER_DATA


# ── Fake RuntimeEntryData ──────────────────────────────────────────────────────

class FakeRuntimeEntryData:
    """Minimal stand-in for homeassistant.components.esphome.entry_data.RuntimeEntryData."""

    def __init__(self, info=None, state=None, device_info=None):
        self.info = info or {}
        self.state = state if state is not None else defaultdict(dict)
        self.device_info = device_info
        self.client = Mock(spec=["number_command", "switch_command"])
        self.available = True
        self._state_subscriptions: dict = {}
        self._device_update_subscriptions: set = set()

    def async_subscribe_state_update(self, *, device_id, state_type, state_key, entity_callback):
        key = (state_type, device_id, state_key)
        self._state_subscriptions[key] = entity_callback
        return lambda: self._state_subscriptions.pop(key, None)

    def async_subscribe_device_updated(self, callback_):
        self._device_update_subscriptions.add(callback_)
        return lambda: self._device_update_subscriptions.discard(callback_)


# ── Helpers ────────────────────────────────────────────────────────────────────

ESPHOME_ENTRY_ID = "esphome-test-entry"

# Realistic entity key assignments (as an ESPHome device would assign them)
KEYS = EspHomeEntityKeys(
    t_boiler=1, t_ret=2, rel_mod_level=3, device_id=4,
    max_capacity=5, min_mod_level=6, t_dhw_set_lb=7, t_dhw_set_ub=8, ch_pressure=9,
    flame_on=10, dhw_active=11, ch_active=12,
    t_set=20, t_dhw_set=21, max_t_set=22, max_rel_mod_level=23,
    ch_enable=30, dhw_enable=31,
)


def _build_info():
    """Build an info dict matching what RuntimeEntryData would contain."""
    return {
        SensorInfo: {
            (0, 1): SensorInfo(object_id="t_boiler", key=1),
            (0, 2): SensorInfo(object_id="t_ret", key=2),
            (0, 3): SensorInfo(object_id="rel_mod_level", key=3),
            (0, 4): SensorInfo(object_id="device_id", key=4),
            (0, 5): SensorInfo(object_id="max_capacity", key=5),
            (0, 6): SensorInfo(object_id="min_mod_level", key=6),
            (0, 7): SensorInfo(object_id="t_dhw_set_lb", key=7),
            (0, 8): SensorInfo(object_id="t_dhw_set_ub", key=8),
            (0, 9): SensorInfo(object_id="ch_pressure", key=9),
            (0, 99): SensorInfo(object_id="unrelated_sensor", key=99),
        },
        BinarySensorInfo: {
            (0, 10): BinarySensorInfo(object_id="flame_on", key=10),
            (0, 11): BinarySensorInfo(object_id="dhw_active", key=11),
            (0, 12): BinarySensorInfo(object_id="ch_active", key=12),
        },
        NumberInfo: {
            (0, 20): NumberInfo(object_id="t_set", key=20),
            (0, 21): NumberInfo(object_id="t_dhw_set", key=21),
            (0, 22): NumberInfo(object_id="max_t_set", key=22),
            (0, 23): NumberInfo(object_id="max_rel_mod_level", key=23),
        },
        SwitchInfo: {
            (0, 30): SwitchInfo(object_id="ch_enable", key=30),
            (0, 31): SwitchInfo(object_id="dhw_enable", key=31),
        },
    }


def _build_state():
    """Build a state dict with realistic boiler values."""
    state = defaultdict(dict)
    state[SensorState][1] = SensorState(key=1, state=45.2)   # t_boiler
    state[SensorState][2] = SensorState(key=2, state=38.1)   # t_ret
    state[SensorState][3] = SensorState(key=3, state=78.5)   # rel_mod_level
    state[SensorState][4] = SensorState(key=4, state=95.0)   # device_id (member_id)
    state[SensorState][5] = SensorState(key=5, state=24.0)   # max_capacity
    state[SensorState][6] = SensorState(key=6, state=20.0)   # min_mod_level
    state[SensorState][7] = SensorState(key=7, state=35.0)   # t_dhw_set_lb
    state[SensorState][8] = SensorState(key=8, state=65.0)   # t_dhw_set_ub
    state[SensorState][9] = SensorState(key=9, state=1.5)    # ch_pressure

    state[BinarySensorState][10] = BinarySensorState(key=10, state=True)   # flame_on
    state[BinarySensorState][11] = BinarySensorState(key=11, state=False)  # dhw_active
    state[BinarySensorState][12] = BinarySensorState(key=12, state=True)   # ch_active

    state[NumberState][20] = NumberState(key=20, state=55.0)  # t_set
    state[NumberState][21] = NumberState(key=21, state=50.0)  # t_dhw_set
    state[NumberState][22] = NumberState(key=22, state=75.0)  # max_t_set
    state[NumberState][23] = NumberState(key=23, state=100.0) # max_rel_mod_level

    state[SwitchState][30] = SwitchState(key=30, state=True)  # ch_enable
    state[SwitchState][31] = SwitchState(key=31, state=True)  # dhw_enable
    return state


def _make_esphome_config():
    data = {**DEFAULT_USER_DATA, CONF_MODE: SatMode.ESPHOME, CONF_DEVICE: ESPHOME_ENTRY_ID}
    return SatConfig(entry_id="esphome-test", data=data, options={**OPTIONS_DEFAULTS})


def _make_fake_esphome_entry(runtime_data):
    """Create a minimal config entry stand-in with runtime_data."""
    return SimpleNamespace(
        entry_id=ESPHOME_ENTRY_ID,
        domain="esphome",
        runtime_data=runtime_data,
    )


@pytest.fixture
def runtime_data():
    return FakeRuntimeEntryData(
        info=_build_info(),
        state=_build_state(),
        device_info=SimpleNamespace(mac_address="AA:BB:CC:DD:EE:FF"),
    )


@pytest.fixture
def esphome_coordinator(hass, runtime_data, monkeypatch):
    """Create a SatEspHomeCoordinator backed by fake runtime data."""
    fake_entry = _make_fake_esphome_entry(runtime_data)

    original_get = hass.config_entries.async_get_entry

    def patched_get(entry_id):
        if entry_id == ESPHOME_ENTRY_ID:
            return fake_entry
        return original_get(entry_id)

    monkeypatch.setattr(hass.config_entries, "async_get_entry", patched_get)

    return SatEspHomeCoordinator(hass, _make_esphome_config())


# ── Discovery tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_discover_entities_finds_all_opentherm_keys(esphome_coordinator):
    keys = esphome_coordinator._discover_entities()
    assert keys == KEYS


@pytest.mark.asyncio
async def test_discover_entities_ignores_unknown_object_ids(esphome_coordinator, runtime_data):
    """Entities with unrecognized object_ids should not appear in the keys."""
    keys = esphome_coordinator._discover_entities()
    # key 99 ("unrelated_sensor") should not be mapped anywhere
    for field_name in vars(keys):
        assert getattr(keys, field_name) != 99


@pytest.mark.asyncio
async def test_discover_entities_with_empty_info(hass, monkeypatch):
    """When the device exposes no entities, all keys should be None."""
    empty_data = FakeRuntimeEntryData(
        info={},
        device_info=SimpleNamespace(mac_address="00:00:00:00:00:00"),
    )
    fake_entry = _make_fake_esphome_entry(empty_data)

    original_get = hass.config_entries.async_get_entry

    def patched_get(entry_id):
        if entry_id == ESPHOME_ENTRY_ID:
            return fake_entry
        return original_get(entry_id)

    monkeypatch.setattr(hass.config_entries, "async_get_entry", patched_get)

    coordinator = SatEspHomeCoordinator(hass, _make_esphome_config())
    keys = coordinator._discover_entities()
    assert keys == EspHomeEntityKeys()


# ── State reading tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_sensor_values(esphome_coordinator):
    esphome_coordinator._keys = KEYS

    assert esphome_coordinator.boiler_temperature == 45.2
    assert esphome_coordinator.return_temperature == 38.1
    assert esphome_coordinator.relative_modulation_value == 78.5
    assert esphome_coordinator.boiler_capacity == 24.0
    assert esphome_coordinator.minimum_relative_modulation_value == 20.0
    assert esphome_coordinator.boiler_pressure == 1.5
    assert esphome_coordinator.minimum_hot_water_setpoint == 35.0
    assert esphome_coordinator.maximum_hot_water_setpoint == 65.0


@pytest.mark.asyncio
async def test_read_binary_sensor_values(esphome_coordinator):
    esphome_coordinator._keys = KEYS

    assert esphome_coordinator.flame_active is True
    assert esphome_coordinator.hot_water_active is False


@pytest.mark.asyncio
async def test_read_number_values(esphome_coordinator):
    esphome_coordinator._keys = KEYS

    assert esphome_coordinator.setpoint == 55.0
    assert esphome_coordinator.hot_water_setpoint == 50.0
    assert esphome_coordinator.maximum_setpoint_value == 75.0
    assert esphome_coordinator.maximum_relative_modulation_value == 100.0


@pytest.mark.asyncio
async def test_read_switch_values(esphome_coordinator):
    esphome_coordinator._keys = KEYS

    assert esphome_coordinator.active is True


@pytest.mark.asyncio
async def test_member_id_conversion(esphome_coordinator):
    esphome_coordinator._keys = KEYS

    assert esphome_coordinator.member_id == 95


@pytest.mark.asyncio
async def test_missing_key_returns_none(esphome_coordinator):
    """When a key is None (entity not discovered), the value should be None."""
    esphome_coordinator._keys = EspHomeEntityKeys()  # All None

    assert esphome_coordinator.boiler_temperature is None
    assert esphome_coordinator.setpoint is None
    assert esphome_coordinator.member_id is None
    assert esphome_coordinator.active is False
    assert esphome_coordinator.flame_active is False


@pytest.mark.asyncio
async def test_missing_state_returns_none(esphome_coordinator, runtime_data):
    """When the state dict has no entry for a key, the value should be None."""
    esphome_coordinator._keys = KEYS
    runtime_data.state[SensorState].clear()

    assert esphome_coordinator.boiler_temperature is None


# ── Command tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_control_setpoint(esphome_coordinator, runtime_data):
    esphome_coordinator._keys = KEYS

    await esphome_coordinator.async_set_control_setpoint(42.5)

    runtime_data.client.number_command.assert_called_once_with(KEYS.t_set, 42.5)


@pytest.mark.asyncio
async def test_set_hot_water_setpoint(esphome_coordinator, runtime_data):
    esphome_coordinator._keys = KEYS

    await esphome_coordinator.async_set_control_hot_water_setpoint(48.0)

    runtime_data.client.number_command.assert_called_once_with(KEYS.t_dhw_set, 48.0)


@pytest.mark.asyncio
async def test_set_heater_state_on(esphome_coordinator, runtime_data):
    esphome_coordinator._keys = KEYS

    await esphome_coordinator.async_set_heater_state(HeaterState.ON)

    runtime_data.client.switch_command.assert_called_once_with(KEYS.ch_enable, True)


@pytest.mark.asyncio
async def test_set_heater_state_off(esphome_coordinator, runtime_data):
    esphome_coordinator._keys = KEYS

    await esphome_coordinator.async_set_heater_state(HeaterState.OFF)

    runtime_data.client.switch_command.assert_called_once_with(KEYS.ch_enable, False)


@pytest.mark.asyncio
async def test_set_max_relative_modulation(esphome_coordinator, runtime_data):
    esphome_coordinator._keys = KEYS

    await esphome_coordinator.async_set_control_max_relative_modulation(80)

    runtime_data.client.number_command.assert_called_once_with(KEYS.max_rel_mod_level, 80.0)


@pytest.mark.asyncio
async def test_set_max_setpoint(esphome_coordinator, runtime_data):
    esphome_coordinator._keys = KEYS

    await esphome_coordinator.async_set_control_max_setpoint(70.0)

    runtime_data.client.number_command.assert_called_once_with(KEYS.max_t_set, 70.0)


@pytest.mark.asyncio
async def test_commands_skipped_when_key_not_discovered(esphome_coordinator, runtime_data):
    """Commands should be no-ops when the entity was not discovered."""
    esphome_coordinator._keys = EspHomeEntityKeys()  # All None

    await esphome_coordinator.async_set_control_setpoint(42.5)
    await esphome_coordinator.async_set_heater_state(HeaterState.ON)

    runtime_data.client.number_command.assert_not_called()
    runtime_data.client.switch_command.assert_not_called()


# ── Subscription / lifecycle tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_added_to_hass_discovers_and_subscribes(esphome_coordinator, runtime_data, hass):
    await esphome_coordinator.async_added_to_hass(hass)

    assert esphome_coordinator._keys == KEYS
    # Should have subscriptions for all discovered keys + 1 device-level subscription
    assert len(esphome_coordinator._unsub_callbacks) > 0
    assert len(runtime_data._state_subscriptions) > 0
    assert len(runtime_data._device_update_subscriptions) == 1


@pytest.mark.asyncio
async def test_will_remove_from_hass_unsubscribes(esphome_coordinator, runtime_data, hass):
    await esphome_coordinator.async_added_to_hass(hass)
    assert len(runtime_data._state_subscriptions) > 0

    await esphome_coordinator.async_will_remove_from_hass()

    assert esphome_coordinator._unsub_callbacks == []
    assert len(runtime_data._state_subscriptions) == 0
    assert len(runtime_data._device_update_subscriptions) == 0


# ── Capability tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_capabilities_reflect_discovered_keys(esphome_coordinator):
    esphome_coordinator._keys = KEYS

    assert esphome_coordinator.supports_setpoint_management is True
    assert esphome_coordinator.supports_hot_water_setpoint_management is True
    assert esphome_coordinator.supports_maximum_setpoint_management is True


@pytest.mark.asyncio
async def test_capabilities_false_when_keys_missing(esphome_coordinator):
    esphome_coordinator._keys = EspHomeEntityKeys()

    assert esphome_coordinator.supports_setpoint_management is False
    assert esphome_coordinator.supports_hot_water_setpoint_management is False
    assert esphome_coordinator.supports_maximum_setpoint_management is False


# ── Identity tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_id_returns_mac_address(esphome_coordinator):
    assert esphome_coordinator.id == "AA:BB:CC:DD:EE:FF"


@pytest.mark.asyncio
async def test_type_returns_esphome(esphome_coordinator):
    assert esphome_coordinator.type == "ESPHome"
