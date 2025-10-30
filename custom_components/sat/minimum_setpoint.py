import logging
from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from pygments.lexers import math

from custom_components.sat.boiler import BoilerState
from custom_components.sat.helpers import clamp, State, update_state
from custom_components.sat.pwm import PWMState

_LOGGER = logging.getLogger(__name__)


class MinimumSetpoint:
    _STORAGE_VERSION = 1
    _STORAGE_KEY = "minimum_setpoint"

    def __init__(self, adjustment_factor: float, configured_minimum_setpoint: float):
        self._store: Store | None = None
        self._base_return_temperature: State = State()
        self._current_minimum_setpoint: State = State()
        self._relative_modulation_level: State = State()

        self._adjustment_factor: float = adjustment_factor
        self._configured_minimum_setpoint: float = configured_minimum_setpoint

    async def async_initialize(self, hass: HomeAssistant) -> None:
        self._store = Store(hass, self._STORAGE_VERSION, self._STORAGE_KEY)

        data = await self._store.async_load()
        if data and "base_return_temperature" in data:
            self._base_return_temperature = data["base_return_temperature"]
            _LOGGER.debug("Loaded base return temperature from storage.")

    def warming_up(self, boiler_state: BoilerState) -> None:
        if self._base_return_temperature.value is not None and self._base_return_temperature.value == boiler_state.return_temperature:
            return

        self._base_return_temperature = update_state(previous=self._base_return_temperature, new_value=boiler_state.return_temperature)
        _LOGGER.debug(f"New temperature set to: %.1f°C.", self._base_return_temperature.value)

        if self._store:
            self._store.async_delay_save(self._data_to_save)

    def calculate(self, boiler_state: BoilerState, pwm_state: PWMState) -> None:
        self._relative_modulation_level = update_state(previous=self._relative_modulation_level, new_value=boiler_state.relative_modulation_level)

        if self._base_return_temperature is None:
            _LOGGER.debug("Skip calculation: base return temperature is not set.")
            return

        if self._is_running_normal_mode(boiler_state, pwm_state):
            proportion = 1.0 - (boiler_state.relative_modulation_level / 100.0)
            proportional_candidate = (proportion * boiler_state.flow_temperature) - 3.0
            new_minimum = clamp(proportional_candidate, 30.0)
        else:
            adjustment = (boiler_state.return_temperature - self._base_return_temperature.value) * self._adjustment_factor
            minimum_setpoint_candidate = self._configured_minimum_setpoint + adjustment
            new_minimum = clamp(minimum_setpoint_candidate, 30.0)

        self._current_minimum_setpoint = update_state(previous=self._current_minimum_setpoint, new_value=new_minimum)
        _LOGGER.debug("Calculated new minimum setpoint: %.1f°C.", self._current_minimum_setpoint.value)

    @property
    def current(self) -> float:
        return self._current_minimum_setpoint if self._current_minimum_setpoint.value is not None else self._configured_minimum_setpoint

    def _data_to_save(self) -> dict:
        return {"base_return_temperature": self._base_return_temperature.value}

    def _is_running_normal_mode(self, boiler_state: BoilerState, pwm_state: PWMState) -> bool:
        return (
                pwm_state is not PWMState.IDLE
                and self._relative_modulation_level.value > 0
                and math.isclose(boiler_state.flow_temperature, boiler_state.setpoint, abs_tol=1.0)
                and (datetime.now() - self._relative_modulation_level.last_changed).total_seconds() > 180
        )
