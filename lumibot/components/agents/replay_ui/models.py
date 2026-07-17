from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .redaction import redact_public_preview, redact_sensitive

PRIORITY_EXPLANATION = (
    "Treat this as the agent-specific system prompt that has priority within the replayed request material."
)


def _tool_name(tool: Any) -> str | None:
    if isinstance(tool, dict):
        name = tool.get("name")
    else:
        name = getattr(tool, "name", None)
    return name if isinstance(name, str) and name else None


def _public_tool_surface(tool_surface: Any) -> list[dict[str, Any]]:
    if not isinstance(tool_surface, list):
        return []

    public_tools: list[dict[str, Any]] = []
    for tool in tool_surface:
        if not isinstance(tool, dict):
            continue
        name = _tool_name(tool)
        if name is None:
            continue

        public_tool: dict[str, Any] = {"name": redact_sensitive(name)}
        for key in (
            "description",
            "signature",
            "annotations",
            "parameters",
            "schema",
            "input_schema",
            "source",
            "metadata",
            "defaults",
            "safety_requirements",
        ):
            if key in tool:
                public_tool[key] = redact_sensitive(tool.get(key))
        public_tools.append(public_tool)

    return public_tools


def _public_tool_availability(tool_availability: Any) -> dict[str, Any]:
    if not isinstance(tool_availability, dict):
        return {"available": [], "filtered": []}

    available = tool_availability.get("available")
    filtered = tool_availability.get("filtered")
    return {
        "available": redact_sensitive(available) if isinstance(available, list) else [],
        "filtered": redact_sensitive(filtered) if isinstance(filtered, list) else [],
    }


@dataclass
class ToolCallReplay:
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_result: Any = None
    error: Any = None
    human_explanation: str | None = None
    timestamp: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": redact_sensitive(self.arguments),
            "raw_result": redact_public_preview(self.raw_result),
            "error": redact_public_preview(self.error),
            "human_explanation": redact_sensitive(self.human_explanation),
            "timestamp": self.timestamp,
            "diagnostics": redact_public_preview(self.diagnostics),
        }


@dataclass
class ToolBatch:
    batch_index: int
    calls: list[ToolCallReplay] = field(default_factory=list)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "batch_index": self.batch_index,
            "calls": [call.to_public_dict() for call in self.calls],
        }


@dataclass
class AgentDependency:
    source_label: str
    target_agent: str
    source_type: str = "context"
    source_agent: str | None = None
    source_status: str = "unresolved_source"

    def to_public_dict(self) -> dict[str, Any]:
        public = {
            "source_label": redact_sensitive(self.source_label),
            "target_agent": redact_sensitive(self.target_agent),
            "source_type": redact_sensitive(self.source_type),
            "source_status": redact_sensitive(self.source_status),
        }
        if self.source_agent:
            public["source_agent"] = redact_sensitive(self.source_agent)
        return public


@dataclass
class AgentReplay:
    id: str
    name: str
    model: str
    trace_path: str
    request: dict[str, Any] = field(default_factory=dict)
    tool_batches: list[ToolBatch] = field(default_factory=list)
    summary: Any = None
    warnings: list[Any] = field(default_factory=list)
    raw_trace: Any = None
    dependencies: list[AgentDependency] = field(default_factory=list)

    def input_material(self) -> dict[str, Any]:
        context = self.request.get("context") or {}
        runtime_context = self.request.get("runtime_context") or {}
        tool_surface = self.request.get("tool_surface") or []
        available_tools = _public_tool_surface(tool_surface)
        tool_names = [str(tool["name"]) for tool in available_tools if tool.get("name")]
        context_keys = sorted(str(key) for key in context.keys()) if isinstance(context, dict) else []

        return {
            "base_system_prompt": redact_sensitive(self.request.get("base_system_prompt")),
            "user_system_prompt_heading": "USER SYSTEM PROMPT:",
            "priority_explanation": PRIORITY_EXPLANATION,
            "agent_system_prompt": redact_sensitive(self.request.get("user_system_prompt")),
            "task_prompt": redact_sensitive(self.request.get("task_prompt")),
            "context": redact_sensitive(context),
            "context_keys": context_keys,
            "run_mode": redact_sensitive(runtime_context.get("mode")) if isinstance(runtime_context, dict) else None,
            "current_time": redact_sensitive(runtime_context.get("current_datetime"))
            if isinstance(runtime_context, dict)
            else None,
            "available_tool_count": len(tool_names),
            "available_tool_names": tool_names,
            "available_tools": available_tools,
            "tool_availability": _public_tool_availability(self.request.get("tool_availability")),
        }

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "model": self.model,
            "trace_path": redact_sensitive(self.trace_path),
            "input_material": self.input_material(),
            "tool_batches": [batch.to_public_dict() for batch in self.tool_batches],
            "summary": redact_sensitive(self.summary),
            "warnings": redact_sensitive(self.warnings),
            "dependencies": [dependency.to_public_dict() for dependency in self.dependencies],
        }


@dataclass
class SystemRun:
    id: str
    current_datetime: str
    mode: str
    agents: list[AgentReplay] = field(default_factory=list)
    dependencies: list[AgentDependency] = field(default_factory=list)
    name: str | None = None
    summary: Any = None
    warnings: list[Any] = field(default_factory=list)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "current_datetime": redact_sensitive(self.current_datetime),
            "mode": redact_sensitive(self.mode),
            "agents": [agent.to_public_dict() for agent in self.agents],
            "dependencies": [dependency.to_public_dict() for dependency in self.dependencies],
            "summary": redact_sensitive(self.summary),
            "warnings": redact_sensitive(self.warnings),
        }


@dataclass
class ReplayRun:
    id: str
    label: str
    strategy_name: str = "unknown-strategy"
    system_runs: list[SystemRun] = field(default_factory=list)
    summary: Any = None
    warnings: list[Any] = field(default_factory=list)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": redact_sensitive(self.label),
            "strategy_name": redact_sensitive(self.strategy_name),
            "system_runs": [system_run.to_public_dict() for system_run in self.system_runs],
            "summary": redact_sensitive(self.summary),
            "warnings": redact_sensitive(self.warnings),
        }


@dataclass
class ReplayDataset:
    runs: list[ReplayRun] = field(default_factory=list)
    generated_at: str | None = None
    source_path: str | None = None
    warnings: list[Any] = field(default_factory=list)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "runs": [run.to_public_dict() for run in self.runs],
            "generated_at": self.generated_at,
            "source_path": redact_sensitive(self.source_path),
            "warnings": redact_sensitive(self.warnings),
        }
