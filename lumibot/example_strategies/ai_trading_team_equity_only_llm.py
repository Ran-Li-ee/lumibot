from __future__ import annotations

import os
from datetime import date as date_type
from pathlib import Path
from typing import Any

from lumibot.components.agents.builtins import BuiltinTools
from lumibot.example_strategies.ai_trading_team_equity_only_helpers import (
    ACTIVE_SELECTION_STATUSES,
    EQUITY_AGENT_NAME,
    EQUITY_BASKET_ID,
    EQUITY_ONLY_BASKET_UNIVERSES,
    WEEKDAY_INDEX_BY_CODE,
    equity_basket_agent_system_prompt,
    equity_basket_agent_task_prompt,
    equity_basket_agent_tools,
    execution_plan_execute_payload,
    iso_week_key,
    normalize_execution_plan,
    normalize_weekly_run_weekday,
    parse_json_summary,
    qqq_historical_equity_basket_agent_system_prompt,
    validate_execution_plan_matches_planner_result,
    validate_execution_plan_symbols,
)
from lumibot.example_strategies.ai_trading_team_growth_execution_test import (
    AITradingTeamGrowthExecutionTestStrategy,
    validate_decision_buy_sizing,
    validate_execution_plan_cash_safety,
)
from lumibot.example_strategies.target_portfolio_to_execution_plan import (
    get_agent_order_cash_check_price as target_portfolio_order_cash_check_price,
)
from lumibot.example_strategies.target_portfolio_to_execution_plan import target_portfolio_to_execution_plan
from lumibot.tools.universe.qqq_nport import NoSnapshotAvailableError, resolve_qqq_snapshot

EQUITY_ONLY_TARGET_WEIGHT = 1.0
ALLOWED_EQUITY_RUN_FREQUENCIES = {"daily", "weekly", "monthly"}


def _normalized_universe(equity_universe: list[str]) -> set[str]:
    return {str(symbol).strip().upper() for symbol in equity_universe if str(symbol).strip()}


def _normalized_symbol_list(symbols: Any) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    if not isinstance(symbols, (list, tuple)):
        return normalized
    for symbol in symbols:
        if not isinstance(symbol, str):
            continue
        clean = symbol.strip().upper()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        normalized.append(clean)
    return normalized


def _normalize_equity_run_frequency(value: Any) -> str:
    if not value:
        return "monthly"
    frequency = str(value).strip().lower()
    if frequency not in ALLOWED_EQUITY_RUN_FREQUENCIES:
        raise ValueError("run_frequency must be 'daily', 'weekly', or 'monthly'.")
    return frequency


def _normalize_qqq_universe_mode(value: Any) -> str:
    if not value:
        return "strict"
    mode = str(value).strip().lower()
    if mode not in {"strict", "prototype"}:
        raise ValueError("qqq_universe_mode must be 'strict' or 'prototype'.")
    return mode


def _month_key(value: date_type) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def _qqq_universe_source_payload(resolution: Any, symbols: list[str]) -> dict[str, Any]:
    return {
        "type": "qqq_nport",
        "mode": str(resolution.mode),
        "as_of_date": resolution.as_of_date.isoformat(),
        "selected_report_date": resolution.selected_report_date.isoformat(),
        "selected_filing_date": resolution.selected_filing_date.isoformat(),
        "accession_number": str(resolution.accession_number),
        "holding_count": len(symbols),
        "snapshot_path": str(resolution.snapshot_path),
        "source_url": str(resolution.source_url) if resolution.source_url else None,
    }


def equity_only_scheduled_workflow_decision(
    *,
    current_date: date_type,
    run_frequency: Any,
    weekly_run_weekday: Any,
    attempted_week_keys: set[str],
    attempted_month_keys: set[str],
) -> dict[str, Any]:
    frequency = _normalize_equity_run_frequency(run_frequency)
    weekday = normalize_weekly_run_weekday(weekly_run_weekday)
    week_key = iso_week_key(current_date)
    month_key = _month_key(current_date)
    decision = {
        "date": current_date.isoformat(),
        "run_frequency": frequency,
        "weekly_run_weekday": weekday,
        "week_key": week_key,
        "month_key": month_key,
    }

    if frequency == "daily":
        return {**decision, "should_run": True, "status": "run", "reason": "daily_frequency"}

    if frequency == "monthly":
        if month_key in attempted_month_keys:
            return {
                **decision,
                "should_run": False,
                "status": "skipped",
                "reason": "monthly_workflow_already_attempted",
            }
        return {
            **decision,
            "should_run": True,
            "status": "run",
            "reason": "monthly_first_observed_trading_day",
        }

    if week_key in attempted_week_keys:
        return {
            **decision,
            "should_run": False,
            "status": "skipped",
            "reason": "weekly_workflow_already_attempted",
        }

    current_weekday_index = current_date.weekday()
    preferred_weekday_index = WEEKDAY_INDEX_BY_CODE[weekday]
    if current_weekday_index == preferred_weekday_index:
        return {**decision, "should_run": True, "status": "run", "reason": "weekly_run_day"}
    if current_weekday_index > preferred_weekday_index:
        return {
            **decision,
            "should_run": True,
            "status": "run",
            "reason": "first_observed_after_preferred_weekday",
        }
    return {**decision, "should_run": False, "status": "skipped", "reason": "before_weekly_run_day"}


def equity_only_target_portfolio(
    equity_report: dict[str, Any],
    *,
    equity_universe: list[str],
) -> list[dict[str, Any]]:
    if not isinstance(equity_report, dict):
        raise ValueError("equity report must be an object.")

    basket_id = str(equity_report.get("basket_id") or "").strip().lower()
    if basket_id != EQUITY_BASKET_ID:
        raise ValueError("basket_id must be 'equity'.")

    status = str(equity_report.get("status") or "").strip().lower()
    if status not in ACTIVE_SELECTION_STATUSES:
        raise ValueError("equity report must be active.")

    selected_symbol = str(equity_report.get("selected_symbol") or "").strip().upper()
    if not selected_symbol:
        raise ValueError("selected_symbol must be non-empty.")

    if selected_symbol not in _normalized_universe(equity_universe):
        raise ValueError("selected equity symbol must be in equity universe.")

    return [{"basket_id": EQUITY_BASKET_ID, "symbol": selected_symbol, "target_weight": EQUITY_ONLY_TARGET_WEIGHT}]


class AITradingTeamEquityOnlyLLMStrategy(AITradingTeamGrowthExecutionTestStrategy):
    parameters = {
        "basket_universes": EQUITY_ONLY_BASKET_UNIVERSES,
        "run_frequency": "monthly",
        "weekly_run_weekday": "MON",
        "weekly_holiday_policy": "first_open_trading_day",
    }
    _execution_agent_base_system_prompt_mode = "execution_minimal"

    def get_agent_order_cash_check_price(self, asset: Any, **kwargs: Any) -> dict[str, Any]:
        return target_portfolio_order_cash_check_price(self, asset, **kwargs)

    def _initialize_equity_only_workflow_state(self) -> None:
        self._run_frequency = _normalize_equity_run_frequency(self.parameters.get("run_frequency", "monthly"))
        self._weekly_run_weekday = normalize_weekly_run_weekday(self.parameters.get("weekly_run_weekday", "MON"))
        self._weekly_holiday_policy = str(
            self.parameters.get("weekly_holiday_policy", "first_open_trading_day")
        ).strip()
        if self._weekly_holiday_policy != "first_open_trading_day":
            raise ValueError("weekly_holiday_policy must be 'first_open_trading_day'.")
        self._scheduled_workflow_attempted_week_keys: set[str] = set()
        self._scheduled_workflow_attempted_month_keys: set[str] = set()
        self._scheduled_workflow_events: list[dict[str, Any]] = []
        self._last_scheduled_workflow_run_date: str | None = None
        self._last_target_portfolio_planner_result = None
        self._last_execution_plan_error = None

    def _equity_agent_system_prompt(self, symbols: str) -> str:
        return equity_basket_agent_system_prompt(symbols)

    def _equity_agent_initial_symbols_text(self) -> str:
        basket_universes = self.parameters.get("basket_universes", EQUITY_ONLY_BASKET_UNIVERSES)
        return ", ".join(basket_universes[EQUITY_BASKET_ID])

    def _equity_universe_for_date(self, current_date: str) -> tuple[list[str], dict[str, Any] | None]:
        basket_universes = self.parameters.get("basket_universes", EQUITY_ONLY_BASKET_UNIVERSES)
        return list(basket_universes[EQUITY_BASKET_ID]), None

    def _equity_agent_context(
        self,
        *,
        current_date: str,
        equity_universe: list[str],
        universe_source: dict[str, Any] | None,
    ) -> dict[str, Any]:
        context = {
            "date": current_date,
            "basket_id": EQUITY_BASKET_ID,
            "basket_symbols": equity_universe,
            "target_weight": EQUITY_ONLY_TARGET_WEIGHT,
        }
        if universe_source is not None:
            context["universe_source"] = universe_source
        return context

    def initialize(self):
        self.sleeptime = "1D"
        self._initialize_equity_only_workflow_state()
        model = os.environ.get("AI_TRADING_TEAM_MODEL", "gemini-3.1-flash-lite")
        equity_symbols = self._equity_agent_initial_symbols_text()

        self.agents.create(
            name=EQUITY_AGENT_NAME,
            model=model,
            allow_trading=False,
            include_builtin_tools=False,
            tools=equity_basket_agent_tools(),
            system_prompt=self._equity_agent_system_prompt(equity_symbols),
        )

        self.agents.create(
            name="execution_agent",
            model=model,
            allow_trading=True,
            base_system_prompt_mode=self._execution_agent_base_system_prompt_mode,
            include_builtin_tools=False,
            tools=[BuiltinTools.orders.execute_plan()],
            system_prompt=(
                "Execution role: execute only provided execution_plan. "
                "Call execution_plan_execute exactly once with the complete execution_plan. "
                "Do not manually execute individual orders. "
                "Do not call lower-level order, account, open-order, or price tools when execution_plan_execute "
                "is available. Do not research, change fields, reorder orders, split orders, or repair the plan. "
                "execution_plan_execute returns a concise execution summary for your final answer; "
                "full audit details are recorded in trace/replay for developer inspection. "
                "If the tool returns plan_status=completed, summarize completed orders. If it returns "
                "plan_status=blocked or invalid, summarize where execution stopped and why."
            ),
        )

    def _scheduled_workflow_decision(self, current_date: date_type) -> dict[str, Any]:
        return equity_only_scheduled_workflow_decision(
            current_date=current_date,
            run_frequency=self._run_frequency,
            weekly_run_weekday=self._weekly_run_weekday,
            attempted_week_keys=self._scheduled_workflow_attempted_week_keys,
            attempted_month_keys=self._scheduled_workflow_attempted_month_keys,
        )

    def _record_scheduled_workflow_event(self, event: dict[str, Any]) -> None:
        normalized = dict(event)
        normalized["last_run_date"] = self._last_scheduled_workflow_run_date
        self._scheduled_workflow_events.append(normalized)

    def _mark_scheduled_workflow_attempted(self, event: dict[str, Any]) -> None:
        if event["run_frequency"] == "weekly":
            self._scheduled_workflow_attempted_week_keys.add(event["week_key"])
        if event["run_frequency"] == "monthly":
            self._scheduled_workflow_attempted_month_keys.add(event["month_key"])
        self._last_scheduled_workflow_run_date = event["date"]
        self._record_scheduled_workflow_event(event)

    def _log_equity_only_workflow_blocked(self, message: str) -> None:
        print(message)

    def _record_equity_only_planner_blocker(
        self,
        *,
        current_date: str,
        validator_name: str,
        error: ValueError,
    ) -> None:
        self._last_execution_plan_error = str(error)
        self._log_equity_only_workflow_blocked(
            f"Equity-only LLM workflow blocked on {current_date}: {validator_name}: {error}"
        )

    def _run_pre_execution_validation(
        self,
        *,
        current_date: str,
        validator_name: str,
        callback: Any,
    ) -> bool:
        try:
            callback()
        except ValueError as exc:
            self._record_equity_only_planner_blocker(
                current_date=current_date,
                validator_name=validator_name,
                error=exc,
            )
            return False
        return True

    def _run_equity_only_workflow(
        self,
        current_date: str,
        scheduled_workflow_event: dict[str, Any] | None = None,
    ) -> None:
        try:
            equity_universe, universe_source = self._equity_universe_for_date(current_date)
        except NoSnapshotAvailableError as exc:
            self._last_execution_plan_error = str(exc)
            if scheduled_workflow_event is not None:
                event = {
                    **scheduled_workflow_event,
                    "status": "blocked",
                    "reason": "qqq_historical_universe_unavailable",
                    "error": str(exc),
                }
            else:
                event = {
                    "date": current_date,
                    "run_frequency": self._run_frequency,
                    "weekly_run_weekday": self._weekly_run_weekday,
                    "week_key": iso_week_key(date_type.fromisoformat(current_date)),
                    "month_key": _month_key(date_type.fromisoformat(current_date)),
                    "should_run": False,
                    "status": "blocked",
                    "reason": "qqq_historical_universe_unavailable",
                    "error": str(exc),
                }
            self._record_scheduled_workflow_event(event)
            self._log_equity_only_workflow_blocked(f"Equity-only LLM workflow blocked: {exc}")
            return
        except ValueError as exc:
            if scheduled_workflow_event is not None:
                self._mark_scheduled_workflow_attempted(scheduled_workflow_event)
            self._last_execution_plan_error = str(exc)
            self._log_equity_only_workflow_blocked(f"Equity-only LLM workflow blocked: {exc}")
            return
        if scheduled_workflow_event is not None:
            self._mark_scheduled_workflow_attempted(scheduled_workflow_event)
        try:
            equity_result = self.agents[EQUITY_AGENT_NAME].run(
                task_prompt=equity_basket_agent_task_prompt(),
                context=self._equity_agent_context(
                    current_date=current_date,
                    equity_universe=equity_universe,
                    universe_source=universe_source,
                ),
            )
            equity_report = parse_json_summary(equity_result.summary, EQUITY_AGENT_NAME)
            target_portfolio = equity_only_target_portfolio(equity_report, equity_universe=equity_universe)
            self._last_target_portfolio_planner_result = None
            planner_result = target_portfolio_to_execution_plan(
                self,
                date=current_date,
                target_portfolio=target_portfolio,
            )
            self._last_target_portfolio_planner_result = planner_result
        except ValueError as exc:
            self._last_execution_plan_error = str(exc)
            self._log_equity_only_workflow_blocked(f"Equity-only LLM workflow blocked: {exc}")
            return

        raw_execution_plan = planner_result.get("execution_plan") if isinstance(planner_result, dict) else None
        if not raw_execution_plan:
            self._record_equity_only_planner_blocker(
                current_date=current_date,
                validator_name="target_portfolio_to_execution_plan",
                error=ValueError("target_portfolio_to_execution_plan did not return execution_plan."),
            )
            return
        if (
            isinstance(raw_execution_plan, dict)
            and str(raw_execution_plan.get("intent") or "").strip().lower() == "blocked"
        ):
            blockers = planner_result.get("blockers") if isinstance(planner_result, dict) else None
            message = "target_portfolio_to_execution_plan returned blocked execution_plan."
            if blockers:
                message = f"{message} blockers={blockers}"
            self._record_equity_only_planner_blocker(
                current_date=current_date,
                validator_name="target_portfolio_to_execution_plan",
                error=ValueError(message),
            )
            return
        try:
            execution_plan = normalize_execution_plan(raw_execution_plan)
        except ValueError as exc:
            self._record_equity_only_planner_blocker(
                current_date=current_date,
                validator_name="normalize_execution_plan",
                error=exc,
            )
            return

        validation_steps = [
            (
                "validate_execution_plan_matches_planner_result",
                lambda: validate_execution_plan_matches_planner_result(self, execution_plan),
            ),
            (
                "validate_execution_plan_symbols",
                lambda: validate_execution_plan_symbols(execution_plan, equity_report),
            ),
            (
                "validate_decision_buy_sizing",
                lambda: validate_decision_buy_sizing(self, execution_plan),
            ),
            (
                "validate_execution_plan_cash_safety",
                lambda: validate_execution_plan_cash_safety(self, execution_plan),
            ),
        ]
        for validator_name, callback in validation_steps:
            if not self._run_pre_execution_validation(
                current_date=current_date,
                validator_name=validator_name,
                callback=callback,
            ):
                return

        self._last_execution_plan_error = None
        if execution_plan["intent"] == "hold" or not execution_plan["orders"]:
            return

        self.agents["execution_agent"].run(
            task_prompt=(
                "Execute the provided execution_plan by calling execution_plan_execute exactly once with the "
                "complete execution_plan. Use the returned concise execution summary to write the final result. "
                "Do not infer missing order details beyond the tool response. Do not call per-order tools."
            ),
            context={
                "date": current_date,
                "execution_plan": execution_plan_execute_payload(execution_plan),
            },
        )

    def on_trading_iteration(self):
        current_datetime = self.get_datetime()
        current_date_obj = current_datetime.date()
        current_date = current_date_obj.isoformat()
        cadence_event = self._scheduled_workflow_decision(current_date_obj)
        if not cadence_event["should_run"]:
            self._record_scheduled_workflow_event(cadence_event)
            return
        self._run_equity_only_workflow(current_date, scheduled_workflow_event=cadence_event)


class AITradingTeamQQQHistoricalEquityOnlyLLMStrategy(AITradingTeamEquityOnlyLLMStrategy):
    parameters = {
        **AITradingTeamEquityOnlyLLMStrategy.parameters,
        "run_frequency": "weekly",
        "weekly_run_weekday": "MON",
        "weekly_holiday_policy": "first_open_trading_day",
        "qqq_universe_mode": "strict",
        "qqq_universe_data_dir": None,
    }

    def _equity_agent_system_prompt(self, symbols: str) -> str:
        return qqq_historical_equity_basket_agent_system_prompt(symbols)

    def _equity_agent_initial_symbols_text(self) -> str:
        return "dynamic QQQ historical constituent universe from local N-PORT snapshots"

    def _qqq_universe_data_dir(self) -> str | Path | None:
        data_dir = self.parameters.get("qqq_universe_data_dir")
        if data_dir in (None, ""):
            return None
        return data_dir

    def _equity_universe_for_date(self, current_date: str) -> tuple[list[str], dict[str, Any] | None]:
        mode = _normalize_qqq_universe_mode(self.parameters.get("qqq_universe_mode", "strict"))
        resolution = resolve_qqq_snapshot(
            current_date,
            mode=mode,
            data_dir=self._qqq_universe_data_dir(),
        )
        symbols = _normalized_symbol_list(list(resolution.symbols))
        if not symbols:
            raise ValueError(f"QQQ historical universe resolved no symbols for {current_date}.")
        return symbols, _qqq_universe_source_payload(resolution, symbols)
