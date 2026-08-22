# QQQ Historical Symbol Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair known bad QQQ historical constituent tickers before they reach the equity agent, and verify the repaired symbols can be used in a short QQQ historical smoke backtest.

**Architecture:** Add a focused symbol repair helper in `lumibot.tools.universe.qqq_nport`, apply it inside `resolve_qqq_snapshot()` after reading snapshot symbols and before returning `AsOfSnapshotResolution`, and pass compact repair metadata through the existing strategy `universe_source` context. Do not mutate cached SEC/N-PORT JSON files and do not change trading prompts or selection logic.

**Tech Stack:** Python dataclasses, existing LumiBot QQQ N-PORT resolver, pytest, existing benchmark runner, existing agent trace files.

---

## File Structure

- Modify: `lumibot/tools/universe/qqq_nport.py`
  - Owns QQQ N-PORT data normalization.
  - Add alias map, repair helper, repair metadata, and resolver integration.
- Modify: `lumibot/example_strategies/ai_trading_team_equity_only_llm.py`
  - Include resolver repair metadata in `universe_source` context.
  - Do not add strategy-specific alias logic here.
- Modify: `tests/test_ai_trading_team_equity_only_llm.py`
  - Add resolver/strategy context tests for repaired symbols and metadata.
  - Keep fixed-50 baseline assertions unchanged.
- Create: `docs/superpowers/notes/2026-08-22-qqq-historical-symbol-repair-validation.md`
  - Record unit test results, smoke backtest command, artifact path, and trace inspection results.

Do not modify:

- `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`
- Equity agent prompt text
- Execution agent prompt text
- Cached files under `C:\Users\Ran\AppData\Local\LumiWealth\lumibot\Cache\1.0\universe\qqq_nport`

---

### Task 1: Add Failing Unit Tests For QQQ Symbol Repair Helper

**Files:**
- Modify: `tests/test_ai_trading_team_equity_only_llm.py`
- Test target: `lumibot.tools.universe.qqq_nport`

- [ ] **Step 1: Add a direct helper test near the existing QQQ tests**

Add this test after `test_qqq_historical_equity_strategy_defaults_to_weekly_strict_without_changing_fixed_baseline`:

```python
def test_qqq_symbol_repair_aliases_and_deduplicates_preserving_order():
    qqq_nport = importlib.import_module("lumibot.tools.universe.qqq_nport")

    symbols, repair = qqq_nport.repair_qqq_symbols(
        ["AAPL", "CPW", "CHKP", "MRVLEUR", "MRVL", "TRI4EUR", "STXN", "", "AAPL"]
    )

    assert symbols == ("AAPL", "CHKP", "MRVL", "TRI", "STX")
    assert repair == {
        "applied": True,
        "raw_count": 9,
        "repaired_count": 4,
        "deduped_count": 3,
        "dropped_count": 1,
        "final_count": 5,
        "aliases": [
            {"from": "CPW", "to": "CHKP"},
            {"from": "MRVLEUR", "to": "MRVL"},
            {"from": "TRI4EUR", "to": "TRI"},
            {"from": "STXN", "to": "STX"},
        ],
    }
```

- [ ] **Step 2: Add a no-op repair metadata test**

Add this test immediately after the previous test:

```python
def test_qqq_symbol_repair_reports_noop_for_clean_symbols():
    qqq_nport = importlib.import_module("lumibot.tools.universe.qqq_nport")

    symbols, repair = qqq_nport.repair_qqq_symbols(["MSFT", "AAPL", "NVDA"])

    assert symbols == ("MSFT", "AAPL", "NVDA")
    assert repair == {
        "applied": False,
        "raw_count": 3,
        "repaired_count": 0,
        "deduped_count": 0,
        "dropped_count": 0,
        "final_count": 3,
        "aliases": [],
    }
```

- [ ] **Step 3: Run the new tests and confirm they fail**

Run:

```powershell
python -m pytest `
  tests/test_ai_trading_team_equity_only_llm.py::test_qqq_symbol_repair_aliases_and_deduplicates_preserving_order `
  tests/test_ai_trading_team_equity_only_llm.py::test_qqq_symbol_repair_reports_noop_for_clean_symbols `
  -q
```

Expected:

```text
FAILED ... AttributeError: module 'lumibot.tools.universe.qqq_nport' has no attribute 'repair_qqq_symbols'
```

- [ ] **Step 4: Commit failing tests**

```powershell
git add tests\test_ai_trading_team_equity_only_llm.py
git commit -m "test: cover qqq historical symbol repair"
```

---

### Task 2: Implement QQQ Symbol Repair Helper

**Files:**
- Modify: `lumibot/tools/universe/qqq_nport.py`
- Test: `tests/test_ai_trading_team_equity_only_llm.py`

- [ ] **Step 1: Add the alias map near QQQ constants**

In `lumibot/tools/universe/qqq_nport.py`, add after `DEFAULT_OPENFIGI_BATCH_SIZE = 10`:

```python
QQQ_SYMBOL_ALIASES = {
    "CPW": "CHKP",
    "MRVLEUR": "MRVL",
    "TRI4EUR": "TRI",
    "STXN": "STX",
}
```

- [ ] **Step 2: Add the repair helper after `normalize_symbol()`**

Add this function immediately after `normalize_symbol`:

```python
def repair_qqq_symbols(symbols: Iterable[object]) -> tuple[tuple[str, ...], dict[str, Any]]:
    raw_count = 0
    repaired_count = 0
    deduped_count = 0
    dropped_count = 0
    repaired_symbols: list[str] = []
    seen: set[str] = set()
    aliases: list[dict[str, str]] = []

    for raw_symbol in symbols:
        raw_count += 1
        normalized = normalize_symbol(raw_symbol)
        if normalized is None:
            dropped_count += 1
            continue

        repaired = normalize_symbol(QQQ_SYMBOL_ALIASES.get(normalized, normalized))
        if repaired is None:
            dropped_count += 1
            continue

        if repaired != normalized:
            repaired_count += 1
            aliases.append({"from": normalized, "to": repaired})

        if repaired in seen:
            deduped_count += 1
            continue

        seen.add(repaired)
        repaired_symbols.append(repaired)

    repair = {
        "applied": bool(repaired_count or deduped_count or dropped_count),
        "raw_count": raw_count,
        "repaired_count": repaired_count,
        "deduped_count": deduped_count,
        "dropped_count": dropped_count,
        "final_count": len(repaired_symbols),
        "aliases": aliases,
    }
    return tuple(repaired_symbols), repair
```

- [ ] **Step 3: Run the helper tests**

Run:

```powershell
python -m pytest `
  tests/test_ai_trading_team_equity_only_llm.py::test_qqq_symbol_repair_aliases_and_deduplicates_preserving_order `
  tests/test_ai_trading_team_equity_only_llm.py::test_qqq_symbol_repair_reports_noop_for_clean_symbols `
  -q
```

Expected:

```text
2 passed
```

- [ ] **Step 4: Commit implementation**

```powershell
git add lumibot\tools\universe\qqq_nport.py
git commit -m "feat: repair known qqq historical symbols"
```

---

### Task 3: Return Repaired Symbols And Metadata From Resolver

**Files:**
- Modify: `lumibot/tools/universe/qqq_nport.py`
- Modify: `tests/test_ai_trading_team_equity_only_llm.py`

- [ ] **Step 1: Add a failing resolver test using temporary snapshot files**

Add this test near the QQQ strategy tests:

```python
def test_resolve_qqq_snapshot_returns_repaired_symbols_and_metadata(tmp_path):
    qqq_nport = importlib.import_module("lumibot.tools.universe.qqq_nport")
    normalized = tmp_path / "normalized"
    normalized.mkdir()
    snapshot = {
        "report_date": "2026-03-31",
        "filing_date": "2026-05-28",
        "accession_number": "0001067839-26-000024",
        "source_url": "https://www.sec.gov/example.xml",
        "holdings": [
            {"symbol": "AAPL"},
            {"symbol": "TRI4EUR"},
            {"symbol": "TRI"},
            {"symbol": "STXN"},
            {"symbol": "CPW"},
        ],
    }
    (normalized / "qqq_nport_2026-03-31_0001067839-26-000024.json").write_text(
        json.dumps(snapshot),
        encoding="utf-8",
    )

    resolution = qqq_nport.resolve_qqq_snapshot(
        "2026-06-01",
        mode="strict",
        data_dir=tmp_path,
    )

    assert resolution.symbols == ("AAPL", "TRI", "STX", "CHKP")
    assert resolution.symbol_repair == {
        "applied": True,
        "raw_count": 5,
        "repaired_count": 3,
        "deduped_count": 1,
        "dropped_count": 0,
        "final_count": 4,
        "aliases": [
            {"from": "TRI4EUR", "to": "TRI"},
            {"from": "STXN", "to": "STX"},
            {"from": "CPW", "to": "CHKP"},
        ],
    }
```

- [ ] **Step 2: Run the resolver test and confirm it fails**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py::test_resolve_qqq_snapshot_returns_repaired_symbols_and_metadata -q
```

Expected:

```text
FAILED ... AttributeError: 'AsOfSnapshotResolution' object has no attribute 'symbol_repair'
```

- [ ] **Step 3: Extend `AsOfSnapshotResolution`**

In `lumibot/tools/universe/qqq_nport.py`, update the dataclass:

```python
@dataclass(frozen=True)
class AsOfSnapshotResolution:
    as_of_date: date
    mode: str
    selected_report_date: date
    selected_filing_date: date
    accession_number: str
    symbols: tuple[str, ...]
    snapshot_path: Path
    source_url: str | None = None
    symbol_repair: dict[str, Any] = field(default_factory=dict)
```

Update `to_dict()` to include:

```python
"symbol_repair": _json_ready(self.symbol_repair),
```

- [ ] **Step 4: Apply repair in `resolve_qqq_snapshot()`**

Replace the return block in `resolve_qqq_snapshot()` with:

```python
    symbols, symbol_repair = repair_qqq_symbols(_snapshot_symbols(snapshot))
    return AsOfSnapshotResolution(
        as_of_date=as_of,
        mode=mode,
        selected_report_date=report,
        selected_filing_date=filing,
        accession_number=accession_number,
        symbols=symbols,
        snapshot_path=path,
        source_url=source_url,
        symbol_repair=symbol_repair,
    )
```

- [ ] **Step 5: Run resolver and helper tests**

Run:

```powershell
python -m pytest `
  tests/test_ai_trading_team_equity_only_llm.py::test_qqq_symbol_repair_aliases_and_deduplicates_preserving_order `
  tests/test_ai_trading_team_equity_only_llm.py::test_qqq_symbol_repair_reports_noop_for_clean_symbols `
  tests/test_ai_trading_team_equity_only_llm.py::test_resolve_qqq_snapshot_returns_repaired_symbols_and_metadata `
  -q
```

Expected:

```text
3 passed
```

- [ ] **Step 6: Commit resolver integration**

```powershell
git add lumibot\tools\universe\qqq_nport.py tests\test_ai_trading_team_equity_only_llm.py
git commit -m "feat: apply qqq symbol repairs in resolver"
```

---

### Task 4: Pass Repair Metadata Into Strategy Trace Context

**Files:**
- Modify: `lumibot/example_strategies/ai_trading_team_equity_only_llm.py`
- Modify: `tests/test_ai_trading_team_equity_only_llm.py`

- [ ] **Step 1: Update the test helper to allow repair metadata**

Modify `qqq_resolution()` in `tests/test_ai_trading_team_equity_only_llm.py` so it accepts `symbol_repair`:

```python
def qqq_resolution(
    *,
    as_of_date="2024-09-05",
    mode="strict",
    selected_report_date="2024-06-30",
    selected_filing_date="2024-08-28",
    accession_number="0001752724-24-196011",
    symbols=("MSFT", "AAPL", "NVDA", "AMZN"),
    snapshot_path="C:/cache/qqq_nport_2024-06-30.json",
    source_url="https://www.sec.gov/example.xml",
    symbol_repair=None,
):
    return SimpleNamespace(
        as_of_date=date.fromisoformat(as_of_date),
        mode=mode,
        selected_report_date=date.fromisoformat(selected_report_date),
        selected_filing_date=date.fromisoformat(selected_filing_date),
        accession_number=accession_number,
        symbols=tuple(symbols),
        snapshot_path=Path(snapshot_path),
        source_url=source_url,
        symbol_repair=symbol_repair or {
            "applied": False,
            "raw_count": len(symbols),
            "repaired_count": 0,
            "deduped_count": 0,
            "dropped_count": 0,
            "final_count": len(symbols),
            "aliases": [],
        },
    )
```

- [ ] **Step 2: Update expected `universe_source` in existing metadata test**

In `test_qqq_historical_strategy_resolves_snapshot_and_passes_metadata_to_equity_agent`, add this field to the expected dictionary:

```python
"symbol_repair": {
    "applied": False,
    "raw_count": 4,
    "repaired_count": 0,
    "deduped_count": 0,
    "dropped_count": 0,
    "final_count": 4,
    "aliases": [],
},
```

- [ ] **Step 3: Add a failing strategy context test for repaired symbols**

Add this test after `test_qqq_historical_strategy_supports_prototype_mode_data_dir_and_symbol_normalization`:

```python
def test_qqq_historical_strategy_passes_repair_metadata_to_equity_agent(monkeypatch):
    module = load_module()
    strategy = make_running_strategy(module.AITradingTeamQQQHistoricalEquityOnlyLLMStrategy)
    strategy.agents.summaries["equity_basket_agent"] = json.dumps(
        {
            "basket_id": "equity",
            "target_weight": 1.0,
            "status": "active",
            "candidate_symbols": ["AAPL", "CHKP", "MRVL"],
            "selected_symbol": "CHKP",
            "reason_brief": "CHKP is selected.",
        }
    )
    strategy.agents.summaries["execution_agent"] = "Executed plan."

    def fake_resolve_qqq_snapshot(as_of_date, *, mode="strict", data_dir=None):
        return qqq_resolution(
            symbols=("AAPL", "CHKP", "MRVL"),
            symbol_repair={
                "applied": True,
                "raw_count": 4,
                "repaired_count": 2,
                "deduped_count": 1,
                "dropped_count": 0,
                "final_count": 3,
                "aliases": [
                    {"from": "CPW", "to": "CHKP"},
                    {"from": "MRVLEUR", "to": "MRVL"},
                ],
            },
        )

    def fake_target_portfolio_to_execution_plan(strategy_arg, *, date, target_portfolio):
        return {
            "schema_version": "1.0",
            "date": date,
            "target_portfolio": target_portfolio,
            "current_vs_target": [],
            "cash_projection": {
                "cash_before": 100000.0,
                "estimated_sell_proceeds": 0.0,
                "estimated_buy_cost": 0.0,
                "cash_after_estimate": 100000.0,
                "buy_sizing_buffer_pct": 0.02,
                "negative_cash_allowed": False,
            },
            "execution_plan": {"schema_version": 1, "intent": "hold", "orders": []},
            "warnings": [],
        }

    monkeypatch.setattr(module, "resolve_qqq_snapshot", fake_resolve_qqq_snapshot)
    monkeypatch.setattr(module, "target_portfolio_to_execution_plan", fake_target_portfolio_to_execution_plan)

    strategy.on_trading_iteration()

    equity_context = strategy.agents["equity_basket_agent"].calls[0]["context"]
    assert equity_context["basket_symbols"] == ["AAPL", "CHKP", "MRVL"]
    assert equity_context["universe_source"]["symbol_repair"] == {
        "applied": True,
        "raw_count": 4,
        "repaired_count": 2,
        "deduped_count": 1,
        "dropped_count": 0,
        "final_count": 3,
        "aliases": [
            {"from": "CPW", "to": "CHKP"},
            {"from": "MRVLEUR", "to": "MRVL"},
        ],
    }
```

- [ ] **Step 4: Run the strategy metadata tests and confirm failure**

Run:

```powershell
python -m pytest `
  tests/test_ai_trading_team_equity_only_llm.py::test_qqq_historical_strategy_resolves_snapshot_and_passes_metadata_to_equity_agent `
  tests/test_ai_trading_team_equity_only_llm.py::test_qqq_historical_strategy_passes_repair_metadata_to_equity_agent `
  -q
```

Expected before implementation:

```text
FAILED ... expected universe_source to include symbol_repair
```

- [ ] **Step 5: Include `symbol_repair` in `_qqq_universe_source_payload()`**

In `lumibot/example_strategies/ai_trading_team_equity_only_llm.py`, update `_qqq_universe_source_payload`:

```python
def _qqq_universe_source_payload(resolution: Any, symbols: list[str]) -> dict[str, Any]:
    payload = {
        "type": "qqq_nport",
        "mode": str(resolution.mode),
        "as_of_date": resolution.as_of_date.isoformat(),
        "selected_report_date": resolution.selected_report_date.isoformat(),
        "selected_filing_date": resolution.selected_filing_date.isoformat(),
        "accession_number": str(resolution.accession_number),
        "holding_count": len(symbols),
        "snapshot_path": str(resolution.snapshot_path),
        "source_url": str(resolution.source_url) if resolution.source_url else None,
    }
    symbol_repair = getattr(resolution, "symbol_repair", None)
    if isinstance(symbol_repair, dict):
        payload["symbol_repair"] = symbol_repair
    return payload
```

- [ ] **Step 6: Run the strategy metadata tests**

Run:

```powershell
python -m pytest `
  tests/test_ai_trading_team_equity_only_llm.py::test_qqq_historical_strategy_resolves_snapshot_and_passes_metadata_to_equity_agent `
  tests/test_ai_trading_team_equity_only_llm.py::test_qqq_historical_strategy_supports_prototype_mode_data_dir_and_symbol_normalization `
  tests/test_ai_trading_team_equity_only_llm.py::test_qqq_historical_strategy_passes_repair_metadata_to_equity_agent `
  -q
```

Expected:

```text
3 passed
```

- [ ] **Step 7: Commit strategy trace metadata**

```powershell
git add lumibot\example_strategies\ai_trading_team_equity_only_llm.py tests\test_ai_trading_team_equity_only_llm.py
git commit -m "feat: expose qqq symbol repair metadata"
```

---

### Task 5: Run Focused Test Suite

**Files:**
- Verify only; no source edits expected.

- [ ] **Step 1: Run QQQ and fixed baseline unit tests**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py -q
```

Expected:

```text
all tests in tests/test_ai_trading_team_equity_only_llm.py pass
```

- [ ] **Step 2: Run lint/format check on changed Python files**

Run:

```powershell
python -m ruff check `
  lumibot/tools/universe/qqq_nport.py `
  lumibot/example_strategies/ai_trading_team_equity_only_llm.py `
  tests/test_ai_trading_team_equity_only_llm.py
```

Expected:

```text
All checks passed!
```

- [ ] **Step 3: Commit if test-only fixes were needed**

If Task 5 required edits, commit them:

```powershell
git add lumibot\tools\universe\qqq_nport.py lumibot\example_strategies\ai_trading_team_equity_only_llm.py tests\test_ai_trading_team_equity_only_llm.py
git commit -m "test: verify qqq symbol repair path"
```

If no edits were needed, do not create an empty commit.

---

### Task 6: Run Short Smoke Backtest And Inspect Trace

**Files:**
- Create: `docs/superpowers/notes/2026-08-22-qqq-historical-symbol-repair-validation.md`
- Read: newest artifact under `artifacts/ai_trading_team_example_benchmarks`

- [ ] **Step 1: Run the short smoke backtest**

Use the 2026 window because the cached snapshot contains `TRI4EUR` and `STXN`.

Run:

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

Expected:

```text
"strategy": "qqq-historical-equity-only-llm"
"status": "passed"
```

- [ ] **Step 2: Locate the newest smoke artifact**

Run this script, which prints the newest QQQ historical strategy artifact path:

```powershell
@'
from pathlib import Path

root = Path("artifacts/ai_trading_team_example_benchmarks")
strategy = "qqq-historical-equity-only-llm"
artifacts = [path / strategy for path in root.iterdir() if (path / strategy).is_dir()]
artifacts.sort(key=lambda path: path.stat().st_mtime, reverse=True)
if not artifacts:
    raise SystemExit("No qqq-historical-equity-only-llm artifacts found")
print(artifacts[0])
'@ | python -
```

Expected:

```text
A path ending with qqq-historical-equity-only-llm is printed.
```

- [ ] **Step 3: Inspect equity traces for repaired symbols and metadata**

Run this script. It automatically uses the newest QQQ historical artifact:

```powershell
@'
import json
from pathlib import Path

artifact_root = Path("artifacts/ai_trading_team_example_benchmarks")
strategy = "qqq-historical-equity-only-llm"
artifacts = [path / strategy for path in artifact_root.iterdir() if (path / strategy).is_dir()]
artifacts.sort(key=lambda path: path.stat().st_mtime, reverse=True)
if not artifacts:
    raise SystemExit("No qqq-historical-equity-only-llm artifacts found")
run_root = artifacts[0]
trace_root = run_root / "cache" / "agent_runtime" / "traces" / "equity_basket_agent"
bad = {"CPW", "MRVLEUR", "TRI4EUR", "STXN"}
expected = {"TRI", "STX", "CHKP", "MRVL"}
seen_bad_context = set()
seen_bad_tool = set()
seen_expected_context = set()
seen_expected_tool = set()
repairs = []
for path in sorted(trace_root.glob("*.json")):
    data = json.loads(path.read_text(encoding="utf-8"))
    context = data.get("request", {}).get("context", {})
    symbols = set(context.get("basket_symbols", []))
    source = context.get("universe_source", {})
    repair = source.get("symbol_repair", {})
    if repair:
        repairs.append(repair)
    seen_bad_context.update(bad & symbols)
    seen_expected_context.update(expected & symbols)
    for call in data.get("tool_calls", []):
        if call.get("tool_name") == "market_load_history_tables_summary":
            tool_symbols = set(call.get("payload", {}).get("symbols", []))
            seen_bad_tool.update(bad & tool_symbols)
            seen_expected_tool.update(expected & tool_symbols)
print("artifact", run_root)
print("bad_in_context", sorted(seen_bad_context))
print("bad_in_tool_call", sorted(seen_bad_tool))
print("expected_in_context", sorted(seen_expected_context))
print("expected_in_tool_call", sorted(seen_expected_tool))
print("repairs", json.dumps(repairs, indent=2, sort_keys=True))
if seen_bad_context or seen_bad_tool:
    raise SystemExit("Known bad symbols reached context or tool calls")
if not repairs:
    raise SystemExit("No symbol_repair metadata found")
'@ | python -
```

Expected:

```text
bad_in_context []
bad_in_tool_call []
repairs prints at least one symbol_repair object.
```

- [ ] **Step 4: Inspect backtest log for known bad symbol Yahoo misses**

Run this script. It automatically uses the newest QQQ historical artifact:

```powershell
@'
from pathlib import Path

artifact_root = Path("artifacts/ai_trading_team_example_benchmarks")
strategy = "qqq-historical-equity-only-llm"
artifacts = [path / strategy for path in artifact_root.iterdir() if (path / strategy).is_dir()]
artifacts.sort(key=lambda path: path.stat().st_mtime, reverse=True)
if not artifacts:
    raise SystemExit("No qqq-historical-equity-only-llm artifacts found")
log_path = artifacts[0] / "backtest.log"
text = log_path.read_text(encoding="utf-8", errors="ignore")
bad = [symbol for symbol in ["CPW", "MRVLEUR", "TRI4EUR", "STXN"] if symbol in text]
print("artifact", artifacts[0])
print("bad_symbol_log_matches", bad)
if bad:
    raise SystemExit("Known bad symbols still appear in backtest.log")
'@ | python -
```

Expected:

```text
bad_symbol_log_matches []
```

- [ ] **Step 5: Write validation note with measured values**

Create `docs/superpowers/notes/2026-08-22-qqq-historical-symbol-repair-validation.md` with these sections and the actual outputs observed in the previous steps:

```markdown
# QQQ Historical Symbol Repair Validation

## Purpose

Validate that known bad QQQ historical symbols are repaired before reaching the equity agent and market summary tool.

## Unit Tests

Record the exact command and result from `python -m pytest tests/test_ai_trading_team_equity_only_llm.py -q`.

## Ruff

Record the exact command and result from `python -m ruff check lumibot/tools/universe/qqq_nport.py lumibot/example_strategies/ai_trading_team_equity_only_llm.py tests/test_ai_trading_team_equity_only_llm.py`.

## Smoke Backtest

Record the strategy, window, model, artifact path, and runner status from the smoke backtest.

## Trace Inspection

Record the printed values for bad symbols, repaired symbols, and `universe_source.symbol_repair` from the trace inspection script.

## Backtest Log Check

Record the printed `bad_symbol_log_matches` value from the log inspection script.

## Notes

This validation does not judge strategy performance and does not replace a full five-year benchmark rerun. `SHOP` remains a separate follow-up because manual checks showed it has Yahoo daily data.
```

- [ ] **Step 6: Commit validation note**

```powershell
git add docs\superpowers\notes\2026-08-22-qqq-historical-symbol-repair-validation.md
git commit -m "docs: validate qqq symbol repair smoke"
```

---

### Task 7: Final Verification And Handoff

**Files:**
- Verify only; no source edits expected.

- [ ] **Step 1: Check git status**

Run:

```powershell
git status --short --branch
```

Expected:

```text
No uncommitted source, test, or docs changes except intentionally ignored runtime artifacts.
```

- [ ] **Step 2: Review acceptance criteria against evidence**

Confirm each item:

```text
1. Known bad QQQ symbols are repaired before reaching basket_symbols.
2. Repaired symbols are deduplicated while preserving order.
3. Repair metadata is visible in strategy context/trace.
4. Fixed-50 strategy tests remain unchanged and passing.
5. A short QQQ historical backtest completes.
6. The short backtest trace shows repaired symbols can reach market_load_history_tables_summary.
7. No full five-year benchmark rerun was performed for this feature.
```

- [ ] **Step 3: Report remaining follow-ups**

Report these explicitly:

```text
1. SHOP data-miss logs are not fixed by this feature.
2. Generic tradability/data-coverage filtering is not implemented yet.
3. Full five-year QQQ benchmark rerun should wait until the symbol repair and any other high-priority data-quality fixes are complete.
```
