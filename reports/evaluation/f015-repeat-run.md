# F015 repeat-run consistency evaluation

- Run at: 2026-11-05T17:20:47+00:00
- Provider: openai
- Model: deepseek-v4.1-flash:free
- Prompt version: v1
- Repeats: 3
- Git commit: 2ff12347410f1394d82b3ab07d01c83981322dcf (working tree clean)
- Purchase request: samples/purchase-request.csv
- Expected source: expected.json
- Categories: stable-and-accurate: 10, stable-but-inaccurate: 0, flaky: 0, infrastructure-or-harness: 0
- Hidden flakiness: 0 pack(s) with a stable result but a passing retry (attempts == 2)

**Result: 30/30 pack runs matched expected.json; 10/10 packs stable.**

| Pack | Expected | Stable | Category | Hidden retry | Retry runs |
|---|---|---|---|---|---|
| 01 | Approve | True | stable-and-accurate | False | - |
| 02 | Approve | True | stable-and-accurate | False | - |
| 03 | Reject | True | stable-and-accurate | False | - |
| 04 | Needs a person to check | True | stable-and-accurate | False | - |
| 05 | Reject | True | stable-and-accurate | False | - |
| 06 | Reject | True | stable-and-accurate | False | - |
| 07 | Needs a person to check | True | stable-and-accurate | False | - |
| 08 | Reject | True | stable-and-accurate | False | - |
| 09 | Needs a person to check | True | stable-and-accurate | False | - |
| 10 | Reject | True | stable-and-accurate | False | - |

## Per-run results

| Pack | Run | Status | Outcome | Decision | Rules | Attempts |
|---|---|---|---|---|---|---|
| 01 | 1 | completed | pass | Approve | - | 1 |
| 01 | 2 | completed | pass | Approve | - | 1 |
| 01 | 3 | completed | pass | Approve | - | 1 |
| 02 | 1 | completed | pass | Approve | - | 1 |
| 02 | 2 | completed | pass | Approve | - | 1 |
| 02 | 3 | completed | pass | Approve | - | 1 |
| 03 | 1 | completed | pass | Reject | R1 | 1 |
| 03 | 2 | completed | pass | Reject | R1 | 1 |
| 03 | 3 | completed | pass | Reject | R1 | 1 |
| 04 | 1 | completed | pass | Needs a person to check | H1 | 1 |
| 04 | 2 | completed | pass | Needs a person to check | H1 | 1 |
| 04 | 3 | completed | pass | Needs a person to check | H1 | 1 |
| 05 | 1 | completed | pass | Reject | R2 | 1 |
| 05 | 2 | completed | pass | Reject | R2 | 1 |
| 05 | 3 | completed | pass | Reject | R2 | 1 |
| 06 | 1 | completed | pass | Reject | R3 | 1 |
| 06 | 2 | completed | pass | Reject | R3 | 1 |
| 06 | 3 | completed | pass | Reject | R3 | 1 |
| 07 | 1 | completed | pass | Needs a person to check | H2 | 1 |
| 07 | 2 | completed | pass | Needs a person to check | H2 | 1 |
| 07 | 3 | completed | pass | Needs a person to check | H2 | 1 |
| 08 | 1 | completed | pass | Reject | N2, R4 | 1 |
| 08 | 2 | completed | pass | Reject | N2, R4 | 1 |
| 08 | 3 | completed | pass | Reject | N2, R4 | 1 |
| 09 | 1 | completed | pass | Needs a person to check | H3, H6 | 1 |
| 09 | 2 | completed | pass | Needs a person to check | H3, H6 | 1 |
| 09 | 3 | completed | pass | Needs a person to check | H3, H6 | 1 |
| 10 | 1 | completed | pass | Reject | H5, R5 | 1 |
| 10 | 2 | completed | pass | Reject | H5, R5 | 1 |
| 10 | 3 | completed | pass | Reject | H5, R5 | 1 |

## Distributions

| Pack | Decision distribution | Rule distribution |
|---|---|---|
| 01 | Approve: 3 | -: 3 |
| 02 | Approve: 3 | -: 3 |
| 03 | Reject: 3 | R1: 3 |
| 04 | Needs a person to check: 3 | H1: 3 |
| 05 | Reject: 3 | R2: 3 |
| 06 | Reject: 3 | R3: 3 |
| 07 | Needs a person to check: 3 | H2: 3 |
| 08 | Reject: 3 | N2, R4: 3 |
| 09 | Needs a person to check: 3 | H3, H6: 3 |
| 10 | Reject: 3 | H5, R5: 3 |
Stable means identical (status, outcome, decision, rule IDs) across all runs; attempts are excluded.
Infrastructure/harness failures are reported in their own category and are not counted as flakiness.
No automatic retry is performed by this harness.
The prompt is frozen at `v1`.
