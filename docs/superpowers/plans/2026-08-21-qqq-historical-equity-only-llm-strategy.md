# QQQ Historical Equity-Only LLM Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new weekly equity-only LLM strategy that uses locally collected point-in-time QQQ N-PORT holdings snapshots as its dynamic stock universe.

**Architecture:** Keep `AITradingTeamEquityOnlyLLMStrategy` as the fixed-50-stock baseline and add a narrow subclass for QQQ historical universe selection. The subclass resolves QQQ symbols at each scheduled workflow run, passes them plus snapshot metadata into the existing equity agent flow, and reuses the existing deterministic target-portfolio planner and execution agent.

**Tech Stack:** Python, Lumibot strategy classes, existing agent runtime, existing QQQ N-PORT universe resolver, pytest, benchmark runner registry.

---

## File Structure

### Modify: `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`

Responsibility:

- Keep fixed-50 equity-only constants unchanged.
- Add prompt helpers for the QQQ historical equity universe so the new strategy can describe the dynamic universe without mutating the fixed strategy prompt.

### Modify: `lumibot/example_strategies/ai_trading_team_equity_only_llm.py`

Responsibility:

- Keep `AITradingTeamEquityOnlyLLMStrategy` unchanged for fixed-50 baseline behavior.
- Add `AITradingTeamQQQHistoricalEquityOnlyLLMStrategy`.
- Add helper methods to resolve QQQ snapshot metadata and symbols at each scheduled workflow date.
- Override only the minimum methods required for dynamic universe resolution and QQQ-specific equity agent prompt wiring.

### Modify: `scripts/run_ai_trading_team_examples_benchmark.py`

Responsibility:

- Register the new benchmark key `qqq-historical-equity-only-llm`.
- Preserve the existing `equity-only-llm` key.

### Modify: `tests/test_ai_trading_team_equity_only_llm.py`

Responsibility:

- Add unit tests for QQQ historical dynamic universe behavior.
- Assert fixed-50 baseline remains unchanged.
- Assert runner exposes both strategy keys.

---

## Task 1: Add Failing Tests for QQQ Historical Strategy Behavior

**Files:**

- Modify: `tests/test_ai_trading_team_equity_only_llm.py`

- [ ] **Step 1: Add imports used by the new tests**

At the top of `tests/test_ai_trading_team_equity_only_llm.py`, extend imports:

```python
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
```

The file already imports `datetime`, `Path`, and `SimpleNamespace`. Only add `date` to the existing datetime import:

```python
from datetime import date, datetime
```

- [ ] **Step 2: Add helper for fake QQQ resolutions**

Append this helper near the existing test helpers, after `tool_names`:

```python
def qqq_resolution(
    *,
    as_of_date="2024-09-05",
    mode="strict",
    selected_report_date="2024-06-30",
    selected_filing_date="2024-08-28",
    accession_number="0001752724-24-196011",
    symbols=("MSFT", "AAPL", "NVDA", "AMZN"),
    snapshot_path="C:/cache/qqq_nport_2024-06-30.json",
    source_url="https://www.sec.gov/example.xml",
):
    return SimpleNamespace(
        as_of_date=date.fromisoformat(as_of_date),
        mode=mode,
        selected_report_date=date.fromisoformat(selected_report_date),
        selected_filing_date=date.fromisoformat(selected_filing_date),
        accession_number=accession_number,
        symbols=tuple(symbols),
        snapshot_path=Path(snapshot_path),
        source_url=source_url,
    )
```

- [ ] **Step 3: Add test for strategy defaults**

Append this test near the fixed-50 strategy tests:

```python
def test_qqq_historical_equity_strategy_defaults_to_weekly_strict_without_changing_fixed_baseline():
    module = load_module()

    fixed = module.AITradingTeamEquityOnlyLLMStrategy
    qqq = module.AITradingTeamQQQHistoricalEquityOnlyLLMStrategy

    assert fixed.parameters["run_frequency"] == "monthly"
    assert fixed.parameters["basket_universes"]["equity"] == EXPECTED_EQUITY_UNIVERSE
    assert "qqq_universe_mode" not in fixed.parameters

    assert qqq.parameters["run_frequency"] == "weekly"
    assert qqq.parameters["weekly_run_weekday"] == "MON"
    assert qqq.parameters["weekly_holiday_policy"] == "first_open_trading_day"
    assert qqq.parameters["qqq_universe_mode"] == "strict"
    assert qqq.parameters["qqq_universe_data_dir"] is None
```

- [ ] **Step 4: Add test for strict QQQ resolver and agent context**

Append this test after `test_equity_agent_task_prompt_teaches_summary_first_top_n_and_strict_json`:

```python
def test_qqq_historical_strategy_resolves_snapshot_and_passes_metadata_to_equity_agent(monkeypatch):
    module = load_module()
    strategy = make_running_strategy(module.AITradingTeamQQQHistoricalEquityOnlyLLMStrategy)
    strategy.get_datetime = lambda: datetime(2024, 9, 5, 9, 30)
    strategy.agents.summaries["equity_basket_agent"] = json.dumps(
        {
            "basket_id": "equity",
            "target_weight": 1.0,
            "status": "active",
            "candidate_symbols": ["MSFT", "AAPL", "NVDA", "AMZN"],
            "selected_symbol": "NVDA",
            "reason_brief": "NVDA has the strongest QQQ constituent evidence.",
        }
    )
    strategy.agents.summaries["execution_agent"] = "Executed plan."
    resolver_calls = []

    def fake_resolve_qqq_snapshot(as_of_date, *, mode="strict", data_dir=None):
        resolver_calls.append({"as_of_date": as_of_date, "mode": mode, "data_dir": data_dir})
        return qqq_resolution(symbols=("MSFT", "AAPL", "NVDA", "AMZN"))

    def fake_target_portfolio_to_execution_plan(strategy_arg, *, date, target_portfolio):
        return {
            "schema_version": "1.0",
            "date": date,
            "target_portfolio": target_portfolio,
            "current_vs_target": [],
            "cash_projection": {
                "cash_before": 100000.0,
                "estimated_sell_proceeds": 0.0,
                "estimated_buy_cost": 0.0,
                "cash_after_estimate": 100000.0,
                "buy_sizing_buffer_pct": 0.02,
                "negative_cash_allowed": False,
            },
            "execution_plan": {"schema_version": 1, "intent": "hold", "orders": []},
            "warnings": [],
        }

    monkeypatch.setattr(module, "resolve_qqq_snapshot", fake_resolve_qqq_snapshot)
    monkeypatch.setattr(module, "target_portfolio_to_execution_plan", fake_target_portfolio_to_execution_plan)

    strategy.on_trading_iteration()

    assert resolver_calls == [{"as_of_date": "2024-09-05", "mode": "strict", "data_dir": None}]
    equity_call = strategy.agents["equity_basket_agent"].calls[0]
    assert equity_call["context"]["basket_symbols"] == ["MSFT", "AAPL", "NVDA", "AMZN"]
    assert equity_call["context"]["universe_source"] == {
        "type": "qqq_nport",
        "mode": "strict",
        "as_of_date": "2024-09-05",
        "selected_report_date": "2024-06-30",
        "selected_filing_date": "2024-08-28",
        "accession_number": "0001752724-24-196011",
        "holding_count": 4,
        "snapshot_path": "C:/cache/qqq_nport_2024-06-30.json",
        "source_url": "https://www.sec.gov/example.xml",
    }
```

- [ ] **Step 5: Add test for prototype mode/data-dir forwarding and symbol normalization**

Append this test:

```python
def test_qqq_historical_strategy_supports_prototype_mode_data_dir_and_symbol_normalization(monkeypatch, tmp_path):
    module = load_module()
    strategy = make_running_strategy(module.AITradingTeamQQQHistoricalEquityOnlyLLMStrategy)
    strategy.parameters["qqq_universe_mode"] = "prototype"
    strategy.parameters["qqq_universe_data_dir"] = str(tmp_path)
    strategy.agents.summaries["equity_basket_agent"] = json.dumps(
        {
            "basket_id": "equity",
            "target_weight": 1.0,
            "status": "active",
            "candidate_symbols": ["AAPL", "MSFT", "NVDA"],
            "selected_symbol": "AAPL",
            "reason_brief": "AAPL is selected.",
        }
    )
    strategy.agents.summaries["execution_agent"] = "Executed plan."
    resolver_calls = []

    def fake_resolve_qqq_snapshot(as_of_date, *, mode="strict", data_dir=None):
        resolver_calls.append({"as_of_date": as_of_date, "mode": mode, "data_dir": data_dir})
        return qqq_resolution(
            mode="prototype",
            selected_report_date="2024-09-30",
            selected_filing_date="2024-11-27",
            symbols=(" aapl ", "", "MSFT", "AAPL", "nvda"),
        )

    def fake_target_portfolio_to_execution_plan(strategy_arg, *, date, target_portfolio):
        return {
            "schema_version": "1.0",
            "date": date,
            "target_portfolio": target_portfolio,
            "current_vs_target": [],
            "cash_projection": {
                "cash_before": 100000.0,
                "estimated_sell_proceeds": 0.0,
                "estimated_buy_cost": 0.0,
                "cash_after_estimate": 100000.0,
                "buy_sizing_buffer_pct": 0.02,
                "negative_cash_allowed": False,
            },
            "execution_plan": {"schema_version": 1, "intent": "hold", "orders": []},
            "warnings": [],
        }

    monkeypatch.setattr(module, "resolve_qqq_snapshot", fake_resolve_qqq_snapshot)
    monkeypatch.setattr(module, "target_portfolio_to_execution_plan", fake_target_portfolio_to_execution_plan)

    strategy.on_trading_iteration()

    assert resolver_calls == [
        {"as_of_date": "2024-09-05", "mode": "prototype", "data_dir": str(tmp_path)}
    ]
    equity_call = strategy.agents["equity_basket_agent"].calls[0]
    assert equity_call["context"]["basket_symbols"] == ["AAPL", "MSFT", "NVDA"]
    assert equity_call["context"]["universe_source"]["mode"] == "prototype"
    assert equity_call["context"]["universe_source"]["holding_count"] == 3
```

- [ ] **Step 6: Add test for missing snapshot blocking**

Append this test:

```python
def test_qqq_historical_strategy_blocks_without_fallback_when_snapshot_missing(monkeypatch):
    module = load_module()
    qqq_nport = importlib.import_module("lumibot.tools.universe.qqq_nport")
    strategy = make_running_strategy(module.AITradingTeamQQQHistoricalEquityOnlyLLMStrategy)

    def fake_resolve_qqq_snapshot(as_of_date, *, mode="strict", data_dir=None):
        raise qqq_nport.NoSnapshotAvailableError("No QQQ snapshot available")

    monkeypatch.setattr(module, "resolve_qqq_snapshot", fake_resolve_qqq_snapshot)

    strategy.on_trading_iteration()

    assert strategy.agents["equity_basket_agent"].calls == []
    assert strategy.agents["execution_agent"].calls == []
    assert strategy._last_execution_plan_error == "No QQQ snapshot available"
    assert strategy._scheduled_workflow_events[-1]["reason"] == "qqq_historical_universe_unavailable"
    assert strategy._scheduled_workflow_events[-1]["status"] == "blocked"
```

- [ ] **Step 7: Add test for selected symbol validation against dynamic universe**

Append this test:

```python
def test_qqq_historical_strategy_rejects_selected_symbol_outside_resolved_universe(monkeypatch):
    module = load_module()
    strategy = make_running_strategy(module.AITradingTeamQQQHistoricalEquityOnlyLLMStrategy)
    strategy.agents.summaries["equity_basket_agent"] = json.dumps(
        {
            "basket_id": "equity",
            "target_weight": 1.0,
            "status": "active",
            "candidate_symbols": ["MSFT", "AAPL", "NVDA"],
            "selected_symbol": "ORCL",
            "reason_brief": "ORCL was incorrectly selected.",
        }
    )

    def fake_resolve_qqq_snapshot(as_of_date, *, mode="strict", data_dir=None):
        return qqq_resolution(symbols=("MSFT", "AAPL", "NVDA"))

    monkeypatch.setattr(module, "resolve_qqq_snapshot", fake_resolve_qqq_snapshot)

    strategy.on_trading_iteration()

    assert strategy._last_execution_plan_error == "selected equity symbol must be in equity universe."
    assert strategy.agents["execution_agent"].calls == []
```

- [ ] **Step 8: Add test for QQQ-specific prompt wording**

Append this test:

```python
def test_qqq_historical_equity_agent_prompt_mentions_historical_constituents_without_weight_bias(monkeypatch):
    module = load_module()
    strategy = make_strategy(module.AITradingTeamQQQHistoricalEquityOnlyLLMStrategy)

    strategy.initialize()

    equity_agent = created_agent_config(strategy, "equity_basket_agent")
    prompt = equity_agent["system_prompt"].lower()

    for required in (
        "qqq historical constituent universe",
        "provided basket_symbols",
        "current backtest date",
        "current rank evidence",
        "do not add symbols",
        "index weight",
    ):
        assert required in prompt

    for forbidden in (
        "automatically high quality",
        "always prefer",
        "buy qqq",
        "sector",
        "style",
        "safety label",
    ):
        assert forbidden not in prompt
```

- [ ] **Step 9: Add test for benchmark runner key**

Replace `test_benchmark_runner_exposes_neutral_equity_only_strategy` with:

```python
def test_benchmark_runner_exposes_fixed_and_qqq_historical_equity_only_strategies():
    benchmark = importlib.import_module("scripts.run_ai_trading_team_examples_benchmark")

    assert "equity-only-llm" in benchmark.STRATEGIES
    assert benchmark.STRATEGIES["equity-only-llm"].__name__ == "AITradingTeamEquityOnlyLLMStrategy"
    assert "qqq-historical-equity-only-llm" in benchmark.STRATEGIES
    assert (
        benchmark.STRATEGIES["qqq-historical-equity-only-llm"].__name__
        == "AITradingTeamQQQHistoricalEquityOnlyLLMStrategy"
    )
```

- [ ] **Step 10: Run focused tests and verify failures**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py -q
```

Expected:

```text
FAIL
AttributeError: module 'lumibot.example_strategies.ai_trading_team_equity_only_llm' has no attribute 'AITradingTeamQQQHistoricalEquityOnlyLLMStrategy'
```

The exact number of failures may be several; they should all point to missing QQQ historical strategy or missing runner key.

- [ ] **Step 11: Commit failing tests**

Run:

```powershell
git add tests/test_ai_trading_team_equity_only_llm.py
git commit -m "test: specify qqq historical equity-only strategy"
```

---

## Task 2: Add QQQ Historical Prompt Helper

**Files:**

- Modify: `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`
- Test: `tests/test_ai_trading_team_equity_only_llm.py`

- [ ] **Step 1: Add prompt helper function**

In `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`, immediately after `equity_basket_agent_system_prompt`, add:

```python
def qqq_historical_equity_basket_agent_system_prompt(symbols: str) -> str:
    return (
        f"Equity-only selection role: choose exactly one stock from the QQQ historical constituent universe "
        f"provided in basket_symbols ({symbols}). "
        "The provided basket_symbols represent the QQQ historical constituent universe available for the "
        "current backtest date. The selected stock receives target_weight 1.0 through downstream deterministic "
        "planning. You cannot place orders or size trades. Use market_load_history_tables_summary first for "
        "multi-symbol comparison. Treat rankings as separate evidence views; do not invent sector, style, "
        "safety, or cyclicality labels. Do not assume QQQ membership itself makes a stock safe or best; select "
        "from current rank evidence. Do not choose based on index weight alone. If one symbol is clearly "
        "stronger across relevant rankings, select it without news. Use alpaca_news only when leading "
        "candidates are close, conflicting, or uncertain; when used, request news only for leading candidates. "
        "If news is unavailable, continue with rank-only evidence. Use only symbols in the provided "
        "basket_symbols and do not add symbols outside the provided universe. Return strict JSON only. "
        "Do not place orders."
    )
```

- [ ] **Step 2: Run prompt-focused test and verify remaining failure is strategy wiring**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_qqq_historical_equity_agent_prompt_mentions_historical_constituents_without_weight_bias -q
```

Expected:

```text
FAIL
AttributeError: module ... has no attribute 'AITradingTeamQQQHistoricalEquityOnlyLLMStrategy'
```

This is acceptable because the helper exists but the strategy is not wired yet.

- [ ] **Step 3: Commit prompt helper**

Run:

```powershell
git add lumibot/example_strategies/ai_trading_team_equity_only_helpers.py
git commit -m "feat: add qqq historical equity prompt helper"
```

---

## Task 3: Implement QQQ Historical Strategy Class

**Files:**

- Modify: `lumibot/example_strategies/ai_trading_team_equity_only_llm.py`
- Test: `tests/test_ai_trading_team_equity_only_llm.py`

- [ ] **Step 1: Add imports**

In `lumibot/example_strategies/ai_trading_team_equity_only_llm.py`, add imports:

```python
from pathlib import Path
```

Extend the helper imports:

```python
    qqq_historical_equity_basket_agent_system_prompt,
```

Add QQQ universe imports:

```python
from lumibot.tools.universe.qqq_nport import NoSnapshotAvailableError, resolve_qqq_snapshot
```

- [ ] **Step 2: Add QQQ mode normalization helper**

After `_normalize_equity_run_frequency`, add:

```python
def _normalize_qqq_universe_mode(value: Any) -> str:
    if not value:
        return "strict"
    mode = str(value).strip().lower()
    if mode not in {"strict", "prototype"}:
        raise ValueError("qqq_universe_mode must be 'strict' or 'prototype'.")
    return mode
```

- [ ] **Step 3: Add symbol normalization helper**

After `_normalized_universe`, add:

```python
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
```

- [ ] **Step 4: Add QQQ metadata serializer**

After `_month_key`, add:

```python
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
```

- [ ] **Step 5: Refactor fixed strategy prompt creation into overridable methods**

Inside `AITradingTeamEquityOnlyLLMStrategy`, add these methods before `initialize`:

```python
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
```

- [ ] **Step 6: Update fixed strategy `initialize` to use overridable prompt method**

In `initialize`, replace:

```python
basket_universes = self.parameters.get("basket_universes", EQUITY_ONLY_BASKET_UNIVERSES)
equity_symbols = ", ".join(basket_universes[EQUITY_BASKET_ID])
```

with:

```python
equity_symbols = self._equity_agent_initial_symbols_text()
```

Replace:

```python
system_prompt=equity_basket_agent_system_prompt(equity_symbols),
```

with:

```python
system_prompt=self._equity_agent_system_prompt(equity_symbols),
```

- [ ] **Step 7: Update fixed strategy workflow to use overridable universe/context methods**

In `_run_equity_only_workflow`, replace:

```python
basket_universes = self.parameters.get("basket_universes", EQUITY_ONLY_BASKET_UNIVERSES)
equity_universe = list(basket_universes[EQUITY_BASKET_ID])
```

with:

```python
try:
    equity_universe, universe_source = self._equity_universe_for_date(current_date)
except NoSnapshotAvailableError as exc:
    self._last_execution_plan_error = str(exc)
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
    self._last_execution_plan_error = str(exc)
    self._log_equity_only_workflow_blocked(f"Equity-only LLM workflow blocked: {exc}")
    return
```

Replace the equity agent context literal:

```python
context={
    "date": current_date,
    "basket_id": EQUITY_BASKET_ID,
    "basket_symbols": equity_universe,
    "target_weight": EQUITY_ONLY_TARGET_WEIGHT,
},
```

with:

```python
context=self._equity_agent_context(
    current_date=current_date,
    equity_universe=equity_universe,
    universe_source=universe_source,
),
```

- [ ] **Step 8: Add QQQ historical subclass**

Append after `AITradingTeamEquityOnlyLLMStrategy`:

```python
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
```

- [ ] **Step 9: Run QQQ strategy tests**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_qqq_historical_equity_strategy_defaults_to_weekly_strict_without_changing_fixed_baseline tests/test_ai_trading_team_equity_only_llm.py::test_qqq_historical_strategy_resolves_snapshot_and_passes_metadata_to_equity_agent tests/test_ai_trading_team_equity_only_llm.py::test_qqq_historical_strategy_supports_prototype_mode_data_dir_and_symbol_normalization tests/test_ai_trading_team_equity_only_llm.py::test_qqq_historical_strategy_blocks_without_fallback_when_snapshot_missing tests/test_ai_trading_team_equity_only_llm.py::test_qqq_historical_strategy_rejects_selected_symbol_outside_resolved_universe tests/test_ai_trading_team_equity_only_llm.py::test_qqq_historical_equity_agent_prompt_mentions_historical_constituents_without_weight_bias -q
```

Expected:

```text
6 passed
```

If `test_qqq_historical_strategy_blocks_without_fallback_when_snapshot_missing` records two scheduled events, assert against the event whose `reason` is `qqq_historical_universe_unavailable`:

```python
blocked_events = [
    event for event in strategy._scheduled_workflow_events
    if event.get("reason") == "qqq_historical_universe_unavailable"
]
assert len(blocked_events) == 1
assert blocked_events[0]["status"] == "blocked"
```

- [ ] **Step 10: Run full equity-only test file**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py -q
```

Expected:

```text
All tests except the benchmark runner key test pass.
```

The runner key test may still fail until Task 4.

- [ ] **Step 11: Commit strategy implementation**

Run:

```powershell
git add lumibot/example_strategies/ai_trading_team_equity_only_llm.py lumibot/example_strategies/ai_trading_team_equity_only_helpers.py tests/test_ai_trading_team_equity_only_llm.py
git commit -m "feat: add qqq historical equity-only strategy"
```

---

## Task 4: Register Benchmark Runner Key

**Files:**

- Modify: `scripts/run_ai_trading_team_examples_benchmark.py`
- Test: `tests/test_ai_trading_team_equity_only_llm.py`

- [ ] **Step 1: Add strategy registry entry**

In `scripts/run_ai_trading_team_examples_benchmark.py`, add this entry immediately after `equity-only-llm`:

```python
        "qqq-historical-equity-only-llm": (
            "lumibot.example_strategies.ai_trading_team_equity_only_llm",
            "AITradingTeamQQQHistoricalEquityOnlyLLMStrategy",
        ),
```

The surrounding registry should include both:

```python
        "equity-only-llm": (
            "lumibot.example_strategies.ai_trading_team_equity_only_llm",
            "AITradingTeamEquityOnlyLLMStrategy",
        ),
        "qqq-historical-equity-only-llm": (
            "lumibot.example_strategies.ai_trading_team_equity_only_llm",
            "AITradingTeamQQQHistoricalEquityOnlyLLMStrategy",
        ),
```

- [ ] **Step 2: Run benchmark registry test**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_benchmark_runner_exposes_fixed_and_qqq_historical_equity_only_strategies -q
```

Expected:

```text
1 passed
```

- [ ] **Step 3: Run full equity-only tests**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py -q
```

Expected:

```text
All tests pass.
```

- [ ] **Step 4: Commit benchmark registration**

Run:

```powershell
git add scripts/run_ai_trading_team_examples_benchmark.py tests/test_ai_trading_team_equity_only_llm.py
git commit -m "feat: register qqq historical equity benchmark"
```

---

## Task 5: Run Regression Tests and Static Checks

**Files:**

- No source edits expected.

- [ ] **Step 1: Run QQQ universe tests**

Run:

```powershell
python -m pytest tests/test_qqq_nport_universe.py -m "not apitest" -q
```

Expected:

```text
All non-API QQQ N-PORT tests pass.
```

- [ ] **Step 2: Run equity-only strategy tests**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py -q
```

Expected:

```text
All equity-only tests pass.
```

- [ ] **Step 3: Run ruff on touched files**

Run:

```powershell
python -m ruff check lumibot/example_strategies/ai_trading_team_equity_only_llm.py lumibot/example_strategies/ai_trading_team_equity_only_helpers.py scripts/run_ai_trading_team_examples_benchmark.py tests/test_ai_trading_team_equity_only_llm.py tests/test_qqq_nport_universe.py
```

Expected:

```text
All checks passed!
```

- [ ] **Step 4: Commit any lint-only fixes**

If Step 3 required formatting/lint fixes, run:

```powershell
git add lumibot/example_strategies/ai_trading_team_equity_only_llm.py lumibot/example_strategies/ai_trading_team_equity_only_helpers.py scripts/run_ai_trading_team_examples_benchmark.py tests/test_ai_trading_team_equity_only_llm.py
git commit -m "style: clean qqq historical equity strategy"
```

If no files changed, do not create an empty commit.

---

## Task 6: Smoke Backtest and Trace Verification

**Files:**

- No source edits expected unless smoke test exposes a bug.

- [ ] **Step 1: Confirm local QQQ N-PORT data is available**

Run:

```powershell
python scripts\collect_qqq_nport_universe.py --as-of 2024-09-05 --mode strict
```

Expected:

```text
2024-09-05 strict: ... report_date=2024-06-30 filing_date=2024-08-28 symbols=...
```

If this command says no snapshot is available, first run:

```powershell
python scripts\collect_qqq_nport_universe.py --start-date 2019-10-01 --end-date 2026-08-21 --mode strict --refresh --write-report
```

Then retry the `--as-of 2024-09-05` command.

- [ ] **Step 2: Run one-week smoke backtest**

Use the same environment/API setup normally used for AI trading team benchmarks. Then run:

```powershell
python scripts\run_ai_trading_team_examples_benchmark.py --strategy qqq-historical-equity-only-llm --start 2024-09-05 --end 2024-09-12 --model openai/gpt-5.6-luna --max-workers 1
```

Expected:

```text
status = success
artifact_dir points to artifacts\ai_trading_team_example_benchmarks\...\qqq-historical-equity-only-llm
```

If the local command-line option is `--end-date` rather than `--end`, inspect:

```powershell
python scripts\run_ai_trading_team_examples_benchmark.py --help
```

and rerun with the actual date option names used by this script.

- [ ] **Step 3: Inspect trace for universe metadata**

Find the newest artifact:

```powershell
Get-ChildItem artifacts\ai_trading_team_example_benchmarks -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1 FullName
```

Then inspect traces:

```powershell
rg -n "\"universe_source\"|qqq_nport|selected_report_date|selected_filing_date|basket_symbols" artifacts\ai_trading_team_example_benchmarks\<NEWEST_RUN>\qqq-historical-equity-only-llm\cache\agent_runtime\traces
```

Expected evidence:

```text
"universe_source"
"type": "qqq_nport"
"mode": "strict"
"selected_report_date": "2024-06-30"
"selected_filing_date": "2024-08-28"
```

- [ ] **Step 4: Record smoke validation note**

Create `docs/superpowers/notes/2026-08-21-qqq-historical-equity-only-llm-validation.md` with:

```markdown
# QQQ Historical Equity-Only LLM Validation

## Commands

```powershell
python -m pytest tests/test_qqq_nport_universe.py -m "not apitest" -q
python -m pytest tests/test_ai_trading_team_equity_only_llm.py -q
python -m ruff check lumibot/example_strategies/ai_trading_team_equity_only_llm.py lumibot/example_strategies/ai_trading_team_equity_only_helpers.py scripts/run_ai_trading_team_examples_benchmark.py tests/test_ai_trading_team_equity_only_llm.py tests/test_qqq_nport_universe.py
python scripts\run_ai_trading_team_examples_benchmark.py --strategy qqq-historical-equity-only-llm --start 2024-09-05 --end 2024-09-12 --model openai/gpt-5.6-luna --max-workers 1
```

## Result

- Unit tests: record the exact pytest pass count from Task 5.
- Ruff: record the exact ruff success line from Task 5.
- Smoke artifact: record the artifact directory printed by the benchmark runner.
- QQQ snapshot used: record `selected_report_date`, `selected_filing_date`, and `accession_number` from trace.
- Equity symbols count: record `len(basket_symbols)` from the equity agent trace context.
- Selected symbol: record `selected_symbol` from the equity agent final JSON summary.
- Execution status: record whether `execution_plan_execute` returned completed, blocked, invalid, or no execution was needed.

## Notes

- The fixed-50 `equity-only-llm` strategy remains available as baseline.
- The new strategy uses strict QQQ N-PORT point-in-time universe selection.
```

Use the concrete outputs observed in Steps 1-3. Do not infer values that were not printed by commands or present in trace.

- [ ] **Step 5: Commit validation note**

Run:

```powershell
git add docs/superpowers/notes/2026-08-21-qqq-historical-equity-only-llm-validation.md
git commit -m "docs: validate qqq historical equity strategy"
```

---

## Task 7: Final Verification and Handoff

**Files:**

- No source edits expected.

- [ ] **Step 1: Run final verification command set**

Run:

```powershell
python -m pytest tests/test_qqq_nport_universe.py -m "not apitest" -q
python -m pytest tests/test_ai_trading_team_equity_only_llm.py -q
python -m ruff check lumibot/example_strategies/ai_trading_team_equity_only_llm.py lumibot/example_strategies/ai_trading_team_equity_only_helpers.py scripts/run_ai_trading_team_examples_benchmark.py tests/test_ai_trading_team_equity_only_llm.py tests/test_qqq_nport_universe.py
```

Expected:

```text
All pytest commands pass.
Ruff reports all checks passed.
```

- [ ] **Step 2: Confirm git state**

Run:

```powershell
git status --short --branch
git log --oneline -5
```

Expected:

```text
No uncommitted source changes.
Recent commits include test/spec/implementation/validation commits for QQQ historical equity-only strategy.
```

- [ ] **Step 3: Summarize implementation outcome**

Prepare a concise handoff summary containing:

```text
Implemented:
- New strategy class:
- Runner key:
- QQQ universe mode:
- Default cadence:
- Validation commands:
- Smoke artifact:

Known limitations:
- Requires local QQQ N-PORT cache.
- Uses quarterly snapshots, not daily holdings.
- Missing/delisted symbol price issues are delegated to market history summary warnings.
- Does not claim performance improvement.
```

Do not merge this branch in this task. Branch integration should use `superpowers:finishing-a-development-branch` after the user reviews benchmark results.
