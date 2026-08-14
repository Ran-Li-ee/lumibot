import os
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
    make_real_macro_regime_classifier_tool,
)

MACRO_REGIME_CLASSIFIER_TOOL_NAME = "macro_regime_classifier"


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
        growth_evidence = macro_report.get("growth_evidence")
        inflation_evidence = macro_report.get("inflation_evidence")
        data_quality = macro_report.get("data_quality")
        if not isinstance(growth_evidence, dict):
            return False
        if not isinstance(inflation_evidence, dict):
            return False
        if not isinstance(data_quality, dict):
            return False

        return (
            macro_report.get("status") == "passed"
            and macro_report.get("tool") == MACRO_REGIME_CLASSIFIER_TOOL_NAME
            and macro_report.get("mock") is False
            and macro_report.get("date") == current_date
            and macro_report.get("as_of") == current_date
            and macro_report.get("mode") == self._macro_regime_mode
            and data_quality.get("required_series") == [
                self._growth_series_id,
                self._inflation_series_id,
            ]
            and growth_evidence.get("lag_months") == self._growth_lag_months
            and inflation_evidence.get("lag_months") == self._inflation_lag_months
            and growth_evidence.get("trend_years") == self._trend_years
            and inflation_evidence.get("trend_years") == self._trend_years
        )

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

        self._last_macro_regime_error = None
        self._commit_accepted_real_regime_if_canonical(macro_report, current_date)
        self._run_growth_inflation_downstream_workflow(
            current_date,
            macro_report,
            "Real quadrant workflow blocked",
        )
