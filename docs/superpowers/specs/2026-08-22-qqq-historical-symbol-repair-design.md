# QQQ Historical Symbol Repair Design

## Purpose

Fix known bad ticker mappings in the QQQ historical constituent universe before
the symbols reach the equity agent.

The immediate goal is not to improve the trading strategy, optimize selection,
or rerun the full five-year benchmark. The goal is to make the QQQ historical
universe cleaner and prove with a small backtest that repaired symbols can be
loaded by the market summary tool.

## Background

The first weekly five-year `qqq-historical-equity-only-llm` run completed, but
the result underperformed the fixed-50 baseline and exposed symbol-quality
problems in the QQQ historical universe path.

Evidence from the run:

- Artifact root:
  `artifacts/ai_trading_team_example_benchmarks/20260822_100114_919015/qqq-historical-equity-only-llm`
- The backtest log contained more than `36,000` Yahoo data miss messages.
- Several QQQ historical holdings had symbols that look syntactically valid but
  are not valid Yahoo/LumiBot backtest tickers for this project.
- These bad symbols came from N-PORT/OpenFIGI normalization and entered the
  agent's `basket_symbols` list.

Known bad mappings confirmed during investigation:

| Bad Symbol | Holding Name | Desired Backtest Symbol | Rationale |
|---|---|---|---|
| `CPW` | Check Point Software Technologies Ltd. | `CHKP` | `CPW` had no Yahoo data; `CHKP` had daily data for the benchmark window. |
| `MRVLEUR` | Marvell Technology Group / Marvell Technology | `MRVL` | `MRVLEUR` had no Yahoo data; `MRVL` had daily data. |
| `TRI4EUR` | Thomson Reuters Corp. | `TRI` | `TRI4EUR` had no Yahoo data; `TRI` had US-listed daily data. |
| `STXN` | Seagate Technology Holdings PLC | `STX` | `STXN` had no Yahoo data; `STX` had daily data. |

`SHOP` is not part of this repair list. Manual checks showed `SHOP` has Yahoo
daily data for the benchmark window, so its repeated data-miss logs likely come
from a separate loading/cache/provider issue. This spec records that as a
follow-up, not as part of this fix.

## Goals

1. Add a small QQQ-specific symbol repair layer.
2. Repair the confirmed bad symbols before they reach the strategy universe.
3. Preserve raw and normalized QQQ snapshot files unchanged.
4. Record symbol repair metadata in the QQQ universe source context so trace/UI
   users can see what was changed.
5. Keep `AITradingTeamEquityOnlyLLMStrategy` unchanged.
6. Keep the QQQ historical strategy workflow unchanged except for the repaired
   universe.
7. Add unit tests proving the known bad symbols are repaired and deduplicated.
8. Run only a short smoke backtest that exercises a date window where repaired
   symbols can appear.
9. Verify the small backtest trace shows repaired symbols and no known bad
   symbols in `basket_symbols`.
10. Verify at least one repaired symbol can be loaded by
    `market_load_history_tables_summary` when present in the resolved universe.

## Non-Goals

This feature does not:

1. Rerun the full five-year QQQ historical benchmark.
2. Change ranking formulas.
3. Change equity agent prompts.
4. Change news usage.
5. Add all-market symbol discovery.
6. Add an automated generic Yahoo tradability scan for every symbol.
7. Fix the separate `SHOP` data miss issue.
8. Rewrite existing cached QQQ snapshot JSON files.
9. Claim that QQQ historical constituents should outperform fixed50 after this
   repair.

## Design

### Repair Location

Apply repairs after resolving a QQQ snapshot and before returning symbols to the
strategy.

Preferred location:

```text
resolve_qqq_snapshot(...)
  -> read normalized snapshot
  -> extract raw snapshot symbols
  -> apply QQQ symbol repairs
  -> deduplicate repaired symbols
  -> return AsOfSnapshotResolution
```

This avoids mutating source data and keeps the repaired universe consistent for
all QQQ historical strategy users.

### Alias Map

Create an explicit QQQ historical alias map:

```python
QQQ_SYMBOL_ALIASES = {
    "CPW": "CHKP",
    "MRVLEUR": "MRVL",
    "TRI4EUR": "TRI",
    "STXN": "STX",
}
```

The map should live near the QQQ universe code, not in the strategy class. The
reason is that these repairs are data-source normalization concerns, not trading
strategy decisions.

### Repair Rules

For each extracted snapshot symbol:

1. Normalize using the existing symbol normalization behavior.
2. If the normalized symbol exists in `QQQ_SYMBOL_ALIASES`, replace it with the
   mapped symbol.
3. Normalize the mapped value as well.
4. Drop empty values.
5. Deduplicate after repair while preserving first-seen order.

Example:

```text
["CPW", "CHKP", "MRVLEUR", "MRVL"]
  -> ["CHKP", "MRVL"]
```

### Metadata

Extend QQQ snapshot resolution metadata with repair details.

The strategy's `universe_source` context should include a compact section such
as:

```json
{
  "symbol_repair": {
    "applied": true,
    "raw_count": 99,
    "repaired_count": 4,
    "deduped_count": 2,
    "final_count": 97,
    "aliases": [
      {"from": "CPW", "to": "CHKP"},
      {"from": "MRVLEUR", "to": "MRVL"}
    ]
  }
}
```

The exact structure may differ if it fits existing dataclasses better, but the
trace must answer:

- how many raw symbols existed;
- which aliases were applied;
- how many symbols remained after repair and dedupe.

### Existing Snapshot Files

Do not rewrite existing files under the LumiBot cache, such as:

```text
C:\Users\Ran\AppData\Local\LumiWealth\lumibot\Cache\1.0\universe\qqq_nport\normalized
```

The source snapshots remain auditable. Repairs happen at runtime/resolution
time.

### Strategy Behavior

The QQQ historical strategy continues to:

```text
weekly scheduled run
  -> resolve QQQ historical universe
  -> equity_basket_agent selects one symbol
  -> deterministic planner creates execution_plan
  -> execution_agent executes the plan
```

The only intended behavioral change is:

```text
bad historical symbols are replaced before the agent receives basket_symbols
```

## Testing

### Unit Tests

Add or update tests in `tests/test_ai_trading_team_equity_only_llm.py` and/or a
QQQ universe-specific test file.

Required coverage:

1. `CPW` becomes `CHKP`.
2. `MRVLEUR` becomes `MRVL`.
3. `TRI4EUR` becomes `TRI`.
4. `STXN` becomes `STX`.
5. Alias replacement happens before deduplication.
6. The original fixed-50 `AITradingTeamEquityOnlyLLMStrategy` universe is
   unchanged.
7. `AITradingTeamQQQHistoricalEquityOnlyLLMStrategy` receives repaired
   `basket_symbols` in agent context.
8. The QQQ `universe_source` context includes symbol repair metadata.

### Smoke Backtest

Run a short QQQ historical backtest that covers at least one snapshot containing
one or more known bad symbols.

Suggested short windows:

- `2021-03-01` to `2021-03-08` for `MRVLEUR -> MRVL` if data and snapshot
  availability line up.
- `2026-03-02` to `2026-03-09` for `TRI4EUR -> TRI` and `STXN -> STX`.

Use a short run only:

```powershell
$env:AI_TRADING_TEAM_MODEL='openai/gpt-5.6-luna'
python scripts\run_ai_trading_team_examples_benchmark.py `
  --strategy qqq-historical-equity-only-llm `
  --start 2026-03-02 `
  --end 2026-03-09 `
  --run-frequency weekly `
  --weekly-run-weekday MON `
  --max-workers 1 `
  --max-run-attempts 1 `
  --agent-run-timeout-seconds 900 `
  --env-file D:\Lumibot\project_notes\API.txt
```

### Smoke Verification

After the smoke backtest, inspect trace files under:

```text
<artifact>\cache\agent_runtime\traces\equity_basket_agent
```

Verify:

1. `basket_symbols` does not contain `CPW`, `MRVLEUR`, `TRI4EUR`, or `STXN`.
2. `basket_symbols` contains expected repairs when their raw symbols were in the
   selected snapshot, such as `CHKP`, `MRVL`, `TRI`, or `STX`.
3. `universe_source.symbol_repair` records the applied repairs.
4. `market_load_history_tables_summary` receives repaired symbols.
5. The backtest finishes without agent warnings or execution-plan blockers.

The smoke backtest does not need to outperform any benchmark. Passing means the
symbol repair path works.

## Risks

| Risk | Mitigation |
|---|---|
| Alias map becomes stale or incomplete | Keep it explicit, trace-visible, and easy to extend. |
| Wrong alias maps a foreign listing to the wrong US ticker | Use holding name and data availability evidence before adding aliases. |
| Runtime repairs hide source-data problems | Preserve raw snapshots and record repair metadata in trace. |
| This fix is mistaken for performance validation | Do not rerun or interpret the five-year benchmark in this feature. |
| `SHOP` remains noisy | Track it as a separate follow-up because it is not an alias failure. |

## Acceptance Criteria

1. Known bad QQQ symbols are repaired before reaching `basket_symbols`.
2. Repaired symbols are deduplicated while preserving order.
3. Repair metadata is visible in strategy context/trace.
4. Fixed-50 strategy tests remain unchanged and passing.
5. A short QQQ historical backtest completes.
6. The short backtest trace shows repaired symbols can reach
   `market_load_history_tables_summary`.
7. No full five-year benchmark rerun is required for this feature.

## Follow-Up Work

1. Investigate why `SHOP` appeared frequently in Yahoo miss logs despite having
   Yahoo daily data in manual checks.
2. Consider a generic tradability/data-coverage filter after this targeted
   repair is validated.
3. Rerun the full five-year QQQ historical benchmark only after symbol repair
   and any additional high-priority data-quality fixes are complete.
