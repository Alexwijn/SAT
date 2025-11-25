import logging
from types import MappingProxyType
from typing import Any, Optional

from homeassistant.components.climate import HVACMode
from homeassistant.const import STATE_UNKNOWN, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State

from .const import CONF_ROOMS, MINIMUM_SETPOINT
from .errors import Errors, Error
from .heating_curve import HeatingCurve
from .helpers import float_value
from .pid import PID
from .util import (
    create_pid_controller,
    create_heating_curve_controller,
)

_LOGGER = logging.getLogger(__name__)
SENSOR_TEMPERATURE_ID = "sensor_temperature_id"


class Area:
    def __init__(self, config_data: MappingProxyType[str, Any], config_options: MappingProxyType[str, Any], entity_id: str):
        self._entity_id: str = entity_id
        self._hass: HomeAssistant | None = None

        # Create controllers with the given configuration options
        self.pid: PID = create_pid_controller(config_options)
        self.heating_curve: HeatingCurve = create_heating_curve_controller(config_data, config_options)

    @property
    def id(self) -> str:
        return self._entity_id

    @property
    def state(self) -> Optional[State]:
        """Retrieve the current state of the climate entity."""
        if (self._hass is None) or (state := self._hass.states.get(self._entity_id)) is None:
            return None

        return state if state.state not in [STATE_UNKNOWN, STATE_UNAVAILABLE] else None

    @property
    def target_temperature(self) -> Optional[float]:
        """Retrieve the target temperature from the climate entity."""
        if (self._hass is None) or (state := self.state) is None:
            return None

        return float_value(state.attributes.get("temperature"))

    @property
    def current_temperature(self) -> Optional[float]:
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
    def error(self) -> Optional[Error]:
        """Calculate the temperature error."""
        target_temperature = self.target_temperature
        current_temperature = self.current_temperature

        if target_temperature is None or current_temperature is None:
            return None

        return Error(self._entity_id, round(target_temperature - current_temperature, 2))

    @property
    def weight(self) -> Optional[float]:
        """
        Room heating demand weight (0-2 range).
        Based on the difference between target and current temperature.
        """
        target_temperature = self.target_temperature
        current_temperature = self.current_temperature

        if target_temperature is None or current_temperature is None:
            return None

        delta = target_temperature - current_temperature
        effective_delta = max(delta - 0.2, 0.0)
        raw_weight = effective_delta * 1.0

        return round(max(0.0, min(raw_weight, 2.0)), 3)

    async def async_added_to_hass(self, hass: HomeAssistant):
        self._hass = hass

    async def async_control_heating_loop(self, _time=None) -> None:
        """Asynchronously control the heating loop."""
        if self.error is None or self.heating_curve.value is None:
            return

        # Control the integral (if exceeded the time limit)
        self.pid.update_integral(self.error, self.heating_curve.value)


class Areas:
    def __init__(self, config_data: MappingProxyType[str, Any], config_options: MappingProxyType[str, Any]):
        """Initialize Areas with multiple Area instances using shared config data and options."""
        self._entity_ids: list[str] = config_data.get(CONF_ROOMS) or []
        self._areas: list[Area] = [Area(config_data, config_options, entity_id) for entity_id in self._entity_ids]

    @property
    def errors(self) -> Errors:
        """Return a list of all the error values for all areas."""
        return Errors([area.error for area in self._areas if area.error is not None])

    @property
    def heating_curves(self):
        """Return an interface to update heating curves for all areas."""
        return Areas._HeatingCurves(self._areas)

    @property
    def pids(self):
        """Return an interface to reset PID controllers for all areas."""
        return Areas._PIDs(self._areas)

    def items(self) -> list[str]:
        return self._entity_ids

    async def async_added_to_hass(self, hass: HomeAssistant):
        for area in self._areas:
            await area.async_added_to_hass(hass)

    async def async_control_heating_loops(self, _time=None) -> None:
        """Asynchronously control heating loop for all areas."""
        for area in self._areas:
            await area.async_control_heating_loop(_time)

    class _HeatingCurves:
        def __init__(self, areas: list[Area]):
            self._areas = areas

        def update(self, current_outside_temperature: float) -> None:
            """Update the heating curve for all areas based on the current outside temperature."""
            for area in self._areas:
                if area.target_temperature is None:
                    continue

                area.heating_curve.update(area.target_temperature, current_outside_temperature)

    class _PIDs:
        def __init__(self, areas: list[Area]):
            self._areas = areas

        @property
        def output(self) -> float:
            outputs = [
                area.heating_curve.value + area.pid.output
                for area in self._areas
                if area.heating_curve.value is not None
            ]

            return round(max(outputs), 1) if outputs else MINIMUM_SETPOINT

        def update(self, entity_id: str) -> None:
            if (area := self._get_area(entity_id)) is None:
                _LOGGER.warning(f"Could not update PID controller for entity {entity_id}")
                return

            if area.error is not None and area.heating_curve.value is not None:
                _LOGGER.info(f"Updating error to {area.error.value} of {area.id} (Reset: False)")
                area.pid.update(area.error, area.heating_curve.value)

        def update_reset(self, entity_id: str) -> None:
            if (area := self._get_area(entity_id)) is None:
                _LOGGER.warning(f"Could not update PID controller for entity {entity_id}")
                return

            if area.error is not None:
                _LOGGER.info(f"Updating error to {area.error.value} of {area.id} (Reset: True)")
                area.pid.update_reset(area.error, area.heating_curve.value)

        def reset(self) -> None:
            for area in self._areas:
                self.update_reset(area.id)

        def _get_area(self, entity_id: str) -> Optional[Area]:
            for area in self._areas:
                if area.id == entity_id:
                    return area

            return None
