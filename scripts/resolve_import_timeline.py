#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType


DEFAULT_RESOLVE_SCRIPT = Path(
    "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules/DaVinciResolveScript.py"
)
DEFAULT_FUSION_SCRIPT = Path(
    "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
)


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON: {path}: {exc}") from exc


def require_path(path: Path, label: str, must_exist: bool = True) -> Path:
    resolved = path.expanduser().resolve()
    if must_exist and not resolved.exists():
        raise SystemExit(f"{label} not found: {resolved}")
    return resolved


def load_resolve_module(path: Path, fusion_script: Path) -> ModuleType:
    os.environ.setdefault("RESOLVE_SCRIPT_API", str(path.parent.parent))
    os.environ.setdefault("RESOLVE_SCRIPT_LIB", str(fusion_script))
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("DaVinciResolveScript", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load Resolve scripting module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def handoff_paths(handoff: dict, base: Path) -> dict[str, Path]:
    files = handoff.get("files") if isinstance(handoff.get("files"), dict) else {}
    paths: dict[str, Path] = {}
    for key, value in files.items():
        path = Path(str(value)).expanduser()
        if not path.is_absolute():
            path = base / path
        paths[key] = path.resolve()
    return paths


def print_plan(handoff_path: Path, handoff: dict, paths: dict[str, Path], resolve_script: Path, fusion_script: Path) -> None:
    project = handoff.get("project") if isinstance(handoff.get("project"), dict) else {}
    timeline = handoff.get("timeline") if isinstance(handoff.get("timeline"), dict) else {}
    print(json.dumps({
        "dryRun": True,
        "handoff": str(handoff_path),
        "project": {
            "id": project.get("id"),
            "title": project.get("title"),
            "sourceMedia": project.get("sourceMedia"),
            "davinciMediaPath": project.get("davinciMediaPath"),
            "fps": project.get("fps"),
        },
        "timeline": {
            "mode": timeline.get("mode"),
            "estimatedDuration": timeline.get("estimatedDuration"),
        },
        "resolve": {
            "scriptModule": str(resolve_script),
            "fusionScript": str(fusion_script),
        },
        "files": {key: str(value) for key, value in paths.items()},
        "missingFiles": {key: str(value) for key, value in paths.items() if not value.exists()},
        "nextStep": "Run with --execute only when you want to connect to the active Resolve scripting API.",
    }, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run DaVinci Resolve import plan from interactive-video-cutter handoff JSON."
    )
    parser.add_argument("--handoff", required=True, help="Path to davinci_handoff.json")
    parser.add_argument("--resolve-script", default=str(DEFAULT_RESOLVE_SCRIPT), help="Path to DaVinciResolveScript.py")
    parser.add_argument("--fusion-script", default=str(DEFAULT_FUSION_SCRIPT), help="Path to fusionscript.so")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Connect to the active Resolve scripting API. Timeline mutation is intentionally not implemented yet.",
    )
    args = parser.parse_args()

    handoff_path = require_path(Path(args.handoff), "handoff")
    resolve_script = require_path(Path(args.resolve_script), "Resolve script module", must_exist=False)
    fusion_script = require_path(Path(args.fusion_script), "Fusion script library", must_exist=False)
    handoff = load_json(handoff_path)
    paths = handoff_paths(handoff, handoff_path.parent)
    print_plan(handoff_path, handoff, paths, resolve_script, fusion_script)

    if not args.execute:
        return 0

    require_path(resolve_script, "Resolve script module")
    require_path(fusion_script, "Fusion script library")
    module = load_resolve_module(resolve_script, fusion_script)
    resolve = module.scriptapp("Resolve")
    if resolve is None:
        raise SystemExit("Resolve scripting API is unavailable. Open Resolve and enable scripting access first.")
    project_manager = resolve.GetProjectManager()
    current_project = project_manager.GetCurrentProject() if project_manager else None
    current_name = current_project.GetName() if current_project else None
    print(json.dumps({
        "dryRun": False,
        "connected": True,
        "currentProject": current_name,
        "implemented": False,
        "message": "Resolve connection works; timeline creation/import is not implemented in this conservative scaffold.",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
