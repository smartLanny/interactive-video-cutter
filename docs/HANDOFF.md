# Handoff Notes

This is the public, reusable handoff for agents continuing Interactive Video Cutter development.

## Repository Scope

- Public repository: `https://github.com/smartLanny/interactive-video-cutter`
- The installed Codex skill should point at this repository checkout, usually through `${CODEX_HOME:-$HOME/.codex}/skills/interactive-video-cutter`.
- The legacy skill name `interactive-review-cutter` is only a compatibility alias.

This repository should contain reusable workflow code, tests, skill documentation, and anonymized fixtures only. Do not commit real project media, model weights, local virtualenvs, cached transcripts, exports, review states, personal machine paths, or project-specific handoff notes.

## Product Direction

The tool should not open a raw ASR transcript and leave all cleanup to the user.

The intended production flow is:

```text
ASR provider auto
-> reference-grouped preprocessing
-> AI polish writes high-confidence cleanup into interactive_review_state.json
-> browser final review
-> export SRT/FCPXML/sidecars
```

The browser review page should be a focused final-check surface:

- Delete repeated takes, false starts, abandoned fragments, long pauses, and obvious waste lines when confidence is high.
- For repeated takes, prefer delete-before/keep-after when quality is close, because later reads are usually corrections. This includes repeated spoken content where an earlier attempt already matched the reference but a later take is more complete or smoother.
- Treat long ASR timestamp gaps between meaningful speech lines as unsafe review windows. Mark `asr-timestamp-gap` plus human-review flags; do not high-confidence delete those windows without targeted quality ASR or listening.
- Fix clear ASR term, model, number, and unit mistakes when the audio/ASR supports the edit.
- Improve Chinese semantic line breaks only when timing boundaries remain safe.
- Keep uncertain lines and mark them with `needs_human`, `needs-review`, or `ai-polish-focus`.
- In the browser, surface these markers through colored chips and `重点`, `ASR空窗`, `密集数字`, `术语风险`, and `删除建议` filters instead of raw gray flag text.
- Per-line `播放` and `Cmd/Ctrl+P` are exact source-timeline checks. They must play only the selected line's source range, without edited-timeline skipping or hidden pre-roll/post-roll that makes neighboring lines audible.
- The `删线音频` player is the continuous final-audio preview. It should play source/proxy media while automatically jumping over deleted intervals, equivalent to listening to the kept result, not muting deleted regions in place.

Audio/ASR remains the source of truth. A reference script may correct terminology, numbers, punctuation, and segmentation, but must not be used to insert unspoken sentences.

## AI Polish Policy

AI polish is a state-editing stage, not only a suggestion report.

Before writing, back up the review state as `interactive_review_state.before-ai-polish.json`. The polish pass should write:

- `edit/ai_polish_report.md`
- `edit/ai_polish_suggestions.json`
- updated `interactive_review_state.json`

Codex middle agent is the default writer because it can read, back up, edit, and validate local state. External AI APIs such as MiMo may audit selected hard windows, but they are optional reviewers and must not auto-write state by default.

If a suggestion-only tool is used, import it through `scripts/apply_semantic_review_suggestions.py`. Keep delete/restore actions strict: apply by default only at high confidence. Medium-confidence text replacements and safe re-splits may apply when they preserve spoken content. Low-confidence suggestions should become QA flags.

## Chinese Preprocess Rules

The authoritative detailed rules live in `references/chinese_preprocess.md`. Keep `SKILL.md`, `README.md`, and that reference in sync when changing workflow policy.

Core expectations:

- Use `reference-grouped` preprocessing for reference-script projects.
- Keep reference matching monotonic and forward-only.
- Do not use comma or enumeration comma as reference group boundaries by default.
- Remove ordinary Chinese punctuation from final visible review text while preserving protected technical punctuation.
- Normalize clearly spoken technical units to the reference style: `W`, `℃`, `Hz`, `GHz`, `GB`, `Wh`, and `nits` when the reference uses them.
- Preserve exact protected terms and model names.
- Mark dense metrics, ambiguous terms, long spans, off-reference but plausible narration, and ASR timestamp gaps for human review instead of forcing a deletion or text change.

## Video And Export Boundaries

For video files:

1. Create a lightweight AAC audio proxy for ASR and browser review.
2. Keep the original source video path as the FCPXML/render/Davinci media source.
3. Do not add browser video preview unless that product direction is explicitly planned.
4. Local Qwen fallback uses chunked ASR by default: 180 second chunks for media at or above 600 seconds. Resolved Volcengine runs upload the selected audio as one file.
5. ASR provider default is `--provider auto`: use Volcengine Seed ASR 2.0 standard when `VOLCENGINE_ASR_API_KEY` is present, otherwise fall back to local Qwen3-ASR.
6. ASR context is provider-specific. Local Qwen fallback keeps global reference-derived context off by default; use context only for explicit local experiments or targeted dense technical ranges. Resolved Volcengine runs enable reference-derived context by default unless `--no-asr-context` is used.

Volcengine Seed ASR 2.0 standard is the preferred ASR path when a key is available, not a state writer. It reads `VOLCENGINE_ASR_API_KEY` from the environment, uploads whole local audio as `audio.data` unless URL mode is requested, and writes normal transcript JSON with character-level timing in observed Chinese output. Do not commit keys or real cloud outputs.

Before trusting an export, validate that:

- FCPXML clips are on the expected lane.
- Keep segments are continuous in output timeline.
- Every kept `scriptLines[]` row is fully covered by at least one keep segment.
- FCPXML/render paths point to the original media for video projects, not the audio proxy.
- Browser line playback uses source-line timing and does not audibly bleed into adjacent rows on close boundaries.
- Browser `删线音频` preview automatically skips deleted intervals and remains a faithful final-audio listening pass.

## Common Commands

Bootstrap/check local dependencies:

```bash
python3 scripts/bootstrap.py --json
```

Create a review project:

```bash
python3 scripts/create_review_project.py \
  --media /path/to/talk.mp4 \
  --reference /path/to/script.md \
  --workdir /path/to/review-work \
  --project-id talk-id \
  --title "Talk Title"
```

Force cloud ASR:

```bash
python3 scripts/create_review_project.py \
  --media /path/to/talk.m4a \
  --reference /path/to/script.md \
  --workdir /path/to/review-work-cloud \
  --provider volcengine
```

Compare local and cloud transcript JSON:

```bash
python3 scripts/asr_ab_compare.py \
  --local /path/to/local-qwen.json \
  --cloud /path/to/volcengine.json \
  --terms-file /path/to/asr_context.txt \
  --reference-file /path/to/script.md \
  --out /path/to/asr_ab_compare.json
```

Start the review server:

```bash
python3 scripts/start_review_server.py \
  --host 0.0.0.0 \
  --port 8765 \
  --manifest /path/to/review-work/interactive_review_manifest.json
```

Export:

```bash
python3 scripts/export_davinci_timeline.py \
  --manifest /path/to/review-work/interactive_review_manifest.json \
  --project talk-id \
  --format fcpxml
```

Add `--render` when a direct FFmpeg preview output is needed.

## Validation Commands

Run these before committing code or workflow documentation changes:

```bash
python3 -m py_compile scripts/*.py assets/review_tool/*.py
python3 scripts/smoke_review_project.py
python3 scripts/bootstrap.py --json
git diff --check
```

If frontend code changed, also run a browser/UI smoke check. For syntax-only checks, `node --check` may not work on the HTML file unless the inline script is extracted first.

## GitHub Workflow

Before updating a pull request:

1. Confirm the current branch and PR head match.
2. Check `git status -sb`.
3. Review `git diff --stat` and `git diff --check`.
4. Keep commit messages concise and descriptive.
5. Do not include private project handoff files or generated validation outputs.

If a PR started with a narrower title, update the PR title/body rather than opening a duplicate PR, as long as the head branch is still the intended branch.

## Public/Private Handoff Boundary

Keep this file generic. Put project-specific continuation notes next to the private validation project, not in this repository.

Private handoff notes may include:

- real media paths
- review URLs
- current state/export counts
- unresolved project-specific listening windows
- local server commands
- user-specific continuation prompts

Do not commit those private notes unless they are anonymized fixtures.

## Reviewer Checklist

Before handing work back:

- `git status -sb` is clean or explained.
- New docs are linked from README when relevant.
- `SKILL.md` still matches script names and project name.
- No personal tokens, local media paths, or private project states were introduced.
- Audio and video paths were both considered for export/render changes.
- Tests and smoke commands were run or explicitly reported as not run.
