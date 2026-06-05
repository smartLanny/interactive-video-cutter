# Interactive Video Cutter

Local-first review and cutting workflow for long Chinese narration videos and audio. Give it a media file plus a reference script; it transcribes with local Qwen3-ASR, cleans the draft against the reference, opens a LAN review page, and exports subtitles, time-alignment sidecars, keep/delete segments, DaVinci/FCPXML handoff files, and optional rendered media.

This is not a SaaS app. It is designed to run on a trusted local editing/render machine and be driven by an agent or CLI.

## What It Does

- Imports audio or video from a local/NAS path.
- For video inputs, creates a lightweight AAC audio proxy for ASR and web review while keeping the original video as the edit/export source.
- Runs local Qwen3-ASR, caches transcript JSON, and writes `edit/takes_packed.md`, `edit/reference_review_report.md`, and compact review artifacts for agent review.
- Uses the reference script to fix terms, numbers, model names, punctuation, and Chinese sentence breaks.
- Runs an AI polish stage before browser review so high-confidence cleanup is already written into `interactive_review_state.json`.
- Opens a browser final-review page with one sentence per line.
- Lets reviewers toggle deletion lines with keyboard shortcuts.
- Keeps timing on the original media timeline.
- Exports both edited-timeline and original-timeline subtitles.
- Exports text/time alignment sidecars for AI labeling.
- Exports DaVinci-compatible FCPXML handoff timelines.
- Optionally renders the edited audio/video directly with FFmpeg.

## Install

Clone the repo:

```bash
git clone https://github.com/smartLanny/interactive-video-cutter.git
cd interactive-video-cutter
```

Install or check dependencies:

```bash
python3 scripts/bootstrap.py --json
python3 scripts/bootstrap.py --install
```

`bootstrap.py` checks:

- `ffmpeg` and `ffprobe`
- a Python virtual environment under `~/.local/share/interactive-video-cutter/.venv`
- `mlx-qwen3-asr` on Apple Silicon, or `qwen-asr` on other platforms
- local Hugging Face caches for `Qwen/Qwen3-ASR-0.6B` or `Qwen/Qwen3-ASR-1.7B`, plus `Qwen/Qwen3-ForcedAligner-0.6B`

The repository does not include model weights. If the machine cannot download models, pre-seed the Hugging Face cache and set `HF_HOME` or `HUGGINGFACE_HUB_CACHE`.

Video inputs are converted once to a small mono AAC proxy at `<workdir>/edit/audio/<media-stem>_asr.m4a` before ASR or transcript import. The web review page uses that audio proxy for preview and transcript correction; browser video preview is intentionally out of scope for now. The transcript JSON and chunk cache still use the original media stem, and FCPXML/render exports still reference the original video path. Pass `--no-audio-proxy` only when you need to force the old direct-video ASR path, or `--refresh-audio-proxy` to rebuild an existing proxy.

Long media is transcribed in chunks by default. Media at or above 600 seconds is split into 180 second ASR chunks, with resumable chunk JSON files under `edit/transcripts/<media-stem>.chunks/`. Tune with `--chunk-seconds` and `--chunk-threshold-seconds`, or pass `--no-chunk-transcribe` to force the whole-file ASR path against the selected ASR input.

ASR profiles are available on project creation and direct transcription:

- `--asr-profile auto`: default; uses `fast` for long media and `quality` for short media.
- `--asr-profile fast`: `Qwen/Qwen3-ASR-0.6B`, faster and useful for full-length long narration drafts.
- `--asr-profile quality`: `Qwen/Qwen3-ASR-1.7B`, slower but more accurate for dense technical ranges or final local reruns.

ASR terminology context is opt-in. `create_review_project.py --asr-context` writes `<workdir>/edit/asr_context.txt` from a compact reference-derived term list and passes it to Qwen3-ASR; `--asr-context-file` passes an explicit context file. Do not use global context as the default for long narration: it can bias or truncate first-pass ASR. Prefer no-context `fast` for the first transcript, then rerun dense technical ranges with `quality` plus a short local context when needed. Transcript caches include the context hash so context and no-context runs do not silently share outputs.

## Quick Start

Create a review project:

```bash
python3 scripts/create_review_project.py \
  --media /path/to/talk.mp4 \
  --reference /path/to/script.md \
  --workdir /path/to/review-work \
  --project-id my-talk \
  --title "My Talk"
```

For production narration, run the AI polish stage below before opening the browser. The browser should be a final-check surface for focus items, not the first cleanup pass over raw ASR.

Start the local/LAN review server:

```bash
python3 scripts/start_review_server.py \
  --host 0.0.0.0 \
  --port 8765 \
  --manifest /path/to/review-work/interactive_review_manifest.json
```

Open:

```text
http://<render-machine-ip>:8765
```

Export after review:

```bash
python3 scripts/export_davinci_timeline.py \
  --manifest /path/to/review-work/interactive_review_manifest.json \
  --project my-talk \
  --format fcpxml
```

Render an edited preview/export with FFmpeg:

```bash
python3 scripts/export_davinci_timeline.py \
  --manifest /path/to/review-work/interactive_review_manifest.json \
  --project my-talk \
  --format fcpxml \
  --render
```

## AI Polish Before Browser Review

The intended production flow is:

```text
fast ASR
-> reference-grouped preprocessing
-> Codex middle AI polish writes high-confidence cleanup into interactive_review_state.json
-> browser final review
-> SRT/FCPXML/sidecar export
```

AI polish is a state-editing stage, not only a suggestion report. Before it writes, back up the review state as `interactive_review_state.before-ai-polish.json`. The polish pass should:

- Delete repeated takes, false starts, abandoned fragments, long pauses, and obvious waste lines when confidence is high.
- For repeated takes, prefer delete-before/keep-after when quality is close, because later takes are usually corrected. Keep earlier takes only when the later take is clearly worse.
- Treat long ASR timestamp gaps as review hazards. Mark them with `asr-timestamp-gap` and human-review flags instead of allowing high-confidence deletion without targeted quality ASR or listening.
- Fix clear ASR term, model, number, and unit mistakes when the audio/ASR supports the edit.
- Improve semantic line breaks only when timing boundaries remain safe.
- Mark uncertain lines with `needs_human`, `needs-review`, or `ai-polish-focus` instead of forcing a deletion or text change.
- Write `edit/ai_polish_report.md`, `edit/ai_polish_suggestions.json`, and the updated `interactive_review_state.json`.

Codex middle agent is the default writer for this stage because it can back up, edit, and validate local files. External AI APIs such as MiMo can be used as optional auditors for selected hard windows, but they should not automatically write back to state by default. To roll back, restore `interactive_review_state.before-ai-polish.json`.

Unit normalization should follow the reference script. For technical narration, prefer forms such as `W`, `℃`, `Hz`, `GHz`, `GB`, `Wh`, and `nits` when the reference uses them; do not expand `W` to `瓦` or `℃` to `摄氏度`.

## Review UI

The page shows the polished script as editable lines:

- One sentence per line.
- Struck-through lines are cut.
- Kept lines remain in the edited output.
- Text edits are saved into `interactive_review_state.json`.
- A project lock prevents two reviewers from silently overwriting each other.
- Video projects use the extracted audio proxy for browser review; the original video remains the export/XML source.

Useful shortcuts:

- `Cmd/Ctrl + S`: save
- `Cmd/Ctrl + D`: toggle delete line
- `ArrowDown` / `ArrowUp`: move to next/previous review line
- `Enter`: split line
- `Tab` / `Shift + Tab`: move editing focus to next/previous line
- line-start `Backspace`: merge with previous line
- `Cmd/Ctrl + J`: merge with next line
- empty-line `Delete` / `Backspace`: remove the line

## Video Processing Logic

For video files, the workflow is:

1. FFmpeg extracts a lightweight AAC audio proxy from the source video for ASR and browser audio review.
2. Qwen3-ASR produces transcript text and word-level timing when available. The default first pass does not use global ASR context.
3. Optional targeted reruns may pass a short terminology context for dense technical ranges.
4. `edit/takes_packed.md` gives agents a compact transcript reading view.
5. Codex middle AI polish backs up `interactive_review_state.json`, then writes high-confidence deletes, term/number/unit fixes, and safe line cleanup directly into state.
6. Direct EDL plus take-clustering validate the polish for missing-reference gaps, ASR timestamp gaps, orphan tails, time overlaps, repeated-take chains, and dense metric/protected-term runs.
7. Only AI polish focus items, EDL/clustering conflicts, and low-confidence audio questions become browser/manual review items. Line-level `semantic_review_packets.jsonl` is a residual QA surface, not the primary review queue.
8. Structured LLM suggestions can be imported with `scripts/apply_semantic_review_suggestions.py`; by default deletes stay high-confidence only while medium-confidence text replacements and re-splits may apply.
9. External AI APIs such as MiMo are optional audit layers for selected hard windows, not default automatic state writers.
10. The reference script corrects obvious ASR issues, anchors repeated-take deletion, and guides long-line splitting without forcing unspoken text.
11. The review state stores `scriptLines[]` with original source-video `start/end` times.
12. Deleted lines become source-time delete intervals.
13. Export builds keep segments from the source timeline.
14. FCPXML uses the original video as the source asset.
15. Optional render uses FFmpeg `trim/atrim` + `concat` with 30 ms audio fades at segment boundaries.

Default video render encoding is `libx264`. Hardware encoders can be selected:

```bash
INTERACTIVE_VIDEO_CUTTER_ENCODER=h264_videotoolbox \
INTERACTIVE_VIDEO_CUTTER_VIDEO_BITRATE=8M \
python3 scripts/export_davinci_timeline.py --manifest /path/to/manifest.json --render
```

## Exported Files

Exports are written to the project `interactive_exports/` directory:

- `review_state.json`
- `script_selected_timeline.srt`
- `script_original_timeline.srt`
- `script_selected_delete_edl.json`
- `script_delete_intervals.csv`
- `script_lines.csv`
- `davinci_timeline.fcpxml`
- `davinci_handoff.json`
- `davinci_handoff_readme.txt`
- `script_selected_text_time_alignment.json`
- `script_selected_text_time_alignment.units.jsonl`
- `script_selected_text_time_alignment.units.csv`
- `script_original_text_time_alignment.json`
- `script_original_text_time_alignment.units.jsonl`
- `script_original_text_time_alignment.units.csv`
- `selected_delete_preview.mp4` or `.m4a` when rendering is enabled

## Using as a Codex Skill

Copy the repository into your Codex skills directory:

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
cp -R interactive-video-cutter "${CODEX_HOME:-$HOME/.codex}/skills/interactive-video-cutter"
```

Then ask Codex to use `$interactive-video-cutter` with a media path and reference script path.

## Development And Handoff

- [Development Guide](docs/DEVELOPMENT.md)
- [Handoff Notes](docs/HANDOFF.md)

## Security Model

- No login system is included.
- The server is meant for a trusted LAN only.
- Browser upload is intentionally not implemented; media import is done by CLI/agent from local paths.
- Manifest `allowedRoots` restricts which files the server can read or export.
- Do not expose the server directly to the public internet.

## Limitations

- Chinese narration is the primary target. Other languages may work through Qwen3-ASR but are less tested.
- Automatic deletion suggestions are conservative and still require review.
- FCPXML import should be conformed in DaVinci Resolve before final delivery.
- Model weights are downloaded or cached externally and are subject to their upstream licenses.
- The FFmpeg direct render path is useful for preview and simple delivery; Resolve/Premiere conform is still recommended for production finishing.

## 中文说明

`interactive-video-cutter` 是一个本地优先的长口播视频/音频审片剪辑工具。你给它一个本地或 NAS 上的视频/音频文件，再给一份口播参考文案，它会用本地 Qwen3-ASR 转写，再用参考文案修正术语、数字、型号、标点和中文断句，生成可在浏览器里审阅的项目。

它不是 SaaS，也不做公网账号系统。推荐运行在剪辑机、渲染机或局域网内的一台固定机器上，由 agent 或 CLI 导入项目，审阅者只在网页里做最后校对和删改。

### 主要功能

- 支持视频和音频输入。
- 自动抽取轻量 `.m4a` 音频代理做 ASR 和网页审阅预览，避免长视频转写/审阅时反复读取原始大文件。
- 用参考文案清洗 raw ASR，避免把 `618`、`DLSS 4.5`、`RTX 5070 Ti` 之类术语识别坏。
- 浏览器审阅前先由 Codex middle agent 预打磨 `interactive_review_state.json`，高置信删除线、术语数字修正和安全断句应先写回 state。
- 审阅页是一整段已预打磨文本，一句一行。
- 删除线表示这一行会被剪掉；取消删除线表示保留。
- 所有时间仍对应原始媒体时间线。
- 导出剪后时间线字幕、原始时间线字幕、逐字/逐行时间对齐 sidecar。
- 导出 DaVinci 可导入的 FCPXML 时间线。
- 可选用 FFmpeg 直接渲染剪后 MP4/M4A。

### 安装

```bash
git clone https://github.com/smartLanny/interactive-video-cutter.git
cd interactive-video-cutter
python3 scripts/bootstrap.py --json
python3 scripts/bootstrap.py --install
```

`bootstrap.py` 会检查并配置：

- `ffmpeg` / `ffprobe`
- 项目专用 Python venv
- Apple Silicon 上的 `mlx-qwen3-asr`
- 其他平台上的 `qwen-asr`
- Qwen3-ASR 和 ForcedAligner 的 Hugging Face 模型缓存

仓库不包含模型权重。无法联网下载模型的机器，需要提前拷贝 Hugging Face cache，并设置 `HF_HOME` 或 `HUGGINGFACE_HUB_CACHE`。

### 从零创建审片项目

```bash
python3 scripts/create_review_project.py \
  --media /path/to/talk.mp4 \
  --reference /path/to/script.md \
  --workdir /path/to/review-work \
  --project-id my-talk \
  --title "My Talk"
```

生产项目先做下面的 AI 预打磨，再启动网页；网页应主要用于重点听审，不是 raw ASR 的第一轮清稿面板。

启动局域网审阅页面：

```bash
python3 scripts/start_review_server.py \
  --host 0.0.0.0 \
  --port 8765 \
  --manifest /path/to/review-work/interactive_review_manifest.json
```

浏览器打开：

```text
http://<剪辑机IP>:8765
```

### AI 预打磨

正式流程不是把 raw ASR 丢给用户逐行清。创建项目后，先备份 `interactive_review_state.json` 为 `interactive_review_state.before-ai-polish.json`，再由 Codex middle agent 直接写回高置信清理结果，并输出：

- `edit/ai_polish_report.md`
- `edit/ai_polish_suggestions.json`
- 更新后的 `interactive_review_state.json`

高置信重复 take、废稿、长停顿可以直接加删除线；重复 take 质量接近时默认删前保后，因为后段通常是修正后的版本。长 ASR 词时间空窗不能当普通高置信停顿处理，要标 `asr-timestamp-gap` 并通过 targeted quality ASR 或人工听审确认。明确的术语、型号、数字和单位错误可以直接修。听不准的密集指标、长时间窗、off-reference 但可能有效的口播，标 `needs_human` / `needs-review` / `ai-polish-focus`，留到网页听审。MiMo 等外部 AI 只作为可选复审，不默认自动写回 state。

单位按参考文案统一：该写 `W`、`℃`、`Hz`、`GHz`、`GB`、`Wh`、`nits` 时不要改成 `瓦`、`摄氏度` 等口语写法。

### 审阅快捷键

- `Cmd/Ctrl + S`：保存
- `Cmd/Ctrl + D`：切换删除线
- `ArrowDown` / `ArrowUp`：切换到下一/上一条审阅行
- `Enter`：拆行
- 行首 `Backspace`：并入上一行
- `Cmd/Ctrl + J`：并入下一行
- 空行 `Delete` / `Backspace`：删除这一行

### 导出

导出 DaVinci/FCPXML、字幕和对齐文件：

```bash
python3 scripts/export_davinci_timeline.py \
  --manifest /path/to/review-work/interactive_review_manifest.json \
  --project my-talk \
  --format fcpxml
```

同时直接渲染剪后视频：

```bash
python3 scripts/export_davinci_timeline.py \
  --manifest /path/to/review-work/interactive_review_manifest.json \
  --project my-talk \
  --format fcpxml \
  --render
```

视频默认用 `libx264`。Apple Silicon 上可以用硬件编码：

```bash
INTERACTIVE_VIDEO_CUTTER_ENCODER=h264_videotoolbox \
INTERACTIVE_VIDEO_CUTTER_VIDEO_BITRATE=8M \
python3 scripts/export_davinci_timeline.py --manifest /path/to/manifest.json --render
```

### 注意事项

- 默认不做登录鉴权，只适合可信局域网。
- 不支持浏览器上传大媒体文件；项目导入由 agent/CLI 完成。
- 多人同时打开同一项目时，页面用项目编辑锁避免覆盖。
- AI 预打磨只直接应用高置信改动，最终仍以人工听审为准。
- DaVinci/Premiere 成片前建议做 conform 检查。

## License

MIT. See [LICENSE](LICENSE).
