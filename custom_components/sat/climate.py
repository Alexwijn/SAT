"""Climate platform for SAT."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

import voluptuous as vol
from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
    PRESET_ACTIVITY,
    PRESET_NONE,
    ATTR_HVAC_MODE,
    ATTR_PRESET_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE, EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, Event, HomeAssistant, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import *
from .contact_sensors import ContactSensors
from .coordinator import SatDataUpdateCoordinator
from .demand_control import SatDemandControl
from .entity import SatEntity
from .entry_data import SatConfig, get_entry_data
from .heating_control import SatHeatingControl
from .helpers import float_value
from .presets import RoomPresets, preset_temperatures
from .summer_simmer import SummerSimmer
from .types import PWMStatus

ATTR_SETPOINT = "setpoint"
ATTR_RELATIVE_MODULATION_VALUE = "relative_modulation_value"

ATTR_PRE_CUSTOM_TEMPERATURE = "pre_custom_temperature"
ATTR_PRE_ACTIVITY_TEMPERATURE = "pre_activity_temperature"
ATTR_PRE_ACTIVITY_PRESET = "pre_activity_preset"

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

        self._room_presets = RoomPresets(config.presets)
        self._presets: dict[str, float] = preset_temperatures(config.presets)

        self._event_listeners_registered: bool = False
        self._pre_activity_preset: Optional[str] = None
        self._pre_custom_temperature: Optional[float] = None
        self._pre_activity_temperature: Optional[float] = None
        self._contact_sensors = ContactSensors(config)

        self._temperature_unit = unit
        self._attr_preset_mode = PRESET_NONE
        self._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
        self._attr_preset_modes = [PRESET_NONE] + list(self._presets.keys())
        self._attr_supported_features = self._build_supported_features(config.rooms_only)

        # System Configuration
        self._attr_name = self._config.name
        self._attr_id = self._config.entry_id

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        if self._demand_control.started:
            await self._demand_control.async_rename(self.entity_id)
        else:
            # Restore the previous state if available, or set default values
            await self._restore_previous_state_or_set_defaults()

            await self._demand_control.async_added_to_hass(self.entity_id)

        if self.hass.state is CoreState.running:
            self._register_event_listeners()
        else:
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, self._on_home_assistant_started)

        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_DEMAND_CONTROL_UPDATED, self._on_demand_control_updated)
        )

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity about to be removed."""
        self._event_listeners_registered = False

        self._contact_sensors.cancel()

        if not self._is_being_renamed:
            await self._demand_control.async_will_remove_from_hass()

        await super().async_will_remove_from_hass()

    @property
    def _is_being_renamed(self) -> bool:
        """Return whether the entity is only removed to come back under another entity id."""
        entry = self.registry_entry

        return entry is not None and not entry.disabled and entry.entity_id != self.entity_id

    async def _restore_previous_state_or_set_defaults(self) -> None:
        """Restore the previous state if available or set default values."""
        old_state = await self.async_get_last_state()

        if old_state is not None:
            self._heating_control.restore(old_state)

            if old_state.attributes.get(ATTR_PRESET_MODE):
                self._attr_preset_mode = old_state.attributes.get(ATTR_PRESET_MODE)

            if old_state.attributes.get(ATTR_PRE_ACTIVITY_TEMPERATURE):
                self._pre_activity_temperature = old_state.attributes.get(ATTR_PRE_ACTIVITY_TEMPERATURE)

            if (preset := old_state.attributes.get(ATTR_PRE_ACTIVITY_PRESET)) in self._attr_preset_modes:
                self._pre_activity_preset = preset
                self._contact_sensors.assume_open()

            if old_state.attributes.get(ATTR_PRE_CUSTOM_TEMPERATURE):
                self._pre_custom_temperature = old_state.attributes.get(ATTR_PRE_CUSTOM_TEMPERATURE)

        self._demand_control.restore(old_state, self.min_temp)
        self._update_window_pause()
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
            "pre_activity_preset": self._pre_activity_preset,

            "room_errors": self._demand_control.room_errors,
            "room_temperatures": self._demand_control.room_temperatures,
            "current_humidity": self.current_humidity,
            "setpoint": self._heating_control.control_setpoint,

            "summer_simmer_index": SummerSimmer.index(self.native_current_temperature, self.current_humidity),
            "summer_simmer_perception": SummerSimmer.perception(self.native_current_temperature, self.current_humidity),

            "valves_open": self._demand_control.valves_open,
            "heating_blocked_by": self._demand_control.heating_blocked_by,
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
        if self._config.rooms_only:
            raise ServiceValidationError(f"{self.entity_id} has no target temperature of its own, set the temperature of the rooms instead")

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
        """Set the preset mode for the thermostat and the rooms."""
        if preset_mode not in self.preset_modes:
            raise ValueError(f"Got unsupported preset_mode {preset_mode}. Must be one of {self.preset_modes}")

        # Return if the given preset mode is already set
        if preset_mode == self._attr_preset_mode:
            return

        previous_preset_mode = self._attr_preset_mode
        self._attr_preset_mode = preset_mode

        if not self._config.rooms_only:
            if preset_mode == PRESET_NONE:
                if self._pre_custom_temperature is not None:
                    await self.async_set_target_temperature(self._pre_custom_temperature)
            else:
                # Save the current target temperature if the preset mode is being set for the first time
                if previous_preset_mode == PRESET_NONE:
                    self._pre_custom_temperature = self.native_target_temperature

                await self.async_set_target_temperature(self._presets[preset_mode])

        if self._config.rooms_only or self._config.presets.sync_climates_with_preset:
            await self._async_apply_preset_to_rooms(preset_mode)

        self._update_window_pause()
        self.async_write_ha_state()

    async def _async_apply_preset_to_rooms(self, preset_mode: str) -> None:
        """Set every room to what the preset means for that room."""
        for entity_id in self._demand_control.areas.ids():
            state = self.hass.states.get(entity_id)
            if state is None or state.state == HVACMode.OFF:
                continue

            target_temperature = self._room_presets.target(entity_id, preset_mode)

            if target_temperature is None or float_value(state.attributes.get(ATTR_TEMPERATURE)) == target_temperature:
                continue

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

    @callback
    def _on_home_assistant_started(self, _event: Event) -> None:
        """Start listening once Home Assistant is up."""
        self._register_event_listeners()

    def _register_event_listeners(self) -> None:
        """Register event listeners."""
        if self._event_listeners_registered:
            return

        self._event_listeners_registered = True

        if len(self._config.window_sensors) > 0 or self._pre_activity_preset is not None:
            self.async_on_remove(self._contact_sensors.track(self.hass, self._async_contacts_changed))

    async def _async_contacts_changed(self, is_open: bool) -> None:
        """Pause while the contacts are open, and undo exactly that pause once they close."""
        if is_open:
            await self._async_pause_for_window()
        else:
            await self._async_resume_from_window()

    async def _async_pause_for_window(self) -> None:
        if self.preset_mode == PRESET_ACTIVITY:
            return

        self._pre_activity_preset = self.preset_mode
        self._pre_activity_temperature = self.native_target_temperature or self.min_temp

        await self.async_set_preset_mode(PRESET_ACTIVITY)

    async def _async_resume_from_window(self) -> None:
        preset, self._pre_activity_preset = self._pre_activity_preset, None

        if preset is None or self.preset_mode != PRESET_ACTIVITY:
            self._update_window_pause()
            self.async_write_ha_state()
            return

        _LOGGER.debug("Restoring the %s preset from before the window opened.", preset)

        if preset == PRESET_NONE and self._pre_activity_temperature is not None:
            self._pre_custom_temperature = self._pre_activity_temperature

        await self.async_set_preset_mode(preset)

    def _update_window_pause(self) -> None:
        self._demand_control.set_window_paused(self._pre_activity_preset is not None and self.preset_mode == PRESET_ACTIVITY)

    @staticmethod
    def _build_supported_features(rooms_only: bool) -> ClimateEntityFeature:
        """Determine supported features based on the configuration and the Home Assistant version."""
        supported = ClimateEntityFeature.PRESET_MODE
        if not rooms_only:
            supported |= ClimateEntityFeature.TARGET_TEMPERATURE

        if hasattr(ClimateEntityFeature, "TURN_ON"):
            supported |= ClimateEntityFeature.TURN_ON

        if hasattr(ClimateEntityFeature, "TURN_OFF"):
            supported |= ClimateEntityFeature.TURN_OFF

        return supported
