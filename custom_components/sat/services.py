from __future__ import annotations

import typing

from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, HVACMode
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from homeassistant.core import ServiceCall

from . import async_reload_entry
from .const import *
from .coordinator import SatDataUpdateCoordinator

if typing.TYPE_CHECKING:
    from .climate import SatClimate


async def set_overshoot_protection_value(coordinator: SatDataUpdateCoordinator, climate: SatClimate, call: ServiceCall):
    """Service to set the overshoot protection value."""
    coordinator.store.update(STORAGE_OVERSHOOT_PROTECTION_VALUE, call.data.get("value"))
    climate.async_write_ha_state()


async def start_overshoot_protection_calculation(coordinator: SatDataUpdateCoordinator, climate: SatClimate, call: ServiceCall):
    """Service to start the overshoot protection calculation process.

    This process will activate overshoot protection by turning on the heater and setting the control setpoint to
    a fixed value. Then, it will collect return water temperature data and calculate the mean of the last 3 data
    points. If the difference between the current return water temperature and the mean is small, it will
    deactivate overshoot protection and store the calculated value.
    """
    if climate.overshoot_protection_calculate:
        coordinator.logger.warning("[Overshoot Protection] Calculation already in progress.")
        return

    climate.overshoot_protection_calculate = True

    from .coordinator import DeviceState
    await coordinator.async_set_heater_state(DeviceState.ON)

    saved_hvac_mode = climate.hvac_mode
    saved_target_temperature = climate.target_temperature

    saved_target_temperatures = {}
    for entity_id in coordinator.store.options.get(CONF_CLIMATES):
        if state := climate.hass.states.get(entity_id):
            saved_target_temperatures[entity_id] = float(state.attributes.get(ATTR_TEMPERATURE))

        data = {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: 30}
        await climate.hass.services.async_call(CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, data, blocking=True)

    await climate.async_set_target_temperature(30)
    await climate.async_set_hvac_mode(HVACMode.HEAT)

    await climate.async_send_notification(
        title="Overshoot Protection Calculation",
        message="Calculation started. This process will run for at least 20 minutes until a stable boiler water temperature is found."
    )

    from .overshoot_protection import OvershootProtection
    overshoot_protection_value = await OvershootProtection(coordinator).calculate(call.data.get("solution"))
    climate.overshoot_protection_calculate = False

    await climate.async_set_hvac_mode(saved_hvac_mode)
    await climate.async_set_target_temperature(saved_target_temperature)

    await coordinator.async_set_control_max_setpoint(coordinator.maximum_setpoint)

    for entity_id in coordinator.store.options.get(CONF_CLIMATES):
        data = {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: saved_target_temperatures[entity_id]}
        await climate.hass.services.async_call(CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, data, blocking=True)

    if overshoot_protection_value is None:
        await climate.async_send_notification(
            title="Overshoot Protection Calculation",
            message=f"Timed out waiting for stable temperature"
        )

        return

    await climate.async_send_notification(
        title="Overshoot Protection Calculation",
        message=f"Finished calculating. Result: {round(overshoot_protection_value, 1)}"
    )

    # Store the new value
    coordinator.store.update(STORAGE_OVERSHOOT_PROTECTION_VALUE, overshoot_protection_value)

    # Reload the system
    await async_reload_entry(coordinator.hass, coordinator.config_entry)
