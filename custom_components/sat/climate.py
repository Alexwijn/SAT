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
    PRESET_COMFORT,
    SERVICE_SET_TEMPERATURE,
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.components.notify import DOMAIN as NOTIFY_DOMAIN, SERVICE_PERSISTENT_NOTIFICATION
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, STATE_UNAVAILABLE, STATE_UNKNOWN, ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
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
    date_time = dt.parse_time(time_str)
    return (date_time.hour * 3600) + (date_time.minute * 60) + date_time.second


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

        self._overshoot_protection_active = False
        self._overshoot_protection_data = []

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
            async_track_state_change_event(
                self.hass, [self.outside_sensor_entity_id], self._async_outside_sensor_changed
            )
        )

        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._async_control_heating, datetime.timedelta(seconds=30)
            )
        )

        for climate_id in (self._climates + self._main_climates):
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

        async def overshoot_protection(_call: ServiceCall):
            if self._overshoot_protection_active:
                _LOGGER.warning("[Overshoot Protection] Already running.")
                return

            self._hvac_mode = HVACMode.HEAT
            self._overshoot_protection_data = []
            self._overshoot_protection_active = True

            _LOGGER.warning("[Overshoot Protection] Enabled for at least 20 minutes until we found a stable return water temperature.")

            await self.hass.services.async_call(NOTIFY_DOMAIN, SERVICE_PERSISTENT_NOTIFICATION, {
                "title": "Overshoot Protection Calculation",
                "message": "Enabled for at least 20 minutes until we found a stable return water temperature."
            })

        self.hass.services.async_register(DOMAIN, "overshoot_protection", overshoot_protection)

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
            "outside_temperature": self._outside_temperature,
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
        system_offset = 28 + self._heating_curve_move
        if self._heating_system == CONF_UNDERFLOOR:
            system_offset = 20 + self._heating_curve_move

        return system_offset + (self._curve_move * (20 - (0.01 * self._outside_temperature ** 2) - (0.8 * self._outside_temperature)))

    def _calculate_control_setpoint(self):
        proportional, integral, derivative = self._pid.components
        _LOGGER.info(f"Calculated pid {self._pid.components}")
        setpoint = self._heating_curve

        if self._pid.auto_mode:
            setpoint += proportional + integral + derivative

        if max(self.climate_differences) >= COLD_TOLERANCE and setpoint < self._heating_curve:
            _LOGGER.info(f"Detected difference higher than {COLD_TOLERANCE}, falling back to heating curve.")
            setpoint = self._heating_curve

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
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        if old_state is None or new_state.state != old_state.state:
            _LOGGER.debug(f"Main Climate State Changed ({new_state.entity_id}).")
            await self._async_control_heating()

    async def _async_control_heating(self, _time=None):
        if self._overshoot_protection_active:
            await self._async_control_overshoot_protection()

            return

        if self._current_temperature is None or self._outside_temperature is None:
            return

        too_cold = self.target_temperature + COLD_TOLERANCE >= self._current_temperature
        too_hot = self.current_temperature >= self._target_temperature + HOT_TOLERANCE
        climates_requires_heat = max(self.climate_differences) >= COLD_TOLERANCE

        # Enable PID Controller when this climate is too cold
        if too_cold:
            self._pid.set_auto_mode(True)

        # Disable PID Controller when this climate is too hot
        if too_hot:
            self._pid.set_auto_mode(False)

        if self._is_device_active:
            if (too_hot and not climates_requires_heat) or not self.valves_open or self.hvac_action == HVACAction.OFF:
                await self._async_control_heater(False)
            elif not self._get_boiler_value(gw_vars.DATA_MASTER_CH_ENABLED):
                await self._async_control_heater(True)

            await self._async_control_setpoint()
        else:
            if (too_cold or climates_requires_heat) and self.valves_open and self.hvac_action != HVACAction.OFF:
                await self._async_control_heater(True)
                await self._async_control_setpoint()
            elif self._get_boiler_value(gw_vars.DATA_MASTER_CH_ENABLED):
                await self._async_control_heater(False)

    async def _async_control_overshoot_protection(self):
        if not self._is_device_active:
            await self._async_control_heater(True)

        await self._async_control_setpoint()

        if return_water_temperature := self._get_boiler_value(gw_vars.DATA_RETURN_WATER_TEMP):
            self._overshoot_protection_data.append(round(return_water_temperature, 1))
            _LOGGER.info(f"[Overshoot Protection] Return Water Temperature Collected: {return_water_temperature:2.1f}")

        if len(self._overshoot_protection_data) < OVERSHOOT_PROTECTION_REQUIRED_DATASET:
            return

        value = mean(self._overshoot_protection_data[-3:])
        difference = abs(round(return_water_temperature, 1) - mean(self._overshoot_protection_data[-3:]))

        if difference < 0.1:
            self._overshoot_protection_active = False
            self._store.store_overshoot_protection_value(round(value, 1))
            _LOGGER.info(f"[Overshoot Protection] Result: {value:2.1f}")

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

            if not self._overshoot_protection_active:
                self._setpoint = round(self._calculate_control_setpoint(), 1)
            else:
                _LOGGER.warning("[Overshoot Protection] Overwritten setpoint to 60 degrees")
                self._setpoint = OVERSHOOT_PROTECTION_SETPOINT
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
            if self.hvac_mode == HVACMode.OFF:
                self._hvac_mode = HVACMode.HEAT

            if self._attr_preset_mode == PRESET_NONE:
                self._saved_target_temperature = self._target_temperature

            self._attr_preset_mode = preset_mode
            await self._async_set_setpoint(self._presets[preset_mode])

    async def _async_set_setpoint(self, temperature: float):
        if self._target_temperature == temperature:
            return

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
