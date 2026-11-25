"""Tests focused on heating control behavior."""

import asyncio
from typing import Optional
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.climate import HVACMode

from custom_components.sat.const import (
    COLD_SETPOINT,
    CONF_FLAME_OFF_SETPOINT_OFFSET_CELSIUS,
    CONF_FLOW_SETPOINT_OFFSET_CELSIUS,
    CONF_FORCE_PULSE_WIDTH_MODULATION,
    CONF_HEATING_SYSTEM,
    CONF_MINIMUM_SETPOINT,
    CONF_MODE,
    CONF_MODULATION_SUPPRESSION_DELAY_SECONDS,
    CONF_OVERSHOOT_PROTECTION,
    HEATER_STARTUP_TIMEFRAME,
    SATURATION_SUSTAIN_SECONDS,
    TARGET_TEMPERATURE_CHANGE_WINDOW_SECONDS,
    UNDERHEAT_SUSTAIN_SECONDS,
    HeatingSystem,
    MINIMUM_RELATIVE_MODULATION,
    MINIMUM_SETPOINT,
    OPTIONS_DEFAULTS,
)
from custom_components.sat.const import OVERSHOOT_REFERENCE_WINDOW_SECONDS, OVERSHOOT_SUSTAIN_SECONDS
from custom_components.sat.cycles.const import OVERSHOOT_MARGIN_CELSIUS
from custom_components.sat.entry_data import SatConfig, SatMode
from custom_components.sat.heating_control import (
    HeatingDemand,
    SatHeatingControl,
)
from custom_components.sat.helpers import timestamp
from custom_components.sat.manufacturer import ManufacturerFactory
from custom_components.sat.types import HeaterState, PWMStatus

pytestmark = pytest.mark.parametrize(
    ("domains", "data", "options", "config"),
    [
        (
            [],
            {
                CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS,
            },
            {},
            {},
        ),
    ],
)


def _update_coordinator_config(coordinator) -> None:
    options = {
        **OPTIONS_DEFAULTS,
    }
    config = SatConfig(
        entry_id="test",
        data={
            CONF_MODE: SatMode.FAKE.value,
            CONF_MINIMUM_SETPOINT: 40.0,
            CONF_OVERSHOOT_PROTECTION: True,
            CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS,
        },
        options=options,
    )
    coordinator._config = config
    coordinator._manufacturer = ManufacturerFactory.resolve_by_name(config.manufacturer)


def _enable_pwm(heating_control: SatHeatingControl, status: PWMStatus) -> None:
    heating_control._pwm._enabled = True
    heating_control._pwm._status = status


def _make_demand(
    requested_setpoint: float,
    hvac_mode: HVACMode = HVACMode.HEAT,
    outside_temperature: float = 10.0,
    valves_open: bool = True,
) -> HeatingDemand:
    return HeatingDemand(
        valves_open=valves_open,
        hvac_mode=hvac_mode,
        timestamp=timestamp(),
        requested_setpoint=requested_setpoint,
        outside_temperature=outside_temperature,
    )


async def test_hvac_off_forces_minimum(heating_control):
    heating_control._control_setpoint = 45.0

    await heating_control.update(_make_demand(45.0, hvac_mode=HVACMode.OFF))

    assert heating_control.control_setpoint == MINIMUM_SETPOINT
    assert heating_control._coordinator.active is False


async def test_setpoint_below_cold_resets_pwm_cycle_and_forces_minimum(heating_control, coordinator):
    _enable_pwm(heating_control, PWMStatus.ON)

    await heating_control.update(_make_demand(COLD_SETPOINT - 0.1))

    assert heating_control.control_setpoint == MINIMUM_SETPOINT
    assert heating_control.relative_modulation_value == heating_control._config.pwm.maximum_relative_modulation
    assert heating_control.pwm_state.enabled is True
    assert heating_control.pwm_state.status is PWMStatus.OFF
    assert coordinator.active is False


async def test_closed_valves_force_minimum_and_keep_pwm_enabled(heating_control, coordinator):
    _enable_pwm(heating_control, PWMStatus.ON)

    await heating_control.update(_make_demand(45.0, valves_open=False))

    assert heating_control.control_setpoint == MINIMUM_SETPOINT
    assert heating_control.relative_modulation_value == heating_control._config.pwm.maximum_relative_modulation
    assert heating_control.pwm_state.enabled is True
    assert heating_control.pwm_state.status is PWMStatus.OFF
    assert coordinator.active is False


async def test_continuous_uses_requested_without_boiler_temperature(heating_control):
    await heating_control.update(_make_demand(42.0))

    assert heating_control.control_setpoint == 42.0


async def test_continuous_follows_requested_at_or_above_boiler_temperature(heating_control, coordinator):
    await coordinator.async_set_boiler_temperature(35.0)
    await coordinator.async_set_heater_state(HeaterState.ON)

    await heating_control.update(_make_demand(40.0))

    assert heating_control.control_setpoint == 40.0


async def test_continuous_clamps_below_boiler_temperature(heating_control, coordinator):
    await coordinator.async_set_boiler_temperature(55.0)
    await coordinator.async_set_heater_state(HeaterState.ON)

    await heating_control.update(_make_demand(40.0))

    expected = 55.0 - OPTIONS_DEFAULTS[CONF_FLOW_SETPOINT_OFFSET_CELSIUS]
    assert heating_control.control_setpoint == expected


async def test_continuous_allows_requested_above_offset(heating_control, coordinator):
    await coordinator.async_set_boiler_temperature(55.0)
    await coordinator.async_set_heater_state(HeaterState.ON)

    requested = 55.0 - OPTIONS_DEFAULTS[CONF_FLOW_SETPOINT_OFFSET_CELSIUS] + 0.5
    await heating_control.update(_make_demand(requested))

    assert heating_control.control_setpoint == requested


async def test_continuous_holds_previous_when_offset_rises(heating_control, coordinator):
    heating_control._control_setpoint = 45.0
    await coordinator.async_set_boiler_temperature(50.0)
    await coordinator.async_set_heater_state(HeaterState.ON)

    await heating_control.update(_make_demand(42.0))

    assert heating_control.control_setpoint == 45.0


async def test_continuous_allows_change_when_flame_off(heating_control, coordinator):
    heating_control._control_setpoint = 45.0
    await coordinator.async_set_boiler_temperature(50.0)
    await coordinator.async_set_heater_state(HeaterState.OFF)

    await heating_control.update(_make_demand(42.0))

    assert heating_control.control_setpoint == 42.0


async def test_continuous_allows_increase_when_requested_increases(heating_control, coordinator):
    heating_control._control_setpoint = 40.0
    await coordinator.async_set_boiler_temperature(50.0)
    await coordinator.async_set_heater_state(HeaterState.ON)

    await heating_control.update(_make_demand(41.0))

    expected = 50.0 - OPTIONS_DEFAULTS[CONF_FLOW_SETPOINT_OFFSET_CELSIUS]
    assert heating_control.control_setpoint == expected


async def test_pwm_suppression_applied(hass, coordinator, monkeypatch):
    _update_coordinator_config(coordinator)

    heating_control = SatHeatingControl(hass=hass, coordinator=coordinator, config=coordinator._config)
    _enable_pwm(heating_control, PWMStatus.ON)
    monkeypatch.setattr(heating_control._pwm, "update", lambda *args, **kwargs: None)

    await coordinator.async_set_heater_state(HeaterState.ON)
    await coordinator.async_set_boiler_temperature(50.0)
    heating_control._device_tracker._last_flame_on_at = timestamp() - (
            OPTIONS_DEFAULTS[CONF_MODULATION_SUPPRESSION_DELAY_SECONDS] + 1
    )

    await heating_control.update(_make_demand(40.0))

    assert heating_control.control_setpoint == 49.0


async def test_pwm_flame_off_return_offset(hass, monkeypatch, coordinator):
    _update_coordinator_config(coordinator)
    monkeypatch.setattr(type(coordinator), "return_temperature", property(lambda self: 30.0))

    heating_control = SatHeatingControl(hass=hass, coordinator=coordinator, config=coordinator._config)
    _enable_pwm(heating_control, PWMStatus.ON)
    monkeypatch.setattr(heating_control._pwm, "update", lambda *args, **kwargs: None)

    await coordinator.async_set_heater_state(HeaterState.OFF)

    await heating_control.update(_make_demand(40.0))

    assert heating_control.control_setpoint == 30.0 + OPTIONS_DEFAULTS[CONF_FLAME_OFF_SETPOINT_OFFSET_CELSIUS]


async def test_flame_off_setpoint_held_until_suppression_delay(hass, monkeypatch, coordinator):
    _update_coordinator_config(coordinator)
    monkeypatch.setattr(type(coordinator), "return_temperature", property(lambda self: 30.0))

    heating_control = SatHeatingControl(hass=hass, coordinator=coordinator, config=coordinator._config)
    _enable_pwm(heating_control, PWMStatus.ON)
    monkeypatch.setattr(heating_control._pwm, "update", lambda *args, **kwargs: None)

    await coordinator.async_set_heater_state(HeaterState.OFF)

    await heating_control.update(_make_demand(40.0))

    assert heating_control.control_setpoint == 30.0 + OPTIONS_DEFAULTS[CONF_FLAME_OFF_SETPOINT_OFFSET_CELSIUS]

    await coordinator.async_set_heater_state(HeaterState.ON)
    await coordinator.async_set_boiler_temperature(50.0)
    heating_control._device_tracker._last_flame_on_at = timestamp() - (
            OPTIONS_DEFAULTS[CONF_MODULATION_SUPPRESSION_DELAY_SECONDS] - 1
    )

    await heating_control.update(_make_demand(40.0))

    assert heating_control.control_setpoint == 30.0 + OPTIONS_DEFAULTS[CONF_FLAME_OFF_SETPOINT_OFFSET_CELSIUS]


async def test_enables_pwm_on_sustained_overshoot(heating_control, coordinator):
    await coordinator.async_set_heater_state(HeaterState.ON)
    await coordinator.async_set_boiler_temperature(40.0)

    requested = 30.0
    start_time = timestamp()

    await heating_control.update(HeatingDemand(
        timestamp=start_time,
        valves_open=True,
        hvac_mode=HVACMode.HEAT,
        outside_temperature=10.0,
        requested_setpoint=requested,
    ))

    assert heating_control.pwm_state.enabled is False

    await heating_control.update(HeatingDemand(
        valves_open=True,
        hvac_mode=HVACMode.HEAT,
        outside_temperature=10.0,
        requested_setpoint=requested,
        timestamp=start_time + OVERSHOOT_SUSTAIN_SECONDS + 1,
    ))

    assert heating_control.pwm_state.enabled is True


async def test_no_overshoot_while_the_flame_is_off(heating_control, coordinator, monkeypatch):
    monkeypatch.setattr(type(coordinator), "flame_active", property(lambda self: False))
    await coordinator.async_set_heater_state(HeaterState.ON)
    await coordinator.async_set_boiler_temperature(40.0)
    start_time = timestamp()

    for offset in (0.0, OVERSHOOT_SUSTAIN_SECONDS + 1):
        await heating_control.update(HeatingDemand(
            valves_open=True,
            hvac_mode=HVACMode.HEAT,
            requested_setpoint=30.0,
            outside_temperature=10.0,
            timestamp=start_time + offset,
        ))

    assert heating_control.pwm_state.enabled is False


async def test_modulation_is_minimum_when_pwm_active_and_supported(heating_control, monkeypatch):
    heating_control._coordinator.config.supports_relative_modulation_management = True
    _enable_pwm(heating_control, PWMStatus.ON)
    monkeypatch.setattr(heating_control._pwm, "update", lambda *args, **kwargs: None)

    await heating_control.update(_make_demand(40.0))

    assert heating_control.relative_modulation_value == MINIMUM_RELATIVE_MODULATION


async def test_modulation_is_minimum_when_pwm_enabled_and_idle(heating_control, monkeypatch):
    heating_control._coordinator.config.supports_relative_modulation_management = True
    _enable_pwm(heating_control, PWMStatus.IDLE)
    monkeypatch.setattr(heating_control._pwm, "update", lambda *args, **kwargs: None)

    await heating_control.update(_make_demand(40.0))

    assert heating_control.relative_modulation_value == MINIMUM_RELATIVE_MODULATION


async def test_modulation_stays_maximum_during_hot_water_when_supported(heating_control, monkeypatch):
    heating_control._coordinator.config.supports_relative_modulation_management = True
    _enable_pwm(heating_control, PWMStatus.ON)
    monkeypatch.setattr(type(heating_control._coordinator), "hot_water_active", property(lambda self: True))

    await heating_control.update(_make_demand(40.0))

    assert heating_control.relative_modulation_value == heating_control._config.pwm.maximum_relative_modulation


async def test_disables_pwm_on_sustained_underheat(heating_control, coordinator, monkeypatch):
    _enable_pwm(heating_control, PWMStatus.ON)
    monkeypatch.setattr(heating_control._pwm, "update", lambda *args, **kwargs: None)

    requested_setpoint = 40.0
    start_time = timestamp()

    await coordinator.async_set_heater_state(HeaterState.ON)
    await coordinator.async_set_boiler_temperature(requested_setpoint - 4.0)
    heating_control._device_tracker._last_flame_on_at = start_time - HEATER_STARTUP_TIMEFRAME

    await heating_control.update(HeatingDemand(
        timestamp=start_time,
        valves_open=True,
        hvac_mode=HVACMode.HEAT,
        requested_setpoint=requested_setpoint,
        outside_temperature=10.0,
    ))
    assert heating_control.pwm_state.enabled is True

    await heating_control.update(HeatingDemand(
        valves_open=True,
        hvac_mode=HVACMode.HEAT,
        requested_setpoint=requested_setpoint,
        outside_temperature=10.0,
        timestamp=start_time + UNDERHEAT_SUSTAIN_SECONDS + 1,
    ))
    assert heating_control.pwm_state.enabled is False


async def test_disables_pwm_on_sustained_saturation(heating_control, coordinator, monkeypatch):
    _enable_pwm(heating_control, PWMStatus.ON)
    monkeypatch.setattr(heating_control._pwm, "update", lambda *args, **kwargs: None)

    requested_setpoint = 40.0
    start_time = timestamp()

    await coordinator.async_set_heater_state(HeaterState.ON)
    await coordinator.async_set_boiler_temperature(requested_setpoint + 20.0)
    heating_control._pwm._duty_cycle = (300, 0)
    heating_control._pwm._effective_on_temperature = 44.0
    heating_control._pwm._floor_observed_at = start_time

    await heating_control.update(HeatingDemand(
        timestamp=start_time,
        valves_open=True,
        hvac_mode=HVACMode.HEAT,
        requested_setpoint=requested_setpoint,
        outside_temperature=10.0,
    ))
    assert heating_control.pwm_state.enabled is True

    await heating_control.update(HeatingDemand(
        valves_open=True,
        hvac_mode=HVACMode.HEAT,
        requested_setpoint=requested_setpoint,
        outside_temperature=10.0,
        timestamp=start_time + SATURATION_SUSTAIN_SECONDS + 1,
    ))
    assert heating_control.pwm_state.enabled is False


async def test_async_added_to_hass_replaces_existing_listeners(heating_control, monkeypatch):
    removed = {"coordinator": 0, "pwm_cycle": 0}
    calls = {"coordinator_add": 0, "pwm_listen": 0}

    heating_control._coordinator_listener_remove = lambda: removed.__setitem__("coordinator", removed["coordinator"] + 1)
    heating_control._pwm_cycle_listener_remove = lambda: removed.__setitem__("pwm_cycle", removed["pwm_cycle"] + 1)

    def _add_listener(callback):
        calls["coordinator_add"] += 1
        return lambda: None

    def _listen(_self, event_type, callback):
        calls["pwm_listen"] += 1
        return lambda: None

    heating_control._coordinator.async_add_listener = _add_listener
    monkeypatch.setattr(type(heating_control._hass.bus), "async_listen", _listen)

    await heating_control.async_added_to_hass()

    assert removed["coordinator"] == 1
    assert removed["pwm_cycle"] == 1
    assert calls["coordinator_add"] == 1
    assert calls["pwm_listen"] == 1


def _config_without_overshoot_protection() -> SatConfig:
    return SatConfig(
        entry_id="test",
        data={
            CONF_MODE: SatMode.FAKE.value,
            CONF_MINIMUM_SETPOINT: 40.0,
            CONF_OVERSHOOT_PROTECTION: False,
            CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS,
        },
        options={**OPTIONS_DEFAULTS},
    )


async def test_no_pwm_enable_on_sustained_overshoot_without_protection(heating_control, coordinator):
    heating_control._config = _config_without_overshoot_protection()

    await coordinator.async_set_heater_state(HeaterState.ON)
    await coordinator.async_set_boiler_temperature(40.0)

    requested = 30.0
    start_time = timestamp()

    await heating_control.update(HeatingDemand(
        timestamp=start_time,
        valves_open=True,
        hvac_mode=HVACMode.HEAT,
        outside_temperature=10.0,
        requested_setpoint=requested,
    ))

    await heating_control.update(HeatingDemand(
        valves_open=True,
        hvac_mode=HVACMode.HEAT,
        outside_temperature=10.0,
        requested_setpoint=requested,
        timestamp=start_time + OVERSHOOT_SUSTAIN_SECONDS + 1,
    ))

    assert heating_control.pwm_state.enabled is False


async def test_pwm_off_phase_forces_minimum_without_protection(heating_control, coordinator):
    heating_control._config = _config_without_overshoot_protection()

    await coordinator.async_set_boiler_temperature(50.0)

    _enable_pwm(heating_control, PWMStatus.OFF)
    heating_control._control_setpoint = 45.0

    await heating_control.update(_make_demand(45.0))

    assert heating_control.control_setpoint == MINIMUM_SETPOINT


async def test_force_pulse_width_modulation_keeps_pwm_enabled(heating_control, coordinator):
    heating_control._config = SatConfig(
        entry_id="test",
        data={
            CONF_MODE: SatMode.FAKE.value,
            CONF_MINIMUM_SETPOINT: 40.0,
            CONF_OVERSHOOT_PROTECTION: True,
            CONF_HEATING_SYSTEM: HeatingSystem.RADIATORS,
        },
        options={**OPTIONS_DEFAULTS, CONF_FORCE_PULSE_WIDTH_MODULATION: True},
    )

    await coordinator.async_set_boiler_temperature(50.0)
    await heating_control.update(_make_demand(45.0))

    assert heating_control.pwm_state.enabled is True

    await heating_control.update(_make_demand(heating_control._config.limits.maximum_setpoint + 1))

    assert heating_control.pwm_state.enabled is True


async def test_underheat_guard_ignores_flow_while_burner_is_off(heating_control, coordinator, monkeypatch):
    _enable_pwm(heating_control, PWMStatus.ON)
    monkeypatch.setattr(heating_control._pwm, "update", lambda *args, **kwargs: None)
    monkeypatch.setattr(type(coordinator), "flame_active", property(lambda self: False))

    requested_setpoint = 40.0
    start_time = timestamp()

    await coordinator.async_set_heater_state(HeaterState.ON)
    await coordinator.async_set_boiler_temperature(requested_setpoint - 4.0)

    for offset in (0.0, UNDERHEAT_SUSTAIN_SECONDS + 1, 2 * UNDERHEAT_SUSTAIN_SECONDS + 2):
        await heating_control.update(HeatingDemand(
            valves_open=True,
            hvac_mode=HVACMode.HEAT,
            requested_setpoint=requested_setpoint,
            outside_temperature=10.0,
            timestamp=start_time + offset,
        ))

    assert heating_control.pwm_state.enabled is True


async def test_underheat_guard_waits_for_burner_to_establish(heating_control, coordinator, monkeypatch):
    _enable_pwm(heating_control, PWMStatus.ON)
    monkeypatch.setattr(heating_control._pwm, "update", lambda *args, **kwargs: None)

    requested_setpoint = 40.0
    start_time = timestamp()

    await coordinator.async_set_heater_state(HeaterState.ON)
    await coordinator.async_set_boiler_temperature(requested_setpoint - 4.0)
    heating_control._device_tracker._last_flame_on_at = start_time
    heating_control._control_setpoint = requested_setpoint

    await heating_control.update(HeatingDemand(
        timestamp=start_time,
        valves_open=True,
        hvac_mode=HVACMode.HEAT,
        requested_setpoint=requested_setpoint,
        outside_temperature=10.0,
    ))

    await heating_control.update(HeatingDemand(
        valves_open=True,
        hvac_mode=HVACMode.HEAT,
        requested_setpoint=requested_setpoint,
        outside_temperature=10.0,
        timestamp=start_time + UNDERHEAT_SUSTAIN_SECONDS + 1,
    ))

    assert heating_control.pwm_state.enabled is True

    await heating_control.update(HeatingDemand(
        valves_open=True,
        hvac_mode=HVACMode.HEAT,
        requested_setpoint=requested_setpoint,
        outside_temperature=10.0,
        timestamp=start_time + HEATER_STARTUP_TIMEFRAME + UNDERHEAT_SUSTAIN_SECONDS + 2,
    ))

    assert heating_control.pwm_state.enabled is False


async def test_falling_request_does_not_count_as_overshoot(heating_control, coordinator):
    await coordinator.async_set_heater_state(HeaterState.ON)
    await coordinator.async_set_boiler_temperature(62.0)
    start_time = timestamp()

    await heating_control.update(HeatingDemand(
        timestamp=start_time,
        valves_open=True,
        hvac_mode=HVACMode.HEAT,
        outside_temperature=10.0,
        requested_setpoint=65.0,
    ))

    for offset in (60.0, 120.0, 180.0):
        await heating_control.update(HeatingDemand(
            valves_open=True,
            hvac_mode=HVACMode.HEAT,
            outside_temperature=10.0,
            requested_setpoint=55.0,
            timestamp=start_time + offset,
        ))

    assert heating_control.pwm_state.enabled is False

    await heating_control.update(HeatingDemand(
        valves_open=True,
        hvac_mode=HVACMode.HEAT,
        outside_temperature=10.0,
        requested_setpoint=55.0,
        timestamp=start_time + OVERSHOOT_REFERENCE_WINDOW_SECONDS + 1,
    ))
    await heating_control.update(HeatingDemand(
        valves_open=True,
        hvac_mode=HVACMode.HEAT,
        outside_temperature=10.0,
        requested_setpoint=55.0,
        timestamp=start_time + OVERSHOOT_REFERENCE_WINDOW_SECONDS + OVERSHOOT_SUSTAIN_SECONDS + 2,
    ))

    assert heating_control.pwm_state.enabled is True


async def test_reopened_valves_wait_out_the_off_phase(heating_control, coordinator):
    await coordinator.async_set_boiler_temperature(45.0)
    pwm = heating_control._pwm
    pwm._enabled = True
    pwm._effective_on_temperature = 50.0
    base_offset = pwm._heating_system.base_offset
    setpoint = base_offset + 0.5 * (50.0 - base_offset)

    now = timestamp()
    pwm._last_update = now - 400.0
    pwm._status = PWMStatus.ON
    pwm._cycle_starts = [now - 400.0]

    await heating_control.update(_make_demand(setpoint, valves_open=False))

    assert heating_control.pwm_state.status is PWMStatus.OFF
    assert pwm.cycles_this_hour == 1

    await heating_control.update(_make_demand(setpoint))

    assert heating_control.pwm_state.status is PWMStatus.OFF
    assert pwm.cycles_this_hour == 1


async def test_fresh_ignition_keeps_flame_off_hold_when_tracker_lags(hass, monkeypatch, coordinator):
    _update_coordinator_config(coordinator)
    monkeypatch.setattr(type(coordinator), "return_temperature", property(lambda self: 30.0))

    heating_control = SatHeatingControl(hass=hass, coordinator=coordinator, config=coordinator._config)
    _enable_pwm(heating_control, PWMStatus.ON)
    monkeypatch.setattr(heating_control._pwm, "update", lambda *args, **kwargs: None)

    await coordinator.async_set_heater_state(HeaterState.OFF)
    await heating_control.update(_make_demand(40.0))

    hold = 30.0 + OPTIONS_DEFAULTS[CONF_FLAME_OFF_SETPOINT_OFFSET_CELSIUS]
    assert heating_control.control_setpoint == hold

    await coordinator.async_set_heater_state(HeaterState.ON)
    await coordinator.async_set_boiler_temperature(41.0)
    heating_control._device_tracker._last_flame_on_at = timestamp() - 1500.0
    heating_control._device_tracker._last_flame_off_at = timestamp() - 200.0

    await heating_control.update(_make_demand(40.0))

    assert heating_control.control_setpoint == hold


async def test_fresh_ignition_without_hold_derives_it_from_return_temperature(hass, monkeypatch, coordinator):
    _update_coordinator_config(coordinator)
    monkeypatch.setattr(type(coordinator), "return_temperature", property(lambda self: 30.0))

    heating_control = SatHeatingControl(hass=hass, coordinator=coordinator, config=coordinator._config)
    _enable_pwm(heating_control, PWMStatus.ON)
    monkeypatch.setattr(heating_control._pwm, "update", lambda *args, **kwargs: None)

    await coordinator.async_set_heater_state(HeaterState.ON)
    await coordinator.async_set_boiler_temperature(41.0)
    heating_control._control_setpoint = MINIMUM_SETPOINT

    await heating_control.update(_make_demand(40.0))

    assert heating_control.control_setpoint == 30.0 + OPTIONS_DEFAULTS[CONF_FLAME_OFF_SETPOINT_OFFSET_CELSIUS]


async def test_pwm_flame_off_without_return_temperature_offsets_the_flow(hass, monkeypatch, coordinator):
    _update_coordinator_config(coordinator)
    monkeypatch.setattr(type(coordinator), "return_temperature", property(lambda self: None))

    heating_control = SatHeatingControl(hass=hass, coordinator=coordinator, config=coordinator._config)
    _enable_pwm(heating_control, PWMStatus.ON)
    monkeypatch.setattr(heating_control._pwm, "update", lambda *args, **kwargs: None)

    await coordinator.async_set_heater_state(HeaterState.OFF)
    await coordinator.async_set_boiler_temperature(35.0)
    heating_control._control_setpoint = MINIMUM_SETPOINT

    await heating_control.update(_make_demand(40.0))

    assert heating_control.control_setpoint == 35.0 + OPTIONS_DEFAULTS[CONF_FLAME_OFF_SETPOINT_OFFSET_CELSIUS]


async def test_fresh_ignition_without_return_temperature_holds_from_the_flow(hass, monkeypatch, coordinator):
    _update_coordinator_config(coordinator)
    monkeypatch.setattr(type(coordinator), "return_temperature", property(lambda self: None))

    heating_control = SatHeatingControl(hass=hass, coordinator=coordinator, config=coordinator._config)
    _enable_pwm(heating_control, PWMStatus.ON)
    monkeypatch.setattr(heating_control._pwm, "update", lambda *args, **kwargs: None)

    await coordinator.async_set_heater_state(HeaterState.ON)
    await coordinator.async_set_boiler_temperature(37.0)
    heating_control._control_setpoint = MINIMUM_SETPOINT

    await heating_control.update(_make_demand(40.0))

    assert heating_control.control_setpoint == 37.0 + OPTIONS_DEFAULTS[CONF_FLAME_OFF_SETPOINT_OFFSET_CELSIUS]


async def _saturated_demand(heating_control, coordinator, monkeypatch, target_changed_seconds_ago: float) -> None:
    _enable_pwm(heating_control, PWMStatus.ON)
    monkeypatch.setattr(heating_control._pwm, "update", lambda *args, **kwargs: None)

    await coordinator.async_set_heater_state(HeaterState.ON)
    await coordinator.async_set_boiler_temperature(60.0)
    heating_control._pwm._duty_cycle = (300, 0)

    now = timestamp()
    heating_control._pwm._effective_on_temperature = 44.0
    heating_control._pwm._floor_observed_at = now
    await heating_control.update(HeatingDemand(
        timestamp=now,
        valves_open=True,
        hvac_mode=HVACMode.HEAT,
        requested_setpoint=40.0,
        outside_temperature=10.0,
        target_temperature_changed_at=now - target_changed_seconds_ago,
    ))


async def test_saturation_right_after_a_target_change_leaves_pwm_at_once(heating_control, coordinator, monkeypatch):
    await _saturated_demand(heating_control, coordinator, monkeypatch, target_changed_seconds_ago=20.0)

    assert heating_control.pwm_state.enabled is False


async def test_saturation_long_after_a_target_change_still_waits(heating_control, coordinator, monkeypatch):
    await _saturated_demand(heating_control, coordinator, monkeypatch, target_changed_seconds_ago=TARGET_TEMPERATURE_CHANGE_WINDOW_SECONDS + 1)

    assert heating_control.pwm_state.enabled is True


async def test_burner_counts_as_recent_for_one_pwm_period(heating_control, coordinator, monkeypatch):
    monkeypatch.setattr(type(coordinator), "flame_active", property(lambda self: False))
    now = timestamp()
    period = heating_control._pwm.maximum_cycle_seconds

    assert heating_control.burned_within_pwm_period(now) is False

    heating_control._device_tracker._last_flame_off_at = now - period + 1.0
    assert heating_control.burned_within_pwm_period(now) is True

    heating_control._device_tracker._last_flame_off_at = now - period - 1.0
    assert heating_control.burned_within_pwm_period(now) is False

    monkeypatch.setattr(type(coordinator), "flame_active", property(lambda self: True))
    assert heating_control.burned_within_pwm_period(now) is True


def _demand_at(requested_setpoint: float, at: float, target_temperature_changed_at: Optional[float] = None) -> HeatingDemand:
    return HeatingDemand(
        timestamp=at,
        valves_open=True,
        hvac_mode=HVACMode.HEAT,
        outside_temperature=10.0,
        requested_setpoint=requested_setpoint,
        target_temperature_changed_at=target_temperature_changed_at,
    )


async def _prime_saturated_pwm(heating_control, coordinator, monkeypatch, floor: Optional[float], observed_at: Optional[float]) -> None:
    _enable_pwm(heating_control, PWMStatus.ON)
    monkeypatch.setattr(heating_control._pwm, "update", lambda *args, **kwargs: None)

    await coordinator.async_set_heater_state(HeaterState.ON)
    await coordinator.async_set_boiler_temperature(21.0)

    heating_control._pwm._duty_cycle = (2400, 0)
    heating_control._pwm._effective_on_temperature = floor
    heating_control._pwm._floor_observed_at = observed_at


async def test_saturation_without_learned_floor_keeps_pwm(heating_control, coordinator, monkeypatch):
    await _prime_saturated_pwm(heating_control, coordinator, monkeypatch, floor=None, observed_at=None)
    now = timestamp()

    await heating_control.update(_demand_at(32.0, now))
    await heating_control.update(_demand_at(32.0, now + SATURATION_SUSTAIN_SECONDS + 1))

    assert heating_control.pwm_state.enabled is True


async def test_saturation_after_target_change_without_floor_keeps_pwm(heating_control, coordinator, monkeypatch):
    await _prime_saturated_pwm(heating_control, coordinator, monkeypatch, floor=None, observed_at=None)
    now = timestamp()

    await heating_control.update(_demand_at(32.0, now, target_temperature_changed_at=now - 20.0))

    assert heating_control.pwm_state.enabled is True


async def test_saturation_on_a_prior_floor_needs_a_clear_margin(heating_control, coordinator, monkeypatch):
    await _prime_saturated_pwm(heating_control, coordinator, monkeypatch, floor=44.0, observed_at=None)
    now = timestamp()

    await heating_control.update(_demand_at(46.0, now, target_temperature_changed_at=now - 20.0))
    assert heating_control.pwm_state.enabled is True

    await heating_control.update(_demand_at(44.0 + OVERSHOOT_MARGIN_CELSIUS, now + 1.0, target_temperature_changed_at=now - 20.0))
    assert heating_control.pwm_state.enabled is False


async def test_saturation_on_an_observed_floor_uses_plain_duty(heating_control, coordinator, monkeypatch):
    now = timestamp()
    await _prime_saturated_pwm(heating_control, coordinator, monkeypatch, floor=44.0, observed_at=now)

    await heating_control.update(_demand_at(45.0, now))
    assert heating_control.pwm_state.enabled is True

    await heating_control.update(_demand_at(45.0, now + SATURATION_SUSTAIN_SECONDS + 1))
    assert heating_control.pwm_state.enabled is False


async def test_cold_ignition_keeps_pwm_with_a_known_floor(heating_control, coordinator, monkeypatch):
    flame = {"active": False}
    monkeypatch.setattr(type(coordinator), "flame_active", property(lambda self: flame["active"]))

    await coordinator.async_set_heater_state(HeaterState.ON)
    await coordinator.async_set_boiler_temperature(21.0)

    now = timestamp()
    pwm = heating_control._pwm
    pwm._enabled = True
    pwm._last_update = now
    pwm._effective_on_temperature = 44.0

    await heating_control.update(_demand_at(32.0, now, target_temperature_changed_at=now))
    assert heating_control.pwm_state.status is PWMStatus.ON

    await heating_control.update(_demand_at(32.0, now + SATURATION_SUSTAIN_SECONDS + 1, target_temperature_changed_at=now))
    assert heating_control.pwm_state.enabled is True

    flame["active"] = True
    await coordinator.async_set_boiler_temperature(27.0)

    await heating_control.update(_demand_at(32.0, now + SATURATION_SUSTAIN_SECONDS + 31, target_temperature_changed_at=now))
    assert heating_control.pwm_state.enabled is True
    assert heating_control.pwm_state.effective_on_temperature == 44.0

    target_changed_at = now + SATURATION_SUSTAIN_SECONDS + 41
    await heating_control.update(_demand_at(61.0, now + SATURATION_SUSTAIN_SECONDS + 61, target_temperature_changed_at=target_changed_at))
    assert heating_control.pwm_state.enabled is False


async def test_coordinator_release_switches_heating_off_once(coordinator, monkeypatch):
    await coordinator.async_set_heater_state(HeaterState.ON)
    await coordinator.async_set_control_setpoint(50.0)

    await coordinator.async_release()

    assert coordinator.released is True
    assert coordinator.active is False
    assert coordinator.setpoint == MINIMUM_SETPOINT

    release = AsyncMock()
    monkeypatch.setattr(coordinator, "_async_release", release)
    await coordinator.async_release()

    release.assert_not_awaited()


async def test_released_boiler_is_kept_released(heating_control, coordinator, monkeypatch):
    await heating_control.async_release()

    keep_released = AsyncMock()
    monkeypatch.setattr(coordinator, "_async_keep_released", keep_released)
    await heating_control.async_release()

    keep_released.assert_awaited_once()
    assert coordinator.released is True


async def test_commanding_again_ends_the_release(coordinator):
    await coordinator.async_release()
    await coordinator.async_set_control_setpoint(45.0)

    assert coordinator.released is False


async def test_undelivered_release_is_retried(coordinator, monkeypatch):
    monkeypatch.setattr(type(coordinator), "available", property(lambda self: False))

    await coordinator.async_release()

    assert coordinator.released is False


async def test_release_waits_for_an_update_in_flight(heating_control, coordinator, monkeypatch):
    sending, finish = asyncio.Event(), asyncio.Event()

    async def _slow_modulation(value):
        sending.set()
        await finish.wait()

    monkeypatch.setattr(coordinator, "async_set_control_max_relative_modulation", _slow_modulation)
    update = asyncio.create_task(heating_control.update(_make_demand(45.0)))
    await sending.wait()
    release = asyncio.create_task(heating_control.async_release())
    await asyncio.sleep(0)

    assert coordinator.released is False

    finish.set()
    await asyncio.gather(update, release)

    assert coordinator.released is True
    assert coordinator.active is False


async def test_backend_that_never_returns_does_not_block_the_next_command(heating_control, coordinator, monkeypatch):
    monkeypatch.setattr("custom_components.sat.heating_control.COMMAND_TIMEOUT_SECONDS", 0.01)

    async def _never_returns(value):
        await asyncio.Event().wait()

    with monkeypatch.context() as hanging:
        hanging.setattr(coordinator, "async_set_control_max_relative_modulation", _never_returns)

        with pytest.raises(TimeoutError):
            await heating_control.update(_make_demand(45.0))

    await heating_control.update(_make_demand(45.0))
    await heating_control.async_release()

    assert coordinator.released is True
