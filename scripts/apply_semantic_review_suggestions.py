#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from preprocess_chinese import build_reference_review, subtitle_display_text


DEFAULT_ACTION_CONFIDENCES = {
    "delete": {"high"},
    "restore": {"high"},
    "replace": {"high", "medium"},
    "replace_and_split": {"high", "medium"},
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def strip_json_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def load_suggestions(path: Path) -> list[dict[str, Any]]:
    raw = strip_json_fence(path.read_text())
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if isinstance(parsed, dict):
        for key in ("suggestions", "items", "actions"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
        else:
            parsed = [parsed]
    if not isinstance(parsed, list):
        raise SystemExit(f"expected suggestion array: {path}")
    return [item for item in parsed if isinstance(item, dict)]


def append_flag(line: dict[str, Any], flag: str) -> None:
    flags = list(line.get("qaFlags") or [])
    if flag not in flags:
        flags.append(flag)
    line["qaFlags"] = flags


def append_source(line: dict[str, Any], marker: str) -> None:
    source = str(line.get("source") or "").strip()
    if marker not in source.split():
        line["source"] = f"{source} {marker}".strip()


def append_review_note(line: dict[str, Any], suggestion: dict[str, Any], applied: bool) -> None:
    notes = list(line.get("semanticReview") or [])
    notes.append({
        "action": suggestion.get("action"),
        "confidence": suggestion.get("confidence"),
        "reason": suggestion.get("reason", ""),
        "applied": applied,
    })
    line["semanticReview"] = notes


def line_id_map(lines: list[dict[str, Any]]) -> dict[int, int]:
    out: dict[int, int] = {}
    for index, line in enumerate(lines):
        try:
            out[int(line.get("id"))] = index
        except (TypeError, ValueError):
            continue
    return out


def clean_suggestion_text(value: Any) -> str:
    return subtitle_display_text(str(value or "")).strip()


def suggestion_line_ids(suggestion: dict[str, Any]) -> list[int]:
    ids = suggestion.get("lineIds") or []
    out: list[int] = []
    for value in ids:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return out


def mark_pending(lines: list[dict[str, Any]], suggestion: dict[str, Any], indices: list[int]) -> None:
    confidence = str(suggestion.get("confidence") or "unknown").lower()
    for index in indices:
        line = lines[index]
        append_flag(line, "semantic-llm-review")
        append_flag(line, f"llm-suggestion-{confidence}")
        append_flag(line, "llm-suggestion-pending")
        for flag in suggestion.get("qaFlags") or []:
            append_flag(line, str(flag))
        append_review_note(line, suggestion, applied=False)


def should_apply_suggestion(suggestion: dict[str, Any], confidence_override: set[str] | None) -> bool:
    action = str(suggestion.get("action") or "")
    confidence = str(suggestion.get("confidence") or "unknown").lower()
    if confidence_override is not None:
        return confidence in confidence_override
    return confidence in DEFAULT_ACTION_CONFIDENCES.get(action, set())


def apply_simple(lines: list[dict[str, Any]], suggestion: dict[str, Any], indices: list[int]) -> int:
    action = str(suggestion.get("action") or "")
    count = 0
    for index in indices:
        line = lines[index]
        if action == "delete":
            line["deleted"] = True
        elif action == "restore":
            line["deleted"] = False
        elif action == "replace":
            text = clean_suggestion_text(suggestion.get("text"))
            if not text:
                continue
            line["text"] = text
        else:
            continue
        append_flag(line, "semantic-llm-review")
        for flag in suggestion.get("qaFlags") or []:
            append_flag(line, str(flag))
        append_source(line, f"semantic-llm:{action}:{suggestion.get('confidence', 'unknown')}")
        append_review_note(line, suggestion, applied=True)
        count += 1
    return count


def source_line_for_time(target_lines: list[dict[str, Any]], start: float, end: float) -> dict[str, Any]:
    midpoint = (start + end) / 2
    for line in target_lines:
        line_start = float(line.get("start") or 0.0)
        line_end = float(line.get("end") or line_start)
        if line_start <= midpoint <= line_end:
            return line
    return target_lines[min(len(target_lines) - 1, 0)]


def apply_replace_and_split(lines: list[dict[str, Any]], suggestion: dict[str, Any], indices: list[int]) -> int:
    split_texts = [clean_suggestion_text(text) for text in suggestion.get("splitTexts") or []]
    split_texts = [text for text in split_texts if text]
    if not split_texts or not indices:
        return 0

    target_lines = [lines[index] for index in sorted(indices)]
    start = min(float(line.get("start") or 0.0) for line in target_lines)
    end = max(float(line.get("end") or line.get("start") or 0.0) for line in target_lines)
    end = max(start + 0.05, end)
    total_chars = sum(max(1, len(text)) for text in split_texts)
    cursor = start
    new_lines: list[dict[str, Any]] = []
    for text in split_texts:
        span = (end - start) * max(1, len(text)) / total_chars
        item_start = cursor
        item_end = min(end, cursor + span)
        base = source_line_for_time(target_lines, item_start, item_end)
        item = dict(base)
        item["start"] = round(item_start, 3)
        item["end"] = round(max(item_start + 0.05, item_end), 3)
        item["text"] = text
        item["deleted"] = False
        append_flag(item, "semantic-llm-review")
        for flag in suggestion.get("qaFlags") or []:
            append_flag(item, str(flag))
        append_source(item, f"semantic-llm:replace_and_split:{suggestion.get('confidence', 'unknown')}")
        append_review_note(item, suggestion, applied=True)
        new_lines.append(item)
        cursor = item_end
    new_lines[-1]["end"] = round(end, 3)

    first = min(indices)
    remove = set(indices)
    rebuilt: list[dict[str, Any]] = []
    inserted = False
    for index, line in enumerate(lines):
        if index == first:
            rebuilt.extend(new_lines)
            inserted = True
        if index not in remove:
            rebuilt.append(line)
    if not inserted:
        rebuilt.extend(new_lines)
    lines[:] = rebuilt
    return len(new_lines)


def reindex_lines(lines: list[dict[str, Any]]) -> None:
    for index, line in enumerate(lines, 1):
        line["id"] = index
        line["index"] = index


def rebuild_reference_review(state: dict[str, Any], pause_threshold: float) -> None:
    review = state.get("referenceReview") or {}
    reference_lines = review.get("referenceLines") or []
    protected_terms = review.get("protectedTerms") or []
    if reference_lines:
        state["referenceReview"] = build_reference_review(
            state.get("scriptLines") or [],
            reference_lines,
            protected_terms,
            mode=review.get("mode") or "reference-grouped",
            pause_threshold=float(review.get("pauseThreshold") or pause_threshold),
        )


def rebuild_cues(state: dict[str, Any]) -> None:
    state["cues"] = [
        {key: line[key] for key in ("id", "index", "start", "end", "text") if key in line}
        for line in state.get("scriptLines") or []
        if not line.get("deleted")
    ]


def normalize_line_texts(lines: list[dict[str, Any]]) -> None:
    for line in lines:
        if line.get("lineType") == "pause":
            continue
        text = subtitle_display_text(str(line.get("text") or "")).strip()
        if text:
            line["text"] = text


def apply_suggestions(
    state: dict[str, Any],
    suggestions: list[dict[str, Any]],
    confidence_override: set[str] | None,
    pause_threshold: float,
) -> dict[str, int]:
    lines = state.get("scriptLines") or []
    stats = {"applied": 0, "pending": 0, "missing": 0, "skipped": 0}
    for suggestion in suggestions:
        ids = suggestion_line_ids(suggestion)
        id_map = line_id_map(lines)
        indices = [id_map[line_id] for line_id in ids if line_id in id_map]
        if not indices:
            stats["missing"] += 1
            continue
        action = str(suggestion.get("action") or "")
        if action == "flag_only":
            mark_pending(lines, suggestion, indices)
            stats["pending"] += 1
            continue
        if not should_apply_suggestion(suggestion, confidence_override):
            mark_pending(lines, suggestion, indices)
            stats["pending"] += 1
            continue
        if action in {"delete", "restore", "replace"}:
            changed = apply_simple(lines, suggestion, indices)
        elif action == "replace_and_split":
            changed = apply_replace_and_split(lines, suggestion, indices)
        else:
            changed = 0
        if changed:
            stats["applied"] += 1
        elif action != "flag_only":
            stats["skipped"] += 1
    normalize_line_texts(lines)
    reindex_lines(lines)
    rebuild_cues(state)
    rebuild_reference_review(state, pause_threshold)
    state.setdefault("semanticReview", {})
    state["semanticReview"]["lastApplied"] = {
        "suggestions": len(suggestions),
        "policy": (
            {"confidenceOverride": sorted(confidence_override)}
            if confidence_override is not None
            else {"delete": ["high"], "restore": ["high"], "replace": ["high", "medium"], "replace_and_split": ["high", "medium"]}
        ),
        "stats": stats,
    }
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply LLM semantic review suggestions to an interactive review state.")
    parser.add_argument("--state", required=True)
    parser.add_argument("--suggestions", required=True)
    parser.add_argument("--output", help="Defaults to overwriting --state")
    parser.add_argument(
        "--include-confidence",
        action="append",
        choices=["high", "medium", "low"],
        help="Override the default action-aware policy and apply these confidence levels for every action.",
    )
    parser.add_argument("--pause-threshold", type=float, default=1.2)
    args = parser.parse_args()

    state_path = Path(args.state).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve() if args.output else state_path
    suggestions_path = Path(args.suggestions).expanduser().resolve()
    confidence_override = set(args.include_confidence) if args.include_confidence else None
    state = read_json(state_path)
    suggestions = load_suggestions(suggestions_path)
    stats = apply_suggestions(state, suggestions, confidence_override, args.pause_threshold)
    write_json(output_path, state)
    print(json.dumps({
        "state": str(output_path),
        "suggestions": len(suggestions),
        "policy": (
            {"confidenceOverride": sorted(confidence_override)}
            if confidence_override is not None
            else {"delete": ["high"], "restore": ["high"], "replace": ["high", "medium"], "replace_and_split": ["high", "medium"]}
        ),
        "stats": stats,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
