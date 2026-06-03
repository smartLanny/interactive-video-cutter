---
name: interactive-video-cutter
description: "Use for portable local long-form Chinese narration video/audio review workflows: bootstrap ASR/ffmpeg dependencies, transcribe media, create reference-corrected review projects, launch the LAN review page, and export subtitles, ASR time-alignment sidecars, rendered previews, and DaVinci/FCPXML handoff timelines."
---

# Interactive Video Cutter

Use this skill when the user wants to open, initialize, or export the local review UI for conservative line-based video or audio cutting.

Core rules:
- Treat `interactive_review_state.json` as the human review source of truth.
- The browser page must receive preprocessed content, not raw ASR. Follow `references/chinese_preprocess.md`: cache ASR, use reference-script semantic units before ASR hard cuts, correct terms/numbers/model names from the reference script, group reference-script takes when available, and pre-mark repeated takes/pause gaps/false starts/abandoned fragments.
- For long narration, default to the fast review flow: middle-thinking `Direct EDL` first creates a keep/delete/review plan, then `take clustering` validates reference gaps, orphan tails, time overlaps, and dense metric runs. Only conflicts and review items go to browser/manual audio review. Do not use hundreds of line-level risk packets as the primary review surface.
- Audio is still source of truth. The reference script corrects terminology and punctuation/segmentation; it must not force text that was not spoken.
- Video files use a lightweight AAC audio proxy by default for ASR and web review playback; FCPXML/export still references the original video media path. Do not add browser video preview unless explicitly planned.
- Long media uses chunked ASR by default: 180 second chunks for media at or above 600 seconds, cached under `edit/transcripts/<media-stem>.chunks/`.
- ASR profiles: `--asr-profile auto` is the default and uses `fast` for long media and `quality` for short media; `fast` uses `Qwen/Qwen3-ASR-0.6B`; `quality` uses `Qwen/Qwen3-ASR-1.7B` for dense technical ranges or final local reruns.
- ASR context is opt-in. Do not pass global reference-derived context on the default first pass; it can bias or truncate ASR. Use `--asr-context-file` or `create_review_project.py --asr-context` only for explicit experiments or targeted dense technical ranges, preferably with a separate output stem/edit dir.
- Imported skeleton lines default to kept unless an input explicitly marks a line deleted. For production projects, prefer alignment JSON or a pre-cleaned state over plain reference text.
- Prefer the bundled scripts over rewriting glue code.

Scripts:
- `scripts/bootstrap.py`: checks ffmpeg/ffprobe, bundled transcription helper, Python ASR packages, and can best-effort install deps with `--install`.
- `scripts/create_review_project.py`: from media + reference script, creates a small video audio proxy when needed, optionally writes `edit/asr_context.txt` for explicit context runs, runs transcription, creates manifest/state/workdir, and writes `edit/takes_packed.md`, `edit/reference_review_report.md`, and `edit/semantic_review_packets.jsonl` for compact agent review.
- `scripts/preprocess_chinese.py`: turns Qwen transcript JSON into timed review lines, aligns reference semantic units to ASR word timestamps in source order, applies reference-script terminology/number/model cleanup, creates optional `referenceReview` take groups, splits long review lines, and marks repeated takes or pause/breath gaps for deletion.
- `scripts/apply_semantic_review_suggestions.py`: imports structured LLM semantic suggestions into `interactive_review_state.json`. By default, delete/restore apply only at `high` confidence while replace/re-split apply at `high` or `medium`; low-confidence suggestions stay as QA flags.
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
2. Run `create_review_project.py --media <audio-or-video> --reference <script.md> --workdir <review-workdir>`. For video, this first writes `<workdir>/edit/audio/<media-stem>_asr.m4a`; the review page uses this audio for human second-pass segmentation and correction. The default ASR pass uses no global context so the first transcript stays closest to the audio. Use `--asr-context` only for explicit reference-derived context experiments, and use `--asr-context-file` for targeted local reruns. Reference-script projects default to `--preprocess-mode reference-grouped`; that mode aligns reference semantic units to ASR words in source order before display-line splitting. Use `--preprocess-mode flat` only when you want the older line list behavior.
3. Use `edit/takes_packed.md`, `edit/reference_review_report.md`, and `interactive_review_state.json` for fast agent reading. For long narration projects, spawn a middle-thinking Direct EDL subagent first. It should output a compact keep/delete/review plan with evidence `lineIds`, not a full line-by-line rewrite.
4. Validate the Direct EDL plan with a take-clustering pass over the same state. Clustering should catch missing-reference gaps, orphan tails, truncated/time-overlap lines, repeated take chains, and dense metric/protected-term runs. Direct EDL and clustering agreement can be auto-applied; conflicts remain review items.
5. Use `semantic_review_packets.jsonl` only as a residual QA or regression surface after the fast EDL path, not as the primary review queue. If obvious kept errors remain, run one focused residual pass; after that, send unresolved issues to browser review instead of looping.
6. Apply structured suggestions with `apply_semantic_review_suggestions.py --state <workdir>/interactive_review_state.json --suggestions <suggestions.json>`. Keep the default action-aware behavior: deletions stay strict (`high` only), but text replacements and semantic re-splits may apply at `medium` when they preserve spoken content. Low-confidence suggestions remain QA flags for browser review.
7. In the browser, review only the EDL/clustering `reviewItems` plus any pending low-confidence audio questions. Use `文案候选` for reference-unit review and `全文行` for line-level cleanup. `Command+Z` / `Ctrl+Z` undo current-session edits; `Command+Shift+Z` / `Ctrl+Shift+Z` redo.
8. Start the LAN server with `start_review_server.py --host 0.0.0.0 --manifest <review-workdir>/interactive_review_manifest.json`.
9. After browser review, export with the page or `export_davinci_timeline.py --manifest <review-workdir>/interactive_review_manifest.json --format fcpxml`.
10. Deliver rendered media when requested, FCPXML, SRT, keep segments, handoff JSON/readme, and `*_text_time_alignment*` sidecars for AI marking.

Portability notes:
- The review server, browser app, Chinese preprocessing script, and transcription helper are bundled in this skill under `assets/` and `scripts/`.
- The bundled server intentionally has no production default project. Always start it through `start_review_server.py` with an explicit project manifest.
- Python packages and Qwen model weights are not bundled. `bootstrap.py --install` installs packages when network/package indexes are available; model weights can also be preseeded in the machine's Hugging Face cache.
- `bootstrap.py --json` reports both `ok` and `offlineReady`. `ok` means the code path can run and may download models; `offlineReady` means both Qwen ASR and forced-aligner model caches were found locally.
- If a machine cannot download model weights, copy the Hugging Face cache manually and set `HF_HOME` or `HUGGINGFACE_HUB_CACHE` before transcription.
