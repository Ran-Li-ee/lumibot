# Agent Replay Tool Definition Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Tool Definition panel to Agent Workflow Replay UI so clicking a tool name in Tool calls shows the recorded tool definition material from the trace.

**Architecture:** Preserve the existing replay trace loader and UI shape. Extend the public `AgentReplay.input_material()` payload to include redacted `available_tools`, then update `app.js` to track the selected tool name per selected agent and render a definition panel below Tool calls. Add focused model/static/browser tests so old traces, missing definitions, and agent changes behave predictably.

**Tech Stack:** Python dataclasses and pytest for replay models, vanilla JavaScript/CSS for the static UI, Playwright browser tests for interaction behavior.

---

## File Structure

- Modify `lumibot/components/agents/replay_ui/models.py`
  - Add redacted `available_tools` to public agent input material.
  - Preserve existing `available_tool_names` and `available_tool_count` behavior.
- Modify `lumibot/components/agents/replay_ui/static/app.js`
  - Add `selectedToolName` state.
  - Clear selected tool when agent/system/run selection changes.
  - Render tool names in Tool calls as buttons.
  - Render a Tool Definition panel below the Tool calls table.
- Modify `lumibot/components/agents/replay_ui/static/styles.css`
  - Style tool-name buttons and the definition panel.
  - Keep long descriptions and JSON readable without horizontal page overflow.
- Modify `tests/test_agent_replay_ui_models.py`
  - Verify `available_tools` is included and redacted.
  - Verify old traces with no `tool_surface` still produce an empty list.
- Modify `tests/test_agent_replay_ui_static.py`
  - Verify static JS/CSS contains the new render hooks and classes.
- Modify `tests/test_agent_replay_ui_browser.py`
  - Verify clicking a tool name renders a definition panel.
  - Verify missing definitions render the empty-state message.
  - Verify selecting another agent clears the previous tool definition.

---

### Task 1: Expose Recorded Tool Surface In Public Replay Model

**Files:**
- Modify: `lumibot/components/agents/replay_ui/models.py`
- Test: `tests/test_agent_replay_ui_models.py`

- [ ] **Step 1: Write failing model tests**

Add these tests to `tests/test_agent_replay_ui_models.py` after `test_agent_replay_public_dict_contains_three_detail_areas`:

```python
def test_agent_replay_public_dict_includes_redacted_available_tools():
    agent = AgentReplay(
        id="trader-1",
        name="trader",
        model="openai/gpt-5.4-mini",
        trace_path="C:/trace/trader.json",
        request={
            "tool_surface": [
                {
                    "name": "orders_submit_order",
                    "description": "Use api_key=sk-test-secret only in test fixtures.",
                    "source": "local",
                    "metadata": {
                        "kind": "builtin",
                        "Authorization": "Bearer abc123",
                    },
                },
                {
                    "name": "market_last_price",
                    "description": "Get the current last price.",
                    "source": "builtin",
                    "metadata": {"kind": "market"},
                },
            ],
        },
    )

    public = agent.to_public_dict()

    assert public["input_material"]["available_tool_count"] == 2
    assert public["input_material"]["available_tool_names"] == [
        "orders_submit_order",
        "market_last_price",
    ]
    assert public["input_material"]["available_tools"] == [
        {
            "name": "orders_submit_order",
            "description": "Use api_key=[REDACTED] only in test fixtures.",
            "source": "local",
            "metadata": {
                "kind": "builtin",
                "Authorization": "[REDACTED]",
            },
        },
        {
            "name": "market_last_price",
            "description": "Get the current last price.",
            "source": "builtin",
            "metadata": {"kind": "market"},
        },
    ]


def test_agent_replay_public_dict_handles_missing_tool_surface():
    agent = AgentReplay(
        id="old-agent-1",
        name="old_agent",
        model="openai/gpt-5.4-mini",
        trace_path="C:/trace/old.json",
        request={},
    )

    public = agent.to_public_dict()

    assert public["input_material"]["available_tool_count"] == 0
    assert public["input_material"]["available_tool_names"] == []
    assert public["input_material"]["available_tools"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_agent_replay_ui_models.py::test_agent_replay_public_dict_includes_redacted_available_tools tests/test_agent_replay_ui_models.py::test_agent_replay_public_dict_handles_missing_tool_surface -q
```

Expected result:

```text
FAILED ... KeyError: 'available_tools'
```

- [ ] **Step 3: Implement available tool serialization**

In `lumibot/components/agents/replay_ui/models.py`, add this helper below `_tool_name`:

```python
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
        ):
            if key in tool:
                public_tool[key] = redact_sensitive(tool.get(key))
        public_tools.append(public_tool)

    return public_tools
```

Then update `AgentReplay.input_material()` from:

```python
tool_surface = self.request.get("tool_surface") or []
tool_names = [name for name in (_tool_name(tool) for tool in tool_surface) if name is not None]
```

to:

```python
tool_surface = self.request.get("tool_surface") or []
available_tools = _public_tool_surface(tool_surface)
tool_names = [str(tool["name"]) for tool in available_tools if tool.get("name")]
```

Then add `"available_tools": available_tools,` to the returned input material dictionary immediately after `"available_tool_names": tool_names,`.

- [ ] **Step 4: Run model tests**

Run:

```powershell
python -m pytest tests/test_agent_replay_ui_models.py -q
```

Expected result:

```text
passed
```

- [ ] **Step 5: Commit**

Run:

```powershell
git add lumibot/components/agents/replay_ui/models.py tests/test_agent_replay_ui_models.py
git commit -m "feat: expose replay tool definitions"
```

---

### Task 2: Add Clickable Tool Names And Tool Definition Panel

**Files:**
- Modify: `lumibot/components/agents/replay_ui/static/app.js`
- Modify: `lumibot/components/agents/replay_ui/static/styles.css`
- Test: `tests/test_agent_replay_ui_static.py`

- [ ] **Step 1: Write failing static tests**

Add this test to `tests/test_agent_replay_ui_static.py` after `test_static_javascript_mentions_required_detail_regions_and_tool_columns`:

```python
def test_static_javascript_renders_tool_definition_panel():
    javascript = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    assert "selectedToolName" in javascript
    assert "renderToolDefinitionPanel" in javascript
    assert "findToolDefinition" in javascript
    assert "tool-name-button" in javascript
    assert "Tool Definition" in javascript
    assert "No tool definition was recorded for this tool in the trace." in javascript
```

Add this test after `test_static_css_keeps_tool_table_readable_on_mobile`:

```python
def test_static_css_defines_tool_definition_panel_styles():
    css = (STATIC_ROOT / "styles.css").read_text(encoding="utf-8")

    assert ".tool-name-button" in css
    assert ".tool-definition-panel" in css
    assert ".tool-definition-grid" in css
    assert ".tool-definition-pre" in css
    assert "overflow-wrap: anywhere" in css
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_agent_replay_ui_static.py::test_static_javascript_renders_tool_definition_panel tests/test_agent_replay_ui_static.py::test_static_css_defines_tool_definition_panel_styles -q
```

Expected result:

```text
FAILED ... AssertionError
```

- [ ] **Step 3: Add selected tool state and clearing rules**

In `lumibot/components/agents/replay_ui/static/app.js`, update the `state` object near the top from:

```javascript
selectedAgentId: null,
```

to:

```javascript
selectedAgentId: null,
selectedToolName: null,
```

Wherever the code sets `state.selectedAgentId = null;` during run/system selection changes, add:

```javascript
state.selectedToolName = null;
```

In `attachGraphNodeHandlers()`, update the click handler from:

```javascript
state.selectedAgentId = button.getAttribute("data-agent-id");
render();
```

to:

```javascript
state.selectedAgentId = button.getAttribute("data-agent-id");
state.selectedToolName = null;
render();
```

In the guard that clears an invalid selected agent, update:

```javascript
state.selectedAgentId = null;
```

to:

```javascript
state.selectedAgentId = null;
state.selectedToolName = null;
```

- [ ] **Step 4: Add tool definition lookup and render helpers**

In `lumibot/components/agents/replay_ui/static/app.js`, add these helpers after `renderToolBatch`:

```javascript
  function renderToolDefinitionPanel(agent) {
    if (!state.selectedToolName) {
      return "";
    }

    const definition = findToolDefinition(agent, state.selectedToolName);
    if (!definition) {
      return `
        <section class="tool-definition-panel" aria-live="polite">
          <h4>Tool Definition</h4>
          <div class="metadata-grid">
            ${metadataItem("Tool name", state.selectedToolName)}
          </div>
          <div class="empty-state">No tool definition was recorded for this tool in the trace.</div>
        </section>
      `;
    }

    const name = definition.name || state.selectedToolName;
    const description = definition.description || "Not recorded in this trace.";
    const signature = firstRecordedField(definition, ["signature", "parameters", "schema", "input_schema"]);
    const annotations = definition.annotations === undefined ? "Not recorded in this trace." : definition.annotations;
    const metadata = definition.metadata === undefined ? "Not recorded in this trace." : definition.metadata;
    const source = definition.source || "Not recorded in this trace.";

    return `
      <section class="tool-definition-panel" aria-live="polite">
        <h4>Tool Definition</h4>
        <div class="tool-definition-grid">
          ${metadataItem("Tool name", name)}
          ${metadataItem("Source", source)}
        </div>
        <h5>Model-Facing Description</h5>
        <pre class="tool-definition-pre">${formatValue(description)}</pre>
        <h5>Recorded Function / Parameter Data</h5>
        <pre class="tool-definition-pre">${formatValue(signature)}</pre>
        <h5>Recorded Annotations</h5>
        <pre class="tool-definition-pre">${formatValue(annotations)}</pre>
        <h5>Replay Metadata</h5>
        <pre class="tool-definition-pre">${formatValue(metadata)}</pre>
      </section>
    `;
  }

  function findToolDefinition(agent, toolName) {
    const input = agent && agent.input_material ? agent.input_material : {};
    const tools = Array.isArray(input.available_tools) ? input.available_tools : [];
    return tools.find((tool) => tool && tool.name === toolName) || null;
  }

  function firstRecordedField(definition, keys) {
    for (const key of keys) {
      if (definition && definition[key] !== undefined) {
        return definition[key];
      }
    }
    return "Not recorded in this trace.";
  }
```

- [ ] **Step 5: Render the panel below Tool calls**

In `renderToolArea(agent)`, change:

```javascript
const body = batches.map((batch, index) => renderToolBatch(batch, index === 0)).join("");
```

to:

```javascript
const body = `
  ${batches.map((batch, index) => renderToolBatch(batch, index === 0)).join("")}
  ${renderToolDefinitionPanel(agent)}
`;
```

In `renderToolRow(row)`, replace:

```javascript
<td><pre>${formatValue(inputPayload)}</pre></td>
```

with:

```javascript
<td>
  ${renderToolNameButton(call.tool_name)}
  <pre>${formatValue(inputPayload)}</pre>
</td>
```

Add this helper after `renderToolRow`:

```javascript
  function renderToolNameButton(toolName) {
    if (!toolName) {
      return "";
    }
    const activeClass = state.selectedToolName === toolName ? " active" : "";
    return `
      <button
        type="button"
        class="tool-name-button${activeClass}"
        data-tool-name="${escapeHtml(toolName)}"
        aria-label="Show tool definition for ${escapeHtml(toolName)}"
      >
        ${escapeHtml(toolName)}
      </button>
    `;
  }
```

In `renderToolArea(agent)`, after assigning `elements.toolArea.innerHTML = ...`, call:

```javascript
attachToolNameHandlers();
```

Add this function near `attachGraphNodeHandlers()`:

```javascript
  function attachToolNameHandlers() {
    elements.toolArea.querySelectorAll(".tool-name-button[data-tool-name]").forEach((button) => {
      button.addEventListener("click", () => {
        state.selectedToolName = button.getAttribute("data-tool-name");
        render();
      });
    });
  }
```

- [ ] **Step 6: Add CSS styles**

Append these styles to `lumibot/components/agents/replay_ui/static/styles.css` near the existing tool table styles:

```css
.tool-name-button {
  display: inline-flex;
  align-items: center;
  max-width: 100%;
  margin: 0 0 8px;
  padding: 3px 6px;
  border: 1px solid #9fb3ad;
  border-radius: 4px;
  background: #f7fbfa;
  color: #102522;
  font: inherit;
  font-weight: 700;
  cursor: pointer;
  overflow-wrap: anywhere;
  text-align: left;
}

.tool-name-button:hover,
.tool-name-button:focus {
  border-color: #1f6f63;
  outline: none;
}

.tool-name-button.active {
  background: #dceee9;
  border-color: #1f6f63;
}

.tool-definition-panel {
  margin-top: 14px;
  padding: 12px;
  border: 1px solid #c6d2cf;
  background: #fbfdfc;
}

.tool-definition-panel h4,
.tool-definition-panel h5 {
  margin: 0 0 8px;
}

.tool-definition-panel h5 {
  margin-top: 12px;
  font-size: 13px;
}

.tool-definition-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 8px;
  margin-bottom: 10px;
}

.tool-definition-pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-width: 100%;
}
```

- [ ] **Step 7: Run static tests**

Run:

```powershell
python -m pytest tests/test_agent_replay_ui_static.py -q
```

Expected result:

```text
passed
```

- [ ] **Step 8: Commit**

Run:

```powershell
git add lumibot/components/agents/replay_ui/static/app.js lumibot/components/agents/replay_ui/static/styles.css tests/test_agent_replay_ui_static.py
git commit -m "feat: render replay tool definitions"
```

---

### Task 3: Add Browser Interaction Coverage

**Files:**
- Modify: `tests/test_agent_replay_ui_browser.py`

- [ ] **Step 1: Update browser trace fixture with tool definitions**

In `tests/test_agent_replay_ui_browser.py`, update `_agent_trace()` so `request.tool_surface` changes from:

```python
"tool_surface": [{"name": "market_last_price"}],
```

to:

```python
"tool_surface": [
    {
        "name": "market_last_price",
        "description": "Get the current last price for one asset. Example: market_last_price(symbol='SPY').",
        "source": "local",
        "metadata": {"kind": "builtin"},
    }
],
```

- [ ] **Step 2: Add browser tests**

Add these tests after `test_default_view_shows_workflow_overview_without_agent_detail`:

```python
def test_clicking_tool_name_shows_tool_definition_panel(page, replay_url):
    page.goto(replay_url)

    page.locator(".graph-node", has_text="macro_agent").click()
    page.locator(".tool-name-button", has_text="market_last_price").click()

    expect(page.locator(".tool-definition-panel")).to_contain_text("Tool Definition")
    expect(page.locator(".tool-definition-panel")).to_contain_text("market_last_price")
    expect(page.locator(".tool-definition-panel")).to_contain_text(
        "Get the current last price for one asset."
    )
    expect(page.locator(".tool-definition-panel")).to_contain_text("Replay Metadata")
    expect(page.locator(".tool-definition-panel")).to_contain_text("Not recorded in this trace.")


def test_missing_tool_definition_renders_clear_empty_state(page, replay_url):
    page.goto(replay_url)

    page.evaluate(
        """() => {
            const macro = window.__AGENT_REPLAY_DATA__.runs[0].system_runs[0].agents.find(
                (agent) => agent.name === 'macro_agent'
            );
            macro.input_material.available_tools = [];
        }"""
    )
    page.locator(".graph-node", has_text="macro_agent").click()
    page.locator(".tool-name-button", has_text="market_last_price").click()

    expect(page.locator(".tool-definition-panel")).to_contain_text(
        "No tool definition was recorded for this tool in the trace."
    )


def test_selecting_another_agent_clears_selected_tool_definition(page, replay_url):
    page.goto(replay_url)

    page.locator(".graph-node", has_text="macro_agent").click()
    page.locator(".tool-name-button", has_text="market_last_price").click()
    expect(page.locator(".tool-definition-panel")).to_contain_text("market_last_price")

    page.locator(".graph-node", has_text="news_agent").click()

    expect(page.locator(".tool-definition-panel")).to_have_count(0)
```

- [ ] **Step 3: Run browser tests to verify behavior**

Run:

```powershell
python -m pytest tests/test_agent_replay_ui_browser.py::test_clicking_tool_name_shows_tool_definition_panel tests/test_agent_replay_ui_browser.py::test_missing_tool_definition_renders_clear_empty_state tests/test_agent_replay_ui_browser.py::test_selecting_another_agent_clears_selected_tool_definition -q
```

Expected result:

```text
passed
```

- [ ] **Step 4: Run full replay UI verification**

Run:

```powershell
python -m pytest tests/test_agent_replay_ui_static.py tests/test_agent_replay_ui_browser.py tests/test_agent_replay_ui_loader.py tests/test_agent_replay_ui_models.py tests/test_agent_replay_ui_formatters.py -q
```

Expected result:

```text
passed
```

Then run:

```powershell
python -m ruff check lumibot/components/agents/replay_ui tests/test_agent_replay_ui_static.py tests/test_agent_replay_ui_browser.py tests/test_agent_replay_ui_loader.py tests/test_agent_replay_ui_models.py tests/test_agent_replay_ui_formatters.py
```

Expected result:

```text
All checks passed!
```

- [ ] **Step 5: Commit**

Run:

```powershell
git add tests/test_agent_replay_ui_browser.py
git commit -m "test: cover replay tool definition panel"
```

---

## Final Verification

After all tasks are complete, run:

```powershell
git status --short --branch
python -m pytest tests/test_agent_replay_ui_static.py tests/test_agent_replay_ui_browser.py tests/test_agent_replay_ui_loader.py tests/test_agent_replay_ui_models.py tests/test_agent_replay_ui_formatters.py -q
python -m ruff check lumibot/components/agents/replay_ui tests/test_agent_replay_ui_static.py tests/test_agent_replay_ui_browser.py tests/test_agent_replay_ui_loader.py tests/test_agent_replay_ui_models.py tests/test_agent_replay_ui_formatters.py
git diff --check
```

Expected:

- branch is `feature/agent-workflow-replay-ui-origin`
- only intentional committed changes plus pre-existing untracked `project_notes/`
- all replay UI tests pass
- ruff passes
- `git diff --check` prints no whitespace errors

## Self-Review Notes

- Spec coverage: Task 1 exposes `available_tools`; Task 2 implements clickable names and the panel; Task 3 covers browser interaction, missing definitions, and agent-change clearing.
- Scope check: The plan does not add exit-position tools, modify prompts, or intercept raw provider requests.
- Compatibility: Current traces with only `name`, `description`, `source`, and `metadata` work; future traces with `signature`, `annotations`, `parameters`, `schema`, or `input_schema` will render those fields automatically.

