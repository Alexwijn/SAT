"""Binary Sensor platform for SAT."""
from __future__ import annotations

import logging
import typing

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass, ENTITY_ID_FORMAT
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import async_generate_entity_id
from pyotgw.vars import *

from . import TRANSLATE_SOURCE
from .coordinator import SatSerialCoordinator
from ..const import *
from ..entity import SatEntity

if typing.TYPE_CHECKING:
    pass

_LOGGER = logging.getLogger(__name__)


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


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities):
    """Setup sensor platform."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]
    has_thermostat = coordinator.data[OTGW].get(OTGW_THRM_DETECT) != "D"

    # Create list of entities to be added
    entities = []

    # Iterate through sensor information
    for key, info in BINARY_SENSOR_INFO.items():
        # Check if the sensor should be added based on its availability and thermostat presence
        for source in info.status_sources:
            if source == THERMOSTAT and has_thermostat is False:
                continue

            if coordinator.data[source].get(key) is not None:
                entities.append(SatBinarySensor(coordinator, config_entry, info, key, source))

    # Add all devices
    async_add_entities(entities)


class SatBinarySensor(SatEntity, BinarySensorEntity):
    _attr_should_poll = False

    def __init__(self, coordinator: SatSerialCoordinator, config_entry: ConfigEntry, info: SatBinarySensorInfo, key: str, source: str):
        super().__init__(coordinator, config_entry)

        self.entity_id = async_generate_entity_id(
            ENTITY_ID_FORMAT, f"{config_entry.data.get(CONF_NAME).lower()}_{source}_{key}", hass=coordinator.hass
        )

        self._key = key
        self._source = source
        self._config_entry = config_entry
        self._device_class = info.device_class

        friendly_name_format = info.friendly_name_format
        if TRANSLATE_SOURCE[source] is not None:
            friendly_name_format = f"{info.friendly_name_format} ({TRANSLATE_SOURCE[source]})"

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
