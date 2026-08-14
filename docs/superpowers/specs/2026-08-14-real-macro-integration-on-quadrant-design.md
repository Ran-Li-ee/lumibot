# Real Macro Integration On Quadrant Design

## Purpose

Integrate the already-developed real FRED-backed Growth / Inflation macro
regime classifier into the current latest quadrant trading workflow.

The current latest workflow lives on:

```text
feature/commodity-basket-universe-expansion
```

It contains the most recent basket, execution, trace, benchmark, and weekly
cadence work, but its runnable quadrant strategy still uses the mock macro
classifier.

The real macro classifier work lives on:

```text
feature/real-fred-growth-inflation-regime
```

It contains the real FRED-backed classifier, the real strategy entrypoint, and
the low-cost regime scanner, but it does not contain the latest downstream
basket and execution system improvements.

This integration should combine the two lines without losing either:

```text
latest quadrant workflow and execution system
+ real FRED-backed macro_regime_classifier
+ real growth-inflation-quadrant benchmark entrypoint
+ real regime scan tool
```

## Current Evidence

The current branch contains recent work that must be preserved:

- Expanded commodity basket.
- Expanded TIPS basket evidence surface.
- Expanded nominal bond basket and duration prompt.
- Weekly scheduled workflow cadence.
- Execution plan model-facing summary and audit detail split.
- Current execution planner and execution tool behavior.
- Current replay / trace support.

The real macro branch contains the required real classifier work:

```text
lumibot/example_strategies/fred_growth_inflation_regime_classifier.py
lumibot/example_strategies/ai_trading_team_growth_inflation_quadrant.py
scripts/scan_growth_inflation_regimes.py
tests/test_ai_trading_team_growth_inflation_quadrant.py
tests/test_growth_inflation_regime_scan.py
```

It also registers:

```text
growth-inflation-quadrant
```

in:

```text
scripts/run_ai_trading_team_examples_benchmark.py
```

The current branch only registers:

```text
mock-growth-inflation-quadrant
```

for the quadrant workflow.

## Additional Branch Audit Findings

The real macro branch contains two pieces of work that should be treated as
first-class migration targets:

1. The real FRED-backed macro regime classifier and real strategy entrypoint.
2. The no-LLM regime scanner for cheaply inspecting historical quadrant
   changes before running paid LLM backtests.

The scanner is not optional support code. It is the safest way to choose a
useful real-regime backtest window and to verify FRED data availability before
the trading workflow spends model tokens.

The real macro branch also contains downstream helper extraction around names
such as:

```text
_create_growth_inflation_downstream_agents
_run_growth_inflation_downstream_workflow
_log_growth_inflation_workflow_blocked
```

Those helper names are useful, but their old implementation must not be copied
blindly. The current branch has newer basket prompts, expanded universes,
execution tools, trace behavior, weekly cadence, and execution audit handling.
Any shared helper extraction must therefore be recreated from the current
branch's latest mock strategy logic.

No other hidden feature work was found on the real macro branch that should
override current branch behavior. The newer basket, execution, UI, trace, and
weekly cadence work should remain authoritative.

## Design Decision

Use the current latest branch as the integration base.

Do not replace the current branch with the real macro branch. Do not directly
switch development back to `feature/real-fred-growth-inflation-regime`.

Preferred integration branch:

```text
feature/real-macro-integration-on-quadrant
```

Create it from:

```text
feature/commodity-basket-universe-expansion
```

Then selectively bring in the real macro classifier work from:

```text
feature/real-fred-growth-inflation-regime
```

## Non-Goals

This integration must not:

- Remove the mock strategy.
- Rebuild the basket workflow from scratch.
- Redesign the four basket universes.
- Change the 50/25/25/0 Research Affiliates-style weight mapping.
- Change execution order handling beyond what is needed to make the real
  strategy use the current execution system.
- Add new macro data series beyond the already-developed real classifier.
- Let the LLM choose GDP/CPI series or write macro formulas.
- Treat annual performance as final proof of strategy quality.
- Copy older real-branch basket prompts, tool surfaces, or execution behavior
  over the current branch.
- Downgrade the current execution summary/audit split, weekly cadence,
  expanded basket universes, trace behavior, or replay UI compatibility.

## Target Strategy Layout

After integration, there should be two clear benchmark entrypoints:

```text
mock-growth-inflation-quadrant
  -> AITradingTeamMockGrowthInflationQuadrantStrategy

growth-inflation-quadrant
  -> AITradingTeamGrowthInflationQuadrantStrategy
```

The mock strategy remains the workflow, UI, trace, and execution stress-test
entrypoint.

The real strategy becomes the strategy-validation entrypoint.

## Source Files To Bring From Real Macro Branch

Bring these files from `feature/real-fred-growth-inflation-regime`:

```text
lumibot/example_strategies/fred_growth_inflation_regime_classifier.py
lumibot/example_strategies/ai_trading_team_growth_inflation_quadrant.py
scripts/scan_growth_inflation_regimes.py
tests/test_ai_trading_team_growth_inflation_quadrant.py
tests/test_growth_inflation_regime_scan.py
docs/superpowers/specs/2026-08-12-growth-inflation-regime-scan-tool-design.md
docs/superpowers/plans/2026-08-12-growth-inflation-regime-scan-tool.md
```

Bring only what is needed for the real classifier and scanner. The scanner
files are mandatory because scanner validation is the first low-cost gate before
real-strategy LLM backtests.

Do not wholesale replace files that have continued evolving on the current
branch.

## Files Requiring Manual Integration

### Benchmark Registry

File:

```text
scripts/run_ai_trading_team_examples_benchmark.py
```

Add the real strategy registry entry without removing the mock entry:

```text
growth-inflation-quadrant
  -> lumibot.example_strategies.ai_trading_team_growth_inflation_quadrant
  -> AITradingTeamGrowthInflationQuadrantStrategy
```

Also verify the runner can pass the existing model, env-file, run-frequency,
and weekly-run-weekday options to the real strategy.

### Real Strategy Class

File:

```text
lumibot/example_strategies/ai_trading_team_growth_inflation_quadrant.py
```

The real strategy should inherit or reuse the current latest mock strategy
downstream workflow, not the older downstream workflow from the real macro
branch.

It must use:

- current basket universes
- current basket agent prompts
- current basket evidence tools
- current target portfolio planner tool
- current execution plan validation
- current execution plan execute tool
- current weekly cadence behavior
- current trace/replay behavior

Only the macro allocation source should change:

```text
mock macro_regime_classifier
  -> real FRED-backed macro_regime_classifier
```

The real strategy may reuse helper names that existed on the real branch, but
the helper bodies must be adapted from the current branch. In practice, the
current mock strategy should expose or share current-equivalent helpers for:

- creating current basket agents with current `basket_agent_tools`,
  `basket_agent_system_prompt`, and `basket_agent_task_prompt`
- running the current portfolio decision flow with the current target portfolio
  planner tool
- running the current execution flow with the current execution plan execute
  tool
- preserving current scheduled workflow state, including
  `_scheduled_workflow_decision`, `_mark_scheduled_workflow_attempted`, and any
  current skip/block bookkeeping
- preserving current trace/replay payload shapes

### Mock Strategy

File:

```text
lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py
```

Avoid replacing this file with the real macro branch version.

If shared helper extraction is needed, make the smallest safe change while
preserving the current latest behavior and tests.

Do not run a command that restores the old real-branch mock strategy over this
file. The mock strategy is currently the best reference implementation for the
downstream basket, portfolio decision, execution, weekly cadence, and trace
workflow.

## Real Macro Classifier Contract

The real macro classifier tool should keep the same public tool name:

```text
macro_regime_classifier
```

It should be available only to:

```text
macro_allocation_agent
```

It should not be given to:

- basket agents
- portfolio_decision_agent
- execution_agent

The real tool should use:

```text
GDPC1    Real GDP
CPIAUCSL CPI inflation
```

Default mode:

```text
fred_ra_simple_lagged
```

Default lags:

```text
growth_lag_months = 6
inflation_lag_months = 1
trend_years = 5
```

The classifier must compute:

```text
growth_metric = year-over-year real GDP growth
growth_trend = five-year average of the growth metric

inflation_metric = year-over-year CPI inflation
inflation_trend = five-year average of the inflation metric
```

Then:

```text
growth_metric > growth_trend       => growth_up
growth_metric <= growth_trend      => growth_down

inflation_metric > inflation_trend => inflation_up
inflation_metric <= inflation_trend=> inflation_down
```

The output must include enough evidence for UI and audit:

- status
- tool
- mock flag
- mode
- date / as_of
- regime
- growth_direction
- inflation_direction
- basket_weights
- growth_evidence
- inflation_evidence
- data_quality
- confidence
- reason_brief or reason fields
- blocked/failed reason when classification cannot proceed

The LLM must not infer missing macro fields.

If the tool returns `status=blocked` or `status=failed`, the strategy should
block the downstream workflow for that scheduled review instead of asking basket
agents to continue from incomplete macro evidence.

## Prompt Integration

The real strategy's macro allocation agent prompt must not mention mock data.

It should say, in effect:

```text
Call the real FRED-backed macro_regime_classifier.
Do not classify the macro regime yourself.
Preserve the classifier's structured result.
Do not place orders.
```

The real strategy's task prompt should also avoid mock wording and should ask
for one JSON object preserving the classifier result.

Basket, portfolio decision, and execution prompts should stay aligned with the
current latest branch, not older prompts from the real macro branch.

## Weekly Cadence Requirement

The real strategy must support the same cadence parameters as the current mock
strategy:

```text
run_frequency
weekly_run_weekday
weekly_holiday_policy
```

The default for the real strategy should be weekly.

The low-cost regime scanner is separate from trading cadence. It may scan weekly
dates directly without invoking LLMs or trading tools.

## Regime Scanner Requirement

Bring forward the existing scanner:

```text
scripts/scan_growth_inflation_regimes.py
```

The scanner should:

- load FRED credentials from an env file
- call the real classifier without LLMs
- scan a date range on a configurable frequency
- write machine-readable artifacts
- write human-readable artifacts
- report regime transitions
- redact secrets from failures

The scanner is the first validation step before any paid annual LLM backtest.

## Validation Plan

Validation should proceed from cheap to expensive.

### 1. Static and Unit Tests

Run focused tests first:

```text
tests/test_ai_trading_team_growth_inflation_quadrant.py
tests/test_growth_inflation_regime_scan.py
tests/test_ai_trading_team_mock_growth_inflation_quadrant.py
```

Then run the broader relevant suite if focused tests pass.

### 2. Real Classifier Unit Validation

Verify the real classifier:

- calculates GDP evidence
- calculates CPI evidence
- applies configured lags
- honors configured trend years
- maps directions to regimes
- returns structured blocked/failed results when data is not usable
- does not mutate last regime state on failed/blocked results

### 3. Downstream Helper Reuse Validation

Verify the real strategy:

- uses current basket agent construction behavior
- uses current basket evidence tools
- uses current target portfolio planner behavior
- uses current execution plan execute behavior
- uses current weekly cadence state handling
- does not call stale real-branch downstream helper implementations

### 4. Scanner Validation

Run the scanner over a short range first. This is mandatory before any paid
real-strategy LLM backtest.

Then scan the past two years weekly.

Expected output:

- at least one generated artifact directory
- CSV/JSON/Markdown output
- no leaked API key
- visible regime counts
- visible transition list

### 5. One-Day Real Strategy Backtest

Run:

```text
--strategy growth-inflation-quadrant
```

over one known date.

Expected:

- benchmark status passed
- macro trace calls the real classifier
- macro trace shows `mock=false`
- downstream agents receive real macro report
- account curve and performance artifacts generate when trades occur
- no unexpected negative cash or blocked order errors

### 6. Short Weekly Real Strategy Backtest

Run a one-month weekly backtest on a window selected by the scanner.

Expected:

- full workflow traces appear only on weekly scheduled reviews
- real macro regimes are used
- at least one basket weight change is visible if scanner chose a transition
  window
- execution uses current execution plan execute behavior

### 7. Annual Weekly Real Strategy Backtest

Only after the above passes, run an annual weekly backtest.

This annual backtest should be interpreted as:

- real macro classifier integration validation
- system stability validation
- early strategy-behavior evidence

It should not be treated as final proof of profitability.

## Merge Strategy

Preferred implementation method:

1. Create a new integration branch from current latest:

   ```text
   feature/real-macro-integration-on-quadrant
   ```

2. Copy or restore the real macro files from
   `feature/real-fred-growth-inflation-regime`.

3. Manually integrate benchmark registry changes.

4. Update the real strategy class to reuse current latest downstream workflow
   and weekly cadence.

5. Run focused tests.

6. Run scanner.

7. Run one-day real backtest.

8. Run short weekly real backtest.

9. Commit the integrated result only after validation passes.

Do not use a blind full-branch merge unless manual file-by-file integration
proves unnecessarily difficult.

If cherry-picking a real-branch commit, expect conflicts or stale helper shapes.
Resolve those conflicts by preserving the current branch's basket, execution,
weekly cadence, and trace behavior. Do not use `git checkout` or equivalent
file replacement to overwrite the current mock strategy with the older
real-branch version.

## Risks And Mitigations

### Risk: overwriting current latest basket/execution behavior

Mitigation: use the current branch as base and manually bring only real macro
files and registry entries forward.

### Risk: real strategy inherits stale downstream behavior

Mitigation: adapt `AITradingTeamGrowthInflationQuadrantStrategy` to call current
shared downstream helpers from the current mock strategy.

### Risk: weekly cadence missing from real strategy

Mitigation: explicitly test real strategy weekly defaults and benchmark
`--run-frequency` overrides.

### Risk: FRED data fails during a paid LLM backtest

Mitigation: run the no-LLM scanner first. Do not run annual paid backtests until
scanner output is clean.

### Risk: prompt says mock when real tool is used

Mitigation: add tests that serialized real strategy prompts include real
FRED-backed wording and do not include mock wording for the real strategy.

### Risk: UI confusion between mock and real strategy runs

Mitigation: keep benchmark strategy names distinct:

```text
mock-growth-inflation-quadrant
growth-inflation-quadrant
```

### Risk: annual results are over-interpreted

Mitigation: label the first annual real run as integration and behavior
validation, not final strategy validation.

## Acceptance Criteria

The integration is complete when:

1. `growth-inflation-quadrant` is registered in the benchmark runner.
2. `mock-growth-inflation-quadrant` still works.
3. The real strategy uses the real FRED-backed `macro_regime_classifier`.
4. The real strategy uses current latest basket universes and execution system.
5. The real strategy supports weekly cadence.
6. The real scanner can scan the past two years without LLM calls.
7. The scanner writes CSV, JSON, and Markdown artifacts with regime counts and
   transitions.
8. The real strategy reuses current downstream helper behavior rather than the
   stale real-branch downstream implementation.
9. Focused tests pass.
10. A one-day real strategy benchmark passes.
11. A short weekly real strategy benchmark passes.
12. The generated traces show real macro evidence, not mock macro evidence.

## Future Work

After this integration, the next development steps are:

1. Use the scanner to choose a one-year window containing at least two real
   regimes.
2. Run the annual weekly real strategy backtest.
3. Analyze performance, drawdown, turnover, blocked orders, and regime behavior.
4. Decide whether the real classifier needs smoothing, confidence bands, or
   less frequent macro updates.
