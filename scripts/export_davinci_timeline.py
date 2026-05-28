#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType


SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SERVER = SKILL_DIR / "assets" / "review_tool" / "interactive_review_server.py"
DEFAULT_MANIFEST = Path.cwd() / "interactive_review_manifest.json"


def load_server(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("interactive_review_server", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load server module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description="Export DaVinci handoff files from review state.")
    parser.add_argument("--server", default=str(DEFAULT_SERVER), help="Path to interactive_review_server.py")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Path to review manifest JSON")
    parser.add_argument("--project", help="Manifest project id; defaults to manifest defaultProject")
    parser.add_argument("--format", choices=["fcpxml"], default="fcpxml", help="Timeline handoff format")
    parser.add_argument("--render", action="store_true", help="Also render preview media via the server export path")
    args = parser.parse_args()

    server = Path(args.server).expanduser().resolve()
    manifest = Path(args.manifest).expanduser().resolve()
    if not server.exists():
        raise SystemExit(f"server not found: {server}")
    if not manifest.exists():
        raise SystemExit(f"manifest not found: {manifest}")

    module = load_server(server)
    module.load_projects(manifest)
    project = module.get_project(args.project)
    state = module.load_state(project)
    result = module.export_state(project, state, render=args.render)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
