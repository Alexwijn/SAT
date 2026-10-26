from __future__ import annotations

import logging
from typing import Any, Optional

from . import SatMqttCoordinator
from ...const import MINIMUM_SETPOINT
from ...helpers import float_value
from ...types import HeaterState

DATA_ON = "on"
DATA_OFF = "off"

DATA_BOILER_DATA = "boiler_data"
DATA_FLAME_ACTIVE = "burngas"
DATA_DHW_SETPOINT = "dhw/seltemp"
DATA_CONTROL_SETPOINT = "selflowtemp"
DATA_REL_MOD_LEVEL = "curburnpow"
DATA_BOILER_TEMPERATURE = "curflowtemp"
DATA_RETURN_TEMPERATURE = "rettemp"

DATA_DHW_ACTIVE = "tapwateractive"
DATA_DHW_ENABLE = "tapactivated"
DATA_CENTRAL_HEATING = "heatingactive"
DATA_BOILER_CAPACITY = "nompower"

DATA_REL_MIN_MOD_LEVEL = "burnminpower"
DATA_MAX_REL_MOD_LEVEL_SETTING = "burnmaxpower"
DATA_SERVICE_CODE_NUMBER = "servicecodenumber"

DEFAULT_MINIMUM_POWER = 20

_LOGGER: logging.Logger = logging.getLogger(__name__)


class SatEmsMqttCoordinator(SatMqttCoordinator):
    """Class to manage to fetch data from the OTGW Gateway using MQTT."""

    @property
    def type(self) -> str:
        return "Energy Management System (via mqtt)"

    @property
    def supports_setpoint_management(self) -> bool:
        return True

    @property
    def supports_hot_water_setpoint_management(self) -> bool:
        return True

    @property
    def supports_maximum_setpoint_management(self) -> bool:
        return True

    @property
    def supports_relative_modulation(self) -> bool:
        return True

    @property
    def active(self) -> bool:
        return self.data.get(DATA_CENTRAL_HEATING) == DATA_ON

    @property
    def flame_active(self) -> bool:
        return self.data.get(DATA_FLAME_ACTIVE) == DATA_ON

    @property
    def hot_water_active(self) -> bool:
        for key in (DATA_DHW_ACTIVE, f"dhw/{DATA_DHW_ACTIVE}", DATA_DHW_ENABLE, f"dhw/{DATA_DHW_ENABLE}"):
            if (value := self.data.get(key)) is not None:
                return value == DATA_ON

        return False

    @property
    def supports_hot_water_management(self) -> bool:
        return True

    @property
    def fault_code(self) -> Optional[int]:
        if (code := float_value(self.data.get(DATA_SERVICE_CODE_NUMBER))) is None:
            return None

        return int(code)

    @property
    def hot_water_enabled(self) -> Optional[bool]:
        for key in (DATA_DHW_ENABLE, f"dhw/{DATA_DHW_ENABLE}"):
            if (value := self.data.get(key)) is not None:
                return value == DATA_ON

        return None

    @property
    def setpoint(self) -> Optional[float]:
        if (value := float_value(self.data.get(DATA_CONTROL_SETPOINT))) is None:
            return None

        if value == 0:
            return MINIMUM_SETPOINT

        if self._requested_setpoint is not None and round(self._requested_setpoint) == round(value):
            return self._requested_setpoint

        return value

    @property
    def hot_water_setpoint(self) -> Optional[float]:
        return float_value(self.data.get(DATA_DHW_SETPOINT))

    @property
    def boiler_temperature(self) -> Optional[float]:
        return float_value(self.data.get(DATA_BOILER_TEMPERATURE))

    @property
    def return_temperature(self) -> Optional[float]:
        return float_value(self.data.get(DATA_RETURN_TEMPERATURE))

    @property
    def relative_modulation_value(self) -> Optional[float]:
        return float_value(self.data.get(DATA_REL_MOD_LEVEL))

    @property
    def boiler_capacity(self) -> Optional[float]:
        return float_value(self.data.get(DATA_BOILER_CAPACITY))

    @property
    def minimum_relative_modulation_value(self) -> Optional[float]:
        return float_value(self.data.get(DATA_REL_MIN_MOD_LEVEL))

    @property
    def maximum_relative_modulation_value(self) -> Optional[float]:
        return float_value(self.data.get(DATA_MAX_REL_MOD_LEVEL_SETTING))

    @property
    def member_id(self) -> Optional[int]:
        # Not supported (yet)
        return None

    async def boot(self) -> None:
        # Nothing needs to be booted (yet)
        pass

    def get_tracked_entities(self) -> list[str]:
        return [DATA_BOILER_DATA]

    async def async_set_control_setpoint(self, value: float) -> None:
        await self._publish_command(f'{{"cmd": "selflowtemp", "value": {0 if value <= MINIMUM_SETPOINT else int(round(value))}}}')

        await super().async_set_control_setpoint(value)

    async def async_set_control_hot_water_setpoint(self, value: float) -> None:
        await self._publish_command(f'{{"cmd": "dhw/seltemp", "value": {value}}}')

        await super().async_set_control_hot_water_setpoint(value)

    async def async_set_hot_water_enabled(self, enabled: bool) -> None:
        await self._publish_command(f'{{"cmd": "dhw/{DATA_DHW_ENABLE}", "value": "{DATA_ON if enabled else DATA_OFF}"}}')

        await super().async_set_hot_water_enabled(enabled)

    async def async_set_control_thermostat_setpoint(self, value: float) -> None:
        # Not supported (yet)
        await super().async_set_control_thermostat_setpoint(value)

    async def async_set_heater_state(self, state: HeaterState) -> None:
        """Heat demand on EMS boilers follows the flow setpoint alone."""
        await super().async_set_heater_state(state)

    async def async_set_control_max_relative_modulation(self, value: int) -> None:
        minimum_power = self.minimum_relative_modulation_value or DEFAULT_MINIMUM_POWER
        await self._publish_command(f'{{"cmd": "burnmaxpower", "value": {max(value, int(minimum_power))}}}')

        await super().async_set_control_max_relative_modulation(value)

    async def async_set_control_max_setpoint(self, value: float) -> None:
        await self._publish_command(f'{{"cmd": "heatingtemp", "value": {value}}}')

        await super().async_set_control_max_setpoint(value)

    def _get_topic_for_subscription(self, key: str) -> str:
        return f"{self._topic}/{key}"

    def _get_topic_for_publishing(self) -> str:
        return f"{self._topic}/boiler"

    def _normalize_payload(self, key: str, payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            normalized: dict[str, Any] = {}

            for name, value in payload.items():
                if isinstance(value, dict):
                    for nested_name, nested_value in value.items():
                        normalized[f"{name}/{nested_name}"] = nested_value
                else:
                    normalized[name] = value

            return normalized

        return {key: payload}
