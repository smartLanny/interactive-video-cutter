#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SERVER = SKILL_DIR / "assets" / "review_tool" / "interactive_review_server.py"
DEFAULT_MANIFEST = Path.cwd() / "interactive_review_manifest.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the local interactive review server.")
    parser.add_argument("--server", default=str(DEFAULT_SERVER), help="Path to interactive_review_server.py")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Path to review manifest JSON")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--python", default=sys.executable or "python3", help="Python executable")
    args = parser.parse_args()

    server = Path(args.server).expanduser().resolve()
    manifest = Path(args.manifest).expanduser().resolve()
    if not server.exists():
        raise SystemExit(f"server not found: {server}")
    if not manifest.exists():
        raise SystemExit(f"manifest not found: {manifest}")

    print(f"Review UI: http://{args.host}:{args.port}")
    print(f"Manifest: {manifest}")
    return subprocess.call([
        args.python,
        str(server),
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--manifest",
        str(manifest),
    ])


if __name__ == "__main__":
    raise SystemExit(main())
