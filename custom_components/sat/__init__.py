import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Config, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt
from pyotgw import OpenThermGateway
from serial import SerialException

from .const import *
from .pid import PID

SCAN_INTERVAL = timedelta(seconds=15)

_LOGGER: logging.Logger = logging.getLogger(__name__)


def create_pid_controller(options):
    sample_time = dt.parse_time(options.get(CONF_SAMPLE_TIME))
    sample_time_seconds = (sample_time.hour * 3600) + (sample_time.minute * 60) + sample_time.second

    if sample_time_seconds <= 0:
        sample_time_seconds = 0.01

    return PID(
        Kp=float(options.get(CONF_PROPORTIONAL)),
        Ki=float(options.get(CONF_INTEGRAL)),
        Kd=float(options.get(CONF_DERIVATIVE)),
        sample_time=sample_time_seconds
    )


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

    coordinator = SatDataUpdateCoordinator(hass, client=client)
    await coordinator.async_refresh()

    if not coordinator.last_update_success:
        raise ConfigEntryNotReady

    hass.data[DOMAIN][entry.entry_id] = {
        COORDINATOR: coordinator,
        CLIMATE: None,
        PID_CONTROLLERS: {}
    }

    await hass.async_add_job(hass.config_entries.async_forward_entry_setup(entry, CLIMATE))
    await hass.async_add_job(hass.config_entries.async_forward_entry_setup(entry, SENSOR))
    await hass.async_add_job(hass.config_entries.async_forward_entry_setup(entry, BINARY_SENSOR))

    climate = hass.data[DOMAIN][entry.entry_id][CLIMATE]
    hass.data[DOMAIN][entry.entry_id][PID_CONTROLLERS][climate.entity_id] = SatPIDController(
        hass, entry, climate.entity_id
    )

    if entry.options.get(CONF_ROOMS):
        for entity_id in entry.options.get(CONF_ROOMS):
            hass.data[DOMAIN][entry.entry_id][PID_CONTROLLERS][entity_id] = SatPIDController(
                hass, entry, entity_id
            )

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
        await coordinator.cleanup()

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

        super().__init__(hass, _LOGGER, name=COORDINATOR, update_interval=SCAN_INTERVAL)

    async def _async_update_data(self):
        """Update data via library."""
        try:
            return await self.api.get_status()
        except Exception as exception:
            raise UpdateFailed() from exception

    async def cleanup(self):
        """Cleanup and disconnect."""
        await self.api.set_control_setpoint(0)
        await self.api.set_max_relative_mod("-")
        await self.api.disconnect()


class SatPIDController(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, climate_id: str) -> None:
        super().__init__(hass, _LOGGER, name=PID_CONTROLLERS)

        self._climate_id = climate_id
        self.pid = create_pid_controller(config_entry.options)

        async_track_state_change_event(
            self.hass, [self._climate_id], self._async_state_changed
        )

    async def _async_state_changed(self, event):
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        old_state = event.data.get("old_state")

        if old_state.attributes.get("temperature") != new_state.attributes.get("temperature"):
            self.pid.setpoint = new_state.attributes.get("temperature")
            self.pid.reset()

        if old_state.attributes.get("current_temperature") != new_state.attributes.get("current_temperature"):
            self.pid(new_state.attributes.get('current_temperature'))
            _LOGGER.debug(f"{new_state.name} PID: {self.pid.components}")
