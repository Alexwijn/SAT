from homeassistant.components import sensor
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import async_generate_entity_id

from ..const import *
from ..entity import SatEntity
from ..simulator import SatSimulatorCoordinator


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities):
    """Setup sensor platform."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]

    # Add all devices
    async_add_entities([
        SatSetpointSensor(coordinator, config_entry),
        SatBoilerTemperatureSensor(coordinator, config_entry),
    ])


class SatSetpointSensor(SatEntity, sensor.SensorEntity):
    def __init__(self, coordinator: SatSimulatorCoordinator, config_entry: ConfigEntry):
        super().__init__(coordinator, config_entry)

        self._config_entry = config_entry
        self.entity_id = async_generate_entity_id(
            sensor.DOMAIN + ".{}", f"{config_entry.data.get(CONF_NAME).lower()}_setpoint", hass=coordinator.hass
        )

    @property
    def name(self) -> str:
        """Return the friendly name of the sensor."""
        return f"Current Setpoint {self._config_entry.data.get(CONF_NAME)} (Boiler)"

    @property
    def device_class(self):
        """Return the device class."""
        return sensor.SensorDeviceClass.TEMPERATURE

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement in native units."""
        return "°C"

    @property
    def available(self):
        """Return availability of the sensor."""
        return self._coordinator.setpoint is not None

    @property
    def native_value(self):
        """Return the state of the device."""
        return self._coordinator.setpoint

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-setpoint"


class SatBoilerTemperatureSensor(SatEntity, sensor.SensorEntity):
    def __init__(self, coordinator: SatSimulatorCoordinator, config_entry: ConfigEntry):
        super().__init__(coordinator, config_entry)

        self._config_entry = config_entry
        self.entity_id = async_generate_entity_id(
            sensor.DOMAIN + ".{}", f"{config_entry.data.get(CONF_NAME).lower()}_boiler_temperature", hass=coordinator.hass
        )

    @property
    def name(self) -> str:
        """Return the friendly name of the sensor."""
        return f"Current Temperature {self._config_entry.data.get(CONF_NAME)} (Boiler)"

    @property
    def device_class(self):
        """Return the device class."""
        return sensor.SensorDeviceClass.TEMPERATURE

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement in native units."""
        return "°C"

    @property
    def available(self):
        """Return availability of the sensor."""
        return self._coordinator.boiler_temperature is not None

    @property
    def native_value(self):
        """Return the state of the device."""
        return self._coordinator.boiler_temperature

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-boiler_temperature"
