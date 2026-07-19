# Execution Agent Minimal Base Prompt Design

## Goal

Allow execution-only agents to use a short execution-focused base system prompt instead of the full LumiBot investor/decision base prompt, so the execution agent stays focused on executing a provided `execution_plan` and does not inherit decision-layer language that can discourage rotation or same-day execution.

## Background

The structured execution test strategy now separates the workflow into:

```text
growth_agent -> decision_agent -> execution_agent
```

`decision_agent` produces strict JSON containing `decision` and `execution_plan`. The strategy parses the JSON and passes only `date` plus the parsed `execution_plan` to `execution_agent`.

However, `AgentHandle` still prepends the full LumiBot base system prompt to every agent. That base prompt contains investor-policy and evidence-gathering language that is appropriate for research and decision agents, but noisy for an execution-only agent. Examples include preferring no trade over a weak trade, requiring a thesis before rotation, avoiding overtrading, and only switching when the new opportunity is clearly better. Those rules should influence `decision_agent`, not `execution_agent`.

## Requirements

1. Add a runtime-supported way to choose the base system prompt style per agent.
2. Preserve the existing full base prompt as the default for all existing agents.
3. Add an execution-only minimal base prompt style for order execution agents.
4. Use the execution-only minimal base prompt for `execution_agent` in `AITradingTeamGrowthExecutionTestStrategy`.
5. Ensure the minimal base prompt still preserves hard execution safety:
   - Runtime context and tool output are ground truth.
   - Backtest look-ahead rules still apply.
   - Broker/tool constraints still apply.
   - The agent must inspect account, positions, open orders, and prices before placing orders.
   - The agent must execute `execution_plan.orders` in sequence order.
   - The agent must not re-rank, substitute symbols, add orders, remove orders, or redo investment analysis.
6. Ensure trace output records the minimal base prompt and effective system prompt so the UI can show exactly what was sent to the model.
7. Do not change the default behavior of existing strategies or agents.

## Non-Goals

1. Do not redesign the trading tool API.
2. Do not add new exit tools in this change.
3. Do not change research or decision prompts except where tests require fixture updates.
4. Do not alter broker order fill semantics.
5. Do not force same-day rotation by hardcoding a symbol or decision.

## Proposed Design

Add a base prompt mode to `AgentHandle` and `AgentManager.create`.

```python
base_system_prompt_mode: Literal["default", "execution_minimal"] = "default"
```

Default mode keeps the current full base prompt unchanged.

Execution minimal mode returns a shorter prompt that is purpose-built for execution agents:

```text
You are operating as an order execution agent inside LumiBot.
Use runtime context and tool outputs as ground truth.
Execute only the provided execution_plan.
Do not perform investment research or change the plan.
Inspect account, positions, open orders, and latest prices before submitting orders.
Execute orders in ascending sequence order.
Block only for execution-level blockers.
Preserve backtesting look-ahead safety.
Finish with RESULT.
```

The existing `_compose_system_prompt()` should keep the same high-level structure:

```text
<selected base prompt>

USER SYSTEM PROMPT:
Treat this as the strategy-specific trading objective...
<agent system prompt>
```

This keeps UI display and trace compatibility intact while changing only the selected base prompt content.

## Expected Data Flow

For `execution_agent` in `AITradingTeamGrowthExecutionTestStrategy`:

```text
base_system_prompt = execution minimal base prompt
user_system_prompt = execution_agent-specific prompt
task_prompt = "Execute only the provided execution_plan object..."
context = {"date": ..., "execution_plan": parsed_execution_plan}
```

The trace should show the minimal base prompt in `base_system_prompt` and should not contain default investor policy language in `effective_system_prompt`.

## Testing

Automated tests should verify:

1. Existing agents still use the default full base prompt by default.
2. Agents created with `base_system_prompt_mode="execution_minimal"` use the minimal base prompt.
3. The minimal prompt does not contain decision-layer language such as `DEFAULT INVESTOR POLICY`, `Prefer no trade`, or `Require a real thesis`.
4. `AITradingTeamGrowthExecutionTestStrategy` creates `execution_agent` with execution minimal mode.
5. The real one-day backtest trace for the growth execution strategy shows the execution agent received the minimal base prompt.

## Success Criteria

The change is successful when:

1. Unit tests pass.
2. Ruff passes for changed files.
3. A one-day growth execution backtest produces a trace where `execution_agent` has the minimal base prompt.
4. The UI can show the minimal prompt in the Input Material panel.
5. The system remains ready for the next test goal: verifying whether a multi-day run can produce and execute same-day rotation.
