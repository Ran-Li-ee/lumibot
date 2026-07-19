# Agent Replay Backtest Result Links Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `Account Curve` and `Performance Report` buttons to Agent Replay UI for the selected backtest run.

**Architecture:** Extend replay dataset models with backtest artifact metadata, let the Flask server map safe opaque artifact tokens to files under configured artifact roots, and render two header buttons in the existing static UI. Keep the account curve page lightweight and generated from `stats.csv`/`result.json`.

**Tech Stack:** Python dataclasses, Flask, pytest, vanilla JavaScript/CSS.

---

## Task 1: Loader and Model Metadata

**Files:**
- Modify: `lumibot/components/agents/replay_ui/models.py`
- Modify: `lumibot/components/agents/replay_ui/loader.py`
- Modify: `tests/test_agent_replay_ui_loader.py`

Steps:

- [ ] Add failing loader test that creates `stats.csv` under a backtest root and asserts `ReplayRun.to_public_dict()["artifacts"]["account_curve"]["available"] is True`.
- [ ] Add failing loader test that creates `example_tearsheet.html` and asserts `performance_report.available is True`.
- [ ] Add `artifacts` field to `ReplayRun`.
- [ ] Add loader helper to derive backtest artifact root from `cache/agent_runtime`.
- [ ] Add artifact availability metadata without exposing raw filesystem paths as URLs.

## Task 2: Flask Artifact Routes

**Files:**
- Modify: `lumibot/components/agents/replay_ui/server.py`
- Test: `tests/test_agent_trace_ui_script.py` or create/extend existing server test.

Steps:

- [ ] Add failing server test for `/artifacts/<token>/account-curve`.
- [ ] Add failing server test for `/artifacts/<token>/performance-report`.
- [ ] Build a per-app artifact registry from configured trace roots.
- [ ] Serve account curve HTML from `stats.csv` and optional `result.json`.
- [ ] Serve existing report HTML read-only when found.
- [ ] Return 404 for unknown tokens or missing artifacts.

## Task 3: Static UI Buttons

**Files:**
- Modify: `lumibot/components/agents/replay_ui/static/index.html`
- Modify: `lumibot/components/agents/replay_ui/static/app.js`
- Modify: `lumibot/components/agents/replay_ui/static/styles.css`
- Modify: `tests/test_agent_replay_ui_static.py`

Steps:

- [ ] Add failing static test requiring `Account Curve` and `Performance Report` controls.
- [ ] Render buttons from selected run artifact metadata.
- [ ] Disable missing buttons with title reason.
- [ ] Open available links in a new tab.
- [ ] Keep layout compact and consistent with existing header controls.

## Task 4: Verification

Steps:

- [ ] Run targeted replay UI tests.
- [ ] Run focused ruff on modified Python files.
- [ ] Start local UI against existing artifacts and inspect `/api/dataset`.
- [ ] Capture or inspect browser rendering enough to confirm buttons appear and disabled state works.
- [ ] Run `git diff --check`.
