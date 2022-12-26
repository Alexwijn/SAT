"""Climate platform for SAT."""
import logging
import time

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
    DOMAIN as CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE, ATTR_HVAC_MODE, SERVICE_SET_TEMPERATURE
)
from homeassistant.components.sensor import (
    DOMAIN as SENSOR_DOMAIN
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (ATTR_TEMPERATURE, STATE_UNAVAILABLE, STATE_UNKNOWN, ATTR_ENTITY_ID)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt

from . import PID, SatCoordinator
from .const import *

_LOGGER = logging.getLogger(__name__)

CONF_PRESETS = {
    p: f"{p}_temperature"
    for p in (
        PRESET_AWAY,
        PRESET_HOME,
        PRESET_SLEEP,
        PRESET_COMFORT,
    )
}


def get_time_in_seconds(time_str: str) -> float:
    time = dt.parse_time(time_str)
    return (time.hour * 3600) + (time.minute * 60) + time.second


def create_pid_controller(options) -> PID:
    sample_time = get_time_in_seconds(
        options.get(CONF_SAMPLE_TIME)
    )

    if sample_time <= 0:
        sample_time = 0.01

    return PID(
        Kp=float(options.get(CONF_PROPORTIONAL)),
        Ki=float(options.get(CONF_INTEGRAL)),
        Kd=float(options.get(CONF_DERIVATIVE)),
        sample_time=sample_time
    )


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities):
    """Setup sensor platform."""
    climates = []
    options = OPTIONS_DEFAULTS.copy()
    options.update(config_entry.data)
    options.update(config_entry.options)

    for climate in options[CONF_CLIMATES]:
        climates.append(SatClimate(
            hass.data[DOMAIN][config_entry.entry_id][COORDINATOR],
            config_entry,
            CLIMATE_DOMAIN,
            climate,
            hass.config.units.temperature_unit
        ))

    for sensor in options[CONF_SENSORS]:
        climates.append(SatClimate(
            hass.data[DOMAIN][config_entry.entry_id][COORDINATOR],
            config_entry,
            SENSOR_DOMAIN,
            sensor,
            hass.config.units.temperature_unit
        ))

    async_add_entities(climates)
    hass.data[DOMAIN][config_entry.entry_id][CLIMATES] = climates


class SatClimate(CoordinatorEntity, ClimateEntity, RestoreEntity):
    def __init__(self, coordinator: SatCoordinator, config_entry: ConfigEntry, domain: str, entity_id: str, unit: str):
        super().__init__(coordinator)

        options = OPTIONS_DEFAULTS.copy()
        options.update(config_entry.options)

        state = coordinator.hass.states.get(entity_id)

        presets = {
            key: options.get(value) for key, value in CONF_PRESETS.items() if value in options
        }

        self._domain = domain
        self._entity_id = entity_id
        self._pid = create_pid_controller(options)

        self.outside_sensor_entity_id = config_entry.data.get(CONF_OUTSIDE_SENSOR_ENTITY_ID)
        outside_sensor_entity = coordinator.hass.states.get(self.outside_sensor_entity_id)

        self._state = state
        self._hvac_mode = None
        self._target_temperature = None
        self._saved_target_temperature = None

        self._current_temperature = None
        if state is not None and state.state not in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
            if domain == SENSOR_DOMAIN:
                self._current_temperature = float(state.state)

            if domain == CLIMATE_DOMAIN and state.attributes.get("current_temperature") is not None:
                self._current_temperature = float(state.attributes.get("current_temperature"))

        self._outside_temperature = None
        if outside_sensor_entity is not None and outside_sensor_entity.state not in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
            self._outside_temperature = float(outside_sensor_entity.state)

        self._attr_name = config_entry.data.get(CONF_NAME)
        self._attr_id = config_entry.data.get(CONF_NAME).lower()

        self._heating_system = options.get(CONF_HEATING_SYSTEM)
        self._sensor_stall = get_time_in_seconds(options.get(CONF_SENSOR_STALL))

        self._attr_temperature_unit = unit
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_preset_mode = PRESET_NONE
        self._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
        self._attr_preset_modes = [PRESET_NONE] + list(presets.keys())
        self._attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE

        self._presets = presets
        self._coordinator = coordinator
        self._config_entry = config_entry

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._entity_id], self._async_entity_state_changed
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
                    self._pid.setpoint = float(old_state.attributes[ATTR_TEMPERATURE])
                    self._target_temperature = float(old_state.attributes[ATTR_TEMPERATURE])

            if not self._hvac_mode and old_state.state:
                self._hvac_mode = old_state.state
        else:
            # No previous state, try and restore defaults
            if self._target_temperature is None:
                self._pid.setpoint = self.min_temp
                self._target_temperature = self.min_temp
                _LOGGER.warning("No previously saved temperature, setting to %s", self._target_temperature)

            # Set default state to off
            if not self._hvac_mode:
                self._hvac_mode = HVACMode.OFF

    @property
    def name(self):
        """Return the friendly name of the sensor."""
        return f"{self._state.name}"

    @property
    def extra_state_attributes(self):
        """Return device state attributes."""
        proportional, integral, derivative = self._pid.components

        return {
            "integral": integral,
            "derivative": derivative,
            "proportional": proportional,
            "pid_auto_mode": self._pid.auto_mode,

            "setpoint": self._coordinator.setpoint,
            "heating_curve": self._coordinator.heating_curve,
            "outside_temperature": self._coordinator.outside_temperature
        }

    @property
    def device_info(self):
        return {
            "name": NAME,
            "model": VERSION,
            "manufacturer": NAME,
            "identifiers": {(DOMAIN, self._config_entry.data.get(CONF_NAME), CLIMATES)},
        }

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME)}-{self._domain}-{self._entity_id}"

    @property
    def current_temperature(self):
        """Return the sensor temperature."""
        return self._current_temperature

    @property
    def current_outside_temperature(self):
        return self._outside_temperature

    @property
    def hvac_action(self):
        if self._domain == CLIMATE_DOMAIN and self._state.attributes.get("hvac_action"):
            return self._state.attributes.get("hvac_action")

        if self._hvac_mode == HVACMode.OFF:
            return HVACAction.OFF

        if not self.coordinator.is_device_active:
            return HVACAction.IDLE

        return HVACAction.HEATING

    @property
    def hvac_mode(self):
        if self._hvac_mode in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
            return HVACMode.OFF

        return self._hvac_mode

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._target_temperature

    @property
    def target_temperature_step(self):
        return 0.5

    @property
    def domain(self):
        return self._domain

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        if preset_mode not in (self.preset_modes or []):
            raise ValueError(f"Got unsupported preset_mode {preset_mode}. Must be one of {self.preset_modes}")

        if preset_mode == self._attr_preset_mode:
            return

        if preset_mode == PRESET_NONE:
            self._target_temperature = self._saved_target_temperature
        else:
            if self._attr_preset_mode == PRESET_NONE:
                self._saved_target_temperature = self._target_temperature

            self._target_temperature = self._presets[preset_mode]

        self._attr_preset_mode = preset_mode
        self.async_write_ha_state()

    async def _async_entity_state_changed(self, event):
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        _LOGGER.debug(f"Entity State Changed {self._domain}.")

        if self._domain == SENSOR_DOMAIN:
            self._current_temperature = float(new_state.state)

        if self._domain == CLIMATE_DOMAIN:
            self._hvac_mode = new_state.state
            self._target_temperature = new_state.attributes.get("temperature")
            self._current_temperature = new_state.attributes.get("current_temperature")

        self._state = new_state
        self.async_write_ha_state()

        await self._async_control_pid()

    async def _async_control_pid(self):
        if self._sensor_stall != 0 and time.monotonic() - self._pid.last_time > self._sensor_stall:
            _LOGGER.debug("Sensor stalled, resetting PID")
            self._pid.reset()

        self._pid(self._current_temperature)

    async def async_set_temperature(self, **kwargs) -> None:
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return

        self._pid.setpoint = temperature
        self._pid.reset()

        self._target_temperature = temperature
        self.async_write_ha_state()

        if self._domain == CLIMATE_DOMAIN:
            data = {ATTR_ENTITY_ID: self._entity_id, ATTR_TEMPERATURE: temperature}
            await self.hass.services.async_call(CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, data, blocking=True)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set hvac mode."""
        if hvac_mode == HVACMode.HEAT:
            self._hvac_mode = HVACMode.HEAT
        elif hvac_mode == HVACMode.OFF:
            self._hvac_mode = HVACMode.OFF
        else:
            _LOGGER.error("Unrecognized hvac mode: %s", hvac_mode)
            return

        if self._domain == CLIMATE_DOMAIN:
            data = {ATTR_ENTITY_ID: self._entity_id, ATTR_HVAC_MODE: hvac_mode}
            await self.hass.services.async_call(CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE, data, blocking=True)

        self.async_write_ha_state()

    def calculate_control_setpoint(self, heating_curve: float):
        proportional, integral, derivative = self._pid.components
        setpoint = (heating_curve + proportional + integral + derivative)

        if setpoint < 10:
            return 10.0
        elif setpoint > 75 and self._heating_system == CONF_RADIATOR_HIGH_TEMPERATURES:
            return 75.0
        elif setpoint > 55 and self._heating_system == CONF_RADIATOR_LOW_TEMPERATURES:
            return 55.0
        elif setpoint > 50 and self._heating_system == CONF_UNDERFLOOR:
            return 50.0

        return setpoint
