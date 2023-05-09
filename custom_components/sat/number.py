from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_MODE, MODE_OPENTHERM, OPTIONS_DEFAULTS
from .opentherm import number as opentherm_number


async def async_setup_entry(_hass: HomeAssistant, _config_entry: ConfigEntry, _async_add_entities: AddEntitiesCallback):
    """
    Add sensors for the OpenTherm protocol if the integration is set to use it.
    """

    # Retrieve the defaults and override it with the user options
    options = OPTIONS_DEFAULTS.copy()
    options.update(_config_entry.data)

    # Check if integration is set to use the OpenTherm protocol
    if options.get(CONF_MODE) == MODE_OPENTHERM:
        # Call function to set up OpenTherm numbers
        await opentherm_number.async_setup_entry(_hass, _config_entry, _async_add_entities)
