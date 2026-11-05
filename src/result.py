"""F008: one normalised result layer for a supplier check.

F009 (reports) and F010 (the Streamlit UI) read ONE CheckResult. They never read
LangGraph state and never see a MatchResult, a Finding or a raw exception.

Plain Python, no LLM, no new framework. Python decides; this module only
reshapes what the fixed workflow (src/graph.py) already decided.

Document problems go to review; system problems are errors with no decision:

    unreadable, blank or too-short PDF, Gate 2 failed twice -> needs_review
    missing PDF path, LLM failure, unusable CSV, bug        -> error (no decision)

Error messages are FIXED, application-written text. Raw provider text, response
fragments, headers, tracebacks and str(exception) never reach a CheckResult.
The only free text that may appear in an error is the app-written detail of a
PurchaseRequestError (kept for the operator).
"""
from __future__ import annotations

import errno
import os
import stat
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from src import config
from src.compare import MatchResult, PurchaseRequestError, load_purchase_requests
from src.graph import (
    STAGE_COMPARE,
    STAGE_EXTRACT,
    STAGE_GATE1,
    STAGE_READ_PDF,
    WorkflowState,
    run_workflow,
)
from src.rules import RULE_FIELDS, Finding
from src.schema import Extraction

# ---- vocabulary (plain strings so reports and the UI can compare them) --------
STATUS_COMPLETED = "completed"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_ERROR = "error"

SOURCE_RULE = "rule"
SOURCE_MATCH = "match"
SOURCE_EXTRACTION = "extraction"
SOURCE_DOCUMENT = "document"

SEVERITY_REJECT = "reject"
SEVERITY_REVIEW = "review"

# Reason codes that are not rule IDs. A mismatch is NOT a rule.
ITEM_MISMATCH = "ITEM_MISMATCH"
GATE2_FAILED = "GATE2_FAILED"
DOCUMENT_UNREADABLE = "DOCUMENT_UNREADABLE"

# Error codes
INPUT_NOT_FOUND = "INPUT_NOT_FOUND"
INPUT_INVALID = "INPUT_INVALID"
INPUT_UNREADABLE = "INPUT_UNREADABLE"
LLM_ERROR = "LLM_ERROR"
PURCHASE_REQUEST_UNUSABLE = "PURCHASE_REQUEST_UNUSABLE"
UNEXPECTED = "UNEXPECTED"

# Stages set by this module. The workflow stages (read_pdf, gate1, extract,
# compare) are set by src/graph.py itself.
STAGE_INPUT = "input"          # PDF path precheck
STAGE_PRECHECK = "precheck"    # CSV precheck, before any model call
STAGE_WORKFLOW = "workflow"    # unexpected exception inside run_workflow (fallback)
STAGE_RESULT = "result"        # unexpected exception while building the result (fallback)

_DECISIONS = (config.APPROVE, config.REJECT, config.NEEDS_CHECK)
_RETRYABLE_CODES = frozenset({LLM_ERROR})
_DETAIL_CHARS = 300

_ERROR_MESSAGES = {
    INPUT_NOT_FOUND: "The supplier PDF was not found. Check the file path and try again.",
    INPUT_INVALID: (
        "The supplier PDF path is not usable. Give the path of one PDF file "
        "(not an empty value or a folder)."
    ),
    INPUT_UNREADABLE: (
        "The supplier PDF exists but this program is not allowed to read it. "
        "Check the file permissions and try again."
    ),
    LLM_ERROR: (
        "The language model could not be reached or did not answer in time, so "
        "nothing was checked. Please try again."
    ),
    PURCHASE_REQUEST_UNUSABLE: "The purchase request file cannot be used.",
    UNEXPECTED: (
        "An unexpected problem stopped the check, so nothing was decided. Please "
        "try again, or ask the person who runs this system to look at it."
    ),
}
_DOCUMENT_FALLBACK = "The PDF could not be read."
_GATE2_FALLBACK = "The extracted values could not be validated after two attempts."
_MISMATCH_BASE = "The quotation could not be matched to the purchase request."


# ---- result types ---------------------------------------------------------------
@dataclass(frozen=True)
class Reason:
    """One thing a person should know. Quotes are exact; nothing is invented."""

    source: str                                # rule | match | extraction | document
    code: str                                  # "R5", ITEM_MISMATCH, GATE2_FAILED, ...
    message: str
    severity: str                              # "reject" | "review"
    evidence: tuple[tuple[str, str], ...] = ()  # (field name, exact quote)
    not_stated: tuple[str, ...] = ()           # fields the document does not state


@dataclass(frozen=True)
class MatchedRow:
    pr_number: str
    item: str
    description: str
    unit: str
    max_unit_price: Decimal
    requested_quantity: Decimal


@dataclass(frozen=True)
class MatchSummary:
    ok: bool
    row: MatchedRow | None = None
    problems: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ErrorInfo:
    code: str
    message: str      # fixed, application-written (plus the CSV detail for the operator)
    stage: str
    retryable: bool


@dataclass(frozen=True)
class CheckResult:
    status: str                       # completed | needs_review | error
    decision: str | None              # exact strings from src/config.py; None for an error
    reasons: tuple[Reason, ...] = ()
    notes: tuple[str, ...] = ()       # remarks that are not reasons, e.g. "VAT included"
    match: MatchSummary | None = None  # None = the purchase request match did not run
    attempts: int = 0                 # model calls made, as far as known (0 if an unexpected error lost the state)
    error: ErrorInfo | None = None


# ---- building blocks ------------------------------------------------------------
def _error(
    code: str, stage: str, attempts: int = 0, detail: object = None
) -> CheckResult:
    message = _ERROR_MESSAGES[code]
    if code == PURCHASE_REQUEST_UNUSABLE and isinstance(detail, str):
        cleaned = " ".join(detail.split())[:_DETAIL_CHARS]
        # compare.py includes raw OS exception text and paths for file failures.
        if cleaned.startswith("Purchase request file could not be read:"):
            cleaned = "Purchase request file could not be read."
        elif cleaned.startswith("Purchase request file not found:"):
            cleaned = "Purchase request file was not found."
        if cleaned:
            message = f"{message} {cleaned}"
    return CheckResult(
        status=STATUS_ERROR,
        decision=None,
        attempts=attempts,
        error=ErrorInfo(code, message, stage, code in _RETRYABLE_CODES),
    )


def _attempts(state: WorkflowState) -> int:
    value = state.get("attempts", 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _evidence(rule_id: str, extraction: Extraction | None):
    """Exact quotes for the fields behind one rule, plus the fields not stated."""
    if extraction is None:
        return (), ()
    quotes: list[tuple[str, str]] = []
    absent: list[str] = []
    for field in RULE_FIELDS.get(rule_id, ()):
        wrapped = getattr(extraction, field, None)
        if wrapped is None:
            continue
        if wrapped.value is None or not wrapped.value.strip():
            absent.append(field)           # null: no quote is ever made up
        elif wrapped.evidence and wrapped.evidence.strip():
            quotes.append((field, wrapped.evidence))   # exactly as extracted
    return tuple(quotes), tuple(absent)


def _rule_reason(
    finding: Finding, extraction: Extraction | None, missing_fields: tuple[str, ...]
) -> Reason:
    if finding.rule_id == "M":
        # M lists the missing fields (rule inputs); it never carries quotes. A field
        # that is written but unreadable is listed too: for M, "not_stated" means
        # "no usable value", not only "absent from the document".
        evidence: tuple[tuple[str, str], ...] = ()
        not_stated = tuple(missing_fields)
    else:
        evidence, not_stated = _evidence(finding.rule_id, extraction)
    return Reason(
        source=SOURCE_RULE,
        code=finding.rule_id,
        message=finding.message,
        severity=finding.severity,
        evidence=evidence,
        not_stated=not_stated,
    )


def _match_summary(match: MatchResult) -> MatchSummary:
    row = match.row
    matched = None
    if row is not None:
        matched = MatchedRow(
            pr_number=row.pr_number,
            item=row.item,
            description=row.description,
            unit=row.unit,
            max_unit_price=row.max_unit_price,
            requested_quantity=row.quantity,
        )
    return MatchSummary(
        ok=match.ok,
        row=matched,
        problems=tuple(match.problems),
        notes=tuple(match.notes),
    )


def _mismatch_reason(match: MatchResult) -> Reason:
    message = _MISMATCH_BASE
    if match.problems:
        message = f"{message} " + " ".join(match.problems)
    return Reason(SOURCE_MATCH, ITEM_MISMATCH, message, SEVERITY_REVIEW)


# ---- build_result: pure function from the final workflow state -------------------
def _completed(state: WorkflowState) -> CheckResult:
    decision = state["decision"]
    if decision not in _DECISIONS:
        raise ValueError("the workflow returned an unknown decision")
    match: MatchResult = state["match"]
    inputs = state.get("rule_inputs")
    missing = tuple(inputs.missing_fields) if inputs is not None else ()
    extraction = state.get("extraction")

    # Order is fixed: rules in evaluate() order, then the match.
    reasons = [_rule_reason(f, extraction, missing) for f in state.get("findings", [])]
    if not match.ok:
        reasons.append(_mismatch_reason(match))

    summary = _match_summary(match)
    return CheckResult(
        status=STATUS_COMPLETED,
        decision=decision,
        reasons=tuple(reasons),
        notes=summary.notes,
        match=summary,
        attempts=_attempts(state),
    )


def _gate2_review(state: WorkflowState) -> CheckResult:
    # state["failures"] holds the FINAL attempt's messages.
    messages = [str(m) for m in state.get("failures", ()) if str(m).strip()]
    if not messages:
        messages = [_GATE2_FALLBACK]
    reasons = tuple(
        Reason(SOURCE_EXTRACTION, GATE2_FAILED, message, SEVERITY_REVIEW)
        for message in messages
    )
    return CheckResult(
        status=STATUS_NEEDS_REVIEW,
        decision=config.NEEDS_CHECK,
        reasons=reasons,
        attempts=_attempts(state),
    )


def _document_review(error_text: object, attempts: int) -> CheckResult:
    # The text comes from src/pdf_reader.py, which writes it itself.
    message = (
        error_text.strip()
        if isinstance(error_text, str) and error_text.strip()
        else _DOCUMENT_FALLBACK
    )
    return CheckResult(
        status=STATUS_NEEDS_REVIEW,
        decision=config.NEEDS_CHECK,
        reasons=(Reason(SOURCE_DOCUMENT, DOCUMENT_UNREADABLE, message, SEVERITY_REVIEW),),
        attempts=attempts,
    )


def _from_error_state(state: WorkflowState) -> CheckResult:
    stage = state.get("stage")
    attempts = _attempts(state)
    if stage in (STAGE_READ_PDF, STAGE_GATE1):
        return _document_review(state.get("error"), attempts)
    if stage == STAGE_EXTRACT:
        # Never show state["error"]: it embeds the raw provider exception text.
        return _error(LLM_ERROR, STAGE_EXTRACT, attempts)
    if stage == STAGE_COMPARE:
        return _error(
            PURCHASE_REQUEST_UNUSABLE, STAGE_COMPARE, attempts, detail=state.get("error")
        )
    # An error state with a missing or unknown stage: say nothing about its text.
    return _error(UNEXPECTED, STAGE_WORKFLOW, attempts)


def build_result(state: WorkflowState) -> CheckResult:
    """Turn the final workflow state into one CheckResult. Pure: no I/O, no mutation."""
    status = state.get("status")
    if status == "completed":
        return _completed(state)
    if status == "needs_review":
        return _gate2_review(state)
    if status == "error":
        return _from_error_state(state)
    return _error(UNEXPECTED, STAGE_WORKFLOW, _attempts(state))


# ---- prechecks and the single entry point ---------------------------------------
def _pdf_path_problem(pdf_path: object) -> str | None:
    """Return an error code when the PDF path itself is unusable, else None.

    The file extension is NOT checked: whether the file is really a PDF is
    read_pdf's job, and a bad document is a review case, not an error.
    """
    if pdf_path is None:
        return INPUT_INVALID
    if isinstance(pdf_path, str) and not pdf_path.strip():
        return INPUT_INVALID
    try:
        path = Path(os.fspath(pdf_path))
        info = path.stat()
    except (TypeError, ValueError):
        return INPUT_INVALID
    except (FileNotFoundError, NotADirectoryError):
        return INPUT_NOT_FOUND
    except PermissionError:
        return INPUT_UNREADABLE
    except OSError as exc:
        if exc.errno in (errno.EINVAL, errno.ENAMETOOLONG):
            return INPUT_INVALID
        return INPUT_UNREADABLE
    if not stat.S_ISREG(info.st_mode):
        return INPUT_INVALID               # a folder, a device, ...
    if not os.access(path, os.R_OK):
        return INPUT_UNREADABLE
    try:
        with path.open("rb"):              # a real read test (os.access is weak on Windows)
            pass
    except OSError:
        return INPUT_UNREADABLE
    return None


def _csv_problem(csv_path: object) -> str | None:
    """Run the real CSV loader. Returns the app-written detail when it is unusable."""
    if csv_path is None or (isinstance(csv_path, str) and not csv_path.strip()):
        return "No purchase request file was given."
    try:
        load_purchase_requests(csv_path)
    except PurchaseRequestError as error:
        return str(error)
    return None


def check_supplier(pdf_path, csv_path) -> CheckResult:
    """The single entry point: prechecks, the fixed workflow, one CheckResult.

    Order is fixed: (1) PDF path prechecks, (2) the purchase request CSV is
    loaded for real, (3) only then the workflow runs. A broken CSV therefore
    never wastes a model call. The graph still loads the CSV itself later.

    Only Exception is caught, never BaseException. An unexpected exception
    becomes a clean UNEXPECTED error with a fixed message and a fallback stage.
    """
    phase = STAGE_INPUT
    try:
        code = _pdf_path_problem(pdf_path)
        if code is not None:
            return _error(code, STAGE_INPUT)

        phase = STAGE_PRECHECK
        detail = _csv_problem(csv_path)
        if detail is not None:
            return _error(PURCHASE_REQUEST_UNUSABLE, STAGE_PRECHECK, detail=detail)

        phase = STAGE_WORKFLOW
        state = run_workflow(pdf_path, csv_path)

        phase = STAGE_RESULT
        return build_result(state)
    except Exception:  # noqa: BLE001 - deliberate: nothing from the exception is shown
        return _error(UNEXPECTED, phase)
