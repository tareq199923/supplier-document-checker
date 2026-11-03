"""Read one PDF into clean text, and run Gate 1 on that text.

Three small, separate pieces:
  normalise_text(text)  - collapse all whitespace; change nothing else.
  read_pdf(path)        - open the file with PyMuPDF and return a PdfResult.
  gate_text(text)       - Gate 1: is there enough text to continue?

Plain code only: no LLM, no API. PyMuPDF's own page and text order is kept;
tables are not reordered or rebuilt.
"""
from dataclasses import dataclass

import pymupdf

from src.config import MIN_TEXT_CHARS


@dataclass(frozen=True)
class PdfResult:
    ok: bool
    text: str          # normalised text; "" on failure
    page_count: int    # real count if the file opened, else 0
    error: str | None  # short plain message; None on success


def normalise_text(text: str) -> str:
    """Collapse every run of whitespace (newline, tab, NBSP, spaces) to one space.

    Punctuation and all other characters are left exactly as they are. The same
    function is used on the PDF text and on every evidence string later, so a
    substring check compares like with like.
    """
    return " ".join(text.split())


def _failure(page_count: int, error: str) -> PdfResult:
    return PdfResult(ok=False, text="", page_count=page_count, error=error)


def read_pdf(path) -> PdfResult:
    """Open a PDF and return its normalised text.

    "No text" means the normalised text is empty (blank or whitespace-only PDF):
    that is a read failure. Short-but-non-empty text still reads fine; the length
    limit belongs to gate_text.
    """
    try:
        doc = pymupdf.open(str(path))
    except Exception as exc:  # only around opening the file
        return _failure(0, f"Could not open the PDF ({type(exc).__name__}).")

    try:
        if doc.needs_pass:
            return _failure(0, "The PDF is password-protected; a password is required to read it.")
        page_count = doc.page_count
        try:
            raw = " ".join(page.get_text() for page in doc)  # PyMuPDF order kept
        except Exception as exc:  # only around reading the pages
            return _failure(page_count, f"Could not read the PDF text ({type(exc).__name__}).")
    finally:
        doc.close()

    text = normalise_text(raw)
    if not text:
        return _failure(page_count, "The PDF has no readable text (it may be blank or scanned).")
    return PdfResult(ok=True, text=text, page_count=page_count, error=None)


def gate_text(text: str) -> str | None:
    """Gate 1. Returns None if the text passes, or a short error message.

    Works on a string only. The length is measured on the NORMALISED text, so
    spaces and line breaks do not count as content. Exactly MIN_TEXT_CHARS passes.
    """
    length = len(normalise_text(text))
    if length < MIN_TEXT_CHARS:
        return (
            f"The PDF text is too short ({length} characters; "
            f"at least {int(MIN_TEXT_CHARS)} needed). It may be scanned or unreadable."
        )
    return None
