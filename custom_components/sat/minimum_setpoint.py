import hashlib
import logging
import time
from typing import List

from custom_components.sat.coordinator import SatDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


def _is_valid(data):
    if not isinstance(data, dict):
        return False

    if not 'value' in data or not isinstance(data['value'], float):
        return False

    if not 'timestamp' in data or not isinstance(data['timestamp'], int):
        return False

    return True


class MinimumSetpoint:
    def __init__(self, coordinator: SatDataUpdateCoordinator):
        self._alpha = 0.2
        self._coordinator = coordinator
        self._adjusted_setpoints = {}

    @staticmethod
    def _get_cache_key(errors: List[float]) -> str:
        errors_str = ','.join(map(str, errors))
        cache_hash = hashlib.sha256(errors_str.encode('utf-8'))
        return cache_hash.hexdigest()

    def restore(self, adjusted_setpoints):
        pass

    def calculate(self, setpoint: float, errors: List[float], adjustment_percentage=10):
        # Check for a valid setpoint
        if setpoint is None:
            return

        # Calculate a cache key for adjusted setpoints
        hash_key = self._get_cache_key(errors)

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

        # Check if we are above configured minimum setpoint, does not make sense if we are below it
        if boiler_temperature <= self._coordinator.minimum_setpoint:
            return

        # Dynamically adjust the minimum setpoint
        adjustment_value = (adjustment_percentage / 100) * (target_setpoint_temperature - boiler_temperature)
        raw_adjusted_setpoint = max(boiler_temperature, target_setpoint_temperature - adjustment_value)

        # Use the moving average to adjust the calculated setpoint
        adjusted_setpoint = raw_adjusted_setpoint
        if hash_key in self._adjusted_setpoints:
            if setpoint in self._adjusted_setpoints[hash_key]:
                adjusted_setpoint = self._alpha * raw_adjusted_setpoint + (1 - self._alpha) * self._adjusted_setpoints[hash_key][setpoint]['value']
        else:
            self._adjusted_setpoints[hash_key] = {}

        # Keep track of the adjusted setpoint and update the timestamp
        self._adjusted_setpoints[hash_key][setpoint] = {
            'errors': errors,
            'timestamp': int(time.time()),
            'value': round(adjusted_setpoint, 1)
        }

    def current(self, errors: List[float]) -> float:
        cache_key = self._get_cache_key(errors)

        if (data := self._adjusted_setpoints.get(cache_key)) is None:
            return self._coordinator.minimum_setpoint + 2

        return min(data.values(), key=lambda x: x['value'])['value']

    @property
    def cache(self) -> dict[str, float]:
        return self._adjusted_setpoints
