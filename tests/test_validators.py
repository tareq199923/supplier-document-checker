"""Gate 2 tests: schema, evidence, value matching, usable values, null checks.

Written BEFORE src/validators.py. The top-level import below is expected to fail
collection until the implementation exists; that is the RED stage.

No API key and no external call is used here.
"""
import json
from dataclasses import FrozenInstanceError, is_dataclass
from functools import lru_cache
from pathlib import Path

import pytest

from src.pdf_reader import normalise_text, read_pdf
from src.schema import FIELD_NAMES, Extraction
from src.validators import (
    Gate2Result,
    _positive_penalty_details,
    check_evidence_in_text,
    check_evidence_present,
    check_null_crosscheck,
    check_value_in_evidence,
    check_value_usable,
    parse_extraction,
    validate_extraction,
)

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
GOLD = json.loads(
    (ROOT / "tests" / "gold_extractions.json").read_text(encoding="utf-8")
)
GOLD_PACKS = sorted(GOLD)


# ---------- helpers ---------------------------------------------------------

def ev(value, evidence):
    """One schema field in its JSON form."""
    return {"value": value, "evidence": evidence}


def full_dict(compact):
    """Expand a compact gold entry to all 26 fields, using nulls where omitted."""
    return {
        name: ev(*compact[name]) if name in compact else ev(None, None)
        for name in FIELD_NAMES
    }


def extraction(**fields):
    """Build an Extraction with every field null unless explicitly supplied."""
    data = {name: ev(None, None) for name in FIELD_NAMES}
    data.update(fields)
    return Extraction.model_validate(data)


def gold_dict(pack):
    return full_dict(GOLD[pack])


def gold_extraction(pack):
    return Extraction.model_validate(gold_dict(pack))


@lru_cache(maxsize=None)
def pdf_text(pack):
    result = read_pdf(SAMPLES / f"supplier-pack-{pack}.pdf")
    assert result.ok, result.error
    return result.text


def has_failure(failures, *needles):
    """True when one failure message contains every needle, case-insensitively."""
    return any(
        all(needle.lower() in failure.lower() for needle in needles)
        for failure in failures
    )


# ---------- gold fixture sanity --------------------------------------------

def test_gold_fixture_has_the_seven_approved_packs():
    assert GOLD_PACKS == ["01", "02", "05", "06", "08", "09", "10"]


def test_gold_fixture_uses_only_schema_fields():
    for pack, fields in GOLD.items():
        unknown = set(fields) - set(FIELD_NAMES)
        assert not unknown, f"{pack}: unknown fields {unknown}"


@pytest.mark.parametrize("pack", GOLD_PACKS)
def test_gold_fixture_expands_to_all_26_fields(pack):
    expanded = full_dict(GOLD[pack])
    assert tuple(expanded) == FIELD_NAMES
    assert len(expanded) == 26


@pytest.mark.parametrize("pack", GOLD_PACKS)
def test_every_gold_evidence_quote_is_in_its_pdf(pack):
    text = pdf_text(pack)
    for name, (_value, evidence) in GOLD[pack].items():
        assert normalise_text(evidence) in text, f"{pack}.{name}"


@pytest.mark.parametrize("pack", GOLD_PACKS)
def test_every_gold_value_is_in_its_evidence_or_exempt(pack):
    for name, (value, evidence) in GOLD[pack].items():
        if name == "price_includes_vat":
            continue
        if name == "advance_pct" and value == "0":
            continue
        assert value.lower() in evidence.lower(), f"{pack}.{name}"


# ---------- JSON and schema ------------------------------------------------

def test_valid_json_returns_an_extraction():
    extraction_obj, failures = parse_extraction(json.dumps(gold_dict("01")))
    assert failures == ()
    assert extraction_obj is not None
    assert tuple(extraction_obj.model_dump()) == FIELD_NAMES


def test_json_with_language_code_fence_is_accepted():
    raw = "```json\n" + json.dumps(gold_dict("01")) + "\n```"
    extraction_obj, failures = parse_extraction(raw)
    assert failures == ()
    assert extraction_obj is not None


def test_json_with_plain_code_fence_is_accepted():
    raw = "```\n" + json.dumps(gold_dict("01")) + "\n```"
    extraction_obj, failures = parse_extraction(raw)
    assert failures == ()
    assert extraction_obj is not None


def test_empty_json_fails():
    extraction_obj, failures = parse_extraction("")
    assert extraction_obj is None
    assert has_failure(failures, "empty")


def test_whitespace_only_json_fails():
    extraction_obj, failures = parse_extraction(" \n\t ")
    assert extraction_obj is None
    assert has_failure(failures, "empty")


def test_invalid_json_fails():
    extraction_obj, failures = parse_extraction("{not valid json")
    assert extraction_obj is None
    assert has_failure(failures, "json")


def test_json_array_fails():
    extraction_obj, failures = parse_extraction("[]")
    assert extraction_obj is None
    assert has_failure(failures, "object")


def test_json_null_fails():
    extraction_obj, failures = parse_extraction("null")
    assert extraction_obj is None
    assert has_failure(failures, "object")


def test_json_string_fails():
    extraction_obj, failures = parse_extraction('"hello"')
    assert extraction_obj is None
    assert has_failure(failures, "object")


def test_missing_key_fails():
    data = gold_dict("01")
    del data["quantity"]
    extraction_obj, failures = parse_extraction(json.dumps(data))
    assert extraction_obj is None
    assert has_failure(failures, "quantity")


def test_every_schema_field_must_be_present():
    for name in FIELD_NAMES:
        data = gold_dict("01")
        del data[name]
        extraction_obj, failures = parse_extraction(json.dumps(data))
        assert extraction_obj is None, name
        assert has_failure(failures, name), name


def test_extra_key_fails():
    data = gold_dict("01")
    data["surprise"] = ev("x", "y")
    extraction_obj, failures = parse_extraction(json.dumps(data))
    assert extraction_obj is None
    assert has_failure(failures, "surprise")


def test_wrong_value_type_fails():
    data = gold_dict("01")
    data["quantity"] = ev(12000, "12,000")
    extraction_obj, failures = parse_extraction(json.dumps(data))
    assert extraction_obj is None
    assert has_failure(failures, "quantity")


def test_nested_structure_as_string_fails():
    data = gold_dict("01")
    data["quantity"] = "12000"
    extraction_obj, failures = parse_extraction(json.dumps(data))
    assert extraction_obj is None
    assert has_failure(failures, "quantity")


def test_nested_null_fails():
    data = gold_dict("01")
    data["quantity"] = None
    extraction_obj, failures = parse_extraction(json.dumps(data))
    assert extraction_obj is None
    assert has_failure(failures, "quantity")


def test_extra_nested_key_fails():
    data = gold_dict("01")
    data["quantity"] = {
        "value": "12,000",
        "evidence": "12,000",
        "surprise": 1,
    }
    extraction_obj, failures = parse_extraction(json.dumps(data))
    assert extraction_obj is None
    assert has_failure(failures, "quantity")


def test_explicit_null_is_allowed():
    data = {name: ev(None, None) for name in FIELD_NAMES}
    extraction_obj, failures = parse_extraction(json.dumps(data))
    assert failures == ()
    assert extraction_obj is not None


def test_all_null_extraction_passes_validation_when_nothing_is_mentioned():
    data = {name: ev(None, None) for name in FIELD_NAMES}
    result = validate_extraction(
        json.dumps(data),
        "Nothing relevant to this extraction appears here.",
    )
    assert result.ok is True
    assert result.extraction is not None
    assert result.failures == ()


# ---------- evidence presence and location ---------------------------------

def test_non_null_value_without_evidence_fails():
    failures = check_evidence_present(extraction(quantity=ev("12,000", None)))
    assert has_failure(failures, "quantity", "evidence")


def test_whitespace_only_evidence_fails():
    failures = check_evidence_present(
        extraction(quantity=ev("12,000", " \n\t "))
    )
    assert has_failure(failures, "quantity", "evidence")


def test_null_value_needs_no_evidence():
    assert check_evidence_present(extraction(quantity=ev(None, None))) == ()


def test_fabricated_evidence_fails():
    ex = extraction(quantity=ev("12,000", "The supplier offers a 50% discount."))
    failures = check_evidence_in_text(ex, pdf_text("01"))
    assert has_failure(failures, "quantity")


def test_one_character_mismatch_fails():
    ex = extraction(
        supplier_name=ev("Padma Textile Mills Ltd", "Padma Textile Mills Lta")
    )
    failures = check_evidence_in_text(ex, pdf_text("01"))
    assert has_failure(failures, "supplier_name")


def test_evidence_spanning_line_breaks_passes():
    pdf = "Terms:\nDelivery within 24\n days of receiving the purchase order."
    ex = extraction(
        delivery_value=ev(
            "24", "Delivery within 24 days of receiving the purchase order."
        )
    )
    assert check_evidence_in_text(ex, pdf) == ()


def test_evidence_check_is_case_sensitive():
    pdf = "VAT included in the unit price."
    ex = extraction(
        price_includes_vat=ev("true", "vat included in the unit price.")
    )
    assert has_failure(check_evidence_in_text(ex, pdf), "price_includes_vat")


def test_evidence_is_whitespace_normalised():
    pdf = "VAT   included\nin the unit price."
    ex = extraction(
        price_includes_vat=ev("true", "VAT included in the unit price.")
    )
    assert check_evidence_in_text(ex, pdf) == ()


def test_paraphrased_evidence_fails():
    pdf = "Delivery within 24 days of receiving the purchase order."
    ex = extraction(
        delivery_value=ev("24", "Delivery in 24 days after the order.")
    )
    assert has_failure(check_evidence_in_text(ex, pdf), "delivery_value")


def test_value_not_in_its_own_evidence_fails():
    line = (
        "A Cotton twill fabric, 280 gsm 12,000 metre "
        "472.00 5,664,000.00"
    )
    ex = extraction(description=ev("Metal zipper, 18 cm", line))
    assert has_failure(check_value_in_evidence(ex), "description")


def test_null_value_is_not_value_matched():
    ex = extraction(quantity=ev(None, "No quantity here."))
    assert check_value_in_evidence(ex) == ()


# ---------- value inside evidence ------------------------------------------

def test_numeric_value_appears_in_its_evidence():
    line = (
        "A Cotton twill fabric, 280 gsm 12,000 metre "
        "472.00 5,664,000.00"
    )
    ex = extraction(quantity=ev("12,000", line))
    assert check_value_in_evidence(ex) == ()


@pytest.mark.parametrize(
    "evidence",
    [
        "Delivery within 130 days.",
        "Delivery within 300 days.",
    ],
)
def test_numeric_value_respects_boundaries(evidence):
    ex = extraction(quantity=ev("30", evidence))
    assert has_failure(check_value_in_evidence(ex), "quantity")


def test_numeric_value_matches_at_a_real_boundary():
    ex = extraction(quantity=ev("30", "Delivery within 30 days."))
    assert check_value_in_evidence(ex) == ()


def test_numeric_grouping_boundary_is_respected():
    ex = extraction(quantity=ev("12,000", "Quantity 112,000 piece."))
    assert has_failure(check_value_in_evidence(ex), "quantity")


@pytest.mark.parametrize(
    "field, value, evidence",
    [
        ("unit_price", "472.00", "Unit price 472."),
        ("quantity", "12,000", "Quantity 12000 piece."),
    ],
)
def test_numeric_exact_written_format_is_required(field, value, evidence):
    ex = extraction(**{field: ev(value, evidence)})
    assert has_failure(check_value_in_evidence(ex), field)


def test_exact_decimal_format_passes():
    ex = extraction(unit_price=ev("472.00", "Unit price 472.00."))
    assert check_value_in_evidence(ex) == ()


def test_bare_percentage_matches_percent_sign():
    ex = extraction(
        penalty_pct=ev(
            "0.75",
            "Late delivery penalty: 0.75% of order value per week.",
        )
    )
    assert check_value_in_evidence(ex) == ()


def test_percentage_with_sign_matches():
    ex = extraction(vat_rate=ev("15%", "VAT 15% 849,600.00."))
    assert check_value_in_evidence(ex) == ()


def test_text_value_matching_is_case_insensitive():
    ex = extraction(
        description=ev(
            "Cotton twill fabric, 280 gsm",
            "a cotton twill fabric, 280 gsm 12,000 metre.",
        )
    )
    assert check_value_in_evidence(ex) == ()


def test_day_matches_days():
    ex = extraction(
        delivery_unit=ev(
            "day", "Delivery within 24 days of receiving the purchase order."
        )
    )
    assert check_value_in_evidence(ex) == ()


@pytest.mark.parametrize(
    "evidence",
    [
        "No advance payment required.",
        "0% advance payment.",
        "Advance payment: 0%.",
    ],
)
def test_zero_advance_with_supporting_evidence_passes(evidence):
    ex = extraction(advance_pct=ev("0", evidence))
    assert check_value_in_evidence(ex) == ()


@pytest.mark.parametrize(
    "evidence",
    [
        "Balance payable within 45 days of delivery.",
        "Advance payment terms apply.",
        "10% advance payable with the purchase order.",
        (
            "No advance payment required. "
            "10% advance payable with the purchase order."
        ),
    ],
)
def test_zero_advance_with_bad_evidence_fails(evidence):
    ex = extraction(advance_pct=ev("0", evidence))
    assert has_failure(check_value_in_evidence(ex), "advance_pct")


REPEATED_40 = (
    "40% advance payable with the purchase order. "
    "40% advance with the purchase order, balance 60% within 30 days of delivery."
)


def test_repeated_40_percent_stays_40():
    ex = extraction(advance_pct=ev("40", REPEATED_40))
    assert check_value_in_evidence(ex) == ()


def test_repeated_40_percent_is_not_80():
    ex = extraction(advance_pct=ev("80", REPEATED_40))
    assert has_failure(check_value_in_evidence(ex), "advance_pct")


@pytest.mark.parametrize(
    "evidence",
    [
        "Unit price (BDT, VAT included).",
        "VAT-inclusive price.",
        "All taxes included.",
        "Price includes VAT.",
    ],
)
def test_vat_included_true_with_supporting_evidence_passes(evidence):
    ex = extraction(price_includes_vat=ev("true", evidence))
    assert check_value_in_evidence(ex) == ()


@pytest.mark.parametrize(
    "evidence",
    [
        "VAT 15%.",
        "VAT 15% shown separately.",
    ],
)
def test_vat_included_true_with_weak_evidence_fails(evidence):
    ex = extraction(price_includes_vat=ev("true", evidence))
    assert has_failure(check_value_in_evidence(ex), "price_includes_vat")


def test_vat_included_false_is_exempt_from_literal_match():
    ex = extraction(price_includes_vat=ev("false", "Unit price (BDT)."))
    assert check_value_in_evidence(ex) == ()


def test_quantity_zero_with_unrelated_evidence_fails():
    ex = extraction(quantity=ev("0", "No advance payment required."))
    assert has_failure(check_value_in_evidence(ex), "quantity")


def test_quantity_zero_with_own_evidence_passes():
    ex = extraction(quantity=ev("0", "Quantity 0 piece."))
    assert check_value_in_evidence(ex) == ()


# ---------- usable numeric and enumerated values ---------------------------

@pytest.mark.parametrize(
    "field, value",
    [
        ("quantity", "12,000"),
        ("unit_price", "472.00"),
        ("line_amount", "5,664,000.00"),
        ("subtotal", "5,664,000.00"),
        ("vat_rate", "15%"),
        ("vat_amount", "849,600.00"),
        ("total", "6,513,600.00"),
        ("advance_pct", "0"),
        ("balance_days_min", "30"),
        ("balance_days_max", "90"),
        ("delivery_value", "4"),
        ("penalty_pct", "0.5%"),
        ("validity_days", "45"),
    ],
)
def test_valid_numeric_formats_are_usable(field, value):
    ex = extraction(**{field: ev(value, "x")})
    assert check_value_usable(ex) == ()


@pytest.mark.parametrize(
    "field, value",
    [
        ("quantity", "12,5"),
        ("quantity", "1,00"),
        ("unit_price", "472.00 BDT"),
        ("vat_rate", "15%%"),
        ("advance_pct", "ten"),
        ("balance_days_min", "30 to 90"),
        ("delivery_value", "four"),
        ("penalty_pct", "-0.5"),
        ("validity_days", ""),
    ],
)
def test_invalid_numeric_formats_fail(field, value):
    ex = extraction(**{field: ev(value, "x")})
    assert has_failure(check_value_usable(ex), field)


def test_percent_sign_is_rejected_on_a_non_percent_field():
    ex = extraction(quantity=ev("15%", "15%"))
    assert has_failure(check_value_usable(ex), "quantity")


@pytest.mark.parametrize("field", ["advance_pct", "vat_rate", "penalty_pct"])
def test_percent_sign_is_accepted_on_percentage_fields(field):
    ex = extraction(**{field: ev("15%", "15%")})
    assert check_value_usable(ex) == ()


@pytest.mark.parametrize("unit", ["day", "days", "week", "weeks", "Day"])
def test_delivery_unit_day_and_week_are_usable(unit):
    ex = extraction(delivery_unit=ev(unit, unit))
    assert check_value_usable(ex) == ()


@pytest.mark.parametrize("unit", ["month", "fortnight", ""])
def test_other_delivery_units_are_not_usable(unit):
    ex = extraction(delivery_unit=ev(unit, unit))
    assert has_failure(check_value_usable(ex), "delivery_unit")


@pytest.mark.parametrize("flag", ["true", "false"])
def test_price_includes_vat_true_and_false_are_usable(flag):
    ex = extraction(price_includes_vat=ev(flag, flag))
    assert check_value_usable(ex) == ()


@pytest.mark.parametrize("flag", ["yes", "1", ""])
def test_other_vat_flags_are_not_usable(flag):
    ex = extraction(price_includes_vat=ev(flag, flag))
    assert has_failure(check_value_usable(ex), "price_includes_vat")


def test_text_fields_are_not_number_parsed():
    ex = extraction(
        description=ev("Poly bag, 30 x 40 cm", "x"),
        quotation_no=ev("QT-2026-901", "x"),
        tin=ev("101458013391", "x"),
    )
    assert check_value_usable(ex) == ()


def test_blank_text_value_fails():
    ex = extraction(supplier_name=ev("   ", "x"))
    assert has_failure(check_value_usable(ex), "supplier_name")


def test_null_values_do_not_fail_usable_check():
    assert check_value_usable(extraction()) == ()


# ---------- null cross-check -----------------------------------------------

def test_tin_mentioned_but_not_extracted_fails():
    failures = check_null_crosscheck(
        extraction(), "Supplier TIN: 101458013391"
    )
    assert has_failure(failures, "tin")


def test_bin_mentioned_but_not_extracted_fails():
    failures = check_null_crosscheck(
        extraction(), "BIN: 000170141-0101"
    )
    assert has_failure(failures, "bin")


def test_tin_and_bin_matching_is_case_insensitive():
    failures = check_null_crosscheck(
        extraction(), "supplier tin: 101458013391 bin: 000170141-0101"
    )
    assert has_failure(failures, "tin")
    assert has_failure(failures, "bin")


def test_tinted_and_cabin_do_not_trigger_identifier_checks():
    failures = check_null_crosscheck(
        extraction(), "The tinted cabin is mentioned."
    )
    assert failures == ()


def test_positive_penalty_details_but_null_percentage_fails():
    failures = check_null_crosscheck(
        extraction(),
        "Late delivery penalty: 1% of order value per week.",
    )
    assert has_failure(failures, "penalty")


def test_negative_penalty_statement_does_not_fail():
    failures = check_null_crosscheck(
        extraction(), "No late delivery penalty applies."
    )
    assert failures == ()


def test_negative_penalty_statement_with_percentage_does_not_fail():
    failures = check_null_crosscheck(
        extraction(), "No late delivery penalty of 1% applies."
    )
    assert failures == ()


def test_no_penalty_mention_does_not_fail():
    assert check_null_crosscheck(gold_extraction("06"), pdf_text("06")) == ()


def test_extracted_penalty_skips_the_cross_check():
    text = "Late delivery penalty of 0.5% per week of delay."
    ex = extraction(penalty_pct=ev("0.5", text))
    assert check_null_crosscheck(ex, text) == ()


def test_pack_08_registration_wording_does_not_trigger_tin_or_bin():
    assert check_null_crosscheck(gold_extraction("08"), pdf_text("08")) == ()


# ---------- integration ----------------------------------------------------

@pytest.mark.parametrize("pack", GOLD_PACKS)
def test_each_gold_pack_passes_gate_2(pack):
    result = validate_extraction(json.dumps(gold_dict(pack)), pdf_text(pack))
    assert result.ok is True
    assert result.extraction is not None
    assert result.failures == ()


def test_extraction_checked_against_the_wrong_pdf_fails():
    result = validate_extraction(
        json.dumps(gold_dict("01")),
        pdf_text("02"),
    )
    assert result.ok is False
    assert result.extraction is None
    assert result.failures


def test_failed_validation_has_no_extraction():
    data = gold_dict("01")
    data["quantity"] = ev("12,000", "This quote is not in the PDF.")
    result = validate_extraction(json.dumps(data), pdf_text("01"))
    assert result.ok is False
    assert result.extraction is None
    assert result.failures


def test_successful_validation_has_no_failures():
    result = validate_extraction(
        json.dumps(gold_dict("02")),
        pdf_text("02"),
    )
    assert result.ok is True
    assert result.failures == ()


@pytest.mark.parametrize("pack", ["05", "09", "10"])
def test_business_rule_violations_do_not_fail_gate_2(pack):
    # 40% advance, an unclear payment window, 4 weeks, 60 days and VAT
    # included are all business-rule cases, not extraction failures.
    result = validate_extraction(json.dumps(gold_dict(pack)), pdf_text(pack))
    assert result.ok is True


@pytest.mark.parametrize("pack", ["06", "08"])
def test_legitimate_nulls_do_not_fail_gate_2(pack):
    # Pack 06 has no penalty clause; pack 08 genuinely has no TIN/BIN.
    # R3, R4 and N2 must handle these, not Gate 2.
    result = validate_extraction(json.dumps(gold_dict(pack)), pdf_text(pack))
    assert result.ok is True


def test_gate2_result_is_a_frozen_dataclass():
    result = Gate2Result(True, extraction(), ())
    assert is_dataclass(result)
    with pytest.raises(FrozenInstanceError):
        result.ok = False


def test_gate2_result_failures_are_a_tuple():
    result = Gate2Result(False, None, ("a failure",))
    assert isinstance(result.failures, tuple)


# ---------- regression: stricter evidence and nested structure --------------

@pytest.mark.parametrize(
    "evidence",
    [
        "Price does not include VAT.",
        "Price doesn't include VAT.",
        "Prices are exclusive of VAT.",
        "VAT is excluded.",
        "Price plus VAT.",
        "VAT is not included in the price.",
    ],
)
def test_vat_included_true_with_negated_evidence_fails(evidence):
    ex = extraction(price_includes_vat=ev("true", evidence))
    assert has_failure(check_value_in_evidence(ex), "price_includes_vat")


def test_inclusive_of_all_taxes_supports_vat_included():
    ex = extraction(price_includes_vat=ev("true", "Inclusive of all taxes."))
    assert check_value_in_evidence(ex) == ()


@pytest.mark.parametrize(
    "evidence",
    [
        "No advance was made last year.",
        "No advance payment was made last year.",
        "No advance.",
    ],
)
def test_zero_advance_with_historical_or_vague_evidence_fails(evidence):
    ex = extraction(advance_pct=ev("0", evidence))
    assert has_failure(check_value_in_evidence(ex), "advance_pct")


@pytest.mark.parametrize(
    "evidence",
    [
        "No advance is required.",
        "Advance payment is not required.",
        "No advance payment payable.",
        "No advance payment of 10% is required.",
    ],
)
def test_zero_advance_with_contractual_wording_passes(evidence):
    ex = extraction(advance_pct=ev("0", evidence))
    assert check_value_in_evidence(ex) == ()


def test_empty_nested_object_fails():
    data = gold_dict("01")
    data["quantity"] = {}
    extraction_obj, failures = parse_extraction(json.dumps(data))
    assert extraction_obj is None
    assert has_failure(failures, "quantity")


def test_every_schema_field_rejects_empty_nested_object():
    for name in FIELD_NAMES:
        data = gold_dict("01")
        data[name] = {}
        extraction_obj, failures = parse_extraction(json.dumps(data))
        assert extraction_obj is None, name
        assert has_failure(failures, name), name


@pytest.mark.parametrize(
    "nested",
    [
        {"evidence": "12,000"},
        {"value": "12,000"},
    ],
)
def test_nested_object_missing_a_required_key_fails(nested):
    data = gold_dict("01")
    data["quantity"] = nested
    extraction_obj, failures = parse_extraction(json.dumps(data))
    assert extraction_obj is None
    assert has_failure(failures, "quantity")


def test_explicit_nested_nulls_still_pass():
    data = gold_dict("01")
    data["quantity"] = ev(None, None)
    extraction_obj, failures = parse_extraction(json.dumps(data))
    assert failures == ()
    assert extraction_obj is not None


@pytest.mark.parametrize(
    "text",
    [
        "No penalty applies, but late delivery penalty is 0.5% per week.",
        "No penalty applies and late delivery penalty is 0.5% per week.",
        "No penalty applies while late delivery penalty is 0.5% per week.",
    ],
)
def test_positive_penalty_after_negative_statement_is_detected(text):
    assert _positive_penalty_details(text) is True
