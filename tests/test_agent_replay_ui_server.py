import json

from lumibot.components.agents.replay_ui.server import create_app


def _write_trace(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_minimal_trace(runtime_root):
    _write_trace(
        runtime_root / "traces" / "growth_agent" / "trace.json",
        {
            "agent": "growth_agent",
            "request": {
                "runtime_context": {
                    "mode": "backtesting",
                    "current_datetime": "2026-04-07T09:30:00-04:00",
                    "strategy_name": "DemoStrategy",
                },
            },
            "summary": "RESULT: done.",
        },
    )


def test_server_serves_existing_native_account_curve_page(tmp_path):
    backtest_root = tmp_path / "benchmarks" / "demo-run" / "growth-execution-test"
    runtime_root = backtest_root / "cache" / "agent_runtime"
    _write_minimal_trace(runtime_root)
    (backtest_root / "growth_execution_test_account_curve.html").write_text(
        "<html><body>Native Account Curve</body></html>",
        encoding="utf-8",
    )

    client = create_app(runtime_root).test_client()
    dataset = client.get("/api/dataset").get_json()
    url = dataset["runs"][0]["artifacts"]["account_curve"]["url"]

    response = client.get(url)

    assert response.status_code == 200
    assert "Native Account Curve" in response.get_data(as_text=True)


def test_server_serves_existing_performance_report_page(tmp_path):
    backtest_root = tmp_path / "benchmarks" / "demo-run" / "growth-execution-test"
    runtime_root = backtest_root / "cache" / "agent_runtime"
    _write_minimal_trace(runtime_root)
    (backtest_root / "stats.csv").write_text("datetime,portfolio_value\n2026-04-07,100000\n", encoding="utf-8")
    (backtest_root / "DemoStrategy_tearsheet.html").write_text(
        "<html><body>Known Report</body></html>",
        encoding="utf-8",
    )

    client = create_app(runtime_root).test_client()
    dataset = client.get("/api/dataset").get_json()
    url = dataset["runs"][0]["artifacts"]["performance_report"]["url"]

    response = client.get(url)

    assert response.status_code == 200
    assert "Known Report" in response.get_data(as_text=True)


def test_server_rejects_unknown_artifact_tokens(tmp_path):
    runtime_root = tmp_path / "cache" / "agent_runtime"
    _write_minimal_trace(runtime_root)

    client = create_app(runtime_root).test_client()

    assert client.get("/artifacts/not-a-real-token/account-curve").status_code == 404
