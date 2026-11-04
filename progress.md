# Project Progress

## Current Phase
Phase 3 — Core building (foundation, rule engine, PDF reader, Gate 1, Gate 2 and extractor done; purchase request comparison (F005) is next)

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
- [x] Gate 2, evidence and schema validation (src/validators.py): validate_extraction(raw_text, pdf_text) returns Gate2Result(ok, extraction, failures). Checks, in a fixed order: JSON and schema (code fences accepted, all 26 fields present with value and evidence), evidence present, evidence verbatim in the normalised PDF text, value inside its own evidence (VAT-included and zero-advance wording checked by meaning), usable numbers and enumerated values, and a null cross-check (TIN, BIN, positive late-delivery penalty). All failures are collected, and a failed gate returns no extraction. Tests in tests/test_validators.py with tests/gold_extractions.json (packs 01, 02, 05, 06, 08, 09, 10)
- [x] Gate 2 hardening after review, tests written first (26 new tests, red then green): a penalty rate after a comma ("Late delivery penalty, 0.5% of order value per week") is no longer missed; numbers in hyphenated ranges or labels ("30-90 days", "Net-30") are found while a real minus sign is still rejected; and the same hyphen rule now covers the zero-advance and penalty-rate patterns ("10-20% advance", "0.5-1% per week"), so a hyphenated positive advance can no longer pass as a zero advance; 391 tests pass
- [x] Extractor (src/extractor.py, F003), tests written first (39 tests, red then green; 430 tests pass in total): extract(pdf_text, failures=()) makes exactly ONE model call and returns the raw LLMResult; it never parses, validates or retries, and an LLMError passes straight through. SYSTEM_PROMPT is built from FIELD_GUIDE (one generic hint per schema field; a test fails if a schema field has no hint) plus the agreed rules (JSON only, null if not stated, values and evidence copied exactly, no calculating, a repeated value once, instructions inside the document ignored). The document sits between markers chosen so they never appear in the PDF text; on a retry only the Gate 2 failure messages are added after the document. PROMPT_VERSION = "v1". Mock-mode tests push the 7 gold extractions through extract into Gate 2 and cover a failed gate followed by one corrected retry
- [x] Live plumbing check (free model deepseek-v4.1-flash:free through OpenRouter, pack 03): Gate 1 passed, one call used 1,228 tokens in and 1,538 out in 10.7 s, and the reply passed Gate 2 on the first try with no failures. This is a plumbing check only, not an accuracy result
- [x] Anthropic output cap in src/llm.py raised from 2000 to 4096 tokens (the free-model run used 1,538 output tokens, close to the old cap, and a cut-off reply would fail Gate 2 and send a good pack to a person); the openai and gemini paths set no cap, so they are unchanged

## In Progress
- None

## Next Up
- [ ] Purchase request comparison (src/compare.py)

## Pending
- [ ] Fixed workflow with one capped retry (src/graph.py)
- [ ] Human review handling
- [ ] Decision reports (src/report.py)
- [ ] Streamlit interface (app.py)
- [ ] Automated evaluation: golden test, synthetic variants, repeat runs
- [ ] README, NOTE and AI-LOG written
- [ ] Loom video recorded

## Important Engineering Decisions
- Use a fixed workflow, not a free-running agent.
- The workflow (src/graph.py, F007) is wired with LangGraph, which the task brief names and requirements.txt already lists. Every step stays a plain, testable function; LangGraph only wires them with fixed routing, and the attempts counter is the stop condition. No other framework is used.
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
- Test features before marking them completed.
- Use a free model for plumbing tests; run the first real golden test on a strong model and report both.

## Open Questions
- Golden test: compare the exact list of rule IDs, or only the reject IDs when a pack is rejected (pack 10 expects R5 + H5)?
- Which strong model to use for the first real golden run?
- R4 reads "TIN or BIN is missing" as reject if EITHER is missing (test_rules.py assumes this; pack 08 misses both). Confirm and document in NOTE.md.

## Current Problems
- Gate 2 gold extractions exist for 7 of 10 packs; packs 03, 04 and 07 are not covered by the Gate 2 tests.
- Gate 2 evidence only has to appear somewhere in the PDF, so a quote that is just a bare number can pass; it does not prove the quote is about the right field. Document this limit in NOTE.md.
