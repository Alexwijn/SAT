from __future__ import annotations

import logging
import time
from typing import Any, Optional

from homeassistant.core import HomeAssistant

from . import SatMqttCoordinator
from ...const import MINIMUM_SETPOINT
from ...entry_data import SatConfig
from ...helpers import float_value
from ...types import HeaterState

DATA_ON = "on"
DATA_OFF = "off"
TRUE_VALUES = (DATA_ON, "ON", True, 1, "1")

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

SETPOINT_HEARTBEAT_INTERVAL = 60  # 60s keepalive to prevent EMS-ESP fallback timeout (~240s) to 0

_LOGGER: logging.Logger = logging.getLogger(__name__)


class SatEmsMqttCoordinator(SatMqttCoordinator):
    """Class to manage to fetch data from the OTGW Gateway using MQTT."""

    def __init__(self, hass: HomeAssistant, config: SatConfig) -> None:
        super().__init__(hass, config)

        self._last_published_control_setpoint: Optional[int] = None
        self._last_published_control_setpoint_time: float = 0.0
        self._last_control_setpoint_value: Optional[float] = None
        self._last_published_max_relative_modulation: Optional[int] = None
        self._last_published_hot_water_setpoint: Optional[float] = None
        self._last_published_max_setpoint: Optional[float] = None

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
        return self.data.get(DATA_CENTRAL_HEATING) in TRUE_VALUES

    @property
    def flame_active(self) -> bool:
        return self.data.get(DATA_FLAME_ACTIVE) in TRUE_VALUES

    @property
    def hot_water_active(self) -> bool:
        if any(k in self.data for k in ("tapwateractive", "dhwcharging", "dhw_charging")):
            return (
                self.data.get("tapwateractive") in TRUE_VALUES
                or self.data.get("dhwcharging") in TRUE_VALUES
                or self.data.get("dhw_charging") in TRUE_VALUES
            )
        return self.data.get(DATA_DHW_ENABLE) in TRUE_VALUES

    @property
    def setpoint(self) -> Optional[float]:
        val = float_value(self.data.get(DATA_CONTROL_SETPOINT))
        if val is None:
            return None
        if val == 0:
            return MINIMUM_SETPOINT

        # EMS bus telegrams have a 1°C integer resolution for boiler flow setpoint.
        # If the reported boiler setpoint matches the integer-rounded value of our
        # last requested setpoint, report the requested value back to SAT to prevent
        # float quantisation mismatches (e.g. 51.9°C requested vs 52°C boiler) from
        # tripping the Control Setpoint Synchronization sensor.
        if (
            self._last_control_setpoint_value is not None
            and round(self._last_control_setpoint_value) == round(val)
        ):
            return self._last_control_setpoint_value

        return val

    @property
    def hot_water_setpoint(self) -> Optional[float]:
        dhw_data = self.data.get("dhw")
        if isinstance(dhw_data, dict):
            return float_value(dhw_data.get("seltemp") or dhw_data.get("settemp"))
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
        self._last_control_setpoint_value = value
        target = 0 if value <= 10 else int(round(value))
        now = time.monotonic()

        # If the boiler is actively producing Domestic Hot Water (ACS),
        # do not interfere with the flow temperature setpoint!
        if self.hot_water_active:
            await super().async_set_control_setpoint(value)
            return

        raw_val = float_value(self.data.get(DATA_CONTROL_SETPOINT))
        raw_boiler_setpoint = int(round(raw_val)) if raw_val is not None else None
        should_publish = (
            self._last_published_control_setpoint is None
            or self._last_published_control_setpoint != target
            or (raw_boiler_setpoint is not None and raw_boiler_setpoint != target)
            or (target > 0 and now - self._last_published_control_setpoint_time >= SETPOINT_HEARTBEAT_INTERVAL)
        )

        if should_publish:
            await self._publish_command(f'{{"cmd": "selflowtemp", "value": {target}}}')
            self._last_published_control_setpoint = target
            self._last_published_control_setpoint_time = now

        await super().async_set_control_setpoint(value)

    async def async_set_control_hot_water_setpoint(self, value: float) -> None:
        current_dhw = self.hot_water_setpoint
        should_publish = (
            self._last_published_hot_water_setpoint is None
            or self._last_published_hot_water_setpoint != value
            or (current_dhw is not None and current_dhw != value)
        )

        if should_publish:
            await self._publish_command(f'{{"cmd": "dhw/seltemp", "value": {value}}}')
            self._last_published_hot_water_setpoint = value

        await super().async_set_control_hot_water_setpoint(value)

    async def async_set_control_thermostat_setpoint(self, value: float) -> None:
        # Not supported (yet)
        await super().async_set_control_thermostat_setpoint(value)

    async def async_set_heater_state(self, state: HeaterState) -> None:
        # On EMS-ESP / Bosch / Buderus boilers, central heating demand is controlled
        # purely via the 'selflowtemp' setpoint (>0 for heat demand, 0 for off).
        # We must NOT toggle 'heatingactivated' here, as that represents the boiler's
        # seasonal mode (Summer/Winter). Toggling it on every cycle trips the boiler's
        # 15-minute Siphon Filling Program (Siphonfüllprogramm), locking power to 14%.
        await super().async_set_heater_state(state)

    async def async_set_control_max_relative_modulation(self, value: int) -> None:
        target = max(value, 20)
        if self._last_published_max_relative_modulation != target:
            await self._publish_command(f'{{"cmd": "burnmaxpower", "value": {target}}}')
            self._last_published_max_relative_modulation = target
            self.data[DATA_MAX_REL_MOD_LEVEL_SETTING] = target

        await super().async_set_control_max_relative_modulation(value)

    async def async_set_control_max_setpoint(self, value: float) -> None:
        if self._last_published_max_setpoint != value:
            await self._publish_command(f'{{"cmd": "heatingtemp", "value": {value}}}')
            self._last_published_max_setpoint = value

        await super().async_set_control_max_setpoint(value)

    def _get_topic_for_subscription(self, key: str) -> str:
        return f"{self._topic}/{key}"

    def _get_topic_for_publishing(self) -> str:
        return f"{self._topic}/boiler"

    def _normalize_payload(self, key: str, payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            return payload

        return {key: payload}
