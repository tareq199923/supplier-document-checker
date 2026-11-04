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

R4 currently rejects if either TIN or BIN is missing. This interpretation is implemented and covered by tests, but the business owner still needs to confirm that policy.

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
- The latest recorded full-suite baseline on 2026-11-05 is **511 passed** (445 earlier tests plus 66 in `tests/test_compare.py`).
- Run tests from the project root with:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

A passing mock/gold test suite verifies deterministic code paths and fixture handling. It does not prove that a live LLM will extract every real quotation correctly. The live OpenRouter check recorded in `progress.md` was a plumbing check on Pack 03, not a broad accuracy evaluation.

## 7. Current implementation status

According to `progress.md` and `feature_list.json` at the time this note was written:

**Completed:** project setup, PDF reading/Gate 1, LLM provider layer, config/schema/parsing, rule engine, Gate 2, extractor, purchase-request comparison (`src/compare.py`, F005), and gold extraction coverage for all ten packs.

**Next:** F007 fixed workflow (`src/graph.py`), starting with the Extraction-to-RuleInputs mapping and the failed-match rule.

**Still pending:** fixed workflow/retry, human-review handling, decision reports, Streamlit interface, full automated evaluation, README, and Loom demo.

The progress document contains an older engineering-decision sentence saying the LangGraph workflow is wired, while its current phase/feature tracker still marks F007 pending. Treat F007 as pending until the implementation and tests are checked and the tracker is reconciled. Do not claim the workflow is complete based only on that older sentence.

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
| `tests/gold_extractions.json` | Gold extraction fixtures for all ten sample packs. |

## 9. Open questions and known risks

1. **R4 business policy:** confirm whether missing either TIN or BIN must always cause rejection. Current code and tests say yes.
2. **Evidence semantics:** Gate 2 does not fully establish that an evidence quote supports the intended field. A quote can exist in the PDF but be contextually wrong.
3. **Live extraction quality:** one successful live plumbing check is not enough to establish accuracy or repeat-run consistency.
4. **Workflow status:** reconcile the older LangGraph statement in `progress.md` with the F007 pending status before describing the workflow as implemented.
5. **Strict purchase-request matching:** the description must equal the CSV description (case and spaces ignored) and the unit must equal the CSV unit; anything else sends the case to a person, because a wrong match is worse than a human check. All ten gold packs match exactly today, but a real supplier who rewords an item will be flagged. The business owner may want a looser rule.
6. **Extraction-to-rule-input mapping:** nothing yet converts an Extraction into RuleInputs (parse failures, `missing_fields`); this is the first job of F007 and needs its own tests.

## 10. Maintenance rules for this note

- Keep stable technical facts here, not a transcript of every coding session.
- When code or business policy changes, verify the implementation and tests before updating this note.
- Keep unresolved policy questions clearly labelled as unresolved.
- Record dated development events and AI mistakes in `AI-LOG.md`.
- Never put API keys, passwords, tokens, or other secrets in project documentation.
