"""Climate platform for SAT."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from time import monotonic, time
from typing import List

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
    PRESET_ACTIVITY,
    PRESET_AWAY,
    PRESET_HOME,
    PRESET_NONE,
    PRESET_SLEEP,
    PRESET_COMFORT,
    ATTR_HVAC_MODE,
    ATTR_PRESET_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.components.notify import DOMAIN as NOTIFY_DOMAIN, SERVICE_PERSISTENT_NOTIFICATION
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.weather import DOMAIN as WEATHER_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, STATE_UNAVAILABLE, STATE_UNKNOWN, ATTR_ENTITY_ID, STATE_ON, STATE_OFF
from homeassistant.core import HomeAssistant, ServiceCall, Event
from homeassistant.helpers import entity_registry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity

from .const import *
from .coordinator import SatDataUpdateCoordinator, DeviceState
from .entity import SatEntity
from .pwm import PWMState
from .relative_modulation import RelativeModulation, RelativeModulationState
from .summer_simmer import SummerSimmer
from .util import create_pid_controller, create_heating_curve_controller, create_pwm_controller, convert_time_str_to_seconds, \
    calculate_derivative_per_hour, create_minimum_setpoint_controller

ATTR_ROOMS = "rooms"
ATTR_WARMING_UP = "warming_up_data"
ATTR_OPTIMAL_COEFFICIENT = "optimal_coefficient"
ATTR_COEFFICIENT_DERIVATIVE = "coefficient_derivative"
ATTR_WARMING_UP_DERIVATIVE = "warming_up_derivative"
ATTR_PRE_CUSTOM_TEMPERATURE = "pre_custom_temperature"
ATTR_PRE_ACTIVITY_TEMPERATURE = "pre_activity_temperature"
ATTR_ADJUSTED_MINIMUM_SETPOINTS = "adjusted_minimum_setpoints"

SENSOR_TEMPERATURE_ID = "sensor_temperature_id"

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(_hass: HomeAssistant, _config_entry: ConfigEntry, _async_add_devices: AddEntitiesCallback):
    """Set up the SatClimate device."""
    coordinator = _hass.data[DOMAIN][_config_entry.entry_id][COORDINATOR]
    climate = SatClimate(coordinator, _config_entry, _hass.config.units.temperature_unit)

    _async_add_devices([climate])
    _hass.data[DOMAIN][_config_entry.entry_id][CLIMATE] = climate


class SatWarmingUp:
    def __init__(self, error: float, boiler_temperature: float = None, started: int = None):
        self.error = error
        self.boiler_temperature = boiler_temperature
        self.started = started if started is not None else int(time())

    @property
    def elapsed(self):
        return int(time()) - self.started


class SatClimate(SatEntity, ClimateEntity, RestoreEntity):
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry, unit: str):
        super().__init__(coordinator, config_entry)

        # Get some sensor entity IDs
        self.inside_sensor_entity_id = config_entry.data.get(CONF_INSIDE_SENSOR_ENTITY_ID)
        self.humidity_sensor_entity_id = config_entry.data.get(CONF_HUMIDITY_SENSOR_ENTITY_ID)

        # Get some sensor entity states
        inside_sensor_entity = coordinator.hass.states.get(self.inside_sensor_entity_id)
        humidity_sensor_entity = coordinator.hass.states.get(self.humidity_sensor_entity_id) if self.humidity_sensor_entity_id is not None else None

        # Get current temperature
        self._current_temperature = None
        if inside_sensor_entity is not None and inside_sensor_entity.state not in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
            self._current_temperature = float(inside_sensor_entity.state)

        # Get current temperature
        self._current_humidity = None
        if humidity_sensor_entity is not None and humidity_sensor_entity.state not in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
            self._current_humidity = float(humidity_sensor_entity.state)

        # Get outside sensor entity IDs
        self.outside_sensor_entities = config_entry.data.get(CONF_OUTSIDE_SENSOR_ENTITY_ID)

        # If outside sensor entity IDs is a string, make it a list
        if isinstance(self.outside_sensor_entities, str):
            self.outside_sensor_entities = [self.outside_sensor_entities]

        # Create config options dictionary with defaults
        config_options = OPTIONS_DEFAULTS.copy()
        config_options.update(config_entry.options)

        # Create dictionary mapping preset keys to temperature options
        conf_presets = {p: f"{p}_temperature" for p in (PRESET_ACTIVITY, PRESET_AWAY, PRESET_HOME, PRESET_SLEEP, PRESET_COMFORT)}

        # Create dictionary mapping preset keys to temperature values
        self._presets = {key: config_options[value] for key, value in conf_presets.items() if key in conf_presets}

        self._alpha = 0.2
        self._sensors = []
        self._rooms = None
        self._setpoint = None
        self._calculated_setpoint = None

        self._warming_up_data = None
        self._warming_up_derivative = None

        self._hvac_mode = None
        self._target_temperature = None
        self._window_sensor_handle = None
        self._pre_custom_temperature = None
        self._pre_activity_temperature = None

        self._attr_temperature_unit = unit
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_preset_mode = PRESET_NONE
        self._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
        self._attr_preset_modes = [PRESET_NONE] + list(self._presets.keys())

        # Add features based on compatibility
        self._attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE

        # Conditionally add TURN_OFF if it exists
        if hasattr(ClimateEntityFeature, 'TURN_OFF'):
            self._attr_supported_features |= ClimateEntityFeature.TURN_OFF

        # System Configuration
        self._attr_name = str(config_entry.data.get(CONF_NAME))
        self._attr_id = str(config_entry.data.get(CONF_NAME)).lower()

        self._climates = config_entry.data.get(CONF_SECONDARY_CLIMATES) or []
        self._main_climates = config_entry.data.get(CONF_MAIN_CLIMATES) or []
        self._window_sensors = config_entry.options.get(CONF_WINDOW_SENSORS) or []

        self._simulation = bool(config_entry.data.get(CONF_SIMULATION))
        self._heating_system = str(config_entry.data.get(CONF_HEATING_SYSTEM))
        self._sync_with_thermostat = bool(config_entry.data.get(CONF_SYNC_WITH_THERMOSTAT))
        self._overshoot_protection = bool(config_entry.data.get(CONF_OVERSHOOT_PROTECTION))

        # User Configuration
        self._heating_mode = str(config_entry.options.get(CONF_HEATING_MODE))
        self._thermal_comfort = bool(config_options.get(CONF_THERMAL_COMFORT))
        self._climate_valve_offset = float(config_options.get(CONF_CLIMATE_VALVE_OFFSET))
        self._target_temperature_step = float(config_options.get(CONF_TARGET_TEMPERATURE_STEP))
        self._dynamic_minimum_setpoint = bool(config_options.get(CONF_DYNAMIC_MINIMUM_SETPOINT))
        self._sync_climates_with_mode = bool(config_options.get(CONF_SYNC_CLIMATES_WITH_MODE))
        self._sync_climates_with_preset = bool(config_options.get(CONF_SYNC_CLIMATES_WITH_PRESET))
        self._maximum_relative_modulation = int(config_options.get(CONF_MAXIMUM_RELATIVE_MODULATION))
        self._force_pulse_width_modulation = bool(config_options.get(CONF_FORCE_PULSE_WIDTH_MODULATION))
        self._sensor_max_value_age = convert_time_str_to_seconds(config_options.get(CONF_SENSOR_MAX_VALUE_AGE))
        self._window_minimum_open_time = convert_time_str_to_seconds(config_options.get(CONF_WINDOW_MINIMUM_OPEN_TIME))

        # Create PID controller with given configuration options
        self.pid = create_pid_controller(config_options)

        # Create Heating Curve controller with given configuration options
        self.heating_curve = create_heating_curve_controller(config_entry.data, config_options)

        # Create PWM controller with given configuration options
        self.pwm = create_pwm_controller(self.heating_curve, config_entry.data, config_options)

        # Create the Minimum Setpoint controller
        self._minimum_setpoint = create_minimum_setpoint_controller(config_entry.data, config_options)

        # Create Relative Modulation controller
        self._relative_modulation = RelativeModulation(coordinator, self._heating_system)

        if self._simulation:
            _LOGGER.warning("Simulation mode!")

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        # Register event listeners
        await self._register_event_listeners()

        # Restore previous state if available, or set default values
        await self._restore_previous_state_or_set_defaults()

        # Update a heating curve if outside temperature is available
        if self.current_outside_temperature is not None:
            self.heating_curve.update(self.target_temperature, self.current_outside_temperature)

        # Start control loop
        await self.async_control_heating_loop()

        # Register services
        await self._register_services()

        # Initialize minimum setpoint system
        await self._minimum_setpoint.async_initialize(self.hass)

        # Let the coordinator know we are ready
        await self._coordinator.async_added_to_hass(self)

    async def _register_event_listeners(self):
        """Register event listeners."""
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self.async_control_heating_loop, timedelta(seconds=30)
            )
        )

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self.inside_sensor_entity_id], self._async_inside_sensor_changed
            )
        )

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self.outside_sensor_entities, self._async_outside_entity_changed
            )
        )

        if self.humidity_sensor_entity_id is not None:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [self.humidity_sensor_entity_id], self._async_humidity_sensor_changed
                )
            )

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self._main_climates, self._async_main_climate_changed
            )
        )

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self._climates, self._async_climate_changed
            )
        )

        if len(self._window_sensors) > 0:
            entities = entity_registry.async_get(self.hass)
            device_name = self._config_entry.data.get(CONF_NAME)
            window_id = entities.async_get_entity_id(BINARY_SENSOR_DOMAIN, DOMAIN, f"{device_name.lower()}-window-sensor")

            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [window_id], self._async_window_sensor_changed
                )
            )

        for climate_id in self._climates:
            state = self.hass.states.get(climate_id)
            if state is not None and (sensor_temperature_id := state.attributes.get(SENSOR_TEMPERATURE_ID)):
                await self.async_track_sensor_temperature(sensor_temperature_id)

    async def _restore_previous_state_or_set_defaults(self):
        """Restore previous state if available, or set default values."""
        old_state = await self.async_get_last_state()

        if old_state is not None:
            self.pid.restore(old_state)

            if self._target_temperature is None:
                if old_state.attributes.get(ATTR_TEMPERATURE) is None:
                    self.pid.setpoint = self.min_temp
                    self._target_temperature = self.min_temp
                    _LOGGER.warning("Undefined target temperature, falling back to %s", self._target_temperature, )
                else:
                    self._target_temperature = float(old_state.attributes[ATTR_TEMPERATURE])

            if old_state.state:
                self._hvac_mode = old_state.state

            if old_state.attributes.get(ATTR_PRESET_MODE):
                self._attr_preset_mode = old_state.attributes.get(ATTR_PRESET_MODE)

            if warming_up := old_state.attributes.get(ATTR_WARMING_UP):
                self._warming_up_data = SatWarmingUp(warming_up["error"], warming_up["boiler_temperature"], warming_up["started"])

            if old_state.attributes.get(ATTR_WARMING_UP_DERIVATIVE):
                self._warming_up_derivative = old_state.attributes.get(ATTR_WARMING_UP_DERIVATIVE)

            if old_state.attributes.get(ATTR_PRE_ACTIVITY_TEMPERATURE):
                self._pre_activity_temperature = old_state.attributes.get(ATTR_PRE_ACTIVITY_TEMPERATURE)

            if old_state.attributes.get(ATTR_PRE_CUSTOM_TEMPERATURE):
                self._pre_custom_temperature = old_state.attributes.get(ATTR_PRE_CUSTOM_TEMPERATURE)

            if old_state.attributes.get(ATTR_OPTIMAL_COEFFICIENT):
                self.heating_curve.restore_autotune(
                    old_state.attributes.get(ATTR_OPTIMAL_COEFFICIENT),
                    old_state.attributes.get(ATTR_COEFFICIENT_DERIVATIVE)
                )

            if old_state.attributes.get(ATTR_ROOMS):
                self._rooms = old_state.attributes.get(ATTR_ROOMS)
            else:
                await self._async_update_rooms_from_climates()
        else:
            if self._rooms is None:
                await self._async_update_rooms_from_climates()

            if self._target_temperature is None:
                self.pid.setpoint = self.min_temp
                self._target_temperature = self.min_temp
                _LOGGER.warning("No previously saved temperature, setting to %s", self._target_temperature)

            if not self._hvac_mode:
                self._hvac_mode = HVACMode.OFF

        self.async_write_ha_state()

    async def _register_services(self):
        async def reset_integral(_call: ServiceCall):
            """Service to reset the integral part of the PID controller."""
            self.pid.reset()

        self.hass.services.async_register(DOMAIN, SERVICE_RESET_INTEGRAL, reset_integral)

    @property
    def name(self):
        """Return the friendly name of the sensor."""
        return self._attr_name

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return self._attr_id

    @property
    def extra_state_attributes(self):
        """Return device state attributes."""
        return {
            "error": self.pid.last_error,
            "integral": self.pid.integral,
            "derivative": self.pid.derivative,
            "proportional": self.pid.proportional,
            "history_size": self.pid.history_size,
            "collected_errors": self.pid.num_errors,
            "integral_enabled": self.pid.integral_enabled,

            "pre_custom_temperature": self._pre_custom_temperature,
            "pre_activity_temperature": self._pre_activity_temperature,

            "derivative_enabled": self.pid.derivative_enabled,
            "derivative_raw": self.pid.raw_derivative,

            "current_kp": self.pid.kp,
            "current_ki": self.pid.ki,
            "current_kd": self.pid.kd,

            "rooms": self._rooms,
            "setpoint": self._setpoint,
            "current_humidity": self._current_humidity,
            "summer_simmer_index": SummerSimmer.index(self._current_temperature, self._current_humidity),
            "summer_simmer_perception": SummerSimmer.perception(self._current_temperature, self._current_humidity),
            "warming_up_data": vars(self._warming_up_data) if self._warming_up_data is not None else None,
            "warming_up_derivative": self._warming_up_derivative,
            "valves_open": self.valves_open,
            "heating_curve": self.heating_curve.value,
            "minimum_setpoint": self.minimum_setpoint,
            "requested_setpoint": self.requested_setpoint,
            "adjusted_minimum_setpoint": self.adjusted_minimum_setpoint,
            "base_return_temperature": self._minimum_setpoint.base_return_temperature,
            "outside_temperature": self.current_outside_temperature,
            "optimal_coefficient": self.heating_curve.optimal_coefficient,
            "coefficient_derivative": self.heating_curve.coefficient_derivative,
            "relative_modulation_value": self.relative_modulation_value,
            "relative_modulation_state": self.relative_modulation_state,
            "relative_modulation_enabled": self._relative_modulation.enabled,
            "pulse_width_modulation_enabled": self.pulse_width_modulation_enabled,
            "pulse_width_modulation_state": self.pwm.state,
            "pulse_width_modulation_duty_cycle": self.pwm.duty_cycle,
        }

    @property
    def current_temperature(self):
        """Return the sensor temperature."""
        if self._thermal_comfort and self._current_humidity is not None:
            return SummerSimmer.index(self._current_temperature, self._current_humidity)

        return self._current_temperature

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._target_temperature

    @property
    def current_humidity(self):
        """Return the sensor humidity."""
        return self._current_humidity

    @property
    def error(self):
        """Return the error value."""
        if self.target_temperature is None or self.current_temperature is None:
            return 0

        return round(self.target_temperature - self.current_temperature, 2)

    @property
    def current_outside_temperature(self):
        """Return the current outside temperature"""
        self.outside_sensor_entities.sort(key=lambda x: "sensor" not in x)
        for entity_id in self.outside_sensor_entities:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
                continue

            if SENSOR_DOMAIN in entity_id:
                return float(state.state)

            if WEATHER_DOMAIN in entity_id:
                return float(state.attributes.get("temperature"))

        return None

    @property
    def target_temperature_step(self):
        """Return the target temperature step to control the thermostat"""
        return self._target_temperature_step

    @property
    def hvac_mode(self):
        """Get the current HVAC mode."""
        if self._hvac_mode in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
            return HVACMode.OFF

        return self._hvac_mode

    @property
    def hvac_action(self):
        """Get the current HVAC action."""
        if self._hvac_mode == HVACMode.OFF:
            return HVACAction.OFF

        if self._coordinator.device_state == DeviceState.OFF:
            return HVACAction.IDLE

        return HVACAction.HEATING

    @property
    def max_error(self) -> float:
        if self._heating_mode == HEATING_MODE_ECO:
            return self.error

        return max([self.error] + self.climate_errors)

    @property
    def setpoint(self) -> float | None:
        return self._setpoint

    @property
    def requested_setpoint(self) -> float:
        """Get the requested setpoint based on the heating curve and PID output."""
        if self.heating_curve.value is None:
            return MINIMUM_SETPOINT

        return round(max(self.heating_curve.value + self.pid.output, MINIMUM_SETPOINT), 1)

    @property
    def climate_errors(self) -> List[float]:
        """Calculate the temperature difference between the current temperature and target temperature for all connected climates."""
        errors = []
        for climate in self._climates:
            # Skip if climate state is unavailable or HVAC mode is off
            state = self.hass.states.get(climate)
            if state is None or state.state in [STATE_UNKNOWN, STATE_UNAVAILABLE, HVACMode.OFF]:
                continue

            # Calculate temperature difference for this climate
            target_temperature = float(state.attributes.get("temperature"))
            current_temperature = float(state.attributes.get("current_temperature") or target_temperature)

            # Retrieve the overridden sensor temperature if set
            if sensor_temperature_id := state.attributes.get(SENSOR_TEMPERATURE_ID):
                sensor_state = self.hass.states.get(sensor_temperature_id)
                if sensor_state is not None and sensor_state.state not in [STATE_UNKNOWN, STATE_UNAVAILABLE, HVACMode.OFF]:
                    current_temperature = float(sensor_state.state)

            # Calculate the error value
            error = round(target_temperature - current_temperature, 2)

            # Add to the list, so we calculate the max. later
            errors.append(error)

        return errors

    @property
    def valves_open(self) -> bool:
        """Determine if any of the controlled thermostats have open valves."""
        # Get the list of all controlled thermostats
        climates = self._climates + self._main_climates

        # If there are no thermostats, we can safely assume the valves are open
        if len(climates) == 0:
            return True

        # Iterate through each controlled thermostat
        for climate in climates:
            # Get the current state of the thermostat
            state = self.hass.states.get(climate)

            # If the thermostat is unavailable or has an unknown state, skip it
            if state is None or state.state in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
                continue

            # If the thermostat is turned off, skip it
            if state.state == HVACMode.OFF:
                continue

            # If the thermostat reports that it is heating, we can assume the valves are open
            if state.attributes.get("hvac_action") == HVACAction.HEATING:
                return True

            # If the thermostat does not support hvac action, we can assume the valves are
            # open if the current temperature is not at the target temperature
            if state.attributes.get("hvac_action") is None:
                target_temperature = state.attributes.get("temperature")
                current_temperature = state.attributes.get("current_temperature")

                # If there is a current temperature, and it is not at the target temperature, we can assume the valves are open
                if current_temperature is not None and float(target_temperature) >= float(current_temperature) + float(self._climate_valve_offset):
                    return True

        # If none of the thermostats have open valves, return False
        return False

    @property
    def pulse_width_modulation_enabled(self) -> bool:
        """Return True if pulse width modulation is enabled, False otherwise."""
        if not self._coordinator.supports_setpoint_management or self._force_pulse_width_modulation:
            return True

        return self._overshoot_protection and self._calculate_control_setpoint() < self.minimum_setpoint

    @property
    def relative_modulation_value(self) -> int:
        return self._maximum_relative_modulation if self._relative_modulation.enabled else MINIMUM_RELATIVE_MOD

    @property
    def relative_modulation_state(self) -> RelativeModulationState:
        return self._relative_modulation.state

    @property
    def warming_up(self) -> bool:
        """Return True if we are warming up, False otherwise."""
        return self._warming_up_data is not None and self._warming_up_data.elapsed < HEATER_STARTUP_TIMEFRAME

    @property
    def minimum_setpoint(self) -> float:
        if not self._dynamic_minimum_setpoint:
            return self._coordinator.minimum_setpoint

        return min(self.adjusted_minimum_setpoint, self._coordinator.maximum_setpoint)

    @property
    def adjusted_minimum_setpoint(self) -> float:
        return self._minimum_setpoint.current()

    def _calculate_control_setpoint(self) -> float:
        """Calculate the control setpoint based on the heating curve and PID output."""
        if self.heating_curve.value is None:
            return MINIMUM_SETPOINT

        # Combine the heating curve value and the calculated output from the pid controller
        requested_setpoint = self.requested_setpoint

        # Ensure setpoint is limited to our max
        return min(requested_setpoint, self._coordinator.maximum_setpoint)

    async def _async_inside_sensor_changed(self, event: Event) -> None:
        """Handle changes to the inside temperature sensor."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        _LOGGER.debug("Inside Sensor Changed.")
        self._current_temperature = float(new_state.state)
        self.async_write_ha_state()

        await self._async_control_pid()
        await self.async_control_heating_loop()

    async def _async_outside_entity_changed(self, event: Event) -> None:
        """Handle changes to the outside entity."""
        if event.data.get("new_state") is None:
            return

        _LOGGER.debug("Outside Sensor Changed.")
        self.async_write_ha_state()

        await self._async_control_pid()
        await self.async_control_heating_loop()

    async def _async_humidity_sensor_changed(self, event: Event) -> None:
        """Handle changes to the inside temperature sensor."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        _LOGGER.debug("Humidity Sensor Changed.")
        self._current_humidity = float(new_state.state)
        self.async_write_ha_state()

        await self._async_control_pid()
        await self.async_control_heating_loop()

    async def _async_main_climate_changed(self, event: Event) -> None:
        """Handle changes to the main climate entity."""
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        if old_state is None or new_state.state != old_state.state:
            _LOGGER.debug(f"Main Climate State Changed ({new_state.entity_id}).")
            await self.async_control_heating_loop()

    async def _async_climate_changed(self, event: Event) -> None:
        """Handle changes to the climate entity.
        If the state, target temperature, or current temperature of the climate
        entity has changed, update the PID controller and heating control.
        """
        # Get the new state of the climate entity
        new_state = event.data.get("new_state")

        # Return if the new state is not available
        if not new_state:
            return

        # Get the old state of the climate entity
        old_state = event.data.get("old_state")

        # Get the attributes of the new state
        new_attrs = new_state.attributes

        # Get the attributes of the old state, if available
        old_attrs = old_state.attributes if old_state else {}

        _LOGGER.debug(f"Climate State Changed ({new_state.entity_id}).")

        # Check if the last state is None, so we can track the attached sensor if needed
        if old_state is None and (sensor_temperature_id := new_attrs.get(SENSOR_TEMPERATURE_ID)):
            await self.async_track_sensor_temperature(sensor_temperature_id)

        # If the state has changed or the old state is not available, update the PID controller
        if not old_state or new_state.state != old_state.state:
            await self._async_control_pid(True)

        # If the target temperature has changed, update the PID controller
        elif new_attrs.get("temperature") != old_attrs.get("temperature"):
            await self._async_control_pid(True)

        # If the current temperature has changed, update the PID controller
        elif not hasattr(new_state.attributes, SENSOR_TEMPERATURE_ID) and new_attrs.get("current_temperature") != old_attrs.get(
                "current_temperature"):
            await self._async_control_pid(False)

        if (self._rooms is not None and new_state.entity_id not in self._rooms) or self.preset_mode in [PRESET_HOME, PRESET_COMFORT]:
            if target_temperature := new_state.attributes.get("temperature"):
                self._rooms[new_state.entity_id] = float(target_temperature)

        # Update the heating control
        await self.async_control_heating_loop()

    async def _async_temperature_change(self, event: Event) -> None:
        """Handle changes to the climate sensor entity.
        If the current temperature of the sensor entity has changed,
        update the PID controller and heating control.
        """
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        _LOGGER.debug(f"Climate Sensor Changed ({new_state.entity_id}).")
        await self._async_control_pid(False)
        await self.async_control_heating_loop()

    async def _async_window_sensor_changed(self, event: Event) -> None:
        """Handle changes to the contact sensor entity."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        _LOGGER.debug(f"Window Sensor Changed to {new_state.state}.")

        if new_state.state == STATE_ON:
            if self.preset_mode == PRESET_ACTIVITY:
                return

            try:
                self._window_sensor_handle = asyncio.create_task(asyncio.sleep(self._window_minimum_open_time))
                self._pre_activity_temperature = self.target_temperature

                await self._window_sensor_handle
                await self.async_set_preset_mode(PRESET_ACTIVITY)
            except asyncio.CancelledError:
                _LOGGER.debug("Window closed before minimum open time.")

            return

        if new_state.state == STATE_OFF:
            if self._window_sensor_handle is not None:
                self._window_sensor_handle.cancel()
                self._window_sensor_handle = None

            if self.preset_mode == PRESET_ACTIVITY:
                _LOGGER.debug(f"Restoring original target temperature.")
                await self.async_set_temperature(temperature=self._pre_activity_temperature)

            return

    async def _async_control_pid(self, reset: bool = False) -> None:
        """Control the PID controller."""
        # We can't continue if we don't have a valid outside temperature
        if self.current_outside_temperature is None:
            return

        # Reset the PID controller if the sensor data is too old
        if self._sensor_max_value_age != 0 and monotonic() - self.pid.last_updated > self._sensor_max_value_age:
            self.pid.reset()

        # Calculate the maximum error between the current temperature and the target temperature of all climates
        max_error = self.max_error

        # Make sure we use the latest heating curve value
        self.heating_curve.update(
            target_temperature=self.target_temperature,
            outside_temperature=self.current_outside_temperature,
        )

        # Update the PID controller with the maximum error
        if not reset:
            _LOGGER.info(f"Updating error value to {max_error} (Reset: False)")

            # Calculate an optimal heating curve when we are in the deadband
            if -DEADBAND <= max_error <= DEADBAND:
                self.heating_curve.autotune(
                    setpoint=self.requested_setpoint,
                    target_temperature=self.target_temperature,
                    outside_temperature=self.current_outside_temperature
                )

            # Since we are in the deadband, we can safely assume we are not warming up anymore
            if self._warming_up_data and max_error <= DEADBAND:
                # Calculate the derivative per hour
                self._warming_up_derivative = calculate_derivative_per_hour(
                    self._warming_up_data.error,
                    self._warming_up_data.elapsed
                )

                # Notify that we are not warming anymore
                _LOGGER.info("Reached deadband, turning off warming up.")
                self._warming_up_data = None

            self.pid.update(
                error=max_error,
                heating_curve_value=self.heating_curve.value,
                boiler_temperature=self._coordinator.filtered_boiler_temperature
            )
        elif max_error != self.pid.last_error:
            _LOGGER.info(f"Updating error value to {max_error} (Reset: True)")

            self.pid.update_reset(error=max_error, heating_curve_value=self.heating_curve.value)
            self._calculated_setpoint = None
            self.pwm.reset()

            # Determine if we are warming up
            if self.max_error > DEADBAND:
                self._warming_up_data = SatWarmingUp(self.max_error, self._coordinator.boiler_temperature)
                _LOGGER.info("Outside of deadband, we are warming up")

        self.async_write_ha_state()

    async def _async_control_setpoint(self, pwm_state: PWMState) -> None:
        """Control the setpoint of the heating system."""
        if self.hvac_mode == HVACMode.HEAT:
            if not self.pulse_width_modulation_enabled or pwm_state == pwm_state.IDLE:
                _LOGGER.info("Running Normal cycle")
                self._setpoint = self._calculated_setpoint
            else:
                _LOGGER.info(f"Running PWM cycle: {pwm_state}")
                self._setpoint = self.minimum_setpoint if pwm_state == pwm_state.ON else MINIMUM_SETPOINT
        else:
            self._calculated_setpoint = None
            self._setpoint = MINIMUM_SETPOINT

        await self._coordinator.async_set_control_setpoint(self._setpoint)

    async def _async_control_relative_modulation(self) -> None:
        """Control the relative modulation value based on the conditions"""
        if self._coordinator.supports_relative_modulation_management:
            await self._relative_modulation.update(self.warming_up, self.pwm.state)
            await self._coordinator.async_set_control_max_relative_modulation(self.relative_modulation_value)

    async def _async_update_rooms_from_climates(self) -> None:
        """Update the temperature setpoint for each room based on their associated climate entity."""
        self._rooms = {}

        # Iterate through each climate entity
        for entity_id in self._climates:
            state = self.hass.states.get(entity_id)

            # Skip any entities that are unavailable or have an unknown state
            if not state or state.state in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
                continue

            # Retrieve the target temperature from the climate entity's attributes
            target_temperature = state.attributes.get("temperature")

            # If the target temperature exists, store it in the _rooms dictionary with the climate entity as the key
            if target_temperature is not None:
                self._rooms[entity_id] = float(target_temperature)

    async def async_track_sensor_temperature(self, entity_id):
        """
        Track the temperature of the sensor specified by the given entity_id.

        Parameters:
        entity_id (str): The entity id of the sensor to track.

        If the sensor is already being tracked, the method will return without doing anything.
        Otherwise, it will register a callback for state changes on the specified sensor and start tracking its temperature.
        """
        if entity_id in self._sensors:
            return

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [entity_id], self._async_temperature_change
            )
        )

        self._sensors.append(entity_id)

    async def async_control_heating_loop(self, _time=None) -> None:
        """Control the heating based on current temperature, target temperature, and outside temperature."""
        # If the current, target or outside temperature is not available, do nothing
        if self.current_temperature is None or self.target_temperature is None or self.current_outside_temperature is None:
            return

        # Control the heating through the coordinator
        await self._coordinator.async_control_heating_loop(self)

        if self._calculated_setpoint is None:
            # Default to the calculated setpoint
            self._calculated_setpoint = self._calculate_control_setpoint()
        else:
            # Apply low filter on requested setpoint
            self._calculated_setpoint = round(self._alpha * self._calculate_control_setpoint() + (1 - self._alpha) * self._calculated_setpoint, 1)

        # Pulse Width Modulation
        if self.pulse_width_modulation_enabled:
            await self.pwm.update(self._calculated_setpoint, self._coordinator.boiler_temperature)
        else:
            self.pwm.reset()

        # Set the control setpoint to make sure we always stay in control
        await self._async_control_setpoint(self.pwm.state)

        # Set the relative modulation value, if supported
        await self._async_control_relative_modulation()

        # Control the integral (if exceeded the time limit)
        self.pid.update_integral(self.max_error, self.heating_curve.value)

        if not self._coordinator.hot_water_active and self._coordinator.flame_active:
            # Calculate the base return temperature
            if self.warming_up:
                self._minimum_setpoint.warming_up(self._coordinator.return_temperature)

            # Calculate the dynamic minimum setpoint
            self._minimum_setpoint.calculate(self._coordinator.return_temperature)

        # If the setpoint is high and the HVAC is not off, turn on the heater
        if self._setpoint > MINIMUM_SETPOINT and self.hvac_mode != HVACMode.OFF:
            await self.async_set_heater_state(DeviceState.ON)
        else:
            await self.async_set_heater_state(DeviceState.OFF)

        self.async_write_ha_state()

    async def async_set_heater_state(self, state: DeviceState):
        if state == DeviceState.ON and not self.valves_open:
            _LOGGER.warning('No valves are open at the moment.')
            return await self._coordinator.async_set_heater_state(DeviceState.OFF)

        return await self._coordinator.async_set_heater_state(state)

    async def async_set_temperature(self, **kwargs) -> None:
        """Set the target temperature."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return

        # Automatically select the preset
        for preset in self._presets:
            if float(self._presets[preset]) == float(temperature):
                return await self.async_set_preset_mode(preset)

        self._attr_preset_mode = PRESET_NONE
        await self.async_set_target_temperature(temperature)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set the heating/cooling mode for the devices and update the state."""
        # Only allow the hvac mode to be set to heat or off
        if hvac_mode == HVACMode.HEAT:
            self._hvac_mode = HVACMode.HEAT
        elif hvac_mode == HVACMode.OFF:
            self._hvac_mode = HVACMode.OFF
        else:
            # If an unsupported mode is passed, log an error message
            _LOGGER.error("Unrecognized hvac mode: %s", hvac_mode)
            return

        # Reset the PID controller
        await self._async_control_pid(True)

        # Collect which climates to control
        climates = self._main_climates[:]
        if self._sync_climates_with_mode:
            climates += self._climates

        # Set the hvac mode for those climate devices
        for entity_id in climates:
            data = {ATTR_ENTITY_ID: entity_id, ATTR_HVAC_MODE: hvac_mode}
            await self.hass.services.async_call(CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE, data, blocking=True)

        # Update the state and control the heating
        self.async_write_ha_state()
        await self.async_control_heating_loop()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the preset mode for the thermostat."""
        # Check if the given preset mode is valid
        if preset_mode not in self.preset_modes:
            raise ValueError(f"Got unsupported preset_mode {preset_mode}. Must be one of {self.preset_modes}")

        # Return if the given preset mode is already set
        if preset_mode == self._attr_preset_mode:
            return

        # Reset the preset mode if `PRESET_NONE` is given
        if preset_mode == PRESET_NONE:
            self._attr_preset_mode = PRESET_NONE
            await self.async_set_target_temperature(self._pre_custom_temperature)
        else:
            # Save the current target temperature if the preset mode is being set for the first time
            if self._attr_preset_mode == PRESET_NONE:
                self._pre_custom_temperature = self._target_temperature

            # Set the preset mode and target temperature
            self._attr_preset_mode = preset_mode
            await self.async_set_target_temperature(self._presets[preset_mode])

            # Set the temperature for each room, when enabled
            if self._sync_climates_with_preset:
                for entity_id in self._climates:
                    state = self.hass.states.get(entity_id)
                    if state is None or state.state == HVACMode.OFF:
                        continue

                    target_temperature = self._presets[preset_mode]
                    if preset_mode == PRESET_HOME or preset_mode == PRESET_COMFORT:
                        target_temperature = self._rooms[entity_id]

                    data = {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: target_temperature}
                    await self.hass.services.async_call(CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, data, blocking=True)

    async def async_set_target_temperature(self, temperature: float) -> None:
        """Set the temperature setpoint for all main climates."""
        if self._target_temperature == temperature:
            return

        # Set the new target temperature
        self._target_temperature = temperature

        # Set the target temperature for each main climate
        for entity_id in self._main_climates:
            data = {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: temperature}
            await self.hass.services.async_call(CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, data, blocking=True)

        if self._sync_with_thermostat:
            # Set the target temperature for the connected boiler
            await self._coordinator.async_set_control_thermostat_setpoint(temperature)

        # Reset the PID controller
        await self._async_control_pid(True)

        # Write the state to Home Assistant
        self.async_write_ha_state()

        # Control the heating based on the new temperature setpoint
        await self.async_control_heating_loop()

    async def async_send_notification(self, title: str, message: str, service: str = SERVICE_PERSISTENT_NOTIFICATION):
        """Send a notification to the user."""
        data = {"title": title, "message": message}
        await self.hass.services.async_call(NOTIFY_DOMAIN, service, data)
