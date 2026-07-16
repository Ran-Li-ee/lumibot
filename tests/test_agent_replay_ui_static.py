from importlib import resources
from pathlib import Path

STATIC_ROOT = Path("lumibot/components/agents/replay_ui/static")
STATIC_FILES = ("index.html", "app.js", "styles.css", "vendor/elk.bundled.js")


def test_static_ui_contains_required_detail_regions():
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")

    assert 'id="strategySelect"' in html
    assert 'id="backtestRunSelect"' in html
    assert 'id="systemRunSelect"' in html
    assert 'id="backtestLabel"' not in html
    assert 'id="workflowGraph"' in html
    assert 'id="toggleWorkflowGraphButton"' in html
    assert 'id="overviewButton"' in html
    assert 'id="overviewArea"' in html
    assert 'id="inputArea"' in html
    assert 'id="toolArea"' in html
    assert 'id="summaryArea"' in html


def test_static_javascript_renders_agent_sections():
    javascript = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    assert "renderInputArea" in javascript
    assert "renderToolArea" in javascript
    assert "renderSummaryArea" in javascript
    assert "Human Explanation" in javascript
    assert "USER SYSTEM PROMPT:" in javascript


def test_static_javascript_mentions_required_detail_regions_and_tool_columns():
    javascript = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    assert "inputArea" in javascript
    assert "toolArea" in javascript
    assert "summaryArea" in javascript
    assert "collapsible-section" in javascript
    assert "collapsible-subsection" in javascript
    assert "Batch ${batchIndex}" in javascript
    assert "Batch" in javascript
    assert "Tool Input" in javascript
    assert "Tool Output" in javascript
    assert "Human Explanation" in javascript


def test_static_javascript_renders_tool_definition_panel():
    javascript = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    assert "selectedToolName" in javascript
    assert "renderToolDefinitionPanel" in javascript
    assert "findToolDefinition" in javascript
    assert "tool-name-button" in javascript
    assert "Tool Definition" in javascript
    assert "No tool definition was recorded for this tool in the trace." in javascript


def test_static_javascript_surfaces_dataset_run_and_system_warnings():
    javascript = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    assert "renderVisibleWarnings" in javascript
    assert "collectVisibleWarnings" in javascript
    assert "dataset.warnings" in javascript
    assert "run.warnings" in javascript
    assert "systemRun.warnings" in javascript
    assert "Warnings exist" in javascript


def test_static_javascript_keeps_global_warnings_out_of_summary_panel():
    javascript = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    assert "collectAgentWarnings(agent)" in javascript
    assert "function renderSummaryArea" in javascript
    assert "renderSummaryArea(agent, run, systemRun)" not in javascript
    assert "const warnings = collectVisibleWarnings(run, systemRun, agent);" not in javascript


def test_static_css_keeps_tool_table_readable_on_mobile():
    css = (STATIC_ROOT / "styles.css").read_text(encoding="utf-8")

    assert ".tool-table" in css
    assert "min-width:" in css
    assert "760px" in css
    assert "overflow-x: auto" in css


def test_static_css_defines_tool_definition_panel_styles():
    css = (STATIC_ROOT / "styles.css").read_text(encoding="utf-8")

    assert ".tool-name-button" in css
    assert ".tool-definition-panel" in css
    assert ".tool-definition-grid" in css
    assert ".tool-definition-pre" in css
    assert "overflow-wrap: anywhere" in css


def test_static_files_are_visible_as_package_resources():
    package_root = resources.files("lumibot.components.agents.replay_ui")

    for name in STATIC_FILES:
        assert package_root.joinpath("static", name).is_file()


def test_static_files_are_included_in_packaging_metadata():
    setup_py = Path("setup.py").read_text(encoding="utf-8")
    manifest = Path("MANIFEST.in").read_text(encoding="utf-8")

    for name in STATIC_FILES:
        assert f"components/agents/replay_ui/static/{name}" in setup_py

    assert "recursive-include lumibot/components/agents/replay_ui/static *" in manifest


def test_static_ui_contains_workflow_graph_and_overview_regions():
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")

    assert 'id="workflowGraph"' in html
    assert 'id="toggleWorkflowGraphButton"' in html
    assert 'id="overviewButton"' in html
    assert 'id="overviewArea"' in html
    assert 'id="inputArea"' in html
    assert 'id="toolArea"' in html
    assert 'id="summaryArea"' in html


def test_static_ui_removes_duplicate_dependency_panel():
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")

    assert 'id="workflowGraph"' in html
    assert 'id="overviewButton"' in html
    assert 'id="dependencyList"' not in html


def test_static_ui_loads_local_elk_and_uses_sticky_workflow_header():
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")

    assert 'class="replay-sticky-header"' in html
    assert 'class="workflow-map-shell"' in html
    assert 'class="topbar"' not in html
    assert 'id="runSelect"' not in html
    assert 'id="backtestLabel"' not in html
    assert 'id="strategySelect"' in html
    assert 'id="backtestRunSelect"' in html
    assert 'src="/static/vendor/elk.bundled.js"' in html
    assert html.index('src="/static/vendor/elk.bundled.js"') < html.index('src="/static/app.js"')
    assert 'class="workspace"' not in html
    assert 'class="workflow-panel"' not in html


def test_static_javascript_defaults_to_system_overview_without_auto_selecting_agent():
    javascript = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    assert "function renderSystemOverview" in javascript
    assert "function renderAgentDetail" in javascript
    assert "function setDetailMode" in javascript
    assert "state.selectedAgentId = null" in javascript
    assert "state.selectedAgentId = agent ? agent.id : null" not in javascript
    assert "if (!state.selectedAgentId && agents.length > 0)" not in javascript


def test_static_javascript_uses_elk_layout_without_dependency_list_renderer():
    javascript = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    assert "function renderWorkflowGraph(systemRun)" in javascript
    assert "function buildElkWorkflowGraph(agents, dependencies)" in javascript
    assert "function layoutWorkflowWithElk(systemRun)" in javascript
    assert "new ELK" in javascript
    assert "'elk.direction': 'RIGHT'" in javascript
    assert "'elk.edgeRouting': 'ORTHOGONAL'" in javascript
    assert "workflow-arrow-layer" in javascript
    assert "workflow-arrowhead-connected" in javascript
    assert "renderDependencyList(" not in javascript
    assert "elements.dependencyList" not in javascript


def test_static_javascript_does_not_use_demo_edges_as_graph_source_of_truth():
    javascript = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    forbidden_edges = [
        "growth_agent -> thoughtful_disagreement",
        "inflation_agent -> thoughtful_disagreement",
        "debt_liquidity_agent -> thoughtful_disagreement",
        "thoughtful_disagreement -> trader",
    ]

    for edge in forbidden_edges:
        assert edge not in javascript


def test_static_css_defines_sticky_horizontal_workflow_graph():
    css = (STATIC_ROOT / "styles.css").read_text(encoding="utf-8")

    assert ".replay-sticky-header" in css
    assert "position: sticky" in css
    assert ".workflow-map-shell" in css
    assert ".workflow-map-viewport" in css
    assert ".workflow-diagram" in css
    assert "position: absolute" in css
    assert ".workflow-arrow-layer" in css
    assert ".workflow-arrow.connected" in css
    assert "#workflow-arrowhead-connected path" in css


def test_static_css_defines_detail_modes():
    css = (STATIC_ROOT / "styles.css").read_text(encoding="utf-8")

    assert ".graph-node" in css
    assert ".graph-node.active" in css
    assert ".overview-card" in css
    assert "[hidden]" in css
