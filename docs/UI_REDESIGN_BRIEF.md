# Review UI Redesign Brief

## Direction

The review page is a text-first narration editing surface. The primary task is reading, listening to, deleting, restoring, and lightly adjusting spoken script segments on the original source timeline.

Visual direction: paper manuscript editor. Text is the main object; tools stay quiet and compact.

## Required Shape

- Keep the existing single-page review app.
- Keep existing save, edit, delete-line, split, merge, candidate-take, export, render, and lock behavior.
- Prefer changes in `assets/review_tool/interactive_review_app.html`.
- Do not change ASR, preprocessing, export schema, state schema, or real project state/media/export files for this branch.

## Main Views

- `字幕`: default view, one sentence per row, text first, time metadata subdued.
- `文案`: article-like reading view, deleted content shown inline with red-brown strikethrough, light background, and duration capsules.
- `当前保留`: aggregate filter pill, not a separate view. In `字幕` it hides deleted rows; in `文案` it keeps the article view but shows only retained content.

The left navigation remains light:

- `文稿`
- `候选`
- `待审`
- `统计`

## Timeline

- One unified bottom timeline only.
- Timeline uses source time and delete intervals.
- Deleted segments render as dark blocks.
- Normal edited preview playback skips deleted segments.
- Clicking a dark block enters delete-segment inspection mode for listen, restore, and boundary nudge.
- Default bottom area keeps a 44-56 px mini source timeline plus a thin shortcut reference strip below it.
- `Cmd+B` expands the same bottom timeline into a decoded real-media waveform with range handles, speed, volume, zoom, pan, and snap controls.
- Timeline defaults to a close active-line window of roughly 20 seconds and follows the active line. Expanded waveform requests peaks for the current viewport instead of sparsely slicing full-project peaks.
- Expanded waveform zoom uses one shared viewport: zoom changes the visible time range, pan moves that range, and waveform peaks, deleted blocks, risk marks, selection, labels, and click-to-seek all render against that same viewport.
- Expanded mode must not show a second mini track above the waveform; it is one timeline in a larger form.

## Reference Build Notes

- Treat `测试 UI 方向` as the accepted visual reference for this branch.
- Line view follows the manuscript table direction: full-width rows, quiet source/output time columns, text-first reading, and a right status label.
- Per-line visible action buttons are removed from the manuscript rows; play/delete/split/merge remain shortcut-driven on the active line.
- Source/output time is hidden by default in line view and expands only after clicking the row number.
- Top review pills are aggregate filters; row-side pills are the concrete hints for that line using the same Chinese taxonomy.
- Raw ASR/LLM tag ids should not be exposed in the manuscript UI.
- Continuous view keeps article reading as the main surface and uses a lightweight audit navigation rail for review targets.
- Manuscript body text uses the built-in simplified Songti family (`Songti SC`) as the first font choice.
- Expanded audio timeline must use decoded real media waveform when available, not simulated bars.
- Do not add decorative chrome such as Mac-style window dots unless it maps to a real interaction.
- The bottom shortcut strip should list the actual available shortcuts in concise Chinese.

## Keyboard

- Existing shortcuts must remain: save, line play, delete, split, merge, undo/redo, search, line navigation.
- `Cmd+B`: expand/collapse bottom timeline.
- `Option+P`: continuous source playback from the current line through the currently visible filtered rows.
- `Cmd+Up` / `Cmd+Down`: jump to previous/next high-risk or review segment.

## Non-Goals

- No landing page.
- No dashboard-style card wall.
- No permanent right-side dual player.
- No persistent export file list.
- No browser video preview.
- No server/API change unless a UI behavior cannot work without it.

## Backend Export Note

- `/api/export`, `/api/render`, and `scripts/export_davinci_timeline.py` keep the legacy export location by default. Optional `outputDir`, `namingPrefix`, or timestamped CLI exports can write a separate handoff package under the project `exportDir`.
- `scripts/resolve_import_timeline.py` is a dry-run Resolve handoff probe by default; it only attempts to connect to the active Resolve scripting API when run with `--execute`.
