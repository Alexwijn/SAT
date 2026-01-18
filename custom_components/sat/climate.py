"""Climate platform for SAT."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta, datetime
from typing import Callable, Optional, Union, Any, Mapping

from homeassistant.components import sensor, weather
from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
    PRESET_ACTIVITY,
    PRESET_HOME,
    PRESET_NONE,
    ATTR_HVAC_MODE,
    ATTR_PRESET_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    DOMAIN as CLIMATE_DOMAIN, PRESET_AWAY, PRESET_SLEEP, PRESET_COMFORT,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, STATE_UNAVAILABLE, STATE_UNKNOWN, ATTR_ENTITY_ID, STATE_ON, STATE_OFF, EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, ServiceCall, Event, CoreState, EventStateChangedData, HassJob
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval, async_call_later
from homeassistant.helpers.restore_state import RestoreEntity

from .area import Areas
from .const import *
from .coordinator import SatDataUpdateCoordinator
from .entity import SatEntity
from .entry_data import SatConfig, get_entry_data
from .heating_control import HeatingDemand, SatHeatingControl
from .heating_curve import HeatingCurve
from .helpers import is_state_stale, state_age_seconds, clamp, ensure_list, event_timestamp
from .pid import PID
from .summer_simmer import SummerSimmer
from .temperature_state import TemperatureState
from .types import PWMStatus, HeatingMode

ATTR_ROOMS = "rooms"
ATTR_SETPOINT = "setpoint"
ATTR_OPTIMAL_COEFFICIENT = "optimal_coefficient"
ATTR_RELATIVE_MODULATION_VALUE = "relative_modulation_value"

ATTR_COEFFICIENT_DERIVATIVE = "coefficient_derivative"
ATTR_PRE_CUSTOM_TEMPERATURE = "pre_custom_temperature"
ATTR_PRE_ACTIVITY_TEMPERATURE = "pre_activity_temperature"

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_devices: AddEntitiesCallback) -> None:
    """Set up the SatClimate device."""
    entry_data = get_entry_data(
        hass,
        config_entry.entry_id
    )

    climate = SatClimate(
        entry_data.coordinator,
        entry_data.heating_control,
        entry_data.config,
        hass.config.units.temperature_unit,
    )

    async_add_devices([climate])
    entry_data.climate = climate
    entry_data.climate_ready.set()


class SatClimate(SatEntity, ClimateEntity, RestoreEntity):
    _enable_turn_on_off_backwards_compatibility: bool = False

    def __init__(self, coordinator: SatDataUpdateCoordinator, heating_control: SatHeatingControl, config: SatConfig, unit: str) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator, config, heating_control)

        self._sensors: dict[str, str] = {}

        self._rooms: Optional[dict[str, float]] = None
        self._presets: dict[str, float] = self._build_presets(config.presets.presets)

        self._target_temperature: Optional[float] = None
        self._pre_custom_temperature: Optional[float] = None
        self._hvac_mode: Optional[Union[HVACMode, str]] = None
        self._pre_activity_temperature: Optional[float] = None
        self._window_sensor_handle: Optional[asyncio.Task[None]] = None
        self._setpoint: Optional[float] = None

        self._attr_temperature_unit = unit
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_preset_mode = PRESET_NONE
        self._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
        self._attr_preset_modes = [PRESET_NONE] + list(self._presets.keys())
        self._attr_supported_features = self._build_supported_features()

        self._control_heating_loop_unsub: Optional[Callable[[], None]] = None

        # System Configuration
        self._attr_name = self._config.name
        self._attr_id = self._config.entry_id

        # Controllers
        self.areas = Areas.from_config(self._config)
        self.heating_curve = HeatingCurve.from_config(self._config)
        self.pid = PID.from_config(self.heating_curve, self._config)

        self._heating_control = heating_control

        if self._config.simulation.enabled:
            _LOGGER.warning("Simulation mode!")

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        # Restore the previous state if available, or set default values
        await self._restore_previous_state_or_set_defaults()

        await self._heating_control.async_added_to_hass()
        await self.pid.async_added_to_hass(self.hass, self.entity_id, self._coordinator.id)

        if self.hass.state is CoreState.running:
            self._update_heating_curves()
            self._register_event_listeners()

            self.control_pid()
            self.schedule_heating_control_loop()
        else:
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, lambda _: self._update_heating_curves())
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, lambda _: self._register_event_listeners())

        await self._register_services()
        await self._coordinator.async_added_to_hass(self.hass)
        await self.areas.async_added_to_hass(self.hass, self._coordinator.id)

        self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self.async_will_remove_from_hass)

    async def async_will_remove_from_hass(self, event: Optional[Event] = None) -> None:
        """Run when entity about to be removed."""
        self._cancel_scheduled_heating_control_loop()

        if self._window_sensor_handle is not None:
            self._window_sensor_handle.cancel()
            self._window_sensor_handle = None

        await self._heating_control.async_will_remove_from_hass()
        await self._coordinator.async_will_remove_from_hass()

        for area in self.areas.items():
            await area.async_will_remove_from_hass()

        await super().async_will_remove_from_hass()

    @property
    def name(self) -> str:
        """Return the friendly name of the sensor."""
        return self._attr_name

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return self._attr_id

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return device state attributes."""
        return {
            "pre_custom_temperature": self._pre_custom_temperature,
            "pre_activity_temperature": self._pre_activity_temperature,

            "rooms": self._rooms,
            "current_humidity": self.current_humidity,
            "setpoint": self._heating_control.control_setpoint,

            "summer_simmer_index": SummerSimmer.index(self.current_temperature, self.current_humidity),
            "summer_simmer_perception": SummerSimmer.perception(self.current_temperature, self.current_humidity),

            "valves_open": self.valves_open,
            "heating_curve": self.heating_curve.value,
            "requested_setpoint": self.requested_setpoint,

            "outside_temperature": self.current_outside_temperature,
            "optimal_coefficient": self.heating_curve.optimal_coefficient,
            "coefficient_derivative": self.heating_curve.coefficient_derivative,

            "relative_modulation_value": self._heating_control.relative_modulation_value,
            "relative_modulation_state": self._heating_control.relative_modulation_state.name,

            "pulse_width_modulation_enabled": self._heating_control.pwm_state.enabled if self._heating_control.pwm_state is not None else False,
            "pulse_width_modulation_duty_cycle": self._heating_control.pwm_state.duty_cycle if self._heating_control.pwm_state is not None else None,
            "pulse_width_modulation_state": self._heating_control.pwm_state.status.name if self._heating_control.pwm_state is not None else PWMStatus.IDLE.name,
        }

    @property
    def target_temperature(self) -> Optional[float]:
        """Return the temperature we try to reach."""
        return self._target_temperature

    @property
    def current_temperature(self) -> Optional[float]:
        """Return the sensor temperature."""
        if (current_temperature := self._get_entity_state_float(self._config.sensors.inside_sensor_entity_id)) is None:
            return None

        if self._config.presets.thermal_comfort:
            return SummerSimmer.index(current_temperature, self.current_humidity)

        return current_temperature

    @property
    def current_humidity(self) -> Optional[float]:
        """Return the sensor humidity."""
        if self._config.sensors.humidity_sensor_entity_id is None:
            return None

        return self._get_entity_state_float(self._config.sensors.humidity_sensor_entity_id)

    @property
    def error(self) -> Optional[TemperatureState]:
        """Return the error value."""
        if self._config.sensors.inside_sensor_entity_id is None:
            return None

        if (state := self.hass.states.get(self._config.sensors.inside_sensor_entity_id)) is None:
            return None

        target_temperature = self.target_temperature
        current_temperature = self.current_temperature
        if target_temperature is None or current_temperature is None:
            return None

        return TemperatureState(
            entity_id=self.entity_id,
            setpoint=target_temperature,
            current=current_temperature,
            last_reported=state.last_reported,
            last_updated=state.last_updated,
            last_changed=state.last_changed,
        )

    @property
    def current_outside_temperature(self) -> Optional[float]:
        """Return the current outside temperature"""
        outside_sensor_entities = ensure_list(self._config.sensors.outside_sensor_entity_id)
        outside_sensor_entities.sort(key=lambda x: "sensor" not in x)

        for entity_id in outside_sensor_entities:
            state = self.hass.states.get(entity_id)
            if state is None:
                continue

            sensor_max_value_age = self._config.sensors.sensor_max_value_age_seconds

            if is_state_stale(state, sensor_max_value_age):
                _LOGGER.debug("Outside sensor %s stale for %s (age=%.1fs > %.1fs)", entity_id, self.entity_id, state_age_seconds(state), sensor_max_value_age)
                continue

            if state.state in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
                continue

            if sensor.DOMAIN in entity_id:
                return float(state.state)

            if weather.DOMAIN in entity_id:
                return float(state.attributes.get("temperature"))

        return None

    @property
    def target_temperature_step(self) -> float:
        """Return the target temperature step to control the thermostat"""
        return self._config.limits.target_temperature_step

    @property
    def hvac_mode(self) -> HVACMode:
        """Get the current HVAC mode."""
        if self._hvac_mode in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
            return HVACMode.OFF

        return self._hvac_mode

    @property
    def hvac_action(self) -> HVACAction:
        """Get the current HVAC action."""
        if self._hvac_mode == HVACMode.OFF:
            return HVACAction.OFF

        if not self._coordinator.active:
            return HVACAction.IDLE

        return HVACAction.HEATING

    @property
    def requested_setpoint(self) -> float:
        """Get the requested setpoint based on the heating curve and PIDs."""
        if self.heating_curve.value is None:
            return MINIMUM_SETPOINT

        setpoint = self.pid.output

        # ECO: only follow the primary PID.
        if self._config.presets.heating_mode == HeatingMode.ECO:
            return clamp(round(setpoint, 1), MINIMUM_SETPOINT, self._coordinator.maximum_setpoint)

        # Secondary rooms: heating and overshoot information.
        secondary_heating = self.areas.pids.output
        overshoot_cap = self.areas.pids.overshoot_cap

        if secondary_heating is not None:
            setpoint = max(setpoint, secondary_heating)

        if overshoot_cap is not None:
            setpoint = min(setpoint, overshoot_cap)

        return clamp(round(setpoint, 1), MINIMUM_SETPOINT, self._coordinator.maximum_setpoint)

    @property
    def valves_open(self) -> bool:
        """Determine if any of the controlled climates have open valves."""
        # Get the list of all controlled climates
        climates = self._config.radiators + self.areas.ids()

        # If there are no radiators attached, there is no way to detect a closed valve
        if len(self._config.radiators) == 0:
            return True

        # Iterate through each controlled thermostat
        for entity_id in climates:
            # Get the current state of the thermostat
            state = self.hass.states.get(entity_id)

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

                if current_temperature is None or target_temperature is None:
                    continue

                # If there is a current temperature, and it is not at the target temperature, we can assume the valves are open
                if float(target_temperature) >= float(current_temperature) + float(self._config.limits.climate_valve_offset):
                    return True

        # If none of the thermostats have open valves, return False
        return False

    @property
    def pwm(self):
        return self._heating_control.pwm_state

    def reset_control_state(self) -> None:
        """Reset control state when major changes occur."""
        self.pid.reset()
        self.areas.pids.reset()
        self._heating_control.reset()

    def control_pid(self, _time: Optional[datetime] = None) -> None:
        """Control the PID controller."""
        sensor_max_value_age = self._config.sensors.sensor_max_value_age_seconds

        if sensor_max_value_age > 0:
            state = self.hass.states.get(self._config.sensors.inside_sensor_entity_id)
            if is_state_stale(state, sensor_max_value_age):
                _LOGGER.debug("Resetting PID for %s due to stale sensor %s (age=%.1fs > %.1fs)", self.entity_id, self._config.sensors.inside_sensor_entity_id, state_age_seconds(state), sensor_max_value_age)
                self.pid.reset()
                return

        if (error := self.error) is None:
            _LOGGER.debug("Skipping control loop for %s because error value is not available.", self.entity_id)
            return

        # Calculate an optimal heating curve when we are in the deadband
        if (
                self.target_temperature is not None
                and self.current_outside_temperature is not None
                and -DEADBAND <= error.error <= DEADBAND
        ):
            self.heating_curve.autotune(
                self.requested_setpoint,
                self.target_temperature,
                self.current_outside_temperature,
            )

        self.pid.update(error)

    def schedule_heating_control_loop(self, _time: Optional[datetime] = None, force: bool = False) -> None:
        """Schedule a debounced execution of the heating control loop."""
        if force:
            # Cancel previous scheduled run, if any
            if self._control_heating_loop_unsub is not None:
                self._control_heating_loop_unsub()
                self._control_heating_loop_unsub = None

            self.hass.async_create_task(self.async_control_heating_loop())
            return

        # If a run is already scheduled, do nothing.
        if self._control_heating_loop_unsub is not None:
            return

        self._control_heating_loop_unsub = async_call_later(self.hass, 1, HassJob(self.async_control_heating_loop))
        self.async_on_remove(self._cancel_scheduled_heating_control_loop)

    def _cancel_scheduled_heating_control_loop(self) -> None:
        if self._control_heating_loop_unsub is None:
            return

        self._control_heating_loop_unsub()
        self._control_heating_loop_unsub = None

    async def async_control_heating_loop(self, time: Optional[datetime] = None) -> None:
        """Control the heating based on current temperature, target temperature, and outside temperature."""
        self._control_heating_loop_unsub = None

        # Abort early if required inputs are missing.
        if self.current_outside_temperature is None:
            return

        # Apply the computed controls.
        await self._heating_control.update(
            HeatingDemand(
                hvac_mode=self.hvac_mode,
                requested_setpoint=self.requested_setpoint,
                outside_temperature=self.current_outside_temperature,
                timestamp=event_timestamp(time),
            )
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return None

        # Automatically select the preset
        for preset in self._presets:
            if float(self._presets[preset]) == float(temperature):
                return await self.async_set_preset_mode(preset)

        self._attr_preset_mode = PRESET_NONE
        return await self.async_set_target_temperature(temperature)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode, cascade: bool = True) -> None:
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

        # Reset the climate
        self.reset_control_state()

        # Collect which climates to control
        climates = self._config.radiators[:]
        if self._config.presets.sync_climates_with_mode:
            climates += self.areas.ids()

        if cascade:
            # Set the hvac mode for those climate devices
            for entity_id in climates:
                state = self.hass.states.get(entity_id)
                if state is None or hvac_mode not in state.attributes.get("hvac_modes"):
                    return

                data = {ATTR_ENTITY_ID: entity_id, ATTR_HVAC_MODE: hvac_mode}
                await self.hass.services.async_call(CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE, data, blocking=True)

        # Update the state and control the heating
        self.schedule_heating_control_loop()
        self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the preset mode for the thermostat."""
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
            if self._config.presets.sync_climates_with_preset:
                for entity_id in self.areas.ids():
                    state = self.hass.states.get(entity_id)
                    if state is None or state.state == HVACMode.OFF:
                        continue

                    if preset_mode != PRESET_HOME:
                        target_temperature = self._presets[preset_mode]
                    else:
                        target_temperature = self._rooms.get(entity_id, self._presets[preset_mode])

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
            for entity_id in self._config.radiators:
                data = {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: temperature}
                await self.hass.services.async_call(CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, data, blocking=True)

            # Set the target temperature for the connected boiler
            if self._config.push_setpoint_to_thermostat:
                await self._coordinator.async_set_control_thermostat_setpoint(temperature)

        # Reset the climate
        self.reset_control_state()

        # Update based on the new temperature setpoint
        self.schedule_heating_control_loop()

        # Write the state to Home Assistant
        self.async_write_ha_state()

    @staticmethod
    def _build_supported_features() -> ClimateEntityFeature:
        """Determine supported features based on Home Assistant version."""
        supported = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
        if hasattr(ClimateEntityFeature, "TURN_ON"):
            supported |= ClimateEntityFeature.TURN_ON

        if hasattr(ClimateEntityFeature, "TURN_OFF"):
            supported |= ClimateEntityFeature.TURN_OFF

        return supported

    def _get_entity_state_float(self, entity_id: str) -> Optional[float]:
        """Return state if available and valid."""
        if entity_id is None:
            return None

        if (entity := self.hass.states.get(entity_id)) is None:
            return None

        sensor_max_value_age = self._config.sensors.sensor_max_value_age_seconds

        if is_state_stale(entity, sensor_max_value_age):
            _LOGGER.debug("Sensor %s stale for %s (age=%.1fs > %.1fs)", entity_id, self.entity_id, state_age_seconds(entity), sensor_max_value_age)
            return None

        if entity.state in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
            return None

        return float(entity.state)

    def _update_heating_curves(self) -> None:
        """Update the heating curves based on the current outside temperature."""
        target_temperature = self.target_temperature
        outside_temperature = self.current_outside_temperature

        if target_temperature is None or outside_temperature is None:
            return

        self.areas.heating_curves.update(outside_temperature)
        self.heating_curve.update(target_temperature, outside_temperature)

    def _register_event_listeners(self) -> None:
        """Register event listeners."""
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self.schedule_heating_control_loop, timedelta(seconds=5)
            )
        )

        self.async_on_remove(
            async_track_time_interval(
                self.hass, self.control_pid, timedelta(seconds=30)
            )
        )

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, ensure_list(self._config.sensors.outside_sensor_entity_id), self._async_outside_entity_changed
            )
        )

        if self._config.thermostat is not None:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, self._config.thermostat, self._async_thermostat_changed
                )
            )

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self.areas.ids(), self._async_climate_changed
            )
        )

        if len(self._config.window_sensors) > 0:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, ensure_list(self._config.window_sensors), self._async_window_sensor_changed
                )
            )

    async def _restore_previous_state_or_set_defaults(self) -> None:
        """Restore the previous state if available or set default values."""
        old_state = await self.async_get_last_state()

        if old_state is not None:
            self._heating_control.restore(old_state)

            if self._target_temperature is None:
                if old_state.attributes.get(ATTR_TEMPERATURE) is None:
                    self.pid.control_setpoint = self.min_temp
                    self._target_temperature = self.min_temp
                    _LOGGER.warning("Undefined target temperature, falling back to %s", self._target_temperature, )
                else:
                    self._target_temperature = float(old_state.attributes[ATTR_TEMPERATURE])

            if old_state.state:
                self._hvac_mode = old_state.state

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
                self.pid.control_setpoint = self.min_temp
                self._target_temperature = self.min_temp
                _LOGGER.warning("No previously saved temperature, setting to %s", self._target_temperature)

            if not self._hvac_mode:
                self._hvac_mode = HVACMode.OFF

        self.async_write_ha_state()

    async def _register_services(self) -> None:
        """Register SAT services with Home Assistant."""

        async def reset_integral(_call: ServiceCall) -> None:
            """Service to reset the integral part of the PID controller."""
            self.pid.reset()
            self.areas.pids.reset()

        self.hass.services.async_register(DOMAIN, SERVICE_RESET_INTEGRAL, reset_integral)

    async def _async_thermostat_changed(self, event: Event[EventStateChangedData]) -> None:
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
            _LOGGER.debug("Thermostat state changed.")
            await self.async_set_target_temperature(new_state.attributes.get("temperature"), cascade=False)

    async def _async_outside_entity_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle changes to the outside entity."""
        if event.data.get("new_state") is None or event.data.get("new_state") in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        self._update_heating_curves()

    async def _async_climate_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle changes to a climate entity."""
        new_state = event.data.get("new_state")

        if not new_state or self._rooms is None:
            return

        # Get the attributes of the new state
        new_attrs = new_state.attributes

        if (target_temperature := new_attrs.get("temperature")) is None:
            return

        if float(target_temperature) == self._rooms.get(new_state.entity_id, target_temperature):
            return

        if new_state.entity_id not in self._rooms or self.preset_mode == PRESET_HOME:
            self._rooms[new_state.entity_id] = float(target_temperature)
            _LOGGER.debug(f"Updated area preset temperature for {new_state.entity_id} to {target_temperature}")

        self.areas.pids.reset(new_state.entity_id)

        self.async_write_ha_state()

    async def _async_window_sensor_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle changes to the contact sensor entity."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        _LOGGER.debug(f"Window Sensor Changed to {new_state.state}.")

        if new_state.state == STATE_ON:
            if self.preset_mode == PRESET_ACTIVITY:
                return

            try:
                self._window_sensor_handle = asyncio.create_task(
                    asyncio.sleep(self._config.sensors.window_minimum_open_time_seconds)
                )
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

    async def _async_update_rooms_from_climates(self) -> None:
        """Update the temperature setpoint for each room based on their associated climate entity."""
        self._rooms = {}

        # Iterate through each climate entity
        for entity_id in self.areas.ids():
            state = self.hass.states.get(entity_id)

            # Skip any entities that are unavailable or have an unknown state
            if not state or state.state in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
                continue

            # Retrieve the target temperature from the climate entity's attributes
            target_temperature = state.attributes.get("temperature")

            # If the target temperature exists, store it in the _rooms dictionary with the climate entity as the key
            if target_temperature is not None:
                self._rooms[entity_id] = float(target_temperature)

    @staticmethod
    def _build_presets(config_options: Mapping[str, float]) -> dict[str, float]:
        """Build preset temperature mapping from config options."""
        conf_presets = {p: f"{p}_temperature" for p in (PRESET_ACTIVITY, PRESET_AWAY, PRESET_HOME, PRESET_SLEEP, PRESET_COMFORT)}
        return {key: config_options[value] for key, value in conf_presets.items() if key in conf_presets}
