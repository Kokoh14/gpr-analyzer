"""Local web app: upload raw files, review the automatic zones, export the report.

    gpr-analyzer-app            # or: python -m gpr_analyzer.app
    gpr-analyzer-app --port 8765 --workdir ~/gpr-jobs --no-browser

It runs a small server on 127.0.0.1 and opens the page in the default browser, so it works
like a desktop program while the screen is plain HTML. Needs the [app] extra.
"""

from __future__ import annotations

import argparse
import threading
import webbrowser
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gpr-analyzer-app", description="GPR upload and review screen (local)")
    p.add_argument("--host", default="127.0.0.1", help="default 127.0.0.1 (this computer only)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--workdir", type=Path, default=Path.home() / "gpr-analyzer-jobs",
                   help="where uploads, survey.json, labels.jsonl and exports are kept")
    p.add_argument("--no-browser", action="store_true", help="do not open the browser")
    args = p.parse_args(argv)
    try:
        import uvicorn

        from gpr_analyzer.app.server import create_app
    except ImportError as e:
        raise SystemExit(f"gpr-analyzer-app: missing dependency ({e.name}); install with: pip install -e .[app]")
    app = create_app(args.workdir.expanduser())
    url = f"http://{args.host}:{args.port}/"
    print(f"GPR Analyzer: {url}  (jobs in {args.workdir})  Ctrl+C to stop")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, (url,)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0
