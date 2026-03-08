"""Tests focused on heating control behavior."""

import pytest
from homeassistant.components.climate import HVACMode

from custom_components.sat.const import (
    COLD_SETPOINT,
    CONF_FLAME_OFF_SETPOINT_OFFSET_CELSIUS,
    CONF_FLOW_SETPOINT_OFFSET_CELSIUS,
    CONF_HEATING_SYSTEM,
    CONF_MINIMUM_SETPOINT,
    CONF_MODE,
    CONF_MODULATION_SUPPRESSION_DELAY_SECONDS,
    CONF_OVERSHOOT_PROTECTION,
    SATURATION_SUSTAIN_SECONDS,
    UNDERHEAT_SUSTAIN_SECONDS,
    HeatingSystem,
    MINIMUM_RELATIVE_MODULATION,
    MINIMUM_SETPOINT,
    OPTIONS_DEFAULTS,
)
from custom_components.sat.const import OVERSHOOT_SUSTAIN_SECONDS
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


async def test_setpoint_below_cold_disables_pwm_and_forces_minimum(heating_control, coordinator):
    _enable_pwm(heating_control, PWMStatus.ON)

    await heating_control.update(_make_demand(COLD_SETPOINT - 0.1))

    assert heating_control.control_setpoint == MINIMUM_SETPOINT
    assert heating_control.relative_modulation_value == heating_control._config.pwm.maximum_relative_modulation
    assert heating_control.pwm_state.enabled is False
    assert coordinator.active is False


async def test_closed_valves_force_minimum_and_disable_pwm(heating_control, coordinator):
    _enable_pwm(heating_control, PWMStatus.ON)

    await heating_control.update(_make_demand(45.0, valves_open=False))

    assert heating_control.control_setpoint == MINIMUM_SETPOINT
    assert heating_control.relative_modulation_value == heating_control._config.pwm.maximum_relative_modulation
    assert heating_control.pwm_state.enabled is False
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
