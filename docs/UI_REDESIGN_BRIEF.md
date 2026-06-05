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

- `分行`: default view, one sentence per row, text first, time metadata subdued.
- `连续`: article-like reading view, deleted content shown inline with red-brown strikethrough, light background, and duration capsules.

The left navigation remains light:

- `文稿`
- `候选`
- `质检`
- `统计`

## Timeline

- One unified bottom timeline only.
- Timeline uses source time and delete intervals.
- Deleted segments render as dark blocks.
- Normal edited preview playback skips deleted segments.
- Clicking a dark block enters delete-segment inspection mode for listen, restore, and boundary nudge.
- Mini bottom timeline should stay around 44-56 px.
- `Cmd+B` expands the bottom audio area to show waveform skeleton, range handles, zoom, speed, volume, and snap controls.

## Reference Build Notes

- Treat `测试 UI 方向` as the accepted visual reference for this branch.
- Line view follows the manuscript table direction: full-width rows, quiet source/output time columns, text-first reading, and a right status label.
- Per-line visible action buttons are removed from the manuscript rows; play/delete/split/merge remain shortcut-driven on the active line.
- Continuous view keeps article reading as the main surface and uses a lightweight audit navigation rail for review targets.

## Keyboard

- Existing shortcuts must remain: save, line play, delete, split, merge, undo/redo, search, line navigation.
- `Cmd+B`: expand/collapse bottom timeline.
- `Cmd+Up` / `Cmd+Down`: jump to previous/next high-risk or review segment.

## Non-Goals

- No landing page.
- No dashboard-style card wall.
- No permanent right-side dual player.
- No persistent export file list.
- No browser video preview.
- No server/API change unless a UI behavior cannot work without it.
