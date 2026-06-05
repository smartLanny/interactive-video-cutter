#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any


DEFAULT_SUBMIT_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit"
DEFAULT_QUERY_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/query"
DEFAULT_RESOURCE_ID = "volc.seedasr.auc"
DONE_STATUS = "20000000"
RUNNING_STATUSES = {"20000001", "20000002"}


def read_text(path: Path | None, max_chars: int) -> str:
    if not path:
        return ""
    value = path.expanduser().resolve().read_text().strip()
    if max_chars > 0:
        value = value[:max_chars]
    return value


def context_hash(value: str) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def infer_audio_format(media: Path, explicit: str = "") -> str:
    if explicit:
        return explicit.lower()
    suffix = media.suffix.lower().lstrip(".")
    if suffix == "oga":
        return "ogg"
    if suffix in {"mp3", "wav", "ogg", "opus", "raw"}:
        return suffix
    if suffix in {"m4a", "mp4", "aac"}:
        return suffix
    mime, _ = mimetypes.guess_type(str(media))
    if mime and "/" in mime:
        return mime.rsplit("/", 1)[-1].lower()
    return "mp3"


def split_hotwords(context: str, limit: int) -> list[str]:
    if not context:
        return []
    raw = context.replace("，", "\n").replace(",", "\n").replace("、", "\n")
    terms: list[str] = []
    seen: set[str] = set()
    for part in raw.splitlines():
        for item in part.split():
            word = item.strip()
            if not word or word in seen:
                continue
            seen.add(word)
            terms.append(word)
            if limit > 0 and len(terms) >= limit:
                return terms
    return terms


def build_corpus(context: str, mode: str, hotword_limit: int) -> dict[str, str] | None:
    if not context:
        return None
    if mode == "dialog":
        payload = {
            "context_type": "dialog_ctx",
            "context_data": [{"text": context}],
        }
    else:
        payload = {"hotwords": [{"word": word} for word in split_hotwords(context, hotword_limit)]}
    return {"context": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}


def request_headers(api_key: str, resource_id: str, request_id: str, include_sequence: bool = True) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": resource_id,
        "X-Api-Request-Id": request_id,
    }
    if include_sequence:
        headers["X-Api-Sequence"] = "-1"
    return headers


def build_ssl_context(ca_bundle: Path | None, insecure_tls: bool) -> ssl.SSLContext | None:
    if insecure_tls:
        return ssl._create_unverified_context()
    if ca_bundle:
        return ssl.create_default_context(cafile=str(ca_bundle.expanduser().resolve()))
    return None


def post_json(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
    ssl_context: ssl.SSLContext | None,
) -> tuple[dict[str, str], Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context) as res:
            raw = res.read()
            body = json.loads(raw.decode("utf-8")) if raw.strip() else {}
            return dict(res.headers.items()), body
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body = json.loads(raw.decode("utf-8")) if raw.strip() else {}
        except json.JSONDecodeError:
            body = raw.decode("utf-8", errors="replace")
        headers_out = dict(exc.headers.items()) if exc.headers else {}
        status = headers_out.get("X-Api-Status-Code", str(exc.code))
        message = headers_out.get("X-Api-Message", "")
        raise RuntimeError(f"Volcengine HTTP error {exc.code}: {status} {message} {body}") from exc


def header_value(headers: dict[str, str], key: str) -> str:
    for current, value in headers.items():
        if current.lower() == key.lower():
            return value
    return ""


def submit_task(
    *,
    media: Path,
    api_key: str,
    submit_url: str,
    resource_id: str,
    request_id: str,
    audio_url: str,
    allow_data_upload: bool,
    audio_format: str,
    language: str,
    context: str,
    context_mode: str,
    hotword_limit: int,
    timeout: float,
    ssl_context: ssl.SSLContext | None,
) -> dict[str, Any]:
    audio: dict[str, Any] = {"format": audio_format}
    if language:
        audio["language"] = language
    upload_mode = "url"
    if audio_url:
        audio["url"] = audio_url
    elif allow_data_upload:
        audio["data"] = base64.b64encode(media.read_bytes()).decode("ascii")
        upload_mode = "data"
    else:
        raise SystemExit(
            "Volcengine standard ASR requires --audio-url for documented submit/query usage. "
            "Pass --allow-data-upload only for experimental local-file direct upload."
        )

    request: dict[str, Any] = {
        "model_name": "bigmodel",
        "enable_itn": True,
        "enable_punc": True,
        "show_utterances": True,
    }
    corpus = build_corpus(context, context_mode, hotword_limit)
    if corpus:
        request["corpus"] = corpus

    payload = {
        "user": {"uid": os.environ.get("USER", "interactive-video-cutter")},
        "audio": audio,
        "request": request,
    }
    headers = request_headers(api_key, resource_id, request_id)
    started = time.monotonic()
    response_headers, body = post_json(submit_url, headers, payload, timeout, ssl_context)
    status = header_value(response_headers, "X-Api-Status-Code")
    message = header_value(response_headers, "X-Api-Message")
    if status != DONE_STATUS:
        raise RuntimeError(f"Volcengine submit failed: {status or '-'} {message or '-'}")
    return {
        "task_id": request_id,
        "submit_headers": response_headers,
        "submit_body": body,
        "submit_elapsed_seconds": round(time.monotonic() - started, 3),
        "audio_upload_mode": upload_mode,
    }


def query_task(
    *,
    api_key: str,
    query_url: str,
    resource_id: str,
    request_id: str,
    poll_seconds: float,
    timeout_seconds: float,
    request_timeout: float,
    ssl_context: ssl.SSLContext | None,
) -> tuple[Any, dict[str, Any]]:
    started = time.monotonic()
    polls = 0
    last_headers: dict[str, str] = {}
    while True:
        polls += 1
        headers = request_headers(api_key, resource_id, request_id, include_sequence=False)
        response_headers, body = post_json(query_url, headers, {}, request_timeout, ssl_context)
        last_headers = response_headers
        status = header_value(response_headers, "X-Api-Status-Code")
        message = header_value(response_headers, "X-Api-Message")
        if status == DONE_STATUS:
            return body, {
                "poll_count": polls,
                "query_elapsed_seconds": round(time.monotonic() - started, 3),
                "query_headers": response_headers,
            }
        if status not in RUNNING_STATUSES:
            raise RuntimeError(f"Volcengine query failed: {status or '-'} {message or '-'}")
        if time.monotonic() - started >= timeout_seconds:
            logid = header_value(last_headers, "X-Tt-Logid")
            raise TimeoutError(f"Volcengine query timed out after {timeout_seconds:.1f}s logid={logid}")
        time.sleep(poll_seconds)


def seconds_from_ms(value: Any) -> float:
    try:
        return round(float(value) / 1000.0, 3)
    except (TypeError, ValueError):
        return 0.0


def normalize_result(raw: Any) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    payload = raw if isinstance(raw, dict) else {}
    result = payload.get("result", payload)
    if isinstance(result, list):
        result = result[0] if result else {}
    if not isinstance(result, dict):
        result = {}

    text = str(result.get("text") or payload.get("text") or "").strip()
    utterances = result.get("utterances") or payload.get("utterances") or []
    segments: list[dict[str, Any]] = []
    words: list[dict[str, Any]] = []
    if isinstance(utterances, list):
        for utterance in utterances:
            if not isinstance(utterance, dict):
                continue
            start = seconds_from_ms(utterance.get("start_time", utterance.get("start")))
            end = seconds_from_ms(utterance.get("end_time", utterance.get("end")))
            segment_text = str(utterance.get("text") or "").strip()
            if segment_text:
                segments.append({"start": start, "end": max(start, end), "text": segment_text})
            for item in utterance.get("words") or []:
                if not isinstance(item, dict):
                    continue
                word_text = str(item.get("text") or "").strip()
                if not word_text:
                    continue
                word_start = seconds_from_ms(item.get("start_time", item.get("start")))
                word_end = seconds_from_ms(item.get("end_time", item.get("end")))
                words.append({"start": word_start, "end": max(word_start, word_end), "text": word_text})
    if not text:
        text = "".join(segment["text"] for segment in segments)
    audio_info = payload.get("audio_info") if isinstance(payload.get("audio_info"), dict) else {}
    return text, segments, words, audio_info


def main() -> int:
    parser = argparse.ArgumentParser(description="Transcribe media with Volcengine Seed ASR 2.0 standard submit/query API.")
    parser.add_argument("media", type=Path, help="Local media path used for metadata, output naming, or experimental data upload")
    parser.add_argument("--audio-url", default=os.environ.get("VOLCENGINE_ASR_AUDIO_URL", ""), help="Publicly reachable audio URL; local audio.data upload is used when this is omitted")
    parser.add_argument("--allow-data-upload", action="store_true", default=True, help=argparse.SUPPRESS)
    parser.add_argument("--no-data-upload", dest="allow_data_upload", action="store_false", help="Require --audio-url instead of sending local file as base64 audio.data")
    parser.add_argument("--edit-dir", type=Path, default=None)
    parser.add_argument("--output-stem", help="Transcript JSON stem; defaults to the input media stem")
    parser.add_argument("--language", default="zh-CN")
    parser.add_argument("--format", default="", help="Audio container format; auto-detected from suffix when omitted")
    parser.add_argument("--context-file", type=Path, help="Read Volcengine corpus context or hotword terms from a text file")
    parser.add_argument("--context-mode", choices=["hotwords", "dialog"], default="hotwords")
    parser.add_argument("--max-context-chars", type=int, default=3000)
    parser.add_argument("--hotword-limit", type=int, default=5000)
    parser.add_argument("--api-key-env", default="VOLCENGINE_ASR_API_KEY")
    parser.add_argument("--resource-id", default=DEFAULT_RESOURCE_ID)
    parser.add_argument("--submit-url", default=DEFAULT_SUBMIT_URL)
    parser.add_argument("--query-url", default=DEFAULT_QUERY_URL)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--timeout-seconds", type=float, default=1800.0)
    parser.add_argument("--request-timeout", type=float, default=60.0)
    parser.add_argument("--request-id", default="")
    parser.add_argument("--ca-bundle", type=Path, help="Custom CA bundle for HTTPS verification")
    parser.add_argument("--insecure-tls", action="store_true", help="Disable HTTPS certificate verification for local proxy diagnostics only")
    args = parser.parse_args()

    media = args.media.expanduser().resolve()
    if not media.exists():
        raise SystemExit(f"media not found: {media}")
    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        raise SystemExit(f"set {args.api_key_env} before calling Volcengine ASR")
    if args.poll_seconds <= 0:
        raise SystemExit("--poll-seconds must be greater than 0")
    if args.timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds must be greater than 0")

    edit_dir = (args.edit_dir or (media.parent / "edit")).expanduser().resolve()
    transcript_dir = edit_dir / "transcripts"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    output_stem = Path(args.output_stem).name if args.output_stem else f"{media.stem}.volcengine-standard"
    out = transcript_dir / f"{output_stem}.json"
    context = read_text(args.context_file, args.max_context_chars)
    request_id = args.request_id or str(uuid.uuid4())
    audio_format = infer_audio_format(media, args.format)
    ssl_context = build_ssl_context(args.ca_bundle, args.insecure_tls)

    started = time.monotonic()
    submit_meta = submit_task(
        media=media,
        api_key=api_key,
        submit_url=args.submit_url,
        resource_id=args.resource_id,
        request_id=request_id,
        audio_url=args.audio_url.strip(),
        allow_data_upload=args.allow_data_upload,
        audio_format=audio_format,
        language=args.language,
        context=context,
        context_mode=args.context_mode,
        hotword_limit=args.hotword_limit,
        timeout=args.request_timeout,
        ssl_context=ssl_context,
    )
    print(f"submitted Volcengine ASR task: {request_id}", file=sys.stderr)
    raw_result, query_meta = query_task(
        api_key=api_key,
        query_url=args.query_url,
        resource_id=args.resource_id,
        request_id=request_id,
        poll_seconds=args.poll_seconds,
        timeout_seconds=args.timeout_seconds,
        request_timeout=args.request_timeout,
        ssl_context=ssl_context,
    )
    text, segments, words, audio_info = normalize_result(raw_result)
    payload = {
        "text": text,
        "segments": segments,
        "words": words,
        "raw": raw_result,
        "metadata": {
            "asr_backend": "volcengine",
            "provider": "volcengine-standard-2.0",
            "resource_id": args.resource_id,
            "request_id": request_id,
            "task_id": submit_meta["task_id"],
            "audio_upload_mode": submit_meta["audio_upload_mode"],
            "audio_url_used": bool(args.audio_url.strip()),
            "audio_format": audio_format,
            "language": args.language,
            "asr_context_hash": context_hash(context),
            "asr_context_chars": len(context),
            "asr_context_mode": args.context_mode if context else "",
            "insecure_tls": bool(args.insecure_tls),
            "audio_info": audio_info,
            "poll_count": query_meta["poll_count"],
            "submit_elapsed_seconds": submit_meta["submit_elapsed_seconds"],
            "query_elapsed_seconds": query_meta["query_elapsed_seconds"],
            "elapsed_seconds": round(time.monotonic() - started, 3),
        },
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"saved: {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
