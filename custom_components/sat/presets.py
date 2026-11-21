"""Preset temperatures per room."""
from __future__ import annotations

from typing import Optional

from homeassistant.components.climate import PRESET_ACTIVITY, PRESET_AWAY, PRESET_COMFORT, PRESET_HOME, PRESET_NONE, PRESET_SLEEP

from .const import CONF_ACTIVITY_TEMPERATURE, CONF_AWAY_TEMPERATURE, CONF_COMFORT_TEMPERATURE, CONF_HOME_TEMPERATURE, CONF_SLEEP_TEMPERATURE
from .entry_data import PresetConfig

PRESET_TEMPERATURE_KEYS: dict[str, str] = {
    PRESET_HOME: CONF_HOME_TEMPERATURE,
    PRESET_AWAY: CONF_AWAY_TEMPERATURE,
    PRESET_SLEEP: CONF_SLEEP_TEMPERATURE,
    PRESET_COMFORT: CONF_COMFORT_TEMPERATURE,
    PRESET_ACTIVITY: CONF_ACTIVITY_TEMPERATURE,
}


def preset_temperatures(config: PresetConfig) -> dict[str, float]:
    """Return the house-wide temperature of every preset."""
    return {preset: float(config.presets[key]) for preset, key in PRESET_TEMPERATURE_KEYS.items()}


class RoomPresets:
    """Decide what a room is set to under a preset."""

    def __init__(self, config: PresetConfig) -> None:
        self._house = preset_temperatures(config)
        self._rooms = config.rooms

    def target(self, room_id: str, preset: str) -> Optional[float]:
        """Return the target a room gets under a preset, or None when the room is left alone."""
        room = self._rooms.get(room_id)

        if room is not None and room.fixed:
            return None

        preset = PRESET_HOME if preset == PRESET_NONE else preset
        temperatures = room.temperatures if room is not None else {}

        if (temperature := temperatures.get(preset)) is not None:
            return temperature

        home_target = temperatures.get(PRESET_HOME, self._house[PRESET_HOME])

        delta = self._house[preset] - self._house[PRESET_HOME]
        setback = min(home_target, home_target + delta)
        floor = min(self._house[preset], home_target)

        return round(max(setback, floor), 1)
