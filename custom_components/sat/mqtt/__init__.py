import asyncio
import logging
from abc import abstractmethod
from typing import Mapping, Any

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from ..const import CONF_MQTT_TOPIC
from ..coordinator import SatDataUpdateCoordinator

_LOGGER: logging.Logger = logging.getLogger(__name__)

STORAGE_VERSION = 1


class SatMqttCoordinator(SatDataUpdateCoordinator):
    """Base class to manage fetching data using MQTT."""

    def __init__(self, hass: HomeAssistant, device_id: str, config_data: Mapping[str, Any], options: Mapping[str, Any] | None = None) -> None:
        super().__init__(hass, config_data, options)

        self._device_id: str = device_id
        self._topic: str = config_data.get(CONF_MQTT_TOPIC)
        self._store: Store = Store(hass, STORAGE_VERSION, f"sat.mqtt.{device_id}")

    @property
    def device_id(self) -> str:
        return self._device_id

    async def async_setup(self):
        await self._load_stored_data()

    async def async_added_to_hass(self, hass: HomeAssistant) -> None:
        await mqtt.async_wait_for_mqtt_client(hass)

        for key in self.get_tracked_entities():
            await mqtt.async_subscribe(
                self.hass,
                self._get_topic_for_subscription(key),
                self._create_message_handler(key)
            )

        await self.boot()

        await super().async_added_to_hass(hass)

    async def async_will_remove_from_hass(self) -> None:
        # Save the updated data to persistent storage
        await self._save_data()

    async def _load_stored_data(self) -> None:
        """Load the data from persistent storage."""
        if stored_data := await self._store.async_load():
            self.async_set_updated_data({key: value for key, value in stored_data.items() if value not in (None, "")})

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
        """Create a message handler to process incoming MQTT messages."""

        @callback
        def message_handler(message):
            """Handle an incoming MQTT message and schedule an update."""

            try:
                # Process the payload and update the data property
                self._process_message_payload(key, message.payload)
            except Exception as e:
                _LOGGER.error("Failed to process message for key '%s': %s", key, str(e))

        return message_handler

    def _process_message_payload(self, key: str, value):
        """Process and store the payload of a received MQTT message."""
        self.async_set_updated_data({key: value})

    async def _publish_command(self, payload: str, wait_time: float = 1.0):
        """Publish a command to the MQTT topic."""
        topic = self._get_topic_for_publishing()

        _LOGGER.debug("Publishing MQTT command: payload='%s', topic='%s', simulation='%s'", payload, topic, self._simulation)

        if self._simulation:
            return

        try:
            await mqtt.async_publish(hass=self.hass, topic=topic, payload=payload, qos=1)

            # Add a small delay to allow processing of the message
            await asyncio.sleep(wait_time)
        except Exception as error:
            _LOGGER.error("Failed to publish MQTT command. Error: %s", error)
