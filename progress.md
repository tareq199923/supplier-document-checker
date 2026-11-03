# Project Progress

## Current Phase
Phase 3 — Core building (foundation done; rule engine is next)

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

## In Progress
- None

## Next Up
- [ ] src/rules.py: make tests/test_rules.py pass (do not edit the tests)

## Pending
- [ ] PDF text extraction and Gate 1 (src/pdf_reader.py)
- [ ] AI-based structured extraction (src/extractor.py)
- [ ] Evidence validation gate (src/validators.py)
- [ ] Purchase request comparison (src/compare.py)
- [ ] Company rule engine (src/rules.py)
- [ ] Fixed workflow with one capped retry (src/graph.py)
- [ ] Human review handling
- [ ] Decision reports (src/report.py)
- [ ] Streamlit interface (app.py)
- [ ] Automated evaluation: golden test, synthetic variants, repeat runs
- [ ] README, NOTE and AI-LOG written
- [ ] Loom video recorded

## Important Engineering Decisions
- Use a fixed workflow, not a free-running agent.
- Use an LLM only for extracting information from supplier documents.
- Use Python for calculations and business rules.
- Require evidence for extracted information; evidence must appear verbatim in the normalised PDF text.
- Never guess missing information. Send uncertain cases to human review.
- Extracted values are strings exactly as written; Python parses them to Decimal.
- Gate 2 value-in-evidence check exemptions: price_includes_vat, and advance_pct when the value is "0".
- Decision strings are exactly: "Approve", "Reject", "Needs a person to check".
- CSV is read with utf-8-sig (it has a BOM) and rows are matched by PR number + item letter.
- Test features before marking them completed.
- Use a free model for plumbing tests; run the first real golden test on a strong model and report both.

## Open Questions
- Golden test: compare the exact list of rule IDs, or only the reject IDs when a pack is rejected (pack 10 expects R5 + H5)?
- Which strong model to use for the first real golden run?
- R4 reads "TIN or BIN is missing" as reject if EITHER is missing (test_rules.py assumes this; pack 08 misses both). Confirm and document in NOTE.md.
- R3 and R4 reject on a null value, but a null can also mean the LLM missed it. Plan: Gate 2 in validators.py searches the PDF text (\bTIN\b, \bBIN\b, "penalt", "late delivery", "delay"); if the text mentions it but the value is null, treat it as an extraction failure (retry, then human check), not a reject. Packs 06 and 08 must still reject.

## Current Problems
- None identified.
