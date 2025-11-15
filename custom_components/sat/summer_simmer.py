from homeassistant.const import UnitOfTemperature
from homeassistant.util.unit_conversion import TemperatureConverter


class SummerSimmer:
    @staticmethod
    def index(temperature: float, humidity: float) -> float | None:
        """
        Calculate the Summer Simmer Index.

        The Summer Simmer Index is a measure of heat and humidity.

        Formula: 1.98 * (F - (0.55 - 0.0055 * H) * (F - 58.0)) - 56.83
        If F < 58, the index is F.

        Returns:
            float: Summer Simmer Index in Celsius.
        """
        # Make sure we have a valid value
        if temperature is None or humidity is None:
            return None

        # Convert temperature to Fahrenheit
        fahrenheit = TemperatureConverter.convert(
            temperature, UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT
        )

        # Calculate Summer Simmer Index
        index = 1.98 * (fahrenheit - (0.55 - 0.0055 * humidity) * (fahrenheit - 58.0)) - 56.83

        # If the temperature is below 58°F, use the temperature as the index
        if fahrenheit < 58:
            index = fahrenheit

        # Convert the result back to Celsius
        return round(TemperatureConverter.convert(index, UnitOfTemperature.FAHRENHEIT, UnitOfTemperature.CELSIUS), 1)

    @staticmethod
    def perception(temperature: float, humidity: float) -> str:
        index = SummerSimmer.index(temperature, humidity)

        if index is None:
            return "Unknown"
        elif index < 21.1:
            return "Cool"
        elif index < 25.0:
            return "Slightly Cool"
        elif index < 28.3:
            return "Comfortable"
        elif index < 32.8:
            return "Slightly Warm"
        elif index < 37.8:
            return "Increasing Discomfort"
        elif index < 44.4:
            return "Extremely Warm"
        elif index < 51.7:
            return "Danger Of Heatstroke"
        elif index < 65.6:
            return "Extreme Danger Of Heatstroke"
        else:
            return "Circulatory Collapse Imminent"
