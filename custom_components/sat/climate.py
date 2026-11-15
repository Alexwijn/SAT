"""Climate platform for SAT."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Mapping, Optional

import voluptuous as vol
from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
    PRESET_ACTIVITY,
    PRESET_AWAY,
    PRESET_COMFORT,
    PRESET_HOME,
    PRESET_NONE,
    PRESET_SLEEP,
    ATTR_HVAC_MODE,
    ATTR_PRESET_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE, EVENT_HOMEASSISTANT_STARTED, STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CoreState, Event, EventStateChangedData, HomeAssistant, SupportsResponse, callback
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity

from .const import *
from .coordinator import SatDataUpdateCoordinator
from .demand_control import SatDemandControl
from .entity import SatEntity
from .entry_data import SatConfig, get_entry_data
from .heating_control import SatHeatingControl
from .summer_simmer import SummerSimmer
from .types import PWMStatus

ATTR_ROOMS = "rooms"
ATTR_SETPOINT = "setpoint"
ATTR_RELATIVE_MODULATION_VALUE = "relative_modulation_value"

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
        entry_data.demand_control,
        entry_data.config,
        hass.config.units.temperature_unit,
    )

    async_add_devices([climate])
    entry_data.climate = climate

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(SERVICE_RESET_INTEGRAL, None, "async_reset_integral")
    platform.async_register_entity_service(SERVICE_PULSE_WIDTH_MODULATION, {vol.Required("enabled"): cv.boolean}, "async_set_pulse_width_modulation")
    platform.async_register_entity_service(
        SERVICE_ESTIMATE_PREHEAT,
        {
            vol.Required("target_temperature"): vol.Coerce(float),
            vol.Optional("ready_time"): cv.datetime,
            vol.Optional("room"): vol.Any(None, "", cv.entity_id),
        },
        "async_estimate_preheat",
        supports_response=SupportsResponse.ONLY,
    )


class SatClimate(SatEntity, ClimateEntity, RestoreEntity):
    _enable_turn_on_off_backwards_compatibility: bool = False

    def __init__(self, coordinator: SatDataUpdateCoordinator, heating_control: SatHeatingControl, demand_control: SatDemandControl, config: SatConfig, unit: str) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator, config, heating_control)

        self._demand_control = demand_control

        self._rooms: Optional[dict[str, float]] = None
        self._presets: dict[str, float] = self._build_presets(config.presets.presets)

        self._event_listeners_registered: bool = False
        self._pre_custom_temperature: Optional[float] = None
        self._pre_activity_temperature: Optional[float] = None
        self._window_sensor_handle: Optional[asyncio.Task[None]] = None

        self._temperature_unit = unit
        self._attr_preset_mode = PRESET_NONE
        self._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
        self._attr_supported_features = self._build_supported_features()
        self._attr_preset_modes = [PRESET_NONE] + list(self._presets.keys())

        # System Configuration
        self._attr_name = self._config.name
        self._attr_id = self._config.entry_id

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        # Restore the previous state if available, or set default values
        await self._restore_previous_state_or_set_defaults()

        await self._demand_control.async_added_to_hass(self.entity_id)

        if self.hass.state is CoreState.running:
            self._register_event_listeners()
        else:
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, lambda _: self._register_event_listeners())

        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_DEMAND_CONTROL_UPDATED, self._on_demand_control_updated)
        )

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity about to be removed."""
        self._event_listeners_registered = False

        if self._window_sensor_handle is not None:
            self._window_sensor_handle.cancel()
            self._window_sensor_handle = None

        await self._demand_control.async_will_remove_from_hass()
        await super().async_will_remove_from_hass()

    async def _restore_previous_state_or_set_defaults(self) -> None:
        """Restore the previous state if available or set default values."""
        old_state = await self.async_get_last_state()

        if old_state is not None:
            self._heating_control.restore(old_state)

            if old_state.attributes.get(ATTR_PRESET_MODE):
                self._attr_preset_mode = old_state.attributes.get(ATTR_PRESET_MODE)

            if old_state.attributes.get(ATTR_PRE_ACTIVITY_TEMPERATURE):
                self._pre_activity_temperature = old_state.attributes.get(ATTR_PRE_ACTIVITY_TEMPERATURE)

            if old_state.attributes.get(ATTR_PRE_CUSTOM_TEMPERATURE):
                self._pre_custom_temperature = old_state.attributes.get(ATTR_PRE_CUSTOM_TEMPERATURE)

            if old_state.attributes.get(ATTR_ROOMS):
                self._rooms = old_state.attributes.get(ATTR_ROOMS)
            else:
                await self._async_update_rooms_from_climates()
        elif self._rooms is None:
            await self._async_update_rooms_from_climates()

        self._demand_control.restore(old_state, self.min_temp)
        self.async_write_ha_state()

    @callback
    def _on_demand_control_updated(self, device_id: str) -> None:
        if device_id != self._coordinator.id:
            return

        self.async_write_ha_state()

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
        solar_gain = self._demand_control.solar_gain_snapshot
        pwm_state = self._heating_control.pwm_state

        return {
            "pre_custom_temperature": self._pre_custom_temperature,
            "pre_activity_temperature": self._pre_activity_temperature,

            "rooms": self._rooms,
            "current_humidity": self.current_humidity,
            "setpoint": self._heating_control.control_setpoint,

            "summer_simmer_index": SummerSimmer.index(self.native_current_temperature, self.current_humidity),
            "summer_simmer_perception": SummerSimmer.perception(self.native_current_temperature, self.current_humidity),

            "valves_open": self._demand_control.valves_open,
            "heating_curve": self._demand_control.heating_curve.value,
            "requested_setpoint": self._demand_control.requested_setpoint,
            "outside_temperature": self._demand_control.current_outside_temperature,
            "wind_speed": self._demand_control.current_wind.speed if self._demand_control.current_wind is not None else None,

            "solar_gain_active": solar_gain.active,
            "solar_gain_sun_elevation": solar_gain.sun_elevation,
            "solar_gain_rise_per_hour": solar_gain.rise_per_hour,

            "frost_protection_active": self._demand_control.frost_protection_active,
            "warm_up_rate_per_hour": self._demand_control.warm_up.rate,

            "relative_modulation_value": self._heating_control.relative_modulation_value,
            "relative_modulation_state": self._heating_control.relative_modulation_state.name,

            "pulse_width_modulation_enabled": pwm_state.enabled if pwm_state is not None else False,
            "pulse_width_modulation_duty_cycle": pwm_state.duty_cycle if pwm_state is not None else None,
            "pulse_width_modulation_state": pwm_state.status.name if pwm_state is not None else PWMStatus.IDLE.name,
            "pulse_width_modulation_switched_at": pwm_state.last_switched_at if pwm_state is not None else None,
            "pulse_width_modulation_effective_on_temperature": pwm_state.effective_on_temperature if pwm_state is not None else None,
        }

    @property
    def native_temperature_unit(self) -> str:
        """Return the unit the temperatures are reported in."""
        return self._temperature_unit

    @property
    def native_target_temperature(self) -> Optional[float]:
        """Return the temperature we try to reach."""
        return self._demand_control.target_temperature

    @property
    def native_current_temperature(self) -> Optional[float]:
        """Return the sensor temperature."""
        return self._demand_control.current_temperature

    # Home Assistant before 2026.11 only reads the non-native names.
    @property
    def temperature_unit(self) -> str:
        return self.native_temperature_unit

    @property
    def target_temperature(self) -> Optional[float]:
        return self.native_target_temperature

    @property
    def current_temperature(self) -> Optional[float]:
        return self.native_current_temperature

    @property
    def current_humidity(self) -> Optional[float]:
        """Return the sensor humidity."""
        return self._demand_control.current_humidity

    @property
    def target_temperature_step(self) -> float:
        """Return the target temperature step to control the thermostat"""
        return self._config.limits.target_temperature_step

    @property
    def hvac_mode(self) -> Optional[HVACMode]:
        """Get the current HVAC mode."""
        return self._demand_control.hvac_mode

    @property
    def hvac_action(self) -> HVACAction:
        """Get the current HVAC action."""
        return self._demand_control.hvac_action

    @property
    def pwm(self):
        return self._heating_control.pwm_state

    async def async_reset_integral(self) -> None:
        """Service to reset the integral part of the PID controllers."""
        _LOGGER.info("Reset Integral action called for %s", self.entity_id)
        self._demand_control.reset_control_state()

    async def async_set_pulse_width_modulation(self, enabled: bool) -> None:
        """Service to enable or disable Pulse Width Modulation."""
        _LOGGER.info("Pulse Width Modulation action called for %s with enabled=%s", self.entity_id, enabled)

        if enabled:
            self._heating_control.enable_pwm()
        else:
            self._heating_control.disable_pwm()

    async def async_estimate_preheat(self, target_temperature: float, ready_time: Optional[datetime] = None, room: Optional[str] = None) -> dict[str, Any]:
        """Service to estimate how long before the ready time heating has to start."""
        return await self._demand_control.async_estimate_preheat(target_temperature, ready_time, room)

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
        if hvac_mode not in (HVACMode.HEAT, HVACMode.OFF):
            _LOGGER.error("Unrecognized hvac mode: %s", hvac_mode)
            return

        self._demand_control.set_hvac_mode(hvac_mode)

        # Collect which climates to control
        climates = self._config.radiators[:]
        if self._config.presets.sync_climates_with_mode:
            climates += self._demand_control.areas.ids()

        if cascade:
            # Set the hvac mode for those climate devices
            for entity_id in climates:
                state = self.hass.states.get(entity_id)
                if state is None or hvac_mode not in (state.attributes.get("hvac_modes") or []):
                    continue

                data = {ATTR_ENTITY_ID: entity_id, ATTR_HVAC_MODE: hvac_mode}
                await self.hass.services.async_call(CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE, data, blocking=True)

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
                self._pre_custom_temperature = self.native_target_temperature

            # Set the preset mode and target temperature
            self._attr_preset_mode = preset_mode
            await self.async_set_target_temperature(self._presets[preset_mode])

            # Set the temperature for each room, when enabled
            if self._config.presets.sync_climates_with_preset:
                for entity_id in self._demand_control.areas.ids():
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
        if not self._demand_control.set_target_temperature(temperature):
            return

        if cascade:
            # Set the target temperature for each main climate
            for entity_id in self._config.radiators:
                data = {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: temperature}
                await self.hass.services.async_call(CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, data, blocking=True)

            # Set the target temperature for the connected thermostat
            if self._config.push_setpoint_to_thermostat:
                if self._config.thermostat is not None:
                    data = {ATTR_ENTITY_ID: self._config.thermostat, ATTR_TEMPERATURE: temperature}
                    await self.hass.services.async_call(CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, data, blocking=True)

                await self._coordinator.async_set_control_thermostat_setpoint(temperature)

        # Write the state to Home Assistant
        self.async_write_ha_state()

    def _register_event_listeners(self) -> None:
        """Register event listeners."""
        if self._event_listeners_registered:
            return

        self._event_listeners_registered = True

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self._demand_control.areas.ids(), self._async_room_changed
            )
        )

        if len(self._config.window_sensors) > 0:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, list(self._config.window_sensors), self._async_window_sensor_changed
                )
            )

    async def _async_room_changed(self, event: Event[EventStateChangedData]) -> None:
        """Remember the target temperature a room has while at home."""
        new_state = event.data.get("new_state")
        if not new_state or self._rooms is None:
            return

        if (target_temperature := new_state.attributes.get(ATTR_TEMPERATURE)) is None:
            return

        if new_state.entity_id in self._rooms and self.preset_mode != PRESET_HOME:
            return

        if self._rooms.get(new_state.entity_id) == float(target_temperature):
            return

        self._rooms[new_state.entity_id] = float(target_temperature)
        _LOGGER.debug(f"Updated area preset temperature for {new_state.entity_id} to {target_temperature}")

        self.async_write_ha_state()

    async def _async_window_sensor_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle changes to the contact sensor entity."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        old_state = event.data.get("old_state")
        if old_state is not None and old_state.state == new_state.state:
            return

        _LOGGER.debug(f"Window Sensor Changed to {new_state.state}.")

        if new_state.state == STATE_ON:
            if self.preset_mode == PRESET_ACTIVITY:
                return

            if self._window_sensor_handle is not None:
                self._window_sensor_handle.cancel()

            handle = asyncio.create_task(
                asyncio.sleep(self._config.sensors.window_minimum_open_time_seconds)
            )
            self._window_sensor_handle = handle
            self._pre_activity_temperature = self.native_target_temperature or self.min_temp

            try:
                await handle
                await self.async_set_preset_mode(PRESET_ACTIVITY)
            except asyncio.CancelledError:
                _LOGGER.debug("Window closed before minimum open time.")
            finally:
                if self._window_sensor_handle is handle:
                    self._window_sensor_handle = None

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
        for entity_id in self._demand_control.areas.ids():
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
    def _build_supported_features() -> ClimateEntityFeature:
        """Determine supported features based on Home Assistant version."""
        supported = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
        if hasattr(ClimateEntityFeature, "TURN_ON"):
            supported |= ClimateEntityFeature.TURN_ON

        if hasattr(ClimateEntityFeature, "TURN_OFF"):
            supported |= ClimateEntityFeature.TURN_OFF

        return supported

    @staticmethod
    def _build_presets(config_options: Mapping[str, float]) -> dict[str, float]:
        """Build preset temperature mapping from config options."""
        conf_presets = {p: f"{p}_temperature" for p in (PRESET_ACTIVITY, PRESET_AWAY, PRESET_HOME, PRESET_SLEEP, PRESET_COMFORT)}
        return {key: config_options[value] for key, value in conf_presets.items() if key in conf_presets}
