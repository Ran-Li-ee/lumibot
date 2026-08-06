import asyncio
import gzip
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

import pandas as pd
import pytest

from lumibot.backtesting import PandasDataBacktesting
from lumibot.components.agents import AgentRunResult
from lumibot.entities import Asset, Data
from lumibot.strategies import Strategy


def _utc_iso_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _event(
    kind: str,
    *,
    text: str | None = None,
    tool_name: str | None = None,
    payload: dict | None = None,
    call_id: str | None = None,
    event_id: str | None = None,
    invocation_id: str | None = None,
):
    from lumibot.components.agents import AgentTraceEvent

    return AgentTraceEvent(
        kind=kind,
        text=text,
        tool_name=tool_name,
        payload=payload,
        timestamp=_utc_iso_timestamp(),
        call_id=call_id,
        event_id=event_id,
        invocation_id=invocation_id,
    )


def _invoke_tool(request, events, tool_name: str, **kwargs):
    from lumibot.components.agents.runtime import _wrap_tool_callable

    tool_context = getattr(request, "_test_tool_context", None)
    if tool_context is None:
        tool_context = {
            "agent_name": request.agent_name,
            "model_call_id": request.model_call_id,
            "enforce_order_readiness": True,
            "tool_calls": [],
        }
        setattr(request, "_test_tool_context", tool_context)
    tool_map = {tool.name: _wrap_tool_callable(tool, tool_context) for tool in request.bound_tools}
    events.append(_event("tool_call", tool_name=tool_name, payload=kwargs))
    result = tool_map[tool_name](**kwargs)
    payload = result if isinstance(result, dict) else {"value": result}
    events.append(_event("tool_result", tool_name=tool_name, payload=payload))
    return result


class StockPlanRuntime:
    call_count = 0

    def run(self, request):
        type(self).call_count += 1
        events = [_event("thinking", text="Inspecting current stock state.")]
        positions = _invoke_tool(request, events, "account_positions")
        _invoke_tool(request, events, "account_portfolio")
        _invoke_tool(request, events, "market_last_price", symbol=request.context["symbol"], asset_type="stock")
        table = _invoke_tool(
            request,
            events,
            "market_load_history_table",
            symbol=request.context["symbol"],
            length=request.context["length"],
            timestep=request.context["timestep"],
            asset_type=request.context.get("asset_type", "stock"),
            table_name="stock_history",
        )
        _invoke_tool(
            request,
            events,
            "duckdb_query",
            sql=f"SELECT AVG(close) AS avg_close, MAX(close) AS max_close FROM {table['table_name']}",
        )
        has_position = any(
            pos.get("asset", {}).get("symbol") == request.context["symbol"] and float(pos.get("quantity") or 0) > 0
            for pos in positions["positions"]
            if isinstance(pos, dict) and isinstance(pos.get("asset"), dict)
        )
        if not has_position:
            _invoke_tool(
                request,
                events,
                "orders_submit_order",
                symbol=request.context["symbol"],
                quantity=1,
                side="buy",
                asset_type="stock",
                order_type="market",
            )
            summary = "Bought the stock after inspecting DuckDB history."
        else:
            summary = "Held the current stock position."
        events.append(_event("text", text=summary))
        return AgentRunResult(
            summary=summary,
            model=request.model,
            events=events,
            boundary_trace=request.boundary_collector.export(),
        )


class OptionPlanRuntime:
    call_count = 0

    def run(self, request):
        type(self).call_count += 1
        events = [_event("thinking", text="Inspecting current option state.")]
        positions = _invoke_tool(request, events, "account_positions")
        _invoke_tool(request, events, "account_portfolio")
        _invoke_tool(
            request,
            events,
            "market_last_price",
            symbol=request.context["symbol"],
            asset_type="option",
            expiration=request.context["expiration"],
            strike=request.context["strike"],
            right=request.context["right"],
        )
        table = _invoke_tool(
            request,
            events,
            "market_load_history_table",
            symbol=request.context["symbol"],
            length=request.context["length"],
            timestep=request.context["timestep"],
            asset_type="option",
            expiration=request.context["expiration"],
            strike=request.context["strike"],
            right=request.context["right"],
            table_name="option_history",
        )
        _invoke_tool(
            request,
            events,
            "duckdb_query",
            sql=f"SELECT COUNT(*) AS rows_seen, MIN(close) AS min_close, MAX(close) AS max_close FROM {table['table_name']}",
        )
        has_position = any(
            pos.get("asset", {}).get("asset_type") == "option" and float(pos.get("quantity") or 0) > 0
            for pos in positions["positions"]
            if isinstance(pos, dict)
        )
        if not has_position:
            _invoke_tool(
                request,
                events,
                "orders_submit_order",
                symbol=request.context["symbol"],
                quantity=1,
                side="buy",
                asset_type="option",
                expiration=request.context["expiration"],
                strike=request.context["strike"],
                right=request.context["right"],
                order_type="market",
            )
            summary = "Bought the option contract after checking history."
        else:
            summary = "Held the current option position."
        events.append(_event("text", text=summary))
        return AgentRunResult(summary=summary, model=request.model, events=events)


class MinuteStressRuntime:
    call_count = 0

    def run(self, request):
        type(self).call_count += 1
        events = [_event("thinking", text="Stress-testing minute DuckDB history refresh.")]
        positions = _invoke_tool(request, events, "account_positions")
        _invoke_tool(request, events, "account_portfolio")
        _invoke_tool(request, events, "market_last_price", symbol=request.context["symbol"], asset_type="stock")
        table = _invoke_tool(
            request,
            events,
            "market_load_history_table",
            symbol=request.context["symbol"],
            length=request.context["length"],
            timestep=request.context["timestep"],
            asset_type="stock",
            table_name="minute_window",
        )
        _invoke_tool(
            request,
            events,
            "duckdb_query",
            sql=f"SELECT COUNT(*) AS rows_seen, AVG(close) AS avg_close, MAX(close) AS max_close FROM {table['table_name']}",
        )
        has_position = any(
            pos.get("asset", {}).get("symbol") == request.context["symbol"] and float(pos.get("quantity") or 0) > 0
            for pos in positions["positions"]
            if isinstance(pos, dict) and isinstance(pos.get("asset"), dict)
        )
        if not has_position:
            _invoke_tool(
                request,
                events,
                "orders_submit_order",
                symbol=request.context["symbol"],
                quantity=1,
                side="buy",
                asset_type="stock",
                order_type="market",
            )
            summary = "Bought once during minute stress test."
        else:
            summary = "Held during minute stress test."
        events.append(_event("text", text=summary))
        return AgentRunResult(summary=summary, model=request.model, events=events)


class PromptCaptureRuntime:
    call_count = 0
    last_request = None

    def run(self, request):
        type(self).call_count += 1
        type(self).last_request = request
        summary = "Captured runtime context for inspection."
        return AgentRunResult(
            summary=summary,
            model=request.model,
            events=[_event("text", text=summary)],
        )


class BoundaryResultRuntime:
    def __init__(self):
        self.call_count = 0

    def run(self, request):
        self.call_count += 1
        collector = request.boundary_collector
        collector.record(
            transition="B09_ADK_TO_LITELLM",
            from_module="google_adk",
            to_module="litellm",
            payload={"contents": [{"role": "user", "text": "test"}]},
        )
        return AgentRunResult(
            summary="RESULT: done",
            model=request.model,
            events=[
                _event(
                    "text",
                    text="RESULT: done",
                    call_id="call-1",
                    event_id="event-1",
                    invocation_id="invocation-1",
                )
            ],
            boundary_trace=collector.export(),
        )


class SensitiveReplayRuntime:
    def __init__(self, api_token, bearer_token):
        self.api_token = api_token
        self.bearer_token = bearer_token
        self.call_count = 0

    def run(self, request):
        self.call_count += 1
        collector = request.boundary_collector
        collector.record(
            transition="B09_ADK_TO_LITELLM",
            from_module="google_adk",
            to_module="litellm",
            payload={"model": request.model, "contents": []},
        )
        summary = (
            f"RESULT: used {self.api_token}; "
            f"Authorization: Bearer {self.bearer_token}"
        )
        return AgentRunResult(
            summary=summary,
            model=request.model,
            events=[
                _event(
                    "tool_call",
                    tool_name="secret_tool",
                    payload={
                        "api_key": self.api_token,
                        "Authorization": f"Bearer {self.bearer_token}",
                    },
                    call_id="secret-call",
                ),
                _event(
                    "tool_result",
                    tool_name="secret_tool",
                    payload={
                        "tool_error": True,
                        "error": {
                            "type": "TimeoutError",
                            "message": summary,
                        },
                    },
                    call_id="secret-call",
                ),
                _event("text", text=summary),
            ],
            usage={
                "prompt_tokens": 101,
                "completion_tokens": 23,
                "total_tokens": 124,
                "cache_write_input_tokens": 7,
                "prompt_tokens_details": {"cached_tokens": 80},
                "completion_tokens_details": {"reasoning_tokens": 9},
            },
            boundary_trace=collector.export(),
        )


class BoundaryFailingRuntime:
    def __init__(self, error):
        self.error = error

    def run(self, request):
        collector = request.boundary_collector
        collector.record(
            transition="B09_ADK_TO_LITELLM",
            from_module="google_adk",
            to_module="litellm",
            model_turn_id=collector.start_model_turn(),
            payload={"model": request.model, "contents": []},
        )
        raise self.error


class NoBoundaryResultRuntime:
    def run(self, request):
        return AgentRunResult(
            summary="RESULT: no boundary capture",
            model=request.model,
            events=[_event("text", text="RESULT: no boundary capture")],
        )


class UsageTelemetryRuntime:
    call_count = 0
    last_result = None

    def run(self, request):
        type(self).call_count += 1
        idx = type(self).call_count
        usage = {
            "prompt_tokens": 1000 + idx,
            "completion_tokens": 200 + idx,
            "total_tokens": 1200 + (idx * 2),
            "prompt_tokens_details": {"cached_tokens": 700 + idx},
            "completion_tokens_details": {"reasoning_tokens": 55 + idx},
            "cache_creation_input_tokens": 123 + idx,
            "tool_use_prompt_token_count": 17 + idx,
        }
        events = [
            _event("thinking", text="I should inspect the account first."),
            _event("tool_call", tool_name="account_portfolio", payload={}),
            _event("tool_result", tool_name="account_portfolio", payload={"cash": 10000, "portfolio_value": 10000}),
            _event("usage", payload=usage),
            _event("text", text="RESULT: Held cash after validating telemetry."),
        ]
        result = AgentRunResult(
            summary="RESULT: Held cash after validating telemetry.",
            model=request.model,
            events=events,
            usage=usage,
        )
        type(self).last_result = result
        return result


class FutureTimestampRuntime:
    call_count = 0

    def run(self, request):
        type(self).call_count += 1
        events = [
            _event("tool_call", tool_name="fred_get_series", payload={"series_id": "CPIAUCSL"}),
            _event(
                "tool_result",
                tool_name="fred_get_series",
                payload={"observations": [{"date": "2025-01-08", "realtime_end": "2025-01-08T00:00:00Z"}]},
            ),
            _event("text", text="Used macro data to inspect inflation."),
        ]
        return AgentRunResult(summary="Used macro data to inspect inflation.", model=request.model, events=events)


class InvalidAssetTypeRuntime:
    call_count = 0

    def run(self, request):
        type(self).call_count += 1
        events = [_event("thinking", text="Testing invalid asset type handling.")]
        result = _invoke_tool(
            request,
            events,
            "market_load_history_table",
            symbol=request.context["symbol"],
            length=request.context["length"],
            timestep=request.context["timestep"],
            asset_type=request.context["asset_type"],
            table_name="invalid_history",
        )
        assert result.get("tool_error") is True
        summary = "Tool rejected an invalid asset_type without crashing the strategy."
        events.append(_event("text", text=summary))
        return AgentRunResult(summary=summary, model=request.model, events=events)


class AgentStockBacktestStrategy(Strategy):
    runtime_class = StockPlanRuntime

    def initialize(self):
        self.sleeptime = "1M"
        self.asset = Asset("AGST", Asset.AssetType.STOCK)
        self.last_agent_result = None
        self.agents.create(
            name="research",
            system_prompt="Use DuckDB and buy once if no position exists.",
            default_model="stub-stock",
            tools=[
                self._builtin_positions(),
                self._builtin_portfolio(),
                self._builtin_last_price(),
                self._builtin_history(),
                self._builtin_query(),
                self._builtin_submit(),
            ],
            _runtime=self.runtime_class(),
        )

    def _builtin_positions(self):
        from lumibot.components.agents import BuiltinTools

        return BuiltinTools.account.positions()

    def _builtin_portfolio(self):
        from lumibot.components.agents import BuiltinTools

        return BuiltinTools.account.portfolio()

    def _builtin_history(self):
        from lumibot.components.agents import BuiltinTools

        return BuiltinTools.market.load_history_table()

    def _builtin_last_price(self):
        from lumibot.components.agents import BuiltinTools

        return BuiltinTools.market.last_price()

    def _builtin_query(self):
        from lumibot.components.agents import BuiltinTools

        return BuiltinTools.duckdb.query()

    def _builtin_submit(self):
        from lumibot.components.agents import BuiltinTools

        return BuiltinTools.orders.submit()

    def on_trading_iteration(self):
        self.last_agent_result = self.agents["research"].run(
            context={
                "symbol": self.asset.symbol,
                "length": 3,
                "timestep": "minute",
                "asset_type": self.parameters.get("asset_type", "stock"),
            }
        )


class AgentOptionBacktestStrategy(Strategy):
    runtime_class = OptionPlanRuntime
    expiration = "2025-01-17"
    strike = 100.0
    right = "CALL"

    def initialize(self):
        self.sleeptime = "1M"
        self.option_asset = Asset(
            "AGOP",
            asset_type=Asset.AssetType.OPTION,
            expiration=date(2025, 1, 17),
            strike=self.strike,
            right=self.right,
        )
        from lumibot.components.agents import BuiltinTools

        self.agents.create(
            name="research",
            system_prompt="Use DuckDB and buy the fixed option contract once if no position exists.",
            default_model="stub-option",
            tools=[
                BuiltinTools.account.positions(),
                BuiltinTools.account.portfolio(),
                BuiltinTools.market.last_price(),
                BuiltinTools.market.load_history_table(),
                BuiltinTools.duckdb.query(),
                BuiltinTools.orders.submit(),
            ],
            _runtime=self.runtime_class(),
        )

    def on_trading_iteration(self):
        self.agents["research"].run(
            context={
                "symbol": self.option_asset.symbol,
                "length": 3,
                "timestep": "minute",
                "expiration": self.expiration,
                "strike": self.strike,
                "right": self.right,
            }
        )


class AgentMinuteStressBacktestStrategy(Strategy):
    runtime_class = MinuteStressRuntime

    def initialize(self):
        self.sleeptime = "1M"
        self.asset = Asset("AGMS", Asset.AssetType.STOCK)
        from lumibot.components.agents import BuiltinTools

        self.agents.create(
            name="research",
            system_prompt="Stress test minute DuckDB history refreshes and only buy once.",
            default_model="stub-minute-stress",
            tools=[
                BuiltinTools.account.positions(),
                BuiltinTools.account.portfolio(),
                BuiltinTools.market.last_price(),
                BuiltinTools.market.load_history_table(),
                BuiltinTools.duckdb.query(),
                BuiltinTools.orders.submit(),
            ],
            _runtime=self.runtime_class(),
        )

    def on_trading_iteration(self):
        self.agents["research"].run(
            context={
                "symbol": self.asset.symbol,
                "length": 120,
                "timestep": "minute",
            }
        )


class PromptCaptureStrategy(Strategy):
    runtime_class = PromptCaptureRuntime

    def log_message(self, message, *args, **kwargs):
        logs = list(self.vars.get("captured_logs", []) or [])
        logs.append(str(message))
        self.vars.captured_logs = logs
        return super().log_message(message, *args, **kwargs)

    def initialize(self):
        self.sleeptime = "1D"
        from lumibot.components.agents import BuiltinTools

        self.agents.create(
            name="research",
            system_prompt="Review the current state before deciding whether to trade.",
            default_model="stub-prompt-capture",
            tools=[BuiltinTools.account.positions(), BuiltinTools.account.portfolio()],
            _runtime=self.runtime_class(),
        )

    def on_trading_iteration(self):
        self.agents["research"].run(context={"symbol": "AGST"})


class UsageTelemetryStrategy(Strategy):
    runtime_class = UsageTelemetryRuntime

    def initialize(self):
        self.sleeptime = "1D"
        from lumibot.components.agents import BuiltinTools

        self.agents.create(
            name="research",
            system_prompt="Capture token usage and observability artifacts.",
            default_model="stub-usage-telemetry",
            tools=[BuiltinTools.account.portfolio()],
            _runtime=self.runtime_class(),
        )

    def on_trading_iteration(self):
        self.agents["research"].run(context={"symbol": "AGST"})


class MultiAgentUsageTelemetryStrategy(Strategy):
    runtime_class = UsageTelemetryRuntime

    def initialize(self):
        self.sleeptime = "1D"
        from lumibot.components.agents import BuiltinTools

        for name in ("research", "portfolio"):
            self.agents.create(
                name=name,
                system_prompt=f"Capture token usage for {name}.",
                default_model="stub-usage-telemetry",
                tools=[BuiltinTools.account.portfolio()],
                _runtime=self.runtime_class(),
            )

    def on_trading_iteration(self):
        self.agents["research"].run(context={"symbol": "AGST"})
        self.agents["portfolio"].run(context={"symbol": "AGST"})


class FutureTimestampWarningStrategy(Strategy):
    runtime_class = FutureTimestampRuntime

    def log_message(self, message, *args, **kwargs):
        logs = list(self.vars.get("warning_logs", []) or [])
        logs.append(str(message))
        self.vars.warning_logs = logs
        return super().log_message(message, *args, **kwargs)

    def initialize(self):
        self.sleeptime = "1D"
        self.agents.create(
            name="research",
            system_prompt="Inspect macro data before deciding whether to trade.",
            default_model="stub-future-warning",
            tools=[],
            _runtime=self.runtime_class(),
        )

    def on_trading_iteration(self):
        result = self.agents["research"].run(context={"target_symbol": "TQQQ"})
        self.vars.result_summary = result.summary
        self.vars.result_warnings = result.warnings


class InvalidAssetTypeStrategy(Strategy):
    runtime_class = InvalidAssetTypeRuntime

    def log_message(self, message, *args, **kwargs):
        logs = list(self.vars.get("invalid_asset_logs", []) or [])
        logs.append(str(message))
        self.vars.invalid_asset_logs = logs
        return super().log_message(message, *args, **kwargs)

    def initialize(self):
        self.sleeptime = "1D"
        from lumibot.components.agents import BuiltinTools

        self.agents.create(
            name="research",
            system_prompt="Use the history tool carefully and surface invalid arguments clearly.",
            default_model="stub-invalid-asset",
            tools=[BuiltinTools.market.load_history_table()],
            _runtime=self.runtime_class(),
        )

    def on_trading_iteration(self):
        result = self.agents["research"].run(
            context={
                "symbol": "AGST",
                "length": 3,
                "timestep": "minute",
                "asset_type": "equity",
            }
        )
        self.vars.result_summary = result.summary
        self.vars.result_warnings = result.warnings


def _build_stock_pandas_data():
    index = pd.date_range("2025-01-06 09:30", periods=6, freq="min", tz="America/New_York")
    df = pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.5, 103.0, 104.0, 104.5],
            "high": [101.0, 103.0, 103.5, 104.0, 105.0, 105.5],
            "low": [99.0, 100.5, 101.5, 102.0, 103.5, 104.0],
            "close": [100.5, 102.0, 103.0, 103.5, 104.5, 105.0],
            "volume": [1000, 1100, 1200, 1300, 1400, 1500],
        },
        index=index,
    )
    asset = Asset("AGST", Asset.AssetType.STOCK)
    return {asset: Data(asset, df, timestep="minute")}


def _build_option_pandas_data():
    index = pd.date_range("2025-01-06 09:30", periods=6, freq="min", tz="America/New_York")
    underlying = Asset("AGOP", Asset.AssetType.STOCK)
    option = Asset(
        "AGOP",
        asset_type=Asset.AssetType.OPTION,
        expiration=date(2025, 1, 17),
        strike=100.0,
        right="CALL",
    )
    quote = Asset("USD", Asset.AssetType.FOREX)
    underlying_df = pd.DataFrame(
        {
            "open": [99.0, 100.0, 101.0, 101.5, 102.0, 102.5],
            "high": [100.0, 101.0, 102.0, 102.5, 103.0, 103.5],
            "low": [98.5, 99.5, 100.5, 101.0, 101.5, 102.0],
            "close": [99.5, 100.5, 101.5, 102.0, 102.5, 103.0],
            "volume": [1500, 1500, 1500, 1500, 1500, 1500],
        },
        index=index,
    )
    option_df = pd.DataFrame(
        {
            "open": [3.0, 3.2, 3.5, 3.7, 3.9, 4.0],
            "high": [3.2, 3.5, 3.8, 4.0, 4.2, 4.3],
            "low": [2.8, 3.0, 3.3, 3.5, 3.7, 3.8],
            "close": [3.1, 3.4, 3.7, 3.9, 4.1, 4.2],
            "volume": [100, 110, 120, 130, 140, 150],
        },
        index=index,
    )
    return {
        underlying: Data(underlying, underlying_df, timestep="minute"),
        option: Data(option, option_df, quote=quote, timestep="minute"),
    }


def _build_minute_stress_pandas_data():
    day_one = pd.date_range("2025-01-06 09:30", periods=390, freq="min", tz="America/New_York")
    day_two = pd.date_range("2025-01-07 09:30", periods=390, freq="min", tz="America/New_York")
    index = day_one.append(day_two)
    base = [100.0 + i * 0.02 for i in range(len(index))]
    df = pd.DataFrame(
        {
            "open": base,
            "high": [value + 0.15 for value in base],
            "low": [value - 0.15 for value in base],
            "close": [value + 0.05 for value in base],
            "volume": [1000 + (i % 100) for i in range(len(index))],
        },
        index=index,
    )
    asset = Asset("AGMS", Asset.AssetType.STOCK)
    return {asset: Data(asset, df, timestep="minute")}


class BoundaryTraceTestVars(dict):
    def set(self, key, value):
        self[key] = value


class BoundaryTraceTestStrategy:
    name = "BoundaryTraceStrategy"
    market = "24/7"

    def __init__(self, *, is_backtesting):
        from lumibot.components.agents.manager import AgentManager

        self.is_backtesting = is_backtesting
        self.vars = BoundaryTraceTestVars()
        self.parameters = {}
        self.agents = AgentManager(self)

    def get_datetime(self):
        return None

    def log_message(self, *args, **kwargs):
        return None


def _build_boundary_trace_handle(monkeypatch, tmp_path, *, is_backtesting, runtime=None):
    monkeypatch.setenv("LUMIBOT_CACHE_FOLDER", str(tmp_path))
    strategy = BoundaryTraceTestStrategy(is_backtesting=is_backtesting)
    if runtime is None:
        runtime = BoundaryResultRuntime()
    handle = strategy.agents.create(
        name="trace_agent",
        system_prompt="test",
        include_builtin_tools=False,
        _runtime=runtime,
    )
    monkeypatch.setattr(strategy.agents, "_record_agent_observability", lambda **kwargs: None)
    monkeypatch.setattr(handle, "_append_memory", lambda result: None)
    monkeypatch.setattr(handle, "_append_run_artifact_summary", lambda result, context: None)
    monkeypatch.setattr(handle, "_log_run_summary", lambda result, context: None)
    return handle, runtime


def test_google_adk_runtime_exports_request_boundary_collector(monkeypatch, tmp_path):
    from lumibot.components.agents.boundary_trace import BoundaryTraceCollector
    from lumibot.components.agents.runtime import GoogleADKRuntime, RuntimeRequest

    collector = BoundaryTraceCollector(
        agent_run_id="google-run",
        artifact_root=tmp_path,
    )
    request = RuntimeRequest(
        agent_name="google_agent",
        model="test-model",
        system_prompt="test",
        task_prompt="test",
        context=None,
        runtime_context=None,
        memory_state=None,
        memory_notes=[],
        bound_tools=[],
        agent_run_id="google-run",
        boundary_collector=collector,
        run_timeout_seconds=None,
    )
    runtime = GoogleADKRuntime()

    async def successful_run(_request):
        return AgentRunResult(
            summary="RESULT: done",
            model=_request.model,
            events=[_event("text", text="RESULT: done")],
        )

    monkeypatch.setattr(runtime, "_run_async", successful_run)

    result = runtime.run(request)

    assert result.boundary_trace == {
        "schema_version": 1,
        "agent_run_id": "google-run",
        "capture_scope": "semantic_boundaries",
        "provider_wire_capture": False,
        "events": [],
        "diagnostics": [],
    }


def test_agent_run_result_accepts_boundary_trace():
    boundary_trace = {"schema_version": 1, "agent_run_id": "test-run"}

    result = AgentRunResult(
        summary="RESULT: done",
        model="test-model",
        events=[],
        boundary_trace=boundary_trace,
    )

    assert result.boundary_trace == boundary_trace


def test_agent_trace_persists_boundary_trace_without_changing_legacy_events(monkeypatch, tmp_path):
    handle, _ = _build_boundary_trace_handle(monkeypatch, tmp_path, is_backtesting=False)

    handle.run(task_prompt="test")

    trace_path = next((tmp_path / "agent_runtime" / "traces" / "trace_agent").glob("*.json"))
    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    assert payload["events"][0]["kind"] == "text"
    assert payload["events"][0]["call_id"] == "call-1"
    assert payload["events"][0]["event_id"] == "event-1"
    assert payload["events"][0]["invocation_id"] == "invocation-1"
    assert payload["boundary_trace"]["schema_version"] == 1
    assert payload["boundary_trace"]["agent_run_id"]
    assert list(trace_path.parent.glob("*.tmp")) == []


def test_handled_agent_failure_persists_partial_boundary_trace(monkeypatch, tmp_path):
    handle, _ = _build_boundary_trace_handle(
        monkeypatch,
        tmp_path,
        is_backtesting=True,
        runtime=BoundaryFailingRuntime(TimeoutError("provider timeout")),
    )

    result = handle.run(task_prompt="test")

    assert "Skipped this iteration" in result.summary
    trace_path = Path(result.payload["trace_path"])
    assert trace_path.is_file()
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace["boundary_trace"]["events"]
    assert trace["boundary_trace"]["diagnostics"]
    assert trace["boundary_trace"]["events"][0]["transition"] == "B09_ADK_TO_LITELLM"
    assert "provider timeout" in trace["boundary_trace"]["diagnostics"][-1]["message"]
    assert list((tmp_path / "agent_runtime" / "replay").rglob("*.json.gz")) == []


def test_handled_agent_failure_redacts_secrets_from_complete_trace(monkeypatch, tmp_path):
    api_token = "sk-proj-synthetic-runtime-secret"
    bearer_token = "synthetic-bearer-runtime-secret"
    error = TimeoutError(
        f"provider timeout for {api_token}; Authorization: Bearer {bearer_token}"
    )
    handle, _ = _build_boundary_trace_handle(
        monkeypatch,
        tmp_path,
        is_backtesting=True,
        runtime=BoundaryFailingRuntime(error),
    )

    result = handle.run(task_prompt="test")

    trace_path = Path(result.payload["trace_path"])
    trace_text = trace_path.read_text(encoding="utf-8")
    trace = json.loads(trace_text)
    for secret in (api_token, bearer_token):
        assert secret not in trace_text
        assert secret not in result.summary
        assert secret not in result.events[0].text
        assert secret not in json.dumps(result.events[0].payload)
        assert secret not in json.dumps(result.warnings)
        assert secret not in json.dumps(result.payload)
    assert "category=transient" in result.summary
    assert "TimeoutError" in result.summary
    assert "[REDACTED]" in result.summary
    assert "TimeoutError" in trace["events"][0]["payload"]["traceback"]
    assert "[REDACTED]" in trace["events"][0]["payload"]["traceback"]
    assert trace["boundary_trace"]["diagnostics"][-1]["kind"] == "agent_runtime_failed"


@pytest.mark.parametrize(
    "error_type",
    [KeyboardInterrupt, SystemExit, GeneratorExit, asyncio.CancelledError],
)
def test_runtime_control_flow_exceptions_propagate(monkeypatch, tmp_path, error_type):
    error = error_type("stop agent run")
    handle, _ = _build_boundary_trace_handle(
        monkeypatch,
        tmp_path,
        is_backtesting=False,
        runtime=BoundaryFailingRuntime(error),
    )

    with pytest.raises(error_type) as exc_info:
        handle.run(task_prompt="test")

    assert exc_info.value is error
    assert list((tmp_path / "agent_runtime" / "traces").rglob("*.json")) == []
    assert list((tmp_path / "agent_runtime" / "replay").rglob("*.json.gz")) == []


def test_trace_write_failure_does_not_change_successful_agent_result(monkeypatch, tmp_path):
    handle, _ = _build_boundary_trace_handle(
        monkeypatch,
        tmp_path,
        is_backtesting=True,
    )

    def fail_trace_write(*_args, **_kwargs):
        raise OSError("trace disk unavailable")

    monkeypatch.setattr(handle, "_write_trace", fail_trace_write)

    result = handle.run(task_prompt="test")

    assert result.summary == "RESULT: done"
    assert result.events[0].text == "RESULT: done"
    assert result.events[0].call_id == "call-1"
    assert result.payload["trace_path"] is None
    assert result.payload["trace_write_error"] is True
    assert result.boundary_trace["diagnostics"][-1]["kind"] == "trace_write_failed"
    assert list((tmp_path / "agent_runtime" / "replay").rglob("*.json.gz")) == []


def test_non_os_trace_write_failure_does_not_change_successful_agent_result(
    monkeypatch,
    tmp_path,
):
    handle, _ = _build_boundary_trace_handle(
        monkeypatch,
        tmp_path,
        is_backtesting=True,
    )

    def fail_trace_write(*_args, **_kwargs):
        raise ValueError("trace serialization failed")

    monkeypatch.setattr(handle, "_write_trace", fail_trace_write)

    result = handle.run(task_prompt="test")

    assert result.summary == "RESULT: done"
    assert result.events[0].text == "RESULT: done"
    assert result.events[0].call_id == "call-1"
    assert result.payload["trace_path"] is None
    assert result.payload["trace_write_error"] is True
    diagnostic = result.boundary_trace["diagnostics"][-1]
    assert diagnostic["kind"] == "trace_write_failed"
    assert diagnostic["message"] == "trace serialization failed"
    assert list((tmp_path / "agent_runtime" / "replay").rglob("*.json.gz")) == []


def test_trace_build_failure_does_not_change_successful_agent_result(monkeypatch, tmp_path):
    secret = "sk-proj-synthetic-trace-build-secret"
    handle, _ = _build_boundary_trace_handle(
        monkeypatch,
        tmp_path,
        is_backtesting=True,
    )

    def fail_trace_build(**_kwargs):
        raise ValueError(f"trace build failed for {secret}")

    monkeypatch.setattr(handle, "_build_trace_payload", fail_trace_build)

    result = handle.run(task_prompt="test")

    assert result.summary == "RESULT: done"
    assert result.events[0].text == "RESULT: done"
    assert result.events[0].call_id == "call-1"
    assert result.payload["trace_path"] is None
    assert result.payload["trace_write_error"] is True
    diagnostic = result.boundary_trace["diagnostics"][-1]
    assert diagnostic["kind"] == "trace_write_failed"
    assert diagnostic["message"] == "trace build failed for [REDACTED]"
    assert secret not in json.dumps(result.boundary_trace)
    assert list((tmp_path / "agent_runtime" / "traces").rglob("*.json")) == []
    assert list((tmp_path / "agent_runtime" / "replay").rglob("*.json.gz")) == []


def test_handled_failure_survives_non_os_trace_write_failure(monkeypatch, tmp_path):
    handle, _ = _build_boundary_trace_handle(
        monkeypatch,
        tmp_path,
        is_backtesting=True,
        runtime=BoundaryFailingRuntime(TimeoutError("provider timeout")),
    )

    def fail_trace_write(*_args, **_kwargs):
        raise TypeError("trace payload is not serializable")

    monkeypatch.setattr(handle, "_write_trace", fail_trace_write)

    result = handle.run(task_prompt="test")

    assert "Skipped this iteration" in result.summary
    assert "no trades placed" in result.summary
    assert result.cache_key is None
    assert result.payload["trace_path"] is None
    assert result.payload["trace_write_error"] is True
    diagnostic = result.boundary_trace["diagnostics"][-1]
    assert diagnostic["kind"] == "trace_write_failed"
    assert diagnostic["message"] == "trace payload is not serializable"
    assert list((tmp_path / "agent_runtime" / "replay").rglob("*.json.gz")) == []


def test_handled_failure_survives_trace_build_failure(monkeypatch, tmp_path):
    secret = "synthetic-trace-build-bearer-secret"
    handle, _ = _build_boundary_trace_handle(
        monkeypatch,
        tmp_path,
        is_backtesting=True,
        runtime=BoundaryFailingRuntime(TimeoutError("provider timeout")),
    )

    def fail_trace_build(**_kwargs):
        raise TypeError(f"trace build failed; Authorization: Bearer {secret}")

    monkeypatch.setattr(handle, "_build_trace_payload", fail_trace_build)

    result = handle.run(task_prompt="test")

    assert "Skipped this iteration" in result.summary
    assert "no trades placed" in result.summary
    assert result.cache_key is None
    assert result.payload["runtime_error"] is True
    assert result.payload["trace_path"] is None
    assert result.payload["trace_write_error"] is True
    diagnostic = result.boundary_trace["diagnostics"][-1]
    assert diagnostic["kind"] == "trace_write_failed"
    assert diagnostic["message"] == "trace build failed; Authorization: [REDACTED]"
    assert secret not in json.dumps(result.boundary_trace)
    assert list((tmp_path / "agent_runtime" / "traces").rglob("*.json")) == []
    assert list((tmp_path / "agent_runtime" / "replay").rglob("*.json.gz")) == []


@pytest.mark.parametrize(
    "error",
    [
        Exception("invalid API key"),
        Exception("invalid model"),
        Exception("insufficient_quota"),
    ],
    ids=["auth", "config", "billing"],
)
def test_backtest_permanent_agent_errors_still_reraise(monkeypatch, tmp_path, error):
    handle, _ = _build_boundary_trace_handle(
        monkeypatch,
        tmp_path,
        is_backtesting=True,
        runtime=BoundaryFailingRuntime(error),
    )

    with pytest.raises(Exception) as exc_info:
        handle.run(task_prompt="test")

    assert exc_info.value is error
    assert list((tmp_path / "agent_runtime" / "traces").rglob("*.json")) == []
    assert list((tmp_path / "agent_runtime" / "replay").rglob("*.json.gz")) == []


@pytest.mark.parametrize(
    "side_effect_name",
    [
        "_record_agent_observability",
        "_append_memory",
        "_append_run_artifact_summary",
        "_log_run_summary",
    ],
)
@pytest.mark.parametrize("handled_failure", [False, True], ids=["success", "handled_failure"])
def test_post_result_artifact_failures_do_not_replace_agent_result(
    monkeypatch,
    tmp_path,
    side_effect_name,
    handled_failure,
):
    runtime = (
        BoundaryFailingRuntime(TimeoutError("provider timeout"))
        if handled_failure
        else BoundaryResultRuntime()
    )
    handle, _ = _build_boundary_trace_handle(
        monkeypatch,
        tmp_path,
        is_backtesting=True,
        runtime=runtime,
    )

    def fail_side_effect(*_args, **_kwargs):
        raise ValueError(f"{side_effect_name} unavailable")

    target = handle.manager if side_effect_name == "_record_agent_observability" else handle
    monkeypatch.setattr(target, side_effect_name, fail_side_effect)

    result = handle.run(task_prompt="test")

    if handled_failure:
        assert "Skipped this iteration" in result.summary
        assert "no trades placed" in result.summary
        assert result.cache_key is None
        assert list((tmp_path / "agent_runtime" / "replay").rglob("*.json.gz")) == []
    else:
        assert result.summary == "RESULT: done"
        assert result.events[0].text == "RESULT: done"
    assert Path(result.payload["trace_path"]).is_file()


@pytest.mark.parametrize(
    "error_type",
    [KeyboardInterrupt, SystemExit, GeneratorExit, asyncio.CancelledError],
)
def test_post_result_control_flow_exceptions_propagate(
    monkeypatch,
    tmp_path,
    error_type,
):
    handle, _ = _build_boundary_trace_handle(
        monkeypatch,
        tmp_path,
        is_backtesting=False,
    )
    error = error_type("stop artifact finalization")

    def cancel_memory(_result):
        raise error

    monkeypatch.setattr(handle, "_append_memory", cancel_memory)

    with pytest.raises(error_type) as exc_info:
        handle.run(task_prompt="test")

    assert exc_info.value is error


def test_replay_cache_write_failure_does_not_replace_successful_result(monkeypatch, tmp_path):
    handle, _ = _build_boundary_trace_handle(
        monkeypatch,
        tmp_path,
        is_backtesting=True,
    )

    def fail_cache_write(*_args, **_kwargs):
        raise ValueError("replay cache unavailable")

    monkeypatch.setattr(handle.manager.replay_cache, "save", fail_cache_write)

    result = handle.run(task_prompt="test")

    assert result.summary == "RESULT: done"
    assert result.events[0].text == "RESULT: done"
    assert Path(result.payload["trace_path"]).is_file()
    assert list((tmp_path / "agent_runtime" / "replay").rglob("*.json.gz")) == []


def test_corrupt_replay_cache_is_a_miss_after_partial_save_failure(monkeypatch, tmp_path):
    runtime = BoundaryResultRuntime()
    handle, _ = _build_boundary_trace_handle(
        monkeypatch,
        tmp_path,
        is_backtesting=True,
        runtime=runtime,
    )
    replay_cache = handle.manager.replay_cache
    real_save = replay_cache.save
    partial_cache_path = None

    def fail_with_partial_cache(cache_key, _payload):
        nonlocal partial_cache_path
        partial_cache_path = replay_cache._path_for(cache_key)
        partial_cache_path.parent.mkdir(parents=True, exist_ok=True)
        partial_cache_path.write_bytes(b"synthetic-partial-gzip")
        raise OSError("synthetic replay cache save failure")

    monkeypatch.setattr(replay_cache, "save", fail_with_partial_cache)

    first_result = handle.run(task_prompt="test")

    assert first_result.summary == "RESULT: done"
    assert first_result.cache_hit is False
    assert runtime.call_count == 1
    assert partial_cache_path is not None
    assert partial_cache_path.read_bytes() == b"synthetic-partial-gzip"

    monkeypatch.setattr(replay_cache, "save", real_save)

    second_result = handle.run(task_prompt="test")

    assert second_result.summary == "RESULT: done"
    assert second_result.cache_hit is False
    assert second_result.cache_key == first_result.cache_key
    assert runtime.call_count == 2
    assert replay_cache.load(second_result.cache_key)["summary"] == "RESULT: done"


def test_remote_replay_cache_hydration_error_is_a_miss_and_recovers(monkeypatch, tmp_path):
    runtime = BoundaryResultRuntime()
    handle, _ = _build_boundary_trace_handle(
        monkeypatch,
        tmp_path,
        is_backtesting=True,
        runtime=runtime,
    )
    replay_cache = handle.manager.replay_cache
    real_ensure_local_file = replay_cache.remote_cache.ensure_local_file
    hydration_attempts = 0

    def fail_first_hydration(path):
        nonlocal hydration_attempts
        hydration_attempts += 1
        if hydration_attempts == 1:
            raise ValueError("synthetic remote hydration failure")
        return real_ensure_local_file(path)

    monkeypatch.setattr(
        replay_cache.remote_cache,
        "ensure_local_file",
        fail_first_hydration,
    )

    first_result = handle.run(task_prompt="test")
    second_result = handle.run(task_prompt="test")

    assert first_result.summary == "RESULT: done"
    assert first_result.cache_hit is False
    assert second_result.cache_hit is True
    assert runtime.call_count == 1
    assert hydration_attempts == 2


def test_agent_trace_atomic_replace_uses_same_directory(monkeypatch, tmp_path):
    from lumibot.components.agents import manager as manager_module

    handle, _ = _build_boundary_trace_handle(monkeypatch, tmp_path, is_backtesting=False)
    replace_calls = []
    real_replace = manager_module.os.replace

    def observed_replace(source, target):
        source_path = Path(source)
        target_path = Path(target)
        replace_calls.append((source_path, target_path))
        assert source_path.parent == target_path.parent
        assert source_path.is_file()
        assert not target_path.exists()
        real_replace(source, target)

    monkeypatch.setattr(manager_module.os, "replace", observed_replace)

    trace_path = handle._write_trace(
        AgentRunResult(summary="done", model="test-model", events=[]),
        {"summary": "done"},
    )

    assert len(replace_calls) == 1
    assert replace_calls[0][1] == trace_path
    assert trace_path.is_file()
    assert list(trace_path.parent.glob("*.tmp")) == []


def test_agent_trace_redaction_preserves_numeric_token_usage(monkeypatch, tmp_path):
    from lumibot.components.agents.manager import _normalize_redacted_payload

    api_token = "sk-proj-synthetic-persisted-usage-secret"
    bearer_token = "synthetic-persisted-usage-bearer-secret"
    handle, _ = _build_boundary_trace_handle(monkeypatch, tmp_path, is_backtesting=False)
    usage = {
        "prompt_tokens": 101,
        "completion_tokens": 23,
        "total_tokens": 124,
        "cache_write_input_tokens": 7,
        "prompt_tokens_details": {"cached_tokens": 80},
        "completion_tokens_details": {"reasoning_tokens": 9},
    }

    trace_path = handle._write_trace(
        AgentRunResult(summary="done", model="test-model", events=[]),
        _normalize_redacted_payload(
            {
                "usage": usage,
                "api_key": api_token,
                "header": f"Authorization: Bearer {bearer_token}",
            }
        ),
    )

    trace_text = trace_path.read_text(encoding="utf-8")
    trace = json.loads(trace_text)
    assert trace["usage"] == usage
    assert trace["api_key"] == "[REDACTED]"
    assert trace["header"] == "Authorization: [REDACTED]"
    assert api_token not in trace_text
    assert bearer_token not in trace_text


def test_agent_run_artifact_summary_redacts_complete_jsonl_record(monkeypatch, tmp_path):
    api_token = "sk-proj-synthetic-run-summary-secret"
    bearer_token = "synthetic-run-summary-bearer-secret"
    handle, _ = _build_boundary_trace_handle(monkeypatch, tmp_path, is_backtesting=False)
    result = AgentRunResult(
        summary=f"RESULT: {api_token}; Authorization: Bearer {bearer_token}",
        model="test-model",
        events=[
            _event(
                "tool_call",
                tool_name=f"tool-{api_token}",
                payload={"Authorization": f"Bearer {bearer_token}"},
            )
        ],
        usage={
            "prompt_tokens": 101,
            "completion_tokens": 23,
            "total_tokens": 124,
            "cache_creation_input_tokens": 7,
            "completion_tokens_details": {"reasoning_tokens": 9},
        },
        warnings=[
            {
                "kind": "synthetic_warning",
                "message": f"warning for {api_token}; Bearer {bearer_token}",
            }
        ],
    )
    result.payload = {"trace_path": None}

    type(handle)._append_run_artifact_summary(
        handle,
        result,
        {"mode": "live"},
    )

    summary_path = tmp_path / "agent_runtime" / "agent_run_summaries.jsonl"
    summary_text = summary_path.read_text(encoding="utf-8")
    summary_record = json.loads(summary_text)
    assert api_token not in summary_text
    assert bearer_token not in summary_text
    assert summary_record["usage"]["input_tokens"] == 101
    assert summary_record["usage"]["output_tokens"] == 23
    assert summary_record["usage"]["total_tokens"] == 124
    assert summary_record["usage"]["thinking_tokens"] == 9
    assert summary_record["usage"]["cache_write_input_tokens"] == 7
    assert "[REDACTED]" in summary_record["summary"]
    assert "[REDACTED]" in summary_record["warning_messages"][0]
    assert "[REDACTED]" in summary_record["tool_calls"][0]


def test_agent_trace_replace_failure_cleans_temp_without_partial_target(monkeypatch, tmp_path):
    from lumibot.components.agents import manager as manager_module

    handle, _ = _build_boundary_trace_handle(monkeypatch, tmp_path, is_backtesting=False)
    replace_calls = []

    def failing_replace(source, target):
        source_path = Path(source)
        target_path = Path(target)
        replace_calls.append((source_path, target_path))
        assert source_path.parent == target_path.parent
        assert source_path.is_file()
        assert not target_path.exists()
        raise OSError("replace failed")

    monkeypatch.setattr(manager_module.os, "replace", failing_replace)

    with pytest.raises(OSError, match="replace failed"):
        handle._write_trace(
            AgentRunResult(summary="done", model="test-model", events=[]),
            {"summary": "done"},
        )

    assert len(replace_calls) == 1
    trace_dir = tmp_path / "agent_runtime" / "traces" / "trace_agent"
    assert list(trace_dir.glob("*.tmp")) == []
    assert list(trace_dir.glob("*.json")) == []


def test_agent_replay_cache_uses_portable_boundary_trace_reference(monkeypatch, tmp_path):
    from lumibot.components.agents import manager as manager_module

    collector_calls = []
    collector_class = manager_module.BoundaryTraceCollector

    def tracking_collector(**kwargs):
        collector_calls.append(kwargs)
        return collector_class(**kwargs)

    monkeypatch.setattr(manager_module, "BoundaryTraceCollector", tracking_collector)
    handle, runtime = _build_boundary_trace_handle(monkeypatch, tmp_path, is_backtesting=True)

    live_result = handle.run(task_prompt="test")
    cached_result = handle.run(task_prompt="test")

    cache_path = next((tmp_path / "agent_runtime" / "replay").rglob("*.json.gz"))
    with gzip.open(cache_path, "rt", encoding="utf-8") as cache_file:
        cache_text = cache_file.read()
    cached_payload = json.loads(cache_text)
    assert tmp_path.as_posix() not in cache_text.replace("\\", "/")
    reference = cached_payload["boundary_trace_ref"]
    payload_trace_path = cached_payload["payload"]["trace_path"]
    assert "boundary_trace" not in cached_payload
    assert reference == {
        "status": "available_original_trace",
        "trace_path": reference["trace_path"],
        "agent_run_id": live_result.boundary_trace["agent_run_id"],
    }
    assert payload_trace_path == reference["trace_path"]
    for portable_path in (payload_trace_path, reference["trace_path"]):
        assert PureWindowsPath(portable_path).drive == ""
        assert not PureWindowsPath(portable_path).is_absolute()
        assert not PurePosixPath(portable_path).is_absolute()
        assert "\\" not in portable_path
        assert (tmp_path / "agent_runtime" / portable_path).is_file()
    assert reference["trace_path"].startswith("traces/trace_agent/")
    live_trace_path = Path(live_result.payload["trace_path"])
    cached_trace_path = Path(cached_result.payload["trace_path"])
    assert live_trace_path.is_absolute()
    assert cached_trace_path.is_absolute()
    assert live_trace_path.is_file()
    assert cached_trace_path.is_file()
    assert live_trace_path == cached_trace_path
    assert cached_payload["events"][0]["call_id"] == "call-1"
    assert cached_payload["events"][0]["event_id"] == "event-1"
    assert cached_payload["events"][0]["invocation_id"] == "invocation-1"
    assert runtime.call_count == 1
    assert len(collector_calls) == 1
    assert cached_result.boundary_trace == {
        **reference,
        "schema_version": 1,
        "execution_source": "replay_cache",
        "events": [],
        "diagnostics": [],
    }
    assert cached_result.events[0].call_id == "call-1"
    assert cached_result.events[0].event_id == "event-1"
    assert cached_result.events[0].invocation_id == "invocation-1"


def test_agent_replay_cache_uses_authoritative_redacted_trace_payload(monkeypatch, tmp_path):
    api_token = "sk-proj-synthetic-replay-secret"
    bearer_token = "synthetic-replay-bearer-secret"
    runtime = SensitiveReplayRuntime(api_token, bearer_token)
    handle, _ = _build_boundary_trace_handle(
        monkeypatch,
        tmp_path,
        is_backtesting=True,
        runtime=runtime,
    )

    live_result = handle.run(task_prompt="test")

    trace_path = Path(live_result.payload["trace_path"])
    trace_text = trace_path.read_text(encoding="utf-8")
    trace = json.loads(trace_text)
    cache_path = next((tmp_path / "agent_runtime" / "replay").rglob("*.json.gz"))
    with gzip.open(cache_path, "rt", encoding="utf-8") as cache_file:
        cache_text = cache_file.read()
    cached_payload = json.loads(cache_text)
    for secret in (api_token, bearer_token):
        assert secret not in trace_text
        assert secret not in cache_text
    for field in ("summary", "events", "warnings", "usage", "timing"):
        assert cached_payload[field] == trace[field]
    assert cached_payload["usage"] == {
        "prompt_tokens": 101,
        "completion_tokens": 23,
        "total_tokens": 124,
        "cache_write_input_tokens": 7,
        "prompt_tokens_details": {"cached_tokens": 80},
        "completion_tokens_details": {"reasoning_tokens": 9},
    }
    reference = cached_payload["boundary_trace_ref"]
    assert cached_payload["payload"]["trace_path"] == reference["trace_path"]
    assert (tmp_path / "agent_runtime" / reference["trace_path"]).resolve() == trace_path.resolve()

    replayed_result = handle.run(task_prompt="test")

    assert runtime.call_count == 1
    assert replayed_result.cache_hit is True
    assert replayed_result.summary == trace["summary"]
    assert replayed_result.usage == trace["usage"]
    assert Path(replayed_result.payload["trace_path"]).resolve() == trace_path.resolve()


def test_legacy_agent_replay_cache_marks_boundary_trace_unavailable(monkeypatch, tmp_path):
    handle, _ = _build_boundary_trace_handle(monkeypatch, tmp_path, is_backtesting=True)
    trace_path = tmp_path / "agent_runtime" / "traces" / "trace_agent" / "legacy.json"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text("{}", encoding="utf-8")

    result = handle._result_from_cached(
        {
            "summary": "Cached.",
            "model": "test-model",
            "events": [],
            "warnings": [],
            "payload": {
                "trace_path": "C:/cache/agent_runtime/traces/trace_agent/legacy.json",
            },
        },
        "legacy-key",
    )

    assert Path(result.payload["trace_path"]) == trace_path.resolve()
    assert Path(result.payload["trace_path"]).is_file()
    assert result.boundary_trace == {
        "schema_version": 1,
        "status": "unavailable_legacy_cache",
        "execution_source": "replay_cache",
        "events": [],
        "diagnostics": [],
    }


def test_agent_replay_cache_marks_missing_boundary_capture_unavailable(monkeypatch, tmp_path):
    handle, _ = _build_boundary_trace_handle(
        monkeypatch,
        tmp_path,
        is_backtesting=True,
        runtime=NoBoundaryResultRuntime(),
    )

    live_result = handle.run(task_prompt="test")
    cached_result = handle.run(task_prompt="test")

    cache_path = next((tmp_path / "agent_runtime" / "replay").rglob("*.json.gz"))
    with gzip.open(cache_path, "rt", encoding="utf-8") as cache_file:
        cached_payload = json.load(cache_file)
    assert cached_payload["boundary_trace_ref"] == {
        "status": "unavailable_no_boundary_capture",
        "trace_path": cached_payload["payload"]["trace_path"],
    }
    assert Path(live_result.payload["trace_path"]).is_file()
    assert Path(cached_result.payload["trace_path"]).is_file()
    assert cached_result.boundary_trace == {
        **cached_payload["boundary_trace_ref"],
        "schema_version": 1,
        "execution_source": "replay_cache",
        "events": [],
        "diagnostics": [],
    }


def test_remote_cache_uses_sanitized_reference_as_authoritative_path(monkeypatch, tmp_path):
    handle, _ = _build_boundary_trace_handle(monkeypatch, tmp_path, is_backtesting=True)
    trace_path = tmp_path / "agent_runtime" / "traces" / "trace_agent" / "remote.json"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text("{}", encoding="utf-8")

    result = handle._result_from_cached(
        {
            "summary": "Cached.",
            "model": "test-model",
            "events": [],
            "warnings": [],
            "payload": {
                "trace_path": "D:/wrong/agent_runtime/traces/trace_agent/wrong.json",
            },
            "boundary_trace_ref": {
                "status": "available_original_trace",
                "trace_path": "C:/remote/agent_runtime/traces/trace_agent/remote.json",
                "agent_run_id": "remote-run",
            },
        },
        "remote-key",
    )

    assert result.boundary_trace["trace_path"] == "traces/trace_agent/remote.json"
    assert Path(result.payload["trace_path"]) == trace_path.resolve()
    assert Path(result.payload["trace_path"]).is_file()


@pytest.mark.parametrize(
    "malicious_path",
    [
        "agent_runtime/C:/secret/trace.json",
        "agent_runtime//server/share/trace.json",
        r"agent_runtime\\server\share\trace.json",
        "agent_runtime/../traces/trace_agent/escape.json",
        "C:/outside/trace.json",
        "/outside/trace.json",
    ],
)
def test_cached_boundary_reference_rejects_nonportable_paths(monkeypatch, tmp_path, malicious_path):
    handle, _ = _build_boundary_trace_handle(monkeypatch, tmp_path, is_backtesting=True)

    result = handle._result_from_cached(
        {
            "summary": "Cached.",
            "model": "test-model",
            "events": [],
            "warnings": [],
            "payload": {"trace_path": "traces/trace_agent/untrusted-fallback.json"},
            "boundary_trace_ref": {
                "status": "available_original_trace",
                "trace_path": malicious_path,
                "agent_run_id": "remote-run",
            },
        },
        "malicious-key",
    )

    assert result.boundary_trace == {
        "schema_version": 1,
        "status": "unavailable_invalid_trace_reference",
        "execution_source": "replay_cache",
        "events": [],
        "diagnostics": [],
    }
    assert result.payload["trace_path"] is None


@pytest.mark.usefixtures("disable_datasource_override")
def test_agent_runtime_stock_backtest_replays_from_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMIBOT_CACHE_FOLDER", str(tmp_path / "cache"))
    StockPlanRuntime.call_count = 0
    params = dict(
        datasource_class=PandasDataBacktesting,
        backtesting_start=datetime(2025, 1, 6, 9, 30),
        backtesting_end=datetime(2025, 1, 6, 9, 35),
        pandas_data=_build_stock_pandas_data(),
        benchmark_asset=None,
        analyze_backtest=False,
        show_plot=False,
        save_tearsheet=False,
        show_tearsheet=False,
        show_indicators=False,
        save_logfile=False,
        show_progress_bar=False,
        quiet_logs=True,
    )

    _, strategy_first = AgentStockBacktestStrategy.run_backtest(**params)
    first_calls = StockPlanRuntime.call_count
    assert first_calls > 0
    assert strategy_first.get_position(Asset("AGST", Asset.AssetType.STOCK)) is not None
    assert strategy_first.agents.duckdb.get_metrics()["history_load_calls"] >= 1
    assert strategy_first.agents.duckdb.get_metrics()["history_bind_calls"] == 1
    assert strategy_first.agents.duckdb.get_metrics()["history_visible_refresh_calls"] >= 1
    state = strategy_first.vars.get("_agent_runtime_state", {})
    assert "research" in state
    assert state["research"]["memory_notes"]

    _, strategy_second = AgentStockBacktestStrategy.run_backtest(**params)
    assert StockPlanRuntime.call_count == first_calls
    assert strategy_second.get_position(Asset("AGST", Asset.AssetType.STOCK)) is not None
    second_state = strategy_second.vars.get("_agent_runtime_state", {})
    assert second_state["research"]["runs"][-1]["cache_hit"] is True
    second_result = strategy_second.last_agent_result
    assert second_result is not None
    # A fresh cache entry retains the original boundary trace reference.
    assert second_result.boundary_trace["execution_source"] == "replay_cache"
    assert second_result.boundary_trace["status"] == "available_original_trace"


@pytest.mark.usefixtures("disable_datasource_override")
def test_agent_runtime_option_backtest_executes_option_order(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMIBOT_CACHE_FOLDER", str(tmp_path / "cache"))
    OptionPlanRuntime.call_count = 0
    _, strategy = AgentOptionBacktestStrategy.run_backtest(
        datasource_class=PandasDataBacktesting,
        backtesting_start=datetime(2025, 1, 6, 9, 30),
        backtesting_end=datetime(2025, 1, 6, 9, 35),
        pandas_data=_build_option_pandas_data(),
        benchmark_asset=None,
        analyze_backtest=False,
        show_plot=False,
        save_tearsheet=False,
        show_tearsheet=False,
        show_indicators=False,
        save_logfile=False,
        show_progress_bar=False,
        quiet_logs=True,
    )
    assert OptionPlanRuntime.call_count > 0
    positions = strategy.get_positions(include_cash_positions=True)
    option_positions = [position for position in positions if position.asset.asset_type == Asset.AssetType.OPTION]
    assert option_positions
    assert strategy.agents.duckdb.get_metrics()["history_load_calls"] >= 1
    assert strategy.agents.duckdb.get_metrics()["history_bind_calls"] == 1


@pytest.mark.usefixtures("disable_datasource_override")
def test_agent_runtime_minute_duckdb_stress_binds_once(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMIBOT_CACHE_FOLDER", str(tmp_path / "cache"))
    MinuteStressRuntime.call_count = 0
    _, strategy = AgentMinuteStressBacktestStrategy.run_backtest(
        datasource_class=PandasDataBacktesting,
        backtesting_start=datetime(2025, 1, 6, 9, 30),
        backtesting_end=datetime(2025, 1, 7, 15, 59),
        pandas_data=_build_minute_stress_pandas_data(),
        benchmark_asset=None,
        analyze_backtest=False,
        show_plot=False,
        save_tearsheet=False,
        show_tearsheet=False,
        show_indicators=False,
        save_logfile=False,
        show_progress_bar=False,
        quiet_logs=True,
    )
    metrics = strategy.agents.duckdb.get_metrics()
    assert MinuteStressRuntime.call_count >= 700
    assert metrics["history_load_calls"] >= 700
    assert metrics["history_bind_calls"] == 1
    assert metrics["history_bind_cache_hits"] >= 700


@pytest.mark.usefixtures("disable_datasource_override")
def test_agent_runtime_injects_base_prompt_runtime_context_and_default_summary_log(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMIBOT_CACHE_FOLDER", str(tmp_path / "cache"))
    PromptCaptureRuntime.call_count = 0
    PromptCaptureRuntime.last_request = None
    _, strategy = PromptCaptureStrategy.run_backtest(
        datasource_class=PandasDataBacktesting,
        backtesting_start=datetime(2025, 1, 6),
        backtesting_end=datetime(2025, 1, 7),
        pandas_data=_build_stock_pandas_data(),
        benchmark_asset=None,
        analyze_backtest=False,
        show_plot=False,
        save_tearsheet=False,
        show_tearsheet=False,
        show_indicators=False,
        save_logfile=False,
        show_progress_bar=False,
        quiet_logs=True,
    )
    request = PromptCaptureRuntime.last_request
    assert request is not None
    assert request.runtime_context["mode"] == "backtesting"
    assert request.runtime_context["current_datetime"]
    assert request.runtime_context["timezone"]
    assert "positions" in request.runtime_context
    assert "account" in request.runtime_context
    assert "recent_orders" in request.runtime_context
    assert "recent_trades" in request.runtime_context
    assert "BACKTESTING SAFETY RULES" in request.system_prompt
    assert "Look-ahead bias" in request.system_prompt
    assert "Current datetime:" not in request.system_prompt
    assert "current_datetime" in request.runtime_context
    assert "DEFAULT INVESTOR POLICY" in request.system_prompt
    assert "Do not trade for the sake of activity." in request.system_prompt
    assert "Do not resist intentional concentration" in request.system_prompt
    assert "Avoid leaving raw cash idle unless there is a specific reason" in request.system_prompt
    assert (
        "use the exact column names returned by market_load_history_table or pragma_table_info"
        in request.system_prompt
    )
    assert "often named Date, not datetime" in request.system_prompt
    assert "Do not assume datetime exists" in request.system_prompt
    assert "use datetime for timestamp columns" not in request.system_prompt
    tool_names = [tool.name for tool in request.bound_tools]
    assert len(tool_names) == len(set(tool_names))
    summary_logs = [line for line in strategy.vars.captured_logs if line.startswith("[agents] name=research")]
    assert summary_logs
    assert "cache_hit=False" in summary_logs[-1]
    assert "tool_calls=0" in summary_logs[-1]
    summary_file = Path(tmp_path / "cache" / "agent_runtime" / "agent_run_summaries.jsonl")
    assert summary_file.exists()
    records = [json.loads(line) for line in summary_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert records
    assert records[-1]["agent_name"] == "research"
    assert records[-1]["trace_relative_path"].startswith("agent_runtime/traces/")


@pytest.mark.usefixtures("disable_datasource_override")
def test_agent_detail_parquet_has_single_token_summary_row_and_full_events(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMIBOT_CACHE_FOLDER", str(tmp_path / "cache"))
    UsageTelemetryRuntime.call_count = 0
    UsageTelemetryRuntime.last_result = None
    _, strategy = UsageTelemetryStrategy.run_backtest(
        datasource_class=PandasDataBacktesting,
        backtesting_start=datetime(2025, 1, 6),
        backtesting_end=datetime(2025, 1, 7),
        pandas_data=_build_stock_pandas_data(),
        benchmark_asset=None,
        analyze_backtest=False,
        show_plot=False,
        save_tearsheet=False,
        show_tearsheet=False,
        show_indicators=False,
        save_logfile=False,
        show_progress_bar=False,
        quiet_logs=True,
    )

    detail_parquet = Path(strategy.parameters["agent_research_detail_parquet"])
    assert detail_parquet.exists()
    assert "agent_research_detail_csv" not in strategy.parameters
    assert not detail_parquet.with_suffix(".csv").exists()
    df = pd.read_parquet(detail_parquet)
    assert "call_summary" in set(df["event_kind"])
    assert "thinking" in set(df["event_kind"])
    assert "tool_call" in set(df["event_kind"])
    assert "tool_result" in set(df["event_kind"])
    assert "usage" in set(df["event_kind"])
    assert df["call_index"].nunique() == UsageTelemetryRuntime.call_count

    summaries = df[df["event_kind"] == "call_summary"]
    assert len(summaries) == UsageTelemetryRuntime.call_count
    call_numbers = list(range(1, UsageTelemetryRuntime.call_count + 1))
    assert summaries["call_input_tokens"].sum() == sum(1000 + idx for idx in call_numbers)
    assert summaries["call_output_tokens"].sum() == sum(200 + idx for idx in call_numbers)
    assert summaries["call_cached_input_tokens"].sum() == sum(700 + idx for idx in call_numbers)
    assert summaries["call_uncached_input_tokens"].sum() == sum(300 for _idx in call_numbers)
    assert summaries["call_thinking_tokens"].sum() == sum(55 + idx for idx in call_numbers)
    assert summaries["call_cache_write_input_tokens"].sum() == sum(123 + idx for idx in call_numbers)
    assert summaries["call_tool_use_input_tokens"].sum() == sum(17 + idx for idx in call_numbers)
    if UsageTelemetryRuntime.call_count > 1:
        assert summaries["call_input_tokens"].nunique() > 1
    assert pd.to_numeric(summaries["call_latency_ms"]).ge(0).all()
    assert "I should inspect the account first." in " ".join(df["thinking_text"].fillna("").astype(str).tolist())
    assert "portfolio_value" in " ".join(df["event_payload_json"].fillna("").astype(str).tolist())
    original_result = UsageTelemetryRuntime.last_result
    assert original_result is not None
    original_trace_path = Path(original_result.payload["trace_path"])
    assert original_trace_path.is_absolute()
    assert original_trace_path.is_file()
    persisted_trace_paths = set(df["trace_path"].dropna().astype(str))
    assert persisted_trace_paths
    for persisted_trace_path in persisted_trace_paths:
        assert PureWindowsPath(persisted_trace_path).drive == ""
        assert not PureWindowsPath(persisted_trace_path).is_absolute()
        assert not PurePosixPath(persisted_trace_path).is_absolute()
        assert "\\" not in persisted_trace_path
        assert (tmp_path / "cache" / "agent_runtime" / persisted_trace_path).is_file()

    non_summary = df[df["event_kind"] != "call_summary"]
    assert non_summary["call_input_tokens"].sum() == 0
    assert non_summary["call_output_tokens"].sum() == 0
    usage_rows = df[df["event_kind"] == "usage"]
    assert usage_rows["event_input_tokens"].sum() == sum(1000 + idx for idx in call_numbers)
    assert usage_rows["event_cached_input_tokens"].sum() == sum(700 + idx for idx in call_numbers)


@pytest.mark.usefixtures("disable_datasource_override")
def test_agent_detail_parquet_combines_multiple_agents(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMIBOT_CACHE_FOLDER", str(tmp_path / "cache"))
    UsageTelemetryRuntime.call_count = 0
    _, strategy = MultiAgentUsageTelemetryStrategy.run_backtest(
        datasource_class=PandasDataBacktesting,
        backtesting_start=datetime(2025, 1, 6),
        backtesting_end=datetime(2025, 1, 7),
        pandas_data=_build_stock_pandas_data(),
        benchmark_asset=None,
        analyze_backtest=False,
        show_plot=False,
        save_tearsheet=False,
        show_tearsheet=False,
        show_indicators=False,
        save_logfile=False,
        show_progress_bar=False,
        quiet_logs=True,
    )

    detail_parquet = Path(strategy.parameters["agent_portfolio_detail_parquet"])
    assert detail_parquet == Path(strategy.parameters["agent_research_detail_parquet"])
    df = pd.read_parquet(detail_parquet)
    summaries = df[df["event_kind"] == "call_summary"]

    assert set(summaries["agent_name"]) == {"research", "portfolio"}
    assert len(summaries) == UsageTelemetryRuntime.call_count
    assert summaries.groupby("agent_name").size().to_dict()["research"] > 0
    assert summaries.groupby("agent_name").size().to_dict()["portfolio"] > 0
    call_numbers = list(range(1, UsageTelemetryRuntime.call_count + 1))
    assert summaries["call_input_tokens"].sum() == sum(1000 + idx for idx in call_numbers)
    assert summaries["call_output_tokens"].sum() == sum(200 + idx for idx in call_numbers)


@pytest.mark.usefixtures("disable_datasource_override")
def test_agent_runtime_replays_market_priming_builtins_on_cache(monkeypatch, tmp_path):
    from lumibot.components.agents import BuiltinTools

    monkeypatch.setenv("LUMIBOT_CACHE_FOLDER", str(tmp_path / "cache"))
    StockPlanRuntime.call_count = 0
    _, strategy = AgentStockBacktestStrategy.run_backtest(
        datasource_class=PandasDataBacktesting,
        backtesting_start=datetime(2025, 1, 6),
        backtesting_end=datetime(2025, 1, 7),
        pandas_data=_build_stock_pandas_data(),
        benchmark_asset=None,
        analyze_backtest=False,
        show_plot=False,
        save_tearsheet=False,
        show_tearsheet=False,
        show_indicators=False,
        save_logfile=False,
        show_progress_bar=False,
        quiet_logs=True,
    )
    market_last_price = BuiltinTools.market.last_price().binder(strategy, strategy.agents)
    market_load_history = BuiltinTools.market.load_history_table().binder(strategy, strategy.agents)
    assert market_last_price.metadata.get("replay_on_cache") is True
    assert market_load_history.metadata.get("replay_on_cache") is True


def test_builtin_market_history_and_duckdb_descriptions_include_schema_hints():
    from lumibot.components.agents import BuiltinTools

    _, strategy = PromptCaptureStrategy.run_backtest(
        datasource_class=PandasDataBacktesting,
        backtesting_start=datetime(2025, 1, 6),
        backtesting_end=datetime(2025, 1, 7),
        pandas_data=_build_stock_pandas_data(),
        benchmark_asset=None,
        analyze_backtest=False,
        show_plot=False,
        save_tearsheet=False,
        show_tearsheet=False,
        show_indicators=False,
        save_logfile=False,
        show_progress_bar=False,
        quiet_logs=True,
    )
    history_tool = BuiltinTools.market.load_history_table().binder(strategy, strategy.agents)
    query_tool = BuiltinTools.duckdb.query().binder(strategy, strategy.agents)

    assert "exact column names" in history_tool.description
    assert "Date" in history_tool.description
    assert "Do not assume datetime exists" in history_tool.description
    assert "close" in history_tool.description
    assert "available_tables" in history_tool.description
    assert "currently queryable tables" in history_tool.description
    assert "exact column names" in query_tool.description
    assert "market_load_history_table" in query_tool.description
    assert "pragma_table_info" in query_tool.description
    assert "Do not invent datetime" in query_tool.description
    assert "close" in query_tool.description
    assert "alias every table" in query_tool.description
    assert "sym, Date, close, and return" in query_tool.description
    assert "q.sym" in query_tool.description
    assert "q.Date" in query_tool.description
    assert "q.close" in query_tool.description
    assert (
        "SELECT q.Date, q.close AS qqq_close, s.close AS spy_close FROM qqq_hist AS q "
        "JOIN spy_hist AS s ON q.Date = s.Date ORDER BY q.Date"
    ) in query_tool.description


@pytest.mark.usefixtures("disable_datasource_override")
def test_duckdb_table_inventory_tracks_fresh_and_cached_history_tables(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMIBOT_CACHE_FOLDER", str(tmp_path / "cache"))
    pandas_data = _build_stock_pandas_data()
    first_data = next(iter(pandas_data.values()))
    second_asset = Asset("AGST2", Asset.AssetType.STOCK)
    second_frame = first_data.df.copy()
    second_frame["custom_signal"] = range(len(second_frame.index))
    pandas_data[second_asset] = Data(second_asset, second_frame, timestep="minute")
    _, strategy = PromptCaptureStrategy.run_backtest(
        datasource_class=PandasDataBacktesting,
        backtesting_start=datetime(2025, 1, 6),
        backtesting_end=datetime(2025, 1, 7),
        pandas_data=pandas_data,
        benchmark_asset=None,
        analyze_backtest=False,
        show_plot=False,
        save_tearsheet=False,
        show_tearsheet=False,
        show_indicators=False,
        save_logfile=False,
        show_progress_bar=False,
        quiet_logs=True,
    )

    first = strategy.agents.duckdb.load_history_table(
        symbol="AGST",
        length=3,
        timestep="minute",
        table_name="z_history",
    )
    second = strategy.agents.duckdb.load_history_table(
        symbol="AGST2",
        length=3,
        timestep="minute",
        table_name="a_history",
    )
    cached_first = strategy.agents.duckdb.load_history_table(
        symbol="AGST",
        length=3,
        timestep="minute",
        table_name="z_history",
    )

    first_columns = first["columns"]
    second_columns = second["columns"]
    assert first_columns != second_columns
    assert first["computed_summary"]["symbol"] == "AGST"
    assert first["computed_summary"]["timestep"] == "minute"
    assert first["computed_summary"]["data_window"]["row_count"] == first["row_count"]
    assert first["computed_summary"]["price"]["latest_close"] is not None
    assert cached_first["computed_summary"] == first["computed_summary"]
    assert first["available_tables"] == [
        {"table_name": "z_history", "columns": first_columns},
    ]
    assert second["available_tables"] == [
        {"table_name": "a_history", "columns": second_columns},
        {"table_name": "z_history", "columns": first_columns},
    ]
    assert cached_first["available_tables"] == [
        {"table_name": "a_history", "columns": second_columns},
        {"table_name": "z_history", "columns": first_columns},
    ]
    assert all("available_tables" not in meta for meta in strategy.agents.duckdb._table_meta.values())


@pytest.mark.usefixtures("disable_datasource_override")
def test_run_backtest_explicit_quiet_logs_false_emits_agent_logs(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("LUMIBOT_CACHE_FOLDER", str(tmp_path / "cache"))
    monkeypatch.delenv("BACKTESTING_QUIET_LOGS", raising=False)
    monkeypatch.delenv("BACKTESTING_SHOW_PROGRESS_BAR", raising=False)
    PromptCaptureRuntime.call_count = 0
    PromptCaptureRuntime.last_request = None
    caplog.set_level(logging.INFO, logger="lumibot")

    PromptCaptureStrategy.run_backtest(
        datasource_class=PandasDataBacktesting,
        backtesting_start=datetime(2025, 1, 6),
        backtesting_end=datetime(2025, 1, 7),
        pandas_data=_build_stock_pandas_data(),
        benchmark_asset=None,
        analyze_backtest=False,
        show_plot=False,
        save_tearsheet=False,
        show_tearsheet=False,
        show_indicators=False,
        save_logfile=False,
        show_progress_bar=False,
        quiet_logs=False,
    )

    assert any("[agents] name=research" in record.getMessage() for record in caplog.records)


@pytest.mark.usefixtures("disable_datasource_override")
def test_run_backtest_explicit_quiet_logs_true_suppresses_agent_logs(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("LUMIBOT_CACHE_FOLDER", str(tmp_path / "cache"))
    monkeypatch.delenv("BACKTESTING_QUIET_LOGS", raising=False)
    monkeypatch.delenv("BACKTESTING_SHOW_PROGRESS_BAR", raising=False)
    PromptCaptureRuntime.call_count = 0
    PromptCaptureRuntime.last_request = None
    caplog.set_level(logging.INFO, logger="lumibot")

    PromptCaptureStrategy.run_backtest(
        datasource_class=PandasDataBacktesting,
        backtesting_start=datetime(2025, 1, 6),
        backtesting_end=datetime(2025, 1, 7),
        pandas_data=_build_stock_pandas_data(),
        benchmark_asset=None,
        analyze_backtest=False,
        show_plot=False,
        save_tearsheet=False,
        show_tearsheet=False,
        show_indicators=False,
        save_logfile=False,
        show_progress_bar=False,
        quiet_logs=True,
    )

    assert not any("[agents] name=research" in record.getMessage() for record in caplog.records)


@pytest.mark.usefixtures("disable_datasource_override")
def test_agent_runtime_emits_future_timestamp_warning_without_blocking(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMIBOT_CACHE_FOLDER", str(tmp_path / "cache"))
    FutureTimestampRuntime.call_count = 0
    _, strategy = FutureTimestampWarningStrategy.run_backtest(
        datasource_class=PandasDataBacktesting,
        backtesting_start=datetime(2025, 1, 6),
        backtesting_end=datetime(2025, 1, 7),
        pandas_data=_build_stock_pandas_data(),
        benchmark_asset=None,
        analyze_backtest=False,
        show_plot=False,
        save_tearsheet=False,
        show_tearsheet=False,
        show_indicators=False,
        save_logfile=False,
        show_progress_bar=False,
        quiet_logs=True,
    )
    assert strategy.vars.result_summary == "Used macro data to inspect inflation."
    assert any(warning.get("kind") == "future_timestamp" for warning in strategy.vars.result_warnings)
    warning_logs = list(strategy.vars.warning_logs or [])
    assert any("[agents][observability_warning]" in line for line in warning_logs)


@pytest.mark.usefixtures("disable_datasource_override")
def test_agent_runtime_invalid_asset_type_emits_observability_warning(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMIBOT_CACHE_FOLDER", str(tmp_path / "cache"))
    InvalidAssetTypeRuntime.call_count = 0
    _, strategy = InvalidAssetTypeStrategy.run_backtest(
        datasource_class=PandasDataBacktesting,
        backtesting_start=datetime(2025, 1, 6),
        backtesting_end=datetime(2025, 1, 7),
        pandas_data=_build_stock_pandas_data(),
        benchmark_asset=None,
        analyze_backtest=False,
        show_plot=False,
        save_tearsheet=False,
        show_tearsheet=False,
        show_indicators=False,
        save_logfile=False,
        show_progress_bar=False,
        quiet_logs=True,
    )
    assert strategy.vars.result_summary == "Tool rejected an invalid asset_type without crashing the strategy."
    assert any(warning.get("kind") == "tool_error" for warning in strategy.vars.result_warnings)
    invalid_asset_logs = list(strategy.vars.invalid_asset_logs or [])
    assert any("[agents][observability_warning]" in line for line in invalid_asset_logs)


def test_builtin_docs_search_returns_local_doc_snippets(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMIBOT_CACHE_FOLDER", str(tmp_path / "cache"))
    _, strategy = PromptCaptureStrategy.run_backtest(
        datasource_class=PandasDataBacktesting,
        backtesting_start=datetime(2025, 1, 6),
        backtesting_end=datetime(2025, 1, 7),
        pandas_data=_build_stock_pandas_data(),
        benchmark_asset=None,
        analyze_backtest=False,
        show_plot=False,
        save_tearsheet=False,
        show_tearsheet=False,
        show_indicators=False,
        save_logfile=False,
        show_progress_bar=False,
        quiet_logs=True,
    )
    from lumibot.components.agents import BuiltinTools

    docs_tool = BuiltinTools.docs.search().binder(strategy, strategy.agents)
    result = docs_tool.function(query="benchmark_asset SPY", max_results=3)
    assert result["result_count"] >= 1
    assert any("SPY" in item["snippet"] or "benchmark" in item["snippet"].lower() for item in result["results"])


def test_agent_runtime_wrap_tool_callable_sanitizes_nan_payloads():
    from lumibot.components.agents.runtime import _wrap_tool_callable
    from lumibot.components.agents.schemas import BoundTool

    def nan_result_tool():
        return {
            "price": float("nan"),
            "nested": {"change": float("inf"), "ok": 1.25},
            "rows": [1.0, float("-inf")],
        }

    wrapped = _wrap_tool_callable(
        BoundTool(
            name="nan_result_tool",
            description="Return NaN values for regression testing.",
            function=nan_result_tool,
        )
    )
    result = wrapped()
    assert result["price"] is None
    assert result["nested"]["change"] is None
    assert result["nested"]["ok"] == 1.25
    assert result["rows"] == [1.0, None]
