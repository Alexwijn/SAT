"""Tests for learning how long rooms take to recover."""

import pytest

from custom_components.sat.preheat import PreheatEvaluator, PreheatMonitor, PreheatRate, RecoveryEpisode, RoomRecoveryTracker
from custom_components.sat.room_balance import BoilerObservation, MAIN_ROOM_ID, RoomObservation, RoomSignalReading

ROOM = "climate.bedroom"
HOUR = 3600.0


def _boiler(eligible: bool = True, flame_active: bool = True, outside_temperature: float = 5.0) -> BoilerObservation:
    return BoilerObservation(eligible=eligible, heating=True, flame_active=flame_active, flow_temperature=50.0, outside_temperature=outside_temperature)


def _room(shortfall, room_id: str = ROOM, **kwargs) -> RoomObservation:
    return RoomObservation(room_id=room_id, shortfall=shortfall, **kwargs)


def _episode(rate: float, outside: float = 5.0, room_id: str = ROOM) -> RecoveryEpisode:
    return RecoveryEpisode(date="2026-11-01", room_id=room_id, degrees=2.0, rate_per_hour=rate, outside_temperature=outside)


def test_known_recovery_gives_the_right_rate():
    tracker = RoomRecoveryTracker()

    assert tracker.update(_boiler(), _room(2.0), 0.0) is None
    assert tracker.update(_boiler(), _room(1.0), HOUR) is None

    episode = tracker.update(_boiler(outside_temperature=3.0), _room(0.0), 2 * HOUR)

    assert episode.rate_per_hour == 1.0
    assert episode.degrees == 2.0
    assert episode.outside_temperature == 4.0


def test_flame_gaps_keep_the_recovery():
    tracker = RoomRecoveryTracker()

    tracker.update(_boiler(), _room(2.0), 0.0)
    tracker.update(_boiler(flame_active=False), _room(1.0), HOUR)

    assert tracker.update(_boiler(), _room(0.0), 2 * HOUR) is not None


@pytest.mark.parametrize(("boiler", "room"), [
    (_boiler(eligible=False), _room(1.0)),
    (_boiler(), _room(1.0, signals=RoomSignalReading(window_open=True))),
    (_boiler(), _room(1.0, signals=RoomSignalReading(valve_alarm=True))),
    (_boiler(), _room(None)),
])
def test_interruption_discards_the_recovery(boiler, room):
    tracker = RoomRecoveryTracker()

    tracker.update(_boiler(), _room(2.0), 0.0)
    tracker.update(boiler, room, HOUR)

    assert tracker.update(_boiler(), _room(0.0), 2 * HOUR) is None


def test_raised_target_discards_the_recovery():
    tracker = RoomRecoveryTracker()

    tracker.update(_boiler(), _room(2.0), 0.0)
    tracker.update(_boiler(), _room(2.6), HOUR)

    assert tracker.update(_boiler(), _room(0.0), 2 * HOUR) is None


def test_recovery_past_the_limit_is_discarded():
    tracker = RoomRecoveryTracker()

    tracker.update(_boiler(), _room(2.0), 0.0)

    assert tracker.update(_boiler(), _room(0.0), 13 * HOUR) is None


def test_small_shortfall_does_not_start_a_recovery():
    tracker = RoomRecoveryTracker()

    tracker.update(_boiler(), _room(0.9), 0.0)

    assert tracker.update(_boiler(), _room(0.0), HOUR) is None


def test_same_band_is_preferred():
    episodes = [_episode(1.0, outside=6.0)] * 3 + [_episode(3.0, outside=-4.0)] * 3

    assert PreheatEvaluator.rate(episodes, 7.0) == PreheatRate(episodes=3, rate_per_hour=1.0)
    assert PreheatEvaluator.rate(episodes, -2.0) == PreheatRate(episodes=3, rate_per_hour=3.0)


def test_nearest_usable_band_is_used():
    episodes = [_episode(1.0, outside=6.0)] * 3 + [_episode(9.0, outside=-4.0)] * 2

    assert PreheatEvaluator.rate(episodes, -3.0).rate_per_hour == 1.0


def test_overall_median_is_used_when_no_band_is_usable():
    episodes = [_episode(1.0, outside=6.0), _episode(2.0, outside=-4.0), _episode(3.0, outside=14.0)]

    assert PreheatEvaluator.rate(episodes, 5.0) == PreheatRate(episodes=3, rate_per_hour=2.0)


def test_unknown_outside_temperature_uses_the_overall_median():
    assert PreheatEvaluator.rate([_episode(1.0)] * 3, None).rate_per_hour == 1.0


def test_too_few_episodes_give_no_rate():
    assert PreheatEvaluator.rate([_episode(1.0)] * 2, 5.0) is None


def test_lead_time_includes_the_margin_and_rounds_up():
    rate = PreheatRate(episodes=3, rate_per_hour=1.0)

    assert rate.lead_minutes(2.0) == 132
    assert rate.lead_minutes(0.0) == 0
    assert rate.lead_minutes(-1.0) == 0


def test_monitor_keeps_rooms_apart():
    monitor = PreheatMonitor("entry")

    monitor.observe(_boiler(), [_room(2.0), _room(1.0, room_id=MAIN_ROOM_ID)], 0.0)
    monitor.observe(_boiler(), [_room(0.0), _room(0.0, room_id=MAIN_ROOM_ID)], HOUR)

    assert monitor.episodes(ROOM)[0].rate_per_hour == 2.0
    assert monitor.episodes(MAIN_ROOM_ID)[0].rate_per_hour == 1.0


async def test_episodes_survive_a_reload(hass):
    monitor = PreheatMonitor("entry")
    await monitor.async_added_to_hass(hass)

    monitor.observe(_boiler(), [_room(2.0)], 0.0)
    monitor.observe(_boiler(), [_room(0.0)], HOUR)
    await monitor.async_save_data()

    restored = PreheatMonitor("entry")
    await restored.async_added_to_hass(hass)

    assert restored.episodes(ROOM)[0].rate_per_hour == 2.0
