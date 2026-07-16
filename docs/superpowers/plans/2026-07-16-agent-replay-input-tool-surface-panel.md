# Agent Replay Input Tool Surface Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `Input Material > Available Tool Names` the primary clickable tool-surface inspector, with usage evidence for used and unused tools.

**Architecture:** Reuse the existing replay dataset fields and selected-tool state. Move the rendered `Tool Definition` panel into the input-material tool subsection, while keeping tool names in `Tool Calls` as secondary selectors that update the same state.

**Tech Stack:** Vanilla JavaScript in `lumibot/components/agents/replay_ui/static/app.js`, CSS in `lumibot/components/agents/replay_ui/static/styles.css`, Playwright browser tests in `tests/test_agent_replay_ui_browser.py`.

---

## File Structure

- Modify `tests/test_agent_replay_ui_browser.py`
  - Add browser coverage for clickable available tool chips, unused available tools, and Tool Calls selecting the input-side panel.
- Modify `lumibot/components/agents/replay_ui/static/app.js`
  - Render available tool names as clickable chips.
  - Compute selected tool usage from the selected agent's `tool_batches`.
  - Render `Tool Definition` inside `Input Material`.
  - Keep Tool Calls buttons as secondary selectors and reveal the input panel.
- Modify `lumibot/components/agents/replay_ui/static/styles.css`
  - Add compact styles for the available tool chips and usage badges.
  - Reuse existing `.tool-definition-panel` styling where possible.

---

### Task 1: Browser Tests for Input Tool Surface Behavior

**Files:**
- Modify: `tests/test_agent_replay_ui_browser.py`

- [ ] **Step 1: Add an unused tool to the synthetic trace**

Update `_agent_trace()` so its `request.tool_surface` includes a second tool that is available but not called:

```python
{
    "name": "duckdb_query",
    "description": "Run SQL against tables loaded during this agent run.",
    "source": "local",
    "parameters": {
        "type": "object",
        "properties": {"sql": {"type": "string"}},
        "required": ["sql"],
    },
    "metadata": {"kind": "builtin"},
}
```

Do not add a `duckdb_query` tool call to `_agent_trace()`; it must remain unused.

- [ ] **Step 2: Replace the old Tool Calls-only panel test**

Replace `test_clicking_tool_name_shows_tool_definition_panel` with a test named:

```python
def test_clicking_available_tool_name_shows_input_tool_definition_panel(page, replay_url):
```

The test should:

```python
page.goto(replay_url)
page.locator(".graph-node", has_text="macro_agent").click()

input_card = page.locator("#inputArea details.collapsible-section").filter(has_text="Input Material")
input_card.locator("summary").first.click()

available_tools = page.locator("#inputArea details.collapsible-subsection").filter(has_text="Available Tool Names")
available_tools.locator("summary").click()
available_tools.get_by_role("button", name=re.compile("market_last_price")).click()

panel = page.locator("#inputArea .tool-definition-panel")
expect(panel).to_contain_text("Tool Definition")
expect(panel).to_contain_text("market_last_price")
expect(panel).to_contain_text("Get the current last price for one asset.")
expect(panel).to_contain_text("Used in this agent run")
expect(panel).to_contain_text("Yes")
expect(panel).to_contain_text("Call count")
expect(panel).to_contain_text("1")
expect(panel).to_contain_text("Batches")
expect(panel).to_contain_text("1")
expect(page.locator("#toolArea .tool-definition-panel")).to_have_count(0)
```

- [ ] **Step 3: Add an unused available tool test**

Add:

```python
def test_clicking_unused_available_tool_shows_available_but_unused_status(page, replay_url):
```

The test should click `duckdb_query` from `Input Material > Available Tool Names` and assert:

```python
panel = page.locator("#inputArea .tool-definition-panel")
expect(panel).to_contain_text("duckdb_query")
expect(panel).to_contain_text("Run SQL against tables loaded during this agent run.")
expect(panel).to_contain_text("Used in this agent run")
expect(panel).to_contain_text("No")
expect(panel).to_contain_text("Call count")
expect(panel).to_contain_text("0")
expect(panel).to_contain_text("Available to the LLM but not called in this agent run.")
```

- [ ] **Step 4: Update missing definition fallback test**

Change `test_missing_tool_definition_renders_clear_empty_state` so it clicks the tool from `#inputArea` rather than `#toolArea`:

```python
input_card = page.locator("#inputArea details.collapsible-section").filter(has_text="Input Material")
input_card.locator("summary").first.click()
available_tools = page.locator("#inputArea details.collapsible-subsection").filter(has_text="Available Tool Names")
available_tools.locator("summary").click()
available_tools.get_by_role("button", name=re.compile("market_last_price")).click()
expect(page.locator("#inputArea .tool-definition-panel")).to_contain_text(
    "No tool definition was recorded for this tool in the trace."
)
```

- [ ] **Step 5: Add Tool Calls secondary selector test**

Add:

```python
def test_clicking_tool_call_name_selects_input_tool_definition_panel(page, replay_url):
```

The test should:

```python
page.goto(replay_url)
page.locator(".graph-node", has_text="macro_agent").click()

page.locator("#toolArea .tool-name-button", has_text="market_last_price").click()

panel = page.locator("#inputArea .tool-definition-panel")
expect(panel).to_contain_text("market_last_price")
expect(panel).to_contain_text("Used in this agent run")
expect(page.locator("#toolArea .tool-definition-panel")).to_have_count(0)
```

- [ ] **Step 6: Update selected-agent clearing test**

Keep `test_selecting_another_agent_clears_selected_tool_definition`, but assert against `#inputArea .tool-definition-panel`.

- [ ] **Step 7: Run browser tests and verify the new tests fail**

Run:

```powershell
python -m pytest tests/test_agent_replay_ui_browser.py -q
```

Expected before implementation: failures showing the available tool buttons or input-side panel do not exist yet.

- [ ] **Step 8: Commit the failing tests**

```powershell
git add tests/test_agent_replay_ui_browser.py
git commit -m "test: cover input tool surface panel"
```

---

### Task 2: Implement Input-Side Tool Definition Panel

**Files:**
- Modify: `lumibot/components/agents/replay_ui/static/app.js`
- Modify: `lumibot/components/agents/replay_ui/static/styles.css`

- [ ] **Step 1: Add usage helpers in `app.js`**

Add helper functions near `findToolDefinition()`:

```javascript
function toolUsageForAgent(agent, toolName) {
  const batches = Array.isArray(agent && agent.tool_batches) ? agent.tool_batches : [];
  const batchNumbers = [];
  let callCount = 0;

  batches.forEach((batch) => {
    const calls = Array.isArray(batch.calls) ? batch.calls : [];
    const matches = calls.filter((call) => call && call.tool_name === toolName);
    if (matches.length > 0) {
      callCount += matches.length;
      batchNumbers.push(batch.batch_index ?? "");
    }
  });

  return {
    used: callCount > 0,
    callCount,
    batches: batchNumbers.filter((value) => value !== "").map((value) => String(value)),
  };
}
```

- [ ] **Step 2: Render clickable available tool chips**

Add:

```javascript
function renderAvailableToolSurface(agent, toolNames) {
  if (!toolNames.length) {
    return `<div class="empty-state">None</div>`;
  }

  const chips = toolNames.map((toolName) => {
    const usage = toolUsageForAgent(agent, toolName);
    const activeClass = state.selectedToolName === toolName ? " active" : "";
    const usedClass = usage.used ? " used" : " unused";
    const countLabel = usage.used ? ` x${usage.callCount}` : " unused";
    return `
      <button
        type="button"
        class="available-tool-chip${activeClass}${usedClass}"
        data-tool-name="${escapeHtml(toolName)}"
        aria-label="Show tool definition for ${escapeHtml(toolName)}"
      >
        <span class="available-tool-name">${escapeHtml(toolName)}</span>
        <span class="available-tool-count">${escapeHtml(countLabel)}</span>
      </button>
    `;
  });

  return `
    <div class="available-tool-surface">
      ${chips.join("")}
    </div>
    ${renderToolDefinitionPanel(agent)}
  `;
}
```

- [ ] **Step 3: Use the chips in `renderInputArea()`**

Replace the body passed to the `Available Tool Names` subsection with:

```javascript
renderAvailableToolSurface(agent, toolNames)
```

Keep the subsection closed by default unless existing tests require a different state.

- [ ] **Step 4: Move the panel out of `Tool Calls`**

In `renderToolArea()`, remove:

```javascript
${renderToolDefinitionPanel(agent)}
```

The `Tool Calls` card should render batches only. Tool-call buttons should continue to exist.

- [ ] **Step 5: Add usage evidence to `renderToolDefinitionPanel()`**

Inside `renderToolDefinitionPanel(agent)`, compute:

```javascript
const usage = toolUsageForAgent(agent, state.selectedToolName);
const usageNote = usage.used
  ? `Called in batch${usage.batches.length === 1 ? "" : "es"} ${usage.batches.join(", ")}.`
  : "Available to the LLM but not called in this agent run.";
```

Render a metadata grid containing:

```javascript
${metadataItem("Used in this agent run", usage.used ? "Yes" : "No")}
${metadataItem("Call count", String(usage.callCount))}
${metadataItem("Batches", usage.batches.length ? usage.batches.join(", ") : "None")}
```

Also render:

```javascript
<div class="notice">${escapeHtml(usageNote)}</div>
```

Both the recorded-definition path and missing-definition fallback path should include usage evidence.

- [ ] **Step 6: Generalize tool click handlers**

Replace `attachToolNameHandlers()` so it attaches to all buttons with `data-tool-name`, not only `elements.toolArea`:

```javascript
function attachToolNameHandlers() {
  document.querySelectorAll("[data-tool-name]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedToolName = button.getAttribute("data-tool-name");
      render();
      const panel = elements.inputArea.querySelector(".tool-definition-panel");
      if (panel) {
        panel.scrollIntoView({ block: "nearest" });
      }
    });
  });
}
```

Ensure `attachToolNameHandlers()` is called after both `renderInputArea(agent)` and `renderToolArea(agent)` have updated the DOM. One simple implementation is to remove the call from `renderToolArea()` and call `attachToolNameHandlers()` once at the end of `renderAgentDetail()`.

- [ ] **Step 7: Add styles in `styles.css`**

Add styles:

```css
.available-tool-surface {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
}

.available-tool-chip {
  align-items: center;
  background: #f8faf9;
  border: 1px solid #ccd8d2;
  border-radius: 4px;
  color: #12221b;
  cursor: pointer;
  display: inline-flex;
  font: inherit;
  gap: 0.35rem;
  max-width: 100%;
  padding: 0.35rem 0.5rem;
  text-align: left;
}

.available-tool-chip.used {
  border-color: #27745f;
}

.available-tool-chip.unused {
  color: #56635d;
}

.available-tool-chip.active,
.available-tool-chip:hover,
.available-tool-chip:focus {
  background: #e7f3ef;
  border-color: #1d5f4d;
  outline: none;
}

.available-tool-name {
  overflow-wrap: anywhere;
}

.available-tool-count {
  color: #56635d;
  font-size: 0.82rem;
  white-space: nowrap;
}
```

- [ ] **Step 8: Run targeted browser tests**

Run:

```powershell
python -m pytest tests/test_agent_replay_ui_browser.py -q
```

Expected: all browser tests pass.

- [ ] **Step 9: Run static tests for JS assets**

Run:

```powershell
python -m pytest tests/test_agent_replay_ui_static.py -q
```

Expected: static UI tests pass.

- [ ] **Step 10: Run ruff on touched files**

Run:

```powershell
python -m ruff check tests/test_agent_replay_ui_browser.py
```

Expected: no lint errors.

- [ ] **Step 11: Commit implementation**

```powershell
git add lumibot/components/agents/replay_ui/static/app.js lumibot/components/agents/replay_ui/static/styles.css
git commit -m "feat: inspect available replay tool surface"
```

---

## Final Verification

Run:

```powershell
python -m pytest tests/test_agent_replay_ui_static.py tests/test_agent_replay_ui_browser.py tests/test_agent_replay_ui_loader.py tests/test_agent_replay_ui_models.py tests/test_agent_replay_ui_formatters.py -q
python -m ruff check lumibot/components/agents/replay_ui tests/test_agent_replay_ui_static.py tests/test_agent_replay_ui_browser.py tests/test_agent_replay_ui_loader.py tests/test_agent_replay_ui_models.py tests/test_agent_replay_ui_formatters.py
git diff --check
```

Expected:

- all listed pytest tests pass
- ruff reports no new errors
- `git diff --check` exits cleanly

## Self-Review

- Spec coverage: each acceptance criterion maps to Task 1 tests and Task 2 implementation.
- Placeholder scan: no TODO/TBD placeholders are present.
- Type consistency: the plan uses existing dataset fields `input_material.available_tool_names`, `input_material.available_tools`, and `tool_batches`.
