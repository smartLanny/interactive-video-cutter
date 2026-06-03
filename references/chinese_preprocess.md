# Chinese Preprocess Rules

Use these rules before writing `scriptLines` for the review page.

- Audio/ASR is the source of truth. The reference script corrects terminology, model names, numbers, punctuation, and segmentation only.
- Do not send raw ASR to the browser for human review.
- ASR context is opt-in. The default first pass should not use global reference-derived context; use terms, model names, ratios, percentages, units, brand/product names, and protected technical punctuation only for targeted reruns or explicit context experiments.
- Segment by Chinese meaning units: topic turns, predicates, modifiers, dense spec clauses, and spoken rhythm.
- When a reference script exists, default to `reference-grouped` preprocessing: build reference semantic units first, then align those units to ASR word timestamps in source order while keeping `scriptLines` as the export source of truth.
- Do not use comma/顿号 as reference group boundaries by default. `，` and `、` are display-line split candidates, not take-group boundaries. Only split long reference sentences at semantic turns such as topic changes, result/evaluation clauses, or discourse markers.
- Reference matching must be monotonic. Search from the current ASR cursor forward in a local window; do not globally fuzzy-match every ASR line against every reference line. Cross-document matches may be reported as QA hints, but must not auto-bind a take.
- High-confidence matches may use reference text for visible correction; medium-confidence matches should keep spoken text, apply protected-term cleanup, and mark QA flags; low-confidence or off-reference speech must not be forced into the reference script.
- Deterministic preprocessing is not enough for production narration review. After preprocessing, use a middle-thinking Direct EDL pass to create a compact keep/delete/review plan, then validate it with take clustering. Use `edit/semantic_review_packets.jsonl` for residual QA or regression checks, not as the primary human review queue.
- LLM semantic suggestions must be structured as `lineIds`, `action`, `confidence`, `reason`, and optional `text` or `splitTexts`. Apply delete/restore only at `high` confidence by default. Apply replace/re-split at `high` or `medium` when the edit preserves spoken content and fixes ASR or segmentation. Keep `low` suggestions as QA flags.
- Use strict confidence: obvious false starts and repeated abandoned takes can be `high` deletes; protected-term punctuation and clear reference-corrected wording can be `high` or `medium` replacements; possible intentional off-reference口播 should be `medium`, `low`, or `flag_only`.
- Keep review lines subtitle-sized. Do not leave obvious long clauses with multiple punctuation marks in one line; split them before browser review.
- Preserve exact terms such as `TrueRGB`, `RGB-MiniLED`, `BT.2020`, `QD-miniLED`, `WOLED`, `Judd Offset`, `1931 2度 D65`, `2015 10度`, `618`, `DLSS 4.5`, `HDMI2.1`, `DP1.4`, `RTX 5070 Ti`, percentages, nits, Hz, Wh, W, and model names from the reference script when they are clearly spoken.
- Preserve protected punctuation inside technical terms. Do not clean `BT.2020` into `BT2020`, `HDMI2.1` into `HDMI21`, or `DP1.4` into `DP14`.
- Treat ASR context terms as decoder hints only. They may improve local technical terms but can also bias or truncate ASR; downstream review must still reject terms that were not actually spoken.
- Remove obvious ASR punctuation noise from visible review text. Final visible review/SRT text should not show ordinary Chinese commas, full stops, enumeration commas, semicolons, colons, question marks, or exclamation marks unless they are part of a protected technical term.
- Pre-mark deletion lines conservatively: repeated takes, false starts, abandoned fragments, long gaps, and lines explicitly covered by delete interval CSV.
- Treat same-reference prefix attempts as false starts when a later complete take clearly covers the same semantic unit. Treat repeated spoken percentage tails such as `百分之六十` after `60%` as duplicate fragments.
- For repeated takes matched to the same reference unit, pre-mark all but the best take as deleted; users can restore any candidate in the browser.
- If several reference units in a row do not match, widen the local search window to resync, but keep the match forward-only. Unmatched ASR should remain visible as `off-reference` for human review.
- The alignment window must be wide enough to skip false starts and short repeated attempts inside a reference unit, then bind the later complete take when its score is clearly better.
- In `文案候选`, single-take reference units are not real candidates. Show them as matched reference rows with play/delete controls only; show "keep this take" controls only when a group has multiple takes.
- Add long silence/breath gaps as `lineType: "pause"` rows with text like `停顿/气口 1.3 秒`, pre-marked deleted by default.
- If a browser edit removes text from a timed line, preserve the timing as a deletion line instead of dropping the timed region.
- Keep `scriptLines[].start/end` on the original media timeline.
- Write `edit/reference_review_report.md` so agents can review suggested deletes, unmatched speech, QA flags, protected-term risks, and pause cuts before opening the browser.
- Write `edit/semantic_review_packets.jsonl` for residual middle-thinking LLM review. Prefer reference-group packets when a risky line belongs to a reference sentence. Packets should include the full reference group, risk lines, nearby context, current delete state, QA flags, match score, and strict review instructions. For long narration, the primary review artifact should be Direct EDL review items plus clustering validator conflicts; packets are for focused follow-up only.
- Export both selected/output timeline and original/source timeline text-time alignment sidecars after human review.
