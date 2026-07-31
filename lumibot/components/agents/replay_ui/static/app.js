(function () {
  "use strict";

  const state = {
    dataset: null,
    selectedStrategyName: null,
    selectedBacktestRunId: null,
    selectedSystemRunId: null,
    selectedAgentId: null,
    workflowGraphHidden: false,
    workflowLayoutRequestId: 0,
    workflowRelayoutFrame: null,
  };

  const elements = {};

  document.addEventListener("DOMContentLoaded", () => {
    elements.statusMessage = document.getElementById("statusMessage");
    elements.strategySelect = document.getElementById("strategySelect");
    elements.backtestRunSelect = document.getElementById("backtestRunSelect");
    elements.systemRunSelect = document.getElementById("systemRunSelect");
    elements.workflowMapShell = document.querySelector(".workflow-map-shell");
    elements.workflowMapViewport = document.getElementById("workflowMapViewport");
    elements.workflowGraph = document.getElementById("workflowGraph");
    elements.toggleWorkflowGraphButton = document.getElementById("toggleWorkflowGraphButton");
    elements.accountCurveLink = document.getElementById("accountCurveLink");
    elements.performanceReportLink = document.getElementById("performanceReportLink");
    elements.overviewButton = document.getElementById("overviewButton");
    elements.overviewArea = document.getElementById("overviewArea");
    elements.inputArea = document.getElementById("inputArea");
    elements.toolArea = document.getElementById("toolArea");
    elements.boundaryTraceArea = document.getElementById("boundaryTraceArea");
    elements.summaryArea = document.getElementById("summaryArea");

    elements.overviewButton.addEventListener("click", () => {
      state.selectedAgentId = null;
      render();
    });

    elements.toggleWorkflowGraphButton.addEventListener("click", () => {
      state.workflowGraphHidden = !state.workflowGraphHidden;
      renderWorkflowGraphVisibility();
      if (!state.workflowGraphHidden) {
        scheduleWorkflowRelayout();
      }
    });

    elements.strategySelect.addEventListener("change", () => {
      state.selectedStrategyName = elements.strategySelect.value;
      const run = getBacktestRunsForStrategy(state.selectedStrategyName)[0] || null;
      const systemRun = getSystemRuns(run)[0] || null;
      state.selectedBacktestRunId = run ? run.id : null;
      state.selectedSystemRunId = systemRun ? systemRun.id : null;
      state.selectedAgentId = null;
      render();
    });

    elements.backtestRunSelect.addEventListener("change", () => {
      state.selectedBacktestRunId = elements.backtestRunSelect.value;
      const systemRun = getSystemRuns(selectedRun()).find((item) => item.id === state.selectedSystemRunId)
        || getSystemRuns(selectedRun())[0]
        || null;
      state.selectedSystemRunId = systemRun ? systemRun.id : null;
      state.selectedAgentId = null;
      render();
    });

    elements.systemRunSelect.addEventListener("change", () => {
      state.selectedSystemRunId = elements.systemRunSelect.value;
      state.selectedAgentId = null;
      render();
    });

    document.addEventListener("click", (event) => {
      const button = event.target.closest(".boundary-sidecar-button");
      if (!button) {
        return;
      }
      loadBoundarySidecar(button);
    });

    window.addEventListener("resize", () => {
      if (state.dataset) {
        scheduleWorkflowRelayout();
      }
    });

    loadDataset();
  });

  async function loadDataset() {
    setStatus("Loading replay dataset...");
    renderLoading();

    try {
      const response = await fetch("/api/dataset", { headers: { Accept: "application/json" } });
      if (!response.ok) {
        throw new Error(`Dataset request failed with HTTP ${response.status}`);
      }

      state.dataset = await response.json();
      initializeSelection();
      render();
    } catch (error) {
      setStatus(`Unable to load replay dataset: ${error.message}`, true);
      renderError(error);
    }
  }

  function initializeSelection() {
    const strategies = getStrategyNames();
    const strategyName = strategies[0] || null;
    const run = getBacktestRunsForStrategy(strategyName)[0] || null;
    const systemRun = getSystemRuns(run)[0] || null;

    state.selectedStrategyName = strategyName;
    state.selectedBacktestRunId = run ? run.id : null;
    state.selectedSystemRunId = systemRun ? systemRun.id : null;
    state.selectedAgentId = null;
  }

  function render() {
    const runs = getRuns();
    if (runs.length === 0) {
      setStatus("No replay runs found.");
      renderEmptyDataset();
      return;
    }

    const run = selectedRun();
    const systemRun = selectedSystemRun(run);

    if (state.selectedAgentId && !selectedAgent(systemRun)) {
      state.selectedAgentId = null;
    }

    setStatus(datasetStatusText(run, systemRun));
    renderStrategySelector();
    renderBacktestRunSelector(run);
    renderSystemRunSelector(run, systemRun);
    renderArtifactLinks(run);
    renderWorkflowGraphVisibility();
    renderWorkflowGraph(systemRun);
    renderRightPanel(run, systemRun);
  }

  function renderLoading() {
    elements.workflowGraph.innerHTML = `<div class="empty-state">${escapeHtml("Loading workflow...")}</div>`;
    setDetailMode("overview");
    elements.overviewArea.innerHTML = sectionShell("System Overview", "Loading selected system run...");
    elements.inputArea.innerHTML = "";
    elements.toolArea.innerHTML = "";
    elements.boundaryTraceArea.innerHTML = "";
    elements.summaryArea.innerHTML = "";
  }

  function renderError(error) {
    const message = error.message || "Unknown dataset error";
    elements.strategySelect.disabled = true;
    elements.backtestRunSelect.disabled = true;
    elements.systemRunSelect.disabled = true;
    renderArtifactLinks(null);
    elements.workflowGraph.innerHTML = `<div class="empty-state">No workflow available.</div>`;
    setDetailMode("overview");
    elements.overviewArea.innerHTML = sectionShell("System Overview", `Dataset error: ${message}`);
    elements.inputArea.innerHTML = "";
    elements.toolArea.innerHTML = "";
    elements.boundaryTraceArea.innerHTML = "";
    elements.summaryArea.innerHTML = "";
  }

  function renderEmptyDataset() {
    const warnings = collectVisibleWarnings(null, null, null);
    const warningText = warnings.length ? " Warnings exist in the replay dataset." : "";
    elements.strategySelect.innerHTML = "<option>No strategies</option>";
    elements.strategySelect.disabled = true;
    elements.backtestRunSelect.innerHTML = "<option>No backtest runs</option>";
    elements.backtestRunSelect.disabled = true;
    elements.systemRunSelect.innerHTML = "<option>No system runs</option>";
    elements.systemRunSelect.disabled = true;
    renderArtifactLinks(null);
    elements.workflowGraph.innerHTML = `<div class="empty-state">No agents were discovered in the trace root.${warningText}</div>`;
    setDetailMode("overview");
    elements.overviewArea.innerHTML = `${sectionShell("System Overview", "No replay runs are available.")}${renderVisibleWarnings(warnings)}`;
    elements.inputArea.innerHTML = "";
    elements.toolArea.innerHTML = "";
    elements.boundaryTraceArea.innerHTML = "";
    elements.summaryArea.innerHTML = "";
  }

  function renderNoSelectedAgent(run, systemRun) {
    renderSystemOverview(run, systemRun);
  }

  function renderWorkflowGraphVisibility() {
    elements.workflowMapShell.classList.toggle("graph-hidden", state.workflowGraphHidden);
    elements.workflowMapViewport.hidden = state.workflowGraphHidden;
    elements.toggleWorkflowGraphButton.textContent = state.workflowGraphHidden ? "Show Graph" : "Hide Graph";
    elements.toggleWorkflowGraphButton.setAttribute("aria-expanded", String(!state.workflowGraphHidden));
  }

  function renderStrategySelector() {
    const strategies = getStrategyNames();
    fillSelect(
      elements.strategySelect,
      strategies,
      (item) => item,
      (item) => item,
      state.selectedStrategyName || "",
    );
    elements.strategySelect.disabled = strategies.length <= 1;
  }

  function renderBacktestRunSelector(run) {
    const runs = getBacktestRunsForStrategy(state.selectedStrategyName);
    fillSelect(
      elements.backtestRunSelect,
      runs,
      (item) => item.id,
      (item) => backtestRunLabel(item),
      run ? run.id : "",
    );
    elements.backtestRunSelect.disabled = runs.length <= 1;
  }

  function renderSystemRunSelector(run, systemRun) {
    const systemRuns = getSystemRuns(run);
    fillSelect(
      elements.systemRunSelect,
      systemRuns,
      (item) => item.id,
      (item) => systemRunLabel(item),
      systemRun ? systemRun.id : "",
    );
    elements.systemRunSelect.disabled = systemRuns.length <= 1;
  }

  function renderArtifactLinks(run) {
    const artifacts = run && run.artifacts ? run.artifacts : {};
    setArtifactLink(elements.accountCurveLink, artifacts.account_curve, "Account Curve");
    setArtifactLink(elements.performanceReportLink, artifacts.performance_report, "Performance Report");
  }

  function setArtifactLink(element, artifact, label) {
    if (!element) {
      return;
    }
    element.textContent = label;
    if (artifact && artifact.available && artifact.url) {
      element.href = artifact.url;
      element.classList.remove("disabled");
      element.removeAttribute("aria-disabled");
      element.title = `Open ${label} for this backtest run`;
      return;
    }

    element.removeAttribute("href");
    element.classList.add("disabled");
    element.setAttribute("aria-disabled", "true");
    element.title = artifact && artifact.reason ? artifact.reason : `${label} is unavailable for this backtest run.`;
  }

  function renderWorkflowGraph(systemRun) {
    const agents = getAgents(systemRun);
    const dependencies = getDependencies(systemRun);
    const layoutRequestId = state.workflowLayoutRequestId + 1;
    state.workflowLayoutRequestId = layoutRequestId;

    if (agents.length === 0) {
      elements.workflowGraph.innerHTML = `<div class="empty-state">No agents in this system run.</div>`;
      return;
    }

    const connectedAgentIds = connectedAgentIdsForSelection(state.selectedAgentId, dependencies, agents);
    elements.workflowGraph.innerHTML = renderWorkflowDiagramShell(renderUnresolvedDependencyNote(dependencies, agents));

    layoutWorkflowWithElk(systemRun).catch((error) => {
      if (layoutRequestId !== state.workflowLayoutRequestId) {
        return;
      }
      elements.workflowGraph.innerHTML = renderWorkflowFallback(
        buildWorkflowGraph(agents, dependencies),
        dependencies,
        connectedAgentIds,
        agents,
        error,
      );
      attachGraphNodeHandlers();
      scheduleDrawWorkflowArrows();
    });
  }

  function attachGraphNodeHandlers() {
    elements.workflowGraph.querySelectorAll(".graph-node[data-agent-id]").forEach((button) => {
      button.addEventListener("click", () => {
        state.selectedAgentId = button.getAttribute("data-agent-id");
        render();
      });
    });
  }

  function renderWorkflowDiagramShell(note) {
    return `
      <div class="workflow-diagram elk-layout" aria-busy="true">
        <svg class="workflow-arrow-layer" aria-hidden="true"></svg>
        <div class="empty-state">Laying out workflow...</div>
      </div>
      ${note}
    `;
  }

  function renderWorkflowDiagram(graph, dependencies, connectedAgentIds, agents) {
    const unresolvedNote = renderUnresolvedDependencyNote(dependencies, agents);
    return `
      <div class="workflow-diagram fallback-layout">
        <svg class="workflow-arrow-layer" aria-hidden="true"></svg>
        ${renderWorkflowStage("Sources", graph.sources, connectedAgentIds)}
        ${renderWorkflowStage("Middle", graph.middle, connectedAgentIds)}
        ${renderWorkflowStage("Terminal", graph.terminal, connectedAgentIds)}
        ${graph.disconnected.length ? renderWorkflowStage("Disconnected", graph.disconnected, connectedAgentIds) : ""}
      </div>
      ${unresolvedNote}
    `;
  }

  function renderWorkflowFallback(graph, dependencies, connectedAgentIds, agents, error) {
    return `${renderWorkflowDiagram(graph, dependencies, connectedAgentIds, agents)}
      <div class="notice">Automatic workflow layout failed: ${escapeHtml(error.message || "Unknown error")}</div>`;
  }

  function buildWorkflowGraph(agents, dependencies) {
    const incoming = new Set();
    const outgoing = new Set();

    dependencies.forEach((dependency) => {
      const target = findAgentByName(dependency.target_agent, agents);
      const source = findDependencySourceAgent(dependency, agents);
      if (target) {
        incoming.add(target.id);
      }
      if (source) {
        outgoing.add(source.id);
      }
    });

    const graph = {
      sources: [],
      middle: [],
      terminal: [],
      disconnected: [],
    };

    agents.forEach((agent) => {
      const hasIncoming = incoming.has(agent.id);
      const hasOutgoing = outgoing.has(agent.id);
      if (!hasIncoming && hasOutgoing) {
        graph.sources.push(agent);
      } else if (hasIncoming && hasOutgoing) {
        graph.middle.push(agent);
      } else if (hasIncoming && !hasOutgoing) {
        graph.terminal.push(agent);
      } else {
        graph.disconnected.push(agent);
      }
    });

    return graph;
  }

  function renderWorkflowStage(title, agents, connectedAgentIds) {
    return `
      <section class="workflow-stage" aria-label="${escapeHtml(title)} agents">
        <div class="workflow-stage-title">${escapeHtml(title)}</div>
        <div class="workflow-stage-nodes">
          ${agents.length ? agents.map((agent) => renderGraphNode(agent, state.selectedAgentId, connectedAgentIds)).join("") : `<div class="empty-state">None</div>`}
        </div>
      </section>
    `;
  }

  function renderGraphNode(agent, selectedAgentId, connectedAgentIds) {
    const classes = graphNodeClasses(agent, selectedAgentId, connectedAgentIds);
    return `
      <button type="button" class="${classes.join(" ")}" data-agent-id="${escapeHtml(agent.id || "")}">
        ${renderGraphNodeContent(agent)}
      </button>
    `;
  }

  function renderPositionedGraphNode(agent, selectedAgentId, connectedAgentIds, child) {
    const classes = graphNodeClasses(agent, selectedAgentId, connectedAgentIds);
    const left = Math.max(Math.round(child.x || 0), 0);
    const top = Math.max(Math.round(child.y || 0), 0);
    const width = Math.max(Math.round(child.width || 180), 120);
    const height = Math.max(Math.round(child.height || 62), 48);
    return `
      <button
        type="button"
        class="${classes.join(" ")}"
        data-agent-id="${escapeHtml(agent.id || "")}"
        style="left: ${left}px; top: ${top}px; width: ${width}px; min-height: ${height}px;"
      >
        ${renderGraphNodeContent(agent)}
      </button>
    `;
  }

  function graphNodeClasses(agent, selectedAgentId, connectedAgentIds) {
    const agentId = agent.id || "";
    const isActive = agentId === selectedAgentId;
    const isConnected = connectedAgentIds.has(agentId);
    const hasWarning = Array.isArray(agent.warnings) && agent.warnings.length > 0;
    const classes = ["graph-node"];
    if (isActive) {
      classes.push("active");
    }
    if (isConnected) {
      classes.push("connected");
    }
    if (hasWarning) {
      classes.push("warning");
    }
    return classes;
  }

  function renderGraphNodeContent(agent) {
    const hasWarning = Array.isArray(agent.warnings) && agent.warnings.length > 0;
    return `
      <span class="agent-name">${escapeHtml(agent.name || agent.id || "Unnamed agent")}</span>
      <span class="agent-meta">${escapeHtml(agent.model || "Model unavailable")}</span>
      ${hasWarning ? `<span class="agent-meta">Warnings: ${agent.warnings.length}</span>` : ""}
    `;
  }

  function connectedAgentIdsForSelection(selectedAgentId, dependencies, agents) {
    const connected = new Set();
    if (!selectedAgentId) {
      return connected;
    }
    dependencies.forEach((dependency) => {
      const source = findDependencySourceAgent(dependency, agents);
      const target = findAgentByName(dependency.target_agent, agents);
      const touchesSelection =
        (source && source.id === selectedAgentId) || (target && target.id === selectedAgentId);
      if (touchesSelection) {
        if (source && source.id !== selectedAgentId) {
          connected.add(source.id);
        }
        if (target && target.id !== selectedAgentId) {
          connected.add(target.id);
        }
      }
    });
    return connected;
  }

  function resolvedDrawableDependencies(dependencies, agents) {
    return dependencies
      .map((dependency, index) => {
        const source = findDependencySourceAgent(dependency, agents);
        const target = findAgentByName(dependency.target_agent, agents);
        return { dependency, index, source, target };
      })
      .filter((edge) => edge.source && edge.target);
  }

  function unresolvedDependencies(dependencies, agents) {
    return dependencies
      .map((dependency) => {
        const source = findDependencySourceAgent(dependency, agents);
        const target = findAgentByName(dependency.target_agent, agents);
        return { dependency, source, target };
      })
      .filter((edge) => !edge.source || !edge.target);
  }

  function renderUnresolvedDependencyNote(dependencies, agents) {
    const unresolved = unresolvedDependencies(dependencies, agents);
    if (unresolved.length === 0) {
      return "";
    }

    return `<div class="notice">${escapeHtml(`${unresolved.length} dependencies could not be drawn because their source or target was unresolved.`)}</div>`;
  }

  function buildElkWorkflowGraph(agents, dependencies) {
    const edges = resolvedDrawableDependencies(dependencies, agents);
    const children = agents.map((agent) => ({
      id: agent.id || agent.name || "agent",
      width: 180,
      height: agent.warnings && agent.warnings.length ? 88 : 62,
      agent,
    }));
    return {
      id: "workflow-root",
      layoutOptions: {
        "elk.algorithm": "layered",
        'elk.direction': 'RIGHT',
        'elk.edgeRouting': 'ORTHOGONAL',
        "elk.spacing.nodeNode": "42",
        "elk.layered.spacing.nodeNodeBetweenLayers": "72",
        "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
      },
      children,
      edges: edges.map((edge, index) => ({
        id: `edge-${index}`,
        sources: [edge.source.id],
        targets: [edge.target.id],
        dependency: edge.dependency,
        sourceAgentId: edge.source.id,
        targetAgentId: edge.target.id,
      })),
    };
  }

  async function layoutWorkflowWithElk(systemRun) {
    const agents = getAgents(systemRun);
    const dependencies = getDependencies(systemRun);
    const connectedAgentIds = connectedAgentIdsForSelection(state.selectedAgentId, dependencies, agents);
    const layoutRequestId = state.workflowLayoutRequestId;
    if (!window.ELK) {
      throw new Error("ELK layout engine is unavailable.");
    }

    const elk = new ELK();
    const layout = await elk.layout(buildElkWorkflowGraph(agents, dependencies));
    if (layoutRequestId !== state.workflowLayoutRequestId) {
      return;
    }
    renderElkWorkflowLayout(layout, dependencies, connectedAgentIds, agents);
  }

  function renderElkWorkflowLayout(layout, dependencies, connectedAgentIds, agents) {
    const children = Array.isArray(layout.children) ? layout.children : [];
    const edgeMetadata = new Map(buildElkWorkflowGraph(agents, dependencies).edges.map((edge) => [edge.id, edge]));
    const nodeRects = elkNodeRects(children);
    const edgePaths = (Array.isArray(layout.edges) ? layout.edges : [])
      .map((edge) => renderElkWorkflowArrow(edge, edgeMetadata.get(edge.id), agents, nodeRects))
      .filter(Boolean)
      .join("");
    const diagramSize = elkDiagramSize(layout);
    const nodes = children
      .map((child) => {
        const agent = findAgentByExactIdentity(child.id, agents) || child.agent;
        return agent ? renderPositionedGraphNode(agent, state.selectedAgentId, connectedAgentIds, child) : "";
      })
      .join("");

    elements.workflowGraph.innerHTML = `
      <div
        class="workflow-diagram elk-layout"
        style="width: ${diagramSize.width}px; height: ${diagramSize.height}px; min-width: ${diagramSize.width}px; min-height: ${diagramSize.height}px;"
      >
        <svg
          class="workflow-arrow-layer"
          aria-hidden="true"
          viewBox="0 0 ${diagramSize.width} ${diagramSize.height}"
          width="${diagramSize.width}"
          height="${diagramSize.height}"
        >
          ${arrowMarkerDefinition()}
          ${edgePaths}
        </svg>
        ${nodes}
      </div>
      ${renderUnresolvedDependencyNote(dependencies, agents)}
    `;
    attachGraphNodeHandlers();
  }

  function renderElkWorkflowArrow(edge, metadata, agents, nodeRects) {
    const sourceAgentId = edge.sourceAgentId || (edge.sources && edge.sources[0]) || (metadata && metadata.sourceAgentId) || "";
    const targetAgentId = edge.targetAgentId || (edge.targets && edge.targets[0]) || (metadata && metadata.targetAgentId) || "";
    const dependency = edge.dependency || (metadata && metadata.dependency) || {};
    const source = findAgentByExactIdentity(sourceAgentId, agents);
    const target = findAgentByExactIdentity(targetAgentId, agents);
    const sections = Array.isArray(edge.sections) ? edge.sections : [];
    const path = routedElkEdgePath(sections, sourceAgentId, targetAgentId, nodeRects);
    if (!path || !sourceAgentId || !targetAgentId) {
      return "";
    }

    const isConnected =
      state.selectedAgentId &&
      (sourceAgentId === state.selectedAgentId || targetAgentId === state.selectedAgentId);
    return `
      <path
        class="workflow-arrow${isConnected ? " connected" : ""}"
        d="${escapeHtml(path)}"
        marker-end="${isConnected ? "url(#workflow-arrowhead-connected)" : "url(#workflow-arrowhead)"}"
        data-source-agent-id="${escapeHtml(sourceAgentId)}"
        data-target-agent-id="${escapeHtml(targetAgentId)}"
      >
        <title>${escapeHtml(dependencyQualityLabel(dependency, source, target))}</title>
      </path>
    `;
  }

  function elkSectionPath(section) {
    if (!section || !section.startPoint || !section.endPoint) {
      return "";
    }
    const points = [section.startPoint].concat(section.bendPoints || [], [section.endPoint]);
    return points
      .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`)
      .join(" ");
  }

  function routedElkEdgePath(sections, sourceAgentId, targetAgentId, nodeRects) {
    const rawPath = sections.map(elkSectionPath).filter(Boolean).join(" ");
    const points = edgeSectionPoints(sections);
    if (points.length < 2 || !edgePathCrossesUnrelatedNode(points, sourceAgentId, targetAgentId, nodeRects)) {
      return rawPath;
    }

    const start = points[0];
    const end = points[points.length - 1];
    const detour = detourPathAroundNodes(start, end, sourceAgentId, targetAgentId, nodeRects);
    return detour || rawPath;
  }

  function edgeSectionPoints(sections) {
    const points = [];
    sections.forEach((section) => {
      if (!section || !section.startPoint || !section.endPoint) {
        return;
      }
      [section.startPoint].concat(section.bendPoints || [], [section.endPoint]).forEach((point) => {
        if (!points.length || points[points.length - 1].x !== point.x || points[points.length - 1].y !== point.y) {
          points.push(point);
        }
      });
    });
    return points;
  }

  function edgePathCrossesUnrelatedNode(points, sourceAgentId, targetAgentId, nodeRects) {
    const obstacles = unrelatedNodeRects(sourceAgentId, targetAgentId, nodeRects);
    for (let index = 0; index < points.length - 1; index += 1) {
      if (segmentCrossesAnyRect(points[index], points[index + 1], obstacles)) {
        return true;
      }
    }
    return false;
  }

  function detourPathAroundNodes(start, end, sourceAgentId, targetAgentId, nodeRects) {
    const obstacles = unrelatedNodeRects(sourceAgentId, targetAgentId, nodeRects);
    const startLaneX = start.x + 30;
    const endLaneX = end.x - 30;
    const candidates = detourLaneCandidates(start, end, obstacles);
    const laneY = candidates.find((candidate) => {
      return !segmentCrossesAnyRect({ x: startLaneX, y: candidate }, { x: endLaneX, y: candidate }, obstacles);
    });
    if (laneY === undefined) {
      return "";
    }
    return [
      `M ${start.x} ${start.y}`,
      `L ${startLaneX} ${start.y}`,
      `L ${startLaneX} ${laneY}`,
      `L ${endLaneX} ${laneY}`,
      `L ${endLaneX} ${end.y}`,
      `L ${end.x} ${end.y}`,
    ].join(" ");
  }

  function detourLaneCandidates(start, end, obstacles) {
    const midY = (start.y + end.y) / 2;
    const candidates = [];
    obstacles.forEach((rect) => {
      candidates.push(Math.max(8, Math.round(rect.y - 18)));
      candidates.push(Math.round(rect.y + rect.height + 18));
    });
    candidates.push(Math.max(8, Math.round(Math.min(start.y, end.y) - 34)));
    candidates.push(Math.round(Math.max(start.y, end.y) + 34));
    return [...new Set(candidates)].sort((left, right) => Math.abs(left - midY) - Math.abs(right - midY));
  }

  function unrelatedNodeRects(sourceAgentId, targetAgentId, nodeRects) {
    return nodeRects.filter((rect) => rect.id !== sourceAgentId && rect.id !== targetAgentId);
  }

  function segmentCrossesAnyRect(start, end, rects) {
    return rects.some((rect) => segmentCrossesRect(start, end, rect));
  }

  function segmentCrossesRect(start, end, rect) {
    const padding = 0;
    const left = rect.x + padding;
    const right = rect.x + rect.width - padding;
    const top = rect.y + padding;
    const bottom = rect.y + rect.height - padding;
    if (start.y === end.y) {
      const y = start.y;
      const minX = Math.min(start.x, end.x);
      const maxX = Math.max(start.x, end.x);
      return y > top && y < bottom && maxX > left && minX < right;
    }
    if (start.x === end.x) {
      const x = start.x;
      const minY = Math.min(start.y, end.y);
      const maxY = Math.max(start.y, end.y);
      return x > left && x < right && maxY > top && minY < bottom;
    }
    return false;
  }

  function elkNodeRects(children) {
    return children.map((child) => ({
      id: child.id,
      x: child.x || 0,
      y: child.y || 0,
      width: child.width || 0,
      height: child.height || 0,
    }));
  }

  function elkDiagramSize(layout) {
    const points = [];
    (Array.isArray(layout.children) ? layout.children : []).forEach((child) => {
      points.push({ x: (child.x || 0) + (child.width || 0), y: (child.y || 0) + (child.height || 0) });
    });
    (Array.isArray(layout.edges) ? layout.edges : []).forEach((edge) => {
      (Array.isArray(edge.sections) ? edge.sections : []).forEach((section) => {
        [section.startPoint].concat(section.bendPoints || [], [section.endPoint]).forEach((point) => {
          if (point) {
            points.push(point);
          }
        });
      });
    });
    const width = Math.max(layout.width || 0, ...points.map((point) => point.x || 0), elements.workflowGraph.clientWidth, 1);
    const height = Math.max(layout.height || 0, ...points.map((point) => point.y || 0), 170);
    return {
      width: Math.ceil(width + 24),
      height: Math.ceil(height + 24),
    };
  }

  function scheduleWorkflowRelayout() {
    if (state.workflowRelayoutFrame !== null) {
      window.cancelAnimationFrame(state.workflowRelayoutFrame);
    }
    state.workflowRelayoutFrame = window.requestAnimationFrame(() => {
      state.workflowRelayoutFrame = null;
      const run = selectedRun();
      const systemRun = selectedSystemRun(run);
      if (systemRun) {
        renderWorkflowGraph(systemRun);
      }
    });
  }

  function scheduleDrawWorkflowArrows() {
    window.requestAnimationFrame(drawWorkflowArrows);
  }

  function drawWorkflowArrows() {
    const run = selectedRun();
    const systemRun = selectedSystemRun(run);
    const agents = getAgents(systemRun);
    const dependencies = getDependencies(systemRun);
    const svg = elements.workflowGraph.querySelector(".workflow-arrow-layer");
    const diagram = elements.workflowGraph.querySelector(".workflow-diagram");
    if (!svg || !diagram) {
      return;
    }

    const diagramBox = diagram.getBoundingClientRect();
    const width = Math.max(diagramBox.width, 1);
    const height = Math.max(diagramBox.height, 1);
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("width", String(width));
    svg.setAttribute("height", String(height));
    svg.innerHTML = arrowMarkerDefinition();

    resolvedDrawableDependencies(dependencies, agents).forEach((edge) => {
      const sourceNode = elements.workflowGraph.querySelector(`[data-agent-id="${cssEscape(edge.source.id || "")}"]`);
      const targetNode = elements.workflowGraph.querySelector(`[data-agent-id="${cssEscape(edge.target.id || "")}"]`);
      if (!sourceNode || !targetNode) {
        return;
      }

      const path = workflowArrowPath(sourceNode.getBoundingClientRect(), targetNode.getBoundingClientRect(), diagramBox);
      const isConnected =
        state.selectedAgentId &&
        (edge.source.id === state.selectedAgentId || edge.target.id === state.selectedAgentId);
      const title = dependencyQualityLabel(edge.dependency, edge.source, edge.target);
      const pathElement = document.createElementNS("http://www.w3.org/2000/svg", "path");
      pathElement.setAttribute("class", `workflow-arrow${isConnected ? " connected" : ""}`);
      pathElement.setAttribute("d", path);
      pathElement.setAttribute(
        "marker-end",
        isConnected ? "url(#workflow-arrowhead-connected)" : "url(#workflow-arrowhead)",
      );
      pathElement.setAttribute("data-source-agent-id", edge.source.id || "");
      pathElement.setAttribute("data-target-agent-id", edge.target.id || "");

      const titleElement = document.createElementNS("http://www.w3.org/2000/svg", "title");
      titleElement.textContent = title;
      pathElement.appendChild(titleElement);
      svg.appendChild(pathElement);
    });
  }

  function arrowMarkerDefinition() {
    return `
      <defs>
        <marker id="workflow-arrowhead" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z"></path>
        </marker>
        <marker id="workflow-arrowhead-connected" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z"></path>
        </marker>
      </defs>
    `;
  }

  function workflowArrowPath(sourceBox, targetBox, diagramBox) {
    const startX = sourceBox.left + sourceBox.width / 2 - diagramBox.left;
    const startY = sourceBox.bottom - diagramBox.top;
    const endX = targetBox.left + targetBox.width / 2 - diagramBox.left;
    const endY = targetBox.top - diagramBox.top;
    const midY = startY + Math.max((endY - startY) / 2, 24);
    return `M ${startX} ${startY} C ${startX} ${midY}, ${endX} ${midY}, ${endX} ${endY}`;
  }

  function cssEscape(value) {
    if (window.CSS && typeof window.CSS.escape === "function") {
      return window.CSS.escape(value);
    }
    return String(value).replace(/["\\]/g, "\\$&");
  }

  function findDependencySourceAgent(dependency, agents) {
    if (!dependency) {
      return null;
    }
    if (dependency.source_agent) {
      return findAgentByExactIdentity(dependency.source_agent, agents);
    }
    return null;
  }

  function findAgentByName(name, agents) {
    return findAgentByExactIdentity(name, agents);
  }

  function findAgentByExactIdentity(name, agents) {
    if (!name) {
      return null;
    }
    const expectedName = String(name);
    return agents.find((agent) => {
      return agent.id === expectedName || agent.name === expectedName;
    }) || null;
  }

  function dependencyQualityLabel(dependency, source, target) {
    const labels = [dependency.source_label || dependency.source_type || "dependency"];
    const sourceStatus = dependency.source_status || (dependency.source_agent ? "resolved_source" : "unresolved_source");
    if (source) {
      labels.push("resolved source");
    } else if (sourceStatus === "ambiguous_source") {
      labels.push("ambiguous source");
    } else if (dependency.source_agent) {
      labels.push("missing source agent");
    } else {
      labels.push("unresolved source");
    }
    if (!target) {
      labels.push("missing target");
    }
    return labels.join(" | ");
  }

  function renderRightPanel(run, systemRun) {
    const agent = selectedAgent(systemRun);
    if (!agent) {
      renderNoSelectedAgent(run, systemRun);
      return;
    }

    renderAgentDetail(agent, systemRun);
  }

  function renderSystemOverview(run, systemRun) {
    const agents = getAgents(systemRun);
    const dependencies = getDependencies(systemRun);
    const warnings = collectVisibleWarnings(run, systemRun, null);
    setDetailMode("overview");
    elements.overviewArea.innerHTML = `
      <div class="section-header">
        <h3>System Overview</h3>
        <span>${escapeHtml(systemRunLabel(systemRun))}</span>
      </div>
      <div class="overview-card">
        <div class="metadata-grid">
          ${metadataItem("Run", run && (run.label || run.id) ? run.label || run.id : "Unavailable")}
          ${metadataItem("Mode", systemRun && systemRun.mode ? systemRun.mode : "Unavailable")}
          ${metadataItem("Current time", systemRun && systemRun.current_datetime ? systemRun.current_datetime : "Unavailable")}
          ${metadataItem("Agents", `${agents.length} agent${agents.length === 1 ? "" : "s"}`)}
          ${metadataItem("Dependencies", `${dependencies.length} dependenc${dependencies.length === 1 ? "y" : "ies"}`)}
          ${metadataItem("Warnings", `${warnings.length}`)}
        </div>
        <div>
          <strong>Agents in this system run</strong>
          <div class="overview-agent-list">
            ${agents.length ? agents.map(renderOverviewAgent).join("") : `<div class="empty-state">No agents in this system run.</div>`}
          </div>
        </div>
        <div class="notice">Select an agent in the workflow graph to inspect input material, tool calls, and final summary.</div>
        ${renderVisibleWarnings(warnings)}
      </div>
    `;
    elements.inputArea.innerHTML = "";
    elements.toolArea.innerHTML = "";
    elements.boundaryTraceArea.innerHTML = "";
    elements.summaryArea.innerHTML = "";
  }

  function renderOverviewAgent(agent) {
    return `
      <div class="dependency-item">
        <strong>${escapeHtml(agent.name || agent.id || "Unnamed agent")}</strong>
        <div class="agent-meta">${escapeHtml(agent.model || "Model unavailable")}</div>
      </div>
    `;
  }

  function renderAgentDetail(agent, systemRun) {
    setDetailMode("agent");
    renderInputArea(agent);
    renderToolArea(agent);
    renderBoundaryTraceArea(agent);
    renderSummaryArea(agent, systemRun);
  }

  function setDetailMode(mode) {
    const showOverview = mode === "overview";
    elements.overviewArea.hidden = !showOverview;
    elements.inputArea.hidden = showOverview;
    elements.toolArea.hidden = showOverview;
    elements.boundaryTraceArea.hidden = showOverview;
    elements.summaryArea.hidden = showOverview;
  }

  function renderInputArea(agent) {
    const input = agent.input_material || {};
    const toolNames = Array.isArray(input.available_tool_names) ? input.available_tool_names : [];
    const contextKeys = Array.isArray(input.context_keys) ? input.context_keys : [];
    const userSystemHeading = input.user_system_prompt_heading || "USER SYSTEM PROMPT:";
    const priorityExplanation = input.priority_explanation || "";
    const summary = `4 prompts | ${contextKeys.length} context key${contextKeys.length === 1 ? "" : "s"} | ${input.available_tool_count || toolNames.length || 0} tools`;

    const body = `
      <div class="metadata-grid">
        ${metadataItem("Run mode", input.run_mode || "Unavailable")}
        ${metadataItem("Current time", input.current_time || "Unavailable")}
        ${metadataItem("Context keys", contextKeys.length ? contextKeys.join(", ") : "None")}
        ${metadataItem("Available tools", `${input.available_tool_count || toolNames.length || 0}`)}
      </div>
      ${renderCollapsibleSubsection(
        "Available Tool Names",
        `${toolNames.length} tool${toolNames.length === 1 ? "" : "s"}`,
        `<div class="prewrap">${escapeHtml(toolNames.length ? toolNames.join(", ") : "None")}</div>`,
        false,
      )}
      ${renderCollapsibleSubsection(
        "Base System Prompt",
        characterCountLabel(input.base_system_prompt),
        `<pre>${formatValue(input.base_system_prompt)}</pre>`,
        false,
      )}
      ${renderCollapsibleSubsection(
        "Effective System Prompt",
        characterCountLabel(input.effective_system_prompt),
        `<pre>${formatValue(input.effective_system_prompt)}</pre>`,
        true,
      )}
      ${renderCollapsibleSubsection(
        userSystemHeading,
        characterCountLabel(input.agent_system_prompt),
        `${priorityExplanation ? `<div class="notice">${escapeHtml(priorityExplanation)}</div>` : ""}<pre>${formatValue(input.agent_system_prompt)}</pre>`,
        false,
      )}
      ${renderCollapsibleSubsection(
        "Task Prompt",
        characterCountLabel(input.task_prompt),
        `<pre>${formatValue(input.task_prompt)}</pre>`,
        false,
      )}
      ${renderCollapsibleSubsection(
        "Context JSON",
        `${contextKeys.length} key${contextKeys.length === 1 ? "" : "s"}`,
        `<pre>${formatValue(input.context)}</pre>`,
        false,
      )}
    `;

    elements.inputArea.innerHTML = renderCollapsibleSection(
      "Input Material",
      `${agent.name || "Unnamed agent"} | ${summary}`,
      body,
      false,
    );
  }

  function renderToolArea(agent) {
    const batches = Array.isArray(agent.tool_batches) ? agent.tool_batches : [];
    const totalCalls = batches.reduce((count, batch) => {
      const calls = Array.isArray(batch.calls) ? batch.calls : [];
      return count + calls.length;
    }, 0);

    if (totalCalls === 0) {
      elements.toolArea.innerHTML = renderCollapsibleSection(
        "Tool Calls",
        "0 recorded calls",
        `<div class="empty-state">No tool calls were recorded for this agent.</div>`,
        true,
      );
      return;
    }

    const body = batches.map((batch, index) => renderToolBatch(batch, index === 0)).join("");
    elements.toolArea.innerHTML = renderCollapsibleSection(
      "Tool Calls",
      `${batches.length} batch${batches.length === 1 ? "" : "es"} | ${totalCalls} recorded call${totalCalls === 1 ? "" : "s"}`,
      body,
      true,
    );
  }

  function renderToolBatch(batch, isOpen) {
    const calls = Array.isArray(batch.calls) ? batch.calls : [];
    const batchIndex = batch.batch_index ?? "";
    const rows = calls.map((call) => ({ batchIndex, call }));
    return renderCollapsibleSubsection(
      `Batch ${batchIndex}`,
      `${calls.length} call${calls.length === 1 ? "" : "s"}`,
      `
        <div class="tool-table-wrap">
          <table class="tool-table">
            <thead>
              <tr>
                <th>Batch</th>
                <th>Tool Input</th>
                <th>Tool Output</th>
                <th>Human Explanation</th>
              </tr>
            </thead>
            <tbody>
              ${rows.map(renderToolRow).join("")}
            </tbody>
          </table>
        </div>
      `,
      isOpen,
    );
  }

  function renderSummaryArea(agent, systemRun) {
    const warnings = collectAgentWarnings(agent);
    const dependencies = connectedDependenciesForAgent(agent, systemRun);
    const body = `
      ${renderCollapsibleSubsection(
        "Connected Dependencies",
        `${dependencies.length} dependenc${dependencies.length === 1 ? "y" : "ies"}`,
        `<pre>${formatValue(dependencies)}</pre>`,
        true,
      )}
      ${renderCollapsibleSubsection(
        "Summary",
        characterCountLabel(agent.summary),
        `<pre>${formatValue(agent.summary)}</pre>`,
        true,
      )}
      ${renderVisibleWarnings(warnings)}
    `;
    elements.summaryArea.innerHTML = renderCollapsibleSection(
      "Final Summary",
      agent.trace_path || "",
      body,
      true,
    );
  }

  function renderBoundaryTraceArea(agent) {
    const trace = agent.boundary_trace || {};
    if (!trace.available) {
      elements.boundaryTraceArea.innerHTML = renderCollapsibleSection(
        "LLM <-> Tool Boundary Trace",
        "not available",
        `<div class="empty-state">${escapeHtml(trace.message || "This trace does not contain 10-step boundary trace data. It may have been created before boundary tracing was added.")}</div>`,
        true,
      );
      return;
    }

    const events = Array.isArray(trace.events) ? trace.events : [];
    const eventsById = boundaryEventsById(events);
    const turns = Array.isArray(trace.model_turns) ? trace.model_turns.map(boundaryItemOrEmpty) : [];
    const body = `
      <div class="boundary-trace">
        ${turns.length ? turns.map((turn, index) => renderBoundaryModelTurn(turn, eventsById, index === 0)).join("") : '<div class="empty-state">Boundary trace contains no model turns.</div>'}
      </div>
    `;
    elements.boundaryTraceArea.innerHTML = renderCollapsibleSection(
      "LLM <-> Tool Boundary Trace",
      `${turns.length} model turn${turns.length === 1 ? "" : "s"} | ${events.length} event${events.length === 1 ? "" : "s"}`,
      body,
      true,
    );
  }

  function boundaryEventsById(events) {
    const map = new Map();
    (Array.isArray(events) ? events : []).forEach((event) => {
      if (event && event.id) {
        map.set(event.id, event);
      }
    });
    return map;
  }

  function renderBoundaryModelTurn(turn, eventsById, isOpen) {
    turn = boundaryItemOrEmpty(turn);
    const requestEvents = eventIdsToEvents(turn.request_response_events, eventsById);
    const batches = Array.isArray(turn.tool_batches) ? turn.tool_batches.map(boundaryItemOrEmpty) : [];
    const body = `
      <div class="boundary-event-group">
        <h4>Model Request / Response</h4>
        ${requestEvents.length ? requestEvents.map(renderBoundaryEventRow).join("") : '<div class="empty-state">No model request/response boundary events recorded.</div>'}
      </div>
      ${batches.map((batch, index) => renderBoundaryToolBatch(batch, eventsById, index === 0)).join("")}
    `;
    return renderCollapsibleSubsection(
      `Model Turn ${modelTurnLabel(turn.model_turn_id)}`,
      turn.model_turn_id || "unknown turn",
      body,
      isOpen,
    );
  }

  function modelTurnLabel(modelTurnId) {
    const match = String(modelTurnId || "").match(/turn[:_-]?(\d+)$/i);
    return match ? String(Number(match[1])) : String(modelTurnId || "unknown");
  }

  function renderBoundaryToolBatch(batch, eventsById, isOpen) {
    batch = boundaryItemOrEmpty(batch);
    const calls = Array.isArray(batch.tool_calls) ? batch.tool_calls.map(boundaryItemOrEmpty) : [];
    const body = calls.length
      ? calls.map((call) => renderBoundaryToolCall(call, eventsById)).join("")
      : '<div class="empty-state">No tool calls recorded in this boundary batch.</div>';
    return renderCollapsibleSubsection(
      `Tool Batch ${batch.tool_batch_id || "unknown"}`,
      `${calls.length} call${calls.length === 1 ? "" : "s"}`,
      body,
      isOpen,
    );
  }

  function renderBoundaryToolCall(call, eventsById) {
    call = boundaryItemOrEmpty(call);
    const events = eventIdsToEvents(call.events, eventsById);
    return `
      <div class="boundary-call-span">
        <div class="boundary-call-heading">
          <strong>${escapeHtml(call.tool_name || "Unknown tool")}</strong>
          <span>${escapeHtml(call.call_id || "unknown call")}</span>
        </div>
        ${events.length ? events.map(renderBoundaryEventRow).join("") : '<div class="empty-state">No boundary events recorded for this tool call.</div>'}
      </div>
    `;
  }

  function eventIdsToEvents(ids, eventsById) {
    return (Array.isArray(ids) ? ids : [])
      .map((id) => eventsById.get(id))
      .filter(Boolean);
  }

  function renderBoundaryEventRow(event) {
    event = boundaryItemOrEmpty(event);
    const summary = boundaryItemOrEmpty(event.summary);
    return `
      <details class="boundary-event">
        <summary>
          <span class="boundary-code">${escapeHtml(event.transition || "UNKNOWN")}</span>
          <span class="boundary-label">${escapeHtml(summary.label || "")}</span>
          <span class="boundary-route">${escapeHtml(summary.source || "?")} -> ${escapeHtml(summary.target || "?")}</span>
          ${renderBoundaryBadges(boundaryBadgesForEvent(event, summary))}
        </summary>
        <div class="boundary-event-body">
          ${summary.explanation ? `<div class="notice">${escapeHtml(summary.explanation)}</div>` : ""}
          <div class="boundary-preview">${escapeHtml(summary.preview || "No preview available.")}</div>
          <pre>${formatValue({
            id: event.id,
            status: event.status,
            model_turn_id: event.model_turn_id,
            tool_batch_id: event.tool_batch_id,
            call_id: event.call_id,
            payload_meta: event.payload_meta,
            payload: event.payload,
          })}</pre>
          ${renderBoundarySidecarButton(event)}
        </div>
      </details>
    `;
  }

  function boundaryBadgesForEvent(event, summary) {
    event = boundaryItemOrEmpty(event);
    summary = boundaryItemOrEmpty(summary);
    const badges = [];
    if (event.status) {
      badges.push(event.status);
    }
    (Array.isArray(summary.badges) ? summary.badges : []).forEach((badge) => {
      if (!badges.includes(badge)) {
        badges.push(badge);
      }
    });
    if (event.sidecar && event.sidecar.available && !badges.includes("sidecar")) {
      badges.push("sidecar");
    }
    return badges;
  }

  function renderBoundaryBadges(badges) {
    return (Array.isArray(badges) ? badges : [])
      .map((badge) => `<span class="boundary-badge">${escapeHtml(badge)}</span>`)
      .join("");
  }

  function renderBoundarySidecarButton(event) {
    event = boundaryItemOrEmpty(event);
    if (!event.sidecar || !event.sidecar.available) {
      return "";
    }
    const eventId = event.sidecar.event_id || event.id;
    if (!eventId) {
      return `<div class="boundary-sidecar-unavailable" role="status">Full sidecar payload unavailable: missing event id.</div>`;
    }
    return `
      <div class="boundary-sidecar-controls">
        <button class="secondary-button boundary-sidecar-button" type="button" data-boundary-event-id="${escapeHtml(eventId)}">Load full sidecar payload</button>
        <pre class="boundary-sidecar-output" hidden></pre>
      </div>
    `;
  }

  async function loadBoundarySidecar(button) {
    const eventId = button.getAttribute("data-boundary-event-id");
    const controls = button.closest(".boundary-sidecar-controls");
    const output = controls ? controls.querySelector(".boundary-sidecar-output") : null;
    if (!eventId || !output) {
      return;
    }

    button.disabled = true;
    button.textContent = "Loading full sidecar payload...";
    output.hidden = true;
    output.textContent = "";
    output.classList.remove("error");

    try {
      const response = await fetch(`/api/boundary-payload/${encodeURIComponent(eventId)}`, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const payload = await response.json();
      output.hidden = false;
      output.textContent = formatPlainValue(payload.payload);
      button.textContent = "Full sidecar payload loaded";
    } catch (error) {
      output.hidden = false;
      output.textContent = `Unable to load sidecar payload: ${error.message}`;
      output.classList.add("error");
      button.textContent = "Load full sidecar payload";
      button.disabled = false;
    }
  }

  function boundaryItemOrEmpty(item) {
    return item && typeof item === "object" && !Array.isArray(item) ? item : {};
  }

  function renderCollapsibleSection(title, meta, body, isOpen) {
    return `
      <details class="collapsible-section" ${isOpen ? "open" : ""}>
        <summary class="collapsible-summary">
          <span class="collapsible-title">${escapeHtml(title)}</span>
          <span class="collapsible-meta">${escapeHtml(meta)}</span>
        </summary>
        <div class="collapsible-body">
          ${body}
        </div>
      </details>
    `;
  }

  function renderCollapsibleSubsection(title, meta, body, isOpen) {
    return `
      <details class="collapsible-subsection" ${isOpen ? "open" : ""}>
        <summary class="collapsible-summary">
          <span class="collapsible-title">${escapeHtml(title)}</span>
          <span class="collapsible-meta">${escapeHtml(meta)}</span>
        </summary>
        <div class="collapsible-body">
          ${body}
        </div>
      </details>
    `;
  }

  function characterCountLabel(value) {
    if (value === null || value === undefined || value === "") {
      return "empty";
    }
    const text = typeof value === "string" ? value : JSON.stringify(value);
    return `${text.length} chars`;
  }

  function connectedDependenciesForAgent(agent, systemRun) {
    const dependencies = getDependencies(systemRun);
    const agents = getAgents(systemRun);
    return dependencies
      .filter((dependency) => {
        const source = findDependencySourceAgent(dependency, agents);
        const target = findAgentByName(dependency.target_agent, agents);
        return (source && source.id === agent.id) || (target && target.id === agent.id);
      })
      .map((dependency) => ({
        source: dependency.source_label || "unknown source",
        target: dependency.target_agent || "unknown target",
        type: dependency.source_type || "dependency",
      }));
  }

  function renderToolRow(row) {
    const call = row.call || {};
    const inputPayload = {
      tool_name: call.tool_name || null,
      arguments: call.arguments || {},
      timestamp: call.timestamp || null,
    };
    const outputPayload = call.error ? { error: call.error, raw_result: call.raw_result } : call.raw_result;
    return `
      <tr>
        <td>${escapeHtml(String(row.batchIndex ?? ""))}</td>
        <td><pre>${formatValue(inputPayload)}</pre></td>
        <td><pre>${formatValue(outputPayload)}</pre></td>
        <td><pre>${formatValue(call.human_explanation)}</pre></td>
      </tr>
    `;
  }

  function renderVisibleWarnings(warnings) {
    if (!warnings.length) {
      return "";
    }
    return `
      <div class="warnings">
        <h4>Warnings</h4>
        <div class="notice">Warnings exist in the replay dataset. Review these before trusting the replay.</div>
        ${warnings.map(renderWarningItem).join("")}
      </div>
    `;
  }

  function renderWarningItem(entry) {
    return `
      <div class="warning-item">
        <strong>${escapeHtml(entry.scope)}</strong>
        <pre>${formatValue(entry.warning)}</pre>
      </div>
    `;
  }

  function collectVisibleWarnings(run, systemRun, agent) {
    const warnings = [];
    const dataset = state.dataset || {};
    appendWarnings(warnings, "Dataset", dataset.warnings);
    appendWarnings(warnings, "Run", run && run.warnings);
    appendWarnings(warnings, "System run", systemRun && systemRun.warnings);
    appendWarnings(warnings, "Agent", agent && agent.warnings);
    return warnings;
  }

  function collectAgentWarnings(agent) {
    const warnings = [];
    appendWarnings(warnings, "Agent", agent && agent.warnings);
    return warnings;
  }

  function appendWarnings(target, scope, warnings) {
    if (!Array.isArray(warnings)) {
      return;
    }
    warnings.forEach((warning) => {
      target.push({ scope, warning });
    });
  }

  function fillSelect(select, items, valueFn, labelFn, selectedValue) {
    select.innerHTML = "";
    items.forEach((item) => {
      const option = document.createElement("option");
      option.value = valueFn(item) || "";
      option.textContent = labelFn(item);
      option.selected = option.value === selectedValue;
      select.appendChild(option);
    });
  }

  function metadataItem(label, value) {
    return `
      <div class="metadata-item">
        <span class="metadata-label">${escapeHtml(label)}</span>
        <span class="metadata-value">${escapeHtml(String(value))}</span>
      </div>
    `;
  }

  function sectionShell(title, message) {
    return `
      <div class="section-header">
        <h3>${escapeHtml(title)}</h3>
      </div>
      <div class="empty-state">${escapeHtml(message)}</div>
    `;
  }

  function datasetStatusText(run, systemRun) {
    const generated = state.dataset && state.dataset.generated_at ? `Generated ${state.dataset.generated_at}` : "";
    const strategyLabel = state.selectedStrategyName ? `Strategy: ${state.selectedStrategyName}` : "";
    const runLabel = run && (run.label || run.id) ? `Backtest: ${backtestRunLabel(run)}` : "";
    const systemLabel = systemRun ? `System: ${systemRunLabel(systemRun)}` : "";
    return [strategyLabel, runLabel, systemLabel, generated].filter(Boolean).join(" | ") || "Replay dataset loaded.";
  }

  function backtestRunLabel(run) {
    if (!run) {
      return "No backtest run";
    }
    const label = run.label || run.id || "Unnamed backtest run";
    const systemRunCount = getSystemRuns(run).length;
    const suffix = systemRunCount === 1 ? "1 system run" : `${systemRunCount} system runs`;
    return `${label} (${suffix})`;
  }

  function systemRunLabel(systemRun) {
    if (!systemRun) {
      return "No system run";
    }
    return systemRun.name || [systemRun.mode, systemRun.current_datetime].filter(Boolean).join(" / ") || systemRun.id || "Unnamed system run";
  }

  function selectedRun() {
    const runs = getBacktestRunsForStrategy(state.selectedStrategyName);
    return runs.find((run) => run.id === state.selectedBacktestRunId) || runs[0] || null;
  }

  function selectedSystemRun(run) {
    const systemRuns = getSystemRuns(run);
    return systemRuns.find((systemRun) => systemRun.id === state.selectedSystemRunId) || systemRuns[0] || null;
  }

  function selectedAgent(systemRun) {
    const agents = getAgents(systemRun);
    if (!state.selectedAgentId) {
      return null;
    }
    return agents.find((agent) => agent.id === state.selectedAgentId) || null;
  }

  function getRuns() {
    return state.dataset && Array.isArray(state.dataset.runs) ? state.dataset.runs : [];
  }

  function getStrategyNames() {
    const names = getRuns()
      .map((run) => strategyNameForRun(run))
      .filter(Boolean);
    return Array.from(new Set(names)).sort();
  }

  function getBacktestRunsForStrategy(strategyName) {
    return getRuns().filter((run) => strategyNameForRun(run) === strategyName);
  }

  function strategyNameForRun(run) {
    return run && typeof run.strategy_name === "string" && run.strategy_name
      ? run.strategy_name
      : "unknown-strategy";
  }

  function getSystemRuns(run) {
    if (!run) {
      return [];
    }
    if (Array.isArray(run.system_runs)) {
      return run.system_runs;
    }
    return Array.isArray(run.systems) ? run.systems : [];
  }

  function getAgents(systemRun) {
    return systemRun && Array.isArray(systemRun.agents) ? systemRun.agents : [];
  }

  function getDependencies(systemRun) {
    return systemRun && Array.isArray(systemRun.dependencies) ? systemRun.dependencies : [];
  }

  function setStatus(message, isError) {
    elements.statusMessage.textContent = message;
    elements.statusMessage.classList.toggle("error", Boolean(isError));
  }

  function formatValue(value) {
    if (value === null || value === undefined || value === "") {
      return escapeHtml("Unavailable");
    }
    if (typeof value === "string") {
      return escapeHtml(value);
    }
    try {
      return escapeHtml(JSON.stringify(value, null, 2));
    } catch (error) {
      return escapeHtml(String(value));
    }
  }

  function formatPlainValue(value) {
    if (value === null || value === undefined || value === "") {
      return "Unavailable";
    }
    if (typeof value === "string") {
      return value;
    }
    try {
      return JSON.stringify(value, null, 2);
    } catch (error) {
      return String(value);
    }
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }
})();
