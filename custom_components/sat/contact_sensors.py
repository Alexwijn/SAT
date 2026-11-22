"""Window and door contacts that pause the heating."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant
from homeassistant.helpers.event import async_track_state_change_event

from .entry_data import SatConfig

_LOGGER = logging.getLogger(__name__)


class ContactSensors:
    """Tell when a contact opens or closes, and whether it stays open long enough."""

    def __init__(self, config: SatConfig) -> None:
        self._entity_ids = list(config.window_sensors)
        self._open_delay = config.sensors.window_minimum_open_time_seconds

        self._opening: Optional[asyncio.Task[None]] = None
        self._listener: Optional[Callable[[bool], Awaitable[None]]] = None

    def track(self, hass: HomeAssistant, listener: Callable[[bool], Awaitable[None]]) -> CALLBACK_TYPE:
        """Call the listener whenever a contact opens or closes."""
        self._listener = listener

        return async_track_state_change_event(hass, self._entity_ids, self._async_changed)

    async def async_stays_open(self) -> bool:
        """Return whether nothing closed during the minimum open time."""
        self.cancel()

        opening = asyncio.create_task(asyncio.sleep(self._open_delay))
        self._opening = opening

        try:
            await opening
        except asyncio.CancelledError:
            _LOGGER.debug("Window closed before minimum open time.")
            return False
        finally:
            if self._opening is opening:
                self._opening = None

        return True

    def cancel(self) -> None:
        """Stop waiting for the minimum open time."""
        if self._opening is not None:
            self._opening.cancel()
            self._opening = None

    async def _async_changed(self, event: Event[EventStateChangedData]) -> None:
        new_state, old_state = event.data.get("new_state"), event.data.get("old_state")

        if new_state is None or new_state.state not in (STATE_ON, STATE_OFF):
            return

        if old_state is not None and old_state.state == new_state.state:
            return

        _LOGGER.debug("Window Sensor Changed to %s.", new_state.state)

        await self._listener(new_state.state == STATE_ON)
