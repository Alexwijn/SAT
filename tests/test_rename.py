from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.climate import HVACMode, PRESET_ACTIVITY, PRESET_COMFORT, PRESET_HOME
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import State
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_mqtt_message, async_fire_time_changed, mock_restore_cache

from custom_components.sat.const import CONF_DEVICE, CONF_MODE, CONF_MQTT_TOPIC, CONF_ROOMS, CONF_WINDOW_SENSORS, DOMAIN
from custom_components.sat.entry_data import SatEntryData, SatMode
from custom_components.sat.helpers import timestamp
from custom_components.sat.pid import TemperatureSample
from custom_components.sat.types import PWMStatus
from tests.const import DEFAULT_USER_DATA
from tests.test_esphome_coordinator import ESPHOME_ENTRY_ID, FakeRuntimeEntryData, _build_info, _build_state, _make_fake_esphome_entry
from tests.test_serial_coordinator import DummyGateway

CLIMATE = "climate.test"
RENAMED = "climate.renamed"
WINDOW = "binary_sensor.window"


async def _set_up(hass, data=None, options=None) -> MockConfigEntry:
    hass.states.async_set("sensor.test_inside_sensor", "18")
    hass.states.async_set("sensor.test_outside_sensor", "3")

    entry = MockConfigEntry(domain=DOMAIN, data={**DEFAULT_USER_DATA, **(data or {})}, options=options or {}, version=13)
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()

    return entry


async def _rename(hass, entity_id: str = CLIMATE, new_entity_id: str = RENAMED) -> None:
    er.async_get(hass).async_update_entity(entity_id, new_entity_id=new_entity_id)
    await hass.async_block_till_done()

    assert hass.states.get(new_entity_id) is not None


def _entry_data(hass, entry: MockConfigEntry) -> SatEntryData:
    return hass.data[DOMAIN][entry.entry_id]


async def test_rename_keeps_the_mqtt_subscriptions(hass, mqtt_mock):
    entry = await _set_up(hass, {CONF_MODE: SatMode.MQTT_OPENTHERM, CONF_DEVICE: "otgw-1", CONF_MQTT_TOPIC: "OTGW"})

    await _rename(hass)
    async_fire_mqtt_message(hass, "OTGW/value/otgw-1/Tboiler", "51")
    await hass.async_block_till_done()

    assert _entry_data(hass, entry).coordinator.boiler_temperature == 51

    await hass.async_stop()


async def test_rename_keeps_the_serial_gateway_connected(hass, monkeypatch):
    tracker = {}
    monkeypatch.setattr("custom_components.sat.coordinator.serial.OpenThermGateway", lambda: DummyGateway(tracker))
    await _set_up(hass, {CONF_MODE: SatMode.SERIAL, CONF_DEVICE: "/dev/ttyUSB0"})

    await _rename(hass)

    assert tracker["instance"].disconnected is False
    assert len(tracker["instance"].subscriptions) == 1


async def test_rename_keeps_the_esphome_subscriptions(hass, monkeypatch):
    runtime_data = FakeRuntimeEntryData(info=_build_info(), state=_build_state(), device_info=SimpleNamespace(mac_address="AA:BB:CC:DD:EE:FF"))
    esphome_entry = _make_fake_esphome_entry(runtime_data)
    original_get = hass.config_entries.async_get_entry
    monkeypatch.setattr(hass.config_entries, "async_get_entry", lambda entry_id: esphome_entry if entry_id == ESPHOME_ENTRY_ID else original_get(entry_id))
    await _set_up(hass, {CONF_MODE: SatMode.ESPHOME, CONF_DEVICE: ESPHOME_ENTRY_ID})

    subscriptions = len(runtime_data._device_update_subscriptions)
    assert subscriptions > 0

    await _rename(hass)

    assert len(runtime_data._device_update_subscriptions) == subscriptions


async def test_rename_does_not_release_the_boiler(hass, monkeypatch):
    entry = await _set_up(hass)
    release = AsyncMock()
    monkeypatch.setattr(_entry_data(hass, entry).coordinator, "_async_release", release)

    await _rename(hass)

    release.assert_not_awaited()


async def test_rename_does_not_interrupt_the_pwm_phase(hass):
    entry = await _set_up(hass)
    heating_control = _entry_data(hass, entry).heating_control
    heating_control._pwm._enabled = True
    heating_control._pwm._status = PWMStatus.ON

    await _rename(hass)

    assert heating_control.pwm_state.status is PWMStatus.ON


async def test_rename_does_not_count_as_a_target_change(hass):
    entry = await _set_up(hass)
    demand_control = _entry_data(hass, entry).demand_control
    demand_control._target_temperature_changed_at = 1000.0

    await _rename(hass)

    assert demand_control.target_temperature_changed_at == 1000.0


async def test_renaming_back_keeps_the_current_state(hass):
    entry = await _set_up(hass)
    climate = _entry_data(hass, entry).climate
    await climate.async_set_hvac_mode(HVACMode.OFF)
    await climate.async_set_preset_mode(PRESET_HOME)

    await _rename(hass)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    await climate.async_set_preset_mode(PRESET_COMFORT)
    await _rename(hass, RENAMED, CLIMATE)

    assert hass.states.get(CLIMATE).state == HVACMode.HEAT
    assert climate.preset_mode == PRESET_COMFORT


async def test_rename_ignores_an_old_state_of_the_new_id(hass):
    mock_restore_cache(hass, [State(RENAMED, HVACMode.OFF, {"preset_mode": PRESET_HOME})])
    entry = await _set_up(hass)
    climate = _entry_data(hass, entry).climate
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    await climate.async_set_preset_mode(PRESET_COMFORT)

    await _rename(hass)

    assert hass.states.get(RENAMED).state == HVACMode.HEAT
    assert climate.preset_mode == PRESET_COMFORT


async def test_rename_moves_the_pid_store(hass, hass_storage):
    entry = await _set_up(hass)
    pid = _entry_data(hass, entry).demand_control.pid
    pid._integral = 2.5
    await pid._async_save_state()

    await _rename(hass)

    assert hass_storage[f"sat.pid.{RENAMED}.Fake"]["data"]["integral"] == 2.5
    assert f"sat.pid.{CLIMATE}.Fake" not in hass_storage
    assert _entry_data(hass, entry).demand_control.id == RENAMED


async def test_reload_after_a_rename_keeps_the_learned_integral(hass):
    entry = await _set_up(hass)
    _entry_data(hass, entry).demand_control.pid._integral = 2.5

    await _rename(hass)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert _entry_data(hass, entry).demand_control.pid.integral == 2.5


async def test_rename_does_not_duplicate_the_samples_of_the_rooms(hass):
    entry = await _set_up(hass, {CONF_ROOMS: ["climate.bedroom"]})
    pid = _entry_data(hass, entry).demand_control.areas.get("climate.bedroom").pid
    pid._samples.append(TemperatureSample(timestamp=timestamp(), temperature=19.5))
    await pid._async_save_state()

    await _rename(hass)

    assert len(pid._samples) == 1


async def test_rename_keeps_a_window_pause(hass):
    entry = await _set_up(hass, options={CONF_WINDOW_SENSORS: [WINDOW]})
    climate = _entry_data(hass, entry).climate
    await climate.async_set_preset_mode(PRESET_COMFORT)
    hass.states.async_set(WINDOW, "on")
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=16))
    await hass.async_block_till_done()

    await _rename(hass)

    assert climate.preset_mode == PRESET_ACTIVITY

    hass.states.async_set(WINDOW, "off")
    await hass.async_block_till_done()

    assert climate.preset_mode == PRESET_COMFORT


async def test_renamed_climate_still_releases_on_unload(hass, monkeypatch):
    entry = await _set_up(hass)
    coordinator = _entry_data(hass, entry).coordinator
    await _rename(hass)
    release, teardown = AsyncMock(), AsyncMock()
    monkeypatch.setattr(coordinator, "_async_release", release)
    monkeypatch.setattr(coordinator, "async_will_remove_from_hass", teardown)

    assert await hass.config_entries.async_unload(entry.entry_id)

    release.assert_awaited_once()
    teardown.assert_awaited_once()


async def test_disabling_the_climate_releases_but_stays_connected(hass, monkeypatch):
    entry = await _set_up(hass)
    coordinator = _entry_data(hass, entry).coordinator
    release, teardown = AsyncMock(), AsyncMock()
    monkeypatch.setattr(coordinator, "_async_release", release)
    monkeypatch.setattr(coordinator, "async_will_remove_from_hass", teardown)

    er.async_get(hass).async_update_entity(CLIMATE, disabled_by=er.RegistryEntryDisabler.USER)
    await hass.async_block_till_done()

    release.assert_awaited_once()
    teardown.assert_not_awaited()


async def test_removing_the_climate_from_the_registry_releases(hass, monkeypatch):
    entry = await _set_up(hass)
    release = AsyncMock()
    monkeypatch.setattr(_entry_data(hass, entry).coordinator, "_async_release", release)

    er.async_get(hass).async_remove(CLIMATE)
    await hass.async_block_till_done()

    release.assert_awaited_once()


@pytest.mark.parametrize("disabled", [False, True])
async def test_stop_disconnects_once(hass, monkeypatch, disabled):
    entry = await _set_up(hass)
    coordinator = _entry_data(hass, entry).coordinator
    teardown = AsyncMock()
    monkeypatch.setattr(coordinator, "async_will_remove_from_hass", teardown)

    if disabled:
        er.async_get(hass).async_update_entity(CLIMATE, disabled_by=er.RegistryEntryDisabler.USER)
        await hass.async_block_till_done()

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    teardown.assert_awaited_once()
