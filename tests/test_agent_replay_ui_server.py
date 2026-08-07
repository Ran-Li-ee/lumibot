import gzip
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


def test_boundary_sidecar_endpoint_returns_known_gzip_payload_with_redaction(tmp_path):
    root = tmp_path / "agent_runtime"
    sidecar_path = root / "boundary_payloads" / "payload.json.gz"
    sidecar_path.parent.mkdir(parents=True)
    sidecar_path.write_bytes(
        gzip.compress(json.dumps({"full": "payload", "api_key": "sk-secret"}).encode("utf-8"))
    )
    trace_path = root / "traces" / "agent" / "trace.json"
    _write_trace(
        trace_path,
        {
            "agent": "agent",
            "model": "openai/test",
            "request": {"context": {}, "runtime_context": {}},
            "events": [],
            "boundary_trace": {
                "schema_version": 1,
                "events": [
                    {
                        "transition": "B09_ADK_TO_LITELLM",
                        "model_turn_id": "turn-1",
                        "payload": {"preview": "small"},
                        "payload_meta": {
                            "sidecar_path": "boundary_payloads/payload.json.gz",
                            "compression": "gzip",
                        },
                    }
                ],
            },
        },
    )
    client = create_app(root).test_client()
    dataset = client.get("/api/dataset").get_json()
    event_id = dataset["runs"][0]["system_runs"][0]["agents"][0]["boundary_trace"]["events"][0]["id"]

    response = client.get(f"/api/boundary-payload/{event_id}")

    assert response.status_code == 200
    assert response.get_json() == {
        "event_id": event_id,
        "payload": {"full": "payload", "api_key": "[REDACTED]"},
    }


def test_boundary_sidecar_endpoint_rejects_unknown_event(tmp_path):
    client = create_app(tmp_path / "agent_runtime").test_client()

    response = client.get("/api/boundary-payload/not-real")

    assert response.status_code == 404


def test_boundary_sidecar_endpoint_rejects_path_traversal_sidecar(tmp_path):
    root = tmp_path / "agent_runtime"
    trace_path = root / "traces" / "agent" / "trace.json"
    _write_trace(
        trace_path,
        {
            "agent": "agent",
            "model": "openai/test",
            "request": {"context": {}, "runtime_context": {}},
            "events": [],
            "boundary_trace": {
                "schema_version": 1,
                "events": [
                    {
                        "transition": "B09_ADK_TO_LITELLM",
                        "model_turn_id": "turn-1",
                        "payload_meta": {"sidecar_path": "../secret.json"},
                    }
                ],
            },
        },
    )
    client = create_app(root).test_client()
    dataset = client.get("/api/dataset").get_json()
    event_id = dataset["runs"][0]["system_runs"][0]["agents"][0]["boundary_trace"]["events"][0]["id"]

    response = client.get(f"/api/boundary-payload/{event_id}")

    assert response.status_code == 404


def test_boundary_sidecar_endpoint_rejects_absolute_sidecar_path(tmp_path):
    root = tmp_path / "agent_runtime"
    secret_path = tmp_path / "secret.json"
    secret_path.write_text('{"leaked": true}', encoding="utf-8")
    trace_path = root / "traces" / "agent" / "trace.json"
    _write_trace(
        trace_path,
        {
            "agent": "agent",
            "model": "openai/test",
            "request": {"context": {}, "runtime_context": {}},
            "events": [],
            "boundary_trace": {
                "schema_version": 1,
                "events": [
                    {
                        "transition": "B09_ADK_TO_LITELLM",
                        "model_turn_id": "turn-1",
                        "payload_meta": {"sidecar_path": str(secret_path)},
                    }
                ],
            },
        },
    )
    client = create_app(root).test_client()
    dataset = client.get("/api/dataset").get_json()
    event_id = dataset["runs"][0]["system_runs"][0]["agents"][0]["boundary_trace"]["events"][0]["id"]

    response = client.get(f"/api/boundary-payload/{event_id}")

    assert response.status_code == 404


def test_boundary_sidecar_endpoint_handles_missing_sidecar_gracefully(tmp_path):
    root = tmp_path / "agent_runtime"
    trace_path = root / "traces" / "agent" / "trace.json"
    _write_trace(
        trace_path,
        {
            "agent": "agent",
            "model": "openai/test",
            "request": {"context": {}, "runtime_context": {}},
            "events": [],
            "boundary_trace": {
                "schema_version": 1,
                "events": [
                    {
                        "transition": "B09_ADK_TO_LITELLM",
                        "model_turn_id": "turn-1",
                        "payload_meta": {"sidecar_path": "boundary_payloads/missing.json.gz"},
                    }
                ],
            },
        },
    )
    client = create_app(root).test_client()
    dataset = client.get("/api/dataset").get_json()
    event_id = dataset["runs"][0]["system_runs"][0]["agents"][0]["boundary_trace"]["events"][0]["id"]

    response = client.get(f"/api/boundary-payload/{event_id}")

    assert response.status_code == 404


def test_boundary_sidecar_endpoint_keeps_large_sidecar_out_of_initial_dataset(tmp_path):
    root = tmp_path / "agent_runtime"
    sidecar_path = root / "boundary_payloads" / "payload.json.gz"
    sidecar_path.parent.mkdir(parents=True)
    sidecar_path.write_bytes(gzip.compress(json.dumps({"full": "x" * 10000}).encode("utf-8")))
    trace_path = root / "traces" / "agent" / "trace.json"
    _write_trace(
        trace_path,
        {
            "agent": "agent",
            "model": "openai/test",
            "request": {"context": {}, "runtime_context": {}},
            "events": [],
            "boundary_trace": {
                "schema_version": 1,
                "events": [
                    {
                        "transition": "B09_ADK_TO_LITELLM",
                        "model_turn_id": "turn-1",
                        "payload": {"preview": "small"},
                        "payload_meta": {
                            "sidecar_path": "boundary_payloads/payload.json.gz",
                            "compression": "gzip",
                        },
                    }
                ],
            },
        },
    )
    client = create_app(root).test_client()

    response_text = client.get("/api/dataset").get_data(as_text=True)

    assert '"preview":"small"' in response_text
    assert "boundary_payloads/payload.json.gz" not in response_text
    assert "xxxxxxxxxxxxxxxx" not in response_text
