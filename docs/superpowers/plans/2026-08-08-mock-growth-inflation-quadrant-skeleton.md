# Mock Growth / Inflation Quadrant Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first runnable mock Growth / Inflation quadrant agent workflow with 7 agents, deterministic mock regime classification, basket-level selection, strict portfolio execution handoff, and replay UI visibility.

**Architecture:** Add a new example strategy rather than modifying the existing growth-decision-execution strategy. Keep the mock macro regime classifier local to the new strategy file, then wire macro allocation, four basket agents, portfolio decision, and execution through the existing LumiBot agent manager and tools. Reuse the existing execution boundary ideas, but use a new parser/validator that permits multi-basket execution plans instead of the old single-buy test strategy contract.

**Tech Stack:** Python, LumiBot `Strategy`, LumiBot agent manager, `BuiltinTools`, local callable tool binding, pytest, ruff, YahooDataBacktesting for manual backtest validation.

---

## File Structure

Create:

- `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`
  - Holds basket constants, mock regime classifier, local tool factory, parser/validator helpers, prompts, and the new strategy class.
- `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`
  - Focused tests for this new strategy and no changes to the old strategy tests.

Modify:

- `scripts/run_ai_trading_team_examples_benchmark.py`
  - Add the new strategy to the existing examples benchmark map so manual paid one-day runs can target it by name.

Do not modify:

- `lumibot/example_strategies/ai_trading_team_growth_execution_test.py`
- `tests/test_ai_trading_team_growth_execution_test.py`
- Any replay UI code unless tests reveal the existing UI cannot discover the new trace shape.
- `docs/strategy_research/`
- `project_notes/Workflow.vsdx`

---

### Task 1: Add Mock Regime And Basket Constants

**Files:**
- Create: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`
- Create: `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Write failing tests for basket constants and deterministic mock classifier**

Add this initial test file:

```python
import importlib
import json
from datetime import datetime
from types import SimpleNamespace

import pytest


def load_strategy_module():
    module = importlib.import_module(
        "lumibot.example_strategies.ai_trading_team_mock_growth_inflation_quadrant"
    )
    return module, module.AITradingTeamMockGrowthInflationQuadrantStrategy


def test_basket_universes_have_at_least_five_semantically_valid_symbols():
    module, _strategy_class = load_strategy_module()

    assert module.BASKET_UNIVERSES == {
        "equity": ["SPY", "QQQ", "IWM", "EEM", "FXI"],
        "commodity": ["GLD", "SLV", "DBC", "PDBC", "GSG"],
        "tips": ["TIP", "SCHP", "VTIP", "STIP", "LTPZ"],
        "nominal_bond": ["SHY", "IEF", "TLT", "GOVT", "VGIT"],
    }
    for symbols in module.BASKET_UNIVERSES.values():
        assert len(symbols) >= 5
        assert len(symbols) == len(set(symbols))


def test_mock_weight_mapping_uses_50_25_25_0_and_sums_to_one():
    module, _strategy_class = load_strategy_module()

    assert set(module.MOCK_WEIGHT_BY_REGIME) == set(module.REGIMES)
    for regime, weights in module.MOCK_WEIGHT_BY_REGIME.items():
        assert set(weights) == set(module.BASKET_UNIVERSES)
        assert sorted(weights.values()) == [0.0, 0.25, 0.25, 0.5], regime
        assert sum(weights.values()) == pytest.approx(1.0)


def test_mock_regime_classifier_seeded_random_is_reproducible():
    module, _strategy_class = load_strategy_module()

    first = module.mock_macro_regime_classifier(date="2024-09-05", seed=42, mode="seeded_random")
    second = module.mock_macro_regime_classifier(date="2024-09-05", seed=42, mode="seeded_random")
    different_seed = module.mock_macro_regime_classifier(date="2024-09-05", seed=43, mode="seeded_random")

    assert first == second
    assert first["mock"] is True
    assert first["mode"] == "seeded_random"
    assert first["seed"] == 42
    assert first["date"] == "2024-09-05"
    assert first["regime"] in module.REGIMES
    assert first["basket_weights"] == module.MOCK_WEIGHT_BY_REGIME[first["regime"]]
    assert first["reason_brief"].startswith("Mock classifier")
    assert different_seed["regime"] in module.REGIMES


def test_mock_regime_classifier_cycle_mode_walks_quadrants_by_date():
    module, _strategy_class = load_strategy_module()

    seen = [
        module.mock_macro_regime_classifier(date=f"2024-09-0{day}", seed=7, mode="cycle")["regime"]
        for day in range(2, 6)
    ]

    assert len(set(seen)) == 4
    assert all(regime in module.REGIMES for regime in seen)


def test_mock_regime_classifier_reports_previous_regime_and_change_flag():
    module, _strategy_class = load_strategy_module()

    result = module.mock_macro_regime_classifier(
        date="2024-09-05",
        seed=42,
        mode="seeded_random",
        previous_regime="growth_up_inflation_down",
    )

    assert result["previous_regime"] == "growth_up_inflation_down"
    assert result["regime_changed"] == (result["regime"] != "growth_up_inflation_down")
```

- [ ] **Step 2: Run the tests and verify they fail because the module does not exist yet**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected: failure with `ModuleNotFoundError: No module named 'lumibot.example_strategies.ai_trading_team_mock_growth_inflation_quadrant'`.

- [ ] **Step 3: Create the new strategy module with constants and mock classifier only**

Create `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py` with:

```python
import hashlib
import json
import math
import os
from datetime import date as date_type
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from lumibot.components.agents.builtins import BuiltinTools
from lumibot.components.agents.schemas import BoundTool
from lumibot.strategies.strategy import Strategy

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

MOCK_WEIGHT_BY_REGIME = {
    "growth_up_inflation_down": {
        "equity": 0.50,
        "commodity": 0.00,
        "tips": 0.25,
        "nominal_bond": 0.25,
    },
    "growth_up_inflation_up": {
        "equity": 0.50,
        "commodity": 0.25,
        "tips": 0.25,
        "nominal_bond": 0.00,
    },
    "growth_down_inflation_up": {
        "equity": 0.00,
        "commodity": 0.50,
        "tips": 0.25,
        "nominal_bond": 0.25,
    },
    "growth_down_inflation_down": {
        "equity": 0.25,
        "commodity": 0.00,
        "tips": 0.25,
        "nominal_bond": 0.50,
    },
}


def _parse_iso_date(value: str) -> date_type:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"date must use YYYY-MM-DD format, got {value!r}") from exc


def _seeded_regime_index(*, date: str, seed: int) -> int:
    digest = hashlib.sha256(f"{date}|{seed}".encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % len(REGIMES)


def _cycle_regime_index(*, date: str, seed: int) -> int:
    parsed = _parse_iso_date(date)
    return (parsed.toordinal() + int(seed)) % len(REGIMES)


def mock_macro_regime_classifier(
    *,
    date: str,
    seed: int = 42,
    mode: str = "seeded_random",
    previous_regime: str | None = None,
) -> dict[str, Any]:
    """Return a reproducible fake Growth / Inflation quadrant for workflow tests."""

    mode = str(mode).strip().lower()
    seed = int(seed)
    if mode == "seeded_random":
        index = _seeded_regime_index(date=date, seed=seed)
    elif mode == "cycle":
        index = _cycle_regime_index(date=date, seed=seed)
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
```

- [ ] **Step 4: Run the tests and verify they pass**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected: all tests in this file pass.

- [ ] **Step 5: Commit**

```powershell
git add -- lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py tests/test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "feat: add mock quadrant classifier constants"
```

---

### Task 2: Add Strategy-Local Macro Tool Binding

**Files:**
- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`
- Modify: `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Add failing tests for the local tool factory**

Append to `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`:

```python
def test_make_macro_regime_classifier_tool_returns_bound_tool_and_updates_previous_regime():
    module, _strategy_class = load_strategy_module()
    strategy = SimpleNamespace(
        _last_mock_regime=None,
        _mock_regime_mode="cycle",
        _mock_regime_seed=7,
        get_datetime=lambda: datetime(2024, 9, 5, 9, 30),
    )

    tool = module.make_macro_regime_classifier_tool(strategy)
    first = tool.function()
    second = tool.function(date="2024-09-06")

    assert tool.name == "macro_regime_classifier"
    assert "deterministic mock Growth / Inflation quadrant" in tool.description
    assert tool.metadata == {"kind": "mock_macro", "mock": True}
    assert first["previous_regime"] is None
    assert second["previous_regime"] == first["regime"]
    assert strategy._last_mock_regime == second["regime"]
```

- [ ] **Step 2: Run the new test and verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ai_trading_team_mock_growth_inflation_quadrant.py::test_make_macro_regime_classifier_tool_returns_bound_tool_and_updates_previous_regime -q
```

Expected: failure with `AttributeError` because `make_macro_regime_classifier_tool` does not exist.

- [ ] **Step 3: Implement the local tool factory**

Append to `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py` after `mock_macro_regime_classifier`:

```python
def make_macro_regime_classifier_tool(strategy: Any) -> BoundTool:
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
        name="macro_regime_classifier",
        description=(
            "Return a deterministic mock Growth / Inflation quadrant and basket weights for workflow testing. "
            "This tool does not perform real macro analysis."
        ),
        function=macro_regime_classifier,
        source="local",
        metadata={"kind": "mock_macro", "mock": True},
    )
```

- [ ] **Step 4: Run the full new test file**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add -- lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py tests/test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "feat: expose mock macro classifier as local agent tool"
```

---

### Task 3: Add Multi-Basket Execution Plan Parser And Validator

**Files:**
- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`
- Modify: `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Add failing parser and validation tests**

Append:

```python
def test_parse_execution_plan_accepts_multiple_market_buy_orders():
    module, _strategy_class = load_strategy_module()
    raw_summary = json.dumps(
        {
            "decision": {"type": "rebalance", "reason_brief": "mock portfolio target"},
            "execution_plan": {
                "schema_version": 1,
                "intent": "rebalance",
                "orders": [
                    {
                        "sequence": 2,
                        "action": "submit_order",
                        "symbol": "TIP",
                        "side": "buy",
                        "quantity_mode": "shares",
                        "quantity": 100,
                        "asset_type": "stock",
                        "order_type": "market",
                        "time_in_force": "day",
                    },
                    {
                        "sequence": 1,
                        "action": "submit_order",
                        "symbol": "QQQ",
                        "side": "buy",
                        "quantity_mode": "shares",
                        "quantity": 50,
                        "asset_type": "stock",
                        "order_type": "market",
                        "time_in_force": "day",
                    },
                ],
            },
        }
    )

    plan = module.parse_execution_plan_from_portfolio_summary(raw_summary)

    assert plan["intent"] == "rebalance"
    assert [order["symbol"] for order in plan["orders"]] == ["QQQ", "TIP"]
    assert [order["quantity"] for order in plan["orders"]] == [50.0, 100.0]


@pytest.mark.parametrize(
    "bad_order, message",
    [
        ({"sequence": 1, "symbol": "QQQ", "side": "buy", "quantity_mode": "full_position", "quantity": 1, "order_type": "market"}, "semantic quantity_mode"),
        ({"sequence": 1, "symbol": "QQQ", "side": "buy", "quantity_mode": "shares", "quantity": 1.5, "order_type": "market"}, "whole-share integer"),
        ({"sequence": 1, "symbol": "QQQ", "side": "buy", "quantity_mode": "shares", "quantity": 1, "order_type": "limit"}, "market-only"),
        ({"sequence": 1, "symbol": "QQQ", "side": "buy", "quantity_mode": "shares", "quantity": 1, "order_type": "market", "limit_price": 10}, "must not include limit_price"),
    ],
)
def test_parse_execution_plan_rejects_non_strict_orders(bad_order, message):
    module, _strategy_class = load_strategy_module()
    raw_summary = json.dumps({"execution_plan": {"schema_version": 1, "intent": "rebalance", "orders": [bad_order]}})

    with pytest.raises(ValueError, match=message):
        module.parse_execution_plan_from_portfolio_summary(raw_summary)


def test_parse_execution_plan_rejects_sell_after_buy_sequence():
    module, _strategy_class = load_strategy_module()
    raw_summary = json.dumps(
        {
            "execution_plan": {
                "schema_version": 1,
                "intent": "rebalance",
                "orders": [
                    {"sequence": 1, "symbol": "QQQ", "side": "buy", "quantity_mode": "shares", "quantity": 1, "order_type": "market"},
                    {"sequence": 2, "symbol": "SPY", "side": "sell", "quantity_mode": "shares", "quantity": 1, "order_type": "market"},
                ],
            }
        }
    )

    with pytest.raises(ValueError, match="sell orders before buy orders"):
        module.parse_execution_plan_from_portfolio_summary(raw_summary)


def test_validate_plan_symbols_match_selected_basket_reports():
    module, _strategy_class = load_strategy_module()
    plan = {
        "schema_version": 1,
        "intent": "rebalance",
        "orders": [
            {"sequence": 1, "symbol": "QQQ", "side": "buy", "quantity": 10.0, "quantity_mode": "shares", "order_type": "market"},
            {"sequence": 2, "symbol": "TIP", "side": "buy", "quantity": 10.0, "quantity_mode": "shares", "order_type": "market"},
        ],
        "constraints": {"allow_negative_cash": False, "if_any_order_blocked": "stop_remaining_orders"},
    }
    basket_reports = [
        {"basket_id": "equity", "status": "active", "selected_symbol": "QQQ"},
        {"basket_id": "tips", "status": "active", "selected_symbol": "TIP"},
        {"basket_id": "commodity", "status": "inactive", "selected_symbol": None},
    ]

    module.validate_execution_plan_symbols(plan, basket_reports)


def test_validate_plan_symbols_rejects_unselected_buy_symbol():
    module, _strategy_class = load_strategy_module()
    plan = {
        "schema_version": 1,
        "intent": "rebalance",
        "orders": [
            {"sequence": 1, "symbol": "FXI", "side": "buy", "quantity": 10.0, "quantity_mode": "shares", "order_type": "market"},
        ],
        "constraints": {"allow_negative_cash": False, "if_any_order_blocked": "stop_remaining_orders"},
    }
    basket_reports = [{"basket_id": "equity", "status": "active", "selected_symbol": "QQQ"}]

    with pytest.raises(ValueError, match="not selected by any active basket"):
        module.validate_execution_plan_symbols(plan, basket_reports)
```

- [ ] **Step 2: Run tests and verify parser functions are missing**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected: failures mentioning `parse_execution_plan_from_portfolio_summary` and `validate_execution_plan_symbols`.

- [ ] **Step 3: Implement parser and symbol validator**

Append these helpers before the strategy class:

```python
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


def _extract_first_json_object(text: str) -> str:
    if not isinstance(text, str):
        raise ValueError("Decision summary must be text.")
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in portfolio decision summary.")
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
    raise ValueError("Unclosed JSON object in portfolio decision summary.")


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object.")
    return value


def _normalize_order(order: Any) -> dict[str, Any]:
    order = _require_dict(order, "order")
    for field in ("sequence", "symbol", "side", "quantity_mode"):
        if field not in order:
            raise ValueError(f"missing required order field: {field}")
    sequence = order["sequence"]
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence <= 0:
        raise ValueError("order sequence must be a positive integer.")
    symbol = str(order["symbol"]).strip().upper()
    if not symbol:
        raise ValueError("order symbol must be non-empty.")
    side = str(order["side"]).strip().lower()
    if side not in ALLOWED_SIDES:
        raise ValueError(f"unsupported order side: {side}")
    action = str(order.get("action", "submit_order")).strip().lower()
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"unsupported order action: {action}")
    quantity_mode = str(order["quantity_mode"]).strip().lower()
    if quantity_mode in REJECTED_SEMANTIC_QUANTITY_MODES:
        raise ValueError(f"semantic quantity_mode is not executable: {quantity_mode}")
    if quantity_mode not in ALLOWED_QUANTITY_MODES:
        raise ValueError(f"unsupported order quantity_mode: {quantity_mode}")
    try:
        quantity = float(order.get("quantity"))
    except (TypeError, ValueError) as exc:
        raise ValueError("order quantity must be positive for shares quantity_mode.") from exc
    if not math.isfinite(quantity) or quantity <= 0:
        raise ValueError("order quantity must be positive for shares quantity_mode.")
    if not quantity.is_integer():
        raise ValueError("order quantity must be a positive whole-share integer.")
    order_type = str(order.get("order_type", "market")).strip().lower()
    if order_type not in ALLOWED_ORDER_TYPES:
        raise ValueError(f"market-only execution_plan cannot use order_type {order_type}.")
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


def parse_execution_plan_from_portfolio_summary(summary: str) -> dict[str, Any]:
    payload = json.loads(_extract_first_json_object(summary))
    payload = _require_dict(payload, "portfolio decision summary JSON")
    plan = _require_dict(payload.get("execution_plan"), "execution_plan")
    schema_version = plan.get("schema_version")
    if schema_version not in {1, 1.0, "1", "1.0"}:
        raise ValueError(f"unsupported execution_plan schema_version: {schema_version}")
    intent = str(plan.get("intent") or "").strip().lower()
    if intent not in ALLOWED_INTENTS:
        raise ValueError(f"unsupported execution_plan intent: {intent}")
    orders = plan.get("orders")
    if not isinstance(orders, list):
        raise ValueError("execution_plan orders must be a list.")
    if intent == "hold" and orders:
        raise ValueError("hold intent cannot include orders.")
    if intent != "hold" and not orders:
        raise ValueError("execution_plan orders are required for non-hold intent.")
    normalized_orders = sorted((_normalize_order(order) for order in orders), key=lambda item: item["sequence"])
    sequences = [order["sequence"] for order in normalized_orders]
    if len(sequences) != len(set(sequences)):
        raise ValueError("duplicate order sequence.")
    first_buy_index = next((index for index, order in enumerate(normalized_orders) if order["side"] == "buy"), None)
    if first_buy_index is not None and any(order["side"] == "sell" for order in normalized_orders[first_buy_index + 1 :]):
        raise ValueError("execution_plan must place all sell orders before buy orders.")
    return {
        "schema_version": 1,
        "intent": intent,
        "orders": normalized_orders,
        "constraints": dict(SYSTEM_EXECUTION_CONSTRAINTS),
    }


def _selected_symbols_from_basket_reports(basket_reports: list[dict[str, Any]]) -> set[str]:
    selected = set()
    for report in basket_reports:
        if report.get("status") != "active":
            continue
        symbol = report.get("selected_symbol")
        if isinstance(symbol, str) and symbol.strip():
            selected.add(symbol.strip().upper())
    return selected


def validate_execution_plan_symbols(execution_plan: dict[str, Any], basket_reports: list[dict[str, Any]]) -> None:
    if execution_plan["intent"] == "hold":
        return
    selected_symbols = _selected_symbols_from_basket_reports(basket_reports)
    for order in execution_plan["orders"]:
        if order["side"] == "buy" and order["symbol"] not in selected_symbols:
            raise ValueError(f"execution_plan buy symbol {order['symbol']} was not selected by any active basket.")
```

- [ ] **Step 4: Run the test file**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add -- lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py tests/test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "feat: validate mock quadrant execution plans"
```

---

### Task 4: Create Seven Agents With Clean Tool Surfaces And Prompts

**Files:**
- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`
- Modify: `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Add fake agent manager helpers and failing initialization tests**

Append near the top of the test file after imports:

```python
class RecordingAgentManager:
    def __init__(self):
        self.created = []
        self._agents = {}
        self.summaries = {}
        self.tool_calls = {}

    def create(self, **kwargs):
        self.created.append(kwargs)
        agent = RecordingAgent(kwargs["name"], self.summaries, self.tool_calls)
        self._agents[kwargs["name"]] = agent
        return agent

    def __getitem__(self, name):
        return self._agents[name]


class RecordingAgent:
    def __init__(self, name, summaries=None, tool_calls=None):
        self.name = name
        self.summaries = summaries if summaries is not None else {}
        self.tool_calls = tool_calls if tool_calls is not None else {}
        self.calls = []

    def run(self, *, task_prompt, context):
        self.calls.append({"task_prompt": task_prompt, "context": context})
        summary = self.summaries.get(self.name, f"{self.name} summary")
        return SimpleNamespace(
            summary=summary,
            tool_calls=[SimpleNamespace(tool_name=tool_name) for tool_name in self.tool_calls.get(self.name, [])],
        )


def make_strategy_with_agent_manager(strategy_class, agent_manager):
    strategy = object.__new__(strategy_class)
    strategy.agents = agent_manager
    strategy.parameters = dict(strategy_class.parameters)
    strategy.get_datetime = lambda: datetime(2024, 9, 5, 9, 30)
    strategy.get_cash = lambda: 100000.0
    strategy.get_portfolio_value = lambda: 100000.0
    strategy.get_last_price = lambda symbol: 100.0
    strategy.get_positions = lambda include_cash_positions=False: []
    return strategy


def created_tool_names(created_agent):
    return {getattr(tool, "name", "") for tool in created_agent.get("tools", [])}
```

Append tests:

```python
def test_initialize_creates_seven_agent_mock_quadrant_workflow(monkeypatch):
    _module, strategy_class = load_strategy_module()
    monkeypatch.setenv("AI_TRADING_TEAM_MODEL", "test-model")
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.initialize()

    assert [agent["name"] for agent in agent_manager.created] == [
        "macro_allocation_agent",
        "equity_basket_agent",
        "commodity_basket_agent",
        "tips_basket_agent",
        "nominal_bond_basket_agent",
        "portfolio_decision_agent",
        "execution_agent",
    ]
    assert [agent["allow_trading"] for agent in agent_manager.created] == [
        False,
        False,
        False,
        False,
        False,
        False,
        True,
    ]
    assert strategy.sleeptime == "1D"
    assert strategy._mock_regime_mode == "seeded_random"
    assert strategy._mock_regime_seed == 42


def test_agents_receive_distinct_tool_surfaces():
    _module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.initialize()

    created = {agent["name"]: agent for agent in agent_manager.created}
    assert created_tool_names(created["macro_allocation_agent"]) == {"macro_regime_classifier"}
    for basket_agent in (
        "equity_basket_agent",
        "commodity_basket_agent",
        "tips_basket_agent",
        "nominal_bond_basket_agent",
    ):
        assert created[basket_agent]["include_builtin_tools"] is False
        assert created_tool_names(created[basket_agent]) == {
            "market_load_history_tables_summary",
            "market_last_price",
        }
    assert created_tool_names(created["portfolio_decision_agent"]) == {
        "account_positions",
        "account_portfolio",
        "market_last_price",
    }
    assert created_tool_names(created["execution_agent"]) == {
        "account_positions",
        "account_portfolio",
        "market_last_price",
        "orders_open_orders",
        "orders_submit_order",
        "orders_confirm_order",
    }
    for non_execution_agent in agent_manager.created[:-1]:
        assert "orders_submit_order" not in created_tool_names(non_execution_agent)
        assert "orders_confirm_order" not in created_tool_names(non_execution_agent)


def test_prompt_boundaries_are_short_and_role_specific():
    _module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.initialize()

    serialized = json.dumps(agent_manager.created, default=str).lower()
    for forbidden_phrase in (
        "prefer doing nothing",
        "avoid overtrading",
        "duckdb",
        "test turnover",
        "limit order",
        "stop loss",
        "cash_buffer_pct",
    ):
        assert forbidden_phrase not in serialized
    assert "call the mock macro_regime_classifier" in serialized
    assert "stay inside the assigned basket" in serialized
    assert "do not redo macro or basket research" in serialized
    assert "execute only the provided execution_plan" in serialized
```

- [ ] **Step 2: Run tests and verify strategy class methods are missing**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected: failures around missing `parameters`, missing `initialize`, or missing class behavior.

- [ ] **Step 3: Implement strategy class and agent initialization**

Append to `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`:

```python
BASKET_AGENT_NAMES = {
    "equity": "equity_basket_agent",
    "commodity": "commodity_basket_agent",
    "tips": "tips_basket_agent",
    "nominal_bond": "nominal_bond_basket_agent",
}


class AITradingTeamMockGrowthInflationQuadrantStrategy(Strategy):
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
        model = os.environ.get("AI_TRADING_TEAM_MODEL", "gemini-3.1-flash-lite")

        self.agents.create(
            name="macro_allocation_agent",
            model=model,
            allow_trading=False,
            include_builtin_tools=False,
            tools=[make_macro_regime_classifier_tool(self)],
            system_prompt=(
                "Macro allocation agent role: call the mock macro_regime_classifier and report only its mock "
                "Growth / Inflation quadrant, regime_changed flag, and basket_weights. Do not choose instruments, "
                "do not perform market research, and do not create orders."
            ),
        )

        for basket_id, agent_name in BASKET_AGENT_NAMES.items():
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
                    f"{basket_id} basket agent role: stay inside the assigned basket symbols from context. "
                    "If target_weight is zero, return inactive with selected_symbol null. If target_weight is "
                    "positive, use compact price summary evidence to select one representative symbol. Do not "
                    "change basket weights, do not choose outside the basket, and do not create orders."
                ),
            )

        self.agents.create(
            name="portfolio_decision_agent",
            model=model,
            allow_trading=False,
            include_builtin_tools=False,
            tools=[
                BuiltinTools.account.positions(),
                BuiltinTools.account.portfolio(),
                BuiltinTools.market.last_price(),
            ],
            system_prompt=(
                "Portfolio decision agent role: merge macro_allocation_report and the four basket reports into "
                "a target portfolio and strict execution_plan. Respect basket_weights and selected_symbol values. "
                "Before a non-hold plan, call account_positions and account_portfolio; call market_last_price for "
                "buy sizing. Return only one JSON object with decision, target_portfolio, and execution_plan. "
                "Use explicit whole-share numeric quantities and market orders. Do not redo macro or basket "
                "research, do not re-rank symbols, and do not place orders."
            ),
        )

        self.agents.create(
            name="execution_agent",
            model=model,
            allow_trading=True,
            base_system_prompt_mode=self._execution_agent_base_system_prompt_mode,
            include_builtin_tools=False,
            tools=[
                BuiltinTools.account.positions(),
                BuiltinTools.account.portfolio(),
                BuiltinTools.market.last_price(),
                BuiltinTools.orders.open_orders(),
                BuiltinTools.orders.submit(),
                BuiltinTools.orders.confirm(),
            ],
            system_prompt=(
                "Execution agent role: execute only the provided execution_plan using native order tools. Treat "
                "execution_plan.orders as authoritative. Do not infer investment reasons, re-rank symbols, "
                "substitute symbols, add orders, remove orders, or reorder orders. Use only market orders. Inspect "
                "positions, portfolio, open orders, and latest prices before submitting. After every "
                "orders_submit_order call, immediately call orders_confirm_order before continuing."
            ),
        )
```

- [ ] **Step 4: Run tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add -- lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py tests/test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "feat: create mock quadrant agent workflow"
```

---

### Task 5: Implement Trading Iteration Handoff And Execution Boundary

**Files:**
- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`
- Modify: `lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py`

- [ ] **Step 1: Add failing tests for one iteration handoff**

Append:

```python
def _json_summary(payload):
    return json.dumps(payload, separators=(",", ":"))


def test_on_trading_iteration_runs_macro_four_baskets_portfolio_then_execution():
    module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.initialize()

    macro_report = {
        "agent": "macro_allocation_agent",
        "regime": "growth_up_inflation_down",
        "regime_changed": True,
        "basket_weights": {"equity": 0.50, "commodity": 0.00, "tips": 0.25, "nominal_bond": 0.25},
        "mock": True,
        "reason_brief": "mock",
    }
    basket_reports = {
        "equity_basket_agent": {
            "basket_id": "equity",
            "target_weight": 0.50,
            "status": "active",
            "candidate_symbols": module.BASKET_UNIVERSES["equity"],
            "selected_symbol": "QQQ",
            "reason_brief": "selected",
        },
        "commodity_basket_agent": {
            "basket_id": "commodity",
            "target_weight": 0.00,
            "status": "inactive",
            "candidate_symbols": module.BASKET_UNIVERSES["commodity"],
            "selected_symbol": None,
            "reason_brief": "inactive",
        },
        "tips_basket_agent": {
            "basket_id": "tips",
            "target_weight": 0.25,
            "status": "active",
            "candidate_symbols": module.BASKET_UNIVERSES["tips"],
            "selected_symbol": "TIP",
            "reason_brief": "selected",
        },
        "nominal_bond_basket_agent": {
            "basket_id": "nominal_bond",
            "target_weight": 0.25,
            "status": "active",
            "candidate_symbols": module.BASKET_UNIVERSES["nominal_bond"],
            "selected_symbol": "IEF",
            "reason_brief": "selected",
        },
    }
    portfolio_summary = {
        "decision": {"type": "rebalance", "reason_brief": "mock target"},
        "target_portfolio": [
            {"basket_id": "equity", "symbol": "QQQ", "target_weight": 0.50},
            {"basket_id": "tips", "symbol": "TIP", "target_weight": 0.25},
            {"basket_id": "nominal_bond", "symbol": "IEF", "target_weight": 0.25},
        ],
        "execution_plan": {
            "schema_version": 1,
            "intent": "rebalance",
            "orders": [
                {"sequence": 1, "symbol": "QQQ", "side": "buy", "quantity_mode": "shares", "quantity": 100, "order_type": "market"},
                {"sequence": 2, "symbol": "TIP", "side": "buy", "quantity_mode": "shares", "quantity": 50, "order_type": "market"},
                {"sequence": 3, "symbol": "IEF", "side": "buy", "quantity_mode": "shares", "quantity": 50, "order_type": "market"},
            ],
        },
    }

    agent_manager.summaries["macro_allocation_agent"] = _json_summary(macro_report)
    for agent_name, report in basket_reports.items():
        agent_manager.summaries[agent_name] = _json_summary(report)
    agent_manager.summaries["portfolio_decision_agent"] = _json_summary(portfolio_summary)
    agent_manager.tool_calls["portfolio_decision_agent"] = [
        "account_positions",
        "account_portfolio",
        "market_last_price",
    ]

    strategy.on_trading_iteration()

    assert [agent_name for agent_name, agent in agent_manager._agents.items() if agent.calls] == [
        "macro_allocation_agent",
        "equity_basket_agent",
        "commodity_basket_agent",
        "tips_basket_agent",
        "nominal_bond_basket_agent",
        "portfolio_decision_agent",
        "execution_agent",
    ]
    macro_context = agent_manager["macro_allocation_agent"].calls[0]["context"]
    assert macro_context["date"] == "2024-09-05"
    assert macro_context["mock_regime_mode"] == "seeded_random"
    assert macro_context["mock_regime_seed"] == 42

    commodity_context = agent_manager["commodity_basket_agent"].calls[0]["context"]
    assert commodity_context["basket_id"] == "commodity"
    assert commodity_context["basket_symbols"] == module.BASKET_UNIVERSES["commodity"]
    assert commodity_context["target_weight"] == 0.0
    assert commodity_context["macro_allocation_report"] == macro_report

    portfolio_context = agent_manager["portfolio_decision_agent"].calls[0]["context"]
    assert portfolio_context["macro_allocation_report"] == macro_report
    assert portfolio_context["equity_basket_report"] == basket_reports["equity_basket_agent"]
    assert portfolio_context["commodity_basket_report"] == basket_reports["commodity_basket_agent"]
    assert portfolio_context["tips_basket_report"] == basket_reports["tips_basket_agent"]
    assert portfolio_context["nominal_bond_basket_report"] == basket_reports["nominal_bond_basket_agent"]

    execution_context = agent_manager["execution_agent"].calls[0]["context"]
    assert set(execution_context) == {"date", "execution_plan"}
    assert [order["symbol"] for order in execution_context["execution_plan"]["orders"]] == ["QQQ", "TIP", "IEF"]
    assert "macro_allocation_report" not in execution_context
    assert "equity_basket_report" not in execution_context


def test_hold_plan_skips_execution_agent():
    _module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
    strategy.initialize()

    agent_manager.summaries["macro_allocation_agent"] = _json_summary(
        {"basket_weights": {"equity": 0.0, "commodity": 0.0, "tips": 0.0, "nominal_bond": 0.0}}
    )
    for agent_name, basket_id in {
        "equity_basket_agent": "equity",
        "commodity_basket_agent": "commodity",
        "tips_basket_agent": "tips",
        "nominal_bond_basket_agent": "nominal_bond",
    }.items():
        agent_manager.summaries[agent_name] = _json_summary(
            {"basket_id": basket_id, "target_weight": 0.0, "status": "inactive", "selected_symbol": None}
        )
    agent_manager.summaries["portfolio_decision_agent"] = _json_summary(
        {"execution_plan": {"schema_version": 1, "intent": "hold", "orders": []}}
    )

    strategy.on_trading_iteration()

    assert agent_manager["portfolio_decision_agent"].calls
    assert agent_manager["execution_agent"].calls == []
```

- [ ] **Step 2: Run tests and verify `on_trading_iteration` is missing**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected: failures around missing `on_trading_iteration` behavior.

- [ ] **Step 3: Implement JSON parsing helpers and trading iteration**

Append helper functions before the class or above `on_trading_iteration`:

```python
def _parse_json_summary(summary: str, label: str) -> dict[str, Any]:
    try:
        return json.loads(_extract_first_json_object(summary))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} JSON is invalid: {exc.msg}") from exc


def _agent_result_tool_names(result: Any) -> set[str]:
    return {event.tool_name for event in getattr(result, "tool_calls", []) if getattr(event, "tool_name", None)}


def validate_portfolio_decision_tool_evidence(execution_plan: dict[str, Any], decision_result: Any) -> None:
    if execution_plan["intent"] == "hold":
        return
    tool_names = _agent_result_tool_names(decision_result)
    missing = sorted({"account_positions", "account_portfolio"} - tool_names)
    if missing:
        raise ValueError(f"portfolio_decision_agent must call account tools before non-hold plan; missing: {', '.join(missing)}")
    if any(order["side"] == "buy" for order in execution_plan["orders"]) and "market_last_price" not in tool_names:
        raise ValueError("portfolio_decision_agent must call market_last_price before buy sizing.")
```

Add this method inside `AITradingTeamMockGrowthInflationQuadrantStrategy`:

```python
    def on_trading_iteration(self):
        context = {
            "date": self.get_datetime().date().isoformat(),
            "mock_regime_mode": self._mock_regime_mode,
            "mock_regime_seed": self._mock_regime_seed,
            "basket_universes": self.parameters["basket_universes"],
        }
        macro_result = self.agents["macro_allocation_agent"].run(
            task_prompt=(
                "Call macro_regime_classifier once for this date. Return only a compact JSON report with regime, "
                "regime_changed, basket_weights, mock, and reason_brief."
            ),
            context=context,
        )
        try:
            macro_report = _parse_json_summary(macro_result.summary, "macro_allocation_report")
        except ValueError as exc:
            self._last_execution_plan_error = str(exc)
            print(f"Mock quadrant workflow blocked: {exc}")
            return

        basket_reports: dict[str, dict[str, Any]] = {}
        basket_weights = macro_report.get("basket_weights", {})
        for basket_id, agent_name in BASKET_AGENT_NAMES.items():
            target_weight = float(basket_weights.get(basket_id, 0.0))
            basket_result = self.agents[agent_name].run(
                task_prompt=(
                    "Use only basket_symbols from context. If target_weight is zero, return inactive with "
                    "selected_symbol null. If target_weight is positive, select one representative symbol from "
                    "basket_symbols using compact price summary evidence. Return only JSON."
                ),
                context={
                    "date": context["date"],
                    "basket_id": basket_id,
                    "basket_symbols": self.parameters["basket_universes"][basket_id],
                    "target_weight": target_weight,
                    "macro_allocation_report": macro_report,
                },
            )
            try:
                basket_reports[basket_id] = _parse_json_summary(basket_result.summary, f"{basket_id}_basket_report")
            except ValueError as exc:
                self._last_execution_plan_error = str(exc)
                print(f"Mock quadrant workflow blocked: {exc}")
                return

        portfolio_result = self.agents["portfolio_decision_agent"].run(
            task_prompt=(
                "Merge macro_allocation_report and basket reports into target_portfolio and strict execution_plan. "
                "Use active basket selected_symbol values and target weights. Before a non-hold plan, inspect account "
                "state; for buys, inspect prices. Return only one JSON object."
            ),
            context={
                "date": context["date"],
                "macro_allocation_report": macro_report,
                "equity_basket_report": basket_reports["equity"],
                "commodity_basket_report": basket_reports["commodity"],
                "tips_basket_report": basket_reports["tips"],
                "nominal_bond_basket_report": basket_reports["nominal_bond"],
            },
        )
        try:
            execution_plan = parse_execution_plan_from_portfolio_summary(portfolio_result.summary)
            validate_portfolio_decision_tool_evidence(execution_plan, portfolio_result)
            validate_execution_plan_symbols(execution_plan, list(basket_reports.values()))
        except ValueError as exc:
            self._last_execution_plan_error = str(exc)
            print(f"Execution plan blocked: {exc}")
            return
        self._last_execution_plan_error = None

        if execution_plan["intent"] == "hold" or not execution_plan["orders"]:
            return

        self.agents["execution_agent"].run(
            task_prompt=(
                "Execute only the provided execution_plan object. Inspect account state, open orders, positions, and "
                "latest prices, then submit only execution_plan.orders with orders_submit_order. After every submit, "
                "confirm that same order with orders_confirm_order before continuing. Preserve sequence order."
            ),
            context={"date": context["date"], "execution_plan": execution_plan},
        )
```

- [ ] **Step 4: Run tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add -- lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py tests/test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "feat: run mock quadrant workflow iteration"
```

---

### Task 6: Add Benchmark Entry And Run Verification

**Files:**
- Modify: `tests/test_ai_trading_team_mock_growth_inflation_quadrant.py`
- Modify: `scripts/run_ai_trading_team_examples_benchmark.py`

- [ ] **Step 1: Add failing test that benchmark script exposes the new strategy**

Append:

```python
def test_examples_benchmark_exposes_mock_quadrant_strategy():
    benchmark = importlib.import_module("scripts.run_ai_trading_team_examples_benchmark")

    assert "mock-growth-inflation-quadrant" in benchmark.STRATEGIES
    strategy_class = benchmark.STRATEGIES["mock-growth-inflation-quadrant"]
    assert strategy_class.__name__ == "AITradingTeamMockGrowthInflationQuadrantStrategy"


def test_examples_benchmark_uses_model_specific_key_check(monkeypatch):
    benchmark = importlib.import_module("scripts.run_ai_trading_team_examples_benchmark")

    monkeypatch.setenv("AI_TRADING_TEAM_MODEL", "openai/gpt-5.6-luna")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    assert benchmark._missing_key_label("openai/gpt-5.6-luna") is None
```

- [ ] **Step 2: Run that test and verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ai_trading_team_mock_growth_inflation_quadrant.py::test_examples_benchmark_exposes_mock_quadrant_strategy -q
```

Expected: failure because the strategy is not imported or registered in the script, and/or because the script does not expose model-specific key checking yet.

- [ ] **Step 3: Register the new strategy and model-specific key check in the examples benchmark script**

In `scripts/run_ai_trading_team_examples_benchmark.py`, add this import near the other example strategy imports:

```python
from lumibot.example_strategies.ai_trading_team_mock_growth_inflation_quadrant import (  # noqa: E402
    AITradingTeamMockGrowthInflationQuadrantStrategy,
)
```

Add this import near the other script helper imports:

```python
from scripts.run_ai_committee_provider_benchmark import _missing_key_label  # noqa: E402
```

Add this entry to `STRATEGIES`:

```python
    "mock-growth-inflation-quadrant": AITradingTeamMockGrowthInflationQuadrantStrategy,
```

Replace the Gemini-only key check in `main()`:

```python
    if not os.environ.get("GOOGLE_API_KEY") and not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError("GOOGLE_API_KEY or GEMINI_API_KEY is required for these examples.")
```

with model-specific checking based on the active model:

```python
    active_model = os.environ.get("AI_TRADING_TEAM_MODEL", "gemini-3.1-flash-lite")
    missing_key = _missing_key_label(active_model)
    if missing_key:
        raise RuntimeError(f"Missing required provider API key(s) for {active_model}: {missing_key}")
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ai_trading_team_mock_growth_inflation_quadrant.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Run regression tests for the old strategy and agent manager boundaries**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ai_trading_team_growth_execution_test.py tests/test_agent_manager.py tests/test_agent_tool_permissions.py -q
```

Expected: all selected tests pass. This confirms the old growth-decision-execution strategy and tool permission boundaries were not broken.

- [ ] **Step 6: Run ruff on changed files**

Run:

```powershell
.\.venv\Scripts\python.exe -m ruff check lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py tests/test_ai_trading_team_mock_growth_inflation_quadrant.py scripts/run_ai_trading_team_examples_benchmark.py
```

Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```powershell
git add -- scripts/run_ai_trading_team_examples_benchmark.py tests/test_ai_trading_team_mock_growth_inflation_quadrant.py
git commit -m "chore: expose mock quadrant strategy benchmark"
```

- [ ] **Step 8: Optional paid one-day backtest smoke test**

Only run this after confirming the intended provider key is available and paid model calls are acceptable.

```powershell
cd D:\Lumibot
$env:LUMIBOT_ALLOW_PAID_AI_TRADING_TEAM_BACKTEST='1'
$env:AI_TRADING_TEAM_MODEL='openai/gpt-5.6-luna'
$env:OPENAI_API_KEY=(Get-Content -LiteralPath project_notes\API.txt | Where-Object { $_ -match '^sk-' } | Select-Object -First 1).Trim()
.\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start 2024-09-05 --end 2024-09-06 --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 1800 --env-file project_notes\API.txt
```

Expected:

- Script prints a JSON object with `"strategy": "mock-growth-inflation-quadrant"` and `"status": "passed"`, or records a clear failure payload in the artifact directory.
- The generated trace can be opened with:

```powershell
cd D:\Lumibot
.\.venv\Scripts\python.exe scripts\agent_trace_ui.py
```

Expected UI observations:

- Strategy selector includes `AITradingTeamMockGrowthInflationQuadrantStrategy`.
- Workflow graph shows 7 agents.
- `macro_allocation_agent` feeds the four basket agents.
- Four basket agents feed `portfolio_decision_agent`.
- `portfolio_decision_agent` feeds `execution_agent` if orders exist.
- Execution agent input contains only `date` and `execution_plan`.

---

## Final Verification Checklist

- [ ] `.\.venv\Scripts\python.exe -m pytest tests/test_ai_trading_team_mock_growth_inflation_quadrant.py -q`
- [ ] `.\.venv\Scripts\python.exe -m pytest tests/test_ai_trading_team_growth_execution_test.py tests/test_agent_manager.py tests/test_agent_tool_permissions.py -q`
- [ ] `.\.venv\Scripts\python.exe -m ruff check lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py tests/test_ai_trading_team_mock_growth_inflation_quadrant.py scripts/run_ai_trading_team_examples_benchmark.py`
- [ ] Optional paid one-day backtest run completes or produces a clear artifact failure.
- [ ] Optional Agent Replay UI inspection confirms 7-agent graph and execution-only handoff.

## Self-Review Against Spec

- Spec requires a copied/new strategy, not modification of the old strategy: covered by Tasks 1, 4, and the do-not-modify list.
- Spec requires only one new mock regime capability: covered by Tasks 1 and 2 as a local strategy tool, not a global BuiltinTools change.
- Spec requires deterministic mock behavior: covered by Task 1 tests for `seeded_random` and `cycle`.
- Spec requires at least five semantically valid symbols per basket: covered by Task 1 constants and tests.
- Spec requires 7-agent workflow: covered by Tasks 4 and 5.
- Spec requires clean prompt boundaries: covered by Task 4 prompt tests.
- Spec requires portfolio decision strict JSON and explicit numeric quantities: covered by Task 3 parser tests and Task 5 handoff tests.
- Spec requires execution agent receives only the execution plan: covered by Task 5.
- Spec requires submit-and-confirm behavior to remain: covered by Task 4 execution prompt/tool surface and optional backtest/UI verification.
- Spec requires UI trace visibility: covered by Task 6 optional smoke test and UI inspection.

No placeholder tasks remain. No task requires real macro data, real performance validation, new basket metadata tools, or changes to the old strategy.
