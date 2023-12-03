from custom_components.sat.coordinator import SatDataUpdateCoordinator


class MinimumSetpoint:
    def __init__(self, coordinator: SatDataUpdateCoordinator):
        self._alpha = 0.2
        self._coordinator = coordinator
        self._previous_minimum_setpoint = None

    def calculate(self, adjustment_percentage=10) -> float:
        # Extract relevant values from the coordinator for clarity
        boiler_temperature = self._coordinator.boiler_temperature
        target_setpoint_temperature = self._coordinator.setpoint
        minimum_setpoint = self._coordinator.minimum_setpoint
        is_flame_active = self._coordinator.flame_active

        # Check if either boiler_temperature or target_setpoint_temperature is None
        if boiler_temperature is None or target_setpoint_temperature is None:
            return minimum_setpoint

        # Check if the boiler temperature is stable at the target temperature
        is_temperature_stable = abs(boiler_temperature - target_setpoint_temperature) <= 1

        if is_temperature_stable:
            # Boiler temperature is stable, return the coordinator's minimum setpoint
            return minimum_setpoint

        # Calculate the adjustment value based on the specified percentage
        adjustment_value = (adjustment_percentage / 100) * (target_setpoint_temperature - boiler_temperature)

        # Determine the minimum setpoint based on flame state and adjustment
        adjusted_setpoint = max(boiler_temperature, target_setpoint_temperature - adjustment_value) if is_flame_active else minimum_setpoint

        # Update the exponential moving average
        if self._previous_minimum_setpoint is None:
            # If it's the first observation, set the EMA to the current value
            ema = adjusted_setpoint
        else:
            # Update the EMA using the smoothing factor (alpha)
            ema = self._alpha * adjusted_setpoint + (1 - self._alpha) * self._previous_minimum_setpoint

        # Keep track of the current EMA for future calculations
        self._previous_minimum_setpoint = ema

    def current(self) -> float:
        if self._previous_minimum_setpoint is None:
            return self._coordinator.minimum_setpoint

        return self._previous_minimum_setpoint
