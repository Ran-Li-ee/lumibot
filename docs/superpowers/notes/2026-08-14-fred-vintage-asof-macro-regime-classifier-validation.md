# FRED Vintage As-Of Macro Regime Classifier Validation

## Test Commands

- `D:\Lumibot\.venv\Scripts\python.exe -m pytest tests/test_ai_trading_team_growth_inflation_quadrant.py -q`
  - Result: `82 passed, 1 warning`
- `D:\Lumibot\.venv\Scripts\python.exe -m pytest tests/test_growth_inflation_regime_scan.py -q`
  - Result: `44 passed, 1 warning`
- `D:\Lumibot\.venv\Scripts\python.exe -m pytest tests/test_fred_macro.py tests/test_fred_growth_inflation_data_availability.py tests/test_ai_trading_team_growth_inflation_quadrant.py tests/test_ai_trading_team_mock_growth_inflation_quadrant.py tests/test_growth_inflation_regime_scan.py -q`
  - Result: `226 passed, 1 warning`
- `D:\Lumibot\.venv\Scripts\python.exe -m ruff check lumibot/example_strategies/fred_growth_inflation_regime_classifier.py lumibot/example_strategies/ai_trading_team_growth_inflation_quadrant.py scripts/scan_growth_inflation_regimes.py tests/test_ai_trading_team_growth_inflation_quadrant.py tests/test_growth_inflation_regime_scan.py`
  - Result: `All checks passed!`
- `D:\Lumibot\.venv\Scripts\python.exe -m pytest tests/test_agent_runtime_provider_keys.py::test_openai_luna_tool_calls_force_reasoning_effort_none tests/test_agent_runtime_provider_keys.py::test_bare_openai_luna_tool_calls_force_reasoning_effort_none tests/test_agent_runtime_provider_keys.py::test_openai_luna_without_tools_omits_reasoning_effort_for_chat_completions tests/test_agent_runtime_provider_keys.py::test_openai_non_luna_tool_calls_preserve_reasoning_effort -q`
  - Result: `4 passed, 1 warning`
- `D:\Lumibot\.venv\Scripts\python.exe -m ruff check lumibot/components/agents/runtime.py tests/test_agent_runtime_provider_keys.py`
  - Result: `All checks passed!`

## Smoke Backtest

- Command:

```powershell
$env:AI_TRADING_TEAM_MODEL = 'gpt-5.6-luna'
D:\Lumibot\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy growth-inflation-quadrant --start 2025-06-24 --end 2025-07-08 --run-frequency weekly --max-workers 1 --env-file D:\Lumibot\project_notes\API.txt
```

- Strategy: `growth-inflation-quadrant`
- Strategy class: `AITradingTeamGrowthInflationQuadrantStrategy`
- Date window: `2025-06-24` to `2025-07-08`
- Effective backtest end in settings: `2025-07-07 23:59:00-04:00`
- Run frequency: `weekly`
- Model: `gpt-5.6-luna`
- Backtest run id: `20260814_221006_841484`
- Artifact directory:
  - `artifacts/ai_trading_team_example_benchmarks/20260814_221006_841484/growth-inflation-quadrant`
- Result: `passed`
- Wall time: `224156 ms`
- Final reported positions:
  - USD: `1810.9841346740634`
  - PPLT: `2016`
  - EEM: `507`
  - SCHR: `1993`
- Reported benchmark result:
  - Total return: `0.011464082054491564`
  - Max drawdown: `0.0029284294243617443`
  - Sharpe: `7.804079675619861`

## Macro Tool Evidence

The macro tool was inspected from:

- `artifacts/ai_trading_team_example_benchmarks/20260814_221006_841484/growth-inflation-quadrant/stats_agent_detail.parquet`

The smoke run produced three `macro_allocation_agent` calls. Each one called `macro_regime_classifier` exactly once.

### 2025-06-24 System Run

- Tool input:
  - `mode`: `fred_ra_vintage_asof`
  - `as_of_policy`: `same_day_vintage`
  - `date`: `2025-06-24`
  - `requested_as_of`: `2025-06-24`
  - `growth_series_id`: `GDPC1`
  - `inflation_series_id`: `CPIAUCSL`
  - `trend_years`: `5`
- Tool output:
  - `status`: `passed`
  - `regime`: `growth_down_inflation_down`
  - `basket_weights`: equity `0.25`, commodity `0.25`, tips `0.0`, nominal_bond `0.5`
  - `requested_as_of`: `2025-06-24`
  - `effective_as_of`: `2025-06-24`
  - `lookahead_clamped`: `false`
  - `as_of_policy`: `same_day_vintage`
  - `data_quality.point_in_time_safe`: `true`
  - `data_quality.uses_revised_data`: `false`
  - `data_quality.source`: `fred_api`
  - `data_quality.warnings`: `[]`
  - `data_quality.errors`: `[]`
- Growth evidence:
  - `series_id`: `GDPC1`
  - `latest_observation_date`: `2025-01-01`
  - `comparison_observation_date`: `2024-01-01`
  - `latest_realtime_start`: `2025-06-24`
  - `latest_realtime_end`: `2025-06-24`
  - `comparison_realtime_start`: `2025-06-24`
  - `comparison_realtime_end`: `2025-06-24`
  - `observation_lag_days`: `174`
  - `metric_value`: `0.02058260454086347`
  - `trend_value`: `0.024833877477764728`
  - `direction`: `down`
- Inflation evidence:
  - `series_id`: `CPIAUCSL`
  - `latest_observation_date`: `2025-05-01`
  - `comparison_observation_date`: `2024-05-01`
  - `latest_realtime_start`: `2025-06-24`
  - `latest_realtime_end`: `2025-06-24`
  - `comparison_realtime_start`: `2025-06-24`
  - `comparison_realtime_end`: `2025-06-24`
  - `observation_lag_days`: `54`
  - `metric_value`: `0.023759340869898393`
  - `trend_value`: `0.04305766433970326`
  - `direction`: `down`

### 2025-06-30 System Run

- Tool input:
  - `mode`: `fred_ra_vintage_asof`
  - `as_of_policy`: `same_day_vintage`
  - `date`: `2025-06-30`
  - `requested_as_of`: `2025-06-30`
  - `growth_series_id`: `GDPC1`
  - `inflation_series_id`: `CPIAUCSL`
  - `trend_years`: `5`
- Tool output:
  - `status`: `passed`
  - `regime`: `growth_down_inflation_down`
  - `requested_as_of`: `2025-06-30`
  - `effective_as_of`: `2025-06-30`
  - `lookahead_clamped`: `false`
  - `as_of_policy`: `same_day_vintage`
  - `data_quality.point_in_time_safe`: `true`
  - `data_quality.uses_revised_data`: `false`
- Growth evidence:
  - `latest_observation_date`: `2025-01-01`
  - `latest_realtime_start`: `2025-06-30`
  - `latest_realtime_end`: `2025-06-30`
  - `observation_lag_days`: `180`
  - `metric_value`: `0.01991763088930587`
  - `trend_value`: `0.024800628795186845`
  - `direction`: `down`
- Inflation evidence:
  - `latest_observation_date`: `2025-05-01`
  - `latest_realtime_start`: `2025-06-30`
  - `latest_realtime_end`: `2025-06-30`
  - `observation_lag_days`: `60`
  - `metric_value`: `0.023759340869898393`
  - `trend_value`: `0.04305766433970326`
  - `direction`: `down`

### 2025-07-07 System Run

- Tool input:
  - `mode`: `fred_ra_vintage_asof`
  - `as_of_policy`: `same_day_vintage`
  - `date`: `2025-07-07`
  - `growth_series_id`: `GDPC1`
  - `inflation_series_id`: `CPIAUCSL`
  - `trend_years`: `5`
- Tool output:
  - `status`: `passed`
  - `regime`: `growth_down_inflation_down`
  - `requested_as_of`: `2025-07-07`
  - `effective_as_of`: `2025-07-07`
  - `lookahead_clamped`: `false`
  - `as_of_policy`: `same_day_vintage`
  - `data_quality.point_in_time_safe`: `true`
  - `data_quality.uses_revised_data`: `false`
- Growth evidence:
  - `latest_observation_date`: `2025-01-01`
  - `latest_realtime_start`: `2025-07-07`
  - `latest_realtime_end`: `2025-07-07`
  - `observation_lag_days`: `187`
  - `metric_value`: `0.01991763088930587`
  - `trend_value`: `0.024800628795186845`
  - `direction`: `down`
- Inflation evidence:
  - `latest_observation_date`: `2025-05-01`
  - `latest_realtime_start`: `2025-07-07`
  - `latest_realtime_end`: `2025-07-07`
  - `observation_lag_days`: `67`
  - `metric_value`: `0.023759340869898393`
  - `trend_value`: `0.04305766433970326`
  - `direction`: `down`

## Result

- The real quadrant strategy defaulted to `macro_regime_mode=fred_ra_vintage_asof`.
- The real quadrant strategy defaulted to `as_of_policy=same_day_vintage`.
- The real macro allocation agent called the classifier rather than hand-classifying the regime.
- The classifier output recorded `requested_as_of`, `effective_as_of`, `lookahead_clamped`, and `as_of_policy`.
- All inspected system runs had `effective_as_of` equal to the simulated system run date and not after it.
- All inspected system runs reported `point_in_time_safe=true` and `uses_revised_data=false`.
- The benchmark proceeded through basket agents, portfolio decision, and execution.

## Follow-Up Issues

- The first smoke attempt with bare model name `gpt-5.6-luna` failed before the macro evidence could be validated because LiteLLM rejected tool calls with `reasoning_effort` for Luna on `/v1/chat/completions`.
- The runtime sanitizer originally handled provider-prefixed `openai/gpt-5.6-luna` but not bare `gpt-5.6-luna`.
- Fixed in commit `dc0c972f` by normalizing provider-prefixed model strings before Luna matching and adding coverage for the bare model name.
- The later smoke backtest above confirms the bare Luna model can call tools after the sanitizer fix.
