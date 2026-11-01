from __future__ import annotations

import asyncio
from typing import Optional, Mapping, Any

from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfTemperature, UnitOfVolume, UnitOfVolumeFlowRate, EntityCategory
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .climate import SatClimate
from .const import *
from .coordinator import SatDataUpdateCoordinator
from .coordinator.serial import sensor as serial_sensor
from .coordinator.simulator import sensor as simulator_sensor
from .entity import SatClimateEntity, SatEntity, SatHeatingControlEntity
from .entry_data import SatConfig, SatMode, get_entry_data
from .heating_control import SatHeatingControl
from .heating_curve import RECOMMENDATION_MINIMUM_DAYS, RECOMMENDATION_MINIMUM_SAMPLES, RECOMMENDATION_WINDOW_SECONDS
from .helpers import timestamp
from .types import BoilerStatus, CycleClassification, HeatingCurveRecommendation, RoomBalanceStatus

ROOM_BALANCE_ISSUES: dict[RoomBalanceStatus, str] = {
    RoomBalanceStatus.ROOM_FAULT: "room_fault",
    RoomBalanceStatus.UNDERSUPPLIED: "room_undersupplied",
    RoomBalanceStatus.SENSOR_MISMATCH: "room_sensor_mismatch",
}


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Add SAT sensors based on the configured integration mode."""
    if config_entry.entry_id not in hass.data.get(DOMAIN, {}):
        return

    entry_data = get_entry_data(hass, config_entry.entry_id)
    if entry_data.climate is None:
        try:
            await asyncio.wait_for(entry_data.climate_ready.wait(), timeout=10)
        except asyncio.TimeoutError:
            return

        if entry_data.climate is None:
            return

    if entry_data.config.mode == SatMode.SERIAL:
        await serial_sensor.async_setup_entry(hass, config_entry, async_add_entities)

    if entry_data.config.mode == SatMode.SIMULATOR:
        await simulator_sensor.async_setup_entry(hass, config_entry, async_add_entities)

    sensors: list[SensorEntity] = [
        SatPidSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.climate),
        SatCycleSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control),
        SatDeviceSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control),
        SatManufacturerSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control),
        SatErrorValueSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.climate),
        SatRequestedSetpoint(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.climate),
        SatOutsideTemperatureSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.climate),
        SatImpliedCurveCoefficientSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.climate),
        SatSeasonalCurveCoefficientSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.climate),
        SatHeatingCurveSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.climate),
        SatHeatingCurveRecommendationSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.climate),
    ]

    for entity_id in entry_data.config.rooms:
        sensors.append(SatPidSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.climate, entity_id))
        sensors.append(SatRoomBalanceSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.climate, entity_id))

    if entry_data.coordinator.supports_relative_modulation_management:
        sensors.append(SatCurrentPowerSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control))

        minimum_consumption = entry_data.config.limits.minimum_consumption
        maximum_consumption = entry_data.config.limits.maximum_consumption

        if minimum_consumption > 0 and maximum_consumption > 0:
            sensors.append(SatCurrentConsumptionSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control))
            sensors.append(SatTotalConsumptionSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control))

    async_add_entities(sensors)


class SatRequestedSetpoint(SatClimateEntity, SensorEntity):
    _attr_translation_key = "requested_setpoint"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_added_to_hass(self) -> None:
        def on_pid_updated(_) -> None:
            self.schedule_update_ha_state()

        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_PID_UPDATED, on_pid_updated))

    @property
    def name(self) -> str:
        return "Requested Setpoint"

    @property
    def device_class(self) -> SensorDeviceClass:
        return SensorDeviceClass.TEMPERATURE

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return UnitOfTemperature.CELSIUS

    @property
    def native_value(self) -> Optional[float]:
        return self._climate.requested_setpoint

    @property
    def unique_id(self) -> str:
        return f"{self._config.entry_id}-requested-setpoint"


class SatOutsideTemperatureSensor(SatClimateEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_added_to_hass(self) -> None:
        def on_pid_updated(_) -> None:
            self.schedule_update_ha_state()

        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_PID_UPDATED, on_pid_updated))

    @property
    def name(self) -> str:
        return "Outside Temperature"

    @property
    def device_class(self) -> SensorDeviceClass:
        return SensorDeviceClass.TEMPERATURE

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return UnitOfTemperature.CELSIUS

    @property
    def native_value(self) -> Optional[float]:
        if (temperature := self._climate.current_outside_temperature) is None:
            return None

        return round(temperature, 1)

    @property
    def unique_id(self) -> str:
        return f"{self._config.entry_id}-outside-temperature"


class SatImpliedCurveCoefficientSensor(SatClimateEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_added_to_hass(self) -> None:
        def on_pid_updated(entity_id: str) -> None:
            if entity_id != self._climate.entity_id:
                return

            self.schedule_update_ha_state()

        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_PID_UPDATED, on_pid_updated))

    @property
    def name(self) -> str:
        return "Implied Curve Coefficient"

    @property
    def native_value(self) -> Optional[float]:
        return self._climate.implied_curve_coefficient

    @property
    def unique_id(self) -> str:
        return f"{self._config.entry_id}-implied-curve-coefficient"


class SatSeasonalCurveCoefficientSensor(SatClimateEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def name(self) -> str:
        return "Seasonal Curve Coefficient"

    @property
    def native_value(self) -> Optional[float]:
        return self._climate.seasonal_curve.fit.coefficient

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        fit = self._climate.seasonal_curve.fit

        return {
            "days": fit.days,
            "hours": fit.hours,
            "cold_coefficient": fit.cold_coefficient,
            "mild_coefficient": fit.mild_coefficient,
            "curve_mismatch": fit.is_mismatch,
            "bins": [{"minimum_temperature": item.minimum_temperature, "coefficient": item.coefficient, "hours": item.hours} for item in fit.bins],
        }

    @property
    def unique_id(self) -> str:
        return f"{self._config.entry_id}-seasonal-curve-coefficient"


class SatPidSensor(SatClimateEntity, SensorEntity):
    _attr_translation_key = "pid"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
            self,
            coordinator: SatDataUpdateCoordinator,
            config: SatConfig,
            heating_control: SatHeatingControl,
            climate: SatClimate,
            area_id: Optional[str] = None,
    ):
        super().__init__(coordinator, config, heating_control, climate)
        self._area_id: Optional[str] = area_id

    async def async_added_to_hass(self) -> None:
        def on_pid_updated(entity_id: str) -> None:
            if entity_id != self._signal_entity_id:
                return

            self.schedule_update_ha_state()

        await super().async_added_to_hass()
        self._signal_entity_id = self._area_id or self._climate.entity_id
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_PID_UPDATED, on_pid_updated))

    @property
    def _pid(self):
        if self._area_id is None:
            return self._climate.pid

        if (area := self._climate.areas.get(self._area_id)) is None:
            return None

        return area.pid

    @property
    def name(self) -> str:
        if self._area_id is None:
            return f"PID"

        return f"PID ({self._area_id})"

    @property
    def device_class(self) -> SensorDeviceClass:
        return SensorDeviceClass.TEMPERATURE

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return UnitOfTemperature.CELSIUS

    @property
    def available(self):
        if (pid := self._pid) is None:
            return False

        return pid.available

    @property
    def native_value(self) -> Optional[float]:
        if (pid := self._pid) is None:
            return None

        return pid.output

    @property
    def unique_id(self) -> str:
        if self._area_id is None:
            return f"{self._config.entry_id}-pid"

        return f"{self._config.entry_id}-{self._area_id}-pid"

    @property
    def extra_state_attributes(self) -> Optional[Mapping[str, Any]]:
        if (pid := self._pid) is None:
            return None

        return {
            "error": pid.last_error,
            "proportional": pid.proportional,
            "integral": pid.integral,
            "derivative": pid.derivative,
        }


class SatRoomBalanceSensor(SatClimateEntity, SensorEntity):
    _attr_translation_key = "room_balance"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_options = [status.value for status in RoomBalanceStatus]

    def __init__(
            self,
            coordinator: SatDataUpdateCoordinator,
            config: SatConfig,
            heating_control: SatHeatingControl,
            climate: SatClimate,
            area_id: str,
    ):
        super().__init__(coordinator, config, heating_control, climate)
        self._area_id: str = area_id

    @property
    def name(self) -> str:
        return f"Room Balance ({self._area_id})"

    @property
    def native_value(self) -> str:
        return self._climate.room_balance.balance(self._area_id).status.value

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        balance = self._climate.room_balance.balance(self._area_id)

        return {
            "days": balance.days,
            "shortfall": balance.shortfall,
            "flow_premium": balance.flow_premium,
            "calling_fraction": balance.calling_fraction,
            "implied_coefficient": balance.implied_coefficient,
            "reference_coefficient": balance.reference_coefficient,

            "warm_up_episodes": balance.warm_up_episodes,
            "warm_up_response": balance.warm_up_response,
            "reference_warm_up_response": balance.reference_warm_up_response,

            "valve_position": balance.valve_position,
            "radiator_heat_rise": balance.heat_rise,
        }

    @property
    def unique_id(self) -> str:
        return f"{self._config.entry_id}-{self._area_id}-room-balance"

    async def async_will_remove_from_hass(self) -> None:
        for key in ROOM_BALANCE_ISSUES.values():
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(key))

        await super().async_will_remove_from_hass()

    @callback
    def _handle_coordinator_update(self) -> None:
        balance = self._climate.room_balance.balance(self._area_id)
        room_state = self.hass.states.get(self._area_id)

        for status, key in ROOM_BALANCE_ISSUES.items():
            if balance.status != status:
                ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(key))
                continue

            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._issue_id(key),
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=key,
                translation_placeholders={
                    "name": self._config.name,
                    "room": room_state.name if room_state is not None else self._area_id,
                    "flow_premium": f"{balance.flow_premium or 0.0:.1f}",
                },
            )

        super()._handle_coordinator_update()

    def _issue_id(self, key: str) -> str:
        return f"{key}_{self._config.entry_id}_{self._area_id}"


class SatCurrentPowerSensor(SatEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def name(self) -> str:
        return "Current Power (Device)"

    @property
    def device_class(self):
        """Return the device class."""
        return SensorDeviceClass.POWER

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return UnitOfPower.KILO_WATT

    @property
    def available(self):
        """Return availability of the sensor."""
        return self._coordinator.boiler_power is not None

    @property
    def native_value(self) -> float:
        """Return the state of the device in native units.

        In this case, the state represents the current power of the boiler in kW.
        """
        return self._coordinator.boiler_power

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config.entry_id}-boiler-current-power"


def calculate_consumption_rate(coordinator: SatDataUpdateCoordinator, minimum_consumption: float, maximum_consumption: float) -> Optional[float]:
    """Estimate the current gas consumption rate in m³/h from the relative modulation."""
    if (relative_modulation_value := coordinator.relative_modulation_value) is None:
        return None

    if not coordinator.active or not coordinator.flame_active:
        return 0.0

    differential_gas_consumption = maximum_consumption - minimum_consumption
    modulation_fraction = relative_modulation_value / 100

    return round(minimum_consumption + (modulation_fraction * differential_gas_consumption), 3)


class SatCurrentConsumptionSensor(SatEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: SatDataUpdateCoordinator, config: SatConfig, heating_control: SatHeatingControl):
        super().__init__(coordinator, config, heating_control)

        self._minimum_consumption = self._config.limits.minimum_consumption
        self._maximum_consumption = self._config.limits.maximum_consumption

    @property
    def name(self) -> str:
        return "Current Consumption (Device)"

    @property
    def device_class(self):
        """Return the device class."""
        return SensorDeviceClass.VOLUME_FLOW_RATE

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR

    @property
    def available(self):
        """Return availability of the sensor."""
        return self._coordinator.relative_modulation_value is not None

    @property
    def native_value(self) -> Optional[float]:
        """Return the state of the device in native units.

        In this case, the state represents the current consumption of the boiler in m³/h.
        """
        return calculate_consumption_rate(self._coordinator, self._minimum_consumption, self._maximum_consumption)

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config.entry_id}-boiler-current-consumption"


class SatTotalConsumptionSensor(SatEntity, RestoreSensor):
    _attr_device_class = SensorDeviceClass.GAS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS

    def __init__(self, coordinator: SatDataUpdateCoordinator, config: SatConfig, heating_control: SatHeatingControl):
        super().__init__(coordinator, config, heating_control)

        self._total: float = 0.0
        self._last_rate: Optional[float] = None
        self._last_update: Optional[float] = None

        self._minimum_consumption = self._config.limits.minimum_consumption
        self._maximum_consumption = self._config.limits.maximum_consumption

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        if (data := await self.async_get_last_sensor_data()) is not None and data.native_value is not None:
            self._total = float(data.native_value)

    @property
    def name(self) -> str:
        return "Total Consumption (Device)"

    @property
    def native_value(self) -> float:
        return round(self._total, 4)

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config.entry_id}-boiler-total-consumption"

    @callback
    def _handle_coordinator_update(self) -> None:
        now = timestamp()

        if self._last_update is not None and self._last_rate is not None:
            elapsed_hours = (now - self._last_update) / 3600

            # Skip gaps over an hour, so restarts and clock jumps cannot inflate the total
            if 0 < elapsed_hours < 1:
                self._total += self._last_rate * elapsed_hours

        self._last_update = now
        self._last_rate = calculate_consumption_rate(self._coordinator, self._minimum_consumption, self._maximum_consumption)

        super()._handle_coordinator_update()


class SatHeatingCurveSensor(SatClimateEntity, SensorEntity):
    _attr_translation_key = "heating_curve"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def name(self) -> str:
        return "Heating Curve"

    @property
    def device_class(self):
        """Return the device class."""
        return SensorDeviceClass.TEMPERATURE

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return UnitOfTemperature.CELSIUS

    @property
    def available(self):
        """Return availability of the sensor."""
        return self._climate.heating_curve.value is not None

    @property
    def native_value(self) -> Optional[float]:
        """Return the state of the device in native units."""
        return self._climate.heating_curve.value

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config.entry_id}-heating-curve"


class SatHeatingCurveRecommendationSensor(SatClimateEntity, SensorEntity):
    _attr_translation_key = "heating_curve_recommendation"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [recommendation.value for recommendation in HeatingCurveRecommendation]

    async def async_added_to_hass(self) -> None:
        def on_pid_updated(entity_id: str) -> None:
            if entity_id != self._climate.entity_id:
                return

            self.schedule_update_ha_state()

        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_PID_UPDATED, on_pid_updated))

    @property
    def name(self) -> str:
        return "Heating Curve Recommendation"

    @property
    def native_value(self) -> str:
        return self._climate.curve_advisor.advice.recommendation.value

    @property
    def extra_state_attributes(self) -> Optional[Mapping[str, Any]]:
        advice = self._climate.curve_advisor.advice
        daily = self._climate.temperature_statistics.window.daily
        recent = self._climate.temperature_statistics.window.recent

        return {
            "confidence": advice.confidence.value if advice.confidence is not None else None,

            "window_days": RECOMMENDATION_WINDOW_SECONDS // (24 * 60 * 60),
            "minimum_days": RECOMMENDATION_MINIMUM_DAYS,
            "minimum_samples": RECOMMENDATION_MINIMUM_SAMPLES,

            "sample_days": advice.sample_days,
            "sample_count": advice.sample_count,
            "median_flow_correction": advice.median_correction,

            "current_coefficient": self._config.pid.heating_curve_coefficient,
            "estimated_coefficient": advice.estimated_coefficient,
            "suggested_coefficient": advice.suggested_coefficient,
            "daily_coefficients": dict(advice.daily_coefficients),

            "recent_mean_error": recent.mean_error,
            "recent_sample_count": recent.sample_count,
            "recent_median_error": recent.median_error,
            "recent_mean_abs_error": recent.mean_abs_error,
            "recent_in_band_fraction": recent.in_band_fraction,

            "daily_mean_error": daily.mean_error,
            "daily_sample_count": daily.sample_count,
            "daily_median_error": daily.median_error,
            "daily_mean_abs_error": daily.mean_abs_error,
            "daily_in_band_fraction": daily.in_band_fraction,
        }

    @property
    def unique_id(self) -> str:
        return f"{self._config.entry_id}-heating-curve-recommendation"


class SatErrorValueSensor(SatClimateEntity, SensorEntity):
    _attr_translation_key = "error_value"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def name(self) -> str:
        return "Error Value"

    @property
    def device_class(self):
        """Return the device class."""
        return SensorDeviceClass.TEMPERATURE

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return UnitOfTemperature.CELSIUS

    @property
    def native_value(self) -> Optional[float]:
        """Return the state of the device in native units."""
        if (temperature_state := self._climate.temperature_state) is None:
            return None

        return temperature_state.error

    @property
    def available(self):
        """Return availability of the sensor."""
        return self._climate.temperature_state is not None

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config.entry_id}-error-value"


class SatManufacturerSensor(SatEntity, SensorEntity):
    _attr_translation_key = "manufacturer"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def name(self) -> str:
        return "Device Manufacturer"

    @property
    def native_value(self) -> Optional[str]:
        manufacturer = self._coordinator.manufacturer
        return manufacturer.friendly_name if manufacturer is not None else None

    @property
    def available(self) -> bool:
        return self._coordinator.manufacturer is not None

    @property
    def unique_id(self) -> str:
        return f"{self._config.entry_id}-manufacturer"


class SatCycleSensor(SatHeatingControlEntity, SensorEntity):
    _attr_translation_key = "cycle_status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_added_to_hass(self) -> None:
        def on_cycle_event(_event: Event) -> None:
            self.schedule_update_ha_state()

        await super().async_added_to_hass()

        self.async_on_remove(self.hass.bus.async_listen(EVENT_SAT_CYCLE_ENDED, on_cycle_event))

    @property
    def name(self) -> str:
        return "Cycle Status"

    @property
    def native_value(self) -> str:
        if self._heating_control is None or self._heating_control.last_cycle is None:
            return CycleClassification.INSUFFICIENT_DATA.name

        return self._heating_control.last_cycle.classification.name

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        if self._heating_control is None:
            return {}

        cycle = self._heating_control.last_cycle
        if cycle is None:
            return {}

        return {
            "kind": cycle.kind.name,
            "sample_count": cycle.sample_count,
            "duration_seconds": round(cycle.duration, 1),
            "max_flow_temperature": cycle.max_flow_temperature,
            "fraction_space_heating": cycle.fraction_space_heating,
            "fraction_domestic_hot_water": cycle.fraction_domestic_hot_water,
            "tail_hot_water_active_fraction": cycle.tail.hot_water_active_fraction,
            "tail_flow_control_setpoint_error_p90": cycle.tail.flow_control_setpoint_error.p90,
            "tail_flow_requested_setpoint_error_p90": cycle.tail.flow_requested_setpoint_error.p90,
            "tail_flow_temperature_p90": cycle.tail.flow_temperature.p90,
            "tail_control_setpoint_p50": cycle.tail.control_setpoint.p50,
            "tail_requested_setpoint_p50": cycle.tail.requested_setpoint.p50,
        }

    @property
    def unique_id(self) -> str:
        return f"{self._config.entry_id}-cycle-status"


class SatDeviceSensor(SatHeatingControlEntity, SensorEntity):
    _attr_translation_key = "boiler_status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def name(self) -> str:
        return "Device Status"

    @property
    def native_value(self) -> str:
        if self._heating_control is None:
            return BoilerStatus.INSUFFICIENT_DATA.name

        return self._heating_control.device_status.name

    @property
    def available(self) -> bool:
        if self._heating_control is None:
            return False

        return self._heating_control.device_status != BoilerStatus.INSUFFICIENT_DATA

    @property
    def unique_id(self) -> str:
        return f"{self._config.entry_id}-boiler-status"
