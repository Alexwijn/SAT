import hashlib
import logging
import time
from datetime import timedelta
from typing import List

from custom_components.sat.coordinator import SatDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


def _is_valid(data):
    _LOGGER.debug(data)
    if not isinstance(data, dict):
        return False

    if not 'value' in data or not isinstance(data['value'], float):
        _LOGGER.debug("Value not found")
        return False

    if not 'timestamp' in data or not isinstance(data['timestamp'], int):
        _LOGGER.debug("Timestamp not found")
        return False

    return True


class MinimumSetpoint:
    def __init__(self, coordinator: SatDataUpdateCoordinator):
        self._alpha = 0.2
        self._coordinator = coordinator
        self._adjusted_setpoints = {}

    @staticmethod
    def _get_cache_key(setpoint: float, errors: List[float]) -> str:
        errors_str = str(setpoint) + ','.join(map(str, errors))
        cache_hash = hashlib.sha256(errors_str.encode('utf-8'))
        return cache_hash.hexdigest()

    def restore(self, adjusted_setpoints):
        self._adjusted_setpoints = adjusted_setpoints

    def calculate(self, setpoint: float, errors: List[float], adjustment_percentage=10):
        # Check for a valid setpoint
        if setpoint is None:
            return

        # Calculate a cache key for adjusted setpoints
        hash_key = self._get_cache_key(setpoint, errors)

        # Cleanup old setpoints
        self._cleanup_old_setpoints()

        # Extract relevant values from the coordinator for clarity
        boiler_temperature = self._coordinator.boiler_temperature
        target_setpoint_temperature = self._coordinator.setpoint
        is_flame_active = self._coordinator.flame_active

        # Check for None values
        if boiler_temperature is None or target_setpoint_temperature is None:
            return

        # Check for flame activity and if we are stable
        if not is_flame_active or abs(target_setpoint_temperature - boiler_temperature) <= 1:
            return

        # Determine the minimum setpoint based on flame state and adjustment
        raw_adjusted_setpoint = max(boiler_temperature, target_setpoint_temperature - adjustment_percentage)

        # Use the moving average to adjust the calculated setpoint
        adjusted_setpoint = raw_adjusted_setpoint
        if hash_key in self._adjusted_setpoints:
            adjusted_setpoint = self._alpha * raw_adjusted_setpoint + (1 - self._alpha) * self._adjusted_setpoints[hash_key]['value']

        # Keep track of the adjusted setpoint and update the timestamp
        self._adjusted_setpoints[hash_key] = {'value': round(adjusted_setpoint, 1), 'setpoint': setpoint, 'errors': errors,
                                              'timestamp': int(time.time())}

    def current(self, setpoint: float, errors: List[float]) -> float:
        # Get the cache key
        cache_key = self._get_cache_key(setpoint, errors)

        # Return the adjusted setpoint if available, else return the configured minimum setpoint
        return self._adjusted_setpoints.get(cache_key, {'value': self._coordinator.minimum_setpoint})['value']

    @property
    def cache(self) -> dict[str, float]:
        return self._adjusted_setpoints

    def _cleanup_old_setpoints(self):
        outdated_keys = [
            key
            for key, data in self._adjusted_setpoints.items()
            if (not _is_valid(data) or (int(time.time()) - data['timestamp']) > timedelta(days=7).total_seconds())
        ]

        _LOGGER.debug(outdated_keys)

        for key in outdated_keys:
            del self._adjusted_setpoints[key]
