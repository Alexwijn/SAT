from typing import Optional

from homeassistant.components.number import NumberDeviceClass, NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import SatDataUpdateCoordinator
from .entity import SatEntity
from .entry_data import SatConfig, get_entry_data
from .heating_control import SatHeatingControl


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities) -> None:
    entry_data = get_entry_data(hass, config_entry.entry_id)
    coordinator = entry_data.coordinator

    if coordinator.supports_maximum_setpoint_management:
        async_add_entities([SatMaximumSetpointEntity(coordinator, entry_data.config, entry_data.heating_control)])

    if coordinator.supports_hot_water_setpoint_management:
        async_add_entities([SatHotWaterSetpointEntity(coordinator, entry_data.config, entry_data.heating_control)])


class SatHotWaterSetpointEntity(SatEntity, NumberEntity):
    def __init__(self, coordinator: SatDataUpdateCoordinator, config: SatConfig, heating_control: SatHeatingControl):
        super().__init__(coordinator, config, heating_control)
        self._name = self._config.name

    @property
    def name(self) -> Optional[str]:
        return f"Hot Water Setpoint {self._name} (Device)"

    @property
    def device_class(self):
        """Return the device class."""
        return NumberDeviceClass.TEMPERATURE

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config.entry_id}-boiler-dhw-setpoint"

    @property
    def icon(self) -> Optional[str]:
        return "mdi:thermometer"

    @property
    def available(self):
        """Return availability of the sensor."""
        return self._coordinator.hot_water_setpoint is not None

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement in native units."""
        return "°C"

    @property
    def native_value(self):
        """Return the state of the device in native units."""
        return self._coordinator.hot_water_setpoint

    @property
    def native_min_value(self) -> float:
        """Return the minimum accepted temperature."""
        return 30

    @property
    def native_max_value(self) -> float:
        """Return the maximum accepted temperature."""
        return 60

    async def async_set_native_value(self, value: float) -> None:
        """Update the setpoint."""
        await self._coordinator.async_set_control_hot_water_setpoint(value)


class SatMaximumSetpointEntity(SatEntity, NumberEntity):
    def __init__(self, coordinator: SatDataUpdateCoordinator, config: SatConfig, heating_control: SatHeatingControl):
        super().__init__(coordinator, config, heating_control)
        self._name = self._config.name

    @property
    def name(self) -> Optional[str]:
        return f"Maximum Setpoint {self._name} (Device)"

    @property
    def device_class(self):
        """Return the device class."""
        return NumberDeviceClass.TEMPERATURE

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config.entry_id}-boiler-maximum-setpoint"

    @property
    def icon(self) -> Optional[str]:
        return "mdi:thermometer"

    @property
    def available(self):
        """Return availability of the sensor."""
        return self._coordinator.maximum_setpoint_value is not None

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement in native units."""
        return "°C"

    @property
    def native_value(self):
        """Return the state of the device in native units."""
        return self._coordinator.maximum_setpoint_value

    @property
    def native_min_value(self) -> float:
        """Return the minimum accepted temperature."""
        return 30

    @property
    def native_max_value(self) -> float:
        """Return the maximum accepted temperature."""
        return 80

    async def async_set_native_value(self, value: float) -> None:
        """Update the setpoint."""
        await self._coordinator.async_set_control_max_setpoint(value)
