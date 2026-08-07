# Agent Replay Multi Trace Root Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `python scripts\agent_trace_ui.py` show local project traces and benchmark artifact traces in one UI dataset by default.

**Architecture:** Add a multi-root loader function that merges `ReplayRun` objects from multiple trace roots while preserving existing single-root behavior. Update the Flask server to accept one or more roots. Update the script to discover default project and benchmark roots unless `--trace-root` is explicitly provided.

**Tech Stack:** Python, pathlib, pytest, existing Flask replay UI.

---

## Files

- Modify: `lumibot/components/agents/replay_ui/loader.py`
  - Add `build_replay_dataset_from_roots(trace_roots)`.
  - Add benchmark artifact label detection.
  - Keep `build_replay_dataset(trace_root)` compatible.
- Modify: `lumibot/components/agents/replay_ui/server.py`
  - Accept a single path or list of paths.
  - Use `build_replay_dataset_from_roots`.
- Modify: `scripts/agent_trace_ui.py`
  - Discover default trace roots.
  - Keep `--trace-root` as an explicit single-root override.
- Modify: `tests/test_agent_replay_ui_loader.py`
  - Add loader tests for merged roots and benchmark labels.
- Add: `tests/test_agent_trace_ui_script.py`
  - Add script discovery tests.

## Task 1: Loader Multi-Root Merge

**Files:**
- Modify: `tests/test_agent_replay_ui_loader.py`
- Modify: `lumibot/components/agents/replay_ui/loader.py`

- [ ] Step 1: Add failing tests.

Add tests:

```python
def test_build_replay_dataset_from_roots_merges_multiple_roots(tmp_path):
    from lumibot.components.agents.replay_ui.loader import build_replay_dataset_from_roots

    first = tmp_path / "first" / "agent_runtime"
    second = tmp_path / "second" / "agent_runtime"
    _write_trace(
        first / "traces" / "one" / "trace.json",
        {
            "agent": "agent_one",
            "request": {
                "runtime_context": {
                    "mode": "backtesting",
                    "current_datetime": "2024-09-05T09:30:00-04:00",
                    "strategy_name": "StrategyOne",
                }
            },
            "events": [],
            "summary": "one",
        },
    )
    _write_trace(
        second / "traces" / "two" / "trace.json",
        {
            "agent": "agent_two",
            "request": {
                "runtime_context": {
                    "mode": "backtesting",
                    "current_datetime": "2024-09-06T09:30:00-04:00",
                    "strategy_name": "StrategyTwo",
                }
            },
            "events": [],
            "summary": "two",
        },
    )

    public = build_replay_dataset_from_roots([first, second]).to_public_dict()

    assert [run["strategy_name"] for run in public["runs"]] == ["StrategyOne", "StrategyTwo"]
    assert "first" in public["source_path"]
    assert "second" in public["source_path"]
```

```python
def test_benchmark_artifact_trace_root_gets_readable_label(tmp_path):
    from lumibot.components.agents.replay_ui.loader import build_replay_dataset

    root = (
        tmp_path
        / "artifacts"
        / "ai_trading_team_example_benchmarks"
        / "20260717_185016_461584"
        / "growth-execution-test"
        / "cache"
        / "agent_runtime"
    )
    _write_trace(
        root / "traces" / "execution_agent" / "trace.json",
        {
            "agent": "execution_agent",
            "request": {
                "runtime_context": {
                    "mode": "backtesting",
                    "current_datetime": "2024-09-05T09:30:00-04:00",
                    "strategy_name": "AITradingTeamGrowthExecutionTestStrategy",
                }
            },
            "events": [],
            "summary": "execution",
        },
    )

    public = build_replay_dataset(root).to_public_dict()

    assert public["runs"][0]["label"] == "20260717_185016_461584 / growth-execution-test"
```

- [ ] Step 2: Run red test.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_replay_ui_loader.py::test_build_replay_dataset_from_roots_merges_multiple_roots tests\test_agent_replay_ui_loader.py::test_benchmark_artifact_trace_root_gets_readable_label -q
```

Expected: import/function or assertion failure.

- [ ] Step 3: Implement loader merge.

In `loader.py`:

```python
def build_replay_dataset(trace_root: str | Path) -> ReplayDataset:
    return _build_replay_dataset_for_root(Path(trace_root))


def build_replay_dataset_from_roots(trace_roots: list[str | Path]) -> ReplayDataset:
    datasets = [_build_replay_dataset_for_root(Path(root)) for root in trace_roots]
    runs = []
    warnings = []
    source_paths = []
    for dataset in datasets:
        runs.extend(dataset.runs)
        warnings.extend(dataset.warnings)
        if dataset.source_path:
            source_paths.append(dataset.source_path)
    runs.sort(key=lambda run: (run.strategy_name, run.label, run.id))
    return ReplayDataset(runs=runs, source_path="; ".join(source_paths), warnings=warnings)
```

Move the old `build_replay_dataset` body into `_build_replay_dataset_for_root(root: Path)`.

Update `_system_run_backtest_key` fallback label:

```python
return strategy_name, _backtest_run_id(root, strategy_name), _trace_root_label(root)
```

Add:

```python
def _trace_root_label(root: Path) -> str:
    parts = root.parts
    marker = "ai_trading_team_example_benchmarks"
    if marker in parts:
        index = parts.index(marker)
        if len(parts) > index + 2:
            return f"{parts[index + 1]} / {parts[index + 2]}"
    return root.name or "agent_runtime"
```

- [ ] Step 4: Run loader tests.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_replay_ui_loader.py -q
```

Expected: pass.

## Task 2: Script Default Root Discovery

**Files:**
- Add: `tests/test_agent_trace_ui_script.py`
- Modify: `scripts/agent_trace_ui.py`
- Modify: `lumibot/components/agents/replay_ui/server.py`

- [ ] Step 1: Add failing script tests.

Create `tests/test_agent_trace_ui_script.py`:

```python
from pathlib import Path

import scripts.agent_trace_ui as agent_trace_ui


def test_default_trace_roots_include_project_and_benchmark_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_trace_ui, "REPO_ROOT", tmp_path)
    local_root = tmp_path / ".lumibot" / "agent_runtime"
    benchmark_root = (
        tmp_path
        / "artifacts"
        / "ai_trading_team_example_benchmarks"
        / "20260717_185016_461584"
        / "growth-execution-test"
        / "cache"
        / "agent_runtime"
    )
    (local_root / "traces").mkdir(parents=True)
    (benchmark_root / "traces").mkdir(parents=True)

    roots = agent_trace_ui.default_trace_roots()

    assert roots == [local_root, benchmark_root]


def test_default_trace_roots_honor_explicit_env_override(tmp_path, monkeypatch):
    explicit = tmp_path / "custom" / "agent_runtime"
    monkeypatch.setenv("LUMIBOT_AGENT_TRACE_ROOT", str(explicit))

    assert agent_trace_ui.default_trace_roots() == [explicit]
```

- [ ] Step 2: Run red script tests.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_trace_ui_script.py -q
```

Expected: `default_trace_roots` missing.

- [ ] Step 3: Implement script discovery and server multi-root.

In `scripts/agent_trace_ui.py`:

```python
def default_trace_roots() -> list[Path]:
    explicit_root = os.environ.get("LUMIBOT_AGENT_TRACE_ROOT")
    if explicit_root:
        return [Path(explicit_root)]
    roots = [REPO_ROOT / ".lumibot" / "agent_runtime"]
    benchmark_roots = sorted(
        (REPO_ROOT / "artifacts" / "ai_trading_team_example_benchmarks").glob("*/*/cache/agent_runtime")
    )
    roots.extend(benchmark_roots)
    return roots
```

Keep `--trace-root` but default it to `None`:

```python
parser.add_argument("--trace-root", type=Path, default=None)
...
trace_roots = [args.trace_root] if args.trace_root is not None else default_trace_roots()
app = create_app(trace_roots)
print("Trace roots:")
for root in trace_roots:
    print(f"  - {root}")
```

In `server.py`:

```python
from .loader import build_replay_dataset_from_roots

def create_app(trace_root: str | Path | list[str | Path]) -> Flask:
    trace_roots = trace_root if isinstance(trace_root, list) else [trace_root]
    app.config["TRACE_ROOTS"] = [Path(root) for root in trace_roots]
    ...
    replay_dataset = build_replay_dataset_from_roots(app.config["TRACE_ROOTS"])
```

- [ ] Step 4: Run script and loader tests.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_trace_ui_script.py tests\test_agent_replay_ui_loader.py -q
```

Expected: pass.

## Task 3: End-To-End Verification

**Files:**
- No source edits expected.

- [ ] Step 1: Run targeted tests.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_trace_ui_script.py tests\test_agent_replay_ui_loader.py -q
.\.venv\Scripts\python.exe -m ruff check scripts\agent_trace_ui.py lumibot\components\agents\replay_ui\loader.py lumibot\components\agents\replay_ui\server.py tests\test_agent_trace_ui_script.py tests\test_agent_replay_ui_loader.py
git diff --check
```

- [ ] Step 2: Verify live dataset contains latest benchmark trace.

Run:

```powershell
.\.venv\Scripts\python.exe -c "from scripts.agent_trace_ui import default_trace_roots; from lumibot.components.agents.replay_ui.loader import build_replay_dataset_from_roots; ds=build_replay_dataset_from_roots(default_trace_roots()).to_public_dict(); print([(r['strategy_name'], r['label']) for r in ds['runs']])"
```

Expected output includes:

```text
('AITradingTeamGrowthExecutionTestStrategy', '20260717_185016_461584 / growth-execution-test')
```

- [ ] Step 3: Commit.

```powershell
git add docs/superpowers/specs/2026-07-17-agent-replay-multi-trace-root-design.md docs/superpowers/plans/2026-07-17-agent-replay-multi-trace-root.md scripts/agent_trace_ui.py lumibot/components/agents/replay_ui/loader.py lumibot/components/agents/replay_ui/server.py tests/test_agent_trace_ui_script.py tests/test_agent_replay_ui_loader.py
git commit -m "feat: load all agent replay trace roots"
```

