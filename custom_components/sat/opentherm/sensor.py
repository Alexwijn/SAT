"""Sensor platform for SAT."""
import logging

from homeassistant.components.sensor import SensorEntity, ENTITY_ID_FORMAT
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import async_generate_entity_id

from .coordinator import SatOpenThermCoordinator
from ..const import *
from ..entity import SatEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities):
    """Setup sensor platform."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]
    has_thermostat = coordinator.data[gw_vars.OTGW].get(gw_vars.OTGW_THRM_DETECT) != "D"

    # Create list of devices to be added
    devices = [SatCurrentPowerSensor(coordinator, config_entry)]

    # Iterate through sensor information
    for key, info in SENSOR_INFO.items():
        unit = info[1]
        device_class = info[0]
        status_sources = info[3]
        friendly_name_format = info[2]

        # Check if the sensor should be added based on its availability and thermostat presence
        for source in status_sources:
            if source == gw_vars.THERMOSTAT and has_thermostat is False:
                continue

            if coordinator.data[source].get(key) is not None:
                devices.append(SatSensor(coordinator, config_entry, key, source, device_class, unit, friendly_name_format))

    # Add all devices
    async_add_entities(devices)


class SatSensor(SatEntity, SensorEntity):
    def __init__(self, coordinator: SatOpenThermCoordinator, config_entry: ConfigEntry, key: str, source: str, device_class: str, unit: str, friendly_name_format: str):
        super().__init__(coordinator, config_entry)

        self.entity_id = async_generate_entity_id(
            ENTITY_ID_FORMAT, f"{config_entry.data.get(CONF_NAME).lower()}_{source}_{key}", hass=coordinator.hass
        )

        self._key = key
        self._unit = unit
        self._source = source
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
    def available(self):
        """Return availability of the sensor."""
        return self._coordinator.data is not None and self._coordinator.data[self._source] is not None

    @property
    def native_value(self):
        """Return the state of the device."""
        value = self._coordinator.data[self._source].get(self._key)
        if isinstance(value, float):
            value = f"{value:2.1f}"

        return value

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-{self._source}-{self._key}"


class SatCurrentPowerSensor(SatEntity, SensorEntity):

    def __init__(self, coordinator: SatOpenThermCoordinator, config_entry: ConfigEntry):
        super().__init__(coordinator, config_entry)

    @property
    def name(self) -> str | None:
        return f"Boiler Current Power {self._config_entry.data.get(CONF_NAME)} (Boiler)"

    @property
    def device_class(self):
        """Return the device class."""
        return SensorDeviceClass.POWER

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return UnitOfPower.KILO_WATT

    @property
    def available(self):
        """Return availability of the sensor."""
        return self._coordinator.data is not None and self._coordinator.data[gw_vars.BOILER] is not None

    @property
    def native_value(self) -> float:
        """Return the state of the device in native units.

        In this case, the state represents the current capacity of the boiler in kW.
        """
        # Get the data of the boiler from the coordinator
        boiler = self._coordinator.data[gw_vars.BOILER]

        # If the flame is off, return 0 kW
        if bool(boiler.get(gw_vars.DATA_SLAVE_FLAME_ON)) is False:
            return 0

        # Get the relative modulation level from the data
        relative_modulation = float(boiler.get(gw_vars.DATA_REL_MOD_LEVEL) or 0)

        # Get the maximum capacity from the data
        if (maximum_capacity := float(boiler.get(gw_vars.DATA_SLAVE_MAX_CAPACITY) or 0)) == 0:
            return 0

        # Get and calculate the minimum capacity from the data
        minimum_capacity = maximum_capacity / (100 / float(boiler.get(gw_vars.DATA_SLAVE_MIN_MOD_LEVEL)))

        # Calculate and return the current capacity in kW
        return minimum_capacity + (((maximum_capacity - minimum_capacity) / 100) * relative_modulation)

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-boiler-current-power"
