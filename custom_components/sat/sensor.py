"""Sensor platform for SAT."""
import logging

import pyotgw.vars as gw_vars
from homeassistant.components.sensor import SensorEntity, ENTITY_ID_FORMAT
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import async_generate_entity_id

from . import SatDataUpdateCoordinator
from .const import SENSOR_INFO, DOMAIN, COORDINATOR, CONF_ID, TRANSLATE_SOURCE, CONF_NAME
from .entity import SatEntity

_LOGGER = logging.getLogger(__package__)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities):
    """Setup sensor platform."""
    sensors = []
    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]
    has_thermostat = coordinator.data[gw_vars.OTGW].get(gw_vars.OTGW_THRM_DETECT) != "D"

    for key, info in SENSOR_INFO.items():
        unit = info[1]
        device_class = info[0]
        status_sources = info[3]
        friendly_name_format = info[2]

        for source in status_sources:
            if source == gw_vars.THERMOSTAT and has_thermostat is False:
                continue

            if coordinator.data[source].get(key) is not None:
                sensors.append(
                    SatSensor(
                        coordinator,
                        config_entry,
                        key,
                        source,
                        device_class,
                        unit,
                        friendly_name_format,
                    )
                )

    async_add_entities(sensors)


class SatSensor(SatEntity, SensorEntity):
    _attr_should_poll = False

    def __init__(
            self,
            coordinator: SatDataUpdateCoordinator,
            config_entry: ConfigEntry,
            key: str,
            source: str,
            device_class: str,
            unit: str,
            friendly_name_format: str
    ):
        super().__init__(coordinator, config_entry)

        self.entity_id = async_generate_entity_id(
            ENTITY_ID_FORMAT, f"{config_entry.data.get(CONF_ID)}_{source}_{key}", hass=coordinator.hass
        )

        self._key = key
        self._unit = unit
        self._source = source
        self._coordinator = coordinator
        self._device_class = device_class
        self._config_entry = config_entry

        if TRANSLATE_SOURCE[source] is not None:
            friendly_name_format = f"{friendly_name_format} ({TRANSLATE_SOURCE[source]})"

        self._friendly_name = friendly_name_format.format(config_entry.data.get(CONF_NAME))

    @property
    def name(self):
        """Return the friendly name of the sensor."""
        return self._friendly_name

    @property
    def device_class(self):
        """Return the device class."""
        return self._device_class

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return self._unit

    @property
    def native_value(self):
        """Return the state of the device."""
        return self._coordinator.data[self._source].get(self._key)

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_ID)}-{self._source}-{self._key}"
