import math
import os
from numbers import Real
from typing import Any

from lumibot.example_strategies.ai_trading_team_mock_growth_inflation_quadrant import (
    BASKET_AGENT_NAMES,
    BASKET_UNIVERSES,
    AITradingTeamMockGrowthInflationQuadrantStrategy,
)
from lumibot.example_strategies.fred_growth_inflation_regime_classifier import (
    DEFAULT_GROWTH_LAG_MONTHS,
    DEFAULT_GROWTH_SERIES_ID,
    DEFAULT_INFLATION_LAG_MONTHS,
    DEFAULT_INFLATION_SERIES_ID,
    DEFAULT_MODE,
    DEFAULT_TREND_YEARS,
    REGIMES,
    WEIGHT_BY_REGIME,
    make_real_macro_regime_classifier_tool,
    regime_from_directions,
)

MACRO_REGIME_CLASSIFIER_TOOL_NAME = "macro_regime_classifier"
REAL_MACRO_BASKET_WEIGHT_TOLERANCE = 1e-6


def _macro_regime_classifier_tool_payload(result: Any) -> tuple[dict[str, Any] | None, str | None]:
    for event in reversed(list(getattr(result, "tool_results", []) or [])):
        if getattr(event, "tool_name", None) != MACRO_REGIME_CLASSIFIER_TOOL_NAME:
            continue
        payload = getattr(event, "payload", None)
        if not isinstance(payload, dict) or not payload:
            return (
                None,
                "macro_allocation_agent macro_regime_classifier tool result payload must be a non-empty object "
                "before a macro report is trusted.",
            )
        return payload, None

    return (
        None,
        "macro_allocation_agent must produce a macro_regime_classifier tool result payload before a macro "
        "report is trusted.",
    )


class AITradingTeamGrowthInflationQuadrantStrategy(
    AITradingTeamMockGrowthInflationQuadrantStrategy
):
    parameters = {
        "basket_universes": BASKET_UNIVERSES,
        "macro_regime_mode": DEFAULT_MODE,
        "growth_series_id": DEFAULT_GROWTH_SERIES_ID,
        "inflation_series_id": DEFAULT_INFLATION_SERIES_ID,
        "growth_lag_months": DEFAULT_GROWTH_LAG_MONTHS,
        "inflation_lag_months": DEFAULT_INFLATION_LAG_MONTHS,
        "trend_years": DEFAULT_TREND_YEARS,
        "run_frequency": "weekly",
        "weekly_run_weekday": "MON",
        "weekly_holiday_policy": "first_open_trading_day",
    }
    _execution_agent_base_system_prompt_mode = "execution_minimal"
    _basket_agent_names = BASKET_AGENT_NAMES

    def initialize(self):
        self.sleeptime = "1D"
        self._macro_regime_mode = self.parameters.get("macro_regime_mode", DEFAULT_MODE)
        self._growth_series_id = self.parameters.get("growth_series_id", DEFAULT_GROWTH_SERIES_ID)
        self._inflation_series_id = self.parameters.get(
            "inflation_series_id",
            DEFAULT_INFLATION_SERIES_ID,
        )
        self._growth_lag_months = int(
            self.parameters.get("growth_lag_months", DEFAULT_GROWTH_LAG_MONTHS)
        )
        self._inflation_lag_months = int(
            self.parameters.get("inflation_lag_months", DEFAULT_INFLATION_LAG_MONTHS)
        )
        self._trend_years = int(self.parameters.get("trend_years", DEFAULT_TREND_YEARS))
        self._last_real_regime = None
        self._last_macro_regime_error = None
        self._initialize_growth_inflation_workflow_state()
        model = os.environ.get("AI_TRADING_TEAM_MODEL", "gemini-3.1-flash-lite")

        self.agents.create(
            name="macro_allocation_agent",
            model=model,
            allow_trading=False,
            include_builtin_tools=False,
            tools=[make_real_macro_regime_classifier_tool()],
            system_prompt=(
                "Macro allocation role: call the real FRED-backed macro_regime_classifier. "
                "Do not classify the macro regime yourself. Preserve the classifier's structured "
                "status, regime, basket_weights, growth_evidence, inflation_evidence, data_quality, "
                "confidence, and reason fields. If the classifier returns status=blocked or status=failed, "
                "return that plainly. Do not place orders."
            ),
        )

        self._create_growth_inflation_downstream_agents(model)

    def _clear_real_macro_downstream_state(self) -> None:
        self._last_execution_plan_error = None
        self._last_target_portfolio_planner_result = None

    def _block_real_macro_workflow(self, reason: str) -> None:
        self._last_macro_regime_error = {
            "status": "failed",
            "reason": reason,
        }
        self._clear_real_macro_downstream_state()
        self._log_growth_inflation_workflow_blocked(f"Real quadrant macro workflow blocked: {reason}")

    def _accepted_real_macro_report_is_canonical(
        self,
        macro_report: dict[str, Any],
        current_date: str,
    ) -> bool:
        return self._real_macro_report_canonical_error(macro_report, current_date) is None

    def _real_macro_report_canonical_error(
        self,
        macro_report: dict[str, Any],
        current_date: str,
    ) -> str | None:
        growth_evidence = macro_report.get("growth_evidence")
        inflation_evidence = macro_report.get("inflation_evidence")
        data_quality = macro_report.get("data_quality")
        basket_weights = macro_report.get("basket_weights")
        if not isinstance(growth_evidence, dict):
            return "growth_evidence must be an object."
        if not isinstance(inflation_evidence, dict):
            return "inflation_evidence must be an object."
        if not isinstance(data_quality, dict):
            return "data_quality must be an object."

        if macro_report.get("status") != "passed":
            return "status must be passed."
        if macro_report.get("tool") != MACRO_REGIME_CLASSIFIER_TOOL_NAME:
            return f"tool must be {MACRO_REGIME_CLASSIFIER_TOOL_NAME!r}."
        if macro_report.get("mock") is not False:
            return "mock must be false for real macro payloads."
        if macro_report.get("date") != current_date or macro_report.get("as_of") != current_date:
            return "current date mismatch: date and as_of must equal the current date."
        if macro_report.get("mode") != self._macro_regime_mode:
            return "configured/default parameters mismatch: mode must match the strategy configuration."

        regime = macro_report.get("regime")
        if regime not in REGIMES:
            return f"regime must be one of the classifier regimes: {REGIMES}."

        if not isinstance(basket_weights, dict):
            return "basket_weights must be an object with all expected basket keys."
        expected_weights = WEIGHT_BY_REGIME[regime]
        expected_basket_keys = set(expected_weights)
        strategy_basket_keys = set(BASKET_AGENT_NAMES.keys())
        if expected_basket_keys != strategy_basket_keys:
            return (
                "classifier basket_weights keys must match strategy basket keys; "
                f"classifier={sorted(expected_basket_keys)}, strategy={sorted(strategy_basket_keys)}."
            )
        actual_basket_keys = set(basket_weights)
        if actual_basket_keys != expected_basket_keys:
            missing_basket_keys = sorted(expected_basket_keys - actual_basket_keys)
            extra_basket_keys = sorted(actual_basket_keys - expected_basket_keys)
            return (
                "basket_weights keys must exactly match expected basket keys; "
                f"missing={missing_basket_keys}, extra={extra_basket_keys}."
            )
        non_numeric_weight_keys = [
            key
            for key, value in basket_weights.items()
            if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value))
        ]
        if non_numeric_weight_keys:
            return f"basket_weights values must be finite numeric values: {non_numeric_weight_keys}."
        mismatched_weight_keys = [
            key
            for key, expected_value in expected_weights.items()
            if abs(float(basket_weights[key]) - float(expected_value)) > REAL_MACRO_BASKET_WEIGHT_TOLERANCE
        ]
        if mismatched_weight_keys:
            return (
                f"basket_weights must match classifier weights for regime {regime!r}; "
                f"mismatched={mismatched_weight_keys}."
            )
        total_weight = sum(float(value) for value in basket_weights.values())
        if abs(total_weight - 1.0) > REAL_MACRO_BASKET_WEIGHT_TOLERANCE:
            return f"basket_weights total must be approximately 1.0; got {total_weight:.12g}."

        expected_series = [self._growth_series_id, self._inflation_series_id]
        if data_quality.get("required_series") != expected_series:
            return (
                "configured/default parameters mismatch: data_quality required_series must match "
                "the strategy growth/inflation series."
            )
        if data_quality.get("status") != "passed":
            return "data_quality status must be passed."
        if data_quality.get("source") != "fred_api":
            return "data_quality source must be fred_api."
        if data_quality.get("point_in_time_safe") is not True:
            return "data_quality point_in_time_safe must be true."
        if data_quality.get("uses_revised_data") is not False:
            return "data_quality uses_revised_data must be false."

        growth_error = self._real_macro_evidence_canonical_error(
            growth_evidence,
            axis="growth",
            series_id=self._growth_series_id,
            lag_months=self._growth_lag_months,
        )
        if growth_error is not None:
            return f"growth_evidence {growth_error}"
        inflation_error = self._real_macro_evidence_canonical_error(
            inflation_evidence,
            axis="inflation",
            series_id=self._inflation_series_id,
            lag_months=self._inflation_lag_months,
        )
        if inflation_error is not None:
            return f"inflation_evidence {inflation_error}"

        try:
            evidence_regime = regime_from_directions(
                growth_evidence["direction"],
                inflation_evidence["direction"],
            )
        except ValueError as exc:
            return f"evidence directions must map to a classifier regime: {exc}"
        if evidence_regime != regime:
            return (
                "evidence directions must match reported regime; "
                f"directions imply {evidence_regime!r}, got {regime!r}."
            )

        return None

    def _real_macro_evidence_canonical_error(
        self,
        evidence: dict[str, Any],
        *,
        axis: str,
        series_id: str,
        lag_months: int,
    ) -> str | None:
        if evidence.get("axis") != axis:
            return f"axis must be {axis!r}."
        if evidence.get("series_id") != series_id:
            return f"series_id must be {series_id!r}."
        if evidence.get("lag_months") != lag_months:
            return f"lag_months must be {lag_months}."
        if evidence.get("trend_years") != self._trend_years:
            return f"trend_years must be {self._trend_years}."
        required_string_fields = (
            "series_name",
            "frequency",
            "data_cutoff",
            "latest_observation_date",
            "comparison_observation_date",
            "metric_name",
            "direction",
        )
        missing_string_fields = [
            field
            for field in required_string_fields
            if not isinstance(evidence.get(field), str) or not evidence.get(field).strip()
        ]
        if missing_string_fields:
            return f"must include non-empty string fields: {missing_string_fields}."
        if evidence.get("direction") not in {"up", "down"}:
            return "direction must be 'up' or 'down'."
        expected_frequency = "quarterly" if axis == "growth" else "monthly"
        if evidence.get("frequency") != expected_frequency:
            return f"frequency must be {expected_frequency!r}."
        if evidence.get("metric_name") != "year_over_year_change":
            return "metric_name must be 'year_over_year_change'."
        required_numeric_fields = (
            "latest_value",
            "comparison_value",
            "metric_value",
            "trend_window_observations",
            "trend_value",
            "margin",
        )
        non_numeric_fields = [
            field
            for field in required_numeric_fields
            if (
                isinstance(evidence.get(field), bool)
                or not isinstance(evidence.get(field), Real)
                or not math.isfinite(float(evidence.get(field)))
            )
        ]
        if non_numeric_fields:
            return f"must include finite numeric fields: {non_numeric_fields}."
        if float(evidence["trend_window_observations"]) <= 0:
            return "trend_window_observations must be > 0."
        return None

    def _commit_accepted_real_regime_if_canonical(
        self,
        macro_report: dict[str, Any],
        current_date: str,
    ) -> None:
        if self._accepted_real_macro_report_is_canonical(macro_report, current_date):
            regime = macro_report.get("regime")
            if isinstance(regime, str) and regime.strip():
                self._last_real_regime = regime

    def on_trading_iteration(self):
        current_datetime = self.get_datetime()
        current_date_obj = current_datetime.date()
        current_date = current_date_obj.isoformat()
        cadence_event = self._scheduled_workflow_decision(current_date_obj)
        if not cadence_event["should_run"]:
            self._record_scheduled_workflow_event(cadence_event)
            return
        self._mark_scheduled_workflow_attempted(cadence_event)
        basket_universes = self.parameters.get("basket_universes", BASKET_UNIVERSES)

        try:
            macro_result = self.agents["macro_allocation_agent"].run(
                task_prompt=(
                    "Run the real FRED-backed macro allocation step by calling macro_regime_classifier. "
                    "Return one JSON object preserving status, regime, basket_weights, growth_evidence, "
                    "inflation_evidence, data_quality, confidence, and reason fields."
                ),
                context={
                    "date": current_date,
                    "macro_regime_mode": self._macro_regime_mode,
                    "growth_series_id": self._growth_series_id,
                    "inflation_series_id": self._inflation_series_id,
                    "growth_lag_months": self._growth_lag_months,
                    "inflation_lag_months": self._inflation_lag_months,
                    "trend_years": self._trend_years,
                    "basket_universes": basket_universes,
                },
            )
        except ValueError as exc:
            self._block_real_macro_workflow(str(exc))
            return

        macro_report, payload_error = _macro_regime_classifier_tool_payload(macro_result)
        if payload_error is not None:
            self._block_real_macro_workflow(payload_error)
            return

        if str(macro_report.get("status") or "").strip().lower() != "passed":
            self._last_macro_regime_error = macro_report
            self._clear_real_macro_downstream_state()
            return

        canonical_error = self._real_macro_report_canonical_error(macro_report, current_date)
        if canonical_error is not None:
            self._block_real_macro_workflow(
                "macro_regime_classifier returned a non-canonical passed payload; "
                f"{canonical_error} Blocking real workflow because accepted tool_result must include "
                "complete regime, basket_weights, evidence, and trusted FRED provenance for the "
                "current date and configured/default parameters."
            )
            return

        self._last_macro_regime_error = None
        self._commit_accepted_real_regime_if_canonical(macro_report, current_date)
        self._run_growth_inflation_downstream_workflow(
            current_date,
            macro_report,
            "Real quadrant workflow blocked",
        )
