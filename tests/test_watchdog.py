import pytest
from homeassistant.helpers import issue_registry as ir

from custom_components.sat.const import CONTROL_LOOP_STALL_SECONDS, DOMAIN
from custom_components.sat.helpers import timestamp
from custom_components.sat.watchdog import ControlWatchdog


def test_watchdog_reports_a_loop_that_stopped_completing():
    watchdog = ControlWatchdog()

    assert watchdog.check(1000.0) is False
    assert watchdog.check(1000.0 + CONTROL_LOOP_STALL_SECONDS) is True

    watchdog.completed(2000.0)

    assert watchdog.check(2001.0) is False


@pytest.mark.parametrize(("domains", "data", "options", "config"), [([], {}, {}, {})])
async def test_stalled_loop_raises_an_issue_that_clears(hass, entry, demand_control, domains, data, options, config):
    issue_id = f"control_loop_stalled_{entry.entry_id}"
    demand_control._watchdog.completed(timestamp() - CONTROL_LOOP_STALL_SECONDS - 1)

    demand_control._update_stall_issue()

    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    demand_control._watchdog.completed(timestamp())
    demand_control._update_stall_issue()

    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
