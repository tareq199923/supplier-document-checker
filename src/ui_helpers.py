"""F010: logic behind the Streamlit page, with no Streamlit import.

PRESENTATION SUPPORT ONLY. Nothing here decides anything. The decision, the reasons and
the match all come from ONE CheckResult built by src.result.check_supplier(); the report
text comes from src.report. This module only:

  * checks that two uploads are present and not too large,
  * runs check_supplier() once inside a private temporary folder and always removes it,
  * reads the written report into memory for download,
  * turns a CheckResult into plain display values (view-models) whose free text has
    already been cleaned with report.clean_text().

Free text from the supplier document (names, quotes, messages) is never trusted. The page
shows it with st.text / st.code only (never as Markdown or HTML), and this module removes
line breaks, control and bidi characters first, exactly as the report does.
"""
from __future__ import annotations

import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from src import config
from src.report import (
    FALLBACK_SEVERITY_LABEL,
    FIELD_LABELS,
    MATCH_FAILED_TEXT,
    MATCH_OK_TEXT,
    NO_DECISION_LINE,
    NO_PROBLEM_TEXT,
    NO_QUOTE_TEXT,
    NO_REASONS_TEXT,
    NOT_AVAILABLE,
    RETRY_MAY_HELP,
    RETRY_WONT_HELP,
    SEVERITY_LABELS,
    MAX_TEXT_CHARS,
    SHORTENED,
    clean_text,
    write_report,
)
from src.result import SEVERITY_REJECT, STATUS_ERROR, UNEXPECTED, CheckResult, check_supplier

# ---- upload limits and fixed wording --------------------------------------------------
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_CSV_BYTES = 1 * 1024 * 1024

PDF_FILENAME = "supplier.pdf"                 # generated names: the upload name never touches disk
CSV_FILENAME = "purchase-request.csv"
REPORT_SUBFOLDER = "report"

LIMITS_NOTE = "Limits: supplier PDF up to 10 MB, purchase request CSV up to 1 MB."
MSG_PDF_MISSING = "Please choose the supplier PDF."
MSG_CSV_MISSING = "Please choose the purchase request CSV."
MSG_PDF_TOO_LARGE = "The supplier PDF is larger than 10 MB. Please choose a smaller file."
MSG_CSV_TOO_LARGE = "The purchase request CSV is larger than 1 MB. Please choose a smaller file."
MSG_TEMP_FAILED = "The uploaded files could not be prepared for checking. Nothing was checked. Please try again."
MSG_CHECK_FAILED = "The check could not be completed. Nothing was decided. Please try again."
MSG_REPORT_FAILED = "The decision report could not be prepared, so it cannot be downloaded. The result above is still valid."

# banner kinds (the page maps each to one Streamlit status element)
KIND_SUCCESS = "success"
KIND_ERROR = "error"
KIND_WARNING = "warning"
KIND_NEUTRAL = "neutral"

_BANNER_KINDS = {
    config.APPROVE: KIND_SUCCESS,
    config.REJECT: KIND_ERROR,
    config.NEEDS_CHECK: KIND_WARNING,
}


# ---- uploads ---------------------------------------------------------------------------
def upload_problems(pdf_bytes: bytes | None, csv_bytes: bytes | None) -> tuple[str, ...]:
    """Fixed messages for missing or oversized uploads, PDF first. Empty tuple = fine.

    Content is not judged here: an empty or odd file goes to the workflow, which handles
    it before any model call.
    """
    problems: list[str] = []
    if pdf_bytes is None:
        problems.append(MSG_PDF_MISSING)
    elif len(pdf_bytes) > MAX_PDF_BYTES:
        problems.append(MSG_PDF_TOO_LARGE)
    if csv_bytes is None:
        problems.append(MSG_CSV_MISSING)
    elif len(csv_bytes) > MAX_CSV_BYTES:
        problems.append(MSG_CSV_TOO_LARGE)
    return tuple(problems)


def fingerprint(pdf_name, pdf_bytes, csv_name, csv_bytes) -> str:
    """One hash of both uploads (names and content). Length-prefixed, so bytes moved from
    one field to the next change it. Used to drop a result when an upload changes."""
    digest = hashlib.sha256()
    for part in (pdf_name, pdf_bytes, csv_name, csv_bytes):
        if part is None:
            digest.update(b"N")
            continue
        data = part.encode("utf-8", "surrogatepass") if isinstance(part, str) else bytes(part)
        digest.update(b"V" + len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


# ---- one submission ---------------------------------------------------------------------
@dataclass(frozen=True)
class Outcome:
    """What one submission produced. Messages are fixed text: no path, no traceback."""

    result: CheckResult | None = None
    report_bytes: bytes | None = None
    report_name: str | None = None
    report_failure: str | None = None     # the result exists but no report could be made
    failure: str | None = None            # no result at all


def _remove(folder: tempfile.TemporaryDirectory) -> None:
    root = folder.name
    try:
        folder.cleanup()
    except Exception:  # noqa: BLE001 - never let cleanup hide the outcome
        shutil.rmtree(root, ignore_errors=True)


def _run_in(root: Path, source_name, pdf_bytes: bytes, csv_bytes: bytes) -> Outcome:
    pdf_path = root / PDF_FILENAME
    csv_path = root / CSV_FILENAME
    try:
        pdf_path.write_bytes(pdf_bytes)
        csv_path.write_bytes(csv_bytes)
    except Exception:  # noqa: BLE001 - no OS text reaches the page
        return Outcome(failure=MSG_TEMP_FAILED)

    try:
        result = check_supplier(str(pdf_path), str(csv_path))
    except Exception:  # noqa: BLE001 - check_supplier does not raise; this is a safety net
        return Outcome(failure=MSG_CHECK_FAILED)

    label = source_name if isinstance(source_name, str) and source_name.strip() else PDF_FILENAME
    try:
        report_path = write_report(result, label, out_dir=root / REPORT_SUBFOLDER)
        report_bytes = Path(report_path).read_bytes()      # read BEFORE the folder is removed
        report_name = Path(report_path).name
    except Exception:  # noqa: BLE001
        return Outcome(result=result, report_failure=MSG_REPORT_FAILED)
    return Outcome(result=result, report_bytes=report_bytes, report_name=report_name)


def run_submission(source_name, pdf_bytes: bytes | None, csv_bytes: bytes | None) -> Outcome:
    """Check one pair of uploads. Never raises. The check runs at most once.

    The uploaded file name is only a label for the report; the files on disk always get
    the generated names above, inside a folder that is removed before this returns.
    """
    problems = upload_problems(pdf_bytes, csv_bytes)
    if problems:
        return Outcome(failure=" ".join(problems))
    try:
        folder = tempfile.TemporaryDirectory(prefix="supplier-check-")
    except Exception:  # noqa: BLE001
        return Outcome(failure=MSG_TEMP_FAILED)
    try:
        return _run_in(Path(folder.name), source_name, pdf_bytes, csv_bytes)
    finally:
        _remove(folder)


# ---- display values ---------------------------------------------------------------------
@dataclass(frozen=True)
class Banner:
    kind: str
    text: str


@dataclass(frozen=True)
class ReasonView:
    number: int
    code: str
    severity: str
    message: str
    evidence: tuple[tuple[str, str], ...]
    not_stated: tuple[str, ...]
    no_quote_text: str | None


@dataclass(frozen=True)
class MatchView:
    summary: str
    row_lines: tuple[tuple[str, str], ...]
    problems: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class ErrorView:
    code: str
    stage: str
    message: str
    retry: str


@dataclass(frozen=True)
class ResultView:
    banner: Banner
    supplier: str
    purchase_request: str | None
    reasons: tuple[ReasonView, ...]
    reasons_note: str | None
    match: MatchView | None
    error: ErrorView | None
    model_calls: str | None


def _display_text(value: object) -> str:
    """Clean supplier-derived text and cap it at the same 240 chars as F009 reports."""
    text = clean_text(value)
    if len(text) <= MAX_TEXT_CHARS:
        return text
    cut = text[:MAX_TEXT_CHARS].rstrip()
    while cut and cut[-1] in ("\u200c", "\u200d"):
        cut = cut[:-1].rstrip()
    return cut + SHORTENED


def _label(field: object) -> str:
    if isinstance(field, str) and field in FIELD_LABELS:
        return FIELD_LABELS[field]
    return _display_text(field)


def _number(value: object) -> str:
    return _display_text(format(value, "f") if isinstance(value, Decimal) else value)


def _ordered(reasons) -> list:
    """Reject-severity first, the rest in their own order. A copy of the report's rule
    (src.report._ordered is private and F009 stays untouched); a test keeps the two equal."""
    return sorted(reasons, key=lambda r: 0 if r.severity == SEVERITY_REJECT else 1)


def _banner(result: CheckResult, no_decision: bool) -> Banner:
    if no_decision:
        return Banner(KIND_ERROR, NO_DECISION_LINE)
    kind = _BANNER_KINDS.get(result.decision, KIND_NEUTRAL)
    return Banner(kind, f"Decision: {clean_text(result.decision)}")


def _reason_view(number: int, reason) -> ReasonView:
    evidence = tuple((_label(field), _display_text(quote)) for field, quote in reason.evidence)
    return ReasonView(
        number=number,
        code=_display_text(reason.code),
        severity=SEVERITY_LABELS.get(reason.severity, FALLBACK_SEVERITY_LABEL),
        message=_display_text(reason.message),
        evidence=evidence,
        not_stated=tuple(_label(field) for field in reason.not_stated),
        no_quote_text=None if evidence else NO_QUOTE_TEXT,      # never an invented quote
    )


def _match_view(result: CheckResult) -> MatchView:
    match = result.match
    row = match.row
    row_lines: tuple[tuple[str, str], ...] = ()
    if row is not None:
        row_lines = (
            ("Purchase request", _display_text(row.pr_number)),
            ("Item", _display_text(row.item)),
            ("Description", _display_text(row.description)),
            ("Unit", _display_text(row.unit)),
            ("Maximum unit price (BDT)", _number(row.max_unit_price)),
            ("Requested quantity", _number(row.requested_quantity)),
        )
    notes = list(match.notes) + [n for n in result.notes if n not in match.notes]
    return MatchView(
        summary=MATCH_OK_TEXT if match.ok else MATCH_FAILED_TEXT,
        row_lines=row_lines,
        problems=tuple(_display_text(p) for p in match.problems),
        notes=tuple(_display_text(n) for n in notes),
    )


def _error_view(result: CheckResult) -> ErrorView:
    error = result.error
    if error is None:
        return ErrorView("UNKNOWN", "", "", RETRY_WONT_HELP)
    return ErrorView(
        code=_display_text(error.code),
        stage=_display_text(error.stage),
        message=_display_text(error.message),
        retry=RETRY_MAY_HELP if error.retryable else RETRY_WONT_HELP,
    )


def build_view(result: CheckResult) -> ResultView:
    """Everything the page shows, as already-cleaned plain values. Pure: no I/O."""
    no_decision = result.status == STATUS_ERROR or result.decision is None
    row = result.match.row if result.match is not None else None
    purchase_request = (
        f"{_display_text(row.pr_number)}, item {_display_text(row.item)}" if row is not None else None
    )

    reasons: tuple[ReasonView, ...] = ()
    reasons_note: str | None = None
    match_view: MatchView | None = None
    error_view: ErrorView | None = None
    if no_decision:
        error_view = _error_view(result)
    else:
        reasons = tuple(_reason_view(n, r) for n, r in enumerate(_ordered(result.reasons), 1))
        if not reasons:
            reasons_note = NO_PROBLEM_TEXT if result.decision == config.APPROVE else NO_REASONS_TEXT
        if result.match is not None:
            match_view = _match_view(result)

    # After an unexpected exception the count is a guess, so it is not shown (as in the report).
    unexpected = result.error is not None and result.error.code == UNEXPECTED
    return ResultView(
        banner=_banner(result, no_decision),
        supplier=_display_text(result.supplier_name) or NOT_AVAILABLE,
        purchase_request=purchase_request,
        reasons=reasons,
        reasons_note=reasons_note,
        match=match_view,
        error=error_view,
        model_calls=None if unexpected else _display_text(result.attempts),
    )
