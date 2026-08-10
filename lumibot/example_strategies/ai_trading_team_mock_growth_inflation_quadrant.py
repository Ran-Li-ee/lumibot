import hashlib
import json
import math
import os
from datetime import date as date_type
from datetime import datetime
from typing import Any

from lumibot.components.agents.builtins import BuiltinTools
from lumibot.components.agents.schemas import BoundTool, ToolDefinition
from lumibot.example_strategies.ai_trading_team_growth_execution_test import (
    AITradingTeamGrowthExecutionTestStrategy,
    validate_decision_buy_sizing,
    validate_execution_plan_cash_safety,
)
from lumibot.example_strategies.target_portfolio_to_execution_plan import (
    TOOL_NAME as TARGET_PORTFOLIO_TO_EXECUTION_PLAN_TOOL_NAME,
)
from lumibot.example_strategies.target_portfolio_to_execution_plan import (
    make_target_portfolio_to_execution_plan_tool,
)

REGIMES = (
    "growth_up_inflation_down",
    "growth_up_inflation_up",
    "growth_down_inflation_up",
    "growth_down_inflation_down",
)

BASKET_UNIVERSES = {
    "equity": ["SPY", "QQQ", "IWM", "EEM", "FXI"],
    "commodity": ["GLD", "SLV", "DBC", "PDBC", "GSG"],
    "tips": ["TIP", "SCHP", "VTIP", "STIP", "LTPZ"],
    "nominal_bond": ["SHY", "IEF", "TLT", "GOVT", "VGIT"],
}

BASKET_AGENT_NAMES = {
    "equity": "equity_basket_agent",
    "commodity": "commodity_basket_agent",
    "tips": "tips_basket_agent",
    "nominal_bond": "nominal_bond_basket_agent",
}

MOCK_WEIGHT_BY_REGIME = {
    "growth_up_inflation_down": {
        "equity": 0.50,
        "commodity": 0.25,
        "tips": 0.00,
        "nominal_bond": 0.25,
    },
    "growth_up_inflation_up": {
        "equity": 0.25,
        "commodity": 0.50,
        "tips": 0.25,
        "nominal_bond": 0.00,
    },
    "growth_down_inflation_up": {
        "equity": 0.00,
        "commodity": 0.25,
        "tips": 0.50,
        "nominal_bond": 0.25,
    },
    "growth_down_inflation_down": {
        "equity": 0.25,
        "commodity": 0.25,
        "tips": 0.00,
        "nominal_bond": 0.50,
    },
}

ALLOWED_INTENTS = {"hold", "rebalance"}
ALLOWED_ACTIONS = {"submit_order"}
ALLOWED_SIDES = {"buy", "sell"}
ALLOWED_QUANTITY_MODES = {"shares"}
REJECTED_SEMANTIC_QUANTITY_MODES = {
    "current_position",
    "full_position",
    "max_affordable_cash",
    "max_affordable_after_prior_sells",
}
ALLOWED_ORDER_TYPES = {"market"}
MARKET_ONLY_FORBIDDEN_PRICE_FIELDS = (
    "limit_price",
    "stop_price",
    "stop_limit_price",
    "trail_price",
    "trail_percent",
)
SYSTEM_EXECUTION_CONSTRAINTS = {
    "allow_negative_cash": False,
    "if_any_order_blocked": "stop_remaining_orders",
}


def _parse_iso_date(value: str) -> date_type:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"date must use YYYY-MM-DD format, got {value!r}") from exc


def _seeded_regime_index(date: str, seed: int) -> int:
    digest = hashlib.sha256(f"{date}|{seed}".encode()).hexdigest()
    return int(digest[:12], 16) % len(REGIMES)


def _cycle_regime_index(date: str, seed: int) -> int:
    parsed = _parse_iso_date(date)
    return (parsed.toordinal() + int(seed)) % len(REGIMES)


def mock_macro_regime_classifier(
    date: str,
    seed: int = 42,
    mode: str = "seeded_random",
    previous_regime: str | None = None,
) -> dict[str, Any]:
    """Return a reproducible fake Growth / Inflation quadrant for workflow tests."""

    mode = str(mode).strip().lower()
    seed = int(seed)
    if mode == "seeded_random":
        index = _seeded_regime_index(date, seed)
    elif mode == "cycle":
        index = _cycle_regime_index(date, seed)
    else:
        raise ValueError("mock macro regime mode must be 'seeded_random' or 'cycle'.")

    regime = REGIMES[index]
    growth_direction, inflation_direction = regime.split("_inflation_")
    growth_direction = growth_direction.removeprefix("growth_")
    weights = dict(MOCK_WEIGHT_BY_REGIME[regime])
    return {
        "tool": "macro_regime_classifier",
        "mock": True,
        "mode": mode,
        "seed": seed,
        "date": date,
        "regime": regime,
        "growth_direction": growth_direction,
        "inflation_direction": inflation_direction,
        "previous_regime": previous_regime,
        "regime_changed": previous_regime is not None and previous_regime != regime,
        "basket_weights": weights,
        "reason_brief": (
            "Mock classifier selected this regime from deterministic date, seed, and mode logic. "
            "This is not real macro evidence."
        ),
    }


def make_macro_regime_classifier_tool() -> ToolDefinition:
    name = "macro_regime_classifier"
    description = (
        "Return a deterministic mock Growth / Inflation quadrant and basket weights for workflow testing. "
        "This tool does not perform real macro analysis."
    )
    metadata = {"kind": "mock_macro", "mock": True}

    def binder(strategy: Any, manager: Any) -> BoundTool:
        def macro_regime_classifier(
            *,
            date: str | None = None,
            seed: int | None = None,
            mode: str | None = None,
        ) -> dict[str, Any]:
            resolved_date = date or strategy.get_datetime().date().isoformat()
            resolved_seed = int(seed if seed is not None else getattr(strategy, "_mock_regime_seed", 42))
            resolved_mode = mode or getattr(strategy, "_mock_regime_mode", "seeded_random")
            previous_regime = getattr(strategy, "_last_mock_regime", None)
            result = mock_macro_regime_classifier(
                date=resolved_date,
                seed=resolved_seed,
                mode=resolved_mode,
                previous_regime=previous_regime,
            )
            strategy._last_mock_regime = result["regime"]
            return result

        return BoundTool(
            name=name,
            description=description,
            function=macro_regime_classifier,
            source="local",
            metadata=metadata,
        )

    return ToolDefinition(name=name, description=description, binder=binder, metadata=metadata)


def _extract_first_json_object(text: str) -> str:
    if not isinstance(text, str):
        raise ValueError("Portfolio summary must be text.")

    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in portfolio summary.")

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    raise ValueError("Unclosed JSON object in portfolio summary.")


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object.")
    return value


def _normalize_order(order: Any) -> dict[str, Any]:
    order = _require_dict(order, "order")
    for field in ("sequence", "symbol", "side"):
        if field not in order:
            raise ValueError(f"missing required order field: {field}")

    sequence = order["sequence"]
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence <= 0:
        raise ValueError("order sequence must be a positive integer.")

    if not isinstance(order["symbol"], str):
        raise ValueError("order symbol must be a string.")
    symbol = order["symbol"].strip().upper()
    if not symbol:
        raise ValueError("order symbol must be non-empty.")

    side = str(order["side"]).strip().lower()
    if side not in ALLOWED_SIDES:
        raise ValueError(f"unsupported order side: {side}")

    action = str(order.get("action", "submit_order")).strip().lower()
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"unsupported order action: {action}")

    quantity_mode = str(order.get("quantity_mode", "shares")).strip().lower()
    if quantity_mode in REJECTED_SEMANTIC_QUANTITY_MODES:
        raise ValueError(
            "executable orders must use explicit shares quantity_mode; "
            f"got semantic quantity_mode: {quantity_mode}"
        )
    if quantity_mode not in ALLOWED_QUANTITY_MODES:
        raise ValueError(f"unsupported order quantity_mode: {quantity_mode}")

    if "quantity" not in order or order["quantity"] is None:
        raise ValueError("order quantity is required for shares quantity_mode.")
    if isinstance(order["quantity"], bool):
        raise ValueError("order quantity must be a positive whole-share integer.")
    try:
        quantity = float(order["quantity"])
    except (TypeError, ValueError) as exc:
        raise ValueError("order quantity must be positive for shares quantity_mode.") from exc
    if not math.isfinite(quantity) or quantity <= 0:
        raise ValueError("order quantity must be positive for shares quantity_mode.")
    if not quantity.is_integer():
        raise ValueError("order quantity must be a positive whole-share integer.")

    order_type = str(order.get("order_type", "market")).strip().lower()
    if order_type not in ALLOWED_ORDER_TYPES:
        raise ValueError(
            "unsupported order_type for market-only mock quadrant strategy: "
            f"{order_type}. Use order_type='market'."
        )
    for field in MARKET_ONLY_FORBIDDEN_PRICE_FIELDS:
        if order.get(field) is not None:
            raise ValueError(f"market-only execution_plan must not include {field}.")

    return {
        "sequence": sequence,
        "action": action,
        "symbol": symbol,
        "asset_type": str(order.get("asset_type", "stock")).strip().lower(),
        "side": side,
        "quantity": quantity,
        "quantity_mode": quantity_mode,
        "order_type": order_type,
        "time_in_force": str(order.get("time_in_force", "day")).strip().lower(),
    }


def normalize_execution_plan(plan: Any) -> dict[str, Any]:
    plan = _require_dict(plan, "execution_plan")
    if "schema_version" not in plan:
        raise ValueError("execution_plan schema_version is required.")
    schema_version = plan["schema_version"]
    if isinstance(schema_version, bool):
        raise ValueError(f"unsupported execution_plan schema_version: {schema_version}")
    if isinstance(schema_version, (int, float)):
        if schema_version != 1:
            raise ValueError(f"unsupported execution_plan schema_version: {schema_version}")
    elif isinstance(schema_version, str):
        if schema_version.strip() not in {"1", "1.0"}:
            raise ValueError(f"unsupported execution_plan schema_version: {schema_version}")
    else:
        raise ValueError(f"unsupported execution_plan schema_version: {schema_version}")

    intent = str(plan.get("intent") or "").strip().lower()
    if not intent:
        raise ValueError("execution_plan intent is required.")
    if intent not in ALLOWED_INTENTS:
        raise ValueError(f"unsupported execution_plan intent: {intent}")

    if "orders" not in plan:
        raise ValueError("execution_plan orders are required.")
    orders = plan["orders"]
    if not isinstance(orders, list):
        raise ValueError("execution_plan orders must be a list.")
    if intent == "hold" and orders:
        raise ValueError("hold intent cannot include orders.")
    if intent == "rebalance" and not orders:
        raise ValueError("execution_plan orders are required for rebalance intent.")

    normalized_orders = sorted((_normalize_order(order) for order in orders), key=lambda order: order["sequence"])
    order_sequences = [order["sequence"] for order in normalized_orders]
    if len(order_sequences) != len(set(order_sequences)):
        raise ValueError("duplicate order sequence.")

    buy_seen = False
    for order in normalized_orders:
        if order["side"] == "buy":
            buy_seen = True
        elif buy_seen and order["side"] == "sell":
            raise ValueError("execution_plan must place sell orders before buy orders.")

    return {
        "schema_version": 1,
        "intent": intent,
        "orders": normalized_orders,
        "constraints": dict(SYSTEM_EXECUTION_CONSTRAINTS),
    }


def parse_execution_plan_from_portfolio_summary(summary: str) -> dict[str, Any]:
    raw_json = _extract_first_json_object(summary)
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Portfolio summary JSON is invalid: {exc.msg}.") from exc

    payload = _require_dict(payload, "portfolio summary JSON")
    if "execution_plan" not in payload:
        raise ValueError("portfolio summary JSON must include execution_plan.")
    return normalize_execution_plan(payload["execution_plan"])


def _selected_symbols_from_basket_reports(basket_reports: Any) -> set[str]:
    if not isinstance(basket_reports, list):
        raise ValueError("basket_reports must be a list.")

    selected_symbols = set()
    for basket_report in basket_reports:
        basket_report = _require_dict(basket_report, "basket_report")
        status = str(basket_report.get("status") or "").strip().lower()
        selected_symbol = basket_report.get("selected_symbol")
        if status == "active" and selected_symbol:
            selected_symbols.add(str(selected_symbol).strip().upper())
    return selected_symbols


def validate_execution_plan_symbols(execution_plan: dict[str, Any], basket_reports: list[dict[str, Any]]) -> None:
    execution_plan = _require_dict(execution_plan, "execution_plan")
    if execution_plan.get("intent") == "hold":
        return

    selected_symbols = _selected_symbols_from_basket_reports(basket_reports)
    for order in execution_plan.get("orders", []):
        order = _require_dict(order, "order")
        if str(order.get("side") or "").strip().lower() != "buy":
            continue
        symbol = str(order.get("symbol") or "").strip().upper()
        if symbol not in selected_symbols:
            raise ValueError(f"execution_plan buy symbol {symbol} is not selected by any active basket.")


def _parse_json_summary(summary: str, label: str) -> dict[str, Any]:
    try:
        return json.loads(_extract_first_json_object(summary))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} JSON is invalid: {exc.msg}") from exc


def _agent_result_tool_names(result: Any) -> set[str]:
    return {event.tool_name for event in getattr(result, "tool_calls", []) if getattr(event, "tool_name", None)}


def validate_execution_plan_matches_planner_result(strategy: Any, execution_plan: dict[str, Any]) -> None:
    planner_result = getattr(strategy, "_last_target_portfolio_planner_result", None)
    if not isinstance(planner_result, dict):
        raise ValueError(
            "portfolio_decision_agent must successfully call target_portfolio_to_execution_plan before execution."
        )
    planner_plan = normalize_execution_plan(planner_result.get("execution_plan"))
    execution_plan = normalize_execution_plan(execution_plan)
    if execution_plan != planner_plan:
        raise ValueError(
            "portfolio_decision_agent execution_plan differs from target_portfolio_to_execution_plan result."
        )


def validate_portfolio_decision_tool_evidence(execution_plan: dict[str, Any], decision_result: Any) -> None:
    tool_names = _agent_result_tool_names(decision_result)
    if TARGET_PORTFOLIO_TO_EXECUTION_PLAN_TOOL_NAME not in tool_names:
        raise ValueError("portfolio_decision_agent must call target_portfolio_to_execution_plan.")


class AITradingTeamMockGrowthInflationQuadrantStrategy(AITradingTeamGrowthExecutionTestStrategy):
    parameters = {
        "basket_universes": BASKET_UNIVERSES,
        "mock_regime_mode": "seeded_random",
        "mock_regime_seed": 42,
    }
    _execution_agent_base_system_prompt_mode = "execution_minimal"

    def initialize(self):
        self.sleeptime = "1D"
        self._mock_regime_mode = self.parameters.get("mock_regime_mode", "seeded_random")
        self._mock_regime_seed = int(self.parameters.get("mock_regime_seed", 42))
        self._last_mock_regime = None
        self._last_target_portfolio_planner_result = None
        model = os.environ.get("AI_TRADING_TEAM_MODEL", "gemini-3.1-flash-lite")

        self.agents.create(
            name="macro_allocation_agent",
            model=model,
            allow_trading=False,
            include_builtin_tools=False,
            tools=[make_macro_regime_classifier_tool()],
            system_prompt=(
                "Macro allocation role: call the mock macro_regime_classifier and return the regime, "
                "basket weights, and a compact allocation note. Do not place orders."
            ),
        )

        basket_universes = self.parameters.get("basket_universes", BASKET_UNIVERSES)
        for basket_id, agent_name in BASKET_AGENT_NAMES.items():
            symbols = ", ".join(basket_universes[basket_id])
            self.agents.create(
                name=agent_name,
                model=model,
                allow_trading=False,
                include_builtin_tools=False,
                tools=[
                    BuiltinTools.market.load_history_tables_summary(),
                    BuiltinTools.market.last_price(),
                ],
                system_prompt=(
                    f"{basket_id.replace('_', ' ').title()} basket role: stay inside the assigned basket "
                    f"({symbols}). Select one symbol when active, or report inactive when its target weight is "
                    "zero. Return basket_id, selected_symbol, status, and reason_brief. Do not place orders."
                ),
            )

        self.agents.create(
            name="portfolio_decision_agent",
            model=model,
            allow_trading=False,
            include_builtin_tools=False,
            tools=[make_target_portfolio_to_execution_plan_tool()],
            system_prompt=(
                "Portfolio decision role: do not redo macro or basket research. Merge the macro allocation report "
                "and basket reports into a target_portfolio, then call "
                f"{TARGET_PORTFOLIO_TO_EXECUTION_PLAN_TOOL_NAME}. Do not "
                "place orders. Do not manually calculate share quantities, cash usage, order side, or order sequence. "
                "The planner tool owns all execution_plan calculations. Return only one valid JSON object; do not "
                "include markdown, RESULT text, or prose after the JSON. The top-level fields decision, "
                "target_portfolio, and execution_plan are required. decision must include type and reason_brief. "
                "target_portfolio must list the selected active basket targets as symbols and target weights. "
                "The final execution_plan must be copied exactly from the planner tool result. Do not modify "
                "tool-generated quantities, sides, order_type, time_in_force, or sequence values."
            ),
        )

        self.agents.create(
            name="execution_agent",
            model=model,
            allow_trading=True,
            base_system_prompt_mode=self._execution_agent_base_system_prompt_mode,
            include_builtin_tools=False,
            tools=[
                BuiltinTools.orders.preflight(),
                BuiltinTools.orders.submit_and_confirm(),
            ],
            system_prompt=(
                "Execution role: execute only the provided execution_plan using the listed order tools. "
                "For each order in sequence, call orders_preflight_check with the exact order fields. "
                "If preflight returns can_submit=true, call orders_submit_and_confirm_order with that same order. "
                "Continue to the next order only when the combined tool returns can_continue=true. "
                "If preflight returns can_submit=false or the combined tool returns can_continue=false, stop "
                "remaining orders and report the blocker. Do not perform investment research, do not change "
                "order fields, and do not call lower-level submit or confirm tools when the combined tool is available."
            ),
        )

    def on_trading_iteration(self):
        current_date = self.get_datetime().date().isoformat()
        basket_universes = self.parameters.get("basket_universes", BASKET_UNIVERSES)

        try:
            macro_result = self.agents["macro_allocation_agent"].run(
                task_prompt=(
                    "Run the mock macro allocation step and return one JSON object with regime, "
                    "basket_weights, mock flag, regime_changed, and reason_brief."
                ),
                context={
                    "date": current_date,
                    "mock_regime_mode": self._mock_regime_mode,
                    "mock_regime_seed": self._mock_regime_seed,
                    "basket_universes": basket_universes,
                },
            )
            macro_report = _parse_json_summary(macro_result.summary, "macro_allocation_agent")

            basket_reports_by_id = {}
            basket_weights = _require_dict(macro_report.get("basket_weights", {}), "macro basket_weights")
            for basket_id, agent_name in BASKET_AGENT_NAMES.items():
                basket_result = self.agents[agent_name].run(
                    task_prompt=(
                        "Review only the assigned basket and return one JSON object with basket_id, "
                        "target_weight, status, candidate_symbols, selected_symbol, and reason_brief."
                    ),
                    context={
                        "date": current_date,
                        "basket_id": basket_id,
                        "basket_symbols": basket_universes[basket_id],
                        "target_weight": float(basket_weights.get(basket_id, 0.0)),
                        "macro_allocation_report": macro_report,
                    },
                )
                basket_reports_by_id[basket_id] = _parse_json_summary(basket_result.summary, agent_name)

            self._last_target_portfolio_planner_result = None
            portfolio_result = self.agents["portfolio_decision_agent"].run(
                task_prompt=(
                    "Create target_portfolio from the provided macro and basket reports, then call "
                    f"{TARGET_PORTFOLIO_TO_EXECUTION_PLAN_TOOL_NAME} with date and target_portfolio. "
                    "Return only the strict JSON object with decision, target_portfolio, and the planner tool's "
                    "execution_plan copied exactly."
                ),
                context={
                    "date": current_date,
                    "macro_allocation_report": macro_report,
                    "equity_basket_report": basket_reports_by_id["equity"],
                    "commodity_basket_report": basket_reports_by_id["commodity"],
                    "tips_basket_report": basket_reports_by_id["tips"],
                    "nominal_bond_basket_report": basket_reports_by_id["nominal_bond"],
                },
            )
            execution_plan = parse_execution_plan_from_portfolio_summary(portfolio_result.summary)
            validate_portfolio_decision_tool_evidence(execution_plan, portfolio_result)
            validate_execution_plan_matches_planner_result(self, execution_plan)
            validate_execution_plan_symbols(execution_plan, list(basket_reports_by_id.values()))
            validate_decision_buy_sizing(self, execution_plan)
            validate_execution_plan_cash_safety(self, execution_plan)
        except ValueError as exc:
            self._last_execution_plan_error = str(exc)
            print(f"Mock quadrant workflow blocked: {exc}")
            return

        self._last_execution_plan_error = None
        if execution_plan["intent"] == "hold" or not execution_plan["orders"]:
            return

        self.agents["execution_agent"].run(
            task_prompt=(
                "Execute the provided execution_plan in sequence order. For each order, call orders_preflight_check "
                "with the exact order fields. If preflight allows it, call orders_submit_and_confirm_order with "
                "that same order. Stop if any preflight or submit-and-confirm result blocks continuation."
            ),
            context={
                "date": current_date,
                "execution_plan": execution_plan,
            },
        )
