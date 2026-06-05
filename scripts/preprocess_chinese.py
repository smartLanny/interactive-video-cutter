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
VISIBLE_SUBTITLE_PUNCT = "，,、；;：:。！？!?"
DECIMAL_DOT_TOKEN = "__DECIMAL_DOT__"
TECH_DOT_TOKEN = "__TECH_DOT__"
RATIO_COLON_TOKEN = "__RATIO_COLON__"
MAX_REVIEW_LINE_CHARS = 28
MAX_REVIEW_SOFT_PUNCT = 1
PREFERRED_REVIEW_LINE_CHARS = 18
DEFAULT_PAUSE_DELETE_THRESHOLD = 1.2
ASR_TIMESTAMP_GAP_REVIEW_THRESHOLD = 12.0
REPEATED_TAKE_SCORE_MARGIN = 0.04
REPEATED_TAKE_COMPLETENESS_MARGIN = 0.12
REFERENCE_HIGH_CONFIDENCE = 0.80
REFERENCE_MID_CONFIDENCE = 0.62
REFERENCE_MIN_RATIO = 0.36
REFERENCE_MAX_RATIO = 2.40
REFERENCE_ALIGN_WINDOW_SECONDS = 120.0
REFERENCE_ALIGN_MAX_START_UNITS = 220
REFERENCE_ALIGN_MAX_SPAN_UNITS = 150
REFERENCE_ALIGN_MIN_SCORE = 0.52
REFERENCE_ALIGN_HIGH_COVERAGE = 0.70
REFERENCE_ALIGN_MID_COVERAGE = 0.45
REVIEW_BREAK_MARKERS = [
    "那么",
    "接下来",
    "按照惯例",
    "模具方面",
    "接口方面",
    "辅助功能方面",
    "背光方面",
    "色彩方面",
    "色准方面",
    "HDR方面",
    "HDR 方面",
    "缺点方面",
    "整体来说",
    "另外",
    "不仅如此",
    "不过",
    "所以",
    "如果",
    "哪怕",
    "听说",
    "本频道",
    "感谢",
    "但是",
    "然后",
    "就是",
    "这里",
]
REFERENCE_UNIT_BREAK_MARKERS = [
    "不管你是",
    "这期视频",
    "用价值百万",
    "首先要",
    "再通过",
    "而背光越是纯净",
    "而索尼",
    "但索尼",
    "反观",
    "这其实是因为",
    "所以",
    "换句话说",
    "比如",
    "而对",
    "传统的色域测试",
    "可以看到",
    "解释一下",
    "而且",
    "暗部",
    "从实景片源",
    "在这几个分区",
    "并且",
    "再看",
    "实际",
]
PROTECTED_REVIEW_PATTERNS = [
    "TrueRGB",
    "RGB-MiniLED",
    "BT.2020",
    "QD-miniLED",
    "WOLED",
    "Judd Offset",
    "1931 2度 D65",
    "2015 10度",
    "HDMI2.1",
    "nits",
    "Hz",
]
ASR_ALIGNMENT_NORMALIZATIONS = [
    ("出发GB", "TrueRGB"),
    ("Mini拉", "miniLED"),
    ("MiniLight", "miniLED"),
    ("QDMiniLight", "QD-miniLED"),
    ("彩尖", "彩监"),
    ("彩渐", "彩监"),
    ("九七二代", "9系II"),
    ("九七2代", "9系II"),
    ("九系二代", "9系II"),
    ("九系2代", "9系II"),
    ("三十万", "30W"),
]
_ALIGNMENT_NORMALIZATION_CACHE: list[tuple[str, str]] | None = None
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


def is_reference_metadata_line(line: str) -> bool:
    if not line:
        return True
    if re.match(r"^<title>.*</title>$", line, re.I):
        return True
    return False


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
    return text.strip()


def normalize_common_display_terms(text: str) -> str:
    left = r"(?<![A-Za-z0-9])"
    right = r"(?![A-Za-z0-9])"
    text = re.sub(rf"{left}BT\s*\.?\s*2020{right}", "BT.2020", text, flags=re.I)
    text = re.sub(rf"{left}QD\s*[- ]?\s*Mini\s*(?:LED|Light)?{right}", "QD-miniLED", text, flags=re.I)
    text = re.sub(rf"{left}QDMini(?:LED|Light)?{right}", "QD-miniLED", text, flags=re.I)
    text = re.sub(rf"{left}QD-miniled{right}", "QD-miniLED", text, flags=re.I)
    text = re.sub(rf"{left}WOI{right}", "WOLED", text, flags=re.I)
    text = re.sub(rf"{left}LG\s*G\s*6{right}", "LG G6", text, flags=re.I)
    text = re.sub(rf"{left}HDMI\s*2\s*\.?\s*1{right}", "HDMI2.1", text, flags=re.I)
    text = re.sub(rf"{left}DP\s*1\s*\.?\s*4{right}", "DP1.4", text, flags=re.I)
    return text


def subtitle_display_text(text: str) -> str:
    text = cleanup_text(normalize_common_display_terms(text))
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r"(?<=\d)\.(?=\d)", DECIMAL_DOT_TOKEN, text)
    text = re.sub(r"(?<=[A-Za-z])\.(?=\d)", TECH_DOT_TOKEN, text)
    text = re.sub(r"(?<=\d):(?=\d)", RATIO_COLON_TOKEN, text)
    text = re.sub(rf"([A-Za-z0-9%])\s*[{re.escape(VISIBLE_SUBTITLE_PUNCT)}]\s*(?=[A-Za-z0-9])", r"\1 ", text)
    text = re.sub(rf"(?<=[A-Za-z0-9%])\s*[{re.escape(VISIBLE_SUBTITLE_PUNCT)}]\s*(?=[\u3400-\u9fff])", " ", text)
    text = re.sub(rf"(?<=[\u3400-\u9fff])\s*[{re.escape(VISIBLE_SUBTITLE_PUNCT)}]\s*(?=[A-Za-z0-9])", " ", text)
    text = re.sub(rf"\s*([{re.escape(VISIBLE_SUBTITLE_PUNCT)}])\s*", "", text)
    text = text.replace(".", "")
    text = text.replace(TECH_DOT_TOKEN, ".")
    text = text.replace(DECIMAL_DOT_TOKEN, ".")
    text = text.replace(RATIO_COLON_TOKEN, ":")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", text)
    return text


def split_long_reference_unit(text: str) -> list[str]:
    text = cleanup_text(text)
    for marker in ("而背光越是纯净",):
        pos = text.find(marker)
        if 14 <= pos <= len(text) - 8:
            return [
                part
                for item in (text[:pos], text[pos:])
                for part in split_long_reference_unit(cleanup_text(item))
                if part
            ]
    if len(text) <= 64:
        return [text] if text else []

    parts: list[str] = []
    rest = text
    while len(rest) > 64:
        candidates: list[int] = []
        for marker in REFERENCE_UNIT_BREAK_MARKERS:
            start = 8
            while True:
                pos = rest.find(marker, start)
                if pos < 0:
                    break
                if 14 <= pos <= min(len(rest) - 8, 72):
                    candidates.append(pos)
                start = pos + max(1, len(marker))
        if candidates:
            cut = min(candidates, key=lambda value: abs(value - 42))
        else:
            soft_positions = [m.end() for m in re.finditer(r"[，,；;]", rest[:73]) if m.end() >= 24]
            cut = soft_positions[-1] if soft_positions else 0
        if cut <= 0 or cut >= len(rest) - 6:
            break
        parts.append(cleanup_text(rest[:cut]))
        rest = cleanup_text(rest[cut:])
    if rest:
        parts.append(rest)
    return [part for part in parts if part]


def split_reference_units(raw: str) -> list[str]:
    lines: list[str] = []
    for raw_line in raw.splitlines():
        raw_line = raw_line.strip()
        if raw_line.startswith("#"):
            continue
        line = cleanup_markdown_line(raw_line)
        if not line or line.startswith("```") or is_reference_metadata_line(line):
            continue
        parts = [part.strip() for part in re.split(r"(?<=[。！？!?；;])", line) if part.strip()]
        for part in parts or [line]:
            part = cleanup_text(part)
            if not part:
                continue
            lines.extend(split_long_reference_unit(part))
    return lines


def split_reference_lines(raw: str) -> list[str]:
    return split_reference_units(raw)


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
        r"(?<![A-Za-z0-9])(?:DLSS|RTX|GTX|HDMI|DP|USB|PCIe|LPDDR|DDR|Wi-?Fi)\s*[A-Za-z0-9.+-]*(?:\s+(?:Ti|SUPER|Super|Max|Pro|Ultra|HX|HS|H|U|W|Wh|Hz|GB|TB|AI)){0,2}",
        r"(?<![A-Za-z0-9])Judd\s+Offset(?![A-Za-z0-9])",
        r"\d+(?:\.\d+){2,}",
        r"\d+(?:\.\d+)?(?::\d+(?:\.\d+)?)+",
        r"\d+(?:\.\d+)?\s*(?:W|Wh|Hz|GHz|MHz|GB|TB|MB|K|nits?|%|英寸|寸)",
        r"\d{2,5}(?:\s*(?:Ti|SUPER|Super|Max|Pro|Ultra|HX|HS|H|U))?",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, raw):
            value = cleanup_text(match.group(0))
            if re.fullmatch(r"\d+\s+[A-Za-z]", value):
                continue
            if re.fullmatch(r"(?i)Offset\s+\d+", value):
                continue
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
    text = normalize_common_display_terms(text)
    return cleanup_text(text)


def normalize_for_match(text: str) -> str:
    text = cleanup_text(text).lower()
    for digit, chars in CJK_DIGITS.items():
        for ch in chars:
            text = text.replace(ch, digit)
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", text)


def normalize_alignment_compact(text: str) -> str:
    global _ALIGNMENT_NORMALIZATION_CACHE
    if _ALIGNMENT_NORMALIZATION_CACHE is None:
        _ALIGNMENT_NORMALIZATION_CACHE = [
            (normalize_for_match(wrong), normalize_for_match(right))
            for wrong, right in ASR_ALIGNMENT_NORMALIZATIONS
        ]
    for wrong, right in _ALIGNMENT_NORMALIZATION_CACHE:
        text = text.replace(wrong, right)
    return text


def normalize_for_alignment(text: str) -> str:
    return normalize_alignment_compact(normalize_for_match(text))


SMALL_CJK_NUMBERS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def small_cjk_number_to_int(value: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    if value.isdigit():
        return int(value)
    if value == "十":
        return 10
    if "十" in value:
        left, _, right = value.partition("十")
        tens = SMALL_CJK_NUMBERS.get(left, 1 if not left else -1)
        ones = SMALL_CJK_NUMBERS.get(right, 0 if not right else -1)
        if tens >= 0 and ones >= 0:
            return tens * 10 + ones
    if all(ch in SMALL_CJK_NUMBERS for ch in value):
        return int("".join(str(SMALL_CJK_NUMBERS[ch]) for ch in value))
    return None


def normalize_spoken_numbers(text: str) -> str:
    def percent_repl(match: re.Match[str]) -> str:
        number = small_cjk_number_to_int(match.group(1))
        return str(number) if number is not None else match.group(0)

    text = re.sub(r"百分之([零〇一二两三四五六七八九十]{1,4})", percent_repl, text)
    return text


def normalize_for_semantic_review(text: str) -> str:
    return normalize_alignment_compact(normalize_for_match(normalize_spoken_numbers(text)))


def shared_char_ratio(left: str, right_chars: set[str]) -> float:
    if not left or not right_chars:
        return 0.0
    left_chars = set(left)
    return len(left_chars & right_chars) / len(right_chars)


def best_reference_candidate(text: str, reference_lines: list[str]) -> tuple[str | None, float, int | None]:
    norm_text = normalize_for_match(text)
    if len(norm_text) < 4:
        return None, 0.0, None
    best_line: str | None = None
    best_index: int | None = None
    best_score = 0.0
    text_len = len(norm_text)
    for ref_index, ref in enumerate(reference_lines):
        norm_ref = normalize_for_match(ref)
        if len(norm_ref) < 4:
            continue
        ratio = text_len / len(norm_ref)
        if ratio < REFERENCE_MIN_RATIO or ratio > REFERENCE_MAX_RATIO:
            continue
        score = difflib.SequenceMatcher(None, norm_text, norm_ref).ratio()
        if score > best_score:
            best_line = ref
            best_index = ref_index
            best_score = score
    return best_line, best_score, best_index


def best_reference_match(text: str, reference_lines: list[str]) -> tuple[str | None, float, int | None]:
    best_line, best_score, best_index = best_reference_candidate(text, reference_lines)
    if best_score >= REFERENCE_HIGH_CONFIDENCE:
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


def text_from_units(units: list[dict[str, Any]]) -> str:
    return cleanup_text("".join(str(unit.get("text") or "") for unit in units))


def make_unmatched_lines(units: list[dict[str, Any]], source: str = "qwen-words preprocessed reference-unmatched") -> list[dict[str, Any]]:
    lines = lines_from_word_units(units)
    for line in lines:
        line["source"] = source
        append_flag(line, "off-reference")
    return lines


def find_reference_span(
    units: list[dict[str, Any]],
    cursor: int,
    reference_text: str,
    max_start_units: int = REFERENCE_ALIGN_MAX_START_UNITS,
) -> dict[str, Any] | None:
    ref_norm = normalize_for_alignment(reference_text)
    if len(ref_norm) < 4 or cursor >= len(units):
        return None

    cursor_time = float(units[cursor].get("start", 0.0))
    max_start = min(len(units), cursor + max_start_units)
    candidates: list[dict[str, Any]] = []
    ref_chars = set(ref_norm)
    target_lengths = sorted({
        max(4, int(len(ref_norm) * ratio))
        for ratio in (0.52, 0.72, 0.92, 1.12, 1.42, 1.72)
    })
    for start_index in range(cursor, max_start):
        if float(units[start_index].get("start", 0.0)) - cursor_time > REFERENCE_ALIGN_WINDOW_SECONDS:
            break
        compact = ""
        target_index = 0
        for end_index in range(start_index, min(len(units), start_index + REFERENCE_ALIGN_MAX_SPAN_UNITS)):
            compact += str(units[end_index].get("_alignNorm") or normalize_for_match(str(units[end_index].get("text") or "")))
            norm_text = normalize_alignment_compact(compact)
            norm_len = len(norm_text)
            if norm_len < target_lengths[0]:
                continue
            should_score = False
            while target_index < len(target_lengths) and norm_len >= target_lengths[target_index]:
                should_score = True
                target_index += 1
            ratio = norm_len / len(ref_norm)
            if ratio > 1.95:
                break
            if ratio < 0.42 or not should_score:
                continue
            if shared_char_ratio(norm_text, ref_chars) < 0.46:
                continue
            score = difflib.SequenceMatcher(None, norm_text, ref_norm).quick_ratio()
            coverage = min(1.0, ratio)
            combined = score * 0.72 + coverage * 0.28
            candidates.append({
                "startIndex": start_index,
                "endIndex": end_index,
                "score": score,
                "coverage": coverage,
                "combinedScore": combined,
                "rawText": text_from_units(units[start_index : end_index + 1]),
                "normText": norm_text,
            })
            if len(candidates) > 24:
                candidates = sorted(
                    candidates,
                    key=lambda item: (item["combinedScore"], item["score"], item["coverage"]),
                    reverse=True,
                )[:12]
    if not candidates:
        return None
    best: dict[str, Any] | None = None
    for candidate in sorted(
        candidates,
        key=lambda item: (item["combinedScore"], item["score"], item["coverage"]),
        reverse=True,
    )[:12]:
        score = difflib.SequenceMatcher(None, str(candidate["normText"]), ref_norm).ratio()
        candidate["score"] = score
        candidate["combinedScore"] = score * 0.72 + candidate["coverage"] * 0.28
        if best is None or (candidate["combinedScore"], candidate["score"], candidate["coverage"]) > (
            best["combinedScore"],
            best["score"],
            best["coverage"],
        ):
            best = candidate
    if best is None:
        return None
    if best["score"] < REFERENCE_ALIGN_MIN_SCORE or best["coverage"] < REFERENCE_ALIGN_MID_COVERAGE:
        return None
    return best


def make_line(index: int, units: list[dict[str, Any]]) -> dict[str, Any]:
    text = subtitle_display_text("".join(str(unit["text"]) for unit in units))
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
        elif plain_len >= 22 and token and token[-1] in SOFT_PUNCT:
            should_cut = True
        elif plain_len >= MAX_REVIEW_LINE_CHARS:
            should_cut = True

        if should_cut and current_text:
            lines.append(make_line(len(lines) + 1, chunk))
            chunk = []
        previous_end = max(previous_end or float(unit["end"]), float(unit["end"]))

    if chunk:
        lines.append(make_line(len(lines) + 1, chunk))
    return [line for line in lines if line["text"]]


def preferred_split_index(text: str) -> int:
    if len(text) <= MAX_REVIEW_LINE_CHARS:
        return len(text)
    limit = min(len(text), MAX_REVIEW_LINE_CHARS)
    preferred = min(len(text), PREFERRED_REVIEW_LINE_CHARS)
    window = text[: limit + 1]
    punct_positions = [m.end() for m in re.finditer(r"[，,、；;：:。！？!?]", window) if m.end() >= 6]
    if punct_positions:
        return max(punct_positions)
    marker_positions = [window.find(marker) for marker in REVIEW_BREAK_MARKERS if 6 <= window.find(marker) <= limit]
    marker_positions = [pos for pos in marker_positions if pos >= 6]
    if marker_positions:
        return min(marker_positions, key=lambda pos: abs(pos - preferred))
    tail = window[:limit]
    particle_positions = [tail.rfind(ch) + 1 for ch in "的了啊吧吗呢" if tail.rfind(ch) >= 6]
    if particle_positions:
        return max(particle_positions)
    return limit


def split_text_for_review(text: str) -> list[str]:
    text = cleanup_text(text)
    if not text:
        return []
    seeds = [cleanup_text(part) for part in re.split(r"(?<=[，,、；;：:。！？!?])", text) if cleanup_text(part)]
    if not seeds:
        seeds = [text]
    parts: list[str] = []
    for seed in seeds:
        rest = seed
        while len(subtitle_display_text(rest)) > MAX_REVIEW_LINE_CHARS:
            cut = preferred_split_index(rest)
            if cut <= 0 or cut >= len(rest):
                break
            parts.append(rest[:cut])
            rest = cleanup_text(rest[cut:])
        if rest:
            parts.append(rest)
    return [part for part in (subtitle_display_text(part) for part in parts) if part]


def split_segment_text(text: str, start: float, end: float) -> list[dict[str, Any]]:
    parts = split_text_for_review(text)
    if not parts:
        parts = [subtitle_display_text(text)] if subtitle_display_text(text) else []
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


def compact_timing_units(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for unit in units:
        text = str(unit.get("text") or "")
        norm = normalize_for_alignment(text)
        if not norm:
            continue
        try:
            start = float(unit["start"])
            end = float(unit["end"])
        except (KeyError, TypeError, ValueError):
            continue
        compact.append({"text": text, "norm": norm, "start": start, "end": max(start + 0.01, end)})
    return compact


def best_timing_end_for_part(
    units: list[dict[str, Any]],
    start_index: int,
    part: str,
    min_remaining_units: int,
) -> int | None:
    part_norm = normalize_for_alignment(part)
    if not part_norm or start_index >= len(units):
        return None

    max_end = max(start_index, len(units) - min_remaining_units - 1)
    best: tuple[float, float, int] | None = None
    compact = ""
    for end_index in range(start_index, max_end + 1):
        compact += str(units[end_index]["norm"])
        if len(compact) < max(1, int(len(part_norm) * 0.35)):
            continue
        score = difflib.SequenceMatcher(None, compact, part_norm).ratio()
        length_score = 1.0 - min(
            1.0,
            abs(len(compact) - len(part_norm)) / max(1, len(compact), len(part_norm)),
        )
        combined = score * 0.78 + length_score * 0.22
        candidate = (combined, score, end_index)
        if best is None or candidate > best:
            best = candidate
        if len(compact) > len(part_norm) * 2.25 and score < 0.58:
            break
    if best is None or best[1] < 0.45:
        return None
    return best[2]


def unit_ranges_for_review_parts(
    raw_units: list[dict[str, Any]],
    parts: list[str],
) -> list[tuple[float, float]] | None:
    units = compact_timing_units(raw_units)
    if len(units) < len(parts) or len(parts) <= 1:
        return None

    cursor = 0
    ranges: list[tuple[float, float]] = []
    for part_index, part in enumerate(parts):
        if cursor >= len(units):
            return None
        if part_index == len(parts) - 1:
            end_index = len(units) - 1
        else:
            min_remaining_units = len(parts) - part_index - 1
            end_index = best_timing_end_for_part(units, cursor, part, min_remaining_units)
            if end_index is None:
                return None
        ranges.append((round(float(units[cursor]["start"]), 3), round(float(units[end_index]["end"]), 3)))
        cursor = end_index + 1
    return ranges


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
    parts = split_text_for_review(text)
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
        keep = choose_best_repeated_take(group)
        for line in group:
            if line is keep:
                continue
            line["deleted"] = True
            line["source"] = f"{line.get('source', '')} suggested-delete:reference-repeat".strip()


def append_source(line: dict[str, Any], marker: str) -> None:
    source = str(line.get("source") or "").strip()
    if marker not in source.split():
        line["source"] = f"{source} {marker}".strip()


def append_flag(line: dict[str, Any], flag: str) -> None:
    flags = list(line.get("qaFlags") or [])
    if flag not in flags:
        flags.append(flag)
    line["qaFlags"] = flags


def is_preroll_test_line(line: dict[str, Any]) -> bool:
    start = float(line.get("start") or 0.0)
    if start >= 40.0:
        return False
    norm = normalize_for_match(str(line.get("text") or ""))
    if not norm:
        return True
    markers = ["喂", "你好", "麦克风", "测试", "试音", "没办法", "垃圾"]
    return any(marker in norm for marker in markers)


def protected_review_flags(text: str, reference_terms: list[str]) -> list[str]:
    flags: list[str] = []
    if re.search(r"[A-Za-z0-9]", text):
        flags.append("protected-term-review")
    risky_patterns = [
        r"HDMI\s*21",
        r"DP\s*14",
        r"BT\s*2020",
        r"Mini\s*Light",
        r"WOI",
        r"彩尖",
        r"彩渐",
        r"出发\s*G\s*B",
        r"Mini\s*拉",
        r"九七二代",
        r"架的offset",
    ]
    if any(re.search(pattern, text, re.I) for pattern in risky_patterns):
        flags.append("protected-term-risk")
    norm_text = normalize_for_match(text)
    for term in reference_terms:
        if not re.search(r"[A-Za-z0-9]", term):
            continue
        norm_term = normalize_for_match(term)
        if len(norm_term) >= 5 and norm_term in norm_text:
            break
    else:
        if reference_terms and re.search(r"[A-Za-z0-9]", text):
            flags.append("term-not-in-reference")
    return flags


def take_quality_score(line: dict[str, Any]) -> tuple[float, float, float, float]:
    score = float(line.get("matchScore") or line.get("_referenceScore") or 0.0)
    text_len = len(normalize_for_match(str(line.get("text") or "")))
    duration = max(0.0, float(line.get("end", 0.0)) - float(line.get("start", 0.0)))
    start = float(line.get("start") or 0.0)
    return (score, min(text_len, 80) / 80.0, min(duration, 12.0) / 12.0, start)


def choose_best_repeated_take(group: list[dict[str, Any]]) -> dict[str, Any]:
    """Prefer the later take when quality is close; later reads are usually corrected."""
    best = max(group, key=take_quality_score)
    later = max(group, key=lambda line: float(line.get("start") or 0.0))
    if later is best:
        return best

    best_score, best_text, best_duration, _ = take_quality_score(best)
    later_score, later_text, later_duration, _ = take_quality_score(later)
    if (
        later_score >= best_score - REPEATED_TAKE_SCORE_MARGIN
        and later_text >= best_text - REPEATED_TAKE_COMPLETENESS_MARGIN
        and later_duration >= best_duration - REPEATED_TAKE_COMPLETENESS_MARGIN
    ):
        return later
    return best


def mark_reference_group_takes(lines: list[dict[str, Any]]) -> None:
    groups: dict[int, list[dict[str, Any]]] = {}
    for line in lines:
        ref_index = line.get("_referenceIndex")
        if ref_index is None:
            continue
        if line.get("lineType") == "pause":
            continue
        text = normalize_for_match(str(line.get("text") or ""))
        if len(text) < 4:
            continue
        groups.setdefault(int(ref_index), []).append(line)

    for group in groups.values():
        keep = choose_best_repeated_take(group)
        for line in group:
            if line is keep:
                line["takeRole"] = "primary"
                continue
            line["deleted"] = True
            line["takeRole"] = "alternate"
            append_flag(line, "duplicate-take")
            append_source(line, "suggested-delete:reference-repeat")


def line_ref_index(line: dict[str, Any]) -> int | None:
    ref_index = line.get("_referenceIndex", line.get("referenceIndex"))
    if ref_index is None:
        return None
    try:
        return int(ref_index)
    except (TypeError, ValueError):
        return None


def line_semantic_norm(line: dict[str, Any], prefer_reference: bool = False) -> str:
    text = ""
    if prefer_reference:
        text = str(line.get("referenceText") or "")
    if not text:
        text = str(line.get("text") or "")
    return normalize_for_semantic_review(text)


def semantic_prefix_score(fragment_norm: str, reference_norm: str) -> float:
    if not fragment_norm or not reference_norm:
        return 0.0
    if len(fragment_norm) < 4:
        return 0.0
    if reference_norm.startswith(fragment_norm):
        return 1.0
    window = reference_norm[: max(len(fragment_norm), min(len(reference_norm), len(fragment_norm) + 6))]
    if not window:
        return 0.0
    return difflib.SequenceMatcher(None, fragment_norm, window[: len(fragment_norm)]).ratio()


def mark_reference_false_starts(lines: list[dict[str, Any]]) -> None:
    for index, line in enumerate(lines):
        if line.get("deleted") or line.get("lineType") == "pause" or line_ref_index(line) is not None:
            continue
        norm = line_semantic_norm(line)
        if len(norm) < 4:
            continue
        start = float(line.get("start") or 0.0)
        end = float(line.get("end") or start)
        for next_line in lines[index + 1 : index + 10]:
            if line_ref_index(next_line) is None or next_line.get("deleted"):
                continue
            next_start = float(next_line.get("start") or 0.0)
            if next_start - end > 24.0:
                break
            reference_norm = line_semantic_norm(next_line, prefer_reference=True)
            score = semantic_prefix_score(norm, reference_norm)
            if score >= 0.82:
                line["deleted"] = True
                line["takeRole"] = line.get("takeRole") or "false-start"
                append_flag(line, "false-start")
                append_flag(line, "semantic-predelete")
                append_source(line, f"suggested-delete:false-start-prefix:{score:.2f}")
                break


def mark_nearby_duplicate_fragments(lines: list[dict[str, Any]]) -> None:
    for index, line in enumerate(lines):
        if line.get("deleted") or line.get("lineType") == "pause":
            continue
        if line_ref_index(line) is not None:
            continue
        text = str(line.get("text") or "")
        norm = line_semantic_norm(line)
        is_spoken_percent_tail = "百分之" in text and len(norm) >= 1
        if len(norm) < 3 and not is_spoken_percent_tail:
            continue
        start = float(line.get("start") or 0.0)
        for previous in reversed(lines[max(0, index - 5) : index]):
            if previous.get("deleted") or previous.get("lineType") == "pause":
                continue
            prev_end = float(previous.get("end") or previous.get("start") or 0.0)
            if start - prev_end > 5.0:
                break
            previous_norm = line_semantic_norm(previous)
            if not previous_norm:
                continue
            contained = norm in previous_norm or previous_norm.endswith(norm)
            if contained and (len(norm) >= 5 or is_spoken_percent_tail or "%" in text):
                line["deleted"] = True
                line["takeRole"] = line.get("takeRole") or "duplicate-fragment"
                append_flag(line, "duplicate-fragment")
                append_flag(line, "semantic-predelete")
                append_source(line, "suggested-delete:duplicate-fragment")
                break


def mark_semantic_predeletes(lines: list[dict[str, Any]]) -> None:
    mark_reference_false_starts(lines)
    mark_nearby_duplicate_fragments(lines)


def flag_long_asr_timestamp_gaps(
    lines: list[dict[str, Any]],
    threshold: float = ASR_TIMESTAMP_GAP_REVIEW_THRESHOLD,
) -> None:
    ordered = sorted(
        [line for line in lines if not line.get("deleted") and line.get("lineType") != "pause"],
        key=lambda item: (float(item.get("start", 0.0)), float(item.get("end", 0.0))),
    )
    previous: dict[str, Any] | None = None
    for line in ordered:
        if previous is not None:
            gap = float(line.get("start", 0.0)) - float(previous.get("end", previous.get("start", 0.0)))
            prev_norm = normalize_for_match(str(previous.get("text") or ""))
            cur_norm = normalize_for_match(str(line.get("text") or ""))
            if gap >= threshold and len(prev_norm) >= 4 and len(cur_norm) >= 4:
                for target in (previous, line):
                    append_flag(target, "needs_human")
                    append_flag(target, "needs-review")
                    append_flag(target, "ai-polish-focus")
                    append_flag(target, "asr-timestamp-gap")
                    append_source(target, f"review:asr-timestamp-gap:{gap:.1f}s")
        previous = line


def add_pause_lines(lines: list[dict[str, Any]], threshold: float = DEFAULT_PAUSE_DELETE_THRESHOLD) -> list[dict[str, Any]]:
    if threshold <= 0:
        return lines
    ordered = sorted(lines, key=lambda item: (float(item.get("start", 0)), float(item.get("end", 0))))
    result: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for line in ordered:
        if previous is not None:
            prev_end = float(previous.get("end", previous.get("start", 0)))
            start = float(line.get("start", 0))
            gap = start - prev_end
            if gap >= threshold:
                qa_flags = ["pause"]
                source = "suggested-delete:pause"
                previous_flags = set(previous.get("qaFlags") or [])
                line_flags = set(line.get("qaFlags") or [])
                if (
                    gap >= ASR_TIMESTAMP_GAP_REVIEW_THRESHOLD
                    and ("asr-timestamp-gap" in previous_flags or "asr-timestamp-gap" in line_flags)
                ):
                    qa_flags.extend(["needs_human", "needs-review", "ai-polish-focus", "asr-timestamp-gap"])
                    source = f"{source} review:asr-timestamp-gap:{gap:.1f}s"
                result.append({
                    "id": 0,
                    "index": 0,
                    "start": round(prev_end, 3),
                    "end": round(start, 3),
                    "text": f"停顿/气口 {gap:.1f} 秒",
                    "deleted": True,
                    "source": source,
                    "lineType": "pause",
                    "takeId": f"pause-{prev_end:.3f}-{start:.3f}",
                    "takeRole": "pause",
                    "qaFlags": qa_flags,
                })
        result.append(line)
        previous = line
    return result


def build_reference_review(
    lines: list[dict[str, Any]],
    reference_lines: list[str],
    reference_terms: list[str],
    mode: str = "reference-grouped",
    pause_threshold: float = DEFAULT_PAUSE_DELETE_THRESHOLD,
) -> dict[str, Any]:
    grouped: dict[int, dict[str, list[dict[str, Any]]]] = {}
    unmatched: list[dict[str, Any]] = []
    pauses: list[dict[str, Any]] = []
    for line in lines:
        summary = {
            "lineId": line.get("id"),
            "lineIds": [line.get("id")],
            "takeId": line.get("takeId") or f"line-{line.get('id')}",
            "index": line.get("index"),
            "start": line.get("start"),
            "end": line.get("end"),
            "text": line.get("text", ""),
            "deleted": bool(line.get("deleted")),
            "takeRole": line.get("takeRole", ""),
            "matchScore": line.get("matchScore", 0.0),
            "qaFlags": line.get("qaFlags") or [],
            "source": line.get("source", ""),
        }
        if line.get("lineType") == "pause":
            pauses.append(summary)
            continue
        ref_index = line.get("referenceIndex")
        if ref_index is None:
            if summary["qaFlags"] or not line.get("deleted"):
                unmatched.append(summary)
            continue
        grouped.setdefault(int(ref_index), {}).setdefault(str(summary["takeId"]), []).append(summary)

    groups: list[dict[str, Any]] = []
    for ref_index, take_parts in sorted(grouped.items()):
        takes: list[dict[str, Any]] = []
        for take_id, parts in sorted(
            take_parts.items(),
            key=lambda item: min(float(part.get("start") or 0.0) for part in item[1]),
        ):
            deleted = all(part.get("deleted") for part in parts)
            flags = sorted({flag for part in parts for flag in (part.get("qaFlags") or [])})
            takes.append({
                "takeId": take_id,
                "lineId": parts[0].get("lineId"),
                "lineIds": [part.get("lineId") for part in parts],
                "index": parts[0].get("index"),
                "start": min(float(part.get("start") or 0.0) for part in parts),
                "end": max(float(part.get("end") or 0.0) for part in parts),
                "text": "".join(str(part.get("text") or "") for part in parts),
                "deleted": deleted,
                "takeRole": parts[0].get("takeRole", ""),
                "matchScore": max(float(part.get("matchScore") or 0.0) for part in parts),
                "qaFlags": flags,
                "source": " ".join(sorted({str(part.get("source") or "") for part in parts if part.get("source")})),
            })
        default_keep = next((take["takeId"] for take in takes if not take["deleted"]), None)
        groups.append({
            "referenceIndex": ref_index,
            "referenceText": reference_lines[ref_index] if 0 <= ref_index < len(reference_lines) else "",
            "defaultKeepTakeId": default_keep,
            "defaultKeepLineId": next((take["lineId"] for take in takes if take["takeId"] == default_keep), None),
            "takes": takes,
        })

    qa_counts: dict[str, int] = {}
    for line in lines:
        for flag in line.get("qaFlags") or []:
            qa_counts[flag] = qa_counts.get(flag, 0) + 1

    return {
        "version": 1,
        "mode": mode,
        "pauseThreshold": pause_threshold,
        "referenceLines": reference_lines,
        "protectedTerms": reference_terms,
        "groups": groups,
        "unmatched": unmatched,
        "pauses": pauses,
        "qaSummary": {
            "groups": len(groups),
            "takes": sum(len(group["takes"]) for group in groups),
            "deleted": sum(1 for line in lines if line.get("deleted")),
            "pauses": len(pauses),
            "unmatched": len(unmatched),
            "flags": qa_counts,
        },
    }


SEMANTIC_REVIEW_RISK_FLAGS = {
    "off-reference",
    "needs-review",
    "protected-term-risk",
    "term-not-in-reference",
    "false-start",
    "duplicate-fragment",
    "duplicate-take",
    "bad-join",
    "bad-segmentation",
    "duplicate-prefix",
    "duplicate-tail",
    "pre-roll-test",
}


def semantic_line_summary(line: dict[str, Any]) -> dict[str, Any]:
    return {
        "lineId": line.get("id"),
        "index": line.get("index"),
        "start": line.get("start"),
        "end": line.get("end"),
        "text": line.get("text", ""),
        "deleted": bool(line.get("deleted")),
        "lineType": line.get("lineType", ""),
        "referenceIndex": line.get("referenceIndex"),
        "referenceText": line.get("referenceText", ""),
        "matchScore": line.get("matchScore", 0.0),
        "matchCoverage": line.get("matchCoverage", 0.0),
        "takeRole": line.get("takeRole", ""),
        "qaFlags": line.get("qaFlags") or [],
        "source": line.get("source", ""),
    }


def semantic_review_risks(line: dict[str, Any]) -> list[str]:
    flags = set(line.get("qaFlags") or [])
    risks = sorted(flags & SEMANTIC_REVIEW_RISK_FLAGS)
    if line.get("lineType") == "pause":
        duration = float(line.get("end") or 0.0) - float(line.get("start") or 0.0)
        if duration >= 3.0:
            risks.append("long-pause")
    if line.get("referenceIndex") is None and line.get("lineType") != "pause":
        risks.append("unmatched")
    score = float(line.get("matchScore") or 0.0)
    if line.get("referenceIndex") is not None and score and score < REFERENCE_HIGH_CONFIDENCE:
        risks.append("mid-confidence-reference")
    if line.get("deleted"):
        risks.append("suggested-delete")
    source = str(line.get("source") or "")
    if "suggested-delete" in source and line.get("lineType") != "pause":
        risks.append("predelete-source")
    return sorted(set(risks))


def reference_group_risks(group: dict[str, Any]) -> list[str]:
    risks: set[str] = set()
    takes = group.get("takes") or []
    if len(takes) > 1:
        risks.add("multi-take-reference-group")
    for take in takes:
        flags = set(take.get("qaFlags") or [])
        risks.update(flags & SEMANTIC_REVIEW_RISK_FLAGS)
        score = float(take.get("matchScore") or 0.0)
        if score and score < REFERENCE_HIGH_CONFIDENCE:
            risks.add("mid-confidence-reference")
        if take.get("deleted"):
            risks.add("suggested-delete")
        if "reference-near" in str(take.get("source") or ""):
            risks.add("reference-near")
    return sorted(risks)


def reference_group_line_ids(group: dict[str, Any]) -> set[int]:
    ids: set[int] = set()
    for take in group.get("takes") or []:
        for value in take.get("lineIds") or []:
            try:
                ids.add(int(value))
            except (TypeError, ValueError):
                continue
    return ids


def reference_group_context(lines: list[dict[str, Any]], line_ids: set[int], context_size: int) -> list[dict[str, Any]]:
    if not line_ids:
        return []
    indices = [
        index for index, line in enumerate(lines)
        if line.get("id") in line_ids
    ]
    if not indices:
        return []
    start = max(0, min(indices) - context_size)
    end = min(len(lines), max(indices) + context_size + 1)
    return [semantic_line_summary(item) for item in lines[start:end]]


def build_semantic_review_packets(
    lines: list[dict[str, Any]],
    reference_review: dict[str, Any] | None = None,
    context_size: int = 2,
) -> list[dict[str, Any]]:
    groups_by_ref = {
        int(group["referenceIndex"]): group
        for group in (reference_review or {}).get("groups", [])
        if group.get("referenceIndex") is not None
    }
    packets: list[dict[str, Any]] = []
    grouped_ref_indices: set[int] = set()
    for ref_index, group in sorted(groups_by_ref.items()):
        risks = reference_group_risks(group)
        if not risks:
            continue
        line_ids = reference_group_line_ids(group)
        grouped_ref_indices.add(ref_index)
        packets.append({
            "packetId": f"reference-{ref_index}",
            "packetType": "reference-group",
            "referenceIndex": ref_index,
            "lineIds": sorted(line_ids),
            "risks": risks,
            "reviewInstruction": (
                "Review this whole reference group as one semantic unit using chinese-subtitle rules. "
                "Prefer replace or replace_and_split when the spoken content clearly supports a text/segmentation fix. "
                "Default import applies high and medium replace/replace_and_split, but applies delete/restore only at high confidence. "
                "Do not add unspoken reference text; keep meaningful off-reference口播 but clean obvious ASR mistakes."
            ),
            "referenceGroup": group,
            "context": reference_group_context(lines, line_ids, context_size),
        })
    for index, line in enumerate(lines):
        risks = semantic_review_risks(line)
        if not risks:
            continue
        ref_index = line.get("referenceIndex")
        reference_group = None
        if ref_index is not None:
            try:
                ref_index_int = int(ref_index)
                if ref_index_int in grouped_ref_indices:
                    continue
                reference_group = groups_by_ref.get(ref_index_int)
            except (TypeError, ValueError):
                reference_group = None
        start = max(0, index - context_size)
        end = min(len(lines), index + context_size + 1)
        packets.append({
            "packetId": f"line-{line.get('id')}",
            "packetType": "line",
            "lineIds": [line.get("id")],
            "risks": risks,
            "reviewInstruction": (
                "Use chinese-subtitle rules. High confidence only for obvious false starts, duplicate takes, "
                "or delete/restore actions. Text replacement and re-splitting can use medium confidence "
                "when they preserve spoken content and fix ASR or segmentation. Use low/flag_only when ambiguous."
            ),
            "line": semantic_line_summary(line),
            "context": [semantic_line_summary(item) for item in lines[start:end]],
            "referenceGroup": reference_group,
        })
    return packets


def should_split_review_text(text: str) -> bool:
    if len(cleanup_text(text)) > MAX_REVIEW_LINE_CHARS:
        return True
    soft_count = sum(text.count(ch) for ch in "，,、；;")
    return soft_count >= MAX_REVIEW_SOFT_PUNCT


def split_review_line(line: dict[str, Any]) -> list[dict[str, Any]]:
    text = cleanup_text(str(line.get("text") or ""))
    parts = split_text_for_review(text)
    if not should_split_review_text(text) and len(parts) <= 1:
        line["text"] = subtitle_display_text(text)
        return [line]
    if len(parts) <= 1:
        line["text"] = subtitle_display_text(text)
        return [line]

    start = float(line.get("start", 0.0))
    end = max(start + 0.05, float(line.get("end", start + 0.05)))
    span = end - start
    total_chars = sum(max(1, len(part)) for part in parts)
    cursor = start
    timing_ranges = unit_ranges_for_review_parts(line.get("_timingUnits") or [], parts)
    split_lines: list[dict[str, Any]] = []
    for index, part in enumerate(parts):
        part_span = span * max(1, len(part)) / total_chars
        item = dict(line)
        if timing_ranges:
            item["start"], item["end"] = timing_ranges[index]
        else:
            item["start"] = round(cursor, 3)
            item["end"] = round(min(end, cursor + part_span), 3)
        item["text"] = part
        item["source"] = f"{item.get('source', '')} reference-split".strip()
        split_lines.append(item)
        cursor += part_span
    if not timing_ranges:
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
        item.pop("_timingUnits", None)
        item["id"] = index
        item["index"] = index
        finalized.append(item)
    return finalized


def reference_terms_for(reference_raw: str) -> list[str]:
    terms = set(extract_reference_terms(reference_raw))
    norm_raw = normalize_for_match(reference_raw)
    for term in PROTECTED_REVIEW_PATTERNS:
        if normalize_for_match(term) in norm_raw:
            terms.add(term)
    return sorted(terms, key=len, reverse=True)


def reference_aligned_lines_from_words(
    words: list[dict[str, Any]],
    reference_lines: list[str],
    reference_terms: list[str],
    rules: list[ReplaceRule],
) -> list[dict[str, Any]]:
    units = units_from_words(words)
    if not units:
        return []
    for unit in units:
        unit["_alignNorm"] = normalize_for_match(str(unit.get("text") or ""))

    lines: list[dict[str, Any]] = []
    cursor = 0
    take_index = 1
    consecutive_misses = 0
    for ref_index, reference_text in enumerate(reference_lines):
        max_start_units = min(
            REFERENCE_ALIGN_MAX_START_UNITS + consecutive_misses * 80,
            520,
        )
        span = find_reference_span(units, cursor, reference_text, max_start_units=max_start_units)
        if span is None:
            consecutive_misses += 1
            continue
        consecutive_misses = 0

        start_index = int(span["startIndex"])
        end_index = int(span["endIndex"])
        if start_index > cursor:
            for unmatched in make_unmatched_lines(units[cursor:start_index]):
                unmatched["takeId"] = f"unmatched-{take_index}"
                take_index += 1
                lines.append(unmatched)

        raw_text = apply_rules(str(span["rawText"]), rules) if rules else cleanup_text(str(span["rawText"]))
        score = float(span["score"])
        coverage = float(span["coverage"])
        high_confidence = score >= REFERENCE_HIGH_CONFIDENCE and coverage >= REFERENCE_ALIGN_HIGH_COVERAGE
        mid_confidence = score >= REFERENCE_MID_CONFIDENCE and coverage >= REFERENCE_ALIGN_MID_COVERAGE
        visible_text = cleanup_text(reference_text) if high_confidence else raw_text
        line = {
            "id": len(lines) + 1,
            "index": len(lines) + 1,
            "start": round(float(units[start_index]["start"]), 3),
            "end": round(float(units[end_index]["end"]), 3),
            "text": visible_text,
            "deleted": False,
            "source": (
                f"qwen-words reference-align:{score:.2f} coverage:{coverage:.2f} "
                + ("reference-match" if high_confidence else "reference-near")
            ).strip(),
            "_referenceIndex": ref_index,
            "_referenceScore": score,
            "referenceIndex": ref_index,
            "referenceText": cleanup_text(reference_text),
            "matchScore": round(score, 4),
            "matchCoverage": round(coverage, 4),
            "takeRole": "candidate" if mid_confidence else "low-confidence",
            "takeId": f"ref-{ref_index}-take-{take_index}",
            "_timingUnits": [
                {"text": unit["text"], "start": unit["start"], "end": unit["end"]}
                for unit in units[start_index : end_index + 1]
            ],
        }
        if not high_confidence:
            append_flag(line, "needs-review")
        for flag in protected_review_flags(line["text"], reference_terms):
            append_flag(line, flag)
        lines.append(line)
        take_index += 1
        cursor = max(cursor, end_index + 1)

    if cursor < len(units):
        for unmatched in make_unmatched_lines(units[cursor:]):
            unmatched["takeId"] = f"unmatched-{take_index}"
            take_index += 1
            lines.append(unmatched)
    return lines


def preprocess_transcript_payload(
    data: dict[str, Any],
    reference_path: Path | None = None,
    preprocess_mode: str = "reference-grouped",
    pause_threshold: float = DEFAULT_PAUSE_DELETE_THRESHOLD,
) -> dict[str, Any]:
    reference_raw = reference_path.read_text() if reference_path and reference_path.exists() else ""
    reference_lines = split_reference_units(reference_raw) if reference_raw else []
    grouped_mode = preprocess_mode == "reference-grouped" and bool(reference_lines)
    if not grouped_mode:
        return preprocess_payload(lines_from_transcript_data(data), reference_path, preprocess_mode, pause_threshold)

    rules = build_rules(reference_raw)
    reference_terms = reference_terms_for(reference_raw)
    processed = reference_aligned_lines_from_words(data.get("words") or [], reference_lines, reference_terms, rules)
    if not processed:
        return preprocess_payload(lines_from_transcript_data(data), reference_path, preprocess_mode, pause_threshold)

    mark_conservative_deletes(processed)
    mark_reference_group_takes(processed)
    mark_semantic_predeletes(processed)
    flag_long_asr_timestamp_gaps(processed)
    for line in processed:
        if line.get("_referenceIndex") is None and is_preroll_test_line(line):
            line["deleted"] = True
            line["takeRole"] = "preroll"
            append_flag(line, "pre-roll-test")
            append_source(line, "suggested-delete:preroll-test")

    with_pauses = add_pause_lines(processed, pause_threshold)
    finalized = finalize_lines(split_long_review_lines(with_pauses))
    return {
        "scriptLines": finalized,
        "referenceReview": build_reference_review(
            finalized,
            reference_lines,
            reference_terms,
            mode="reference-grouped",
            pause_threshold=pause_threshold,
        ),
    }


def preprocess_payload(
    lines: list[dict[str, Any]],
    reference_path: Path | None = None,
    preprocess_mode: str = "reference-grouped",
    pause_threshold: float = DEFAULT_PAUSE_DELETE_THRESHOLD,
) -> dict[str, Any]:
    reference_raw = reference_path.read_text() if reference_path and reference_path.exists() else ""
    rules = build_rules(reference_raw) if reference_raw else []
    reference_lines = split_reference_lines(reference_raw) if reference_raw else []
    reference_terms = reference_terms_for(reference_raw) if reference_raw else []
    grouped_mode = preprocess_mode == "reference-grouped" and bool(reference_lines)

    processed: list[dict[str, Any]] = []
    for index, line in enumerate(lines, 1):
        item = dict(line)
        text = apply_rules(str(item.get("text") or ""), rules) if rules else cleanup_text(str(item.get("text") or ""))
        match, score, ref_index = best_reference_candidate(text, reference_lines)
        if grouped_mode and match and ref_index is not None and score >= REFERENCE_HIGH_CONFIDENCE:
            text = cleanup_text(match)
            item["source"] = f"{item.get('source', '')} reference-match:{score:.2f}".strip()
            item["_referenceIndex"] = ref_index
            item["_referenceScore"] = score
            item["referenceIndex"] = ref_index
            item["referenceText"] = cleanup_text(match)
            item["matchScore"] = round(score, 4)
            item["takeRole"] = "candidate"
        elif grouped_mode and match and ref_index is not None and score >= REFERENCE_MID_CONFIDENCE:
            item["source"] = f"{item.get('source', '')} reference-near:{score:.2f}".strip()
            item["_referenceIndex"] = ref_index
            item["_referenceScore"] = score
            item["referenceIndex"] = ref_index
            item["referenceText"] = cleanup_text(match)
            item["matchScore"] = round(score, 4)
            item["takeRole"] = "candidate"
            append_flag(item, "needs-review")
        else:
            item["source"] = f"{item.get('source', '')} reference-normalized".strip()
            if grouped_mode:
                append_flag(item, "off-reference")
        item["id"] = index
        item["index"] = index
        item["takeId"] = f"take-{index}"
        item["text"] = cleanup_text(text)
        for flag in protected_review_flags(item["text"], reference_terms):
            append_flag(item, flag)
        if grouped_mode and item.get("_referenceIndex") is None and is_preroll_test_line(item):
            item["deleted"] = True
            item["takeRole"] = "preroll"
            append_flag(item, "pre-roll-test")
            append_source(item, "suggested-delete:preroll-test")
        processed.append(item)

    mark_conservative_deletes(processed)
    if grouped_mode:
        mark_reference_group_takes(processed)
        mark_semantic_predeletes(processed)
        flag_long_asr_timestamp_gaps(processed)
    else:
        mark_reference_duplicate_takes(processed)
    with_pauses = add_pause_lines(processed, pause_threshold if grouped_mode else 0.0)
    finalized = finalize_lines(split_long_review_lines(with_pauses))
    payload: dict[str, Any] = {"scriptLines": finalized}
    if grouped_mode:
        payload["referenceReview"] = build_reference_review(
            finalized,
            reference_lines,
            reference_terms,
            mode="reference-grouped",
            pause_threshold=pause_threshold,
        )
    return payload


def preprocess_lines(
    lines: list[dict[str, Any]],
    reference_path: Path | None = None,
    preprocess_mode: str = "flat",
    pause_threshold: float = DEFAULT_PAUSE_DELETE_THRESHOLD,
) -> list[dict[str, Any]]:
    return preprocess_payload(lines, reference_path, preprocess_mode, pause_threshold)["scriptLines"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Preprocess Chinese ASR transcript into timed review lines.")
    parser.add_argument("--transcript-json", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--preprocess-mode", choices=["reference-grouped", "flat"], default="reference-grouped")
    parser.add_argument("--pause-threshold", type=float, default=DEFAULT_PAUSE_DELETE_THRESHOLD)
    args = parser.parse_args()

    transcript = read_json(Path(args.transcript_json).expanduser().resolve())
    payload = preprocess_transcript_payload(
        transcript,
        Path(args.reference).expanduser().resolve(),
        args.preprocess_mode,
        args.pause_threshold,
    )
    Path(args.out).expanduser().resolve().write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(json.dumps({
        "out": args.out,
        "scriptLines": len(payload["scriptLines"]),
        "referenceGroups": len(payload.get("referenceReview", {}).get("groups", [])),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
