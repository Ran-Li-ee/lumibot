"""Read-only local Flask server for agent replay datasets."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from flask import Flask, Response, abort, jsonify

from .loader import build_replay_dataset

STATIC_ALLOWLIST = {
    "app.js",
    "styles.css",
    "vendor/elk.bundled.js",
}


def create_app(trace_root: str | Path) -> Flask:
    """Create a read-only replay UI Flask application."""

    app = Flask(__name__, static_folder=None)
    app.config["TRACE_ROOT"] = Path(trace_root)

    @app.get("/api/dataset")
    def dataset() -> Response:
        replay_dataset = build_replay_dataset(app.config["TRACE_ROOT"])
        return jsonify(replay_dataset.to_public_dict())

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

    return app


def _package_static_response(filename: str, mimetype: str) -> Response:
    static_file = resources.files(__package__).joinpath("static", filename)
    if not static_file.is_file():
        abort(404)

    return Response(static_file.read_bytes(), mimetype=mimetype)
