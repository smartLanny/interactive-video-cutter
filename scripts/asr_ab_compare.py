#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


MATCH_PUNCT_RE = re.compile(r"[\s，,。.!！?？;；:：、\"'“”‘’（）()\[\]【】<>《》-]+")


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.expanduser().resolve().read_text())
    if not isinstance(data, dict):
        raise SystemExit(f"transcript JSON must be an object: {path}")
    return data


def normalize_text(value: str) -> str:
    return MATCH_PUNCT_RE.sub("", value).lower()


def visible_length(value: str) -> int:
    return len(normalize_text(value))


def text_of(data: dict[str, Any]) -> str:
    text = str(data.get("text") or "")
    if text:
        return text
    segments = data.get("segments") or []
    if isinstance(segments, list):
        return "".join(str(item.get("text") or "") for item in segments if isinstance(item, dict))
    return ""


def as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def word_time(word: dict[str, Any], key: str) -> float | None:
    value = as_float(word.get(key))
    if value is None:
        value = as_float(word.get(f"{key}_time"))
        if value is not None:
            value = value / 1000.0
    return value


def extract_segments(data: dict[str, Any], pause_threshold: float = 1.2, max_chars: int = 36) -> list[dict[str, Any]]:
    raw_segments = data.get("segments") or data.get("utterances") or []
    segments: list[dict[str, Any]] = []
    if isinstance(raw_segments, list):
        for index, item in enumerate(raw_segments):
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            start = as_float(item.get("start"))
            end = as_float(item.get("end"))
            if start is None:
                start = as_float(item.get("start_time"))
                if start is not None:
                    start /= 1000.0
            if end is None:
                end = as_float(item.get("end_time"))
                if end is not None:
                    end /= 1000.0
            if text:
                segments.append({"id": item.get("id", index), "text": text, "start": start, "end": end})
    if segments:
        return segments

    words = data.get("words") or []
    if not isinstance(words, list):
        return []
    current: list[dict[str, Any]] = []
    last_end: float | None = None
    for word in words:
        if not isinstance(word, dict):
            continue
        text = str(word.get("text") or "").strip()
        start = word_time(word, "start")
        end = word_time(word, "end")
        if not text or start is None or end is None:
            continue
        gap = start - last_end if last_end is not None else 0.0
        current_text = "".join(str(item.get("text") or "") for item in current)
        if current and (gap >= pause_threshold or visible_length(current_text) >= max_chars):
            segments.append(words_to_segment(current, len(segments)))
            current = []
        current.append({"text": text, "start": start, "end": end})
        last_end = end
    if current:
        segments.append(words_to_segment(current, len(segments)))
    return segments


def words_to_segment(words: list[dict[str, Any]], index: int) -> dict[str, Any]:
    return {
        "id": index,
        "text": "".join(str(item.get("text") or "") for item in words),
        "start": words[0].get("start"),
        "end": words[-1].get("end"),
    }


def metadata(data: dict[str, Any]) -> dict[str, Any]:
    value = data.get("metadata") or {}
    return value if isinstance(value, dict) else {}


def first_number(meta: dict[str, Any], names: list[str]) -> float | None:
    for name in names:
        value = as_float(meta.get(name))
        if value is not None:
            return value
    return None


def speed_summary(data: dict[str, Any]) -> dict[str, Any]:
    meta = metadata(data)
    elapsed = first_number(meta, ["elapsed_seconds", "wall_time_seconds", "processing_seconds", "runtime_seconds"])
    duration = first_number(meta, ["source_duration", "duration", "audio_duration", "media_duration"])
    if duration is None:
        audio_info = data.get("audio_info") or meta.get("audio_info")
        if isinstance(audio_info, dict):
            duration_ms = as_float(audio_info.get("duration"))
            duration = duration_ms / 1000.0 if duration_ms else None
    speed = round(duration / elapsed, 3) if duration and elapsed and elapsed > 0 else None
    return {
        "elapsedSeconds": elapsed,
        "sourceDurationSeconds": duration,
        "speedXRealtime": speed,
    }


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        current = [i]
        for j, char_b in enumerate(b, 1):
            insert_cost = current[j - 1] + 1
            delete_cost = previous[j] + 1
            replace_cost = previous[j - 1] + (0 if char_a == char_b else 1)
            current.append(min(insert_cost, delete_cost, replace_cost))
        previous = current
    return previous[-1]


def best_substring_match(fragment: str, text: str, max_windows: int = 1200) -> dict[str, Any]:
    frag = normalize_text(fragment)
    target = normalize_text(text)
    if not frag or not target:
        return {"ratio": 0.0, "editDistance": len(frag), "matched": ""}
    if frag in target:
        return {"ratio": 1.0, "editDistance": 0, "matched": frag}
    target_len = len(frag)
    lengths = sorted({max(1, int(target_len * 0.75)), target_len, max(1, int(target_len * 1.25))})
    step = max(1, target_len // 4)
    starts = list(range(0, max(1, len(target)), step))
    if len(starts) > max_windows:
        stride = math.ceil(len(starts) / max_windows)
        starts = starts[::stride]

    best_ratio = 0.0
    best_window = ""
    for start in starts:
        for length in lengths:
            window = target[start:start + length]
            if not window:
                continue
            ratio = SequenceMatcher(None, frag, window).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_window = window
                if ratio >= 0.995:
                    distance = levenshtein(frag, best_window)
                    return {"ratio": round(best_ratio, 4), "editDistance": distance, "matched": best_window}
    distance = levenshtein(frag, best_window) if best_window else len(frag)
    return {"ratio": round(best_ratio, 4), "editDistance": distance, "matched": best_window}


def whole_text_match(local_text: str, cloud_text: str) -> dict[str, Any]:
    local = normalize_text(local_text)
    cloud = normalize_text(cloud_text)
    if not local or not cloud:
        return {"ratio": 0.0, "editDistance": None}
    ratio = SequenceMatcher(None, local, cloud).ratio()
    distance = levenshtein(local, cloud) if max(len(local), len(cloud)) <= 3000 else None
    return {"ratio": round(ratio, 4), "editDistance": distance}


def read_terms(args: argparse.Namespace) -> list[str]:
    terms: list[str] = []
    for value in args.term or []:
        terms.extend(re.split(r"[\s,，、;；|]+", value))
    if args.terms_file:
        raw = args.terms_file.expanduser().resolve().read_text()
        terms.extend(re.split(r"[\s,，、;；|]+", raw))
    seen: set[str] = set()
    out: list[str] = []
    for item in terms:
        term = item.strip()
        if not term:
            continue
        key = normalize_text(term)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(term)
    return out


def term_hits(terms: list[str], local_text: str, cloud_text: str) -> list[dict[str, Any]]:
    local_norm = normalize_text(local_text)
    cloud_norm = normalize_text(cloud_text)
    rows: list[dict[str, Any]] = []
    for term in terms:
        key = normalize_text(term)
        local_count = local_norm.count(key)
        cloud_count = cloud_norm.count(key)
        rows.append({
            "term": term,
            "localCount": local_count,
            "cloudCount": cloud_count,
            "deltaCloudMinusLocal": cloud_count - local_count,
        })
    return rows


def read_reference_fragments(args: argparse.Namespace) -> list[str]:
    fragments: list[str] = []
    fragments.extend(args.reference_fragment or [])
    if args.reference_file:
        for line in args.reference_file.expanduser().resolve().read_text().splitlines():
            line = line.strip()
            if line:
                fragments.append(line)
    seen: set[str] = set()
    out: list[str] = []
    for fragment in fragments:
        value = re.sub(r"\s+", " ", fragment).strip()
        if not value:
            continue
        if len(value) > args.max_reference_fragment_chars:
            value = value[:args.max_reference_fragment_chars].strip()
        key = normalize_text(value)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value)
        if len(out) >= args.max_reference_fragments:
            break
    return out


def reference_matches(fragments: list[str], local_text: str, cloud_text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fragment in fragments:
        local = best_substring_match(fragment, local_text)
        cloud = best_substring_match(fragment, cloud_text)
        rows.append({
            "fragment": fragment,
            "localRatio": local["ratio"],
            "localEditDistance": local["editDistance"],
            "cloudRatio": cloud["ratio"],
            "cloudEditDistance": cloud["editDistance"],
            "deltaCloudMinusLocal": round(float(cloud["ratio"]) - float(local["ratio"]), 4),
        })
    return rows


def overlapping_text(segment: dict[str, Any], candidates: list[dict[str, Any]], pad_seconds: float) -> str:
    start = as_float(segment.get("start"))
    end = as_float(segment.get("end"))
    if start is None or end is None:
        return ""
    parts: list[str] = []
    for item in candidates:
        other_start = as_float(item.get("start"))
        other_end = as_float(item.get("end"))
        if other_start is None or other_end is None:
            continue
        if other_end >= start - pad_seconds and other_start <= end + pad_seconds:
            parts.append(str(item.get("text") or ""))
    return "".join(parts)


def suspicious_segments(
    source_segments: list[dict[str, Any]],
    target_segments: list[dict[str, Any]],
    target_text: str,
    label: str,
    min_chars: int,
    min_ratio: float,
    pad_seconds: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for segment in source_segments:
        text = str(segment.get("text") or "").strip()
        if visible_length(text) < min_chars:
            continue
        target_window = overlapping_text(segment, target_segments, pad_seconds) or target_text
        match = best_substring_match(text, target_window)
        if float(match["ratio"]) < min_ratio:
            rows.append({
                "source": label,
                "id": segment.get("id"),
                "start": segment.get("start"),
                "end": segment.get("end"),
                "text": text,
                "targetRatio": match["ratio"],
                "targetEditDistance": match["editDistance"],
            })
    return rows


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    local = load_json(args.local)
    cloud = load_json(args.cloud)
    local_text = text_of(local)
    cloud_text = text_of(cloud)
    local_segments = extract_segments(local)
    cloud_segments = extract_segments(cloud)
    terms = read_terms(args)
    fragments = read_reference_fragments(args)
    text_match = whole_text_match(local_text, cloud_text)

    return {
        "inputs": {
            "local": str(args.local.expanduser().resolve()),
            "cloud": str(args.cloud.expanduser().resolve()),
            "termsFile": str(args.terms_file.expanduser().resolve()) if args.terms_file else "",
            "referenceFile": str(args.reference_file.expanduser().resolve()) if args.reference_file else "",
        },
        "summary": {
            "localChars": visible_length(local_text),
            "cloudChars": visible_length(cloud_text),
            "deltaCloudMinusLocalChars": visible_length(cloud_text) - visible_length(local_text),
            "localSegments": len(local_segments),
            "cloudSegments": len(cloud_segments),
            "textSimilarityRatio": text_match["ratio"],
            "textEditDistanceApprox": text_match["editDistance"],
            "localSpeed": speed_summary(local),
            "cloudSpeed": speed_summary(cloud),
        },
        "termHits": term_hits(terms, local_text, cloud_text),
        "referenceMatches": reference_matches(fragments, local_text, cloud_text),
        "suspiciousMissing": {
            "cloudSegmentsWeakInLocal": suspicious_segments(
                cloud_segments,
                local_segments,
                local_text,
                "cloud",
                args.min_missing_chars,
                args.missing_ratio,
                args.overlap_pad_seconds,
            ),
            "localSegmentsWeakInCloud": suspicious_segments(
                local_segments,
                cloud_segments,
                cloud_text,
                "local",
                args.min_missing_chars,
                args.missing_ratio,
                args.overlap_pad_seconds,
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare local and cloud ASR transcript JSON files.")
    parser.add_argument("--local", type=Path, required=True, help="Local Qwen transcript JSON")
    parser.add_argument("--cloud", type=Path, required=True, help="Cloud transcript JSON")
    parser.add_argument("--term", action="append", help="Important term or comma-separated term list")
    parser.add_argument("--terms-file", type=Path, help="Term list file; one term per line or comma-separated")
    parser.add_argument("--reference-file", type=Path, help="Reference script whose lines are matched against both transcripts")
    parser.add_argument("--reference-fragment", action="append", help="Specific reference fragment to match")
    parser.add_argument("--max-reference-fragments", type=int, default=80)
    parser.add_argument("--max-reference-fragment-chars", type=int, default=160)
    parser.add_argument("--min-missing-chars", type=int, default=8)
    parser.add_argument("--missing-ratio", type=float, default=0.45)
    parser.add_argument("--overlap-pad-seconds", type=float, default=1.0)
    parser.add_argument("--out", type=Path, help="Write comparison JSON here; defaults to stdout")
    args = parser.parse_args()

    report = build_report(args)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        out = args.out.expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n")
        print(f"saved: {out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
