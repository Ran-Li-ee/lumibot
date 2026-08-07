# Agent Prompt Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild agent prompt assembly into modular, non-overlapping prompt layers so each agent receives only the runtime, strategy, tool, role, output, and sizing instructions it actually needs.

**Architecture:** Add focused prompt module builders in `lumibot/components/agents/manager.py`, route modules by `base_system_prompt_mode` and available tools, then update the growth/decision/execution strategy prompts and tool descriptions to align with the new architecture. Validate with static effective-prompt tests, tool-description tests, existing strategy tests, and a one-day trace run.

**Tech Stack:** Python, pytest, Lumibot `AgentManager` / `AgentHandle`, built-in `BoundTool` descriptions, existing benchmark runner and Agent Replay trace artifacts.

---

## File Structure

### Modify

- `lumibot/components/agents/manager.py`
  - Owns `AgentHandle._base_system_prompt()`, `_execution_minimal_base_system_prompt()`, and `_compose_system_prompt()`.
  - Add small private helpers for prompt modules.
  - Remove broad default investor/tool evidence stack language from the base prompt.
  - Ensure historical/DuckDB policy is included only for research agents with the relevant tools.

- `lumibot/components/agents/runtime.py`
  - Owns runtime-side `_instruction_for()`, which currently appends generic tool guidance and repeats history/DuckDB guidance.
  - Align runtime instruction assembly with manager-side prompt policy.
  - Avoid sending historical/DuckDB guidance to agents whose request does not need it.

- `lumibot/components/agents/builtins.py`
  - Owns model-facing tool descriptions.
  - Rewrite `market_load_history_table`, `market_load_history_tables_summary`, and `duckdb_query` descriptions so they reinforce summary-first behavior and targeted follow-up.

- `lumibot/example_strategies/ai_trading_team_growth_execution_test.py`
  - Owns current growth/decision/execution strategy-specific prompts.
  - Reduce agent prompts so role, output, and sizing responsibility are cleanly separated.
  - Do not duplicate base runtime/tool policy text inside strategy-specific prompts.

- `tests/test_agent_manager.py`
  - Add static effective-prompt tests for module routing and banned prompt sections.

- `tests/test_ai_trading_team_growth_execution_test.py`
  - Update strategy prompt tests to match the new modular architecture.
  - Assert each agent receives only relevant prompt material.

- `tests/test_agent_runtime_provider_keys.py` or `tests/test_agent_manager.py`
  - Use whichever file already covers runtime request construction most directly after inspecting the local tests during implementation.
  - Add or update tests for `_instruction_for()` if needed.

- `tests/test_agent_history_summary.py` or `tests/test_agent_manager.py`
  - Add tool-description assertions for summary-first / targeted follow-up language.

### Do Not Modify Unless Required By Tests

- `lumibot/components/agents/replay_ui/*`
  - UI should continue consuming trace request fields. This feature changes prompt contents, not the UI schema.

- `lumibot/components/agents/duckdb_tools.py`
  - DuckDB behavior should remain unchanged.

- Order validation and execution code in `ai_trading_team_growth_execution_test.py`
  - No-negative-cash and structured-plan parsing are out of scope.

---

## Task 1: Add Effective-Prompt Tests For Base Runtime Modules

**Files:**
- Modify: `tests/test_agent_manager.py`
- Later implementation: `lumibot/components/agents/manager.py`

- [ ] **Step 1: Write failing tests for the default base prompt**

Add these tests near `test_agent_handle_uses_default_base_prompt_by_default()`:

```python
def test_default_base_prompt_contains_only_global_runtime_rules():
    handle = AgentHandle(
        manager=DummyManager(),
        name="research_agent",
        system_prompt="Research prompt.",
        default_model="test-model",
        runtime=object(),
    )

    prompt = handle._compose_system_prompt({"mode": "backtesting"})
    prompt_lower = prompt.lower()

    for required_phrase in (
        "runtime context is the ground truth",
        "tool outputs outrank model memory",
        "context pruning is a normal runtime mechanism",
        "current simulated datetime",
        "hard wall",
        "do not use future data",
        "Research prompt.",
    ):
        assert required_phrase.lower() in prompt_lower

    for forbidden_phrase in (
        "default investor policy",
        "prefer no trade over a weak trade",
        "load recent price history for any asset",
        "duckdb analysis",
        "do not submit a material equity order until",
        "sec financial/filing tools",
        "position sizing and order execution",
    ):
        assert forbidden_phrase not in prompt_lower
```

- [ ] **Step 2: Write failing tests for the execution-minimal base prompt**

Update `test_agent_handle_execution_minimal_base_prompt_omits_decision_policy()` or add a new adjacent test:

```python
def test_execution_minimal_base_prompt_contains_no_research_or_history_policy():
    handle = AgentHandle(
        manager=DummyManager(),
        name="execution_agent",
        system_prompt="Execution prompt.",
        default_model="test-model",
        runtime=object(),
        base_system_prompt_mode="execution_minimal",
    )

    prompt = handle._compose_system_prompt({"mode": "backtesting"})
    prompt_lower = prompt.lower()

    for required_phrase in (
        "order execution agent",
        "execute only the provided execution_plan",
        "do not perform investment research",
        "runtime context is the ground truth",
        "current simulated datetime",
        "Execution prompt.",
    ):
        assert required_phrase.lower() in prompt_lower

    for forbidden_phrase in (
        "default investor policy",
        "market_load_history_tables_summary",
        "market_load_history_table",
        "duckdb_query",
        "computed summaries",
        "98% cash rule",
        "cash_buffer_pct",
        "relative-strength",
    ):
        assert forbidden_phrase not in prompt_lower
```

- [ ] **Step 3: Run tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_manager.py::test_default_base_prompt_contains_only_global_runtime_rules tests\test_agent_manager.py::test_execution_minimal_base_prompt_contains_no_research_or_history_policy -q
```

Expected:

- Both tests fail because the current base prompt still contains `DEFAULT INVESTOR POLICY`, broad history loading, and broad tool evidence stack language.

- [ ] **Step 4: Refactor manager prompt modules**

In `lumibot/components/agents/manager.py`, replace the body of `_base_system_prompt()` with small module builders.

Add helper methods inside `AgentHandle` near `_base_system_prompt()`:

```python
    def _global_runtime_rules_prompt(self, runtime_context: dict[str, Any]) -> str:
        mode = runtime_context.get("mode") or "live"
        lines = [
            "You are operating as a trading agent inside LumiBot.",
            "Runtime Context JSON is the ground truth for current account state, mode, datetime, timezone, positions, cash, portfolio value, recent orders, and recent trades.",
            "Tool outputs outrank model memory. Ground claims in tool results or runtime context instead of unsupported prior knowledge.",
            "Do not invent facts that are not present in runtime context or tool output.",
            "Context pruning is a normal runtime mechanism used to manage context size.",
            "If older tool outputs are marked as pruned, do not treat pruning itself as evidence failure.",
            "Base your conclusion on the evidence still visible in context, and call targeted tools again if a pruned result is essential.",
        ]
        if mode == "backtesting":
            lines.extend(
                [
                    "",
                    "BACKTESTING SAFETY RULES:",
                    "The current simulated datetime is a hard wall.",
                    "Do not use future data.",
                    "Only use bars, news, macro data, filings, prices, positions, and events available at or before the current simulated datetime.",
                    "If a tool has any parameter that controls a time range, date filter, or temporal bound, set it so no data after the current simulated datetime can be returned.",
                    "If a tool response seems to include future timestamps, treat that as suspicious and do not rely on those records.",
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "LIVE TRADING RULES:",
                    "Act on the current visible account, broker, order, and market state.",
                ]
            )
        return "\n".join(lines).strip()
```

Then make `_base_system_prompt()` return:

```python
    def _base_system_prompt(self, runtime_context: dict[str, Any]) -> str:
        return self._global_runtime_rules_prompt(runtime_context)
```

Update `_execution_minimal_base_system_prompt()` to use equivalent wording and avoid historical/research references:

```python
    def _execution_minimal_base_system_prompt(self, runtime_context: dict[str, Any]) -> str:
        mode = runtime_context.get("mode") or "live"
        lines = [
            "You are operating as an order execution agent inside LumiBot.",
            "Runtime Context JSON is the ground truth for current account state, mode, datetime, timezone, positions, cash, portfolio value, recent orders, and recent trades.",
            "Tool outputs outrank model memory.",
            "Execute only the provided execution_plan.",
            "Do not perform investment research, do not re-rank candidates, do not substitute symbols, and do not change the plan.",
            "Do not add, remove, replace, or reorder execution_plan.orders.",
            "Before submitting any order, inspect current positions, available cash, portfolio value, open orders, and the latest price for the ordered asset.",
            "Execute execution_plan.orders in ascending sequence order.",
            "When switching from one asset to another, submit the sell or reduce order before the replacement buy order when that is the sequence provided.",
            "Use whole-share quantities unless the tool and asset type explicitly support fractional quantities.",
            "Block or pause only for execution-level blockers such as missing required order fields, insufficient cash after required prior sells, broker/tool rejection, unavailable price data, or invalid order parameters.",
            "Report each order sequence as submitted or blocked.",
            "Finish every run with a short summary sentence starting with RESULT: that explains what execution action you took.",
        ]
        if mode == "backtesting":
            lines.extend(
                [
                    "",
                    "BACKTESTING SAFETY RULES:",
                    "The current simulated datetime is a hard wall.",
                    "Do not use future data.",
                    "If a tool has any parameter that controls a time range, date filter, or temporal bound, set it so no data after the current simulated datetime can be returned.",
                    "If a tool response seems to include future timestamps, treat that as suspicious and do not rely on those records.",
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "LIVE TRADING RULES:",
                    "Act on the current visible account, broker, order, and market state.",
                ]
            )
        return "\n".join(lines).strip()
```

- [ ] **Step 5: Run tests and verify they pass**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_manager.py::test_default_base_prompt_contains_only_global_runtime_rules tests\test_agent_manager.py::test_execution_minimal_base_prompt_contains_no_research_or_history_policy -q
```

Expected:

- Both tests pass.

- [ ] **Step 6: Run nearby manager tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_manager.py -q
```

Expected:

- Existing manager tests pass, except tests with old prompt expectations that must be updated to the new architecture in the next task.

- [ ] **Step 7: Commit**

```powershell
git add lumibot/components/agents/manager.py tests/test_agent_manager.py
git commit -m "refactor: slim base agent runtime prompts"
```

---

## Task 2: Add Tool Policy Modules And Route Them By Agent Needs

**Files:**
- Modify: `tests/test_agent_manager.py`
- Modify: `lumibot/components/agents/manager.py`

- [ ] **Step 1: Write failing tests for historical policy routing**

Add tests to `tests/test_agent_manager.py` near the prompt tests:

```python
def test_research_agent_with_history_tools_receives_summary_first_policy():
    def market_load_history_table():
        return None

    def market_load_history_tables_summary():
        return None

    def duckdb_query():
        return None

    handle = AgentHandle(
        manager=DummyManager(),
        name="growth_agent",
        system_prompt="Growth role.",
        default_model="test-model",
        runtime=object(),
        tools=[market_load_history_table, market_load_history_tables_summary, duckdb_query],
        include_builtin_tools=False,
    )

    prompt = handle._compose_system_prompt(
        {"mode": "backtesting"},
        bound_tools=handle._ensure_bound_tools(),
    )
    prompt_lower = prompt.lower()

    assert "price/history tool policy" in prompt_lower
    assert "market_load_history_tables_summary is the default tool for multi-symbol price-history comparison" in prompt_lower
    assert "market_load_history_table is targeted single-symbol follow-up" in prompt_lower
    assert "duckdb_query is targeted follow-up only" in prompt_lower
    assert "do not load raw history tables for every symbol" in prompt_lower
```

- [ ] **Step 2: Write failing tests that execution agents do not receive historical policy**

Add:

```python
def test_execution_agent_with_order_tools_does_not_receive_history_policy():
    def orders_submit_order():
        return None

    def market_last_price():
        return None

    handle = AgentHandle(
        manager=DummyManager(),
        name="execution_agent",
        system_prompt="Execution role.",
        default_model="test-model",
        runtime=object(),
        tools=[orders_submit_order, market_last_price],
        include_builtin_tools=False,
        base_system_prompt_mode="execution_minimal",
    )

    prompt = handle._compose_system_prompt(
        {"mode": "backtesting"},
        bound_tools=handle._ensure_bound_tools(),
    )
    prompt_lower = prompt.lower()

    assert "execution tool policy" in prompt_lower
    assert "orders_submit_order executes explicit order fields from execution_plan.orders" in prompt_lower
    for forbidden_phrase in (
        "price/history tool policy",
        "market_load_history_tables_summary",
        "market_load_history_table",
        "duckdb_query",
        "computed summaries",
    ):
        assert forbidden_phrase not in prompt_lower
```

- [ ] **Step 3: Run tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_manager.py::test_research_agent_with_history_tools_receives_summary_first_policy tests\test_agent_manager.py::test_execution_agent_with_order_tools_does_not_receive_history_policy -q
```

Expected:

- The research test fails because the exact new policy does not exist yet.
- The execution test may fail because the execution policy does not exist yet or because historical guidance is still routed too broadly.

- [ ] **Step 4: Implement tool policy helpers**

In `lumibot/components/agents/manager.py`, add helper methods inside `AgentHandle`:

```python
    def _account_tool_policy_prompt(self, tool_names: set[str]) -> str:
        lines = ["ACCOUNT TOOL POLICY:"]
        if "account_positions" in tool_names:
            lines.append("Use account_positions to inspect current holdings.")
        if "account_portfolio" in tool_names:
            lines.append("Use account_portfolio to inspect cash and portfolio value.")
        if len(lines) == 1:
            return ""
        return "\n".join(lines)

    def _history_tool_policy_prompt(self, tool_names: set[str]) -> str:
        has_summary = "market_load_history_tables_summary" in tool_names
        has_single = "market_load_history_table" in tool_names
        has_duckdb = "duckdb_query" in tool_names
        if not (has_summary or has_single or has_duckdb):
            return ""
        lines = ["PRICE/HISTORY TOOL POLICY:"]
        if has_summary:
            lines.append("market_load_history_tables_summary is the default tool for multi-symbol price-history comparison.")
        if has_single:
            lines.append("market_load_history_table is targeted single-symbol follow-up when summary evidence is missing, contradictory, or insufficient.")
        if has_summary and has_single:
            lines.append("Do not load raw history tables for every symbol when summary rankings already answer the task.")
        if has_duckdb:
            lines.append("duckdb_query is targeted follow-up only when computed summaries and rankings do not answer a specific question.")
            lines.append("Do not treat DuckDB as a required step in every research workflow.")
        return "\n".join(lines)

    def _execution_tool_policy_prompt(self, tool_names: set[str]) -> str:
        execution_tools = {"orders_submit_order", "orders_cancel_order", "orders_modify_order", "orders_open_orders"}
        if not (tool_names & execution_tools):
            return ""
        lines = [
            "EXECUTION TOOL POLICY:",
            "Execution tools are not research tools.",
        ]
        if "orders_submit_order" in tool_names:
            lines.append("orders_submit_order executes explicit order fields from execution_plan.orders.")
        if "orders_open_orders" in tool_names:
            lines.append("Use orders_open_orders to inspect outstanding orders before submitting new orders.")
        if "orders_cancel_order" in tool_names:
            lines.append("Use orders_cancel_order only for explicit execution-level order management.")
        if "orders_modify_order" in tool_names:
            lines.append("Use orders_modify_order only for explicit execution-level order management.")
        return "\n".join(lines)
```

Then update `_compose_system_prompt()`:

```python
        if bound_tools:
            tool_names = {tool.name for tool in bound_tools}
            account_policy = self._account_tool_policy_prompt(tool_names)
            if account_policy:
                prompt_parts.append(account_policy)
            if self.base_system_prompt_mode != "execution_minimal":
                history_policy = self._history_tool_policy_prompt(tool_names)
                if history_policy:
                    prompt_parts.append(history_policy)
            execution_policy = self._execution_tool_policy_prompt(tool_names)
            if execution_policy:
                prompt_parts.append(execution_policy)
```

Remove the old `HISTORICAL DATA TOOL PRIORITY` block from `_compose_system_prompt()`.

- [ ] **Step 5: Run tests and verify they pass**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_manager.py::test_research_agent_with_history_tools_receives_summary_first_policy tests\test_agent_manager.py::test_execution_agent_with_order_tools_does_not_receive_history_policy -q
```

Expected:

- Both tests pass.

- [ ] **Step 6: Run manager prompt tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_manager.py -q
```

Expected:

- Manager tests pass after updating old expectations that explicitly required old investor-policy wording.

- [ ] **Step 7: Commit**

```powershell
git add lumibot/components/agents/manager.py tests/test_agent_manager.py
git commit -m "feat: route modular agent tool policies"
```

---

## Task 3: Align Runtime `_instruction_for()` With Prompt Modules

**Files:**
- Modify: `tests/test_agent_runtime_provider_keys.py` or `tests/test_agent_manager.py`
- Modify: `lumibot/components/agents/runtime.py`

- [ ] **Step 1: Locate the best existing runtime instruction test**

Run:

```powershell
rg -n "_instruction_for|General rules|Available tool names|computed summaries|RuntimeRequest" tests
```

Use the file with existing `RuntimeRequest` helpers. If none exists, add a small unit test in `tests/test_agent_runtime_provider_keys.py` using existing imports from that file.

- [ ] **Step 2: Write failing runtime instruction test**

Add a test equivalent to this, adapting imports and constructors to the existing test helpers:

```python
def test_runtime_instruction_does_not_reintroduce_history_policy_for_execution_agent():
    runtime = AgentRuntime()
    request = RuntimeRequest(
        agent_name="execution_agent",
        model="test-model",
        system_prompt="Execution prompt.",
        task_prompt="Execute.",
        context={},
        runtime_context={"mode": "backtesting"},
        bound_tools=[
            BoundTool(name="orders_submit_order", description="submit", function=lambda: None),
            BoundTool(name="market_last_price", description="price", function=lambda: None),
        ],
    )

    instruction = runtime._instruction_for(request)
    instruction_lower = instruction.lower()

    assert "available tool names for this run" in instruction_lower
    assert "orders_submit_order" in instruction_lower
    assert "market_load_history_table" not in instruction_lower
    assert "duckdb_query" not in instruction_lower
    assert "computed summaries" not in instruction_lower
```

If `RuntimeRequest` requires additional fields in the current code, copy the minimal constructor style from nearby tests rather than inventing a new helper.

- [ ] **Step 3: Write failing runtime history policy test for research agents**

Add:

```python
def test_runtime_instruction_uses_short_history_policy_for_research_agents():
    runtime = AgentRuntime()
    request = RuntimeRequest(
        agent_name="growth_agent",
        model="test-model",
        system_prompt="Growth prompt.",
        task_prompt="Rank.",
        context={},
        runtime_context={"mode": "backtesting"},
        bound_tools=[
            BoundTool(name="market_load_history_tables_summary", description="summary", function=lambda: None),
            BoundTool(name="market_load_history_table", description="history", function=lambda: None),
            BoundTool(name="duckdb_query", description="sql", function=lambda: None),
        ],
    )

    instruction = runtime._instruction_for(request)
    instruction_lower = instruction.lower()

    assert "available tool names for this run" in instruction_lower
    assert "use tools for structured data and trading actions" in instruction_lower
    assert "computed summaries" not in instruction_lower
    assert "do not request raw historical rows by default" not in instruction_lower
```

This test intentionally asserts the runtime layer no longer repeats the history policy because manager-side prompt assembly owns that policy.

- [ ] **Step 4: Run tests and verify they fail**

Run the exact tests added in Step 2 and Step 3.

Expected:

- At least the research test fails because `_instruction_for()` currently repeats computed-summary guidance.

- [ ] **Step 5: Simplify `_instruction_for()`**

In `lumibot/components/agents/runtime.py`, update `_instruction_for()` to keep only runtime execution scaffolding:

```python
    def _instruction_for(self, request: RuntimeRequest) -> str:
        lines = [request.system_prompt.strip()]
        lines.append("")
        lines.append("General rules:")
        lines.append("- Use tools for structured data and trading actions.")
        if request.bound_tools:
            available_tool_names = {tool.name for tool in request.bound_tools}
            tool_names = ", ".join(sorted(available_tool_names))
            lines.append(f"- Available tool names for this run: {tool_names}.")
            lines.append("- Only call tool names that appear in the available tool list for this run.")
        lines.append("- Return a short final summary after you finish using tools.")
        return "\n".join(lines).strip()
```

Do not add history or DuckDB guidance here; that belongs to manager prompt modules.

- [ ] **Step 6: Run runtime instruction tests**

Run the exact tests added in Step 2 and Step 3.

Expected:

- Both pass.

- [ ] **Step 7: Run runtime-related test file**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_runtime_provider_keys.py -q
```

If the tests were added to a different file, run that file instead.

Expected:

- Pass after updating old expectations.

- [ ] **Step 8: Commit**

```powershell
git add lumibot/components/agents/runtime.py tests/test_agent_runtime_provider_keys.py tests/test_agent_manager.py
git commit -m "refactor: keep runtime instructions prompt-neutral"
```

---

## Task 4: Rewrite History Tool Descriptions To Match Summary-First Architecture

**Files:**
- Modify: `tests/test_agent_history_summary.py` or `tests/test_agent_manager.py`
- Modify: `lumibot/components/agents/builtins.py`

- [ ] **Step 1: Find current built-in tool description tests**

Run:

```powershell
rg -n "market_load_history_table|market_load_history_tables_summary|duckdb_query|description" tests
```

Add tests to the file that already inspects built-in tools. If no suitable file exists, use `tests/test_agent_history_summary.py`.

- [ ] **Step 2: Write failing tests for history tool descriptions**

Add:

```python
def test_history_tool_descriptions_are_summary_first():
    tools = {tool.name: tool for tool in builtin_tools_for_strategy(DummyStrategy(), DummyManager())}

    single = tools["market_load_history_table"].description.lower()
    multi = tools["market_load_history_tables_summary"].description.lower()
    duckdb = tools["duckdb_query"].description.lower()

    assert "targeted single-symbol follow-up" in single
    assert "not the default tool for every symbol" in single
    assert "summary-first" in single
    assert "cross-symbol comparison" in multi
    assert "default tool for multi-symbol" in multi
    assert "targeted follow-up" in duckdb
    assert "computed summaries or rankings are insufficient" in duckdb
    assert "load a table first with market_load_history_table, then analyze it here" not in duckdb
```

Adapt `builtin_tools_for_strategy(DummyStrategy(), DummyManager())` to the real helper names in the chosen test file. If needed, instantiate via the existing built-in registry path used by current tests.

- [ ] **Step 3: Run the test and verify it fails**

Run the single new test.

Expected:

- It fails because `duckdb_query` still says "Load a table first..." and `market_load_history_table` does not yet say it is not the default for every symbol.

- [ ] **Step 4: Update tool descriptions**

In `lumibot/components/agents/builtins.py`, edit descriptions:

For `market_load_history_table`, include:

```text
This is a summary-first targeted single-symbol follow-up tool, not the default tool for every symbol in a universe ranking.
Use it when market_load_history_tables_summary is missing, contradictory, or insufficient for one symbol.
```

For `market_load_history_tables_summary`, include:

```text
This is the default tool for multi-symbol price-history comparison and universe ranking.
Use it before per-symbol raw history tables for common cross-symbol ranking tasks.
```

For `duckdb_query`, replace the old "Load a table first..." sentence with:

```text
Use this as targeted follow-up only when computed summaries or rankings are insufficient for a specific question and a relevant DuckDB table is already available.
```

Keep the useful schema-safety details:

- exact column names
- `Date` vs `datetime`
- table aliases for joins
- read-only SQL

- [ ] **Step 5: Run tool-description test**

Run the new test.

Expected:

- Pass.

- [ ] **Step 6: Run relevant tool/history tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_history_summary.py -q
```

If the new test lives elsewhere, run that file too.

Expected:

- Pass after updating old text expectations.

- [ ] **Step 7: Commit**

```powershell
git add lumibot/components/agents/builtins.py tests/test_agent_history_summary.py tests/test_agent_manager.py
git commit -m "docs: align history tool descriptions with summary-first policy"
```

---

## Task 5: Clean Growth / Decision / Execution Strategy Prompts

**Files:**
- Modify: `tests/test_ai_trading_team_growth_execution_test.py`
- Modify: `lumibot/example_strategies/ai_trading_team_growth_execution_test.py`

- [ ] **Step 1: Write failing tests for growth prompt boundaries**

Update or add to `tests/test_ai_trading_team_growth_execution_test.py`:

```python
def test_growth_prompt_is_research_only_and_summary_first():
    _strategy_module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    for name in ("growth_agent", "decision_agent", "execution_agent"):
        agent_manager._agents[name] = RecordingAgent(name)
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.initialize()
    strategy.on_trading_iteration()

    created = [item for item in agent_manager.created if item["name"] == "growth_agent"][0]
    prompt_text = json.dumps(created) + json.dumps(agent_manager["growth_agent"].calls)
    prompt_lower = prompt_text.lower().replace('\\"', '"')

    for required_phrase in (
        "rank the etf universe",
        "use computed summary metrics as default evidence",
        "identify the strongest candidate",
        "do not place orders",
        "do not calculate final executable share quantities",
        "do not exhaustively load raw history tables",
    ):
        assert required_phrase in prompt_lower

    for forbidden_phrase in (
        "execution_plan",
        "schema_version",
        "98% cash rule",
        "cash_buffer_pct",
        "orders_submit_order",
    ):
        assert forbidden_phrase not in prompt_lower
```

- [ ] **Step 2: Write failing tests for decision prompt boundaries**

Add or update:

```python
def test_decision_prompt_receives_contract_and_sizing_but_not_history_research_policy():
    _strategy_module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    for name in ("growth_agent", "decision_agent", "execution_agent"):
        agent_manager._agents[name] = RecordingAgent(name)
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.initialize()
    strategy.on_trading_iteration()

    created = [item for item in agent_manager.created if item["name"] == "decision_agent"][0]
    prompt_text = json.dumps(created) + json.dumps(agent_manager["decision_agent"].calls)
    prompt_lower = prompt_text.lower().replace('\\"', '"')

    for required_phrase in (
        "convert growth_report and current account state",
        "return only one valid json object",
        "execution_plan",
        "numeric share quantities",
        "use the 98% cash rule only as an internal sizing rule",
        "do not output cash_buffer_pct",
        "call account_positions and account_portfolio",
        "call market_last_price when sizing buy orders",
    ):
        assert required_phrase in prompt_lower

    for forbidden_phrase in (
        "market_load_history_tables_summary is the default",
        "market_load_history_table is targeted",
        "duckdb_query is targeted",
        "do not exhaustively load raw history tables",
        "orders_submit_order executes",
    ):
        assert forbidden_phrase not in prompt_lower
```

- [ ] **Step 3: Write failing tests for execution prompt boundaries**

Extend `test_execution_prompt_treats_execution_plan_as_authoritative()` or add:

```python
def test_execution_prompt_is_execution_only():
    _strategy_module, strategy_class = load_strategy_module()
    agent_manager = RecordingAgentManager()
    for name in ("growth_agent", "decision_agent", "execution_agent"):
        agent_manager._agents[name] = RecordingAgent(name)
    strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)

    strategy.initialize()
    strategy.on_trading_iteration()

    created = [item for item in agent_manager.created if item["name"] == "execution_agent"][0]
    prompt_text = json.dumps(created) + json.dumps(agent_manager["execution_agent"].calls)
    prompt_lower = prompt_text.lower().replace('\\"', '"')

    for required_phrase in (
        "execute only the provided execution_plan",
        "execution_plan.orders",
        "authoritative",
        "do not re-rank",
        "do not substitute",
        "execution-level blockers",
        "submit the explicit numeric share quantities",
    ):
        assert required_phrase in prompt_lower

    for forbidden_phrase in (
        "growth_report",
        "relative-strength account management",
        "market_load_history_tables_summary",
        "market_load_history_table",
        "duckdb_query",
        "98% cash rule",
        "cash_buffer_pct",
        "rank the etf universe",
    ):
        assert forbidden_phrase not in prompt_lower
```

- [ ] **Step 4: Run the three tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_execution_test.py::test_growth_prompt_is_research_only_and_summary_first tests\test_ai_trading_team_growth_execution_test.py::test_decision_prompt_receives_contract_and_sizing_but_not_history_research_policy tests\test_ai_trading_team_growth_execution_test.py::test_execution_prompt_is_execution_only -q
```

Expected:

- At least one fails because current prompts still duplicate older module responsibilities or lack the exact architecture wording.

- [ ] **Step 5: Update growth agent system and task prompts**

In `lumibot/example_strategies/ai_trading_team_growth_execution_test.py`, replace growth agent `system_prompt` with:

```python
            system_prompt=(
                "Growth agent role: rank the ETF universe for relative-strength account management. "
                "Use computed summary metrics as default evidence. Identify the strongest candidate and compare "
                "the current ETF holding, if any, against that candidate. State whether the current holding should "
                "be kept, reduced, or replaced. Do not place orders. Do not calculate final executable share "
                "quantities. Do not exhaustively load raw history tables when summary evidence is sufficient."
            ),
```

Replace growth task prompt with:

```python
            task_prompt=(
                "Use the date and universe to rank the ETF universe from current evidence. Prefer compact computed "
                "summaries and rankings for price-history evidence. Identify the strongest candidate, summarize the "
                "main evidence, compare any current ETF holding against the strongest candidate, and make a research "
                "recommendation. Finish with RESULT."
            ),
```

- [ ] **Step 6: Update decision agent system and task prompts**

In the same file, replace decision `system_prompt` with a concise role + contract + sizing prompt:

```python
            system_prompt=(
                "Decision agent role: convert growth_report and current account state into a concrete account "
                "management decision and strict execution_plan. Do not place orders and do not perform broad ETF "
                "research again. Choose exactly one decision.type from hold, buy, rotate, reduce, close. "
                "Return only one valid JSON object with top-level fields decision and execution_plan. Do not include "
                "markdown, RESULT text, or prose after the JSON. decision must include type, from, to, and "
                "reason_brief. execution_plan must include schema_version, intent, and orders. intent must be one of "
                "hold, enter_position, rotate, reduce_position, close_position. Each executable order must include "
                "sequence, symbol, side, quantity_mode, quantity, asset_type, order_type, and time_in_force. Use "
                "numeric share quantities; do not use full_position, current_position, max_affordable_cash, or "
                "max_affordable_after_prior_sells. Before a non-hold decision, call account_positions and "
                "account_portfolio. Call market_last_price when sizing buy orders. For selling all or part of a "
                "position, calculate the share quantity from account tool output. For buy sizing, choose order_type "
                "first, choose sizing_price based on order_type, use the 98% cash rule only as an internal sizing "
                "rule, and output only the final numeric share quantity. For market buys, use a conservative "
                "sizing_price based on available price evidence; it may be higher than market_last_price in daily "
                "backtests. For limit buys, use limit_price. For stop_limit buys, use stop_limit_price or the final "
                "bounded execution price. Do not output cash_buffer_pct or any buffer field in execution_plan. Never "
                "produce orders that would make cash negative. For rotate decisions, sell the source holding before "
                "buying the destination holding."
            ),
```

Replace decision task prompt with:

```python
            task_prompt=(
                "Use growth_report and current account state to produce the strict decision JSON. If the account "
                "holds only cash or a cash-like position and growth_report identifies a strongest ETF candidate, "
                "choose buy unless a clear blocking reason exists. If the account holds an ETF, compare the holding "
                "against the strongest candidate and choose rotate only when the candidate is clearly stronger and "
                "the planned sell and buy quantities can be expressed as executable numeric share orders. Return "
                "only the JSON object."
            ),
```

- [ ] **Step 7: Update execution agent system and task prompts**

Replace execution `system_prompt` with:

```python
            system_prompt=(
                "Execution agent role: execute only the provided execution_plan object using native execution tools, "
                "especially orders_submit_order. Treat execution_plan.orders as authoritative. Do not read or infer "
                "investment reasons. Do not re-rank candidates, do not substitute symbols, and do not use upstream "
                "research to override the plan. Do not add, remove, replace, or reorder orders. Inspect positions, "
                "portfolio, open orders, and latest prices before submitting orders. Execute orders in ascending "
                "sequence order. Submit the explicit numeric share quantities in execution_plan.orders. Do not "
                "compute semantic sizing. Block or pause only for execution-level blockers. Report each sequence as "
                "submitted or blocked."
            ),
```

Replace execution task prompt with:

```python
            task_prompt=(
                "Execute only the provided execution_plan object. Inspect account state, open orders, positions, and "
                "latest prices, then submit only execution_plan.orders with orders_submit_order. Preserve sequence "
                "order and report each sequence as submitted or blocked. Block solely for execution-level blockers."
            ),
```

- [ ] **Step 8: Run strategy prompt boundary tests**

Run the three tests from Step 4.

Expected:

- Pass after adjusting exact assertions to the final accepted wording.

- [ ] **Step 9: Run the full strategy test file**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_ai_trading_team_growth_execution_test.py -q
```

Expected:

- Pass after updating old prompt expectation tests.

- [ ] **Step 10: Commit**

```powershell
git add lumibot/example_strategies/ai_trading_team_growth_execution_test.py tests/test_ai_trading_team_growth_execution_test.py
git commit -m "refactor: modularize growth execution strategy prompts"
```

---

## Task 6: Full Static Regression Test Run

**Files:**
- No code changes unless tests reveal prompt expectation drift.

- [ ] **Step 1: Run focused prompt/tool tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_manager.py tests\test_agent_history_summary.py tests\test_ai_trading_team_growth_execution_test.py -q
```

Expected:

- All pass.

- [ ] **Step 2: Run runtime/provider tests touched by the plan**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_runtime_provider_keys.py tests\test_agent_trace_ui_script.py tests\test_agent_replay_ui_loader.py -q
```

Expected:

- All pass.
- Replay UI loader should not require schema changes.

- [ ] **Step 3: Inspect effective prompt output manually**

Run a small extraction script:

```powershell
@'
import json
from tests.test_ai_trading_team_growth_execution_test import (
    RecordingAgent,
    RecordingAgentManager,
    load_strategy_module,
    make_strategy_with_agent_manager,
)

_module, strategy_class = load_strategy_module()
agent_manager = RecordingAgentManager()
for name in ("growth_agent", "decision_agent", "execution_agent"):
    agent_manager._agents[name] = RecordingAgent(name)
strategy = make_strategy_with_agent_manager(strategy_class, agent_manager)
strategy.initialize()
strategy.on_trading_iteration()
for created in agent_manager.created:
    print("\\n===", created["name"], "===")
    print(created.get("system_prompt", "")[:1200])
for name in ("growth_agent", "decision_agent", "execution_agent"):
    calls = agent_manager[name].calls
    if calls:
        print("\\nTASK", name)
        print(calls[0]["task_prompt"][:1200])
'@ | .\.venv\Scripts\python.exe -
```

Expected:

- Growth prompt has summary-first research language.
- Decision prompt has JSON contract and sizing.
- Execution prompt is short and execution-only.

- [ ] **Step 4: Commit any test expectation cleanup**

Only if Step 1 or Step 2 required small test expectation changes:

```powershell
git add tests
git commit -m "test: update prompt architecture expectations"
```

If no cleanup was needed, skip this commit.

---

## Task 7: One-Day Backtest Trace Validation

**Files:**
- No code changes unless validation reveals a real defect.

- [ ] **Step 1: Run one-day benchmark**

Run:

```powershell
cd D:\Lumibot
$apiText = Get-Content -Raw -Path project_notes\API.txt
$openai = [regex]::Match($apiText, 'sk-proj-[A-Za-z0-9_-]+').Value
if (-not $openai) { throw 'OPENAI_API_KEY not found in project_notes/API.txt' }
$env:OPENAI_API_KEY = $openai
$env:GOOGLE_API_KEY = 'dummy-google-key-for-openai-model-run'
$env:AI_TRADING_TEAM_MODEL = 'openai/gpt-5.4-mini'
.\.venv\Scripts\python.exe scripts\run_ai_trading_team_examples_benchmark.py --strategy growth-execution-test --start 2024-09-05 --end 2024-09-06 --max-workers 1 --max-run-attempts 1 --agent-run-timeout-seconds 1800
```

Expected:

- Benchmark exits `0`.
- Output includes `"status": "passed"` or a traceable execution-level block.
- New artifact path is printed.

- [ ] **Step 2: Inspect artifact summary**

Replace `<ARTIFACT>` with the printed artifact timestamp directory:

```powershell
Get-Content -Raw artifacts\ai_trading_team_example_benchmarks\<ARTIFACT>\summary.json
```

Expected:

- `growth-execution-test.status` is `passed`.
- `trades_file`, `stats_file`, and account curve file are present.

- [ ] **Step 3: Validate trace warnings and SQL/tool errors**

Run:

```powershell
rg -n "ERROR|Traceback|Exception|Warning|warning|blocked|reject|NEGATIVE|Ambiguous|Binder|failed" artifacts\ai_trading_team_example_benchmarks\<ARTIFACT>\growth-execution-test -S --glob "!*.html" --glob "!*.parquet"
```

Expected:

- No unexpected errors.
- If `blocked` appears, it must be an explicit execution-level blocker worth investigating before continuing.

- [ ] **Step 4: Count tool calls by agent**

Run:

```powershell
@'
import json
from collections import Counter
from pathlib import Path
root = Path(r"artifacts/ai_trading_team_example_benchmarks/<ARTIFACT>/growth-execution-test/cache/agent_runtime/traces")
for trace in sorted(root.glob("*/*.json")):
    d = json.loads(trace.read_text(encoding="utf-8"))
    counts = Counter(c.get("tool_name") for c in d.get("tool_calls", []))
    print(d["agent"], dict(counts))
    print("warnings", d.get("warnings"))
    print("summary", str(d.get("summary", ""))[:300].replace("\\n", " "))
'@ | .\.venv\Scripts\python.exe -
```

Expected:

- `growth_agent` calls `market_load_history_tables_summary`.
- `growth_agent` does not default to 24 `market_load_history_table` calls.
- `duckdb_query` is `0` unless the trace shows a specific targeted follow-up need.
- `decision_agent` uses `account_positions`, `account_portfolio`, and `market_last_price`.
- `execution_agent` uses account/order/price tools only.

- [ ] **Step 5: Validate execution handoff cleanliness**

Run:

```powershell
rg -n "cash_buffer_pct|market_load_history_tables_summary|duckdb_query|growth_report|98% cash rule" artifacts\ai_trading_team_example_benchmarks\<ARTIFACT>\growth-execution-test\cache\agent_runtime\traces\execution_agent -S
```

Expected:

- No `cash_buffer_pct`.
- No `growth_report`.
- No history/DuckDB guidance.
- No `98% cash rule`.

- [ ] **Step 6: Validate UI can read the run**

Start UI:

```powershell
cd D:\Lumibot
.\.venv\Scripts\python.exe scripts\agent_trace_ui.py
```

Open:

```text
http://127.0.0.1:8765
```

Expected:

- New backtest run appears in Strategy / Backtest Run / System Run selectors.
- Workflow graph shows growth -> decision -> execution.
- Agent prompt sections are readable.
- Tool call details still render.

- [ ] **Step 7: Commit validation notes if needed**

If this task produces a short note or updates a project note, commit it:

```powershell
git add docs/superpowers/notes project_notes
git commit -m "docs: record prompt architecture validation run"
```

If no notes are added, skip this commit.

---

## Task 8: Final Review And Branch Hygiene

**Files:**
- No required code changes.

- [ ] **Step 1: Run final focused verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_manager.py tests\test_agent_history_summary.py tests\test_ai_trading_team_growth_execution_test.py tests\test_agent_runtime_provider_keys.py -q
```

Expected:

- All tests pass.

- [ ] **Step 2: Check git status**

Run:

```powershell
git status --short
```

Expected:

- Only intentional files are modified/untracked.
- Existing unrelated dirty files from earlier work remain untouched.

- [ ] **Step 3: Review final diff**

Run:

```powershell
git diff -- lumibot/components/agents/manager.py lumibot/components/agents/runtime.py lumibot/components/agents/builtins.py lumibot/example_strategies/ai_trading_team_growth_execution_test.py tests/test_agent_manager.py tests/test_agent_history_summary.py tests/test_ai_trading_team_growth_execution_test.py tests/test_agent_runtime_provider_keys.py
```

Expected:

- Diff shows prompt architecture changes only.
- No order execution or cash-safety logic changes.
- No UI schema changes unless explicitly justified by tests.

- [ ] **Step 4: Prepare final summary**

Summarize:

- Prompt modules added.
- Old broad prompt language removed.
- Tool descriptions aligned.
- Static tests run and result.
- One-day backtest artifact path and result.
- Tool call count comparison, especially `market_load_history_table`.
- Any remaining non-blocking observations.

Do not claim completion until Step 1 and the one-day backtest validation have fresh passing evidence.

