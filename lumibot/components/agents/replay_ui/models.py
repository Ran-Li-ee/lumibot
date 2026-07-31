from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .redaction import redact_public_preview, redact_sensitive

PRIORITY_EXPLANATION = (
    "Treat this as the agent-specific system prompt that has priority within the replayed request material."
)

SIDECAR_PUBLIC_METADATA_KEYS = ("event_id", "byte_count", "compression", "sha256", "available")


def _tool_name(tool: Any) -> str | None:
    if isinstance(tool, dict):
        name = tool.get("name")
    else:
        name = getattr(tool, "name", None)
    return name if isinstance(name, str) and name else None


def _redact_public_metadata(value: Any) -> Any:
    return redact_public_preview(value, max_text=256, max_items=20, max_depth=2)


def _public_sidecar_metadata(sidecar: dict[str, Any], event_id: str) -> dict[str, Any]:
    public = {
        key: _redact_public_metadata(sidecar[key])
        for key in SIDECAR_PUBLIC_METADATA_KEYS
        if key in sidecar
    }
    public.setdefault("event_id", redact_sensitive(event_id))
    if "available" in public:
        public["available"] = bool(public["available"])
    return public


def _public_boundary_payload_meta(payload_meta: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): _redact_public_metadata(value)
        for key, value in payload_meta.items()
        if str(key) != "sidecar_path"
    }


def _string_list_metadata(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [redact_sensitive(item) for item in value if isinstance(item, str)]


def _public_boundary_tool_call(call: Any) -> dict[str, Any]:
    if not isinstance(call, dict):
        return {}
    public: dict[str, Any] = {}
    call_id = call.get("call_id")
    if isinstance(call_id, str) and call_id:
        public["call_id"] = redact_sensitive(call_id)
    events = _string_list_metadata(call.get("events"))
    if events:
        public["events"] = events
    return public


def _public_boundary_tool_batch(batch: Any) -> dict[str, Any]:
    if not isinstance(batch, dict):
        return {}
    public: dict[str, Any] = {}
    batch_id = batch.get("tool_batch_id")
    if isinstance(batch_id, str) and batch_id:
        public["tool_batch_id"] = redact_sensitive(batch_id)
    tool_calls = batch.get("tool_calls")
    if isinstance(tool_calls, list):
        public["tool_calls"] = [
            public_call
            for call in tool_calls
            if (public_call := _public_boundary_tool_call(call))
        ]
    return public


def _public_boundary_model_turn(turn: Any) -> dict[str, Any]:
    if not isinstance(turn, dict):
        return {}
    public: dict[str, Any] = {}
    turn_id = turn.get("model_turn_id")
    if isinstance(turn_id, str) and turn_id:
        public["model_turn_id"] = redact_sensitive(turn_id)
    public["request_response_events"] = _string_list_metadata(turn.get("request_response_events"))
    tool_batches = turn.get("tool_batches")
    public["tool_batches"] = (
        [
            public_batch
            for batch in tool_batches
            if (public_batch := _public_boundary_tool_batch(batch))
        ]
        if isinstance(tool_batches, list)
        else []
    )
    return public


@dataclass
class ToolCallReplay:
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_result: Any = None
    error: Any = None
    human_explanation: str | None = None
    timestamp: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": redact_sensitive(self.arguments),
            "raw_result": redact_public_preview(self.raw_result),
            "error": redact_public_preview(self.error),
            "human_explanation": redact_sensitive(self.human_explanation),
            "timestamp": self.timestamp,
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
class BoundaryEventReplay:
    id: str
    transition: str
    model_turn_id: str | None = None
    tool_batch_id: str | None = None
    call_id: str | None = None
    status: str | None = None
    timestamp: str | None = None
    payload: Any = None
    payload_meta: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    sidecar: dict[str, Any] | None = None

    def to_public_dict(self) -> dict[str, Any]:
        public = {
            "id": redact_sensitive(self.id),
            "transition": redact_sensitive(self.transition),
            "model_turn_id": redact_sensitive(self.model_turn_id),
            "tool_batch_id": redact_sensitive(self.tool_batch_id),
            "call_id": redact_sensitive(self.call_id),
            "status": redact_sensitive(self.status),
            "timestamp": redact_sensitive(self.timestamp),
            "payload": redact_public_preview(self.payload),
            "payload_meta": _public_boundary_payload_meta(self.payload_meta),
            "summary": redact_sensitive(self.summary),
        }
        if self.sidecar is not None:
            public["sidecar"] = _public_sidecar_metadata(self.sidecar, self.id)
        return public


@dataclass
class BoundaryTraceReplay:
    available: bool = False
    schema_version: int | None = None
    events: list[BoundaryEventReplay] = field(default_factory=list)
    model_turns: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[Any] = field(default_factory=list)
    message: str = (
        "This trace does not contain 10-step boundary trace data. "
        "It may have been created before boundary tracing was added."
    )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "schema_version": self.schema_version,
            "events": [event.to_public_dict() for event in self.events],
            "model_turns": [
                public_turn
                for turn in self.model_turns
                if (public_turn := _public_boundary_model_turn(turn))
            ],
            "diagnostics": redact_sensitive(self.diagnostics),
            "message": redact_sensitive(self.message),
        }


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
    boundary_trace: BoundaryTraceReplay = field(default_factory=BoundaryTraceReplay)

    def input_material(self) -> dict[str, Any]:
        context = self.request.get("context") or {}
        runtime_context = self.request.get("runtime_context") or {}
        tool_surface = self.request.get("tool_surface") or []
        tool_names = [name for name in (_tool_name(tool) for tool in tool_surface) if name is not None]
        context_keys = sorted(str(key) for key in context.keys()) if isinstance(context, dict) else []

        return {
            "base_system_prompt": redact_sensitive(self.request.get("base_system_prompt")),
            "effective_system_prompt": redact_sensitive(self.request.get("effective_system_prompt")),
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
            "boundary_trace": self.boundary_trace.to_public_dict(),
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
    artifacts: dict[str, Any] = field(default_factory=dict)
    summary: Any = None
    warnings: list[Any] = field(default_factory=list)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": redact_sensitive(self.label),
            "strategy_name": redact_sensitive(self.strategy_name),
            "system_runs": [system_run.to_public_dict() for system_run in self.system_runs],
            "artifacts": redact_sensitive(self.artifacts),
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
