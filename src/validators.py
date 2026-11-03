"""Gate 2: validate the LLM extraction before any downstream Python logic.

This module validates only. It never calls an LLM, never applies company rules,
and never compares with the purchase request. Failures are short, field-specific
strings so the workflow layer can decide whether to retry or send the document
to a person.

The single public entry point is validate_extraction(raw_text, pdf_text).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal

from pydantic import ValidationError

from src.parsing import to_decimal
from src.pdf_reader import normalise_text
from src.schema import (
    FIELD_NAMES,
    VALUE_CHECK_EXEMPT_FIELDS,
    ZERO_VALUE_EXEMPT_FIELDS,
    Extraction,
)

_ZERO = Decimal("0")

# Schema facts used by the validator. These are not business thresholds.
_NUMERIC_FIELDS = frozenset(
    {
        "quantity",
        "unit_price",
        "line_amount",
        "subtotal",
        "vat_rate",
        "vat_amount",
        "total",
        "advance_pct",
        "balance_days_min",
        "balance_days_max",
        "delivery_value",
        "penalty_pct",
        "validity_days",
    }
)
_PERCENT_FIELDS = frozenset({"advance_pct", "vat_rate", "penalty_pct"})
_DELIVERY_UNITS = frozenset({"day", "days", "week", "weeks"})
_VAT_FLAGS = frozenset({"true", "false"})
_NESTED_FIELD_NAMES = ("value", "evidence")

_CODE_FENCE = re.compile(
    r"^\s*```(?:json)?\s*(.*?)\s*```\s*$",
    re.IGNORECASE | re.DOTALL,
)

# A numeric value must be a complete token, not part of 130 or 472.00.
# A trailing "." or "," is allowed when it is punctuation, but not when it
# starts another number ("472.00.5", "12,000,5").
_NUMERIC_BOUNDARY_BEFORE = r"(?<![\d.,+-])"
_NUMERIC_BOUNDARY_AFTER = r"(?!\d|[.,]\d)"

_TIN_WORD = re.compile(r"\bTIN\b", re.IGNORECASE)
_BIN_WORD = re.compile(r"\bBIN\b", re.IGNORECASE)

# ---- zero advance -----------------------------------------------------------
_ADVANCE_NEGATED_PATTERNS = (
    re.compile(
        r"\b(?:no|not|none|never|without)\b[^.;]*?\badvance\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\badvance\b[^.;]*?\b(?:not|never)\s+"
        r"(?:required|payable|applicable|due|made|paid)\b",
        re.IGNORECASE,
    ),
)
_POSITIVE_ADVANCE_PATTERNS = (
    re.compile(
        r"(?<![\d.,+-])"
        r"(?:[1-9]\d*(?:\.\d+)?|0*\.\d*[1-9]\d*)"
        r"\s*%\s*(?:of\s+)?(?:the\s+)?(?:total\s+)?(?:price\s+)?advance\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\badvance\b[^.;]*?"
        r"(?<![\d.,+-])"
        r"(?:[1-9]\d*(?:\.\d+)?|0*\.\d*[1-9]\d*)\s*%",
        re.IGNORECASE,
    ),
)
_ZERO_ADVANCE_PATTERNS = (
    re.compile(
        r"\bno\s+advance(?:\s+payment)?\s+"
        r"(?:is\s+|will\s+be\s+)?"
        r"(?:required|payable|applicable|due)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\badvance(?:\s+payment)?\s+(?:is\s+)?"
        r"not\s+(?:required|payable|applicable|due)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bwithout\s+(?:any\s+)?advance\s+payment\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bno\s+advance\s+payment\b"
        r"(?!\s+(?:was|were|has|have|had|made|paid|received)\b)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![\d.,+-])0(?:\.0+)?\s*%\s*"
        r"(?:of\s+)?(?:the\s+)?(?:total\s+)?(?:price\s+)?advance\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\badvance\b[^.;]*?(?<![\d.,+-])0(?:\.0+)?\s*%",
        re.IGNORECASE,
    ),
)

# ---- VAT included -----------------------------------------------------------
_TAX_WORD = re.compile(r"\b(?:VAT|tax(?:es)?)\b", re.IGNORECASE)
_VAT_NEGATED_PATTERNS = (
    re.compile(
        r"\b(?:does\s+not|doesn't|do\s+not|don't|did\s+not|didn't|"
        r"is\s+not|isn't|are\s+not|aren't|was\s+not|wasn't|"
        r"were\s+not|weren't|not|never)\s+"
        r"(?:include|includes|included|including)\b[^.;]*?"
        r"\b(?:VAT|tax(?:es)?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:VAT|tax(?:es)?)\b[^.;]*?\b(?:not|never)\s+"
        r"(?:include[sd]?|included|inclusive)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bexclude[sd]?\b[^.;]*?\b(?:VAT|tax(?:es)?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:VAT|tax(?:es)?)\b[^.;]*?"
        r"\b(?:excluded|excludes|exclusive|extra|additional)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:exclusive|excl\.?)\s+of\s+(?:all\s+)?(?:the\s+)?"
        r"(?:VAT|tax(?:es)?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bplus\s+(?:the\s+)?(?:VAT|tax(?:es)?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:VAT|tax(?:es)?)\s+to\s+be\s+"
        r"(?:added|charged)\b",
        re.IGNORECASE,
    ),
)
_VAT_INCLUDED_PATTERNS = (
    re.compile(r"\bVAT[\s-]+inclusive\b", re.IGNORECASE),
    re.compile(r"\bVAT\s+(?:is\s+)?included\b", re.IGNORECASE),
    re.compile(
        r"\binclude[sd]?\s+(?:all\s+)?(?:the\s+)?"
        r"(?:VAT|tax(?:es)?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\binclusive\s+of\s+(?:all\s+)?(?:the\s+)?"
        r"(?:VAT|tax(?:es)?)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\ball\s+tax(?:es)?\s+(?:are\s+)?included\b", re.IGNORECASE),
    re.compile(r"\btax(?:es)?\s+(?:are\s+)?included\b", re.IGNORECASE),
    re.compile(
        r"\bincluding\s+(?:all\s+)?(?:the\s+)?(?:VAT|tax(?:es)?)\b",
        re.IGNORECASE,
    ),
)

# ---- penalty null cross-check ----------------------------------------------
# Commas only split when followed by whitespace, so grouped numbers such as
# "5,664,000.00" stay intact.
_CLAUSE_BOUNDARY = re.compile(
    r"(?<=[.!?])\s+"
    r"|\s*;\s*"
    r"|\s*,\s+"
    r"|\s+\b(?:and|but|however|yet|while|whereas|although|though|"
    r"instead|unless)\b\s+",
    re.IGNORECASE,
)
_PENALTY_KEYWORD = re.compile(
    r"\b(?:penalt(?:y|ies)|late[\s-]+delivery|delay(?:ed|s)?)\b",
    re.IGNORECASE,
)
_PERCENT = re.compile(r"(?<![\d.,+-])(\d+(?:\.\d+)?)\s*%")
_NEGATED_PENALTY_PATTERNS = (
    re.compile(
        r"\b(?:no|not|none|never|without)\b[^.;]*?"
        r"\b(?:penalt(?:y|ies)|late[\s-]+delivery|delay(?:ed|s)?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:penalt(?:y|ies)|late[\s-]+delivery|delay(?:ed|s)?)\b"
        r"[^.;]*?\b(?:not|never|does\s+not|doesn't|is\s+not|isn't|"
        r"will\s+not|won't)\b",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True)
class Gate2Result:
    """The outcome of Gate 2. Unverified data never leaks downstream."""

    ok: bool
    extraction: Extraction | None
    failures: tuple[str, ...]


def _fail(field: str, reason: str) -> str:
    return f"{field}: {reason}"


def parse_extraction(
    raw_text: str,
) -> tuple[Extraction | None, tuple[str, ...]]:
    """Parse JSON and validate the schema; return (extraction, failures).

    On success the failures tuple is empty. On any JSON/schema failure the
    extraction is None so the caller cannot accidentally use partial data.
    """
    if not isinstance(raw_text, str) or not raw_text.strip():
        return None, ("The response is empty.",)

    text = raw_text.lstrip("\ufeff")
    fence = _CODE_FENCE.match(text)
    if fence:
        text = fence.group(1)
    text = text.strip()
    if not text:
        return None, ("The response is empty.",)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, (
            f"Invalid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno}).",
        )

    if not isinstance(data, dict):
        return None, ("The top-level JSON value must be an object.",)

    failures: list[str] = []

    missing = [name for name in FIELD_NAMES if name not in data]
    extra = sorted(str(key) for key in data if key not in FIELD_NAMES)
    if missing:
        failures.append("Missing JSON field(s): " + ", ".join(missing) + ".")
    if extra:
        failures.append("Unexpected JSON field(s): " + ", ".join(extra) + ".")

    for name in FIELD_NAMES:
        if name not in data:
            continue
        entry = data[name]
        if not isinstance(entry, dict):
            continue
        missing_nested = [
            key for key in _NESTED_FIELD_NAMES if key not in entry
        ]
        if missing_nested:
            failures.append(
                f"{name}: missing nested field(s): "
                + ", ".join(missing_nested)
                + "."
            )

    # Fill omitted fields only so Pydantic can still type-check the values that
    # were supplied. The missing-field failure above still makes the result fail.
    candidate = {
        name: data.get(name, {"value": None, "evidence": None})
        for name in FIELD_NAMES
    }

    extraction: Extraction | None = None
    try:
        extraction = Extraction.model_validate(candidate)
    except ValidationError as exc:
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"]) or "schema"
            failures.append(f"{location}: {error['msg']}")

    if failures:
        return None, tuple(failures)
    return extraction, ()


def check_evidence_present(extraction: Extraction) -> tuple[str, ...]:
    """Every non-null value must carry a non-blank evidence quote."""
    failures: list[str] = []
    for name in FIELD_NAMES:
        field = getattr(extraction, name)
        if field.value is None:
            continue
        if field.evidence is None:
            failures.append(
                _fail(name, "value is present but evidence is missing.")
            )
        elif not field.evidence.strip():
            failures.append(
                _fail(name, "evidence is empty or whitespace-only.")
            )
    return tuple(failures)


def check_evidence_in_text(
    extraction: Extraction, pdf_text: str
) -> tuple[str, ...]:
    """Evidence must appear verbatim (case-sensitive) in normalised PDF text."""
    text = normalise_text(pdf_text or "")
    failures: list[str] = []
    for name in FIELD_NAMES:
        field = getattr(extraction, name)
        if field.value is None:
            continue
        if field.evidence is None or not field.evidence.strip():
            continue
        quote = normalise_text(field.evidence)
        if quote not in text:
            failures.append(
                _fail(
                    name,
                    "evidence quote is not found verbatim in the PDF text.",
                )
            )
    return tuple(failures)


def check_value_in_evidence(extraction: Extraction) -> tuple[str, ...]:
    """Each value must appear in its own quote, with the approved exceptions."""
    failures: list[str] = []
    for name in FIELD_NAMES:
        field = getattr(extraction, name)
        if field.value is None:
            continue
        if field.evidence is None or not field.evidence.strip():
            continue

        value = field.value.strip()
        if not value:
            continue
        quote = normalise_text(field.evidence)

        if name in VALUE_CHECK_EXEMPT_FIELDS:
            if name == "price_includes_vat" and value.lower() == "true":
                if not _vat_included_supported(quote):
                    failures.append(
                        _fail(
                            name,
                            "evidence does not state that VAT or taxes "
                            "are included.",
                        )
                    )
            continue

        if name in ZERO_VALUE_EXEMPT_FIELDS and _is_zero(value):
            if not _zero_advance_supported(quote):
                failures.append(
                    _fail(
                        name,
                        "evidence does not support a zero advance payment.",
                    )
                )
            continue

        if name in _NUMERIC_FIELDS:
            if not _numeric_value_in_quote(value, quote):
                failures.append(
                    _fail(
                        name,
                        f"value '{field.value}' does not appear in its evidence.",
                    )
                )
        elif value.lower() not in quote.lower():
            failures.append(
                _fail(
                    name,
                    f"value '{field.value}' does not appear in its evidence.",
                )
            )

    return tuple(failures)


def check_value_usable(extraction: Extraction) -> tuple[str, ...]:
    """Numeric and enumerated values must be usable by the plain-code steps."""
    failures: list[str] = []
    for name in FIELD_NAMES:
        field = getattr(extraction, name)
        if field.value is None:
            continue

        value = field.value.strip()
        if not value:
            failures.append(
                _fail(name, "value is empty or whitespace-only.")
            )
            continue

        if name in _NUMERIC_FIELDS:
            if name not in _PERCENT_FIELDS and "%" in value:
                failures.append(
                    _fail(
                        name,
                        f"value '{field.value}' is not a usable number.",
                    )
                )
                continue
            if to_decimal(value) is None:
                failures.append(
                    _fail(
                        name,
                        f"value '{field.value}' is not a usable number.",
                    )
                )
        elif name == "delivery_unit":
            if value.lower() not in _DELIVERY_UNITS:
                failures.append(
                    _fail(
                        name,
                        f"value '{field.value}' must be day or week.",
                    )
                )
        elif name == "price_includes_vat":
            if value.lower() not in _VAT_FLAGS:
                failures.append(
                    _fail(
                        name,
                        f"value '{field.value}' must be true or false.",
                    )
                )
        # Every other field is free text; non-blank is enough.

    return tuple(failures)


def check_null_crosscheck(
    extraction: Extraction, pdf_text: str
) -> tuple[str, ...]:
    """Catch important nulls that the document text says should be present."""
    text = normalise_text(pdf_text or "")
    failures: list[str] = []

    if extraction.tin.value is None and _TIN_WORD.search(text):
        failures.append(
            _fail("tin", "the PDF mentions TIN but no value was extracted.")
        )
    if extraction.bin.value is None and _BIN_WORD.search(text):
        failures.append(
            _fail("bin", "the PDF mentions BIN but no value was extracted.")
        )
    if extraction.penalty_pct.value is None and _positive_penalty_details(text):
        failures.append(
            _fail(
                "penalty_pct",
                "the PDF contains positive late-delivery penalty details "
                "but no penalty percentage was extracted.",
            )
        )

    return tuple(failures)


def validate_extraction(raw_text: str, pdf_text: str) -> Gate2Result:
    """Run Gate 2 in a fixed order and collect every applicable failure."""
    extraction, failures = parse_extraction(raw_text)
    if extraction is None:
        return Gate2Result(False, None, failures)

    all_failures = (
        check_evidence_present(extraction)
        + check_evidence_in_text(extraction, pdf_text)
        + check_value_in_evidence(extraction)
        + check_value_usable(extraction)
        + check_null_crosscheck(extraction, pdf_text)
    )

    if all_failures:
        return Gate2Result(False, None, all_failures)
    return Gate2Result(True, extraction, ())


# ---- helpers ---------------------------------------------------------------

def _numeric_value_in_quote(value: str, quote: str) -> bool:
    pattern = (
        _NUMERIC_BOUNDARY_BEFORE + re.escape(value) + _NUMERIC_BOUNDARY_AFTER
    )
    return re.search(pattern, quote) is not None


def _is_zero(value: str) -> bool:
    number = to_decimal(value)
    return number is not None and number == _ZERO


def _split_clauses(text: str) -> tuple[str, ...]:
    return tuple(
        part.strip()
        for part in _CLAUSE_BOUNDARY.split(text)
        if part.strip()
    )


def _zero_advance_supported(quote: str) -> bool:
    text = normalise_text(quote)
    supported = False
    for clause in _split_clauses(text):
        if any(
            pattern.search(clause)
            for pattern in _POSITIVE_ADVANCE_PATTERNS
        ):
            if not any(
                pattern.search(clause)
                for pattern in _ADVANCE_NEGATED_PATTERNS
            ):
                return False
        if any(
            pattern.search(clause) for pattern in _ZERO_ADVANCE_PATTERNS
        ):
            supported = True
    return supported


def _vat_included_supported(quote: str) -> bool:
    text = normalise_text(quote)
    for clause in _split_clauses(text):
        if not _TAX_WORD.search(clause):
            continue
        if any(
            pattern.search(clause) for pattern in _VAT_NEGATED_PATTERNS
        ):
            continue
        if any(
            pattern.search(clause) for pattern in _VAT_INCLUDED_PATTERNS
        ):
            return True
    return False


def _penalty_is_negated(clause: str) -> bool:
    return any(
        pattern.search(clause) for pattern in _NEGATED_PENALTY_PATTERNS
    )


def _positive_penalty_details(text: str) -> bool:
    for clause in _split_clauses(text):
        if not _PENALTY_KEYWORD.search(clause):
            continue
        percentages = _PERCENT.findall(clause)
        if not percentages:
            continue
        if not any(Decimal(value) > _ZERO for value in percentages):
            continue
        if _penalty_is_negated(clause):
            continue
        return True
    return False
