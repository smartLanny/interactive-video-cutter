# Chinese Preprocess Rules

Use these rules before writing `scriptLines` for the review page.

- Audio/ASR is the source of truth. The reference script corrects terminology, model names, numbers, punctuation, and segmentation only.
- Do not send raw ASR to the browser for human review.
- Segment by Chinese meaning units: topic turns, predicates, modifiers, dense spec clauses, and spoken rhythm.
- Preserve exact terms such as `618`, `DLSS 4.5`, `HDMI2.1`, `DP1.4`, `RTX 5070 Ti`, percentages, nits, Hz, Wh, W, and model names from the reference script when they are clearly spoken.
- Remove obvious ASR punctuation noise from visible review text.
- Pre-mark deletion lines conservatively: repeated takes, false starts, abandoned fragments, long gaps, and lines explicitly covered by delete interval CSV.
- Keep `scriptLines[].start/end` on the original media timeline.
- Export both selected/output timeline and original/source timeline text-time alignment sidecars after human review.
