#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


DEFAULT_MODEL = os.environ.get("QWEN3_ASR_MODEL", "Qwen/Qwen3-ASR-1.7B")
DEFAULT_ALIGNER = os.environ.get("QWEN3_ASR_ALIGNER", "Qwen/Qwen3-ForcedAligner-0.6B")


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Transcribe media with local Qwen3-ASR and write transcript JSON.")
    parser.add_argument("media", type=Path)
    parser.add_argument("--edit-dir", type=Path, default=None)
    parser.add_argument("--language", default="zh")
    parser.add_argument("--backend", choices=["mlx", "official"], default=os.environ.get("QWEN3_ASR_BACKEND", "mlx"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--aligner", default=DEFAULT_ALIGNER)
    args = parser.parse_args()

    media = args.media.expanduser().resolve()
    if not media.exists():
        raise SystemExit(f"media not found: {media}")
    edit_dir = (args.edit_dir or (media.parent / "edit")).expanduser().resolve()
    transcript_dir = edit_dir / "transcripts"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    out = transcript_dir / f"{media.stem}.json"
    if out.exists():
        print(f"cached: {out}")
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / f"{media.stem}.wav"
        print(f"extracting audio: {media.name}", file=sys.stderr)
        run_ffmpeg_extract(media, audio)
        print(f"transcribing with {args.backend}: {args.model}", file=sys.stderr)
        if args.backend == "mlx":
            payload = transcribe_mlx(audio, args.language, args.model, args.aligner)
        else:
            payload = transcribe_official(audio, args.language, args.model, args.aligner)

    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"saved: {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
