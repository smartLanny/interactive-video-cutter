#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import re
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
                    {"start": 0.0, "end": 0.3, "text": "第一句保留。"},
                    {"start": 1.6, "end": 1.8, "text": "第二句删除。"},
                ]
            },
            ensure_ascii=False,
        )
    )
    delete_csv.write_text(
        "start,end,duration,summary,reason,source\n"
        "1.6,1.8,0.2,第二句删除,smoke,smoke\n"
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


def load_server_module(root: Path):
    module_path = root / "assets" / "review_tool" / "interactive_review_server.py"
    spec = importlib.util.spec_from_file_location("interactive_review_server_smoke", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load interactive_review_server.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def require_file(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(f"expected output missing: {path}")
    if path.is_file() and path.stat().st_size <= 0:
        raise RuntimeError(f"expected non-empty output: {path}")


def fcpx_seconds(value: str) -> float:
    value = value.rstrip("s")
    if "/" in value:
        left, right = value.split("/", 1)
        return float(left) / float(right)
    return float(value)


def fcpxml_clip_ranges(path: Path) -> list[tuple[float, float]]:
    text = path.read_text()
    ranges: list[tuple[float, float]] = []
    for match in re.finditer(r'<asset-clip[^>]*start="([^"]+)"[^>]*duration="([^"]+)"', text):
        start = fcpx_seconds(match.group(1))
        duration = fcpx_seconds(match.group(2))
        ranges.append((round(start, 3), round(start + duration, 3)))
    return ranges


def fcpxml_timeline_ranges(path: Path) -> list[tuple[float, float]]:
    text = path.read_text()
    ranges: list[tuple[float, float]] = []
    for match in re.finditer(r'<asset-clip[^>]*offset="([^"]+)"[^>]*duration="([^"]+)"', text):
        start = fcpx_seconds(match.group(1))
        duration = fcpx_seconds(match.group(2))
        ranges.append((round(start, 3), round(start + duration, 3)))
    return ranges


def assert_preprocess_regressions(root: Path) -> None:
    module_path = root / "scripts" / "preprocess_chinese.py"
    spec = importlib.util.spec_from_file_location("preprocess_chinese_smoke", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load preprocess_chinese.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    cleaned = module.subtitle_display_text("USB4, HDMI2.1, DP1.4.")
    if cleaned != "USB4 HDMI2.1 DP1.4":
        raise RuntimeError(f"technical punctuation cleanup regression: {cleaned}")
    cleaned_bt = module.subtitle_display_text("BT.2020 一般能覆盖60%。")
    if cleaned_bt != "BT.2020 一般能覆盖60%":
        raise RuntimeError(f"BT.2020 punctuation cleanup regression: {cleaned_bt}")

    parts = module.split_text_for_review("第一段很多内容，第二段也很多，第三段继续，不要留普通句号。")
    if len(parts) < 3 or any("，" in part or "。" in part for part in parts):
        raise RuntimeError(f"review split regression: {parts}")

    repeated = [
        {
            "id": 101,
            "start": 10.0,
            "end": 12.0,
            "text": "这是一段重复口播的完整内容",
            "_referenceIndex": 1,
            "_referenceScore": 0.90,
            "matchScore": 0.90,
        },
        {
            "id": 102,
            "start": 18.0,
            "end": 20.0,
            "text": "这是一段重复口播的完整内容",
            "_referenceIndex": 1,
            "_referenceScore": 0.88,
            "matchScore": 0.88,
        },
    ]
    module.mark_reference_group_takes(repeated)
    if not repeated[0].get("deleted") or repeated[1].get("deleted") or repeated[1].get("takeRole") != "primary":
        raise RuntimeError(f"repeated take should prefer later close-quality take: {repeated}")

    later_more_complete = [
        {
            "id": 103,
            "start": 40.0,
            "end": 42.0,
            "text": "从配置上看这代的升级点有五个",
            "_referenceIndex": 2,
            "_referenceScore": 0.95,
            "matchScore": 0.95,
        },
        {
            "id": 104,
            "start": 45.0,
            "end": 50.0,
            "text": "从配置上看这代的升级点有五个五零七零钛版本呢起步就是六十",
            "_referenceIndex": 2,
            "_referenceScore": 0.76,
            "matchScore": 0.76,
        },
    ]
    module.mark_reference_group_takes(later_more_complete)
    if later_more_complete[0].get("deleted") is not True or later_more_complete[1].get("takeRole") != "primary":
        raise RuntimeError(f"later more complete take should beat shorter high-score take: {later_more_complete}")

    later_unmatched_repeat = [
        {
            "id": 111,
            "start": 160.0,
            "end": 162.0,
            "text": "其实这次散热的内部变化不大",
            "deleted": False,
            "_referenceIndex": 20,
            "referenceIndex": 20,
            "referenceText": "其实这次散热的内部变化不大依然是三风扇大面积均热板加液金的设计",
            "takeRole": "primary",
        },
        {
            "id": 112,
            "start": 162.0,
            "end": 163.0,
            "text": "依然是三风扇",
            "deleted": False,
            "_referenceIndex": 20,
            "referenceIndex": 20,
            "referenceText": "其实这次散热的内部变化不大依然是三风扇大面积均热板加液金的设计",
            "takeRole": "primary",
        },
        {
            "id": 113,
            "start": 163.0,
            "end": 165.0,
            "text": "大面积均热板加液金的设计",
            "deleted": False,
            "_referenceIndex": 20,
            "referenceIndex": 20,
            "referenceText": "其实这次散热的内部变化不大依然是三风扇大面积均热板加液金的设计",
            "takeRole": "primary",
        },
        {
            "id": 114,
            "start": 166.0,
            "end": 170.5,
            "text": "其实啊这次散热的内部变化不大依然是三风扇大面积均热板加液晶的设计",
            "deleted": False,
        },
    ]
    module.mark_semantic_predeletes(later_unmatched_repeat)
    if not all(line.get("deleted") for line in later_unmatched_repeat[:3]) or later_unmatched_repeat[3].get("deleted"):
        raise RuntimeError(f"later unmatched repeat should delete earlier take: {later_unmatched_repeat}")
    if "later-repeat-keep" not in later_unmatched_repeat[3].get("qaFlags", []):
        raise RuntimeError(f"later repeat keep was not flagged: {later_unmatched_repeat}")

    later_contains_previous = [
        {"id": 121, "start": 180.0, "end": 182.0, "text": "从配置上看这代的升级点有五个", "deleted": False},
        {"id": 122, "start": 183.0, "end": 188.0, "text": "从配置上看这代的升级点有五个五零七零钛版本呢起步就是六十", "deleted": False},
    ]
    module.mark_semantic_predeletes(later_contains_previous)
    if not later_contains_previous[0].get("deleted") or later_contains_previous[1].get("deleted"):
        raise RuntimeError(f"later containing repeat should delete shorter earlier take: {later_contains_previous}")

    short_sandwich = [
        {
            "id": 131,
            "start": 200.0,
            "end": 201.0,
            "text": "前面的场景会更复杂",
            "deleted": False,
            "_referenceIndex": 3,
            "referenceIndex": 3,
            "_referenceScore": 0.92,
            "matchScore": 0.92,
        },
        {
            "id": 132,
            "start": 201.1,
            "end": 201.4,
            "text": "场景更",
            "deleted": False,
            "_referenceIndex": 3,
            "referenceIndex": 3,
            "_referenceScore": 0.74,
            "matchScore": 0.74,
        },
        {
            "id": 133,
            "start": 201.5,
            "end": 202.4,
            "text": "接近真实游戏负载",
            "deleted": False,
            "_referenceIndex": 4,
            "referenceIndex": 4,
            "_referenceScore": 0.91,
            "matchScore": 0.91,
        },
    ]
    module.mark_reference_group_takes(short_sandwich)
    if short_sandwich[1].get("deleted"):
        raise RuntimeError(f"short sandwiched fragment should not be auto-deleted: {short_sandwich}")
    if module.is_filler_or_fragment("接近", 0.3):
        raise RuntimeError("semantic two-character fragment was treated as filler")

    gap_lines = [
        {"id": 201, "start": 0.0, "end": 2.0, "text": "前面一段有效口播", "deleted": False},
        {"id": 202, "start": 24.5, "end": 27.0, "text": "后面一段有效口播", "deleted": False},
    ]
    module.flag_long_asr_timestamp_gaps(gap_lines)
    gap_with_pause = module.add_pause_lines(gap_lines, 1.2)
    pause = next((line for line in gap_with_pause if line.get("lineType") == "pause"), None)
    if not pause or "asr-timestamp-gap" not in pause.get("qaFlags", []):
        raise RuntimeError(f"long ASR timestamp gap was not flagged on pause line: {gap_with_pause}")
    if any("asr-timestamp-gap" not in line.get("qaFlags", []) for line in gap_lines):
        raise RuntimeError(f"long ASR timestamp gap was not flagged on neighbor lines: {gap_lines}")

    reference_units = module.split_reference_units(
        "为了让你知道这台电视有多好，我们找来了索尼30W彩监，旗舰WOLED电视，以及传统QD-miniLED，"
        "用价值百万的仪器实测，不管你是持币待购，还是说就看个热闹，这期视频都干货拉满，包你看爽。\n"
    )
    if "旗舰WOLED电视" in reference_units or "以及传统QD-miniLED" in reference_units:
        raise RuntimeError(f"reference semantic unit was comma-fragmented: {reference_units}")

    def timed_words(text: str, start: float = 0.0, step: float = 0.08) -> list[dict[str, Any]]:
        tokens = re.findall(r"[A-Za-z0-9.%-]+|[\u3400-\u9fff]", text)
        words: list[dict[str, Any]] = []
        cursor = start
        for token in tokens:
            words.append({"text": token, "start": round(cursor, 3), "end": round(cursor + step, 3)})
            cursor += step
        return words

    with tempfile.TemporaryDirectory(prefix="interactive-video-cutter-preprocess.") as tmp:
        ref_path = Path(tmp) / "reference.txt"
        ref_path.write_text(
            "为了让你知道这台电视有多好，我们找来了索尼30W彩监，旗舰WOLED电视，以及传统QD-miniLED。\n"
            "所有的miniLED电视，不论RGB、QD，无一例外，都是用液晶调控背光的明暗。\n"
        )
        words = (
            timed_words("为了让你知道这台电视有多好我们找来了索尼的三十万彩尖旗舰WOLED电视以及传统的QDMiniLED", 0.0)
            + timed_words("所有的Mini拉的电视不管RGBQD无一例外都是用液晶调控背光的明暗", 10.0)
            + timed_words("这是后面亮度最强的MiniLED电视不要误匹配到前面的所有miniLED电视", 60.0)
        )
        payload = module.preprocess_transcript_payload(
            {"words": words},
            ref_path,
            "reference-grouped",
            1.2,
        )
        joined = " ".join(str(line.get("text") or "") for line in payload["scriptLines"])
        if "30W彩监" not in joined or "QD-miniLED" not in joined or "彩尖" in joined:
            raise RuntimeError(f"reference-first term correction regression: {joined}")
        first_group = next(
            group for group in payload["referenceReview"]["groups"]
            if group["referenceIndex"] == 1
        )
        first_take = first_group["takes"][0]
        if float(first_take["start"]) >= 20.0:
            raise RuntimeError(f"reference alignment jumped to later similar text: {first_take}")

        split_ref_path = Path(tmp) / "reference_split.txt"
        split_ref_path.write_text(
            "这次的散热也有升级，实测手动模式双烤做到了165W，比上一代提升25W。\n"
        )
        split_words = (
            timed_words("这次散热也有升级", 30.0, 0.05)
            + timed_words("实测手动模式双烤做到了165W", 35.0, 0.05)
            + timed_words("比上一代提升25W", 38.0, 0.05)
        )
        split_payload = module.preprocess_transcript_payload(
            {"words": split_words},
            split_ref_path,
            "reference-grouped",
            1.2,
        )
        split_lines = [
            line for line in split_payload["scriptLines"]
            if line.get("source", "").find("reference-split") >= 0
        ]
        split_starts = [round(float(line["start"]), 2) for line in split_lines[:3]]
        if split_starts != [30.0, 35.0, 38.0]:
            raise RuntimeError(f"reference split did not preserve word timing: {split_lines[:3]}")


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


def assert_server_export_options_and_waveform(root: Path, manifest: Path, export_dir: Path) -> None:
    module = load_server_module(root)
    module.load_projects(manifest)
    project = module.get_project("smoke-video")

    path, peaks = module.waveform_peaks(project, "", 24)
    if project.draft_media and path != project.draft_media:
        raise RuntimeError(f"waveform should prefer draft audio proxy for video projects: {path}")
    if len(peaks) != 120:
        raise RuntimeError(f"waveform bins were not clamped to the minimum: {len(peaks)}")
    for bucket in peaks:
        for key in ("min", "max", "trace"):
            value = bucket.get(key)
            if not isinstance(value, (int, float)) or not -1.0 <= value <= 1.0:
                raise RuntimeError(f"waveform peak out of range: {bucket}")
        if bucket["min"] > bucket["max"]:
            raise RuntimeError(f"waveform min/max inverted: {bucket}")

    first_dir = module.allocate_unique_child_dir(export_dir, "timestamp-smoke")
    second_dir = module.allocate_unique_child_dir(export_dir, "timestamp-smoke")
    if first_dir.name != "timestamp-smoke" or second_dir.name != "timestamp-smoke-2":
        raise RuntimeError(f"timestamped export directory names were not sequential: {first_dir}, {second_dir}")

    try:
        module.export_options_from_payload(project, {"outputDir": "../outside"})
    except ValueError:
        pass
    else:
        raise RuntimeError("export outputDir outside exportDir was not rejected")

    cli_proc = run([
        sys.executable,
        str(root / "scripts" / "export_davinci_timeline.py"),
        "--manifest",
        str(manifest),
        "--project",
        "smoke-video",
        "--output-dir",
        "ui-cli",
        "--naming-prefix",
        "cli",
    ])
    cli_result = parse_json_output(cli_proc)
    cli_dir = export_dir / "ui-cli"
    if Path(cli_result.get("outputDir", "")).resolve() != cli_dir.resolve():
        raise RuntimeError(f"CLI export outputDir mismatch: {cli_result}")
    if cli_result.get("namingPrefix") != "cli-":
        raise RuntimeError(f"CLI export naming prefix mismatch: {cli_result}")
    require_file(cli_dir / "cli-review_state.json")
    require_file(cli_dir / "cli-davinci_timeline.fcpxml")


def assert_script_line_regression(root: Path, manifest: Path, state_path: Path, export_dir: Path) -> None:
    state = {
        "selectedDeletes": {},
        "deleteNotes": {},
        "cues": [],
        "scriptLines": [
            {"id": 1, "index": 1, "start": 0.0, "end": 0.5, "text": "USB4, HDMI2.1, DP1.4.", "deleted": False, "source": "smoke"},
            {"id": 2, "index": 2, "start": 0.5, "end": 1.0, "text": "", "deleted": True, "source": "manual-delete"},
            {"id": 3, "index": 3, "start": 1.0, "end": 1.8, "text": "RTX 5070 Ti，140W，DLSS 4.5.", "deleted": False, "source": "smoke"},
            {"id": 4, "index": 4, "start": 1.8, "end": 2.0, "text": "中置信删除待人工确认，QD-miniled，LGG6，WOI，只有大约 2000:1。", "deleted": False, "source": "smoke"},
            {"id": 5, "index": 5, "start": 2.0, "end": 2.6, "text": "从配置上看", "deleted": False, "source": "smoke"},
            {"id": 6, "index": 6, "start": 2.6, "end": 3.4, "text": "这代的升级点有五个", "deleted": False, "source": "smoke"},
            {"id": 7, "index": 7, "start": 3.4, "end": 6.0, "text": "从配置上看这代的升级点有五个五零七零钛版本呢起步就是六十", "deleted": False, "source": "smoke"},
            {"id": 8, "index": 8, "start": 6.0, "end": 7.0, "text": "5070 Ti 版本起步就是 64GB 内存", "deleted": False, "source": "smoke"},
            {"id": 9, "index": 9, "start": 7.0, "end": 8.2, "text": "很少有 CPU 多核满载的高功耗场景", "deleted": False, "source": "smoke"},
            {"id": 10, "index": 10, "start": 8.2, "end": 8.5, "text": "场景更", "deleted": False, "source": "smoke"},
            {"id": 11, "index": 11, "start": 8.5, "end": 9.2, "text": "最重要的还是能效表现", "deleted": False, "source": "smoke"},
        ],
        "useScriptLines": True,
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    suggestions_path = state_path.parent / "semantic_review_suggestions.json"
    suggestions_path.write_text(json.dumps([
        {
            "lineIds": [1],
            "action": "replace",
            "confidence": "high",
            "reason": "smoke protected punctuation",
            "text": "USB4 HDMI2.1 DP1.4 BT.2020",
            "qaFlags": ["semantic-llm-review", "protected-term-review"],
        },
        {
            "lineIds": [3],
            "action": "replace",
            "confidence": "medium",
            "reason": "smoke medium text repair",
            "text": "RTX 5070 Ti 140W DLSS 4.5 BT.2020",
            "qaFlags": ["semantic-llm-review", "protected-term-review"],
        },
        {
            "lineIds": [4],
            "action": "delete",
            "confidence": "medium",
            "reason": "smoke pending only",
            "qaFlags": ["semantic-llm-review"],
        },
        {
            "lineIds": [7],
            "action": "delete",
            "confidence": "high",
            "reason": "smoke should not keep early shorter take over later complete take",
            "qaFlags": ["semantic-llm-review"],
        },
        {
            "lineIds": [10],
            "action": "delete",
            "confidence": "high",
            "reason": "smoke short middle fragment should remain reviewable",
            "qaFlags": ["semantic-llm-review"],
        },
    ], ensure_ascii=False, indent=2))
    run([
        sys.executable,
        str(root / "scripts" / "apply_semantic_review_suggestions.py"),
        "--state",
        str(state_path),
        "--suggestions",
        str(suggestions_path),
    ])
    applied_state = json.loads(state_path.read_text())
    if applied_state["scriptLines"][0]["text"] != "USB4 HDMI2.1 DP1.4 BT.2020":
        raise RuntimeError("high-confidence semantic suggestion was not applied")
    if applied_state["scriptLines"][2]["text"] != "RTX 5070 Ti 140W DLSS 4.5 BT.2020":
        raise RuntimeError("medium-confidence text repair was not applied")
    if applied_state["scriptLines"][3].get("deleted"):
        raise RuntimeError("medium-confidence delete suggestion was auto-applied")
    if "llm-suggestion-pending" not in applied_state["scriptLines"][3].get("qaFlags", []):
        raise RuntimeError("medium-confidence delete suggestion was not flagged pending")
    if applied_state["scriptLines"][3]["text"] != "中置信删除待人工确认 QD-miniLED LG G6 WOLED 只有大约 2000:1":
        raise RuntimeError("existing line technical cleanup regression")
    lines_by_id = {int(line["id"]): line for line in applied_state["scriptLines"]}
    if lines_by_id[7].get("deleted"):
        raise RuntimeError("later more complete take was auto-deleted by LLM suggestion")
    if "keep-after-review" not in lines_by_id[7].get("qaFlags", []):
        raise RuntimeError("later more complete take was not flagged for keep-after review")
    if lines_by_id[10].get("deleted"):
        raise RuntimeError("short middle fragment was auto-deleted by LLM suggestion")
    if "keep-after-review" not in lines_by_id[10].get("qaFlags", []):
        raise RuntimeError("short middle fragment was not flagged for keep-after review")

    run([
        sys.executable,
        str(root / "scripts" / "export_davinci_timeline.py"),
        "--manifest",
        str(manifest),
        "--project",
        "smoke-video",
    ])

    ranges = fcpxml_clip_ranges(export_dir / "davinci_timeline.fcpxml")
    if ranges != [(0.0, 0.5), (1.0, 2.0)]:
        raise RuntimeError(f"FCPXML did not cut blank deleted line: {ranges}")
    timeline_ranges = fcpxml_timeline_ranges(export_dir / "davinci_timeline.fcpxml")
    if timeline_ranges != [(0.0, 0.5), (0.5, 1.5)]:
        raise RuntimeError(f"FCPXML output timeline is not continuous: {timeline_ranges}")

    delete_csv = (export_dir / "script_delete_intervals.csv").read_text()
    if "手动删除" not in delete_csv:
        raise RuntimeError("blank deleted line did not get manual delete summary")

    selected_srt = (export_dir / "script_selected_timeline.srt").read_text()
    expected_text = [
        "USB4 HDMI2.1 DP1.4 BT.2020",
        "RTX 5070 Ti 140W DLSS 4.5 BT.2020",
        "中置信删除待人工确认 QD-miniLED LG G6 WOLED 只有大约 2000:1",
    ]
    if any(text not in selected_srt for text in expected_text):
        raise RuntimeError(f"SRT technical text cleanup regression: {selected_srt}")
    if "DP1.4." in selected_srt or "DLSS 4.5." in selected_srt or "，" in selected_srt or "。" in selected_srt:
        raise RuntimeError(f"SRT punctuation cleanup regression: {selected_srt}")

    units_jsonl = (export_dir / "script_selected_text_time_alignment.units.jsonl").read_text()
    if any(text not in units_jsonl for text in ["HDMI2.1", "DLSS", "4.5"]):
        raise RuntimeError("alignment sidecar did not preserve cleaned technical units")


def smoke(root: Path, keep_temp: bool) -> dict[str, Any]:
    require_command("ffmpeg")
    require_command("ffprobe")
    assert_preprocess_regressions(root)
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
        reference_report = Path(create_result["referenceReviewReport"])
        semantic_packets = Path(create_result["semanticReviewPackets"])
        audio_proxy = Path(create_result["audioProxy"])
        if create_result.get("asrContext"):
            raise RuntimeError("ASR context should be opt-in, not enabled by default")
        if (review_dir / "edit" / "asr_context.txt").exists():
            raise RuntimeError("default project creation unexpectedly wrote asr_context.txt")
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
        require_file(reference_report)
        require_file(semantic_packets)
        packed_text = takes_packed.read_text()
        if "第一句保留" not in packed_text or "第二句删除" not in packed_text:
            raise RuntimeError("packed transcript did not include expected transcript phrases")
        report_text = reference_report.read_text()
        if "Reference Review Report" not in report_text:
            raise RuntimeError("reference review report was not generated")
        packet_lines = [line for line in semantic_packets.read_text().splitlines() if line.strip()]
        if not packet_lines:
            raise RuntimeError("semantic review packets were not generated")
        state_data = json.loads(Path(create_result["state"]).read_text())
        reference_review = state_data.get("referenceReview") or {}
        if not reference_review.get("groups"):
            raise RuntimeError("reference-grouped preprocess did not create groups")
        if not any(line.get("lineType") == "pause" and line.get("deleted") for line in state_data.get("scriptLines", [])):
            raise RuntimeError("pause/breath line was not pre-marked for deletion")

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
        assert_script_line_regression(root, manifest, Path(create_result["state"]), export_dir)
        assert_server_export_options_and_waveform(root, manifest, export_dir)
        summary = {
            "ok": True,
            "tempDir": str(tmp_root),
            "manifest": str(manifest),
            "state": create_result.get("state"),
            "audioProxy": str(audio_proxy),
            "takesPacked": str(takes_packed),
            "referenceReviewReport": str(reference_report),
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
