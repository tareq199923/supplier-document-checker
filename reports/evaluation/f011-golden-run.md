# F011 live golden evaluation

- Run at: 2026-11-05T16:23:25+00:00
- Provider: openai
- Model: deepseek-v4.1-flash:free
- Prompt version: v1
- Git commit: a9f325a85fc78a8023ef4e871846e5b6b1976704 (working tree clean)
- Purchase request: samples/purchase-request.csv
- Expected source: expected.json
- Outcomes: pass: 10

**Result: 10/10 packs matched expected.json.**

| Pack | Expected | Actual | Rules (expected -> actual) | Status | Calls | Seconds | Outcome |
|---|---|---|---|---|---|---|---|
| 01 | Approve | Approve | - -> - | completed | 1 | 13.9 | pass |
| 02 | Approve | Approve | - -> - | completed | 1 | 9.8 | pass |
| 03 | Reject | Reject | R1 -> R1 | completed | 1 | 15.2 | pass |
| 04 | Needs a person to check | Needs a person to check | H1 -> H1 | completed | 1 | 28.2 | pass |
| 05 | Reject | Reject | R2 -> R2 | completed | 1 | 17.4 | pass |
| 06 | Reject | Reject | R3 -> R3 | completed | 1 | 10.7 | pass |
| 07 | Needs a person to check | Needs a person to check | H2 -> H2 | completed | 1 | 37.8 | pass |
| 08 | Reject | Reject | N2, R4 -> N2, R4 | completed | 1 | 12.2 | pass |
| 09 | Needs a person to check | Needs a person to check | H3, H6 -> H3, H6 | completed | 1 | 10.9 | pass |
| 10 | Reject | Reject | H5, R5 -> H5, R5 | completed | 1 | 10.1 | pass |

All ten packs matched `expected.json`.

Model calls are the official `CheckResult.attempts` count, as far as known.
The prompt is frozen at `v1` and `expected.json` was not changed for this run.
