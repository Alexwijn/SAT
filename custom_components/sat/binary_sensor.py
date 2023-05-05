"""Binary Sensor platform for SAT."""
import logging

import pyotgw.vars as gw_vars
from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.components.binary_sensor import ENTITY_ID_FORMAT
from homeassistant.components.climate import HVACAction
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import async_generate_entity_id

from .climate import SatClimate
from .const import DOMAIN, COORDINATOR, CLIMATE, TRANSLATE_SOURCE, CONF_NAME, BINARY_SENSOR_INFO
from .entity import SatEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities):
    """Setup sensor platform."""
    climate = hass.data[DOMAIN][config_entry.entry_id][CLIMATE]
    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]
    has_thermostat = coordinator.data[gw_vars.OTGW].get(gw_vars.OTGW_THRM_DETECT) != "D"

    # Create list of devices to be added
    sensors = [
        SatControlSetpointSynchroSensor(coordinator, climate, config_entry),
        SatCentralHeatingSynchroSensor(coordinator, climate, config_entry),
    ]

    # Iterate through sensor information
    for key, info in BINARY_SENSOR_INFO.items():
        device_class = info[0]
        status_sources = info[2]
        friendly_name_format = info[1]

        # Check if the sensor should be added based on its availability and thermostat presence
        for source in status_sources:
            if source == gw_vars.THERMOSTAT and has_thermostat is False:
                continue

            if coordinator.data[source].get(key) is not None:
                sensors.append(SatBinarySensor(coordinator, config_entry, key, source, device_class, friendly_name_format))

    # Add all devices
    async_add_entities(sensors)


class SatBinarySensor(SatEntity, BinarySensorEntity):
    _attr_should_poll = False

    def __init__(
            self,
            coordinator,
            config_entry: ConfigEntry,
            key: str,
            source: str,
            device_class: str,
            friendly_name_format: str
    ):
        super().__init__(coordinator, config_entry)

        self.entity_id = async_generate_entity_id(
            ENTITY_ID_FORMAT, f"{config_entry.data.get(CONF_NAME).lower()}_{source}_{key}", hass=coordinator.hass
        )

        self._key = key
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
    def available(self):
        """Return availability of the sensor."""
        return self._coordinator.data is not None and self._coordinator.data[self._source] is not None

    @property
    def is_on(self):
        """Return the state of the device."""
        return self._coordinator.data[self._source].get(self._key)

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME.lower())}-{self._source}-{self._key}"


class SatControlSetpointSynchroSensor(SatEntity, BinarySensorEntity):

    def __init__(self, coordinator, climate: SatClimate, config_entry: ConfigEntry):
        super().__init__(coordinator, config_entry)

        self._coordinator = coordinator
        self._climate = climate

    @property
    def name(self):
        """Return the friendly name of the sensor."""
        return "Control Setpoint Synchro"

    @property
    def device_class(self):
        """Return the device class."""
        return BinarySensorDeviceClass.PROBLEM

    @property
    def available(self):
        """Return availability of the sensor."""
        if self._climate is None:
            return False

        if self._coordinator.data is None or self._coordinator.data[gw_vars.BOILER] is None:
            return False

        return True

    @property
    def is_on(self):
        """Return the state of the sensor."""
        boiler_setpoint = float(self._coordinator.data[gw_vars.BOILER].get(gw_vars.DATA_CONTROL_SETPOINT) or 0)
        climate_setpoint = float(self._climate.extra_state_attributes.get("setpoint") or boiler_setpoint)

        return not (
                self._climate.state_attributes.get("hvac_action") != HVACAction.HEATING or
                round(climate_setpoint, 1) == round(boiler_setpoint, 1)
        )

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-control-setpoint-synchro"


class SatCentralHeatingSynchroSensor(SatEntity, BinarySensorEntity):
    def __init__(self, coordinator, climate: SatClimate, config_entry: ConfigEntry) -> None:
        """Initialize the Central Heating Synchro sensor."""
        super().__init__(coordinator, config_entry)

        self._coordinator = coordinator
        self._climate = climate

    @property
    def name(self) -> str:
        """Return the friendly name of the sensor."""
        return "Central Heating Synchro"

    @property
    def device_class(self) -> str:
        """Return the device class."""
        return BinarySensorDeviceClass.PROBLEM

    @property
    def available(self) -> bool:
        """Return availability of the sensor."""
        if self._climate is None:
            return False

        if self._coordinator.data is None or self._coordinator.data[gw_vars.BOILER] is None:
            return False

        return True

    @property
    def is_on(self) -> bool:
        """Return the state of the sensor."""
        boiler = self._coordinator.data[gw_vars.BOILER]
        boiler_central_heating = bool(boiler.get(gw_vars.DATA_MASTER_CH_ENABLED))
        climate_hvac_action = self._climate.state_attributes.get("hvac_action")

        return not (
                (climate_hvac_action == HVACAction.OFF and not boiler_central_heating) or
                (climate_hvac_action == HVACAction.IDLE and not boiler_central_heating) or
                (climate_hvac_action == HVACAction.HEATING and boiler_central_heating)
        )

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-central-heating-synchro"
