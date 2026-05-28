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
│   └── transcribe_qwen3.py
└── docs/
```

## Main Workflow

1. `scripts/bootstrap.py`
   - Checks `ffmpeg`, `ffprobe`, ASR Python package, bundled transcription helper, and Qwen model caches.
   - Creates a venv under `~/.local/share/interactive-video-cutter/.venv` when `--install` is used.
   - Prefer Python 3.12/3.11 for ML dependencies.

2. `scripts/create_review_project.py`
   - Takes `--media`, `--reference`, and `--workdir`.
   - Runs ASR unless `--transcript-json --skip-transcribe` is provided.
   - Calls `import_review_project.py` to create manifest and state.

3. `scripts/transcribe_qwen3.py`
   - Extracts mono 16 kHz audio with FFmpeg.
   - Runs `mlx-qwen3-asr` on Apple Silicon or `qwen-asr` when requested.
   - Writes transcript JSON to `<workdir>/edit/transcripts/<media-stem>.json`.

4. `scripts/preprocess_chinese.py`
   - Converts ASR `words`, `segments`, or `text` into timed review lines.
   - Uses reference text to correct terms, numbers, model names, punctuation, and sentence breaks.
   - Keeps audio/video ASR as source of truth; reference text must not add unspoken content.

5. `assets/review_tool/interactive_review_server.py`
   - Serves the local review page and media.
   - Saves `interactive_review_state.json`.
   - Provides editing locks.
   - Exports SRT, CSV, JSON, alignment sidecars, DaVinci handoff, and FCPXML.
   - Renders preview/output media when requested.

6. `assets/review_tool/interactive_review_app.html`
   - Single-file browser UI.
   - One sentence per line.
   - Struck-through lines are cut.
   - Keyboard-first editing.

## Data Model

Project manifest:

- `allowedRoots`: hard file access boundary.
- `projects[]`: project definitions.
- `statePath`: one state file per project.
- `exportDir`: one export directory per project.
- `mediaType`: `audio` or `video`.
- `sourceMedia`: original source media.
- `davinciMediaPath`: optional path visible to the editing machine.

Review state:

- `scriptLines[]`: primary editable review state.
- `scriptLines[].text`: user-facing cleaned text.
- `scriptLines[].start/end`: original media timeline seconds.
- `scriptLines[].deleted`: whether that line should be cut.
- `useScriptLines`: should remain true for the current workflow.

Exports:

- `script_selected_timeline.srt`: subtitles on edited timeline.
- `script_original_timeline.srt`: subtitles on original source timeline.
- `script_selected_text_time_alignment.*`: selected timeline text/time sidecars.
- `script_original_text_time_alignment.*`: original timeline text/time sidecars.
- `davinci_timeline.fcpxml`: importable editing timeline.
- `selected_delete_preview.mp4/.m4a`: optional FFmpeg render.

## Video Logic

Video import and export are intentionally conservative:

- ASR uses extracted audio only.
- Visible review text is generated from ASR plus reference cleanup.
- All review line timings stay on the original source-video timeline.
- Deletions become source-time intervals.
- Keep segments are calculated as the inverse of delete intervals.
- FCPXML references the original media asset.
- Direct render uses FFmpeg `trim/atrim` and `concat`.

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

Audio smoke test:

```bash
tmp=$(mktemp -d)
say -v Tingting -o "$tmp/test.aiff" "欢迎收看装机宅六一八笔记本推荐"
printf '欢迎收看装机宅 618 笔记本推荐。\n' > "$tmp/ref.txt"
python3 scripts/create_review_project.py \
  --media "$tmp/test.aiff" \
  --reference "$tmp/ref.txt" \
  --workdir "$tmp/review" \
  --project-id smoke-audio \
  --title SmokeAudio
```

Video export smoke test:

```bash
tmp=$(mktemp -d)
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i testsrc=size=640x360:rate=30:duration=2 \
  -f lavfi -i sine=frequency=1000:sample_rate=44100:duration=2 \
  -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest "$tmp/test.mp4"
printf '第一句保留。\n第二句删除。\n' > "$tmp/ref.txt"
cat > "$tmp/transcript.json" <<'JSON'
{"segments":[{"start":0.0,"end":0.9,"text":"第一句保留。"},{"start":1.0,"end":1.8,"text":"第二句删除。"}]}
JSON
cat > "$tmp/delete.csv" <<'CSV'
start,end,duration,summary,reason,source
1.0,1.8,0.8,第二句删除,test,test
CSV
python3 scripts/create_review_project.py \
  --media "$tmp/test.mp4" \
  --reference "$tmp/ref.txt" \
  --workdir "$tmp/review" \
  --project-id smoke-video \
  --title SmokeVideo \
  --transcript-json "$tmp/transcript.json" \
  --delete-csv "$tmp/delete.csv" \
  --skip-transcribe
python3 scripts/export_davinci_timeline.py \
  --manifest "$tmp/review/interactive_review_manifest.json" \
  --project smoke-video \
  --render
```

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
- When changing export behavior, test both audio and video paths.
- When changing text preprocessing, test protected terms such as `618`, `DLSS 4.5`, `RTX 5070 Ti`, `HDMI2.1`, `DP1.4`.

## Release Flow

```bash
git status -sb
python3 -m py_compile scripts/*.py assets/review_tool/interactive_review_server.py
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
