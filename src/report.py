"""F009: a plain-text decision report built from ONE CheckResult.

PRESENTATION ONLY. This module never decides anything, never calls a model,
never recalculates a number and never reads a PDF (AGENTS.md rule 9: report text
comes from templates and verified values). Rule messages already exist in
Reason.message; the report adds layout, labels and the fixed next-step wording.

Plain Python, standard library only (besides the CheckResult vocabulary).

Free text in a result can come from the untrusted supplier PDF: evidence quotes,
Gate 2 failure messages, match problems, the supplier name. EVERY free-text value
goes through clean_text() before it is printed, and quotes are always printed inside
quote marks on an indented, labelled line, so supplier text can never start a line,
create a heading or fake a "Decision:" line.

Limits of the cleaner (see NOTE.md): it keeps every visible character (no Unicode
normalisation), so look-alike letters and homoglyphs are NOT detected, and it does
not judge what a quote means. It removes layout tricks, not lies.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
import unicodedata
from decimal import Decimal
from pathlib import Path

from src import config
from src.result import (
    DOCUMENT_UNREADABLE,
    GATE2_FAILED,
    INPUT_INVALID,
    INPUT_NOT_FOUND,
    INPUT_UNREADABLE,
    ITEM_MISMATCH,
    LLM_ERROR,
    PURCHASE_REQUEST_UNUSABLE,
    SEVERITY_REJECT,
    STATUS_ERROR,
    UNEXPECTED,
    CheckResult,
    Reason,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = PROJECT_ROOT / "reports" / "decisions"   # NOT reports/ itself: it holds raw PDF text

MAX_TEXT_CHARS = 240
MAX_STEM_CHARS = 60

# =============================== FIXED WORDING (review here) =========================
# Everything a reader sees that is not a value from the result lives in this block.
TITLE = "SUPPLIER QUOTATION CHECK"
HEADING_WHY = "WHY"
HEADING_MATCH = "PURCHASE REQUEST MATCH"
HEADING_NEXT = "WHAT A PERSON SHOULD CHECK"
HEADING_RUN = "RUN DETAILS"
HEADING_ERROR = "WHAT HAPPENED"

NOT_AVAILABLE = "not available"
NO_DECISION_LINE = "Decision: No decision was made."
SHORTENED = " [shortened]"
NO_QUOTE_TEXT = "No quote available."
NOT_STATED_PREFIX = "Not stated in the document: "
NO_PROBLEM_TEXT = "No rule found a problem with this quotation."
NO_REASONS_TEXT = "No reasons were recorded."
NO_FOLLOW_UP_TEXT = "Nothing further is required by these rules."
MATCH_OK_TEXT = "The quotation matched a row of the purchase request."
MATCH_FAILED_TEXT = "The quotation could not be matched to a row of the purchase request."
MODEL_CALLS_LABEL = "Model calls recorded: "
MADE_BY_LINE = "Made by: program rules; the language model only extracted quoted values."
RETRY_MAY_HELP = "Trying again may work."
RETRY_WONT_HELP = "Trying again will not help until the problem above is fixed."

SEVERITY_LABELS = {"reject": "Reject", "review": "Review"}
FALLBACK_SEVERITY_LABEL = "Review"

FIELD_LABELS: dict[str, str] = {
    "supplier_name": "Supplier name",
    "quotation_no": "Quotation number",
    "quotation_date": "Quotation date",
    "pr_reference": "Purchase request reference",
    "item_code": "Item code",
    "description": "Item description",
    "quantity": "Quantity",
    "unit": "Unit",
    "unit_price": "Unit price",
    "price_includes_vat": "Price includes VAT",
    "line_amount": "Line amount",
    "subtotal": "Subtotal",
    "vat_rate": "VAT rate",
    "vat_amount": "VAT amount",
    "total": "Total",
    "tin": "TIN",
    "bin": "BIN",
    "advance_pct": "Advance payment",
    "balance_days_min": "Balance payment days (minimum)",
    "balance_days_max": "Balance payment days (maximum)",
    "delivery_value": "Delivery time",
    "delivery_unit": "Delivery time unit",
    "penalty_pct": "Late-delivery penalty rate",
    "penalty_period": "Late-delivery penalty period",
    "penalty_basis": "Late-delivery penalty basis",
    "validity_days": "Quotation validity (days)",
}

# One dictionary for reason codes AND error codes. Plain, neutral wording; no business
# process is invented. An error report must never contain a decision string, so the
# error entries avoid those words.
NEXT_STEPS: dict[str, str] = {
    "R1": "Compare the quoted unit price with the maximum unit price on the purchase request.",
    "R2": "Check the advance payment the quotation asks for against the company limit for advance payments.",
    "R3": "Check the quotation for a late-delivery penalty clause. The program did not find a stated penalty.",
    "R4": "Check whether the quotation states both the supplier's TIN and BIN.",
    "R5": "Check the delivery time stated in the quotation against the company delivery limit.",
    "H1": "Compare the quoted unit price with the maximum unit price on the purchase request.",
    "H2": "Compare the quoted quantity with the quantity on the purchase request.",
    "H3": "Check the balance payment terms in the quotation against the company payment terms.",
    "H4": "Check the delivery time stated in the quotation.",
    "H5": "Check how VAT is shown in the quotation: the rate and the amount.",
    "H6": "Check the amounts in the quotation (line amount, subtotal, VAT and total). The program could not reconcile them.",
    "H7": "Check how long the quotation stays valid.",
    "N1": "Check the late-delivery penalty rate and period in the quotation against the company minimum.",
    "N2": "Check what the late-delivery penalty is based on in the quotation.",
    "M": "Check the supplier document for the fields listed as not stated. The program could not use a value for them.",
    ITEM_MISMATCH: "Check that the item in the quotation is the item on the purchase request: purchase request number, item letter, description and unit.",
    GATE2_FAILED: "The extracted values could not be confirmed against the text of the document. Read the supplier document yourself.",
    DOCUMENT_UNREADABLE: "Open the supplier document and check whether it can be read. The program could not read its text.",
    INPUT_NOT_FOUND: "Check that the supplier PDF exists at the path given, then run the check again.",
    INPUT_INVALID: "Give the path of one PDF file, then run the check again.",
    INPUT_UNREADABLE: "Check the permissions on the supplier PDF, then run the check again.",
    LLM_ERROR: "Run the check again. If it keeps failing, check the language model settings and the network connection.",
    PURCHASE_REQUEST_UNUSABLE: "Check the purchase request file named in the message above, then run the check again.",
    UNEXPECTED: "Run the check again. If it fails again, ask the person who runs this system to look at it.",
}
GENERIC_NEXT_STEP = "Read the reason above and check the supplier document."
GENERIC_ERROR_NEXT_STEP = (
    "Run the check again. If it fails again, ask the person who runs this system to look at it."
)
# =====================================================================================

# ---- untrusted-text cleaner -------------------------------------------------------
# Characters that end a line (or look like they do) become one space.
_LINE_BREAKS = dict.fromkeys(
    map(ord, "\n\r\t\v\f\x1c\x1d\x1e\x85\u2028\u2029"), " "
)
_REMOVED_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})   # Cs: a lone surrogate cannot be written as UTF-8
_JOINERS = "\u200c\u200d"                             # ZWNJ, ZWJ: real parts of Bengali conjuncts


def _is_bengali(ch: str) -> bool:
    return "\u0980" <= ch <= "\u09ff"


def _drop_stray_joiners(text: str) -> str:
    """Keep a ZWJ/ZWNJ only when both neighbours are Bengali-block characters."""
    if not any(j in text for j in _JOINERS):
        return text
    kept = []
    last = len(text) - 1
    for i, ch in enumerate(text):
        if ch in _JOINERS:
            if 0 < i < last and _is_bengali(text[i - 1]) and _is_bengali(text[i + 1]):
                kept.append(ch)
            continue
        kept.append(ch)
    return "".join(kept)


def clean_text(value: object) -> str:
    """Make untrusted text safe for one report line. Never changes a visible character.

    1. Line breaks and separators (\\n \\r \\t, U+2028, U+2029, ...) become a space.
    2. Every control (Cc) and format (Cf) character is removed: zero-width characters,
       bidi overrides and isolates, BOM, soft hyphen. Exception: ZWJ/ZWNJ between two
       Bengali-block characters stay, because they form real conjuncts.
    3. Runs of whitespace collapse to one space.
    No NFKC or any other normalisation: quotes stay faithful.
    """
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = text.translate(_LINE_BREAKS)
    text = "".join(
        ch for ch in text
        if ch in _JOINERS or unicodedata.category(ch) not in _REMOVED_CATEGORIES
    )
    text = _drop_stray_joiners(text)
    return " ".join(text.split())


def _shorten(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_TEXT_CHARS:
        return text, False
    cut = text[:MAX_TEXT_CHARS].rstrip()
    while cut and cut[-1] in _JOINERS:                # a cut must not leave a dangling joiner
        cut = cut[:-1].rstrip()
    return cut, True


def _free(value: object) -> str:
    """Cleaned free text, capped; the marker follows the text."""
    text, shortened = _shorten(clean_text(value))
    return text + (SHORTENED if shortened else "")


def _quoted(value: object) -> str:
    """A quote inside quote marks; the marker stays OUTSIDE the marks."""
    text, shortened = _shorten(clean_text(value))
    return f'"{text}"' + (SHORTENED if shortened else "")


def _label(field: object) -> str:
    if isinstance(field, str) and field in FIELD_LABELS:
        return FIELD_LABELS[field]
    return _free(field)


def _number(value: object) -> str:
    return format(value, "f") if isinstance(value, Decimal) else _free(value)


def _as_text(path_like: object) -> str:
    if isinstance(path_like, os.PathLike):
        path_like = os.fspath(path_like)
    if isinstance(path_like, bytes):
        return os.fsdecode(path_like)
    return path_like if isinstance(path_like, str) else str(path_like)


def _base_name(path_like: object) -> str:
    """The file NAME only. Handles both separators on any platform."""
    return os.path.basename(clean_text(_as_text(path_like)).replace("\\", "/"))


# ---- report sections ---------------------------------------------------------------
def _ordered(reasons) -> list[Reason]:
    """Reject-severity first, then everything else. sorted() is stable."""
    return sorted(reasons, key=lambda r: 0 if r.severity == SEVERITY_REJECT else 1)


def _reason_lines(number: int, reason: Reason) -> list[str]:
    severity = SEVERITY_LABELS.get(reason.severity, FALLBACK_SEVERITY_LABEL)
    lines = [
        f"{number}. [{_free(reason.code)}] {severity} - {_free(reason.message)}",
        "   Evidence:",
    ]
    if reason.evidence:
        for field, quote in reason.evidence:
            lines.append(f"     {_label(field)}: {_quoted(quote)}")
    else:
        lines.append(f"     {NO_QUOTE_TEXT}")            # never an invented quote
    if reason.not_stated:
        lines.append("   " + NOT_STATED_PREFIX + ", ".join(_label(f) for f in reason.not_stated))
    return lines


def _bullets(title: str, items) -> list[str]:
    items = list(items)
    if not items:
        return [f"{title}: none"]
    return [f"{title}:"] + [f"  - {_free(item)}" for item in items]


def _why_lines(result: CheckResult, reasons: list[Reason]) -> list[str]:
    lines = ["", HEADING_WHY]
    if not reasons:
        lines.append(NO_PROBLEM_TEXT if result.decision == config.APPROVE else NO_REASONS_TEXT)
        return lines
    for number, reason in enumerate(reasons, 1):
        lines += _reason_lines(number, reason)
    return lines


def _match_lines(result: CheckResult) -> list[str]:
    match = result.match
    lines = ["", HEADING_MATCH, "Result: " + (MATCH_OK_TEXT if match.ok else MATCH_FAILED_TEXT)]
    row = match.row
    if row is not None:
        lines += [
            f"Matched row: {_free(row.pr_number)}, item {_free(row.item)}",
            f"Description: {_quoted(row.description)}",
            f"Unit: {_free(row.unit)}",
            f"Maximum unit price (BDT): {_number(row.max_unit_price)}",
            f"Requested quantity: {_number(row.requested_quantity)}",
        ]
    lines += _bullets("Problems", match.problems)
    notes = list(match.notes) + [n for n in result.notes if n not in match.notes]
    lines += _bullets("Notes", notes)
    return lines


def _error_lines(result: CheckResult) -> tuple[list[str], str]:
    error = result.error
    code = error.code if error is not None else "UNKNOWN"
    message = error.message if error is not None else ""
    stage = error.stage if error is not None else ""
    retryable = bool(error.retryable) if error is not None else False
    lines = [
        "",
        HEADING_ERROR,
        f"Error code: {_free(code)}",
        f"Message: {_free(message)}",
        f"Stage: {_free(stage)}",
        "Retry: " + (RETRY_MAY_HELP if retryable else RETRY_WONT_HELP),
    ]
    return lines, code if isinstance(code, str) else ""


def _next_step_lines(result: CheckResult, reasons: list[Reason], error_code: str | None) -> list[str]:
    lines = ["", HEADING_NEXT]
    if error_code is not None:
        lines.append("- " + NEXT_STEPS.get(error_code, GENERIC_ERROR_NEXT_STEP))
        return lines
    if not reasons:
        lines.append(
            NO_FOLLOW_UP_TEXT if result.decision == config.APPROVE else GENERIC_NEXT_STEP
        )
        return lines
    seen: set[str] = set()
    for reason in reasons:
        code = reason.code if isinstance(reason.code, str) else ""
        if code in seen:
            continue                                  # one next step per code
        seen.add(code)
        lines.append(f"- [{_free(code)}] {NEXT_STEPS.get(code, GENERIC_NEXT_STEP)}")
    return lines


def _run_lines(result: CheckResult) -> list[str]:
    lines = ["", HEADING_RUN]
    # After an unexpected exception the workflow state is lost, so the count would be a
    # guess: leave the line out rather than show 0 as if it proved "no model call".
    if not (result.error is not None and result.error.code == UNEXPECTED):
        lines.append(MODEL_CALLS_LABEL + _free(result.attempts))
    lines.append(MADE_BY_LINE)
    return lines


def render_report(
    result: CheckResult,
    source_name: str,
    supplier_name: str | None = None,
    generated_at: str | None = None,
) -> str:
    """Pure and deterministic: the same input gives the same text. No I/O, no mutation."""
    no_decision = result.status == STATUS_ERROR or result.decision is None
    chosen = supplier_name if supplier_name is not None else getattr(result, "supplier_name", None)
    match = result.match
    row = match.row if match is not None else None

    lines = [
        TITLE,
        f"Source file: {_free(_base_name(source_name)) or NOT_AVAILABLE}",
        f"Supplier: {_free(chosen) or NOT_AVAILABLE}",
    ]
    if row is not None:
        lines.append(f"Purchase request: {_free(row.pr_number)}, item {_free(row.item)}")
    stamp = _free(generated_at) if generated_at is not None else ""
    if stamp:
        lines.append(f"Generated at: {stamp}")
    lines += ["", NO_DECISION_LINE if no_decision else f"Decision: {_free(result.decision)}"]

    reasons: list[Reason] = []
    error_code: str | None = None
    if no_decision:
        body, error_code = _error_lines(result)
        lines += body
    else:
        reasons = _ordered(result.reasons)
        lines += _why_lines(result, reasons)
        if match is not None:
            lines += _match_lines(result)
    lines += _next_step_lines(result, reasons, error_code)
    lines += _run_lines(result)
    return "\n".join(lines) + "\n"


# ---- writing -----------------------------------------------------------------------
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def _safe_stem(source_path: object) -> str:
    name = os.path.basename(_as_text(source_path).replace("\\", "/"))
    stem = os.path.splitext(name)[0].strip(". ")
    stem = _UNSAFE_CHARS.sub("_", stem)[:MAX_STEM_CHARS].strip(".")
    if not stem:
        stem = "report"
    if stem.split(".")[0].upper() in _WINDOWS_RESERVED:   # "con" and also "con.v2"
        stem = "_" + stem
    return stem


def _path_digest(source_path: object) -> str:
    text = _as_text(source_path)
    try:
        text = os.path.abspath(text)
    except (ValueError, OSError):
        pass                                              # keep the raw text; still deterministic
    text = os.path.normcase(text)
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:8]


def report_filename(source_path: object) -> str:
    """<safe stem>-<8 hex of sha256(normcase(abspath))>.decision.txt. No file I/O.

    The digest keeps two different files with the same name in different folders
    from sharing (and silently overwriting) one report. The same source path always
    gives the same name, so the latest check of that file replaces the earlier one.
    """
    return f"{_safe_stem(source_path)}-{_path_digest(source_path)}.decision.txt"


def write_report(result: CheckResult, source_path: object, out_dir: object = REPORT_DIR) -> Path:
    """Render and write one report atomically (temp file in the same folder, then replace)."""
    text = render_report(result, _as_text(source_path))
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / report_filename(source_path)
    handle, temp_name = tempfile.mkstemp(dir=folder, prefix=".report-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temp_name, target)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return target
