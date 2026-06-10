"""Water heater platform for SAT."""
from __future__ import annotations

import logging
from typing import Any, Optional

from homeassistant.components.water_heater import (
    STATE_GAS,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, STATE_OFF, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SatDataUpdateCoordinator
from .entity import SatEntity
from .entry_data import SatConfig, get_entry_data
from .heating_control import SatHeatingControl

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Add the SAT water heater when the device supports domestic hot water control."""
    if config_entry.entry_id not in hass.data.get(DOMAIN, {}):
        return

    entry_data = get_entry_data(hass, config_entry.entry_id)
    coordinator = entry_data.coordinator

    if not coordinator.supports_hot_water_setpoint_management and not coordinator.supports_hot_water_management:
        return

    async_add_entities([SatWaterHeater(coordinator, entry_data.config, entry_data.heating_control)])


class SatWaterHeater(SatEntity, WaterHeaterEntity):
    _attr_operation_list = [STATE_GAS, STATE_OFF]
    _attr_temperature_unit = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator: SatDataUpdateCoordinator, config: SatConfig, heating_control: SatHeatingControl) -> None:
        super().__init__(coordinator, config, heating_control)

        self._optimistic_enabled: Optional[bool] = None

    @property
    def name(self) -> str:
        return "Hot Water"

    @property
    def unique_id(self) -> str:
        return f"{self._config.entry_id}-water-heater"

    @property
    def supported_features(self) -> WaterHeaterEntityFeature:
        features = WaterHeaterEntityFeature(0)

        if self._coordinator.supports_hot_water_setpoint_management:
            features |= WaterHeaterEntityFeature.TARGET_TEMPERATURE

        if self._coordinator.supports_hot_water_management:
            features |= WaterHeaterEntityFeature.ON_OFF | WaterHeaterEntityFeature.OPERATION_MODE

        return features

    @property
    def _enabled(self) -> bool:
        if (enabled := self._coordinator.hot_water_enabled) is not None:
            return enabled

        if self._optimistic_enabled is not None:
            return self._optimistic_enabled

        return True

    @property
    def current_operation(self) -> str:
        return STATE_GAS if self._enabled else STATE_OFF

    @property
    def target_temperature(self) -> Optional[float]:
        return self._coordinator.hot_water_setpoint

    @property
    def min_temp(self) -> float:
        return self._coordinator.minimum_hot_water_setpoint

    @property
    def max_temp(self) -> float:
        return self._coordinator.maximum_hot_water_setpoint

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return

        await self._coordinator.async_set_control_hot_water_setpoint(float(temperature))
        self.async_write_ha_state()

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        await self._async_set_enabled(operation_mode != STATE_OFF)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set_enabled(False)

    async def _async_set_enabled(self, enabled: bool) -> None:
        self._optimistic_enabled = enabled
        await self._coordinator.async_set_hot_water_enabled(enabled)
        self.async_write_ha_state()
