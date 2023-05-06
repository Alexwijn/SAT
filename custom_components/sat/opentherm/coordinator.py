from __future__ import annotations

import typing
from functools import partial
from typing import Optional, Any

from homeassistant.components.climate import HVACMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from pyotgw import OpenThermGateway

from .services import start_overshoot_protection_calculation
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

    async def async_added_to_hass(self, climate: SatClimate) -> None:
        """Run when entity about to be added."""
        await self._async_control_max_setpoint()

        if self._overshoot_protection and self._store.get(STORAGE_OVERSHOOT_PROTECTION_VALUE) is None:
            self._overshoot_protection = False

            await self.async_send_notification(
                title="Smart Autotune Thermostat",
                message="Disabled overshoot protection because no overshoot value has been found."
            )

        if self._force_pulse_width_modulation and self._store.get(STORAGE_OVERSHOOT_PROTECTION_VALUE) is None:
            self._force_pulse_width_modulation = False

            await self.async_send_notification(
                title="Smart Autotune Thermostat",
                message="Disabled forced pulse width modulation because no overshoot value has been found."
            )

        self.hass.services.async_register(
            DOMAIN,
            SERVICE_OVERSHOOT_PROTECTION_CALCULATION,
            partial(start_overshoot_protection_calculation, self, climate)
        )

    @property
    def supports_setpoint_management(self):
        """Control the setpoint temperature for the device."""
        return True

    def get(self, key: str) -> Optional[Any]:
        """Get the value for the given `key` from the boiler data.

        :param key: Key of the value to retrieve from the boiler data.
        :return: Value for the given key from the boiler data, or None if the boiler data or the value are not available.
        """
        return self.data[gw_vars.BOILER].get(key) if self.data[gw_vars.BOILER] else None

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

    async def async_control_heating_loop(self, climate: SatClimate, _time=None) -> None:
        """Control the max relative mod of the heating system."""
        await super().async_control_heating_loop(climate)

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
        """Set a maximum temperature limit on the boiler."""
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
