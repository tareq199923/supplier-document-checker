# F015 synthetic quotation evaluation

- Run at: 2026-11-05T17:14:49+00:00
- Provider: openai
- Model: deepseek-v4.1-flash:free
- Prompt version: v1
- Git commit: 2ff12347410f1394d82b3ab07d01c83981322dcf (working tree clean)
- Purchase request: samples/purchase-request.csv
- Expected source: tests/synthetic_cases.json
- Outcomes: pass: 20

**Result: 20/20 synthetic cases matched their expected decision and rule IDs.**

| Case | Expected | Actual | Rules (expected -> actual) | Status | Calls | Seconds | Outcome |
|---|---|---|---|---|---|---|---|
| S01 | Approve | Approve | - -> - | completed | 1 | 13.2 | pass |
| S02 | Needs a person to check | Needs a person to check | H1 -> H1 | completed | 1 | 15.6 | pass |
| S03 | Reject | Reject | R1 -> R1 | completed | 1 | 32.5 | pass |
| S04 | Reject | Reject | R2 -> R2 | completed | 1 | 19.5 | pass |
| S05 | Needs a person to check | Needs a person to check | H4 -> H4 | completed | 1 | 10.6 | pass |
| S06 | Reject | Reject | R5 -> R5 | completed | 1 | 10.4 | pass |
| S07 | Approve | Approve | - -> - | completed | 1 | 12.0 | pass |
| S08 | Needs a person to check | Needs a person to check | H7 -> H7 | completed | 1 | 12.1 | pass |
| S09 | Needs a person to check | Needs a person to check | H2 -> H2 | completed | 1 | 9.1 | pass |
| S10 | Needs a person to check | Needs a person to check | H3 -> H3 | completed | 1 | 13.0 | pass |
| S11 | Needs a person to check | Needs a person to check | H5 -> H5 | completed | 1 | 40.5 | pass |
| S12 | Needs a person to check | Needs a person to check | H6 -> H6 | completed | 1 | 29.5 | pass |
| S13 | Reject | Reject | R3 -> R3 | completed | 1 | 10.3 | pass |
| S14 | Needs a person to check | Needs a person to check | N1 -> N1 | completed | 1 | 12.7 | pass |
| S15 | Needs a person to check | Needs a person to check | N2 -> N2 | completed | 1 | 30.5 | pass |
| S16 | Approve | Approve | - -> - | completed | 1 | 24.3 | pass |
| S17 | Reject | Reject | R4 -> R4 | completed | 1 | 11.6 | pass |
| S18 | Approve | Approve | - -> - | completed | 1 | 8.8 | pass |
| S19 | Reject | Reject | R1 -> R1 | completed | 1 | 10.4 | pass |
| S20 | Needs a person to check | Needs a person to check | M -> M | completed | 1 | 12.0 | pass |

All synthetic cases matched their expected decision and rule IDs.

Model calls are the official `CheckResult.attempts` count, as far as known.
The prompt is frozen at `v1`; no expected.json or rule was changed for this run.
