"""Climate platform for SAT."""
import datetime
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
    PRESET_COMFORT, DOMAIN as CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, STATE_UNAVAILABLE, STATE_UNKNOWN, ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt

from . import SatDataUpdateCoordinator, mean
from .const import *
from .entity import SatEntity
from .pid import PID

HOT_TOLERANCE = 0.3
COLD_TOLERANCE = 0.1

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


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_devices):
    """Setup sensor platform."""
    climate = SatClimate(
        hass.data[DOMAIN][config_entry.entry_id][COORDINATOR],
        config_entry,
        hass.config.units.temperature_unit
    )

    async_add_devices([climate])
    hass.data[DOMAIN][config_entry.entry_id][CLIMATE] = climate


class SatClimate(SatEntity, ClimateEntity, RestoreEntity):
    def __init__(self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry, unit: str):
        super().__init__(coordinator, config_entry)

        options = OPTIONS_DEFAULTS.copy()
        options.update(config_entry.options)

        presets = {key: options.get(value) for key, value in CONF_PRESETS.items() if value in options}

        self._pid = create_pid_controller(options)

        self.inside_sensor_entity_id = config_entry.data.get(CONF_INSIDE_SENSOR_ENTITY_ID)
        inside_sensor_entity = coordinator.hass.states.get(self.inside_sensor_entity_id)

        self.outside_sensor_entity_id = config_entry.data.get(CONF_OUTSIDE_SENSOR_ENTITY_ID)
        outside_sensor_entity = coordinator.hass.states.get(self.outside_sensor_entity_id)

        self._hvac_mode = None
        self._target_temperature = None
        self._saved_target_temperature = None

        self._current_temperature = None
        if inside_sensor_entity is not None and inside_sensor_entity.state not in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
            self._current_temperature = float(inside_sensor_entity.state)

        self._outside_temperature = None
        if outside_sensor_entity is not None and outside_sensor_entity.state not in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
            self._outside_temperature = float(outside_sensor_entity.state)

        self._setpoint = None
        self._heating_curve = None
        self._is_device_active = False

        self._climates = options.get(CONF_CLIMATES)
        self._main_climates = options.get(CONF_MAIN_CLIMATES)

        self._simulation = options.get(CONF_SIMULATION)
        self._curve_move = options.get(CONF_HEATING_CURVE)
        self._heating_system = options.get(CONF_HEATING_SYSTEM)
        self._heating_curve_move = options.get(CONF_HEATING_CURVE_MOVE)
        self._overshoot_protection = options.get(CONF_OVERSHOOT_PROTECTION)
        self._target_temperature_step = options.get(CONF_TARGET_TEMPERATURE_STEP)
        self._sensor_max_value_age = get_time_in_seconds(options.get(CONF_SENSOR_MAX_VALUE_AGE))

        self._attr_name = config_entry.data.get(CONF_NAME)
        self._attr_id = config_entry.data.get(CONF_NAME).lower()

        self._attr_temperature_unit = unit
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_preset_mode = PRESET_NONE
        self._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
        self._attr_preset_modes = [PRESET_NONE] + list(presets.keys())
        self._attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE

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
            async_track_state_change_event(
                self.hass, [self.outside_sensor_entity_id], self._async_outside_sensor_changed
            )
        )

        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._async_control_heating, datetime.timedelta(seconds=30)
            )
        )

        for climate_id in self._main_climates:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [climate_id], self._async_main_climate_changed
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

    @property
    def name(self):
        """Return the friendly name of the sensor."""
        return self._attr_name

    @property
    def extra_state_attributes(self):
        """Return device state attributes."""
        proportional, integral, derivative = self._pid.components

        return {
            "integral": integral,
            "derivative": derivative,
            "proportional": proportional,
            "pid_auto_mode": self._pid.auto_mode,
            "pid_last_update": self._pid.last_update,

            "setpoint": self._setpoint,
            "valves_open": self.valves_open,
            "heating_curve": self._heating_curve,
            "outside_temperature": self._outside_temperature
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
        if self._hvac_mode in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
            return HVACMode.OFF

        return self._hvac_mode

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._target_temperature

    @property
    def target_temperature_step(self):
        return self._target_temperature_step

    @property
    def climate_differences(self):
        differences = []
        for climate in self._climates:
            state = self.hass.states.get(climate)
            if state is None or state.state in [STATE_UNKNOWN, STATE_UNAVAILABLE, HVACMode.OFF]:
                continue

            target_temperature = float(state.attributes.get("temperature"))
            current_temperature = float(state.attributes.get("current_temperature") or target_temperature)

            differences.append(round(target_temperature - current_temperature, 1))

        if len(differences) == 0:
            return [0]

        return differences

    @property
    def valves_open(self):
        climates = self._climates + self._main_climates

        if len(climates) == 0:
            return True

        for climate in climates:
            state = self.hass.states.get(climate)

            if state is None or state.state in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
                continue

            if state.state == HVACMode.OFF:
                continue

            # If the climate hvac action report heating we can safely assume it's open
            if state.attributes.get("hvac_action") == HVACAction.HEATING:
                return True

            # For climate that doesn't support hvac action, we can assume that if the temperature is not at target it's open
            if state.attributes.get("hvac_action") is None:
                target_temperature = state.attributes.get("temperature")
                current_temperature = state.attributes.get("current_temperature")

                if current_temperature is not None and float(target_temperature) + HOT_TOLERANCE >= float(current_temperature):
                    return True

        return False

    def _get_boiler_value(self, key: str):
        boiler = self._coordinator.data[gw_vars.BOILER]
        if boiler is None:
            return None

        return boiler.get(key)

    def _calculate_heating_curve_value(self) -> float:
        system_offset = 0
        if self._heating_system == CONF_UNDERFLOOR:
            system_offset = 8

        if self._curve_move == 0.1:
            return self._heating_curve_move - system_offset + 36.4 - (0.00495 * self._outside_temperature ** 2) - (0.32 * self._outside_temperature)

        if self._curve_move == 0.2:
            return self._heating_curve_move - system_offset + 37.7 - (0.0052 * self._outside_temperature ** 2) - (0.38 * self._outside_temperature)

        if self._curve_move == 0.3:
            return self._heating_curve_move - system_offset + 39.0 - (0.00545 * self._outside_temperature ** 2) - (0.44 * self._outside_temperature)

        if self._curve_move == 0.4:
            return self._heating_curve_move - system_offset + 40.3 - (0.0057 * self._outside_temperature ** 2) - (0.5 * self._outside_temperature)

        if self._curve_move == 0.5:
            return self._heating_curve_move - system_offset + 41.6 - (0.00595 * self._outside_temperature ** 2) - (0.56 * self._outside_temperature)

        if self._curve_move == 0.6:
            return self._heating_curve_move - system_offset + 43.1 - (0.0067 * self._outside_temperature ** 2) - (0.62 * self._outside_temperature)

        if self._curve_move == 0.7:
            return self._heating_curve_move - system_offset + 44.6 - (0.00745 * self._outside_temperature ** 2) - (0.68 * self._outside_temperature)

        if self._curve_move == 0.8:
            return self._heating_curve_move - system_offset + 46.1 - (0.0082 * self._outside_temperature ** 2) - (0.74 * self._outside_temperature)

        if self._curve_move == 0.9:
            return self._heating_curve_move - system_offset + 47.6 - (0.00895 * self._outside_temperature ** 2) - (0.8 * self._outside_temperature)

        if self._curve_move == 1.0:
            return self._heating_curve_move - system_offset + 49.1 - (0.0097 * self._outside_temperature ** 2) - (0.86 * self._outside_temperature)

        if self._curve_move == 1.1:
            return self._heating_curve_move - system_offset + 50.8 - (0.01095 * self._outside_temperature ** 2) - (0.92 * self._outside_temperature)

        if self._curve_move == 1.2:
            return self._heating_curve_move - system_offset + 52.5 - (0.0122 * self._outside_temperature ** 2) - (0.98 * self._outside_temperature)

        if self._curve_move == 1.3:
            return self._heating_curve_move - system_offset + 54.2 - (0.01345 * self._outside_temperature ** 2) - (1.04 * self._outside_temperature)

        if self._curve_move == 1.4:
            return self._heating_curve_move - system_offset + 55.9 - (0.0147 * self._outside_temperature ** 2) - (1.1 * self._outside_temperature)

        if self._curve_move == 1.5:
            return self._heating_curve_move - system_offset + 57.5 - (0.0157 * self._outside_temperature ** 2) - (1.16 * self._outside_temperature)

        if self._curve_move == 1.6:
            return self._heating_curve_move - system_offset + 59.4 - (0.01644 * self._outside_temperature ** 2) - (1.24 * self._outside_temperature)

        if self._curve_move == 1.7:
            return self._heating_curve_move - system_offset + 61.3 - (0.01718 * self._outside_temperature ** 2) - (1.32 * self._outside_temperature)

        if self._curve_move == 1.8:
            return self._heating_curve_move - system_offset + 63.2 - (0.01792 * self._outside_temperature ** 2) - (1.4 * self._outside_temperature)

        if self._curve_move == 1.9:
            return self._heating_curve_move - system_offset + 65.1 - (0.01866 * self._outside_temperature ** 2) - (1.48 * self._outside_temperature)

        if self._curve_move == 2.0:
            return self._heating_curve_move - system_offset + 67.0 - (0.0194 * self._outside_temperature ** 2) - (1.56 * self._outside_temperature)

        if self._curve_move == 2.1:
            return self._heating_curve_move - system_offset + 69.1 - (0.0197 * self._outside_temperature ** 2) - (1.66 * self._outside_temperature)

        if self._curve_move == 2.2:
            return self._heating_curve_move - system_offset + 71.2 - (0.01995 * self._outside_temperature ** 2) - (1.76 * self._outside_temperature)

        if self._curve_move == 2.3:
            return self._heating_curve_move - system_offset + 73.3 - (0.0202 * self._outside_temperature ** 2) - (1.86 * self._outside_temperature)

        if self._curve_move == 2.4:
            return self._heating_curve_move - system_offset + 75.4 - (0.02045 * self._outside_temperature ** 2) - (1.96 * self._outside_temperature)

        if self._curve_move == 2.5:
            return self._heating_curve_move - system_offset + 77.5 - (0.02007 * self._outside_temperature ** 2) - (2.06 * self._outside_temperature)

        if self._curve_move == 2.6:
            return self._heating_curve_move - system_offset + 79.8 - (0.02045 * self._outside_temperature ** 2) - (2.18 * self._outside_temperature)

        if self._curve_move == 2.7:
            return self._heating_curve_move - system_offset + 82.1 - (0.0202 * self._outside_temperature ** 2) - (2.3 * self._outside_temperature)

        if self._curve_move == 2.8:
            return self._heating_curve_move - system_offset + 84.4 - (0.01995 * self._outside_temperature ** 2) - (2.42 * self._outside_temperature)

        if self._curve_move == 2.9:
            return self._heating_curve_move - system_offset + 86.7 - (0.0197 * self._outside_temperature ** 2) - (2.54 * self._outside_temperature)

        if self._curve_move == 3.0:
            return self._heating_curve_move - system_offset + 89.0 - (0.01945 * self._outside_temperature ** 2) - (2.66 * self._outside_temperature)

    def _calculate_control_setpoint(self):
        if max(self.climate_differences) >= COLD_TOLERANCE:
            _LOGGER.info(f"Detected difference higher than {COLD_TOLERANCE}, falling back to heating curve,")
            setpoint = self._heating_curve
        else:
            proportional, integral, derivative = self._pid.components
            _LOGGER.info(f"Calculated pid {self._pid.components}")
            setpoint = (self._heating_curve + proportional + integral + derivative)

        if setpoint < 10:
            return 10.0
        elif setpoint > 75 and self._heating_system == CONF_RADIATOR_HIGH_TEMPERATURES:
            return 75.0
        elif setpoint > 55 and self._heating_system == CONF_RADIATOR_LOW_TEMPERATURES:
            return 55.0
        elif setpoint > 50 and self._heating_system == CONF_UNDERFLOOR:
            return 50.0

        return setpoint

    async def _async_inside_sensor_changed(self, event):
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        _LOGGER.debug("Inside Sensor Changed.")
        self._current_temperature = float(new_state.state)
        self.async_write_ha_state()

        await self._async_control_pid()
        await self._async_control_heating()

    async def _async_outside_sensor_changed(self, event):
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        _LOGGER.debug("Outside Sensor Changed.")
        self._outside_temperature = float(new_state.state)
        self.async_write_ha_state()

        await self._async_control_heating()

    async def _async_main_climate_changed(self, event):
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        target_temperature = new_state.attributes.get("temperature")
        if target_temperature is not None and float(target_temperature) != self._target_temperature:
            _LOGGER.debug("Main Climate Changed.")
            await self._async_set_setpoint(target_temperature)

    async def _async_control_heating(self, time=None):
        if self._current_temperature is None or self._outside_temperature is None:
            return

        climates_difference = mean(self.climate_differences)
        too_cold = self.target_temperature + COLD_TOLERANCE >= self._current_temperature
        too_hot = self.current_temperature >= self._target_temperature + HOT_TOLERANCE

        if not too_cold and climates_difference >= COLD_TOLERANCE:
            too_cold = True

        if too_hot and climates_difference >= -HOT_TOLERANCE:
            too_hot = False

        if self._is_device_active:
            if too_hot or not self.valves_open or self.hvac_action == HVACAction.OFF:
                await self._async_control_heater(False)

            await self._async_control_setpoint()
        else:
            if too_cold and self.valves_open and self.hvac_action != HVACAction.OFF:
                await self._async_control_heater(True)
                await self._async_control_setpoint()
            elif self._get_boiler_value(gw_vars.DATA_MASTER_CH_ENABLED):
                await self._async_control_heater(False)

    async def _async_control_pid(self):
        if self._sensor_max_value_age != 0 and time.monotonic() - self._pid.last_update > self._sensor_max_value_age:
            self._pid.reset()

        self._pid(self._current_temperature)

    async def _async_control_heater(self, enabled: bool):
        if not self._simulation:
            await self._coordinator.api.set_ch_enable_bit(int(enabled))

        self._is_device_active = enabled

        _LOGGER.info("Set central heating to %d", enabled)

    async def _async_control_setpoint(self):
        if self._is_device_active:
            self._heating_curve = round(self._calculate_heating_curve_value(), 1)
            _LOGGER.info("Calculated heating curve: %d", self._heating_curve)

            self._setpoint = round(self._calculate_control_setpoint(), 1)
        else:
            self._setpoint = 10
            self._heating_curve = None

        if not self._simulation:
            await self._coordinator.api.set_control_setpoint(self._setpoint)

        _LOGGER.info("Set control setpoint to %d", self._setpoint)

    async def async_set_temperature(self, **kwargs) -> None:
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return

        self._attr_preset_mode = PRESET_NONE
        await self._async_set_setpoint(temperature)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode not in self.preset_modes:
            raise ValueError(f"Got unsupported preset_mode {preset_mode}. Must be one of {self.preset_modes}")

        if preset_mode == self._attr_preset_mode:
            return

        if preset_mode == PRESET_NONE:
            self._attr_preset_mode = PRESET_NONE
            await self._async_set_setpoint(self._saved_target_temperature)
        else:
            if self._attr_preset_mode == PRESET_NONE:
                self._saved_target_temperature = self._target_temperature

            self._attr_preset_mode = preset_mode
            await self._async_set_setpoint(self._presets[preset_mode])

    async def _async_set_setpoint(self, temperature: float):
        self._target_temperature = temperature

        self._pid.setpoint = temperature
        self._pid.reset()

        for entity_id in self._main_climates:
            data = {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: temperature}
            await self.hass.services.async_call(CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, data, blocking=True)

        self.async_write_ha_state()
        await self._async_control_heating()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.HEAT:
            self._hvac_mode = HVACMode.HEAT
        elif hvac_mode == HVACMode.OFF:
            self._hvac_mode = HVACMode.OFF
        else:
            _LOGGER.error("Unrecognized hvac mode: %s", hvac_mode)
            return

        self.async_write_ha_state()
        await self._async_control_heating()
