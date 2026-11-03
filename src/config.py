"""Every threshold and constant lives here, in ONE place.

Changing a rule during a live review should be a one-line edit in this file.
All numbers are Decimal (never float) so boundaries like exactly 3.00% are exact.
"""
from decimal import Decimal

# ---- decisions (exact strings; tests and expected.json use the same) ----------
APPROVE = "Approve"
REJECT = "Reject"
NEEDS_CHECK = "Needs a person to check"

# ---- reject rules -------------------------------------------------------------
PRICE_REJECT_PCT = Decimal("3")     # R1: unit price more than this % above the max
MAX_ADVANCE_PCT = Decimal("20")     # R2: advance payment above this %
MAX_DELIVERY_DAYS = 45              # R5: delivery later than this

# ---- needs-a-person rules -----------------------------------------------------
QTY_TOLERANCE_PCT = Decimal("5")    # H2: quantity differs by more than this %
PAYMENT_MIN_DAYS = 30               # H3: balance payment window, lower bound
PAYMENT_MAX_DAYS = 60               # H3: balance payment window, upper bound
REVIEW_DELIVERY_FROM_DAYS = 31      # H4: delivery 31..MAX_DELIVERY_DAYS days
VAT_RATE_PCT = Decimal("15")        # H5: VAT must be shown separately at this rate
MIN_VALIDITY_DAYS = 30              # H7: quotation valid for less than this

# ---- extra interpretation findings (documented in NOTE.md) --------------------
MIN_PENALTY_PCT_PER_WEEK = Decimal("0.5")   # N1: penalty below this per week
PENALTY_BASIS = "order value"               # N2: basis must be the order value

# ---- arithmetic ---------------------------------------------------------------
ARITHMETIC_TOLERANCE = Decimal("0.01")      # H6: allowed rounding difference
DAYS_PER_WEEK = 7                           # "4 weeks" -> 28 days, done in Python

# ---- gates and workflow -------------------------------------------------------
MIN_TEXT_CHARS = 100        # Gate 1: less text than this = unreadable PDF, no LLM call
MAX_ATTEMPTS = 2            # 1 first try + at most 1 retry

# ---- rule M: fields that must be present (others have their own rule) ---------
# tin/bin -> R4, penalty_* -> R3/N1/N2, balance_days_* -> H3, subtotal/vat_* -> H5
REQUIRED_FIELDS = (
    "supplier_name",
    "quotation_no",
    "quotation_date",
    "item_code",
    "quantity",
    "unit_price",
    "line_amount",
    "total",
    "advance_pct",
    "delivery_value",
    "delivery_unit",
    "validity_days",
)
