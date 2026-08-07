# Agent Replay Boundary Trace Inspector Validation

**Date:** 2026-07-31
**Branch:** feature/structured-execution-plan-handoff

## Automated Tests

- `python -m pytest tests/test_agent_replay_ui_models.py tests/test_agent_replay_ui_loader.py tests/test_agent_replay_ui_boundary_formatters.py tests/test_agent_replay_ui_static.py tests/test_agent_replay_ui_browser.py tests/test_agent_replay_ui_server.py -q`: PASS, 116 passed.
- `python -m ruff check lumibot/components/agents/replay_ui/models.py lumibot/components/agents/replay_ui/loader.py lumibot/components/agents/replay_ui/boundary_formatters.py lumibot/components/agents/replay_ui/server.py tests/test_agent_replay_ui_models.py tests/test_agent_replay_ui_loader.py tests/test_agent_replay_ui_boundary_formatters.py tests/test_agent_replay_ui_static.py tests/test_agent_replay_ui_browser.py tests/test_agent_replay_ui_server.py`: PASS. Ruff printed the existing top-level linter configuration deprecation warning.

## Real Trace Checked

- Trace root: `D:\Lumibot\artifacts\ai_trading_team_provider_benchmarks\20260729_192401_680014_13876\openai_gpt-5.4-mini\cache\agent_runtime`
- Strategy: `AITradingTeamBullBearLeveragedETFStrategy`
- Backtest run: `agent_runtime`
- System run: `backtesting|2024-09-05T09:30:00-04:00|AITradingTeamBullBearLeveragedETFStrategy`
- Agent checked: `bear`

## Dataset/API Findings

- `/api/dataset` returned HTTP 200.
- Selected agent boundary trace contained 3 model turns and 72 boundary events.
- The selected agent contained all B01-B10 transitions:
  - `B01_PROVIDER_TO_LITELLM`
  - `B02_LITELLM_TO_ADK`
  - `B03_ADK_TO_FUNCTION_TOOL`
  - `B04_FUNCTION_TOOL_TO_WRAPPER`
  - `B05_WRAPPER_TO_PYTHON_TOOL`
  - `B06_PYTHON_TOOL_TO_WRAPPER`
  - `B07_WRAPPER_TO_FUNCTION_TOOL`
  - `B08_FUNCTION_TOOL_TO_ADK`
  - `B09_ADK_TO_LITELLM`
  - `B10_LITELLM_TO_PROVIDER`
- A sidecar-backed event was available: `30ac5e3d275050781c71b84b`.
- `/api/boundary-payload/30ac5e3d275050781c71b84b` returned HTTP 200.

## Browser UI Findings

- Started a local Flask replay UI server using the real trace root.
- Browser loaded the Agent Replay UI successfully.
- Strategy selector showed `AITradingTeamBullBearLeveragedETFStrategy`.
- Clicked the `bear` agent in the workflow graph.
- `LLM <-> Tool Boundary Trace` was visible.
- B-step rows were visible, including:
  - `B09_ADK_TO_LITELLM`
  - `B10_LITELLM_TO_PROVIDER`
  - `B01_PROVIDER_TO_LITELLM`
  - `B03_ADK_TO_FUNCTION_TOOL`
- Expanded details and clicked `Load full sidecar payload`.
- Browser requested `/api/boundary-payload/30ac5e3d275050781c71b84b`.
- UI displayed `Full sidecar payload loaded`.

## Notes

- No trading behavior was changed by this feature.
- Old traces without `boundary_trace` remain supported by the legacy fallback message.
- Sidecar payload loading uses event IDs only; the browser does not send filesystem paths.
- Path traversal, absolute path, missing sidecar, and symlink/junction trace-root escape cases are covered by automated tests.

