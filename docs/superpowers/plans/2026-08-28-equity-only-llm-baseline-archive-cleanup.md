# Equity-Only LLM Baseline Archive and Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Archive the current QQQ historical equity-only LLM strategy as a reproducible five-year weekly baseline, then perform conservative cleanup without changing strategy behavior.

**Architecture:** Preserve the exact behavior first by auditing the branch and creating an immutable baseline tag. Record the milestone run in a lightweight markdown note, keep large generated artifacts local, then run focused verification and push the branch/tag to GitHub.

**Tech Stack:** Git, PowerShell, Python 3, pytest, ruff, LumiBot benchmark artifacts, local markdown documentation.

---

## Safety Notes

- Work only in `C:\Users\Ran\.config\superpowers\worktrees\lumibot\optimize-equity-only-llm`.
- Current branch at plan-writing time: `feature/qqq-historical-constituent-universe`.
- Do not merge this branch into `dev`.
- Do not submit a PR to upstream `Lumiwealth/lumibot`.
- Do not commit `project_notes/API.txt`.
- Do not commit `artifacts/`, `logs/`, `.env`, `.env.local`, `.secrets/lumi_secrets.env`, or generated trace directories.
- Do not re-run the full five-year backtest during this archive task.
- If an existing tag named `baseline/equity-only-qqq-weekly-5y-20260828` is found, stop and ask the user before doing anything with that tag.
- If any command prints a real API key, stop, clear the visible output from follow-up summaries, and only report that a secret exposure risk was detected.

---

## File Structure

Create:

- `docs/superpowers/notes/2026-08-28-qqq-historical-equity-only-llm-weekly-5y-baseline.md`
  - Human-readable record of the milestone benchmark.

Do not create:

- New strategy files.
- New tests.
- New artifact copies.
- New API key files.

Modify only if verification shows a real issue:

- `.gitignore`
  - Only add missing ignores for local secret or artifact files if they are not already ignored.

Git refs to create:

- Tag: `baseline/equity-only-qqq-weekly-5y-20260828`
- Optional archive branch: `archive/equity-only-qqq-weekly-5y-20260828`

---

## Baseline Run To Record

Use this completed benchmark as the canonical run:

```text
Strategy: qqq-historical-equity-only-llm
Window: 2021-08-16 to 2026-08-14
Cadence: weekly
Weekly run weekday: MON
Model: openai/gpt-5.6-luna
Status: passed
Total return: 471.98%
CAGR: 41.82%
Max drawdown: 19.17%
Sharpe: 1.53
Volatility: 24.86%
ROMAD: 2.181
Artifact root: artifacts/ai_trading_team_example_benchmarks/20260827_181522_001118
```

Canonical command:

```powershell
cd C:\Users\Ran\.config\superpowers\worktrees\lumibot\optimize-equity-only-llm
$env:AI_TRADING_TEAM_MODEL='openai/gpt-5.6-luna'

python scripts\run_ai_trading_team_examples_benchmark.py `
  --strategy qqq-historical-equity-only-llm `
  --start 2021-08-16 `
  --end 2026-08-14 `
  --run-frequency weekly `
  --weekly-run-weekday MON `
  --max-workers 1 `
  --max-run-attempts 1 `
  --agent-run-timeout-seconds 900 `
  --env-file D:\Lumibot\project_notes\API.txt
```

---

## Task 1: Audit Current Git State

**Files:**

- Read only: repository metadata and ignored-file state.

- [ ] **Step 1: Confirm branch and working tree**

Run:

```powershell
cd C:\Users\Ran\.config\superpowers\worktrees\lumibot\optimize-equity-only-llm
git branch --show-current
git status --short --branch
git log --oneline -8
```

Expected:

```text
feature/qqq-historical-constituent-universe
```

`git status --short --branch` may show only this implementation plan if it has not yet been committed. It must not show staged or unstaged API key files.

- [ ] **Step 2: Confirm remotes**

Run:

```powershell
git remote -v
```

Expected:

```text
origin points to the user's fork or writable GitHub remote.
upstream may point to Lumiwealth/lumibot if configured.
```

If `origin` points to `Lumiwealth/lumibot`, stop and ask the user before pushing.

- [ ] **Step 3: Confirm the milestone artifact exists**

Run:

```powershell
$runRoot = "artifacts\ai_trading_team_example_benchmarks\20260827_181522_001118"
$strategyRoot = Join-Path $runRoot "qqq-historical-equity-only-llm"
$paths = @(
  (Join-Path $runRoot "summary.json"),
  (Join-Path $strategyRoot "qqq_historical_equity_only_llm_account_curve.html"),
  (Join-Path $strategyRoot "qqq_historical_equity_only_llm_tearsheet.html"),
  (Join-Path $strategyRoot "qqq_historical_equity_only_llm_tearsheet_metrics.json"),
  (Join-Path $strategyRoot "trades.csv"),
  (Join-Path $strategyRoot "stats.csv"),
  (Join-Path $strategyRoot "backtest.log")
)
$paths | ForEach-Object { [PSCustomObject]@{ Path = $_; Exists = Test-Path $_ } } | Format-Table -AutoSize
```

Expected: every listed path has `Exists` equal to `True`.

- [ ] **Step 4: Inspect benchmark summary**

Run:

```powershell
$summary = Get-Content -Raw "artifacts\ai_trading_team_example_benchmarks\20260827_181522_001118\summary.json" | ConvertFrom-Json
$result = $summary.results[0]
[PSCustomObject]@{
  Strategy = $result.strategy
  Status = $result.status
  Start = $result.window.start
  End = $result.window.end
  TotalReturn = "{0:P2}" -f $result.backtest_result.total_return
  CAGR = "{0:P2}" -f $result.backtest_result.cagr
  MaxDrawdown = "{0:P2}" -f $result.backtest_result.max_drawdown.drawdown
  Sharpe = "{0:N2}" -f $result.backtest_result.sharpe
  Volatility = "{0:P2}" -f $result.backtest_result.volatility
  ROMAD = "{0:N3}" -f $result.backtest_result.romad
} | Format-List
```

Expected:

```text
Strategy    : qqq-historical-equity-only-llm
Status      : passed
Start       : 2021-08-16
End         : 2026-08-14
TotalReturn : 471.98%
CAGR        : 41.82%
MaxDrawdown : 19.17%
Sharpe      : 1.53
Volatility  : 24.86%
ROMAD       : 2.181
```

---

## Task 2: Check Secret and Artifact Safety

**Files:**

- Read only unless `.gitignore` lacks required ignore rules.

- [ ] **Step 1: Confirm sensitive files are not tracked**

Run:

```powershell
git ls-files project_notes/API.txt artifacts logs .env .env.local .secrets
```

Expected:

```text
.secrets/lumi_secrets.env.example
```

This tracked example file is acceptable if it contains no real secrets. `project_notes/API.txt`, `artifacts`, `logs`, `.env`, and `.env.local` must not appear.

- [ ] **Step 2: Confirm ignore rules cover local sensitive paths**

Run:

```powershell
Select-String -Path ".gitignore" -Pattern "project_notes/API.txt|artifacts/|logs|\.env|\.env.local" | Select-Object LineNumber, Line
```

Expected: matching lines for `project_notes/API.txt`, `artifacts/`, `logs`, `.env`, and `.env.local`.

- [ ] **Step 3: Check ignored status for local artifact and API files**

Run:

```powershell
git status --ignored --short project_notes/API.txt artifacts logs .env .env.local
```

Expected: existing local files under these paths appear as ignored with `!!`, or do not appear if the path does not exist. They must not appear as `??`, `M`, `A`, or staged changes.

- [ ] **Step 4: Inspect staged files**

Run:

```powershell
git diff --cached --name-only
```

Expected: no staged files before starting baseline commits, unless this plan document has already been intentionally staged by the implementation worker.

---

## Task 3: Commit The Implementation Plan If Needed

**Files:**

- Commit: `docs/superpowers/plans/2026-08-28-equity-only-llm-baseline-archive-cleanup.md`

- [ ] **Step 1: Check whether this plan file is already committed**

Run:

```powershell
git ls-files docs/superpowers/plans/2026-08-28-equity-only-llm-baseline-archive-cleanup.md
```

Expected:

```text
docs/superpowers/plans/2026-08-28-equity-only-llm-baseline-archive-cleanup.md
```

If the command prints nothing, continue with Step 2. If it prints the path, skip to Task 4.

- [ ] **Step 2: Commit only the plan file**

Run:

```powershell
git add docs/superpowers/plans/2026-08-28-equity-only-llm-baseline-archive-cleanup.md
git diff --cached --name-only
git diff --cached --check
git commit -m "docs: add equity llm baseline archive cleanup plan"
```

Expected:

```text
docs/superpowers/plans/2026-08-28-equity-only-llm-baseline-archive-cleanup.md
```

The commit succeeds with no whitespace errors.

---

## Task 4: Freeze The Raw Baseline With Git Refs

**Files:**

- No file changes.
- Create tag: `baseline/equity-only-qqq-weekly-5y-20260828`
- Create branch: `archive/equity-only-qqq-weekly-5y-20260828`

- [ ] **Step 1: Verify the baseline tag does not already exist**

Run:

```powershell
$tag = "baseline/equity-only-qqq-weekly-5y-20260828"
if (git tag --list $tag) {
  throw "Baseline tag already exists: $tag. Stop and ask the user before moving or replacing it."
}
```

Expected: no output and no exception.

- [ ] **Step 2: Verify the archive branch does not already exist locally**

Run:

```powershell
$archiveBranch = "archive/equity-only-qqq-weekly-5y-20260828"
if (git branch --list $archiveBranch) {
  throw "Archive branch already exists locally: $archiveBranch. Stop and ask the user before replacing it."
}
```

Expected: no output and no exception.

- [ ] **Step 3: Create the annotated baseline tag**

Run:

```powershell
$tag = "baseline/equity-only-qqq-weekly-5y-20260828"
git tag -a $tag -m "Baseline: QQQ historical equity-only LLM weekly 5Y run, 2021-08-16 to 2026-08-14"
git rev-parse $tag
```

Expected: command prints the full commit hash for the baseline tag target.

- [ ] **Step 4: Create the archive branch at the same commit**

Run:

```powershell
$archiveBranch = "archive/equity-only-qqq-weekly-5y-20260828"
git branch $archiveBranch
git rev-parse $archiveBranch
git rev-parse baseline/equity-only-qqq-weekly-5y-20260828
```

Expected: both `git rev-parse` commands print the same commit hash.

---

## Task 5: Create The Baseline Note

**Files:**

- Create: `docs/superpowers/notes/2026-08-28-qqq-historical-equity-only-llm-weekly-5y-baseline.md`

- [ ] **Step 1: Generate the note from the recorded run and baseline tag**

Run:

```powershell
$tag = "baseline/equity-only-qqq-weekly-5y-20260828"
$baselineCommit = git rev-parse $tag
$baselineShort = git rev-parse --short $tag
$branch = git branch --show-current
$runRoot = "C:/Users/Ran/.config/superpowers/worktrees/lumibot/optimize-equity-only-llm/artifacts/ai_trading_team_example_benchmarks/20260827_181522_001118"
$strategyRoot = "$runRoot/qqq-historical-equity-only-llm"
$notePath = "docs/superpowers/notes/2026-08-28-qqq-historical-equity-only-llm-weekly-5y-baseline.md"

@"
# QQQ Historical Equity-Only LLM Weekly 5Y Baseline

## Purpose

Record the current QQQ historical equity-only LLM strategy as the milestone baseline for future optimization.

This note records the run result, git baseline reference, reproduction command, local artifact paths, known warnings, and interpretation limits.

## Git Reference

Branch at archive time:

    $branch

Baseline tag:

    $tag

Baseline commit:

    $baselineCommit

Short commit:

    $baselineShort

The tag points to the behavior baseline. Later cleanup or optimization commits should be compared against this tag.

## Benchmark Setup

    Strategy: qqq-historical-equity-only-llm
    Window: 2021-08-16 to 2026-08-14
    Cadence: weekly
    Weekly run weekday: MON
    Model: openai/gpt-5.6-luna
    Status: passed

## Reproduction Command

    cd C:\Users\Ran\.config\superpowers\worktrees\lumibot\optimize-equity-only-llm
    `$env:AI_TRADING_TEAM_MODEL='openai/gpt-5.6-luna'
    python scripts\run_ai_trading_team_examples_benchmark.py --strategy qqq-historical-equity-only-llm --start 2021-08-16 --end 2026-08-14 --run-frequency weekly --weekly-run-weekday MON --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 900 --env-file D:\Lumibot\project_notes\API.txt

## Result Metrics

| Metric | Value |
|---|---:|
| Total return | 471.98% |
| CAGR | 41.82% |
| Max drawdown | 19.17% |
| Sharpe | 1.53 |
| Volatility | 24.86% |
| ROMAD | 2.181 |

## Final Positions

The completed benchmark summary reported these final positions:

    USD cash-like position: 7096.21850585926
    FTNT: 299 shares
    PANW: 132 shares
    DXCM: 564 shares
    AMD: 98 shares
    CRWD: 218 shares

## Local Artifacts

Run directory:

    $strategyRoot

Summary file:

    $runRoot/summary.json

Account curve:

    $strategyRoot/qqq_historical_equity_only_llm_account_curve.html

Tearsheet:

    $strategyRoot/qqq_historical_equity_only_llm_tearsheet.html

Tearsheet metrics:

    $strategyRoot/qqq_historical_equity_only_llm_tearsheet_metrics.json

Trades:

    $strategyRoot/trades.csv

Stats:

    $strategyRoot/stats.csv

Backtest log:

    $strategyRoot/backtest.log

## Trace Health

The milestone investigation found:

    Agent summaries: 545
    Trace JSON files: 545
    First system run: 2021-08-16T09:30:00-04:00
    Last system run: 2026-08-10T09:30:00-04:00
    Fatal runner failure: none observed
    Negative cash blocker: none observed in this milestone summary

Known warning:

    2024-10-07 equity_basket_agent alpaca_news ReadTimeout from data.alpaca.markets with 20 second read timeout.

## Interpretation

This run is the current optimization baseline.

The result is important because the strategy roughly kept pace with SPY before 2026 and clearly separated above SPY after September 2025. The result also suggests that the equity-only LLM route deserves further work even though the strategy still has rough edges.

## Limitations

1. This is still a backtest, not live or paper trading proof.
2. The result may include model stochasticity.
3. One completed run is not enough to prove robustness.
4. News API timeout behavior still needs future hardening.
5. Later strategy changes should be compared against this baseline using the same window, cadence, model family, and artifact review process.

## Next Optimization Direction

Use this baseline to compare future equity-only LLM improvements, especially changes to:

1. Evidence ranking.
2. Candidate selection.
3. News usage.
4. Position sizing.
5. Exit logic.
6. Model choice and prompt architecture.
"@ | Set-Content -Path $notePath -Encoding utf8
```

Expected: `docs/superpowers/notes/2026-08-28-qqq-historical-equity-only-llm-weekly-5y-baseline.md` exists and contains the full commit hash printed by `git rev-parse baseline/equity-only-qqq-weekly-5y-20260828`.

- [ ] **Step 2: Review note content**

Run:

```powershell
Get-Content -TotalCount 240 docs/superpowers/notes/2026-08-28-qqq-historical-equity-only-llm-weekly-5y-baseline.md
```

Expected: the note is readable, contains no API key, and records the baseline tag, commit, metrics, command, artifact paths, warning, interpretation, and limitations.

- [ ] **Step 3: Commit only the baseline note**

Run:

```powershell
git add docs/superpowers/notes/2026-08-28-qqq-historical-equity-only-llm-weekly-5y-baseline.md
git diff --cached --name-only
git diff --cached --check
git commit -m "docs: record qqq equity llm weekly baseline"
```

Expected:

```text
docs/superpowers/notes/2026-08-28-qqq-historical-equity-only-llm-weekly-5y-baseline.md
```

The commit succeeds with no whitespace errors.

---

## Task 6: Conservative Cleanup Audit

**Files:**

- Modify `.gitignore` only if the required ignore rules are missing.

- [ ] **Step 1: List ignored generated files without deleting anything**

Run:

```powershell
git clean -ndX
```

Expected: command lists ignored generated files only. Do not run `git clean -fdX` in this task.

- [ ] **Step 2: Check untracked files**

Run:

```powershell
git status --short
```

Expected: no untracked source files that belong to this archive task. Ignored artifacts and API files should not appear.

- [ ] **Step 3: Verify `.gitignore` already protects local benchmark outputs and API keys**

Run:

```powershell
Select-String -Path ".gitignore" -Pattern "^artifacts/$|^logs$|^project_notes/API.txt$|^\.env$|^\.env.local$" | Select-Object LineNumber, Line
```

Expected: each required pattern appears.

If a pattern is missing, add only the missing line to `.gitignore`, then run:

```powershell
git add .gitignore
git diff --cached --check
git commit -m "chore: protect local baseline artifacts and secrets"
```

Expected: `.gitignore` commit only contains missing ignore rules.

---

## Task 7: Focused Verification

**Files:**

- Read only.

- [ ] **Step 1: Run focused unit tests**

Run:

```powershell
python -m pytest `
  tests/test_agent_history_summary.py `
  tests/test_ai_trading_team_equity_only_llm.py `
  tests/test_dynamic_equity_portfolio_constructor.py `
  tests/test_equity_trailing_stop_to_execution_plan.py `
  tests/test_qqq_nport_universe.py `
  -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run focused replay UI tests**

Run:

```powershell
python -m pytest `
  tests/test_agent_replay_ui_loader.py `
  tests/test_agent_replay_ui_formatters.py `
  tests/test_agent_replay_ui_server.py `
  tests/test_agent_replay_ui_static.py `
  -q
```

Expected: all selected tests pass.

- [ ] **Step 3: Run ruff on touched strategy and agent files**

Run:

```powershell
python -m ruff check `
  lumibot/example_strategies/ai_trading_team_equity_only_helpers.py `
  lumibot/example_strategies/ai_trading_team_equity_only_llm.py `
  lumibot/example_strategies/dynamic_equity_portfolio_constructor.py `
  lumibot/example_strategies/equity_trailing_stop_to_execution_plan.py `
  lumibot/components/agents/history_summary.py `
  lumibot/components/agents/duckdb_tools.py `
  lumibot/components/agents/replay_ui
```

Expected: ruff passes.

- [ ] **Step 4: Confirm replay UI starts**

Run:

```powershell
python scripts\agent_trace_ui.py
```

Expected:

```text
Running on http://127.0.0.1:8765
```

After the server starts, stop it with `Ctrl+C`. Do not leave the server running at the end of this task.

- [ ] **Step 5: Confirm baseline refs**

Run:

```powershell
git rev-parse baseline/equity-only-qqq-weekly-5y-20260828
git rev-parse archive/equity-only-qqq-weekly-5y-20260828
git status --short --branch
```

Expected: the tag and archive branch resolve successfully. Working tree is clean.

---

## Task 8: Push Branch, Archive Branch, and Tag

**Files:**

- No file changes.

- [ ] **Step 1: Push the working feature branch**

Run:

```powershell
git push -u origin feature/qqq-historical-constituent-universe
```

Expected: push succeeds to the user's fork.

- [ ] **Step 2: Push the archive branch**

Run:

```powershell
git push -u origin archive/equity-only-qqq-weekly-5y-20260828
```

Expected: push succeeds to the user's fork.

- [ ] **Step 3: Push the baseline tag**

Run:

```powershell
git push origin baseline/equity-only-qqq-weekly-5y-20260828
```

Expected: push succeeds to the user's fork.

- [ ] **Step 4: Verify remote refs**

Run:

```powershell
git ls-remote --heads origin feature/qqq-historical-constituent-universe archive/equity-only-qqq-weekly-5y-20260828
git ls-remote --tags origin baseline/equity-only-qqq-weekly-5y-20260828
```

Expected: remote feature branch, archive branch, and tag are listed.

---

## Task 9: Final Handoff Summary

**Files:**

- Read only.

- [ ] **Step 1: Collect final reference information**

Run:

```powershell
$tag = "baseline/equity-only-qqq-weekly-5y-20260828"
$archiveBranch = "archive/equity-only-qqq-weekly-5y-20260828"
[PSCustomObject]@{
  CurrentBranch = git branch --show-current
  Head = git rev-parse HEAD
  BaselineTag = $tag
  BaselineCommit = git rev-parse $tag
  ArchiveBranch = $archiveBranch
  ArchiveCommit = git rev-parse $archiveBranch
  Status = (git status --short)
} | Format-List
```

Expected: current branch is `feature/qqq-historical-constituent-universe`; status is empty; tag and archive branch point to the intended baseline commit.

- [ ] **Step 2: Report concise outcome to the user**

Include:

```text
Baseline note path
Baseline tag
Archive branch
Feature branch
Baseline commit hash
Verification commands run
Any failed or skipped verification
Whether pushes succeeded
```

Do not claim that the five-year backtest was re-run during this archive task.

---

## Acceptance Checklist

- [ ] Current branch and git status audited.
- [ ] Secret and artifact safety checked.
- [ ] Baseline tag created without moving an existing tag.
- [ ] Archive branch created without replacing an existing branch.
- [ ] Baseline note created and committed.
- [ ] Large artifacts remain local and ignored.
- [ ] Focused tests and ruff pass, or failures are reported clearly.
- [ ] Replay UI startup checked.
- [ ] Feature branch, archive branch, and tag pushed to GitHub.
- [ ] User receives exact baseline references.
