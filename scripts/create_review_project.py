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

from preprocess_chinese import build_semantic_review_packets, cleanup_text, reference_terms_for


SKILL_DIR = Path(__file__).resolve().parents[1]
BOOTSTRAP = Path(__file__).resolve().parent / "bootstrap.py"
IMPORT_PROJECT = Path(__file__).resolve().parent / "import_review_project.py"
ASR_PROFILE_MODELS = {
    "fast": "Qwen/Qwen3-ASR-0.6B",
    "quality": "Qwen/Qwen3-ASR-1.7B",
}
ASR_PROFILE_CHOICES = ("auto", "fast", "quality")
DEFAULT_ASR_PROFILE = os.environ.get("INTERACTIVE_VIDEO_CUTTER_ASR_PROFILE", "auto")
if DEFAULT_ASR_PROFILE not in ASR_PROFILE_CHOICES:
    DEFAULT_ASR_PROFILE = "auto"
AUDIO_EXTS = {".aac", ".aiff", ".flac", ".m4a", ".mp3", ".ogg", ".wav", ".wma"}
VIDEO_EXTS = {".m4v", ".mkv", ".mov", ".mp4", ".webm"}
PACKED_SILENCE_THRESHOLD = 0.5
PACKED_END_PUNCT = set("。！？!?；;")
AUDIO_PROXY_SUFFIX = "_asr.m4a"
AUDIO_PROXY_BITRATE = os.environ.get("INTERACTIVE_VIDEO_CUTTER_AUDIO_PROXY_BITRATE", "64k")
ASR_CONTEXT_MAX_CHARS = int(os.environ.get("INTERACTIVE_VIDEO_CUTTER_ASR_CONTEXT_CHARS", "2000"))
ASR_CONTEXT_MAX_TERMS = int(os.environ.get("INTERACTIVE_VIDEO_CUTTER_ASR_CONTEXT_TERMS", "180"))
ASR_CONTEXT_CJK_TERMS = [
    "索尼",
    "三星",
    "LG",
    "TCL",
    "海信",
    "小米",
    "回音壁",
    "彩监",
    "监视器",
    "MiniLED",
    "QD-miniLED",
    "WOLED",
    "OLED",
    "RGB-MiniLED",
    "TrueRGB",
    "背光",
    "色域",
    "色准",
    "白点偏移",
    "低蓝光",
    "Judd Offset",
    "EOTF",
    "BT.2020",
]


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


def audio_proxy_path(media: Path, workdir: Path) -> Path:
    return workdir / "edit" / "audio" / f"{media.stem}{AUDIO_PROXY_SUFFIX}"


def prepare_audio_proxy(media: Path, workdir: Path, refresh: bool = False) -> Path:
    proxy = audio_proxy_path(media, workdir)
    if proxy.exists() and proxy.stat().st_size > 0 and not refresh:
        print(f"cached audio proxy: {proxy}", file=sys.stderr)
        return proxy

    proxy.parent.mkdir(parents=True, exist_ok=True)
    tmp = proxy.with_name(f".{proxy.stem}.tmp{proxy.suffix}")
    if tmp.exists():
        tmp.unlink()
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(media),
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "aac",
        "-b:a",
        AUDIO_PROXY_BITRATE,
        "-movflags",
        "+faststart",
        str(tmp),
    ]
    print("+", " ".join(cmd), file=sys.stderr)
    subprocess.run(cmd, check=True)
    tmp.replace(proxy)
    print(f"saved audio proxy: {proxy}", file=sys.stderr)
    return proxy


def probe_duration(media: Path) -> float | None:
    proc = subprocess.run([
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(media),
    ], text=True, capture_output=True)
    if proc.returncode != 0:
        return None
    try:
        duration = float((proc.stdout or "0").strip() or 0.0)
    except ValueError:
        return None
    return duration if duration > 0 else None


def unique_terms(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        value = cleanup_text(item)
        if not value or len(value) > 48:
            continue
        if re.fullmatch(r"\d+", value) and len(value) < 3:
            continue
        if re.fullmatch(r"\d+\s+[A-Za-z]", value):
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def build_asr_context(reference_raw: str, max_terms: int = ASR_CONTEXT_MAX_TERMS, max_chars: int = ASR_CONTEXT_MAX_CHARS) -> str:
    terms: list[str] = []
    terms.extend(reference_terms_for(reference_raw))
    extra_patterns = [
        r"\d+(?:\.\d+)?(?::\d+(?:\.\d+)?)+",
        r"\d+(?:\.\d+){2,}",
        r"\d+(?:\.\d+)?%",
        r"\d+(?:\.\d+)?\s*(?:声道|尼特|nits?|Hz|K|W|Wh|GB|TB|MB)",
        r"Judd\s+Offset",
        r"BT\s*\.?\s*2020",
    ]
    for pattern in extra_patterns:
        for match in re.finditer(pattern, reference_raw, re.I):
            terms.append(match.group(0))
    for term in ASR_CONTEXT_CJK_TERMS:
        if term in reference_raw:
            terms.append(term)

    ranked = sorted(
        unique_terms(terms),
        key=lambda value: (
            0 if re.search(r"[A-Za-z0-9.:%+-]", value) else 1,
            -len(value),
            value,
        ),
    )
    packed: list[str] = []
    total = 0
    for term in ranked[:max_terms]:
        next_total = total + len(term) + (1 if packed else 0)
        if max_chars > 0 and next_total > max_chars:
            break
        packed.append(term)
        total = next_total
    return " ".join(packed)


def write_asr_context(reference: Path, workdir: Path, use_reference_context: bool, extra_context_file: Path | None) -> Path | None:
    if not use_reference_context and not extra_context_file:
        return None
    parts: list[str] = []
    if use_reference_context:
        parts.append(build_asr_context(reference.read_text()))
    if extra_context_file:
        path = extra_context_file.expanduser().resolve()
        if not path.exists():
            raise SystemExit(f"ASR context file not found: {path}")
        parts.append(path.read_text())
    context = re.sub(r"\s+", " ", " ".join(part.strip() for part in parts if part.strip())).strip()
    if not context:
        return None
    edit_dir = workdir / "edit"
    edit_dir.mkdir(parents=True, exist_ok=True)
    path = edit_dir / "asr_context.txt"
    path.write_text(context + "\n")
    return path


def transcribe(
    media: Path,
    workdir: Path,
    status: dict,
    language: str,
    backend: str,
    asr_profile: str,
    model: str,
    chunk_seconds: float | None = None,
    chunk_threshold_seconds: float | None = None,
    no_chunk: bool = False,
    output_stem: str | None = None,
    context_file: Path | None = None,
) -> Path:
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
    ]
    if output_stem:
        cmd.extend(["--output-stem", output_stem])
    cmd.extend([
        "--language",
        language,
        "--backend",
        backend,
        "--asr-profile",
        asr_profile,
    ])
    if model:
        cmd.extend(["--model", model])
    if context_file:
        cmd.extend(["--context-file", str(context_file)])
    if chunk_seconds:
        cmd.extend(["--chunk-seconds", str(chunk_seconds)])
    if chunk_threshold_seconds:
        cmd.extend(["--chunk-threshold-seconds", str(chunk_threshold_seconds)])
    if no_chunk:
        cmd.append("--no-chunk")
    print("+", " ".join(cmd), file=sys.stderr)
    subprocess.run(cmd, check=True, stdout=sys.stderr, stderr=sys.stderr)
    transcript = edit_dir / "transcripts" / f"{output_stem or media.stem}.json"
    if not transcript.exists():
        raise SystemExit(f"transcript was not created: {transcript}")
    return transcript


def write_reference_copy(reference: Path, workdir: Path) -> Path:
    dest = workdir / "reference_script.txt"
    dest.write_text(reference.read_text())
    return dest


def write_agent_brief(
    workdir: Path,
    media: Path,
    reference: Path,
    transcript: Path,
    state: Path,
    reference_report: Path | None = None,
    semantic_packets: Path | None = None,
    audio_proxy: Path | None = None,
    asr_context: Path | None = None,
) -> None:
    audio_proxy_note = f"- ASR used extracted audio proxy `{audio_proxy}`; FCPXML and renders still target the original media.\n" if audio_proxy else ""
    report_note = f"- Agent semantic QA report: `{reference_report}`.\n" if reference_report else ""
    packets_note = f"- Residual semantic review packets: `{semantic_packets}`. Use these for focused follow-up after Direct EDL and clustering validation, not as the primary human review queue.\n" if semantic_packets else ""
    context_note = f"- ASR used optional terminology context `{asr_context}` to bias terms during transcription.\n" if asr_context else ""
    brief = f"""# Preprocessing Brief

This project was created automatically from media + reference script.

The import step has already generated timed review lines from `{transcript}`, then normalized visible text with `{reference}`:

- Audio/ASR is the source of truth.
- Use the reference script only to correct product names, model names, numbers, technical terms, punctuation, and semantic segmentation.
- Do not force unspoken reference text into the transcript.
- Conservative deletion lines may already be marked for repeated speech, false starts, long gaps, or clearly discarded takes.
- Reference-grouped projects show reference-script semantic groups plus spoken take candidates. Keep audio as truth; use the reference for terminology, numbers, segmentation, and obvious ASR fixes.
- Follow the chinese-subtitle skill for QA: check semantic bad breaks, protected terms, numbers/units, repeated takes, and long pause/breath cuts.
- For long narration, use the fast review path first: a middle-thinking Direct EDL pass creates a compact keep/delete/review plan with evidence lineIds, then a take-clustering validator checks missing-reference gaps, orphan tails, truncated/time-overlap lines, repeated-take chains, and dense metric runs.
- Browser/manual review should focus on Direct EDL reviewItems, clustering conflicts, and low-confidence audio questions. Do not send hundreds of line-level QA flags to the user.
- Keep `scriptLines[].start/end` on original media time.
{audio_proxy_note}- FCPXML guidance should reference the original media path, not temporary ASR audio.
{context_note}- ASR context is only a terminology hint. It must not be treated as proof that a term was spoken.
- For quick review, read `{workdir / "edit" / "takes_packed.md"}` before opening raw transcript JSON.
{report_note}- For semantic review, read `{workdir / "edit" / "reference_review_report.md"}` before opening the browser page.
{packets_note}- Apply delete/restore only at high confidence; medium text replacements and re-splits may apply when they preserve spoken content. Low-confidence suggestions should remain QA flags for browser review.
- Before sharing the page, inspect `{state}` once for obvious bad ASR matches, missed protected terms, leftover false starts, repeated takes, and unresolved Direct EDL/clustering conflicts.

Media: `{media}`
"""
    (workdir / "PREPROCESSING_BRIEF.md").write_text(brief)


def line_time(line: dict) -> str:
    return f"{float(line.get('start', 0.0)):06.2f}-{float(line.get('end', 0.0)):06.2f}"


def write_reference_review_report(workdir: Path, media: Path, state_path: Path) -> Path:
    edit_dir = workdir / "edit"
    edit_dir.mkdir(parents=True, exist_ok=True)
    report = edit_dir / "reference_review_report.md"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    lines = state.get("scriptLines") or []
    review = state.get("referenceReview") or {}
    groups = review.get("groups") or []
    qa_summary = review.get("qaSummary") or {}

    deleted = [line for line in lines if line.get("deleted")]
    pauses = [line for line in lines if line.get("lineType") == "pause"]
    flagged = [line for line in lines if line.get("qaFlags")]
    multi_take_groups = [group for group in groups if len(group.get("takes") or []) > 1]
    unmatched = review.get("unmatched") or [
        line for line in lines
        if line.get("referenceIndex") is None and line.get("lineType") != "pause"
    ]

    out = [
        "# Reference Review Report",
        "",
        f"Media: `{media}`",
        "",
        "## Agent QA Rules",
        "- Audio/口播 is source of truth; do not force unspoken reference text into subtitles.",
        "- Follow chinese-subtitle segmentation: Chinese semantics first, then length as a safety limit.",
        "- Check protected terms, numbers, units, bad joins, repeated takes, and pause/breath cuts.",
        "- In the browser, confirm suggested deletes before final export when the content is ambiguous.",
        "",
        "## Summary",
        f"- Reference groups: {qa_summary.get('groups', len(groups))}",
        f"- Candidate takes: {qa_summary.get('takes', 0)}",
        f"- Suggested deleted lines: {len(deleted)}",
        f"- Pause/breath lines: {len(pauses)}",
        f"- Unmatched/off-reference lines: {len(unmatched)}",
        f"- QA-flagged lines: {len(flagged)}",
        "",
    ]

    if multi_take_groups:
        out.extend(["## Multi-take Groups", ""])
        for group in multi_take_groups[:120]:
            out.append(f"### Ref {int(group.get('referenceIndex', 0)) + 1}: {group.get('referenceText', '')}")
            for take in group.get("takes") or []:
                state_label = "KEEP" if not take.get("deleted") else "DELETE"
                score = float(take.get("matchScore") or 0.0)
                out.append(
                    f"- {state_label} #{take.get('lineId')} [{line_time(take)}] "
                    f"score={score:.2f} flags={','.join(take.get('qaFlags') or []) or '-'}: {take.get('text', '')}"
                )
            out.append("")

    if deleted:
        out.extend(["## Suggested Deletes", ""])
        for line in deleted[:200]:
            out.append(
                f"- #{line.get('id')} [{line_time(line)}] {line.get('source', '')}: {line.get('text', '')}"
            )
        out.append("")

    if unmatched:
        out.extend(["## Unmatched / Off-reference", ""])
        for line in unmatched[:160]:
            out.append(
                f"- #{line.get('lineId', line.get('id'))} [{line_time(line)}] "
                f"flags={','.join(line.get('qaFlags') or []) or '-'}: {line.get('text', '')}"
            )
        out.append("")

    if flagged:
        out.extend(["## QA Flags", ""])
        for line in flagged[:200]:
            out.append(
                f"- #{line.get('id')} [{line_time(line)}] {','.join(line.get('qaFlags') or [])}: {line.get('text', '')}"
            )
        out.append("")

    protected_terms = review.get("protectedTerms") or []
    if protected_terms:
        out.extend(["## Protected Terms", ""])
        out.append(", ".join(str(term) for term in protected_terms[:120]))
        out.append("")

    report.write_text("\n".join(out))
    return report


def write_semantic_review_packets(workdir: Path, state_path: Path) -> Path:
    edit_dir = workdir / "edit"
    edit_dir.mkdir(parents=True, exist_ok=True)
    packets_path = edit_dir / "semantic_review_packets.jsonl"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    packets = build_semantic_review_packets(
        state.get("scriptLines") or [],
        state.get("referenceReview") or {},
    )
    with packets_path.open("w") as f:
        for packet in packets:
            f.write(json.dumps(packet, ensure_ascii=False, separators=(",", ":")) + "\n")
    return packets_path


def format_time(seconds: float) -> str:
    return f"{seconds:06.2f}"


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    return f"{minutes}m {seconds - minutes * 60:04.1f}s"


def token_text(items: list[dict]) -> str:
    parts = [str(item.get("text") or "").strip() for item in items]
    parts = [part for part in parts if part]
    if any(re.search(r"[\u3400-\u9fff]", part) for part in parts):
        return "".join(parts)
    return " ".join(parts)


def transcript_phrases(transcript: Path) -> list[dict]:
    data = json.loads(transcript.read_text())
    words = data.get("words") or []
    phrases: list[dict] = []
    current: list[dict] = []
    previous_end: float | None = None

    def flush() -> None:
        nonlocal current
        if not current:
            return
        text = token_text(current).strip()
        if text:
            phrases.append({
                "start": float(current[0]["start"]),
                "end": float(current[-1]["end"]),
                "text": text,
            })
        current = []

    for item in words:
        text = str(item.get("text") or "").strip()
        if not text or item.get("start") is None or item.get("end") is None:
            continue
        start = float(item["start"])
        end = float(item["end"])
        if previous_end is not None and start - previous_end >= PACKED_SILENCE_THRESHOLD:
            flush()
        current.append({"text": text, "start": start, "end": end})
        if text[-1] in PACKED_END_PUNCT:
            flush()
        previous_end = max(previous_end or end, end)
    flush()

    if phrases:
        return phrases

    for segment in data.get("segments") or []:
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start = float(segment.get("start", 0.0))
        end = float(segment.get("end", start + max(1.0, len(text) / 8.0)))
        phrases.append({"start": start, "end": max(start + 0.01, end), "text": text})
    return phrases


def write_packed_transcript(transcript: Path, workdir: Path, media: Path) -> Path:
    edit_dir = workdir / "edit"
    edit_dir.mkdir(parents=True, exist_ok=True)
    phrases = transcript_phrases(transcript)
    duration = phrases[-1]["end"] - phrases[0]["start"] if phrases else 0.0
    lines = [
        "# Packed transcript",
        "",
        f"Source: {media.name}",
        f"Transcript: {transcript}",
        f"Grouped on silences >= {PACKED_SILENCE_THRESHOLD:.1f}s when word timing is available.",
        "",
        f"## {media.stem}  (duration: {format_duration(duration)}, {len(phrases)} phrases)",
    ]
    if phrases:
        for phrase in phrases:
            lines.append(
                f"  [{format_time(float(phrase['start']))}-{format_time(float(phrase['end']))}] {phrase['text']}"
            )
    else:
        lines.append("  _no speech detected_")
    lines.append("")
    out = edit_dir / "takes_packed.md"
    out.write_text("\n".join(lines))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an interactive review project from media plus a reference script.")
    parser.add_argument("--media", required=True, help="Audio or video file path accessible to the review machine")
    parser.add_argument("--reference", required=True, help="Clean text/markdown reference narration script")
    parser.add_argument("--workdir", required=True, help="Project working directory to create")
    parser.add_argument("--project-id", help="Stable project id; defaults to a slug from title/media")
    parser.add_argument("--title", default="", help="Human-readable project title")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--backend", default="mlx" if platform.system() == "Darwin" else "official")
    parser.add_argument("--asr-profile", choices=ASR_PROFILE_CHOICES, default=DEFAULT_ASR_PROFILE, help="auto=fast for long media and quality for short media; fast=Qwen3-ASR-0.6B, quality=Qwen3-ASR-1.7B")
    parser.add_argument("--model", default=os.environ.get("QWEN3_ASR_MODEL", ""), help="Override --asr-profile with an explicit Qwen model id")
    parser.add_argument("--chunk-seconds", type=float, help="ASR chunk length in seconds for long media")
    parser.add_argument("--chunk-threshold-seconds", type=float, help="Use chunked ASR when media duration is at least this many seconds")
    parser.add_argument("--no-chunk-transcribe", action="store_true", help="Disable chunked ASR and transcribe whole extracted audio")
    parser.add_argument("--no-audio-proxy", action="store_true", help="Pass video files directly to ASR instead of extracting a small AAC audio proxy")
    parser.add_argument("--asr-context", action="store_true", help="Generate and pass reference-derived terminology context to ASR; opt-in because global context can bias or truncate ASR")
    parser.add_argument("--no-asr-context", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--asr-context-file", type=Path, help="Pass an explicit terminology context file to ASR; use a separate output stem/edit dir for A/B runs")
    parser.add_argument("--refresh-audio-proxy", action="store_true", help="Recreate an existing AAC audio proxy before transcription")
    parser.add_argument("--install-missing", action="store_true", help="Run bootstrap.py --install before transcription")
    parser.add_argument("--skip-transcribe", action="store_true", help="Only create project from existing --transcript-json")
    parser.add_argument("--transcript-json", help="Existing transcript JSON to use instead of running ASR")
    parser.add_argument("--alignment-json", help="Existing preprocessed alignment JSON")
    parser.add_argument("--delete-csv", help="Existing conservative delete interval CSV")
    parser.add_argument("--preprocess-mode", choices=["reference-grouped", "flat"])
    parser.add_argument("--pause-threshold", type=float, default=1.2)
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
    source_media_type = media_type(media)
    source_duration = probe_duration(media)
    audio_proxy = None
    asr_context = None if args.no_asr_context else write_asr_context(reference, workdir, args.asr_context, args.asr_context_file)

    if args.transcript_json:
        transcript = Path(args.transcript_json).expanduser().resolve()
        if source_media_type == "video" and not args.no_audio_proxy:
            audio_proxy = prepare_audio_proxy(media, workdir, args.refresh_audio_proxy)
    elif args.skip_transcribe:
        raise SystemExit("--skip-transcribe requires --transcript-json")
    else:
        status = ensure_bootstrap(args.install_missing)
        transcribe_media = media
        output_stem = None
        if source_media_type == "video" and not args.no_audio_proxy:
            audio_proxy = prepare_audio_proxy(media, workdir, args.refresh_audio_proxy)
            transcribe_media = audio_proxy
            output_stem = media.stem
        transcript = transcribe(
            transcribe_media,
            workdir,
            status,
            args.language,
            args.backend,
            args.asr_profile,
            args.model,
            args.chunk_seconds,
            args.chunk_threshold_seconds,
            args.no_chunk_transcribe,
            output_stem,
            asr_context,
        )

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
        source_media_type,
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
    if args.preprocess_mode:
        cmd.extend(["--preprocess-mode", args.preprocess_mode])
    if args.pause_threshold:
        cmd.extend(["--pause-threshold", str(args.pause_threshold)])
    if source_duration:
        cmd.extend(["--duration", f"{source_duration:.3f}"])
    if audio_proxy:
        cmd.extend(["--draft-media", str(audio_proxy)])
    if args.alignment_json:
        cmd.extend(["--alignment-json", str(Path(args.alignment_json).expanduser().resolve())])
    if args.delete_csv:
        cmd.extend(["--delete-csv", str(Path(args.delete_csv).expanduser().resolve())])
    if args.source_fps:
        cmd.extend(["--source-fps", str(args.source_fps)])
    print("+", " ".join(cmd), file=sys.stderr)
    proc = subprocess.run(cmd, check=True, text=True, capture_output=True)
    takes_packed = write_packed_transcript(transcript, workdir, media)
    import_result = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else {}
    reference_review_report = write_reference_review_report(workdir, media, state)
    semantic_review_packets = write_semantic_review_packets(workdir, state)
    write_agent_brief(
        workdir,
        media,
        reference_copy,
        transcript,
        state,
        reference_review_report,
        semantic_review_packets,
        audio_proxy,
        asr_context,
    )
    print(json.dumps({
        "projectId": project_id,
        "workdir": str(workdir),
        "manifest": str(manifest),
        "state": str(state),
        "transcript": str(transcript),
        "audioProxy": str(audio_proxy) if audio_proxy else "",
        "asrContext": str(asr_context) if asr_context else "",
        "sourceDuration": source_duration,
        "takesPacked": str(takes_packed),
        "referenceReviewReport": str(reference_review_report),
        "semanticReviewPackets": str(semantic_review_packets),
        "reference": str(reference_copy),
        "scriptLines": import_result.get("scriptLines"),
        "referenceGroups": import_result.get("referenceGroups"),
        "next": f"python3 {SKILL_DIR}/scripts/start_review_server.py --host 0.0.0.0 --manifest {manifest}",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
