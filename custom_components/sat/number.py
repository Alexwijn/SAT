from homeassistant.components.number import NumberEntity, NumberDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import *
from .coordinators.opentherm import SatOpenThermCoordinator
from .entity import SatEntity


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities):
    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]
    async_add_entities([SatHotWaterSetpointEntity(coordinator, config_entry)])


class SatHotWaterSetpointEntity(SatEntity, NumberEntity):
    def __init__(self, coordinator: SatOpenThermCoordinator, config_entry: ConfigEntry):
        super().__init__(coordinator, config_entry)

        self._coordinator = coordinator

    @property
    def name(self) -> str | None:
        return f"Hot Water Setpoint {self._config_entry.data.get(CONF_NAME)} (Boiler)"

    @property
    def device_class(self):
        """Return the device class."""
        return NumberDeviceClass.TEMPERATURE

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-boiler-dhw-setpoint"

    @property
    def icon(self) -> str | None:
        return "mdi:thermometer"

    @property
    def available(self):
        """Return availability of the sensor."""
        return self._coordinator.data is not None and self._coordinator.data[gw_vars.BOILER] is not None

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement in native units."""
        return "°C"

    @property
    def native_value(self):
        """Return the state of the device in native units."""
        return self._coordinator.data[gw_vars.BOILER][gw_vars.DATA_DHW_SETPOINT]

    @property
    def native_min_value(self) -> float:
        """Return the minimum accepted temperature."""
        return self._coordinator.data[gw_vars.BOILER][gw_vars.DATA_SLAVE_DHW_MIN_SETP]

    @property
    def native_max_value(self) -> float:
        """Return the maximum accepted temperature."""
        return self._coordinator.data[gw_vars.BOILER][gw_vars.DATA_SLAVE_DHW_MAX_SETP]

    async def async_set_native_value(self, value: float) -> None:
        """Update the setpoint."""
        await self._coordinator.api.set_dhw_setpoint(value)
