import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Config, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pyotgw import OpenThermGateway

from .const import (
    CONF_ID,
    CONF_DEVICE,
    DOMAIN,
    SENSOR,
    CLIMATE,
    BINARY_SENSOR,
    CONF_UNDERFLOOR,
    CONF_RADIATOR_LOW_TEMPERATURES,
    CONF_RADIATOR_HIGH_TEMPERATURES, COORDINATOR,
)

SCAN_INTERVAL = timedelta(seconds=30)

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup(hass: HomeAssistant, config: Config):
    """Set up this integration using YAML is not supported."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up this integration using UI."""

    if hass.data.get(DOMAIN) is None:
        hass.data.setdefault(DOMAIN, {})

    client = OpenThermGateway()
    await client.connect(entry.data.get(CONF_DEVICE))

    coordinator = SatDataUpdateCoordinator(hass, client=client)
    await coordinator.async_refresh()

    if not coordinator.last_update_success:
        raise ConfigEntryNotReady

    hass.data[DOMAIN][entry.entry_id] = {
        COORDINATOR: coordinator,
        CLIMATE: None
    }

    await hass.async_add_job(hass.config_entries.async_forward_entry_setup(entry, CLIMATE))
    await hass.async_add_job(hass.config_entries.async_forward_entry_setup(entry, SENSOR))
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
        hass.data[DOMAIN].pop(entry.entry_id)

    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


class SatDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the OTGW Gateway."""

    def __init__(self, hass: HomeAssistant, client: OpenThermGateway) -> None:
        """Initialize."""
        self.api = client

        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)

    async def _async_update_data(self):
        """Update data via library."""
        try:
            return await self.api.get_status()
        except Exception as exception:
            raise UpdateFailed() from exception

    @staticmethod
    def calculate_heating_curve_value(
            heating_system: str,
            curve_move: float,
            heating_curve_move: float,
            outside_temperature: float = None
    ):

        system = 0
        if heating_system == CONF_UNDERFLOOR:
            system = 8

        if outside_temperature is None:
            outside_temperature = 1

        if heating_curve_move == 0.1:
            return curve_move - system + 36.4 - (0.00495 * outside_temperature ** 2) - (0.32 * outside_temperature)

        if heating_curve_move == 0.2:
            return curve_move - system + 37.7 - (0.0052 * outside_temperature ** 2) - (0.38 * outside_temperature)

        if heating_curve_move == 0.3:
            return curve_move - system + 39.0 - (0.00545 * outside_temperature ** 2) - (0.44 * outside_temperature)

        if heating_curve_move == 0.4:
            return curve_move - system + 40.3 - (0.0057 * outside_temperature ** 2) - (0.5 * outside_temperature)

        if heating_curve_move == 0.5:
            return curve_move - system + 41.6 - (0.00595 * outside_temperature ** 2) - (0.56 * outside_temperature)

        if heating_curve_move == 0.6:
            return curve_move - system + 43.1 - (0.0067 * outside_temperature ** 2) - (0.62 * outside_temperature)

        if heating_curve_move == 0.7:
            return curve_move - system + 44.6 - (0.00745 * outside_temperature ** 2) - (0.68 * outside_temperature)

        if heating_curve_move == 0.8:
            return curve_move - system + 46.1 - (0.0082 * outside_temperature ** 2) - (0.74 * outside_temperature)

        if heating_curve_move == 0.9:
            return curve_move - system + 47.6 - (0.00895 * outside_temperature ** 2) - (0.8 * outside_temperature)

        if heating_curve_move == 1.0:
            return curve_move - system + 49.1 - (0.0097 * outside_temperature ** 2) - (0.86 * outside_temperature)

        if heating_curve_move == 1.1:
            return curve_move - system + 50.8 - (0.01095 * outside_temperature ** 2) - (0.92 * outside_temperature)

        if heating_curve_move == 1.2:
            return curve_move - system + 52.5 - (0.0122 * outside_temperature ** 2) - (0.98 * outside_temperature)

        if heating_curve_move == 1.3:
            return curve_move - system + 54.2 - (0.01345 * outside_temperature ** 2) - (1.04 * outside_temperature)

        if heating_curve_move == 1.4:
            return curve_move - system + 55.9 - (0.0147 * outside_temperature ** 2) - (1.1 * outside_temperature)

        if heating_curve_move == 1.5:
            return curve_move - system + 57.5 - (0.0157 * outside_temperature ** 2) - (1.16 * outside_temperature)

        if heating_curve_move == 1.6:
            return curve_move - system + 59.4 - (0.01644 * outside_temperature ** 2) - (1.24 * outside_temperature)

        if heating_curve_move == 1.7:
            return curve_move - system + 61.3 - (0.01718 * outside_temperature ** 2) - (1.32 * outside_temperature)

        if heating_curve_move == 1.8:
            return curve_move - system + 63.2 - (0.01792 * outside_temperature ** 2) - (1.4 * outside_temperature)

        if heating_curve_move == 1.9:
            return curve_move - system + 65.1 - (0.01866 * outside_temperature ** 2) - (1.48 * outside_temperature)

        if heating_curve_move == 2.0:
            return curve_move - system + 67.0 - (0.0194 * outside_temperature ** 2) - (1.56 * outside_temperature)

        if heating_curve_move == 2.1:
            return curve_move - system + 69.1 - (0.0197 * outside_temperature ** 2) - (1.66 * outside_temperature)

        if heating_curve_move == 2.2:
            return curve_move - system + 71.2 - (0.01995 * outside_temperature ** 2) - (1.76 * outside_temperature)

        if heating_curve_move == 2.3:
            return curve_move - system + 73.3 - (0.0202 * outside_temperature ** 2) - (1.86 * outside_temperature)

        if heating_curve_move == 2.4:
            return curve_move - system + 75.4 - (0.02045 * outside_temperature ** 2) - (1.96 * outside_temperature)

        if heating_curve_move == 2.5:
            return curve_move - system + 77.5 - (0.02007 * outside_temperature ** 2) - (2.06 * outside_temperature)

        if heating_curve_move == 2.6:
            return curve_move - system + 79.8 - (0.02045 * outside_temperature ** 2) - (2.18 * outside_temperature)

        if heating_curve_move == 2.7:
            return curve_move - system + 82.1 - (0.0202 * outside_temperature ** 2) - (2.3 * outside_temperature)

        if heating_curve_move == 2.8:
            return curve_move - system + 84.4 - (0.01995 * outside_temperature ** 2) - (2.42 * outside_temperature)

        if heating_curve_move == 2.9:
            return curve_move - system + 86.7 - (0.0197 * outside_temperature ** 2) - (2.54 * outside_temperature)

        if heating_curve_move == 3.0:
            return curve_move - system + 89.0 - (0.01945 * outside_temperature ** 2) - (2.66 * outside_temperature)

    @staticmethod
    def calculate_control_setpoint(
            heating_system: str,
            heating_curve: float,
            proportional: float,
            integral: float,
            derivative: float
    ):
        setpoint = (heating_curve + proportional + integral + derivative)

        if setpoint < 10:
            return 10.0
        elif setpoint > 75 and heating_system == CONF_UNDERFLOOR:
            return 75.0
        elif setpoint > 55 and heating_system == CONF_RADIATOR_LOW_TEMPERATURES:
            return 55.0
        elif setpoint > 50 and heating_system == CONF_RADIATOR_HIGH_TEMPERATURES:
            return 50.0

        return round(setpoint, 1)
