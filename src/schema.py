"""The handoff object between the LLM step and the plain-code steps.

Every extracted value is wrapped with its evidence, and every field may be null.
The LLM returns facts and exact quotes ONLY: no decisions, no calculations.

Values are STRINGS exactly as written in the document ("5,664,000.00", "12,000").
Python parses them into Decimal later. A number sent as a JSON number (not a string)
fails validation on purpose, so the retry can ask for the text as written.
"""
from pydantic import BaseModel, ConfigDict, Field


class Evidenced(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str | None = None      # as written in the document (or "0" / "true" / "false", see below)
    evidence: str | None = None   # exact text copied from the PDF


def _field() -> Evidenced:
    return Field(default_factory=Evidenced)


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # identity
    supplier_name: Evidenced = _field()
    quotation_no: Evidenced = _field()
    quotation_date: Evidenced = _field()
    pr_reference: Evidenced = _field()

    # line item
    item_code: Evidenced = _field()
    description: Evidenced = _field()
    quantity: Evidenced = _field()
    unit: Evidenced = _field()
    unit_price: Evidenced = _field()
    price_includes_vat: Evidenced = _field()   # value "true" / "false"
    line_amount: Evidenced = _field()

    # totals
    subtotal: Evidenced = _field()
    vat_rate: Evidenced = _field()
    vat_amount: Evidenced = _field()
    total: Evidenced = _field()

    # registration
    tin: Evidenced = _field()
    bin: Evidenced = _field()

    # payment: "within 45 days" -> min 45, max 45; "30 to 90 days" -> min 30, max 90
    # Python (not the LLM) decides whether the wording is unclear.
    advance_pct: Evidenced = _field()          # "No advance payment required" -> value "0"
    balance_days_min: Evidenced = _field()
    balance_days_max: Evidenced = _field()

    # delivery: "4 weeks" -> value "4", unit "week" (converted to days in Python)
    delivery_value: Evidenced = _field()
    delivery_unit: Evidenced = _field()        # "day" or "week"

    # penalty
    penalty_pct: Evidenced = _field()
    penalty_period: Evidenced = _field()       # e.g. "week"
    penalty_basis: Evidenced = _field()        # e.g. "order value"; null if not stated

    # validity
    validity_days: Evidenced = _field()


FIELD_NAMES: tuple[str, ...] = tuple(Extraction.model_fields)

# Gate 2, check 4 ("the value appears inside its evidence") cannot apply to values
# that are a yes/no label or a zero for "no advance payment". These are the only
# exceptions; every other value must literally appear in its quote.
VALUE_CHECK_EXEMPT_FIELDS = {"price_includes_vat"}
ZERO_VALUE_EXEMPT_FIELDS = {"advance_pct"}   # exempt only when value == "0"
