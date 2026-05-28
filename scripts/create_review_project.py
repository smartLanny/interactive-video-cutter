#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
BOOTSTRAP = Path(__file__).resolve().parent / "bootstrap.py"
IMPORT_PROJECT = Path(__file__).resolve().parent / "import_review_project.py"
AUDIO_EXTS = {".aac", ".aiff", ".flac", ".m4a", ".mp3", ".ogg", ".wav", ".wma"}
VIDEO_EXTS = {".m4v", ".mkv", ".mov", ".mp4", ".webm"}


def slugify(value: str) -> str:
    value = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "-", value.strip())
    value = value.strip("-._")
    return value or "review-project"


def read_bootstrap_status() -> dict:
    proc = subprocess.run([sys.executable, str(BOOTSTRAP), "--json"], text=True, capture_output=True)
    raw = proc.stdout or proc.stderr
    start = raw.find("{")
    if start < 0:
        raise SystemExit(raw.strip() or "bootstrap status failed")
    return json.loads(raw[start:])


def ensure_bootstrap(install: bool) -> dict:
    if install:
        subprocess.run([sys.executable, str(BOOTSTRAP), "--install"], check=True)
    return read_bootstrap_status()


def media_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in VIDEO_EXTS:
        return "video"
    if suffix in AUDIO_EXTS:
        return "audio"
    return "video" if suffix else "audio"


def transcribe(media: Path, workdir: Path, status: dict, language: str, backend: str, model: str) -> Path:
    helper = status.get("transcription", {}).get("transcribeHelper")
    python = status.get("pythonRuntime", {}).get("venvPython") or sys.executable
    if not helper:
        raise SystemExit("No transcribe helper found. Run bootstrap.py --install first.")
    runtime = status.get("pythonRuntime", {})
    if backend == "mlx" and not runtime.get("mlx_qwen3_asr"):
        raise SystemExit("mlx_qwen3_asr is missing. Run bootstrap.py --install or provide --transcript-json.")
    if backend == "official" and not runtime.get("qwen_asr"):
        raise SystemExit("qwen_asr is missing. Run bootstrap.py --install or provide --transcript-json.")
    edit_dir = workdir / "edit"
    cmd = [
        python,
        helper,
        str(media),
        "--edit-dir",
        str(edit_dir),
        "--language",
        language,
        "--backend",
        backend,
    ]
    if model:
        cmd.extend(["--model", model])
    print("+", " ".join(cmd), file=sys.stderr)
    subprocess.run(cmd, check=True, stdout=sys.stderr, stderr=sys.stderr)
    transcript = edit_dir / "transcripts" / f"{media.stem}.json"
    if not transcript.exists():
        raise SystemExit(f"transcript was not created: {transcript}")
    return transcript


def write_reference_copy(reference: Path, workdir: Path) -> Path:
    dest = workdir / "reference_script.txt"
    dest.write_text(reference.read_text())
    return dest


def write_agent_brief(workdir: Path, media: Path, reference: Path, transcript: Path, state: Path) -> None:
    brief = f"""# Preprocessing Brief

This project was created automatically from media + reference script.

The import step has already generated timed review lines from `{transcript}`, then normalized visible text with `{reference}`:

- Audio/ASR is the source of truth.
- Use the reference script only to correct product names, model names, numbers, technical terms, punctuation, and semantic segmentation.
- Do not force unspoken reference text into the transcript.
- Conservative deletion lines may already be marked for repeated speech, false starts, long gaps, or clearly discarded takes.
- Keep `scriptLines[].start/end` on original media time.
- Before sharing the page, inspect `{state}` once for obvious bad ASR matches or missed protected terms.

Media: `{media}`
"""
    (workdir / "PREPROCESSING_BRIEF.md").write_text(brief)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an interactive review project from media plus a reference script.")
    parser.add_argument("--media", required=True, help="Audio or video file path accessible to the review machine")
    parser.add_argument("--reference", required=True, help="Clean text/markdown reference narration script")
    parser.add_argument("--workdir", required=True, help="Project working directory to create")
    parser.add_argument("--project-id", help="Stable project id; defaults to a slug from title/media")
    parser.add_argument("--title", default="", help="Human-readable project title")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--backend", default="mlx" if platform.system() == "Darwin" else "official")
    parser.add_argument("--model", default=os.environ.get("QWEN3_ASR_MODEL", "Qwen/Qwen3-ASR-1.7B"))
    parser.add_argument("--install-missing", action="store_true", help="Run bootstrap.py --install before transcription")
    parser.add_argument("--skip-transcribe", action="store_true", help="Only create project from existing --transcript-json")
    parser.add_argument("--transcript-json", help="Existing transcript JSON to use instead of running ASR")
    parser.add_argument("--alignment-json", help="Existing preprocessed alignment JSON")
    parser.add_argument("--delete-csv", help="Existing conservative delete interval CSV")
    parser.add_argument("--source-fps", type=float)
    parser.add_argument("--davinci-media-path", default="")
    args = parser.parse_args()

    media = Path(args.media).expanduser().resolve()
    reference = Path(args.reference).expanduser().resolve()
    workdir = Path(args.workdir).expanduser().resolve()
    if not media.exists():
        raise SystemExit(f"media not found: {media}")
    if not reference.exists():
        raise SystemExit(f"reference not found: {reference}")
    workdir.mkdir(parents=True, exist_ok=True)

    title = args.title or media.stem
    project_id = args.project_id or slugify(title)
    reference_copy = write_reference_copy(reference, workdir)

    if args.transcript_json:
        transcript = Path(args.transcript_json).expanduser().resolve()
    elif args.skip_transcribe:
        raise SystemExit("--skip-transcribe requires --transcript-json")
    else:
        status = ensure_bootstrap(args.install_missing)
        transcript = transcribe(media, workdir, status, args.language, args.backend, args.model)

    manifest = workdir / "interactive_review_manifest.json"
    state = workdir / "interactive_review_state.json"
    export_dir = workdir / "interactive_exports"
    cmd = [
        sys.executable,
        str(IMPORT_PROJECT),
        "--project-root",
        str(workdir),
        "--project-id",
        project_id,
        "--title",
        title,
        "--media",
        str(media),
        "--media-type",
        media_type(media),
        "--manifest-out",
        str(manifest),
        "--state-out",
        str(state),
        "--export-dir",
        str(export_dir),
        "--transcript-json",
        str(transcript),
        "--reference-text",
        str(reference_copy),
        "--davinci-media-path",
        args.davinci_media_path or str(media),
    ]
    if args.alignment_json:
        cmd.extend(["--alignment-json", str(Path(args.alignment_json).expanduser().resolve())])
    if args.delete_csv:
        cmd.extend(["--delete-csv", str(Path(args.delete_csv).expanduser().resolve())])
    if args.source_fps:
        cmd.extend(["--source-fps", str(args.source_fps)])
    print("+", " ".join(cmd), file=sys.stderr)
    proc = subprocess.run(cmd, check=True, text=True, capture_output=True)
    write_agent_brief(workdir, media, reference_copy, transcript, state)
    import_result = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else {}
    print(json.dumps({
        "projectId": project_id,
        "workdir": str(workdir),
        "manifest": str(manifest),
        "state": str(state),
        "transcript": str(transcript),
        "reference": str(reference_copy),
        "scriptLines": import_result.get("scriptLines"),
        "next": f"python3 {SKILL_DIR}/scripts/start_review_server.py --host 0.0.0.0 --manifest {manifest}",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
