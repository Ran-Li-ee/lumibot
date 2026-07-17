import scripts.agent_trace_ui as agent_trace_ui


def test_default_trace_roots_include_project_and_benchmark_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_trace_ui, "REPO_ROOT", tmp_path)
    local_root = tmp_path / ".lumibot" / "agent_runtime"
    benchmark_root = (
        tmp_path
        / "artifacts"
        / "ai_trading_team_example_benchmarks"
        / "20260717_185016_461584"
        / "growth-execution-test"
        / "cache"
        / "agent_runtime"
    )
    (local_root / "traces").mkdir(parents=True)
    (benchmark_root / "traces").mkdir(parents=True)

    roots = agent_trace_ui.default_trace_roots()

    assert roots == [local_root, benchmark_root]


def test_default_trace_roots_honor_explicit_env_override(tmp_path, monkeypatch):
    explicit = tmp_path / "custom" / "agent_runtime"
    monkeypatch.setenv("LUMIBOT_AGENT_TRACE_ROOT", str(explicit))

    assert agent_trace_ui.default_trace_roots() == [explicit]
