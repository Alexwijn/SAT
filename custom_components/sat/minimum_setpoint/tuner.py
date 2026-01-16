from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from .anchors import AnchorCalculator, AnchorRelaxationRequest
from .const import *
from .regimes import RegimeState
from ..boiler import BoilerCapabilities
from ..cycles import Cycle, CycleStatistics
from ..cycles.const import TARGET_MIN_ON_TIME_SECONDS, ULTRA_SHORT_MIN_ON_TIME_SECONDS
from ..cycles.types import CycleKind
from ..helpers import clamp
from ..types import CycleClassification

_LOGGER = logging.getLogger(__name__)


class MinimumSetpointTuner:
    @staticmethod
    def tune(boiler_capabilities: BoilerCapabilities, cycles: CycleStatistics, cycle: Cycle, regime_state: RegimeState) -> None:
        """Decide whether and how to adjust the learned minimum setpoint for the active regime after a cycle."""
        # Only use cycles that are predominantly space heating.
        if cycle.kind not in (CycleKind.CENTRAL_HEATING, CycleKind.MIXED):
            _LOGGER.debug("Ignoring non-heating cycle kind=%s for tuning.", cycle.kind)
            return

        if cycle.fraction_space_heating < MIN_SPACE_HEATING_FRACTION_FOR_TUNING:
            _LOGGER.debug("Cycle has too little space-heating fraction (%.2f), ignoring.", cycle.fraction_space_heating)
            return

        classification = cycle.classification

        if classification == CycleClassification.PREMATURE_OFF:
            off_with_demand_minutes = None
            if cycles.window.off_with_demand_duration is not None:
                off_with_demand_minutes = max(0.0, float(cycles.window.off_with_demand_duration) / 60.0)

            step = MinimumSetpointTuner._compute_scaled_step(base=0.5, scale=0.1, value=off_with_demand_minutes, fallback=1.0)
            step = MinimumSetpointTuner._scale_step_for_regime(regime_state, step)
            regime_state.minimum_setpoint += step
            _LOGGER.debug("Premature flame off detected; increasing minimum setpoint to %.2f", regime_state.minimum_setpoint)
            return

        # Check if the current regime is suitable for minimum tuning.
        if not MinimumSetpointTuner._is_tunable_regime(cycles):
            old_minimum_setpoint = regime_state.minimum_setpoint
            relaxation = AnchorCalculator.relax(AnchorRelaxationRequest(
                cycle=cycle,
                boiler_capabilities=boiler_capabilities,
                old_minimum_setpoint=old_minimum_setpoint,
                factor=MINIMUM_RELAX_FACTOR_WHEN_UNTUNABLE,
            ))
            regime_state.minimum_setpoint = relaxation.minimum_setpoint
            _LOGGER.debug(
                "Relaxing regime %s minimum toward anchor=%.1f: %.1f -> %.1f (factor=%.2f, ran_near_minimum=%s, anchor_source=%s)",
                regime_state.key.to_storage(),
                relaxation.anchor,
                old_minimum_setpoint,
                relaxation.minimum_setpoint,
                MINIMUM_RELAX_FACTOR_WHEN_UNTUNABLE,
                relaxation.ran_near_minimum,
                relaxation.anchor_source,
            )
            return

        reference_setpoint = cycle.tail.setpoint.p50

        if reference_setpoint is None:
            _LOGGER.debug("No setpoint found for cycle, skipping tuning.")
            return

        current_minimum = regime_state.minimum_setpoint
        learning_band = MinimumSetpointTuner._learning_band(regime_state)

        if abs(reference_setpoint - current_minimum) > learning_band:
            _LOGGER.debug(
                "Cycle reference_setpoint=%.1f is too far from regime minimum_setpoint=%.1f (band=%.1f), skipping tuning.",
                reference_setpoint,
                current_minimum,
                learning_band,
            )
            return

        if classification in (CycleClassification.FAST_OVERSHOOT, CycleClassification.TOO_SHORT_OVERSHOOT):
            is_ultra_short = cycle.duration < ULTRA_SHORT_MIN_ON_TIME_SECONDS
            is_very_short = cycle.duration < (TARGET_MIN_ON_TIME_SECONDS * 0.5)
            is_low_duty = cycles.window.duty_ratio_last_15m <= LOW_LOAD_MAXIMUM_DUTY_RATIO_15_M
            is_frequent_cycles = cycles.window.last_hour_count >= LOW_LOAD_MINIMUM_CYCLES_PER_HOUR

            if is_very_short and is_low_duty and is_frequent_cycles and (not is_ultra_short):
                _LOGGER.debug(
                    "Ignoring %s for minimum tuning under low-load: duration=%.1fs (< %.1fs), duty_15m=%.2f (<= %.2f), cycles_last_hour=%.1f (>= %.1f).",
                    classification.name,
                    cycle.duration,
                    TARGET_MIN_ON_TIME_SECONDS * 0.5,
                    cycles.window.duty_ratio_last_15m,
                    LOW_LOAD_MAXIMUM_DUTY_RATIO_15_M,
                    cycles.window.last_hour_count,
                    LOW_LOAD_MINIMUM_CYCLES_PER_HOUR,
                )
                return

        # INSUFFICIENT_DATA:
        #   We do not know enough to make a safe decision.
        if classification == CycleClassification.INSUFFICIENT_DATA:
            return

        # UNCERTAIN:
        #   - Conflicting signals, borderline flows, or sensor noise.
        #   - Neither direction (up or down) is reliable.
        if classification == CycleClassification.UNCERTAIN:
            return

        # GOOD:
        #   The boiler produced a long, stable burn without overshoot or underheat.
        #   This means the current minimum_setpoint is appropriate for this regime.
        #   But we do try to find a better value.
        if classification == CycleClassification.GOOD:
            if regime_state.stable_cycles >= 2:
                step = MinimumSetpointTuner._scale_step_for_regime(regime_state, 0.3)
                regime_state.minimum_setpoint -= step
                _LOGGER.debug("GOOD stable cycle; decreasing minimum setpoint by %.2f.", step)

            MinimumSetpointTuner._apply_condensing_bias(cycle, regime_state)
            return

        # FAST_UNDERHEAT / TOO_SHORT_UNDERHEAT:
        #   - Boiler fails to approach the requested flow temperature.
        #   - Indicates the requested flow setpoint is too high for the available heat output.
        if classification in (CycleClassification.FAST_UNDERHEAT, CycleClassification.TOO_SHORT_UNDERHEAT):
            step = MinimumSetpointTuner._scale_step_for_regime(regime_state, 1.0)
            regime_state.minimum_setpoint -= step
            _LOGGER.debug("Underheat cycle; decreasing minimum setpoint by %.2f.", step)
            return

        # FAST_OVERSHOOT / TOO_SHORT_OVERSHOOT:
        #   - Boiler fails to stay stable at the requested flow temperature.
        #   - Indicates the requested flow setpoint is too low for the available heat output.
        if classification in (CycleClassification.FAST_OVERSHOOT, CycleClassification.TOO_SHORT_OVERSHOOT):
            if MinimumSetpointTuner._is_load_drop_overshoot(cycle):
                _LOGGER.debug("Overshoot likely due to load drop; skipping minimum setpoint increase.")
                return

            step = MinimumSetpointTuner._scale_step_for_regime(regime_state, 1.0)
            if (applied_step := MinimumSetpointTuner._apply_increase_with_limits(regime_state, step=step)) > 0.0:
                _LOGGER.debug("Overshoot cycle; increasing minimum setpoint by %.2f.", applied_step)
            return

        # LONG_UNDERHEAT:
        #   - Long burn, but flow temperature remains below setpoint.
        #   - Indicates chronic underheating at this setpoint.
        if classification == CycleClassification.LONG_UNDERHEAT:
            error = cycle.tail.flow_intent_setpoint_error.p90
            step = MinimumSetpointTuner._compute_scaled_step(base=0.3, scale=0.1, value=abs(error) if error is not None else None, fallback=0.5)
            step = MinimumSetpointTuner._scale_step_for_regime(regime_state, step)

            regime_state.minimum_setpoint -= step
            _LOGGER.debug("Long underheat; decreasing minimum setpoint by %.2f.", step)
            MinimumSetpointTuner._apply_condensing_bias(cycle, regime_state)
            return

        # LONG_OVERSHOOT:
        #   - Sustained overshoot during a longer burn.
        #   - More likely indicates the requested flow setpoint is genuinely too low for stable operation.
        if classification == CycleClassification.LONG_OVERSHOOT:
            if MinimumSetpointTuner._is_load_drop_overshoot(cycle):
                _LOGGER.debug("Overshoot likely due to load drop; skipping minimum setpoint increase.")
                return

            error = cycle.tail.flow_intent_setpoint_error.p90
            step = MinimumSetpointTuner._compute_scaled_step(base=0.3, scale=0.1, value=abs(error) if error is not None else None, fallback=0.5)
            step = MinimumSetpointTuner._scale_step_for_regime(regime_state, step)

            if (applied_step := MinimumSetpointTuner._apply_increase_with_limits(regime_state, step=step)) > 0.0:
                _LOGGER.debug("Long overshoot; increasing minimum setpoint by %.2f.", applied_step)

    @staticmethod
    def _is_load_drop_overshoot(cycle: Cycle) -> bool:
        if (delta := cycle.tail.flow_return_delta.p90) is None:
            return False

        flow_temp = cycle.tail.flow_temperature.p90
        dynamic_threshold = LOAD_DROP_FLOW_RETURN_DELTA_THRESHOLD

        # Scale threshold by flow temp to avoid treating high-ΔT systems as load-drop overshoot.
        if flow_temp is not None:
            dynamic_threshold = max(dynamic_threshold, flow_temp * LOAD_DROP_FLOW_RETURN_DELTA_FRACTION)

        return delta >= dynamic_threshold

    @staticmethod
    def _apply_condensing_bias(cycle: Cycle, regime_state: RegimeState) -> None:
        # Only bias downward when return temps are high, to encourage condensing efficiency.
        if cycle.tail.return_temperature.p90 is None or cycle.tail.return_temperature.p90 <= CONDENSING_RETURN_TEMP_TARGET:
            return

        overshoot = cycle.tail.return_temperature.p90 - CONDENSING_RETURN_TEMP_TARGET
        step = MinimumSetpointTuner._compute_scaled_step(
            base=CONDENSING_STEP_BASE,
            scale=CONDENSING_STEP_SCALE,
            value=overshoot,
            fallback=CONDENSING_STEP_FALLBACK,
        )

        regime_state.minimum_setpoint -= step

        _LOGGER.debug(
            "Condensing bias applied: return_temperature=%.1f°C target=%.1f°C step=%.2f.",
            cycle.tail.return_temperature.p90, CONDENSING_RETURN_TEMP_TARGET, step,
        )

    @staticmethod
    def _apply_increase_with_limits(regime_state: RegimeState, step: float) -> float:
        if step <= 0.0:
            return 0.0

        now = datetime.now(timezone.utc)

        # Cooldown prevents rapid increases from a single noisy regime.
        if regime_state.last_increase_at is not None and (now - regime_state.last_increase_at) < MINIMUM_SETPOINT_INCREASE_COOLDOWN:
            _LOGGER.debug("Skipping minimum setpoint increase due to cooldown.")
            return 0.0

        window_start = regime_state.increase_window_start
        if window_start is None or (now - window_start) >= timedelta(days=1):
            regime_state.increase_window_start = now
            regime_state.increase_window_total = 0.0

        remaining = MAX_MINIMUM_SETPOINT_INCREASE_PER_DAY - regime_state.increase_window_total
        # Daily cap stops slow drift upward due to persistent but non-actionable overshoot.
        if remaining <= 0.0:
            _LOGGER.debug("Daily minimum setpoint increase limit reached; skipping increase.")
            return 0.0

        applied_step = min(step, remaining)
        regime_state.last_increase_at = now
        regime_state.minimum_setpoint += applied_step
        regime_state.increase_window_total += applied_step

        _LOGGER.debug(
            "Applied minimum setpoint increase: step=%.2f remaining_today=%.2f.",
            applied_step, MAX_MINIMUM_SETPOINT_INCREASE_PER_DAY - regime_state.increase_window_total,
        )

        return applied_step

    @staticmethod
    def _compute_scaled_step(base: float, scale: float, value: Optional[float], fallback: float) -> float:
        """Compute a clamped step size from a linear scale."""
        if value is None:
            return fallback

        return clamp(base + (scale * value), MINIMUM_SETPOINT_STEP_MIN, MINIMUM_SETPOINT_STEP_MAX)

    @staticmethod
    def _learning_band(regime_state: RegimeState) -> float:
        if regime_state.completed_cycles >= MINIMUM_SETPOINT_EARLY_TUNING_CYCLES:
            return MINIMUM_SETPOINT_LEARNING_BAND

        remaining = MINIMUM_SETPOINT_EARLY_TUNING_CYCLES - regime_state.completed_cycles
        bonus = MINIMUM_SETPOINT_LEARNING_BAND_EARLY_BONUS * (remaining / MINIMUM_SETPOINT_EARLY_TUNING_CYCLES)
        return MINIMUM_SETPOINT_LEARNING_BAND + bonus

    @staticmethod
    def _scale_step_for_regime(regime_state: RegimeState, step: float) -> float:
        if step == 0.0:
            return 0.0

        multiplier = 1.0
        if regime_state.completed_cycles < MINIMUM_SETPOINT_EARLY_TUNING_CYCLES:
            multiplier = MINIMUM_SETPOINT_EARLY_TUNING_MULTIPLIER

        scaled = step * multiplier
        magnitude = clamp(abs(scaled), MINIMUM_SETPOINT_STEP_MIN, MINIMUM_SETPOINT_STEP_MAX)
        return magnitude if scaled >= 0.0 else -magnitude

    @staticmethod
    def _is_tunable_regime(cycles: CycleStatistics) -> bool:
        """Decide whether the current conditions are suitable for minimum tuning."""
        if cycles.window.sample_count_4h < MINIMUM_ON_SAMPLES_FOR_TUNING:
            return False

        if cycles.window.last_hour_count < LOW_LOAD_MINIMUM_CYCLES_PER_HOUR:
            return False

        return True
