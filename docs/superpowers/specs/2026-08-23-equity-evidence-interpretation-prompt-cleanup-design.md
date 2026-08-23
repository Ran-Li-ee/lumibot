# Equity Evidence Interpretation Prompt Cleanup Design

## Purpose

Clean up and lightly rewrite the equity selection prompts so the LLM knows how to interpret the five evidence groups produced by `market_load_history_tables_summary`.

The immediate goal is not to add more indicators, change rankings, change order execution, or optimize performance. The goal is to remove hidden prompt ambiguity and add one short, clear evidence interpretation policy.

The target behavior is:

```text
rank data first
  -> interpret the five evidence groups with clear priority
  -> choose exactly five stocks
  -> use news only when rank evidence is close, conflicting, or uncertain
```

## Background

The current QQQ historical equity-only strategy asks `equity_basket_agent` to select exactly five symbols from the point-in-time QQQ constituent universe. A deterministic planner then assigns equal target weights to those five stocks and sends an execution plan to `execution_agent`.

Recent work upgraded the rank layer so `market_load_history_tables_summary` returns five evidence groups:

```text
1. momentum
2. trend_quality
3. risk_adjusted_momentum
4. breakout_near_high
5. volume_confirmation
```

The current prompt already tells the model to compare these groups and not blindly copy one ranking list. That was useful, but still too vague. It does not tell the model which groups are primary evidence, which groups are quality checks, and which groups are confirmation only.

That ambiguity can make the LLM treat all rank groups as equal votes, overvalue volume, overvalue one isolated ranking, or use news when rank evidence is already clear.

## Design Principle

This feature should reduce prompt ambiguity, not increase prompt length.

The fix should be a concise prompt rewrite:

1. Delete vague or misleading old wording.
2. Add one short `Evidence Interpretation Policy`.
3. Keep tool-use instructions minimal.
4. Keep output-format instructions strict.
5. Preserve the existing workflow shape.

## Literature-Informed Interpretation

The evidence policy is informed by the strategy research we have already discussed:

1. Classic cross-sectional momentum research supports treating multi-month relative strength as primary evidence.
2. Clenow-style momentum systems support using trend quality and adjusted slope to prefer smoother, more persistent momentum.
3. Risk-adjusted momentum helps avoid selecting names that are strong only because they are extremely volatile.
4. 52-week-high and breakout research supports using near-high / breakout data as leadership and timing confirmation.
5. CAN SLIM / IBD-style volume logic supports using volume as confirmation, not as a standalone buy reason.

The prompt should not cite these papers directly. It should translate them into short operating rules for the LLM.

## Scope

This feature includes:

1. Review the current equity agent system prompt and task prompt for hidden misleading hints.
2. Rewrite `equity_basket_agent_system_prompt`.
3. Rewrite `qqq_historical_equity_basket_agent_system_prompt`.
4. Rewrite `equity_basket_agent_task_prompt`.
5. Add a concise `Evidence Interpretation Policy` to both equity system prompts.
6. Keep the task prompt focused on the current run: call the summary tool, compare evidence, optionally use news, return strict JSON.
7. Add or update prompt tests that lock in the intended interpretation policy.
8. Run focused tests and a one-day smoke backtest after implementation.

## Non-Goals

This feature does not:

1. Change `market_load_history_tables_summary` output.
2. Add, remove, or recalculate indicators.
3. Change ranking list membership.
4. Change QQQ historical universe resolution.
5. Change the selected-symbol count of five.
6. Change equal-weight target portfolio construction.
7. Change `target_portfolio_to_execution_plan`.
8. Change `execution_agent` tools or behavior, except for checking that prompts remain compatible.
9. Change news API behavior.
10. Add fundamental-data tools.
11. Add sector, style, safety, defensive, speculative, or cyclicality labels.
12. Run long-window performance optimization.

## Current Prompt Problems To Fix

### 1. Equal-Weight Evidence Ambiguity

Current prompt wording says the five rank groups are separate evidence lenses, but it does not specify priority.

Risk:

```text
The model may treat momentum, trend quality, risk-adjusted momentum, breakout, and volume as equal votes.
```

Fix:

```text
Momentum and trend quality are primary evidence.
Risk-adjusted momentum is a quality check.
Breakout / near-high is timing and leadership confirmation.
Volume confirmation is supporting evidence only.
```

### 2. Vague "Leading Group" Wording

Current wording says:

```text
If the leading group is clear ...
```

Risk:

```text
The model may interpret "group" as one evidence group rather than a leading candidate set.
```

Fix:

Use clearer wording:

```text
If the leading candidate set is clear from rank evidence, select without news.
```

### 3. Single-Group Permission

Current wording allows:

```text
If a selected symbol is supported by only one group, explain why ...
```

Risk:

```text
The model may see single-group support as normal.
```

Fix:

Make single-group selection an exception:

```text
Avoid selecting a stock supported by only one evidence group unless the other candidates are weaker or conflicting; explain the exception.
```

### 4. Composite Score Reminder

Current wording says not to treat `composite_score` as final.

Risk:

```text
This is useful guardrail text, but repeating composite_score can keep the idea salient.
```

Fix:

Keep one concise guardrail only if tests still need it:

```text
Do not treat any single ranking or combined score as the final answer.
```

This avoids spotlighting `composite_score` by name unless required by current tests.

### 5. News Trigger Ambiguity

Current wording correctly says news is conditional, but it should remain subordinate to rank evidence.

Risk:

```text
The model may use news too broadly or as a replacement for rank evidence.
```

Fix:

State:

```text
Use news only as a tie-breaker or risk/catalyst check for leading candidates when rank evidence is close, conflicting, or uncertain.
```

## Evidence Interpretation Policy

Add this short policy, or an equivalent concise version, to the equity agent system prompts:

```text
Evidence Interpretation Policy:
Use momentum and trend quality as primary selection evidence.
Use risk-adjusted momentum to prefer strength that is not purely volatility-driven.
Use breakout / near-high evidence as timing and leadership confirmation.
Use volume confirmation only as supporting evidence, not as a standalone reason to select a stock.
Prefer candidates that are strong across primary evidence and confirmed by secondary evidence.
Do not average all ranking groups equally, and do not select a stock solely because it leads one ranking list.
```

This policy is intentionally short. It should not include formulas, academic citations, or long examples.

## Prompt Architecture

### System Prompt Shape

The system prompt should define:

1. Agent role.
2. Universe boundary.
3. Selection count.
4. Tool-use boundary.
5. Evidence Interpretation Policy.
6. News-use policy.
7. Output discipline.

It should not mix in:

1. Per-run tool arguments.
2. JSON schema details beyond "strict JSON".
3. Formula descriptions.
4. Portfolio sizing instructions.
5. Order execution instructions.

### Task Prompt Shape

The task prompt should define:

1. Use only `basket_symbols`.
2. First call `market_load_history_tables_summary` with:

   ```text
   symbols=basket_symbols
   length=252
   timestep='day'
   top_n=10
   candidate_summary_limit=25
   ```

3. Compare the five evidence groups using the system prompt policy.
4. Select exactly five unique symbols.
5. Use `alpaca_news` only for leading candidates if rank evidence is close, conflicting, or uncertain.
6. Return exactly one strict JSON object with:

   ```text
   basket_id
   target_weight
   status
   candidate_symbols
   selected_symbols
   reason_brief
   ```

7. Require `candidate_symbols` to copy the assigned `basket_symbols` exactly.
8. Require `selected_symbols` to contain exactly five unique symbols from `basket_symbols`.

## Prompt Text To Remove Or Avoid

The rewritten prompts should avoid:

1. `leading group` if it can be confused with an evidence group.
2. Any wording that implies all five evidence groups have equal weight.
3. Any wording that implies volume can independently justify selection.
4. Any wording that encourages selecting the safest, most defensive, most stable, or lowest-volatility stock.
5. Any wording that encourages selecting the highest raw-return stock blindly.
6. Any wording that encourages broad news searches across the full universe.
7. Any wording that encourages DuckDB SQL for ordinary rank interpretation.
8. Any wording that asks the equity agent to size trades, produce orders, or optimize weights.
9. Sector/style labels such as core, growth, defensive, cyclical, speculative, or quality unless those are backed by future explicit data fields.

## Expected Model Behavior

After the prompt cleanup, the equity agent should reason roughly like this:

```text
1. Identify leading candidates from momentum and trend quality.
2. Check whether those candidates still look attractive on risk-adjusted momentum.
3. Use breakout / near-high evidence to confirm leadership and timing.
4. Use volume as confirmation only, especially when candidates are close.
5. If rank evidence is clear, select five without news.
6. If rank evidence is close, conflicting, or uncertain, call news only for leading candidates.
7. Explain selected symbols by evidence groups, not by invented labels.
```

This should be stated as policy, not as an overly detailed chain-of-thought requirement.

## Testing Requirements

### Prompt Unit Tests

Update or add tests in:

```text
tests/test_ai_trading_team_equity_only_llm.py
```

Tests should verify that both equity system prompts:

1. Include `Evidence Interpretation Policy`.
2. Say momentum and trend quality are primary selection evidence.
3. Say risk-adjusted momentum is used to prefer strength not purely volatility-driven.
4. Say breakout / near-high is timing and leadership confirmation.
5. Say volume confirmation is supporting evidence only.
6. Say not to average all ranking groups equally.
7. Say not to select solely because a stock leads one ranking list.
8. Preserve conditional news behavior.
9. Preserve strict JSON behavior.
10. Preserve no-order/no-sizing boundary.

Tests should verify that the task prompt:

1. Calls `market_load_history_tables_summary` first.
2. Uses `length=252`.
3. Uses `timestep='day'`.
4. Uses `top_n=10`.
5. Uses `candidate_summary_limit=25`.
6. Requires exactly five selected symbols.
7. Requires `candidate_symbols` to copy `basket_symbols`.
8. Refers back to the evidence interpretation policy rather than repeating a long policy.

Tests should verify that prompts do not contain:

1. `leading group`
2. `safest`
3. `defensive`
4. `cyclical`
5. `speculative`
6. `optimize weights`
7. `DuckDB` as a normal required step

If existing tests still assert the literal term `composite_score`, update them to prefer the more general guardrail:

```text
Do not treat any single ranking or combined score as the final answer.
```

### Focused Regression Tests

Run the focused tests covering the equity strategy prompts and rank-layer assumptions:

```text
python -m pytest tests/test_ai_trading_team_equity_only_llm.py tests/test_agent_manager.py -q
```

If implementation only changes the strategy prompt file, a smaller test command is acceptable first, followed by the broader command before completion.

### Smoke Backtest

Run a one-day QQQ historical equity-only smoke backtest after implementation.

The smoke test should verify:

1. `equity_basket_agent` sees the rewritten system prompt.
2. `equity_basket_agent` sees the rewritten task prompt.
3. `equity_basket_agent` calls `market_load_history_tables_summary`.
4. The call uses `top_n=10` and `candidate_summary_limit=25`.
5. The agent selects exactly five symbols from `basket_symbols`.
6. `reason_brief` references evidence groups in a way consistent with the new policy.
7. The workflow reaches deterministic planning and execution.

The smoke test is a prompt-wiring and workflow check, not a performance claim.

## Acceptance Criteria

This feature is complete when:

1. Equity system prompts are rewritten around the short Evidence Interpretation Policy.
2. Vague or misleading wording is removed, especially `leading group`.
3. Prompt text distinguishes primary evidence, quality checks, timing confirmation, and supporting confirmation.
4. News remains conditional and candidate-limited.
5. The equity agent still returns strict JSON with exactly five selected symbols.
6. No indicator logic, universe logic, or execution logic changes are introduced.
7. Focused prompt tests pass.
8. A one-day smoke backtest confirms the updated prompt appears in trace and the strategy still runs.

## Risks And Mitigations

| Risk | Mitigation |
|---|---|
| Prompt becomes too long again | Keep policy short and avoid formula explanations. |
| LLM overweights momentum and ignores risk | Explicitly define risk-adjusted momentum as a quality check. |
| LLM overweights volume | State volume is supporting evidence only. |
| LLM treats breakout as a standalone reason | Define breakout / near-high as timing and leadership confirmation, not primary evidence. |
| LLM stops using news entirely | Preserve conditional news trigger for close, conflicting, or uncertain leading candidates. |
| Tests become brittle around exact wording | Test for key policy concepts, not full paragraph equality. |
| Generic and QQQ prompts drift apart | Use shared helper text for the Evidence Interpretation Policy if implementation patterns make that clean. |

## Future Work

Not part of this feature:

1. Evaluate whether the policy improves five-year performance.
2. Add fundamentals or earnings evidence.
3. Add sector or theme diversification controls.
4. Add a model-independent rank voting or scoring layer.
5. Tune how often news should be used.
6. Change candidate summary size or ranking group composition.
