"""Boundary tests for the rule engine. No LLM, no API key, runs in milliseconds.

Written BEFORE src/rules.py. Do not edit these tests to make the code pass.
"""
import json
from decimal import Decimal as D
from pathlib import Path

import pytest

from src import config
from src.rules import (
    RuleInputs,
    check_h1,
    check_h2,
    check_h3,
    check_h4,
    check_h5,
    check_h6,
    check_h7,
    check_m,
    check_n1,
    check_n2,
    check_r1,
    check_r2,
    check_r3,
    check_r4,
    check_r5,
    decide,
    evaluate,
    Finding,
)


def rid(finding):
    return finding.rule_id if finding else None


def dec(text):
    return None if text is None else D(text)


# ---- R1 / H1: price against the maximum (3% boundary) ---------------------------
@pytest.mark.parametrize(
    "price, expected",
    [("480.00", None), ("494.40", None), ("494.45", "R1"), ("600.00", "R1")],
)
def test_r1_price_over_limit(price, expected):
    assert rid(check_r1(D(price), D("480"))) == expected


@pytest.mark.parametrize(
    "price, expected",
    [("470.00", None), ("480.00", None), ("480.01", "H1"), ("494.40", "H1"), ("494.45", None)],
)
def test_h1_price_slightly_over(price, expected):
    assert rid(check_h1(D(price), D("480"))) == expected


def test_price_rules_ignore_missing_values():
    assert check_r1(None, D("480")) is None
    assert check_h1(D("500"), None) is None


# ---- R2: advance payment ---------------------------------------------------------
@pytest.mark.parametrize("advance, expected", [("0", None), ("20", None), ("21", "R2"), ("40", "R2")])
def test_r2_advance(advance, expected):
    assert rid(check_r2(D(advance))) == expected


def test_r2_missing_advance_is_not_a_reject():
    assert check_r2(None) is None


# ---- R3: no penalty clause -------------------------------------------------------
@pytest.mark.parametrize("pct, expected", [(None, "R3"), ("0.1", None), ("0.5", None)])
def test_r3_penalty_clause_present(pct, expected):
    assert rid(check_r3(dec(pct))) == expected


# ---- R4: TIN or BIN --------------------------------------------------------------
@pytest.mark.parametrize(
    "tin, bin_no, expected",
    [
        ("101458013391", "000170141-0101", None),
        (None, "000170141-0101", "R4"),
        ("101458013391", None, "R4"),
        (None, None, "R4"),
    ],
)
def test_r4_registration(tin, bin_no, expected):
    assert rid(check_r4(tin, bin_no)) == expected


# ---- R5 / H4: delivery days ------------------------------------------------------
@pytest.mark.parametrize("days, expected", [(30, None), (31, None), (45, None), (46, "R5")])
def test_r5_delivery_too_late(days, expected):
    assert rid(check_r5(D(days))) == expected


@pytest.mark.parametrize("days, expected", [(20, None), (30, None), (31, "H4"), (45, "H4"), (46, None)])
def test_h4_delivery_in_review_window(days, expected):
    assert rid(check_h4(D(days))) == expected


# ---- H2: quantity differs by more than 5% ---------------------------------------
@pytest.mark.parametrize(
    "qty, expected",
    [
        ("20000", None),
        ("21000", None),   # exactly +5.0%
        ("21020", "H2"),   # +5.1%
        ("19000", None),   # exactly -5.0%
        ("18980", "H2"),   # -5.1%
        ("16500", "H2"),   # pack 07: 17.5% below
    ],
)
def test_h2_quantity(qty, expected):
    assert rid(check_h2(D(qty), D("20000"))) == expected


# ---- H3: balance payment days ----------------------------------------------------
@pytest.mark.parametrize(
    "low, high, expected",
    [
        ("30", "30", None),
        ("45", "45", None),
        ("60", "60", None),
        ("29", "29", "H3"),
        ("61", "61", "H3"),
        ("30", "90", "H3"),    # pack 09: a range is unclear
        (None, None, "H3"),
        ("30", None, "H3"),
    ],
)
def test_h3_payment_days(low, high, expected):
    assert rid(check_h3(dec(low), dec(high))) == expected


# ---- H5: VAT shown separately at 15% ---------------------------------------------
@pytest.mark.parametrize(
    "rate, amount, expected",
    [
        ("15", "849600", None),
        (None, None, "H5"),     # pack 10
        ("10", "566400", "H5"),
        ("15", None, "H5"),
        (None, "849600", "H5"),
    ],
)
def test_h5_vat_line(rate, amount, expected):
    assert rid(check_h5(dec(rate), dec(amount))) == expected


# ---- H6: the numbers add up ------------------------------------------------------
def h6(**over):
    args = dict(
        quantity=D("50000"),
        price=D("6.40"),
        line_amount=D("320000"),
        subtotal=D("320000"),
        vat_rate=D("15"),
        vat_amount=D("48000"),
        total=D("368000"),
    )
    args.update(over)
    return check_h6(**args)


def test_h6_consistent_numbers_pass():
    assert h6() is None


def test_h6_pack_09_line_total_wrong():
    finding = h6(line_amount=D("322000"), subtotal=D("322000"), vat_amount=D("48300"), total=D("370300"))
    assert rid(finding) == "H6"
    assert "320,000.00" in finding.message and "322,000.00" in finding.message


@pytest.mark.parametrize(
    "over",
    [
        {"subtotal": D("321000")},
        {"vat_amount": D("48300")},
        {"total": D("369000")},
    ],
)
def test_h6_each_check_can_fail(over):
    assert rid(h6(**over)) == "H6"


def test_h6_tolerance_is_one_cent():
    assert h6(line_amount=D("320000.01")) is None
    assert rid(h6(line_amount=D("320000.02"))) == "H6"


def test_h6_skips_checks_with_missing_values():
    assert h6(subtotal=None, vat_rate=None, vat_amount=None, total=None) is None


# ---- H7: validity ----------------------------------------------------------------
@pytest.mark.parametrize("days, expected", [(30, None), (35, None), (29, "H7")])
def test_h7_validity(days, expected):
    assert rid(check_h7(D(days))) == expected


# ---- N1 / N2: penalty strength and basis (interpretation choices) ----------------
@pytest.mark.parametrize(
    "pct, period, expected",
    [
        ("0.5", "week", None),
        ("0.49", "week", "N1"),
        ("1", "week", None),
        ("0.5", "day", "N1"),      # cannot compare, so a person checks
        ("0.5", None, "N1"),
        (None, "week", None),      # no clause is rule R3's job
    ],
)
def test_n1_penalty_rate(pct, period, expected):
    assert rid(check_n1(dec(pct), period)) == expected


@pytest.mark.parametrize(
    "pct, basis, expected",
    [
        ("0.5", "order value", None),
        ("0.5", "Order Value", None),
        ("0.5", None, "N2"),        # pack 08
        ("0.5", "total price", "N2"),
        (None, None, None),
    ],
)
def test_n2_penalty_basis(pct, basis, expected):
    assert rid(check_n2(dec(pct), basis)) == expected


# ---- M: other required fields ----------------------------------------------------
def test_m_missing_required_fields():
    assert check_m([]) is None
    finding = check_m(["quotation_no", "unit_price"])
    assert rid(finding) == "M" and finding.severity == "review"
    assert "quotation_no" in finding.message


# ---- decision --------------------------------------------------------------------
def test_decide():
    reject = Finding("R1", "reject", "x")
    review = Finding("H1", "review", "x")
    assert decide([]) == config.APPROVE
    assert decide([review]) == config.NEEDS_CHECK
    assert decide([reject]) == config.REJECT
    assert decide([review, reject]) == config.REJECT


# ---- all ten sample packs, from hand-typed inputs (no PDF, no LLM) ----------------
def make(price, max_price, qty, requested, **over):
    price, max_price, qty, requested = D(price), D(max_price), D(qty), D(requested)
    line = over.pop("line_amount", qty * price)
    subtotal = over.pop("subtotal", line)
    vat_amount = over.pop("vat_amount", None if subtotal is None else subtotal * D("0.15"))
    total = over.pop("total", None if subtotal is None or vat_amount is None else subtotal + vat_amount)
    base = dict(
        price=price,
        max_price=max_price,
        quantity=qty,
        requested_qty=requested,
        advance_pct=D("10"),
        balance_days_min=D("45"),
        balance_days_max=D("45"),
        delivery_days=D("24"),
        penalty_pct=D("1"),
        penalty_period="week",
        penalty_basis="order value",
        tin="101458013391",
        bin_no="000170141-0101",
        vat_rate=D("15"),
        vat_amount=vat_amount,
        line_amount=line,
        subtotal=subtotal,
        total=total,
        validity_days=D("30"),
    )
    base.update(over)
    return RuleInputs(**base)


PACKS = {
    "01": make("472", "480", "12000", "12000"),
    "02": make("36", "38", "20000", "20000", advance_pct=D("0"), balance_days_min=D("30"),
               balance_days_max=D("30"), delivery_days=D("21"), penalty_pct=D("0.75")),
    "03": make("229", "210", "800", "800", advance_pct=D("15"), balance_days_min=D("40"),
               balance_days_max=D("40"), delivery_days=D("20"), penalty_pct=D("0.5")),
    "04": make("6.65", "6.50", "50000", "50000", balance_days_min=D("30"), balance_days_max=D("30"),
               delivery_days=D("28"), penalty_pct=D("0.6"), validity_days=D("40")),
    "05": make("92", "95", "3000", "3000", advance_pct=D("40"), balance_days_min=D("30"),
               balance_days_max=D("30"), delivery_days=D("25")),
    "06": make("465", "480", "12000", "12000", balance_days_min=D("60"), balance_days_max=D("60"),
               delivery_days=D("27"), penalty_pct=None, penalty_period=None, penalty_basis=None),
    "07": make("35", "38", "16500", "20000", advance_pct=D("0"), delivery_days=D("22"),
               penalty_pct=D("0.5"), validity_days=D("35")),
    "08": make("205", "210", "800", "800", balance_days_min=D("30"), balance_days_max=D("30"),
               delivery_days=D("18"), penalty_pct=D("0.5"), penalty_basis=None, tin=None, bin_no=None),
    "09": make("6.40", "6.50", "50000", "50000", line_amount=D("322000"), advance_pct=D("5"),
               balance_days_min=D("30"), balance_days_max=D("90"), delivery_days=D("28"),
               penalty_pct=D("0.5")),
    "10": make("94", "95", "3000", "3000", subtotal=None, vat_amount=None, vat_rate=None,
               total=D("282000"), delivery_days=D("60"), penalty_pct=D("0.5")),
}

ANSWER_KEY = json.loads(Path(__file__).resolve().parent.parent.joinpath("expected.json").read_text(encoding="utf-8"))
ANSWER_KEY = ANSWER_KEY["PR-2026-114"]


@pytest.mark.parametrize("pack", sorted(PACKS))
def test_pack_rule_ids_match_answer_key(pack):
    findings = evaluate(PACKS[pack])
    assert sorted(f.rule_id for f in findings) == sorted(ANSWER_KEY[pack]["rule_ids"])


@pytest.mark.parametrize("pack", sorted(PACKS))
def test_pack_decision_matches_answer_key(pack):
    assert decide(evaluate(PACKS[pack])) == ANSWER_KEY[pack]["decision"]


def test_engine_reports_every_triggered_rule_not_just_the_first():
    assert [f.rule_id for f in evaluate(PACKS["10"])] == ["R5", "H5"]
    assert [f.rule_id for f in evaluate(PACKS["09"])] == ["H3", "H6"]


# ---- added: a missing value never triggers a rule; rule M reports the missing field ----
def test_rules_skip_missing_values():
    assert check_r5(None) is None
    assert check_h4(None) is None
    assert check_h7(None) is None
    assert check_h2(None, D("20000")) is None
    assert check_h2(D("20000"), None) is None


# ---- added: a payment range is unclear even when both ends sit inside 30 to 60 ----
def test_h3_range_inside_window_is_still_unclear():
    assert rid(check_h3(D("30"), D("60"))) == "H3"
