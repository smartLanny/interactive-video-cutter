# Development Guide

This document is for agents or developers continuing work on Interactive Video Cutter.

## Source Of Truth

- GitHub repository: `https://github.com/smartLanny/interactive-video-cutter`
- Local development checkout: any clone of the GitHub repository
- Recommended local skill install: symlink or copy this repo to `${CODEX_HOME:-$HOME/.codex}/skills/interactive-video-cutter`
- Legacy local skill name: `interactive-review-cutter`; keep only as a compatibility alias.

Treat the GitHub repo as the authoritative codebase. Do not patch only the installed skill copy unless you also port the change back to the repo.

## Project Shape

```text
interactive-video-cutter/
├── SKILL.md
├── README.md
├── agents/openai.yaml
├── assets/review_tool/
│   ├── interactive_review_app.html
│   └── interactive_review_server.py
├── references/chinese_preprocess.md
├── scripts/
│   ├── bootstrap.py
│   ├── create_review_project.py
│   ├── export_davinci_timeline.py
│   ├── import_review_project.py
│   ├── preprocess_chinese.py
│   ├── start_review_server.py
│   ├── transcribe_qwen3.py
│   ├── transcribe_volcengine.py
│   └── asr_ab_compare.py
└── docs/
```

## Main Workflow

1. `scripts/bootstrap.py`
   - Checks `ffmpeg`, `ffprobe`, ASR Python package, bundled transcription helper, and Qwen model caches.
   - Creates a venv under `~/.local/share/interactive-video-cutter/.venv` when `--install` is used.
   - Prefer Python 3.12/3.11 for ML dependencies.

2. `scripts/create_review_project.py`
   - Takes `--media`, `--reference`, and `--workdir`.
   - For video inputs, creates `<workdir>/edit/audio/<media-stem>_asr.m4a` and uses that small AAC proxy for ASR and browser review playback.
   - Passes the probed source-media duration into the manifest so source-tail timing is not truncated to the last transcript line.
   - Runs ASR unless `--transcript-json --skip-transcribe` is provided.
   - Optionally builds `<workdir>/edit/asr_context.txt` from reference-script terminology. For local Qwen, `--asr-context` or `--asr-context-file` is opt-in and the default first pass does not use global ASR context. For `--provider volcengine`, reference-derived context is enabled by default unless `--no-asr-context` is used.
   - Supports `--provider qwen` for local default transcription and `--provider volcengine` for Volcengine Seed ASR 2.0 standard submit/query comparison. Volcengine reads `VOLCENGINE_ASR_API_KEY` from the environment and uploads the whole selected audio as `audio.data` unless URL mode is requested.
   - Calls `import_review_project.py` to create manifest and state.
   - Writes `<workdir>/edit/takes_packed.md` as a compact phrase-level transcript for agent review.
   - Writes `<workdir>/edit/reference_review_report.md` and `<workdir>/edit/semantic_review_packets.jsonl`. Long-form review should use Direct EDL plus take-clustering validation as the primary review path; packets are a residual QA surface.

3. AI polish before browser review
   - Back up `<workdir>/interactive_review_state.json` to `<workdir>/interactive_review_state.before-ai-polish.json` before writing.
   - Codex middle agent is the default writer. It should directly apply high-confidence repeated-take deletes, false-start deletes, obvious term/number/unit fixes, and safe semantic cleanup to `interactive_review_state.json`.
   - Repeated-take cleanup uses delete-before/keep-after when take quality is close. Later takes are preferred unless they are clearly incomplete, lower confidence, or semantically wrong.
   - Long ASR word-time gaps are review hazards. Preprocessing marks them with `asr-timestamp-gap` plus human-review flags; AI polish must not treat those windows as high-confidence deletes without targeted quality ASR or listening.
   - Suggestions-only is not enough for the intended workflow; the browser should open a pre-polished state, not a raw transcript.
   - Write `<workdir>/edit/ai_polish_report.md` and `<workdir>/edit/ai_polish_suggestions.json` for audit and rollback context.
   - Mark uncertain dense metrics, off-reference but plausible narration, long time spans, low match score, or risky term/number edits with `needs_human`, `needs-review`, or `ai-polish-focus`.
   - Normalize units to the reference style when clearly spoken: `W`, `℃`, `Hz`, `GHz`, `GB`, `Wh`, `nits`. Do not expand `W` to `瓦` or `℃` to `摄氏度` when the reference uses symbolic units.
   - External AI APIs such as MiMo are optional auditors for selected hard windows. They are not default automatic state writers.

4. `scripts/apply_semantic_review_suggestions.py`
   - Applies structured LLM semantic review suggestions to `interactive_review_state.json`.
   - Defaults to action-aware confidence: delete/restore require `high`, replace/re-split allow `high` or `medium`.
   - Marks low-confidence suggestions, and medium-confidence delete/restore suggestions, as QA flags for browser review instead of applying them.

5. `scripts/transcribe_qwen3.py`
   - Extracts mono 16 kHz temporary audio chunks with FFmpeg from the selected ASR input.
   - Runs `mlx-qwen3-asr` on Apple Silicon or `qwen-asr` when requested.
   - Writes transcript JSON to `<workdir>/edit/transcripts/<media-stem>.json`.
   - Uses 180 second chunks for media at or above 600 seconds, caching chunks in `<media-stem>.chunks/`.
   - Accepts `--context` / `--context-file`; transcript and chunk caches include the context hash, including the empty no-context hash.

6. `scripts/transcribe_volcengine.py`
   - Calls Volcengine Seed ASR 2.0 standard submit/query with resource id `volc.seedasr.auc`.
   - Reads only `VOLCENGINE_ASR_API_KEY` or the configured `--api-key-env`; never hard-code keys in commands, docs, tests, or fixtures.
   - Sends whole local audio as base64 `audio.data` by default; `--audio-url --no-data-upload` keeps URL-only mode available.
   - Accepts `--context-file` and serializes it into `request.corpus.context` as hotwords by default.
   - Writes the same transcript JSON shape as local ASR: `text`, `segments`, `words`, and `metadata`.

7. `scripts/asr_ab_compare.py`
   - Compares local and cloud transcript JSON files for speed, text length, term hits, reference-fragment matches, and suspicious missing segments.
   - Use it on short targeted windows before deciding whether a full cloud run is worth the cost/time.

8. `scripts/preprocess_chinese.py`
   - Converts ASR `words`, `segments`, or `text` into timed review lines.
   - Uses reference text to correct terms, numbers, model names, punctuation, and sentence breaks.
   - Uses reference matches to mark repeated takes for deletion and split long comma-heavy review lines.
   - Flags long timestamp gaps between meaningful speech lines as `asr-timestamp-gap` so pause rows remain visible for focused review.
   - Keeps audio/video ASR as source of truth; reference text must not add unspoken content.

9. `assets/review_tool/interactive_review_server.py`
   - Serves the local review page and media.
   - Saves `interactive_review_state.json`.
   - Provides editing locks.
   - Exports SRT, CSV, JSON, alignment sidecars, DaVinci handoff, and FCPXML.
   - Renders preview/output media when requested.

10. `assets/review_tool/interactive_review_app.html`
   - Single-file browser UI.
   - One sentence per line.
   - Struck-through lines are cut.
   - Keyboard-first editing.
   - Renders `qaFlags` as colored chips and provides `全部`, `重点`, `ASR空窗`, `密集数字`, `术语风险`, and `删除建议` filters.
   - Search includes visible text, source notes, raw flags, and Chinese risk labels.

## Data Model

Project manifest:

- `allowedRoots`: hard file access boundary.
- `projects[]`: project definitions.
- `statePath`: one state file per project.
- `exportDir`: one export directory per project.
- `mediaType`: `audio` or `video`.
- `sourceMedia`: original source media.
- `draftMedia`: optional review proxy media, typically the extracted AAC audio proxy for large videos.
- `davinciMediaPath`: optional path visible to the editing machine.

Review state:

- `scriptLines[]`: primary editable review state.
- `scriptLines[].text`: user-facing cleaned text.
- `scriptLines[].start/end`: original media timeline seconds.
- `scriptLines[].deleted`: whether that line should be cut.
- `scriptLines[].qaFlags`: QA markers such as `needs_human`, `needs-review`, and `ai-polish-focus` for browser final review.
- `useScriptLines`: should remain true for the current workflow.

Exports:

- `script_selected_timeline.srt`: subtitles on edited timeline.
- `script_original_timeline.srt`: subtitles on original source timeline.
- `script_selected_text_time_alignment.*`: selected timeline text/time sidecars.
- `script_original_text_time_alignment.*`: original timeline text/time sidecars.
- `davinci_timeline.fcpxml`: importable editing timeline.
- `selected_delete_preview.mp4/.m4a`: optional FFmpeg render.

Project setup also writes `<workdir>/edit/takes_packed.md` as a lightweight transcript reading view and `<workdir>/edit/semantic_review_packets.jsonl` for focused LLM follow-up. The fast review path is reference-grouped preprocessing, Codex middle AI polish direct state write, Direct EDL/take-clustering validation, then browser review only for focus items, conflicts, and low-confidence audio questions.

## Video Logic

Video import and export are intentionally conservative:

- ASR uses an extracted AAC audio proxy by default for video inputs.
- Browser review uses the proxy audio when it exists. Browser video preview is intentionally deferred; the original video remains the export source, not the review playback default.
- Visible review text is generated from ASR plus reference cleanup.
- All review line timings stay on the original source-video timeline.
- Deletions become source-time intervals.
- Keep segments are calculated as the inverse of delete intervals.
- FCPXML references the original media asset.
- Direct render uses FFmpeg `trim/atrim` and `concat`, with 30 ms audio fades at each keep-segment boundary.

Default render encoder is `libx264`. For Apple Silicon preview renders:

```bash
INTERACTIVE_VIDEO_CUTTER_ENCODER=h264_videotoolbox \
INTERACTIVE_VIDEO_CUTTER_VIDEO_BITRATE=8M \
python3 scripts/export_davinci_timeline.py --manifest /path/to/manifest.json --render
```

## Local Development Commands

Syntax check:

```bash
python3 -m py_compile scripts/*.py assets/review_tool/interactive_review_server.py
```

Dependency status:

```bash
python3 scripts/bootstrap.py --json
```

Long-media ASR dry run:

```bash
python3 scripts/transcribe_qwen3.py --help
python3 scripts/transcribe_volcengine.py --help
python3 scripts/create_review_project.py --help | rg 'chunk|audio-proxy|no-chunk|provider|volcengine'
```

Volcengine A/B transcript comparison:

```bash
python3 scripts/asr_ab_compare.py \
  --local /path/to/local-qwen.json \
  --cloud /path/to/volcengine.json \
  --terms-file /path/to/asr_context.txt \
  --reference-file /path/to/script.md \
  --out /path/to/asr_ab_compare.json
```

Review workflow smoke test:

```bash
python3 scripts/smoke_review_project.py
```

This uses a generated temp video, a tiny transcript JSON, and a delete interval CSV.
It does not run ASR, download models, or touch real project media. It verifies project
creation, state/manifest writing, SRT/FCPXML/alignment export, DaVinci handoff files,
and direct preview rendering.

```bash
python3 scripts/smoke_review_project.py --keep-temp
```

Use `--keep-temp` only when you need to inspect the generated project after a failure.

Sensitive path scan before publishing:

```bash
rg -n '(/Users/|/Volumes/|gho_|HF_TOKEN\s*=|HUGGINGFACE_HUB_TOKEN\s*=|password\s*=|secret\s*=)' . || true
```

## Development Rules

- Do not commit media, transcripts from real projects, local state files, export output, venvs, or model weights.
- Keep `SKILL.md` concise. Put developer-facing details in `docs/`.
- Keep browser upload out of scope unless explicitly planned; import should remain CLI/agent-driven.
- Keep public-internet access out of scope; the server assumes a trusted LAN.
- Do not silently expand `allowedRoots`.
- Do not overwrite an existing review state unless the user explicitly asks.
- Do not make external AI APIs default automatic writers until they pass fixture gates for false deletes, unsafe reference insertion, and missed `needs_human` items. Volcengine ASR is a transcription comparison provider, not a state writer.
- Do not commit API keys, request headers, real cloud transcript outputs, or project-specific A/B reports.
- When changing export behavior, test both audio and video paths.
- When changing text preprocessing or AI polish, test protected terms such as `618`, `DLSS 4.5`, `RTX 5070 Ti`, `HDMI2.1`, `DP1.4`, plus units such as `W`, `℃`, `Hz`, `GHz`, `GB`, `Wh`, and `nits`.

## Release Flow

```bash
git status -sb
python3 -m py_compile scripts/*.py assets/review_tool/interactive_review_server.py
python3 scripts/smoke_review_project.py
python3 scripts/bootstrap.py --json
git diff --check
git add .
git commit -m "Describe the change"
git push
```

After a release, verify:

```bash
gh repo view smartLanny/interactive-video-cutter --json nameWithOwner,url,visibility,defaultBranchRef
curl -L https://raw.githubusercontent.com/smartLanny/interactive-video-cutter/main/README.md | sed -n '1,40p'
```
