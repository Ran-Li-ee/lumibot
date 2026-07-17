"""Run the local Lumibot agent trace replay UI server."""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if (REPO_ROOT / "lumibot").is_dir() and str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lumibot import LUMIBOT_CACHE_FOLDER  # noqa: E402
from lumibot.components.agents.replay_ui import create_app  # noqa: E402


def default_trace_root() -> Path:
    """Return the project-local agent runtime trace root."""

    explicit_root = os.environ.get("LUMIBOT_AGENT_TRACE_ROOT")
    if explicit_root:
        return Path(explicit_root)
    return REPO_ROOT / ".lumibot" / "agent_runtime"


def default_trace_roots() -> list[Path]:
    """Return all project-local trace roots the replay UI should show by default."""

    explicit_root = os.environ.get("LUMIBOT_AGENT_TRACE_ROOT")
    if explicit_root:
        return [Path(explicit_root)]

    roots = [REPO_ROOT / ".lumibot" / "agent_runtime"]
    benchmark_root = REPO_ROOT / "artifacts" / "ai_trading_team_example_benchmarks"
    roots.extend(sorted(benchmark_root.glob("*/*/cache/agent_runtime")))
    return roots


def global_trace_root() -> Path:
    """Return the legacy global Lumibot agent runtime trace root."""

    return Path(LUMIBOT_CACHE_FOLDER) / "agent_runtime"


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the local Lumibot agent trace replay UI.")
    parser.add_argument("--trace-root", type=Path, default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    trace_roots = [args.trace_root] if args.trace_root is not None else default_trace_roots()
    app = create_app(trace_roots)
    url = f"http://{args.host}:{args.port}/"

    print("Trace roots:")
    for trace_root in trace_roots:
        print(f"  - {trace_root}")
    default_root = default_trace_root()
    if args.trace_root is None and default_root not in trace_roots and global_trace_root().exists():
        print(f"Global trace root not used by default: {global_trace_root()}")
        print("Pass --trace-root explicitly to inspect global or older project traces.")
    print(f"URL: {url}")

    if not args.no_browser:
        webbrowser.open(url)

    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
