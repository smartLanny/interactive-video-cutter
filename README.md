# Interactive Video Cutter

Local-first review and cutting workflow for long Chinese narration videos and audio. Give it a media file plus a reference script; it transcribes with local Qwen3-ASR, cleans the draft against the reference, opens a LAN review page, and exports subtitles, time-alignment sidecars, keep/delete segments, DaVinci/FCPXML handoff files, and optional rendered media.

This is not a SaaS app. It is designed to run on a trusted local editing/render machine and be driven by an agent or CLI.

## What It Does

- Imports audio or video from a local/NAS path.
- Runs local Qwen3-ASR, caches transcript JSON, and writes `edit/takes_packed.md` for agent review.
- Uses the reference script to fix terms, numbers, model names, punctuation, and Chinese sentence breaks.
- Opens a browser review page with one sentence per line.
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
- local Hugging Face caches for `Qwen/Qwen3-ASR-1.7B` and `Qwen/Qwen3-ForcedAligner-0.6B`

The repository does not include model weights. If the machine cannot download models, pre-seed the Hugging Face cache and set `HF_HOME` or `HUGGINGFACE_HUB_CACHE`.

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

## Review UI

The page shows the whole script as editable lines:

- One sentence per line.
- Struck-through lines are cut.
- Kept lines remain in the edited output.
- Text edits are saved into `interactive_review_state.json`.
- A project lock prevents two reviewers from silently overwriting each other.

Useful shortcuts:

- `Cmd/Ctrl + S`: save
- `Cmd/Ctrl + D`: toggle delete line
- `Enter`: split line
- line-start `Backspace`: merge with previous line
- `Cmd/Ctrl + J`: merge with next line
- empty-line `Delete` / `Backspace`: remove the line

## Video Processing Logic

For video files, the workflow is:

1. FFmpeg extracts audio from the source video for ASR.
2. Qwen3-ASR produces transcript text and word-level timing when available.
3. `edit/takes_packed.md` gives agents a compact transcript reading view.
4. The reference script corrects obvious ASR issues without forcing unspoken text.
5. The review state stores `scriptLines[]` with original source-video `start/end` times.
6. Deleted lines become source-time delete intervals.
7. Export builds keep segments from the source timeline.
8. FCPXML uses the original video as the source asset.
9. Optional render uses FFmpeg `trim/atrim` + `concat` with 30 ms audio fades at segment boundaries.

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
- 自动抽取视频音轨做 ASR。
- 用参考文案清洗 raw ASR，避免把 `618`、`DLSS 4.5`、`RTX 5070 Ti` 之类术语识别坏。
- 审阅页是一整段文本，一句一行。
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

### 审阅快捷键

- `Cmd/Ctrl + S`：保存
- `Cmd/Ctrl + D`：切换删除线
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
- 自动删除线只是初步建议，最终仍以人工审阅为准。
- DaVinci/Premiere 成片前建议做 conform 检查。

## License

MIT. See [LICENSE](LICENSE).
