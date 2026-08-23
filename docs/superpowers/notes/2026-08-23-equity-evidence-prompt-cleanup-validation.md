# Equity Evidence Prompt Cleanup Validation

## Date

2026-08-23

## Branch

feature/qqq-historical-constituent-universe

## Focused Tests

```text
python -m pytest tests/test_ai_trading_team_equity_only_llm.py tests/test_agent_manager.py -q
```

Result: PASS

Observed result from implementation and review agents:

```text
70 passed, 1 warning
```

## Smoke Backtest

```text
python scripts\run_ai_trading_team_examples_benchmark.py --strategy qqq-historical-equity-only-llm --start 2024-09-05 --end 2024-09-06 --run-frequency weekly --weekly-run-weekday THU --env-file D:\Lumibot\project_notes\API.txt --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 240
```

Model:

```text
gpt-5.6-luna
```

Artifact:

```text
C:\Users\Ran\.config\superpowers\worktrees\lumibot\optimize-equity-only-llm\artifacts\ai_trading_team_example_benchmarks\20260823_103103_923275\qqq-historical-equity-only-llm
```

Benchmark status:

```text
passed
```

## Prompt Checks

- Evidence Interpretation Policy present: yes
- Momentum and trend quality marked as primary evidence: yes
- Risk-adjusted momentum described as a check against pure volatility-driven strength: yes
- Breakout / near-high described as timing and leadership confirmation: yes
- Volume described as supporting evidence only: yes
- News remains conditional and candidate-limited: yes
- Old `leading group` wording absent from prompts: yes
- Old `composite_score` wording absent from prompts: yes

## Workflow Checks

- Equity agent called `market_load_history_tables_summary`: yes
- `top_n=10`: yes
- `candidate_summary_limit=25`: yes
- Equity agent selected exactly five symbols: yes
- Selected symbols: `CTAS`, `KDP`, `FTNT`, `ISRG`, `ADBE`
- Workflow reached deterministic planning and execution: yes
- Execution summary reported all five planned market buy orders filled and confirmed: yes

## Notes

This smoke test validates prompt wiring and workflow continuity only. It is not a performance claim.

The run emitted an Alpaca websocket timeout log during startup, but the benchmark still completed with status `passed`; this appears to be stream connection noise rather than a blocker for this prompt cleanup feature.
