"""Tests for the purchase request comparison (F005). No LLM, no API key.

Written BEFORE src/compare.py. Do not edit these tests to make the code pass.

Rules under test (see AGENTS.md hard rule 10 and the F005 plan):
- the CSV is read with utf-8-sig and rows are keyed by PR number + item letter;
- a quote matches only when PR number, item letter, description AND unit agree
  (case and extra spaces are ignored, nothing is fuzzy);
- a failed match gives the rules NO price and NO quantity, never the wrong row's;
- "VAT included" adds a note, not a problem and not a rule finding.
"""
import csv
import json
from dataclasses import FrozenInstanceError
from decimal import Decimal as D
from pathlib import Path

import pytest

from src.compare import (
    MatchResult,
    PurchaseRequestError,
    PurchaseRequestRow,
    load_purchase_requests,
    match_item,
)
from src.schema import Evidenced, Extraction

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_CSV = ROOT / "samples" / "purchase-request.csv"
GOLD = json.loads((ROOT / "tests" / "gold_extractions.json").read_text(encoding="utf-8"))

HEADER = [
    "PR number",
    "Item",
    "Description",
    "Unit",
    "Quantity requested",
    "Maximum unit price (BDT)",
]


# ---- helpers ----------------------------------------------------------------------
def write_csv(path, rows, header=HEADER, encoding="utf-8-sig"):
    with open(path, "w", encoding=encoding, newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def gold(pack, **overrides):
    """The approved extraction for one sample pack, with optional field overrides."""
    fields = {
        name: Evidenced(value=pair[0], evidence=pair[1])
        for name, pair in GOLD[pack].items()
    }
    for name, value in overrides.items():
        fields[name] = Evidenced(
            value=value, evidence=None if value is None else "placeholder quote"
        )
    return Extraction(**fields)


@pytest.fixture(scope="module")
def table():
    return load_purchase_requests(SAMPLE_CSV)


def mentions(result, text):
    return any(text in problem for problem in result.problems)


# ---- loader: the real sample file ------------------------------------------------------
def test_loader_reads_the_five_real_rows(table):
    assert sorted(table) == [("PR-2026-114", letter) for letter in "ABCDE"]


def test_loader_row_values_are_exact_and_decimal(table):
    row = table[("PR-2026-114", "A")]
    assert isinstance(row, PurchaseRequestRow)
    assert row.pr_number == "PR-2026-114"
    assert row.item == "A"
    assert row.description == "Cotton twill fabric, 280 gsm"
    assert row.unit == "metre"
    assert row.quantity == D("12000") and isinstance(row.quantity, D)
    assert row.max_unit_price == D("480.00") and isinstance(row.max_unit_price, D)


def test_loader_keeps_a_comma_inside_a_quoted_description(table):
    assert table[("PR-2026-114", "D")].description == "Poly bag, 30 x 40 cm"


def test_loader_accepts_a_path_given_as_text():
    assert len(load_purchase_requests(str(SAMPLE_CSV))) == 5


# ---- loader: other files ---------------------------------------------------------------
GOOD_ROW = ["PR-9", "A", "Widget", "piece", "10", "5.00"]


def test_loader_reads_a_file_with_a_bom_and_a_file_without_one(tmp_path):
    with_bom = write_csv(tmp_path / "bom.csv", [GOOD_ROW], encoding="utf-8-sig")
    without_bom = write_csv(tmp_path / "plain.csv", [GOOD_ROW], encoding="utf-8")
    assert ("PR-9", "A") in load_purchase_requests(with_bom)
    assert ("PR-9", "A") in load_purchase_requests(without_bom)


def test_loader_normalises_spaces_and_case_in_the_key(tmp_path):
    path = write_csv(
        tmp_path / "keys.csv",
        [["  pr-9 ", " a ", "Widget", "piece", "10", "5.00"]],
    )
    assert ("PR-9", "A") in load_purchase_requests(path)


def test_loader_accepts_a_properly_grouped_number(tmp_path):
    path = write_csv(
        tmp_path / "grouped.csv",
        [["PR-9", "A", "Widget", "piece", "12,000", "1,250.50"]],
    )
    row = load_purchase_requests(path)[("PR-9", "A")]
    assert row.quantity == D("12000") and row.max_unit_price == D("1250.50")


def test_loader_missing_file_is_a_clean_error(tmp_path):
    with pytest.raises(PurchaseRequestError):
        load_purchase_requests(tmp_path / "does-not-exist.csv")


def test_loader_missing_column_is_a_clean_error(tmp_path):
    path = write_csv(
        tmp_path / "short.csv",
        [["PR-9", "A", "Widget", "piece", "10"]],
        header=HEADER[:-1],
    )
    with pytest.raises(PurchaseRequestError):
        load_purchase_requests(path)


def test_loader_header_only_file_is_a_clean_error(tmp_path):
    with pytest.raises(PurchaseRequestError):
        load_purchase_requests(write_csv(tmp_path / "empty.csv", []))


@pytest.mark.parametrize(
    "quantity, price",
    [
        ("abc", "5.00"),        # not a number
        ("12,5", "5.00"),       # could be a decimal comma, so never guessed
        ("-10", "5.00"),        # negative
        ("0", "5.00"),          # zero quantity makes no sense
        ("10", "0.00"),         # zero price makes no sense
        ("10", ""),             # blank
        ("", "5.00"),           # blank
    ],
)
def test_loader_bad_number_is_a_clean_error(tmp_path, quantity, price):
    path = write_csv(
        tmp_path / "bad.csv",
        [["PR-9", "A", "Widget", "piece", quantity, price]],
    )
    with pytest.raises(PurchaseRequestError):
        load_purchase_requests(path)


@pytest.mark.parametrize(
    "row",
    [
        ["", "A", "Widget", "piece", "10", "5.00"],      # no PR number
        ["PR-9", "", "Widget", "piece", "10", "5.00"],   # no item letter
        ["PR-9", "A", "", "piece", "10", "5.00"],        # no description
        ["PR-9", "A", "Widget", "", "10", "5.00"],       # no unit
    ],
)
def test_loader_blank_text_field_is_a_clean_error(tmp_path, row):
    with pytest.raises(PurchaseRequestError):
        load_purchase_requests(write_csv(tmp_path / "blank.csv", [row]))


def test_loader_duplicate_key_is_a_clean_error(tmp_path):
    path = write_csv(tmp_path / "dup.csv", [GOOD_ROW, GOOD_ROW])
    with pytest.raises(PurchaseRequestError):
        load_purchase_requests(path)


# ---- matcher: all ten approved packs -----------------------------------------------------
# Item, maximum unit price and requested quantity, typed by hand. They are the same
# numbers tests/test_rules.py uses for each pack, so the two sources cannot drift apart.
PACK_EXPECTED = {
    "01": ("A", "480", "12000"),
    "02": ("B", "38", "20000"),
    "03": ("C", "210", "800"),
    "04": ("D", "6.50", "50000"),
    "05": ("E", "95", "3000"),
    "06": ("A", "480", "12000"),
    "07": ("B", "38", "20000"),
    "08": ("C", "210", "800"),
    "09": ("D", "6.50", "50000"),
    "10": ("E", "95", "3000"),
}


def test_the_expected_table_covers_all_ten_packs():
    assert sorted(PACK_EXPECTED) == sorted(GOLD) == [f"{n:02d}" for n in range(1, 11)]


@pytest.mark.parametrize("pack", sorted(PACK_EXPECTED))
def test_every_approved_pack_matches_its_csv_row(table, pack):
    item, max_price, requested = PACK_EXPECTED[pack]
    result = match_item(gold(pack), table)
    assert result.ok is True
    assert result.problems == ()
    assert result.row.item == item
    assert result.max_price == D(max_price)
    assert result.requested_qty == D(requested)


def test_only_pack_10_carries_the_vat_note(table):
    for pack in sorted(PACK_EXPECTED):
        result = match_item(gold(pack), table)
        if pack == "10":
            assert len(result.notes) == 1
            assert "VAT" in result.notes[0]
            assert "not be comparable" in result.notes[0]
        else:
            assert result.notes == ()


def test_vat_note_is_not_a_problem(table):
    result = match_item(gold("10"), table)
    assert result.ok is True and result.problems == ()


def test_price_includes_vat_false_or_missing_adds_no_note(table):
    assert match_item(gold("01", price_includes_vat="false"), table).notes == ()
    assert match_item(gold("01", price_includes_vat=None), table).notes == ()


# ---- matcher: harmless differences are accepted -----------------------------------------
@pytest.mark.parametrize(
    "field, value",
    [
        ("pr_reference", "  pr-2026-114  "),
        ("pr_reference", "Pr-2026-114"),
        ("item_code", " a "),
        ("item_code", "a"),
        ("description", "cotton   twill fabric, 280 GSM"),
        ("description", "  Cotton twill fabric, 280 gsm "),
        ("unit", "Metre"),
        ("unit", " metre "),
    ],
)
def test_case_and_extra_spaces_do_not_matter(table, field, value):
    result = match_item(gold("01", **{field: value}), table)
    assert result.ok is True
    assert result.max_price == D("480.00")


# ---- matcher: anything else is a problem, and the rules get no price or quantity ---------
def assert_failed(result):
    assert result.ok is False
    assert result.row is None
    assert result.max_price is None
    assert result.requested_qty is None
    assert len(result.problems) >= 1


def test_unknown_pr_number(table):
    result = match_item(gold("01", pr_reference="PR-2026-999"), table)
    assert_failed(result)
    assert mentions(result, "PR-2026-999") and mentions(result, "not found")


@pytest.mark.parametrize("item", ["F", "AB", "1"])
def test_unknown_item_letter(table, item):
    result = match_item(gold("01", item_code=item), table)
    assert_failed(result)
    assert mentions(result, "not found")


def test_right_item_but_for_a_different_pr_is_not_found(tmp_path):
    path = write_csv(tmp_path / "other.csv", [GOOD_ROW])
    other = load_purchase_requests(path)
    assert_failed(match_item(gold("01"), other))


def test_description_must_be_the_same_words(table):
    result = match_item(gold("01", description="Cotton twill fabric, 300 gsm"), table)
    assert_failed(result)
    assert mentions(result, "description")


def test_description_is_never_fuzzy(table):
    # A missing comma is a different string. A person decides, not the code.
    result = match_item(gold("01", description="Cotton twill fabric 280 gsm"), table)
    assert_failed(result)
    assert mentions(result, "description")


def test_description_of_another_item_is_not_accepted(table):
    result = match_item(gold("01", description="Metal zipper, 18 cm"), table)
    assert_failed(result)
    assert mentions(result, "description")


def test_unit_must_agree(table):
    result = match_item(gold("02", unit="dozen"), table)
    assert_failed(result)
    assert mentions(result, "unit")


@pytest.mark.parametrize(
    "field", ["pr_reference", "item_code", "description", "unit"]
)
def test_a_missing_identifying_field_is_a_problem(table, field):
    result = match_item(gold("01", **{field: None}), table)
    assert_failed(result)
    assert mentions(result, field)


def test_missing_pr_and_item_report_both_problems(table):
    result = match_item(gold("01", pr_reference=None, item_code=None), table)
    assert_failed(result)
    assert mentions(result, "pr_reference") and mentions(result, "item_code")


def test_missing_description_and_unit_report_both_problems(table):
    result = match_item(gold("01", description=None, unit=None), table)
    assert_failed(result)
    assert mentions(result, "description") and mentions(result, "unit")


def test_a_wrong_description_and_a_wrong_unit_report_both_problems(table):
    result = match_item(gold("01", description="Something else", unit="dozen"), table)
    assert_failed(result)
    assert mentions(result, "description") and mentions(result, "unit")


def test_an_empty_extraction_never_raises(table):
    result = match_item(Extraction(), table)
    assert_failed(result)
    assert len(result.problems) >= 2


def test_matching_against_an_empty_table_is_a_problem_not_a_crash():
    assert_failed(match_item(gold("01"), {}))


def test_a_blank_string_counts_as_missing(table):
    assert_failed(match_item(gold("01", item_code="   "), table))


# ---- result object ---------------------------------------------------------------------------
def test_ok_always_agrees_with_row_and_problems(table):
    good = match_item(gold("01"), table)
    bad = match_item(gold("01", item_code="F"), table)
    assert good.ok == (good.row is not None) == (good.problems == ())
    assert bad.ok == (bad.row is not None) == (bad.problems == ())


def test_result_is_immutable(table):
    result = match_item(gold("01"), table)
    assert isinstance(result, MatchResult)
    with pytest.raises(FrozenInstanceError):
        result.ok = False


def test_matching_does_not_change_the_table(table):
    before = dict(table)
    match_item(gold("01"), table)
    match_item(gold("01", item_code="F"), table)
    assert table == before
