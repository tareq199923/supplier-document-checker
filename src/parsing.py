"""Turn extracted TEXT into numbers. Plain code, no LLM, never float.

The LLM returns values as written ("12,000", "10%", "4"). These helpers convert
them. Anything that is not clearly a number becomes None, never a guess.
"""
import re
from decimal import Decimal

from src import config

# Digits only, e.g. "472" or "472.00".
_PLAIN = re.compile(r"\d+(?:\.\d+)?")
# Proper thousands grouping only, e.g. "12,000" or "5,664,000.00".
# "12,5" or "1,00" could be a decimal comma, so they are NOT accepted (never guess).
_GROUPED = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")


def to_decimal(text: str | None) -> Decimal | None:
    """'5,664,000.00' -> Decimal('5664000.00'); '10%' -> Decimal('10'); junk -> None."""
    if text is None:
        return None
    cleaned = text.strip().removesuffix("%").strip()
    if _PLAIN.fullmatch(cleaned) or _GROUPED.fullmatch(cleaned):
        return Decimal(cleaned.replace(",", ""))
    return None


def to_days(value: str | None, unit: str | None) -> Decimal | None:
    """('4', 'weeks') -> 28; ('45', 'days') -> 45; anything else -> None."""
    number = to_decimal(value)
    if number is None or unit is None:
        return None
    unit = unit.strip().lower()
    if unit.startswith("week"):
        return number * config.DAYS_PER_WEEK
    if unit.startswith("day"):
        return number
    return None
