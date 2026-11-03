"""Tests for src/pdf_reader.py. Written first: red until the module exists."""
import json
from pathlib import Path

import pymupdf
import pytest

from src.config import MIN_TEXT_CHARS
from src.pdf_reader import PdfResult, gate_text, normalise_text, read_pdf

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
EXPECTED = json.loads((ROOT / "expected.json").read_text(encoding="utf-8"))["PR-2026-114"]
PACKS = sorted(EXPECTED)  # "01" .. "10"
MIN_CHARS = int(MIN_TEXT_CHARS)  # config values may be Decimal


def pack_path(pack):
    return SAMPLES / f"supplier-pack-{pack}.pdf"


def quotation_no(pack):
    return f"QT-2026-{900 + int(pack)}"  # pack 01 -> QT-2026-901 ... pack 10 -> QT-2026-910


def make_pdf(path, pages):
    """Build a small PDF; each item in `pages` is the text of one page."""
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text, fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


# ---------- expected.json structure ----------

def test_expected_json_has_ten_packs_with_supplier_names():
    assert PACKS == [f"{n:02d}" for n in range(1, 11)]
    for pack in PACKS:
        assert isinstance(EXPECTED[pack]["supplier"], str)
        assert EXPECTED[pack]["supplier"]


# ---------- normalise_text ----------

def test_normalise_collapses_all_kinds_of_whitespace():
    assert normalise_text("a\nb\r\nc\td\u00a0e   f") == "a b c d e f"


def test_normalise_strips_both_ends_and_handles_empty():
    assert normalise_text("  \n hello \n ") == "hello"
    assert normalise_text("") == ""
    assert normalise_text(" \n\t\u00a0 ") == ""


def test_normalise_changes_nothing_but_whitespace():
    raw = "Unit price (BDT, VAT included):\n12,000 / 6.50 / 20% / 000170141-0101"
    assert normalise_text(raw) == "Unit price (BDT, VAT included): 12,000 / 6.50 / 20% / 000170141-0101"


def test_normalise_is_idempotent():
    once = normalise_text("a \n b\u00a0\u00a0c")
    assert normalise_text(once) == once


# ---------- gate_text (string only; None = pass, non-empty str = error) ----------

def test_gate_passes_at_exactly_min_chars():
    assert gate_text("a" * MIN_CHARS) is None


def test_gate_fails_one_char_below_min():
    error = gate_text("a" * (MIN_CHARS - 1))
    assert isinstance(error, str) and error


def test_gate_fails_on_empty_string():
    error = gate_text("")
    assert isinstance(error, str) and error


def test_gate_fails_on_min_chars_spaces():
    # Gate 1 must measure the NORMALISED text, so spaces do not count as content.
    error = gate_text(" " * MIN_CHARS)
    assert isinstance(error, str) and error


def test_gate_fails_when_padding_whitespace_hides_short_text():
    error = gate_text("short" + " \n " * MIN_CHARS)
    assert isinstance(error, str) and error


# ---------- read_pdf: success on the 10 real PDFs ----------

@pytest.mark.parametrize("pack", PACKS)
def test_read_real_pdf_success_shape(pack):
    result = read_pdf(pack_path(pack))
    assert isinstance(result, PdfResult)
    assert result.ok is True
    assert result.error is None
    assert result.page_count == 1
    assert result.text


@pytest.mark.parametrize("pack", PACKS)
def test_real_pdf_has_supplier_name_and_quotation_number(pack):
    text = read_pdf(pack_path(pack)).text
    assert EXPECTED[pack]["supplier"] in text      # name from expected.json
    assert quotation_no(pack) in text              # checked separately


def test_pack_01_exact_phrases():
    text = read_pdf(pack_path("01")).text
    assert "Padma Textile Mills Ltd" in text
    assert "QT-2026-901" in text


@pytest.mark.parametrize("pack", PACKS)
def test_real_pdf_text_has_no_stray_whitespace(pack):
    text = read_pdf(pack_path(pack)).text
    for bad in ("\n", "\r", "\t", "\u00a0", "  "):
        assert bad not in text, f"pack {pack} contains {bad!r}"
    assert text == text.strip()


@pytest.mark.parametrize("pack", PACKS)
def test_real_pdf_text_keeps_pymupdf_order(pack):
    """Independent reference: plain PyMuPDF extraction, joined and normalised."""
    with pymupdf.open(str(pack_path(pack))) as doc:
        reference = normalise_text(" ".join(page.get_text() for page in doc))
    assert read_pdf(pack_path(pack)).text == reference


@pytest.mark.parametrize("pack", PACKS)
def test_real_pdfs_pass_gate_1(pack):
    assert gate_text(read_pdf(pack_path(pack)).text) is None


def test_read_accepts_str_path_too():
    assert read_pdf(str(pack_path("01"))).ok is True


# ---------- read_pdf: generated PDFs ----------

def test_two_page_pdf_counts_pages_and_keeps_order(tmp_path):
    path = make_pdf(tmp_path / "two.pdf", ["First page text", "Second page text"])
    result = read_pdf(path)
    assert result.ok is True
    assert result.page_count == 2
    assert result.text.index("First page text") < result.text.index("Second page text")


def test_blank_pdf_is_a_read_failure(tmp_path):
    result = read_pdf(make_pdf(tmp_path / "blank.pdf", [""]))
    assert result.ok is False
    assert result.text == ""
    assert result.page_count == 1          # the file opened
    assert result.error


def test_whitespace_only_pdf_is_a_read_failure(tmp_path):
    result = read_pdf(make_pdf(tmp_path / "spaces.pdf", ["          "]))
    assert result.ok is False
    assert result.text == ""
    assert result.page_count == 1
    assert result.error


def test_short_but_non_empty_pdf_passes_read_and_fails_gate_1(tmp_path):
    result = read_pdf(make_pdf(tmp_path / "short.pdf", ["Short quote only"]))
    assert result.ok is True
    assert result.error is None
    assert 0 < len(result.text) < MIN_CHARS
    error = gate_text(result.text)         # Gate 1 returns the error message
    assert isinstance(error, str) and error


def test_password_protected_pdf_is_a_clear_read_failure(tmp_path):
    path = tmp_path / "locked.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Secret quotation text", fontsize=11)
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256,
             user_pw="open-sesame", owner_pw="owner-pw")
    doc.close()

    # prove the file really needs a password (it must NOT open freely)
    with pymupdf.open(str(path)) as check:
        assert check.needs_pass
        assert check.authenticate("") == 0   # an empty password does not unlock it

    result = read_pdf(path)
    assert result.ok is False
    assert result.text == ""
    assert result.page_count == 0          # cannot be counted before authentication
    assert "password" in result.error.lower()


# ---------- read_pdf: error handling ----------

def test_missing_file_returns_error_not_exception(tmp_path):
    result = read_pdf(tmp_path / "nope.pdf")
    assert result.ok is False
    assert result.text == ""
    assert result.page_count == 0
    assert result.error


def test_file_that_is_not_a_pdf_returns_error(tmp_path):
    path = tmp_path / "fake.pdf"
    path.write_bytes(b"this is not a pdf")
    result = read_pdf(path)
    assert result.ok is False
    assert result.page_count == 0
    assert result.error


def test_only_exception_is_caught_not_base_exception(monkeypatch):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(pymupdf, "open", interrupted)
    with pytest.raises(KeyboardInterrupt):
        read_pdf(pack_path("01"))
