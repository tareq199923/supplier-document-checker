# AI-LOG: how I used AI tools

I used an AI coding agent (Claude) for planning, tests and code, and I reviewed every step.
My rule: the agent makes a plan first, I approve it, tests come before code, and nobody edits a
test or `expected.json` to make something pass. This file lists mistakes the AI made and how I
caught them.

## Mistakes the AI made, and how they were caught

1. **"pytest must pass" with no tests.** An early plan said the tests must pass, but the tests
   folder was empty, so there was nothing to run. Caught by reading the plan against the folder.
2. **A live check before the code existed.** An early plan proposed a live extraction check before
   `extractor.py` existed. Caught in review; the order was fixed (tests, code, then live check).
3. **Expired approval tokens.** Tool approvals expired in the middle of a task and some writes had
   to be redone. Lesson: confirm each action promptly and check the file afterwards.
4. **A quoting error that looked like a code bug.** A one-line Python check failed because of
   PowerShell quotes, not because the code was wrong. Fixed by avoiding inner double quotes.
5. **The agent's tool shell was not PowerShell.** A command with `; ` failed with "path not found"
   because the tool runs commands in `cmd.exe`. Fixed by using `cd /d ... && ...`. The test file
   itself was fine.
6. **Planning work that was already done, and nearly overwriting real tests.** The agent wrote a
   plan and new tests for Gate 2 (`validators.py`), but Gate 2 was already built, hardened and
   committed. Its staged write would have replaced my real `tests/test_validators.py` (29 KB) with
   a smaller one (19 KB). Caught before it was confirmed, because the agent checked `git status`,
   `git log` and the folder listing first. The approval had also expired, so nothing was lost.
   Lesson: read the current state of the repo before planning, and never trust a summary alone.
7. **A bug in the agent's own test helper.** A new test fixture tried to attach an attribute to a
   plain Python list, so 5 tests errored while 34 passed. Caught by running pytest straight away
   and fixed in the test helper only; no assertion was changed and the code was not touched.
8. **A memory claim checked against the file.** The agent said the Anthropic output cap was 2,000
   tokens. A code search found nothing, so it re-read `llm.py` before editing. The claim was right,
   and the cap was raised to 4,096 only after that check.
9. **Incomplete workflow-stage metadata in F008.** The first implementation tagged PDF-read and
   Gate 1 errors but omitted the `extract` and `compare` stages. The new tests exposed the omission:
   7 of 74 result tests failed, including LLM/CSV classification and sanitisation cases. The agent
   added the two missing stage values and reran the suite. A later audit also found that the CSV
   loader can wrap raw operating-system read details in `PurchaseRequestError`; a new regression
   test caught that those details could reach the public result. The result layer now replaces
   that specific raw OS detail with a fixed message. Lesson: test every workflow error branch and
   inspect the source of exception messages, not just the exception class.

10. **Implementation written before its tests (F010).** After the helper tests were green, the agent
    prepared `app.py` before `tests/test_app.py` existed, which breaks the tests-first rule. Caught
    by the agent itself when it checked its own next step against that rule; the staged write was
    declined before it landed, `test_app.py` was written and confirmed red, and only then was
    `app.py` written. Lesson: check each write against the order tests, red, code, green.
11. **A button that stayed disabled (F010).** The first `app.py` drew the Check button disabled
    during the run that performs the check. A page test that clicks twice failed with "disabled
    widget", which showed that in a real browser the button would stay greyed out after the check
    finished. The page now draws a disabled copy while the check runs and swaps in the normal
    button in the same slot afterwards. The test was not changed. Lesson: a status flag that
    disables a control must be cleared in the same script run, not only on the next one.
12. **A test helper that returned an exception instead of raising it (F015).** The fake checker in
    `run_with` returned a `RuntimeError` object. The runner correctly catches raised exceptions,
    but because the helper did not raise, the test later failed with `AttributeError:
    'RuntimeError' object has no attribute 'reasons'`. Caught by the focused pytest run. Fixed in
    the test helper only (`if isinstance(value, Exception): raise value`); no assertion was
    relaxed.
13. **PDF lines wider than the page (F015).** The first synthetic renderer put the payment terms
    and some clauses on long single lines. `write_pdf` refused any line wider than 500 pt, so 41
    focused tests failed before extraction could run. Caught immediately by the focused suite.
    Split the payment heading, advance sentence and balance sentence, and split the quality and
    dispute sentences; the evidence sentences stayed whole. No assertion changed.
14. **Weak VAT evidence in the synthetic oracle (F015).** The first oracle attached the table
    header to `price_includes_vat = "false"` because Gate 2 exempts that field from the
    value-in-evidence check. Pre-commit review showed the header does not show VAT; the evidence
    now cites the VAT line, or `"Prices include VAT."` when the value is true. Caught by
    self-review, not by a failing test. Lesson: a Gate 2 exemption is not permission to attach
    unrelated evidence.

## Prompts that worked

(To be written by Tareq in his own words before submission: 3 to 4 prompts that gave good results.)

## Development history

### 2026-11-04 — Gold extraction coverage completed for all ten packs

- Added gold extraction fixtures for Packs 03, 04, and 07 in `tests/gold_extractions.json`.
- Updated the gold-fixture guard so it checks all ten packs instead of the earlier seven-pack count.
- The project owner manually checked the new evidence quotes against the extracted PDF text and checked the arithmetic for the three added packs. This was an owner review, not an automated proof of extraction accuracy.
- Boundary outcomes reviewed: Pack 03 is Reject (R1); Pack 04 needs a person to check (H1); Pack 07 needs a person to check (H2).
- A stale test guard initially failed because it still expected seven packs. The fixture cases themselves passed; updating the guard to ten resolved the stale count.
- Full test suite after the update: **445 passed**.
- Changes were committed in `e212667` (gold fixtures and guard) and `33a91fd` (progress update).

### 2026-11-04 — Technical foundation and AI history documented

- Created `NOTE.md` as the stable technical reference for architecture, extraction/evidence contracts, business rules, test baseline, current feature status, and known risks.
- Extended this file with the verified project milestones and current status.
- Preserved all eight previously recorded AI mistakes above without removing or rewriting their substance.
- The documentation review found two tracker items to reconcile later: the old LangGraph statement conflicts with F007 still being marked pending, and the old golden-test question is already resolved by tests comparing complete rule-ID lists. These were documented as status/maintenance notes; no other project files were changed.
- No application source code, tests, fixtures, progress tracker, feature list, or README were changed as part of this documentation task.

### 2026-11-05 — F008 human review and error handling

- Added `src/result.py` as the single adapter from final LangGraph state to immutable `CheckResult` objects. It carries ordered rule/match/extraction/document reasons, exact evidence quotes, missing-field names, match summaries, attempt counts and safe error information.
- Added PDF path and full CSV-loader prechecks before the workflow, so an unusable purchase-request CSV does not trigger an LLM call. Document-read/Gate 1 problems map to human review; system/input failures map to errors with no decision.
- Added stage metadata additively to graph error states. The first pass missed `extract` and `compare`; tests exposed this before completion, and both were added without changing F007 routing or decisions.
- Added 76 tests in `tests/test_result.py`, including all ten gold packs, error classification, evidence invariants, sanitisation and a regression test for raw CSV OS-error details. Focused tests: **76 passed**; full suite: **619 passed**.
- F008 cleanup: a review found two more points. The CSV loader itself (not only the result layer) leaked the full folder path and raw `[Errno ...]` text, and the result layer hid it by matching the start of the message, which would break if the wording changed. Two tests written first failed against the loader; `src/compare.py` was then fixed at the source, and an end-to-end test with the real loader was added. The documented test counts were also off by one and were corrected. Full suite: **622 passed**.
- Updated `progress.md`, `feature_list.json`, and this technical note. F009 reports and F010 UI remain out of scope.

### 2026-11-05 — F009 supplier decision reports

- Added `src/report.py`, a deterministic plain-text report renderer and writer that consumes one immutable `CheckResult`; it does not rerun rules, recalculate values, or ask the language model to make or explain a decision.
- Added the optional supplier name to `CheckResult` using the existing extraction result, and writes reports under `reports/decisions/` without changing the raw supplier-pack files.
- Report evidence is sanitized as untrusted text. Control/format characters are removed except Bengali ZWNJ/ZWJ when both adjacent characters are Bengali; reasons are stably ordered with Reject-severity findings first; the exact `Made by:` footer identifies the rules/model boundary.
- Added report tests for decision/reason rendering, evidence sanitization, Bengali joiners, stable ordering, deterministic output, path safety and all ten gold packs. Full suite: **769 passed**.
- Rendered and checked sample reports. F009 is complete; F010 and the remaining evaluation/documentation/demo features remain pending.

### 2026-11-05 — F010 Streamlit interface (implemented, awaiting owner verification)

- Approved plan followed: baseline `pytest` first (769 passed, Streamlit 1.66.1, Python 3.12.10), tests before code, no change to the core modules, `expected.json`, F009 or the existing tests. `git ls-files reports` confirmed the ten `reports/supplier-pack-NN.txt` files are tracked; the app never writes there.
- Added `src/ui_helpers.py` (no Streamlit import): upload checks (10 MB PDF, 1 MB CSV), one temporary folder per submission that is always removed, the original upload name used only as the report label, the report read into memory, a fingerprint of both uploads to drop stale results, and cleaned display values. Added `app.py`: presentation only, the check starts only from the button, the result is kept in session state, text from the document is shown with `st.text` only, and a download is offered for every result including errors.
- Reasons use a copy of the report's stable "Reject first" sort, with a test that compares the two orderings; the README was left untouched (F016).
- Tests: 53 in `tests/test_ui_helpers.py` and 25 in `tests/test_app.py` (AppTest with `check_supplier` mocked). Full suite: **847 passed**. The server starts headless and the health endpoint answers `ok`. Two AI mistakes were caught during the work (items 10 and 11 above).
- **Status at initial handoff:** F010 was left `pending`; the real browser run and download click were not done. Nothing was committed or pushed.

### 2026-11-05 — F010 follow-up (configuration and text-limit decisions implemented)

- Added a tests-first AppTest regression for long supplier names. It failed before the implementation, demonstrating that the UI previously displayed the entire string.
- Added presentation-layer `_display_text()` in `src/ui_helpers.py`: clean text, cap it at F009's `MAX_TEXT_CHARS` (240), and append the existing `[shortened]` marker. Applied to supplier-derived display fields, including supplier name, reason messages/evidence, match details, and error text. F009 report logic remains unchanged.
- Added `.streamlit/config.toml` with `[server] maxUploadSize = 10`, matching the 10 MB PDF cap. CSV helper cap remains 1 MB.
- Full test suite after the change: **848 passed**. Headless Streamlit health endpoint: **200 ok**. Updated NOTE/progress to reflect the resolved configuration/text decisions and remaining manual browser/download smoke test. `feature_list.json` status was set to `completed` after the automated tests and startup check. No commit or push.

### 2026-11-05 — F010 owner manual browser verification

- Owner launched the Streamlit app in a browser and uploaded `supplier-pack-07.pdf` with `purchase-request.csv`. The app generated the expected H2 quantity-mismatch human-review result (16,500 quoted vs 20,000 requested; 17.50% difference vs 5% tolerance). The owner clicked Download and confirmed it worked.
- Owner uploaded `supplier-pack-10.pdf` with the valid purchase request. The app returned the expected Reject decision for R5 (60-day delivery vs 45-day maximum) and H5 human-review finding because VAT was not shown separately.
- Owner created a temporary 1.37 MB CSV and confirmed the app rejected it with: `The purchase request CSV is larger than 1 MB. Please choose a smaller file.` This verifies the helper-level CSV limit while remaining below Streamlit's 10 MB global upload cap.
- Owner uploaded the unrelated `bank-faq.csv` and confirmed the app returned `PURCHASE_REQUEST_UNUSABLE`, listed the missing purchase-request columns, made no decision, and recorded zero model calls. Replacing it with the valid purchase request allowed pack 10 to process normally.
- Manual browser checks now cover upload, check, report display, download, CSV size rejection, and invalid CSV schema handling. PDF-over-10-MB rejection and visual long-text truncation were not tested. No source code or feature status changed during this verification; this entry records the manual results.

### 2026-11-05 — F011 live golden evaluation

- Added `src/evaluation.py` with 43 offline tests and an opt-in live test (`live` marker). The default suite at that commit was **891 passed, 1 skipped**; the live test runs only with `RUN_LIVE_EVALUATION=1`, so normal test runs never call a model.
- The first ten-pack live run (2026-11-05, `deepseek-v4.1-flash:free`, prompt `v1`, commit `8593502`) passed **9/10**. Pack 02 extracted `penalty_basis = "the order value"`; N2 compares the exact basis `"order value"`, so the pack went to "Needs a person to check" instead of Approve. A follow-up diagnostic extraction confirmed the model's wording.
- Deliberately did not change the frozen prompt, N2 or `expected.json` in that change. The mismatch is recorded in the preserved first-run evidence `reports/evaluation/f011-golden-run-9of10.json` and `.md`. The live test asserts all ten match, so it exits non-zero on a 9/10 result; the default suite is unaffected. The N2 wording issue was fixed separately, tests first, in the next entry.
- F011 is marked completed on the agreed basis: the harness executed and recorded all ten packs, with the actual 9/10 accuracy reported honestly. F015 remains for synthetic variants and repeat-run consistency.

### 2026-11-05 — N2 wording fix and second live golden run

- Fixed rule N2 separately from F011, tests first. The frozen prompt tells the model to copy the clause wording, so pack 02 returned `penalty_basis = "the order value"`; N2 required the exact string `"order value"` and sent a correct quotation to human review. The company rule only requires the basis to be order value, so the rule was stricter than the requirement.
- Wrote the new cases first in `tests/test_rules.py`: kept the two existing accepted forms (`"order value"`, `"Order Value"`) and added three more (`"the order value"`, `"The Order Value"`, `"  the   order   value  "`), plus six new negatives (`""`, `"the invoice value"`, `"the order value excluding VAT"`, `"the total order value"`, `"theorder value"`, `"then order value"`), plus a pack 02 `evaluate()` regression. Red first: 4 failed for the expected reason (the three new accepted forms and the pack 02 regression); the negative cases already passed.
- Changed only `check_n2`: after whitespace collapse and lowercasing, remove one leading whole word `"the"`, then compare the remainder to `config.PENALTY_BASIS` exactly. No fuzzy, contains or startswith matching. The finding message still shows the original basis text; `None` and empty still trigger N2; pack 08 still reports N2 + R4.
- Did not change `FIELD_GUIDE`, prompt `v1`, `expected.json`, `config.PENALTY_BASIS` or any other rule. Full default suite after the fix: **901 passed, 1 skipped**.
- Preserved the first-run evidence before rerunning: `reports/evaluation/f011-golden-run.json` and `.md` were copied to `f011-golden-run-9of10.json` and `.md`. Committed the code and tests as `a9f325a` first, then reran the live evaluation at that clean commit.
- Second live run: **10/10** (`deepseek-v4.1-flash:free`, prompt `v1`, commit `a9f325a`, working tree clean, 2026-11-05T16:23:25+00:00). Pack 02 now passes and pack 08 still correctly reports N2 + R4. New evidence is in `reports/evaluation/f011-golden-run.json` and `.md`; the old 9/10 files remain as history. F015 was not started.

### 2026-11-05 — F015 synthetic variants and repeat-run consistency

- Wrote `tests/synthetic_cases.json`, `tests/test_synthetic.py` and `tests/test_consistency.py` first. The red checkpoint was the expected import failure for `src.synthetic` and `src.consistency`, not a failed assertion or a typo.
- Added 20 deterministic cases S01–S20. Each case is one base spec plus explicit changes; money is derived with `Decimal` (`ROUND_HALF_UP`), each PDF is rendered as one fact per line, and the oracle extraction uses the same spec. Tests check every expected decision and rule-ID list against the real `evaluate()`/`decide()`, and push all 20 oracle payloads through Gate 2 and the real workflow with the mock provider.
- Added the repeat-run harness: 3 repeats of the existing ten-pack evaluation, categories for stable-and-accurate, stable-but-inaccurate, flaky, and infrastructure-or-harness, plus hidden-flakiness reporting when a passing run used `attempts == 2`. Stability excludes attempts so a retry cannot disappear. No automatic retry was added, and the F011 artifacts are never written.
- Added opt-in live tests with their own flag `RUN_LIVE_F015=1`; `RUN_LIVE_EVALUATION` stays F011-only. Default suite: **1040 passed, 3 skipped** (the three live tests). Focused F015 tests: 139 passed.
- Live run at the clean implementation commit `2ff1234` with `deepseek-v4.1-flash:free` and prompt `v1`: **20/20** synthetic cases passed, then **30/30** observations across 3 repeats passed, with **10/10 packs stable-and-accurate**, **0 flaky**, **0 infrastructure/harness failures** and **0 hidden retries**. The command reported `2 passed, 1 skipped, 1040 deselected in 833.22s`; the skip is the F011 live test because `RUN_LIVE_EVALUATION` was unset.
- Recorded the actual evidence in `reports/evaluation/f015-synthetic.json`/`.md` and `f015-repeat-run.json`/`.md`, then updated `progress.md`, `NOTE.md`, this log and `feature_list.json`. The F011 evidence files were not touched. The synthetic PDFs use a fixed template, so this result covers controlled wording, not real-world layouts, fonts or scans.

