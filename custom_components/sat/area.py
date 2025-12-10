import logging
from datetime import timedelta, datetime
from types import MappingProxyType
from typing import Any, Optional

from homeassistant.components.climate import HVACMode
from homeassistant.const import (
    STATE_UNKNOWN,
    STATE_UNAVAILABLE,
    EVENT_HOMEASSISTANT_STARTED,
)
from homeassistant.core import HomeAssistant, State, CoreState
from homeassistant.helpers.event import async_track_time_interval

from .const import CONF_ROOMS, COLD_SETPOINT
from .errors import Errors, Error
from .heating_curve import HeatingCurve
from .helpers import float_value
from .pid import PID
from .util import create_pid_controller

_LOGGER = logging.getLogger(__name__)

ATTR_TEMPERATURE = "temperature"
ATTR_CURRENT_TEMPERATURE = "current_temperature"
ATTR_SENSOR_TEMPERATURE_ID = "sensor_temperature_id"

COMFORT_BAND = 0.1
COOLING_SLOPE = 4.0
OVERSHOOT_MARGIN = 0.3
COOLING_HEADROOM = 10.0


class Area:
    """Represents a single climate-controlled area."""

    def __init__(self, _config_data: MappingProxyType[str, Any], config_options: MappingProxyType[str, Any], heating_curve: HeatingCurve, entity_id: str) -> None:
        self._time_interval = None
        self._sensor_handler = None

        self._entity_id: str = entity_id
        self._hass: HomeAssistant | None = None

        # Controllers and heating curve
        self.heating_curve: HeatingCurve = heating_curve
        self.pid: PID = create_pid_controller(config_options)

    @property
    def id(self) -> str:
        """Return the entity id of this area."""
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
        if sensor_temperature_id := state.attributes.get(ATTR_SENSOR_TEMPERATURE_ID):
            sensor_state = self._hass.states.get(sensor_temperature_id)
            if sensor_state and sensor_state.state not in [STATE_UNKNOWN, STATE_UNAVAILABLE, HVACMode.OFF]:
                return float_value(sensor_state.state)

        return float_value(state.attributes.get("current_temperature") or self.target_temperature)

    @property
    def error(self) -> Optional[Error]:
        """Calculate the temperature error (target - current)."""
        target_temperature = self.target_temperature
        current_temperature = self.current_temperature

        if target_temperature is None or current_temperature is None:
            return None

        return Error(self._entity_id, round(target_temperature - current_temperature, 2))

    @property
    def weight(self) -> Optional[float]:
        """
        Room heating demand weight in range [0, 2].

        Based on the difference between target and current temperature.
        """
        target_temperature = self.target_temperature
        current_temperature = self.current_temperature

        if target_temperature is None or current_temperature is None:
            return None

        delta = target_temperature - current_temperature
        effective_delta = max(delta - 0.2, 0.0)
        raw_weight = effective_delta * 1.0
        clamped_weight = max(0.0, min(raw_weight, 2.0))

        return round(clamped_weight, 3)

    async def async_added_to_hass(self, hass: HomeAssistant) -> None:
        """Run when the area is added to Home Assistant."""
        self._hass = hass

        if hass.state is CoreState.running:
            self.update()
        else:
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, self.update)

        # Periodic update as a fallback when we do not have a dedicated sensor listener
        self._time_interval = async_track_time_interval(
            self._hass, self.update, timedelta(seconds=60)
        )

    async def async_will_remove_from_hass(self) -> None:
        """Run when the area is about to be removed."""
        if self._time_interval is not None:
            self._time_interval()
            self._time_interval = None

    async def async_control_heating_loop(self, _time: datetime | None = None) -> None:
        """Asynchronously control the heating loop."""
        if self.error is None:
            _LOGGER.debug("Skipping control loop for %s because error could not be computed", self._entity_id)
            return

        if self.heating_curve.value is None:
            _LOGGER.debug("Skipping control loop for %s because heating curve has no value", self._entity_id)
            return

        self.pid.update_integral(self.error, self.heating_curve.value)

    def update(self, _now: datetime | None = None) -> None:
        """Update the PID controller with the current error and heating curve."""
        if self.error is None:
            return

        if self.heating_curve.value is None:
            return

        self.pid.update(self.error, self.heating_curve.value)
        _LOGGER.debug("PID update for %s (error=%s, curve=%s)", self._entity_id, self.error.value, self.heating_curve.value)


class Areas:
    """Container for multiple Area instances."""

    def __init__(self, config_data: MappingProxyType[str, Any], config_options: MappingProxyType[str, Any], heating_curve: HeatingCurve) -> None:
        """Initialize Areas with multiple Area instances using shared config data and options."""
        self._entity_ids: list[str] = config_data.get(CONF_ROOMS) or []
        self._areas: list[Area] = [Area(config_data, config_options, heating_curve, entity_id) for entity_id in self._entity_ids]

        _LOGGER.debug("Initialized Areas with entity_ids=%s", ", ".join(self._entity_ids) if self._entity_ids else "<none>")

    @property
    def errors(self) -> Errors:
        """Return a collection of all the error values for all areas."""
        error_list = [area.error for area in self._areas if area.error is not None]
        return Errors(error_list)

    @property
    def pids(self) -> "Areas._PIDs":
        """Return an interface to reset PID controllers for all areas."""
        return Areas._PIDs(self._areas)

    def get(self, entity_id: str) -> Optional[Area]:
        """Return the Area instance for a given entity id, if present."""
        for area in self._areas:
            if area.id == entity_id:
                return area
        return None

    def ids(self) -> list[str]:
        """Return all configured entity ids."""
        return list(self._entity_ids)

    def items(self) -> list[Area]:
        """Return all Area instances."""
        return list(self._areas)

    async def async_added_to_hass(self, hass: HomeAssistant) -> None:
        """Call async_added_to_hass for all areas."""
        for area in self._areas:
            await area.async_added_to_hass(hass)

    async def async_control_heating_loops(self, _time: datetime | None = None) -> None:
        """Asynchronously control heating loop for all areas."""
        for area in self._areas:
            await area.async_control_heating_loop(_time)

    class _PIDs:
        """Helper for interacting with PID controllers of all areas."""

        def __init__(self, areas, percentile: float = 0.75, headroom: float = 5.0):
            self._areas = areas
            self._headroom = headroom
            self._percentile = percentile

        @property
        def output(self) -> float | None:
            """Aggregate PID output for areas that still need heat."""
            outputs: list[float] = []

            for area in self._areas:
                if not area.pid.available:
                    continue

                # Only consider rooms that are actually below target by a meaningful amount.
                if area.error.value <= COMFORT_BAND:
                    continue

                try:
                    value = area.pid.output
                except Exception as exception:
                    _LOGGER.warning("Failed to compute PID output for area %s: %s", area.id, exception)
                    continue

                outputs.append(value)

            if not outputs:
                return None

            outputs.sort()

            index = max(0, min(len(outputs) - 1, int(len(outputs) * self._percentile)))
            baseline = outputs[index]

            allowed = baseline + self._headroom
            chosen = min(outputs[-1], allowed)

            return round(chosen, 1)

        @property
        def overshoot_cap(self) -> float | None:
            """Compute a cooling-driven cap based on overshooting rooms."""
            caps: list[float] = []

            for area in self._areas:
                if not area.pid.available:
                    continue

                error = area.target_temperature - area.current_temperature

                # Overshoot: current > target and margin (error negative beyond margin)
                if error >= -OVERSHOOT_MARGIN:
                    continue

                # Degrees above target (positive number)
                degrees_over = -error

                # Start from a “max allowed in cooling” and pull it down with overshoot severity.
                cap = COLD_SETPOINT + COOLING_HEADROOM - COOLING_SLOPE * degrees_over
                caps.append(cap)

            if not caps:
                return None

            # The strictest cap wins: we take the minimum.
            cap = min(caps)

            # Ensure we never go below the minimum.
            cap = max(COLD_SETPOINT, cap)

            return round(cap, 1)

        def reset(self):
            for area in self._areas:
                area.pid.reset()

        def update(self, entity_id: str) -> None:
            """Update the PID controller for a specific area."""
            if area := self._get_area(entity_id) is None:
                return

            if area.error is None or area.heating_curve.value is None:
                return

            _LOGGER.info("Updating PID for %s with error=%s", area.id, area.error.value)
            area.pid.update(area.error, area.heating_curve.value)

        def _get_area(self, entity_id: str) -> Optional[Area]:
            for area in self._areas:
                if area.id == entity_id:
                    return area

            return None
