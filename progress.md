# Project Progress

## Current Phase
Phase 3 — Core building (foundation, rule engine, PDF reader, Gate 1, Gate 2, extractor, purchase request comparison (F005), fixed workflow (F007), and human review/error handling (F008) done)

## Completed
- [x] Created project directory, Python 3.12 virtual environment and project folders
- [x] Installed and verified Python dependencies
- [x] Copied the 10 supplier PDFs, purchase-request.csv and company-rules.md into samples/
- [x] Defined expected results for all 10 supplier documents (expected.json), checked against the real PDF text
- [x] Created project instructions (AGENTS.md)
- [x] Published the repository to GitHub
- [x] Multi-provider LLM layer (src/llm.py): anthropic, openai (also OpenAI-compatible endpoints), gemini, mock; one LLMError failure path; verified live through OpenRouter
- [x] Config thresholds (src/config.py), all Decimal
- [x] Extraction schema (src/schema.py), 26 evidence-wrapped fields
- [x] Parsing helpers (src/parsing.py): only plain or properly grouped numbers are accepted (no guessed decimal commas, no negatives); weeks to days; 24 tests pass
- [x] Rule engine boundary tests written first (tests/test_rules.py, includes all 10 packs against expected.json); red until src/rules.py exists
- [x] Rule engine (src/rules.py): R1-R5, H1-H7, N1, N2, M as pure functions; all 10 packs match expected.json; RULE_FIELDS map plus schema test; 122 tests pass
- [x] PDF reader and Gate 1 (src/pdf_reader.py): normalise_text, read_pdf returning PdfResult(ok, text, page_count, error), gate_text (None = pass, string = error, measured on normalised text); blank, whitespace-only, short, password-protected, missing and corrupt PDFs handled; PyMuPDF order kept; 70 new tests (tests/test_pdf_reader.py); 192 tests pass; extracted text of all 10 real PDFs inspected and looks correctly ordered
- [x] Gate 2, evidence and schema validation (src/validators.py): validate_extraction(raw_text, pdf_text) returns Gate2Result(ok, extraction, failures). Checks, in a fixed order: JSON and schema (code fences accepted, all 26 fields present with value and evidence), evidence present, evidence verbatim in the normalised PDF text, value inside its own evidence (VAT-included and zero-advance wording checked by meaning), usable numbers and enumerated values, and a null cross-check (TIN, BIN, positive late-delivery penalty). All failures are collected, and a failed gate returns no extraction. Tests in tests/test_validators.py with tests/gold_extractions.json (all 10 packs; 03, 04 and 07 added later)
- [x] Gate 2 hardening after review, tests written first (26 new tests, red then green): a penalty rate after a comma ("Late delivery penalty, 0.5% of order value per week") is no longer missed; numbers in hyphenated ranges or labels ("30-90 days", "Net-30") are found while a real minus sign is still rejected; and the same hyphen rule now covers the zero-advance and penalty-rate patterns ("10-20% advance", "0.5-1% per week"), so a hyphenated positive advance can no longer pass as a zero advance; 391 tests pass
- [x] Extractor (src/extractor.py, F003), tests written first (39 tests, red then green; 430 tests pass in total): extract(pdf_text, failures=()) makes exactly ONE model call and returns the raw LLMResult; it never parses, validates or retries, and an LLMError passes straight through. SYSTEM_PROMPT is built from FIELD_GUIDE (one generic hint per schema field; a test fails if a schema field has no hint) plus the agreed rules (JSON only, null if not stated, values and evidence copied exactly, no calculating, a repeated value once, instructions inside the document ignored). The document sits between markers chosen so they never appear in the PDF text; on a retry only the Gate 2 failure messages are added after the document. PROMPT_VERSION = "v1". Mock-mode tests push the gold extractions through extract into Gate 2 and cover a failed gate followed by one corrected retry
- [x] Live plumbing check (free model deepseek-v4.1-flash:free through OpenRouter, pack 03): Gate 1 passed, one call used 1,228 tokens in and 1,538 out in 10.7 s, and the reply passed Gate 2 on the first try with no failures. This is a plumbing check only, not an accuracy result
- [x] Anthropic output cap in src/llm.py raised from 2000 to 4096 tokens (the free-model run used 1,538 output tokens, close to the old cap, and a cut-off reply would fail Gate 2 and send a good pack to a person); the openai and gemini paths set no cap, so they are unchanged
- [x] Gate 2 gold extractions added for packs 03, 04 and 07 (tests/gold_extractions.json); all 10 packs now covered; the gold-pack guard test (test_gold_fixture_has_the_ten_approved_packs) asserts all ten; 445 tests pass
- [x] Purchase request comparison (src/compare.py, F005), tests written first (66 new tests in tests/test_compare.py, red then green; 511 tests pass in total): load_purchase_requests reads the CSV (utf-8-sig) into {(PR number, item): PurchaseRequestRow} with Decimal quantity and max price, and raises PurchaseRequestError for a missing, empty or unreadable file, missing column, blank field, bad or non-positive number, short line or duplicate key. match_item(extraction, table) never raises and returns MatchResult(row, problems, notes, ok, max_price, requested_qty). Strict matching: PR number, item letter, description and unit must agree after trimming, collapsing spaces and ignoring case; a failed match returns no row, so the rules never get another item's price. "VAT included" is a note, not a problem, so rule IDs stay unchanged (pack 10 still R5 + H5). A guard test keeps max_price and requested_qty equal to the hand-typed values in test_rules.py

- [x] Fixed LangGraph workflow (src/graph.py, F007), tests written first: maps validated Extraction values to Decimal RuleInputs, converts delivery weeks to days, records absent/unparseable required fields for rule M, uses only confirmed purchase-request values, runs every rule, and blocks Approve when the purchase-request match fails while preserving Reject. LangGraph wires Gate 1, extraction, Gate 2, one capped retry, CSV matching, rule evaluation and final routing; PDF, LLM and CSV errors return clean error states. 25 workflow tests cover mapping, retry/error paths and all 10 approved golden extraction fixtures against expected rule IDs and decisions (LLM/Gate 2 stubbed in these workflow tests); full suite: 536 passed.
- [x] F007 follow-up review (2026-11-05), tests first (3 red, then green): the mapper now lists unreadable optional numbers and unknown delivery units for rule M instead of dropping them, and an unreadable penalty no longer causes a false R3 reject (R3 fires only when the penalty is truly null). Added workflow tests for a failed match on a clean pack and on an R1 pack (R1/H1/H2 cannot run without a confirmed maximum price, so the case goes to a person), and for a truly absent penalty still rejecting. NOTE.md section 7, 9 and the key-files table updated. Full suite: 543 passed.

- [x] F008 Human review and error handling (`src/result.py`): added immutable `CheckResult`, `Reason`, `MatchSummary` and `ErrorInfo` types; `build_result` maps final workflow states without exposing raw provider errors; `check_supplier` prechecks the PDF and fully validates the CSV before any LLM call, then returns one result. Document problems map to human review; missing/invalid inputs, LLM failures, unusable CSV and unexpected exceptions map to clean errors with no decision. Exact evidence quotes come from `RULE_FIELDS`; rule M lists missing fields without quotes. Added 75 result-layer tests, including ten gold packs, sanitisation, attempts, stage mapping and invariants. A regression test also ensures raw OS error details from CSV reads are not exposed. Full suite: 618 passed.

## In Progress
- None

## Next Up
- [ ] Supplier decision reports (F009)

## Pending
- [ ] Decision reports (src/report.py)
- [ ] Streamlit interface (app.py)
- [ ] Automated evaluation: golden test, synthetic variants, repeat runs
- [ ] README, NOTE and AI-LOG written
- [ ] Loom video recorded

## Important Engineering Decisions
- Use a fixed workflow, not a free-running agent.
- F007 is implemented in src/graph.py with LangGraph (listed in requirements.txt) only wiring the fixed steps with explicit routing. Every step stays a plain, testable function; the attempts counter enforces the one-retry limit. No free-running agent or other framework is used.
- The extractor makes exactly one model call per attempt and returns raw text. Parsing and checking are Gate 2; the single capped retry and the API-failure path are graph.py. PROMPT_VERSION is bumped whenever the prompt changes, and the prompt is frozen before the first golden run.
- Use an LLM only for extracting information from supplier documents.
- Use Python for calculations and business rules.
- Require evidence for extracted information; evidence must appear verbatim in the normalised PDF text.
- Never guess missing information. Send uncertain cases to human review.
- Extracted values are strings exactly as written; Python parses them to Decimal.
- Gate 2 value-in-evidence check exemptions: price_includes_vat, and advance_pct when the value is "0".
- Gate 2 null cross-check: R3 and R4 reject on a null value, but a null can also mean the LLM missed it. If the PDF mentions TIN, BIN or a positive late-delivery penalty and the value is null, that is an extraction failure (retry, then human check), not a reject. Packs 06 and 08 still reject because their text has no such mention.
- Gate 2 penalty detection never joins sentences. Inside one sentence, a clause that names the penalty but has no rate is checked together with the next clause; a negation ("No penalty applies") still wins.
- Gate 2 number matching: a hyphen after a letter or digit is a hyphen ("30-90", "Net-30"); a hyphen after a space, a bracket or the start of the text is a minus sign and does not match. The same rule is used by value matching, the zero-advance patterns and the penalty-rate pattern.
- Decision strings are exactly: "Approve", "Reject", "Needs a person to check".
- Findings carry an optional evidence tuple; RULE_FIELDS in src/rules.py maps each rule to the schema fields the report will quote.
- CSV is read with utf-8-sig (it has a BOM) and rows are matched by PR number + item letter.
- Purchase request matching (src/compare.py) is strict: PR number, item letter, description and unit must all agree after ignoring case and extra spaces. A close but different description or a different unit is a problem for a person, never a fuzzy match. A failed match gives the rules no maximum price and no requested quantity (rules.py skips rules whose input is None, so without the graph step a failed match could look like a clean Approve). A mismatch is not a new rule ID: the graph (F007) runs all rules and, when the match failed, turns "Approve" into "Needs a person to check" (a Reject from R2 to R5 stays a Reject). An unusable CSV raises PurchaseRequestError, which the graph turns into a clean error result. "VAT included" is a note, not a finding.
- Test features before marking them completed.
- Use a free model for plumbing tests. For now the first golden run uses the same model (decided 2026-11-05); a stronger model can be added later and both results reported.

## Open Questions
- None.

## Resolved Decisions (2026-11-05)
- Golden test compares the EXACT list of rule IDs (R and H rules) against expected.json for every pack, not only the reject IDs. Pack 10 expects R5 + H5.
- Golden runs use the same model as the plumbing check (deepseek-v4.1-flash:free through OpenRouter) for now. No separate strong-model run for the moment.
- R4 rejects the supplier if EITHER the TIN or the BIN is missing (pack 08 misses both). Documented in NOTE.md.
- Gate 2 evidence validation stays as it is. Its limit is documented in NOTE.md (sections 4 and 9).
- README is postponed until later (F016).

## Current Problems
- Gate 2 evidence only has to appear somewhere in the PDF, so a quote that is just a bare number can pass; it does not prove the quote is about the right field. Accepted and documented in NOTE.md (sections 4 and 9). Do not describe Gate 2 as proving field meaning.
