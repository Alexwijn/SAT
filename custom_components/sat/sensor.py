from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_MODE, MODE_OPENTHERM
from .opentherm import sensor as opentherm_sensor


async def async_setup_entry(_hass: HomeAssistant, _config_entry: ConfigEntry, _async_add_entities: AddEntitiesCallback):
    """
    Add sensors for the OpenTherm protocol if the integration is set to use it.
    """

    # Check if integration is set to use the OpenTherm protocol
    if _config_entry.data.get(CONF_MODE) == MODE_OPENTHERM:
        # Call function to set up OpenTherm sensors
        await opentherm_sensor.async_setup_entry(_hass, _config_entry, _async_add_entities)
