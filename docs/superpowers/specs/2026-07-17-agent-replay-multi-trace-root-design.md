# Agent Replay Multi Trace Root Design

## Purpose

Agent Replay UI should show all locally available replayable backtest traces by default. The user should not need to know where a specific benchmark artifact stored its `agent_runtime` traces or restart the UI with a different `--trace-root` for each run.

## Problem

`scripts/agent_trace_ui.py` currently serves one trace root. By default it reads:

```text
D:\Lumibot\.lumibot\agent_runtime
```

Benchmark runs store traces under paths like:

```text
D:\Lumibot\artifacts\ai_trading_team_example_benchmarks\20260717_185016_461584\growth-execution-test\cache\agent_runtime
```

The UI therefore opens successfully but shows whichever single root was selected, often an older local trace instead of the latest benchmark run.

## Goals

- Keep the simple command:

```powershell
cd D:\Lumibot
python scripts\agent_trace_ui.py
```

- By default, load traces from:
  - project-local `.lumibot/agent_runtime`
  - benchmark artifact trace roots under `artifacts/ai_trading_team_example_benchmarks/*/*/cache/agent_runtime`
- Merge those roots into one replay dataset.
- Let the existing UI selectors choose `Strategy`, `Backtest Run`, and `System Run` across all discovered roots.
- Give benchmark runs useful labels such as:

```text
20260717_185016_461584 / growth-execution-test
```

- Preserve `--trace-root` for focused/debug usage.

## Non-Goals

- Do not redesign the browser UI controls.
- Do not copy trace files into `.lumibot/agent_runtime`.
- Do not add caching or database indexing yet.
- Do not scan unrelated directories outside the repo.
- Do not read non-trace files such as API key notes.

## Design

Add multi-root support at the loader boundary.

Current:

```text
single trace_root -> build_replay_dataset(trace_root) -> ReplayDataset
```

New:

```text
list of trace_roots -> build_replay_dataset_from_roots(trace_roots) -> merged ReplayDataset
```

`build_replay_dataset(trace_root)` remains for compatibility and delegates internally to the multi-root path for one root.

The script discovers default roots:

```text
REPO_ROOT\.lumibot\agent_runtime
REPO_ROOT\artifacts\ai_trading_team_example_benchmarks\*\*\cache\agent_runtime
```

Only paths containing `traces/` are useful, but missing/empty roots may be passed safely and produce no runs.

## Labeling

For benchmark artifact roots matching:

```text
artifacts/ai_trading_team_example_benchmarks/<artifact_id>/<strategy_key>/cache/agent_runtime
```

the backtest run label should be:

```text
<artifact_id> / <strategy_key>
```

For local project traces, keep the existing fallback label:

```text
agent_runtime
```

Run ids must remain unique across roots. Existing hash-based fallback ids are acceptable.

## Error Handling

- Parse errors from individual traces continue to appear in dataset warnings.
- Missing roots are ignored.
- The dataset source path should describe multiple roots in a readable way.
- `--trace-root` should bypass auto-discovery and load only the explicitly requested root.

## Validation

Automated tests should verify:

- Multiple trace roots merge into one dataset.
- The merged dataset can contain multiple strategies.
- Benchmark artifact roots get readable labels.
- Explicit `--trace-root` still loads one selected root.
- Discovery does not require reading unrelated files.

Manual validation should run:

```powershell
cd D:\Lumibot
python scripts\agent_trace_ui.py
```

and confirm that the dropdown includes both older local traces and benchmark traces such as `AITradingTeamGrowthExecutionTestStrategy`.

