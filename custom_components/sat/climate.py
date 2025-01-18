"""Climate platform for SAT."""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import timedelta, datetime
from time import monotonic, time
from typing import Optional

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
from homeassistant.const import ATTR_TEMPERATURE, STATE_UNAVAILABLE, STATE_UNKNOWN, ATTR_ENTITY_ID, STATE_ON, STATE_OFF, EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant, ServiceCall, Event, CoreState
from homeassistant.helpers import entity_registry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity

from .area import Areas, SENSOR_TEMPERATURE_ID
from .boiler import BoilerState
from .const import *
from .coordinator import SatDataUpdateCoordinator, DeviceState, DeviceStatus
from .entity import SatEntity
from .helpers import convert_time_str_to_seconds, seconds_since
from .pwm import PWMState
from .relative_modulation import RelativeModulation, RelativeModulationState
from .setpoint_adjuster import SetpointAdjuster
from .summer_simmer import SummerSimmer
from .util import create_pid_controller, create_heating_curve_controller, create_pwm_controller, create_minimum_setpoint_controller

ATTR_ROOMS = "rooms"
ATTR_SETPOINT = "setpoint"
ATTR_OPTIMAL_COEFFICIENT = "optimal_coefficient"
ATTR_COEFFICIENT_DERIVATIVE = "coefficient_derivative"
ATTR_PRE_CUSTOM_TEMPERATURE = "pre_custom_temperature"
ATTR_PRE_ACTIVITY_TEMPERATURE = "pre_activity_temperature"

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(_hass: HomeAssistant, _config_entry: ConfigEntry, _async_add_devices: AddEntitiesCallback):
    """Set up the SatClimate device."""
    coordinator = _hass.data[DOMAIN][_config_entry.entry_id][COORDINATOR]
    climate = SatClimate(coordinator, _config_entry, _hass.config.units.temperature_unit)

    _async_add_devices([climate])
    _hass.data[DOMAIN][_config_entry.entry_id][CLIMATE] = climate


class SatWarmingUp:
    def __init__(self, error: float, boiler_temperature: Optional[float] = None, started: Optional[int] = None):
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

        # Setup some public variables
        self.thermostat = config_entry.data.get(CONF_THERMOSTAT)

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
        self._last_boiler_temperature = None

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

        # Conditionally add TURN_ON if it exists
        if hasattr(ClimateEntityFeature, 'TURN_ON'):
            self._attr_supported_features |= ClimateEntityFeature.TURN_ON

        # Conditionally add TURN_OFF if it exists
        if hasattr(ClimateEntityFeature, 'TURN_OFF'):
            self._attr_supported_features |= ClimateEntityFeature.TURN_OFF

        # System Configuration
        self._attr_name = str(config_entry.data.get(CONF_NAME))
        self._attr_id = str(config_entry.data.get(CONF_NAME)).lower()

        self._rooms = config_entry.data.get(CONF_ROOMS) or []
        self._radiators = config_entry.data.get(CONF_RADIATORS) or []
        self._window_sensors = config_entry.options.get(CONF_WINDOW_SENSORS) or []

        self._simulation = bool(config_entry.data.get(CONF_SIMULATION))
        self._heating_system = str(config_entry.data.get(CONF_HEATING_SYSTEM))
        self._overshoot_protection = bool(config_entry.data.get(CONF_OVERSHOOT_PROTECTION))
        self._push_setpoint_to_thermostat = bool(config_entry.data.get(CONF_PUSH_SETPOINT_TO_THERMOSTAT))

        # User Configuration
        self._heating_mode = str(config_entry.options.get(CONF_HEATING_MODE))
        self._thermal_comfort = bool(config_options.get(CONF_THERMAL_COMFORT))
        self._climate_valve_offset = float(config_options.get(CONF_CLIMATE_VALVE_OFFSET))
        self._target_temperature_step = float(config_options.get(CONF_TARGET_TEMPERATURE_STEP))
        self._dynamic_minimum_setpoint = bool(config_options.get(CONF_DYNAMIC_MINIMUM_SETPOINT))
        self._sync_climates_with_mode = bool(config_options.get(CONF_SYNC_CLIMATES_WITH_MODE))
        self._sync_climates_with_preset = bool(config_options.get(CONF_SYNC_CLIMATES_WITH_PRESET))
        self._maximum_relative_modulation = int(config_options.get(CONF_MAXIMUM_RELATIVE_MODULATION))
        self._minimum_setpoint_version = int(config_options.get(CONF_DYNAMIC_MINIMUM_SETPOINT_VERSION))
        self._force_pulse_width_modulation = bool(config_options.get(CONF_FORCE_PULSE_WIDTH_MODULATION))
        self._sensor_max_value_age = convert_time_str_to_seconds(config_options.get(CONF_SENSOR_MAX_VALUE_AGE))
        self._window_minimum_open_time = convert_time_str_to_seconds(config_options.get(CONF_WINDOW_MINIMUM_OPEN_TIME))

        # Create the Setpoint Adjuster controller
        self._setpoint_adjuster = SetpointAdjuster()

        # Create PID controller with given configuration options
        self.pid = create_pid_controller(config_options)

        # Create Area controllers
        self.areas = Areas(config_entry.data, config_options)

        # Create Relative Modulation controller
        self._relative_modulation = RelativeModulation(coordinator, self._heating_system)

        # Create Heating Curve controller with given configuration options
        self.heating_curve = create_heating_curve_controller(config_entry.data, config_options)

        # Create the Minimum Setpoint controller
        self._minimum_setpoint = create_minimum_setpoint_controller(config_entry.data, config_options)

        # Create PWM controller with given configuration options
        self.pwm = create_pwm_controller(self.heating_curve, config_entry.data, config_options)

        if self._simulation:
            _LOGGER.warning("Simulation mode!")

    def async_track_coordinator_data(self):
        """Track changes in the coordinator's boiler temperature and trigger the heating loop."""
        if self._coordinator.boiler_temperature is not None and self._last_boiler_temperature == self._coordinator.boiler_temperature:
            return

        # Schedule an asynchronous task to control the heating loop
        asyncio.create_task(self.async_control_heating_loop())
        self._last_boiler_temperature = self._coordinator.boiler_temperature

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        # Restore previous state if available, or set default values
        await self._restore_previous_state_or_set_defaults()

        # Update a heating curve if outside temperature is available
        if self.current_outside_temperature is not None:
            self.areas.heating_curves.update(self.current_outside_temperature)
            self.heating_curve.update(self.target_temperature, self.current_outside_temperature)

        if self.hass.state is not CoreState.running:
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, self._register_event_listeners)
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, self.async_control_heating_loop)
        else:
            await self._register_event_listeners()
            await self.async_control_heating_loop()

        # Register services
        await self._register_services()

        # Initialize the area system
        await self.areas.async_added_to_hass(self.hass)

        # Let the coordinator know we are ready
        await self._coordinator.async_added_to_hass()

    async def _register_event_listeners(self, _time: Optional[datetime] = None):
        """Register event listeners."""
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self.async_control_heating_loop, timedelta(seconds=30)
            )
        )

        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_track_coordinator_data)
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

        if self.thermostat is not None:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, self.thermostat, self._async_thermostat_changed
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
                self.hass, self._radiators, self._async_main_climate_changed
            )
        )

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self._rooms, self._async_climate_changed
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

        for climate_id in self._rooms:
            state = self.hass.states.get(climate_id)
            if state is not None and (sensor_temperature_id := state.attributes.get(SENSOR_TEMPERATURE_ID)):
                await self.async_track_sensor_temperature(sensor_temperature_id)

    async def _restore_previous_state_or_set_defaults(self):
        """Restore previous state if available, or set default values."""
        old_state = await self.async_get_last_state()

        if old_state is not None:
            self.pwm.restore(old_state)
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

            if old_state.attributes.get(ATTR_SETPOINT):
                self._setpoint = old_state.attributes.get(ATTR_SETPOINT)

            if old_state.attributes.get(ATTR_PRESET_MODE):
                self._attr_preset_mode = old_state.attributes.get(ATTR_PRESET_MODE)

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
            self.areas.pids.reset()

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

            "boiler_temperature_cold": self._coordinator.boiler_temperature_cold,
            "boiler_temperature_tracking": self._coordinator.boiler_temperature_tracking,
            "boiler_temperature_derivative": self._coordinator.boiler_temperature_derivative,

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

            "valves_open": self.valves_open,
            "heating_curve": self.heating_curve.value,
            "minimum_setpoint": self.minimum_setpoint,
            "requested_setpoint": self.requested_setpoint,
            "outside_temperature": self.current_outside_temperature,
            "optimal_coefficient": self.heating_curve.optimal_coefficient,
            "coefficient_derivative": self.heating_curve.coefficient_derivative,

            "relative_modulation_value": self.relative_modulation_value,
            "relative_modulation_state": self.relative_modulation_state,
            "relative_modulation_enabled": self._relative_modulation.enabled,

            "pulse_width_modulation_state": self.pwm.state,
            "pulse_width_modulation_enabled": self.pwm.enabled,
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

        if not self._coordinator.device_active:
            return HVACAction.IDLE

        return HVACAction.HEATING

    @property
    def max_error(self) -> float:
        if self._heating_mode == HEATING_MODE_ECO:
            return self.error

        return max([self.error] + self.areas.errors)

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
    def valves_open(self) -> bool:
        """Determine if any of the controlled thermostats have open valves."""
        # Get the list of all controlled thermostats
        climates = self._rooms + self._radiators

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

        if not self._overshoot_protection or self._calculated_setpoint is None:
            return False

        if not self._dynamic_minimum_setpoint:
            return self._coordinator.minimum_setpoint > self._calculated_setpoint

        if self._minimum_setpoint_version == 1:
            return self._minimum_setpoint.current > self._calculated_setpoint

        return self.pwm.enabled

    @property
    def relative_modulation_value(self) -> int:
        return self._maximum_relative_modulation if self._relative_modulation.enabled else MINIMUM_RELATIVE_MOD

    @property
    def relative_modulation_state(self) -> RelativeModulationState:
        return self._relative_modulation.state

    @property
    def minimum_setpoint(self) -> float:
        if self._dynamic_minimum_setpoint:
            if self._minimum_setpoint_version == 1 and self._minimum_setpoint.current is not None:
                return self._minimum_setpoint.current

            if self._minimum_setpoint_version == 2 and self._setpoint_adjuster.current is not None:
                return self._setpoint_adjuster.current

        return self._coordinator.minimum_setpoint

    def _calculate_control_setpoint(self) -> float:
        """Calculate the control setpoint based on the heating curve and PID output."""
        if self.heating_curve.value is None:
            return MINIMUM_SETPOINT

        # Combine the heating curve value and the calculated output from the pid controller
        requested_setpoint = self.requested_setpoint

        # Ensure setpoint is limited to our max
        return min(requested_setpoint, self._coordinator.maximum_setpoint)

    async def _async_thermostat_changed(self, event: Event) -> None:
        """Handle changes to the connected thermostat."""
        old_state = event.data.get("old_state")
        if old_state is None or old_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        if (
                old_state.state != new_state.state or
                old_state.attributes.get("temperature") != new_state.attributes.get("temperature")
        ):
            _LOGGER.debug("Thermostat State Changed.")
            await self.async_set_hvac_mode(new_state.state, cascade=False)
            await self.async_set_target_temperature(new_state.attributes.get("temperature"), cascade=False)

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
            self._setpoint_adjuster.reset()
            await self._async_control_pid(True)

        # If the target temperature has changed, update the PID controller
        elif new_attrs.get("temperature") != old_attrs.get("temperature"):
            self._setpoint_adjuster.reset()
            await self._async_control_pid(True)

        # If the current temperature has changed, update the PID controller
        elif SENSOR_TEMPERATURE_ID not in new_state.attributes and new_attrs.get("current_temperature") != old_attrs.get("current_temperature"):
            await self._async_control_pid()

        if (self._rooms is not None and new_state.entity_id not in self._rooms) or self.preset_mode in [PRESET_HOME, PRESET_COMFORT]:
            if target_temperature := new_state.attributes.get("temperature"):
                self._rooms[new_state.entity_id] = float(target_temperature)

        # Update the heating control
        await self.async_control_heating_loop()

    async def _async_temperature_change(self, event: Event) -> None:
        """Handle changes to the climate sensor entity."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        _LOGGER.debug(f"Climate Sensor Changed ({new_state.entity_id}).")
        await self._async_control_pid()
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
                self._pre_activity_temperature = self.target_temperature or self.min_temp

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
            self.areas.pids.reset()

        # Calculate the maximum error between the current temperature and the target temperature of all climates
        max_error = self.max_error

        # Make sure we use the latest heating curve value
        if self.target_temperature is not None:
            self.areas.heating_curves.update(self.current_outside_temperature)
            self.heating_curve.update(self.target_temperature, self.current_outside_temperature)

        # Update the PID controller with the maximum error
        if not reset:
            _LOGGER.info(f"Updating error value to {max_error} (Reset: False)")

            # Calculate an optimal heating curve when we are in the deadband
            if self.target_temperature is not None and -DEADBAND <= max_error <= DEADBAND:
                self.heating_curve.autotune(
                    setpoint=self.requested_setpoint,
                    target_temperature=self.target_temperature,
                    outside_temperature=self.current_outside_temperature
                )

            # Update our PID controllers if we have valid values
            if self._coordinator.boiler_temperature_filtered is not None:
                self.areas.pids.update(self._coordinator.boiler_temperature_filtered)

                if self.heating_curve.value is not None:
                    self.pid.update(max_error, self.heating_curve.value, self._coordinator.boiler_temperature_filtered)

        elif max_error != self.pid.last_error:
            _LOGGER.info(f"Updating error value to {max_error} (Reset: True)")

            self.pid.update_reset(error=max_error, heating_curve_value=self.heating_curve.value)
            self._calculated_setpoint = None
            self.pwm.reset()

        self.async_write_ha_state()

    async def _async_control_setpoint(self, pwm_state: PWMState) -> None:
        """Control the setpoint of the heating system based on the current mode and PWM state."""

        # Check if the system is in HEAT mode
        if self.hvac_mode != HVACMode.HEAT:
            # If not in HEAT mode, set to minimum setpoint
            self._calculated_setpoint = None
            self._setpoint = MINIMUM_SETPOINT
            _LOGGER.info("HVAC mode is not HEAT. Setting setpoint to minimum: %.1f°C", MINIMUM_SETPOINT)

        elif not self.pulse_width_modulation_enabled or pwm_state == PWMState.IDLE:
            # Normal cycle without PWM
            self._setpoint = self._calculated_setpoint
            _LOGGER.info("Pulse Width Modulation is disabled or in IDLE state. Running normal heating cycle.")
            _LOGGER.debug("Calculated setpoint for normal cycle: %.1f°C", self._calculated_setpoint)
        else:
            # PWM is enabled and actively controlling the cycle
            _LOGGER.info("Running PWM cycle with state: %s", pwm_state)

            if pwm_state == PWMState.ON:
                if self._dynamic_minimum_setpoint:
                    if self._minimum_setpoint_version == 1:
                        self._setpoint = self._minimum_setpoint.current

                    if self._minimum_setpoint_version == 2:
                        if self._coordinator.flame_active and seconds_since(self._coordinator.flame_on_since) > 6 and self._coordinator.device_status != DeviceStatus.PUMP_STARTING:
                            self._setpoint = self._setpoint_adjuster.adjust(self._coordinator.boiler_temperature - 2)
                        elif self._setpoint_adjuster.current is not None:
                            self._setpoint = self._setpoint_adjuster.current
                        elif not self._coordinator.flame_active:
                            self._setpoint = self._setpoint_adjuster.force(self._coordinator.boiler_temperature + 10)
                        elif self._setpoint is None:
                            _LOGGER.debug("Setpoint not available.")
                            return
                else:
                    self._setpoint = self._coordinator.minimum_setpoint

                _LOGGER.debug("Setting setpoint to minimum: %.1f°C", self._setpoint)
            else:
                self._setpoint = MINIMUM_SETPOINT
                _LOGGER.debug("Setting setpoint to absolute minimum: %.1f°C", MINIMUM_SETPOINT)

        # Apply the setpoint using the coordinator
        await self._coordinator.async_set_control_setpoint(self._setpoint)
        _LOGGER.info("Control setpoint has been updated to: %.1f°C", self._setpoint)

    async def _async_control_relative_modulation(self) -> None:
        """Control the relative modulation value based on the conditions."""
        if not self._coordinator.supports_relative_modulation_management:
            _LOGGER.debug("Relative modulation management is not supported. Skipping control.")
            return

        # Update relative modulation state
        await self._relative_modulation.update(self.pulse_width_modulation_enabled)

        # Determine if the value needs to be updated
        if self._coordinator.maximum_relative_modulation_value == self.relative_modulation_value:
            _LOGGER.debug("Relative modulation value unchanged (%d%%). No update necessary.", self.relative_modulation_value)
            return

        await self._coordinator.async_set_control_max_relative_modulation(self.relative_modulation_value)

    async def _async_update_rooms_from_climates(self) -> None:
        """Update the temperature setpoint for each room based on their associated climate entity."""
        self._rooms = {}

        # Iterate through each climate entity
        for entity_id in self._rooms:
            state = self.hass.states.get(entity_id)

            # Skip any entities that are unavailable or have an unknown state
            if not state or state.state in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
                continue

            # Retrieve the target temperature from the climate entity's attributes
            target_temperature = state.attributes.get("temperature")

            # If the target temperature exists, store it in the _rooms dictionary with the climate entity as the key
            if target_temperature is not None:
                self._rooms[entity_id] = float(target_temperature)

    async def reset_control_state(self):
        """Reset control state when major changes occur."""
        self.pwm.disable()
        self._setpoint_adjuster.reset()

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

    async def async_control_heating_loop(self, _time: Optional[datetime] = None) -> None:
        """Control the heating based on current temperature, target temperature, and outside temperature."""
        # If the current, target or outside temperature is not available, do nothing
        if self.current_temperature is None or self.target_temperature is None or self.current_outside_temperature is None:
            return

        # No need to do anything if we are not on
        if self.hvac_mode != HVACMode.HEAT:
            return

        # Control the heating through the coordinator
        await self._coordinator.async_control_heating_loop(self)

        if self._calculated_setpoint is None:
            # Default to the calculated setpoint
            self._calculated_setpoint = self._calculate_control_setpoint()
        else:
            # Apply low filter on requested setpoint
            self._calculated_setpoint = round(self._alpha * self._calculate_control_setpoint() + (1 - self._alpha) * self._calculated_setpoint, 1)

        # Check for overshoot
        if self._coordinator.device_status == DeviceStatus.OVERSHOOT_HANDLING:
            _LOGGER.info("Overshoot Handling detected, enabling Pulse Width Modulation.")
            self.pwm.enable()

        # Check if we are above the overshoot temperature
        if (
                self._coordinator.device_status == DeviceStatus.COOLING_DOWN and
                self._setpoint_adjuster.current is not None and math.floor(self._calculated_setpoint) > math.floor(self._setpoint_adjuster.current)
        ):
            _LOGGER.info("Setpoint stabilization detected, disabling Pulse Width Modulation.")
            self.pwm.disable()

        # Pulse Width Modulation
        if self.pulse_width_modulation_enabled:
            boiler_state = BoilerState(
                flame_active=self._coordinator.flame_active,
                device_active=self._coordinator.device_active,
                hot_water_active=self._coordinator.hot_water_active,
                temperature=self._coordinator.boiler_temperature
            )

            await self.pwm.update(self._calculated_setpoint, boiler_state)
        else:
            self.pwm.reset()

        # Set the control setpoint to make sure we always stay in control
        await self._async_control_setpoint(self.pwm.state)

        # Set the relative modulation value, if supported
        await self._async_control_relative_modulation()

        # Control the integral (if exceeded the time limit)
        if self.heating_curve.value is not None:
            self.pid.update_integral(self.max_error, self.heating_curve.value)

        # Control our areas
        await self.areas.async_control_heating_loops()

        # Control our dynamic minimum setpoint (version 1)
        if not self._coordinator.hot_water_active and self._coordinator.flame_active:
            # Calculate the base return temperature
            if self._coordinator.device_status == DeviceStatus.HEATING_UP:
                self._minimum_setpoint.warming_up(self._coordinator.return_temperature)

            # Calculate the dynamic minimum setpoint
            self._minimum_setpoint.calculate(self._coordinator.return_temperature)

        # If the setpoint is high, turn on the heater
        await self.async_set_heater_state(DeviceState.ON if self._setpoint > MINIMUM_SETPOINT else DeviceState.OFF)

        self.async_write_ha_state()

    async def async_set_heater_state(self, state: DeviceState):
        """Set the heater state, ensuring proper conditions are met."""
        _LOGGER.debug("Attempting to set heater state to: %s", state)

        if state == DeviceState.ON:
            if self._coordinator.device_active:
                _LOGGER.info("Heater is already active. No action taken.")
                return

            if not self.valves_open:
                _LOGGER.warning("Cannot turn on heater: no valves are open.")
                return

        elif state == DeviceState.OFF:
            if not self._coordinator.device_active:
                _LOGGER.info("Heater is already off. No action taken.")
                return

        await self._coordinator.async_set_heater_state(state)

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

    async def async_set_hvac_mode(self, hvac_mode: HVACMode, cascade: bool = True) -> None:
        """Set the heating/cooling mode for the devices and update the state."""
        # Only allow the hvac mode to be set to heat or off
        if hvac_mode == HVACMode.HEAT:
            self._hvac_mode = HVACMode.HEAT
        elif hvac_mode == HVACMode.OFF:
            self._hvac_mode = HVACMode.OFF
            await self.async_set_heater_state(DeviceState.OFF)
        else:
            # If an unsupported mode is passed, log an error message
            _LOGGER.error("Unrecognized hvac mode: %s", hvac_mode)
            return

        # Reset the PID controller
        await self._async_control_pid(True)

        # Reset the climate
        await self.reset_control_state()

        # Collect which climates to control
        climates = self._radiators[:]
        if self._sync_climates_with_mode:
            climates += self._rooms

        if cascade:
            # Set the hvac mode for those climate devices
            for entity_id in climates:
                state = self.hass.states.get(entity_id)
                if state is None or hvac_mode not in state.attributes.get("hvac_modes"):
                    return

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
                for entity_id in self._rooms:
                    state = self.hass.states.get(entity_id)
                    if state is None or state.state == HVACMode.OFF:
                        continue

                    target_temperature = self._presets[preset_mode]
                    if preset_mode == PRESET_HOME or preset_mode == PRESET_COMFORT:
                        target_temperature = self._rooms[entity_id]

                    data = {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: target_temperature}
                    await self.hass.services.async_call(CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, data, blocking=True)

    async def async_set_target_temperature(self, temperature: float, cascade: bool = True) -> None:
        """Set the temperature setpoint for all main climates."""
        if self._target_temperature == temperature:
            return

        # Set the new target temperature
        self._target_temperature = temperature

        if cascade:
            # Set the target temperature for each main climate
            for entity_id in self._radiators:
                data = {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: temperature}
                await self.hass.services.async_call(CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, data, blocking=True)

            # Set the target temperature for the connected boiler
            if self._push_setpoint_to_thermostat:
                await self._coordinator.async_set_control_thermostat_setpoint(temperature)

        # Reset the PID controller
        await self._async_control_pid(True)

        # Reset the climate
        await self.reset_control_state()

        # Write the state to Home Assistant
        self.async_write_ha_state()

        # Control the heating based on the new temperature setpoint
        await self.async_control_heating_loop()

    async def async_send_notification(self, title: str, message: str, service: str = SERVICE_PERSISTENT_NOTIFICATION):
        """Send a notification to the user."""
        data = {"title": title, "message": message}
        await self.hass.services.async_call(NOTIFY_DOMAIN, service, data)
