# Handoff Notes

This document is the primary handoff for agents continuing Interactive Video Cutter.

## Current Repository

- Public repository: `https://github.com/smartLanny/interactive-video-cutter`
- Local checkout: `/Users/lann/Downloads/interactive-video-cutter`
- Current working branch: `codex/add-review-smoke-script`
- GitHub workflow: continue the existing Draft PR #1 unless the user explicitly asks for a new branch or PR.
- Installed skill source of truth: `${CODEX_HOME:-$HOME/.codex}/skills/interactive-video-cutter -> /Users/lann/Downloads/interactive-video-cutter`
- Legacy skill name `interactive-review-cutter` should remain only as a compatibility alias.

The repository contains reusable workflow code and skill documentation. Do not commit real project media, model weights, local virtualenvs, or project-specific review states unless they are intentionally anonymized fixtures.

## Current Product Direction

The tool should not open a raw ASR transcript and leave all cleanup to the user.

The intended flow is:

```text
fast ASR
-> reference-grouped preprocessing
-> AI polish writes high-confidence cleanup directly into interactive_review_state.json
-> browser final review
-> export SRT/FCPXML/sidecars
```

The AI polish stage should create a browser-ready review state:

- Delete repeated takes, false starts, abandoned fragments, long pauses, and obvious waste lines.
- For repeated takes, prefer delete-before/keep-after when quality is close, because later reads are usually corrected.
- Treat long ASR timestamp gaps between meaningful speech lines as unsafe review windows. Mark `asr-timestamp-gap` plus human-review flags; do not high-confidence delete those windows without targeted quality ASR or listening.
- Fix clear ASR term and number mistakes.
- Improve Chinese semantic line breaks when timing boundaries are safe.
- Keep uncertain lines and mark them with `needs_human`, `needs-review`, or `ai-polish-focus`.
- Leave the browser page as a focused final-check surface, not a raw transcript.

Audio/ASR remains the source of truth. A reference script may correct terminology, numbers, punctuation, and segmentation, but must not be used to insert unspoken sentences.

## Current Validation Project

The current real project is the `幻16Air` voice memo review.

- Stable audio: `/Users/lann/Downloads/interactive-video-cutter-validation/zephyrus-g16-air/media/幻16Air.m4a`
- Reference script: `/Users/lann/Downloads/interactive-video-cutter-validation/zephyrus-g16-air/reference_script.md`
- Workdir: `/Users/lann/Downloads/interactive-video-cutter-validation/zephyrus-g16-air/review-fast-20260604`
- Manifest: `/Users/lann/Downloads/interactive-video-cutter-validation/zephyrus-g16-air/review-fast-20260604/interactive_review_manifest.json`
- Current state: `/Users/lann/Downloads/interactive-video-cutter-validation/zephyrus-g16-air/review-fast-20260604/interactive_review_state.json`
- State before AI polish: `/Users/lann/Downloads/interactive-video-cutter-validation/zephyrus-g16-air/review-fast-20260604/interactive_review_state.before-ai-polish.json`
- AI polish report: `/Users/lann/Downloads/interactive-video-cutter-validation/zephyrus-g16-air/review-fast-20260604/edit/ai_polish_report.md`
- AI polish suggestions: `/Users/lann/Downloads/interactive-video-cutter-validation/zephyrus-g16-air/review-fast-20260604/edit/ai_polish_suggestions.json`
- Review URL: `http://127.0.0.1:8774/?project=zephyrus-g16-air-fast`

If the review server is not running:

```bash
cd /Users/lann/Downloads/interactive-video-cutter
python3 scripts/start_review_server.py --host 127.0.0.1 --port 8774 --manifest /Users/lann/Downloads/interactive-video-cutter-validation/zephyrus-g16-air/review-fast-20260604/interactive_review_manifest.json
```

## Current Processing Status

Completed for the `幻16Air` project:

- Fetched the Feishu reference document to `reference_script.md`.
- Copied the Voice Memos temporary audio to the stable media path.
- Created the review project with the fastest validated local ASR route:
  - `Qwen3-ASR-0.6B`
  - `--asr-profile fast`
  - 180 second ASR chunks
  - no global ASR context
  - `--preprocess-mode reference-grouped`
- Initial state:
  - 645 script lines
  - 155 deleted lines
  - 91 pause/breath lines
- A Codex middle subagent directly polished and wrote back the state:
  - text fixes: 68 lines
  - additional/high-confidence deletes: 130 lines
  - restore: 0 lines
  - kept cues compressed from 490 to 360
  - current deleted lines: 285
  - current focus/review lines: about 118

Important: the AI polish step already wrote to `interactive_review_state.json`. Do not re-run ASR or overwrite this state unless there is a clear reason. Use `interactive_review_state.before-ai-polish.json` for comparison or rollback.

## Current State Audit

Checked on 2026-06-04:

- Current state still uses `useScriptLines: true`.
- Backup state has 645 `scriptLines`; current state has 649 `scriptLines` because AI polish split several dense lines into safer review lines.
- Initial AI polish changed deleted lines from 155 to 285, matching the 130 additional high-confidence deletes in the report.
- `edit/ai_polish_suggestions.json` has 103 suggestion records: high-confidence deletes, high/medium replacements, and low-confidence `flag_only` items.

Audit conclusion:

- High-confidence deletes are directionally reasonable: they target repeated takes, false starts, abandoned fragments, long pauses, and older attempts covered by later complete takes.
- The report correctly leaves dense metric windows and long-span/off-reference areas for human listening instead of forcing final text.
- Minor count mismatch: report says `Text fixes: 68`, while suggestion JSON has 69 replace records. Deletion counts and kept cue counts match.

Targeted state fix on 2026-06-05:

- Backup before targeted fix: `interactive_review_state.before-targeted-fix-20260605-0012.json`.
- `#28` was changed from `通过HDR TrueBlack 1000认证` to `通过 HDR 1000 认证` to avoid reference-only insertion; it remains marked for listening.
- `#646` was normalized to `1100nits`; `#169` was normalized to `95℃`.
- `#171` residual temperature fragment was deleted.
- `#313` / `#314` were restored so the browser keeps the `250W` adapter and `100W PD` small-adapter point; both remain marked for listening.
- `#604` was restored so the browser keeps the "整理邮件/发邮件也都可以" point; it remains marked for listening.
- `#174-#204`, `#245-#254`, and `#481` now have focus/review flags for dense performance, battery, and HDR-always-on checks.
- Current state after targeted fix: 649 `scriptLines`, 283 deleted lines, 366 kept `scriptLines`, and 366 synced `cues`.

## AI Route Decisions

Current best route for making a usable browser review page:

```text
fast ASR
-> reference-grouped preprocessing
-> Codex middle agent directly polishes interactive_review_state.json
-> browser final review
```

External MiMo API findings:

- `mimo-v2.5 thinking disabled`: fast in tiny samples, but over-confident on a 15-window fixture. It missed many `needs_human` cases and produced false deletes. Do not make it the default writer.
- `mimo-v2.5 thinking enabled`: better as optional accurate review for hard windows.
- `mimo-v2.5-pro thinking`: can pass a hard single-window test with a high token budget, but is too slow and token-heavy for routine review. Use only as complex fallback.
- Codex middle agent is currently best for directly editing local state because it can read/write files, back up state, validate JSON, and reason about the project format.

Recommended product modes:

1. Local low-cost mode: fast ASR + preprocessing + browser review.
2. AI pre-polish mode: Codex middle agent directly edits state before browser review.
3. External AI quick audit: MiMo v2.5 no-thinking only as a suggestion layer for selected windows, never automatic state write.
4. External AI accurate audit: MiMo v2.5 thinking or Codex/GPT medium for hard windows and release QA.
5. Complex fallback: MiMo v2.5-pro thinking for a very small number of hardest windows, with high token budget and parse validation.

## Skill And Docs Status

Updated on 2026-06-04 so future agents use the improved workflow by default.

Covered files:

- `SKILL.md`
- `README.md`
- `docs/DEVELOPMENT.md`
- `references/chinese_preprocess.md`

Covered content:

- Explicit `AI polish before browser review` stage.
- AI polish directly writes high-confidence changes to `interactive_review_state.json` after backing it up.
- Suggestions-only is not enough for the intended workflow.
- External AI APIs are optional auditors, not default automatic writers.
- Rollback through `interactive_review_state.before-ai-polish.json`.
- Expected outputs:
  - `edit/ai_polish_report.md`
  - `edit/ai_polish_suggestions.json`
  - updated `interactive_review_state.json`
- When to mark `needs_human` instead of changing text or deletion status.

## Text And Unit Normalization Rules

Future AI polish should normalize obvious ASR mistakes and units according to the reference script and Chinese subtitle rules.

For this project, prefer reference-style units:

- Power: `W`, not `瓦`, when the reference uses `W`.
- Temperature: `℃`, not `摄氏度`, when the reference uses `℃`.
- Frequency: `Hz`, `GHz`.
- Memory, storage, and capacity: `GB`, `Wh`.
- Brightness: follow the project reference style consistently, such as `nits` or `尼特`.
- Keep technical terms intact and do not split them.

Common protected terms for the current `幻16Air` project:

```text
幻16Air
ROG
5070 Ti
64GB
PCIe 5.0
OLED
2.5K 240Hz
HDR TrueBlack 1000
Ultra 9 386H
Panther Lake
18A
50 TOPS
4P+8E+4LPE
285H
HX370
液金
Time Spy
枪神 10
CS2
Dota2
DLSS 帧生成
PCMark 10
Cinebench 2024
V-Ray
APL
EOTF
Delta E
SVM
JNCD
GTG
NPU
PTL
Mac
M5 Max
```

Only apply text replacements when the spoken content supports the replacement. Do not insert full reference sentences that are absent from the ASR/audio.

## Current Review Focus

The AI polish report lists the main areas for the user to listen-check:

- `00:10-00:17` line `#8`: opening 2026/update/price joke may be incomplete.
- `01:02-01:20` lines `#25-#31`: OLED/HDR/Ultra 9/Panther Lake/18A/50 TOPS/core configuration.
- `03:52-04:03` lines `#92-#98`: Time Spy/18200/枪神10/5070 Ti/8% metrics.
- `06:47-07:25` lines `#155-#164`: CS2, 120W, 100W, CPU power sharing, 386H/5070Ti.
- `07:43-08:30` lines `#166-#171`: R23 loop, 71-73W, 95℃ area, possible missing sentence.
- `10:56-11:13` lines `#223-#241`: PCMark 10, HX370, 2.7W, 285H 1/3, 8000 score.
- `20:29-22:00` lines `#358-#362`: A-cover badge segment has long time spans and repeats.
- `25:36-27:06` lines `#438-#476`: display brightness, EOTF, APL, Delta E, HDR/SDR metrics.
- `27:49-29:31` lines `#484-#526`: white point, 1931 2°/2015 10°, blue light, SVM, JNCD.
- `30:43-31:12` lines `#553-#569`: GTG 0.54ms, 240Hz, 3.38ms, HDR summary.
- `32:48-33:35` lines `#589-#607`: PTL/NPU/AI assistant/skills/LOG terminology.
- `34:48-35:00` lines `#618-#639`: Mac/M5 Max/10000/285H ending comparison.

## Common Commands

Create a new review project:

```bash
python3 scripts/create_review_project.py \
  --media /path/to/talk.mp4 \
  --reference /path/to/script.md \
  --workdir /path/to/review-work \
  --project-id talk-id \
  --title "Talk Title"
```

Start review server:

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

Add `--render` when a direct FFmpeg output is needed.

## Validation Commands

After docs or code changes:

```bash
cd /Users/lann/Downloads/interactive-video-cutter
python3 -m py_compile scripts/*.py assets/review_tool/interactive_review_server.py
python3 scripts/smoke_review_project.py
python3 scripts/bootstrap.py --json
git diff --check
```

If frontend was changed:

```bash
node --check assets/review_tool/interactive_review_app.html
```

Validate current review project API:

```bash
curl -s -o /tmp/ivc_api_data.json -w '%{http_code} %{size_download}\n' 'http://127.0.0.1:8774/api/data?project=zephyrus-g16-air-fast'
python3 -m json.tool /tmp/ivc_api_data.json >/dev/null
```

## GitHub Sync

After updating skill/docs and validating:

```bash
cd /Users/lann/Downloads/interactive-video-cutter
git status
git diff
python3 -m py_compile scripts/*.py assets/review_tool/interactive_review_server.py
python3 scripts/smoke_review_project.py
python3 scripts/bootstrap.py --json
git diff --check
git add .
git commit -m "完善 AI 预打磨审阅流程"
git push
```

Keep the commit message in Chinese and describe what changed.

## Important Boundaries

- Do not add browser upload unless the product direction changes.
- Do not add authentication unless the tool is meant to leave a trusted LAN.
- Do not let the reference script override audio truth.
- Do not commit machine-local caches or model files.
- Do not commit project-specific review states unless they are explicitly anonymized fixtures.
- Do not make external AI APIs default automatic writers until they pass the expanded fixture gate without false deletes, unsafe reference insertion, or missed `needs_human` items.

## Prompt For Future Browser Review

Use this prompt when starting a new agent for final browser review or targeted state polishing:

```text
继续 /Users/lann/Downloads/interactive-video-cutter 的工作。当前分支 codex/add-review-smoke-script，继续沿用 GitHub Draft PR #1。

先阅读 docs/HANDOFF.md。当前幻16Air 项目已经完成 fast ASR 和 Codex middle AI polish，项目在：
/Users/lann/Downloads/interactive-video-cutter-validation/zephyrus-g16-air/review-fast-20260604

你的任务：
1. 打开 http://127.0.0.1:8774/?project=zephyrus-g16-air-fast 做最终网页听审。
2. 优先检查 docs/HANDOFF.md 里的 Current Review Focus 窗口。
3. 如果要继续写回 state，先备份当前 `interactive_review_state.json`，只做明确目标改动。
4. 单位按参考文案统一：W、℃、Hz、GHz、GB、Wh、nits，不要把 W 写成“瓦”、℃ 写成“摄氏度”。
5. 不要重新 ASR，除非用户明确要求。
```

## Reviewer Checklist For Future Agents

Before handing work back:

- `git status -sb` is clean or explained.
- New docs are linked from README when relevant.
- `SKILL.md` still matches script names and project name.
- No personal tokens were introduced.
- Existing generated project states were not modified accidentally.
- Audio and video paths were both considered for export/render changes.
