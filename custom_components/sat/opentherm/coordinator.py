from __future__ import annotations

import typing
from typing import Optional, Any

from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, HVACMode
from homeassistant.components.notify import DOMAIN as NOTIFY_DOMAIN, SERVICE_PERSISTENT_NOTIFICATION
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.update_coordinator import UpdateFailed
from pyotgw import OpenThermGateway

from ..config_store import SatConfigStore
from ..const import *
from ..coordinator import DeviceState, SatDataUpdateCoordinator

if typing.TYPE_CHECKING:
    from ..climate import SatClimate


class SatOpenThermCoordinator(SatDataUpdateCoordinator):
    """Class to manage fetching data from the OTGW Gateway."""

    def __init__(self, hass: HomeAssistant, store: SatConfigStore, client: OpenThermGateway) -> None:
        """Initialize."""
        super().__init__(hass, store)

        self.api = client
        self.api.subscribe(self._async_coroutine)

        self._overshoot_protection = bool(self._store.options.get(CONF_OVERSHOOT_PROTECTION))
        self._force_pulse_width_modulation = bool(self._store.options.get(CONF_FORCE_PULSE_WIDTH_MODULATION))

    @property
    def supports_setpoint_management(self):
        return True

    def get(self, key: str) -> Optional[Any]:
        """Get the value for the given `key` from the boiler data.

        :param key: Key of the value to retrieve from the boiler data.
        :return: Value for the given key from the boiler data, or None if the boiler data or the value are not available.
        """
        return self.data[gw_vars.BOILER].get(key) if self.data[gw_vars.BOILER] else None

    async def async_added_to_hass(self, climate: SatClimate) -> None:
        """Run when entity about to be added."""
        await self._async_control_max_setpoint()

        if self._overshoot_protection and self._store.get(STORAGE_OVERSHOOT_PROTECTION_VALUE) is None:
            self._overshoot_protection = False

            await self.hass.services.async_call(NOTIFY_DOMAIN, SERVICE_PERSISTENT_NOTIFICATION, {
                "title": "Smart Autotune Thermostat",
                "message": "Disabled overshoot protection because no overshoot value has been found."
            })

        if self._force_pulse_width_modulation and self._store.get(STORAGE_OVERSHOOT_PROTECTION_VALUE) is None:
            self._force_pulse_width_modulation = False

            await self.hass.services.async_call(NOTIFY_DOMAIN, SERVICE_PERSISTENT_NOTIFICATION, {
                "title": "Smart Autotune Thermostat",
                "message": "Disabled forced pulse width modulation because no overshoot value has been found."
            })

        async def start_overshoot_protection_calculation(_call: ServiceCall):
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
                    saved_target_temperatures[entity_id] = float(state.attributes.get("temperature"))

                data = {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: 30}
                await self.hass.services.async_call(CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, data, blocking=True)

            await climate.async_set_target_temperature(30)
            await climate.async_set_hvac_mode(HVACMode.HEAT)

            await self.hass.services.async_call(NOTIFY_DOMAIN, SERVICE_PERSISTENT_NOTIFICATION, {
                "title": "Overshoot Protection Calculation",
                "message": "Calculation started. This process will run for at least 20 minutes until a stable boiler water temperature is found."
            })

            from .overshoot_protection import OvershootProtection
            overshoot_protection_value = await OvershootProtection(self).calculate(_call.data.get("solution"))
            self._overshoot_protection_calculate = False

            await climate.async_set_hvac_mode(saved_hvac_mode)

            await self._async_control_max_setpoint()
            await climate.async_set_target_temperature(saved_target_temperature)

            for entity_id in self._store.options.get(CONF_CLIMATES):
                data = {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: saved_target_temperatures[entity_id]}
                await self.hass.services.async_call(CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, data, blocking=True)

            if overshoot_protection_value is None:
                await self.hass.services.async_call(NOTIFY_DOMAIN, SERVICE_PERSISTENT_NOTIFICATION, {
                    "title": "Overshoot Protection Calculation",
                    "message": f"Timed out waiting for stable temperature"
                })
            else:
                await self.hass.services.async_call(NOTIFY_DOMAIN, SERVICE_PERSISTENT_NOTIFICATION, {
                    "title": "Overshoot Protection Calculation",
                    "message": f"Finished calculating. Result: {round(overshoot_protection_value, 1)}"
                })

                # Turn the overshoot protection settings back on
                self._overshoot_protection = bool(self._store.options.get(CONF_OVERSHOOT_PROTECTION))
                self._force_pulse_width_modulation = bool(self._store.options.get(CONF_FORCE_PULSE_WIDTH_MODULATION))

                # Store the new value
                self._store.update(STORAGE_OVERSHOOT_PROTECTION_VALUE, overshoot_protection_value)

        self.hass.services.async_register(DOMAIN, "start_overshoot_protection_calculation", start_overshoot_protection_calculation)

    async def cleanup(self) -> None:
        """Cleanup and disconnect."""
        self.api.unsubscribe(self._async_coroutine)

        await self.api.set_control_setpoint(0)
        await self.api.set_max_relative_mod("-")
        await self.api.disconnect()

    async def _async_update_data(self):
        """Update data via library."""
        try:
            return await self.api.get_status()
        except Exception as exception:
            raise UpdateFailed() from exception

    async def _async_coroutine(self, data):
        self.async_set_updated_data(data)

    async def async_control_heating(self, climate: SatClimate, _time=None) -> None:
        """Control the max relative mod of the heating system."""
        await super().async_control_heating(climate)

        if climate.hvac_mode == HVACMode.OFF and bool(self.get(gw_vars.DATA_MASTER_CH_ENABLED)):
            await self.async_set_heater_state(DeviceState.OFF)

        await self._async_control_max_relative_mod(climate)

    async def _async_control_max_relative_mod(self, climate: SatClimate, _time=None) -> None:
        max_relative_mod = self._calculate_max_relative_mod(climate)
        if float(self.get(gw_vars.DATA_SLAVE_MAX_RELATIVE_MOD)) == max_relative_mod:
            return

        if not self._simulation:
            await self.api.set_max_relative_mod(max_relative_mod)

        self.logger.info("Set max relative mod to %d", max_relative_mod)

    async def _async_control_max_setpoint(self) -> None:
        if not self._simulation:
            await self.api.set_max_ch_setpoint(self.maximum_setpoint)

        self.logger.info(f"Set max setpoint to {self.maximum_setpoint}")

    async def async_control_setpoint(self, value: float) -> None:
        if not self._simulation:
            await self.api.set_control_setpoint(value)

        await super().async_control_setpoint(value)

    async def async_set_heater_state(self, state: DeviceState) -> None:
        """Control the state of the central heating."""
        if not self._simulation:
            await self.api.set_ch_enable_bit(1 if state == DeviceState.ON else 0)

        await super().async_set_heater_state(state)

    def _calculate_max_relative_mod(self, climate: SatClimate) -> int:
        """Calculate the maximum relative modulation for the heating system.

        This method determines the maximum relative modulation that should be used for the heating system, based on the current
        climate conditions and system configuration. If the heating system is currently in heat mode, or if domestic hot water
        is active, or if the setpoint is below a certain minimum value, the maximum relative modulation is returned as a constant value.

        Otherwise, if overshoot protection is enabled and certain conditions are met, the maximum relative modulation is also set
        to a constant value. Otherwise, we return the minimum relative modulation.

        Args:
            climate: A `SatClimate` object representing the current climate conditions.

        Returns:
            An integer representing the maximum relative modulation for the heating system.
        """
        setpoint = float(self.get(gw_vars.DATA_CONTROL_SETPOINT))

        if climate.hvac_mode == HVACMode.HEAT or bool(self.get(gw_vars.DATA_SLAVE_DHW_ACTIVE)) or setpoint <= MINIMUM_SETPOINT:
            return MAXIMUM_RELATIVE_MOD

        if self._overshoot_protection and not self._force_pulse_width_modulation:
            overshoot_protection_value = self._store.get(STORAGE_OVERSHOOT_PROTECTION_VALUE)

            if overshoot_protection_value is None or (abs(climate.max_error) > 0.1 and setpoint >= (overshoot_protection_value - 2)):
                return MAXIMUM_RELATIVE_MOD

        return MINIMUM_RELATIVE_MOD
