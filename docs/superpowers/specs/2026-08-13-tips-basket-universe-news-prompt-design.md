# TIPS Basket Universe, News Evidence, And Prompt Design

## Purpose

This spec defines a focused enhancement of the TIPS basket in the mock
growth/inflation quadrant strategy.

The goal is to make `tips_basket_agent` behave like a defensive
inflation-protection specialist. It should choose from a compact TIPS universe
using computed price/statistical rankings as the primary evidence, and use
Alpaca/Benzinga news only as secondary context when the ranking evidence is
close, conflicting, incomplete, stale, or when long-duration TIPS looks
unusually attractive.

This is not a macro-classifier feature and not a new FRED/real-yield evidence
tool. Those are intentionally deferred.

## Background

The current strategy lives in:

```text
lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py
```

The current TIPS basket is:

```python
"tips": ["TIP", "SCHP", "VTIP", "STIP", "LTPZ"]
```

The strategy's current Research Affiliates-style four-quadrant weight mapping
assigns the largest TIPS weight in the growth-down / inflation-up regime:

| Regime | Equity | Commodity | TIPS | Nominal Bond |
|---|---:|---:|---:|---:|
| `growth_up_inflation_down` | 0.50 | 0.25 | 0.00 | 0.25 |
| `growth_up_inflation_up` | 0.25 | 0.50 | 0.25 | 0.00 |
| `growth_down_inflation_up` | 0.00 | 0.25 | 0.50 | 0.25 |
| `growth_down_inflation_down` | 0.25 | 0.25 | 0.00 | 0.50 |

The TIPS basket's role is therefore defensive: protect purchasing power and
control drawdown when inflation pressure is important and growth is weak. The
agent should not treat the TIPS basket like the equity or commodity basket.

## Scope

This feature includes:

1. Expand `BASKET_UNIVERSES["tips"]` to the approved six-symbol list.
2. Keep the TIPS basket compact and purpose-specific rather than expanding it
   into a large opportunistic universe.
3. Give `tips_basket_agent` access to `alpaca_news` as conditional secondary
   evidence, if Alpaca news credentials are configured.
4. Update `tips_basket_agent` system prompt and task prompt so the basket role
   is defensive, rank-first, and duration-aware.
5. Preserve the current flat model-facing candidate list. The model should see
   the assigned symbols but should not receive hard category labels that force a
   preselected answer.
6. Update tests that currently assume the old five-symbol TIPS universe.
7. Verify that all six TIPS symbols can be loaded through the current Yahoo
   daily backtest data path for a representative window.
8. Verify that the strategy still works when Alpaca news credentials are
   missing; rankings alone must remain sufficient.
9. Verify with at least one one-day mock growth/inflation quadrant backtest that
   the expanded TIPS basket does not break macro allocation, basket reports,
   portfolio decision, execution-plan generation, execution, trace capture, or
   replay UI discovery.

## Non-Goals

This feature does not:

1. Add FRED real-yield, breakeven-inflation, Fed Funds, Treasury-yield, or CPI
   tools.
2. Add a dedicated TIPS fundamentals or real-rate scoring model.
3. Change macro regime classification.
4. Change basket target weights.
5. Change portfolio decision, target-portfolio planner, execution-plan logic,
   order-confirmation logic, or replay UI schemas.
6. Expand the nominal bond basket.
7. Expand the TIPS basket to dozens of near-duplicate funds.
8. Make any performance claim about TIPS or about this four-quadrant strategy.
9. Require news before selecting a TIPS symbol.

## Approved TIPS Universe

`BASKET_UNIVERSES["tips"]` must become exactly:

```python
[
    "VTIP",
    "STIP",
    "SCHP",
    "TIP",
    "SPIP",
    "LTPZ",
]
```

The ordering is a stable configuration order, not a preference ranking.

The intended role of each symbol is:

| Symbol | Intended Interpretation |
|---|---|
| `VTIP` | Short-duration TIPS exposure; usually more stable and lower duration risk. |
| `STIP` | Short-duration TIPS exposure; similar defensive role to `VTIP`. |
| `SCHP` | Broad/full-curve TIPS exposure; low-cost standard TIPS allocation. |
| `TIP` | Broad/full-curve TIPS exposure; liquid standard TIPS allocation. |
| `SPIP` | Broad/full-curve TIPS exposure; additional standard TIPS candidate. |
| `LTPZ` | Long-duration TIPS exposure; higher volatility and more real-rate sensitivity. |

These interpretations are for design and prompt intent. The model-facing context
should still pass `basket_symbols` as a flat symbol list.

## Evidence Policy

### Primary Evidence

The primary evidence for `tips_basket_agent` is the existing computed
price/statistical ranking output from:

```text
market_load_history_tables_summary
```

The agent should first compare:

- composite ranking
- momentum ranking
- trend alignment
- drawdown
- volatility
- range/price context
- any other existing model-facing history summary fields

The agent should not call news by default when one symbol is clearly superior
across the ranking evidence and the choice is consistent with the defensive
TIPS role.

### Secondary News Evidence

`alpaca_news` should be available to `tips_basket_agent` only as secondary
evidence.

News may be useful when:

1. short-duration and full-curve TIPS candidates are close;
2. ranking evidence is conflicting, incomplete, or stale;
3. `LTPZ` ranks unusually well and the agent needs context before taking
   long-duration risk;
4. price evidence conflicts with the macro allocation;
5. the agent needs supporting context about Fed policy, Treasury yields, real
   yields, rate-cut/rate-hike expectations, inflation expectations, or
   bond-market stress.

News must not be used as a keyword search. The current Alpaca news tool is
symbol/date-window retrieval. The prompt should teach the agent to use
symbol-based scans.

Recommended symbol groups for news scans:

```text
TIPS candidates: VTIP,STIP,SCHP,TIP,SPIP,LTPZ
Rate/bond proxies: TLT,IEF,SHY,GOVT
Broad-market stress proxies: SPY,QQQ,DIA,IWM
```

The agent should usually scan first with `include_content=False`. If an article
looks important, it may call the same tool again with `include_content=True` and
`exclude_contentless=True` for a narrower window.

## Prompt Requirements

### TIPS Basket System Prompt

The `tips_basket_agent` system prompt should extend the generic basket prompt
with TIPS-specific instructions:

```text
For TIPS selection, use computed ranking evidence as the primary selection
evidence. This basket exists to provide inflation-protected defensive exposure,
especially when macro allocation gives TIPS a positive target weight.

Choose the TIPS exposure that best protects purchasing power while controlling
drawdown and interest-rate sensitivity. Prefer short-duration TIPS exposure when
the evidence favors stable inflation defense and lower volatility. Use
full-curve TIPS exposure when it offers a better balance of inflation
protection, liquidity, and ranking evidence. Treat long-duration TIPS as a
higher-volatility real-rate position, not as the default safe choice. Select
long-duration TIPS only when ranking evidence and supporting context clearly
justify taking duration risk.

Use news only as secondary evidence when ranking evidence is close, conflicting,
incomplete, stale, or when long-duration TIPS looks unusually attractive. Do not
use generic inflation headlines alone to justify long-duration TIPS.
```

The final wording may be shorter in code, but it must preserve these ideas.

### TIPS Basket Task Prompt

The `tips_basket_agent` task prompt should extend the generic basket task prompt
with TIPS-specific instructions:

```text
For TIPS, use computed ranking evidence first. If rank evidence clearly favors
one defensive TIPS candidate, select it directly. Use news only when leading
candidates are close, conflicting, incomplete, stale, or when a long-duration
candidate requires confirmation.
```

The task prompt must continue requiring:

- one JSON object;
- `basket_id`;
- `target_weight`;
- `status`;
- `candidate_symbols`;
- `selected_symbol`;
- `reason_brief`;
- `candidate_symbols` copied exactly from assigned `basket_symbols`, not
  replaced by a shortlist.

## Tool Surface

`tips_basket_agent` should receive:

```text
market_load_history_tables_summary
market_last_price
alpaca_news
```

If Alpaca news credentials are missing, the existing unavailable-tool behavior
is acceptable. The agent must still be able to choose from ranking evidence
alone.

No other basket agents should be changed by this feature unless tests require a
small shared helper refactor.

## Expected Runtime Behavior

### Inactive TIPS Weight

If `target_weight` is zero, `tips_basket_agent` should:

1. avoid unnecessary research;
2. return `status: "inactive"`;
3. copy the full assigned `candidate_symbols`;
4. set `selected_symbol` to `null` or the existing strategy-compatible inactive
   value;
5. explain briefly that the macro allocation assigned zero TIPS weight.

### Active TIPS Weight

If `target_weight` is positive, `tips_basket_agent` should:

1. load the TIPS history summary for the full assigned TIPS universe;
2. compare rank/statistical evidence;
3. select directly if one candidate is clearly best for the defensive TIPS role;
4. call `alpaca_news` only when secondary context is warranted;
5. return a compact JSON report.

## Testing Requirements

Unit tests should verify:

1. `BASKET_UNIVERSES["tips"]` equals the approved six-symbol list.
2. `tips_basket_agent` tool permissions include `alpaca_news`.
3. `tips_basket_agent` still includes `market_load_history_tables_summary` and
   `market_last_price`.
4. Other basket tool permissions are not unintentionally changed.
5. The TIPS system prompt includes rank-first wording.
6. The TIPS system prompt describes the defensive inflation-protection role.
7. The TIPS system prompt says long-duration TIPS is not the default safe
   choice.
8. The TIPS prompt says news is secondary evidence.
9. The TIPS task prompt preserves exact `candidate_symbols` behavior.
10. Missing Alpaca news credentials do not remove the ability to create and run
    the TIPS agent with an unavailable-tool response.

Data/path verification should verify:

1. `VTIP`, `STIP`, `SCHP`, `TIP`, `SPIP`, and `LTPZ` can load daily
   Yahoo-style history for a representative 2024 backtest window.
2. The history summary tool can return model-facing rankings for the expanded
   TIPS basket without raw-data bloat.

Smoke verification should run at least one one-day benchmark:

```text
scripts/run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start <date> --end <date>
```

Acceptance requires:

1. strategy status is `passed`, or any failure is clearly unrelated to the TIPS
   basket changes;
2. `tips_basket_agent` appears in trace when the workflow runs;
3. the TIPS agent's available tools include `alpaca_news` when configured or an
   unavailable `alpaca_news` tool when credentials are missing;
4. if TIPS has positive target weight, the TIPS report contains a valid selected
   symbol from the approved universe;
5. replay UI can discover the run.

## Risks And Mitigations

| Risk | Mitigation |
|---|---|
| The model over-selects short-duration TIPS and never chooses `LTPZ`. | Prompt says short-duration is usually more stable, but allows long-duration when ranking evidence and context clearly support duration risk. |
| The model treats generic inflation headlines as enough reason to buy long-duration TIPS. | Prompt explicitly says generic inflation headlines alone do not justify long-duration TIPS. |
| News calls add noise or token usage. | News is secondary and conditional; rank evidence remains sufficient. |
| A ticker can resolve to an unrelated security despite looking relevant. | Keep the approved universe data-verified; remove any symbol that is not a TIPS ETF. |
| The change accidentally alters commodity/equity/nominal bond behavior. | Tests should assert only TIPS tool surface and TIPS prompt behavior changed. |
| Alpaca news credentials are absent. | Existing disabled-tool behavior remains acceptable; ranking-only selection must still work. |

## Out Of Scope Follow-Ups

Future work may add:

1. FRED real-yield evidence.
2. FRED breakeven-inflation evidence.
3. Treasury yield curve context.
4. Fed Funds / policy-rate context.
5. A dedicated TIPS duration-risk score.
6. A stricter rule that requires real-yield confirmation before selecting
   long-duration TIPS.
7. A nominal-bond basket expansion and prompt rewrite.

These should not be included in this feature.
