"""Climate platform for SAT."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta, datetime
from types import MappingProxyType
from typing import Any, Callable, Iterable, Optional, Union

from homeassistant.components import sensor, weather
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
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, STATE_UNAVAILABLE, STATE_UNKNOWN, ATTR_ENTITY_ID, STATE_ON, STATE_OFF, EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, ServiceCall, Event, CoreState, EventStateChangedData, HassJob
from homeassistant.helpers import entity_registry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval, async_call_later
from homeassistant.helpers.restore_state import RestoreEntity

from .area import Areas
from .boiler import BoilerControlIntent
from .const import *
from .coordinator import SatDataUpdateCoordinator
from .entity import SatEntity
from .errors import Error
from .helpers import convert_time_str_to_seconds, float_value, is_state_stale, state_age_seconds, event_timestamp
from .manufacturers.geminox import Geminox
from .summer_simmer import SummerSimmer
from .types import BoilerStatus, RelativeModulationState, PWMStatus, DeviceState
from .util import create_pid_controller, create_heating_curve_controller, create_pwm_controller, create_dynamic_minimum_setpoint_controller

ATTR_ROOMS = "rooms"
ATTR_SETPOINT = "setpoint"
ATTR_OPTIMAL_COEFFICIENT = "optimal_coefficient"
ATTR_COEFFICIENT_DERIVATIVE = "coefficient_derivative"
ATTR_PRE_CUSTOM_TEMPERATURE = "pre_custom_temperature"
ATTR_PRE_ACTIVITY_TEMPERATURE = "pre_activity_temperature"

DECREASE_PERSISTENCE_TICKS = 3
NEAR_TARGET_MARGIN_CELSIUS = 2.0
INCREASE_STEP_THRESHOLD_CELSIUS = 0.5
DECREASE_STEP_THRESHOLD_CELSIUS = 0.5

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(_hass: HomeAssistant, _config_entry: ConfigEntry, _async_add_devices: AddEntitiesCallback) -> None:
    """Set up the SatClimate device."""
    coordinator = _hass.data[DOMAIN][_config_entry.entry_id][COORDINATOR]
    climate = SatClimate(coordinator, _config_entry, _hass.config.units.temperature_unit)

    _async_add_devices([climate])
    _hass.data[DOMAIN][_config_entry.entry_id][CLIMATE] = climate


class SatClimate(SatEntity, ClimateEntity, RestoreEntity):
    _enable_turn_on_off_backwards_compatibility: bool = False

    def __init__(self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry, unit: str) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator, config_entry)

        self.thermostat: Optional[str] = config_entry.data.get(CONF_THERMOSTAT)
        self.inside_sensor_entity_id: str = config_entry.data.get(CONF_INSIDE_SENSOR_ENTITY_ID)
        self.humidity_sensor_entity_id: Optional[str] = config_entry.data.get(CONF_HUMIDITY_SENSOR_ENTITY_ID)
        self.outside_sensor_entities: list[str] = self._ensure_list(config_entry.data.get(CONF_OUTSIDE_SENSOR_ENTITY_ID))

        config_options = self._build_config_options(config_entry)
        self._presets: dict[str, float] = self._build_presets(config_options)

        self._sensors: dict[str, str] = {}
        self._setpoint: Optional[float] = None
        self._requested_setpoint_down_ticks: int = 0
        self._low_modulation_ticks: int = 0
        self._rooms: Optional[dict[str, float]] = None
        self._last_requested_setpoint: Optional[float] = None

        self._hvac_mode: Optional[Union[HVACMode, str]] = None
        self._target_temperature: Optional[float] = None
        self._window_sensor_handle: Optional[asyncio.Task[None]] = None
        self._pre_custom_temperature: Optional[float] = None
        self._pre_activity_temperature: Optional[float] = None

        self._attr_temperature_unit = unit
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_preset_mode = PRESET_NONE
        self._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
        self._attr_preset_modes = [PRESET_NONE] + list(self._presets.keys())
        self._attr_supported_features = self._build_supported_features()

        self._control_heating_loop_unsub: Optional[Callable[[], None]] = None

        # System Configuration
        self._attr_name = str(config_entry.data.get(CONF_NAME))
        self._attr_id = str(config_entry.data.get(CONF_NAME)).lower()

        self._radiators: list[str] = config_entry.data.get(CONF_RADIATORS) or []
        self._window_sensors: list[str] = config_entry.options.get(CONF_WINDOW_SENSORS) or []

        self._simulation: bool = bool(config_entry.data.get(CONF_SIMULATION))
        self._heating_system: str = str(config_entry.data.get(CONF_HEATING_SYSTEM))
        self._overshoot_protection: bool = bool(config_entry.data.get(CONF_OVERSHOOT_PROTECTION))
        self._push_setpoint_to_thermostat: bool = bool(config_entry.data.get(CONF_PUSH_SETPOINT_TO_THERMOSTAT))

        # User Configuration
        self._heating_mode: str = str(config_entry.options.get(CONF_HEATING_MODE))
        self._thermal_comfort: bool = bool(config_options.get(CONF_THERMAL_COMFORT))
        self._climate_valve_offset: float = float(config_options.get(CONF_CLIMATE_VALVE_OFFSET))
        self._target_temperature_step: float = float(config_options.get(CONF_TARGET_TEMPERATURE_STEP))
        self._dynamic_minimum_setpoint: bool = bool(config_options.get(CONF_DYNAMIC_MINIMUM_SETPOINT))
        self._sync_climates_with_mode: bool = bool(config_options.get(CONF_SYNC_CLIMATES_WITH_MODE))
        self._sync_climates_with_preset: bool = bool(config_options.get(CONF_SYNC_CLIMATES_WITH_PRESET))
        self._maximum_relative_modulation: int = int(config_options.get(CONF_MAXIMUM_RELATIVE_MODULATION))
        self._sensor_max_value_age: float = convert_time_str_to_seconds(config_options.get(CONF_SENSOR_MAX_VALUE_AGE))
        self._window_minimum_open_time: float = convert_time_str_to_seconds(config_options.get(CONF_WINDOW_MINIMUM_OPEN_TIME))
        self._force_pulse_width_modulation: bool = bool(config_entry.data.get(CONF_MODE) == MODE_SWITCH) or bool(config_options.get(CONF_FORCE_PULSE_WIDTH_MODULATION))

        # Controllers
        self.pid = create_pid_controller(config_entry.data, config_options)
        self.heating_curve = create_heating_curve_controller(config_entry.data, config_options)
        self.minimum_setpoint = create_dynamic_minimum_setpoint_controller(config_entry.data, config_options)
        self.pwm = create_pwm_controller(self.heating_curve, config_entry.data, config_options)
        self.areas = Areas(config_entry.data, config_options, self.heating_curve)

        if self._simulation:
            _LOGGER.warning("Simulation mode!")

    @staticmethod
    def _ensure_list(value: Optional[Union[Iterable[str], str]]) -> list[str]:
        """Normalize a config option to a list of strings."""
        if value is None:
            return []

        if isinstance(value, str):
            return [value]

        return list(value)

    @staticmethod
    def _build_config_options(config_entry: ConfigEntry) -> MappingProxyType[str, Any]:
        """Merge default options with config entry overrides."""
        config_options = OPTIONS_DEFAULTS.copy()
        config_options.update(config_entry.options)
        return config_options

    @staticmethod
    def _build_presets(config_options: MappingProxyType[str, Any]) -> dict[str, float]:
        """Build preset temperature mapping from config options."""
        conf_presets = {p: f"{p}_temperature" for p in (PRESET_ACTIVITY, PRESET_AWAY, PRESET_HOME, PRESET_SLEEP, PRESET_COMFORT)}
        return {key: config_options[value] for key, value in conf_presets.items() if key in conf_presets}

    @staticmethod
    def _build_supported_features() -> ClimateEntityFeature:
        """Determine supported features based on Home Assistant version."""
        supported = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
        if hasattr(ClimateEntityFeature, "TURN_ON"):
            supported |= ClimateEntityFeature.TURN_ON

        if hasattr(ClimateEntityFeature, "TURN_OFF"):
            supported |= ClimateEntityFeature.TURN_OFF

        return supported

    def _get_entity_state_float(self, entity_id: Optional[str]) -> Optional[float]:
        """Return state as float if available and valid."""
        if entity_id is None:
            return None

        if (entity := self._coordinator.hass.states.get(entity_id)) is None:
            return None

        if is_state_stale(entity, self._sensor_max_value_age):
            _LOGGER.debug("Sensor %s stale for %s (age=%.1fs > %.1fs)", entity_id, self.entity_id, state_age_seconds(entity), self._sensor_max_value_age)
            return None

        if entity.state in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
            return None

        return float(entity.state)

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        # Restore the previous state if available, or set default values
        await self._restore_previous_state_or_set_defaults()

        # Update a heating curve if outside temperature is available
        if self.current_outside_temperature is not None:
            self.heating_curve.update(self.target_temperature, self.current_outside_temperature)

        if self.hass.state is CoreState.running:
            self._register_event_listeners()

            await self.async_control_pid()
            await self.async_control_heating_loop()
        else:
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, lambda _: self._register_event_listeners())

        await self._register_services()
        await self.areas.async_added_to_hass(self.hass)
        await self._coordinator.async_added_to_hass(self.hass)
        await self.minimum_setpoint.async_added_to_hass(self.hass, self._coordinator.device_id)

        self.async_on_remove(self.hass.bus.async_listen(
            EVENT_SAT_CYCLE_STARTED,
            lambda event: self.minimum_setpoint.on_cycle_start(boiler_capabilities=self._coordinator.device_capabilities, sample=event.data.get("sample"))
        ))

        self.async_on_remove(self.hass.bus.async_listen(
            EVENT_SAT_CYCLE_ENDED,
            lambda event: self.minimum_setpoint.on_cycle_end(boiler_capabilities=self._coordinator.device_capabilities, cycles=self._coordinator.cycles, cycle=event.data.get("cycle"))
        ))

        self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self.async_will_remove_from_hass)

    async def async_will_remove_from_hass(self, event: Optional[Event] = None) -> None:
        """Run when entity about to be removed."""
        await self.minimum_setpoint.async_save_regimes()
        await self._coordinator.async_will_remove_from_hass()

        for area in self.areas.items():
            await area.async_will_remove_from_hass()

        await super().async_will_remove_from_hass()

    def _register_event_listeners(self) -> None:
        """Register event listeners."""
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self.schedule_control_heating_loop, timedelta(seconds=10)
            )
        )

        self.async_on_remove(
            async_track_time_interval(
                self.hass, self.async_control_pid, timedelta(seconds=30)
            )
        )

        if self.thermostat is not None:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, self.thermostat, self._async_thermostat_changed
                )
            )

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self.areas.ids(), self._async_climate_changed
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

    async def _restore_previous_state_or_set_defaults(self) -> None:
        """Restore the previous state if available or set default values."""
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
                self._setpoint = float_value(old_state.attributes.get(ATTR_SETPOINT))

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

    async def _register_services(self) -> None:
        """Register SAT services with Home Assistant."""

        async def reset_integral(_call: ServiceCall) -> None:
            """Service to reset the integral part of the PID controller."""
            self.pid.reset()
            self.areas.pids.reset()

        self.hass.services.async_register(DOMAIN, SERVICE_RESET_INTEGRAL, reset_integral)

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
            "integral": self.pid.integral,
            "derivative": self.pid.derivative,
            "proportional": self.pid.proportional,
            "error": self.error.value if self.error is not None else None,

            "pre_custom_temperature": self._pre_custom_temperature,
            "pre_activity_temperature": self._pre_activity_temperature,

            "current_kp": self.pid.kp,
            "current_ki": self.pid.ki,
            "current_kd": self.pid.kd,

            "rooms": self._rooms,
            "setpoint": self._setpoint,
            "current_humidity": self.current_humidity,

            "summer_simmer_index": SummerSimmer.index(self.current_temperature, self.current_humidity),
            "summer_simmer_perception": SummerSimmer.perception(self.current_temperature, self.current_humidity),

            "valves_open": self.valves_open,
            "heating_curve": self.heating_curve.value,
            "requested_setpoint": self.requested_setpoint,
            "minimum_setpoint": self.minimum_setpoint.value,

            "outside_temperature": self.current_outside_temperature,
            "optimal_coefficient": self.heating_curve.optimal_coefficient,
            "coefficient_derivative": self.heating_curve.coefficient_derivative,

            "relative_modulation_value": self.relative_modulation_value,
            "relative_modulation_state": self.relative_modulation_state.name,

            "pulse_width_modulation_enabled": self.pwm.enabled,
            "pulse_width_modulation_state": self.pwm.status.name,
            "pulse_width_modulation_duty_cycle": self.pwm.state.duty_cycle,
        }

    @property
    def target_temperature(self) -> Optional[float]:
        """Return the temperature we try to reach."""
        return self._target_temperature

    @property
    def current_temperature(self) -> Optional[float]:
        """Return the sensor temperature."""
        if (current_temperature := self._get_entity_state_float(self.inside_sensor_entity_id)) is None:
            return None

        if self._thermal_comfort:
            return SummerSimmer.index(current_temperature, self.current_humidity)

        return current_temperature

    @property
    def current_humidity(self) -> Optional[float]:
        """Return the sensor humidity."""
        if self.humidity_sensor_entity_id is None:
            return None

        return self._get_entity_state_float(self.humidity_sensor_entity_id)

    @property
    def error(self) -> Optional[Error]:
        """Return the error value."""
        target_temperature = self.target_temperature
        current_temperature = self.current_temperature

        if target_temperature is None or current_temperature is None:
            return None

        return Error(self.entity_id, round(target_temperature - current_temperature, 2))

    @property
    def current_outside_temperature(self) -> Optional[float]:
        """Return the current outside temperature"""
        self.outside_sensor_entities.sort(key=lambda x: "sensor" not in x)
        for entity_id in self.outside_sensor_entities:
            state = self.hass.states.get(entity_id)
            if state is None:
                continue

            if is_state_stale(state, self._sensor_max_value_age):
                _LOGGER.debug("Outside sensor %s stale for %s (age=%.1fs > %.1fs)", entity_id, self.entity_id, state_age_seconds(state), self._sensor_max_value_age)
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
        return self._target_temperature_step

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

        if not self._coordinator.device_active:
            return HVACAction.IDLE

        return HVACAction.HEATING

    @property
    def setpoint(self) -> Optional[float]:
        """Return the current boiler control setpoint."""
        return self._setpoint

    @property
    def requested_setpoint(self) -> float:
        """Get the requested setpoint based on the heating curve and PIDs."""
        if self.heating_curve.value is None:
            return MINIMUM_SETPOINT

        setpoint = self.pid.output

        # ECO: only follow the primary PID.
        if self._heating_mode == HEATING_MODE_ECO:
            return round(setpoint, 1)

        # Secondary rooms: heating and overshoot information.
        secondary_heating = self.areas.pids.output
        overshoot_cap = self.areas.pids.overshoot_cap

        if secondary_heating is not None:
            setpoint = max(setpoint, secondary_heating)

        if overshoot_cap is not None:
            setpoint = min(setpoint, overshoot_cap)

        return round(max(MINIMUM_SETPOINT, setpoint), 1)

    @property
    def valves_open(self) -> bool:
        """Determine if any of the controlled climates have open valves."""
        # Get the list of all controlled climates
        climates = self._radiators + self.areas.ids()

        # If there are no radiators attached, there is no way to detect a closed valve
        if len(self._radiators) == 0:
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
                if float(target_temperature) >= float(current_temperature) + float(self._climate_valve_offset):
                    return True

        # If none of the thermostats have open valves, return False
        return False

    @property
    def pulse_width_modulation_enabled(self) -> bool:
        """Determine if pulse width modulation should be enabled."""
        # No setpoint means no safe PWM decision.
        if self._setpoint is None:
            return False

        # Forced on (relay-only or explicit override).
        if not self._coordinator.supports_setpoint_management or self._force_pulse_width_modulation:
            return True

        # Disabled by config.
        if not self._overshoot_protection:
            return False

        # Static vs dynamic minimum-setpoint logic.
        if not self._dynamic_minimum_setpoint:
            return self._should_enable_static_pwm()

        return self._should_enable_dynamic_pwm()

    def _should_enable_static_pwm(self) -> bool:
        """Determine if PWM should be enabled based on the static minimum setpoint."""
        if self.pwm.enabled:
            return self._coordinator.minimum_setpoint > self._setpoint - BOILER_DEADBAND

        return self._coordinator.minimum_setpoint > self._setpoint

    def _should_enable_dynamic_pwm(self) -> bool:
        """Determine if PWM should be enabled based on the dynamic minimum setpoint."""
        last_cycle = self._coordinator.last_cycle
        boiler_status = self._coordinator.device_status

        if boiler_status == BoilerStatus.STALLED_IGNITION:
            return True

        if last_cycle is not None and last_cycle.classification in UNHEALTHY_CYCLES:
            return True

        if (enabled := self._should_enable_low_modulation_pwm()) is not None:
            return enabled

        if (enabled := self._should_enable_pwm_from_setpoint_delta()) is not None:
            return enabled

        return self.pwm.enabled

    def _should_enable_low_modulation_pwm(self) -> Optional[bool]:
        """Enable PWM if the boiler stays at low relative modulation for a while."""

        def _reset_low_modulation_ticks() -> bool:
            self._low_modulation_ticks = 0
            return False

        if not self._coordinator.supports_relative_modulation:
            return _reset_low_modulation_ticks()

        if self._coordinator.hot_water_active:
            return _reset_low_modulation_ticks()

        state = self._coordinator.device_state
        if state.modulation_reliable == False or state.relative_modulation_level is None or not state.flame_active:
            return None

        if state.relative_modulation_level <= PWM_ENABLE_LOW_MODULATION_PERCENT:
            self._low_modulation_ticks += 1

        if state.relative_modulation_level >= PWM_DISABLE_LOW_MODULATION_PERCENT:
            self._low_modulation_ticks = 0

        return self._low_modulation_ticks >= PWM_LOW_MODULATION_PERSISTENCE_TICKS

    def _should_enable_pwm_from_setpoint_delta(self) -> Optional[bool]:
        """Return PWM decision based on requested/minimum delta, or None to keep state."""
        delta = self.requested_setpoint - self.minimum_setpoint.value

        # Near/below dynamic minimum -> PWM.
        if delta <= PWM_ENABLE_MARGIN_CELSIUS:
            return True

        # When above the hysteresis band -> no PWM.
        if delta >= PWM_DISABLE_MARGIN_CELSIUS:
            return False

        return None

    @property
    def relative_modulation_value(self) -> int:
        """Return the capped maximum relative modulation value."""
        if not self._coordinator.supports_relative_modulation_management or self.relative_modulation_state != RelativeModulationState.OFF:
            return self._maximum_relative_modulation

        return MINIMUM_RELATIVE_MODULATION

    @property
    def relative_modulation_state(self) -> RelativeModulationState:
        """Return the computed relative modulation state."""
        if self._coordinator.hot_water_active:
            return RelativeModulationState.HOT_WATER

        if self.pwm.status == PWMStatus.IDLE:
            if self._coordinator.setpoint is None or self._coordinator.setpoint <= MINIMUM_SETPOINT:
                return RelativeModulationState.COLD

            return RelativeModulationState.PWM_OFF

        return RelativeModulationState.OFF

    @property
    def minimum_setpoint_value(self) -> float:
        """Get the minimum allowable setpoint temperature."""
        if self._dynamic_minimum_setpoint:
            return self.minimum_setpoint.value

        # Default to coordinator's minimum setpoint
        return self._coordinator.minimum_setpoint

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

    async def _async_control_setpoint(self) -> None:
        """Control the setpoint of the heating system based on the current mode and PWM state."""

        # Check if the system is in HEAT mode
        if self.hvac_mode != HVACMode.HEAT:
            # If not in HEAT mode, set to the minimum setpoint
            self._setpoint = MINIMUM_SETPOINT
            _LOGGER.info("Heating disabled (HVAC mode=%s). Forcing boiler setpoint to minimum: %.1f°C", self.hvac_mode, MINIMUM_SETPOINT)

        elif not self.pulse_width_modulation_enabled or self.pwm.status == PWMStatus.IDLE:
            # Normal cycle without PWM
            _LOGGER.info("PWM inactive (enabled=%s, state=%s). Using continuous heating control.", self.pulse_width_modulation_enabled, self.pwm.status.name)

            requested_setpoint = self.requested_setpoint
            if self._setpoint is None:
                self._setpoint = requested_setpoint

            requested_setpoint_delta = requested_setpoint - self._setpoint

            # Track persistent decreases
            if requested_setpoint_delta < -DECREASE_STEP_THRESHOLD_CELSIUS:
                if self._last_requested_setpoint is not None and requested_setpoint < self._last_requested_setpoint:
                    self._requested_setpoint_down_ticks += 1
                else:
                    self._requested_setpoint_down_ticks = 1
            else:
                self._requested_setpoint_down_ticks = 0

            # Near-target downward lock while actively heating
            is_near_target = self._coordinator.boiler_temperature is not None and self._coordinator.boiler_temperature >= (self._setpoint - NEAR_TARGET_MARGIN_CELSIUS)
            if self._coordinator.flame_active and is_near_target:
                previous_setpoint = self._setpoint
                self._setpoint = max(self._setpoint, requested_setpoint)

                _LOGGER.info(
                    "Holding boiler setpoint near target to avoid premature flame-off (requested=%.1f°C, held=%.1f°C, previous=%.1f°C, boiler=%.1f°C, margin=%.1f°C)",
                    requested_setpoint, self._setpoint, previous_setpoint, self._coordinator.boiler_temperature, NEAR_TARGET_MARGIN_CELSIUS
                )

            # Allow meaningful increases immediately
            elif requested_setpoint_delta > INCREASE_STEP_THRESHOLD_CELSIUS:
                _LOGGER.info("Increasing boiler setpoint due to rising heat demand (requested=%.1f°C, previous=%.1f°C)", requested_setpoint, self._setpoint)
                self._setpoint = requested_setpoint

            # Allow meaningful decreases if they persist and we are not near target
            elif self._requested_setpoint_down_ticks >= DECREASE_PERSISTENCE_TICKS:
                _LOGGER.info("Lowering boiler setpoint after sustained lower demand (requested=%.1f°C persisted for %d cycles)", requested_setpoint, self._requested_setpoint_down_ticks)
                self._setpoint = requested_setpoint

            # Allow any changes when flame is not turned on
            elif not self._coordinator.flame_active:
                _LOGGER.info("Updating boiler setpoint while flame is off (requested=%.1f°)", requested_setpoint)
                self._setpoint = requested_setpoint
                self._requested_setpoint_up_ticks = 0
                self._requested_setpoint_down_ticks = 0

            self._last_requested_setpoint = requested_setpoint
        else:
            # PWM is enabled and actively controlling the cycle
            _LOGGER.info("PWM active (%s). Boiler setpoint governed by minimum-setpoint strategy.", self.pwm.status.name)

            if self.pwm.status == PWMStatus.ON:
                self._setpoint = self.minimum_setpoint_value
                _LOGGER.debug("PWM ON: enforcing minimum boiler setpoint %.1f°C to limit output", self._setpoint)
            else:
                self._setpoint = MINIMUM_SETPOINT
                _LOGGER.debug("PWM OFF phase: forcing boiler to absolute minimum %.1f°C", self._setpoint)

        # Apply the setpoint using the coordinator
        await self._coordinator.async_set_control_setpoint(self._setpoint if self._setpoint > COLD_SETPOINT else MINIMUM_SETPOINT)

    async def _async_control_relative_modulation(self) -> None:
        """Control the relative modulation value based on the conditions."""
        if not self._coordinator.supports_relative_modulation_management:
            _LOGGER.debug("Relative modulation management is not supported. Skipping control.")
            return

        # Retrieve the relative modulation
        relative_modulation_value = self.relative_modulation_value

        # Apply some filters based on the manufacturer
        if isinstance(self._coordinator.manufacturer, Geminox):
            relative_modulation_value = max(10, relative_modulation_value)

        # Determine if the value needs to be updated
        if self._coordinator.maximum_relative_modulation_value == relative_modulation_value:
            _LOGGER.debug("Relative modulation value unchanged (%d%%). No update necessary.", relative_modulation_value)
            return

        await self._coordinator.async_set_control_max_relative_modulation(relative_modulation_value)

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

    def reset_control_state(self) -> None:
        """Reset control state when major changes occur."""
        self.pid.reset()
        self.areas.pids.reset()
        self._last_requested_setpoint = None
        self._low_modulation_ticks = 0

    async def async_control_pid(self, time: Optional[datetime] = None) -> None:
        """Control the PID controller."""
        if self._sensor_max_value_age > 0:
            state = self.hass.states.get(self.inside_sensor_entity_id)
            if is_state_stale(state, self._sensor_max_value_age):
                _LOGGER.debug("Resetting PID for %s due to stale sensor %s (age=%.1fs > %.1fs)", self.entity_id, self.inside_sensor_entity_id, state_age_seconds(state), self._sensor_max_value_age)
                self.pid.reset()
                return

        if self.error is None:
            _LOGGER.debug("Skipping control loop for %s because error could not be computed", self.entity_id)
            return

        if self.current_outside_temperature is None:
            _LOGGER.warning("Current outside temperature is not available. Skipping PID control.")
            return

        # Make sure we use the latest heating curve value
        if self.target_temperature is not None:
            self.heating_curve.update(self.target_temperature, self.current_outside_temperature)

        # Calculate an optimal heating curve when we are in the deadband
        if self.target_temperature is not None and -DEADBAND <= self.error.value <= DEADBAND:
            self.heating_curve.autotune(self.requested_setpoint, self.target_temperature, self.current_outside_temperature)

        if self.heating_curve.value is None:
            _LOGGER.debug("Skipping PID update for %s because heating curve has no value", self.entity_id)
            return

        self.pid.update(self.error, event_timestamp(time), self.heating_curve.value)
        _LOGGER.debug("PID update for %s (error=%s, curve=%s, output=%s)", self.entity_id, self.error.value, self.heating_curve.value, self.pid.output)

        self.async_write_ha_state()

    def schedule_control_heating_loop(self, _time: Optional[datetime] = None, force: bool = False) -> None:
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

        self._control_heating_loop_unsub = async_call_later(self.hass, 5, HassJob(self.async_control_heating_loop))

    async def async_control_heating_loop(self, time: Optional[datetime] = None) -> None:
        """Control the heating based on current temperature, target temperature, and outside temperature."""
        self._control_heating_loop_unsub = None
        timestamp = event_timestamp(time)

        # Abort early if required inputs are missing.
        required_values = (
            self.target_temperature,
            self.heating_curve.value,
            self.current_temperature,
            self.current_outside_temperature,
        )

        if any(value is None for value in required_values):
            return

        # Skip control while not in heat mode.
        if self.hvac_mode != HVACMode.HEAT:
            return

        control_intent = BoilerControlIntent(
            setpoint=self.requested_setpoint,
            relative_modulation=self.relative_modulation_value
        )

        # Update PWM state from the latest device state and requested setpoint.
        if self.pulse_width_modulation_enabled:
            self.pwm.update(self._coordinator.device_state, control_intent, timestamp)
        else:
            self.pwm.disable()

        # Pass the control intent and context to the coordinator for sampling.
        self._coordinator.set_control_context(pwm_state=self.pwm.state, outside_temperature=self.current_outside_temperature)
        self._coordinator.set_control_intent(BoilerControlIntent(setpoint=self.requested_setpoint, relative_modulation=self.relative_modulation_value))
        await self._coordinator.async_control_heating_loop(time)

        # Apply the computed boiler controls.
        await self._async_control_setpoint()
        await self._async_control_relative_modulation()
        await self.async_set_heater_state(DeviceState.ON if self._setpoint is not None and self._setpoint > COLD_SETPOINT else DeviceState.OFF)

        self.async_write_ha_state()

    async def async_set_heater_state(self, state: DeviceState) -> None:
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
            await self.async_set_heater_state(DeviceState.OFF)
        else:
            # If an unsupported mode is passed, log an error message
            _LOGGER.error("Unrecognized hvac mode: %s", hvac_mode)
            return

        # Reset the climate
        self.reset_control_state()

        # Collect which climates to control
        climates = self._radiators[:]
        if self._sync_climates_with_mode:
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
        self.async_write_ha_state()
        self.schedule_control_heating_loop()

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
            if self._sync_climates_with_preset:
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
            for entity_id in self._radiators:
                data = {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: temperature}
                await self.hass.services.async_call(CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, data, blocking=True)

            # Set the target temperature for the connected boiler
            if self._push_setpoint_to_thermostat:
                await self._coordinator.async_set_control_thermostat_setpoint(temperature)

        # Reset the climate
        self.reset_control_state()

        # Write the state to Home Assistant
        self.async_write_ha_state()

        # Control the heating based on the new temperature setpoint
        self.schedule_control_heating_loop(force=True)
