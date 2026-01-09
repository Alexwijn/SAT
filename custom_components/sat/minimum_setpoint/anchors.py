from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .const import (
    ANCHOR_SOURCE_FLOW_FLOOR,
    ANCHOR_SOURCE_INTENT_SETPOINT,
    ANCHOR_SOURCE_TAIL_SETPOINT,
    FLOOR_MARGIN,
    MINIMUM_SETPOINT_LEARNING_BAND,
)
from ..boiler import BoilerCapabilities
from ..cycles import Cycle
from ..helpers import clamp


@dataclass(frozen=True, slots=True)
class AnchorRelaxationRequest:
    cycle: Cycle
    boiler_capabilities: BoilerCapabilities
    old_minimum_setpoint: float
    factor: float


@dataclass(frozen=True, slots=True)
class AnchorRelaxation:
    minimum_setpoint: float
    anchor: float
    ran_near_minimum: bool
    anchor_source: str


class AnchorCalculator:
    @staticmethod
    def relax(request: AnchorRelaxationRequest) -> AnchorRelaxation:
        """Relax a minimum setpoint toward a stable, outcome-derived anchor."""
        tail_setpoint_p50: Optional[float] = request.cycle.tail.setpoint.p50
        tail_setpoint_p90: Optional[float] = request.cycle.tail.setpoint.p90
        effective_setpoint: Optional[float] = tail_setpoint_p50 if tail_setpoint_p50 is not None else tail_setpoint_p90

        ran_near_minimum = False
        if effective_setpoint is not None:
            ran_near_minimum = (abs(effective_setpoint - request.old_minimum_setpoint) <= MINIMUM_SETPOINT_LEARNING_BAND)

        anchor_candidate_from_flow: Optional[float] = None
        if ran_near_minimum:
            tail_flow_p50 = request.cycle.tail.flow_temperature.p50
            tail_flow_p90 = request.cycle.tail.flow_temperature.p90
            max_flow_temperature = request.cycle.max_flow_temperature

            flow_reference = (
                tail_flow_p50
                if tail_flow_p50 is not None
                else (tail_flow_p90 if tail_flow_p90 is not None else max_flow_temperature)
            )

            if flow_reference is not None:
                anchor_candidate_from_flow = flow_reference - FLOOR_MARGIN

        if anchor_candidate_from_flow is not None:
            anchor = anchor_candidate_from_flow
            anchor_source = ANCHOR_SOURCE_FLOW_FLOOR
        elif effective_setpoint is not None:
            anchor = effective_setpoint
            anchor_source = ANCHOR_SOURCE_TAIL_SETPOINT
        else:
            anchor = request.cycle.metrics.intent_setpoint.p90
            anchor_source = ANCHOR_SOURCE_INTENT_SETPOINT

        anchor = clamp(anchor, request.boiler_capabilities.minimum_setpoint, request.boiler_capabilities.maximum_setpoint)
        new_minimum_setpoint = round(request.factor * request.old_minimum_setpoint + (1.0 - request.factor) * anchor, 1)
        new_minimum_setpoint = clamp(
            new_minimum_setpoint,
            request.boiler_capabilities.minimum_setpoint,
            request.boiler_capabilities.maximum_setpoint,
        )

        return AnchorRelaxation(
            minimum_setpoint=new_minimum_setpoint,
            anchor=anchor,
            ran_near_minimum=ran_near_minimum,
            anchor_source=anchor_source,
        )
