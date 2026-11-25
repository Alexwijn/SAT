import asyncio
import json
import logging
from abc import abstractmethod
from typing import Any, Callable, Optional

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .. import SatDataUpdateCoordinator
from ...entry_data import SatConfig

_LOGGER: logging.Logger = logging.getLogger(__name__)

STORAGE_VERSION = 1
PAYLOAD_ONLINE = "online"
PAYLOAD_OFFLINE = "offline"


class SatMqttCoordinator(SatDataUpdateCoordinator):
    """Base class to manage fetching data using MQTT."""

    def __init__(self, hass: HomeAssistant, config: SatConfig) -> None:
        super().__init__(hass, config)

        self._device_id: str = str(self._config.device)
        self._topic: str = self._config.mqtt_topic or ""
        self._online: bool = False
        self._was_offline: bool = False
        self._subscriptions: list[Callable[[], None]] = []
        self._store: Store = Store(hass, STORAGE_VERSION, f"sat.mqtt.{self._device_id}")

    @property
    def id(self) -> str:
        return self._device_id

    @property
    def available(self) -> bool:
        """Whether the gateway is known to be there, which stored and retained values do not prove."""
        return self._online or self._config.simulation.enabled

    @callback
    def _mark_online(self) -> None:
        """Remember that the gateway is there, bringing it up to date when it was offline."""
        self._online = True

        if self._was_offline:
            self._was_offline = False
            self.hass.async_create_task(self.async_resynchronize())

    @callback
    def _handle_availability_message(self, message) -> None:
        """Follow the online and offline announcements of the gateway."""
        payload = str(message.payload).strip().lower()

        if payload == PAYLOAD_ONLINE:
            self._mark_online()

        if payload == PAYLOAD_OFFLINE:
            self._online = False
            self._was_offline = True

    async def async_resynchronize(self) -> None:
        """Repeat what a gateway forgets while it is offline."""
        await self.boot()

    @staticmethod
    def _decode_payload(value: Any) -> Any:
        if isinstance(value, (bytes, bytearray)):
            try:
                value = value.decode()
            except UnicodeDecodeError:
                return value

        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value

        return value

    async def async_setup(self):
        await self._load_stored_data()

    async def async_added_to_hass(self, hass: HomeAssistant) -> None:
        await mqtt.async_wait_for_mqtt_client(hass)

        if (availability_topic := self._get_topic_for_availability()) is not None:
            self._subscriptions.append(await mqtt.async_subscribe(self.hass, availability_topic, self._handle_availability_message))

        for key in self.get_tracked_entities():
            self._subscriptions.append(await mqtt.async_subscribe(
                self.hass,
                self._get_topic_for_subscription(key),
                self._create_message_handler(key)
            ))

        await self.boot()

        await super().async_added_to_hass(hass)

    async def async_will_remove_from_hass(self) -> None:
        for unsubscribe in self._subscriptions:
            unsubscribe()

        self._subscriptions.clear()

        # Save the updated data to persistent storage
        await self._save_data()
        await super().async_will_remove_from_hass()

    async def _load_stored_data(self) -> None:
        """Load the data from persistent storage."""
        if stored_data := await self._store.async_load():
            self.async_set_updated_data(self._restorable(stored_data))

    async def _save_data(self) -> None:
        """Save the data to persistent storage."""
        await self._store.async_save(self._restorable(self.data))

    def _restorable(self, data: dict[str, Any]) -> dict[str, Any]:
        """Return the values that still hold after a restart."""
        return {key: value for key, value in data.items() if key in self.get_restorable_entities() and value not in (None, "")}

    def get_restorable_entities(self) -> list[str]:
        """Return the values that cannot change while Home Assistant is down."""
        return []

    @abstractmethod
    def get_tracked_entities(self) -> list[str]:
        """Method to be overridden in subclasses to provide specific entities to track."""
        pass

    @abstractmethod
    def _get_topic_for_subscription(self, key: str) -> str:
        """Method to be overridden in subclasses to provide a specific topic for subscribing."""
        pass

    def _get_topic_for_availability(self) -> Optional[str]:
        """Return the topic on which the gateway announces that it is online or offline."""
        return None

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

            if not message.retain:
                self._mark_online()

            try:
                # Process the payload and update the data property
                self._process_message_payload(key, message.payload)
            except Exception as e:
                _LOGGER.error("Failed to process message for key '%s': %s", key, str(e))

        return message_handler

    def _process_message_payload(self, key: str, value: Any):
        """Process and store the payload of a received MQTT message."""
        payload = self._decode_payload(value)

        try:
            update = self._normalize_payload(key, payload)
        except Exception as error:  # pragma: no cover - defensive guard
            _LOGGER.error(
                "Failed to normalize MQTT payload for key '%s': %s",
                key,
                error,
            )
            return

        if not update:
            return

        self.async_set_updated_data(update)

    async def _publish_command(self, payload: str, wait_time: float = 0.2, suffix: Optional[str] = None):
        """Publish a command to the MQTT topic."""
        topic = self._build_publish_topic(suffix)

        _LOGGER.debug("Publishing MQTT command: payload='%s', topic='%s', simulation='%s'", payload, topic, self._config.simulation.enabled)

        if self._config.simulation.enabled:
            return

        try:
            await mqtt.async_publish(hass=self.hass, topic=topic, payload=payload, qos=1)

            # Add a small delay to allow processing of the message
            await asyncio.sleep(wait_time)
        except Exception as error:
            _LOGGER.error("Failed to publish MQTT command. Error: %s", error)

    def _build_publish_topic(self, suffix: Optional[str] = None) -> str:
        base_topic = self._get_topic_for_publishing()
        if not suffix:
            return base_topic

        return f"{base_topic}/{suffix}"

    def _normalize_payload(self, key: str, payload: Any) -> dict[str, Any]:
        return {key: payload}
