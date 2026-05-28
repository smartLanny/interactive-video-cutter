#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from preprocess_chinese import lines_from_transcript_data, preprocess_lines


DEFAULT_PROJECT_ROOT = Path.cwd()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def write_text_if_missing(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(text)


def srt_time_to_seconds(value: str) -> float:
    hms, ms = value.strip().split(",", 1)
    h, m, s = [int(part) for part in hms.split(":")]
    return h * 3600 + m * 60 + s + int(ms) / 1000


def parse_srt(path: Path) -> list[dict[str, Any]]:
    blocks = re.split(r"\n\s*\n", path.read_text().strip())
    cues: list[dict[str, Any]] = []
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        start_raw, end_raw = [part.strip() for part in lines[1].split("-->", 1)]
        cues.append({
            "id": len(cues) + 1,
            "index": len(cues) + 1,
            "start": srt_time_to_seconds(start_raw),
            "end": srt_time_to_seconds(end_raw),
            "text": " ".join(line.strip() for line in lines[2:] if line.strip()),
        })
    return cues


def split_reference_text(path: Path) -> list[dict[str, Any]]:
    raw_lines = [line.strip() for line in path.read_text().splitlines()]
    text_lines = [line for line in raw_lines if line and not line.startswith("#")]
    lines: list[dict[str, Any]] = []
    cursor = 0.0
    for item in text_lines:
        duration = max(1.2, min(8.0, len(item) / 7.0))
        lines.append({
            "id": len(lines) + 1,
            "index": len(lines) + 1,
            "start": round(cursor, 3),
            "end": round(cursor + duration, 3),
            "text": item,
            "deleted": False,
            "source": "reference-text",
        })
        cursor += duration
    return lines


def lines_from_transcript_json(path: Path, reference_path: Path | None = None) -> list[dict[str, Any]]:
    data = read_json(path)
    lines = lines_from_transcript_data(data)
    return preprocess_lines(lines, reference_path)


def lines_from_alignment_json(path: Path) -> list[dict[str, Any]]:
    data = read_json(path)
    cues = data.get("cues") or []
    lines: list[dict[str, Any]] = []
    for cue in cues:
        text = str(cue.get("text") or "").strip()
        if not text:
            continue
        start = float(cue.get("start", 0.0))
        end = float(cue.get("end", start + max(1.0, len(text) / 8.0)))
        lines.append({
            "id": len(lines) + 1,
            "index": len(lines) + 1,
            "start": round(start, 3),
            "end": round(max(start + 0.05, end), 3),
            "text": text,
            "deleted": False,
            "source": "alignment-cues",
        })
    return lines


def load_delete_intervals(path: Path) -> list[tuple[float, float]]:
    if not path.exists():
        return []
    intervals: list[tuple[float, float]] = []
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                start = float(row.get("start") or row.get("source_start") or 0)
                end = float(row.get("end") or row.get("source_end") or start)
            except ValueError:
                continue
            if end > start + 0.02:
                intervals.append((start, end))
    return intervals


def apply_delete_intervals(lines: list[dict[str, Any]], intervals: list[tuple[float, float]]) -> None:
    if not intervals:
        return
    for line in lines:
        start = float(line.get("start", 0))
        end = float(line.get("end", start))
        for d_start, d_end in intervals:
            overlap = min(end, d_end) - max(start, d_start)
            if overlap > 0.05:
                line["deleted"] = True
                line["source"] = f"{line.get('source', '').strip()} delete-csv".strip()
                break


def probe_duration(media: Path) -> float:
    try:
        raw = subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(media),
        ], text=True).strip()
        return float(raw)
    except Exception:
        return 0.0


def infer_duration(lines: list[dict[str, Any]], fallback: float | None, media: Path) -> float:
    if fallback is not None:
        return fallback
    if lines:
        return max(float(line["end"]) for line in lines)
    return probe_duration(media)


def build_manifest(args: argparse.Namespace, duration: float) -> dict[str, Any]:
    project_root = Path(args.project_root).expanduser().resolve()
    state_out = Path(args.state_out).expanduser().resolve()
    export_dir = Path(args.export_dir).expanduser().resolve() if args.export_dir else project_root / "interactive_exports"
    source_media = Path(args.media).expanduser().resolve()
    manifest_out = Path(args.manifest_out).expanduser().resolve()
    allowed_roots = sorted({
        str(project_root),
        str(source_media.parent),
        str(state_out.parent),
        str(export_dir),
        str(manifest_out.parent),
    })
    project = {
        "id": args.project_id,
        "title": args.title or args.project_id,
        "mediaType": args.media_type,
        "duration": duration,
        "sourceMedia": str(source_media),
        "draftMedia": str(Path(args.draft_media).expanduser().resolve()) if args.draft_media else "",
        "draftSrt": str(Path(args.srt).expanduser().resolve()) if args.srt else str(project_root / "empty_draft.srt"),
        "deleteCsv": str(Path(args.delete_csv).expanduser().resolve()) if args.delete_csv else str(project_root / "empty_delete_intervals.csv"),
        "alignmentJson": str(Path(args.alignment_json).expanduser().resolve()) if args.alignment_json else str(project_root / "empty_alignment.json"),
        "transcriptJson": str(Path(args.transcript_json).expanduser().resolve()) if args.transcript_json else str(project_root / "empty_transcript.json"),
        "statePath": str(state_out),
        "exportDir": str(export_dir),
        "sourceLabel": args.source_label or args.title or args.project_id,
        "sourceTimecodeStart": args.source_timecode_start,
        "davinciMediaPath": args.davinci_media_path or str(source_media),
        "handoffNotes": args.handoff_notes,
    }
    if args.source_fps:
        project["sourceFps"] = args.source_fps
    return {
        "defaultProject": args.project_id,
        "allowedRoots": allowed_roots,
        "projects": [project],
    }


def ensure_placeholder_inputs(args: argparse.Namespace) -> None:
    project_root = Path(args.project_root).expanduser().resolve()
    if not args.srt:
        write_text_if_missing(project_root / "empty_draft.srt", "")
    if not args.delete_csv:
        write_text_if_missing(project_root / "empty_delete_intervals.csv", "start,end,duration,summary,reason,source\n")
    if not args.alignment_json:
        write_text_if_missing(project_root / "empty_alignment.json", '{"segments": [], "cues": []}\n')
    if not args.transcript_json:
        write_text_if_missing(project_root / "empty_transcript.json", '{"text": "", "words": [], "segments": []}\n')


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a conservative review manifest/state from preprocessed transcript artifacts.")
    parser.add_argument("--project-root", default=str(DEFAULT_PROJECT_ROOT))
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--title", default="")
    parser.add_argument("--media", "--source-media", dest="media", required=True)
    parser.add_argument("--media-type", choices=["audio", "video"], default="audio")
    parser.add_argument("--duration", type=float)
    parser.add_argument("--manifest-out", required=True)
    parser.add_argument("--state-out", required=True)
    parser.add_argument("--export-dir")
    parser.add_argument("--draft-media")
    parser.add_argument("--srt")
    parser.add_argument("--delete-csv")
    parser.add_argument("--alignment-json")
    parser.add_argument("--transcript-json")
    parser.add_argument("--reference-text")
    parser.add_argument("--source-label", default="")
    parser.add_argument("--source-fps", type=float)
    parser.add_argument("--source-timecode-start", default="00:00:00:00")
    parser.add_argument("--davinci-media-path", default="")
    parser.add_argument("--handoff-notes", default="")
    parser.add_argument("--no-state", action="store_true", help="Only write manifest")
    args = parser.parse_args()

    lines: list[dict[str, Any]] = []
    if args.alignment_json:
        lines = lines_from_alignment_json(Path(args.alignment_json).expanduser().resolve())
    if not lines and args.srt:
        lines = [
            {**cue, "deleted": False, "source": "srt"}
            for cue in parse_srt(Path(args.srt).expanduser().resolve())
        ]
    if not lines and args.transcript_json:
        reference_path = Path(args.reference_text).expanduser().resolve() if args.reference_text else None
        lines = lines_from_transcript_json(Path(args.transcript_json).expanduser().resolve(), reference_path)
    if not lines and args.reference_text:
        lines = split_reference_text(Path(args.reference_text).expanduser().resolve())
    if args.delete_csv:
        apply_delete_intervals(lines, load_delete_intervals(Path(args.delete_csv).expanduser().resolve()))

    duration = infer_duration(lines, args.duration, Path(args.media).expanduser().resolve())
    ensure_placeholder_inputs(args)
    manifest = build_manifest(args, duration)
    write_json(Path(args.manifest_out).expanduser().resolve(), manifest)

    if not args.no_state:
        state = {
            "selectedDeletes": {},
            "deleteNotes": {},
            "cues": [
                {key: line[key] for key in ("id", "index", "start", "end", "text")}
                for line in lines
                if not line.get("deleted")
            ],
            "scriptLines": lines,
            "useScriptLines": True,
            "updatedAt": None,
        }
        write_json(Path(args.state_out).expanduser().resolve(), state)

    print(json.dumps({
        "manifest": str(Path(args.manifest_out).expanduser().resolve()),
        "state": None if args.no_state else str(Path(args.state_out).expanduser().resolve()),
        "scriptLines": len(lines),
        "duration": duration,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
