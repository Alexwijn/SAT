from homeassistant.components.number import NumberEntity, NumberDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import *
from .coordinator import SatDataUpdateCoordinator
from .entity import SatEntity


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities):
    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]

    if coordinator.supports_maximum_setpoint_management:
        async_add_entities([SatMaximumSetpointEntity(coordinator, config_entry)])

    if coordinator.supports_hot_water_setpoint_management:
        async_add_entities([SatHotWaterSetpointEntity(coordinator, config_entry)])


class SatHotWaterSetpointEntity(SatEntity, NumberEntity):
    def __init__(self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry):
        super().__init__(coordinator, config_entry)
        self._name = self._config_entry.data.get(CONF_NAME)

    @property
    def name(self) -> str | None:
        return f"Hot Water Setpoint {self._name} (Boiler)"

    @property
    def device_class(self):
        """Return the device class."""
        return NumberDeviceClass.TEMPERATURE

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._name.lower()}-boiler-dhw-setpoint"

    @property
    def icon(self) -> str | None:
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
    def __init__(self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry):
        super().__init__(coordinator, config_entry)
        self._name = self._config_entry.data.get(CONF_NAME)

    @property
    def name(self) -> str | None:
        return f"Maximum Setpoint {self._name} (Boiler)"

    @property
    def device_class(self):
        """Return the device class."""
        return NumberDeviceClass.TEMPERATURE

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._name.lower()}-boiler-maximum-setpoint"

    @property
    def icon(self) -> str | None:
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
