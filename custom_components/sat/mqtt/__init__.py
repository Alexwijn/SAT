import logging
from abc import ABC, abstractmethod
from typing import Mapping, Any

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from ..climate import SatClimate
from ..const import CONF_MQTT_TOPIC
from ..coordinator import SatDataUpdateCoordinator
from ..util import snake_case

_LOGGER: logging.Logger = logging.getLogger(__name__)

STORAGE_VERSION = 1


class SatMqttCoordinator(ABC, SatDataUpdateCoordinator):
    """Base class to manage fetching data using MQTT."""

    def __init__(self, hass: HomeAssistant, device_id: str, data: Mapping[str, Any], options: Mapping[str, Any] | None = None) -> None:
        super().__init__(hass, data, options)

        _LOGGER.debug(snake_case(f"{self.__class__.__name__}"))
        _LOGGER.debug(device_id)

        self.data = {}
        self._device_id = device_id
        self._topic = data.get(CONF_MQTT_TOPIC)
        self._store = Store(hass, STORAGE_VERSION, snake_case(f"{self.__class__.__name__}_{device_id}"))

    @property
    def device_id(self) -> str:
        return self._device_id

    async def async_added_to_hass(self, climate: SatClimate) -> None:
        await mqtt.async_wait_for_mqtt_client(self.hass)

        for key in self.get_tracked_entities():
            await mqtt.async_subscribe(
                self.hass,
                self._get_topic_for_subscription(key),
                self._create_message_handler(key)
            )

        await self.boot()

        await super().async_added_to_hass(climate)

    async def async_notify_listeners(self):
        """Notify listeners of an update asynchronously."""
        # Make sure we do not spam
        self._async_unsub_refresh()
        self._debounced_refresh.async_cancel()

        # Save the updated data to persistent storage
        await self._save_data()

        # Inform the listeners that we are updated
        self.async_update_listeners()

    async def _load_stored_data(self) -> None:
        """Load the data from persistent storage."""
        if stored_data := await self._store.async_load():
            self.data.update(stored_data)

    async def _save_data(self) -> None:
        """Save the data to persistent storage."""
        await self._store.async_save(self.data)

    @abstractmethod
    def get_tracked_entities(self) -> list[str]:
        """Method to be overridden in subclasses to provide specific entities to track."""
        pass

    @abstractmethod
    def _get_topic_for_subscription(self, key: str) -> str:
        """Method to be overridden in subclasses to provide a specific topic for subscribing."""
        pass

    @abstractmethod
    def _get_topic_for_publishing(self) -> str:
        """Method to be overridden in subclasses to provide a specific topic for publishing."""
        pass

    @abstractmethod
    async def boot(self) -> None:
        """Method to be overridden in subclasses to provide specific boot functionality."""
        pass

    def _create_message_handler(self, key: str):
        """Create a message handler to properly schedule updates."""

        @callback
        def message_handler(msg):
            """Handle received MQTT message and schedule data update."""
            _LOGGER.debug(f"Receiving '{key}'='{msg.payload}' from MQTT.")

            # Store the new value
            self.data[key] = msg.payload

            # Schedule the update so our entities are updated
            self.hass.async_create_task(self.async_notify_listeners())

        return message_handler

    async def _publish_command(self, payload: str):
        _LOGGER.debug(f"Publishing '{payload}' to MQTT.")

        if not self._simulation:
            await mqtt.async_publish(self.hass, self._get_topic_for_publishing(), payload)
