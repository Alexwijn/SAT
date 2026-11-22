"""Window and door contacts that pause the heating."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Coroutine, Optional

from homeassistant.const import STATE_ON
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .entry_data import SatConfig

_LOGGER = logging.getLogger(__name__)


class ContactSensors:
    """All contacts as one: open while any is open, where one that is lost counts as closed."""

    def __init__(self, config: SatConfig) -> None:
        self._entity_ids = list(config.window_sensors)
        self._open_delay = config.sensors.window_minimum_open_time_seconds
        self._close_delay = config.sensors.window_minimum_closed_time_seconds

        self._is_open: bool = False
        self._hass: Optional[HomeAssistant] = None
        self._cancel_change: Optional[CALLBACK_TYPE] = None
        self._listener: Optional[Callable[[bool], Coroutine[None, None, None]]] = None

    def assume_open(self) -> None:
        """Continue from a pause that was running before a restart."""
        self._is_open = True

    @callback
    def track(self, hass: HomeAssistant, listener: Callable[[bool], Coroutine[None, None, None]]) -> CALLBACK_TYPE:
        """Call the listener when the contacts open or close, starting from how they are now."""
        self._hass, self._listener = hass, listener

        untrack = async_track_state_change_event(hass, self._entity_ids, self._changed)
        self._evaluate()

        return untrack

    def cancel(self) -> None:
        """Forget a change that has not lasted long enough yet."""
        if self._cancel_change is not None:
            self._cancel_change()
            self._cancel_change = None

    @callback
    def _changed(self, _event: Event[EventStateChangedData]) -> None:
        self._evaluate()

    @callback
    def _evaluate(self) -> None:
        is_open = any((state := self._hass.states.get(entity_id)) is not None and state.state == STATE_ON for entity_id in self._entity_ids)

        if is_open == self._is_open:
            self.cancel()
            return

        if self._cancel_change is not None:
            return

        if (delay := self._open_delay if is_open else self._close_delay) > 0:
            self._cancel_change = async_call_later(self._hass, delay, self._change)
            return

        self._change()

    @callback
    def _change(self, _now: Optional[datetime] = None) -> None:
        self._cancel_change = None
        self._is_open = not self._is_open

        _LOGGER.debug("Contacts are now %s.", "open" if self._is_open else "closed")

        self._hass.async_create_task(self._listener(self._is_open))
