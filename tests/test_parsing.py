from decimal import Decimal as D

import pytest

from src.parsing import to_days, to_decimal


@pytest.mark.parametrize(
    "text, expected",
    [
        ("5,664,000.00", D("5664000.00")),
        ("12,000", D("12000")),
        ("10%", D("10")),
        ("  472.00 ", D("472.00")),
        ("0", D("0")),
        (None, None),
        ("abc", None),
        ("", None),
        ("30 to 90", None),
        ("1.5 days", None),
        # added: only proper thousands grouping is accepted, never a guessed decimal comma
        ("1,234", D("1234")),
        ("12,5", None),
        ("1,00", None),
        ("1,2,3", None),
        # added: negatives and doubled percent signs are not valid extracted values
        ("-5", None),
        ("10%%", None),
    ],
)
def test_to_decimal(text, expected):
    assert to_decimal(text) == expected


@pytest.mark.parametrize(
    "value, unit, expected",
    [
        ("4", "weeks", D("28")),
        ("4", "week", D("28")),
        ("45", "days", D("45")),
        ("45", "Day", D("45")),
        ("1", "month", None),
        (None, "day", None),
        ("4", None, None),
        ("four", "weeks", None),
    ],
)
def test_to_days(value, unit, expected):
    assert to_days(value, unit) == expected
