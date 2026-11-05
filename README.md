# Supplier Document Checker

Reads **one supplier quotation PDF**, compares it with a purchase request (CSV) and the company
rules, and returns exactly one of three decisions:

- **Approve**
- **Reject**
- **Needs a person to check**

Every decision comes with the exact quotes from the PDF that support it. When the system is
unsure, the answer is "Needs a person to check". It never guesses.

The sample company and rules are fictional (Aamar Garments Ltd, see `samples/company-rules.md`).

## How it works

This is a **fixed workflow, not a free-running agent**. Code decides every step. The language
model only extracts values and exact quotes from the PDF text. It never calculates, never judges
and never writes the explanation.

```text
PDF
  -> read PDF
  -> Gate 1: is there enough usable text?
  -> LLM extraction (values + exact quotes)
  -> Gate 2: schema check and every quote must appear verbatim in the PDF
  -> compare with purchase-request.csv
  -> rule engine (all rules run, all findings collected)
  -> one decision
  -> report built from templates and verified numbers
```

Key design choices:

- **Python decides.** Money, percentages and thresholds use `Decimal`. All thresholds live in
  `src/config.py`.
- **Evidence is mandatory.** A non-empty extracted value needs a quote that appears in the PDF
  text. A missing quote is an extraction failure, not a guess.
- **One retry at most.** If Gate 2 fails, the model is asked once more with only the specific
  failure messages. A second failure routes to "Needs a person to check".
- **PDF text is untrusted data.** Instructions written inside a PDF are ignored.
- **Clean failures.** Every model call has a timeout, and any provider error becomes a clean
  error result.
- **LangGraph is used only for wiring** the fixed steps in `src/graph.py`. Each step is a plain,
  testable function.

The rules (R1-R5 reject, H1-H7 review, plus extra findings N1, N2 and M) are listed in
[`samples/company-rules.md`](samples/company-rules.md) and explained in [`NOTE.md`](NOTE.md).

## Setup

Requires Python 3.12 (developed and tested on 3.12.10, Windows).

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

Then edit `.env` and choose a provider. The code reads these variables:

| Variable | Meaning |
|---|---|
| `LLM_PROVIDER` | `anthropic`, `openai` or `gemini` (`mock` is for tests only) |
| `LLM_MODEL` | Model name for the chosen provider (required) |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` | Key for the chosen provider |
| `OPENAI_BASE_URL` | Optional. Lets the `openai` provider use any OpenAI-compatible endpoint |
| `LLM_TIMEOUT_SECONDS` | Optional. Default 60 |

Never commit `.env`. It is listed in `.gitignore`.

## Run the app

```powershell
.venv\Scripts\python.exe -m streamlit run app.py
```

Upload a supplier PDF (for example `samples/supplier-pack-03.pdf`) and
`samples/purchase-request.csv`, then press **Check quotation**. The page shows the decision, the
reasons with their evidence quotes, the purchase-request match and the number of model calls. You
can download the decision report as a `.txt` file.

The app calls a real model, so a working provider and key in `.env` are required. Limits: PDF up
to 10 MB, CSV up to 1 MB.

## Run the tests

```powershell
.venv\Scripts\python.exe -m pytest -q
```

The default suite never calls a model (it uses the `mock` provider and fixtures), so it needs no
API key. The live evaluations are opt-in and are skipped by default.

## Evaluation results

All live results below used `deepseek-v4.1-flash:free` through an OpenAI-compatible endpoint with
prompt `v1`, on 2026-11-05. Evidence files are in `reports/evaluation/`.

| Evaluation | Result |
|---|---|
| Ten sample packs (F011), first run | **9/10**. Pack 02 returned `"the order value"` where rule N2 required exactly `"order value"`, so it went to human review instead of Approve. |
| Ten sample packs (F011), after a separate tests-first N2 fix | **10/10** |
| 20 synthetic variants S01-S20 (F015) | **20/20** |
| Repeat run, 10 packs x 3 repeats (F015) | **30/30**, all 10 packs stable, 0 flaky, 0 infrastructure failures, 0 hidden retries |

The first 9/10 run is kept as history (`f011-golden-run-9of10.json` and `.md`). The fix changed
only the N2 wording check. The prompt, `expected.json` and every other rule were not changed.

To rerun the live evaluations (they call the real model and the F015 run took about 14 minutes):

```powershell
$env:RUN_LIVE_EVALUATION="1"; .venv\Scripts\python.exe -m pytest -m live -q -s   # F011, ten packs
$env:RUN_LIVE_F015="1"; .venv\Scripts\python.exe -m pytest -m live -q -s          # F015, synthetic + repeats
```

## Known limitations

- **Evidence is checked loosely.** Gate 2 proves a quote appears somewhere in the PDF and that the
  value appears in its quote (with documented exceptions). It does not prove the quote is about
  the right field. A bare number can pass.
- **The results are narrow.** They cover one model, one frozen prompt and the current ten sample
  packs. They are not evidence of general accuracy.
- **Synthetic PDFs are easy.** They come from one fixed text template, so they test controlled
  wording, not real supplier layouts, fonts or scan quality.
- **Purchase-request matching is strict.** The description and unit must match the CSV (case and
  extra spaces ignored, no fuzzy matching). A reworded item is sent to a person.
- **Two manual UI checks are still open:** rejecting a PDF over 10 MB and visually confirming the
  240-character text truncation.

## Project layout

| Path | Purpose |
|---|---|
| `app.py` | Streamlit page (presentation only) |
| `src/` | Workflow (`graph.py`), rules, gates (`validators.py`), model layer (`llm.py`), reports, evaluation harnesses |
| `samples/` | Ten sample quotation PDFs, the purchase request and the company rules |
| `tests/` | Default test suite and opt-in live tests |
| `reports/evaluation/` | Recorded live evaluation evidence |
| `expected.json` | Approved expected decisions and rule IDs for the ten packs |
| `NOTE.md` | Technical foundation: architecture, rules, test and evaluation status, risks |
| `AI-LOG.md` | Mistakes made by the AI assistant that were caught, and how |
| `progress.md`, `feature_list.json` | Build progress |
