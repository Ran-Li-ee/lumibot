import json
import re
import sys
import threading
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server


def _write_trace(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


# create_app imports lumibot, which installs entities.* lazy aliases; remove
# them so Playwright stack inspection does not trigger unrelated module loads.
def _remove_lumibot_compat_aliases() -> None:
    for module_name in list(sys.modules):
        if module_name == "entities" or module_name.startswith("entities."):
            sys.modules.pop(module_name, None)


def _agent_trace(agent_name: str, context: dict, summary: str) -> dict:
    return {
        "agent": agent_name,
        "model": "openai/test-model",
        "request": {
            "base_system_prompt": "Base prompt",
            "user_system_prompt": f"{agent_name} system prompt",
            "task_prompt": f"{agent_name} task prompt",
            "context": context,
            "runtime_context": {
                "mode": "backtesting",
                "current_datetime": "2026-04-07T09:30:00-04:00",
                "strategy_name": "NonDemoWorkflow",
            },
            "tool_surface": [{"name": "market_last_price"}],
        },
        "events": [
            {
                "kind": "tool_call",
                "tool_name": "market_last_price",
                "payload": {"symbol": "SPY"},
                "timestamp": "2026-04-07T09:30:01-04:00",
            },
            {
                "kind": "tool_result",
                "tool_name": "market_last_price",
                "payload": {"symbol": "SPY", "price": 500.0},
                "timestamp": "2026-04-07T09:30:02-04:00",
            },
        ],
        "summary": summary,
        "warnings": [],
    }


def _public_agent(agent_id: str, boundary_trace: dict | None = None) -> dict:
    return {
        "id": agent_id,
        "name": agent_id,
        "model": "openai/test-model",
        "trace_path": f"/tmp/{agent_id}/trace.json",
        "input_material": {
            "base_system_prompt": "Base prompt",
            "agent_system_prompt": f"{agent_id} system prompt",
            "task_prompt": f"{agent_id} task prompt",
            "context": {},
            "context_keys": [],
            "available_tool_count": 0,
            "available_tool_names": [],
        },
        "tool_batches": [],
        "summary": f"{agent_id} summary",
        "warnings": [],
        "dependencies": [],
        "boundary_trace": boundary_trace
        if boundary_trace is not None
        else {
            "available": False,
            "events": [],
            "model_turns": [],
            "message": "This trace does not contain 10-step boundary trace data.",
        },
    }


def _public_dataset(agents: list[dict], dependencies: list[dict]) -> dict:
    return {
        "runs": [
            {
                "id": "run-1",
                "label": "Synthetic run",
                "strategy_name": "SyntheticStrategy",
                "system_runs": [
                    {
                        "id": "system-1",
                        "name": "Synthetic system",
                        "current_datetime": "2026-04-07T09:30:00-04:00",
                        "mode": "backtesting",
                        "agents": agents,
                        "dependencies": dependencies,
                        "summary": None,
                        "warnings": [],
                    }
                ],
                "summary": None,
                "warnings": [],
            }
        ],
        "generated_at": "2026-04-07T09:31:00-04:00",
        "source_path": "/tmp/synthetic",
        "warnings": [],
    }


def _rects_overlap(a, b):
    return not (
        a["x"] + a["width"] <= b["x"]
        or b["x"] + b["width"] <= a["x"]
        or a["y"] + a["height"] <= b["y"]
        or b["y"] + b["height"] <= a["y"]
    )


def _sampled_workflow_arrow_card_overlaps(page) -> list[dict]:
    return page.locator("#workflowGraph").evaluate(
        """(graph) => {
            const diagram = graph.querySelector('.workflow-diagram');
            if (!diagram) {
                return [{ error: 'missing workflow diagram' }];
            }
            const diagramBox = diagram.getBoundingClientRect();
            const nodeBoxes = Array.from(graph.querySelectorAll('.graph-node')).map((node) => {
                const box = node.getBoundingClientRect();
                return {
                    id: node.getAttribute('data-agent-id'),
                    label: node.querySelector('.agent-name')?.textContent?.trim() || node.textContent.trim(),
                    x: box.left - diagramBox.left,
                    y: box.top - diagramBox.top,
                    width: box.width,
                    height: box.height,
                };
            });
            return Array.from(graph.querySelectorAll('.workflow-arrow')).flatMap((arrow) => {
                const totalLength = arrow.getTotalLength();
                const sampleCount = Math.max(24, Math.ceil(totalLength / 8));
                const source = arrow.getAttribute('data-source-agent-id');
                const target = arrow.getAttribute('data-target-agent-id');
                const overlaps = [];
                for (let index = 0; index <= sampleCount; index += 1) {
                    const point = arrow.getPointAtLength((totalLength * index) / sampleCount);
                    nodeBoxes
                        .filter((node) => node.id !== source && node.id !== target)
                        .filter((node) => (
                            point.x > node.x
                            && point.x < node.x + node.width
                            && point.y > node.y
                            && point.y < node.y + node.height
                        ))
                        .forEach((node) => {
                            overlaps.push({
                                source,
                                target,
                                node: node.id,
                                label: node.label,
                                sample: index,
                                x: Math.round(point.x),
                                y: Math.round(point.y),
                            });
                        });
                }
                return overlaps;
            });
        }"""
    )


def _assert_workflow_arrows_do_not_cross_unrelated_agent_cards(page) -> None:
    assert _sampled_workflow_arrow_card_overlaps(page) == []


def _write_non_demo_workflow(root: Path) -> None:
    traces = root / "traces"
    _write_trace(
        traces / "macro_agent" / "trace.json",
        _agent_trace("macro_agent", {"date": "2026-04-07"}, "Macro summary"),
    )
    _write_trace(
        traces / "news_agent" / "trace.json",
        _agent_trace("news_agent", {"date": "2026-04-07"}, "News summary"),
    )
    _write_trace(
        traces / "valuation_agent" / "trace.json",
        _agent_trace("valuation_agent", {"date": "2026-04-07"}, "Valuation summary"),
    )
    _write_trace(
        traces / "portfolio_agent" / "trace.json",
        _agent_trace(
            "portfolio_agent",
            {
                "date": "2026-04-07",
                "macro_summary": "Macro summary",
                "news_summary": "News summary",
            },
            "Portfolio summary",
        ),
    )
    _write_trace(
        traces / "trader" / "trace.json",
        _agent_trace(
            "trader",
            {
                "date": "2026-04-07",
                "portfolio_summary": "Portfolio summary",
                "valuation_summary": "Valuation summary",
            },
            "Trader summary",
        ),
    )


@pytest.fixture
def replay_url(tmp_path):
    _remove_lumibot_compat_aliases()
    from lumibot.components.agents.replay_ui.server import create_app

    root = tmp_path / "agent_runtime"
    _write_non_demo_workflow(root)
    app = create_app(root)
    _remove_lumibot_compat_aliases()
    server = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture
def page():
    _remove_lumibot_compat_aliases()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1366, "height": 900})
        yield page
        _remove_lumibot_compat_aliases()
        browser.close()


def test_default_view_shows_workflow_overview_without_agent_detail(page, replay_url):
    page.goto(replay_url)

    expect(page.locator("#runSelect")).to_have_count(0)
    expect(page.locator("#backtestLabel")).to_have_count(0)
    expect(page.locator("#strategySelect")).to_have_value("NonDemoWorkflow")
    expect(page.locator("#backtestRunSelect")).to_have_value(re.compile(r"trace-root-"))
    expect(page.locator("#systemRunSelect")).to_have_value(
        "backtesting|2026-04-07T09:30:00-04:00|NonDemoWorkflow"
    )
    expect(page.locator("#workflowGraph")).to_contain_text("macro_agent")
    expect(page.locator("#workflowGraph")).to_contain_text("valuation_agent")
    expect(page.locator("#overviewArea")).to_contain_text("System Overview")
    expect(page.locator("#overviewArea")).to_contain_text("5 agents")
    expect(page.locator("#inputArea")).to_be_hidden()
    expect(page.locator("#toolArea")).to_be_hidden()
    expect(page.locator("#summaryArea")).to_be_hidden()


def test_workflow_graph_can_be_hidden_without_hiding_selectors_or_details(page, replay_url):
    dataset = _public_dataset([_public_agent("source"), _public_agent("target")], [])
    dataset["runs"][0]["system_runs"].append(
        {
            "id": "system-2",
            "name": "Synthetic system 2",
            "current_datetime": "2026-04-08T09:30:00-04:00",
            "mode": "backtesting",
            "agents": [_public_agent("second_day_agent")],
            "dependencies": [],
            "summary": None,
            "warnings": [],
        }
    )
    page.route("**/api/dataset", lambda route: route.fulfill(json=dataset))

    page.goto(replay_url)

    expect(page.locator("#workflowGraph")).to_be_visible()
    expect(page.locator("#workflowGraph")).to_contain_text("source")
    expect(page.get_by_role("button", name="Hide Graph")).to_be_visible()

    page.get_by_role("button", name="Hide Graph").click()

    expect(page.locator("#workflowGraph")).to_be_hidden()
    expect(page.locator("#strategySelect")).to_be_visible()
    expect(page.locator("#backtestRunSelect")).to_be_visible()
    expect(page.locator("#systemRunSelect")).to_be_visible()
    expect(page.locator("#overviewArea")).to_be_visible()
    expect(page.get_by_role("button", name="Show Graph")).to_be_visible()

    page.locator("#systemRunSelect").select_option("system-2")

    expect(page.locator("#workflowGraph")).to_be_hidden()
    expect(page.locator("#overviewArea")).to_contain_text("Synthetic system 2")

    page.get_by_role("button", name="Show Graph").click()

    expect(page.locator("#workflowGraph")).to_be_visible()
    expect(page.locator("#workflowGraph")).to_contain_text("second_day_agent")


def test_workflow_graph_uses_non_demo_agents_and_dependencies(page, replay_url):
    page.goto(replay_url)

    graph = page.locator("#workflowGraph")
    expect(graph).to_contain_text("macro_agent")
    expect(graph).to_contain_text("news_agent")
    expect(graph).to_contain_text("valuation_agent")
    expect(graph).to_contain_text("portfolio_agent")
    expect(graph).to_contain_text("trader")
    expect(page.locator("#workflowGraph svg.workflow-arrow-layer .workflow-arrow")).to_have_count(4, timeout=5000)
    expect(page.locator("#workflowGraph .graph-edge")).to_have_count(0)
    expect(graph).not_to_contain_text("growth_agent")
    expect(graph).not_to_contain_text("thoughtful_disagreement")


def test_ambiguous_context_source_stays_unresolved_in_workflow_graph(page, replay_url):
    dataset = _public_dataset(
        [_public_agent("research_agent"), _public_agent("equity_research_agent"), _public_agent("trader")],
        [
            {
                "source_label": "context:research_summary",
                "target_agent": "trader",
                "source_type": "context",
                "source_status": "ambiguous_source",
            }
        ],
    )
    page.route("**/api/dataset", lambda route: route.fulfill(json=dataset))

    page.goto(replay_url)

    expect(page.locator("#workflowGraph")).to_contain_text(
        "1 dependencies could not be drawn because their source or target was unresolved."
    )
    expect(page.locator("#workflowGraph svg.workflow-arrow-layer .workflow-arrow")).to_have_count(0, timeout=5000)
    expect(page.locator("#workflowGraph .graph-edge")).to_have_count(0)
    expect(page.locator("#workflowGraph")).not_to_contain_text("research_agent -> trader")


def test_context_source_is_not_inferred_from_key_name(page, replay_url):
    dataset = _public_dataset(
        [_public_agent("research_agent"), _public_agent("equity_research_agent"), _public_agent("trader")],
        [{"source_label": "context:equity_research_summary", "target_agent": "trader", "source_type": "context"}],
    )
    page.route("**/api/dataset", lambda route: route.fulfill(json=dataset))

    page.goto(replay_url)

    expect(page.locator("#workflowGraph")).to_contain_text(
        "1 dependencies could not be drawn because their source or target was unresolved."
    )
    expect(page.locator("#workflowGraph svg.workflow-arrow-layer .workflow-arrow")).to_have_count(0, timeout=5000)
    expect(page.locator("#workflowGraph .graph-edge")).to_have_count(0)
    expect(page.locator("#workflowGraph")).not_to_contain_text("equity_research_agent -> trader")


def test_explicit_source_agent_renders_dependency_arrow(page, replay_url):
    dataset = _public_dataset(
        [_public_agent("research_agent"), _public_agent("trader")],
        [
            {
                "source_label": "context:research_summary",
                "target_agent": "trader",
                "source_type": "context",
                "source_agent": "research_agent",
                "source_status": "resolved_summary",
            }
        ],
    )
    page.route("**/api/dataset", lambda route: route.fulfill(json=dataset))

    page.goto(replay_url)

    expect(page.locator("#workflowGraph svg.workflow-arrow-layer")).to_have_count(1)
    expect(page.locator("#workflowGraph svg.workflow-arrow-layer .workflow-arrow")).to_have_count(1, timeout=5000)
    expect(page.locator("#workflowGraph .graph-edge")).to_have_count(0)

    page.get_by_role("button", name=re.compile("trader")).click()

    expect(page.locator("#summaryArea")).to_contain_text("Connected Dependencies")
    expect(page.locator("#summaryArea")).to_contain_text("context:research_summary")
    expect(page.locator("#summaryArea")).to_contain_text("trader")


def test_explicit_source_agent_uses_exact_agent_identity(page, replay_url):
    dataset = _public_dataset(
        [_public_agent("research"), _public_agent("research_summary"), _public_agent("trader")],
        [
            {
                "source_label": "context:research_output",
                "target_agent": "trader",
                "source_type": "context",
                "source_agent": "research_summary",
                "source_status": "resolved_summary",
            }
        ],
    )
    page.route("**/api/dataset", lambda route: route.fulfill(json=dataset))

    page.goto(replay_url)

    expect(page.locator("#workflowGraph svg.workflow-arrow-layer")).to_have_count(1)
    expect(page.locator("#workflowGraph svg.workflow-arrow-layer .workflow-arrow")).to_have_count(1, timeout=5000)
    expect(page.locator("#workflowGraph .graph-edge")).to_have_count(0)
    expect(page.locator("#workflowGraph")).not_to_contain_text("research -> trader")

    page.get_by_role("button", name=re.compile("trader")).click()

    expect(page.locator('#workflowGraph .graph-node.connected[data-agent-id="research_summary"]')).to_have_count(1)
    expect(page.locator('#workflowGraph .graph-node.connected[data-agent-id="research"]')).to_have_count(0)
    expect(page.locator("#summaryArea")).to_contain_text("Connected Dependencies")
    expect(page.locator("#summaryArea")).to_contain_text("context:research_output")


def test_missing_dependency_target_is_marked_in_graph_without_dependency_list(page, replay_url):
    dataset = _public_dataset(
        [_public_agent("research_agent"), _public_agent("trader")],
        [{"source_label": "context:risk_summary", "target_agent": "risk_agent", "source_type": "context"}],
    )
    page.route("**/api/dataset", lambda route: route.fulfill(json=dataset))

    page.goto(replay_url)

    expect(page.locator("#workflowGraph")).to_contain_text(
        "1 dependencies could not be drawn because their source or target was unresolved."
    )
    expect(page.locator("#workflowGraph svg.workflow-arrow-layer .workflow-arrow")).to_have_count(0, timeout=5000)
    expect(page.locator("#workflowGraph .graph-edge")).to_have_count(0)
    expect(page.locator("#dependencyList")).to_have_count(0)


def test_clicking_agent_switches_right_panel_to_detail_and_overview_button_resets(page, replay_url):
    page.goto(replay_url)

    page.get_by_role("button", name=re.compile("portfolio_agent")).click()
    expect(page.locator("#overviewArea")).to_be_hidden()
    expect(page.locator("#inputArea")).to_be_visible()
    expect(page.locator("#inputArea")).to_contain_text("Input Material")
    expect(page.locator("#toolArea")).to_contain_text("Tool Calls")
    expect(page.locator("#summaryArea")).to_contain_text("Portfolio summary")
    expect(page.locator(".graph-node.active")).to_contain_text("portfolio_agent")

    page.get_by_role("button", name="System Overview").click()
    expect(page.locator("#overviewArea")).to_be_visible()
    expect(page.locator("#overviewArea")).to_contain_text("System Overview")
    expect(page.locator("#inputArea")).to_be_hidden()
    expect(page.locator(".graph-node.active")).to_have_count(0)


def test_agent_detail_cards_and_subcards_are_collapsible(page, replay_url):
    agent = _public_agent("research_agent")
    agent["input_material"]["available_tool_names"] = ["market_last_price", "duckdb_query"]
    agent["input_material"]["available_tool_count"] = 2
    agent["input_material"]["context"] = {"symbol": "SPY"}
    agent["input_material"]["context_keys"] = ["symbol"]
    agent["tool_batches"] = [
        {
            "batch_index": 1,
            "calls": [
                {
                    "tool_name": "market_last_price",
                    "arguments": {"symbol": "SPY"},
                    "raw_result": {"price": 500},
                    "error": None,
                    "human_explanation": "Looked up SPY price.",
                    "timestamp": "2026-04-07T09:30:01-04:00",
                }
            ],
        },
        {
            "batch_index": 2,
            "calls": [
                {
                    "tool_name": "duckdb_query",
                    "arguments": {"sql": "SELECT 1"},
                    "raw_result": {"rows": [{"one": 1}]},
                    "error": None,
                    "human_explanation": "Calculated a small SQL result.",
                    "timestamp": "2026-04-07T09:30:02-04:00",
                }
            ],
        },
    ]
    dataset = _public_dataset([agent], [])
    page.route("**/api/dataset", lambda route: route.fulfill(json=dataset))

    page.goto(replay_url)
    page.get_by_role("button", name=re.compile("research_agent")).click()

    input_card = page.locator("#inputArea details.collapsible-section").filter(has_text="Input Material")
    tool_card = page.locator("#toolArea details.collapsible-section").filter(has_text="Tool Calls")
    summary_card = page.locator("#summaryArea details.collapsible-section").filter(has_text="Final Summary")
    expect(input_card).not_to_have_attribute("open", "")
    expect(tool_card).to_have_attribute("open", "")
    expect(summary_card).to_have_attribute("open", "")

    expect(page.locator("#inputArea pre").filter(has_text="Base prompt")).to_be_hidden()
    input_card.locator("summary").first.click()

    base_prompt = page.locator("#inputArea details.collapsible-subsection").filter(has_text="Base System Prompt")
    expect(base_prompt).not_to_have_attribute("open", "")
    base_prompt.locator("summary").click()
    expect(base_prompt).to_contain_text("Base prompt")

    batch_one = page.locator("#toolArea details.collapsible-subsection").filter(has_text="Batch 1")
    batch_two = page.locator("#toolArea details.collapsible-subsection").filter(has_text="Batch 2")
    expect(batch_one).to_have_attribute("open", "")
    expect(batch_two).not_to_have_attribute("open", "")
    expect(page.locator("#toolArea pre").filter(has_text="Looked up SPY price.")).to_be_visible()
    expect(page.locator("#toolArea pre").filter(has_text="Calculated a small SQL result.")).to_be_hidden()

    batch_two.locator("summary").click()
    expect(page.locator("#toolArea pre").filter(has_text="Calculated a small SQL result.")).to_be_visible()

    tool_card.locator("summary").first.click()
    expect(page.locator("#toolArea pre").filter(has_text="Looked up SPY price.")).to_be_hidden()


def test_browser_renders_boundary_trace_for_selected_agent(page, replay_url):
    agent = _public_agent(
        "growth_agent",
        boundary_trace={
            "available": True,
            "schema_version": 1,
            "message": "Boundary trace data is available for this agent run.",
            "events": [
                {
                    "id": "event-b09",
                    "transition": "B09_ADK_TO_LITELLM",
                    "status": "success",
                    "summary": {
                        "label": "ADK builds model request",
                        "source": "Google ADK",
                        "target": "LiteLLM",
                        "badges": ["success", "complete"],
                        "preview": "1 model message(s)",
                    },
                    "payload": {"messages": [{"role": "user", "content": "hello"}]},
                    "payload_meta": {"semantic_completeness": "complete"},
                },
                {
                    "id": "event-b03",
                    "transition": "B03_ADK_TO_FUNCTION_TOOL",
                    "status": "success",
                    "summary": {
                        "label": "ADK dispatches FunctionTool",
                        "source": "Google ADK",
                        "target": "ADK FunctionTool",
                        "badges": ["success", "complete"],
                        "preview": "market_last_price",
                    },
                    "payload": {"tool_name": "market_last_price", "args": {"symbol": "SPY"}},
                    "payload_meta": {"semantic_completeness": "complete"},
                },
                {
                    "id": "event-sidecar",
                    "transition": "B10_LITELLM_TO_PROVIDER",
                    "status": "success",
                    "summary": {
                        "label": "LiteLLM sends provider request",
                        "source": "LiteLLM",
                        "target": "Model provider",
                        "badges": ["success", "sidecar"],
                        "preview": "sidecar preview only",
                    },
                    "payload": {"preview": "small"},
                    "payload_meta": {"semantic_completeness": "partial"},
                    "sidecar": {"available": True, "event_id": "event-sidecar"},
                },
            ],
            "model_turns": [
                {
                    "model_turn_id": "turn-1",
                    "request_response_events": ["event-b09", "event-sidecar"],
                    "tool_batches": [
                        {
                            "tool_batch_id": "turn-1:batch:0001",
                            "tool_calls": [
                                {
                                    "call_id": "call-1",
                                    "tool_name": "market_last_price",
                                    "events": ["event-b03"],
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    )
    dataset = _public_dataset([agent], [])
    page.route("**/api/dataset", lambda route: route.fulfill(json=dataset))

    page.goto(replay_url)
    page.get_by_role("button", name=re.compile("growth_agent")).click()

    expect(page.get_by_text("LLM <-> Tool Boundary Trace")).to_be_visible()
    expect(page.get_by_text("Model Turn 1")).to_be_visible()
    expect(page.get_by_text("Model Request / Response")).to_be_visible()
    expect(page.get_by_text("B09_ADK_TO_LITELLM")).to_be_visible()
    expect(page.get_by_text("B10_LITELLM_TO_PROVIDER")).to_be_visible()
    expect(page.get_by_text("B03_ADK_TO_FUNCTION_TOOL")).to_be_visible()
    expect(page.get_by_text("Google ADK -> LiteLLM")).to_be_visible()
    expect(page.locator(".boundary-call-heading").get_by_text("market_last_price")).to_be_visible()
    expect(page.locator(".boundary-sidecar-button")).to_have_count(1)


def test_browser_renders_boundary_trace_legacy_fallback(page, replay_url):
    dataset = _public_dataset([_public_agent("legacy_agent")], [])
    page.route("**/api/dataset", lambda route: route.fulfill(json=dataset))

    page.goto(replay_url)
    page.get_by_role("button", name=re.compile("legacy_agent")).click()

    expect(page.get_by_text("This trace does not contain 10-step boundary trace data")).to_be_visible()


def test_clicking_agent_still_shows_detail_without_dependency_panel(page, replay_url):
    page.goto(replay_url)
    expect(page.locator("#workflowGraph svg.workflow-arrow-layer .workflow-arrow")).to_have_count(4, timeout=5000)

    page.get_by_role("button", name=re.compile("portfolio_agent")).click()

    expect(page.locator("#overviewArea")).to_be_hidden()
    expect(page.locator("#inputArea")).to_be_visible()
    expect(page.locator("#toolArea")).to_be_visible()
    expect(page.locator("#summaryArea")).to_be_visible()
    expect(page.locator("#summaryArea")).to_contain_text("Connected Dependencies")
    expect(page.locator("#dependencyList")).to_have_count(0)


def test_mobile_layout_keeps_workflow_and_overview_readable(page, replay_url):
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(replay_url)

    expect(page.locator("#workflowGraph")).to_be_visible()
    expect(page.locator("#overviewArea")).to_be_visible()
    expect(page.locator("#workflowGraph")).to_contain_text("portfolio_agent")
    expect(page.locator("#overviewArea")).to_contain_text("System Overview")

    graph_box = page.locator("#workflowGraph").bounding_box()
    overview_box = page.locator("#overviewArea").bounding_box()
    assert graph_box is not None
    assert overview_box is not None
    assert overview_box["y"] > graph_box["y"]


def test_workflow_graph_renders_each_agent_node_once(page, replay_url):
    page.goto(replay_url)

    nodes = page.locator("#workflowGraph .graph-node")
    expect(nodes).to_have_count(5)

    for agent_name in ["macro_agent", "news_agent", "valuation_agent", "portfolio_agent", "trader"]:
        expect(page.locator("#workflowGraph .graph-node").filter(has_text=agent_name)).to_have_count(1)


def test_workflow_graph_draws_svg_arrows_for_resolved_dependencies(page, replay_url):
    page.goto(replay_url)

    expect(page.locator("#workflowGraph svg.workflow-arrow-layer")).to_have_count(1)
    arrows = page.locator("#workflowGraph svg.workflow-arrow-layer .workflow-arrow")
    expect(arrows).to_have_count(4, timeout=5000)
    expect(page.locator("#workflowGraph .graph-edge")).to_have_count(0)
    expect(page.locator("#workflowGraph")).not_to_contain_text("macro_agent -> portfolio_agent")
    expect(page.locator("#workflowGraph")).not_to_contain_text("portfolio_agent -> trader")


def test_switching_to_empty_system_run_ignores_pending_elk_layout(page, replay_url):
    dataset = _public_dataset(
        [_public_agent("source"), _public_agent("target")],
        [
            {
                "source_label": "context:source_summary",
                "target_agent": "target",
                "source_type": "context",
                "source_agent": "source",
                "source_status": "resolved_summary",
            }
        ],
    )
    dataset["runs"][0]["system_runs"].append(
        {
            "id": "system-empty",
            "name": "Empty system",
            "current_datetime": "2026-04-08T09:30:00-04:00",
            "mode": "backtesting",
            "agents": [],
            "dependencies": [],
            "summary": None,
            "warnings": [],
        }
    )
    page.route("**/api/dataset", lambda route: route.fulfill(json=dataset))
    page.add_init_script(
        """
        (() => {
          window.__pendingElkLayouts = [];
          window.__elkLayoutCalls = 0;
          window.__releaseNextElkLayout = () => {
            const release = window.__pendingElkLayouts.shift();
            if (release) {
              release();
            }
          };

          const layoutFromGraph = (graph) => ({
            id: graph.id,
            width: 420,
            height: 150,
            children: graph.children.map((child, index) => ({
              ...child,
              x: 24 + index * 220,
              y: 32,
            })),
            edges: graph.edges.map((edge) => ({
              ...edge,
              sections: [
                {
                  startPoint: { x: 204, y: 63 },
                  endPoint: { x: 244, y: 63 },
                },
              ],
            })),
          });

          Object.defineProperty(window, "ELK", {
            configurable: true,
            get() {
              return this.__WrappedELK;
            },
            set(OriginalELK) {
              const WrappedELK = function (...args) {
                const instance = new OriginalELK(...args);
                instance.layout = (graph) => {
                  window.__elkLayoutCalls += 1;
                  return new Promise((resolve) => {
                    window.__pendingElkLayouts.push(() => resolve(layoutFromGraph(graph)));
                  });
                };
                return instance;
              };
              WrappedELK.prototype = OriginalELK.prototype;
              this.__WrappedELK = WrappedELK;
            },
          });
        })();
        """
    )

    page.goto(replay_url)
    page.wait_for_function("window.__elkLayoutCalls === 1 && window.__pendingElkLayouts.length === 1")

    page.locator("#systemRunSelect").select_option("system-empty")
    expect(page.locator("#workflowGraph")).to_contain_text("No agents in this system run.")

    page.evaluate(
        """async () => {
          window.__releaseNextElkLayout();
          await new Promise((resolve) => requestAnimationFrame(resolve));
          await new Promise((resolve) => requestAnimationFrame(resolve));
        }"""
    )

    expect(page.locator("#workflowGraph")).to_contain_text("No agents in this system run.")
    expect(page.locator("#workflowGraph .graph-node")).to_have_count(0)
    expect(page.locator("#workflowGraph .workflow-arrow")).to_have_count(0)


def test_workflow_arrows_redraw_after_system_run_change(page, replay_url):
    dataset = _public_dataset(
        [_public_agent("source"), _public_agent("target")],
        [
            {
                "source_label": "context:source_summary",
                "target_agent": "target",
                "source_type": "context",
                "source_agent": "source",
                "source_status": "resolved_summary",
            }
        ],
    )
    dataset["runs"][0]["system_runs"].append(
        {
            "id": "system-2",
            "name": "Synthetic system 2",
            "current_datetime": "2026-04-08T09:30:00-04:00",
            "mode": "backtesting",
            "agents": [_public_agent("a"), _public_agent("b"), _public_agent("c")],
            "dependencies": [
                {
                    "source_label": "context:a_summary",
                    "target_agent": "b",
                    "source_type": "context",
                    "source_agent": "a",
                    "source_status": "resolved_summary",
                },
                {
                    "source_label": "context:b_summary",
                    "target_agent": "c",
                    "source_type": "context",
                    "source_agent": "b",
                    "source_status": "resolved_summary",
                },
            ],
            "summary": None,
            "warnings": [],
        }
    )
    page.route("**/api/dataset", lambda route: route.fulfill(json=dataset))

    page.goto(replay_url)
    expect(page.locator("#workflowGraph svg.workflow-arrow-layer .workflow-arrow")).to_have_count(1, timeout=5000)

    page.locator("#systemRunSelect").select_option("system-2")

    expect(page.locator("#workflowGraph svg.workflow-arrow-layer .workflow-arrow")).to_have_count(2, timeout=5000)


def test_strategy_selector_filters_backtest_and_system_run_choices(page, replay_url):
    strategy_a = _public_dataset([_public_agent("a_trader")], [])
    strategy_a["runs"][0]["id"] = "run-a"
    strategy_a["runs"][0]["label"] = "Prompt baseline"
    strategy_a["runs"][0]["strategy_name"] = "StrategyA"
    strategy_a["runs"][0]["system_runs"][0]["id"] = "system-a"
    strategy_a["runs"][0]["system_runs"][0]["name"] = "StrategyA day"

    strategy_b = _public_dataset([_public_agent("b_trader")], [])
    strategy_b["runs"][0]["id"] = "run-b"
    strategy_b["runs"][0]["label"] = "Added macro agent"
    strategy_b["runs"][0]["strategy_name"] = "StrategyB"
    strategy_b["runs"][0]["system_runs"][0]["id"] = "system-b"
    strategy_b["runs"][0]["system_runs"][0]["name"] = "StrategyB day"

    dataset = strategy_a
    dataset["runs"].append(strategy_b["runs"][0])
    page.route("**/api/dataset", lambda route: route.fulfill(json=dataset))

    page.goto(replay_url)

    expect(page.locator("#strategySelect")).to_have_value("StrategyA")
    expect(page.locator("#backtestRunSelect")).to_have_value("run-a")
    expect(page.locator("#systemRunSelect")).to_have_value("system-a")
    expect(page.locator("#workflowGraph")).to_contain_text("a_trader")

    page.locator("#strategySelect").select_option("StrategyB")

    expect(page.locator("#backtestRunSelect")).to_have_value("run-b")
    expect(page.locator("#systemRunSelect")).to_have_value("system-b")
    expect(page.locator("#workflowGraph")).to_contain_text("b_trader")
    expect(page.locator("#workflowGraph")).not_to_contain_text("a_trader")


def test_duplicate_dependencies_panel_is_not_rendered(page, replay_url):
    page.goto(replay_url)

    expect(page.locator("#dependencyList")).to_have_count(0)


def test_empty_dataset_warnings_remain_visible_without_dependency_panel(page, replay_url):
    dataset = {
        "runs": [],
        "generated_at": "2026-04-07T09:31:00-04:00",
        "source_path": "/tmp/synthetic",
        "warnings": [{"kind": "trace_parse_error", "path": "bad.json", "message": "broken trace"}],
    }
    page.route("**/api/dataset", lambda route: route.fulfill(json=dataset))

    page.goto(replay_url)

    expect(page.locator("#dependencyList")).to_have_count(0)
    expect(page.locator("#overviewArea")).to_contain_text("broken trace")
    expect(page.locator("#overviewArea")).to_contain_text("bad.json")
    expect(page.locator("#workflowGraph")).not_to_contain_text("review the warning panel below")


def test_selected_agent_highlights_connected_arrows(page, replay_url):
    page.goto(replay_url)
    expect(page.locator("#workflowGraph svg.workflow-arrow-layer .workflow-arrow")).to_have_count(4, timeout=5000)

    page.get_by_role("button", name=re.compile("portfolio_agent")).click()

    connected_arrows = page.locator("#workflowGraph svg.workflow-arrow-layer .workflow-arrow.connected")
    expect(connected_arrows).to_have_count(3, timeout=5000)
    expect(page.locator('#workflowGraph .graph-node.connected[data-agent-id^="macro_agent-"]')).to_have_count(1)
    expect(page.locator('#workflowGraph .graph-node.connected[data-agent-id^="news_agent-"]')).to_have_count(1)
    expect(page.locator('#workflowGraph .graph-node.connected[data-agent-id^="trader-"]')).to_have_count(1)
    expect(page.locator('#workflowGraph .graph-node.connected[data-agent-id^="portfolio_agent-"]')).to_have_count(0)
    expect(page.locator("#workflowGraph .graph-node.linked")).to_have_count(0)
    marker_ends = connected_arrows.evaluate_all(
        "(arrows) => arrows.map((arrow) => arrow.getAttribute('marker-end'))"
    )
    assert marker_ends == ["url(#workflow-arrowhead-connected)"] * 3


def test_workflow_graph_is_sticky_top_horizontal_map(page, replay_url):
    page.goto(replay_url)
    expect(page.locator("#workflowGraph svg.workflow-arrow-layer .workflow-arrow")).to_have_count(4, timeout=10000)

    header = page.locator(".replay-sticky-header")
    graph = page.locator("#workflowGraph")
    first_box = header.bounding_box()
    assert first_box is not None

    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_function(
        "() => Math.abs(document.querySelector('.replay-sticky-header').getBoundingClientRect().top) <= 1",
        timeout=5000,
    )

    second_box = header.bounding_box()
    graph_box = graph.bounding_box()
    overview_box = page.locator("#overviewArea").bounding_box()
    assert second_box is not None
    assert graph_box is not None
    assert overview_box is not None
    assert second_box["y"] <= 1
    assert graph_box["width"] > 900
    assert overview_box["y"] > graph_box["y"]

    node_centers = page.locator("#workflowGraph").evaluate(
        """(graph) => Object.fromEntries(
            Array.from(graph.querySelectorAll('.graph-node')).map((node) => {
                const box = node.getBoundingClientRect();
                const label = node.querySelector('.agent-name')?.textContent?.trim() || node.textContent.trim();
                return [label, { x: box.left + box.width / 2, y: box.top + box.height / 2 }];
            })
        )"""
    )
    assert node_centers["macro_agent"]["x"] < node_centers["portfolio_agent"]["x"]
    assert node_centers["news_agent"]["x"] < node_centers["portfolio_agent"]["x"]
    assert node_centers["portfolio_agent"]["x"] < node_centers["trader"]["x"]
    assert node_centers["valuation_agent"]["x"] < node_centers["trader"]["x"]


def test_workflow_arrows_do_not_cross_unrelated_agent_cards(page, replay_url):
    page.goto(replay_url)
    expect(page.locator("#workflowGraph svg.workflow-arrow-layer .workflow-arrow")).to_have_count(4, timeout=10000)

    _assert_workflow_arrows_do_not_cross_unrelated_agent_cards(page)


def test_dalio_shaped_workflow_renders_once_with_clear_routed_arrows(page, replay_url):
    agents = [
        _public_agent("growth_agent"),
        _public_agent("inflation_agent"),
        _public_agent("debt_liquidity_agent"),
        _public_agent("thoughtful_disagreement"),
        _public_agent("trader"),
    ]
    agents[3]["warnings"] = ["missing filing", "weak assumption", "stale source"]
    dependencies = [
        {
            "source_label": "context:growth_summary",
            "target_agent": "thoughtful_disagreement",
            "source_type": "context",
            "source_agent": "growth_agent",
            "source_status": "resolved_summary",
        },
        {
            "source_label": "context:inflation_summary",
            "target_agent": "thoughtful_disagreement",
            "source_type": "context",
            "source_agent": "inflation_agent",
            "source_status": "resolved_summary",
        },
        {
            "source_label": "context:debt_liquidity_summary",
            "target_agent": "thoughtful_disagreement",
            "source_type": "context",
            "source_agent": "debt_liquidity_agent",
            "source_status": "resolved_summary",
        },
        {
            "source_label": "context:thoughtful_disagreement_summary",
            "target_agent": "trader",
            "source_type": "context",
            "source_agent": "thoughtful_disagreement",
            "source_status": "resolved_summary",
        },
        {
            "source_label": "context:growth_summary",
            "target_agent": "trader",
            "source_type": "context",
            "source_agent": "growth_agent",
            "source_status": "resolved_summary",
        },
        {
            "source_label": "context:inflation_summary",
            "target_agent": "trader",
            "source_type": "context",
            "source_agent": "inflation_agent",
            "source_status": "resolved_summary",
        },
        {
            "source_label": "context:debt_liquidity_summary",
            "target_agent": "trader",
            "source_type": "context",
            "source_agent": "debt_liquidity_agent",
            "source_status": "resolved_summary",
        },
    ]
    page.route("**/api/dataset", lambda route: route.fulfill(json=_public_dataset(agents, dependencies)))

    page.goto(replay_url)

    expect(page.locator("#workflowGraph .graph-node")).to_have_count(5)
    expect(page.locator("#workflowGraph svg.workflow-arrow-layer .workflow-arrow")).to_have_count(7, timeout=10000)
    for agent_name in [
        "growth_agent",
        "inflation_agent",
        "debt_liquidity_agent",
        "thoughtful_disagreement",
        "trader",
    ]:
        expect(page.locator("#workflowGraph .graph-node").filter(has_text=agent_name)).to_have_count(1)

    _assert_workflow_arrows_do_not_cross_unrelated_agent_cards(page)
