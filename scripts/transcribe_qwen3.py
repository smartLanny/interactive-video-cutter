#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


DEFAULT_MODEL = os.environ.get("QWEN3_ASR_MODEL", "Qwen/Qwen3-ASR-1.7B")
DEFAULT_ALIGNER = os.environ.get("QWEN3_ASR_ALIGNER", "Qwen/Qwen3-ForcedAligner-0.6B")
DEFAULT_CHUNK_SECONDS = float(os.environ.get("INTERACTIVE_VIDEO_CUTTER_ASR_CHUNK_SECONDS", "180"))
DEFAULT_CHUNK_THRESHOLD_SECONDS = float(os.environ.get("INTERACTIVE_VIDEO_CUTTER_ASR_CHUNK_THRESHOLD_SECONDS", "600"))


def run_ffmpeg_extract(media: Path, audio: Path) -> None:
    subprocess.run([
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(media),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(audio),
    ], check=True)


def run_ffmpeg_extract_range(media: Path, audio: Path, start: float, duration: float) -> None:
    subprocess.run([
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(media),
        "-t",
        f"{duration:.3f}",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(audio),
    ], check=True)


def media_duration(path: Path) -> float:
    proc = subprocess.run([
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ], text=True, capture_output=True, check=True)
    return float((proc.stdout or "0").strip() or 0.0)


def language_name(value: str | None) -> str | None:
    if not value:
        return None
    mapping = {
        "zh": "Chinese",
        "zh-cn": "Chinese",
        "cn": "Chinese",
        "en": "English",
        "ja": "Japanese",
        "ko": "Korean",
        "yue": "Cantonese",
    }
    return mapping.get(value.lower(), value)


def get_value(obj: Any, *names: str) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


def is_cjk(text: str) -> bool:
    return any("\u3400" <= ch <= "\u9fff" for ch in text)


def fallback_units(text: str, start: float, end: float) -> list[dict[str, Any]]:
    text = text.strip()
    if not text:
        return []
    units = [ch for ch in text if not ch.isspace()] if is_cjk(text) else text.split()
    if not units:
        return []
    span = max(0.01, end - start)
    step = span / len(units)
    return [
        {
            "type": "word",
            "text": unit,
            "start": round(start + i * step, 3),
            "end": round(start + (i + 1) * step, 3),
        }
        for i, unit in enumerate(units)
    ]


def timestamp_to_word(item: Any) -> dict[str, Any] | None:
    text = get_value(item, "text", "word", "token")
    start = get_value(item, "start", "start_time")
    end = get_value(item, "end", "end_time")
    if text is None or start is None or end is None:
        return None
    raw = str(text).strip()
    if not raw:
        return None
    return {
        "type": "word",
        "text": raw,
        "start": round(float(start), 3),
        "end": round(float(end), 3),
    }


def normalize_result(result: Any, audio: Path, backend: str, model: str) -> dict[str, Any]:
    if isinstance(result, list):
        result = result[0] if result else {}
    text = str(get_value(result, "text") or "")
    raw_timestamps = get_value(result, "time_stamps", "timestamps", "words") or []
    raw_segments = get_value(result, "segments", "chunks") or []

    words: list[dict[str, Any]] = []
    for item in raw_timestamps:
        word = timestamp_to_word(item)
        if word:
            words.append(word)

    if not words:
        for segment in raw_segments:
            word = timestamp_to_word(segment)
            if word:
                words.append(word)
                continue
            seg_text = get_value(segment, "text")
            start = get_value(segment, "start", "start_time")
            end = get_value(segment, "end", "end_time")
            if seg_text is not None and start is not None and end is not None:
                words.extend(fallback_units(str(seg_text), float(start), float(end)))

    if not words and text:
        words = fallback_units(text, 0.0, media_duration(audio))

    words.sort(key=lambda item: (float(item["start"]), float(item["end"])))
    return {
        "text": text or "".join(str(item["text"]) for item in words),
        "words": words,
        "metadata": {
            "asr_backend": backend,
            "model_id": model,
            "format": "interactive-video-cutter",
        },
    }


def transcribe_mlx(audio: Path, language: str | None, model: str, aligner: str) -> dict[str, Any]:
    try:
        from mlx_qwen3_asr import transcribe as qwen_transcribe  # type: ignore
    except Exception as exc:
        raise RuntimeError("mlx_qwen3_asr is not installed; run scripts/bootstrap.py --install") from exc

    kwargs: dict[str, Any] = {
        "model": model,
        "language": language_name(language),
        "return_timestamps": True,
        "forced_aligner": aligner,
        "verbose": False,
    }
    try:
        result = qwen_transcribe(str(audio), **kwargs)
    except TypeError:
        kwargs.pop("verbose", None)
        result = qwen_transcribe(str(audio), **kwargs)
    return normalize_result(result, audio, "mlx-qwen3-asr", model)


def transcribe_official(audio: Path, language: str | None, model: str, aligner: str) -> dict[str, Any]:
    try:
        import torch  # type: ignore
        from qwen_asr import Qwen3ASRModel  # type: ignore
    except Exception as exc:
        raise RuntimeError("qwen_asr is not installed; run scripts/bootstrap.py --install") from exc

    device_map = "cuda:0" if torch.cuda.is_available() else "cpu"
    asr = Qwen3ASRModel.from_pretrained(
        model,
        dtype=torch.bfloat16 if device_map.startswith("cuda") else torch.float32,
        device_map=device_map,
        forced_aligner=aligner,
        forced_aligner_kwargs={
            "dtype": torch.bfloat16 if device_map.startswith("cuda") else torch.float32,
            "device_map": device_map,
        },
    )
    try:
        result = asr.transcribe(
            audio=str(audio),
            language=language_name(language),
            return_time_stamps=True,
        )
        return normalize_result(result, audio, "qwen-asr", model)
    finally:
        del asr


def transcribe_audio(audio: Path, backend: str, language: str | None, model: str, aligner: str) -> dict[str, Any]:
    if backend == "mlx":
        return transcribe_mlx(audio, language, model, aligner)
    return transcribe_official(audio, language, model, aligner)


def shift_words(words: list[dict[str, Any]], offset: float) -> list[dict[str, Any]]:
    shifted: list[dict[str, Any]] = []
    for item in words:
        word = dict(item)
        try:
            word["start"] = round(float(word["start"]) + offset, 3)
            word["end"] = round(float(word["end"]) + offset, 3)
        except (KeyError, TypeError, ValueError):
            continue
        shifted.append(word)
    return shifted


def combine_text(parts: list[str]) -> str:
    parts = [part.strip() for part in parts if part.strip()]
    if not parts:
        return ""
    if any(is_cjk(part) for part in parts):
        return "".join(parts)
    return " ".join(parts)


def transcribe_chunked(
    media: Path,
    transcript_dir: Path,
    duration: float,
    chunk_seconds: float,
    backend: str,
    language: str | None,
    model: str,
    aligner: str,
) -> dict[str, Any]:
    chunk_dir = transcript_dir / f"{media.stem}.chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    all_words: list[dict[str, Any]] = []
    text_parts: list[str] = []
    chunk_count = math.ceil(duration / chunk_seconds)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for index in range(chunk_count):
            start = index * chunk_seconds
            chunk_duration = min(chunk_seconds, max(0.0, duration - start))
            if chunk_duration <= 0:
                continue
            chunk_json = chunk_dir / f"chunk_{index:04d}.json"
            if chunk_json.exists():
                payload = json.loads(chunk_json.read_text())
                print(f"cached chunk {index + 1}/{chunk_count}: {chunk_json}", file=sys.stderr)
            else:
                audio = tmp_dir / f"{media.stem}_chunk_{index:04d}.wav"
                print(
                    f"extracting chunk {index + 1}/{chunk_count}: "
                    f"{start:.1f}s-{start + chunk_duration:.1f}s",
                    file=sys.stderr,
                )
                run_ffmpeg_extract_range(media, audio, start, chunk_duration)
                print(f"transcribing chunk {index + 1}/{chunk_count} with {backend}: {model}", file=sys.stderr)
                payload = transcribe_audio(audio, backend, language, model, aligner)
                payload["metadata"] = {
                    **payload.get("metadata", {}),
                    "chunk_index": index,
                    "chunk_start": round(start, 3),
                    "chunk_duration": round(chunk_duration, 3),
                }
                chunk_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
            text_parts.append(str(payload.get("text") or ""))
            all_words.extend(shift_words(payload.get("words") or [], start))

    all_words.sort(key=lambda item: (float(item["start"]), float(item["end"])))
    return {
        "text": combine_text(text_parts) or "".join(str(item["text"]) for item in all_words),
        "words": all_words,
        "metadata": {
            "asr_backend": backend,
            "model_id": model,
            "format": "interactive-video-cutter",
            "chunked": True,
            "chunk_seconds": chunk_seconds,
            "chunk_count": chunk_count,
            "source_duration": round(duration, 3),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Transcribe media with local Qwen3-ASR and write transcript JSON.")
    parser.add_argument("media", type=Path)
    parser.add_argument("--edit-dir", type=Path, default=None)
    parser.add_argument("--language", default="zh")
    parser.add_argument("--backend", choices=["mlx", "official"], default=os.environ.get("QWEN3_ASR_BACKEND", "mlx"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--aligner", default=DEFAULT_ALIGNER)
    parser.add_argument("--chunk-seconds", type=float, default=DEFAULT_CHUNK_SECONDS)
    parser.add_argument("--chunk-threshold-seconds", type=float, default=DEFAULT_CHUNK_THRESHOLD_SECONDS)
    parser.add_argument("--no-chunk", action="store_true", help="Transcribe the whole extracted audio in one ASR call")
    args = parser.parse_args()

    media = args.media.expanduser().resolve()
    if not media.exists():
        raise SystemExit(f"media not found: {media}")
    if args.chunk_seconds <= 0:
        raise SystemExit("--chunk-seconds must be greater than 0")
    if args.chunk_threshold_seconds <= 0:
        raise SystemExit("--chunk-threshold-seconds must be greater than 0")
    edit_dir = (args.edit_dir or (media.parent / "edit")).expanduser().resolve()
    transcript_dir = edit_dir / "transcripts"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    out = transcript_dir / f"{media.stem}.json"
    if out.exists():
        print(f"cached: {out}")
        return 0

    duration = media_duration(media)
    use_chunks = not args.no_chunk and duration >= args.chunk_threshold_seconds
    if use_chunks:
        print(
            f"transcribing in chunks: duration={duration:.1f}s "
            f"chunk_seconds={args.chunk_seconds:.1f}s threshold={args.chunk_threshold_seconds:.1f}s",
            file=sys.stderr,
        )
        payload = transcribe_chunked(
            media,
            transcript_dir,
            duration,
            args.chunk_seconds,
            args.backend,
            args.language,
            args.model,
            args.aligner,
        )
    else:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / f"{media.stem}.wav"
            print(f"extracting audio: {media.name}", file=sys.stderr)
            run_ffmpeg_extract(media, audio)
            print(f"transcribing with {args.backend}: {args.model}", file=sys.stderr)
            payload = transcribe_audio(audio, args.backend, args.language, args.model, args.aligner)

    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"saved: {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
