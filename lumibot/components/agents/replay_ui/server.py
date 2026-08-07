"""Read-only local Flask server for agent replay datasets."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from flask import Flask, Response, abort, jsonify

from .loader import (
    artifact_token_for_root,
    backtest_artifact_root,
    boundary_sidecar_index_for_roots,
    build_replay_dataset_from_roots,
    find_account_curve_report,
    find_performance_report,
    load_boundary_sidecar_payload,
)
from .redaction import redact_public_preview

STATIC_ALLOWLIST = {
    "app.js",
    "styles.css",
    "vendor/elk.bundled.js",
}


def create_app(trace_root: str | Path | list[str | Path]) -> Flask:
    """Create a read-only replay UI Flask application."""

    app = Flask(__name__, static_folder=None)
    trace_roots = trace_root if isinstance(trace_root, list) else [trace_root]
    app.config["TRACE_ROOTS"] = [Path(root) for root in trace_roots]
    app.config["ARTIFACT_ROOTS_BY_TOKEN"] = _artifact_roots_by_token(app.config["TRACE_ROOTS"])
    app.config["BOUNDARY_SIDECARS_BY_EVENT_ID"] = boundary_sidecar_index_for_roots(app.config["TRACE_ROOTS"])

    @app.get("/api/dataset")
    def dataset() -> Response:
        replay_dataset = build_replay_dataset_from_roots(app.config["TRACE_ROOTS"])
        return jsonify(replay_dataset.to_public_dict())

    @app.get("/api/boundary-payload/<event_id>")
    def boundary_payload(event_id: str) -> Response:
        index = app.config.get("BOUNDARY_SIDECARS_BY_EVENT_ID") or {}
        entry = index.get(event_id)
        if entry is None:
            abort(404)
        try:
            payload = load_boundary_sidecar_payload(entry)
        except Exception:
            abort(404)
        return jsonify({"event_id": event_id, "payload": redact_public_preview(payload)})

    @app.get("/healthz")
    def healthz() -> Response:
        return jsonify({"ok": True})

    @app.get("/")
    def index() -> Response:
        return _package_static_response("index.html", "text/html; charset=utf-8")

    @app.get("/static/<path:filename>")
    def static_file(filename: str) -> Response:
        if filename not in STATIC_ALLOWLIST:
            abort(404)

        mimetype = "text/css; charset=utf-8" if filename == "styles.css" else "text/javascript; charset=utf-8"
        return _package_static_response(filename, mimetype)

    @app.get("/artifacts/<token>/account-curve")
    def account_curve(token: str) -> Response:
        artifact_root = _artifact_root_for_token(app, token)
        account_curve_path = find_account_curve_report(artifact_root)
        if account_curve_path is None:
            abort(404)
        return Response(account_curve_path.read_bytes(), mimetype="text/html; charset=utf-8")

    @app.get("/artifacts/<token>/performance-report")
    def performance_report(token: str) -> Response:
        artifact_root = _artifact_root_for_token(app, token)
        report_path = find_performance_report(artifact_root)
        if report_path is None:
            abort(404)
        return Response(report_path.read_bytes(), mimetype="text/html; charset=utf-8")

    return app


def _package_static_response(filename: str, mimetype: str) -> Response:
    static_file = resources.files(__package__).joinpath("static", filename)
    if not static_file.is_file():
        abort(404)

    return Response(static_file.read_bytes(), mimetype=mimetype)


def _artifact_roots_by_token(trace_roots: list[Path]) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for trace_root in trace_roots:
        artifact_root = backtest_artifact_root(trace_root).resolve()
        roots[artifact_token_for_root(artifact_root)] = artifact_root
    return roots


def _artifact_root_for_token(app: Flask, token: str) -> Path:
    roots = app.config.get("ARTIFACT_ROOTS_BY_TOKEN") or {}
    artifact_root = roots.get(token)
    if artifact_root is None:
        abort(404)
    return artifact_root
