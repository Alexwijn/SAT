from typing import Optional, cast

from homeassistant.components import sensor
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import async_generate_entity_id
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SatSimulatorCoordinator
from ...entity import SatEntity
from ...entry_data import SatConfig, get_entry_data


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Setup sensor platform."""
    entry_data = get_entry_data(hass, config_entry.entry_id)
    coordinator = cast(SatSimulatorCoordinator, entry_data.coordinator)

    # Add all devices
    async_add_entities([
        SatSetpointSensor(coordinator, entry_data.config),
        SatBoilerTemperatureSensor(coordinator, entry_data.config),
    ])


class SatSetpointSensor(SatEntity, sensor.SensorEntity):
    def __init__(self, coordinator: SatSimulatorCoordinator, config: SatConfig):
        super().__init__(coordinator, config)
        self.entity_id = async_generate_entity_id(
            sensor.DOMAIN + ".{}", f"{self._config.name_lower}_setpoint", hass=coordinator.hass
        )

    @property
    def name(self) -> str:
        """Return the friendly name of the sensor."""
        return f"Current Setpoint {self._config.name} (Boiler)"

    @property
    def device_class(self) -> sensor.SensorDeviceClass:
        """Return the device class."""
        return sensor.SensorDeviceClass.TEMPERATURE

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement in native units."""
        return "°C"

    @property
    def available(self) -> bool:
        """Return availability of the sensor."""
        return self._coordinator.setpoint is not None

    @property
    def native_value(self) -> Optional[float]:
        """Return the state of the device."""
        return self._coordinator.setpoint

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config.name_lower}-setpoint"


class SatBoilerTemperatureSensor(SatEntity, sensor.SensorEntity):
    def __init__(self, coordinator: SatSimulatorCoordinator, config: SatConfig):
        super().__init__(coordinator, config)
        self.entity_id = async_generate_entity_id(
            sensor.DOMAIN + ".{}", f"{self._config.name_lower}_boiler_temperature", hass=coordinator.hass
        )

    @property
    def name(self) -> str:
        """Return the friendly name of the sensor."""
        return f"Current Temperature {self._config.name} (Boiler)"

    @property
    def device_class(self) -> sensor.SensorDeviceClass:
        """Return the device class."""
        return sensor.SensorDeviceClass.TEMPERATURE

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement in native units."""
        return "°C"

    @property
    def available(self) -> bool:
        """Return availability of the sensor."""
        return self._coordinator.boiler_temperature is not None

    @property
    def native_value(self) -> Optional[float]:
        """Return the state of the device."""
        return self._coordinator.boiler_temperature

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config.name_lower}-boiler_temperature"
