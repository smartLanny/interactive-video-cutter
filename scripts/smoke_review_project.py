#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(cmd), file=sys.stderr)
    return subprocess.run(cmd, cwd=cwd, check=True, text=True, capture_output=True)


def require_command(name: str) -> None:
    if not shutil.which(name):
        raise SystemExit(f"missing required command: {name}")


def write_inputs(workdir: Path) -> dict[str, Path]:
    media = workdir / "test.mp4"
    reference = workdir / "reference.txt"
    transcript = workdir / "transcript.json"
    delete_csv = workdir / "delete.csv"

    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=30:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=44100:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(media),
        ],
        check=True,
    )
    reference.write_text("第一句保留。\n第二句删除。\n")
    transcript.write_text(
        json.dumps(
            {
                "segments": [
                    {"start": 0.0, "end": 0.9, "text": "第一句保留。"},
                    {"start": 1.0, "end": 1.8, "text": "第二句删除。"},
                ]
            },
            ensure_ascii=False,
        )
    )
    delete_csv.write_text(
        "start,end,duration,summary,reason,source\n"
        "1.0,1.8,0.8,第二句删除,smoke,smoke\n"
    )
    return {
        "media": media,
        "reference": reference,
        "transcript": transcript,
        "delete_csv": delete_csv,
    }


def parse_json_output(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    text = (proc.stdout or "").strip()
    start = text.find("{")
    if start < 0:
        raise RuntimeError(f"expected JSON output, got: {text}")
    return json.loads(text[start:])


def require_file(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(f"expected output missing: {path}")
    if path.is_file() and path.stat().st_size <= 0:
        raise RuntimeError(f"expected non-empty output: {path}")


def assert_outputs(export_dir: Path) -> list[str]:
    expected = [
        "review_state.json",
        "script_selected_timeline.srt",
        "script_original_timeline.srt",
        "script_selected_delete_edl.json",
        "script_delete_intervals.csv",
        "script_lines.csv",
        "davinci_timeline.fcpxml",
        "davinci_handoff.json",
        "davinci_handoff_readme.txt",
        "davinci_keep_segments.csv",
        "script_selected_text_time_alignment.json",
        "script_selected_text_time_alignment.units.jsonl",
        "script_selected_text_time_alignment.units.csv",
        "script_original_text_time_alignment.json",
        "script_original_text_time_alignment.units.jsonl",
        "script_original_text_time_alignment.units.csv",
        "selected_delete_preview.mp4",
    ]
    for name in expected:
        require_file(export_dir / name)

    selected_srt = (export_dir / "script_selected_timeline.srt").read_text()
    if "第一句保留" not in selected_srt or "第二句删除" in selected_srt:
        raise RuntimeError("selected timeline SRT did not reflect the delete interval")
    return expected


def smoke(root: Path, keep_temp: bool) -> dict[str, Any]:
    require_command("ffmpeg")
    require_command("ffprobe")
    if keep_temp:
        tmp_root = Path(tempfile.mkdtemp(prefix="interactive-video-cutter-smoke."))
        cleanup = False
    else:
        tmp_ctx = tempfile.TemporaryDirectory(prefix="interactive-video-cutter-smoke.")
        tmp_root = Path(tmp_ctx.name)
        cleanup = True

    try:
        inputs = write_inputs(tmp_root)
        review_dir = tmp_root / "review"
        create_proc = run(
            [
                sys.executable,
                str(root / "scripts" / "create_review_project.py"),
                "--media",
                str(inputs["media"]),
                "--reference",
                str(inputs["reference"]),
                "--workdir",
                str(review_dir),
                "--project-id",
                "smoke-video",
                "--title",
                "SmokeVideo",
                "--transcript-json",
                str(inputs["transcript"]),
                "--delete-csv",
                str(inputs["delete_csv"]),
                "--skip-transcribe",
            ]
        )
        create_result = parse_json_output(create_proc)
        manifest = Path(create_result["manifest"])
        takes_packed = Path(create_result["takesPacked"])
        audio_proxy = Path(create_result["audioProxy"])
        require_file(audio_proxy)
        manifest_data = json.loads(manifest.read_text())
        project = manifest_data["projects"][0]
        if project.get("sourceMedia") != str(inputs["media"].resolve()):
            raise RuntimeError("manifest sourceMedia did not point to the original video")
        if project.get("draftMedia") != str(audio_proxy):
            raise RuntimeError("manifest draftMedia did not point to the audio proxy")
        if not audio_proxy.name.endswith("_asr.m4a"):
            raise RuntimeError("audio proxy did not use the expected m4a path")
        require_file(takes_packed)
        packed_text = takes_packed.read_text()
        if "第一句保留" not in packed_text or "第二句删除" not in packed_text:
            raise RuntimeError("packed transcript did not include expected transcript phrases")

        export_proc = run(
            [
                sys.executable,
                str(root / "scripts" / "export_davinci_timeline.py"),
                "--manifest",
                str(manifest),
                "--project",
                "smoke-video",
                "--render",
            ]
        )
        export_result = parse_json_output(export_proc)
        export_dir = review_dir / "interactive_exports"
        outputs = assert_outputs(export_dir)
        summary = {
            "ok": True,
            "tempDir": str(tmp_root),
            "manifest": str(manifest),
            "state": create_result.get("state"),
            "audioProxy": str(audio_proxy),
            "takesPacked": str(takes_packed),
            "exportDir": str(export_dir),
            "estimatedDuration": export_result.get("estimatedDuration"),
            "checkedOutputs": outputs,
        }
        if cleanup:
            summary["tempDir"] = None
        return summary
    finally:
        if cleanup:
            tmp_ctx.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a minimal local review-project smoke test.")
    parser.add_argument("--keep-temp", action="store_true", help="Keep the generated temp project for debugging")
    args = parser.parse_args()
    print(json.dumps(smoke(ROOT, args.keep_temp), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
