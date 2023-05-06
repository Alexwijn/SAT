"""Climate platform for SAT."""
from __future__ import annotations

import logging
from collections import deque
from datetime import timedelta
from statistics import mean
from time import monotonic
from typing import List

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
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
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.weather import DOMAIN as WEATHER_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, STATE_UNAVAILABLE, STATE_UNKNOWN, ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall, Event
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt

from .config_store import SatConfigStore
from .const import *
from .coordinator import SatDataUpdateCoordinator, DeviceState
from .entity import SatEntity
from .heating_curve import HeatingCurve
from .pid import PID
from .pwm import PWM, PWMState

ATTR_ROOMS = "rooms"
SENSOR_TEMPERATURE_ID = "sensor_temperature_id"

_LOGGER = logging.getLogger(__name__)


def convert_time_str_to_seconds(time_str: str) -> float:
    """Convert a time string in the format 'HH:MM:SS' to seconds.

    Args:
        time_str: A string representing a time in the format 'HH:MM:SS'.

    Returns:
        float: The time in seconds.
    """
    date_time = dt.parse_time(time_str)
    # Calculate the number of seconds by multiplying the hours, minutes and seconds
    return (date_time.hour * 3600) + (date_time.minute * 60) + date_time.second


def create_pid_controller(options) -> PID:
    """Create and return a PID controller instance with the given configuration options."""
    # Extract the configuration options
    kp = float(options.get(CONF_PROPORTIONAL))
    ki = float(options.get(CONF_INTEGRAL))
    kd = float(options.get(CONF_DERIVATIVE))
    heating_system = options.get(CONF_HEATING_SYSTEM)
    automatic_gains = bool(options.get(CONF_AUTOMATIC_GAINS))
    sample_time_limit = convert_time_str_to_seconds(options.get(CONF_SAMPLE_TIME))

    # Return a new PID controller instance with the given configuration options
    return PID(kp=kp, ki=ki, kd=kd, heating_system=heating_system, automatic_gains=automatic_gains, sample_time_limit=sample_time_limit)


def create_heating_curve_controller(options) -> HeatingCurve:
    """Create and return a PID controller instance with the given configuration options."""
    # Extract the configuration options
    heating_system = options.get(CONF_HEATING_SYSTEM)
    coefficient = float(options.get(CONF_HEATING_CURVE_COEFFICIENT))

    # Return a new heating Curve controller instance with the given configuration options
    return HeatingCurve(heating_system=heating_system, coefficient=coefficient)


def create_pwm_controller(heating_curve: HeatingCurve, store: SatConfigStore, options) -> PWM | None:
    """Create and return a PWM controller instance with the given configuration options."""
    # Extract the configuration options
    force = bool(options.get(CONF_MODE) == MODE_SWITCH)
    automatic_duty_cycle = bool(options.get(CONF_AUTOMATIC_DUTY_CYCLE))
    max_cycle_time = int(convert_time_str_to_seconds(options.get(CONF_DUTY_CYCLE)))

    # Return a new PWM controller instance with the given configuration options
    return PWM(store=store, heating_curve=heating_curve, max_cycle_time=max_cycle_time, automatic_duty_cycle=automatic_duty_cycle, force=force)


async def async_setup_entry(_hass: HomeAssistant, _config_entry: ConfigEntry, _async_add_devices: AddEntitiesCallback):
    """Set up the SatClimate device."""
    coordinator = _hass.data[DOMAIN][_config_entry.entry_id][COORDINATOR]
    climate = SatClimate(coordinator, _config_entry, _hass.config.units.temperature_unit)

    _async_add_devices([climate])
    _hass.data[DOMAIN][_config_entry.entry_id][CLIMATE] = climate


class SatClimate(SatEntity, ClimateEntity, RestoreEntity):
    def __init__(self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry, unit: str):
        super().__init__(coordinator, config_entry)

        self._store = coordinator.store

        # Create dictionary mapping preset keys to temperature options
        conf_presets = {p: f"{p}_temperature" for p in (PRESET_AWAY, PRESET_HOME, PRESET_SLEEP, PRESET_COMFORT)}

        # Create dictionary mapping preset keys to temperature values
        self._presets = {key: self._store.options[value] for key, value in conf_presets.items() if value in self._store.options}

        # Create PID controller with given configuration options
        self._pid = create_pid_controller(self._store.options)

        # Get inside sensor entity ID
        self.inside_sensor_entity_id = self._store.options.get(CONF_INSIDE_SENSOR_ENTITY_ID)

        # Get inside sensor entity state
        inside_sensor_entity = coordinator.hass.states.get(self.inside_sensor_entity_id)

        # Get current temperature
        self._current_temperature = None
        if inside_sensor_entity is not None and inside_sensor_entity.state not in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
            self._current_temperature = float(inside_sensor_entity.state)

        # Get outside sensor entity IDs
        self.outside_sensor_entities = config_entry.data.get(CONF_OUTSIDE_SENSOR_ENTITY_ID)

        # If outside sensor entity IDs is a string, make it a list
        if isinstance(self.outside_sensor_entities, str):
            self.outside_sensor_entities = [self.outside_sensor_entities]

        # Create Heating Curve controller with given configuration options
        self._heating_curve = create_heating_curve_controller(self._store.options)

        # Create PWM controller with given configuration options
        self._pwm = create_pwm_controller(self._heating_curve, self._store, self._store.options)

        self._sensors = []
        self._rooms = None
        self._setpoint = None
        self._warming_up = False
        self._outputs = deque(maxlen=50)

        self._hvac_mode = None
        self._target_temperature = None
        self._saved_target_temperature = None
        self._overshoot_protection_calculate = False

        self._climates = self._store.options.get(CONF_CLIMATES)
        self._main_climates = self._store.options.get(CONF_MAIN_CLIMATES)

        self._simulation = bool(self._store.options.get(CONF_SIMULATION))
        self._heating_system = str(self._store.options.get(CONF_HEATING_SYSTEM))
        self._overshoot_protection = bool(self._store.options.get(CONF_OVERSHOOT_PROTECTION))
        self._climate_valve_offset = float(self._store.options.get(CONF_CLIMATE_VALVE_OFFSET))
        self._target_temperature_step = float(self._store.options.get(CONF_TARGET_TEMPERATURE_STEP))
        self._sync_climates_with_preset = bool(self._store.options.get(CONF_SYNC_CLIMATES_WITH_PRESET))
        self._force_pulse_width_modulation = bool(self._store.options.get(CONF_FORCE_PULSE_WIDTH_MODULATION))
        self._sensor_max_value_age = convert_time_str_to_seconds(self._store.options.get(CONF_SENSOR_MAX_VALUE_AGE))

        self._attr_name = str(config_entry.data.get(CONF_NAME))
        self._attr_id = str(config_entry.data.get(CONF_NAME)).lower()

        self._attr_temperature_unit = unit
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_preset_mode = PRESET_NONE
        self._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
        self._attr_preset_modes = [PRESET_NONE] + list(self._presets.keys())
        self._attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE

        if self._simulation:
            _LOGGER.warning("Simulation mode!")

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        # Register event listeners
        await self._register_event_listeners()

        # Restore previous state if available, or set default values
        await self._restore_previous_state_or_set_defaults()

        # Update heating curve if outside temperature is available
        if self.current_outside_temperature is not None:
            self._heating_curve.update(self.target_temperature, self.current_outside_temperature)

        # Start control loop
        await self._async_control_heating_loop()

        # Register services
        await self._register_services()

        # Let the coordinator know we are ready
        await self._coordinator.async_added_to_hass(self)

    async def _register_event_listeners(self):
        """Register event listeners."""
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self.inside_sensor_entity_id], self._async_inside_sensor_changed
            )
        )

        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._async_control_heating_loop, timedelta(seconds=30)
            )
        )

        for entity_id in self.outside_sensor_entities:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [entity_id], self._async_outside_entity_changed
                )
            )

        for climate_id in self._main_climates:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [climate_id], self._async_main_climate_changed
                )
            )

        for climate_id in self._climates:
            state = self.hass.states.get(climate_id)
            if state is not None and (sensor_temperature_id := state.attributes.get(SENSOR_TEMPERATURE_ID)):
                await self.track_sensor_temperature(sensor_temperature_id)

            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [climate_id], self._async_climate_changed
                )
            )

    async def _restore_previous_state_or_set_defaults(self):
        """Restore previous state if available, or set default values."""
        old_state = await self.async_get_last_state()

        if old_state is not None:
            if self._target_temperature is None:
                if old_state.attributes.get(ATTR_TEMPERATURE) is None:
                    self._pid.setpoint = self.min_temp
                    self._target_temperature = self.min_temp
                    _LOGGER.warning("Undefined target temperature, falling back to %s", self._target_temperature, )
                else:
                    self._pid.restore(old_state)
                    self._target_temperature = float(old_state.attributes[ATTR_TEMPERATURE])

            if old_state.state:
                self._hvac_mode = old_state.state

            if old_state.attributes.get(ATTR_PRESET_MODE):
                self._attr_preset_mode = old_state.attributes.get(ATTR_PRESET_MODE)

            if old_state.attributes.get(ATTR_ROOMS):
                self._rooms = old_state.attributes.get(ATTR_ROOMS)
            else:
                await self._async_update_rooms_from_climates()
        else:
            if self._rooms is None:
                await self._async_update_rooms_from_climates()

            if self._target_temperature is None:
                self._pid.setpoint = self.min_temp
                self._target_temperature = self.min_temp
                _LOGGER.warning("No previously saved temperature, setting to %s", self._target_temperature)

            if not self._hvac_mode:
                self._hvac_mode = HVACMode.OFF

    async def _register_services(self):
        async def reset_integral(_call: ServiceCall):
            """Service to reset the integral part of the PID controller."""
            self._pid.reset()

        async def set_overshoot_protection_value(_call: ServiceCall):
            """Service to set the overshoot protection value."""
            self._store.update(STORAGE_OVERSHOOT_PROTECTION_VALUE, _call.data.get("value"))

        self.hass.services.async_register(DOMAIN, SERVICE_RESET_INTEGRAL, reset_integral)
        self.hass.services.async_register(DOMAIN, SERVICE_SET_OVERSHOOT_PROTECTION_VALUE, set_overshoot_protection_value)

    async def track_sensor_temperature(self, entity_id):
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

    @property
    def name(self):
        """Return the friendly name of the sensor."""
        return self._attr_name

    @property
    def extra_state_attributes(self):
        """Return device state attributes."""
        return {
            "error": self._pid.last_error,
            "integral": self._pid.integral,
            "derivative": self._pid.derivative,
            "proportional": self._pid.proportional,
            "history_size": self._pid.history_size,
            "collected_errors": self._pid.num_errors,
            "integral_enabled": self._pid.integral_enabled,

            "derivative_enabled": self._pid.derivative_enabled,
            "derivative_raw": self._pid.raw_derivative,

            "current_kp": self._pid.kp,
            "current_ki": self._pid.ki,
            "current_kd": self._pid.kd,

            "rooms": self._rooms,
            "setpoint": self._setpoint,
            "warming_up": self._warming_up,
            "valves_open": self.valves_open,
            "heating_curve": self._heating_curve.value,
            "outside_temperature": self.current_outside_temperature,
            "optimal_coefficient": self._heating_curve.optimal_coefficient,
            "pulse_width_modulation_enabled": self.pulse_width_modulation_enabled,
            "pulse_width_modulation_state": self._pwm.state,
            "pulse_width_modulation_duty_cycle": self._pwm.duty_cycle,
            "overshoot_protection_calculating": self._overshoot_protection_calculate,
            "overshoot_protection_value": self._store.get(STORAGE_OVERSHOOT_PROTECTION_VALUE),
        }

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return self._attr_id

    @property
    def current_temperature(self):
        """Return the sensor temperature."""
        return self._current_temperature

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._target_temperature

    @property
    def error(self):
        """Return the error value."""
        if self._target_temperature is None or self._current_temperature is None:
            return 0

        return round(self._target_temperature - self._current_temperature, 2)

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

        if not self._coordinator.device_state == DeviceState.ON:
            return HVACAction.IDLE

        return HVACAction.HEATING

    @property
    def max_error(self) -> float:
        return max([self.error] + self.climate_errors)

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

            # Retrieve the overriden sensor temperature if set
            if sensor_temperature_id := state.attributes.get(SENSOR_TEMPERATURE_ID):
                sensor_state = self.hass.states.get(sensor_temperature_id)
                if sensor_state is not None and sensor_state.state not in [STATE_UNKNOWN, STATE_UNAVAILABLE, HVACMode.OFF]:
                    current_temperature = float(sensor_state.state)

            errors.append(round(target_temperature - current_temperature, 2))

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
        """Return True if pulse width modulation is enabled, False otherwise.

        If an overshoot protection value is not set, pulse width modulation is disabled.
        If we are a coordinator that doesn't support it, it is enabled.
        If pulse width modulation is forced on, it is enabled.
        If overshoot protection is enabled, and we are below the overshoot protection value.
        """
        if (overshoot_protection_value := self._store.get(STORAGE_OVERSHOOT_PROTECTION_VALUE)) is None:
            return False

        if not self._coordinator.supports_setpoint_management or self._force_pulse_width_modulation:
            return True

        if self._overshoot_protection:
            if self.max_error <= 0.1:
                return True

            if self._warming_up and self._setpoint < (overshoot_protection_value - 2):
                return True

        return False

    def _get_requested_setpoint(self):
        return max(self._heating_curve.value + self._pid.output, MINIMUM_SETPOINT)

    def _calculate_control_setpoint(self) -> float:
        """Calculate the control setpoint based on the heating curve and PID output."""
        if self._heating_curve.value is None:
            return MINIMUM_SETPOINT

        # Combine the heating curve value and the calculated output from the pid controller
        requested_setpoint = self._get_requested_setpoint()

        # Make sure we are above the base setpoint when we are below target temperature
        if self.max_error > 0:
            requested_setpoint = max(requested_setpoint, self._heating_curve.value)

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
        await self._async_control_heating_loop()

    async def _async_outside_entity_changed(self, event: Event) -> None:
        """Handle changes to the outside entity."""
        if event.data.get("new_state") is None:
            return

        await self._async_control_heating_loop()

    async def _async_main_climate_changed(self, event: Event) -> None:
        """Handle changes to the main climate entity."""
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        if old_state is None or new_state.state != old_state.state:
            _LOGGER.debug(f"Main Climate State Changed ({new_state.entity_id}).")
            await self._async_control_heating_loop()

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
            await self.track_sensor_temperature(sensor_temperature_id)

        # If the state has changed or the old state is not available, update the PID controller
        if not old_state or new_state.state != old_state.state:
            await self._async_control_pid(True)

        # If the target temperature has changed, update the PID controller
        elif new_attrs.get("temperature") != old_attrs.get("temperature"):
            await self._async_control_pid(True)

        # If current temperature has changed, update the PID controller
        elif not hasattr(new_state.attributes, SENSOR_TEMPERATURE_ID) and new_attrs.get("current_temperature") != old_attrs.get("current_temperature"):
            await self._async_control_pid(False)

        if (self._rooms is not None and new_state.entity_id not in self._rooms) or self.preset_mode in [PRESET_HOME, PRESET_COMFORT]:
            if target_temperature := new_state.attributes.get("temperature"):
                self._rooms[new_state.entity_id] = float(target_temperature)

        # Update the heating control
        await self._async_control_heating_loop()

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
        await self._async_control_heating_loop()

    async def _async_control_heating_loop(self, _time=None) -> None:
        """Control the heating based on current temperature, target temperature, and outside temperature."""
        # If overshoot protection is active, we are not doing anything since we already have task running in async
        if self._overshoot_protection_calculate:
            return

        # If the current, target or outside temperature is not available, do nothing
        if self.current_temperature is None or self.target_temperature is None or self.current_outside_temperature is None:
            return

        # Control the heating through the coordinator
        await self._coordinator.async_control_heating_loop(self)

        # Pulse Width Modulation
        if self._overshoot_protection or self._force_pulse_width_modulation:
            await self._pwm.update(self._get_requested_setpoint())

        # Set the control setpoint to make sure we always stay in control
        await self._async_control_setpoint(self._pwm.state)

        # Control the integral (if exceeded the time limit)
        self._pid.update_integral(self.max_error, self._heating_curve.value)

        if self._coordinator.device_state == DeviceState.ON:
            # If the setpoint is too low or the valves are closed or HVAC is off, turn off the heater
            if self._setpoint <= MINIMUM_SETPOINT or not self.valves_open or self.hvac_mode == HVACMode.OFF:
                await self._coordinator.async_set_heater_state(DeviceState.OFF)
            else:
                await self._coordinator.async_set_heater_state(DeviceState.ON)

        if self._coordinator.device_state == DeviceState.OFF:
            # If the setpoint is high and the valves are open and the HVAC is not off, turn on the heater
            if self._setpoint > MINIMUM_SETPOINT and self.valves_open and self.hvac_mode != HVACMode.OFF:
                await self._coordinator.async_set_heater_state(DeviceState.ON)
            else:
                await self._coordinator.async_set_heater_state(DeviceState.OFF)

        self.async_write_ha_state()

    async def _async_control_pid(self, reset: bool = False):
        """Control the PID controller."""
        # We can't continue if we don't have a valid outside temperature
        if self.current_outside_temperature is None:
            return

        # Reset the PID controller if the sensor data is too old
        if self._sensor_max_value_age != 0 and monotonic() - self._pid.last_updated > self._sensor_max_value_age:
            self._pid.reset()

        # Calculate the maximum error between the current temperature and the target temperature of all climates
        max_error = self.max_error

        # Make sure we use the latest heating curve value
        self._heating_curve.update(
            target_temperature=self.target_temperature,
            outside_temperature=self.current_outside_temperature,
        )

        # Update the PID controller with the maximum error
        if not reset:
            _LOGGER.info(f"Updating error value to {max_error} (Reset: False)")

            # Calculate optimal heating curve when we are in the deadband
            if -0.1 <= max_error <= 0.1:
                self._heating_curve.autotune(
                    setpoint=self._get_requested_setpoint(),
                    target_temperature=self.target_temperature,
                    outside_temperature=self.current_outside_temperature
                )

            # Since we are in the deadband we can safely assume we are not warming up anymore
            if self._warming_up and max_error <= 0.1:
                self._warming_up = False
                _LOGGER.info("Reached deadband, turning off warming up.")

            # Update the pid controller
            self._pid.update(error=max_error, heating_curve_value=self._heating_curve.value)
        elif max_error != self._pid.last_error:
            _LOGGER.info(f"Updating error value to {max_error} (Reset: True)")

            self._pid.update_reset(error=max_error, heating_curve_value=self._heating_curve.value)
            self._outputs.clear()
            self._pwm.reset()

            # Determine if we are warming up
            if self.max_error > 0.1:
                self._warming_up = True
                _LOGGER.info("Outside of deadband, we are warming up")

        self.async_write_ha_state()

    async def _async_control_setpoint(self, pwm_state: PWMState):
        """Control the setpoint of the heating system."""
        if self.hvac_mode == HVACMode.HEAT:
            if self.pulse_width_modulation_enabled and pwm_state != pwm_state.IDLE:
                self._setpoint = self._store.get(STORAGE_OVERSHOOT_PROTECTION_VALUE) if pwm_state == pwm_state.ON else MINIMUM_SETPOINT
                _LOGGER.info(f"Running pulse width modulation cycle: {pwm_state}")
            else:
                self._outputs.append(self._calculate_control_setpoint())
                self._setpoint = mean(list(self._outputs)[-5:])
                _LOGGER.info("Running normal cycle")
        else:
            self._outputs.clear()
            self._setpoint = MINIMUM_SETPOINT

        await self._coordinator.async_control_setpoint(self._setpoint)

    async def _async_update_rooms_from_climates(self):
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

    async def async_set_temperature(self, **kwargs) -> None:
        """Set the target temperature."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return

        # Ignore the request when we are in calculation mode
        if self._overshoot_protection_calculate:
            return

        # Automatically select the preset
        for preset in self._presets:
            if float(self._presets[preset]) == float(temperature):
                return await self.async_set_preset_mode(preset)

        self._attr_preset_mode = PRESET_NONE
        await self.async_set_target_temperature(temperature)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the preset mode for the thermostat."""
        # Check if the given preset mode is valid
        if preset_mode not in self.preset_modes:
            raise ValueError(f"Got unsupported preset_mode {preset_mode}. Must be one of {self.preset_modes}")

        # Ignore the request when we are in calculation mode
        if self._overshoot_protection_calculate:
            return

        # Return if the given preset mode is already set
        if preset_mode == self._attr_preset_mode:
            return

        # Reset the preset mode if `PRESET_NONE` is given
        if preset_mode == PRESET_NONE:
            self._attr_preset_mode = PRESET_NONE
            await self.async_set_target_temperature(self._saved_target_temperature)
        else:
            # Set the HVAC mode to `HEAT` if it is currently `OFF`
            if self.hvac_mode == HVACMode.OFF:
                await self.async_set_hvac_mode(HVACMode.HEAT)

            # Save the current target temperature if the preset mode is being set for the first time
            if self._attr_preset_mode == PRESET_NONE:
                self._saved_target_temperature = self._target_temperature

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

    async def async_set_target_temperature(self, temperature: float):
        """Set the temperature setpoint for all main climates."""
        if self._target_temperature == temperature:
            return

        # Set the new target temperature
        self._target_temperature = temperature

        # Reset the PID controller
        await self._async_control_pid(True)

        # Set the temperature for each main climate
        for entity_id in self._main_climates:
            data = {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: temperature}
            await self.hass.services.async_call(CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, data, blocking=True)

        # Write the state to Home Assistant
        self.async_write_ha_state()

        # Control the heating based on the new temperature setpoint
        await self._async_control_heating_loop()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set the heating/cooling mode for the devices and update the state."""
        # Ignore the request when we are in calculation mode
        if self._overshoot_protection_calculate:
            return

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

        # Set the hvac mode for all climate devices
        for entity_id in (self._climates + self._main_climates):
            data = {ATTR_ENTITY_ID: entity_id, ATTR_HVAC_MODE: hvac_mode}
            await self.hass.services.async_call(CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE, data, blocking=True)

        # Update the state and control the heating
        self.async_write_ha_state()
        await self._async_control_heating_loop()
