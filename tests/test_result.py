"""F008 result layer tests. Written first: red until src/result.py exists.

No live LLM call anywhere. The LLM and Gate 2 are stubbed the same way as in
tests/test_graph.py; the PDF reader is real wherever a real file is built.
"""
import copy
import dataclasses
import json
import os
from pathlib import Path

import pymupdf
import pytest

from src import config
from src.rules import RULE_FIELDS
from src.schema import Evidenced, Extraction

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "samples" / "purchase-request.csv"
GOLD = json.loads((ROOT / "tests" / "gold_extractions.json").read_text(encoding="utf-8"))
ANSWER_KEY = json.loads((ROOT / "expected.json").read_text(encoding="utf-8"))["PR-2026-114"]

CSV_HEADER = "PR number,Item,Description,Unit,Quantity requested,Maximum unit price (BDT)\n"

POISON = (
    "sk-ant-api03-SECRETKEY1234567890abcdef",
    "Bearer eyJhbGciOiJIUzI1NiJ9.SECRETTOKEN",
    "x-api-key: sk-live-ABCDEF123456",
    "Authorization: Bearer SECRETAUTHVALUE",
    '{"error": {"message": "Invalid API key RESPONSEBODYFRAGMENT"}}',
)
POISON_TEXT = " | ".join(POISON)
POISON_TOKENS = (
    "sk-ant-api03", "SECRETKEY", "Bearer", "SECRETTOKEN", "x-api-key", "sk-live",
    "ABCDEF123456", "Authorization", "SECRETAUTHVALUE", "RESPONSEBODYFRAGMENT",
    "Invalid API key",
)


# ---- helpers ------------------------------------------------------------------
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


def make_pdf(path, pages):
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text, fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


def build_document(kind, tmp_path):
    path = tmp_path / f"{kind}.pdf"
    if kind == "corrupt":
        path.write_bytes(b"this is not a pdf")
    elif kind == "password":
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), "Secret quotation text", fontsize=11)
        doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256,
                 user_pw="open-sesame", owner_pw="owner-pw")
        doc.close()
    elif kind == "blank":
        make_pdf(path, [""])
    elif kind == "short":
        make_pdf(path, ["Short quote only"])
    return path


def bad_csv(kind, tmp_path):
    path = tmp_path / "pr.csv"
    if kind == "empty":
        path.write_text("", encoding="utf-8")
    elif kind == "missing_column":
        path.write_text(
            "PR number,Item,Description,Unit,Quantity requested\nPR-1,A,Widget,piece,10\n",
            encoding="utf-8",
        )
    elif kind == "duplicate_key":
        path.write_text(
            CSV_HEADER + "PR-1,A,Widget,piece,10,5\nPR-1,A,Widget,piece,10,5\n",
            encoding="utf-8",
        )
    elif kind == "missing_file":
        return tmp_path / "gone.csv"
    elif kind == "none":
        return None
    elif kind == "empty_string":
        return ""
    return path


@pytest.fixture
def pdf_file(tmp_path):
    """An existing file that passes the path precheck. The reader is stubbed where used."""
    path = tmp_path / "supplier.pdf"
    path.write_bytes(b"%PDF-1.4 stub")
    return path


def gate2_pass(item):
    from src.validators import Gate2Result

    return Gate2Result(True, item, ())


def gate2_fail(*messages):
    from src.validators import Gate2Result

    return Gate2Result(False, None, tuple(messages))


def stub_pipeline(monkeypatch, outcomes, *, extract=None, read=None):
    """Stub the PDF reader, the LLM call and Gate 2 inside src.graph."""
    import src.graph as graph
    from src.pdf_reader import PdfResult

    queue = list(outcomes)
    calls = []

    def default_extract(text, failures=()):
        calls.append(tuple(failures))
        return Raw()

    monkeypatch.setattr(
        graph, "read_pdf", read or (lambda path: PdfResult(True, "x" * 120, 1, None))
    )
    monkeypatch.setattr(graph, "extract", extract or default_extract)
    monkeypatch.setattr(graph, "validate_extraction", lambda raw, text: queue.pop(0))
    return calls


def check(pdf, csv=CSV):
    from src.result import check_supplier

    return check_supplier(pdf, csv)


def run_pack(monkeypatch, pdf, item):
    stub_pipeline(monkeypatch, [gate2_pass(item)])
    return check(pdf)


def reason(result, code):
    matches = [r for r in result.reasons if r.code == code]
    assert len(matches) == 1, f"expected exactly one {code} reason, got {len(matches)}"
    return matches[0]


def serialise(result):
    return json.dumps(dataclasses.asdict(result), default=str)


def assert_no_poison(result):
    for text in (serialise(result), repr(result), str(result)):
        for token in POISON_TOKENS:
            assert token not in text, f"{token!r} leaked into the result"


def assert_invariants(result, item=None):
    assert result.status in {"completed", "needs_review", "error"}
    if result.status == "error":
        assert result.decision is None
        assert result.error is not None
        assert result.reasons == ()
    else:
        assert result.decision in {config.APPROVE, config.REJECT, config.NEEDS_CHECK}
        assert result.error is None
    if result.decision == config.APPROVE:
        assert result.reasons == ()
    if result.decision == config.REJECT:
        assert any(r.severity == "reject" for r in result.reasons)
    if result.status == "needs_review":
        assert result.decision == config.NEEDS_CHECK
        assert result.reasons
    for r in result.reasons:
        assert r.source in {"rule", "match", "extraction", "document"}
        assert r.severity in {"reject", "review"}
        assert r.message
        quoted = {field for field, _ in r.evidence}
        assert not quoted & set(r.not_stated)
        for field, quote in r.evidence:
            assert field in RULE_FIELDS[r.code]
            if item is not None:
                assert quote in getattr(item, field).evidence
        for field in r.not_stated:
            assert field in RULE_FIELDS[r.code]
            if item is not None:
                assert getattr(item, field).value is None


def assert_input_error(result, code, stage="input"):
    assert result.status == "error"
    assert result.decision is None
    assert result.reasons == ()
    assert result.attempts == 0
    assert result.match is None
    assert result.error.code == code
    assert result.error.stage == stage
    assert result.error.retryable is False
    assert result.error.message
    assert_invariants(result)


def no_workflow(monkeypatch):
    import src.result as result_module

    def fail(*args, **kwargs):
        pytest.fail("the workflow must not run after a failed precheck")

    monkeypatch.setattr(result_module, "run_workflow", fail)


def llm_must_not_run(monkeypatch):
    import src.graph as graph

    def fail(*args, **kwargs):
        pytest.fail("the LLM must not be called")

    monkeypatch.setattr(graph, "extract", fail)


# ---- 1-3: completed results ---------------------------------------------------
def test_completed_approve_has_no_reasons(monkeypatch, pdf_file):
    result = run_pack(monkeypatch, pdf_file, extraction("01"))
    assert result.status == "completed"
    assert result.decision == config.APPROVE
    assert result.reasons == ()
    assert result.error is None
    assert result.match.ok is True
    assert_invariants(result, extraction("01"))


def test_completed_reject_carries_a_rule_reason_with_the_exact_quote(monkeypatch, pdf_file):
    result = run_pack(monkeypatch, pdf_file, extraction("03"))
    assert result.status == "completed"
    assert result.decision == config.REJECT
    (r1,) = result.reasons
    assert (r1.source, r1.code, r1.severity) == ("rule", "R1", "reject")
    assert r1.message
    assert r1.evidence == (("unit_price", GOLD["03"]["unit_price"][1]),)
    assert r1.not_stated == ()
    assert_invariants(result, extraction("03"))


def test_completed_needs_check_carries_a_rule_reason_with_the_exact_quote(monkeypatch, pdf_file):
    result = run_pack(monkeypatch, pdf_file, extraction("04"))
    assert result.decision == config.NEEDS_CHECK
    (h1,) = result.reasons
    assert (h1.source, h1.code, h1.severity) == ("rule", "H1", "review")
    assert h1.evidence == (("unit_price", GOLD["04"]["unit_price"][1]),)
    assert_invariants(result, extraction("04"))


# ---- 3: purchase-request match ------------------------------------------------
def test_failed_match_gives_item_mismatch_with_the_real_problems(monkeypatch, pdf_file):
    from src.compare import load_purchase_requests, match_item

    item = mismatched("01")
    real = match_item(item, load_purchase_requests(CSV))
    assert not real.ok and real.problems

    result = run_pack(monkeypatch, pdf_file, item)
    assert result.status == "completed"
    assert result.decision == config.NEEDS_CHECK
    (mismatch,) = result.reasons
    assert (mismatch.source, mismatch.code, mismatch.severity) == (
        "match", "ITEM_MISMATCH", "review",
    )
    for problem in real.problems:
        assert problem in mismatch.message
    assert result.match.ok is False
    assert result.match.row is None
    assert result.match.problems == real.problems
    assert "ITEM_MISMATCH" not in RULE_FIELDS  # a mismatch is not a rule ID
    assert_invariants(result, item)


def test_reject_with_a_failed_match_keeps_rule_reasons_and_the_mismatch(monkeypatch, pdf_file):
    result = run_pack(monkeypatch, pdf_file, mismatched("10"))
    assert result.decision == config.REJECT
    assert [r.code for r in result.reasons] == ["R5", "H5", "ITEM_MISMATCH"]
    assert [r.source for r in result.reasons] == ["rule", "rule", "match"]
    assert_invariants(result, mismatched("10"))


def test_vat_included_is_a_note_and_not_a_reason(monkeypatch, pdf_file):
    result = run_pack(monkeypatch, pdf_file, extraction("10"))
    assert result.match.ok is True
    assert any("VAT" in note for note in result.notes)
    assert result.notes == result.match.notes
    assert [r.code for r in result.reasons] == ["R5", "H5"]
    assert all(r.source == "rule" for r in result.reasons)


def test_match_summary_carries_the_matched_row_so_reports_need_no_matchresult(
    monkeypatch, pdf_file
):
    from src.compare import load_purchase_requests, match_item
    from decimal import Decimal

    expected = match_item(extraction("01"), load_purchase_requests(CSV)).row
    summary = run_pack(monkeypatch, pdf_file, extraction("01")).match
    assert summary.ok is True
    assert summary.problems == ()
    row = summary.row
    assert (row.pr_number, row.item, row.description, row.unit) == (
        expected.pr_number, expected.item, expected.description, expected.unit,
    )
    assert row.max_unit_price == expected.max_unit_price
    assert row.requested_quantity == expected.quantity
    assert isinstance(row.max_unit_price, Decimal)
    assert isinstance(row.requested_quantity, Decimal)


# ---- 4: Gate 2 failed twice ---------------------------------------------------
def test_two_gate2_failures_need_review_with_the_final_attempts_messages(monkeypatch, pdf_file):
    calls = stub_pipeline(monkeypatch, [
        gate2_fail("unit_price: quote not found in the PDF"),
        gate2_fail("tin: value not inside its evidence", "bin: quote missing"),
    ])
    result = check(pdf_file)
    assert len(calls) == 2
    assert result.status == "needs_review"
    assert result.decision == config.NEEDS_CHECK
    assert result.attempts == 2
    assert result.error is None
    assert result.match is None  # the match step never ran
    assert [(r.source, r.code, r.severity) for r in result.reasons] == [
        ("extraction", "GATE2_FAILED", "review")
    ] * 2
    assert [r.message for r in result.reasons] == [
        "tin: value not inside its evidence",
        "bin: quote missing",
    ]
    assert_invariants(result)


# ---- 5: document problems go to review ---------------------------------------
@pytest.mark.parametrize("kind", ["corrupt", "password", "blank", "short"])
def test_document_problems_need_review_and_never_reach_the_llm(monkeypatch, tmp_path, kind):
    llm_must_not_run(monkeypatch)
    result = check(build_document(kind, tmp_path))
    assert result.status == "needs_review"
    assert result.decision == config.NEEDS_CHECK
    assert result.error is None
    assert result.attempts == 0
    assert result.match is None
    (doc,) = result.reasons
    assert (doc.source, doc.code, doc.severity) == ("document", "DOCUMENT_UNREADABLE", "review")
    assert doc.message
    assert doc.evidence == () and doc.not_stated == ()
    if kind == "password":
        assert "password" in doc.message.lower()
    assert_invariants(result)


def test_a_path_that_passes_the_precheck_but_fails_to_open_needs_review(monkeypatch, pdf_file):
    llm_must_not_run(monkeypatch)

    def cannot_open(*args, **kwargs):
        raise RuntimeError("cannot open this file")

    monkeypatch.setattr(pymupdf, "open", cannot_open)
    result = check(pdf_file)
    assert result.status == "needs_review"
    assert result.decision == config.NEEDS_CHECK
    assert result.error is None
    assert result.reasons[0].code == "DOCUMENT_UNREADABLE"
    assert_invariants(result)


# ---- 6: PDF path prechecks (system problems, never review) --------------------
def test_missing_pdf_path_is_input_not_found(monkeypatch, tmp_path):
    no_workflow(monkeypatch)
    assert_input_error(check(tmp_path / "nope.pdf"), "INPUT_NOT_FOUND")


@pytest.mark.parametrize("bad", ["directory", "", "   ", None])
def test_directory_empty_and_none_paths_are_input_invalid(monkeypatch, tmp_path, bad):
    no_workflow(monkeypatch)
    target = tmp_path if bad == "directory" else bad
    assert_input_error(check(target), "INPUT_INVALID")


def test_unreadable_pdf_is_input_unreadable_when_access_is_denied(monkeypatch, pdf_file):
    no_workflow(monkeypatch)
    monkeypatch.setattr(os, "access", lambda path, mode, **kwargs: False)
    assert_input_error(check(pdf_file), "INPUT_UNREADABLE")


def test_unreadable_pdf_is_input_unreadable_when_opening_is_denied(monkeypatch, pdf_file):
    no_workflow(monkeypatch)
    real_open = Path.open

    def deny(self, *args, **kwargs):
        if self == pdf_file:
            raise PermissionError(13, "Permission denied")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny)
    assert_input_error(check(pdf_file), "INPUT_UNREADABLE")


def test_a_non_pdf_file_name_is_not_rejected_by_the_precheck(monkeypatch, tmp_path):
    from src.pdf_reader import PdfResult

    path = make_pdf(tmp_path / "quotation.dat", ["Real PDF content in an odd file name"])
    seen = []

    def read(p):
        seen.append(p)
        return PdfResult(True, "x" * 120, 1, None)

    stub_pipeline(monkeypatch, [gate2_pass(extraction("01"))], read=read)
    result = check(path)
    assert seen == [str(path)]  # the precheck let it through; read_pdf decides
    assert result.status == "completed"


# ---- 7-8: LLM failures are system errors -------------------------------------
@pytest.mark.parametrize("message", [
    "anthropic call failed: APIConnectionError: Connection error.",
    "openai call failed: APITimeoutError: Request timed out.",
])
def test_llm_unavailable_or_timeout_is_a_retryable_error(monkeypatch, pdf_file, message):
    from src.llm import LLMError

    def fail(text, failures=()):
        raise LLMError(message)

    stub_pipeline(monkeypatch, [], extract=fail)
    result = check(pdf_file)
    assert result.status == "error"
    assert result.decision is None
    assert result.reasons == ()
    assert result.attempts == 1
    assert result.match is None
    assert result.error.code == "LLM_ERROR"
    assert result.error.stage == "extract"
    assert result.error.retryable is True
    assert message not in result.error.message  # fixed app text, not the provider text
    assert_invariants(result)


def test_llm_failure_during_the_retry_is_an_error_not_a_review(monkeypatch, pdf_file):
    from src.llm import LLMError

    calls = []

    def extract(text, failures=()):
        calls.append(tuple(failures))
        if len(calls) == 2:
            raise LLMError("provider timed out")
        return Raw()

    stub_pipeline(monkeypatch, [gate2_fail("unit_price: quote missing")], extract=extract)
    result = check(pdf_file)
    assert calls == [(), ("unit_price: quote missing",)]
    assert result.status == "error"
    assert result.decision is None
    assert result.reasons == ()  # the first attempt's Gate 2 failure is not shown
    assert result.attempts == 2
    assert result.error.code == "LLM_ERROR"
    assert result.error.stage == "extract"
    assert result.error.retryable is True
    assert_invariants(result)


# ---- 9: purchase-request CSV ---------------------------------------------------
@pytest.mark.parametrize("kind", [
    "empty", "missing_column", "duplicate_key", "missing_file", "none", "empty_string",
])
def test_unusable_csv_is_a_precheck_error_before_any_llm_call(
    monkeypatch, tmp_path, pdf_file, kind
):
    llm_must_not_run(monkeypatch)
    result = check(pdf_file, bad_csv(kind, tmp_path))
    assert_input_error(result, "PURCHASE_REQUEST_UNUSABLE", stage="precheck")


def test_csv_error_keeps_the_app_written_detail_for_the_operator(tmp_path, pdf_file):
    result = check(pdf_file, bad_csv("missing_column", tmp_path))
    assert "Maximum unit price (BDT)" in result.error.message


def test_a_csv_that_breaks_after_the_precheck_is_a_compare_stage_error(monkeypatch, pdf_file):
    import src.graph as graph
    from src.compare import PurchaseRequestError

    stub_pipeline(monkeypatch, [gate2_pass(extraction("01"))])

    def broken(path):
        raise PurchaseRequestError("late detail from the CSV")

    monkeypatch.setattr(graph, "load_purchase_requests", broken)  # the precheck is unaffected
    result = check(pdf_file)
    assert result.status == "error"
    assert result.decision is None
    assert result.attempts == 1  # the model was already called once
    assert result.error.code == "PURCHASE_REQUEST_UNUSABLE"
    assert result.error.stage == "compare"
    assert result.error.retryable is False
    assert "late detail from the CSV" in result.error.message
    assert_invariants(result)


# ---- 10: unexpected exceptions -------------------------------------------------
def test_unexpected_exception_in_the_workflow_is_a_clean_error(monkeypatch, pdf_file):
    import src.result as result_module

    def boom(*args, **kwargs):
        raise RuntimeError("boom internal detail")

    monkeypatch.setattr(result_module, "run_workflow", boom)
    result = check(pdf_file)
    assert result.status == "error"
    assert result.decision is None
    assert result.error.code == "UNEXPECTED"
    assert result.error.stage == "workflow"
    assert result.error.retryable is False
    text = serialise(result) + repr(result)
    for leaked in ("boom", "Traceback", "RuntimeError", "File \""):
        assert leaked not in text
    assert_invariants(result)


def test_unexpected_exception_while_building_the_result_has_stage_result(monkeypatch, pdf_file):
    import src.result as result_module

    stub_pipeline(monkeypatch, [gate2_pass(extraction("01"))])

    def boom(state):
        raise ValueError("cannot build this result")

    monkeypatch.setattr(result_module, "build_result", boom)
    result = check(pdf_file)
    assert result.status == "error"
    assert result.decision is None
    assert result.error.code == "UNEXPECTED"
    assert result.error.stage == "result"
    assert result.error.retryable is False
    assert "cannot build" not in serialise(result)
    assert_invariants(result)


def test_base_exceptions_are_not_swallowed(monkeypatch, pdf_file):
    import src.result as result_module

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(result_module, "run_workflow", interrupted)
    with pytest.raises(KeyboardInterrupt):
        check(pdf_file)


# ---- 11: error stage -----------------------------------------------------------
@pytest.mark.parametrize("case", ["read_pdf", "gate1", "extract", "compare"])
def test_graph_error_states_carry_the_stage(monkeypatch, tmp_path, case):
    import src.graph as graph
    from src.llm import LLMError
    from src.pdf_reader import PdfResult

    csv = CSV
    if case == "read_pdf":
        stub_pipeline(monkeypatch, [], read=lambda p: PdfResult(False, "", 0, "bad PDF"))
    elif case == "gate1":
        stub_pipeline(monkeypatch, [], read=lambda p: PdfResult(True, "short", 1, None))
    elif case == "extract":
        def fail(text, failures=()):
            raise LLMError("provider unavailable")
        stub_pipeline(monkeypatch, [], extract=fail)
    else:
        stub_pipeline(monkeypatch, [gate2_pass(extraction("01"))])
        csv = tmp_path / "missing.csv"
    state = graph.run_workflow("supplier.pdf", csv)
    assert state["status"] == "error"
    assert state["stage"] == case


def test_build_result_classifies_each_graph_error_stage():
    from src.result import build_result

    llm = build_result({"status": "error", "stage": "extract", "error": "x", "attempts": 1})
    assert llm.status == "error"
    assert (llm.error.code, llm.error.stage, llm.error.retryable) == ("LLM_ERROR", "extract", True)
    assert llm.attempts == 1

    csv = build_result({
        "status": "error", "stage": "compare",
        "error": "Purchase request file not found: pr.csv", "attempts": 1,
    })
    assert (csv.error.code, csv.error.stage, csv.error.retryable) == (
        "PURCHASE_REQUEST_UNUSABLE", "compare", False,
    )
    assert "not found" in csv.error.message

    for stage in ("read_pdf", "gate1"):
        doc = build_result({
            "status": "error", "stage": stage,
            "error": "Could not open the PDF (FileNotFoundError).", "attempts": 0,
        })
        assert doc.status == "needs_review"
        assert doc.decision == config.NEEDS_CHECK
        assert doc.error is None
        assert doc.reasons[0].code == "DOCUMENT_UNREADABLE"
        assert "Could not open the PDF" in doc.reasons[0].message
        assert doc.attempts == 0

    unknown = build_result({"status": "error", "error": "leak", "attempts": 1})
    assert unknown.error.code == "UNEXPECTED"
    assert unknown.error.stage == "workflow"
    assert "leak" not in serialise(unknown)


# ---- 12: attempts --------------------------------------------------------------
def test_attempts_count_the_model_calls(monkeypatch, pdf_file):
    stub_pipeline(monkeypatch, [gate2_pass(extraction("01"))])
    assert check(pdf_file).attempts == 1  # completed on the first try

    stub_pipeline(monkeypatch, [
        gate2_fail("unit_price: quote missing"),
        gate2_pass(extraction("01")),
    ])
    retried = check(pdf_file)
    assert retried.status == "completed"
    assert retried.attempts == 2  # completed after one retry


# ---- 13-14: evidence -----------------------------------------------------------
def test_a_null_field_is_not_stated_and_no_quote_is_invented(monkeypatch, pdf_file):
    r3 = reason(run_pack(monkeypatch, pdf_file, extraction("06")), "R3")  # no penalty clause
    assert r3.evidence == ()
    assert r3.not_stated == ("penalty_pct",)

    r4 = reason(run_pack(monkeypatch, pdf_file, extraction("08")), "R4")  # no TIN, no BIN
    assert r4.evidence == ()
    assert r4.not_stated == ("tin", "bin")


def test_one_missing_registration_number_is_not_stated_and_the_other_is_quoted(
    monkeypatch, pdf_file
):
    r4 = reason(run_pack(monkeypatch, pdf_file, extraction("01", tin=None)), "R4")
    assert r4.not_stated == ("tin",)
    assert r4.evidence == (("bin", GOLD["01"]["bin"][1]),)


def test_several_fields_for_one_rule_are_each_labelled_with_their_field(monkeypatch, pdf_file):
    r5 = reason(run_pack(monkeypatch, pdf_file, extraction("10")), "R5")
    assert r5.evidence == (
        ("delivery_value", GOLD["10"]["delivery_value"][1]),
        ("delivery_unit", GOLD["10"]["delivery_unit"][1]),
    )
    assert r5.not_stated == ()


def test_n2_quotes_the_penalty_and_lists_the_missing_basis(monkeypatch, pdf_file):
    n2 = reason(run_pack(monkeypatch, pdf_file, extraction("08")), "N2")
    assert n2.evidence == (("penalty_pct", GOLD["08"]["penalty_pct"][1]),)
    assert n2.not_stated == ("penalty_basis",)


# ---- 14: rule M ----------------------------------------------------------------
def test_rule_m_lists_only_the_missing_fields_and_has_no_quotes(monkeypatch, pdf_file):
    result = run_pack(
        monkeypatch, pdf_file, extraction("01", supplier_name=None, unit_price=None)
    )
    m = reason(result, "M")
    assert (m.source, m.severity) == ("rule", "review")
    assert m.evidence == ()
    assert m.not_stated == ("supplier_name", "unit_price")


# ---- 15: sanitisation (the WHOLE result is inspected) --------------------------
def test_a_poisoned_llm_error_never_reaches_the_result(monkeypatch, pdf_file):
    from src.llm import LLMError

    def fail(text, failures=()):
        raise LLMError(f"anthropic call failed: AuthenticationError: {POISON_TEXT}")

    stub_pipeline(monkeypatch, [], extract=fail)
    result = check(pdf_file)
    assert result.error.code == "LLM_ERROR"
    assert_no_poison(result)


@pytest.mark.parametrize("stage", ["extract", None, "mystery"])
def test_a_poisoned_state_error_is_never_copied_into_the_result(stage):
    from src.result import build_result

    state = {
        "status": "error", "error": POISON_TEXT,
        "failures": (POISON_TEXT,), "attempts": 1,
    }
    if stage is not None:
        state["stage"] = stage
    result = build_result(state)
    assert result.status == "error"
    assert_no_poison(result)


@pytest.mark.parametrize("where", ["run_workflow", "build_result"])
def test_a_poisoned_unexpected_exception_never_reaches_the_result(monkeypatch, pdf_file, where):
    import src.result as result_module

    stub_pipeline(monkeypatch, [gate2_pass(extraction("01"))])

    def boom(*args, **kwargs):
        raise RuntimeError(POISON_TEXT)

    monkeypatch.setattr(result_module, where, boom)
    result = check(pdf_file)
    assert result.error.code == "UNEXPECTED"
    assert_no_poison(result)


# ---- 16: all ten gold packs ----------------------------------------------------
@pytest.mark.parametrize("pack", sorted(GOLD))
def test_all_ten_gold_packs_match_expected_json(monkeypatch, pdf_file, pack):
    result = run_pack(monkeypatch, pdf_file, extraction(pack))
    assert result.status == "completed"
    assert result.decision == ANSWER_KEY[pack]["decision"]
    # exact list and exact order; a matched pack has no mismatch reason
    assert [r.code for r in result.reasons] == ANSWER_KEY[pack]["rule_ids"]
    assert all(r.source == "rule" for r in result.reasons)


# ---- 17: invariants over all ten packs -----------------------------------------
@pytest.mark.parametrize("pack", sorted(GOLD))
def test_invariants_hold_for_every_gold_pack(monkeypatch, pdf_file, pack):
    item = extraction(pack)
    assert_invariants(run_pack(monkeypatch, pdf_file, item), item)


# ---- shape ---------------------------------------------------------------------
def test_results_are_frozen_and_hold_tuples(monkeypatch, pdf_file):
    result = run_pack(monkeypatch, pdf_file, extraction("08"))
    assert isinstance(result.reasons, tuple) and isinstance(result.notes, tuple)
    for r in result.reasons:
        assert isinstance(r.evidence, tuple) and isinstance(r.not_stated, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.status = "error"
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.reasons[0].code = "X"


def test_build_result_is_pure_and_leaves_the_state_unchanged(monkeypatch):
    import src.graph as graph
    from src.result import build_result

    stub_pipeline(monkeypatch, [gate2_pass(extraction("10"))])
    state = graph.run_workflow("supplier.pdf", CSV)
    snapshot = copy.deepcopy(state)
    first = build_result(state)
    second = build_result(state)
    assert first == second
    assert state == snapshot


def test_raw_csv_os_error_detail_is_not_exposed(monkeypatch, pdf_file):
    import src.result as result_module
    from src.compare import PurchaseRequestError

    private_detail = (
        "Purchase request file could not be read: [Errno 13] Permission denied: "
        "'C:\\\\private\\\\requests.csv'"
    )

    def broken(path):
        raise PurchaseRequestError(private_detail)

    monkeypatch.setattr(result_module, "load_purchase_requests", broken)
    result = check(pdf_file)

    assert result.status == "error"
    assert result.error.code == "PURCHASE_REQUEST_UNUSABLE"
    assert "Purchase request file could not be read." in result.error.message
    assert "Permission denied" not in repr(result)
    assert "C:\\\\private" not in repr(result)


def test_missing_csv_path_is_not_exposed(pdf_file, tmp_path):
    private_path = tmp_path / "private" / "requests.csv"
    result = check(pdf_file, private_path)
    assert result.status == "error"
    assert result.error.code == "PURCHASE_REQUEST_UNUSABLE"
    assert "Purchase request file was not found." in result.error.message
    assert str(private_path) not in repr(result)
