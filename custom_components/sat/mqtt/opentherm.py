from __future__ import annotations

import logging

from . import SatMqttCoordinator
from ..coordinator import DeviceState
from ..manufacturers.ideal import Ideal
from ..manufacturers.immergas import Immergas
from ..manufacturers.intergas import Intergas

STATE_ON = "ON"

DATA_FLAME_ACTIVE = "flame"
DATA_DHW_SETPOINT = "TdhwSet"
DATA_CONTROL_SETPOINT = "TSet"
DATA_MAXIMUM_CONTROL_SETPOINT = "MaxTSet"
DATA_REL_MOD_LEVEL = "RelModLevel"
DATA_BOILER_TEMPERATURE = "Tboiler"
DATA_RETURN_TEMPERATURE = "Tret"
DATA_DHW_ENABLE = "domestichotwater"
DATA_CENTRAL_HEATING = "centralheating"
DATA_SLAVE_MEMBERID = "slave_memberid_code"
DATA_BOILER_CAPACITY = "MaxCapacityMinModLevel_hb_u8"
DATA_REL_MIN_MOD_LEVEL = "MaxCapacityMinModLevel_lb_u8"
DATA_REL_MIN_MOD_LEVEL_LEGACY = "MaxCapacityMinModLevell_lb_u8"
DATA_MAX_REL_MOD_LEVEL_SETTING = "MaxRelModLevelSetting"
DATA_DHW_SETPOINT_MINIMUM = "TdhwSetUBTdhwSetLB_value_lb"
DATA_DHW_SETPOINT_MAXIMUM = "TdhwSetUBTdhwSetLB_value_hb"

_LOGGER: logging.Logger = logging.getLogger(__name__)


class SatOpenThermMqttCoordinator(SatMqttCoordinator):
    """Class to manage to fetch data from the OTGW Gateway using mqtt."""

    @property
    def device_type(self) -> str:
        return "OpenThermGateway (via mqtt)"

    @property
    def supports_setpoint_management(self):
        return True

    @property
    def supports_hot_water_setpoint_management(self):
        return True

    @property
    def supports_maximum_setpoint_management(self):
        return True

    @property
    def supports_relative_modulation(self):
        return True

    @property
    def device_active(self) -> bool:
        return self.data.get(DATA_CENTRAL_HEATING) == STATE_ON

    @property
    def flame_active(self) -> bool:
        return self.data.get(DATA_FLAME_ACTIVE) == STATE_ON

    @property
    def hot_water_active(self) -> bool:
        return self.data.get(DATA_DHW_ENABLE) == STATE_ON

    @property
    def setpoint(self) -> float | None:
        if (setpoint := self.data.get(DATA_CONTROL_SETPOINT)) is not None:
            return float(setpoint)

        return None

    @property
    def maximum_setpoint_value(self) -> float | None:
        if (setpoint := self.data.get(DATA_MAXIMUM_CONTROL_SETPOINT)) is not None:
            return float(setpoint)

        return super().maximum_setpoint_value

    @property
    def hot_water_setpoint(self) -> float | None:
        if (setpoint := self.data.get(DATA_DHW_SETPOINT)) is not None:
            return float(setpoint)

        return super().hot_water_setpoint

    @property
    def minimum_hot_water_setpoint(self) -> float:
        if (setpoint := self.data.get(DATA_DHW_SETPOINT_MINIMUM)) is not None:
            return float(setpoint)

        return super().minimum_hot_water_setpoint

    @property
    def maximum_hot_water_setpoint(self) -> float:
        if (setpoint := self.data.get(DATA_DHW_SETPOINT_MAXIMUM)) is not None:
            return float(setpoint)

        return super().maximum_hot_water_setpoint

    @property
    def boiler_temperature(self) -> float | None:
        if (value := self.data.get(DATA_BOILER_TEMPERATURE)) is not None:
            return float(value)

        return super().boiler_temperature

    @property
    def return_temperature(self) -> float | None:
        if (value := self.data.get(DATA_RETURN_TEMPERATURE)) is not None:
            return float(value)

        return super().return_temperature

    @property
    def relative_modulation_value(self) -> float | None:
        if (value := self.data.get(DATA_REL_MOD_LEVEL)) is not None:
            return float(value)

        return super().relative_modulation_value

    @property
    def boiler_capacity(self) -> float | None:
        if (value := self.data.get(DATA_BOILER_CAPACITY)) is not None:
            return float(value)

        return super().boiler_capacity

    @property
    def minimum_relative_modulation_value(self) -> float | None:
        if (value := self.data.get(DATA_REL_MIN_MOD_LEVEL)) is not None:
            return float(value)

        # Legacy
        if (value := self.data.get(DATA_REL_MIN_MOD_LEVEL_LEGACY)) is not None:
            return float(value)

        return super().minimum_relative_modulation_value

    @property
    def maximum_relative_modulation_value(self) -> float | None:
        if (value := self.data.get(DATA_MAX_REL_MOD_LEVEL_SETTING)) is not None:
            return float(value)

        return super().maximum_relative_modulation_value

    @property
    def member_id(self) -> int | None:
        if (value := self.data.get(DATA_SLAVE_MEMBERID)) is not None:
            return int(value)

        return None

    async def boot(self) -> None:
        await self._publish_command("PM=3")
        await self._publish_command("PM=15")
        await self._publish_command("PM=48")

        if isinstance(self.manufacturer, (Ideal, Intergas)):
            await self._publish_command("MI=500")

    def get_tracked_entities(self) -> list[str]:
        return [
            DATA_SLAVE_MEMBERID,
            DATA_CENTRAL_HEATING,
            DATA_FLAME_ACTIVE,
            DATA_DHW_ENABLE,
            DATA_DHW_SETPOINT,
            DATA_CONTROL_SETPOINT,
            DATA_REL_MOD_LEVEL,
            DATA_BOILER_TEMPERATURE,
            DATA_RETURN_TEMPERATURE,
            DATA_BOILER_CAPACITY,
            DATA_REL_MIN_MOD_LEVEL,
            DATA_REL_MIN_MOD_LEVEL_LEGACY,
            DATA_MAX_REL_MOD_LEVEL_SETTING,
            DATA_DHW_SETPOINT_MINIMUM,
            DATA_DHW_SETPOINT_MAXIMUM,
        ]

    async def async_set_control_setpoint(self, value: float) -> None:
        await self._publish_command(f"CS={value}")
        await self._publish_command(f"PM=25")

        await super().async_set_control_setpoint(value)

    async def async_set_control_hot_water_setpoint(self, value: float) -> None:
        await self._publish_command(f"SW={value}")

        await super().async_set_control_hot_water_setpoint(value)

    async def async_set_control_thermostat_setpoint(self, value: float) -> None:
        await self._publish_command(f"TC={value}")

        await super().async_set_control_thermostat_setpoint(value)

    async def async_set_heater_state(self, state: DeviceState) -> None:
        await self._publish_command(f"CH={1 if state == DeviceState.ON else 0}")

        await super().async_set_heater_state(state)

    async def async_set_control_max_relative_modulation(self, value: int) -> None:
        if isinstance(self.manufacturer, Immergas):
            await self._publish_command(f"TP=11:12={min(value, 80)}")

        await self._publish_command(f"MM={value}")

        await super().async_set_control_max_relative_modulation(value)

    async def async_set_control_max_setpoint(self, value: float) -> None:
        await self._publish_command(f"SH={value}")

        await super().async_set_control_max_setpoint(value)

    def _get_topic_for_subscription(self, key: str) -> str:
        return f"{self._topic}/value/{self._device_id}/{key}"

    def _get_topic_for_publishing(self) -> str:
        return f"{self._topic}/set/{self._device_id}/command"
