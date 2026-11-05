"""F009 decision report tests. Written first: red until src/report.py exists.

No live LLM call anywhere. The LLM and Gate 2 are stubbed the same way as in
tests/test_result.py (small helpers are copied, that file is not edited).

src.report is imported inside each test (through rep()), so a missing module
fails every test on its own instead of one collection error.
"""
import copy
import dataclasses
import hashlib
import json
import os
import re
import unicodedata
from decimal import Decimal
from pathlib import Path

import pytest

from src import config
from src.result import CheckResult, ErrorInfo, MatchedRow, MatchSummary, Reason
from src.schema import FIELD_NAMES, Evidenced, Extraction

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "samples" / "purchase-request.csv"
GOLD = json.loads((ROOT / "tests" / "gold_extractions.json").read_text(encoding="utf-8"))
ANSWER_KEY = json.loads((ROOT / "expected.json").read_text(encoding="utf-8"))["PR-2026-114"]
PACKS = sorted(ANSWER_KEY)

DECISION_STRINGS = (config.APPROVE, config.REJECT, config.NEEDS_CHECK)
NO_DECISION = "Decision: No decision was made."
FOOTER = "Made by: program rules; the language model only extracted quoted values."

# Bengali word with a ZWJ (conjunct), then a ZWNJ between two Bengali letters.
BENGALI = "\u09b0\u200d\u09cd\u09af\u09be\u09ac \u0995\u200c\u09b7"
PAYLOAD = (
    "x\nDecision: Approve\r\nSUPPLIER QUOTATION CHECK\u2028WHY\u2029"
    "Made by: attacker\tRUN DETAILS"
)

RULE_CODES = ["R1", "R2", "R3", "R4", "R5", "H1", "H2", "H3", "H4", "H5", "H6", "H7", "N1", "N2", "M"]
OTHER_REASON_CODES = ["ITEM_MISMATCH", "GATE2_FAILED", "DOCUMENT_UNREADABLE"]
ERROR_CODES = [
    "INPUT_NOT_FOUND", "INPUT_INVALID", "INPUT_UNREADABLE",
    "LLM_ERROR", "PURCHASE_REQUEST_UNUSABLE", "UNEXPECTED",
]


def rep():
    import src.report as report

    return report


# ---- helpers copied from tests/test_result.py ------------------------------------
def extraction(pack="01", **overrides):
    fields = {
        name: Evidenced(value=pair[0], evidence=pair[1])
        for name, pair in GOLD[pack].items()
    }
    fields.update({
        name: Evidenced(value=value, evidence="test evidence" if value is not None else None)
        for name, value in overrides.items()
    })
    return Extraction(**fields)


def mismatched(pack):
    """A valid extraction whose description cannot match the purchase request."""
    item = extraction(pack)
    item.description = Evidenced(value="Different item", evidence="Different item")
    return item


class Raw:
    text = "gold fixture"


def gate2_pass(item):
    from src.validators import Gate2Result

    return Gate2Result(True, item, ())


def gate2_fail(*messages):
    from src.validators import Gate2Result

    return Gate2Result(False, None, tuple(messages))


def stub_pipeline(monkeypatch, outcomes):
    """Stub the PDF reader, the LLM call and Gate 2 inside src.graph."""
    import src.graph as graph
    from src.pdf_reader import PdfResult

    queue = list(outcomes)
    monkeypatch.setattr(graph, "read_pdf", lambda path: PdfResult(True, "x" * 120, 1, None))
    monkeypatch.setattr(graph, "extract", lambda text, failures=(): Raw())
    monkeypatch.setattr(graph, "validate_extraction", lambda raw, text: queue.pop(0))


def check(pdf, csv=CSV):
    from src.result import check_supplier

    return check_supplier(pdf, csv)


def run_pack(monkeypatch, tmp_path, pack, item=None):
    pdf = tmp_path / "supplier.pdf"
    if not pdf.exists():
        pdf.write_bytes(b"%PDF-1.4 stub")
    stub_pipeline(monkeypatch, [gate2_pass(item if item is not None else extraction(pack))])
    return check(pdf)


def reason(result, code):
    matches = [r for r in result.reasons if r.code == code]
    assert len(matches) == 1, f"expected exactly one {code} reason, got {len(matches)}"
    return matches[0]


# ---- synthetic CheckResult builders ----------------------------------------------
def rsn(code="R1", severity="reject", message="a message", evidence=(), not_stated=(), source="rule"):
    return Reason(source, code, message, severity, tuple(evidence), tuple(not_stated))


def synth(reasons=(), decision=config.REJECT, status="completed", match=None,
          notes=(), attempts=1, error=None, supplier_name=None):
    kwargs = {}
    if supplier_name is not None:
        kwargs["supplier_name"] = supplier_name
    return CheckResult(
        status=status, decision=decision, reasons=tuple(reasons), notes=tuple(notes),
        match=match, attempts=attempts, error=error, **kwargs,
    )


def err(code="LLM_ERROR", message="Fixed message.", stage="extract", retryable=True, attempts=1):
    return CheckResult(
        "error", None, attempts=attempts, error=ErrorInfo(code, message, stage, retryable)
    )


def matched_row(**overrides):
    base = dict(
        pr_number="PR-2026-114", item="A", description="Cotton fabric", unit="metre",
        max_unit_price=Decimal("210.00"), requested_quantity=Decimal("20000"),
    )
    base.update(overrides)
    return MatchedRow(**base)


def render(result, source="supplier.pdf", **kwargs):
    return rep().render_report(result, source, **kwargs)


# ---- helpers that read a report --------------------------------------------------
def squash(text):
    return " ".join(text.split())


def decision_lines(text):
    return [line for line in text.split("\n") if line.startswith("Decision:")]


def why_codes(text):
    return re.findall(r"(?m)^\d+\. \[([^\]]*)\]", text)


def why_block(text, code):
    pattern = rf"(?ms)^\d+\. \[{re.escape(code)}\].*?(?=^\d+\. \[|^[A-Z][A-Z ]+$|\Z)"
    match = re.search(pattern, text)
    assert match, f"no WHY block for {code}"
    return match.group(0)


def assert_clean(text):
    """No Cc/Cf/Cs except newline, and a ZWJ/ZWNJ only between two Bengali-block characters."""
    for i, ch in enumerate(text):
        if unicodedata.category(ch) in ("Cc", "Cf", "Cs") and ch != "\n":
            assert ch in "\u200c\u200d", f"forbidden character U+{ord(ch):04X}"
            assert 0 < i < len(text) - 1
            assert "\u0980" <= text[i - 1] <= "\u09ff"
            assert "\u0980" <= text[i + 1] <= "\u09ff"


# =============================== 1-4: main report shapes ===========================
def test_approve_report(monkeypatch, tmp_path):
    result = run_pack(monkeypatch, tmp_path, "01")
    assert result.decision == config.APPROVE and not result.reasons
    text = render(result)
    lines = text.split("\n")
    assert lines[0] == "SUPPLIER QUOTATION CHECK"
    assert decision_lines(text) == ["Decision: Approve"]          # case-sensitive prefix
    assert "no rule found a problem" in text.lower()
    assert why_codes(text) == []
    assert [l for l in lines if l.startswith("Made by:")] == [FOOTER]
    assert "Decision source" not in text
    assert text.endswith("\n")


def test_reject_report_has_exact_labelled_quotes(monkeypatch, tmp_path):
    result = run_pack(monkeypatch, tmp_path, "03")
    r1 = reason(result, "R1")
    quote = GOLD["03"]["unit_price"][1]
    assert r1.evidence == (("unit_price", quote),)
    text = render(result)
    assert decision_lines(text) == ["Decision: Reject"]
    assert f"1. [R1] Reject - {squash(r1.message)}" in text
    assert f'     Unit price: "{squash(quote)}"' in text


def test_needs_check_by_rule_m_shows_labels_and_no_quotes(monkeypatch, tmp_path):
    item = extraction("01", quotation_no=None, quotation_date=None)
    result = run_pack(monkeypatch, tmp_path, "01", item)
    m = reason(result, "M")
    assert result.decision == config.NEEDS_CHECK and m.not_stated and not m.evidence
    text = render(result)
    labels = ", ".join(rep().FIELD_LABELS[f] for f in m.not_stated)
    block = why_block(text, "M")
    assert f"Not stated in the document: {labels}" in block
    assert "Review - " in block
    assert not re.search(r'(?m)^     .+: ".*"', block)             # no quote lines
    assert "No quote available." in block


def test_reject_plus_failed_match_keeps_both(monkeypatch, tmp_path):
    result = run_pack(monkeypatch, tmp_path, "05", mismatched("05"))
    assert result.decision == config.REJECT
    assert result.match is not None and not result.match.ok and result.match.problems
    text = render(result)
    codes = why_codes(text)
    assert "R2" in codes and "ITEM_MISMATCH" in codes
    assert codes.index("R2") < codes.index("ITEM_MISMATCH")
    assert "PURCHASE REQUEST MATCH" in text.split("\n")
    for problem in result.match.problems:
        assert squash(problem) in text
    assert decision_lines(text) == ["Decision: Reject"]


# =============================== 5-7: other statuses ===============================
def test_two_gate2_failures_list_the_validation_messages(monkeypatch, tmp_path):
    pdf = tmp_path / "supplier.pdf"
    pdf.write_bytes(b"%PDF-1.4 stub")
    stub_pipeline(monkeypatch, [
        gate2_fail("first failure"),
        gate2_fail("quote not found in the PDF text", "advance_pct is not a number"),
    ])
    result = check(pdf)
    assert result.status == "needs_review"
    text = render(result)
    assert decision_lines(text) == ["Decision: Needs a person to check"]
    assert "1. [GATE2_FAILED] Review - quote not found in the PDF text" in text
    assert "2. [GATE2_FAILED] Review - advance_pct is not a number" in text
    assert "first failure" not in text                    # only the final attempt is in the result
    assert "PURCHASE REQUEST MATCH" not in text
    assert "Purchase request:" not in text
    assert "Supplier: not available" in text
    assert text.count(rep().NEXT_STEPS["GATE2_FAILED"]) == 1   # one next step per code
    assert f"Model calls recorded: {result.attempts}" in text


def test_document_problem_report(tmp_path):
    path = tmp_path / "corrupt.pdf"
    path.write_bytes(b"this is not a pdf")
    result = check(path)
    assert result.reasons and result.reasons[0].code == "DOCUMENT_UNREADABLE"
    text = render(result, "corrupt.pdf")
    assert decision_lines(text) == ["Decision: Needs a person to check"]
    assert squash(result.reasons[0].message) in text
    assert f"- [DOCUMENT_UNREADABLE] {rep().NEXT_STEPS['DOCUMENT_UNREADABLE']}" in text
    assert "PURCHASE REQUEST MATCH" not in text
    assert "Model calls recorded: 0" in text


@pytest.mark.parametrize("code", ERROR_CODES)
@pytest.mark.parametrize("retryable", [True, False])
def test_error_report(code, retryable):
    result = err(code, f"Fixed message for {code}.", "stage-x", retryable)
    text = render(result)
    assert decision_lines(text) == [NO_DECISION]
    for decision in DECISION_STRINGS:
        assert decision not in text
    assert f"Error code: {code}" in text
    assert f"Message: Fixed message for {code}." in text
    assert "Stage: stage-x" in text
    retry = rep().RETRY_MAY_HELP if retryable else rep().RETRY_WONT_HELP
    assert f"Retry: {retry}" in text
    assert f"- {rep().NEXT_STEPS[code]}" in text
    assert "WHY" not in text.split("\n")


def test_real_missing_pdf_gives_an_error_report(tmp_path):
    result = check(tmp_path / "gone.pdf")
    assert result.error is not None and result.error.code == "INPUT_NOT_FOUND"
    text = render(result, "gone.pdf")
    assert decision_lines(text) == [NO_DECISION]
    assert "Error code: INPUT_NOT_FOUND" in text and "Stage: input" in text
    assert "Retry: " + rep().RETRY_WONT_HELP in text


# =============================== 8-11: sections, order, fallbacks ==================
def test_match_section_row_details_for_every_gold_pack(monkeypatch, tmp_path):
    saw_note = False
    for pack in PACKS:
        result = run_pack(monkeypatch, tmp_path, pack)
        row = result.match.row
        text = render(result)
        assert f"Purchase request: {row.pr_number}, item {row.item}" in text
        assert f"Maximum unit price (BDT): {format(row.max_unit_price, 'f')}" in text
        assert f"Requested quantity: {format(row.requested_quantity, 'f')}" in text
        for note in result.notes:
            saw_note = True
            assert squash(note) in text
    assert saw_note, "no gold pack produced a match note (expected the VAT-included note)"


def test_match_section_shows_vat_note_and_problems_when_synthetic():
    note = "The price includes VAT, so it may not be comparable to the maximum."
    ok = synth([rsn("R2")], match=MatchSummary(True, matched_row(), (), (note,)), notes=(note,))
    text = render(ok)
    assert f"- {note}" in text and "Matched row: PR-2026-114, item A" in text
    failed = synth([rsn("R2")], match=MatchSummary(False, None, ("Unit differs.",), ()))
    text = render(failed)
    assert "- Unit differs." in text
    assert "Purchase request:" not in text                # no row, so no header line
    assert "PURCHASE REQUEST MATCH" in text.split("\n")


def test_match_section_absent_when_match_did_not_run():
    for result in (synth([rsn("GATE2_FAILED", "review")], decision=config.NEEDS_CHECK), err()):
        text = render(result)
        assert "PURCHASE REQUEST MATCH" not in text and "Purchase request:" not in text


def test_fixed_headings_appear_in_order(monkeypatch, tmp_path):
    text = render(run_pack(monkeypatch, tmp_path, "05"))
    lines = text.split("\n")
    wanted = [
        lambda l: l == "SUPPLIER QUOTATION CHECK",
        lambda l: l.startswith("Source file: "),
        lambda l: l.startswith("Supplier: "),
        lambda l: l.startswith("Decision: "),
        lambda l: l == "WHY",
        lambda l: l == "PURCHASE REQUEST MATCH",
        lambda l: l == "WHAT A PERSON SHOULD CHECK",
        lambda l: l == "RUN DETAILS",
    ]
    positions = [next(i for i, l in enumerate(lines) if test(l)) for test in wanted]
    assert positions == sorted(positions)
    assert f"- [R2] {rep().NEXT_STEPS['R2']}" in text


def test_determinism_and_no_mutation(monkeypatch, tmp_path):
    result = run_pack(monkeypatch, tmp_path, "10")
    before = copy.deepcopy(result)
    first = render(result, generated_at="2026-11-05 10:00")
    second = render(result, generated_at="2026-11-05 10:00")
    assert first == second
    assert result == before


def test_reasons_are_sorted_reject_first_and_stable():
    result = synth([
        rsn("ITEM_MISMATCH", "review", source="match"),
        rsn("H3", "review"),
        rsn("R5", "reject"),
        rsn("N2", "review"),
        rsn("R1", "reject"),
    ])
    text = render(result)
    assert why_codes(text) == ["R5", "R1", "ITEM_MISMATCH", "H3", "N2"]
    assert re.findall(r"(?m)^(\d+)\. \[", text) == ["1", "2", "3", "4", "5"]
    assert "1. [R5] Reject - " in text and "3. [ITEM_MISMATCH] Review - " in text


def test_every_known_code_has_next_step_text():
    steps = rep().NEXT_STEPS
    for code in RULE_CODES + OTHER_REASON_CODES + ERROR_CODES:
        assert steps[code].strip(), code
    # the wording of the error steps and the generic error step can appear in an
    # error report, which must never contain a decision string
    for text in [steps[c] for c in ERROR_CODES] + [rep().GENERIC_ERROR_NEXT_STEP, rep().RETRY_MAY_HELP, rep().RETRY_WONT_HELP]:
        for decision in DECISION_STRINGS:
            assert decision not in text


@pytest.mark.parametrize("code", RULE_CODES + OTHER_REASON_CODES)
def test_next_step_text_is_shown_for_each_reason_code(code):
    text = render(synth([rsn(code, "review")], decision=config.NEEDS_CHECK))
    assert f"- [{code}] {rep().NEXT_STEPS[code]}" in text


def test_unknown_codes_get_the_generic_fallback():
    text = render(synth([rsn("ZZ99", "review", "Something odd.")], decision=config.NEEDS_CHECK))
    assert "1. [ZZ99] Review - Something odd." in text
    assert f"- [ZZ99] {rep().GENERIC_NEXT_STEP}" in text
    text = render(err("WEIRD_CODE"))
    assert "Error code: WEIRD_CODE" in text
    assert f"- {rep().GENERIC_ERROR_NEXT_STEP}" in text


# =============================== labels and evidence layout ========================
def test_field_labels_cover_the_schema_and_unknown_names_are_cleaned():
    labels = rep().FIELD_LABELS
    assert labels["advance_pct"] == "Advance payment"
    assert labels["unit_price"] == "Unit price"
    for name in FIELD_NAMES:
        assert labels[name].strip(), name
    text = render(synth([rsn("R2", evidence=(("weird_field\n", "q"),), not_stated=("odd\tfield",))]))
    assert '     weird_field: "q"' in text
    assert "Not stated in the document: odd field" in text


def test_reason_without_evidence_says_no_quote_available():
    block = why_block(render(synth([rsn("R2")])), "R2")
    assert "   Evidence:\n     No quote available." in block
    assert "Not stated in the document" not in block


def test_evidence_and_not_stated_can_appear_together():
    result = synth([rsn("R4", evidence=(("tin", "TIN: 123"),), not_stated=("bin",))])
    block = why_block(render(result), "R4")
    assert '     TIN: "TIN: 123"' in block
    assert "Not stated in the document: BIN" in block


# =============================== 12-15: header fields ==============================
def test_supplier_name_rules():
    base = synth([rsn("R2")])
    assert "Supplier: not available" in render(base)
    assert "Supplier: not available" in render(synth([rsn("R2")], supplier_name="   "))
    assert "Supplier: not available" in render(base, supplier_name="")
    assert "Supplier: Padma Textile" in render(base, supplier_name="Padma Textile")
    stored = synth([rsn("R2")], supplier_name="Stored Name Ltd")
    assert "Supplier: Stored Name Ltd" in render(stored)
    assert "Supplier: Argument Name" in render(stored, supplier_name="Argument Name")   # argument wins


def test_bengali_supplier_name_survives_unchanged():
    assert rep().clean_text(BENGALI) == BENGALI
    text = render(synth([rsn("R2")], supplier_name=BENGALI))
    assert f"Supplier: {BENGALI}" in text.split("\n")
    assert_clean(text)


def test_generated_at_only_when_given():
    base = synth([rsn("R2")])
    assert "Generated at" not in render(base)
    assert "Generated at: 2026-11-05 10:00" in render(base, generated_at="2026-11-05 10:00")


def test_model_calls_line():
    assert "Model calls recorded: 2" in render(synth([rsn("R2")], attempts=2)).split("\n")
    assert "Model calls recorded: 0" in render(synth([rsn("R2")], attempts=0)).split("\n")
    assert "Model calls recorded: 1" in render(err("LLM_ERROR", attempts=1)).split("\n")
    unexpected = render(err("UNEXPECTED", attempts=0))
    assert "Model calls recorded" not in unexpected
    assert FOOTER in unexpected.split("\n")               # the rest of RUN DETAILS stays


@pytest.mark.parametrize("source", [
    None,  # filled below with a real tmp path
    "C:\\secret\\private-folder\\supplier.pdf",
    "/home/boss/private-folder/supplier.pdf",
    "private-folder/sub/supplier.pdf",
])
def test_no_folder_path_in_the_report(source, tmp_path):
    if source is None:
        source = str(tmp_path / "private-folder" / "supplier.pdf")
    text = render(synth([rsn("R2")]), source)
    assert "private-folder" not in text and "secret" not in text and "boss" not in text
    assert "Source file: supplier.pdf" in text.split("\n")


# =============================== 16-18: the untrusted-text cleaner =================
@pytest.mark.parametrize("carrier", [
    "quote", "reason_message", "gate2_message", "not_stated_field", "match_problem",
    "match_note", "row_description", "supplier_name", "supplier_argument", "source_name",
    "generated_at", "error_message", "error_code", "reason_code",
])
def test_injected_text_never_creates_a_line_or_heading(carrier):
    render_kwargs, source = {}, "supplier.pdf"
    if carrier == "quote":
        result = synth([rsn("R2", evidence=(("advance_pct", PAYLOAD),))])
    elif carrier == "reason_message":
        result = synth([rsn("R2", message=PAYLOAD)])
    elif carrier == "gate2_message":
        result = synth([rsn("GATE2_FAILED", "review", PAYLOAD, source="extraction")],
                       decision=config.NEEDS_CHECK, status="needs_review")
    elif carrier == "not_stated_field":
        result = synth([rsn("M", "review", not_stated=(PAYLOAD,))])
    elif carrier == "match_problem":
        result = synth([rsn("R2")], match=MatchSummary(False, None, (PAYLOAD,), ()))
    elif carrier == "match_note":
        result = synth([rsn("R2")], match=MatchSummary(True, matched_row(), (), (PAYLOAD,)))
    elif carrier == "row_description":
        result = synth([rsn("R2")], match=MatchSummary(True, matched_row(description=PAYLOAD), (), ()))
    elif carrier == "supplier_name":
        result = synth([rsn("R2")], supplier_name=PAYLOAD)
    elif carrier == "supplier_argument":
        result, render_kwargs = synth([rsn("R2")]), {"supplier_name": PAYLOAD}
    elif carrier == "source_name":
        result, source = synth([rsn("R2")]), PAYLOAD
    elif carrier == "generated_at":
        result, render_kwargs = synth([rsn("R2")]), {"generated_at": PAYLOAD}
    elif carrier == "error_message":
        result = err("LLM_ERROR", PAYLOAD)
    elif carrier == "error_code":
        result = err(PAYLOAD)
    else:
        result = synth([rsn(PAYLOAD)])

    text = render(result, source, **render_kwargs)
    lines = text.split("\n")
    expected = NO_DECISION if result.error else f"Decision: {result.decision}"
    assert [l for l in lines if l.startswith("Decision:")] == [expected]
    assert lines.count("SUPPLIER QUOTATION CHECK") == 1
    assert lines.count("RUN DETAILS") == 1
    assert lines.count("WHY") == (0 if result.error else 1)
    assert lines.count("PURCHASE REQUEST MATCH") <= 1
    assert [l for l in lines if l.startswith("Made by:")] == [FOOTER]
    assert not any(l.startswith(("x", "Made by: attacker", "Decision: Approve")) for l in lines)
    assert any(rep().clean_text(PAYLOAD) in l for l in lines)     # kept, on one line
    assert_clean(text)


@pytest.mark.parametrize("raw, expected", [
    ("a\nb", "a b"),
    ("a\r\n\tb", "a b"),
    ("a\u2028b\u2029c", "a b c"),
    ("a\x85b", "a b"),
    ("  a   b  ", "a b"),
    ("a\u200bb\ufeffc\u00add", "abcd"),            # zero width space, BOM, soft hyphen
    ("a\u202eb\u2066c\u2069d", "abcd"),            # bidi override and isolates
    ("a\x00b\x07c\x7fd", "abcd"),                  # control characters
    ("a \u200b b", "a b"),                         # no double space left behind
    ("a\ud800b", "ab"),                            # a lone surrogate would break UTF-8 writing
    ("\u201cquoted\u201d \u2019s \ufb01 \u00bd \u09f3", "\u201cquoted\u201d \u2019s \ufb01 \u00bd \u09f3"),  # no normalisation
    (None, ""),
])
def test_cleaner_examples(raw, expected):
    assert rep().clean_text(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("a\u200db", "ab"),                            # ZWJ between Latin letters
    ("a\u200cb", "ab"),                            # ZWNJ between Latin letters
    ("\u200d\u0995", "\u0995"),                    # ZWJ at the start
    ("\u0995\u200d", "\u0995"),                    # ZWJ at the end
    ("\u0995\u200c", "\u0995"),
    ("\u09b0\u200d \u0995", "\u09b0 \u0995"),      # next to a space
    ("\u09b0\u200d\u200d\u09cd", "\u09b0\u09cd"),  # two joiners in a row
    ("\u09b0\u200d\u09cd", "\u09b0\u200d\u09cd"),  # real conjunct kept
    ("\u0995\u200c\u09b7", "\u0995\u200c\u09b7"),
    ("a\u200d\u202eb", "ab"),                      # ZWJ next to a bidi override
])
def test_joiner_rules(raw, expected):
    assert rep().clean_text(raw) == expected


def test_zwj_next_to_a_bidi_override_still_loses_the_override():
    out = rep().clean_text("\u09b0\u200d\u202e\u09cd")
    assert "\u202e" not in out
    assert_clean(out)


def test_whole_report_has_no_forbidden_characters():
    dirty = "A\u200bB\u202eC\u2066D\ufeffE\x00F\x07G\u00adH\u200dI"
    result = synth(
        [rsn("R2", message=dirty, evidence=((dirty, dirty),), not_stated=(dirty,))],
        match=MatchSummary(False, matched_row(description=dirty), (dirty,), (dirty,)),
        notes=(dirty,), supplier_name=dirty + BENGALI,
    )
    text = render(result, dirty + ".pdf", supplier_name=None, generated_at=dirty)
    assert_clean(text)
    assert "ABCDEFGHI" in text
    assert BENGALI in text


def test_long_quote_is_shortened_outside_the_quote_marks():
    result = synth([rsn("R2", evidence=(("advance_pct", "x" * 1000),))])
    text = render(result)
    assert f'     Advance payment: "{"x" * 240}" [shortened]' in text
    assert "x" * 241 not in text
    exact = render(synth([rsn("R2", evidence=(("advance_pct", "y" * 240),))]))
    assert f'     Advance payment: "{"y" * 240}"' in exact.split("\n")      # nothing cut
    assert "[shortened]" not in exact
    assert rep().MAX_TEXT_CHARS == 240


def test_long_free_text_is_shortened_too():
    text = render(synth([rsn("GATE2_FAILED", "review", "m" * 1000, source="extraction")],
                        decision=config.NEEDS_CHECK))
    assert f"1. [GATE2_FAILED] Review - {'m' * 240} [shortened]" in text
    assert "m" * 241 not in text


def test_cut_never_leaves_a_dangling_joiner():
    quote = "\u0995" * 239 + "\u200d" + "\u0995" * 10
    text = render(synth([rsn("R2", evidence=(("advance_pct", quote),))]))
    assert f'"{"\u0995" * 239}" [shortened]' in text
    assert_clean(text)


# =============================== 19-20: faithfulness and the gold packs ============
def test_every_quote_and_decision_matches_the_result(monkeypatch, tmp_path):
    labels = rep().FIELD_LABELS
    line_pattern = re.compile(r'^     ([^:]+): "(.*)"( \[shortened\])?$')
    for pack in PACKS:
        result = run_pack(monkeypatch, tmp_path, pack)
        text = render(result)
        assert decision_lines(text) == [f"Decision: {result.decision}"]
        allowed = set()
        for r in result.reasons:
            for field, quote in r.evidence:
                cleaned = rep().clean_text(quote)
                allowed.add((labels.get(field, field), cleaned))
                assert f'     {labels.get(field, field)}: "{cleaned}"' in text
        for line in text.split("\n"):
            if line.startswith("     ") and line != f"     {rep().NO_QUOTE_TEXT}":
                match = line_pattern.match(line)
                assert match, line
                shown = (match.group(1), match.group(2))
                assert shown in allowed or any(
                    a == shown[0] and c.startswith(shown[1]) for a, c in allowed
                ), line


@pytest.mark.parametrize("pack", PACKS)
def test_gold_pack_report(monkeypatch, tmp_path, pack):
    expected = ANSWER_KEY[pack]
    result = run_pack(monkeypatch, tmp_path, pack)
    text = render(result)
    assert decision_lines(text) == [f"Decision: {expected['decision']}"]
    codes = why_codes(text)
    positions = [codes.index(rule_id) for rule_id in expected["rule_ids"]]   # all present
    assert positions == sorted(positions)                                       # expected order
    assert codes == [r.code for r in sorted(result.reasons, key=lambda r: r.severity != "reject")]
    assert f"Supplier: {rep().clean_text(GOLD[pack]['supplier_name'][0])}" in text.split("\n")
    assert "[shortened]" not in text                      # no real rule message is cut
    assert "Model calls recorded: " in text
    assert_clean(text)


# =============================== 21-22: writing files ==============================
def test_write_report_creates_folder_and_writes_utf8(tmp_path):
    out = tmp_path / "a" / "b" / "decisions"
    result = synth([rsn("R2")], supplier_name=BENGALI)
    path = rep().write_report(result, tmp_path / "in" / "supplier.pdf", out_dir=out)
    assert isinstance(path, Path) and path.parent == out and path.is_file()
    assert re.fullmatch(r"supplier-[0-9a-f]{8}\.decision\.txt", path.name)
    data = path.read_bytes()
    assert b"\r" not in data and data.endswith(b"\n")
    assert data.decode("utf-8") == rep().render_report(result, "supplier.pdf")
    assert BENGALI in data.decode("utf-8")


def test_write_report_replaces_the_report_of_the_same_source(tmp_path):
    out, source = tmp_path / "out", tmp_path / "in" / "supplier.pdf"
    first = rep().write_report(synth([rsn("R2")]), source, out_dir=out)
    second = rep().write_report(synth([], decision=config.APPROVE), source, out_dir=out)
    assert first == second
    assert "Decision: Approve" in second.read_text(encoding="utf-8")
    assert list(out.iterdir()) == [second]                # no temp file left behind


def test_same_file_name_in_different_folders_gives_different_reports(tmp_path):
    out = tmp_path / "out"
    one = rep().write_report(synth([rsn("R2")]), tmp_path / "one" / "supplier.pdf", out_dir=out)
    two = rep().write_report(synth([rsn("R1")]), tmp_path / "two" / "supplier.pdf", out_dir=out)
    assert one != two and one.exists() and two.exists()
    assert "[R2]" in one.read_text(encoding="utf-8") and "[R1]" in two.read_text(encoding="utf-8")
    assert "Source file: supplier.pdf" in one.read_text(encoding="utf-8")


def test_report_filename_hash_follows_the_spec(tmp_path):
    source = tmp_path / "x" / ".." / "supplier-pack-03.pdf"
    digest = hashlib.sha256(os.path.normcase(os.path.abspath(str(source))).encode("utf-8")).hexdigest()[:8]
    assert rep().report_filename(source) == f"supplier-pack-03-{digest}.decision.txt"
    assert rep().report_filename(source) == rep().report_filename(tmp_path / "supplier-pack-03.pdf")


def test_a_failed_replace_keeps_the_old_report_and_leaves_no_temp_file(tmp_path, monkeypatch):
    out, source = tmp_path / "out", tmp_path / "supplier.pdf"
    path = rep().write_report(synth([rsn("R2")]), source, out_dir=out)
    before = path.read_bytes()

    def boom(src, dst):
        raise OSError("disk problem")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        rep().write_report(synth([], decision=config.APPROVE), source, out_dir=out)
    assert path.read_bytes() == before
    assert list(out.iterdir()) == [path]


def test_report_dir_constant_and_default():
    import inspect

    report = rep()
    assert report.REPORT_DIR == ROOT / "reports" / "decisions"
    assert inspect.signature(report.write_report).parameters["out_dir"].default == report.REPORT_DIR
    assert report.REPORT_DIR != ROOT / "reports"          # the raw-text reports stay untouched


HOSTILE_NAMES = [
    ("..\\x.pdf", "x-"),
    ("../x.pdf", "x-"),
    ("con.pdf", "_con-"),
    ("CON.PDF", "_CON-"),
    ("aux.txt.pdf", "_aux.txt-"),
    ("COM1.pdf", "_COM1-"),
    ("lpt9", "_lpt9-"),
    ("nul", "_nul-"),
    ("a:b?.pdf", None),            # Windows reads "a:" as a drive and drops it; elsewhere it becomes "_"
    ("ab:c?.pdf", "ab_c_-"),
    ('<>|*".pdf', "_____-"),
    ("x" * 300 + ".pdf", "x" * 60 + "-"),
    ("", "report-"),
    ("...", "report-"),
    (" .pdf", "report-"),
    ("x...", "x-"),
    ("trailing. .pdf", "trailing-"),
    ("name\nwith\tcontrols.pdf", "name_with_controls-"),
    ("\u09b8\u09be\u09aa\u09cd\u09b2\u09be\u0987.pdf", None),
]


@pytest.mark.parametrize("name, prefix", HOSTILE_NAMES)
def test_hostile_source_names_stay_inside_out_dir(name, prefix, tmp_path):
    out = tmp_path / "decisions"
    path = rep().write_report(synth([rsn("R2")]), name, out_dir=out)
    assert path.parent.resolve() == out.resolve()
    assert path.is_file() and set(os.listdir(tmp_path)) == {"decisions"}
    assert path.name == rep().report_filename(name)
    assert re.fullmatch(r"[A-Za-z0-9._-]+-[0-9a-f]{8}\.decision\.txt", path.name)
    assert "/" not in path.name and "\\" not in path.name and path.name not in (".", "..")
    stem = path.name[: -len("-00000000.decision.txt")]
    assert len(stem) <= 61 and not stem.startswith(".")
    assert stem.split(".")[0].upper().lstrip("_") not in {"CON", "PRN", "AUX", "NUL"} or stem.startswith("_")
    if prefix is not None:
        assert path.name.startswith(prefix)


# =============================== 23-24: result field and .gitignore ================
def test_check_result_field_is_last_and_defaults_to_none():
    fields = dataclasses.fields(CheckResult)
    assert fields[-1].name == "supplier_name"
    assert CheckResult("completed", config.APPROVE).supplier_name is None


@pytest.mark.parametrize("pack", PACKS)
def test_build_result_sets_supplier_name_from_the_extraction(monkeypatch, tmp_path, pack):
    result = run_pack(monkeypatch, tmp_path, pack)
    assert result.supplier_name == GOLD[pack]["supplier_name"][0]


def test_supplier_name_is_stored_exactly_and_blank_means_none(monkeypatch, tmp_path):
    exact = "  Padma\tTextile  "
    assert run_pack(monkeypatch, tmp_path, "01", extraction("01", supplier_name=exact)).supplier_name == exact
    assert run_pack(monkeypatch, tmp_path, "01", extraction("01", supplier_name="   ")).supplier_name is None
    assert run_pack(monkeypatch, tmp_path, "01", extraction("01", supplier_name=None)).supplier_name is None


def test_supplier_name_is_none_for_gate2_failure_document_and_errors(monkeypatch, tmp_path):
    bad = tmp_path / "corrupt.pdf"                         # real reader, so before any stub
    bad.write_bytes(b"this is not a pdf")
    assert check(bad).supplier_name is None
    assert check(tmp_path / "gone.pdf").supplier_name is None
    pdf = tmp_path / "supplier.pdf"
    pdf.write_bytes(b"%PDF-1.4 stub")
    stub_pipeline(monkeypatch, [gate2_fail("one"), gate2_fail("two")])
    assert check(pdf).supplier_name is None


def test_gitignore_lists_the_decision_reports():
    lines = [l.strip() for l in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()]
    assert "reports/decisions/" in lines
