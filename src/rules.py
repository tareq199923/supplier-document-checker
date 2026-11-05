"""The company rule engine: plain code, deterministic, never an LLM.

Every rule is a small pure function that returns a Finding or None. evaluate()
runs ALL of them and collects every finding; decide() maps the list to one of
the three exact decision strings. All thresholds live in src/config.py.

A missing value (None) never triggers a rule by itself; rule M reports fields
that are absent. R3, R4, H3 and H5 are the documented exceptions: a missing
penalty clause, TIN/BIN, payment window or VAT line is itself the finding.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from src import config

_ONE = Decimal(1)
_HUNDRED = Decimal(100)


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: str          # "reject" or "review"
    message: str
    evidence: tuple[str, ...] | None = None


@dataclass(frozen=True)
class RuleInputs:
    price: Decimal | None = None
    max_price: Decimal | None = None
    quantity: Decimal | None = None
    requested_qty: Decimal | None = None
    advance_pct: Decimal | None = None
    balance_days_min: Decimal | None = None
    balance_days_max: Decimal | None = None
    delivery_days: Decimal | None = None
    penalty_pct: Decimal | None = None
    penalty_period: str | None = None
    penalty_basis: str | None = None
    tin: str | None = None
    bin_no: str | None = None
    vat_rate: Decimal | None = None
    vat_amount: Decimal | None = None
    line_amount: Decimal | None = None
    subtotal: Decimal | None = None
    total: Decimal | None = None
    validity_days: Decimal | None = None
    missing_fields: tuple[str, ...] = ()


# ---- formatting helpers (messages only; no calculation happens here) ----------
def _money(value: Decimal) -> str:
    """Decimal('320000') -> '320,000.00'."""
    return f"{value:,.2f}"


def _trimmed(value: Decimal) -> str:
    """Decimal('0.50') -> '0.5'; Decimal('31.00') -> '31'."""
    return format(value.normalize(), "f")


def _count(value: Decimal) -> str:
    """Decimal('16500') -> '16,500'; Decimal('2.5') -> '2.5'."""
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _percent(value: Decimal) -> str:
    return _trimmed(value)


# ---- reject rules -------------------------------------------------------------
def check_r1(price: Decimal | None, max_price: Decimal | None) -> Finding | None:
    if price is None or max_price is None:
        return None
    limit = max_price * (_ONE + config.PRICE_REJECT_PCT / _HUNDRED)
    if price > limit:
        return Finding(
            "R1",
            "reject",
            f"Unit price {_money(price)} is more than "
            f"{_percent(config.PRICE_REJECT_PCT)}% above the maximum "
            f"{_money(max_price)}.",
        )
    return None


def check_r2(advance_pct: Decimal | None) -> Finding | None:
    if advance_pct is None or advance_pct <= config.MAX_ADVANCE_PCT:
        return None
    return Finding(
        "R2",
        "reject",
        f"Advance payment {_percent(advance_pct)}% is above the maximum "
        f"{_percent(config.MAX_ADVANCE_PCT)}%.",
    )


def check_r3(penalty_pct: Decimal | None) -> Finding | None:
    if penalty_pct is not None:
        return None
    return Finding(
        "R3",
        "reject",
        "The document states no late-delivery penalty clause.",
    )


def check_r4(tin: str | None, bin_no: str | None) -> Finding | None:
    if tin is not None and bin_no is not None:
        return None
    if tin is None and bin_no is None:
        message = "Supplier TIN and BIN are both missing."
    elif tin is None:
        message = "Supplier TIN is missing."
    else:
        message = "Supplier BIN is missing."
    return Finding("R4", "reject", message)


def check_r5(days: Decimal | None) -> Finding | None:
    if days is None or days <= config.MAX_DELIVERY_DAYS:
        return None
    return Finding(
        "R5",
        "reject",
        f"Delivery in {_trimmed(days)} days is later than the maximum "
        f"{config.MAX_DELIVERY_DAYS} days.",
    )


# ---- needs-a-person rules -----------------------------------------------------
def check_h1(price: Decimal | None, max_price: Decimal | None) -> Finding | None:
    if price is None or max_price is None:
        return None
    limit = max_price * (_ONE + config.PRICE_REJECT_PCT / _HUNDRED)
    if max_price < price <= limit:
        return Finding(
            "H1",
            "review",
            f"Unit price {_money(price)} is above the maximum "
            f"{_money(max_price)}, but by no more than "
            f"{_percent(config.PRICE_REJECT_PCT)}%.",
        )
    return None


def check_h2(
    quantity: Decimal | None, requested_qty: Decimal | None
) -> Finding | None:
    if quantity is None or requested_qty is None or requested_qty == 0:
        return None
    difference = abs(quantity - requested_qty) / requested_qty * _HUNDRED
    if difference > config.QTY_TOLERANCE_PCT:
        return Finding(
            "H2",
            "review",
            f"Quantity {_count(quantity)} differs from the requested "
            f"{_count(requested_qty)} by {difference:.2f}%, more than the "
            f"{_percent(config.QTY_TOLERANCE_PCT)}% tolerance.",
        )
    return None


def check_h3(
    balance_days_min: Decimal | None, balance_days_max: Decimal | None
) -> Finding | None:
    window = (
        f"{config.PAYMENT_MIN_DAYS} and {config.PAYMENT_MAX_DAYS} days"
    )
    if balance_days_min is None or balance_days_max is None:
        return Finding(
            "H3",
            "review",
            "Balance payment days are missing or incomplete (a single value "
            f"between {window} is required).",
        )
    if balance_days_min != balance_days_max:
        return Finding(
            "H3",
            "review",
            f"Balance payment window {_trimmed(balance_days_min)} to "
            f"{_trimmed(balance_days_max)} days is unclear (a single value "
            f"between {window} is required).",
        )
    outside = not (
        config.PAYMENT_MIN_DAYS
        <= balance_days_min
        <= config.PAYMENT_MAX_DAYS
    )
    if outside:
        return Finding(
            "H3",
            "review",
            f"Balance payment of {_trimmed(balance_days_min)} days is outside "
            f"the allowed {window}.",
        )
    return None


def check_h4(days: Decimal | None) -> Finding | None:
    if days is None:
        return None
    if config.REVIEW_DELIVERY_FROM_DAYS <= days <= config.MAX_DELIVERY_DAYS:
        return Finding(
            "H4",
            "review",
            f"Delivery in {_trimmed(days)} days is in the "
            f"{config.REVIEW_DELIVERY_FROM_DAYS} to "
            f"{config.MAX_DELIVERY_DAYS} day review window.",
        )
    return None


def check_h5(vat_rate: Decimal | None, vat_amount: Decimal | None) -> Finding | None:
    if vat_rate is None and vat_amount is None:
        message = "VAT is not shown separately (rate and amount are both missing)."
    elif vat_rate is None:
        message = "VAT rate is missing."
    elif vat_amount is None:
        message = "VAT amount is missing."
    elif vat_rate != config.VAT_RATE_PCT:
        message = (
            f"VAT rate {_percent(vat_rate)}% is not the required "
            f"{_percent(config.VAT_RATE_PCT)}%."
        )
    else:
        return None
    return Finding("H5", "review", message)


def check_h6(
    *,
    quantity: Decimal | None = None,
    price: Decimal | None = None,
    line_amount: Decimal | None = None,
    subtotal: Decimal | None = None,
    vat_rate: Decimal | None = None,
    vat_amount: Decimal | None = None,
    total: Decimal | None = None,
) -> Finding | None:
    tolerance = config.ARITHMETIC_TOLERANCE
    failures: list[str] = []

    if quantity is not None and price is not None and line_amount is not None:
        expected = quantity * price
        if abs(line_amount - expected) > tolerance:
            failures.append(
                f"line amount is {_money(line_amount)} but quantity x unit "
                f"price is {_money(expected)}"
            )

    if line_amount is not None and subtotal is not None:
        if abs(subtotal - line_amount) > tolerance:
            failures.append(
                f"subtotal is {_money(subtotal)} but the line amount is "
                f"{_money(line_amount)}"
            )

    if subtotal is not None and vat_rate is not None and vat_amount is not None:
        expected = subtotal * vat_rate / _HUNDRED
        if abs(vat_amount - expected) > tolerance:
            failures.append(
                f"VAT amount is {_money(vat_amount)} but subtotal x "
                f"{_percent(vat_rate)}% is {_money(expected)}"
            )

    if subtotal is not None and vat_amount is not None and total is not None:
        expected = subtotal + vat_amount
        if abs(total - expected) > tolerance:
            failures.append(
                f"total is {_money(total)} but subtotal + VAT is "
                f"{_money(expected)}"
            )

    if not failures:
        return None
    return Finding(
        "H6",
        "review",
        "The numbers do not add up: " + "; ".join(failures) + ".",
    )


def check_h7(validity_days: Decimal | None) -> Finding | None:
    if validity_days is None or validity_days >= config.MIN_VALIDITY_DAYS:
        return None
    return Finding(
        "H7",
        "review",
        f"The quotation is valid for {_trimmed(validity_days)} days, less "
        f"than the required {config.MIN_VALIDITY_DAYS} days.",
    )


# ---- extra interpretation findings --------------------------------------------
def check_n1(
    penalty_pct: Decimal | None, penalty_period: str | None
) -> Finding | None:
    if penalty_pct is None:
        return None

    period = penalty_period.strip() if penalty_period else ""
    is_week = period.lower().startswith("week")
    below_min = penalty_pct < config.MIN_PENALTY_PCT_PER_WEEK
    if is_week and not below_min:
        return None

    rate = _percent(penalty_pct)
    minimum = _percent(config.MIN_PENALTY_PCT_PER_WEEK)
    if not is_week:
        if period:
            message = (
                f"Late-delivery penalty is {rate}% per {period.lower()}, not "
                f"per week; the {minimum}% per week minimum cannot be "
                "confirmed."
            )
        else:
            message = (
                f"Late-delivery penalty is {rate}% but no period is stated; "
                f"it must be at least {minimum}% per week."
            )
        if below_min:
            message += f" The rate is also below the {minimum}% minimum."
    else:
        message = (
            f"Late-delivery penalty is {rate}% per week, below the "
            f"{minimum}% minimum."
        )
    return Finding("N1", "review", message)


def check_n2(
    penalty_pct: Decimal | None, penalty_basis: str | None
) -> Finding | None:
    if penalty_pct is None:
        return None
    basis = " ".join(penalty_basis.split()) if penalty_basis else ""
    if not basis:
        return Finding(
            "N2",
            "review",
            "The late-delivery penalty basis is not stated; it must be the "
            f"{config.PENALTY_BASIS}.",
        )
    normalised = basis.lower()
    head, separator, tail = normalised.partition(" ")
    if head == "the" and separator:
        normalised = tail  # remove one leading whole word "the" only
    if normalised != config.PENALTY_BASIS.strip().lower():
        return Finding(
            "N2",
            "review",
            f"The late-delivery penalty basis is '{basis}'; it must be the "
            f"{config.PENALTY_BASIS}.",
        )
    return None


# ---- M: other required fields -------------------------------------------------
def check_m(missing_fields: Iterable[str]) -> Finding | None:
    names = [str(name) for name in missing_fields]
    if not names:
        return None
    return Finding(
        "M",
        "review",
        "Required fields are missing: " + ", ".join(names) + ".",
    )


# ---- decision and full evaluation ---------------------------------------------
def decide(findings: Iterable[Finding]) -> str:
    """Any reject wins; then any finding at all; otherwise approve."""
    findings = list(findings)
    if any(finding.severity == "reject" for finding in findings):
        return config.REJECT
    if findings:
        return config.NEEDS_CHECK
    return config.APPROVE


def evaluate(inputs: RuleInputs) -> list[Finding]:
    """Run every rule in a fixed order and return all findings, not just the first."""
    findings = [
        check_r1(inputs.price, inputs.max_price),
        check_r2(inputs.advance_pct),
        check_r3(inputs.penalty_pct),
        check_r4(inputs.tin, inputs.bin_no),
        check_r5(inputs.delivery_days),
        check_h1(inputs.price, inputs.max_price),
        check_h2(inputs.quantity, inputs.requested_qty),
        check_h3(inputs.balance_days_min, inputs.balance_days_max),
        check_h4(inputs.delivery_days),
        check_h5(inputs.vat_rate, inputs.vat_amount),
        check_h6(
            quantity=inputs.quantity,
            price=inputs.price,
            line_amount=inputs.line_amount,
            subtotal=inputs.subtotal,
            vat_rate=inputs.vat_rate,
            vat_amount=inputs.vat_amount,
            total=inputs.total,
        ),
        check_h7(inputs.validity_days),
        check_n1(inputs.penalty_pct, inputs.penalty_period),
        check_n2(inputs.penalty_pct, inputs.penalty_basis),
        check_m(inputs.missing_fields),
    ]
    return [finding for finding in findings if finding is not None]


# ---- report helper: which schema fields support each rule ---------------------
RULE_FIELDS: dict[str, tuple[str, ...]] = {
    "R1": ("unit_price",),
    "R2": ("advance_pct",),
    "R3": ("penalty_pct",),
    "R4": ("tin", "bin"),
    "R5": ("delivery_value", "delivery_unit"),
    "H1": ("unit_price",),
    "H2": ("quantity",),
    "H3": ("balance_days_min", "balance_days_max"),
    "H4": ("delivery_value", "delivery_unit"),
    "H5": ("vat_rate", "vat_amount"),
    "H6": (
        "quantity",
        "unit_price",
        "line_amount",
        "subtotal",
        "vat_rate",
        "vat_amount",
        "total",
    ),
    "H7": ("validity_days",),
    "N1": ("penalty_pct", "penalty_period"),
    "N2": ("penalty_pct", "penalty_basis"),
    "M": config.REQUIRED_FIELDS,
}
