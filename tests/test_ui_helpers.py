"""F010 UI helper tests. Written first: red until src/ui_helpers.py exists.

No live LLM call and no Streamlit import anywhere in this file. check_supplier and
write_report are replaced by spies, except in the tests that say they use the real
workflow (those force LLM_PROVIDER=mock with an empty answer queue, so nothing can
reach the network).

src.ui_helpers is imported inside each test (through ui()), so a missing module fails
every test on its own instead of one collection error. Small helpers are copied from
tests/test_report.py; that file is not edited.
"""
import ast
import itertools
import re
from decimal import Decimal
from pathlib import Path

import pytest

from src import config
from src.result import (
    STATUS_COMPLETED,
    STATUS_ERROR,
    STATUS_NEEDS_REVIEW,
    CheckResult,
    ErrorInfo,
    MatchedRow,
    MatchSummary,
    Reason,
)

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
PDF_BYTES = b"%PDF-1.4 uploaded supplier quotation"
CSV_BYTES = b"PR Number,Item\r\nPR-1,A\r\n"
DECISIONS = (config.APPROVE, config.REJECT, config.NEEDS_CHECK)
LEAK_MARKERS = ("Traceback", "C:\\", "C:/", "SECRET", "sk-ant", "Errno")


def ui():
    import src.ui_helpers as helpers

    return helpers


# ---- builders ----------------------------------------------------------------------
def reason(code="R1", severity="reject", message="A rule found a problem.", evidence=(), not_stated=()):
    return Reason("rule", code, message, severity, tuple(evidence), tuple(not_stated))


def completed(decision=config.APPROVE, reasons=(), **extra):
    return CheckResult(
        status=STATUS_COMPLETED, decision=decision, reasons=tuple(reasons), **extra
    )


def failed(code="LLM_ERROR", stage="extract", message="Nothing was checked.", retryable=True, **extra):
    return CheckResult(
        status=STATUS_ERROR,
        decision=None,
        error=ErrorInfo(code, message, stage, retryable),
        **extra,
    )


def matched(ok=True, problems=(), notes=(), with_row=True):
    row = (
        MatchedRow("PR-2026-114", "A", "Cement", "bag", Decimal("210.50"), Decimal("20000"))
        if with_row
        else None
    )
    return MatchSummary(ok=ok, row=row, problems=tuple(problems), notes=tuple(notes))


# ---- spies -------------------------------------------------------------------------
class CheckerSpy:
    """Stands in for check_supplier and records what the files looked like during the call."""

    def __init__(self, result=None, error=None):
        self.result = result if result is not None else completed()
        self.error = error
        self.calls = []

    def __call__(self, pdf_path, csv_path):
        pdf, csv = Path(pdf_path), Path(csv_path)
        self.calls.append(
            {
                "pdf": pdf,
                "csv": csv,
                "exists": pdf.is_file() and csv.is_file(),
                "pdf_bytes": pdf.read_bytes(),
                "csv_bytes": csv.read_bytes(),
            }
        )
        if self.error is not None:
            raise self.error
        return self.result


class WriterSpy:
    """Records the call, then delegates to the REAL write_report (into the folder given)."""

    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def __call__(self, result, source_path, out_dir):
        self.calls.append({"result": result, "source_path": source_path, "out_dir": out_dir})
        if self.error is not None:
            raise self.error
        from src.report import write_report

        return write_report(result, source_path, out_dir)


@pytest.fixture
def spies(monkeypatch):
    import src.ui_helpers as helpers

    checker, writer = CheckerSpy(), WriterSpy()
    monkeypatch.setattr(helpers, "check_supplier", checker)
    monkeypatch.setattr(helpers, "write_report", writer)
    return checker, writer


def submit(name="quote.pdf", pdf=PDF_BYTES, csv=CSV_BYTES):
    return ui().run_submission(name, pdf, csv)


def snapshot(folder):
    if not folder.exists():
        return None
    return sorted(
        (p.relative_to(folder).as_posix(), p.stat().st_size, p.stat().st_mtime_ns)
        for p in folder.rglob("*")
    )


def assert_no_leak(text):
    for marker in LEAK_MARKERS:
        assert marker not in text, marker


# ======================= upload and input handling ===================================
def test_both_files_present_is_fine():
    assert ui().upload_problems(PDF_BYTES, CSV_BYTES) == ()


def test_missing_pdf_is_reported():
    h = ui()
    assert h.upload_problems(None, CSV_BYTES) == (h.MSG_PDF_MISSING,)
    assert "PDF" in h.MSG_PDF_MISSING


def test_missing_csv_is_reported():
    h = ui()
    assert h.upload_problems(PDF_BYTES, None) == (h.MSG_CSV_MISSING,)
    assert "CSV" in h.MSG_CSV_MISSING


def test_both_missing_gives_both_messages_pdf_first():
    h = ui()
    assert h.upload_problems(None, None) == (h.MSG_PDF_MISSING, h.MSG_CSV_MISSING)


def test_limits_are_ten_and_one_megabyte_and_the_messages_say_so():
    h = ui()
    assert h.MAX_PDF_BYTES == 10 * 1024 * 1024
    assert h.MAX_CSV_BYTES == 1 * 1024 * 1024
    assert "10 MB" in h.MSG_PDF_TOO_LARGE
    assert "1 MB" in h.MSG_CSV_TOO_LARGE and "10 MB" not in h.MSG_CSV_TOO_LARGE
    assert "10 MB" in h.LIMITS_NOTE and "1 MB" in h.LIMITS_NOTE


def test_a_file_exactly_at_the_limit_is_accepted_and_one_byte_more_is_not():
    h = ui()
    assert h.upload_problems(b"x" * h.MAX_PDF_BYTES, b"x" * h.MAX_CSV_BYTES) == ()
    assert h.upload_problems(b"x" * (h.MAX_PDF_BYTES + 1), CSV_BYTES) == (h.MSG_PDF_TOO_LARGE,)
    assert h.upload_problems(PDF_BYTES, b"x" * (h.MAX_CSV_BYTES + 1)) == (h.MSG_CSV_TOO_LARGE,)
    assert h.upload_problems(b"x" * (h.MAX_PDF_BYTES + 1), b"x" * (h.MAX_CSV_BYTES + 1)) == (
        h.MSG_PDF_TOO_LARGE,
        h.MSG_CSV_TOO_LARGE,
    )


def test_missing_pdf_never_starts_a_check(spies):
    checker, writer = spies
    outcome = ui().run_submission("quote.pdf", None, CSV_BYTES)
    assert checker.calls == [] and writer.calls == []
    assert outcome.result is None and outcome.failure == ui().MSG_PDF_MISSING
    assert outcome.report_bytes is None


def test_missing_csv_never_starts_a_check(spies):
    checker, writer = spies
    outcome = ui().run_submission("quote.pdf", PDF_BYTES, None)
    assert checker.calls == [] and writer.calls == []
    assert outcome.result is None and outcome.failure == ui().MSG_CSV_MISSING


def test_oversized_upload_never_starts_a_check(spies):
    checker, writer = spies
    outcome = ui().run_submission("quote.pdf", b"x" * (ui().MAX_PDF_BYTES + 1), CSV_BYTES)
    assert checker.calls == [] and writer.calls == []
    assert outcome.result is None and outcome.failure == ui().MSG_PDF_TOO_LARGE


def test_empty_and_oddly_named_files_are_left_to_the_workflow(spies):
    """The UI does not judge content or extension; the existing workflow does."""
    checker, _ = spies
    ui().run_submission("supplier.exe", b"", b"")
    assert len(checker.calls) == 1
    assert checker.calls[0]["pdf_bytes"] == b"" and checker.calls[0]["csv_bytes"] == b""


# ======================= temporary files =============================================
def test_the_workflow_gets_real_files_holding_exactly_the_uploaded_bytes(spies):
    checker, _ = spies
    submit()
    call = checker.calls[0]
    assert call["exists"] is True
    assert call["pdf_bytes"] == PDF_BYTES and call["csv_bytes"] == CSV_BYTES
    assert call["pdf"].parent == call["csv"].parent


@pytest.mark.parametrize("hostile", ["../../evil.pdf", "..\\..\\evil.pdf", "C:\\Windows\\evil.pdf", "con.pdf"])
def test_the_uploaded_name_is_never_used_in_a_disk_path(spies, hostile):
    checker, _ = spies
    submit(name=hostile)
    call = checker.calls[0]
    assert call["pdf"].name == "supplier.pdf"
    assert call["csv"].name == "purchase-request.csv"
    assert "evil" not in str(call["pdf"]).lower() and "windows" not in str(call["pdf"]).lower()


def test_every_submission_gets_its_own_folder_and_both_are_removed(spies):
    checker, _ = spies
    submit()
    submit()
    first, second = checker.calls
    assert first["pdf"].parent != second["pdf"].parent
    for call in checker.calls:
        assert not call["pdf"].exists() and not call["csv"].exists()
        assert not call["pdf"].parent.exists()


def test_the_folder_is_removed_when_the_workflow_raises(spies):
    checker, _ = spies
    checker.error = RuntimeError("boom")
    submit()
    assert not checker.calls[0]["pdf"].parent.exists()


def test_the_folder_is_removed_when_the_report_writer_raises(spies):
    checker, writer = spies
    writer.error = OSError("disk problem")
    submit()
    assert not checker.calls[0]["pdf"].parent.exists()


def test_a_temp_folder_that_cannot_be_created_is_a_safe_failure(spies, monkeypatch):
    checker, writer = spies
    h = ui()

    def boom(*args, **kwargs):
        raise OSError("disk full C:\\Temp\\secret")

    monkeypatch.setattr(h.tempfile, "TemporaryDirectory", boom)
    outcome = submit()
    assert checker.calls == [] and writer.calls == []
    assert outcome.result is None and outcome.failure == h.MSG_TEMP_FAILED
    assert_no_leak(repr(outcome))


def test_files_that_cannot_be_written_are_a_safe_failure(spies, monkeypatch):
    checker, _ = spies
    h = ui()

    def boom(self, data):
        raise OSError("no space C:\\Temp\\secret")

    monkeypatch.setattr(Path, "write_bytes", boom)
    outcome = submit()
    assert checker.calls == []
    assert outcome.result is None and outcome.failure == h.MSG_TEMP_FAILED
    assert_no_leak(repr(outcome))


def test_real_workflow_releases_a_real_pdf_so_the_folder_can_be_removed(monkeypatch):
    """Windows keeps a folder locked while a file in it is open: use the real PDF reader."""
    import src.ui_helpers as helpers
    from src import llm

    monkeypatch.setenv("LLM_PROVIDER", "mock")
    llm.set_mock_responses([])          # no queued answer: the model step fails cleanly, offline
    folders = []
    real = helpers.check_supplier

    def recording(pdf_path, csv_path):
        folders.append(Path(pdf_path).parent)
        return real(pdf_path, csv_path)

    monkeypatch.setattr(helpers, "check_supplier", recording)
    outcome = helpers.run_submission(
        "supplier-pack-01.pdf",
        (SAMPLES / "supplier-pack-01.pdf").read_bytes(),
        (SAMPLES / "purchase-request.csv").read_bytes(),
    )
    assert len(folders) == 1 and not folders[0].exists()
    assert outcome.result is not None
    assert outcome.result.status == STATUS_ERROR and outcome.result.decision is None
    assert b"Source file: supplier-pack-01.pdf" in outcome.report_bytes


def test_real_workflow_sends_an_unreadable_pdf_to_a_person_and_cleans_up(monkeypatch):
    import src.ui_helpers as helpers
    from src import llm

    monkeypatch.setenv("LLM_PROVIDER", "mock")
    llm.set_mock_responses([])
    folders = []
    real = helpers.check_supplier

    def recording(pdf_path, csv_path):
        folders.append(Path(pdf_path).parent)
        return real(pdf_path, csv_path)

    monkeypatch.setattr(helpers, "check_supplier", recording)
    outcome = helpers.run_submission(
        "scan.pdf", b"this is not a pdf", (SAMPLES / "purchase-request.csv").read_bytes()
    )
    assert not folders[0].exists()
    assert outcome.result.status == STATUS_NEEDS_REVIEW
    assert outcome.result.decision == config.NEEDS_CHECK


# ======================= workflow integration ========================================
def test_the_workflow_runs_exactly_once_per_submission(spies):
    checker, writer = spies
    submit()
    assert len(checker.calls) == 1 and len(writer.calls) == 1
    submit()
    assert len(checker.calls) == 2 and len(writer.calls) == 2


def test_the_workflow_result_is_the_result_that_is_used(spies):
    checker, writer = spies
    checker.result = completed(config.REJECT, [reason()])
    outcome = submit()
    assert outcome.result is checker.result
    assert writer.calls[0]["result"] is checker.result


def test_workflow_exception_is_a_safe_failure_with_no_report(spies):
    checker, writer = spies
    checker.error = RuntimeError("sk-ant-SECRET C:\\Users\\SARK\\x")
    outcome = submit()
    assert outcome.result is None and outcome.failure == ui().MSG_CHECK_FAILED
    assert writer.calls == [] and outcome.report_bytes is None
    assert_no_leak(repr(outcome))


# ======================= report download =============================================
def test_the_report_is_written_from_the_same_result_under_the_original_name(spies):
    from src.report import REPORT_DIR

    checker, writer = spies
    outcome = submit(name="Quote (final).pdf")
    call = writer.calls[0]
    assert call["result"] is checker.result
    assert call["source_path"] == "Quote (final).pdf"           # the label, never a temp path
    out = Path(call["out_dir"])
    assert out.is_relative_to(checker.calls[0]["pdf"].parent)   # inside this submission's folder
    assert out.resolve() != REPORT_DIR.resolve()
    assert outcome.report_failure is None


def test_report_bytes_are_exactly_the_rendered_report(spies):
    from src.report import render_report

    checker, _ = spies
    checker.result = completed(config.REJECT, [reason()], supplier_name="ACME Ltd")
    outcome = submit(name="quote.pdf")
    assert outcome.report_bytes == render_report(checker.result, "quote.pdf").encode("utf-8")
    assert outcome.report_name.endswith(".decision.txt")
    assert "/" not in outcome.report_name and "\\" not in outcome.report_name


def test_error_results_also_get_a_report(spies):
    checker, _ = spies
    checker.result = failed()
    outcome = submit()
    assert outcome.report_bytes is not None
    assert b"No decision was made." in outcome.report_bytes
    assert not any(d.encode() in outcome.report_bytes for d in DECISIONS)


def test_report_failure_keeps_the_result_and_shows_a_fixed_message(spies):
    checker, writer = spies
    writer.error = OSError("C:\\Users\\SARK\\secret\\report.txt")
    outcome = submit()
    assert outcome.result is checker.result
    assert outcome.report_bytes is None and outcome.report_name is None
    assert outcome.report_failure == ui().MSG_REPORT_FAILED
    assert_no_leak(repr(outcome))


def test_nothing_is_written_to_the_project_report_folders(spies):
    before = snapshot(ROOT / "reports")        # the tracked raw-text files and reports/decisions
    submit()
    submit(name="other.pdf")
    assert snapshot(ROOT / "reports") == before


def test_files_in_the_working_folder_are_not_overwritten_or_added_to(spies, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "supplier.pdf").write_bytes(b"my own file")
    (tmp_path / "purchase-request.csv").write_bytes(b"my own csv")
    submit(name="supplier.pdf")
    assert (tmp_path / "supplier.pdf").read_bytes() == b"my own file"
    assert (tmp_path / "purchase-request.csv").read_bytes() == b"my own csv"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["purchase-request.csv", "supplier.pdf"]


# ======================= stale-result fingerprint ====================================
def test_fingerprint_changes_when_either_upload_changes():
    h = ui()
    base = h.fingerprint("a.pdf", b"1", "b.csv", b"2")
    assert base == h.fingerprint("a.pdf", b"1", "b.csv", b"2")
    for other in (
        ("x.pdf", b"1", "b.csv", b"2"),
        ("a.pdf", b"9", "b.csv", b"2"),
        ("a.pdf", b"1", "x.csv", b"2"),
        ("a.pdf", b"1", "b.csv", b"9"),
        ("a.pdf", b"2", "b.csv", b"1"),
        (None, None, None, None),
    ):
        assert h.fingerprint(*other) != base


def test_fingerprint_cannot_be_fooled_by_moving_bytes_between_fields():
    h = ui()
    assert h.fingerprint("ab", b"c", "x", b"") != h.fingerprint("a", b"bc", "x", b"")


# ======================= decision display ============================================
@pytest.mark.parametrize(
    "decision,kind",
    [(config.APPROVE, "success"), (config.REJECT, "error"), (config.NEEDS_CHECK, "warning")],
)
def test_each_decision_gets_its_own_banner(decision, kind):
    view = ui().build_view(completed(decision))
    assert view.banner.kind == kind
    assert view.banner.text == f"Decision: {decision}"


def test_a_needs_review_result_shows_the_needs_check_banner():
    result = CheckResult(
        status=STATUS_NEEDS_REVIEW,
        decision=config.NEEDS_CHECK,
        reasons=(Reason("document", "DOCUMENT_UNREADABLE", "The PDF could not be read.", "review"),),
    )
    view = ui().build_view(result)
    assert view.banner.kind == "warning"
    assert view.banner.text == f"Decision: {config.NEEDS_CHECK}"
    assert view.reasons[0].code == "DOCUMENT_UNREADABLE"


def test_an_error_result_has_no_decision_and_shows_the_error():
    from src.report import NO_DECISION_LINE, RETRY_MAY_HELP

    view = ui().build_view(failed("LLM_ERROR", "extract", "Nothing was checked.", True, attempts=1))
    assert view.banner.kind == "error" and view.banner.text == NO_DECISION_LINE
    assert not any(d in view.banner.text for d in DECISIONS)
    assert view.reasons == () and view.match is None
    assert (view.error.code, view.error.stage, view.error.message) == (
        "LLM_ERROR",
        "extract",
        "Nothing was checked.",
    )
    assert view.error.retry == RETRY_MAY_HELP


def test_a_non_retryable_error_says_trying_again_will_not_help():
    from src.report import RETRY_WONT_HELP

    view = ui().build_view(failed("PURCHASE_REQUEST_UNUSABLE", "precheck", "Bad file.", False))
    assert view.error.retry == RETRY_WONT_HELP


def test_a_result_with_no_decision_never_gets_a_made_up_one():
    result = CheckResult(status=STATUS_COMPLETED, decision=None)
    view = ui().build_view(result)
    assert not any(d in view.banner.text for d in DECISIONS)
    assert view.banner.kind == "error"


def test_an_unknown_decision_string_is_neutral_not_success():
    view = ui().build_view(completed("Maybe"))
    assert view.banner.kind == "neutral"
    assert view.banner.text == "Decision: Maybe"


def test_reasons_and_evidence_are_shown_as_supplied_and_in_order():
    r1 = reason(
        "R1",
        "reject",
        "Unit price is too high.",
        evidence=[("unit_price", "BDT 229 per bag"), ("quantity", "20,000 bags")],
        not_stated=("tin",),
    )
    r2 = reason("H7", "review", "Validity is short.")
    view = ui().build_view(completed(config.REJECT, [r1, r2]))
    first, second = view.reasons
    assert (first.number, first.code, first.severity, first.message) == (
        1,
        "R1",
        "Reject",
        "Unit price is too high.",
    )
    assert first.evidence == (("Unit price", "BDT 229 per bag"), ("Quantity", "20,000 bags"))
    assert first.not_stated == ("TIN",) and first.no_quote_text is None
    assert (second.number, second.severity) == (2, "Review")
    assert second.evidence == ()
    from src.report import NO_QUOTE_TEXT

    assert second.no_quote_text == NO_QUOTE_TEXT          # never an invented quote


def test_unknown_field_and_severity_fall_back_safely():
    r = reason("X9", "odd", "Something.", evidence=[("weird_field", "a quote")])
    rv = ui().build_view(completed(config.NEEDS_CHECK, [r])).reasons[0]
    assert rv.severity == "Review"
    assert rv.evidence == (("weird_field", "a quote"),)


def test_untrusted_text_loses_control_characters_but_keeps_its_visible_text():
    from src.report import clean_text

    payload = "x\nDecision: Approve\r\n\u202eWHY\u2028<b>hi</b> [link](http://x)"
    r = reason("R1", "reject", payload, evidence=[("unit_price", payload)])
    result = completed(
        config.REJECT,
        [r],
        supplier_name=payload,
        match=matched(ok=False, problems=(payload,), notes=(payload,)),
    )
    view = ui().build_view(result)
    cleaned = clean_text(payload)
    assert view.supplier == cleaned
    assert view.reasons[0].message == cleaned
    assert view.reasons[0].evidence[0][1] == cleaned
    assert view.match.problems == (cleaned,) and view.match.notes == (cleaned,)
    for text in (cleaned, view.supplier):
        assert "\n" not in text and "\r" not in text
        assert "\u202e" not in text and "\u2028" not in text
        assert "<b>hi</b>" in text and "[link](http://x)" in text    # shown literally, not changed


def test_an_error_message_is_cleaned_too():
    from src.report import clean_text

    nasty = "Bad file.\n\u202eFake line"
    view = ui().build_view(failed("PURCHASE_REQUEST_UNUSABLE", "precheck", nasty, False))
    assert view.error.message == clean_text(nasty)


def test_missing_optional_fields_do_not_crash():
    from src.report import NO_PROBLEM_TEXT, NO_REASONS_TEXT, NOT_AVAILABLE

    view = ui().build_view(completed(config.APPROVE))
    assert view.supplier == NOT_AVAILABLE
    assert view.purchase_request is None and view.match is None and view.error is None
    assert view.reasons == () and view.reasons_note == NO_PROBLEM_TEXT
    assert view.model_calls == "0"
    assert ui().build_view(completed(config.NEEDS_CHECK)).reasons_note == NO_REASONS_TEXT


def test_the_model_call_count_is_hidden_after_an_unexpected_error():
    h = ui()
    assert h.build_view(failed("UNEXPECTED", "workflow", "Oops.", False, attempts=0)).model_calls is None
    assert h.build_view(failed("LLM_ERROR", attempts=1)).model_calls == "1"


def test_match_details_come_from_the_result_and_notes_are_merged_without_duplicates():
    from src.report import MATCH_OK_TEXT

    vat = "VAT included: the price may not be comparable."
    result = completed(
        config.REJECT,
        [reason()],
        match=matched(ok=True, notes=(vat,)),
        notes=(vat, "Another note."),
    )
    view = ui().build_view(result)
    assert view.purchase_request == "PR-2026-114, item A"
    assert view.match.summary == MATCH_OK_TEXT
    assert view.match.row_lines == (
        ("Purchase request", "PR-2026-114"),
        ("Item", "A"),
        ("Description", "Cement"),
        ("Unit", "bag"),
        ("Maximum unit price (BDT)", "210.50"),
        ("Requested quantity", "20000"),
    )
    assert view.match.notes == (vat, "Another note.")
    assert view.match.problems == ()


def test_a_failed_match_shows_its_problems_and_no_row():
    from src.report import MATCH_FAILED_TEXT

    result = completed(
        config.NEEDS_CHECK,
        [reason("ITEM_MISMATCH", "review", "Could not match.")],
        match=matched(ok=False, problems=("The unit differs.",), with_row=False),
    )
    view = ui().build_view(result)
    assert view.match.summary == MATCH_FAILED_TEXT
    assert view.match.row_lines == () and view.match.problems == ("The unit differs.",)
    assert view.purchase_request is None


# ======================= reason order ================================================
def test_reject_reasons_come_first_and_the_rest_keep_their_order():
    reasons = [reason("H1", "review"), reason("R1", "reject"), reason("N2", "review"), reason("R5", "reject")]
    view = ui().build_view(completed(config.REJECT, reasons))
    assert [r.code for r in view.reasons] == ["R1", "R5", "H1", "N2"]
    assert [r.number for r in view.reasons] == [1, 2, 3, 4]


def report_pairs(result):
    """(code, severity label) of every numbered reason line in the rendered report."""
    from src.report import render_report

    text = render_report(result, "quote.pdf")
    return re.findall(r"^\d+\. \[([^\]]*)\] (\w+) - ", text, flags=re.M)


def test_ui_order_always_matches_the_report_order():
    """Drift guard: if report.py ever orders reasons differently, this fails."""
    items = [
        reason("A1", "review"),
        reason("B1", "reject"),
        reason("C1", "odd"),
        reason("D1", "reject"),
        reason("A1", "review", message="Same code again."),
    ]
    for ordering in itertools.permutations(items):
        result = completed(config.REJECT, ordering)
        view = ui().build_view(result)
        assert [(r.code, r.severity) for r in view.reasons] == report_pairs(result)


def test_an_error_result_has_no_reasons_in_either_place():
    result = failed()
    assert ui().build_view(result).reasons == () and report_pairs(result) == []


# ======================= no business logic in the UI layer ===========================
ALLOWED_SRC_IMPORTS = {"src.result", "src.report", "src.config"}


def src_imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "src":
                found.update(f"src.{alias.name}" for alias in node.names)
            else:
                found.add(node.module)
    return found


def test_ui_helpers_reuses_only_result_report_and_config():
    path = ROOT / "src" / "ui_helpers.py"
    found = src_imports(path)
    assert {n for n in found if n == "src" or n.startswith("src.")} <= ALLOWED_SRC_IMPORTS
    assert not any(n.split(".")[0] == "streamlit" for n in found)
    assert "unsafe_allow_html" not in path.read_text(encoding="utf-8")
