"""Sensor platform for SAT."""
import logging

from homeassistant.components.sensor import SensorEntity, ENTITY_ID_FORMAT
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import async_generate_entity_id

from . import SatDataUpdateCoordinator
from .const import SENSOR_INFO, DOMAIN, CONF_ID, TRANSLATE_SOURCE
from .entity import SatEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities):
    """Setup sensor platform."""
    sensors = []
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    for key, info in SENSOR_INFO.items():
        unit = info[1]
        device_class = info[0]
        status_sources = info[3]
        friendly_name_format = info[2]

        for source in status_sources:
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
        self._friendly_name = f"{friendly_name_format} ({TRANSLATE_SOURCE[source]})"

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_ID)}-{self._source}-{self._key}"

    @property
    def native_value(self):
        """Return the state of the device."""
        return self._coordinator.data[self._source].get(self._key)
