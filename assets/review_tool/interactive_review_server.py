#!/usr/bin/env python3
from __future__ import annotations

import argparse
import array
import csv
import hashlib
import json
import mimetypes
import os
import re
import secrets
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse


ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST = ROOT / "interactive_review_manifest.json"
APP_HTML = ROOT / "interactive_review_app.html"
END_PUNCT = set("。！？!?；;")
SOFT_PUNCT = set("，,、")
VISIBLE_SUBTITLE_PUNCT = "，,、；;：:。！？!?"
DECIMAL_DOT_TOKEN = "__DECIMAL_DOT__"
TECH_DOT_TOKEN = "__TECH_DOT__"
RATIO_COLON_TOKEN = "__RATIO_COLON__"
LOCK_TTL_SECONDS = 75
RENDER_AUDIO_FADE_SECONDS = 0.03
PROJECTS: dict[str, "ProjectConfig"] = {}
DEFAULT_PROJECT_ID = ""
MANIFEST_PATH = DEFAULT_MANIFEST
SERVER_MUTEX = threading.RLock()
WAVEFORM_CACHE: dict[tuple[str, str, int, str, float, float], list[dict[str, float]]] = {}


@dataclass(frozen=True)
class ProjectConfig:
    id: str
    title: str
    media_type: str
    duration: float
    source_media: Path
    draft_media: Path | None
    draft_srt: Path
    delete_csv: Path
    alignment_json: Path
    transcript_json: Path
    state_path: Path
    export_dir: Path
    source_video: Path | None = None
    preview_video: Path | None = None
    preview_media: Path | None = None
    source_label: str = ""
    source_fps: float | None = None
    source_timecode_start: str = "00:00:00:00"
    davinci_media_path: str = ""
    handoff_notes: str = ""


@dataclass(frozen=True)
class ExportOptions:
    output_dir: Path
    naming_prefix: str = ""


class RevisionConflict(RuntimeError):
    def __init__(self, current_revision: str) -> None:
        super().__init__("revision conflict")
        self.current_revision = current_revision


class LockConflict(RuntimeError):
    def __init__(self, lock: dict) -> None:
        super().__init__("project locked")
        self.lock = lock


def default_manifest() -> dict:
    return {"defaultProject": "", "allowedRoots": [str(ROOT)], "projects": []}


def resolve_config_path(value: str, base: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def is_within(path: Path, root: Path) -> bool:
    path = path.resolve()
    root = root.resolve()
    return path == root or root in path.parents


def require_allowed_path(path: Path | None, allowed_roots: list[Path]) -> Path | None:
    if path is None:
        return None
    resolved = path.resolve()
    if any(is_within(resolved, root) for root in allowed_roots):
        return resolved
    raise ValueError(f"path is outside allowed roots: {resolved}")


def make_project(item: dict, manifest_dir: Path, allowed_roots: list[Path]) -> ProjectConfig:
    def path_field(name: str, required: bool = True) -> Path | None:
        value = item.get(name)
        if value in (None, ""):
            if required:
                raise ValueError(f"project {item.get('id', '<missing>')} missing {name}")
            return None
        return require_allowed_path(resolve_config_path(str(value), manifest_dir), allowed_roots)

    media_type = str(item.get("mediaType", "audio")).lower()
    if media_type not in {"audio", "video"}:
        raise ValueError(f"unsupported mediaType for {item.get('id')}: {media_type}")
    return ProjectConfig(
        id=str(item["id"]),
        title=str(item.get("title") or item["id"]),
        media_type=media_type,
        duration=float(item["duration"]),
        source_media=path_field("sourceMedia"),
        draft_media=path_field("draftMedia", required=False),
        draft_srt=path_field("draftSrt"),
        delete_csv=path_field("deleteCsv"),
        alignment_json=path_field("alignmentJson"),
        transcript_json=path_field("transcriptJson"),
        state_path=path_field("statePath"),
        export_dir=path_field("exportDir"),
        source_video=path_field("sourceVideo", required=False),
        preview_video=path_field("previewVideo", required=False),
        preview_media=path_field("previewMedia", required=False),
        source_label=str(item.get("sourceLabel") or item.get("title") or item["id"]),
        source_fps=float(item["sourceFps"]) if item.get("sourceFps") not in (None, "") else None,
        source_timecode_start=str(item.get("sourceTimecodeStart") or "00:00:00:00"),
        davinci_media_path=str(item.get("davinciMediaPath") or item.get("sourceMedia") or ""),
        handoff_notes=str(item.get("handoffNotes") or ""),
    )


def load_projects(manifest_path: Path) -> None:
    global PROJECTS, DEFAULT_PROJECT_ID, MANIFEST_PATH
    MANIFEST_PATH = manifest_path
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
    else:
        raise FileNotFoundError(
            f"manifest not found: {manifest_path}. "
            "Create one with scripts/create_review_project.py and start with --manifest."
        )
    manifest_dir = manifest_path.resolve().parent
    allowed_roots = [
        resolve_config_path(str(value), manifest_dir)
        for value in manifest.get("allowedRoots", [str(ROOT)])
    ]
    allowed_roots = [root.resolve() for root in allowed_roots]
    projects = {
        project.id: project
        for project in (
            make_project(item, manifest_dir, allowed_roots)
            for item in manifest.get("projects", [])
        )
    }
    if not projects:
        raise ValueError("manifest has no projects")
    DEFAULT_PROJECT_ID = str(manifest.get("defaultProject") or next(iter(projects)))
    if DEFAULT_PROJECT_ID not in projects:
        DEFAULT_PROJECT_ID = next(iter(projects))
    PROJECTS = projects


def get_project(project_id: str | None = None) -> ProjectConfig:
    project_id = project_id or DEFAULT_PROJECT_ID
    if project_id not in PROJECTS:
        raise KeyError(project_id)
    return PROJECTS[project_id]


def project_from_query(parsed) -> ProjectConfig:
    query = parse_qs(parsed.query)
    project_id = query.get("project", [DEFAULT_PROJECT_ID])[0]
    return get_project(project_id)


def file_revision(path: Path) -> str:
    if not path.exists():
        return "missing"
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()[:16]
    return f"{path.stat().st_mtime_ns}:{digest}"


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    tmp.replace(path)


def write_json_atomic(path: Path, payload: dict | list) -> None:
    write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2))


def safe_name_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    cleaned = cleaned.strip(".-")
    return cleaned[:80]


def safe_prefix_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())[:80]


def allocate_unique_child_dir(parent: Path, name: str) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    for index in range(1, 1000):
        suffix = "" if index == 1 else f"-{index}"
        candidate = parent / f"{name}{suffix}"
        try:
            candidate.mkdir(exist_ok=False)
            return candidate
        except FileExistsError:
            continue
    raise RuntimeError(f"could not allocate export directory under {parent}")


def export_url(project: ProjectConfig, path: Path) -> str | None:
    resolved = path.resolve()
    export_dir = project.export_dir.resolve()
    if not is_within(resolved, export_dir):
        return None
    rel = resolved.relative_to(export_dir).as_posix()
    return f"/exports/{project.id}/{quote(rel)}"


def export_file(options: ExportOptions, name: str) -> Path:
    return options.output_dir / f"{options.naming_prefix}{name}"


def default_export_options(project: ProjectConfig) -> ExportOptions:
    return ExportOptions(project.export_dir)


def export_options_from_payload(project: ProjectConfig, payload: dict) -> ExportOptions:
    output_dir = project.export_dir
    raw_output_dir = str(payload.get("outputDir") or "").strip()
    timestamped = bool(payload.get("timestamped") or payload.get("timestampedExport"))
    auto_output_dir = False
    if raw_output_dir and timestamped and raw_output_dir.lower() not in {"auto", "timestamp", "timestamped"}:
        raise ValueError("timestamped export cannot be combined with an explicit outputDir; use outputDir=auto")
    if raw_output_dir:
        if raw_output_dir.lower() in {"auto", "timestamp", "timestamped"}:
            timestamped = True
            auto_output_dir = True
        else:
            candidate = Path(raw_output_dir).expanduser()
            if not candidate.is_absolute():
                candidate = project.export_dir / candidate
            output_dir = candidate.resolve()
            if not is_within(output_dir, project.export_dir):
                raise ValueError(f"outputDir must stay under project exportDir: {project.export_dir}")
    if timestamped and (auto_output_dir or not raw_output_dir):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base = safe_name_part(f"{project.id}-{stamp}") or stamp
        output_dir = allocate_unique_child_dir(project.export_dir, base).resolve()
    raw_prefix = str(payload.get("namingPrefix") or payload.get("prefix") or "").strip()
    naming_prefix = safe_prefix_part(raw_prefix)
    if raw_prefix and naming_prefix != raw_prefix:
        raise ValueError("namingPrefix may only contain letters, numbers, dot, underscore, and hyphen")
    if naming_prefix and not naming_prefix.endswith(("-", "_", ".")):
        naming_prefix = f"{naming_prefix}-"
    return ExportOptions(output_dir=output_dir, naming_prefix=naming_prefix)


def ensure_no_conflict(project: ProjectConfig, base_revision: str | None, force: bool = False) -> None:
    current_revision = file_revision(project.state_path)
    if force or not base_revision:
        return
    if str(base_revision) != current_revision:
        raise RevisionConflict(current_revision)


def save_state(project: ProjectConfig, state: dict, base_revision: str | None = None, force: bool = False) -> str:
    with SERVER_MUTEX:
        ensure_no_conflict(project, base_revision, force=force)
        write_json_atomic(project.state_path, state)
        return file_revision(project.state_path)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def lock_path_for(project: ProjectConfig) -> Path:
    return project.export_dir / ".review_lock.json"


def read_lock(project: ProjectConfig) -> dict | None:
    path = lock_path_for(project)
    if not path.exists():
        return None
    try:
        lock = json.loads(path.read_text())
    except Exception:
        return None
    expires_at = parse_iso(lock.get("expiresAt"))
    lock["expired"] = bool(expires_at and expires_at <= now_utc())
    return lock


def active_lock(project: ProjectConfig) -> dict | None:
    lock = read_lock(project)
    if not lock or lock.get("expired"):
        return None
    return lock


def public_lock(lock: dict | None, include_token: bool = False) -> dict | None:
    if not lock:
        return None
    data = dict(lock)
    if not include_token:
        data.pop("token", None)
    return data


def write_lock(project: ProjectConfig, owner_id: str, owner_name: str, tab_id: str = "") -> dict:
    project.export_dir.mkdir(parents=True, exist_ok=True)
    now = now_utc()
    lock = {
        "projectId": project.id,
        "token": secrets.token_urlsafe(24),
        "ownerId": owner_id,
        "ownerName": owner_name or owner_id,
        "tabId": tab_id,
        "acquiredAt": iso(now),
        "updatedAt": iso(now),
        "expiresAt": iso(now + timedelta(seconds=LOCK_TTL_SECONDS)),
        "ttlSeconds": LOCK_TTL_SECONDS,
    }
    write_json_atomic(lock_path_for(project), lock)
    return lock


def refresh_lock(project: ProjectConfig, lock: dict) -> dict:
    now = now_utc()
    lock = dict(lock)
    lock["updatedAt"] = iso(now)
    lock["expiresAt"] = iso(now + timedelta(seconds=LOCK_TTL_SECONDS))
    lock["ttlSeconds"] = LOCK_TTL_SECONDS
    write_json_atomic(lock_path_for(project), lock)
    return lock


def lock_status_payload(project: ProjectConfig, token: str | None = None, owner_id: str | None = None) -> dict:
    lock = read_lock(project)
    active = bool(lock and not lock.get("expired"))
    held_by_me = bool(active and (
        (token and lock.get("token") == token)
        or (not token and owner_id and lock.get("ownerId") == owner_id)
    ))
    return {
        "ok": True,
        "projectId": project.id,
        "locked": active,
        "heldByMe": held_by_me,
        "lock": public_lock(lock, include_token=held_by_me) if active else None,
        "expiredLock": public_lock(lock) if lock and lock.get("expired") else None,
        "ttlSeconds": LOCK_TTL_SECONDS,
    }


def require_lock_owner(payload: dict) -> tuple[str, str, str]:
    owner_id = str(payload.get("ownerId") or "").strip()
    owner_name = str(payload.get("ownerName") or owner_id).strip()
    tab_id = str(payload.get("tabId") or "").strip()
    if not owner_id:
        raise ValueError("missing ownerId")
    return owner_id, owner_name, tab_id


def ensure_write_lock(project: ProjectConfig, lock_token: str | None) -> None:
    lock = active_lock(project)
    if lock and lock.get("token") != lock_token:
        raise LockConflict(public_lock(lock) or {})


def media_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".mp4", ".mov", ".m4v"}:
        return "video"
    return "audio"


def media_items(project: ProjectConfig) -> list[dict]:
    draft_is_audio = bool(project.draft_media and media_type_for(project.draft_media) == "audio")
    if project.media_type == "video" and draft_is_audio:
        items = []
    else:
        items = [{
            "id": "source",
            "label": "原始视频" if project.media_type == "video" else "原始音频",
            "type": project.media_type,
            "url": f"/media/{project.id}/source",
        }]
    if project.draft_media:
        items.append({
            "id": "draft",
            "label": "草案视频" if media_type_for(project.draft_media) == "video" else "审阅音频",
            "type": media_type_for(project.draft_media),
            "url": f"/media/{project.id}/draft",
        })
    if project.source_video:
        items.append({
            "id": "source-video",
            "label": "原始视频",
            "type": "video",
            "url": f"/media/{project.id}/source-video",
        })
    if project.preview_video:
        items.append({
            "id": "preview-video",
            "label": "预览视频",
            "type": "video",
            "url": f"/media/{project.id}/preview-video",
        })
    if project.preview_media:
        items.append({
            "id": "preview-media",
            "label": "预览视频" if media_type_for(project.preview_media) == "video" else "预览音频",
            "type": media_type_for(project.preview_media),
            "url": f"/media/{project.id}/preview-media",
        })
    return items


def media_path_for(project: ProjectConfig, kind: str) -> Path | None:
    if kind in {"source", "original", "original-audio"}:
        return project.source_media
    if kind in {"draft", "draft-audio"}:
        return project.draft_media
    if kind == "source-video":
        return project.source_video or (project.source_media if project.media_type == "video" else None)
    if kind == "preview-video":
        return project.preview_video
    if kind == "preview-media":
        return project.preview_media
    return None


def waveform_media_for(project: ProjectConfig, kind: str = "") -> Path | None:
    if kind:
        return media_path_for(project, kind)
    return project.draft_media or project.source_media


def waveform_cache_key(project: ProjectConfig, path: Path, bins: int, start: float, end: float) -> tuple[str, str, int, str, float, float]:
    stat = path.stat()
    revision = f"{stat.st_mtime_ns}:{stat.st_size}"
    return (project.id, str(path), bins, revision, round(start, 3), round(end, 3))


def build_waveform_peaks(path: Path, bins: int, start: float = 0.0, end: float = 0.0) -> list[dict[str, float]]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    if start > 0:
        command += ["-ss", f"{start:.3f}"]
    command += [
        "-i",
        str(path),
    ]
    if end > start:
        command += ["-t", f"{end - start:.3f}"]
    command += [
        "-vn",
        "-ac",
        "1",
        "-ar",
        "8000",
        "-f",
        "s16le",
        "pipe:1",
    ]
    proc = subprocess.run(command, check=True, capture_output=True, timeout=120)
    raw = proc.stdout
    if len(raw) < 2:
        return []
    if len(raw) % 2:
        raw = raw[:-1]
    samples = array.array("h")
    samples.frombytes(raw)
    if sys.byteorder != "little":
        samples.byteswap()
    total = len(samples)
    if total <= 0:
        return []

    buckets: list[tuple[int, int, float]] = []
    max_abs = 0
    for index in range(bins):
        start = int(index * total / bins)
        end = int((index + 1) * total / bins)
        if end <= start:
            end = min(total, start + 1)
        window = samples[start:end]
        if not window:
            buckets.append((0, 0, 0.0))
            continue
        low = min(window)
        high = max(window)
        max_abs = max(max_abs, abs(low), abs(high))
        center = (start + end) // 2
        trace_start = max(start, center - 8)
        trace_end = min(end, trace_start + 16)
        trace_window = samples[trace_start:trace_end] or window[:1]
        trace = sum(trace_window) / max(1, len(trace_window))
        buckets.append((low, high, trace))

    scale = max(max_abs, 1)
    return [
        {
            "min": max(-1.0, min(1.0, low / scale)),
            "max": max(-1.0, min(1.0, high / scale)),
            "trace": max(-1.0, min(1.0, trace / scale)),
        }
        for low, high, trace in buckets
    ]


def waveform_peaks(project: ProjectConfig, kind: str, bins: int, start: float = 0.0, end: float = 0.0) -> tuple[Path, list[dict[str, float]], float, float]:
    path = waveform_media_for(project, kind)
    if not path:
        raise FileNotFoundError("waveform media not found")
    start = max(0.0, float(start or 0.0))
    end = max(0.0, float(end or 0.0))
    if end <= start:
        start = 0.0
        end = 0.0
    bins = max(120, min(2400, bins))
    key = waveform_cache_key(project, path, bins, start, end)
    with SERVER_MUTEX:
        cached = WAVEFORM_CACHE.get(key)
    if cached is not None:
        return path, cached, start, end
    peaks = build_waveform_peaks(path, bins, start, end)
    with SERVER_MUTEX:
        WAVEFORM_CACHE[key] = peaks
    return path, peaks, start, end


def parse_srt(path: Path) -> list[dict]:
    text = path.read_text()
    blocks = re.split(r"\n\s*\n", text.strip())
    cues: list[dict] = []
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3:
            continue
        timing = lines[1]
        if "-->" not in timing:
            continue
        start_raw, end_raw = [x.strip() for x in timing.split("-->", 1)]
        cues.append({
            "id": len(cues) + 1,
            "index": int(lines[0]) if lines[0].isdigit() else len(cues) + 1,
            "start": srt_time_to_seconds(start_raw),
            "end": srt_time_to_seconds(end_raw),
            "text": "\n".join(lines[2:]).strip(),
        })
    return cues


def srt_time_to_seconds(value: str) -> float:
    hms, ms = value.split(",", 1)
    h, m, s = [int(x) for x in hms.split(":")]
    return h * 3600 + m * 60 + s + int(ms) / 1000


def seconds_to_srt(value: float) -> str:
    value = max(0.0, value)
    total_ms = int(round(value * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def load_deletes(project: ProjectConfig) -> list[dict]:
    rows: list[dict] = []
    with project.delete_csv.open() as f:
        for row in csv.DictReader(f):
            start = float(row["start"])
            end = float(row["end"])
            rows.append({
                "id": len(rows) + 1,
                "start": start,
                "end": end,
                "duration": end - start,
                "summary": row.get("summary", ""),
                "reason": row.get("reason", ""),
                "source": row.get("source", ""),
            })
    return rows


def default_state(project: ProjectConfig) -> dict:
    deletes = load_deletes(project)
    return {
        "selectedDeletes": {str(row["id"]): True for row in deletes},
        "deleteNotes": {},
        "cues": parse_srt(project.draft_srt),
        "scriptLines": build_script_lines(project, deletes),
        "useScriptLines": True,
        "referenceReview": None,
        "updatedAt": None,
    }


def load_state(project: ProjectConfig) -> dict:
    state = default_state(project)
    if project.state_path.exists():
        try:
            saved = json.loads(project.state_path.read_text())
            state["selectedDeletes"].update(saved.get("selectedDeletes", {}))
            state["deleteNotes"].update(saved.get("deleteNotes", {}))
            if saved.get("cues"):
                state["cues"] = saved["cues"]
            if saved.get("scriptLines"):
                state["scriptLines"] = saved["scriptLines"]
            if "referenceReview" in saved:
                state["referenceReview"] = saved.get("referenceReview")
            state["useScriptLines"] = saved.get("useScriptLines", state.get("useScriptLines", True))
            state["updatedAt"] = saved.get("updatedAt")
        except Exception:
            pass
    return state


def text_unit(char: str) -> bool:
    return bool(char.strip()) and char.isalnum()


def clean_line_text(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"\s+([，。！？；、,.!?;])", r"\1", value)
    value = re.sub(r"(?<=\d)\.(?=\d)", DECIMAL_DOT_TOKEN, value)
    value = re.sub(r"(?<=[A-Za-z])\.(?=\d)", TECH_DOT_TOKEN, value)
    value = re.sub(r"(?<=\d):(?=\d)", RATIO_COLON_TOKEN, value)
    value = re.sub(rf"([A-Za-z0-9%])\s*[{re.escape(VISIBLE_SUBTITLE_PUNCT)}]\s*(?=[A-Za-z0-9])", r"\1 ", value)
    value = re.sub(rf"\s*([{re.escape(VISIBLE_SUBTITLE_PUNCT)}])\s*", "", value)
    value = value.replace(".", "")
    value = value.replace(TECH_DOT_TOKEN, ".")
    value = value.replace(DECIMAL_DOT_TOKEN, ".")
    value = value.replace(RATIO_COLON_TOKEN, ":")
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", value)
    return value


def clean_generated_text(value: str) -> str:
    value = str(value or "")
    replacements = {
        "六幺八": "618",
        "五零六零": "5060",
        "五零七零钛": "5070Ti",
        "五零七零": "5070",
        "五零八零": "5080",
        "五幺二": "512",
        "三A二C": "3A2C",
        "一百一十五瓦": "115W",
        "一百六十瓦": "160W",
        "二十五瓦": "25W",
        "五十瓦": "50W",
        "六十五瓦": "65W",
        "二点五K": "2.5K",
        "二点五 k": "2.5K",
        "二点八K": "2.8K",
        "三点二K": "3.2K",
        "一百二十赫兹": "120Hz",
        "一百六十赫兹": "160Hz",
        "两百四十赫兹": "240Hz",
        "六十赫兹": "60Hz",
        "七八四零H": "7840H",
        "一TB": "1TB",
        "CS二": "CS2",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    value = re.sub(r"DLSS\s*4\.5", "DLSS4.5", value, flags=re.I)
    value = re.sub(r"RTX\s*(\d{4})(\s*Ti)?", lambda m: f"RTX {m.group(1)}{'Ti' if m.group(2) else ''}", value, flags=re.I)
    value = re.sub(r"(\d)\s*A\s*(\d)\s*C", r"\1A\2C", value)
    value = re.sub(r"(\d(?:\.\d)?)\s*K(?!g)", r"\1K", value, flags=re.I)
    value = re.sub(r"(\d(?:\.\d+)?)\s*KG\b", r"\1kg", value, flags=re.I)
    value = re.sub(r"(?<!\d)\.(?!\d)", " ", value)
    value = re.sub(r"([A-Za-z0-9])RTX", r"\1 RTX", value)
    value = re.sub(r"([A-Za-z0-9])Ultra", r"\1 Ultra", value)
    value = re.sub(r"([A-Za-z0-9])P3", r"\1 P3", value)
    value = re.sub(r"([A-Za-z0-9])OLED", r"\1 OLED", value)
    value = re.sub(r"([A-Za-z0-9])AI\b", r"\1 AI", value)
    value = re.sub(r"nits\s*100%", "nits 100%", value, flags=re.I)
    value = value.replace("5070 对比 60", "5070 对比 5060")
    value = value.replace("雷神的AIBook", "雷神 AIBook")
    value = value.replace("雷神AIBook", "雷神 AIBook")
    value = re.sub(r"([\u4e00-\u9fff])([A-Za-z0-9])", r"\1 \2", value)
    value = re.sub(r"([A-Za-z0-9%])([\u4e00-\u9fff])", r"\1 \2", value)
    value = re.sub(r"战\s*7000\s*P", "战7000P", value, flags=re.I)
    value = re.sub(r"战\s*7000", "战7000", value, flags=re.I)
    value = re.sub(r"Y\s*7000\s*X", "Y7000X", value, flags=re.I)
    value = re.sub(r"雷电\s*5", "雷电5", value)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"[“”‘’\"，。！？；、,!?;：:]+", " ", value)
    value = re.sub(r"\.{2,}|…+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def transcript_tokens(project: ProjectConfig) -> list[dict]:
    data = json.loads(project.transcript_json.read_text())
    timed_chars: list[dict] = []
    for word in data.get("words", []):
        if word.get("type") != "word":
            continue
        chars = [ch for ch in str(word.get("text", "")) if ch.strip()]
        if not chars:
            continue
        start = float(word["start"])
        end = max(start, float(word["end"]))
        step = (end - start) / max(1, len(chars))
        for i, ch in enumerate(chars):
            timed_chars.append({
                "text": ch,
                "start": start + step * i,
                "end": start + step * (i + 1),
                "timed": True,
            })

    tokens: list[dict] = []
    cursor = 0
    last_end = 0.0
    for ch in str(data.get("text", "")):
        if not ch.strip():
            continue
        if text_unit(ch):
            if cursor >= len(timed_chars):
                continue
            token = dict(timed_chars[cursor])
            token["text"] = ch
            tokens.append(token)
            last_end = token["end"]
            cursor += 1
        else:
            tokens.append({"text": ch, "start": last_end, "end": last_end, "timed": False})
    return tokens


def tokens_in_window(tokens: list[dict], start: float, end: float) -> list[dict]:
    selected: list[dict] = []
    for token in tokens:
        if token["timed"]:
            mid = (float(token["start"]) + float(token["end"])) / 2
            if start <= mid < end:
                selected.append(token)
        elif selected and start <= float(token["start"]) <= end:
            selected.append(token)
    return selected


def make_script_line(line_id: int, start: float, end: float, text: str, deleted: bool, source: str = "") -> dict:
    return {
        "id": line_id,
        "index": line_id,
        "start": round(float(start), 3),
        "end": round(max(float(start) + 0.05, float(end)), 3),
        "text": clean_line_text(text),
        "deleted": bool(deleted),
        "source": source,
    }


def append_text_lines(lines: list[dict], tokens: list[dict], line_id: int, deleted: bool = False, source: str = "") -> int:
    current: list[dict] = []

    def commit() -> None:
        if not current:
            return
        text = clean_line_text("".join(token["text"] for token in current))
        timed = [token for token in current if token["timed"]]
        current.clear()
        if not text or not timed:
            return
        lines.append(make_script_line(
            len(lines) + 1,
            timed[0]["start"],
            timed[-1]["end"],
            text,
            deleted,
            source,
        ))

    for token in tokens:
        current.append(token)
        text_len = len(clean_line_text("".join(item["text"] for item in current)))
        if token["text"] in END_PUNCT:
            commit()
        elif token["text"] in SOFT_PUNCT and text_len >= 34:
            commit()
        elif text_len >= 56:
            commit()
    commit()
    return line_id


def clean_kept_script_lines(project: ProjectConfig) -> list[dict]:
    if not project.alignment_json.exists():
        return []
    data = json.loads(project.alignment_json.read_text())
    segments = data.get("segments", [])
    lines: list[dict] = []
    for cue in data.get("cues", []):
        text = clean_generated_text(cue.get("text", ""))
        if not text:
            continue
        start = output_to_source_time_for_start(float(cue["start"]), segments)
        end = output_to_source_time_for_end(float(cue["end"]), segments)
        lines.append(make_script_line(len(lines) + 1, start, end, text, False))
    return lines


def clean_delete_text(delete: dict, tokens: list[dict]) -> str:
    summary = clean_generated_text(delete.get("summary", ""))
    actual = clean_generated_text("".join(token["text"] for token in tokens_in_window(tokens, float(delete["start"]), float(delete["end"]))))
    if summary and (not actual or len(actual) > 80 or actual.endswith(("这", "不", "的", "和", "但"))):
        return summary
    return actual or summary or "长停顿"


def clean_script_lines_from_alignment(project: ProjectConfig, deletes: list[dict]) -> list[dict]:
    kept = clean_kept_script_lines(project)
    if not kept:
        return []
    tokens = transcript_tokens(project)
    lines: list[dict] = list(kept)
    for delete in deletes:
        lines.append(make_script_line(
            len(lines) + 1,
            float(delete["start"]),
            float(delete["end"]),
            clean_delete_text(delete, tokens),
            True,
            f"delete#{delete['id']} {delete.get('summary', '')}".strip(),
        ))
    lines = avoid_deleted_overlaps(lines)
    lines.sort(key=lambda line: (float(line["start"]), 0 if line.get("deleted") else 1, float(line["end"])))
    for i, line in enumerate(lines, 1):
        line["id"] = i
        line["index"] = i
    return lines


def avoid_deleted_overlaps(lines: list[dict]) -> list[dict]:
    deletes = [
        (float(line["start"]), float(line["end"]))
        for line in lines
        if line.get("deleted")
    ]
    cleaned: list[dict] = []
    for line in lines:
        if line.get("deleted"):
            cleaned.append(line)
            continue
        portions = [(float(line["start"]), float(line["end"]))]
        for d_start, d_end in deletes:
            next_portions: list[tuple[float, float]] = []
            for start, end in portions:
                if end <= d_start + 0.02 or start >= d_end - 0.02:
                    next_portions.append((start, end))
                    continue
                if start < d_start - 0.05:
                    next_portions.append((start, d_start))
                if end > d_end + 0.05:
                    next_portions.append((d_end, end))
            portions = next_portions
            if not portions:
                break
        if not portions:
            continue
        start, end = max(portions, key=lambda item: item[1] - item[0])
        if end <= start + 0.05:
            continue
        updated = dict(line)
        updated["start"] = round(start, 3)
        updated["end"] = round(end, 3)
        cleaned.append(updated)
    return cleaned


def build_script_lines(project: ProjectConfig, deletes: list[dict] | None = None) -> list[dict]:
    deletes = deletes or load_deletes(project)
    clean_lines = clean_script_lines_from_alignment(project, deletes)
    if clean_lines:
        return clean_lines

    tokens = transcript_tokens(project)
    lines: list[dict] = []
    cursor = 0.0
    for delete in sorted(deletes, key=lambda item: (item["start"], item["end"])):
        start = float(delete["start"])
        end = float(delete["end"])
        if start > cursor:
            append_text_lines(lines, tokens_in_window(tokens, cursor, start), len(lines) + 1)
        deleted_tokens = tokens_in_window(tokens, start, end)
        text = clean_line_text("".join(token["text"] for token in deleted_tokens))
        if not text:
            text = f"（{delete.get('summary') or '长停顿'}）"
        lines.append(make_script_line(
            len(lines) + 1,
            start,
            end,
            text,
            True,
            f"delete#{delete['id']} {delete.get('summary', '')}".strip(),
        ))
        cursor = max(cursor, end)
    if cursor < project.duration:
        append_text_lines(lines, tokens_in_window(tokens, cursor, project.duration + 1), len(lines) + 1)
    for i, line in enumerate(lines, 1):
        line["index"] = i
    return lines


def write_srt(cues: list[dict], path: Path) -> None:
    lines: list[str] = []
    for i, cue in enumerate(sorted(cues, key=lambda c: (float(c["start"]), float(c["end"]))), 1):
        text = clean_line_text(cue.get("text", ""))
        if not text:
            continue
        start = float(cue["start"])
        end = max(start + 0.05, float(cue["end"]))
        lines.extend([str(i), f"{seconds_to_srt(start)} --> {seconds_to_srt(end)}", text, ""])
    path.write_text("\n".join(lines))


def output_to_source_time(value: float, segments: list[dict]) -> float:
    value = float(value)
    if not segments:
        return value
    for seg in segments:
        if seg["out_start"] - 0.02 <= value <= seg["out_end"] + 0.02:
            offset = min(max(value - seg["out_start"], 0.0), seg["end"] - seg["start"])
            return seg["start"] + offset
    if value <= segments[0]["out_start"]:
        return segments[0]["start"]
    for prev, nxt in zip(segments, segments[1:]):
        if prev["out_end"] < value < nxt["out_start"]:
            return prev["end"] if value - prev["out_end"] <= nxt["out_start"] - value else nxt["start"]
    return segments[-1]["end"]


def output_to_source_time_for_start(value: float, segments: list[dict]) -> float:
    value = float(value)
    if not segments:
        return value
    eps = 0.001
    for seg in segments:
        if seg["out_start"] - eps <= value < seg["out_end"] - eps:
            offset = min(max(value - seg["out_start"], 0.0), seg["end"] - seg["start"])
            return seg["start"] + offset
    for seg in segments:
        if abs(value - seg["out_start"]) <= 0.02:
            return seg["start"]
    return output_to_source_time(value, segments)


def output_to_source_time_for_end(value: float, segments: list[dict]) -> float:
    value = float(value)
    if not segments:
        return value
    eps = 0.001
    for seg in segments:
        if seg["out_start"] + eps < value <= seg["out_end"] + eps:
            offset = min(max(value - seg["out_start"], 0.0), seg["end"] - seg["start"])
            return seg["start"] + offset
    for seg in reversed(segments):
        if abs(value - seg["out_end"]) <= 0.02:
            return seg["end"]
    return output_to_source_time(value, segments)


def source_to_output_time(value: float, segments: list[dict]) -> float:
    value = float(value)
    if not segments:
        return value
    for seg in segments:
        if seg["start"] - 0.02 <= value <= seg["end"] + 0.02:
            offset = min(max(value - seg["start"], 0.0), seg["end"] - seg["start"])
            return seg["out_start"] + offset
    if value <= segments[0]["start"]:
        return segments[0]["out_start"]
    for prev, nxt in zip(segments, segments[1:]):
        if prev["end"] < value < nxt["start"]:
            return prev["out_end"] if value - prev["end"] <= nxt["start"] - value else nxt["out_start"]
    return segments[-1]["out_end"]


def remap_cues(cues: list[dict], source_segments: list[dict], target_segments: list[dict]) -> list[dict]:
    remapped: list[dict] = []
    for cue in cues:
        source_start = output_to_source_time(float(cue["start"]), source_segments)
        source_end = output_to_source_time(float(cue["end"]), source_segments)
        start = source_to_output_time(source_start, target_segments)
        end = source_to_output_time(source_end, target_segments)
        if end <= start:
            end = start + 0.05
        updated = dict(cue)
        updated["start"] = start
        updated["end"] = end
        remapped.append(updated)
    return remapped


def build_keep_from_intervals(active: list[dict], duration: float) -> list[dict]:
    active.sort(key=lambda d: (d["start"], d["end"]))
    merged: list[dict] = []
    for item in active:
        if merged and item["start"] <= merged[-1]["end"] + 0.02:
            merged[-1]["end"] = max(merged[-1]["end"], item["end"])
        else:
            merged.append({"start": item["start"], "end": item["end"]})

    keep: list[dict] = []
    cursor = 0.0
    out = 0.0
    for item in merged:
        if item["start"] > cursor + 0.05:
            seg = {"start": cursor, "end": item["start"], "out_start": out}
            out += seg["end"] - seg["start"]
            seg["out_end"] = out
            keep.append(seg)
        cursor = max(cursor, item["end"])
    if duration > cursor + 0.05:
        seg = {"start": cursor, "end": duration, "out_start": out}
        out += seg["end"] - seg["start"]
        seg["out_end"] = out
        keep.append(seg)
    return keep


def build_keep_segments(project: ProjectConfig, deletes: list[dict], selected: dict) -> list[dict]:
    active = [d for d in deletes if selected.get(str(d["id"]), False)]
    return build_keep_from_intervals(active, project.duration)


def write_delete_csv(deletes: list[dict], selected: dict, notes: dict, path: Path) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "selected", "start", "end", "duration", "summary", "reason", "note", "source"])
        writer.writeheader()
        for item in deletes:
            writer.writerow({
                "id": item["id"],
                "selected": bool(selected.get(str(item["id"]), False)),
                "start": f"{item['start']:.3f}",
                "end": f"{item['end']:.3f}",
                "duration": f"{item['duration']:.3f}",
                "summary": item["summary"],
                "reason": item["reason"],
                "note": notes.get(str(item["id"]), ""),
                "source": item["source"],
            })


def script_delete_intervals(lines: list[dict]) -> list[dict]:
    intervals: list[dict] = []
    for line in lines:
        text = clean_line_text(line.get("text", ""))
        if not line.get("deleted"):
            continue
        start = float(line.get("start", 0))
        end = float(line.get("end", start))
        if end <= start + 0.02:
            continue
        intervals.append({
            "id": line.get("id", len(intervals) + 1),
            "start": start,
            "end": end,
            "duration": end - start,
            "summary": text or "手动删除",
            "reason": line.get("source", ""),
            "source": "scriptLines",
        })
    return intervals


def script_lines_to_cues(lines: list[dict], keep: list[dict] | None = None) -> list[dict]:
    cues: list[dict] = []
    for line in sorted(lines, key=lambda item: (float(item.get("start", 0)), float(item.get("end", 0)))):
        text = clean_line_text(line.get("text", ""))
        if line.get("deleted") or not text:
            continue
        start = float(line.get("start", 0))
        end = max(start + 0.05, float(line.get("end", start + 0.05)))
        if keep is not None:
            start = source_to_output_time(start, keep)
            end = source_to_output_time(end, keep)
        cues.append({
            "id": line.get("id", len(cues) + 1),
            "index": len(cues) + 1,
            "start": start,
            "end": max(start + 0.05, end),
            "text": text,
        })
    return cues


def write_script_csv(lines: list[dict], path: Path) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "deleted", "start", "end", "duration", "text", "source"])
        writer.writeheader()
        for line in lines:
            start = float(line.get("start", 0))
            end = max(start, float(line.get("end", start)))
            writer.writerow({
                "id": line.get("id", ""),
                "deleted": bool(line.get("deleted")),
                "start": f"{start:.3f}",
                "end": f"{end:.3f}",
                "duration": f"{end - start:.3f}",
                "text": clean_line_text(line.get("text", "")),
                "source": line.get("source", ""),
            })


def alignment_units_for_cues(cues: list[dict]) -> list[dict]:
    units: list[dict] = []
    for cue_index, cue in enumerate(cues, 1):
        text = clean_line_text(cue.get("text", ""))
        start = float(cue.get("start", 0))
        end = max(start + 0.05, float(cue.get("end", start + 0.05)))
        text_units = alignment_text_units(text)
        if not text_units:
            continue
        step = (end - start) / len(text_units)
        for i, unit_text in enumerate(text_units):
            units.append({
                "cueIndex": cue_index,
                "unitIndex": i + 1,
                "text": unit_text,
                "start": round(start + step * i, 3),
                "end": round(start + step * (i + 1), 3),
                "timing": "line-interpolated",
            })
    return units


def alignment_text_units(text: str) -> list[str]:
    units: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if not ch.strip():
            i += 1
            continue
        if re.match(r"[A-Za-z0-9]", ch):
            start = i
            i += 1
            while i < len(text) and re.match(r"[A-Za-z0-9.+%_-]", text[i]):
                i += 1
            units.append(text[start:i])
            continue
        units.append(ch)
        i += 1
    return units


def write_alignment_sidecars(cues: list[dict], base_path: Path, timeline: str) -> dict[str, Path]:
    units = alignment_units_for_cues(cues)
    payload = {
        "timeline": timeline,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "cues": [
            {
                "index": i,
                "start": round(float(cue["start"]), 3),
                "end": round(float(cue["end"]), 3),
                "text": clean_line_text(cue.get("text", "")),
                "units": [unit for unit in units if unit["cueIndex"] == i],
            }
            for i, cue in enumerate(cues, 1)
        ],
        "units": units,
    }
    json_path = base_path.with_suffix(".json")
    jsonl_path = base_path.with_suffix(".units.jsonl")
    csv_path = base_path.with_suffix(".units.csv")
    write_json_atomic(json_path, payload)
    jsonl_path.write_text("".join(json.dumps(unit, ensure_ascii=False) + "\n" for unit in units))
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["cueIndex", "unitIndex", "text", "start", "end", "timing"])
        writer.writeheader()
        writer.writerows(units)
    return {
        f"{timeline}AlignmentJson": json_path,
        f"{timeline}AlignmentUnitsJsonl": jsonl_path,
        f"{timeline}AlignmentUnitsCsv": csv_path,
    }


def write_keep_csv(segments: list[dict], path: Path) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "index", "source_start", "source_end", "source_duration",
            "timeline_start", "timeline_end", "timeline_duration",
        ])
        writer.writeheader()
        for i, seg in enumerate(segments, 1):
            source_start = float(seg["start"])
            source_end = float(seg["end"])
            timeline_start = float(seg["out_start"])
            timeline_end = float(seg["out_end"])
            writer.writerow({
                "index": i,
                "source_start": f"{source_start:.3f}",
                "source_end": f"{source_end:.3f}",
                "source_duration": f"{source_end - source_start:.3f}",
                "timeline_start": f"{timeline_start:.3f}",
                "timeline_end": f"{timeline_end:.3f}",
                "timeline_duration": f"{timeline_end - timeline_start:.3f}",
            })


def fcpx_time(seconds: float) -> str:
    ms = max(0, int(round(float(seconds) * 1000)))
    return "0s" if ms == 0 else f"{ms}/1000s"


def fcpx_frame_duration(fps: float | None) -> str:
    fps = float(fps or 30)
    if abs(fps - 23.976) < 0.01:
        return "1001/24000s"
    if abs(fps - 29.97) < 0.01:
        return "1001/30000s"
    if abs(fps - 59.94) < 0.01:
        return "1001/60000s"
    return f"1/{max(1, int(round(fps)))}s"


def xml_escape(value: str) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def media_uri(value: str) -> str:
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", value):
        return value
    return "file://" + quote(value, safe="/:@")


def validate_keep_segments(segments: list[dict], intervals: list[dict] | None = None) -> None:
    previous_out = -0.001
    intervals = intervals or []
    for i, seg in enumerate(segments, 1):
        start = float(seg["start"])
        end = float(seg["end"])
        out_start = float(seg["out_start"])
        out_end = float(seg["out_end"])
        if end <= start:
            raise ValueError(f"keep segment {i} has non-positive source duration")
        if out_end <= out_start:
            raise ValueError(f"keep segment {i} has non-positive timeline duration")
        if i == 1 and abs(out_start) > 0.02:
            raise ValueError("first keep segment does not start at timeline zero")
        if i > 1 and abs(out_start - previous_out) > 0.02:
            raise ValueError(f"keep segment {i} output timeline has a gap or overlap")
        if out_start + 0.02 < previous_out:
            raise ValueError(f"keep segment {i} output timeline is not monotonic")
        if abs((out_end - out_start) - (end - start)) > 0.05:
            raise ValueError(f"keep segment {i} source/output duration mismatch")
        for interval in intervals:
            d_start = float(interval["start"])
            d_end = float(interval["end"])
            if start < d_end - 0.02 and end > d_start + 0.02:
                raise ValueError(f"keep segment {i} overlaps delete interval {interval.get('id', '')}")
        previous_out = out_end


def write_fcpxml_timeline(project: ProjectConfig, segments: list[dict], path: Path, intervals: list[dict] | None = None) -> None:
    validate_keep_segments(segments, intervals)
    timeline_duration = sum(float(seg["end"]) - float(seg["start"]) for seg in segments)
    source = project.davinci_media_path or str(project.source_media)
    source_name = project.source_label or project.title
    has_video = project.media_type == "video"
    asset_attrs = [
        'id="r1"',
        f'name="{xml_escape(source_name)}"',
        'start="0s"',
        f'duration="{fcpx_time(project.duration)}"',
        'hasAudio="1"',
    ]
    if has_video:
        asset_attrs.extend(['hasVideo="1"', 'format="r0"'])
    clips = []
    for i, seg in enumerate(segments, 1):
        clips.append(
            f'          <asset-clip name="{xml_escape(source_name)} keep {i:03d}" ref="r1" '
            f'offset="{fcpx_time(float(seg["out_start"]))}" '
            f'start="{fcpx_time(float(seg["start"]))}" '
            f'duration="{fcpx_time(float(seg["end"]) - float(seg["start"]))}"/>\n'
        )
    body = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE fcpxml>',
        '<fcpxml version="1.10">',
        '  <resources>',
        f'    <format id="r0" name="FFVideoFormat1080p{int(round(float(project.source_fps or 30)))}" '
        f'frameDuration="{fcpx_frame_duration(project.source_fps)}" width="1920" height="1080"/>',
        f'    <asset {" ".join(asset_attrs)}>',
        f'      <media-rep kind="original-media" src="{xml_escape(media_uri(source))}"/>',
        '    </asset>',
        '  </resources>',
        '  <library>',
        f'    <event name="{xml_escape(project.title)}">',
        f'      <project name="{xml_escape(project.title)} edited">',
        f'        <sequence duration="{fcpx_time(timeline_duration)}" format="r0" tcStart="0s" tcFormat="NDF">',
        '          <spine>',
        *[clip.rstrip("\n") for clip in clips],
        '          </spine>',
        '        </sequence>',
        '      </project>',
        '    </event>',
        '  </library>',
        '</fcpxml>',
        '',
    ]
    write_text_atomic(path, "\n".join(body))


def write_davinci_handoff(
    project: ProjectConfig,
    state: dict,
    keep: list[dict],
    intervals: list[dict],
    export_paths: dict[str, Path],
    mode: str,
    options: ExportOptions,
) -> dict[str, Path]:
    handoff_json = export_file(options, "davinci_handoff.json")
    handoff_readme = export_file(options, "davinci_handoff_readme.txt")
    keep_csv = export_file(options, "davinci_keep_segments.csv")
    write_keep_csv(keep, keep_csv)
    payload = {
        "project": {
            "id": project.id,
            "title": project.title,
            "mediaType": project.media_type,
            "sourceLabel": project.source_label,
            "sourceMedia": str(project.source_media),
            "davinciMediaPath": project.davinci_media_path,
            "duration": project.duration,
            "fps": project.source_fps,
            "timecodeStart": project.source_timecode_start,
            "handoffNotes": project.handoff_notes,
        },
        "timeline": {
            "mode": mode,
            "timebase": "seconds",
            "sourceTimeline": "original media seconds",
            "outputTimeline": "delete-line selected timeline seconds",
            "estimatedDuration": sum(float(seg["end"]) - float(seg["start"]) for seg in keep),
        },
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "files": {key: str(value) for key, value in export_paths.items()},
        "keepSegments": keep,
        "deleteIntervals": intervals,
        "stateUpdatedAt": state.get("updatedAt"),
    }
    write_json_atomic(handoff_json, payload)
    readme = "\n".join([
        f"DaVinci handoff for {project.title}",
        "",
        "Use this folder as a handoff package, not as the only source of truth.",
        f"Source media: {project.source_media}",
        f"DaVinci media path: {project.davinci_media_path or project.source_media}",
        f"Source label: {project.source_label}",
        f"FPS: {project.source_fps if project.source_fps is not None else 'unspecified'}",
        f"Timecode start: {project.source_timecode_start}",
        "Timebase: seconds.",
        f"Mode: {mode}",
        "delete_intervals are original media time ranges to cut.",
        "keep_segments are original media ranges to keep and their output timeline positions.",
        "script_selected_timeline.srt is already remapped to the edited output timeline.",
        "script_original_timeline.srt stays on the original source timeline.",
        f"Notes: {project.handoff_notes or 'none'}",
        "",
        "Recommended Resolve flow:",
        "1. Import the source media.",
        "2. Use davinci_keep_segments.csv or davinci_handoff.json to rebuild the voice timeline.",
        "3. Import script_selected_timeline.srt to check captions after cuts.",
        "4. Use selected_delete_preview media, if present, only as a reference/rendered preview.",
        "",
        "Generated files:",
        *[f"- {key}: {value}" for key, value in export_paths.items()],
        f"- keepCsv: {keep_csv}",
        f"- handoffJson: {handoff_json}",
    ])
    write_text_atomic(handoff_readme, readme + "\n")
    return {
        "davinciHandoff": handoff_json,
        "davinciReadme": handoff_readme,
        "davinciKeepCsv": keep_csv,
    }


def render_audio(project: ProjectConfig, segments: list[dict], output: Path) -> None:
    parts: list[str] = []
    labels: list[str] = []
    for i, seg in enumerate(segments):
        duration = float(seg["end"]) - float(seg["start"])
        if duration < 0.08:
            continue
        fade_out = max(0.0, duration - RENDER_AUDIO_FADE_SECONDS)
        label = f"a{i}"
        parts.append(
            f"[0:a]atrim=start={seg['start']:.3f}:end={seg['end']:.3f},"
            f"asetpts=PTS-STARTPTS,"
            f"afade=t=in:st=0:d={RENDER_AUDIO_FADE_SECONDS:.3f},"
            f"afade=t=out:st={fade_out:.3f}:d={RENDER_AUDIO_FADE_SECONDS:.3f}"
            f"[{label}]"
        )
        labels.append(f"[{label}]")
    if not labels:
        raise RuntimeError("no keep segments")
    parts.append("".join(labels) + f"concat=n={len(labels)}:v=0:a=1[outa]")
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(project.source_media),
        "-filter_complex", ";".join(parts),
        "-map", "[outa]",
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        str(output),
    ], check=True)


def video_encode_args() -> list[str]:
    encoder = os.environ.get("INTERACTIVE_VIDEO_CUTTER_ENCODER", "libx264")
    if encoder in {"h264_videotoolbox", "hevc_videotoolbox", "h264_nvenc", "hevc_nvenc", "h264_qsv", "hevc_qsv"}:
        bitrate = os.environ.get("INTERACTIVE_VIDEO_CUTTER_VIDEO_BITRATE", "8M")
        return ["-c:v", encoder, "-b:v", bitrate]
    crf = os.environ.get("INTERACTIVE_VIDEO_CUTTER_VIDEO_CRF", "23")
    preset = os.environ.get("INTERACTIVE_VIDEO_CUTTER_VIDEO_PRESET", "veryfast")
    return ["-c:v", encoder, "-preset", preset, "-crf", crf]


def render_video(project: ProjectConfig, segments: list[dict], output: Path) -> None:
    parts: list[str] = []
    labels: list[str] = []
    for i, seg in enumerate(segments):
        duration = float(seg["end"]) - float(seg["start"])
        if duration < 0.08:
            continue
        v_label = f"v{i}"
        a_label = f"a{i}"
        fade_out = max(0.0, duration - RENDER_AUDIO_FADE_SECONDS)
        parts.append(
            f"[0:v]trim=start={seg['start']:.3f}:end={seg['end']:.3f},"
            f"setpts=PTS-STARTPTS[{v_label}]"
        )
        parts.append(
            f"[0:a]atrim=start={seg['start']:.3f}:end={seg['end']:.3f},"
            f"asetpts=PTS-STARTPTS,"
            f"afade=t=in:st=0:d={RENDER_AUDIO_FADE_SECONDS:.3f},"
            f"afade=t=out:st={fade_out:.3f}:d={RENDER_AUDIO_FADE_SECONDS:.3f}"
            f"[{a_label}]"
        )
        labels.append(f"[{v_label}][{a_label}]")
    if not labels:
        raise RuntimeError("no keep segments")
    parts.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=1[outv][outa]")
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(project.source_media),
        "-filter_complex", ";".join(parts),
        "-map", "[outv]", "-map", "[outa]",
        *video_encode_args(),
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        str(output),
    ], check=True)


def render_media(project: ProjectConfig, segments: list[dict], output: Path) -> None:
    if project.media_type == "video":
        render_video(project, segments, output)
        return
    render_audio(project, segments, output)


def export_state(project: ProjectConfig, state: dict, render: bool = False, options: ExportOptions | None = None) -> dict:
    options = options or default_export_options(project)
    options.output_dir.mkdir(parents=True, exist_ok=True)
    deletes = load_deletes(project)
    if state.get("useScriptLines", True) and state.get("scriptLines"):
        lines = state.get("scriptLines", [])
        intervals = script_delete_intervals(lines)
        keep = build_keep_from_intervals(intervals, project.duration)
        state_out = export_file(options, "review_state.json")
        srt_out = export_file(options, "script_selected_timeline.srt")
        current_srt_out = export_file(options, "script_original_timeline.srt")
        edl_out = export_file(options, "script_selected_delete_edl.json")
        delete_out = export_file(options, "script_delete_intervals.csv")
        line_out = export_file(options, "script_lines.csv")
        fcpxml_out = export_file(options, "davinci_timeline.fcpxml")
        selected_alignment_base = export_file(options, "script_selected_text_time_alignment")
        original_alignment_base = export_file(options, "script_original_text_time_alignment")
        selected_cues = script_lines_to_cues(lines, keep)
        original_cues = script_lines_to_cues(lines)
        write_json_atomic(state_out, state)
        write_srt(selected_cues, srt_out)
        write_srt(original_cues, current_srt_out)
        write_json_atomic(edl_out, keep)
        write_delete_csv(intervals, {str(item["id"]): True for item in intervals}, {}, delete_out)
        write_script_csv(lines, line_out)
        write_fcpxml_timeline(project, keep, fcpxml_out, intervals)
        alignment_paths = {
            **write_alignment_sidecars(selected_cues, selected_alignment_base, "selectedTimeline"),
            **write_alignment_sidecars(original_cues, original_alignment_base, "originalTimeline"),
        }
        davinci_paths = write_davinci_handoff(project, state, keep, intervals, {
            "state": state_out,
            "selectedTimelineSrt": srt_out,
            "originalTimelineSrt": current_srt_out,
            "keepSegmentsJson": edl_out,
            "deleteIntervalsCsv": delete_out,
            "scriptLinesCsv": line_out,
            "davinciTimelineFcpxml": fcpxml_out,
            **alignment_paths,
        }, "scriptLines", options)
        result = {
            "mode": "scriptLines",
            "outputDir": str(options.output_dir),
            "namingPrefix": options.naming_prefix,
            "state": str(state_out),
            "srt": str(srt_out),
            "currentTimelineSrt": str(current_srt_out),
            "edl": str(edl_out),
            "deletes": str(delete_out),
            "scriptLines": str(line_out),
            "davinciTimelineFcpxml": str(fcpxml_out),
            "selectedAlignmentJson": str(alignment_paths["selectedTimelineAlignmentJson"]),
            "selectedAlignmentUnitsJsonl": str(alignment_paths["selectedTimelineAlignmentUnitsJsonl"]),
            "selectedAlignmentUnitsCsv": str(alignment_paths["selectedTimelineAlignmentUnitsCsv"]),
            "originalAlignmentJson": str(alignment_paths["originalTimelineAlignmentJson"]),
            "originalAlignmentUnitsJsonl": str(alignment_paths["originalTimelineAlignmentUnitsJsonl"]),
            "originalAlignmentUnitsCsv": str(alignment_paths["originalTimelineAlignmentUnitsCsv"]),
            "davinciHandoff": str(davinci_paths["davinciHandoff"]),
            "davinciReadme": str(davinci_paths["davinciReadme"]),
            "davinciKeepCsv": str(davinci_paths["davinciKeepCsv"]),
            "estimatedDuration": sum(seg["end"] - seg["start"] for seg in keep),
        }
        if render:
            suffix = ".mp4" if project.media_type == "video" else ".m4a"
            media_out = export_file(options, f"selected_delete_preview{suffix}")
            render_media(project, keep, media_out)
            result["media"] = str(media_out)
            media_url = export_url(project, media_out)
            if media_url:
                result["mediaUrl"] = media_url
            if project.media_type == "video":
                result["video"] = str(media_out)
                if media_url:
                    result["videoUrl"] = media_url
            else:
                result["audio"] = str(media_out)
                if media_url:
                    result["audioUrl"] = media_url
        return result

    selected = state.get("selectedDeletes", {})
    keep = build_keep_segments(project, deletes, selected)
    default_selected = {str(item["id"]): True for item in deletes}
    default_keep = build_keep_segments(project, deletes, default_selected)
    state_out = export_file(options, "review_state.json")
    current_srt_out = export_file(options, "edited_current_timeline.srt")
    srt_out = export_file(options, "edited_selected_timeline.srt")
    edl_out = export_file(options, "selected_delete_edl.json")
    delete_out = export_file(options, "selected_delete_intervals.csv")
    fcpxml_out = export_file(options, "davinci_timeline.fcpxml")
    selected_alignment_base = export_file(options, "edited_selected_text_time_alignment")
    original_alignment_base = export_file(options, "edited_original_text_time_alignment")
    write_json_atomic(state_out, state)
    cues = state.get("cues", [])
    write_srt(cues, current_srt_out)
    selected_cues = remap_cues(cues, default_keep, keep)
    write_srt(selected_cues, srt_out)
    write_json_atomic(edl_out, keep)
    write_delete_csv(deletes, selected, state.get("deleteNotes", {}), delete_out)
    active_deletes = [d for d in deletes if selected.get(str(d["id"]), False)]
    write_fcpxml_timeline(project, keep, fcpxml_out, active_deletes)
    alignment_paths = {
        **write_alignment_sidecars(selected_cues, selected_alignment_base, "selectedTimeline"),
        **write_alignment_sidecars(cues, original_alignment_base, "originalTimeline"),
    }
    davinci_paths = write_davinci_handoff(project, state, keep, active_deletes, {
        "state": state_out,
        "selectedTimelineSrt": srt_out,
        "originalTimelineSrt": current_srt_out,
        "keepSegmentsJson": edl_out,
        "deleteIntervalsCsv": delete_out,
        "davinciTimelineFcpxml": fcpxml_out,
        **alignment_paths,
    }, "legacySelectedDeletes", options)
    result = {
        "outputDir": str(options.output_dir),
        "namingPrefix": options.naming_prefix,
        "state": str(state_out),
        "srt": str(srt_out),
        "currentTimelineSrt": str(current_srt_out),
        "edl": str(edl_out),
        "deletes": str(delete_out),
        "davinciTimelineFcpxml": str(fcpxml_out),
        "selectedAlignmentJson": str(alignment_paths["selectedTimelineAlignmentJson"]),
        "selectedAlignmentUnitsJsonl": str(alignment_paths["selectedTimelineAlignmentUnitsJsonl"]),
        "selectedAlignmentUnitsCsv": str(alignment_paths["selectedTimelineAlignmentUnitsCsv"]),
        "originalAlignmentJson": str(alignment_paths["originalTimelineAlignmentJson"]),
        "originalAlignmentUnitsJsonl": str(alignment_paths["originalTimelineAlignmentUnitsJsonl"]),
        "originalAlignmentUnitsCsv": str(alignment_paths["originalTimelineAlignmentUnitsCsv"]),
        "davinciHandoff": str(davinci_paths["davinciHandoff"]),
        "davinciReadme": str(davinci_paths["davinciReadme"]),
        "davinciKeepCsv": str(davinci_paths["davinciKeepCsv"]),
        "estimatedDuration": sum(seg["end"] - seg["start"] for seg in keep),
    }
    if render:
        suffix = ".mp4" if project.media_type == "video" else ".m4a"
        media_out = export_file(options, f"selected_delete_preview{suffix}")
        render_media(project, keep, media_out)
        result["media"] = str(media_out)
        media_url = export_url(project, media_out)
        if media_url:
            result["mediaUrl"] = media_url
        if project.media_type == "video":
            result["video"] = str(media_out)
            if media_url:
                result["videoUrl"] = media_url
        else:
            result["audio"] = str(media_out)
            if media_url:
                result["audioUrl"] = media_url
    return result


def json_response(handler: BaseHTTPRequestHandler, payload: dict, code: int = 200) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode()
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".m4a":
        return "audio/mp4"
    if suffix == ".mp4":
        return "video/mp4"
    if suffix == ".mov":
        return "video/quicktime"
    if suffix in {".wav", ".wave"}:
        return "audio/wav"
    if suffix == ".mp3":
        return "audio/mpeg"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def send_no_content(handler: BaseHTTPRequestHandler) -> None:
    handler.send_response(204)
    handler.send_header("Content-Length", "0")
    handler.end_headers()


def send_file(
    handler: BaseHTTPRequestHandler,
    path: Path,
    *,
    include_body: bool = True,
    cache_control: str | None = None,
) -> None:
    if not path.exists():
        handler.send_error(404)
        return
    size = path.stat().st_size
    start = 0
    end = size - 1
    range_header = handler.headers.get("Range")
    if range_header:
        m = re.match(r"bytes=(\d*)-(\d*)", range_header)
        if m:
            if m.group(1):
                start = int(m.group(1))
            if m.group(2):
                end = min(size - 1, int(m.group(2)))
    length = max(0, end - start + 1)
    handler.send_response(206 if range_header else 200)
    handler.send_header("Content-Type", content_type(path))
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Content-Length", str(length))
    if cache_control:
        handler.send_header("Cache-Control", cache_control)
    if range_header:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.end_headers()
    if not include_body:
        return
    with path.open("rb") as f:
        f.seek(start)
        remaining = length
        while remaining > 0:
            chunk = f.read(min(1024 * 512, remaining))
            if not chunk:
                break
            try:
                handler.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                return
            remaining -= len(chunk)


class Handler(BaseHTTPRequestHandler):
    def route_static(self, *, include_body: bool = True) -> bool:
        parsed = urlparse(self.path)
        if parsed.path == "/favicon.ico":
            send_no_content(self)
            return True
        if parsed.path in {"/", "/interactive_review_app.html"}:
            send_file(self, APP_HTML, include_body=include_body, cache_control="no-store")
            return True
        if parsed.path.startswith("/media/"):
            parts = [unquote(part) for part in parsed.path.split("/") if part]
            try:
                if len(parts) == 2:
                    project = get_project(DEFAULT_PROJECT_ID)
                    kind = {"original": "source", "draft": "draft"}.get(parts[1], parts[1])
                elif len(parts) == 3:
                    project = get_project(parts[1])
                    kind = parts[2]
                else:
                    self.send_error(404)
                    return True
                path = media_path_for(project, kind)
                if not path:
                    self.send_error(404)
                    return True
                send_file(self, path, include_body=include_body)
            except KeyError:
                self.send_error(404)
            return True
        if parsed.path == "/media/draft":
            project = get_project(DEFAULT_PROJECT_ID)
            if not project.draft_media:
                self.send_error(404)
                return True
            send_file(self, project.draft_media, include_body=include_body)
            return True
        if parsed.path == "/media/original":
            send_file(self, get_project(DEFAULT_PROJECT_ID).source_media, include_body=include_body)
            return True
        if parsed.path.startswith("/exports/"):
            parts = [unquote(part) for part in parsed.path.split("/") if part]
            try:
                if len(parts) >= 3 and parts[1] in PROJECTS:
                    project = get_project(parts[1])
                    rel_parts = parts[2:]
                else:
                    project = get_project(DEFAULT_PROJECT_ID)
                    rel_parts = parts[1:]
                requested = (project.export_dir / Path(*rel_parts)).resolve()
                export_dir = project.export_dir.resolve()
                if requested == export_dir or export_dir in requested.parents:
                    send_file(self, requested, include_body=include_body)
                else:
                    self.send_error(403)
            except (KeyError, ValueError):
                self.send_error(404)
            return True
        return False

    def do_HEAD(self) -> None:
        if self.route_static(include_body=False):
            return
        self.send_error(404)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/projects":
            payload = {
                "defaultProject": DEFAULT_PROJECT_ID,
                "projects": [
                    {
                        "id": project.id,
                        "title": project.title,
                        "mediaType": project.media_type,
                        "duration": project.duration,
                        "stateRevision": file_revision(project.state_path),
                        "updatedAt": load_state(project).get("updatedAt"),
                    }
                    for project in PROJECTS.values()
                ],
            }
            json_response(self, payload)
            return
        if parsed.path == "/api/lock/status":
            try:
                project = project_from_query(parsed)
                query = parse_qs(parsed.query)
                token = query.get("lockToken", query.get("token", [""]))[0]
                owner_id = query.get("ownerId", [""])[0]
                json_response(self, lock_status_payload(project, token=token, owner_id=owner_id))
            except KeyError:
                json_response(self, {"ok": False, "error": "unknown project"}, 404)
            return
        if parsed.path == "/api/waveform":
            try:
                project = project_from_query(parsed)
                query = parse_qs(parsed.query)
                kind = str(query.get("kind", [""])[0] or "")
                try:
                    bins = int(query.get("bins", ["720"])[0])
                except ValueError:
                    bins = 720
                try:
                    start = float(query.get("start", ["0"])[0])
                    end = float(query.get("end", ["0"])[0])
                except ValueError:
                    start = 0.0
                    end = 0.0
                path, peaks, start, end = waveform_peaks(project, kind, bins, start, end)
                json_response(self, {
                    "ok": True,
                    "project": project.id,
                    "media": str(path),
                    "bins": len(peaks),
                    "start": start,
                    "end": end,
                    "peaks": peaks,
                })
            except KeyError:
                json_response(self, {"ok": False, "error": "unknown project"}, 404)
            except FileNotFoundError as exc:
                json_response(self, {"ok": False, "error": str(exc)}, 404)
            except Exception as exc:
                json_response(self, {"ok": False, "error": str(exc)}, 500)
            return
        if parsed.path == "/api/data":
            try:
                project = project_from_query(parsed)
                query = parse_qs(parsed.query)
                token = query.get("lockToken", query.get("token", [""]))[0]
                owner_id = query.get("ownerId", [""])[0]
                deletes = load_deletes(project)
                state = load_state(project)
                revision = file_revision(project.state_path)
                payload = {
                    "meta": {
                        "projectId": project.id,
                        "title": project.title,
                        "mediaType": project.media_type,
                        "originalDuration": project.duration,
                        "media": media_items(project),
                        "draftAudio": f"/media/{project.id}/draft" if project.draft_media else None,
                        "originalAudio": f"/media/{project.id}/source" if project.media_type == "audio" else None,
                        "sourceMedia": f"/media/{project.id}/source",
                        "statePath": str(project.state_path),
                        "exportDir": str(project.export_dir),
                        "stateRevision": revision,
                    },
                    "deletes": deletes,
                    "state": state,
                    "revision": revision,
                    "lockStatus": lock_status_payload(project, token=token, owner_id=owner_id),
                }
                json_response(self, payload)
            except KeyError:
                json_response(self, {"ok": False, "error": "unknown project"}, 404)
            except Exception as exc:
                json_response(self, {"ok": False, "error": str(exc)}, 500)
            return
        if self.route_static():
            return
        self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode() or "{}")
        except Exception:
            json_response(self, {"ok": False, "error": "invalid json"}, 400)
            return

        if parsed.path in {
            "/api/lock/acquire",
            "/api/lock/heartbeat",
            "/api/lock/release",
            "/api/lock/takeover",
        }:
            try:
                project = project_from_query(parsed)
                owner_id, owner_name, tab_id = require_lock_owner(payload)
                token = str(payload.get("lockToken") or payload.get("token") or "").strip()
                with SERVER_MUTEX:
                    lock = active_lock(project)
                    if parsed.path == "/api/lock/acquire":
                        if lock and not (
                            lock.get("token") == token
                            or (lock.get("ownerId") == owner_id and lock.get("tabId") == tab_id)
                        ):
                            json_response(self, {
                                "ok": False,
                                "locked": True,
                                "error": "locked by another editor",
                                "lock": public_lock(lock),
                            }, 423)
                            return
                        lock = refresh_lock(project, lock) if lock else write_lock(project, owner_id, owner_name, tab_id)
                        json_response(self, {
                            "ok": True,
                            "locked": True,
                            "heldByMe": True,
                            "token": lock.get("token"),
                            "lock": public_lock(lock, include_token=True),
                        })
                        return
                    if parsed.path == "/api/lock/takeover":
                        lock = write_lock(project, owner_id, owner_name, tab_id)
                        json_response(self, {
                            "ok": True,
                            "locked": True,
                            "heldByMe": True,
                            "token": lock.get("token"),
                            "lock": public_lock(lock, include_token=True),
                        })
                        return
                    if parsed.path == "/api/lock/heartbeat":
                        if not lock or lock.get("token") != token:
                            json_response(self, {
                                "ok": False,
                                "locked": bool(lock),
                                "error": "lock expired or owned by another editor",
                                "lock": public_lock(lock),
                            }, 423)
                            return
                        lock = refresh_lock(project, lock)
                        json_response(self, {
                            "ok": True,
                            "locked": True,
                            "heldByMe": True,
                            "token": lock.get("token"),
                            "lock": public_lock(lock, include_token=True),
                        })
                        return
                    if parsed.path == "/api/lock/release":
                        if lock and lock.get("token") != token:
                            json_response(self, {
                                "ok": False,
                                "locked": True,
                                "error": "lock owned by another editor",
                                "lock": public_lock(lock),
                            }, 423)
                            return
                        if lock_path_for(project).exists():
                            lock_path_for(project).unlink()
                        json_response(self, {"ok": True, "locked": False, "heldByMe": False})
                        return
            except KeyError:
                json_response(self, {"ok": False, "error": "unknown project"}, 404)
            except ValueError as exc:
                json_response(self, {"ok": False, "error": str(exc)}, 400)
            except Exception as exc:
                json_response(self, {"ok": False, "error": str(exc)}, 500)
            return

        if parsed.path in {"/api/save", "/api/export", "/api/render"}:
            try:
                project = project_from_query(parsed)
                state = payload.get("state") if isinstance(payload.get("state"), dict) else payload
                base_revision = payload.get("baseRevision") if isinstance(payload, dict) else None
                force = bool(payload.get("force")) if isinstance(payload, dict) else False
                lock_token = str(payload.get("lockToken") or payload.get("token") or "").strip()
                with SERVER_MUTEX:
                    ensure_write_lock(project, lock_token)
                    new_revision = save_state(project, state, base_revision, force=force)
                if parsed.path == "/api/save":
                    json_response(self, {
                        "ok": True,
                        "path": str(project.state_path),
                        "revision": new_revision,
                    })
                    return
                options = export_options_from_payload(project, payload)
                result = export_state(project, state, render=(parsed.path == "/api/render"), options=options)
                json_response(self, {
                    "ok": True,
                    "result": result,
                    "revision": file_revision(project.state_path),
                })
            except KeyError:
                json_response(self, {"ok": False, "error": "unknown project"}, 404)
            except RevisionConflict as exc:
                json_response(self, {
                    "ok": False,
                    "conflict": True,
                    "error": "revision conflict",
                    "currentRevision": exc.current_revision,
                }, 409)
            except ValueError as exc:
                json_response(self, {"ok": False, "error": str(exc)}, 400)
            except LockConflict as exc:
                json_response(self, {
                    "ok": False,
                    "locked": True,
                    "error": "project locked",
                    "lock": exc.lock,
                }, 423)
            except Exception as exc:
                json_response(self, {"ok": False, "error": str(exc)}, 500)
            return
        json_response(self, {"ok": False, "error": "unknown endpoint"}, 404)

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive transcript review server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    args = parser.parse_args()
    load_projects(Path(args.manifest).resolve())
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Interactive review server: http://{args.host}:{args.port}")
    print(f"Projects: {', '.join(PROJECTS)}")
    server.serve_forever()


if __name__ == "__main__":
    main()
