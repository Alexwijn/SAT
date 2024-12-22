from __future__ import annotations

import json
import logging

from . import SatMqttCoordinator
from ..coordinator import DeviceState
from ..util import float_value

DATA_ON = "on"
DATA_OFF = "off"

DATA_BOILER_DATA = "boiler_data"
DATA_FLAME_ACTIVE = "burngas"
DATA_DHW_SETPOINT = "dhw/seltemp"
DATA_CONTROL_SETPOINT = "selflowtemp"
DATA_REL_MOD_LEVEL = "curburnpow"
DATA_BOILER_TEMPERATURE = "curflowtemp"
DATA_RETURN_TEMPERATURE = "rettemp"

DATA_DHW_ENABLE = "tapactivated"
DATA_CENTRAL_HEATING = "heatingactive"
DATA_BOILER_CAPACITY = "nompower"

DATA_REL_MIN_MOD_LEVEL = "burnminpower"
DATA_MAX_REL_MOD_LEVEL_SETTING = "burnmaxpower"

_LOGGER: logging.Logger = logging.getLogger(__name__)


class SatEmsMqttCoordinator(SatMqttCoordinator):
    """Class to manage fetching data from the OTGW Gateway using MQTT."""

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
    def supports_relative_modulation_management(self) -> bool:
        return True

    @property
    def device_active(self) -> bool:
        return self.data.get(DATA_CENTRAL_HEATING) == DATA_ON

    @property
    def flame_active(self) -> bool:
        return self.data.get(DATA_FLAME_ACTIVE) == DATA_ON

    @property
    def hot_water_active(self) -> bool:
        return self.data.get(DATA_DHW_ENABLE) == DATA_ON

    @property
    def setpoint(self) -> float | None:
        return float_value(self.data.get(DATA_CONTROL_SETPOINT))

    @property
    def hot_water_setpoint(self) -> float | None:
        return float_value(self.data.get(DATA_DHW_SETPOINT))

    @property
    def boiler_temperature(self) -> float | None:
        return float_value(self.data.get(DATA_BOILER_TEMPERATURE))

    @property
    def return_temperature(self) -> float | None:
        return float_value(self.data.get(DATA_RETURN_TEMPERATURE))

    @property
    def relative_modulation_value(self) -> float | None:
        return float_value(self.data.get(DATA_REL_MOD_LEVEL))

    @property
    def boiler_capacity(self) -> float | None:
        return float_value(self.data.get(DATA_BOILER_CAPACITY))

    @property
    def minimum_relative_modulation_value(self) -> float | None:
        return float_value(self.data.get(DATA_REL_MIN_MOD_LEVEL))

    @property
    def maximum_relative_modulation_value(self) -> float | None:
        return float_value(self.data.get(DATA_MAX_REL_MOD_LEVEL_SETTING))

    @property
    def member_id(self) -> int | None:
        # Not supported (yet)
        return None

    async def boot(self) -> SatMqttCoordinator:
        # Nothing needs to be booted (yet)
        return self

    def get_tracked_entities(self) -> list[str]:
        return [DATA_BOILER_DATA]

    async def async_set_control_setpoint(self, value: float) -> None:
        await self._publish_command(f'{{"cmd": "selflowtemp", "value": {0 if value == 10 else value}}}')

        await super().async_set_control_setpoint(value)

    async def async_set_control_hot_water_setpoint(self, value: float) -> None:
        await self._publish_command(f'{{"cmd": "dhw/seltemp", "value": {value}}}')

        await super().async_set_control_hot_water_setpoint(value)

    async def async_set_control_thermostat_setpoint(self, value: float) -> None:
        # Not supported (yet)
        await super().async_set_control_thermostat_setpoint(value)

    async def async_set_heater_state(self, state: DeviceState) -> None:
        if (state == DeviceState.ON) != self.device_active:
            await self._publish_command(f'{{"cmd": "heatingoff", "value": "{DATA_OFF if state == DeviceState.ON else DATA_ON}"}}')

        await super().async_set_heater_state(state)

    async def async_set_control_max_relative_modulation(self, value: int) -> None:
        await self._publish_command(f'{{"cmd": "burnmaxpower", "value": {min(value, 20)}}}')

        await super().async_set_control_max_relative_modulation(value)

    async def async_set_control_max_setpoint(self, value: float) -> None:
        await self._publish_command(f'{{"cmd": "heatingtemp", "value": {value}}}')

        await super().async_set_control_max_setpoint(value)

    def _get_topic_for_subscription(self, key: str) -> str:
        return f"{self._topic}/{key}"

    def _get_topic_for_publishing(self) -> str:
        return f"{self._topic}/boiler"

    def _process_message_payload(self, key: str, payload):
        try:
            self.data = json.loads(payload)
        except json.JSONDecodeError as error:
            _LOGGER.error("Failed to decode JSON payload: %s. Error: %s", payload, error)
