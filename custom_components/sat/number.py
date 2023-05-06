from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_MODE, MODE_OPENTHERM
from .opentherm import number


async def async_setup_entry(_hass: HomeAssistant, _config_entry: ConfigEntry, _async_add_entities: AddEntitiesCallback):
    if _config_entry.data.get(CONF_MODE) == MODE_OPENTHERM:
        await number.async_setup_entry(_hass, _config_entry, _async_add_entities)
