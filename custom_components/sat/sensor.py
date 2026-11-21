from __future__ import annotations

from datetime import datetime
from typing import Optional, Mapping, Any

from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfSpeed, UnitOfTemperature, UnitOfVolume, UnitOfVolumeFlowRate, EntityCategory
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import *
from .coordinator import SatDataUpdateCoordinator
from .coordinator.serial import sensor as serial_sensor
from .coordinator.simulator import sensor as simulator_sensor
from .demand_control import SatDemandControl
from .entity import SatDemandControlEntity, SatEntity, SatHeatingControlEntity
from .entry_data import SatConfig, SatMode, get_entry_data
from .heating_control import SatHeatingControl
from .heating_curve import RECOMMENDATION_MINIMUM_DAYS, RECOMMENDATION_MINIMUM_SAMPLES, RECOMMENDATION_WINDOW_SECONDS
from .seasonal_curve import MINIMUM_DAYS as SEASONAL_MINIMUM_DAYS, MINIMUM_HOURS as SEASONAL_MINIMUM_HOURS
from .helpers import float_value, timestamp
from .room_balance import MAIN_ROOM_ID
from .types import BoilerStatus, CycleClassification, HeatingCurveRecommendation, RoomBalanceStatus

ROOM_BALANCE_ISSUES: dict[RoomBalanceStatus, str] = {
    RoomBalanceStatus.ROOM_FAULT: "room_fault",
    RoomBalanceStatus.UNDERSUPPLIED: "room_undersupplied",
    RoomBalanceStatus.SENSOR_MISMATCH: "room_sensor_mismatch",
}

BOILER_STATUS_ICONS: dict[BoilerStatus, str] = {
    BoilerStatus.OFF: "mdi:water-boiler-off",
    BoilerStatus.IDLE: "mdi:water-boiler-auto",
    BoilerStatus.COOLING: "mdi:snowflake-thermometer",
    BoilerStatus.PUMP_STARTING: "mdi:pump",
    BoilerStatus.ANTI_CYCLING: "mdi:timer-pause",
    BoilerStatus.CENTRAL_HEATING: "mdi:radiator",
    BoilerStatus.STALLED_IGNITION: "mdi:water-boiler-alert",
    BoilerStatus.INSUFFICIENT_DATA: "mdi:water-boiler-alert",
    BoilerStatus.WAITING_FOR_FLAME: "mdi:fire-alert",
    BoilerStatus.OVERSHOOT_COOLING: "mdi:snowflake-thermometer",
    BoilerStatus.HEATING_HOT_WATER: "mdi:water-thermometer",
}

CYCLE_STATUS_ICONS: dict[CycleClassification, str] = {
    CycleClassification.GOOD: "mdi:check-circle",
    CycleClassification.UNCERTAIN: "mdi:help-circle",
    CycleClassification.OVERSHOOT: "mdi:arrow-up-bold-circle",
    CycleClassification.UNDERHEAT: "mdi:arrow-down-bold-circle",
    CycleClassification.SHORT_CYCLING: "mdi:alert-circle",
    CycleClassification.INSUFFICIENT_DATA: "mdi:timer-sand",
}


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Add SAT sensors based on the configured integration mode."""
    if config_entry.entry_id not in hass.data.get(DOMAIN, {}):
        return

    entry_data = get_entry_data(hass, config_entry.entry_id)

    if entry_data.config.mode == SatMode.SERIAL:
        await serial_sensor.async_setup_entry(hass, config_entry, async_add_entities)

    if entry_data.config.mode == SatMode.SIMULATOR:
        await simulator_sensor.async_setup_entry(hass, config_entry, async_add_entities)

    sensors: list[SensorEntity] = [
        SatCycleSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control),
        SatDeviceSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control),
        SatManufacturerSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control),
        SatRequestedSetpoint(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.demand_control),
        SatOutsideTemperatureSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.demand_control),
        SatWindSpeedSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.demand_control),
        SatImpliedCurveCoefficientSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.demand_control),
        SatSeasonalCurveCoefficientSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.demand_control),
        SatHeatingEfficiencySensor(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.demand_control),
        SatHeatingCurveRecommendationSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.demand_control),
    ]

    if not entry_data.config.rooms_only:
        sensors.extend([
            SatPidSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.demand_control),
            SatErrorValueSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.demand_control),
            SatRoomBalanceSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.demand_control, MAIN_ROOM_ID),
            SatHeatingCurveSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.demand_control),
        ])

    for entity_id in entry_data.config.rooms:
        sensors.append(SatPidSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.demand_control, entity_id))
        sensors.append(SatRoomBalanceSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control, entry_data.demand_control, entity_id))

    if entry_data.coordinator.supports_relative_modulation_management:
        sensors.append(SatCurrentPowerSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control))

        minimum_consumption = entry_data.config.limits.minimum_consumption
        maximum_consumption = entry_data.config.limits.maximum_consumption

        if minimum_consumption > 0 and maximum_consumption > 0:
            sensors.append(SatCurrentConsumptionSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control))
            sensors.append(SatTotalConsumptionSensor(entry_data.coordinator, entry_data.config, entry_data.heating_control))

    async_add_entities(sensors)


class SatRequestedSetpoint(SatDemandControlEntity, SensorEntity):
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
        return self._demand_control.requested_setpoint

    @property
    def unique_id(self) -> str:
        return f"{self._config.entry_id}-requested-setpoint"


class SatOutsideTemperatureSensor(SatDemandControlEntity, SensorEntity):
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
        if (temperature := self._demand_control.current_outside_temperature) is None:
            return None

        return round(temperature, 1)

    @property
    def unique_id(self) -> str:
        return f"{self._config.entry_id}-outside-temperature"


class SatWindSpeedSensor(SatDemandControlEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_added_to_hass(self) -> None:
        def on_pid_updated(_) -> None:
            self.schedule_update_ha_state()

        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_PID_UPDATED, on_pid_updated))

    @property
    def name(self) -> str:
        return "Wind Speed"

    @property
    def device_class(self) -> SensorDeviceClass:
        return SensorDeviceClass.WIND_SPEED

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return UnitOfSpeed.KILOMETERS_PER_HOUR

    @property
    def native_value(self) -> Optional[float]:
        if (wind := self._demand_control.current_wind) is None:
            return None

        return wind.speed

    @property
    def extra_state_attributes(self) -> Optional[Mapping[str, Any]]:
        wind = self._demand_control.current_wind

        return {"source": wind.entity_id if wind is not None else None}

    @property
    def unique_id(self) -> str:
        return f"{self._config.entry_id}-wind-speed"


class SatImpliedCurveCoefficientSensor(SatDemandControlEntity, SensorEntity, RestoreEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SatDataUpdateCoordinator, config: SatConfig, heating_control: SatHeatingControl, demand_control: SatDemandControl) -> None:
        super().__init__(coordinator, config, heating_control, demand_control)

        self._last_value: Optional[float] = None
        self._last_measured_at: Optional[datetime] = None

    async def async_added_to_hass(self) -> None:
        def on_pid_updated(entity_id: str) -> None:
            if entity_id != self._demand_control.id:
                return

            if (value := self._demand_control.implied_curve_coefficient) is not None:
                self._last_value, self._last_measured_at = value, dt_util.utcnow()

            self.schedule_update_ha_state()

        await super().async_added_to_hass()

        if (state := await self.async_get_last_state()) is not None and (last_value := float_value(state.attributes.get("last_value"))) is not None:
            self._last_value = last_value
            self._last_measured_at = dt_util.parse_datetime(str(state.attributes.get("last_measured_at")))

        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_PID_UPDATED, on_pid_updated))

    @property
    def name(self) -> str:
        return "Implied Curve Coefficient"

    @property
    def native_value(self) -> Optional[float]:
        return self._demand_control.implied_curve_coefficient

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        return {"last_value": self._last_value, "last_measured_at": self._last_measured_at}

    @property
    def unique_id(self) -> str:
        return f"{self._config.entry_id}-implied-curve-coefficient"


class SatSeasonalCurveCoefficientSensor(SatDemandControlEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def name(self) -> str:
        return "Seasonal Curve Coefficient"

    @property
    def native_value(self) -> Optional[float]:
        return self._demand_control.seasonal_curve.fit.coefficient

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        fit = self._demand_control.seasonal_curve.fit

        return {
            "days": fit.days,
            "hours": fit.hours,
            "minimum_days": SEASONAL_MINIMUM_DAYS,
            "minimum_hours": SEASONAL_MINIMUM_HOURS,
            "cold_coefficient": fit.cold_coefficient,
            "mild_coefficient": fit.mild_coefficient,
            "curve_mismatch": fit.is_mismatch,
            "bins": [{"minimum_temperature": item.minimum_temperature, "coefficient": item.coefficient, "hours": item.hours} for item in fit.bins],
        }

    @property
    def unique_id(self) -> str:
        return f"{self._config.entry_id}-seasonal-curve-coefficient"


class SatHeatingEfficiencySensor(SatDemandControlEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def name(self) -> str:
        return "Heating Efficiency"

    @property
    def native_unit_of_measurement(self) -> Optional[str]:
        """Return the gas used per degree day, in the unit of the gas statistics."""
        report = self._demand_control.efficiency.report

        return f"{report.unit}/°Cd" if report.unit is not None else None

    @property
    def native_value(self) -> Optional[float]:
        report = self._demand_control.efficiency.report

        return report.current.gas_per_degree_day if report.current is not None else None

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        report = self._demand_control.efficiency.report
        current, previous = report.current, report.previous

        return {
            "source": report.source,
            "days": current.days if current is not None else None,
            "degree_days": current.degree_days if current is not None else None,
            "baseload_per_day": current.baseload if current is not None else None,
            "previous_year": previous.gas_per_degree_day if previous is not None else None,
            "change_percentage": report.change_percentage,
            "gas_estimate_ratio": report.gas_estimate_ratio,
        }

    @property
    def unique_id(self) -> str:
        return f"{self._config.entry_id}-heating-efficiency"


class SatPidSensor(SatDemandControlEntity, SensorEntity):
    _attr_translation_key = "pid"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
            self,
            coordinator: SatDataUpdateCoordinator,
            config: SatConfig,
            heating_control: SatHeatingControl,
            demand_control: SatDemandControl,
            area_id: Optional[str] = None,
    ):
        super().__init__(coordinator, config, heating_control, demand_control)
        self._area_id: Optional[str] = area_id

    async def async_added_to_hass(self) -> None:
        def on_pid_updated(entity_id: str) -> None:
            if entity_id != (self._area_id or self._demand_control.id):
                return

            self.schedule_update_ha_state()

        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_PID_UPDATED, on_pid_updated))

    @property
    def _pid(self):
        if self._area_id is None:
            return self._demand_control.pid

        if (area := self._demand_control.areas.get(self._area_id)) is None:
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


class SatRoomBalanceSensor(SatDemandControlEntity, SensorEntity):
    _attr_translation_key = "room_balance"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_options = [status.value for status in RoomBalanceStatus]

    def __init__(
            self,
            coordinator: SatDataUpdateCoordinator,
            config: SatConfig,
            heating_control: SatHeatingControl,
            demand_control: SatDemandControl,
            area_id: str,
    ):
        super().__init__(coordinator, config, heating_control, demand_control)
        self._area_id: str = area_id

    @property
    def name(self) -> str:
        return "Room Balance" if self._area_id == MAIN_ROOM_ID else f"Room Balance ({self._area_id})"

    @property
    def native_value(self) -> str:
        return self._demand_control.room_balance.balance(self._area_id).status.value

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        balance = self._demand_control.room_balance.balance(self._area_id)

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
            "cooling_time_constant_hours": balance.cooling_time_constant,
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
        balance = self._demand_control.room_balance.balance(self._area_id)
        room_name = self._config.name if self._area_id == MAIN_ROOM_ID else self._room_name()

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
                    "room": room_name,
                    "flow_premium": f"{balance.flow_premium or 0.0:.1f}",
                },
            )

        super()._handle_coordinator_update()

    def _issue_id(self, key: str) -> str:
        return f"{key}_{self._config.entry_id}_{self._area_id}"

    def _room_name(self) -> str:
        if (room_state := self.hass.states.get(self._area_id)) is None:
            return self._area_id

        return room_state.name


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


class SatHeatingCurveSensor(SatDemandControlEntity, SensorEntity):
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
        return self._demand_control.heating_curve.value is not None

    @property
    def native_value(self) -> Optional[float]:
        """Return the state of the device in native units."""
        return self._demand_control.heating_curve.value

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config.entry_id}-heating-curve"


class SatHeatingCurveRecommendationSensor(SatDemandControlEntity, SensorEntity):
    _attr_translation_key = "heating_curve_recommendation"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [recommendation.value for recommendation in HeatingCurveRecommendation]

    async def async_added_to_hass(self) -> None:
        def on_pid_updated(entity_id: str) -> None:
            if entity_id != self._demand_control.id:
                return

            self.schedule_update_ha_state()

        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_PID_UPDATED, on_pid_updated))

    @property
    def name(self) -> str:
        return "Heating Curve Recommendation"

    @property
    def native_value(self) -> str:
        return self._demand_control.curve_advisor.advice.recommendation.value

    @property
    def extra_state_attributes(self) -> Optional[Mapping[str, Any]]:
        advice = self._demand_control.curve_advisor.advice
        wind_sensitivity = self._demand_control.wind_sensitivity.sensitivity
        daily = self._demand_control.temperature_statistics.window.daily
        recent = self._demand_control.temperature_statistics.window.recent

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
            "daily_wind_speeds": dict(advice.daily_wind_speeds),

            "wind_days": wind_sensitivity.day_count,
            "wind_spread": wind_sensitivity.wind_spread,
            "calm_coefficient": wind_sensitivity.calm_coefficient,
            "wind_sensitivity_percent_per_10_kmh": wind_sensitivity.percent_per_10_kmh,

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


class SatErrorValueSensor(SatDemandControlEntity, SensorEntity):
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
        if (temperature_state := self._demand_control.temperature_state) is None:
            return None

        return temperature_state.error

    @property
    def available(self):
        """Return availability of the sensor."""
        return self._demand_control.temperature_state is not None

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
        return self._classification.name

    @property
    def icon(self) -> Optional[str]:
        return CYCLE_STATUS_ICONS.get(self._classification, "mdi:sync")

    @property
    def _classification(self) -> CycleClassification:
        if self._heating_control is None or self._heating_control.last_cycle is None:
            return CycleClassification.INSUFFICIENT_DATA

        return self._heating_control.last_cycle.classification

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
        return self._status.name

    @property
    def icon(self) -> Optional[str]:
        return BOILER_STATUS_ICONS.get(self._status, "mdi:water-boiler")

    @property
    def _status(self) -> BoilerStatus:
        if self._heating_control is None:
            return BoilerStatus.INSUFFICIENT_DATA

        return self._heating_control.device_status

    @property
    def available(self) -> bool:
        if self._heating_control is None:
            return False

        return self._heating_control.device_status != BoilerStatus.INSUFFICIENT_DATA

    @property
    def unique_id(self) -> str:
        return f"{self._config.entry_id}-boiler-status"
