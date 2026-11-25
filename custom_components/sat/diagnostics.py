"""Diagnostics support for SAT."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_DEVICE, CONF_MQTT_TOPIC
from .entry_data import get_entry_data
from .preheat import PreheatEvaluator
from .room_balance import MAIN_ROOM_ID

TO_REDACT = {CONF_DEVICE, CONF_MQTT_TOPIC}


def _safe(getter: Callable[[], Any]) -> Any:
    """Return the value of a getter, or the error it raised."""
    try:
        value = getter()
    except Exception as exception:
        return f"error: {exception}"

    if hasattr(value, "name") and not isinstance(value, (str, int, float, bool)):
        return value.name

    return value


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    entry_data = get_entry_data(hass, entry.entry_id)

    coordinator = entry_data.coordinator
    heating_control = entry_data.heating_control

    diagnostics: dict[str, Any] = {
        "entry": {
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "coordinator": {
            "type": _safe(lambda: coordinator.type),
            "active": _safe(lambda: coordinator.active),
            "setpoint": _safe(lambda: coordinator.setpoint),
            "flame_active": _safe(lambda: coordinator.flame_active),
            "hot_water_active": _safe(lambda: coordinator.hot_water_active),
            "boiler_temperature": _safe(lambda: coordinator.boiler_temperature),
            "return_temperature": _safe(lambda: coordinator.return_temperature),
            "boiler_pressure": _safe(lambda: coordinator.boiler_pressure),
            "boiler_power": _safe(lambda: coordinator.boiler_power),
            "relative_modulation_value": _safe(lambda: coordinator.relative_modulation_value),
            "minimum_relative_modulation_value": _safe(lambda: coordinator.minimum_relative_modulation_value),
            "maximum_relative_modulation_value": _safe(lambda: coordinator.maximum_relative_modulation_value),
            "supports_setpoint_management": _safe(lambda: coordinator.supports_setpoint_management),
            "supports_hot_water_setpoint_management": _safe(lambda: coordinator.supports_hot_water_setpoint_management),
            "supports_relative_modulation_management": _safe(lambda: coordinator.supports_relative_modulation_management),
            "supports_maximum_setpoint_management": _safe(lambda: coordinator.supports_maximum_setpoint_management),
            "member_id": _safe(lambda: coordinator.member_id),
            "manufacturer": _safe(lambda: coordinator.manufacturer.friendly_name if coordinator.manufacturer else None),
        },
        "heating_control": {
            "device_status": _safe(lambda: heating_control.device_status),
            "control_setpoint": _safe(lambda: heating_control.control_setpoint),
            "control_mode": _safe(lambda: heating_control.control_mode),
            "relative_modulation_value": _safe(lambda: heating_control.relative_modulation_value),
            "relative_modulation_state": _safe(lambda: heating_control.relative_modulation_state),
            "pwm": {
                "enabled": _safe(lambda: heating_control.pwm_state.enabled),
                "status": _safe(lambda: heating_control.pwm_state.status),
                "duty_cycle": _safe(lambda: list(heating_control.pwm_state.duty_cycle) if heating_control.pwm_state.duty_cycle else None),
                "effective_on_temperature": _safe(lambda: heating_control.pwm_state.effective_on_temperature),
            },
            "last_cycle": {
                "kind": _safe(lambda: heating_control.last_cycle.kind if heating_control.last_cycle else None),
                "control_mode": _safe(lambda: heating_control.last_cycle.control_mode if heating_control.last_cycle else None),
                "classification": _safe(lambda: heating_control.last_cycle.classification if heating_control.last_cycle else None),
            },
            "boiler_health": {
                "days": _safe(lambda: len(heating_control.boiler_health.days)),
                "problem": _safe(lambda: heating_control.boiler_health.trends.is_problem),
                "trends": _safe(lambda: asdict(heating_control.boiler_health.trends)),
            },
        },
    }

    demand_control = entry_data.demand_control
    advice = demand_control.curve_advisor.advice

    diagnostics["climate"] = {
        "hvac_mode": _safe(lambda: demand_control.hvac_mode),
        "preset_mode": _safe(lambda: entry_data.climate.preset_mode if entry_data.climate is not None else None),
        "target_temperature": _safe(lambda: demand_control.target_temperature),
        "current_temperature": _safe(lambda: demand_control.current_temperature),
        "outside_temperature": _safe(lambda: demand_control.current_outside_temperature),
        "wind": _safe(lambda: asdict(demand_control.current_wind) if demand_control.current_wind is not None else None),
        "requested_setpoint": _safe(lambda: demand_control.requested_setpoint),
        "valves_open": _safe(lambda: demand_control.valves_open),
        "heating_blocked_by": _safe(lambda: demand_control.heating_blocked_by),
        "heating_curve_value": _safe(lambda: demand_control.heating_curve.value),
        "pid": {
            "output": _safe(lambda: demand_control.pid.output),
            "proportional": _safe(lambda: demand_control.pid.proportional),
            "integral": _safe(lambda: demand_control.pid.integral),
            "derivative": _safe(lambda: demand_control.pid.derivative),
        },
        "curve_advisor": {
            "recommendation": _safe(lambda: advice.recommendation),
            "confidence": _safe(lambda: advice.confidence),
            "sample_count": advice.sample_count,
            "sample_days": advice.sample_days,
            "median_correction": advice.median_correction,
            "estimated_coefficient": advice.estimated_coefficient,
            "suggested_coefficient": advice.suggested_coefficient,
            "daily_coefficients": dict(advice.daily_coefficients),
            "daily_wind_speeds": dict(advice.daily_wind_speeds),
        },
        "wind_sensitivity": _safe(lambda: {
            **asdict(demand_control.wind_sensitivity.sensitivity),
            "percent_per_10_kmh": demand_control.wind_sensitivity.sensitivity.percent_per_10_kmh,
            "days": [asdict(day) for day in demand_control.wind_sensitivity.days],
        }),
        "efficiency": _safe(lambda: {**asdict(demand_control.efficiency.report), "change_percentage": demand_control.efficiency.report.change_percentage}),
        "preheat": _safe(lambda: {
            room_id: {
                "episodes": len(demand_control.preheat.episodes(room_id)),
                "bands": {band: asdict(rate) for band, rate in PreheatEvaluator.bands(demand_control.preheat.episodes(room_id)).items()},
            }
            for room_id in [MAIN_ROOM_ID, *demand_control.areas.ids()]
        }),
        "room_balance": _safe(lambda: {room_id: asdict(demand_control.room_balance.balance(room_id)) for room_id in [MAIN_ROOM_ID, *demand_control.areas.ids()]}),
        "seasonal_curve": _safe(lambda: {**asdict(demand_control.seasonal_curve.fit), "curve_mismatch": demand_control.seasonal_curve.fit.is_mismatch}),
    }

    return diagnostics
