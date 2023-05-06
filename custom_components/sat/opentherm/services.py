from __future__ import annotations

import typing

from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, HVACMode
from homeassistant.components.notify import DOMAIN as NOTIFY_DOMAIN, SERVICE_PERSISTENT_NOTIFICATION
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from homeassistant.core import ServiceCall

from ..const import *
from ..coordinator import DeviceState

if typing.TYPE_CHECKING:
    from ..climate import SatClimate


async def start_overshoot_protection_calculation(self, climate: SatClimate, call: ServiceCall):
    """Service to start the overshoot protection calculation process.

    This process will activate overshoot protection by turning on the heater and setting the control setpoint to
    a fixed value. Then, it will collect return water temperature data and calculate the mean of the last 3 data
    points. If the difference between the current return water temperature and the mean is small, it will
    deactivate overshoot protection and store the calculated value.
    """
    if self._overshoot_protection_calculate:
        self.logger.warning("[Overshoot Protection] Calculation already in progress.")
        return

    self._device_state = DeviceState.ON
    self._overshoot_protection_calculate = True

    saved_hvac_mode = climate.hvac_mode
    saved_target_temperature = climate.target_temperature

    saved_target_temperatures = {}
    for entity_id in self._store.options.get(CONF_CLIMATES):
        if state := self.hass.states.get(entity_id):
            saved_target_temperatures[entity_id] = float(state.attributes.get(ATTR_TEMPERATURE))

        data = {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: 30}
        await self.hass.services.async_call(CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, data, blocking=True)

    await climate.async_set_target_temperature(30)
    await climate.async_set_hvac_mode(HVACMode.HEAT)

    await self.hass.services.async_call(NOTIFY_DOMAIN, SERVICE_PERSISTENT_NOTIFICATION, {
        "title": "Overshoot Protection Calculation",
        "message": "Calculation started. This process will run for at least 20 minutes until a stable boiler water temperature is found."
    })

    from .overshoot_protection import OvershootProtection
    overshoot_protection_value = await OvershootProtection(self).calculate(call.data.get("solution"))
    self._overshoot_protection_calculate = False

    await climate.async_set_hvac_mode(saved_hvac_mode)

    await self._async_control_max_setpoint()
    await climate.async_set_target_temperature(saved_target_temperature)

    for entity_id in self._store.options.get(CONF_CLIMATES):
        data = {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: saved_target_temperatures[entity_id]}
        await self.hass.services.async_call(CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, data, blocking=True)

    if overshoot_protection_value is None:
        await self.async_send_notification(
            title="Overshoot Protection Calculation",
            message=f"Timed out waiting for stable temperature"
        )

        return

    await self.async_send_notification(
        title="Overshoot Protection Calculation",
        message=f"Finished calculating. Result: {round(overshoot_protection_value, 1)}"
    )

    # Turn the overshoot protection settings back on
    self._overshoot_protection = bool(self._store.options.get(CONF_OVERSHOOT_PROTECTION))
    self._force_pulse_width_modulation = bool(self._store.options.get(CONF_FORCE_PULSE_WIDTH_MODULATION))

    # Store the new value
    self._store.update(STORAGE_OVERSHOOT_PROTECTION_VALUE, overshoot_protection_value)
