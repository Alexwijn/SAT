from __future__ import annotations

import logging
from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Any, TYPE_CHECKING, Optional

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .boiler import Boiler, BoilerState, BoilerCapabilities, BoilerControlIntent
from .const import *
from .cycles import CycleTracker, CycleHistory, CycleStatistics, Cycle
from .helpers import calculate_default_maximum_setpoint, event_timestamp, timestamp
from .manufacturer import Manufacturer, ManufacturerFactory
from .manufacturers.geminox import Geminox
from .manufacturers.ideal import Ideal
from .manufacturers.intergas import Intergas
from .manufacturers.nefit import Nefit
from .types import BoilerStatus, DeviceState

if TYPE_CHECKING:
    from .pwm import PWMState

_LOGGER: logging.Logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ControlLoopSample:
    timestamp: float

    pwm: "PWMState"
    state: "BoilerState"
    intent: "BoilerControlIntent"
    outside_temperature: Optional[float] = None


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
    def resolve(hass: HomeAssistant, mode: str, device: str, data: Mapping[str, Any], options: Optional[Mapping[str, Any]] = None) -> SatDataUpdateCoordinator:
        if mode == MODE_FAKE:
            from .fake import SatFakeCoordinator
            return SatFakeCoordinator(hass=hass, config_data=data, options=options)

        if mode == MODE_SIMULATOR:
            from .simulator import SatSimulatorCoordinator
            return SatSimulatorCoordinator(hass=hass, config_data=data, options=options)

        if mode == MODE_SWITCH:
            from .switch import SatSwitchCoordinator
            return SatSwitchCoordinator(hass=hass, entity_id=device, config_data=data, options=options)

        if mode == MODE_ESPHOME:
            from .esphome import SatEspHomeCoordinator
            return SatEspHomeCoordinator(hass=hass, device_id=device, config_data=data, options=options)

        if mode == MODE_MQTT_EMS:
            from .mqtt.ems import SatEmsMqttCoordinator
            return SatEmsMqttCoordinator(hass=hass, device_id=device, config_data=data, options=options)

        if mode == MODE_MQTT_OPENTHERM:
            from .mqtt.opentherm import SatOpenThermMqttCoordinator
            return SatOpenThermMqttCoordinator(hass=hass, device_id=device, config_data=data, options=options)

        if mode == MODE_SERIAL:
            from .serial import SatSerialCoordinator
            return SatSerialCoordinator(hass=hass, port=device, config_data=data, options=options)

        raise Exception(f'Invalid mode[{mode}]')


class SatDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, config_data: Mapping[str, Any], options: Optional[Mapping[str, Any]] = None) -> None:
        """Initialize."""
        super().__init__(hass, _LOGGER, name=DOMAIN)
        self.data: SatData = SatData()

        self._boiler: Boiler = Boiler()
        self._cycles: CycleHistory = CycleHistory()
        self._cycle_tracker: CycleTracker = CycleTracker(hass, self._cycles)

        self._device_on_since: Optional[float] = None
        self._control_pwm_state: Optional["PWMState"] = None
        self._control_outside_temperature: Optional[float] = None
        self._control_intent: Optional[BoilerControlIntent] = None
        self._hass_notify_debouncer = Debouncer(hass=self.hass, logger=_LOGGER, cooldown=0.2, immediate=False, function=self.async_update_listeners)
        self._control_update_debouncer = Debouncer(hass=self.hass, logger=_LOGGER, cooldown=0.2, immediate=False, function=self.async_update_control)

        self._options: Mapping[str, Any] = options or {}
        self._config_data: Mapping[str, Any] = config_data

        self._manufacturer: Optional[Manufacturer] = None
        self._simulation: bool = bool(self._options.get(CONF_SIMULATION))
        self._heating_system: str = str(config_data.get(CONF_HEATING_SYSTEM, HEATING_SYSTEM_UNKNOWN))

        if config_data.get(CONF_MANUFACTURER) is not None:
            self._manufacturer = ManufacturerFactory.resolve_by_name(config_data.get(CONF_MANUFACTURER))

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
        return max(float(self._config_data.get(CONF_MINIMUM_SETPOINT)), MINIMUM_SETPOINT)

    @property
    def maximum_setpoint(self) -> float:
        """Return the maximum setpoint temperature that the device can support."""
        default_maximum_setpoint = calculate_default_maximum_setpoint(self._heating_system)
        return float(self._options.get(CONF_MAXIMUM_SETPOINT, default_maximum_setpoint))

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

    def set_control_context(self, pwm_state: "PWMState", outside_temperature: Optional[float] = None) -> None:
        """Store the latest control context produced by a climate entity."""
        self._control_pwm_state = pwm_state
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
        self._boiler.update(state=self.device_state, last_cycle=self.last_cycle)

        if self._control_intent is not None and self._control_pwm_state is not None:
            self._cycle_tracker.update(ControlLoopSample(
                timestamp=timestamp(),
                state=self.device_state,
                intent=self._control_intent,
                pwm=self._control_pwm_state,
                outside_temperature=self._control_outside_temperature,
            ))

    @callback
    def async_notify_listeners(self, force: bool = True) -> None:
        self.hass.async_add_job(self._control_update_debouncer.async_call())

        if not force and not self.data.is_dirty():
            return

        self.data.reset_dirty()
        self.hass.async_add_job(self._hass_notify_debouncer.async_call())

    @callback
    def async_set_updated_data(self, data: dict) -> None:
        self.data.update(data)
        self.async_notify_listeners(False)


class SatEntityCoordinator(DataUpdateCoordinator):
    def get(self, domain: str, key: str) -> Optional[Any]:
        """Get the value for the given `key` from the boiler data.

        :param domain: Domain of where this value is located.
        :param key: Key of the value to retrieve from the boiler data.
        :return: Value for the given key from the boiler data, or None if the boiler data or the value are not available.
        """
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
