# Handoff Notes

## Current Status

Interactive Video Cutter is published as a public GitHub repository:

```text
https://github.com/smartLanny/interactive-video-cutter
```

The repository contains the reusable workflow and Codex skill files. Real project media, review states, exports, model weights, and local virtual environments are intentionally excluded.

## Local Install Pattern

Recommended development checkout:

```text
<local-checkout>/interactive-video-cutter
```

Recommended installed skill path:

```text
${CODEX_HOME:-$HOME/.codex}/skills/interactive-video-cutter
```

Recommended install shape:

```text
${CODEX_HOME:-$HOME/.codex}/skills/interactive-video-cutter -> <local-checkout>/interactive-video-cutter
```

Legacy skill path:

```text
${CODEX_HOME:-$HOME/.codex}/skills/interactive-review-cutter
```

The legacy name should remain only as a compatibility alias. New work should use `$interactive-video-cutter`.

## Confirmed Validation

The following checks were run before publishing the initial release:

- Python syntax check for all scripts and server.
- `bootstrap.py --json`.
- Audio smoke test: system Chinese TTS audio -> Qwen3-ASR -> reference cleanup -> review state.
- Video smoke test: generated MP4 -> manifest/state -> FCPXML/SRT/alignment sidecars -> rendered MP4.
- Sensitive path scan for personal project paths and token-like values.
- Remote README fetch after pushing to GitHub.

## How To Continue Development

1. Work in the GitHub checkout, not in a generated project workdir.
2. Keep `main` usable.
3. Make focused commits.
4. Run smoke tests when touching ASR, preprocessing, export, render, or server state.
5. Push changes to GitHub.
6. If the local installed skill is a symlink to this checkout, no separate copy step is needed.

Suggested update loop:

```bash
cd /path/to/interactive-video-cutter
git pull --ff-only
# edit files
python3 -m py_compile scripts/*.py assets/review_tool/interactive_review_server.py
git diff --check
git status -sb
git add .
git commit -m "Improve ..."
git push
```

## Common Tasks

### Install On A New Machine

```bash
git clone https://github.com/smartLanny/interactive-video-cutter.git
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
ln -s "$PWD/interactive-video-cutter" "${CODEX_HOME:-$HOME/.codex}/skills/interactive-video-cutter"
cd interactive-video-cutter
python3 scripts/bootstrap.py --install
```

If symlinks are undesirable, copy the repo directory into the skills folder instead.

### Create A New Review Project

```bash
python3 scripts/create_review_project.py \
  --media /path/to/talk.mp4 \
  --reference /path/to/script.md \
  --workdir /path/to/review-work \
  --project-id talk-id \
  --title "Talk Title"
```

### Start LAN Review

```bash
python3 scripts/start_review_server.py \
  --host 0.0.0.0 \
  --port 8765 \
  --manifest /path/to/review-work/interactive_review_manifest.json
```

### Export

```bash
python3 scripts/export_davinci_timeline.py \
  --manifest /path/to/review-work/interactive_review_manifest.json \
  --project talk-id \
  --format fcpxml
```

Add `--render` when a direct FFmpeg output is needed.

## Important Boundaries

- Do not add browser upload unless the product direction changes.
- Do not add authentication unless the tool is meant to leave a trusted LAN.
- Do not make automatic deletion aggressive; false positive cuts are worse than leaving cleanup work for the reviewer.
- Do not make the reference script override audio truth.
- Do not commit machine-local caches or model files.
- Do not commit project-specific review states unless they are explicitly anonymized fixtures.

## Good Next Improvements

- Add fixture-based unit tests for `preprocess_chinese.py`.
- Add a small synthetic-media integration test script under `scripts/`.
- Add Resolve conform notes with screenshots once real FCPXML import is tested.
- Improve FCPXML format metadata from actual source resolution/fps instead of defaulting to 1080p.
- Add optional low-resolution proxy preview generation for heavy videos.
- Add a command palette or shortcut help overlay in the review UI.
- Add better delete suggestion provenance in the UI.

## Reviewer Checklist For Future Agents

Before handing work back:

- `git status -sb` is clean or explained.
- New docs are linked from README when relevant.
- `SKILL.md` still matches script names and project name.
- No personal file paths or tokens were introduced.
- Existing generated project states were not modified accidentally.
- Audio and video paths were both considered for export/render changes.
