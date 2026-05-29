---
name: interactive-video-cutter
description: Use for portable local long-form Chinese narration video/audio review workflows: bootstrap ASR/ffmpeg dependencies, transcribe media, create reference-corrected review projects, launch the LAN review page, and export subtitles, ASR time-alignment sidecars, rendered previews, and DaVinci/FCPXML handoff timelines.
---

# Interactive Video Cutter

Use this skill when the user wants to open, initialize, or export the local review UI for conservative line-based video or audio cutting.

Core rules:
- Treat `interactive_review_state.json` as the human review source of truth.
- The browser page must receive preprocessed content, not raw ASR. Follow `references/chinese_preprocess.md`: cache ASR, correct terms/numbers/model names from the reference script, segment by Chinese meaning, use the reference script to mark repeated takes, and pre-mark conservative deletion lines.
- Audio is still source of truth. The reference script corrects terminology and punctuation/segmentation; it must not force text that was not spoken.
- Video files use a lightweight AAC audio proxy by default for ASR and web review playback; FCPXML/export still references the original video media path. Do not add browser video preview unless explicitly planned.
- Long media uses chunked ASR by default: 180 second chunks for media at or above 600 seconds, cached under `edit/transcripts/<media-stem>.chunks/`.
- Imported skeleton lines default to kept unless an input explicitly marks a line deleted. For production projects, prefer alignment JSON or a pre-cleaned state over plain reference text.
- Prefer the bundled scripts over rewriting glue code.

Scripts:
- `scripts/bootstrap.py`: checks ffmpeg/ffprobe, bundled transcription helper, Python ASR packages, and can best-effort install deps with `--install`.
- `scripts/create_review_project.py`: from media + reference script, creates a small video audio proxy when needed, runs transcription, creates manifest/state/workdir, and writes `edit/takes_packed.md` for compact agent review.
- `scripts/preprocess_chinese.py`: turns Qwen transcript JSON into timed review lines, applies reference-script terminology/number/model cleanup, splits long review lines, and marks reference-matched repeated takes for deletion.
- `scripts/start_review_server.py`: starts the bundled review server with a manifest.
- `scripts/export_davinci_timeline.py`: loads the server module and writes the standard export package, including DaVinci FCPXML and subtitle alignment sidecars.
- `scripts/import_review_project.py`: builds a manifest plus optional initial state from preprocessed text/timing inputs.

Typical commands:

```bash
SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/interactive-video-cutter"
python3 "$SKILL_DIR/scripts/bootstrap.py" --install
python3 "$SKILL_DIR/scripts/create_review_project.py" --media /path/to/talk.mp4 --reference /path/to/script.md --workdir /path/to/review-work
python3 "$SKILL_DIR/scripts/start_review_server.py" --host 0.0.0.0 --manifest /path/to/review-work/interactive_review_manifest.json
python3 "$SKILL_DIR/scripts/export_davinci_timeline.py" --manifest /path/to/review-work/interactive_review_manifest.json --format fcpxml
```

For a new review project, call `import_review_project.py` with explicit `--media`, `--manifest-out`, and `--state-out`. Include `--transcript-json`, `--alignment-json`, `--srt`, or `--reference-text` only when those artifacts already exist.

New project workflow:
1. Run `bootstrap.py --json`; if deps are missing, run `bootstrap.py --install` or follow its printed manual actions.
2. Run `create_review_project.py --media <audio-or-video> --reference <script.md> --workdir <review-workdir>`. For video, this first writes `<workdir>/edit/audio/<media-stem>_asr.m4a`; the review page uses this audio for human second-pass segmentation and correction. When ASR is needed it then uses the bundled transcribe helper and local Qwen3-ASR package, and finally runs the bundled Chinese preprocessing layer.
3. Use `edit/takes_packed.md` for fast agent reading, then inspect `interactive_review_state.json` for obvious missed protected terms or bad matches before sharing the page. Do not leave raw ASR in production review projects.
4. Start the LAN server with `start_review_server.py --host 0.0.0.0 --manifest <review-workdir>/interactive_review_manifest.json`.
5. After browser review, export with the page or `export_davinci_timeline.py --manifest <review-workdir>/interactive_review_manifest.json --format fcpxml`.
6. Deliver rendered media when requested, FCPXML, SRT, keep segments, handoff JSON/readme, and `*_text_time_alignment*` sidecars for AI marking.

Portability notes:
- The review server, browser app, Chinese preprocessing script, and transcription helper are bundled in this skill under `assets/` and `scripts/`.
- The bundled server intentionally has no production default project. Always start it through `start_review_server.py` with an explicit project manifest.
- Python packages and Qwen model weights are not bundled. `bootstrap.py --install` installs packages when network/package indexes are available; model weights can also be preseeded in the machine's Hugging Face cache.
- `bootstrap.py --json` reports both `ok` and `offlineReady`. `ok` means the code path can run and may download models; `offlineReady` means both Qwen ASR and forced-aligner model caches were found locally.
- If a machine cannot download model weights, copy the Hugging Face cache manually and set `HF_HOME` or `HUGGINGFACE_HUB_CACHE` before transcription.
