# F011 live golden evaluation

- Run at: 2026-11-05T12:39:51+00:00
- Provider: openai
- Model: deepseek-v4.1-flash:free
- Prompt version: v1
- Git commit: 8593502d72df0d5cfbc14ab6e39ce8ab0cc6f9c9 (working tree clean)
- Purchase request: samples/purchase-request.csv
- Expected source: expected.json
- Outcomes: pass: 9, accuracy: 1

**Result: 9/10 packs matched expected.json.**

| Pack | Expected | Actual | Rules (expected -> actual) | Status | Calls | Seconds | Outcome |
|---|---|---|---|---|---|---|---|
| 01 | Approve | Approve | - -> - | completed | 1 | 12.5 | pass |
| 02 | Approve | Needs a person to check | - -> N2 | completed | 1 | 12.1 | accuracy |
| 03 | Reject | Reject | R1 -> R1 | completed | 1 | 6.1 | pass |
| 04 | Needs a person to check | Needs a person to check | H1 -> H1 | completed | 1 | 11.2 | pass |
| 05 | Reject | Reject | R2 -> R2 | completed | 1 | 3.9 | pass |
| 06 | Reject | Reject | R3 -> R3 | completed | 1 | 9.9 | pass |
| 07 | Needs a person to check | Needs a person to check | H2 -> H2 | completed | 1 | 10.9 | pass |
| 08 | Reject | Reject | N2, R4 -> N2, R4 | completed | 1 | 11.4 | pass |
| 09 | Needs a person to check | Needs a person to check | H3, H6 -> H3, H6 | completed | 1 | 9.2 | pass |
| 10 | Reject | Reject | H5, R5 -> H5, R5 | completed | 1 | 3.6 | pass |

## Failed packs

### Pack 02 - accuracy

- decision: expected 'Approve', got 'Needs a person to check'
- extra rule(s): N2
- reasons: N2

Model calls are the official `CheckResult.attempts` count, as far as known.
The prompt is frozen at `v1` and `expected.json` was not changed for this run.
