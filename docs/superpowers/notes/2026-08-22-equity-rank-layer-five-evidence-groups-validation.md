# Equity Rank Layer Five Evidence Groups Validation

## Test Window

- Strategy: `qqq-historical-equity-only-llm`
- Window: `2024-09-05` to `2024-09-06`
- Cadence: weekly, Thursday
- Model: `gpt-5.6-luna`
- Artifact: `artifacts/ai_trading_team_example_benchmarks/20260822_235323_472146/qqq-historical-equity-only-llm`

## Verification

- The smoke backtest completed with status `passed`.
- `equity_basket_agent` called `market_load_history_tables_summary`.
- The tool call used `top_n=10` and `candidate_summary_limit=25`.
- The pruned model-facing tool result was valid JSON and included:
  - `coverage`
  - `rank_groups`
  - `rankings`
  - `ranking_details`
  - `candidate_summary`
- `coverage` reported `96` requested symbols, `96` loaded symbols, `0` failed symbols, `top_n=10`, `candidate_summary_limit=25`, and `25` ranking lists.
- `rank_groups` exposed all five evidence groups:
  - `momentum`
  - `trend_quality`
  - `risk_adjusted_momentum`
  - `breakout_near_high`
  - `volume_confirmation`
- `equity_basket_agent` selected exactly five symbols from the provided QQQ historical universe:
  - `CTAS`
  - `FTNT`
  - `KDP`
  - `ISRG`
  - `CCEP`
- The equity agent's `reason_brief` referenced the five evidence groups rather than blindly copying one ranking list.
- `execution_agent` ran after equity selection and submitted the planned five-stock allocation.

## Tests

- `python -m pytest tests/test_agent_history_summary.py tests/test_agent_manager.py tests/test_ai_trading_team_equity_only_llm.py tests/test_agent_replay_ui_formatters.py tests/backtest/test_agent_runtime_backtest.py::test_duckdb_history_tables_summary_returns_rankings_and_queryable_tables -q`
  - Result: `140 passed, 2 warnings`
- `python -m ruff check lumibot/components/agents/runtime.py lumibot/components/agents/history_summary.py tests/test_agent_history_summary.py`
  - Result: passed

## Notes

- A broader ruff command that included `lumibot/components/agents/builtins.py` and `tests/backtest/test_agent_runtime_backtest.py` still reports pre-existing style debt, mostly long lines and constant-attribute `setattr` calls outside this feature's changed lines.
- This smoke test validates feature wiring and model-facing evidence visibility. It is not a strategy-performance claim.
