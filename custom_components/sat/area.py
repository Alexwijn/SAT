from types import MappingProxyType
from typing import Any, List

from homeassistant.components.climate import HVACMode
from homeassistant.const import STATE_UNKNOWN, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State

from .heating_curve import HeatingCurve
from .helpers import float_value
from .pid import PID
from .pwm import PWM
from .util import (
    create_pwm_controller,
    create_pid_controller,
    create_heating_curve_controller,
)

SENSOR_TEMPERATURE_ID = "sensor_temperature_id"


class Area:
    def __init__(self, config_data: MappingProxyType[str, Any], config_options: MappingProxyType[str, Any], entity_id: str):
        self._entity_id: str = entity_id
        self._hass: HomeAssistant | None = None

        # Create controllers with the given configuration options
        self.pid: PID = create_pid_controller(config_options)
        self.heating_curve: HeatingCurve = create_heating_curve_controller(config_data, config_options)
        self.pwm: PWM = create_pwm_controller(self.heating_curve, config_data, config_options)

    @property
    def id(self) -> str:
        return self._entity_id

    @property
    def state(self) -> State | None:
        """Retrieve the current state of the climate entity."""
        if (self._hass is None) or (state := self._hass.states.get(self._entity_id)) is None:
            return None

        return state if state.state not in [STATE_UNKNOWN, STATE_UNAVAILABLE] else None

    @property
    def target_temperature(self) -> float | None:
        """Retrieve the target temperature from the climate entity."""
        if (self._hass is None) or (state := self.state) is None:
            return None

        return float_value(state.attributes.get("temperature"))

    @property
    def current_temperature(self) -> float | None:
        """Retrieve the current temperature, overridden by a sensor if set."""
        if (self._hass is None) or (state := self.state) is None:
            return None

        # Check if there is an overridden sensor temperature
        if sensor_temperature_id := state.attributes.get(SENSOR_TEMPERATURE_ID):
            sensor_state = self._hass.states.get(sensor_temperature_id)
            if sensor_state and sensor_state.state not in [STATE_UNKNOWN, STATE_UNAVAILABLE, HVACMode.OFF]:
                return float_value(sensor_state.state)

        return float_value(state.attributes.get("current_temperature") or self.target_temperature)

    @property
    def error(self) -> float | None:
        """Calculate the temperature error."""
        target_temperature = self.target_temperature
        current_temperature = self.current_temperature

        if target_temperature is None or current_temperature is None:
            return None

        return round(target_temperature - current_temperature, 2)

    async def async_added_to_hass(self, hass: HomeAssistant):
        self._hass = hass

    async def async_control_heating_loop(self, _time=None) -> None:
        """Asynchronously control the heating loop."""
        if self.error is None or self.heating_curve.value is None:
            return

        # Control the integral (if exceeded the time limit)
        self.pid.update_integral(self.error, self.heating_curve.value)


class Areas:
    def __init__(self, config_data: MappingProxyType[str, Any], config_options: MappingProxyType[str, Any], entity_ids: list[str]):
        """Initialize Areas with multiple Area instances using shared config data and options."""
        self._entity_ids: list[str] = entity_ids
        self._config_data: MappingProxyType[str, Any] = config_data
        self._config_options: MappingProxyType[str, Any] = config_options
        self._areas: list[Area] = [Area(config_data, config_options, entity_id) for entity_id in entity_ids]

    @property
    def errors(self) -> List[float]:
        """Return a list of all the error values for all areas."""
        return [area.error for area in self._areas if area.error is not None]

    @property
    def heating_curves(self):
        """Return an interface to update heating curves for all areas."""
        return Areas._HeatingCurves(self._areas)

    @property
    def pids(self):
        """Return an interface to reset PID controllers for all areas."""
        return Areas._PIDs(self._areas)

    async def async_added_to_hass(self, hass: HomeAssistant):
        for area in self._areas:
            await area.async_added_to_hass(hass)

    async def async_control_heating_loops(self, _time=None) -> None:
        """Asynchronously control heating loop for all areas."""
        for area in self._areas:
            await area.async_control_heating_loop(_time)

    class _HeatingCurves:
        def __init__(self, areas: list[Area]):
            self.areas = areas

        def update(self, current_outside_temperature: float) -> None:
            """Update the heating curve for all areas based on current outside temperature."""
            for area in self.areas:
                if area.target_temperature is None:
                    continue

                area.heating_curve.update(area.target_temperature, current_outside_temperature)

    class _PIDs:
        def __init__(self, areas: list[Area]):
            self.areas = areas

        def update(self, boiler_temperature: float) -> None:
            for area in self.areas:
                if area.error is not None:
                    area.pid.update(area.error, area.heating_curve.value, boiler_temperature)

        def reset(self) -> None:
            """Reset PID controllers for all areas."""
            for area in self.areas:
                area.pid.reset()
