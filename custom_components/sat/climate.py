"""Climate platform for SAT."""
import asyncio
import datetime
import logging

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (ATTR_TEMPERATURE, STATE_UNAVAILABLE, STATE_UNKNOWN)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from simple_pid import PID

from .const import (
    DOMAIN,
    CONF_NAME,
    CONF_ID,
    CONF_INSIDE_SENSOR_ENTITY_ID,
    CONF_OUTSIDE_SENSOR_ENTITY_ID,
    OPTIONS_DEFAULTS,
    CONF_HEATING_CURVE,
    CONF_HEATING_CURVE_MOVE,
    CONF_HEATING_SYSTEM, CONF_SIMULATION
)

from . import SatDataUpdateCoordinator
from .entity import SatEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_devices):
    """Setup sensor platform."""
    async_add_devices([SatClimate(
        hass.data[DOMAIN][config_entry.entry_id],
        config_entry,
        hass.config.units.temperature_unit
    )])


class SatClimate(SatEntity, ClimateEntity, RestoreEntity):
    def __init__(self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry, unit: str):
        super().__init__(coordinator, config_entry)

        options = OPTIONS_DEFAULTS.copy()
        options.update(config_entry.options)

        self.inside_sensor_entity_id = config_entry.data.get(CONF_INSIDE_SENSOR_ENTITY_ID)
        self.outside_sensor_entity_id = config_entry.data.get(CONF_OUTSIDE_SENSOR_ENTITY_ID)

        self._is_device_active = False
        self._temperature_lock = asyncio.Lock()

        self._hvac_mode = None
        self._target_temperature = None
        self._current_temperature = None
        self._outside_temperature = None

        self._simulation = options.get(CONF_SIMULATION)
        self._curve_move = options.get(CONF_HEATING_CURVE)
        self._heating_curve = options.get(CONF_HEATING_CURVE_MOVE)
        self._heating_system = options.get(CONF_HEATING_SYSTEM)

        self._attr_id = config_entry.data.get(CONF_ID)
        self._attr_name = config_entry.data.get(CONF_NAME)

        self._attr_temperature_unit = unit
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
        self._attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE

        self._pid = PID(45, 0, 6000)
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

        if self.outside_sensor_entity_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [self.outside_sensor_entity_id], self._async_outside_sensor_changed
                )
            )

        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._async_control_heating, datetime.timedelta(seconds=30)
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
    def extra_state_attributes(self):
        """Return device state attributes."""
        proportional, integral, derivative = self._pid.components

        return {
            "integral": integral,
            "derivative": derivative,
            "proportional": proportional,
            "outside_temperature": self._outside_temperature
        }

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return self._config_entry.data.get(CONF_ID)

    @property
    def current_temperature(self):
        """Return the sensor temperature."""
        return self._current_temperature

    @property
    def current_outside_temperature(self):
        return self._outside_temperature

    @property
    def hvac_action(self):
        if self._hvac_mode == HVACMode.OFF:
            return HVACAction.OFF

        if not self._is_device_active:
            return HVACAction.IDLE

        return HVACAction.HEATING

    @property
    def hvac_mode(self):
        return self._hvac_mode

    def _async_inside_sensor_changed(self, event):
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        self._current_temperature = float(new_state.state)

    def _async_outside_sensor_changed(self, event):
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        self._outside_temperature = float(new_state.state)

    async def _async_control_heating(self, time=None):
        if self._current_temperature is None:
            return

        self._pid(self.current_temperature)

        too_cold = self.target_temperature >= self._current_temperature + 0.3
        too_hot = self.current_temperature - 0.3 >= self._target_temperature

        if self._is_device_active:
            if too_hot:
                await self._async_control_heater(False)
            else:
                await self._async_control_setpoint()
        else:
            if too_cold:
                await self._async_control_heater(True)
                await self._async_control_setpoint()

    async def _async_control_heater(self, enabled: bool):
        self._is_device_active = enabled

        if enabled:
            _LOGGER.info("Turning on heater")
        else:
            _LOGGER.info("Turning off heater")

        if not self._simulation:
            await self._coordinator.api.set_ch_enable_bit(enabled)

    async def _async_control_setpoint(self):
        proportional, integral, derivative = self._pid.components

        heating_curve = self._coordinator.calculate_heating_curve_value(
            self._heating_system,
            self._outside_temperature,
            self._curve_move,
            self._heating_curve
        )

        _LOGGER.info("Calculated heating curve: %d", heating_curve)

        setpoint = self._coordinator.calculate_control_setpoint(
            self._heating_system,
            heating_curve,
            proportional,
            integral,
            derivative,
        )

        _LOGGER.info("Setting control setpoint to %d", setpoint)

        if not self._simulation:
            await self._coordinator.api.set_control_setpoint(setpoint)

    def set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set hvac mode."""
        if hvac_mode == HVACMode.HEAT:
            self._hvac_mode = HVACMode.HEAT
        elif hvac_mode == HVACMode.OFF:
            self._hvac_mode = HVACMode.OFF
        else:
            _LOGGER.error("Unrecognized hvac mode: %s", hvac_mode)
            return

        # Ensure we update the current operation after changing the mode
        self.async_write_ha_state()

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._target_temperature

    def set_temperature(self, **kwargs) -> None:
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return

        self._pid.setpoint = temperature
        self._pid.reset()

        self._target_temperature = temperature
        self._async_control_heating()
        self.async_write_ha_state()

    @property
    def target_temperature_step(self):
        return 0.5

    def set_humidity(self, humidity: int) -> None:
        pass

    def set_fan_mode(self, fan_mode: str) -> None:
        pass

    def set_swing_mode(self, swing_mode: str) -> None:
        pass

    def set_preset_mode(self, preset_mode: str) -> None:
        pass

    def turn_aux_heat_on(self) -> None:
        pass

    def turn_aux_heat_off(self) -> None:
        pass
