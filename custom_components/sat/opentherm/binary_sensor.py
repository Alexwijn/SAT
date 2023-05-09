"""Binary Sensor platform for SAT."""
from __future__ import annotations

import logging
import typing

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass, ENTITY_ID_FORMAT
from homeassistant.components.climate import HVACAction
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import async_generate_entity_id

from .coordinator import SatOpenThermCoordinator
from ..const import *
from ..entity import SatEntity

if typing.TYPE_CHECKING:
    from ..climate import SatClimate

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities):
    """Setup sensor platform."""
    climate = hass.data[DOMAIN][config_entry.entry_id][CLIMATE]
    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]
    has_thermostat = coordinator.data[OTGW].get(OTGW_THRM_DETECT) != "D"

    # Create list of devices to be added
    sensors = [
        SatControlSetpointSynchroSensor(coordinator, climate, config_entry),
        SatCentralHeatingSynchroSensor(coordinator, climate, config_entry),
    ]

    # Iterate through sensor information
    for key, info in BINARY_SENSOR_INFO.items():
        # Check if the sensor should be added based on its availability and thermostat presence
        for source in info.status_sources:
            if source == THERMOSTAT and has_thermostat is False:
                continue

            if coordinator.data[source].get(key) is not None:
                sensors.append(SatBinarySensor(coordinator, config_entry, key, source, info.device_class, info.friendly_name_format))

    # Add all devices
    async_add_entities(sensors)


class SatBinarySensor(SatEntity, BinarySensorEntity):
    _attr_should_poll = False

    def __init__(
            self,
            coordinator: SatOpenThermCoordinator,
            config_entry: ConfigEntry,
            key: str,
            source: str,
            device_class: str,
            friendly_name_format: str
    ):
        super().__init__(coordinator, config_entry)

        self.entity_id = async_generate_entity_id(
            ENTITY_ID_FORMAT, f"{config_entry.data.get(CONF_NAME).lower()}_{source}_{key}", hass=coordinator.hass
        )

        self._key = key
        self._source = source
        self._device_class = device_class
        self._config_entry = config_entry

        if TRANSLATE_SOURCE[source] is not None:
            friendly_name_format = f"{friendly_name_format} ({TRANSLATE_SOURCE[source]})"

        self._friendly_name = friendly_name_format.format(config_entry.data.get(CONF_NAME))

    @property
    def name(self):
        """Return the friendly name of the sensor."""
        return self._friendly_name

    @property
    def device_class(self):
        """Return the device class."""
        return self._device_class

    @property
    def available(self):
        """Return availability of the sensor."""
        return self._coordinator.data is not None and self._coordinator.data[self._source] is not None

    @property
    def is_on(self):
        """Return the state of the device."""
        return self._coordinator.data[self._source].get(self._key)

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME.lower())}-{self._source}-{self._key}"


class SatControlSetpointSynchroSensor(SatEntity, BinarySensorEntity):

    def __init__(self, coordinator, climate: SatClimate, config_entry: ConfigEntry):
        super().__init__(coordinator, config_entry)
        self._climate = climate

    @property
    def name(self):
        """Return the friendly name of the sensor."""
        return "Control Setpoint Synchro"

    @property
    def device_class(self):
        """Return the device class."""
        return BinarySensorDeviceClass.PROBLEM

    @property
    def available(self):
        """Return availability of the sensor."""
        if self._climate is None:
            return False

        if self._coordinator.data is None or self._coordinator.data[BOILER] is None:
            return False

        return True

    @property
    def is_on(self):
        """Return the state of the sensor."""
        boiler_setpoint = float(self._coordinator.data[BOILER].get(DATA_CONTROL_SETPOINT) or 0)
        climate_setpoint = float(self._climate.extra_state_attributes.get("setpoint") or boiler_setpoint)

        return not (
                self._climate.state_attributes.get("hvac_action") != HVACAction.HEATING or
                round(climate_setpoint, 1) == round(boiler_setpoint, 1)
        )

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-control-setpoint-synchro"


class SatCentralHeatingSynchroSensor(SatEntity, BinarySensorEntity):
    def __init__(self, coordinator, climate: SatClimate, config_entry: ConfigEntry) -> None:
        """Initialize the Central Heating Synchro sensor."""
        super().__init__(coordinator, config_entry)
        self._climate = climate

    @property
    def name(self) -> str:
        """Return the friendly name of the sensor."""
        return "Central Heating Synchro"

    @property
    def device_class(self) -> str:
        """Return the device class."""
        return BinarySensorDeviceClass.PROBLEM

    @property
    def available(self) -> bool:
        """Return availability of the sensor."""
        if self._climate is None:
            return False

        if self._coordinator.data is None or self._coordinator.data[BOILER] is None:
            return False

        return True

    @property
    def is_on(self) -> bool:
        """Return the state of the sensor."""
        boiler = self._coordinator.data[BOILER]
        boiler_central_heating = bool(boiler.get(DATA_MASTER_CH_ENABLED))
        climate_hvac_action = self._climate.state_attributes.get("hvac_action")

        return not (
                (climate_hvac_action == HVACAction.OFF and not boiler_central_heating) or
                (climate_hvac_action == HVACAction.IDLE and not boiler_central_heating) or
                (climate_hvac_action == HVACAction.HEATING and boiler_central_heating)
        )

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-central-heating-synchro"


class SatBinarySensorInfo:
    def __init__(self, device_class: typing.Optional[str], friendly_name_format: str, status_sources: typing.List[str]):
        self.device_class = device_class
        self.status_sources = status_sources
        self.friendly_name_format = friendly_name_format


BINARY_SENSOR_INFO: dict[str, SatBinarySensorInfo] = {
    DATA_MASTER_CH_ENABLED: SatBinarySensorInfo(None, "Thermostat Central Heating {}", [BOILER, THERMOSTAT]),
    DATA_MASTER_DHW_ENABLED: SatBinarySensorInfo(None, "Thermostat Hot Water {}", [BOILER, THERMOSTAT]),
    DATA_MASTER_COOLING_ENABLED: SatBinarySensorInfo(None, "Thermostat Cooling {}", [BOILER, THERMOSTAT]),
    DATA_MASTER_OTC_ENABLED: SatBinarySensorInfo(None, "Thermostat Outside Temperature Correction {}", [BOILER, THERMOSTAT]),
    DATA_MASTER_CH2_ENABLED: SatBinarySensorInfo(None, "Thermostat Central Heating 2 {}", [BOILER, THERMOSTAT]),

    DATA_SLAVE_FAULT_IND: SatBinarySensorInfo(BinarySensorDeviceClass.PROBLEM, "Boiler Fault {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_CH_ACTIVE: SatBinarySensorInfo(BinarySensorDeviceClass.HEAT, "Boiler Central Heating {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_DHW_ACTIVE: SatBinarySensorInfo(BinarySensorDeviceClass.HEAT, "Boiler Hot Water {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_FLAME_ON: SatBinarySensorInfo(BinarySensorDeviceClass.HEAT, "Boiler Flame {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_COOLING_ACTIVE: SatBinarySensorInfo(BinarySensorDeviceClass.COLD, "Boiler Cooling {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_CH2_ACTIVE: SatBinarySensorInfo(BinarySensorDeviceClass.HEAT, "Boiler Central Heating 2 {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_DIAG_IND: SatBinarySensorInfo(BinarySensorDeviceClass.PROBLEM, "Boiler Diagnostics {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_DHW_PRESENT: SatBinarySensorInfo(None, "Boiler Hot Water Present {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_CONTROL_TYPE: SatBinarySensorInfo(None, "Boiler Control Type {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_COOLING_SUPPORTED: SatBinarySensorInfo(None, "Boiler Cooling Support {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_DHW_CONFIG: SatBinarySensorInfo(None, "Boiler Hot Water Configuration {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_MASTER_LOW_OFF_PUMP: SatBinarySensorInfo(None, "Boiler Pump Commands Support {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_CH2_PRESENT: SatBinarySensorInfo(None, "Boiler Central Heating 2 Present {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_SERVICE_REQ: SatBinarySensorInfo(BinarySensorDeviceClass.PROBLEM, "Boiler Service Required {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_REMOTE_RESET: SatBinarySensorInfo(None, "Boiler Remote Reset Support {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_LOW_WATER_PRESS: SatBinarySensorInfo(BinarySensorDeviceClass.PROBLEM, "Boiler Low Water Pressure {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_GAS_FAULT: SatBinarySensorInfo(BinarySensorDeviceClass.PROBLEM, "Boiler Gas Fault {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_AIR_PRESS_FAULT: SatBinarySensorInfo(BinarySensorDeviceClass.PROBLEM, "Boiler Air Pressure Fault {}", [BOILER, THERMOSTAT]),
    DATA_SLAVE_WATER_OVERTEMP: SatBinarySensorInfo(BinarySensorDeviceClass.PROBLEM, "Boiler Water Over-temperature {}", [BOILER, THERMOSTAT]),
    DATA_REMOTE_TRANSFER_DHW: SatBinarySensorInfo(None, "Remote Hot Water Setpoint Transfer Support {}", [BOILER, THERMOSTAT]),
    DATA_REMOTE_TRANSFER_MAX_CH: SatBinarySensorInfo(None, "Remote Maximum Central Heating Setpoint Write Support {}", [BOILER, THERMOSTAT]),
    DATA_REMOTE_RW_DHW: SatBinarySensorInfo(None, "Remote Hot Water Setpoint Write Support {}", [BOILER, THERMOSTAT]),
    DATA_REMOTE_RW_MAX_CH: SatBinarySensorInfo(None, "Remote Central Heating Setpoint Write Support {}", [BOILER, THERMOSTAT]),
    DATA_ROVRD_MAN_PRIO: SatBinarySensorInfo(None, "Remote Override Manual Change Priority {}", [BOILER, THERMOSTAT]),
    DATA_ROVRD_AUTO_PRIO: SatBinarySensorInfo(None, "Remote Override Program Change Priority {}", [BOILER, THERMOSTAT]),

    OTGW_GPIO_A_STATE: SatBinarySensorInfo(None, "Gateway GPIO A {}", [OTGW]),
    OTGW_GPIO_B_STATE: SatBinarySensorInfo(None, "Gateway GPIO B {}", [OTGW]),
    OTGW_IGNORE_TRANSITIONS: SatBinarySensorInfo(None, "Gateway Ignore Transitions {}", [OTGW]),
    OTGW_OVRD_HB: SatBinarySensorInfo(None, "Gateway Override High Byte {}", [OTGW]),
}
