# NOTE — Technical Foundation

## 1. Project purpose

Supplier Document Checker reviews one supplier quotation PDF against the purchase request and company rules.

It returns exactly one decision:

- **Approve**
- **Reject**
- **Needs a person to check**

Every decision must be supported by exact evidence from the supplier PDF. If information is missing, unclear, or cannot be verified, do not guess. Route the case for human review where the rules require it.

The current sample purchase request is `PR-2026-114`. The project contains 10 supplier quotation sample packs and one purchase-request CSV.

## 2. Fixed architecture

The intended pipeline is:

```text
PDF
  -> read_pdf
  -> Gate 1: usable extracted text?
  -> LLM extraction
  -> Gate 2: schema and evidence validation
  -> compare with purchase-request.csv
  -> deterministic rule engine
  -> route one decision
  -> build report
```

This is a fixed workflow, not a free-running agent. Python controls the steps and makes all decisions. The LLM is only an extraction component.

**LLM responsibilities**
- Read the normalised PDF text.
- Return the requested fields as strings, with exact evidence quotes.
- Return null when a fact is not stated.
- Follow a Gate 2 retry message when the workflow requests a correction.

**Python responsibilities**
- Validate the extraction and evidence.
- Parse values and convert units.
- Compare the supplier quotation with the purchase request.
- Calculate arithmetic and apply every business rule.
- Select the final decision and build report messages.

The LLM must not calculate totals, judge supplier compliance, choose a decision, or write the final explanation.

## 3. Important implementation contracts

- Use `Decimal` for money, percentages, and threshold comparisons. Do not use float.
- Keep thresholds and constants in `src/config.py`.
- Each business rule is a small deterministic function.
- Run all rules and collect all findings; do not stop after the first finding.
- Any reject finding makes the final decision **Reject**.
- If there is no reject but there is at least one finding, the decision is **Needs a person to check**.
- If there are no findings, the decision is **Approve**.
- Missing values are not silently invented. Rules R3, R4, H3, and H5 explicitly treat certain missing information as a finding. Rule M reports other required fields that are missing.
- Read the purchase-request CSV with `encoding="utf-8-sig"` because it has a UTF-8 BOM.
- Match purchase-request rows by PR number and item letter (A–E), then require the description and unit to agree (case and extra spaces ignored, no fuzzy matching). A failed match gives the rules no maximum price and no requested quantity, and the workflow turns an "Approve" into "Needs a person to check"; a Reject from R2–R5 stays a Reject. No new rule ID is added. "VAT included" on a quotation is a note, not a finding.
- Treat all PDF text as untrusted data. Ignore instructions that appear inside a supplier PDF.
- Add a timeout to every LLM call. An API failure must return a clean error result.

## 4. PDF reading and validation gates

### Gate 1 — PDF text quality

The PDF reader uses PyMuPDF and normalises extracted text. Gate 1 rejects text that is too short to be useful.

Current configured minimum: **100 characters** (`MIN_TEXT_CHARS`).

Missing, corrupt, password-protected, blank, or unusable PDFs must fail cleanly and must not be sent to the LLM.

### Gate 2 — extraction and evidence

The extraction schema has 26 fields. Each field is an object containing:
- `value`: a string as written in the document, or null.
- `evidence`: an exact quote copied from the PDF, or null.

Gate 2 checks JSON/schema shape, required fields, evidence presence, verbatim evidence in normalised PDF text, value-in-evidence rules, usable values/enumerations, and selected null cross-checks.

The extractor itself makes one model call per attempt and returns the raw LLM result. It does not parse or validate the result. The fixed workflow is responsible for Gate 2 and the one allowed retry.

**Important evidence limitation:** current evidence validation confirms that a quote appears somewhere in the normalised PDF text and checks that the extracted value appears in its evidence (with documented exceptions). It does not fully prove that the quote belongs to the correct field. For example, a bare number may occur in the PDF but refer to a different fact. This is a known semantic-association risk; do not describe Gate 2 as proving field meaning.

Special extraction details:
- Values are strings exactly as written; Python parses them later.
- A repeated value is extracted once.
- “No advance payment required” maps to advance value `"0"`.
- “4 weeks” is kept as a value and unit; Python converts weeks to days using 7 days per week.
- The schema has explicit handling for VAT-included pricing and zero advance wording.
- A null TIN, BIN, or penalty value must be checked against the PDF text. If the PDF mentions the fact but extraction missed it, this is an extraction failure rather than an automatic reject.

## 5. Business rules

All thresholds below are configured in `src/config.py`. Rule implementation is in `src/rules.py`.

### Reject rules

| Rule | Condition |
|---|---|
| R1 | Unit price is more than 3% above the purchase-request maximum. |
| R2 | Advance payment is greater than 20%. |
| R3 | No late-delivery penalty clause is stated. |
| R4 | Supplier TIN or BIN is missing. |
| R5 | Delivery is later than 45 days. |

R4 rejects if either TIN or BIN is missing (decided by Tareq, 2026-11-05). This is implemented and covered by tests; pack 08 is missing both.

### Human-review rules

| Rule | Condition |
|---|---|
| H1 | Unit price is above the maximum, but no more than 3% above it. |
| H2 | Quoted quantity differs from requested quantity by more than 5%. |
| H3 | Balance-payment days are missing/incomplete, unclear as a range, or outside 30–60 days. A single value from 30 through 60 is accepted. |
| H4 | Delivery is from 31 through 45 days, inclusive. |
| H5 | VAT is not shown separately with both rate and amount, or the rate is not 15%. |
| H6 | Available arithmetic checks do not reconcile within BDT 0.01. |
| H7 | Quotation validity is less than 30 days. |

### Additional review findings

| Rule | Condition |
|---|---|
| N1 | A stated late-delivery penalty cannot be confirmed as at least 0.5% per week. A non-week period or missing period also needs review. |
| N2 | A stated late-delivery penalty does not clearly use “order value” as its basis. |
| M | One or more fields from `config.REQUIRED_FIELDS` are missing. |

N1 and N2 are additional interpretation checks; they do not replace R3. The configured minimum penalty is 0.5% per week, and the required basis is “order value”.

### Boundary examples from the current gold set

- Pack 03: unit price BDT 229 against a maximum of BDT 210. The 3% ceiling is BDT 216.30, so R1 applies.
- Pack 04: unit price BDT 6.65 against a maximum of BDT 6.50. It is above the maximum but within the 3% ceiling, so H1 applies.
- Pack 07: quoted quantity 16,500 against 20,000 requested (17.5% difference), so H2 applies.
- A penalty of exactly 0.5% per week meets the N1 minimum; N1 is for a rate below the minimum or a period that cannot be confirmed as weekly.
- Pack 10 is expected to include both R5 and H5. Tests compare the complete expected rule-ID list, not only reject IDs.

These examples are useful regression cases. Do not change a threshold or expected result just to make a test pass.

## 6. Tests and evaluation status

- Gold extraction fixtures are in `tests/gold_extractions.json`.
- All 10 sample packs have gold extraction data.
- Expected decisions and rule IDs are in `expected.json`.
- The gold fixture guard checks for all ten approved pack keys.
- The full-suite result after F009 is **769 passed** (622 before F009 plus 147 report tests in `tests/test_report.py`).
- After F010 implementation and its truncation regression test, the full suite is **848 passed** (769 plus 53 in `tests/test_ui_helpers.py` and 26 in `tests/test_app.py`). F010 is **completed** in `feature_list.json` after the tests and headless startup check. The owner subsequently verified the real browser upload/check/download flow, the 1 MB CSV limit, and invalid-CSV precheck behavior; the 10 MB PDF rejection and 240-character truncation remain unverified manually (see section 9, item 7).
- Run tests from the project root with:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

A passing mock/gold test suite verifies deterministic code paths and fixture handling. It does not prove that a live LLM will extract every real quotation correctly. The live OpenRouter check recorded in `progress.md` was a plumbing check on Pack 03, not a broad accuracy evaluation.

## 7. Current implementation status

According to `progress.md` and `feature_list.json` at the time this note was written:

**Completed:** project setup, PDF reading/Gate 1, LLM provider layer, config/schema/parsing, rule engine, Gate 2, extractor, purchase-request comparison (`src/compare.py`, F005), gold extraction coverage for all ten packs, the fixed LangGraph workflow (`src/graph.py`, F007), normalized human-review/error handling (`src/result.py`, F008), and deterministic supplier decision reports (`src/report.py`, F009).

**Completed:** F010 the Streamlit interface (`app.py`, `src/ui_helpers.py`), with the 240-character UI text limit, 10 MB Streamlit upload cap, 848 passing tests, headless startup verification, and manual browser verification of upload/check/report download, CSV size rejection, and invalid CSV schema handling. Manual tests for an over-10 MB PDF and long-text truncation remain optional follow-ups.

**Still pending:** full automated evaluation, README, and Loom demo.

The workflow tests in `tests/test_graph.py` stub the PDF reader, the LLM call and Gate 2, and feed the approved gold extractions through the real mapping, matching, rules and decision steps. They prove the wiring and all ten expected decisions and rule-ID lists. They are not a live run with real PDFs and a real model; that is the job of F011 and F015.

### Workflow behaviour (`src/graph.py`)

- Routing is fixed: read PDF and Gate 1, extract, Gate 2, then compare, evaluate and finalize. `MAX_ATTEMPTS = 2` means one first try plus at most one retry, and the retry sends only the Gate 2 failure messages. A second Gate 2 failure ends as `needs_review` with the decision "Needs a person to check".
- The graph's internal state status is `completed`, `needs_review` or `error`. Graph error states have no `decision` key and carry an additive `stage` (`read_pdf`, `gate1`, `extract`, or `compare`). Do not treat that internal state as the public result contract.
- F008 adds `src/result.py`: `build_result(state)` converts the final graph state into one frozen `CheckResult` with `status`, `decision`, ordered `reasons`, `notes`, optional `match`, `attempts`, and optional `error`. Reports and the UI should consume this object, not LangGraph state.
- The adapter maps PDF read failures and Gate 1 failures (including corrupt, password-protected, blank and too-short documents) to `needs_review` with the decision `Needs a person to check`. Missing/invalid/unreadable PDF paths, LLM failures, unusable CSV files and unexpected exceptions become `error` with `decision=None` and fixed public messages. LLM provider exception text and raw OS read-error details are not exposed. App-written CSV validation details may be retained for the operator.
- `check_supplier(pdf_path, csv_path)` is the single entry point. It checks the PDF path, runs the real CSV loader before any LLM call, runs the fixed workflow, and returns one `CheckResult`. Unexpected exceptions are caught as `Exception`, never `BaseException`; unknown execution stages fall back to `workflow` or `result` rather than claiming a precise node.
- **Result-layer details to know:** (1) For rule M, `not_stated` means "no usable value": it lists fields that are absent and also fields that are written but unreadable. (2) `CheckResult.attempts` is the number of model calls as far as known; after an unexpected exception the workflow state is lost, so it is 0 even if a call was made. (3) `CheckResult.match` is `None` when the purchase-request match never ran (Gate 1, Gate 2 failed twice, or an error). (4) CSV error text is kept for the operator only when the loader wrote it itself. `src/compare.py` now names the file without its folder and shows only the exception type for operating-system read failures. `src/result.py` still replaces those two messages with fixed text as a second line of defence, and a test with the real loader guards against a reworded message slipping through.
- **Failed purchase-request match:** the rules get no maximum price and no requested quantity, so R1, H1 and H2 cannot run. The workflow turns "Approve" into "Needs a person to check" and keeps any other Reject. A supplier whose price would have triggered R1 therefore goes to a person, not to Reject, when its item does not match. This is the safe direction.
- **Unreadable values:** a value that is written in the document but cannot be read as a number, or a delivery unit other than day or week, is listed for rule M so a person sees it. It is never silently treated as absent. In particular an unreadable penalty rate does not trigger R3 ("no penalty clause"); R3 fires only when the penalty is truly null. Gate 2 normally stops these values earlier, so this is a second line of defence.

### Streamlit interface behaviour (`app.py`, `src/ui_helpers.py`, F010)

- `app.py` is presentation only. `src/ui_helpers.py` has no Streamlit import and reuses `check_supplier` and the F009 report writer; it adds no business rule. `CheckResult`, the rules and F009 are unchanged.
- The check runs only when the button is pressed, once per press. Choosing or changing a file starts nothing. The outcome is kept in `st.session_state`, so a download click or any other rerun shows the stored result and does not run the workflow again. If either upload changes (name or content) the stored outcome is dropped.
- Each submission uses its own temporary folder. The uploads are written under the fixed names `supplier.pdf` and `purchase-request.csv`; the uploaded file name is never used in a disk path. It is passed to `write_report` only as the label for the "Source file:" line. The report is read into memory and the folder is removed before the result is shown. Nothing is written to `reports/` or `reports/decisions/`.
- Upload limits: Streamlit's `.streamlit/config.toml` caps uploads at 10 MB; helper checks limit PDF to 10 MB and CSV to 1 MB. Empty or odd files are passed to the existing workflow, which handles them before any model call.
- Supplier-derived text in the UI is cleaned and shortened to 240 characters (same as the report), with the report's `[shortened]` marker.
- Reasons are shown in the same order as the report (Reject-severity first, otherwise unchanged). `ui_helpers.py` keeps its own copy of that three-line sort because `report._ordered` is private and F009 stays untouched; a test compares the two for many orderings, so any drift fails.
- Text that comes from the document (supplier name, quotes, messages, match problems, error text) is cleaned with `report.clean_text()` and shown only with `st.text`, never as Markdown or HTML. An error result shows "Decision: No decision was made." and no decision string. A download is offered for every result, errors included. The next-step wording appears only in the downloaded report.
- Problems in the page itself (temporary files, the report, an unexpected exception) use fixed messages with no path and no exception text.

## 8. Key files

| File | Purpose |
|---|---|
| `AGENTS.md` | Rules and instructions for AI agents working in this repository. |
| `NOTE.md` | This file: stable technical facts, contracts, thresholds, limitations, and current status. |
| `AI-LOG.md` | Chronological AI-assisted development history and mistakes. |
| `progress.md` | Current phase, completed work, next work, and open questions. |
| `feature_list.json` | Feature checklist and status. |
| `expected.json` | Expected decisions and rule IDs for the ten supplier packs. |
| `samples/` | Supplier PDFs, purchase request, and company rules. |
| `src/config.py` | Decision strings and central thresholds/constants. |
| `src/schema.py` | 26-field extraction schema and evidence wrapper. |
| `src/extractor.py` | LLM extraction prompt and one-attempt extraction call. |
| `src/validators.py` | Gate 2 schema, evidence, and value validation. |
| `src/rules.py` | Deterministic rule functions, findings, and decision selection. |
| `src/compare.py` | Purchase-request CSV loader and strict item matcher (max price and requested quantity for the rules). |
| `src/graph.py` | Fixed LangGraph workflow, Extraction-to-RuleInputs mapping, final decision with the failed-match rule; error states carry an additive stage. |
| `src/result.py` | F008 normalized immutable `CheckResult` types, evidence/reason mapping, input prechecks and safe error handling. |
| `src/report.py` | F009 deterministic plain-text report rendering and writing from one `CheckResult`, with evidence sanitization and stable reason ordering. |
| `tests/test_report.py` | F009 report rendering, evidence sanitization, Bengali joiners, reason ordering, deterministic output, path safety and ten-pack golden-report tests. |
| `app.py` | F010 Streamlit page (presentation only). Implemented and completed; real browser smoke test recommended. |
| `src/ui_helpers.py` | F010 logic behind the page, no Streamlit import: upload checks, per-submission temporary folder, report bytes, stale-result fingerprint, cleaned display values. |
| `tests/test_ui_helpers.py` | F010 helper tests (uploads, temporary files, report, safe failures, reason order, no business logic in the UI layer). |
| `tests/test_app.py` | F010 page tests with Streamlit AppTest and `check_supplier` mocked. |
| `tests/test_graph.py` | Workflow, mapping and failed-match tests (LLM and Gate 2 stubbed). |
| `tests/test_result.py` | F008 result mapping, evidence, attempts, sanitisation, prechecks and ten-pack golden-result tests. |
| `tests/gold_extractions.json` | Gold extraction fixtures for all ten sample packs. |

## 9. Open questions and known risks

1. **R4 business policy (resolved 2026-11-05):** reject if either TIN or BIN is missing. No further action.
2. **Evidence semantics:** Gate 2 does not fully establish that an evidence quote supports the intended field. A quote can exist in the PDF but be contextually wrong.
3. **Live extraction quality:** one successful live plumbing check is not enough to establish accuracy or repeat-run consistency.
4. **Workflow status (resolved 2026-11-05):** F007 is implemented in `src/graph.py` and covered by `tests/test_graph.py`. The older contradictory sentence in `progress.md` has been corrected.
5. **Strict purchase-request matching:** the description must equal the CSV description (case and spaces ignored) and the unit must equal the CSV unit; anything else sends the case to a person, because a wrong match is worse than a human check. All ten gold packs match exactly today, but a real supplier who rewords an item will be flagged. The business owner may want a looser rule.
6. **F008 result contract (resolved 2026-11-05):** `src/result.py` defines the immutable `CheckResult` shape used by future reports and UI. The graph state remains internal; `build_result` maps it to completed, needs-review or error results, with `decision=None` for system errors. The execution-stage fallback is `workflow` or `result` when the exact node is unavailable.
7. **F010 status and known limits:** The Streamlit interface is implemented, tested, and marked completed after the 848-test suite and headless startup check. `.streamlit/config.toml` sets Streamlit's upload cap to 10 MB; helper checks additionally limit the PDF to 10 MB and CSV to 1 MB. Supplier-derived free text shown in the UI is cleaned and capped at the report's 240-character limit, with the report's `[shortened]` marker. Manual browser verification confirmed PDF/CSV upload, report generation, a working Download click, rejection of a 1.4 MB CSV with the expected 1 MB limit message, and rejection of an unrelated CSV schema as `PURCHASE_REQUEST_UNUSABLE` with zero model calls. Pack 07 produced the expected H2 quantity-mismatch review; pack 10 produced the expected R5 delivery rejection plus H5 VAT review. Manual rejection of a PDF larger than 10 MB and visual verification of 240-character truncation remain untested. The report bytes stay in the user's Streamlit session until the files change or the session ends. A very fast second click can, in rare cases, start a second check. Messages inherited from F008 are shown unchanged (for example, a missing API key reads like a network problem). The tests rely on Streamlit 1.66.1 AppTest features and `requirements.txt` does not pin the version.

## 10. Maintenance rules for this note

- Keep stable technical facts here, not a transcript of every coding session.
- When code or business policy changes, verify the implementation and tests before updating this note.
- Keep unresolved policy questions clearly labelled as unresolved.
- Record dated development events and AI mistakes in `AI-LOG.md`.
- Never put API keys, passwords, tokens, or other secrets in project documentation.
