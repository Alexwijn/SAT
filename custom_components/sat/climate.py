"""Climate platform for SAT."""
import datetime
import logging
import time
from typing import List, Optional, Any

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
from homeassistant.components.notify import DOMAIN as NOTIFY_DOMAIN, SERVICE_PERSISTENT_NOTIFICATION
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.weather import DOMAIN as WEATHER_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, STATE_UNAVAILABLE, STATE_UNKNOWN, ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall, Event
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt

from . import SatDataUpdateCoordinator, mean, SatConfigStore
from .const import *
from .entity import SatEntity
from .pid import PID

HOT_TOLERANCE = 0.3
COLD_TOLERANCE = 0.1

OVERSHOOT_PROTECTION_SETPOINT = 60
OVERSHOOT_PROTECTION_REQUIRED_DATASET = 40

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
    sample_time_limit = convert_time_str_to_seconds(options.get(CONF_SAMPLE_TIME))

    # Return a new PID controller instance with the given configuration options
    return PID(kp=kp, ki=ki, kd=kd, sample_time_limit=sample_time_limit)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_devices):
    """Set up the SatClimate device."""
    store = SatConfigStore(hass)
    await store.async_initialize()

    climate = SatClimate(
        hass.data[DOMAIN][config_entry.entry_id][COORDINATOR],
        store,
        config_entry,
        hass.config.units.temperature_unit
    )

    async_add_devices([climate])
    hass.data[DOMAIN][config_entry.entry_id][CLIMATE] = climate


class SatClimate(SatEntity, ClimateEntity, RestoreEntity):
    def __init__(self, coordinator: SatDataUpdateCoordinator, store: SatConfigStore, config_entry: ConfigEntry, unit: str):
        super().__init__(coordinator, config_entry)

        # Get configuration options and update with default values
        options = OPTIONS_DEFAULTS.copy()
        options.update(config_entry.options)

        # Create dictionary mapping preset keys to temperature options
        conf_presets = {p: f"{p}_temperature" for p in (PRESET_AWAY, PRESET_HOME, PRESET_SLEEP, PRESET_COMFORT)}
        # Create dictionary mapping preset keys to temperature values
        presets = {key: options[value] for key, value in conf_presets.items() if value in options}

        # Create PID controller with given configuration options
        self._pid = create_pid_controller(options)

        # Get inside sensor entity ID
        self.inside_sensor_entity_id = config_entry.data.get(CONF_INSIDE_SENSOR_ENTITY_ID)

        # Get inside sensor entity state
        inside_sensor_entity = coordinator.hass.states.get(self.inside_sensor_entity_id)

        # Get outside sensor entity IDs
        self.outside_sensor_entities = config_entry.data.get(CONF_OUTSIDE_SENSOR_ENTITY_ID)

        # If outside sensor entity IDs is a string, make it a list
        if isinstance(self.outside_sensor_entities, str):
            self.outside_sensor_entities = [self.outside_sensor_entities]

        # Get current temperature
        self._current_temperature = None
        if inside_sensor_entity is not None and inside_sensor_entity.state not in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
            self._current_temperature = float(inside_sensor_entity.state)

        self._setpoint = None
        self._heating_curve = None
        self._is_device_active = False

        self._hvac_mode = None
        self._target_temperature = None
        self._saved_target_temperature = None

        self._overshoot_protection_active = False
        self._overshoot_protection_data = []

        self._climates = options.get(CONF_CLIMATES)
        self._main_climates = options.get(CONF_MAIN_CLIMATES)

        self._simulation = options.get(CONF_SIMULATION)
        self._curve_move = options.get(CONF_HEATING_CURVE)
        self._heating_system = options.get(CONF_HEATING_SYSTEM)
        self._min_num_outputs = int(options.get(CONF_MIN_NUM_OUTPUTS))
        self._heating_curve_move = options.get(CONF_HEATING_CURVE_MOVE)
        self._overshoot_protection = options.get(CONF_OVERSHOOT_PROTECTION)
        self._target_temperature_step = options.get(CONF_TARGET_TEMPERATURE_STEP)
        self._sensor_max_value_age = convert_time_str_to_seconds(options.get(CONF_SENSOR_MAX_VALUE_AGE))

        self._attr_name = config_entry.data.get(CONF_NAME)
        self._attr_id = config_entry.data.get(CONF_NAME).lower()

        self._attr_temperature_unit = unit
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_preset_mode = PRESET_NONE
        self._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
        self._attr_preset_modes = [PRESET_NONE] + list(presets.keys())
        self._attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE

        self._store = store
        self._presets = presets
        self._coordinator = coordinator
        self._config_entry = config_entry

        if self._simulation:
            _LOGGER.warning("Simulation mode!")

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self.inside_sensor_entity_id], self._async_inside_sensor_changed
            )
        )

        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._async_control_heating, datetime.timedelta(seconds=30)
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
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [climate_id], self._async_climate_changed
                )
            )

        # Check If we have an old state
        if (old_state := await self.async_get_last_state()) is not None:
            # If we have no initial temperature, restore
            if self._target_temperature is None:
                # If we have a previously saved temperature
                if old_state.attributes.get(ATTR_TEMPERATURE) is None:
                    self._pid.setpoint = self.min_temp
                    self._target_temperature = self.min_temp
                    _LOGGER.warning("Undefined target temperature, falling back to %s", self._target_temperature, )
                else:
                    self._pid.restore(old_state)
                    self._target_temperature = float(old_state.attributes[ATTR_TEMPERATURE])

            if not self._hvac_mode and old_state.state:
                self._hvac_mode = old_state.state

            if not self._attr_preset_mode and old_state.attributes.get(ATTR_PRESET_MODE):
                self._attr_preset_mode = old_state.attributes.get(ATTR_PRESET_MODE)
        else:
            # No previous state, try and restore defaults
            if self._target_temperature is None:
                self._pid.setpoint = self.min_temp
                self._target_temperature = self.min_temp
                _LOGGER.warning("No previously saved temperature, setting to %s", self._target_temperature)

            # Set default state to off
            if not self._hvac_mode:
                self._hvac_mode = HVACMode.OFF

        self.async_write_ha_state()
        await self._async_control_heating()

        async def start_overshoot_protection_calculation(_call: ServiceCall):
            """Service to start the overshoot protection calculation process.

            This process will activate overshoot protection by turning on the heater and setting the control setpoint to
            a fixed value. Then, it will collect return water temperature data and calculate the mean of the last 3 data
            points. If the difference between the current return water temperature and the mean is small, it will
            deactivate overshoot protection and store the calculated value.
            """
            if self._overshoot_protection_active:
                _LOGGER.warning("[Overshoot Protection] Calculation already in progress.")
                return

            self._hvac_mode = HVACMode.HEAT
            self._overshoot_protection_data = []
            self._overshoot_protection_active = True

            description = "[Overshoot Protection] Calculation started. "
            description += "This process will run for at least 20 minutes until a stable return water temperature is found."

            _LOGGER.warning(description)

            await self.hass.services.async_call(NOTIFY_DOMAIN, SERVICE_PERSISTENT_NOTIFICATION, {
                "title": "Overshoot Protection Calculation",
                "message": description
            })

        self.hass.services.async_register(DOMAIN, "start_overshoot_protection_calculation", start_overshoot_protection_calculation)

        async def reset_integral(_call: ServiceCall):
            """Service to reset the integral part of the PID controller."""
            await self._async_control_pid(True)

        self.hass.services.async_register(DOMAIN, "reset_integral", reset_integral)

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
            "integral_enabled": self._pid.integral_enabled,

            "autotune_enabled": self._pid.autotune_enabled,
            "collected_outputs": self._pid.num_outputs,
            "optimal_kp": self._pid.optimal_kp,
            "optimal_ki": self._pid.optimal_ki,
            "optimal_kd": self._pid.optimal_kd,

            "setpoint": self._setpoint,
            "valves_open": self.valves_open,
            "heating_curve": self._heating_curve,
            "outside_temperature": self.current_outside_temperature,
            "overshoot_protection_value": self._store.retrieve_overshoot_protection_value()
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
        if self.current_temperature is None:
            return 0

        return round(self.target_temperature - self.current_temperature, 1)

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

        if not self._is_device_active:
            return HVACAction.IDLE

        return HVACAction.HEATING

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
            errors.append(round(target_temperature - current_temperature, 1))

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
                if current_temperature is not None and float(target_temperature) >= float(current_temperature):
                    return True

        # If none of the thermostats have open valves, return False
        return False

    def _get_boiler_value(self, key: str) -> Optional[Any]:
        """Get the value for the given `key` from the boiler data.

        :param key: Key of the value to retrieve from the boiler data.
        :return: Value for the given key from the boiler data, or None if the boiler data or the value are not available.
        """
        return self._coordinator.data[gw_vars.BOILER].get(key) if self._coordinator.data[gw_vars.BOILER] else None

    def _calculate_heating_curve_value(self) -> float:
        """Calculate the heating curve value based on the outside temperature."""
        system_offset = 28 + self._heating_curve_move
        if self._heating_system == CONF_UNDERFLOOR:
            system_offset = 20 + self._heating_curve_move

        # Calculate the curve value using the outside temperature
        curve_value = (20 - (0.01 * self.current_outside_temperature ** 2) - (0.8 * self.current_outside_temperature))
        return system_offset + (self._curve_move * curve_value)

    def _calculate_control_setpoint(self):
        """Calculate the control setpoint based on the heating curve and PID output."""
        setpoint = self._heating_curve + self._pid.output

        # Ensure setpoint is within allowed range for each heating system
        if self._heating_system == CONF_RADIATOR_HIGH_TEMPERATURES:
            setpoint = min(setpoint, 75.0)
        elif self._heating_system == CONF_RADIATOR_LOW_TEMPERATURES:
            setpoint = min(setpoint, 55.0)
        elif self._heating_system == CONF_UNDERFLOOR:
            setpoint = min(setpoint, 50.0)

        # Ensure setpoint is at least 10
        return max(setpoint, 10.0)

    async def _async_inside_sensor_changed(self, event: Event) -> None:
        """Handle changes to the inside temperature sensor."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        _LOGGER.debug("Inside Sensor Changed.")
        self._current_temperature = float(new_state.state)
        self.async_write_ha_state()

        await self._async_control_pid()
        await self._async_control_heating()

    async def _async_outside_entity_changed(self, event: Event) -> None:
        """Handle changes to the outside entity."""
        if event.data.get("new_state") is None:
            return

        await self._async_control_heating()

    async def _async_main_climate_changed(self, event: Event) -> None:
        """Handle changes to the main climate entity."""
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        if old_state is None or new_state.state != old_state.state:
            _LOGGER.debug(f"Main Climate State Changed ({new_state.entity_id}).")
            await self._async_control_heating()

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

        # If the state has changed or the old state is not available, update the PID controller
        if not old_state or new_state.state != old_state.state:
            await self._async_control_pid(True)

        # If the target temperature has changed, update the PID controller
        elif new_attrs.get("temperature") != old_attrs.get("temperature"):
            await self._async_control_pid(True)

        # If current temperature has changed, update the PID controller
        elif new_attrs.get("current_temperature") != old_attrs.get("current_temperature"):
            await self._async_control_pid(False)

        # Update the heating control
        await self._async_control_heating()

    async def _async_control_heating(self, _time=None) -> None:
        """Control the heating based on current temperature, target temperature, and outside temperature."""
        # If overshoot protection is active, run the overshoot protection control function
        if self._overshoot_protection_active:
            await self._async_control_overshoot_protection()
            return

        # If the current or outside temperature is not available, do nothing
        if self.current_temperature is None or self.current_outside_temperature is None:
            return

        # Check if the temperature is too cold or any climate requires heat
        climate_errors = self.climate_errors
        climates_require_heat = max(climate_errors) if len(climate_errors) > 0 else 0 >= COLD_TOLERANCE
        too_cold = self.target_temperature + COLD_TOLERANCE >= self._current_temperature

        # Check if the temperature is too hot
        too_hot = self.current_temperature >= self._target_temperature + HOT_TOLERANCE

        if self._is_device_active:
            # If the temperature is too hot or the valves are closed or HVAC is off, turn off the heater
            if (too_hot and not climates_require_heat) or not self.valves_open or self.hvac_action == HVACAction.OFF:
                await self._async_control_heater(False)
            # If the central heating is not enabled, turn on the heater
            elif not self._get_boiler_value(gw_vars.DATA_MASTER_CH_ENABLED):
                await self._async_control_heater(True)

            # Set the control setpoint
            await self._async_control_setpoint()
        else:
            # If the temperature is too cold and the valves are open and the HVAC is not off, turn on the heater
            if (too_cold or climates_require_heat) and self.valves_open and self.hvac_action != HVACAction.OFF:
                await self._async_control_heater(True)
                await self._async_control_setpoint()
            # If the central heating is enabled, turn off the heater
            elif self._get_boiler_value(gw_vars.DATA_MASTER_CH_ENABLED):
                await self._async_control_heater(False)

        self.async_write_ha_state()

    async def _async_control_overshoot_protection(self):
        """This method handles the overshoot protection process. It will turn on the heater if it's not already active,
        set the control setpoint to a fixed value, collect and store return water temperature data, and calculate
        the mean of the last 3 data points. If the difference between the current return water temperature and
        the mean is small, it will deactivate overshoot protection and store the calculated value.

        Note that this method will run every 30 seconds, but it won't activate the overshoot protection if it's already running.
        """
        # Turn on the heater if it's not already active
        if not self._is_device_active:
            await self._async_control_heater(True)

        # Collect central heating water temperature data
        return_water_temperature = self._get_boiler_value(gw_vars.DATA_CH_WATER_TEMP)
        if return_water_temperature is None:
            return

        # Set the control setpoint to a fixed value for overshoot protection
        await self._async_control_setpoint()

        self._overshoot_protection_data.append(round(return_water_temperature, 1))
        _LOGGER.info("[Overshoot Protection] Return Water Temperature Collected: %2.1f", return_water_temperature)

        # Calculate the mean of the last 3 data points only if there are enough data points collected
        if len(self._overshoot_protection_data) < OVERSHOOT_PROTECTION_REQUIRED_DATASET:
            return

        value = mean(self._overshoot_protection_data[-3:])
        difference = abs(round(return_water_temperature, 1) - mean(self._overshoot_protection_data[-3:]))

        # Deactivate overshoot protection if the difference between the current return water temperature and the mean
        # is small and store the calculated value
        if difference < 0.1:
            self._overshoot_protection_active = False
            self._store.store_overshoot_protection_value(round(value, 1))
            _LOGGER.info("[Overshoot Protection] Result: %2.1f", value)

    async def _async_control_pid(self, reset: bool = False):
        """Control the PID controller."""
        # Reset the PID controller if the sensor data is too old
        if self._sensor_max_value_age != 0 and time.time() - self._pid.last_updated > self._sensor_max_value_age:
            self._pid.reset()

        # Calculate the maximum error between the current temperature and the target temperature of all climates
        max_error = max([self.error] + self.climate_errors)

        # Update the PID controller with the maximum error
        if not reset:
            self._pid.update(max_error)
            _LOGGER.info(f"Updating error value to {max_error} (Reset: False)")

            if self._pid.num_outputs >= self._min_num_outputs:
                self._pid.autotune()
        else:
            self._pid.update_reset(max_error)
            _LOGGER.info(f"Updating error value to {max_error} (Reset: True)")

        self.async_write_ha_state()

    async def _async_control_heater(self, enabled: bool) -> None:
        """Control the state of the central heating."""
        if not self._simulation:
            await self._coordinator.api.set_ch_enable_bit(int(enabled))

        self._is_device_active = enabled
        self._pid.enable_integral(enabled)

        _LOGGER.info("Set central heating to %d", enabled)

    async def _async_control_setpoint(self):
        """Control the setpoint of the heating system."""
        if self._is_device_active:
            # Calculate the heating curve value
            self._heating_curve = round(self._calculate_heating_curve_value(), 1)
            _LOGGER.info("Calculated heating curve: %d", self._heating_curve)

            if self._overshoot_protection_active:
                # If overshoot protection is active, set the setpoint to a fixed value
                _LOGGER.warning("[Overshoot Protection] Overwritten setpoint to 60 degrees")
                self._setpoint = OVERSHOOT_PROTECTION_SETPOINT
            else:
                # Calculate the control setpoint
                self._setpoint = round(self._calculate_control_setpoint(), 1)
        else:
            # If the device is not active, set the setpoint to a fixed value and clear the heating curve value
            self._setpoint = 10
            self._heating_curve = None

        if not self._simulation:
            # Set the control setpoint on the device
            await self._coordinator.api.set_control_setpoint(self._setpoint)

        _LOGGER.info("Set control setpoint to %d", self._setpoint)

    async def async_set_temperature(self, **kwargs) -> None:
        """Set the target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        self._attr_preset_mode = PRESET_NONE
        await self._async_set_setpoint(temperature)

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
            await self._async_set_setpoint(self._saved_target_temperature)
        else:
            # Set the HVAC mode to `HEAT` if it is currently `OFF`
            if self.hvac_mode == HVACMode.OFF:
                self._hvac_mode = HVACMode.HEAT

            # Save the current target temperature if the preset mode is being set for the first time
            if self._attr_preset_mode == PRESET_NONE:
                self._saved_target_temperature = self._target_temperature

            # Set the preset mode and target temperature
            self._attr_preset_mode = preset_mode
            await self._async_set_setpoint(self._presets[preset_mode])

    async def _async_set_setpoint(self, temperature: float):
        """Set the temperature setpoint for all main climates."""
        if self._target_temperature == temperature:
            return

        # Set the new target temperature
        self._target_temperature = temperature

        # Reset the PID controller
        await self._async_control_pid(True)

        # Start auto-tuning
        self._pid.enable_autotune(True)

        # Set the temperature for each main climate
        for entity_id in self._main_climates:
            data = {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: temperature}
            await self.hass.services.async_call(CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, data, blocking=True)

        # Write the state to Home Assistant
        self.async_write_ha_state()

        # Control the heating based on the new temperature setpoint
        await self._async_control_heating()

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

        # Set the hvac mode for all climate devices
        for entity_id in (self._climates + self._main_climates):
            data = {ATTR_ENTITY_ID: entity_id, ATTR_HVAC_MODE: hvac_mode}
            await self.hass.services.async_call(CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE, data, blocking=True)

        # Update the state and control the heating
        self.async_write_ha_state()
        await self._async_control_heating()
