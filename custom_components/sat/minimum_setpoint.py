import hashlib
from typing import List

from custom_components.sat.coordinator import SatDataUpdateCoordinator


class MinimumSetpoint:
    @staticmethod
    def _get_cache_key(errors: List[float]) -> str:
        errors_str = ','.join(map(str, errors))
        cache_hash = hashlib.sha256(errors_str.encode('utf-8'))
        return cache_hash.hexdigest()

    def __init__(self, coordinator: SatDataUpdateCoordinator):
        self._alpha = 0.2
        self._coordinator = coordinator
        self._adjusted_setpoints = {}

    def restore(self, adjusted_setpoints):
        self._adjusted_setpoints = adjusted_setpoints

    def calculate(self, errors, adjustment_percentage=10):
        # Calculate a hash key
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

        # Determine the minimum setpoint based on flame state and adjustment
        raw_adjusted_setpoint = max(boiler_temperature, target_setpoint_temperature - adjustment_percentage)

        # Use the moving average to adjust the calculated setpoint
        adjusted_setpoint = raw_adjusted_setpoint
        if hash_key in self._adjusted_setpoints:
            adjusted_setpoint = self._alpha * raw_adjusted_setpoint + (1 - self._alpha) * self._adjusted_setpoints[hash_key]

        # Keep track of the adjusted setpoint for the current target setpoint
        self._adjusted_setpoints[hash_key] = round(adjusted_setpoint, 1)

    def current(self, errors) -> float:
        # Get the cache key
        cache_key = self._get_cache_key(errors)

        # Return the adjusted setpoint if available, else return the configured minimum setpoint
        return self._adjusted_setpoints.get(cache_key, self._coordinator.minimum_setpoint)

    def cache(self) -> dict[str, float]:
        return self._adjusted_setpoints
