from __future__ import annotations

import logging
from typing import Any, Optional

from . import SatMqttCoordinator
from ...helpers import float_value
from ...types import DeviceState

_LOGGER: logging.Logger = logging.getLogger(__name__)

COMMAND_PAYLOAD_HEAT = "heat"
COMMAND_PAYLOAD_OFF = "off"

COMMAND_SUFFIX_CH_MODE = "chMode1"
COMMAND_SUFFIX_CH_SETPOINT = "chSetTemp1"
COMMAND_SUFFIX_DHW_SETPOINT = "dwhSetTemp"

DATA_BOILER_CAPACITY = "max_capacity"
DATA_BOILER_TEMPERATURE = "flow_t"
DATA_CENTRAL_HEATING = "ch_mode"
DATA_CONTROL_SETPOINT = "ch_set_t"
DATA_DHW_ENABLE = "dhw_mode"
DATA_DHW_SETPOINT = "dhw_set_t"
DATA_DHW_SETPOINT_MAXIMUM = "dhwMax"
DATA_DHW_SETPOINT_MINIMUM = "dhwMin"
DATA_FLAME_ACTIVE = "flame"
DATA_MAX_REL_MOD_LEVEL_SETTING = "min_modulation"
DATA_REL_MOD_LEVEL = "rel_mod"
DATA_REL_MIN_MOD_LEVEL = "maxModulation"
DATA_RETURN_TEMPERATURE = "return_t"
DATA_SLAVE = "slave"
DATA_SLAVE_MEMBERID = "memberId"
DATA_STATE = "state"
DATA_STATUS = "status"
DATA_THERMOSTAT = "thermostat"
DATA_THERMOSTAT_CH_ENABLE = "ch_enable"
DATA_THERMOSTAT_DHW_ENABLE = "dhw_enable"
TOPIC_STATE = "state"


class SatOtthingMqttCoordinator(SatMqttCoordinator):
    """Coordinator that handles OTthing MQTT telemetry and commands."""

    @property
    def device_type(self) -> str:
        return "OTthing (via mqtt)"

    @property
    def supports_setpoint_management(self) -> bool:
        return True

    @property
    def supports_hot_water_setpoint_management(self) -> bool:
        return True

    @property
    def supports_relative_modulation(self) -> bool:
        return True

    @property
    def supports_relative_modulation_management(self) -> bool:
        return False

    @property
    def device_active(self) -> bool:
        return bool(
            self._thermostat_status().get(DATA_THERMOSTAT_CH_ENABLE)
            or self._slave_status().get(DATA_CENTRAL_HEATING)
        )

    @property
    def flame_active(self) -> bool:
        return bool(self._slave_status().get(DATA_FLAME_ACTIVE))

    @property
    def hot_water_active(self) -> bool:
        return bool(
            self._thermostat_status().get(DATA_THERMOSTAT_DHW_ENABLE)
            or self._slave_status().get(DATA_DHW_ENABLE)
        )

    @property
    def setpoint(self) -> Optional[float]:
        return float_value(self._thermostat().get(DATA_CONTROL_SETPOINT))

    @property
    def hot_water_setpoint(self) -> Optional[float]:
        return float_value(self._thermostat().get(DATA_DHW_SETPOINT))

    @property
    def boiler_temperature(self) -> Optional[float]:
        return float_value(self._slave().get(DATA_BOILER_TEMPERATURE))

    @property
    def return_temperature(self) -> Optional[float]:
        return float_value(self._slave().get(DATA_RETURN_TEMPERATURE))

    @property
    def relative_modulation_value(self) -> Optional[float]:
        return float_value(self._slave().get(DATA_REL_MOD_LEVEL))

    @property
    def hot_water_maximum(self) -> Optional[float]:
        return float_value(self._slave().get(DATA_DHW_SETPOINT_MAXIMUM))

    @property
    def hot_water_minimum(self) -> Optional[float]:
        return float_value(self._slave().get(DATA_DHW_SETPOINT_MINIMUM))

    @property
    def minimum_hot_water_setpoint(self) -> float:
        if (value := self.hot_water_minimum) is not None:
            return value

        return super().minimum_hot_water_setpoint

    @property
    def maximum_hot_water_setpoint(self) -> float:
        if (value := self.hot_water_maximum) is not None:
            return value

        return super().maximum_hot_water_setpoint

    @property
    def boiler_capacity(self) -> Optional[float]:
        return float_value(self._slave().get(DATA_BOILER_CAPACITY))

    @property
    def member_id(self) -> Optional[int]:
        if member_id := self._slave().get(DATA_SLAVE_MEMBERID):
            try:
                return int(member_id)
            except (TypeError, ValueError):
                return None

        return None

    @property
    def minimum_relative_modulation_value(self) -> Optional[float]:
        if (value := float_value(self._slave().get(DATA_REL_MIN_MOD_LEVEL))) is not None:
            return value

        return super().minimum_relative_modulation_value

    @property
    def maximum_relative_modulation_value(self) -> Optional[float]:
        if (value := float_value(self._slave().get(DATA_MAX_REL_MOD_LEVEL_SETTING))) is not None:
            return value

        return super().maximum_relative_modulation_value

    def get_tracked_entities(self) -> list[str]:
        return [DATA_STATE]

    async def boot(self) -> None:
        # Nothing to initialize for OTthing
        pass

    async def async_set_control_setpoint(self, value: float) -> None:
        await self._publish_command(COMMAND_PAYLOAD_HEAT, suffix=COMMAND_SUFFIX_CH_MODE)
        await self._publish_command(self._format_temperature(value), suffix=COMMAND_SUFFIX_CH_SETPOINT)

        await super().async_set_control_setpoint(value)

    async def async_set_control_hot_water_setpoint(self, value: float) -> None:
        await self._publish_command(self._format_temperature(value), suffix=COMMAND_SUFFIX_DHW_SETPOINT)

        await super().async_set_control_hot_water_setpoint(value)

    async def async_set_heater_state(self, state: DeviceState) -> None:
        await self._publish_command(
            COMMAND_PAYLOAD_HEAT if state == DeviceState.ON else COMMAND_PAYLOAD_OFF,
            suffix=COMMAND_SUFFIX_CH_MODE,
        )

        await super().async_set_heater_state(state)

    @staticmethod
    def _format_temperature(value: float) -> str:
        return f"{value:.1f}"

    def _get_topic_for_subscription(self, key: str) -> str:
        return f"{self._topic}/{TOPIC_STATE}"

    def _get_topic_for_publishing(self) -> str:
        return self._topic

    def _normalize_payload(self, key: str, payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            update = {**payload}
            update.setdefault(DATA_SLAVE, {})
            update.setdefault(DATA_THERMOSTAT, {})
            update[DATA_STATE] = payload

            return update

        return super()._normalize_payload(key, payload)

    def _slave(self) -> dict[str, Any]:
        return self.data.get(DATA_SLAVE) or {}

    def _slave_status(self) -> dict[str, Any]:
        return self._slave().get(DATA_STATUS) or {}

    def _thermostat_status(self) -> dict[str, Any]:
        return self._thermostat().get(DATA_STATUS) or {}

    def _thermostat(self) -> dict[str, Any]:
        return self.data.get(DATA_THERMOSTAT) or {}
