from __future__ import annotations

from datetime import datetime, timezone

from lumibot.components.agents import AgentManager
from lumibot.components.agents.manager import (
    _tool_safety_requirements_for_trace,
    _tool_surface_entry_for_trace,
)
from lumibot.components.agents.schemas import BoundTool, ToolDefinition


class _Vars(dict):
    def get(self, key, default=None):
        return super().get(key, default)

    def set(self, key, value):
        self[key] = value


class _Strategy:
    is_backtesting = True
    parameters = {}
    vars = _Vars()

    def get_datetime(self):
        return datetime(2026, 1, 2, tzinfo=timezone.utc)

    def log_message(self, *args, **kwargs):
        return None


def sample_tool(symbol: str, quantity: int = 1, side: str = "buy") -> dict[str, object]:
    return {"symbol": symbol, "quantity": quantity, "side": side}


def test_tool_surface_entry_records_signature_annotations_defaults_and_metadata():
    tool = BoundTool(
        name="orders_submit_order",
        description="Submit an order.",
        function=sample_tool,
        source="local",
        metadata={"kind": "builtin", "replay_on_cache": True, "mutates_trading": True},
    )

    entry = _tool_surface_entry_for_trace(tool)

    assert entry["name"] == "orders_submit_order"
    assert entry["description"] == "Submit an order."
    assert entry["source"] == "local"
    assert "symbol" in entry["signature"]
    assert entry["annotations"]["symbol"] == "str"
    assert entry["annotations"]["quantity"] == "int"
    assert entry["defaults"]["quantity"] == 1
    assert entry["defaults"]["side"] == "buy"
    assert entry["metadata"]["mutates_trading"] is True
    assert entry["safety_requirements"] == _tool_safety_requirements_for_trace("orders_submit_order")


def test_tool_surface_entry_handles_tools_without_annotations_or_defaults():
    def no_annotations(value):
        return value

    tool = BoundTool(name="plain_tool", description="Plain.", function=no_annotations)

    entry = _tool_surface_entry_for_trace(tool)

    assert entry["name"] == "plain_tool"
    assert "value" in entry["signature"]
    assert entry["annotations"] == {}
    assert entry["defaults"] == {}
    assert entry["safety_requirements"] == []


class _ReplayCache:
    def compute_key(self, payload):
        return "cache-key"

    def load(self, key):
        return None

    def save(self, key, payload):
        return None


class _DuckDBQueryLayer:
    def __init__(self, strategy):
        self.strategy = strategy

    def get_metrics(self):
        return {}


def test_filter_tools_returns_filtered_mutating_tools_with_reasons(monkeypatch):
    monkeypatch.setattr(
        "lumibot.components.agents.manager._get_replay_imports",
        lambda: (_ReplayCache, lambda value: value),
    )
    monkeypatch.setattr(
        "lumibot.components.agents.manager._get_duckdb_query_layer_class",
        lambda: _DuckDBQueryLayer,
    )
    manager = AgentManager(_Strategy())
    handle = manager.create(
        name="read_only_agent",
        system_prompt="Read only.",
        model="stub-model",
        allow_trading=False,
    )

    available_names = [getattr(tool, "name", "") for tool in handle._tool_inputs]
    filtered_names = [entry["name"] for entry in handle._filtered_tool_inputs]

    assert "orders_submit_order" not in available_names
    assert "orders_submit_order" in filtered_names
    submit_entry = next(entry for entry in handle._filtered_tool_inputs if entry["name"] == "orders_submit_order")
    assert submit_entry["reason"] == "filtered because allow_trading is false"
    assert submit_entry["metadata"]["mutates_trading"] is True


def test_trace_request_payload_uses_rich_tool_surface_without_mutating_cache_payload(monkeypatch):
    monkeypatch.setattr(
        "lumibot.components.agents.manager._get_replay_imports",
        lambda: (_ReplayCache, lambda value: value),
    )
    monkeypatch.setattr(
        "lumibot.components.agents.manager._get_duckdb_query_layer_class",
        lambda: _DuckDBQueryLayer,
    )
    manager = AgentManager(_Strategy())
    custom_tool = ToolDefinition(
        name="custom_tool",
        description="Custom.",
        binder=lambda strategy, manager: BoundTool(
            name="custom_tool",
            description="Custom.",
            function=sample_tool,
            source="custom",
        ),
    )
    handle = manager.create(
        name="trace_agent",
        system_prompt="Trace.",
        model="stub-model",
        tools=[custom_tool],
        include_builtin_tools=False,
        allow_trading=True,
    )
    cache_payload = {
        "model": "stub-model",
        "tool_surface": [{"name": "custom_tool", "metadata": {"kind": "cache-only"}}],
    }

    trace_payload = handle._trace_request_payload(cache_payload)

    assert cache_payload["tool_surface"] == [{"name": "custom_tool", "metadata": {"kind": "cache-only"}}]
    assert trace_payload["tool_surface"][0]["name"] == "custom_tool"
    assert trace_payload["tool_surface"][0]["annotations"]["symbol"] == "str"
    assert trace_payload["tool_surface"][0]["defaults"]["quantity"] == 1
    assert trace_payload["tool_availability"]["available"] == [
        {"name": "custom_tool", "source": "custom", "metadata": {}, "reason": "available"}
    ]
    assert trace_payload["tool_availability"]["filtered"] == []
