import asyncio
import logging
from datetime import timedelta

from homeassistant.components.climate import HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Config, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pyotgw import OpenThermGateway
from serial import SerialException

from .const import *
from .pid import PID

HOT_TOLERANCE = 0.5
COLD_TOLERANCE = 0.1
SCAN_INTERVAL = timedelta(seconds=15)

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: Config):
    """Set up this integration using YAML is not supported."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up this integration using UI."""

    if hass.data.get(DOMAIN) is None:
        hass.data.setdefault(DOMAIN, {})

    try:
        client = OpenThermGateway()
        await client.connect(port=entry.data.get(CONF_DEVICE), timeout=5)
    except (asyncio.TimeoutError, ConnectionError, SerialException) as ex:
        raise ConfigEntryNotReady(f"Could not connect to gateway at {entry.data.get(CONF_DEVICE)}: {ex}") from ex

    coordinator = SatCoordinator(hass, client, entry)
    hass.data[DOMAIN][entry.entry_id] = {COORDINATOR: coordinator, CLIMATES: []}

    await coordinator.async_refresh()

    if not coordinator.last_update_success:
        raise ConfigEntryNotReady

    await hass.async_add_job(hass.config_entries.async_forward_entry_setup(entry, SENSOR))
    await hass.async_add_job(hass.config_entries.async_forward_entry_setup(entry, CLIMATE))
    await hass.async_add_job(hass.config_entries.async_forward_entry_setup(entry, BINARY_SENSOR))

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    unloaded = all(
        await asyncio.gather(
            hass.config_entries.async_forward_entry_unload(entry, CLIMATE),
            hass.config_entries.async_forward_entry_unload(entry, SENSOR),
            hass.config_entries.async_forward_entry_unload(entry, BINARY_SENSOR)
        )
    )

    if unloaded:
        coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]
        await coordinator.unload_entry()

        hass.data[DOMAIN].pop(entry.entry_id)

    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


class SatCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, client: OpenThermGateway, config_entry: ConfigEntry) -> None:
        self.api = client

        options = OPTIONS_DEFAULTS.copy()
        options.update(config_entry.options)

        self._simulation = options.get(CONF_SIMULATION)
        self._curve_move = options.get(CONF_HEATING_CURVE)
        self._heating_system = options.get(CONF_HEATING_SYSTEM)
        self._heating_curve_move = options.get(CONF_HEATING_CURVE_MOVE)

        self._config_entry = config_entry
        self._outside_sensor_entity_id = config_entry.data.get(CONF_OUTSIDE_SENSOR_ENTITY_ID)

        self.setpoint = None
        self.heating_curve = None
        self.is_device_active = False
        self.outside_temperature = None

        super().__init__(hass, _LOGGER, name=COORDINATOR, update_interval=SCAN_INTERVAL)

    async def _async_update_data(self):
        try:
            data = await self.api.get_status()
        except Exception as exception:
            raise UpdateFailed() from exception

        await self._async_control_heating(data)

        return data

    async def _async_control_heating(self, data):
        if (climate := self.climate) is None:
            if self.is_device_active or data[gw_vars.BOILER].get(gw_vars.DATA_MASTER_CH_ENABLED):
                await self._async_control_heater(False)

            return

        too_cold = climate.target_temperature + COLD_TOLERANCE >= climate.current_temperature
        too_hot = climate.current_temperature >= climate.target_temperature + HOT_TOLERANCE

        if self.is_device_active:
            if climate.hvac_mode == HVACMode.OFF or too_hot:
                await self._async_control_heater(False)

            await self._async_control_setpoint(climate)
        else:
            if climate.hvac_mode == HVACMode.HEAT and too_cold:
                await self._async_control_heater(True)
                await self._async_control_setpoint(climate)
            elif data[gw_vars.BOILER](gw_vars.DATA_MASTER_CH_ENABLED):
                await self._async_control_heater(False)

    async def _async_control_heater(self, enabled: bool):
        if not self._simulation:
            await self.api.set_ch_enable_bit(int(enabled))

        self.is_device_active = enabled

        _LOGGER.info("Set central heating to %d", enabled)

    async def _async_control_setpoint(self, climate):
        if self.is_device_active:
            self.heating_curve = round(self._calculate_heating_curve_value(), 1)
            _LOGGER.info("Calculated heating curve: %d", self.heating_curve)

            self.setpoint = round(climate.calculate_control_setpoint(self.heating_curve), 1)
        else:
            self.setpoint = 10
            self.heating_curve = None

        if not self._simulation:
            await self.api.set_control_setpoint(self.setpoint)

        _LOGGER.info("Set control setpoint to %d", self.setpoint)

    @property
    def climate(self):
        current_climate = None
        climate_difference = 0
        climates = self._climates

        for climate in climates:
            if climate.hvac_mode == HVACMode.OFF:
                continue

            if climate.current_temperature >= climate.target_temperature + HOT_TOLERANCE:
                continue

            current_temperature = climate.target_temperature
            target_temperature = climate.current_temperature
            if current_temperature is None or target_temperature is None:
                continue

            difference = float(current_temperature) - float(target_temperature)
            if difference < 0:
                continue

            _LOGGER.debug(f"Found {climate.entity_id} with a difference of {difference}")

            if difference > climate_difference:
                current_climate = climate
                climate_difference = difference

        return current_climate

    def _calculate_heating_curve_value(self) -> float:
        system_offset = 0
        if self._heating_system == CONF_UNDERFLOOR:
            system_offset = 8

        if self._curve_move == 0.1:
            return self._heating_curve_move - system_offset + 36.4 - (0.00495 * self._outside_temperature ** 2) - (0.32 * self._outside_temperature)

        if self._curve_move == 0.2:
            return self._heating_curve_move - system_offset + 37.7 - (0.0052 * self._outside_temperature ** 2) - (0.38 * self._outside_temperature)

        if self._curve_move == 0.3:
            return self._heating_curve_move - system_offset + 39.0 - (0.00545 * self._outside_temperature ** 2) - (0.44 * self._outside_temperature)

        if self._curve_move == 0.4:
            return self._heating_curve_move - system_offset + 40.3 - (0.0057 * self._outside_temperature ** 2) - (0.5 * self._outside_temperature)

        if self._curve_move == 0.5:
            return self._heating_curve_move - system_offset + 41.6 - (0.00595 * self._outside_temperature ** 2) - (0.56 * self._outside_temperature)

        if self._curve_move == 0.6:
            return self._heating_curve_move - system_offset + 43.1 - (0.0067 * self._outside_temperature ** 2) - (0.62 * self._outside_temperature)

        if self._curve_move == 0.7:
            return self._heating_curve_move - system_offset + 44.6 - (0.00745 * self._outside_temperature ** 2) - (0.68 * self._outside_temperature)

        if self._curve_move == 0.8:
            return self._heating_curve_move - system_offset + 46.1 - (0.0082 * self._outside_temperature ** 2) - (0.74 * self._outside_temperature)

        if self._curve_move == 0.9:
            return self._heating_curve_move - system_offset + 47.6 - (0.00895 * self._outside_temperature ** 2) - (0.8 * self._outside_temperature)

        if self._curve_move == 1.0:
            return self._heating_curve_move - system_offset + 49.1 - (0.0097 * self._outside_temperature ** 2) - (0.86 * self._outside_temperature)

        if self._curve_move == 1.1:
            return self._heating_curve_move - system_offset + 50.8 - (0.01095 * self._outside_temperature ** 2) - (0.92 * self._outside_temperature)

        if self._curve_move == 1.2:
            return self._heating_curve_move - system_offset + 52.5 - (0.0122 * self._outside_temperature ** 2) - (0.98 * self._outside_temperature)

        if self._curve_move == 1.3:
            return self._heating_curve_move - system_offset + 54.2 - (0.01345 * self._outside_temperature ** 2) - (1.04 * self._outside_temperature)

        if self._curve_move == 1.4:
            return self._heating_curve_move - system_offset + 55.9 - (0.0147 * self._outside_temperature ** 2) - (1.1 * self._outside_temperature)

        if self._curve_move == 1.5:
            return self._heating_curve_move - system_offset + 57.5 - (0.0157 * self._outside_temperature ** 2) - (1.16 * self._outside_temperature)

        if self._curve_move == 1.6:
            return self._heating_curve_move - system_offset + 59.4 - (0.01644 * self._outside_temperature ** 2) - (1.24 * self._outside_temperature)

        if self._curve_move == 1.7:
            return self._heating_curve_move - system_offset + 61.3 - (0.01718 * self._outside_temperature ** 2) - (1.32 * self._outside_temperature)

        if self._curve_move == 1.8:
            return self._heating_curve_move - system_offset + 63.2 - (0.01792 * self._outside_temperature ** 2) - (1.4 * self._outside_temperature)

        if self._curve_move == 1.9:
            return self._heating_curve_move - system_offset + 65.1 - (0.01866 * self._outside_temperature ** 2) - (1.48 * self._outside_temperature)

        if self._curve_move == 2.0:
            return self._heating_curve_move - system_offset + 67.0 - (0.0194 * self._outside_temperature ** 2) - (1.56 * self._outside_temperature)

        if self._curve_move == 2.1:
            return self._heating_curve_move - system_offset + 69.1 - (0.0197 * self._outside_temperature ** 2) - (1.66 * self._outside_temperature)

        if self._curve_move == 2.2:
            return self._heating_curve_move - system_offset + 71.2 - (0.01995 * self._outside_temperature ** 2) - (1.76 * self._outside_temperature)

        if self._curve_move == 2.3:
            return self._heating_curve_move - system_offset + 73.3 - (0.0202 * self._outside_temperature ** 2) - (1.86 * self._outside_temperature)

        if self._curve_move == 2.4:
            return self._heating_curve_move - system_offset + 75.4 - (0.02045 * self._outside_temperature ** 2) - (1.96 * self._outside_temperature)

        if self._curve_move == 2.5:
            return self._heating_curve_move - system_offset + 77.5 - (0.02007 * self._outside_temperature ** 2) - (2.06 * self._outside_temperature)

        if self._curve_move == 2.6:
            return self._heating_curve_move - system_offset + 79.8 - (0.02045 * self._outside_temperature ** 2) - (2.18 * self._outside_temperature)

        if self._curve_move == 2.7:
            return self._heating_curve_move - system_offset + 82.1 - (0.0202 * self._outside_temperature ** 2) - (2.3 * self._outside_temperature)

        if self._curve_move == 2.8:
            return self._heating_curve_move - system_offset + 84.4 - (0.01995 * self._outside_temperature ** 2) - (2.42 * self._outside_temperature)

        if self._curve_move == 2.9:
            return self._heating_curve_move - system_offset + 86.7 - (0.0197 * self._outside_temperature ** 2) - (2.54 * self._outside_temperature)

        if self._curve_move == 3.0:
            return self._heating_curve_move - system_offset + 89.0 - (0.01945 * self._outside_temperature ** 2) - (2.66 * self._outside_temperature)

    @property
    def _outside_temperature(self):
        return float(self.hass.states.get(self._outside_sensor_entity_id).state)

    @property
    def _climates(self):
        return self.hass.data[DOMAIN][self._config_entry.entry_id][CLIMATES]

    async def unload_entry(self):
        """Cleanup and disconnect."""
        await self.api.set_control_setpoint(0)
        await self.api.set_max_relative_mod("-")
        await self.api.disconnect()
