from __future__ import annotations

import logging
from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Optional, Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from ..boiler import Boiler, BoilerState, BoilerCapabilities, BoilerControlIntent
from ..const import DOMAIN, COLD_SETPOINT, MINIMUM_SETPOINT
from ..cycles import CycleTracker, CycleHistory, CycleStatistics, Cycle
from ..entry_data import SatConfig, SatMode
from ..helpers import calculate_default_maximum_setpoint, event_timestamp, timestamp
from ..manufacturer import Manufacturer, ManufacturerFactory
from ..manufacturers.geminox import Geminox
from ..manufacturers.ideal import Ideal
from ..manufacturers.intergas import Intergas
from ..manufacturers.nefit import Nefit
from ..types import BoilerStatus, DeviceState

if TYPE_CHECKING:
    from ..pwm import PWMState

_LOGGER: logging.Logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ControlLoopSample:
    timestamp: float

    pwm: "PWMState"
    state: "BoilerState"
    intent: "BoilerControlIntent"
    outside_temperature: Optional[float] = None
    requested_setpoint: Optional[float] = None


class SatData(dict):
    _is_dirty: bool = False

    def __setitem__(self, key, value):
        if self.get(key) != value:
            self._is_dirty = True

        super().__setitem__(key, value)

    def update(self, other: dict, **kwargs):
        for key, value in {**other, **kwargs}.items():
            if self.get(key) != value:
                self._is_dirty = True

            super().__setitem__(key, value)

    def __delitem__(self, key):
        self._is_dirty = True
        super().__delitem__(key)

    def reset_dirty(self):
        self._is_dirty = False

    def is_dirty(self) -> bool:
        return self._is_dirty


class SatDataUpdateCoordinatorFactory:
    @staticmethod
    def resolve(hass: HomeAssistant, config: SatConfig) -> SatDataUpdateCoordinator:
        if config.mode == SatMode.FAKE:
            from .fake import SatFakeCoordinator
            return SatFakeCoordinator(hass=hass, config=config)

        if config.mode == SatMode.SIMULATOR:
            from .simulator import SatSimulatorCoordinator
            return SatSimulatorCoordinator(hass=hass, config=config)

        if config.mode == SatMode.SWITCH:
            from .switch import SatSwitchCoordinator
            return SatSwitchCoordinator(hass=hass, config=config)

        if config.mode == SatMode.ESPHOME:
            from .esphome import SatEspHomeCoordinator
            return SatEspHomeCoordinator(hass=hass, config=config)

        if config.mode == SatMode.MQTT_EMS:
            from .mqtt.ems import SatEmsMqttCoordinator
            return SatEmsMqttCoordinator(hass=hass, config=config)

        if config.mode == SatMode.MQTT_OPENTHERM:
            from .mqtt.opentherm import SatOpenThermMqttCoordinator
            return SatOpenThermMqttCoordinator(hass=hass, config=config)

        if config.mode == SatMode.MQTT_OTTHING:
            from .mqtt.otthing import SatOtthingMqttCoordinator
            return SatOtthingMqttCoordinator(hass=hass, config=config)

        if config.mode == SatMode.SERIAL:
            from .serial import SatSerialCoordinator
            return SatSerialCoordinator(hass=hass, config=config)

        raise ValueError(f"Invalid mode[{config.mode}]")


class SatDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, config: SatConfig) -> None:
        """Initialize."""
        super().__init__(hass, _LOGGER, name=DOMAIN)
        self.data: SatData = SatData()

        self._boiler: Boiler = Boiler()
        self._cycles: CycleHistory = CycleHistory()
        self._cycle_tracker: CycleTracker = CycleTracker(hass, self._cycles)

        self._device_on_since: Optional[float] = None
        self._flame_off_hold_setpoint: Optional[float] = None

        self._control_pwm_state: Optional["PWMState"] = None
        self._control_requested_setpoint: Optional[float] = None
        self._control_outside_temperature: Optional[float] = None
        self._control_intent: Optional[BoilerControlIntent] = None
        self._hass_notify_debouncer = Debouncer(hass=self.hass, logger=_LOGGER, cooldown=0.2, immediate=False, function=self.async_update_listeners)
        self._control_update_debouncer = Debouncer(hass=self.hass, logger=_LOGGER, cooldown=0.2, immediate=False, function=self.async_update_control)

        self._config: SatConfig = config
        self._manufacturer: Optional[Manufacturer] = None

        if self._config.manufacturer is not None:
            self._manufacturer = ManufacturerFactory.resolve_by_name(self._config.manufacturer)

    @property
    @abstractmethod
    def device_id(self) -> str:
        """Expose the unique device identifier."""
        pass

    @property
    @abstractmethod
    def device_type(self) -> str:
        """Expose the device backend type."""
        pass

    @property
    def device_status(self) -> BoilerStatus:
        """Report the current boiler status."""
        return self._boiler.status

    @property
    def device_capabilities(self) -> BoilerCapabilities:
        """Describe setpoint capabilities for this device."""
        return BoilerCapabilities(
            minimum_setpoint=self.minimum_setpoint,
            maximum_setpoint=self.maximum_setpoint_value,
        )

    @property
    def device_state(self) -> BoilerState:
        """Snapshot the current boiler state for control logic."""
        return BoilerState(
            flame_active=self.flame_active,
            central_heating=self.device_active,
            hot_water_active=self.hot_water_active,
            modulation_reliable=self._boiler.modulation_reliable,

            flame_on_since=self._boiler.flame_on_since,
            flame_off_since=self._boiler.flame_off_since,

            setpoint=self.setpoint,
            flow_temperature=self.boiler_temperature,
            return_temperature=self.return_temperature,
            relative_modulation_level=self.relative_modulation_value,
            max_modulation_level=self.maximum_relative_modulation_value,
        )

    @property
    def cycles(self) -> CycleStatistics:
        """Expose aggregated cycle statistics."""
        return self._cycles.statistics

    @property
    def last_cycle(self) -> Optional[Cycle]:
        """Expose the most recent completed cycle."""
        return self._cycles.last_cycle

    @property
    def manufacturer(self) -> Optional[Manufacturer]:
        """Report the detected manufacturer, if any."""
        return self._manufacturer

    @property
    @abstractmethod
    def setpoint(self) -> Optional[float]:
        """Current boiler setpoint reported by the device."""
        pass

    @property
    @abstractmethod
    def device_active(self) -> bool:
        """Whether the device is actively heating."""
        pass

    @property
    @abstractmethod
    def member_id(self) -> Optional[int]:
        """Member id used to infer manufacturer for some devices."""
        pass

    @property
    def flame_active(self) -> bool:
        """Expose flame activity; defaults to device_active."""
        return self.device_active

    @property
    def heater_on_since(self) -> Optional[float]:
        """Timestamp when the heater last turned on."""
        return self._device_on_since

    @property
    def hot_water_active(self) -> bool:
        """Whether domestic hot water is active."""
        return False

    @property
    def hot_water_setpoint(self) -> Optional[float]:
        """Domestic hot water setpoint, if supported."""
        return None

    @property
    def boiler_temperature(self) -> Optional[float]:
        """Current boiler flow temperature."""
        return None

    @property
    def return_temperature(self) -> Optional[float]:
        """Current boiler return temperature."""
        return None

    @property
    def minimum_hot_water_setpoint(self) -> float:
        """Minimum supported hot water setpoint."""
        return 30

    @property
    def maximum_hot_water_setpoint(self) -> float:
        """Maximum supported hot water setpoint."""
        return 60

    @property
    def relative_modulation_value(self) -> Optional[float]:
        """Current relative modulation level."""
        return None

    @property
    def minimum_setpoint_value(self) -> Optional[float]:
        """Expose the effective minimum setpoint value."""
        return self.minimum_setpoint

    @property
    def maximum_setpoint_value(self) -> Optional[float]:
        """Expose the effective maximum setpoint value."""
        return self.maximum_setpoint

    @property
    def boiler_capacity(self) -> Optional[float]:
        """Nominal boiler capacity in kW (if available)."""
        return None

    @property
    def minimum_boiler_capacity(self) -> Optional[float]:
        """Minimum boiler capacity at minimum modulation."""
        if (minimum_relative_modulation_value := self.minimum_relative_modulation_value) is None:
            return None

        if (boiler_capacity := self.boiler_capacity) is None:
            return None

        if boiler_capacity == 0:
            return 0

        return boiler_capacity * (minimum_relative_modulation_value / 100)

    @property
    def boiler_power(self) -> Optional[float]:
        """Estimate current boiler power output."""
        if (boiler_capacity := self.boiler_capacity) is None:
            return None

        if (minimum_boiler_capacity := self.minimum_boiler_capacity) is None:
            return None

        if (relative_modulation_value := self.relative_modulation_value) is None:
            return None

        if not self.flame_active:
            return 0

        return minimum_boiler_capacity + ((boiler_capacity - minimum_boiler_capacity) * (relative_modulation_value / 100))

    @property
    def minimum_relative_modulation_value(self) -> Optional[float]:
        """Minimum supported relative modulation value."""
        return None

    @property
    def maximum_relative_modulation_value(self) -> Optional[float]:
        """Maximum supported relative modulation value."""
        return None

    @property
    def minimum_setpoint(self) -> float:
        """Return the minimum setpoint temperature before the device starts to overshoot."""
        return max(self._config.limits.minimum_setpoint, MINIMUM_SETPOINT)

    @property
    def maximum_setpoint(self) -> float:
        """Return the maximum setpoint temperature that the device can support."""
        default_maximum_setpoint = calculate_default_maximum_setpoint(self._config.heating_system)
        maximum_setpoint = self._config.limits.maximum_setpoint

        if maximum_setpoint is None:
            return float(default_maximum_setpoint)

        return float(maximum_setpoint)

    @property
    def supports_setpoint_management(self):
        """Returns whether the device supports setting a boiler setpoint.

        This property is used to determine whether the coordinator can send a setpoint to the device.
        If a device doesn't support setpoint management, the coordinator won't be able to control the temperature.
        """
        return False

    @property
    def supports_hot_water_setpoint_management(self):
        """Returns whether the device supports setting a hot water setpoint.

        This property is used to determine whether the coordinator can send a setpoint to the device.
        If a device doesn't support setpoint management, the coordinator won't be able to control the temperature.
        """
        return False

    @property
    def supports_relative_modulation(self):
        """Returns whether the device supports having relative modulation value.

        This property is used to determine whether the coordinator can retrieve the relative modulation value from the device.
        If a device doesn't support the relative modulation value, the coordinator won't be able to retrieve the value.
        """
        if isinstance(self.manufacturer, (Ideal, Intergas, Geminox, Nefit)):
            return False

        return True

    @property
    def supports_relative_modulation_management(self):
        """Returns whether the device supports setting a relative modulation value.

        This property is used to determine whether the coordinator can send a relative modulation value to the device.
        If a device doesn't support relative modulation management, the coordinator won't be able to control the value.
        """
        return True

    @property
    def supports_maximum_setpoint_management(self):
        """Returns whether the device supports setting a maximum setpoint.

        This property is used to determine whether the coordinator can send a maximum setpoint to the device.
        If a device doesn't support maximum setpoint management, the coordinator won't be able to control the value.
        """
        return False

    async def async_setup(self) -> None:
        """Perform setup when the integration is about to be added to Home Assistant."""
        pass

    async def async_added_to_hass(self, hass: HomeAssistant) -> None:
        """Perform setup when the integration is added to Home Assistant."""
        pass

    async def async_will_remove_from_hass(self) -> None:
        """Run when an entity is removed from hass."""
        pass

    def set_control_intent(self, intent: BoilerControlIntent) -> None:
        """Store the latest control intent produced by a climate entity."""
        self._control_intent = intent

    def set_control_context(self, pwm_state: "PWMState", requested_setpoint: Optional[float] = None, outside_temperature: Optional[float] = None) -> None:
        """Store the latest control context produced by a climate entity."""
        self._control_pwm_state = pwm_state
        self._control_requested_setpoint = requested_setpoint
        self._control_outside_temperature = outside_temperature

    async def async_control_heating_loop(self, time: Optional[datetime] = None) -> None:
        """Control the heating loop for the device."""
        timestamp = event_timestamp(time)

        # Track how long the device has been on.
        if not self.device_active:
            self._device_on_since = None
        elif self._device_on_since is None:
            self._device_on_since = timestamp

        # Backfill manufacturer from member_id when not set (deprecated path).
        if self._manufacturer is None and self.member_id is not None:
            manufacturers = ManufacturerFactory.resolve_by_member_id(self.member_id)
            self._manufacturer = manufacturers[0] if len(manufacturers) > 0 else None

        if self._control_intent is not None:
            if self._control_intent.setpoint is not None:
                setpoint = self._apply_control_setpoint_override(self._control_intent.setpoint)
                await self.async_set_control_setpoint(setpoint if setpoint > COLD_SETPOINT else MINIMUM_SETPOINT)

            if self._control_intent.relative_modulation is not None:
                await self.async_set_control_max_relative_modulation(int(self._control_intent.relative_modulation))

    async def async_set_heater_state(self, state: DeviceState) -> None:
        """Set the state of the device heater."""
        _LOGGER.info("Set central heater state %s", state)

    async def async_set_control_setpoint(self, value: float) -> None:
        """Control the boiler setpoint temperature for the device."""
        if self.supports_setpoint_management:
            _LOGGER.info("Set control boiler setpoint to %.1f°C", value)

    async def async_set_control_hot_water_setpoint(self, value: float) -> None:
        """Control the DHW setpoint temperature for the device."""
        if self.supports_hot_water_setpoint_management:
            _LOGGER.info("Set control hot water setpoint to %.1f°C", value)

    async def async_set_control_max_setpoint(self, value: float) -> None:
        """Control the maximum setpoint temperature for the device."""
        if self.supports_maximum_setpoint_management:
            _LOGGER.info("Set maximum setpoint to %.1f°C", value)

    async def async_set_control_max_relative_modulation(self, value: int) -> None:
        """Control the maximum relative modulation for the device."""
        if self.supports_relative_modulation_management:
            _LOGGER.info("Set maximum relative modulation to %d%%", value)

    async def async_set_control_thermostat_setpoint(self, value: float) -> None:
        """Control the setpoint temperature for the thermostat."""
        pass

    async def async_update_control(self) -> None:
        """Update the control state."""
        self._boiler.update(state=self.device_state, last_cycle=self.last_cycle)

        if self._control_intent is not None and self._control_pwm_state is not None:
            self._cycle_tracker.update(ControlLoopSample(
                timestamp=timestamp(),
                state=self.device_state,
                intent=self._control_intent,
                pwm=self._control_pwm_state,
                outside_temperature=self._control_outside_temperature,
                requested_setpoint=self._control_requested_setpoint,
            ))

    @callback
    def async_notify_listeners(self, force: bool = True) -> None:
        """Notify listeners that the data has changed."""
        self.hass.async_create_task(self._control_update_debouncer.async_call())

        if not force and not self.data.is_dirty():
            return

        self.data.reset_dirty()
        self.hass.async_create_task(self._hass_notify_debouncer.async_call())

    @callback
    def async_set_updated_data(self, data: dict) -> None:
        """Update the internal data store with new values."""
        self.data.update(data)
        self.async_notify_listeners(False)

    def _apply_control_setpoint_override(self, intended_setpoint: float) -> float:
        """Apply manufacturer specific logic to override the intended setpoint."""
        if (manufacturer := self.manufacturer) is None:
            return intended_setpoint

        if self._control_pwm_state is None or not self._control_pwm_state.enabled:
            return intended_setpoint

        if self.hot_water_active or self._control_intent.setpoint <= COLD_SETPOINT:
            return intended_setpoint

        if not self.flame_active:
            if (return_temperature := self.return_temperature) is None:
                return intended_setpoint

            if (flame_off_config := manufacturer.flame_off_setpoint) is None:
                return intended_setpoint

            overridden = return_temperature + flame_off_config.offset_celsius
            self._flame_off_hold_setpoint = overridden

            _LOGGER.debug(
                "Setpoint override (flame off) for %s: return=%.1f°C offset=%.1f°C -> %.1f°C (intended=%.1f°C)",
                manufacturer.friendly_name, return_temperature, flame_off_config.offset_celsius, overridden, intended_setpoint
            )

            return overridden

        if (suppression := manufacturer.modulation_suppression) is None:
            return intended_setpoint

        if (flame_on_since := self._boiler.flame_on_since) is None:
            return intended_setpoint

        elapsed_since_flame_on = timestamp() - flame_on_since
        if elapsed_since_flame_on < suppression.delay_seconds:
            if self._flame_off_hold_setpoint is not None:
                _LOGGER.debug(
                    "Setpoint override hold for %s: using flame-off setpoint %.1f°C for %.1fs more.",
                    manufacturer.friendly_name,
                    self._flame_off_hold_setpoint,
                    suppression.delay_seconds - elapsed_since_flame_on,
                )

                return self._flame_off_hold_setpoint

            _LOGGER.debug(
                "Setpoint override pending for %s: waiting %.1fs more (elapsed=%.1fs, delay=%.1fs).",
                manufacturer.friendly_name, suppression.delay_seconds - elapsed_since_flame_on, elapsed_since_flame_on, suppression.delay_seconds
            )

            return intended_setpoint

        if (flow_temperature := self.boiler_temperature) is None:
            return intended_setpoint

        suppressed_setpoint = flow_temperature - suppression.offset_celsius
        if suppressed_setpoint <= self._control_intent.setpoint:
            self._flame_off_hold_setpoint = None
            return intended_setpoint

        _LOGGER.debug(
            "Setpoint override (suppression) for %s: flow=%.1f°C offset=%.1f°C -> %.1f°C (min=%.1f°C, intended=%.1f°C)",
            manufacturer.friendly_name, flow_temperature, suppression.offset_celsius, suppressed_setpoint, self._control_intent.setpoint, intended_setpoint
        )

        self._flame_off_hold_setpoint = None
        return suppressed_setpoint


class SatEntityCoordinator(DataUpdateCoordinator):
    def get(self, domain: str, key: str) -> Optional[Any]:
        """Get the value for the given `key` from the boiler data."""
        entity_id = self._get_entity_id(domain, key)
        if entity_id is None:
            return None

        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None

        return state.state

    @abstractmethod
    def _get_entity_id(self, domain: str, key: str):
        pass
