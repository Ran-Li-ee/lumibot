# Equity-Only LLM Baseline Archive and Cleanup Design

## Purpose

Preserve the current equity-only LLM strategy as a milestone baseline before continuing development.

The immediate goal is not to improve strategy performance. The goal is to make the current working version traceable, reproducible, and safe to build from.

This baseline matters because the latest five-year weekly backtest showed a material improvement profile:

```text
Strategy: qqq-historical-equity-only-llm
Window: 2021-08-16 to 2026-08-14
Cadence: weekly
Model: openai/gpt-5.6-luna
Total return: 471.98%
CAGR: 41.82%
Max drawdown: 19.17%
Sharpe: 1.53
Volatility: 24.86%
ROMAD: 2.181
Status: passed
```

The user considers this a milestone because the strategy roughly kept pace with SPY before 2026 and clearly separated above SPY after September 2025.

## Background

The current branch contains a long sequence of equity-only LLM strategy work, including:

1. QQQ historical constituent universe support.
2. Symbol normalization and repair for historical constituents.
3. Weekly strategy cadence.
4. Dynamic equity portfolio construction.
5. Order execution and confirmation improvements.
6. Momentum-stage evidence ranking.
7. Prompt cleanup for the equity-only workflow.
8. Backtest trace and replay UI improvements from earlier work.

The current version still has known imperfections and at least one observed warning, but it is good enough to serve as a stable comparison point for later strategy optimization.

## Design Principle

Archive first, clean second.

The exact code state that produced the milestone backtest is valuable. Cleanup should not happen before that state is preserved, because even harmless-looking cleanup can change behavior or make later reproduction harder.

The preferred workflow is:

```text
1. Audit current git state.
2. Preserve the current reproducible baseline.
3. Record the baseline result in a human-readable note.
4. Tag the baseline commit.
5. Perform small cleanup separately.
6. Verify the cleaned version still runs.
7. Push the branch and tag to GitHub.
```

## Scope

This task includes:

1. Inspect the current branch, commit history, and working tree state.
2. Identify files that should be committed as part of the baseline.
3. Identify files that must not be committed, especially API keys and oversized generated artifacts.
4. Create a baseline note under `docs/superpowers/notes/`.
5. Record the exact benchmark command, model, date window, artifact paths, metrics, and known warnings.
6. Commit the current baseline state if there are relevant uncommitted changes.
7. Create an immutable git tag for the baseline commit.
8. Do limited cleanup only after the raw baseline has been preserved.
9. Run focused verification after cleanup.
10. Push the branch and baseline tag to GitHub when safe.

## Non-Goals

This task does not:

1. Change trading strategy logic.
2. Change prompts for performance improvement.
3. Add or remove tools.
4. Change benchmark windows or model selection.
5. Re-run the full five-year backtest unless explicitly requested.
6. Merge this branch into `dev`.
7. Submit a pull request to the upstream Lumibot repository.
8. Commit secrets from `project_notes/API.txt`.
9. Commit large trace or artifact folders directly to git unless explicitly approved.

## Baseline Artifacts

The milestone run to preserve is:

```text
Run directory:
C:/Users/Ran/.config/superpowers/worktrees/lumibot/optimize-equity-only-llm/artifacts/ai_trading_team_example_benchmarks/20260827_181522_001118/qqq-historical-equity-only-llm

Summary file:
C:/Users/Ran/.config/superpowers/worktrees/lumibot/optimize-equity-only-llm/artifacts/ai_trading_team_example_benchmarks/20260827_181522_001118/summary.json

Account curve:
C:/Users/Ran/.config/superpowers/worktrees/lumibot/optimize-equity-only-llm/artifacts/ai_trading_team_example_benchmarks/20260827_181522_001118/qqq-historical-equity-only-llm/qqq_historical_equity_only_llm_account_curve.html

Tearsheet:
C:/Users/Ran/.config/superpowers/worktrees/lumibot/optimize-equity-only-llm/artifacts/ai_trading_team_example_benchmarks/20260827_181522_001118/qqq-historical-equity-only-llm/qqq_historical_equity_only_llm_tearsheet.html
```

The full artifact directory should remain local by default. Git should store a lightweight baseline note and small summary data only, unless a later decision is made to use Git LFS, a GitHub release attachment, or an external archive.

## Baseline Note

Create:

```text
docs/superpowers/notes/2026-08-28-qqq-historical-equity-only-llm-weekly-5y-baseline.md
```

The note should include:

1. Strategy name.
2. Branch name.
3. Commit hash.
4. Git tag name.
5. Model name.
6. Backtest command.
7. Backtest date window.
8. Run frequency and weekday.
9. Key metrics.
10. Local artifact paths.
11. Trace health summary.
12. Known warnings and anomalies.
13. Interpretation as the current optimization baseline.
14. Explicit limitations so later comparisons do not overstate the result.

## Proposed Git Names

Use a readable baseline tag:

```text
baseline/equity-only-qqq-weekly-5y-20260828
```

If a branch-level archive is also desired, use:

```text
archive/equity-only-qqq-weekly-5y-20260828
```

The tag should point to the exact commit chosen as the reproducible baseline. Once pushed, it should not be moved unless the user explicitly requests rewriting the archive.

## Cleanup Rules

Cleanup should be conservative and should happen after the baseline commit/tag.

Allowed cleanup:

1. Remove temporary debug files that are not part of the strategy.
2. Ensure generated caches and large artifacts are ignored unless intentionally tracked.
3. Improve small documentation wording around how to reproduce the benchmark.
4. Confirm API key files are ignored and not staged.
5. Confirm runner names and strategy names are understandable.

Avoid during this cleanup:

1. Refactoring strategy logic.
2. Changing prompts.
3. Changing rank calculations.
4. Changing execution behavior.
5. Changing benchmark runner behavior.
6. Moving large directories unless necessary.

## Secret and Artifact Safety

Before committing, inspect:

```text
project_notes/API.txt
.env
.env.local
.secrets
artifacts/
logs/
```

Required outcome:

1. API keys are not staged.
2. Secrets are not committed.
3. Large generated artifacts are not committed accidentally.
4. If a small summary file is committed, it must not contain credentials.

## Verification

Before considering the archive complete:

1. Run the focused tests relevant to the changed files.
2. Run formatter/lint checks if they were part of the branch's normal workflow.
3. Confirm the baseline note points to existing local artifact paths.
4. Confirm the tag points to the intended commit.
5. Confirm the branch can still launch the replay UI.

Full five-year backtest verification is not required for this archive step because the milestone run has already completed. A short smoke backtest may be run after cleanup if code changes occur after the baseline tag.

## Acceptance Criteria

This task is complete when:

1. Current git state has been audited.
2. A baseline note exists under `docs/superpowers/notes/`.
3. Relevant code and docs are committed.
4. Secrets and large accidental artifacts are excluded.
5. A baseline git tag exists locally.
6. The baseline tag and working branch are pushed to GitHub.
7. Any cleanup is recorded separately from the raw baseline when practical.
8. The user can clearly identify which commit represents the milestone version.

## Recommended Next Step

After this spec is approved, write an implementation plan that performs the archive in two phases:

```text
Phase 1: Audit and freeze the exact milestone baseline.
Phase 2: Conservative cleanup and verification.
```

No merge to `dev` should happen as part of this task unless the user separately asks for it.
