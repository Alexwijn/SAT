import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)


class MinimumSetpoint:
    _STORAGE_VERSION = 1
    _STORAGE_KEY = "minimum_setpoint"

    def __init__(self, adjustment_factor: float, configured_minimum_setpoint: float):
        self._store = None
        self.base_boiler_temperature = None
        self.current_minimum_setpoint = None

        self.adjustment_factor = adjustment_factor
        self.configured_minimum_setpoint = configured_minimum_setpoint

    async def async_initialize(self, hass: HomeAssistant) -> None:
        self._store = Store(hass, self._STORAGE_VERSION, self._STORAGE_KEY)

        data = await self._store.async_load()
        if data and "base_boiler_temperature" in data:
            self.base_boiler_temperature = data["base_boiler_temperature"]
            _LOGGER.debug("Loaded base boiler temperature from storage.")

    def warming_up(self, boiler_temperature: float) -> None:
        if self.base_boiler_temperature is not None and self.base_boiler_temperature > boiler_temperature:
            return

        # Use the new value if it's higher or none is set
        self.base_boiler_temperature = boiler_temperature
        _LOGGER.debug(f"Higher temperature set to: {boiler_temperature}.")

        # Make sure to remember this value
        if self._store:
            self._store.async_delay_save(self._data_to_save)
            _LOGGER.debug("Stored base return temperature changes.")

    def calculate(self, boiler_temperature: float) -> None:
        if self.base_boiler_temperature is None:
            return

        adjustment = (boiler_temperature - self.base_boiler_temperature) * self.adjustment_factor
        self.current_minimum_setpoint = self.configured_minimum_setpoint + adjustment

        _LOGGER.debug("Calculated new minimum setpoint: %d°C", self.current_minimum_setpoint)

    def current(self) -> float:
        return self.current_minimum_setpoint if self.current_minimum_setpoint is not None else self.configured_minimum_setpoint

    def _data_to_save(self) -> dict:
        return {"base_boiler_temperature": self.base_boiler_temperature}
