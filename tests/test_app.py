"""F010 page tests with Streamlit's AppTest. Written first: red until app.py exists.

check_supplier is replaced by a spy in every test, so nothing here reaches a model or the
network. The real report writer runs (into a temporary folder, through run_submission).
"""
import ast
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src import config
from src.result import (
    STATUS_COMPLETED,
    STATUS_ERROR,
    STATUS_NEEDS_REVIEW,
    CheckResult,
    ErrorInfo,
    MatchSummary,
    Reason,
)

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app.py"
PDF_BYTES = b"%PDF-1.4 uploaded supplier quotation"
CSV_BYTES = b"PR Number,Item\r\nPR-1,A\r\n"
OTHER_CSV = b"PR Number,Item\r\nPR-2,B\r\n"
PAYLOAD = "<script>alert(1)</script> **bold** [x](http://evil.example) # heading"


def reason(code="R1", severity="reject", message="A rule found a problem.", evidence=(), not_stated=()):
    return Reason("rule", code, message, severity, tuple(evidence), tuple(not_stated))


def completed(decision=config.APPROVE, reasons=(), **extra):
    return CheckResult(status=STATUS_COMPLETED, decision=decision, reasons=tuple(reasons), **extra)


def failed():
    return CheckResult(
        status=STATUS_ERROR,
        decision=None,
        error=ErrorInfo("LLM_ERROR", "Nothing was checked.", "extract", True),
        attempts=1,
    )


class CheckerSpy:
    def __init__(self, result=None, error=None):
        self.result = result if result is not None else completed()
        self.error = error
        self.calls = 0

    def __call__(self, pdf_path, csv_path):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def checker(monkeypatch):
    import src.ui_helpers as helpers

    spy = CheckerSpy()
    monkeypatch.setattr(helpers, "check_supplier", spy)
    return spy


def start():
    return AppTest.from_file(str(APP), default_timeout=20).run()


def upload(at, pdf=PDF_BYTES, csv=CSV_BYTES, pdf_name="quote.pdf", csv_name="request.csv"):
    if pdf is not None:
        at.file_uploader[0].set_value((pdf_name, pdf, "application/pdf"))
    if csv is not None:
        at.file_uploader[1].set_value((csv_name, csv, "text/csv"))
    return at.run()


def press(at):
    at.button[0].click()
    return at.run()


def full_run():
    at = upload(start())
    return press(at)


def values(elements):
    return [e.value for e in elements]


def banner_values(at):
    return values(at.success) + values(at.error) + values(at.warning) + values(at.info)


def text_values(at):
    return values(at.text)


# ======================= before any check ============================================
def test_the_page_loads_with_no_error_and_shows_the_limits(checker):
    import src.ui_helpers as helpers

    at = start()
    assert not at.exception
    assert len(at.file_uploader) == 2
    assert helpers.LIMITS_NOTE in values(at.caption)
    assert checker.calls == 0


def test_the_button_is_disabled_until_both_files_are_present(checker):
    at = start()
    assert at.button[0].disabled is True
    at = upload(at, csv=None)
    assert at.button[0].disabled is True
    at = upload(at)
    assert at.button[0].disabled is False


def test_choosing_files_never_starts_a_check(checker):
    at = upload(start())
    assert checker.calls == 0
    assert text_values(at) == [] and at.get("download_button") == []


def test_an_oversized_upload_shows_a_message_and_keeps_the_button_disabled(checker):
    import src.ui_helpers as helpers

    at = upload(start(), pdf=b"x" * (helpers.MAX_PDF_BYTES + 1))
    assert helpers.MSG_PDF_TOO_LARGE in values(at.error)
    assert at.button[0].disabled is True
    assert checker.calls == 0


# ======================= running a check =============================================
def test_exactly_one_call_per_click_and_none_on_a_later_rerun(checker):
    at = full_run()
    assert checker.calls == 1
    at.run()
    at.run()
    assert checker.calls == 1
    assert at.session_state["running"] is False


def test_a_second_click_checks_again_once(checker):
    at = full_run()
    press(at)
    assert checker.calls == 2


@pytest.mark.parametrize(
    "decision,element",
    [(config.APPROVE, "success"), (config.REJECT, "error"), (config.NEEDS_CHECK, "warning")],
)
def test_each_decision_screen(checker, decision, element):
    checker.result = completed(decision, [reason()] if decision != config.APPROVE else [])
    at = full_run()
    assert not at.exception
    assert f"Decision: {decision}" in values(getattr(at, element))


def test_a_needs_review_result_renders(checker):
    checker.result = CheckResult(
        status=STATUS_NEEDS_REVIEW,
        decision=config.NEEDS_CHECK,
        reasons=(Reason("document", "DOCUMENT_UNREADABLE", "The PDF could not be read.", "review"),),
    )
    at = full_run()
    assert not at.exception
    assert f"Decision: {config.NEEDS_CHECK}" in values(at.warning)
    assert any("DOCUMENT_UNREADABLE" in t for t in text_values(at))


def test_an_error_result_shows_no_decision_and_the_error(checker):
    from src.report import NO_DECISION_LINE

    checker.result = failed()
    at = full_run()
    assert not at.exception
    assert NO_DECISION_LINE in values(at.error)
    joined = "\n".join(text_values(at))
    assert "LLM_ERROR" in joined and "Nothing was checked." in joined
    assert not any(f"Decision: {d}" in "\n".join(banner_values(at)) for d in (config.APPROVE, config.REJECT, config.NEEDS_CHECK))


def test_reasons_appear_in_the_report_order(checker):
    checker.result = completed(
        config.REJECT,
        [reason("H1", "review", "Review one."), reason("R1", "reject", "Reject one.")],
    )
    at = full_run()
    blocks = [t for t in text_values(at) if t[:3] in ("1. ", "2. ")]
    assert blocks[0].startswith("1. [R1] Reject - Reject one.")
    assert blocks[1].startswith("2. [H1] Review - Review one.")


def test_evidence_and_missing_fields_are_shown_as_given_and_never_invented(checker):
    checker.result = completed(
        config.REJECT,
        [
            reason("R1", "reject", "Price too high.", evidence=[("unit_price", "BDT 229 per bag")], not_stated=("tin",)),
            reason("H7", "review", "Validity is short."),
        ],
    )
    at = full_run()
    joined = "\n".join(text_values(at))
    assert 'Unit price: "BDT 229 per bag"' in joined
    assert "Not stated in the document: TIN" in joined
    assert "No quote available." in joined


def test_missing_optional_fields_do_not_crash(checker):
    checker.result = completed(config.APPROVE)          # no supplier, no match, no reasons
    at = full_run()
    assert not at.exception
    assert any(t == "Supplier: not available" for t in text_values(at))


def test_long_supplier_text_is_truncated_to_the_report_limit(checker):
    from src.report import MAX_TEXT_CHARS, SHORTENED

    supplier = "S" * (MAX_TEXT_CHARS + 50)
    checker.result = completed(config.APPROVE, supplier_name=supplier)
    at = full_run()
    shown = next(t for t in text_values(at) if t.startswith("Supplier: "))
    assert shown == "Supplier: " + supplier[:MAX_TEXT_CHARS] + SHORTENED
    assert len(shown.removeprefix("Supplier: ").removesuffix(SHORTENED)) == MAX_TEXT_CHARS


def test_a_failed_match_and_notes_are_shown(checker):
    checker.result = completed(
        config.NEEDS_CHECK,
        [reason("ITEM_MISMATCH", "review", "Could not match.")],
        match=MatchSummary(ok=False, problems=("The unit differs.",), notes=("VAT included.",)),
    )
    at = full_run()
    joined = "\n".join(text_values(at))
    assert "The unit differs." in joined and "VAT included." in joined


# ======================= untrusted text ==============================================
def test_injected_markdown_and_html_stay_in_plain_text_elements(checker):
    checker.result = completed(
        config.REJECT,
        [reason("R1", "reject", PAYLOAD, evidence=[("unit_price", PAYLOAD)])],
        supplier_name=PAYLOAD,
        match=MatchSummary(ok=False, problems=(PAYLOAD,), notes=(PAYLOAD,)),
    )
    at = full_run()
    assert not at.exception
    assert any(PAYLOAD in t for t in text_values(at))
    for kind in ("markdown", "success", "error", "warning", "info", "caption", "subheader", "header", "title"):
        assert not any(PAYLOAD in v for v in values(at.get(kind))), kind


def test_an_error_message_from_the_result_is_shown_as_plain_text(checker):
    checker.result = CheckResult(
        status=STATUS_ERROR,
        decision=None,
        error=ErrorInfo("PURCHASE_REQUEST_UNUSABLE", PAYLOAD, "precheck", False),
    )
    at = full_run()
    assert any(PAYLOAD in t for t in text_values(at))
    for kind in ("markdown", "success", "error", "warning", "info", "caption", "subheader"):
        assert not any(PAYLOAD in v for v in values(at.get(kind))), kind


# ======================= stale results ===============================================
@pytest.mark.parametrize("which", ["pdf", "csv"])
def test_the_result_is_cleared_when_an_upload_changes(checker, which):
    at = full_run()
    assert at.get("download_button") != []
    if which == "pdf":
        at = upload(at, pdf=b"%PDF-1.4 another quotation", pdf_name="other.pdf", csv=None)
    else:
        at = upload(at, pdf=None, csv=OTHER_CSV, csv_name="other.csv")
    assert not at.exception
    assert at.get("download_button") == [] and text_values(at) == []
    assert banner_values(at) == []
    assert checker.calls == 1


def test_clearing_an_upload_clears_the_result(checker):
    at = full_run()
    at.file_uploader[0].clear()
    at = at.run()
    assert text_values(at) == [] and at.get("download_button") == []
    assert at.button[0].disabled is True


def test_the_same_files_again_keep_the_result(checker):
    at = full_run()
    at.run()
    assert checker.calls == 1 and banner_values(at) != []


# ======================= download ====================================================
def test_a_download_is_offered_for_a_decision_and_for_an_error(checker):
    assert len(full_run().get("download_button")) == 1
    checker.result = failed()
    assert len(full_run().get("download_button")) == 1


def test_a_report_failure_shows_a_fixed_warning_and_keeps_the_result(checker, monkeypatch):
    import src.ui_helpers as helpers

    def boom(*args, **kwargs):
        raise OSError("C:\\Users\\SARK\\secret")

    monkeypatch.setattr(helpers, "write_report", boom)
    at = full_run()
    assert not at.exception
    assert helpers.MSG_REPORT_FAILED in values(at.warning)
    assert f"Decision: {config.APPROVE}" in values(at.success)
    assert at.get("download_button") == []
    assert "secret" not in "\n".join(banner_values(at) + text_values(at))


def test_a_workflow_exception_shows_a_fixed_message_and_no_download(checker):
    import src.ui_helpers as helpers

    checker.error = RuntimeError("sk-ant-SECRET C:\\Users\\SARK\\x")
    at = full_run()
    assert not at.exception
    assert helpers.MSG_CHECK_FAILED in values(at.error)
    assert at.get("download_button") == []
    assert "SECRET" not in "\n".join(banner_values(at) + text_values(at))


# ======================= keep the page presentation-only =============================
def test_app_imports_only_streamlit_and_the_ui_layer_and_never_renders_markdown_or_html():
    text = APP.read_text(encoding="utf-8")
    tree = ast.parse(text)
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "src":
                modules.update(f"src.{alias.name}" for alias in node.names)
            else:
                modules.add(node.module)
    assert modules <= {"streamlit", "src.ui_helpers", "src.report"}
    for forbidden in ("unsafe_allow_html", "st.markdown", "st.write", "st.html", "check_supplier"):
        assert forbidden not in text, forbidden
