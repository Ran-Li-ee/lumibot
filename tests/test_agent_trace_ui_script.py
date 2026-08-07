import scripts.agent_trace_ui as agent_trace_ui


def test_default_trace_roots_include_project_global_and_benchmark_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_trace_ui, "REPO_ROOT", tmp_path)
    local_root = tmp_path / ".lumibot" / "agent_runtime"
    global_root = tmp_path / "global_cache" / "agent_runtime"
    example_benchmark_root = (
        tmp_path
        / "artifacts"
        / "ai_trading_team_example_benchmarks"
        / "20260717_185016_461584"
        / "growth-execution-test"
        / "cache"
        / "agent_runtime"
    )
    provider_benchmark_root = (
        tmp_path
        / "artifacts"
        / "ai_trading_team_provider_benchmarks"
        / "20260731_154745_169491_15832"
        / "openai_gpt-5.4-mini"
        / "cache"
        / "agent_runtime"
    )
    (local_root / "traces").mkdir(parents=True)
    (global_root / "traces").mkdir(parents=True)
    (example_benchmark_root / "traces").mkdir(parents=True)
    (provider_benchmark_root / "traces").mkdir(parents=True)
    monkeypatch.setattr(agent_trace_ui, "LUMIBOT_CACHE_FOLDER", str(tmp_path / "global_cache"))

    roots = agent_trace_ui.default_trace_roots()

    assert roots == [local_root, global_root, example_benchmark_root, provider_benchmark_root]


def test_default_trace_roots_honor_explicit_env_override(tmp_path, monkeypatch):
    explicit = tmp_path / "custom" / "agent_runtime"
    monkeypatch.setenv("LUMIBOT_AGENT_TRACE_ROOT", str(explicit))

    assert agent_trace_ui.default_trace_roots() == [explicit]
