#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


END_PUNCT = "。！？!?"
SOFT_PUNCT = "，,、；;：:"
MAX_REVIEW_LINE_CHARS = 42
MAX_REVIEW_SOFT_PUNCT = 2
CJK_DIGITS = {
    "0": "零〇",
    "1": "一幺壹",
    "2": "二两贰",
    "3": "三叁",
    "4": "四肆",
    "5": "五伍",
    "6": "六陆",
    "7": "七柒",
    "8": "八捌",
    "9": "九玖",
}
TECH_ACRONYMS = {
    "AI",
    "AMD",
    "CPU",
    "CUDA",
    "DLSS",
    "DP",
    "DDR",
    "GPU",
    "HDR",
    "HDMI",
    "IPS",
    "NPU",
    "OLED",
    "PCIe",
    "RTX",
    "TB",
    "USB",
    "VRAM",
}


@dataclass(frozen=True)
class ReplaceRule:
    pattern: re.Pattern[str]
    value: str
    source: str


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def cleanup_markdown_line(line: str) -> str:
    line = re.sub(r"^\s{0,3}#{1,6}\s+", "", line.strip())
    line = re.sub(r"^\s*[-*+>]\s+", "", line)
    line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
    line = re.sub(r"`([^`]+)`", r"\1", line)
    return line.strip()


def cleanup_text(text: str) -> str:
    text = text.strip()
    text = text.replace("﹐", "，").replace("｡", "。").replace("．", ".")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", text)
    text = re.sub(r"\s*([，,、；;：:。！？!?])\s*", r"\1", text)
    text = re.sub(r"[，,、]{2,}", "，", text)
    text = re.sub(r"[；;：:]{2,}", "，", text)
    text = re.sub(r"[。\.]{2,}", "。", text)
    text = re.sub(r"[，,、；;：:]+$", "", text)
    text = re.sub(r"^[，,、；;：:]+", "", text)
    if len(text) <= 42 and text.count("，") >= 3:
        text = text.replace("，", "")
    return text.strip()


def split_reference_lines(raw: str) -> list[str]:
    lines: list[str] = []
    for raw_line in raw.splitlines():
        line = cleanup_markdown_line(raw_line)
        if not line or line.startswith("```"):
            continue
        parts = [part.strip() for part in re.split(r"(?<=[。！？!?；;])", line) if part.strip()]
        for part in parts or [line]:
            part = cleanup_text(part)
            if not part:
                continue
            if len(part) > 46:
                subparts = [item.strip() for item in re.split(r"(?<=[，,、])", part) if item.strip()]
                if len(subparts) > 1:
                    lines.extend(cleanup_text(item) for item in subparts if cleanup_text(item))
                    continue
            lines.append(part)
    return lines


def digit_pattern(number: str) -> str | None:
    if "." in number:
        left, right = number.split(".", 1)
        if len(left) != 1 or len(right) != 1:
            return None
        return f"[{re.escape(left)}{CJK_DIGITS.get(left, '')}](?:点|\\.)[{re.escape(right)}{CJK_DIGITS.get(right, '')}]"
    if len(number) < 2 or len(number) > 5 or not number.isdigit():
        return None
    return "".join(f"[{re.escape(ch)}{CJK_DIGITS.get(ch, '')}]" for ch in number)


def flexible_ascii_pattern(term: str) -> str:
    pieces: list[str] = []
    for ch in term:
        if ch.isspace():
            pieces.append(r"[\s·,，、-]*")
        elif ch == ".":
            pieces.append(r"(?:\.|点)")
        elif ch in "-+/":
            pieces.append(r"[\s" + re.escape(ch) + r"]*")
        elif ch.isalnum():
            pieces.append(re.escape(ch) + r"\s*")
        else:
            pieces.append(re.escape(ch))
    return "".join(pieces).rstrip(r"\s*")


def extract_reference_terms(raw: str) -> list[str]:
    terms: set[str] = set()
    for item in TECH_ACRONYMS:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(item)}(?![A-Za-z0-9])", raw, re.I):
            terms.add(item)

    patterns = [
        r"(?<![A-Za-z0-9])(?:[A-Z]{2,}|[A-Za-z]+)\s*\d+(?:\.\d+)?(?:\s*(?:Ti|SUPER|Super|Max|Pro|Ultra|HX|HS|H|U|W|Wh|Hz|GB|TB|K|AI))*",
        r"(?<![A-Za-z0-9])(?:DLSS|RTX|GTX|HDMI|DP|USB|PCIe|LPDDR|DDR|Wi-?Fi)\s*[A-Za-z0-9.+-]*(?:\s+[A-Za-z0-9.+-]+){0,3}",
        r"\d+(?:\.\d+)?\s*(?:W|Wh|Hz|GHz|MHz|GB|TB|MB|K|nits?|%|英寸|寸)",
        r"\d{2,5}(?:\s*(?:Ti|SUPER|Super|Max|Pro|Ultra|HX|HS|H|U))?",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, raw):
            value = cleanup_text(match.group(0))
            if 1 < len(value) <= 32:
                terms.add(value)
    return sorted(terms, key=len, reverse=True)


def build_rules(reference_raw: str) -> list[ReplaceRule]:
    rules: list[ReplaceRule] = []

    for number in sorted(set(re.findall(r"\d+(?:\.\d+)?", reference_raw)), key=len, reverse=True):
        pattern = digit_pattern(number)
        if pattern:
            rules.append(ReplaceRule(re.compile(pattern), number, "number"))

    for term in extract_reference_terms(reference_raw):
        if not re.search(r"[A-Za-z]", term):
            continue
        pattern = flexible_ascii_pattern(term)
        if pattern:
            rules.append(ReplaceRule(re.compile(pattern, re.I), term, "term"))

    if "618" in reference_raw:
        rules.append(ReplaceRule(re.compile(r"六[一幺1][八8]|6[一幺]8|六18"), "618", "known-618"))
    if re.search(r"DLSS\s*4\.5", reference_raw, re.I):
        rules.append(ReplaceRule(re.compile(r"D\s*L\s*S\s*S\s*(?:4(?:\.|点)5|四点五)", re.I), "DLSS 4.5", "known-dlss"))

    return rules


def apply_rules(text: str, rules: list[ReplaceRule]) -> str:
    for rule in rules:
        text = rule.pattern.sub(rule.value, text)
    text = re.sub(r"(?i)\br\s*t\s*x\b", "RTX", text)
    text = re.sub(r"(?i)\bd\s*l\s*s\s*s\b", "DLSS", text)
    text = re.sub(r"(?i)\bh\s*d\s*m\s*i\b", "HDMI", text)
    text = re.sub(r"(?i)\bu\s*s\s*b\b", "USB", text)
    text = re.sub(r"(?<=\d)\s*赫兹", "Hz", text)
    text = re.sub(r"(?<=\d)\s*瓦时", "Wh", text)
    text = re.sub(r"(?<=\d)\s*瓦", "W", text)
    return cleanup_text(text)


def normalize_for_match(text: str) -> str:
    text = cleanup_text(text).lower()
    for digit, chars in CJK_DIGITS.items():
        for ch in chars:
            text = text.replace(ch, digit)
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", text)


def best_reference_match(text: str, reference_lines: list[str]) -> tuple[str | None, float, int | None]:
    norm_text = normalize_for_match(text)
    if len(norm_text) < 6:
        return None, 0.0, None
    best_line: str | None = None
    best_index: int | None = None
    best_score = 0.0
    text_len = len(norm_text)
    for ref_index, ref in enumerate(reference_lines):
        norm_ref = normalize_for_match(ref)
        if len(norm_ref) < 6:
            continue
        ratio = text_len / len(norm_ref)
        if ratio < 0.58 or ratio > 1.72:
            continue
        score = difflib.SequenceMatcher(None, norm_text, norm_ref).ratio()
        if score > best_score:
            best_line = ref
            best_index = ref_index
            best_score = score
    if best_score >= 0.82:
        return best_line, best_score, best_index
    return None, best_score, None


def units_from_words(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for item in words:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        if item.get("type") == "spacing" and text.isspace():
            continue
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        units.append({"text": text, "start": start, "end": max(start + 0.01, end)})
    return units


def make_line(index: int, units: list[dict[str, Any]]) -> dict[str, Any]:
    text = cleanup_text("".join(str(unit["text"]) for unit in units))
    return {
        "id": index,
        "index": index,
        "start": round(float(units[0]["start"]), 3),
        "end": round(float(units[-1]["end"]), 3),
        "text": text,
        "deleted": False,
        "source": "qwen-words preprocessed",
    }


def lines_from_word_units(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not units:
        return []
    lines: list[dict[str, Any]] = []
    chunk: list[dict[str, Any]] = []
    previous_end: float | None = None

    for unit in units:
        gap = 0.0 if previous_end is None else float(unit["start"]) - previous_end
        if chunk and gap > 1.1 and len(cleanup_text("".join(str(item["text"]) for item in chunk))) >= 4:
            lines.append(make_line(len(lines) + 1, chunk))
            chunk = []

        chunk.append(unit)
        current_text = cleanup_text("".join(str(item["text"]) for item in chunk))
        plain_len = len(re.sub(r"\s+", "", current_text))
        token = str(unit["text"])
        should_cut = False
        if token and token[-1] in END_PUNCT:
            should_cut = True
        elif plain_len >= 30 and token and token[-1] in SOFT_PUNCT:
            should_cut = True
        elif plain_len >= 42:
            should_cut = True

        if should_cut and current_text:
            lines.append(make_line(len(lines) + 1, chunk))
            chunk = []
        previous_end = max(previous_end or float(unit["end"]), float(unit["end"]))

    if chunk:
        lines.append(make_line(len(lines) + 1, chunk))
    return [line for line in lines if line["text"]]


def split_segment_text(text: str, start: float, end: float) -> list[dict[str, Any]]:
    parts = [cleanup_text(part) for part in re.split(r"(?<=[。！？!?；;])", text) if cleanup_text(part)]
    if not parts:
        parts = [cleanup_text(text)] if cleanup_text(text) else []
    total_chars = sum(max(1, len(part)) for part in parts)
    cursor = start
    lines: list[dict[str, Any]] = []
    span = max(0.05, end - start)
    for part in parts:
        part_span = span * max(1, len(part)) / total_chars
        lines.append({
            "id": len(lines) + 1,
            "index": len(lines) + 1,
            "start": round(cursor, 3),
            "end": round(min(end, cursor + part_span), 3),
            "text": part,
            "deleted": False,
            "source": "transcript-segments preprocessed",
        })
        cursor += part_span
    return lines


def lines_from_transcript_data(data: dict[str, Any]) -> list[dict[str, Any]]:
    words = data.get("words") or []
    word_lines = lines_from_word_units(units_from_words(words))
    if word_lines:
        return word_lines

    lines: list[dict[str, Any]] = []
    for segment in data.get("segments") or []:
        text = cleanup_text(str(segment.get("text") or ""))
        if not text:
            continue
        start = float(segment.get("start", 0.0))
        end = float(segment.get("end", start + max(1.0, len(text) / 8.0)))
        for item in split_segment_text(text, start, max(start + 0.05, end)):
            item["id"] = len(lines) + 1
            item["index"] = len(lines) + 1
            lines.append(item)
    if lines:
        return lines

    text = cleanup_text(str(data.get("text") or ""))
    if not text:
        return []
    parts = [part for part in re.split(r"(?<=[。！？!?；;])", text) if cleanup_text(part)]
    cursor = 0.0
    for part in parts:
        part = cleanup_text(part)
        duration = max(1.2, min(8.0, len(part) / 7.0))
        lines.append({
            "id": len(lines) + 1,
            "index": len(lines) + 1,
            "start": round(cursor, 3),
            "end": round(cursor + duration, 3),
            "text": part,
            "deleted": False,
            "source": "transcript-text preprocessed",
        })
        cursor += duration
    return lines


def is_filler_or_fragment(text: str, duration: float) -> bool:
    norm = normalize_for_match(text)
    if not norm:
        return True
    if norm in {"嗯", "啊", "呃", "额", "然后", "这个", "就是", "对", "好"} and duration <= 2.5:
        return True
    return len(norm) <= 2 and duration <= 1.5


def mark_conservative_deletes(lines: list[dict[str, Any]]) -> None:
    previous: dict[str, Any] | None = None
    for line in lines:
        text = str(line.get("text") or "")
        duration = float(line.get("end", 0)) - float(line.get("start", 0))
        if is_filler_or_fragment(text, duration):
            line["deleted"] = True
            line["source"] = f"{line.get('source', '')} suggested-delete:filler".strip()
            continue

        if previous and not previous.get("deleted"):
            prev_text = normalize_for_match(str(previous.get("text") or ""))
            cur_text = normalize_for_match(text)
            gap = float(line.get("start", 0)) - float(previous.get("end", 0))
            if len(cur_text) >= 8 and gap <= 6:
                score = difflib.SequenceMatcher(None, prev_text, cur_text).ratio()
                if score >= 0.94:
                    previous["deleted"] = True
                    previous["source"] = f"{previous.get('source', '')} suggested-delete:repeat".strip()
        previous = line


def mark_reference_duplicate_takes(lines: list[dict[str, Any]]) -> None:
    groups: dict[int, list[dict[str, Any]]] = {}
    for line in lines:
        ref_index = line.get("_referenceIndex")
        if ref_index is None:
            continue
        text = normalize_for_match(str(line.get("text") or ""))
        if len(text) < 8:
            continue
        groups.setdefault(int(ref_index), []).append(line)

    for group in groups.values():
        if len(group) <= 1:
            continue
        keep = max(
            group,
            key=lambda line: (
                float(line.get("_referenceScore") or 0.0),
                float(line.get("start") or 0.0),
            ),
        )
        for line in group:
            if line is keep:
                continue
            line["deleted"] = True
            line["source"] = f"{line.get('source', '')} suggested-delete:reference-repeat".strip()


def should_split_review_text(text: str) -> bool:
    if len(cleanup_text(text)) > MAX_REVIEW_LINE_CHARS:
        return True
    soft_count = sum(text.count(ch) for ch in "，,、；;")
    return soft_count >= MAX_REVIEW_SOFT_PUNCT


def split_review_line(line: dict[str, Any]) -> list[dict[str, Any]]:
    text = cleanup_text(str(line.get("text") or ""))
    if not should_split_review_text(text):
        return [line]

    parts = [cleanup_text(part) for part in re.split(r"(?<=[，,、；;：:])", text)]
    parts = [part for part in parts if part]
    if len(parts) <= 1:
        return [line]

    start = float(line.get("start", 0.0))
    end = max(start + 0.05, float(line.get("end", start + 0.05)))
    span = end - start
    total_chars = sum(max(1, len(part)) for part in parts)
    cursor = start
    split_lines: list[dict[str, Any]] = []
    for part in parts:
        part_span = span * max(1, len(part)) / total_chars
        item = dict(line)
        item["start"] = round(cursor, 3)
        item["end"] = round(min(end, cursor + part_span), 3)
        item["text"] = part
        item["source"] = f"{item.get('source', '')} reference-split".strip()
        split_lines.append(item)
        cursor += part_span
    split_lines[-1]["end"] = round(end, 3)
    return split_lines


def split_long_review_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    split_lines: list[dict[str, Any]] = []
    for line in lines:
        split_lines.extend(split_review_line(line))
    return split_lines


def finalize_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    finalized: list[dict[str, Any]] = []
    for index, line in enumerate(lines, 1):
        item = dict(line)
        item.pop("_referenceIndex", None)
        item.pop("_referenceScore", None)
        item["id"] = index
        item["index"] = index
        finalized.append(item)
    return finalized


def preprocess_lines(lines: list[dict[str, Any]], reference_path: Path | None = None) -> list[dict[str, Any]]:
    reference_raw = reference_path.read_text() if reference_path and reference_path.exists() else ""
    rules = build_rules(reference_raw) if reference_raw else []
    reference_lines = split_reference_lines(reference_raw) if reference_raw else []

    processed: list[dict[str, Any]] = []
    for index, line in enumerate(lines, 1):
        item = dict(line)
        text = apply_rules(str(item.get("text") or ""), rules) if rules else cleanup_text(str(item.get("text") or ""))
        match, score, ref_index = best_reference_match(text, reference_lines)
        if match:
            text = cleanup_text(match)
            item["source"] = f"{item.get('source', '')} reference-match:{score:.2f}".strip()
            item["_referenceIndex"] = ref_index
            item["_referenceScore"] = score
        else:
            item["source"] = f"{item.get('source', '')} reference-normalized".strip()
        item["id"] = index
        item["index"] = index
        item["text"] = text
        processed.append(item)

    mark_conservative_deletes(processed)
    mark_reference_duplicate_takes(processed)
    return finalize_lines(split_long_review_lines(processed))


def main() -> int:
    parser = argparse.ArgumentParser(description="Preprocess Chinese ASR transcript into timed review lines.")
    parser.add_argument("--transcript-json", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    transcript = read_json(Path(args.transcript_json).expanduser().resolve())
    lines = preprocess_lines(lines_from_transcript_data(transcript), Path(args.reference).expanduser().resolve())
    Path(args.out).expanduser().resolve().write_text(json.dumps({"scriptLines": lines}, ensure_ascii=False, indent=2))
    print(json.dumps({"out": args.out, "scriptLines": len(lines)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
