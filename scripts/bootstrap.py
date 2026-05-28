#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(os.environ.get("INTERACTIVE_VIDEO_CUTTER_TOOLS_DIR", Path.home() / ".local/share/interactive-video-cutter"))
VENV_DIR = Path(os.environ.get("INTERACTIVE_VIDEO_CUTTER_VENV", TOOLS_DIR / ".venv"))
BUNDLED_TRANSCRIBE = SKILL_DIR / "scripts" / "transcribe_qwen3.py"
QWEN_ASR_CACHE_NAME = "models--Qwen--Qwen3-ASR-1.7B"
QWEN_ALIGNER_CACHE_NAME = "models--Qwen--Qwen3-ForcedAligner-0.6B"


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def python_bin() -> Path:
    return VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def pip_bin() -> Path:
    return VENV_DIR / ("Scripts/pip.exe" if os.name == "nt" else "bin/pip")


def command(name: str) -> str | None:
    return shutil.which(name)


def bootstrap_python() -> str:
    override = os.environ.get("INTERACTIVE_VIDEO_CUTTER_BOOTSTRAP_PYTHON")
    if override:
        return override
    for name in ("python3.12", "python3.11", "python3.10"):
        found = command(name)
        if found:
            return found
    return sys.executable


def python_version(python: str | Path) -> str:
    try:
        proc = subprocess.run([str(python), "--version"], text=True, capture_output=True, check=True)
        return (proc.stdout or proc.stderr).strip().replace("Python ", "")
    except Exception:
        return ""


def module_exists(name: str, python: Path | None = None) -> bool:
    if python and python.exists():
        code = f"import importlib.util; raise SystemExit(0 if importlib.util.find_spec({name!r}) else 1)"
        return subprocess.run([str(python), "-c", code]).returncode == 0
    return importlib.util.find_spec(name) is not None


def video_use_helper() -> Path | None:
    candidates = [
        BUNDLED_TRANSCRIBE,
    ]
    for item in candidates:
        if item and item.exists():
            return item
    return None


def hf_cache_roots() -> list[Path]:
    roots: list[Path] = []
    for value in [
        os.environ.get("HF_HOME"),
        os.environ.get("HUGGINGFACE_HUB_CACHE"),
        str(TOOLS_DIR / "huggingface"),
        str(Path.home() / ".cache/huggingface"),
        str(SKILL_DIR / "assets/models/huggingface"),
    ]:
        if value:
            roots.append(Path(value).expanduser())
    return roots


def find_hf_model_cache(cache_name: str) -> Path | None:
    for root in hf_cache_roots():
        candidates = [
            root / cache_name,
            root / "hub" / cache_name,
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
    return None


def system_install_ffmpeg() -> str:
    if command("ffmpeg") and command("ffprobe"):
        return "present"
    system = platform.system().lower()
    if system == "darwin" and command("brew"):
        run(["brew", "install", "ffmpeg"])
        return "installed via brew"
    if system == "linux":
        return "missing; install with apt/yum/pacman, e.g. sudo apt-get install -y ffmpeg"
    if system == "windows":
        return "missing; install ffmpeg and add it to PATH"
    return "missing; install ffmpeg and ffprobe"


def install_python_stack() -> None:
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    if not python_bin().exists():
        run([bootstrap_python(), "-m", "venv", str(VENV_DIR)])
    run([str(python_bin()), "-m", "pip", "install", "--upgrade", "pip"])
    run([str(pip_bin()), "install", "numpy", "pillow", "matplotlib", "librosa"])
    if platform.system() == "Darwin" and platform.machine().lower() in {"arm64", "aarch64"}:
        run([str(pip_bin()), "install", "mlx-qwen3-asr"])
    else:
        run([str(pip_bin()), "install", "qwen-asr"])


def status() -> dict:
    helper = video_use_helper()
    venv_python = python_bin() if python_bin().exists() else None
    active_python = venv_python or Path(sys.executable)
    return {
        "skillDir": str(SKILL_DIR),
        "toolsDir": str(TOOLS_DIR),
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
            "bootstrapPython": bootstrap_python(),
            "bootstrapPythonVersion": python_version(bootstrap_python()),
        },
        "commands": {
            "ffmpeg": command("ffmpeg"),
            "ffprobe": command("ffprobe"),
            "git": command("git"),
        },
        "pythonRuntime": {
            "venv": str(VENV_DIR),
            "venvPython": str(venv_python) if venv_python else None,
            "activePython": str(active_python),
            "activePythonVersion": python_version(active_python),
            "mlx_qwen3_asr": module_exists("mlx_qwen3_asr", active_python),
            "qwen_asr": module_exists("qwen_asr", active_python),
        },
        "transcription": {
            "dir": str(helper.parent) if helper else None,
            "transcribeHelper": str(helper) if helper else None,
            "bundled": bool(helper and helper == BUNDLED_TRANSCRIBE),
        },
        "reviewTool": {
            "server": str(SKILL_DIR / "assets/review_tool/interactive_review_server.py"),
            "app": str(SKILL_DIR / "assets/review_tool/interactive_review_app.html"),
        },
        "models": {
            "qwen3_asr": str(find_hf_model_cache(QWEN_ASR_CACHE_NAME) or ""),
            "qwen3_forced_aligner": str(find_hf_model_cache(QWEN_ALIGNER_CACHE_NAME) or ""),
            "cacheRoots": [str(root) for root in hf_cache_roots()],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check and optionally install dependencies for interactive-video-cutter.")
    parser.add_argument("--install", action="store_true", help="Best-effort install: ffmpeg via brew when available and Qwen3-ASR Python deps.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable status JSON.")
    args = parser.parse_args()

    if args.install:
        ffmpeg_state = system_install_ffmpeg()
        print(f"ffmpeg: {ffmpeg_state}")
        install_python_stack()

    report = status()
    asr_ready = bool(report["pythonRuntime"]["mlx_qwen3_asr"] or report["pythonRuntime"]["qwen_asr"])
    ok = bool(report["commands"]["ffmpeg"] and report["commands"]["ffprobe"] and report["transcription"]["transcribeHelper"] and asr_ready)
    report["ok"] = ok
    report["offlineReady"] = bool(ok and report["models"]["qwen3_asr"] and report["models"]["qwen3_forced_aligner"])
    missing: list[str] = []
    if not report["commands"]["ffmpeg"]:
        missing.append("ffmpeg")
    if not report["commands"]["ffprobe"]:
        missing.append("ffprobe")
    if not report["transcription"]["transcribeHelper"]:
        missing.append("bundled-transcribe-helper")
    if not asr_ready:
        missing.append("python-asr-package")
    if not report["models"]["qwen3_asr"]:
        missing.append("qwen3-asr-model-cache")
    if not report["models"]["qwen3_forced_aligner"]:
        missing.append("qwen3-forced-aligner-cache")
    report["missing"] = missing
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not ok:
            print("\nRun with --install, or install the missing items shown above.")
    return 0 if ok or args.install else 1


if __name__ == "__main__":
    raise SystemExit(main())
