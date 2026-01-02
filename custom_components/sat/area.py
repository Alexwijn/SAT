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

from .const import CONF_ROOMS, COLD_SETPOINT, CONF_ROOM_WEIGHTS, MINIMUM_SETPOINT, CONF_SENSOR_MAX_VALUE_AGE
from .errors import Errors, Error
from .heating_curve import HeatingCurve
from .helpers import float_value, convert_time_str_to_seconds, is_state_stale, state_age_seconds, event_timestamp
from .pid import PID
from .util import create_pid_controller

_LOGGER = logging.getLogger(__name__)

ATTR_TEMPERATURE = "temperature"
ATTR_CURRENT_TEMPERATURE = "current_temperature"
ATTR_CURRENT_VALVE_POSITION = "current_valve_position"

ATTR_SENSOR_TEMPERATURE_ID = "sensor_temperature_id"

COMFORT_BAND = 0.1
COOLING_SLOPE = 4.0
OVERSHOOT_MARGIN = 0.3
COOLING_HEADROOM = 10.0


class Area:
    """Represents a single climate-controlled area."""

    def __init__(self, config_data: MappingProxyType[str, Any], config_options: MappingProxyType[str, Any], heating_curve: HeatingCurve, entity_id: str) -> None:
        self._time_interval = None
        self._sensor_handler = None

        self._entity_id: str = entity_id
        self._hass: HomeAssistant | None = None
        self._sensor_max_value_age: float = convert_time_str_to_seconds(config_options.get(CONF_SENSOR_MAX_VALUE_AGE))

        # Controllers and heating curve
        self.heating_curve: HeatingCurve = heating_curve
        self.pid: PID = create_pid_controller(config_data, config_options)

        # Per-room influence scaling for demand calculations.
        raw_weights = config_options.get(CONF_ROOM_WEIGHTS, {}) or {}
        raw_value = raw_weights.get(entity_id, 1.0)

        try:
            room_weight = float(raw_value)
        except (TypeError, ValueError):
            room_weight = 1.0

        # Clamp for safety; keep consistent with your UI min/max (0.1..3.0)
        self._room_weight: float = max(0.1, min(room_weight, 3.0))

        _LOGGER.debug("Area %s initialized with room_weight=%.3f", self._entity_id, self._room_weight)

    @property
    def id(self) -> str:
        """Return the entity id of this area."""
        return self._entity_id

    @property
    def room_weight(self) -> float:
        """User-defined influence scaling factor for this room."""
        return self._room_weight

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
        if (sensor_temperature_id := state.attributes.get(ATTR_SENSOR_TEMPERATURE_ID)) is not None:
            sensor_state = self._hass.states.get(sensor_temperature_id)
            if sensor_state and sensor_state.state not in [STATE_UNKNOWN, STATE_UNAVAILABLE, HVACMode.OFF]:
                if is_state_stale(sensor_state, self._sensor_max_value_age):
                    _LOGGER.debug("Area sensor %s stale for %s (age=%.1fs > %.1fs)", sensor_temperature_id, self._entity_id, state_age_seconds(sensor_state), self._sensor_max_value_age)
                    return None

                return float_value(sensor_state.state)

        if is_state_stale(state, self._sensor_max_value_age):
            _LOGGER.debug("Area climate %s stale for %s (age=%.1fs > %.1fs)", self._entity_id, self._entity_id, state_age_seconds(state), self._sensor_max_value_age)
            return None

        return float_value(state.attributes.get("current_temperature") or self.target_temperature)

    @property
    def valve_position(self) -> Optional[float]:
        """Retrieve the valve position from the climate entity."""
        if (self._hass is None) or (state := self.state) is None:
            return None

        if ATTR_CURRENT_VALVE_POSITION not in state.attributes:
            return None

        raw_value = state.attributes.get(ATTR_CURRENT_VALVE_POSITION)

        try:
            return float_value(raw_value)
        except (TypeError, ValueError):
            _LOGGER.debug("Invalid valve position for %s: %r", self.id, raw_value)
            return None

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

    @property
    def demand_weight(self) -> Optional[float]:
        """Scaled demand weight, applying the user-defined room_weight."""
        base = self.weight
        if base is None:
            return None

        return round(base * self._room_weight, 3)

    @property
    def requires_heat(self) -> bool:
        """Determine if this area should influence heating arbitration."""
        valve_position = self.valve_position
        if valve_position is not None:
            return valve_position >= 10.0

        if not self.pid.available:
            return False

        return self.pid.output > COLD_SETPOINT

    async def async_added_to_hass(self, hass: HomeAssistant) -> None:
        """Run when the area is added to Home Assistant."""
        self._hass = hass

        if hass.state is CoreState.running:
            self.update()
        else:
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, self.update)

        # Periodic update as a fallback when we do not have a dedicated sensor listener
        self._time_interval = async_track_time_interval(
            self._hass, self.update, timedelta(seconds=30)
        )

    async def async_will_remove_from_hass(self) -> None:
        """Run when the area is about to be removed."""
        if self._time_interval is not None:
            self._time_interval()
            self._time_interval = None

    def update(self, time: Optional[datetime] = None) -> None:
        """Update the PID controller with the current error and heating curve."""
        if self.error is None:
            _LOGGER.debug("Skipping control loop for %s because error could not be computed", self._entity_id)
            return

        if self.heating_curve.value is None:
            _LOGGER.debug("Skipping control loop for %s because heating curve has no value", self._entity_id)
            return

        self.pid.update(self.error, event_timestamp(time), self.heating_curve.value)
        _LOGGER.debug("PID update for %s (error=%s, curve=%s, output=%s)", self._entity_id, self.error.value, self.heating_curve.value, self.pid.output)


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

    class _PIDs:
        """Helper for interacting with PID controllers of all areas."""

        def __init__(self, areas, percentile: float = 0.75, headroom: float = 5.0):
            self._areas = areas
            self._headroom = headroom
            self._percentile = percentile

        @property
        def output(self) -> float:
            """Aggregate PID output + count for areas that are calling for heat."""
            area_count = 0
            outputs: list[float] = []

            for area in self._areas:
                if not area.pid.available or not area.requires_heat:
                    continue

                try:
                    value = area.pid.output
                except Exception as exception:
                    _LOGGER.warning("Failed to compute PID output for area %s: %s", area.id, exception)
                    continue

                area_count += 1
                outputs.append(value)

            if not outputs:
                return MINIMUM_SETPOINT

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
                if not area.pid.available or not area.requires_heat:
                    continue

                error = area.error
                if error is None:
                    continue

                if error.value >= -OVERSHOOT_MARGIN:
                    continue

                # Degrees above target (positive number)
                degrees_over = -error.value

                # Start from a “max allowed in cooling” and pull it down with overshoot severity.
                caps.append(COLD_SETPOINT + COOLING_HEADROOM - COOLING_SLOPE * degrees_over)

            if not caps:
                return None

            # The strictest cap wins: we take the minimum.
            cap = min(caps)

            # Ensure we never go below the minimum.
            cap = max(COLD_SETPOINT, cap)

            return round(cap, 1)

        def reset(self, entity_id: Optional[str] = None) -> None:
            for area in self._areas:
                if entity_id is None or entity_id == area.id:
                    _LOGGER.info("Reset PID controller for %s", area.id)
                    area.pid.reset()
