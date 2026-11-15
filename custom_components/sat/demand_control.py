"""Heating demand logic for SAT."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Callable, Optional, Union

from homeassistant.components import sensor, weather
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN, HVACAction, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CoreState, Event, EventStateChangedData, HassJob, HomeAssistant, State, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from .area import Area, Areas, DrivingRoom
from .const import (
    COLD_SETPOINT,
    FROST_PROTECTION_HYSTERESIS_CELSIUS,
    FROST_PROTECTION_SETPOINT,
    HEATING_CONTROL_SCHEDULE_TIMEOUT_SECONDS,
    HEATING_CURVE_SETTLE_SECONDS,
    MINIMUM_SETPOINT,
    SIGNAL_DEMAND_CONTROL_UPDATED,
)
from .coordinator import SatDataUpdateCoordinator
from .efficiency import EfficiencyMonitor
from .entry_data import SatConfig
from .heating_control import HeatingDemand, SatHeatingControl
from .heating_curve import (
    HeatingCurve, HeatingCurveAdvisor, HeatingCurveAdviceSample,
    RECOMMENDATION_MAXIMUM_ERROR_CELSIUS, RECOMMENDATION_MAXIMUM_SUN_ELEVATION, RECOMMENDATION_REQUEST_TOLERANCE_CELSIUS, RECOMMENDATION_STEADY_RANGE_CELSIUS, RECOMMENDATION_STEADY_WINDOW_SECONDS,
)
from .helpers import clamp, ensure_list, event_timestamp, is_state_stale, state_age_seconds, timestamp
from .pid import PID, PID_UPDATE_INTERVAL
from .preheat import PreheatForecast, PreheatMonitor
from .room_balance import BoilerObservation, MAIN_ROOM_ID, RoomBalanceMonitor, RoomObservation, RoomSignals
from .seasonal_curve import SeasonalCurveMonitor
from .solar_gain import SolarGainController, SolarGainSample, SolarGainSignals, SolarGainSnapshot
from .summer_simmer import SummerSimmer
from .temperature.history import TemperatureHistory, TemperatureStatistics
from .temperature.state import TemperatureState
from .types import HeatingMode
from .warm_up import WARM_UP_MINIMUM_ERROR_CELSIUS, WarmUpEstimator
from .wind import WIND_HISTORY_STARTUP_DELAY_SECONDS, WindHistory, WindMonitor, WindReading
from .wind_sensitivity import WIND_SENSITIVITY_UPDATE_INTERVAL, WindSensitivityMonitor

ATTR_WARM_UP_RATE_PER_HOUR = "warm_up_rate_per_hour"

_LOGGER = logging.getLogger(__name__)


class SatDemandControl:
    """Turn the state of the primary room and the rooms into a heating demand."""

    def __init__(self, hass: HomeAssistant, coordinator: SatDataUpdateCoordinator, heating_control: SatHeatingControl, config: SatConfig) -> None:
        self._hass = hass
        self._config = config
        self._coordinator = coordinator
        self._heating_control = heating_control

        self._started: bool = False
        self._id: Optional[str] = None

        self._frost_protection_active: bool = False
        self._target_temperature: Optional[float] = None
        self._hvac_mode: Optional[Union[HVACMode, str]] = None
        self._target_temperature_changed_at: Optional[float] = None
        self._solar_gain_snapshot = SolarGainSnapshot(active=False, rise_per_hour=None, sun_elevation=None)

        self._event_listeners_registered: bool = False
        self._unsubscribers: list[Callable[[], None]] = []
        self._cancel_wind_backfill: Optional[Callable[[], None]] = None
        self._control_heating_loop_scheduled_at: Optional[float] = None
        self._control_heating_loop_unsub: Optional[Callable[[], None]] = None

        # Controllers
        self.areas = Areas.from_config(self._config)
        self.heating_curve = HeatingCurve.from_config(self._config)
        self.pid = PID.from_config(self.heating_curve, self._config)

        # Monitors
        self._wind = WindMonitor(self._config.sensors)
        self._wind_sensitivity = WindSensitivityMonitor()
        self._wind_history = WindHistory(self._config.entry_id, self._config.sensors)
        self._solar_gain = SolarGainController(self._config.solar_gain)
        self._curve_advisor = HeatingCurveAdvisor(self._config.pid.heating_curve_coefficient)
        self._curve_advice_sample: Optional[HeatingCurveAdviceSample] = None
        self._seasonal_curve = SeasonalCurveMonitor(self._config.entry_id)
        self._room_balance = RoomBalanceMonitor(self._config.entry_id, self.areas.ids())
        self._preheat = PreheatMonitor(self._config.entry_id)
        self._efficiency = EfficiencyMonitor(self._config.entry_id, ensure_list(self._config.sensors.outside_sensor_entity_id))
        self._warm_up = WarmUpEstimator()
        self._temperature_history = TemperatureHistory()

        if self._config.simulation.enabled:
            _LOGGER.warning("Simulation mode!")

    @property
    def id(self) -> Optional[str]:
        """Return the id of the primary room."""
        return self._id

    @property
    def hvac_mode(self) -> Optional[HVACMode]:
        """Get the current HVAC mode."""
        if self._hvac_mode in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
            return HVACMode.OFF

        return self._hvac_mode

    @property
    def hvac_action(self) -> HVACAction:
        """Get the current HVAC action."""
        if self._hvac_mode == HVACMode.OFF:
            if self._frost_protection_active and self._coordinator.active:
                return HVACAction.HEATING

            return HVACAction.OFF

        if not self._coordinator.active:
            return HVACAction.IDLE

        return HVACAction.HEATING

    @property
    def target_temperature(self) -> Optional[float]:
        """Return the temperature the primary room tries to reach."""
        return self._target_temperature

    @property
    def target_temperature_changed_at(self) -> Optional[float]:
        return self._target_temperature_changed_at

    @property
    def current_temperature(self) -> Optional[float]:
        """Return the temperature of the primary room, or the median room temperature in rooms-only mode."""
        if self._config.rooms_only:
            return self.median_room_temperature

        if (current_temperature := self._get_entity_state_float(self._config.sensors.inside_sensor_entity_id)) is None:
            return None

        if self._config.presets.thermal_comfort:
            return SummerSimmer.index(current_temperature, self.current_humidity)

        return current_temperature

    @property
    def room_temperatures(self) -> dict[str, Optional[float]]:
        """Return the temperature of every room."""
        return {area.id: area.room_temperature for area in self.areas.items()}

    @property
    def room_errors(self) -> dict[str, Optional[float]]:
        """Return how far every room is below its target."""
        return {area.id: temperature_state.error if (temperature_state := area.temperature_state) is not None else None for area in self.areas.items()}

    @property
    def median_room_temperature(self) -> Optional[float]:
        temperatures = [temperature for temperature in self.room_temperatures.values() if temperature is not None]

        return round(median(temperatures), 2) if temperatures else None

    @property
    def coldest_room_temperature(self) -> Optional[float]:
        temperatures = [temperature for temperature in self.room_temperatures.values() if temperature is not None]

        return min(temperatures) if temperatures else None

    @property
    def current_humidity(self) -> Optional[float]:
        """Return the humidity of the primary room."""
        if self._config.sensors.humidity_sensor_entity_id is None:
            return None

        return self._get_entity_state_float(self._config.sensors.humidity_sensor_entity_id)

    @property
    def temperature_statistics(self) -> TemperatureStatistics:
        """Return temperature error statistics."""
        return self._temperature_history.statistics

    @property
    def curve_advisor(self) -> HeatingCurveAdvisor:
        """Return the heating curve advisor."""
        return self._curve_advisor

    @property
    def wind_sensitivity(self) -> WindSensitivityMonitor:
        """Return the wind sensitivity monitor."""
        return self._wind_sensitivity

    @property
    def efficiency(self) -> EfficiencyMonitor:
        """Return the efficiency monitor."""
        return self._efficiency

    @property
    def preheat(self) -> PreheatMonitor:
        """Return the preheat monitor."""
        return self._preheat

    @property
    def room_balance(self) -> RoomBalanceMonitor:
        """Return the per-room balance monitor."""
        return self._room_balance

    @property
    def seasonal_curve(self) -> SeasonalCurveMonitor:
        """Return the seasonal heating curve monitor."""
        return self._seasonal_curve

    @property
    def warm_up(self) -> WarmUpEstimator:
        """Return the warm-up rate estimator."""
        return self._warm_up

    @property
    def solar_gain_snapshot(self) -> SolarGainSnapshot:
        """Return the latest solar gain assessment."""
        return self._solar_gain_snapshot

    @property
    def frost_protection_active(self) -> bool:
        return self._frost_protection_active

    @property
    def implied_curve_coefficient(self) -> Optional[float]:
        """Return the coefficient the house currently needs, while the system is in steady heating operation."""
        return self._curve_advisor.implied_coefficient(self._curve_advice_sample)

    @property
    def temperature_state(self) -> Optional[TemperatureState]:
        """Return the temperature state of the primary room."""
        if self._config.sensors.inside_sensor_entity_id is None:
            return None

        if (state := self._hass.states.get(self._config.sensors.inside_sensor_entity_id)) is None:
            return None

        target_temperature = self.target_temperature
        current_temperature = self.current_temperature
        if target_temperature is None or current_temperature is None:
            return None

        return TemperatureState(
            entity_id=self._id or MAIN_ROOM_ID,
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
            state = self._hass.states.get(entity_id)
            if state is None:
                continue

            sensor_max_value_age = self._config.sensors.sensor_max_value_age_seconds

            if is_state_stale(state, sensor_max_value_age):
                _LOGGER.debug("Outside sensor %s stale for %s (age=%.1fs > %.1fs)", entity_id, self._id, state_age_seconds(state), sensor_max_value_age)
                continue

            if state.state in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
                continue

            if sensor.DOMAIN in entity_id:
                return float(state.state)

            if weather.DOMAIN in entity_id:
                return float(state.attributes.get("temperature"))

        return None

    @property
    def current_wind(self) -> Optional[WindReading]:
        """Return the wind speed averaged over the last hour"""
        return self._wind.reading

    @property
    def requested_setpoint(self) -> float:
        """Get the requested setpoint based on the heating curve and PIDs."""
        if self._config.rooms_only:
            setpoint = self.areas.pids.output
        else:
            if self.heating_curve.value is None:
                return MINIMUM_SETPOINT

            setpoint = self.pid.output

            # ECO: only follow the primary PID.
            if self._config.presets.heating_mode == HeatingMode.ECO:
                return clamp(round(setpoint, 1), MINIMUM_SETPOINT, self._coordinator.maximum_setpoint)

            # Secondary rooms: heating information.
            if (secondary_heating := self.areas.pids.output) is not None:
                setpoint = max(setpoint, secondary_heating)

        # Rooms that are overshooting cap the request.
        overshoot_cap = self.areas.pids.overshoot_cap

        if overshoot_cap is not None:
            setpoint = min(setpoint, overshoot_cap)

        if self._solar_gain_snapshot.active and self._config.solar_gain.enabled:
            setpoint -= self._config.solar_gain.setpoint_offset_celsius

        return clamp(setpoint, MINIMUM_SETPOINT, self._coordinator.maximum_setpoint)

    @property
    def median_error(self) -> Optional[float]:
        errors: list[float] = []

        if (primary_temperature := self.temperature_state) is not None:
            errors.append(primary_temperature.error)

        for area in self.areas.items():
            if (temperature := area.temperature_state) is None:
                continue

            errors.append(temperature.error)

        if not errors:
            return None

        return median(errors)

    @property
    def valves_open(self) -> bool:
        """Determine if any of the controlled climates have open valves."""
        # Get the list of all controlled climates
        climates = self._config.radiators + self.areas.ids()

        # Without radiators only rooms-only mode can detect a closed valve
        if len(self._config.radiators) == 0:
            if self._config.rooms_only:
                return any(area.is_calling for area in self.areas.items())

            return True

        # Iterate through each controlled thermostat
        for entity_id in climates:
            # Get the current state of the thermostat
            state = self._hass.states.get(entity_id)

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

    def restore(self, old_state: Optional[State], fallback_target_temperature: float) -> None:
        """Restore the demand state from the persisted climate state, or fall back to defaults."""
        if old_state is not None:
            self._warm_up.restore(old_state.attributes.get(ATTR_WARM_UP_RATE_PER_HOUR))

            if self._target_temperature is None and not self._config.rooms_only:
                if old_state.attributes.get(ATTR_TEMPERATURE) is None:
                    self.pid.control_setpoint = fallback_target_temperature
                    self._target_temperature = fallback_target_temperature
                    _LOGGER.warning("Undefined target temperature, falling back to %s", self._target_temperature)
                else:
                    self._target_temperature = float(old_state.attributes[ATTR_TEMPERATURE])

            if old_state.state:
                self._hvac_mode = old_state.state

            return

        if self._target_temperature is None and not self._config.rooms_only:
            self.pid.control_setpoint = fallback_target_temperature
            self._target_temperature = fallback_target_temperature
            _LOGGER.warning("No previously saved temperature, setting to %s", self._target_temperature)

        if not self._hvac_mode:
            self._hvac_mode = HVACMode.OFF

    async def async_added_to_hass(self, entity_id: str) -> None:
        """Start controlling, with the given id for the primary room."""
        self._id = entity_id
        self._started = True

        await self._heating_control.async_added_to_hass()
        await self.pid.async_added_to_hass(self._hass, entity_id, self._coordinator.id)
        await self._curve_advisor.async_added_to_hass(self._hass, self._config.entry_id)
        await self._wind_sensitivity.async_added_to_hass(self._hass, self._config.entry_id)
        self._cancel_wind_backfill = async_call_later(self._hass, WIND_HISTORY_STARTUP_DELAY_SECONDS, self._async_backfill_curve_advisor_wind)
        await self._seasonal_curve.async_added_to_hass(self._hass)
        await self._room_balance.async_added_to_hass(self._hass)
        await self._preheat.async_added_to_hass(self._hass)
        await self._efficiency.async_added_to_hass(self._hass)
        await self.areas.async_added_to_hass(self._hass, self._coordinator.id)

        if self._hass.state is CoreState.running:
            self._update_heating_curves()
            self._register_event_listeners()

            self.control_pid()
            self.areas.control_pid()
            self.schedule_heating_control_loop()
        else:
            self._hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, lambda _: self._update_heating_curves())
            self._hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, lambda _: self._register_event_listeners())

            self._hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, lambda _: self.control_pid())
            self._hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, lambda _: self.areas.control_pid())
            self._hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, lambda _: self.schedule_heating_control_loop())

        self._target_temperature_changed_at = timestamp()

        self._unsubscribers.append(
            self._hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self.async_will_remove_from_hass)
        )

    async def async_will_remove_from_hass(self, _event: Optional[Event] = None) -> None:
        """Stop controlling and persist what the monitors learned."""
        if not self._started:
            return

        self._started = False
        self._event_listeners_registered = False
        self._cancel_scheduled_heating_control_loop()

        for unsubscribe in self._unsubscribers:
            unsubscribe()

        self._unsubscribers.clear()

        if self._cancel_wind_backfill is not None:
            self._cancel_wind_backfill()
            self._cancel_wind_backfill = None

        await self._heating_control.async_will_remove_from_hass()
        await self._seasonal_curve.async_will_remove_from_hass()
        await self._efficiency.async_will_remove_from_hass()
        await self._room_balance.async_save_data()
        await self._preheat.async_save_data()
        await self._coordinator.async_will_remove_from_hass()

        for area in self.areas.items():
            await area.async_will_remove_from_hass()

    @callback
    def async_update_listeners(self) -> None:
        """Tell the entities that read from this control that something changed."""
        async_dispatcher_send(self._hass, SIGNAL_DEMAND_CONTROL_UPDATED, self._coordinator.id)

    def reset_control_state(self) -> None:
        """Reset control state when major changes occur."""
        self.pid.reset()
        self.areas.pids.reset()

    @callback
    def set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Switch heating on or off."""
        self._hvac_mode = hvac_mode

        self.reset_control_state()
        self.schedule_heating_control_loop()
        self.async_update_listeners()

    @callback
    def set_target_temperature(self, temperature: float) -> bool:
        """Set the target temperature of the primary room, returning whether it changed."""
        if self._target_temperature == temperature:
            return False

        self._target_temperature = temperature
        self._target_temperature_changed_at = timestamp()

        self.reset_control_state()
        self._update_heating_curves()
        self.schedule_heating_control_loop()
        self.async_update_listeners()

        return True

    async def async_estimate_preheat(self, target_temperature: float, ready_time: Optional[datetime] = None, room: Optional[str] = None) -> dict[str, Any]:
        """Estimate how long before the ready time heating has to start."""
        room_id, current_temperature = MAIN_ROOM_ID, self.current_temperature

        if room:
            if (area := self.areas.get(room)) is None:
                raise ServiceValidationError(f"{room} is not configured as a room of {self._id}")

            room_id, current_temperature = room, area.current_temperature

        if ready_time is not None and ready_time.tzinfo is None:
            ready_time = ready_time.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)

        forecast_temperature = None
        if ready_time is not None:
            forecast_temperature = await PreheatForecast.async_temperature(self._hass, ensure_list(self._config.sensors.outside_sensor_entity_id), ready_time)

        outside_temperature = forecast_temperature if forecast_temperature is not None else self.current_outside_temperature
        rate = self._preheat.rate(room_id, outside_temperature)

        lead_minutes = None
        if rate is not None and current_temperature is not None:
            lead_minutes = rate.lead_minutes(target_temperature - current_temperature)

        start_time = None
        if lead_minutes is not None and ready_time is not None:
            start_time = (ready_time - timedelta(minutes=lead_minutes)).isoformat()

        return {
            "start_time": start_time,
            "lead_minutes": lead_minutes,
            "outside_temperature": outside_temperature,
            "forecast_used": forecast_temperature is not None,
            "episodes": len(self._preheat.episodes(room_id)),
            "rate_per_hour": rate.rate_per_hour if rate is not None else None,
        }

    def control_pid(self, _time: Optional[datetime] = None) -> None:
        """Control the PID controller of the primary room and feed the monitors."""
        self._curve_advice_sample = None
        temperature_state = self.temperature_state

        if not self._config.rooms_only:
            sensor_max_value_age = self._config.sensors.sensor_max_value_age_seconds

            if sensor_max_value_age > 0:
                state = self._hass.states.get(self._config.sensors.inside_sensor_entity_id)
                if is_state_stale(state, sensor_max_value_age):
                    _LOGGER.debug(
                        "Resetting PID for %s due to stale sensor %s (age=%.1fs > %.1fs)",
                        self._id, self._config.sensors.inside_sensor_entity_id, state_age_seconds(state), sensor_max_value_age
                    )
                    self.pid.reset()
                    return

            if temperature_state is None:
                _LOGGER.debug("Skipping control loop for %s because temperature is not available.", self._id)
                return

        now = event_timestamp(_time)
        self._wind.observe(self._hass, now)

        if temperature_state is not None:
            self._solar_gain_snapshot = self._solar_gain.update(
                SolarGainSignals(
                    sample=SolarGainSample(temperature=temperature_state.current, timestamp=now),
                    valves_open=self.valves_open,
                    sun_elevation=self._sun_elevation(),
                    flame_active=self._coordinator.flame_active,
                    is_heating_mode=self.hvac_mode == HVACMode.HEAT,
                    relative_modulation=self._coordinator.relative_modulation_value,
                )
            )

        if self.hvac_mode == HVACMode.HEAT and self.valves_open and (median_error := self.median_error) is not None:
            self._temperature_history.record(median_error, now)

        if temperature_state is not None:
            self.pid.update(
                temperature_state,
                freeze_integral=self._solar_gain_snapshot.active and self._config.solar_gain.freeze_integral,
            )

        self._record_curve_advice_sample(now, temperature_state)
        self._record_room_observations(now, temperature_state)

        if temperature_state is not None:
            self._warm_up.update(
                active=(
                        self.hvac_mode == HVACMode.HEAT
                        and self.valves_open
                        and self._coordinator.flame_active
                        and not self._solar_gain_snapshot.active
                        and temperature_state.error > WARM_UP_MINIMUM_ERROR_CELSIUS
                ),
                temperature=temperature_state.current,
                timestamp=now,
            )

    def _update_frost_protection(self) -> bool:
        """Track whether the primary room, or the coldest room in rooms-only mode, is cold enough to require heating while off."""
        threshold = self._config.frost_protection_temperature
        current_temperature = self.coldest_room_temperature if self._config.rooms_only else self.current_temperature

        if threshold <= 0 or current_temperature is None:
            self._frost_protection_active = False
        elif current_temperature <= threshold:
            self._frost_protection_active = True
        elif current_temperature >= threshold + FROST_PROTECTION_HYSTERESIS_CELSIUS:
            self._frost_protection_active = False

        return self._frost_protection_active

    async def _async_backfill_curve_advisor_wind(self, _now: Optional[datetime] = None) -> None:
        """Fill in the wind speed of curve advisor samples recorded without one from the recorder."""
        self._cancel_wind_backfill = None

        if (start := self._curve_advisor.first_timestamp_without_wind) is not None:
            if filled := self._curve_advisor.backfill_wind(await self._wind_history.async_hourly(self._hass, start)):
                _LOGGER.debug("Filled in the wind speed of %d heating curve advisor samples for %s.", filled, self._id)

        self._update_wind_sensitivity()

    @callback
    def _update_wind_sensitivity(self, _now: Optional[datetime] = None) -> None:
        """Hand the advisor days with wind to the wind sensitivity monitor."""
        self._wind_sensitivity.update(self._curve_advisor.advice.wind_days, dt_util.now().date())

    def _record_curve_advice_sample(self, now: float, temperature_state: Optional[TemperatureState]) -> None:
        """Feed the heating curve advisor when the system is in steady heating operation."""
        self._curve_advice_sample = self._steady_curve_advice_sample(now, temperature_state)

        if self._curve_advice_sample is not None:
            self._curve_advisor.record(self._curve_advice_sample)

    def _record_room_observations(self, now: float, temperature_state: Optional[TemperatureState]) -> None:
        """Feed the monitors with the current state of the primary room and every room."""
        boiler = BoilerObservation(
            eligible=(
                    self.hvac_mode == HVACMode.HEAT
                    and self.valves_open
                    and not self._solar_gain_snapshot.active
                    and not self._coordinator.hot_water_active
            ),
            heating=self.hvac_action == HVACAction.HEATING,
            flame_active=self._coordinator.flame_active,
            flow_temperature=self._coordinator.boiler_temperature,
            outside_temperature=self.current_outside_temperature,
            sun_below_horizon=(sun_elevation := self._sun_elevation()) is not None and sun_elevation < 0,
        )

        rooms = [self._room_observation(area, now) for area in self.areas.items()]

        if temperature_state is not None:
            rooms.insert(0, RoomObservation(
                room_id=MAIN_ROOM_ID,
                shape=self.heating_curve.shape,
                shortfall=temperature_state.error,
                temperature=temperature_state.current,
                calling=self.hvac_action == HVACAction.HEATING,
                implied_coefficient=self._implied_room_coefficient(self.heating_curve, self.pid, now),
                steady=self._temperature_history.is_steady(RECOMMENDATION_STEADY_WINDOW_SECONDS, RECOMMENDATION_STEADY_RANGE_CELSIUS),
            ))

        self._preheat.observe(boiler, rooms, now)
        self._room_balance.observe(boiler, rooms, now)

    def _room_observation(self, area: Area, now: float) -> RoomObservation:
        temperature_state = area.temperature_state

        return RoomObservation(
            room_id=area.id,
            steady=area.is_steady,
            calling=area.is_calling,
            shape=area.heating_curve.shape,
            valve_position=area.valve_position,
            signals=RoomSignals.read(self._hass, area.id),
            shortfall=temperature_state.error if temperature_state is not None else None,
            temperature=temperature_state.current if temperature_state is not None else None,
            implied_coefficient=self._implied_room_coefficient(area.heating_curve, area.pid, now),
        )

    def _implied_room_coefficient(self, heating_curve: HeatingCurve, pid: PID, now: float) -> Optional[float]:
        if heating_curve.shape is None or not pid.available:
            return None

        return self._curve_advisor.implied_coefficient(HeatingCurveAdviceSample(
            shape=heating_curve.shape,
            timestamp=now,
            correction=pid.proportional + pid.integral,
        ))

    def _steady_curve_advice_sample(self, now: float, temperature_state: Optional[TemperatureState]) -> Optional[HeatingCurveAdviceSample]:
        if self.hvac_mode != HVACMode.HEAT or not self.valves_open:
            return None

        if self._solar_gain_snapshot.active or self._coordinator.hot_water_active:
            return None

        if (room := self._driving_room(now, temperature_state)) is None:
            return None

        if (sun_elevation := self._sun_elevation()) is not None and sun_elevation >= RECOMMENDATION_MAXIMUM_SUN_ELEVATION:
            return None

        if abs(room.error) > RECOMMENDATION_MAXIMUM_ERROR_CELSIUS:
            return None

        if not room.temperature_history.is_steady(RECOMMENDATION_STEADY_WINDOW_SECONDS, RECOMMENDATION_STEADY_RANGE_CELSIUS):
            return None

        if (median_error := room.temperature_history.median_error(RECOMMENDATION_STEADY_WINDOW_SECONDS)) is None or abs(median_error) > RECOMMENDATION_MAXIMUM_ERROR_CELSIUS:
            return None

        if room.target_temperature_changed_at is None or (now - room.target_temperature_changed_at) < HEATING_CURVE_SETTLE_SECONDS:
            return None

        if (shape := room.heating_curve.shape) is None or (curve_value := room.heating_curve.value) is None:
            return None

        return HeatingCurveAdviceSample(
            shape=shape,
            timestamp=now,
            wind=self.current_wind,
            correction=room.pid.output - curve_value,
        )

    def _driving_room(self, now: float, temperature_state: Optional[TemperatureState]) -> Optional[DrivingRoom]:
        """Return the room whose request the boiler is heating on, the only demand the curve can be judged on."""
        requested_setpoint = self.requested_setpoint

        if requested_setpoint <= COLD_SETPOINT or not self._heating_control.burned_within_pwm_period(now):
            return None

        if temperature_state is not None and abs(requested_setpoint - self.pid.output) <= RECOMMENDATION_REQUEST_TOLERANCE_CELSIUS:
            return DrivingRoom(
                pid=self.pid,
                error=temperature_state.error,
                heating_curve=self.heating_curve,
                temperature_history=self._temperature_history,
                target_temperature_changed_at=self._target_temperature_changed_at,
            )

        for area in self.areas.items():
            if not area.pid.available or not area.requires_heat or abs(requested_setpoint - area.pid.output) > RECOMMENDATION_REQUEST_TOLERANCE_CELSIUS:
                continue

            if (signals := RoomSignals.read(self._hass, area.id)).window_open or signals.valve_alarm:
                return None

            return area.driving_room

        return None

    def _sun_elevation(self) -> Optional[float]:
        sun_state = self._hass.states.get("sun.sun")
        if sun_state is None:
            return None

        elevation = sun_state.attributes.get("elevation")
        if elevation is None:
            return None

        return float(elevation)

    @callback
    def schedule_heating_control_loop(self, _time: Optional[datetime] = None, force: bool = False) -> None:
        """Schedule a debounced execution of the heating control loop."""
        if force or self._scheduled_heating_control_loop_is_overdue():
            self._cancel_scheduled_heating_control_loop()
            self._hass.async_create_task(self.async_control_heating_loop())
            return

        if self._control_heating_loop_unsub is not None:
            return

        self._control_heating_loop_scheduled_at = timestamp()
        self._control_heating_loop_unsub = async_call_later(self._hass, 1, HassJob(self.async_control_heating_loop))

    def _scheduled_heating_control_loop_is_overdue(self) -> bool:
        """A scheduled run that never started would otherwise block every later run."""
        if self._control_heating_loop_unsub is None or self._control_heating_loop_scheduled_at is None:
            return False

        if (age := timestamp() - self._control_heating_loop_scheduled_at) < HEATING_CONTROL_SCHEDULE_TIMEOUT_SECONDS:
            return False

        _LOGGER.warning("Heating control loop for %s did not run %.0fs after being scheduled; running it now.", self._id, age)
        return True

    def _cancel_scheduled_heating_control_loop(self) -> None:
        self._control_heating_loop_scheduled_at = None

        if self._control_heating_loop_unsub is None:
            return

        self._control_heating_loop_unsub()
        self._control_heating_loop_unsub = None

    async def async_control_heating_loop(self, time: Optional[datetime] = None) -> None:
        """Control the heating based on current temperature, target temperature, and outside temperature."""
        self._control_heating_loop_unsub = None
        self._control_heating_loop_scheduled_at = None

        if self._hvac_mode == HVACMode.OFF:
            if self._update_frost_protection():
                await self._heating_control.update(
                    HeatingDemand(
                        valves_open=True,
                        hvac_mode=HVACMode.HEAT,
                        timestamp=event_timestamp(time),
                        requested_setpoint=max(self._coordinator.minimum_setpoint, FROST_PROTECTION_SETPOINT),
                        outside_temperature=self.current_outside_temperature,
                    )
                )
                return

            await self._heating_control.update(
                HeatingDemand(
                    hvac_mode=HVACMode.OFF,
                    valves_open=self.valves_open,
                    timestamp=event_timestamp(time),
                    requested_setpoint=MINIMUM_SETPOINT,
                    outside_temperature=self.current_outside_temperature,
                )
            )
            return

        if self.current_outside_temperature is None:
            return

        await self._heating_control.update(
            HeatingDemand(
                hvac_mode=self.hvac_mode,
                valves_open=self.valves_open,
                timestamp=event_timestamp(time),
                requested_setpoint=self.requested_setpoint,
                outside_temperature=self.current_outside_temperature,
                target_temperature_changed_at=self._target_temperature_changed_at,
            )
        )

    def _get_entity_state_float(self, entity_id: Optional[str]) -> Optional[float]:
        """Return state if available and valid."""
        if entity_id is None:
            return None

        if (entity := self._hass.states.get(entity_id)) is None:
            return None

        sensor_max_value_age = self._config.sensors.sensor_max_value_age_seconds

        if is_state_stale(entity, sensor_max_value_age):
            _LOGGER.debug("Sensor %s stale for %s (age=%.1fs > %.1fs)", entity_id, self._id, state_age_seconds(entity), sensor_max_value_age)
            return None

        if entity.state in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
            return None

        value = entity.attributes.get("current_temperature") if entity.domain == CLIMATE_DOMAIN else entity.state

        try:
            return float(value)
        except (TypeError, ValueError):
            _LOGGER.warning("Sensor %s for %s has a non-numeric value %r", entity_id, self._id, value)
            return None

    def _update_heating_curves(self) -> None:
        """Update the heating curves based on the current outside temperature."""
        target_temperature = self.target_temperature
        outside_temperature = self.current_outside_temperature

        if outside_temperature is None:
            return

        self.areas.heating_curves.update(outside_temperature)

        if target_temperature is not None:
            self.heating_curve.update(target_temperature, outside_temperature)

    def _register_event_listeners(self) -> None:
        """Register event listeners."""
        if self._event_listeners_registered:
            return

        self._event_listeners_registered = True

        self._unsubscribers.append(
            async_track_time_interval(
                self._hass, self.schedule_heating_control_loop, timedelta(seconds=5)
            )
        )

        self._unsubscribers.append(
            async_track_time_interval(
                self._hass, self.control_pid, timedelta(seconds=PID_UPDATE_INTERVAL), cancel_on_shutdown=True
            )
        )

        self._unsubscribers.append(
            async_track_time_interval(
                self._hass, self._update_wind_sensitivity, WIND_SENSITIVITY_UPDATE_INTERVAL, cancel_on_shutdown=True
            )
        )

        if self._config.sensors.inside_sensor_entity_id is not None:
            self._unsubscribers.append(
                async_track_state_change_event(
                    self._hass, [self._config.sensors.inside_sensor_entity_id], self._async_entity_changed
                )
            )

        self._unsubscribers.append(
            async_track_state_change_event(
                self._hass, ensure_list(self._config.sensors.outside_sensor_entity_id), self._async_entity_changed
            )
        )

        if self._config.thermostat is not None:
            self._unsubscribers.append(
                async_track_state_change_event(
                    self._hass, self._config.thermostat, self._async_thermostat_changed
                )
            )

        self._unsubscribers.append(
            async_track_state_change_event(
                self._hass, self.areas.ids(), self._async_climate_changed
            )
        )

    async def _async_thermostat_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle changes to the connected thermostat."""
        old_state = event.data.get("old_state")
        if old_state is None or old_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        if old_state.state != new_state.state:
            _LOGGER.debug("Thermostat state changed.")
            self.pid.reset()
            self.control_pid()
            self.schedule_heating_control_loop()

        if (new_temperature := new_state.attributes.get("temperature")) is None:
            return

        if old_state.attributes.get("temperature") != new_temperature:
            _LOGGER.debug("Thermostat temperature changed.")
            self.set_target_temperature(new_temperature)

    async def _async_entity_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle changes to an entity."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        self._update_heating_curves()
        self.async_update_listeners()

    async def _async_climate_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle changes to a room climate entity."""
        new_state = event.data.get("new_state")
        if not new_state:
            return

        old_state = event.data.get("old_state")
        if old_state is not None and old_state.state != new_state.state:
            _LOGGER.debug(f"Climate state changed for {new_state.entity_id}.")
            self.areas.pids.reset(new_state.entity_id)
            self.schedule_heating_control_loop()

        if (target_temperature := new_state.attributes.get(ATTR_TEMPERATURE)) is None:
            return

        if old_state is not None and old_state.attributes.get(ATTR_TEMPERATURE) == target_temperature:
            return

        self.areas.pids.reset(new_state.entity_id)
        self._update_heating_curves()
        self.async_update_listeners()
