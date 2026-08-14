# Nominal Bond Basket Universe And Duration Prompt Design

## Summary

This spec defines a focused enhancement of the nominal bond basket in the mock
Growth / Inflation quadrant strategy.

The nominal bond basket should not be treated as a generic "safe bond" bucket.
It should be treated as a U.S. nominal Treasury duration-selection basket. The
agent's core job is to choose one Treasury ETF from the assigned basket based on
computed ranking evidence and the maturity / duration exposure that the selected
ETF represents.

This feature expands the nominal bond universe from five symbols to a broader
set of Treasury ETFs covering cash-like, short, intermediate, broad-curve, long,
and extended-duration Treasury exposure. It also adds nominal-bond-specific
system and task prompts that avoid unsafe shortcuts such as always choosing the
lowest-volatility asset or always choosing long-duration bonds in weak-growth
regimes.

## Current Context

The strategy currently lives in:

```text
lumibot/example_strategies/ai_trading_team_mock_growth_inflation_quadrant.py
```

The current nominal bond basket is:

```python
"nominal_bond": ["SHY", "IEF", "TLT", "GOVT", "VGIT"]
```

The current basket-agent prompt system has dedicated branches for commodity and
TIPS, but no dedicated branch for nominal bonds. As a result,
`nominal_bond_basket_agent` currently receives only the generic basket prompt:

```text
stay inside the assigned basket
select one symbol when active
return basket_id, selected_symbol, status, and reason_brief
do not place orders
```

That is not enough for nominal bonds because the main decision is not merely
"which bond ETF has the best recent score." The main decision is which part of
the U.S. Treasury curve to own:

```text
cash-like / ultra-short
short-term
intermediate-term
broad-curve
long-term
extended-duration / zero-coupon
```

## Quadrant Role

The current four-quadrant allocation map gives nominal bonds their largest
weight when growth is down and inflation is down:

| Regime | Equity | Commodity | TIPS | Nominal Bond |
|---|---:|---:|---:|---:|
| `growth_up_inflation_down` | 0.50 | 0.25 | 0.00 | 0.25 |
| `growth_up_inflation_up` | 0.25 | 0.50 | 0.25 | 0.00 |
| `growth_down_inflation_up` | 0.00 | 0.25 | 0.50 | 0.25 |
| `growth_down_inflation_down` | 0.25 | 0.25 | 0.00 | 0.50 |

This feature does not change the quadrant classifier or the 50/25/25/0 weight
map. The goal is narrower: when the nominal bond basket receives a positive
weight, the basket agent should choose among Treasury duration exposures more
intelligently.

## Goals

1. Expand `BASKET_UNIVERSES["nominal_bond"]` to the approved 16-symbol list.
2. Keep the basket pure to U.S. nominal Treasury exposure.
3. Add neutral maturity / duration metadata for each symbol.
4. Add a nominal-bond-specific system prompt branch.
5. Add a nominal-bond-specific task prompt branch.
6. Keep computed ranking evidence as the primary evidence.
7. Do not add news tools for nominal bonds in this feature.
8. Do not add FRED yield, yield-curve, Fed Funds, or rate-expectation tools in
   this feature.
9. Verify that the expanded symbols can load daily history through the current
   Yahoo-style backtest data path.
10. Verify with at least one one-day mock Growth / Inflation quadrant backtest
    that the expanded nominal bond basket does not break macro allocation,
    basket reports, portfolio decision, execution-plan generation, execution,
    trace capture, or replay UI discovery.

## Non-Goals

This feature does not:

1. Redesign the macro quadrant classifier.
2. Change any four-quadrant target weights.
3. Add corporate bonds, high-yield bonds, mortgage bonds, municipal bonds, or
   international bonds.
4. Add credit-risk selection.
5. Add FRED yield tools or rate-curve tools.
6. Add `alpaca_news` to `nominal_bond_basket_agent`.
7. Add a dedicated duration-risk scoring tool.
8. Optimize the nominal bond basket based on historical performance.
9. Make any performance claim about nominal bonds or about this four-quadrant
   strategy.
10. Force the agent to prefer short duration, long duration, or cash-like
    exposure by default.

## Approved Nominal Bond Universe

`BASKET_UNIVERSES["nominal_bond"]` must become exactly:

```python
[
    "SGOV",
    "BIL",
    "SHV",
    "SHY",
    "VGSH",
    "SCHO",
    "IEI",
    "IEF",
    "VGIT",
    "SCHR",
    "GOVT",
    "TLH",
    "TLT",
    "VGLT",
    "EDV",
    "ZROZ",
]
```

The ordering is a stable configuration order, not a preference ranking.

## Neutral Exposure Metadata

The model-facing prompt should include neutral maturity / duration metadata for
the assigned symbols. This metadata describes the type of Treasury exposure; it
must not label one type as "best", "safest", or "default".

| Symbol Group | Symbols | Neutral Exposure Description |
|---|---|---|
| Cash-like / ultra-short Treasury | `SGOV`, `BIL`, `SHV` | Treasury bill or ultra-short Treasury exposure with low interest-rate sensitivity. |
| Short-term Treasury | `SHY`, `VGSH`, `SCHO` | Short-term Treasury exposure, roughly 1-3 year maturity range. |
| Intermediate Treasury | `IEI`, `IEF`, `VGIT`, `SCHR` | Intermediate Treasury exposure, roughly 3-10 year maturity range depending on fund. |
| Broad Treasury curve | `GOVT` | Broad U.S. Treasury exposure across much of the maturity curve. |
| Long Treasury | `TLH`, `TLT`, `VGLT` | Long-duration Treasury exposure with high interest-rate sensitivity. |
| Extended-duration / zero-coupon Treasury | `EDV`, `ZROZ` | Very high-duration Treasury exposure with very high interest-rate sensitivity. |

The prompt may explain that duration sensitivity means:

```text
longer-duration Treasury ETFs usually react more strongly to interest-rate
changes than shorter-duration Treasury ETFs.
```

The prompt must not imply:

```text
short duration is always correct
long duration is always correct in weak growth
cash-like exposure is the safest default
extended duration is the preferred recession trade
```

## Evidence Policy

### Primary Evidence

The primary evidence remains the existing computed price/statistical ranking
output from:

```text
market_load_history_tables_summary
```

The nominal bond agent should first compare:

1. composite ranking evidence;
2. momentum ranking evidence;
3. drawdown and volatility evidence;
4. moving-average or trend evidence;
5. whether leading candidates are clearly separated or close.

### Secondary Interpretation

After reading ranking evidence, the agent should interpret the selected symbol
through maturity / duration exposure:

```text
Why this duration exposure, not merely why this ticker?
```

For example:

```text
Selected IEF because the ranking evidence favors intermediate Treasury exposure,
and intermediate duration is a balanced choice relative to both cash-like and
long-duration alternatives.
```

or:

```text
Selected TLT because ranking evidence strongly favors long Treasury exposure,
and the agent accepts higher interest-rate sensitivity based on that evidence.
```

### No News In This Feature

`nominal_bond_basket_agent` should not receive `alpaca_news` in this feature.
Bond duration choice should first be improved through ranking evidence and clear
duration framing. News may be evaluated later if traces show that the agent needs
qualitative rate-context support.

### No FRED In This Feature

The agent should not receive FRED tools in this feature.

FRED rate and yield-curve evidence is a good future improvement, but adding it
now would mix two changes:

1. expanding the basket and fixing prompt framing;
2. adding a new macro/rates evidence layer.

Those should remain separate so benchmark traces can show which change affects
behavior.

## Tool Surface

`nominal_bond_basket_agent` should keep the same tool surface as the generic
non-news basket agents:

```text
market_load_history_tables_summary
market_last_price
```

It should not receive:

```text
alpaca_news
list_fred_series
get_fred_series
get_fred_latest
get_fred_snapshot
orders_submit_order
orders_execute_order
orders_confirm_order
```

The nominal bond agent remains a research/selection agent, not an execution
agent.

## Prompt Design

### Prompt Cleanup Principles

The implementation should remove or avoid any nominal-bond wording that implies:

1. nominal bonds are simply "safe assets";
2. the agent should prefer the lowest-volatility option by default;
3. the agent should prefer the longest-duration option by default;
4. weak growth automatically means long bonds;
5. capital preservation overrides ranking evidence;
6. news is required before selecting a Treasury ETF;
7. the agent should classify the macro regime itself.

### Nominal Bond System Prompt

The nominal-bond-specific system prompt should extend the generic basket prompt
with wording equivalent to:

```text
For nominal bond selection, use computed ranking evidence as the primary
selection evidence. This basket is a U.S. nominal Treasury duration-selection
basket, not a corporate-bond or credit-risk basket.

The main decision is maturity / duration exposure: cash-like, short-term,
intermediate-term, broad-curve, long-term, or extended-duration Treasury
exposure.

Use the maturity / duration metadata only to understand what each symbol
represents. Do not select the lowest-volatility symbol by default. Do not select
the longest-duration symbol by default.

When target_weight is positive, choose the Treasury exposure that best matches
the ranking evidence. Cash-like or short-term exposure may be appropriate when
ranking evidence favors low interest-rate sensitivity. Intermediate or
broad-curve exposure may be appropriate when ranking evidence is balanced.
Long or extended-duration exposure should be selected only when ranking evidence
clearly justifies taking high interest-rate sensitivity.

Treat long-duration and zero-coupon Treasury ETFs as high-volatility
rate-sensitive positions, not as default safe assets.
```

The final implementation may adapt wording for brevity, but the required
meanings above must remain intact.

### Nominal Bond Task Prompt

The nominal-bond-specific task prompt should extend the generic basket task with
wording equivalent to:

```text
For nominal bonds, use computed ranking evidence first. Select one symbol from
candidate_symbols when target_weight is positive. Explain the selected symbol in
terms of ranking evidence, maturity / duration exposure, and why that duration
choice fits the current nominal bond basket role.
```

If `target_weight` is zero, the agent should return inactive and should not
select a symbol.

## Expected Agent Behavior

### Inactive Nominal Bond Weight

If `target_weight` is zero, `nominal_bond_basket_agent` should:

1. avoid loading unnecessary history if the workflow already provides enough
   context to know the basket is inactive;
2. return `status = "inactive"`;
3. set `selected_symbol = null` or equivalent;
4. preserve `candidate_symbols` exactly from assigned `basket_symbols`;
5. explain briefly that the macro allocation assigned zero nominal bond weight.

### Active Nominal Bond Weight

If `target_weight` is positive, `nominal_bond_basket_agent` should:

1. load the history summary for the full assigned nominal bond universe;
2. inspect ranking output rather than writing ad hoc DuckDB SQL by default;
3. compare candidates using ranking evidence;
4. interpret the selected symbol's maturity / duration exposure;
5. select directly if one candidate is clearly best for the nominal bond role;
6. avoid unsupported claims about future interest rates, recession, or Fed
   action unless those claims are present in available evidence;
7. return one selected symbol and a concise `reason_brief`.

## Tests

Unit tests should verify:

1. `BASKET_UNIVERSES["nominal_bond"]` equals the approved 16-symbol list.
2. `nominal_bond_basket_agent` receives `market_load_history_tables_summary`.
3. `nominal_bond_basket_agent` receives `market_last_price`.
4. `nominal_bond_basket_agent` does not receive `alpaca_news`.
5. `nominal_bond_basket_agent` does not receive trading tools.
6. The nominal bond system prompt includes rank-first wording.
7. The nominal bond system prompt describes Treasury duration selection.
8. The nominal bond system prompt says long-duration and zero-coupon Treasury
   ETFs are not default safe assets.
9. The nominal bond system prompt says the lowest-volatility symbol should not
   be selected by default.
10. The nominal bond task prompt asks for ranking evidence, duration exposure,
    and fit to the nominal bond basket role.
11. The nominal bond task prompt preserves exact `candidate_symbols` behavior.
12. Existing commodity, TIPS, and equity prompt tests continue to pass.

Data/path verification should verify:

1. All approved nominal bond symbols can load daily Yahoo-style history for a
   representative 2024 backtest window.
2. The history summary tool can return model-facing rankings for the expanded
   nominal bond basket without raw-data bloat.

Benchmark verification should run:

```text
scripts/run_ai_trading_team_examples_benchmark.py --strategy mock-growth-inflation-quadrant --start <date> --end <date>
```

Acceptance requires:

1. strategy status is `passed`, or any failure is clearly unrelated to the
   nominal bond universe/prompt changes;
2. `nominal_bond_basket_agent` appears in the trace;
3. the nominal bond candidate symbols appear in agent input/context or tool
   results;
4. if nominal bond has positive target weight, the nominal bond report contains
   a valid selected symbol from the approved universe;
5. replay UI discovery still shows the run.

## Risks And Mitigations

| Risk | Mitigation |
|---|---|
| The model over-selects cash-like Treasury ETFs. | Prompt says not to select the lowest-volatility symbol by default. |
| The model over-selects long or extended-duration ETFs. | Prompt says long and zero-coupon Treasury ETFs are high-volatility rate-sensitive positions, not default safe assets. |
| The model treats weak growth as automatic long-duration evidence. | Prompt requires ranking evidence and forbids unsupported rate/recession claims. |
| The expanded universe adds too many near-duplicates. | Universe is capped at 16 symbols across distinct Treasury maturity buckets. |
| The agent asks for news or FRED context it does not have. | Tool surface remains history/price only; prompt does not require news or FRED. |
| A ticker resolves to an unrelated security. | Implementation must run data-path verification and remove any symbol that is not a U.S. nominal Treasury ETF. |
| The change accidentally alters other baskets. | Tests should assert nominal-bond-specific behavior and keep existing basket prompt/tool tests green. |

## Future Work

Deferred improvements:

1. FRED Treasury yield evidence such as `DGS2`, `DGS10`, `DGS30`, `T10Y2Y`,
   and `FEDFUNDS`.
2. A dedicated duration-risk score or yield-curve interpretation tool.
3. A rule-backed nominal bond duration selector that reduces LLM discretion.
4. Conditional news or rate-commentary access if traces show ranking evidence is
   insufficient.
5. Separate testing of the macro quadrant classifier's lag problem.
6. Separate testing of whether the 50% nominal bond weight should be adjusted in
   the growth-down / inflation-down quadrant.

