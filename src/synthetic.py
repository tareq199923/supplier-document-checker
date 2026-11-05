"""F015: deterministic synthetic quotation variants and their evidence writers.

The synthetic cases are fixtures, not a second business pipeline. They build a
plain, English-only quotation from one spec, derive every money field with
Decimal, render one line per fact, and write a small PDF with PyMuPDF. The
oracle extraction uses the same spec so Gate 2 and the real workflow can prove
that each fixture is extractable before a live model is asked to read it.

This module never writes the F011 evidence files and never calls
evaluation.write_artifacts.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pymupdf

from src import evaluation
from src.config import APPROVE, NEEDS_CHECK, REJECT, REQUIRED_FIELDS
from src.extractor import PROMPT_VERSION
from src.parsing import to_decimal
from src.result import CheckResult, check_supplier
from src.rules import RULE_FIELDS
from src.schema import FIELD_NAMES

ROOT = Path(__file__).resolve().parent.parent
CASES_PATH = ROOT / "tests" / "synthetic_cases.json"
EVALUATION_DIR = ROOT / "reports" / "evaluation"
PAUSE_SECONDS = evaluation.PAUSE_SECONDS

_CENT = Decimal("0.01")
_HUNDRED = Decimal("100")
_DECISIONS = (APPROVE, REJECT, NEEDS_CHECK)
_CASE_ID = re.compile(r"^S\d{2}$")

_SPEC_KEYS = (
    "supplier_name",
    "quotation_no",
    "quotation_date",
    "pr_reference",
    "item_code",
    "description",
    "quantity",
    "unit",
    "unit_price",
    "price_includes_vat",
    "advance_pct",
    "advance_zero_wording",
    "balance_days_min",
    "balance_days_max",
    "delivery_value",
    "delivery_unit",
    "penalty_pct",
    "penalty_period",
    "penalty_basis",
    "validity_days",
    "vat_rate",
    "tin",
    "bin",
    "line_amount_delta",
    "injection_note",
)


class SyntheticError(Exception):
    """The synthetic case file or one case spec is not usable."""


@dataclass(frozen=True)
class Money:
    line_amount: Decimal
    subtotal: Decimal
    vat_amount: Decimal
    total: Decimal


@dataclass(frozen=True)
class SyntheticSpec:
    supplier_name: str | None
    quotation_no: str | None
    quotation_date: str
    pr_reference: str
    item_code: str
    description: str
    quantity: Decimal
    unit: str
    unit_price: Decimal
    price_includes_vat: str | None
    advance_pct: Decimal | None
    advance_zero_wording: bool
    balance_days_min: Decimal
    balance_days_max: Decimal
    delivery_value: Decimal
    delivery_unit: str
    penalty_pct: Decimal | None
    penalty_period: str | None
    penalty_basis: str | None
    validity_days: Decimal
    vat_rate: Decimal
    tin: str | None
    bin: str | None
    line_amount_delta: Decimal
    injection_note: str | None
    money: Money


@dataclass(frozen=True)
class SyntheticCase:
    case_id: str
    spec: SyntheticSpec
    expected_decision: str
    expected_rule_ids: tuple[str, ...]

    def expected_entry(self) -> dict:
        return {
            "decision": self.expected_decision,
            "rule_ids": list(self.expected_rule_ids),
        }


# ---- spec loading ---------------------------------------------------------------
def _text(mapping: dict, key: str, *, optional: bool = False) -> str | None:
    value = mapping.get(key)
    if value is None:
        if optional:
            return None
        raise SyntheticError(f"{key}: a value is required")
    if not isinstance(value, str):
        raise SyntheticError(f"{key}: expected a string")
    cleaned = value.strip()
    if not cleaned:
        if optional:
            return None
        raise SyntheticError(f"{key}: a value is required")
    return cleaned


def _decimal(mapping: dict, key: str, *, optional: bool = False) -> Decimal | None:
    raw = mapping.get(key)
    if raw is None:
        if optional:
            return None
        raise SyntheticError(f"{key}: a number is required")
    if isinstance(raw, bool):
        raise SyntheticError(f"{key}: expected a number, not a boolean")
    number = to_decimal(str(raw))
    if number is None:
        raise SyntheticError(f"{key}: '{raw}' is not a usable number")
    return number


def _flag(mapping: dict, key: str, *, default: bool = False) -> bool:
    value = mapping.get(key, default)
    if not isinstance(value, bool):
        raise SyntheticError(f"{key}: expected true or false")
    return value


def derive_money(
    quantity: Decimal,
    unit_price: Decimal,
    vat_rate: Decimal,
    line_amount_delta: Decimal = Decimal("0"),
) -> Money:
    """Derive every money field from the intended inputs, with Decimal only."""
    line = (quantity * unit_price + line_amount_delta).quantize(
        _CENT, rounding=ROUND_HALF_UP
    )
    subtotal = line
    vat = (subtotal * vat_rate / _HUNDRED).quantize(
        _CENT, rounding=ROUND_HALF_UP
    )
    total = (subtotal + vat).quantize(_CENT, rounding=ROUND_HALF_UP)
    return Money(
        line_amount=line,
        subtotal=subtotal,
        vat_amount=vat,
        total=total,
    )


def _build_spec(mapping: dict, case_id: str) -> SyntheticSpec:
    quantity = _decimal(mapping, "quantity")
    unit_price = _decimal(mapping, "unit_price")
    vat_rate = _decimal(mapping, "vat_rate")
    balance_min = _decimal(mapping, "balance_days_min")
    balance_max = _decimal(mapping, "balance_days_max")
    delivery_value = _decimal(mapping, "delivery_value")
    validity_days = _decimal(mapping, "validity_days")
    delta = _decimal(mapping, "line_amount_delta", optional=True) or Decimal("0")
    advance_pct = _decimal(mapping, "advance_pct", optional=True)
    penalty_pct = _decimal(mapping, "penalty_pct", optional=True)
    zero_wording = _flag(mapping, "advance_zero_wording")

    if zero_wording and (advance_pct is None or advance_pct != Decimal("0")):
        raise SyntheticError(
            f"{case_id}: advance_zero_wording needs advance_pct = 0"
        )

    delivery_raw = _text(mapping, "delivery_unit")
    lowered = delivery_raw.lower()
    if lowered.startswith("week"):
        delivery_unit = "week"
    elif lowered.startswith("day"):
        delivery_unit = "day"
    else:
        raise SyntheticError(
            f"{case_id}: delivery_unit must be day or week, got {delivery_raw!r}"
        )

    money = derive_money(quantity, unit_price, vat_rate, delta)
    return SyntheticSpec(
        supplier_name=_text(mapping, "supplier_name", optional=True),
        quotation_no=_text(mapping, "quotation_no", optional=True),
        quotation_date=_text(mapping, "quotation_date") or "",
        pr_reference=_text(mapping, "pr_reference") or "",
        item_code=_text(mapping, "item_code") or "",
        description=_text(mapping, "description") or "",
        quantity=quantity,
        unit=_text(mapping, "unit") or "",
        unit_price=unit_price,
        price_includes_vat=_text(mapping, "price_includes_vat", optional=True),
        advance_pct=advance_pct,
        advance_zero_wording=zero_wording,
        balance_days_min=balance_min,
        balance_days_max=balance_max,
        delivery_value=delivery_value,
        delivery_unit=delivery_unit,
        penalty_pct=penalty_pct,
        penalty_period=_text(mapping, "penalty_period", optional=True),
        penalty_basis=_text(mapping, "penalty_basis", optional=True),
        validity_days=validity_days,
        vat_rate=vat_rate,
        tin=_text(mapping, "tin", optional=True),
        bin=_text(mapping, "bin", optional=True),
        line_amount_delta=delta,
        injection_note=_text(mapping, "injection_note", optional=True),
        money=money,
    )


def load_synthetic_cases(path: object = CASES_PATH) -> tuple[SyntheticCase, ...]:
    """Read and validate the 20 case specs. Never reads expected.json."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SyntheticError(
            f"synthetic case file cannot be read: {type(exc).__name__}"
        ) from exc

    if not isinstance(data, dict):
        raise SyntheticError("synthetic case file must contain an object")
    base = data.get("base")
    entries = data.get("cases")
    if not isinstance(base, dict):
        raise SyntheticError("synthetic case file has no base object")
    unknown_base = sorted(set(base) - set(_SPEC_KEYS))
    if unknown_base:
        raise SyntheticError(
            "base has unknown field(s): " + ", ".join(unknown_base)
        )
    if not isinstance(entries, list) or not entries:
        raise SyntheticError("synthetic case file has no cases list")

    cases: list[SyntheticCase] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise SyntheticError("each synthetic case must be an object")
        unknown_entry = sorted(set(entry) - {"id", "expected", "changes"})
        if unknown_entry:
            raise SyntheticError(
                "case has unknown field(s): " + ", ".join(unknown_entry)
            )
        case_id = entry.get("id")
        if not isinstance(case_id, str) or not _CASE_ID.match(case_id):
            raise SyntheticError(f"case id is not S01..S99: {case_id!r}")
        if case_id in seen:
            raise SyntheticError(f"duplicate case id: {case_id}")
        seen.add(case_id)

        changes = entry.get("changes", {})
        if not isinstance(changes, dict):
            raise SyntheticError(f"{case_id}: changes must be an object")
        unknown_changes = sorted(set(changes) - set(_SPEC_KEYS))
        if unknown_changes:
            raise SyntheticError(
                f"{case_id}: unknown change(s): " + ", ".join(unknown_changes)
            )
        merged = dict(base)
        merged.update(changes)

        expected = entry.get("expected")
        if not isinstance(expected, dict):
            raise SyntheticError(f"{case_id}: expected must be an object")
        decision = expected.get("decision")
        if decision not in _DECISIONS:
            raise SyntheticError(f"{case_id}: expected decision is invalid")
        rule_ids = expected.get("rule_ids")
        if not isinstance(rule_ids, list) or any(
            not isinstance(rule, str) for rule in rule_ids
        ):
            raise SyntheticError(f"{case_id}: expected rule_ids must be a list")
        if len(set(rule_ids)) != len(rule_ids):
            raise SyntheticError(f"{case_id}: duplicate expected rule id")
        unknown_rules = sorted(set(rule_ids) - set(RULE_FIELDS))
        if unknown_rules:
            raise SyntheticError(
                f"{case_id}: unknown rule(s): " + ", ".join(unknown_rules)
            )

        cases.append(
            SyntheticCase(
                case_id=case_id,
                spec=_build_spec(merged, case_id),
                expected_decision=str(decision),
                expected_rule_ids=tuple(sorted(rule_ids)),
            )
        )
    return tuple(cases)


# ---- text helpers ---------------------------------------------------------------
def _trim(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _money(value: Decimal) -> str:
    return f"{value:,.2f}"


def _quantity(value: Decimal) -> str:
    if value == value.to_integral_value():
        return f"{value:,.0f}"
    return f"{value:,f}"


def _supplier_line(spec: SyntheticSpec) -> str | None:
    if spec.supplier_name is None:
        return None
    return f"{spec.supplier_name} QUOTATION"


def _quotation_line(spec: SyntheticSpec) -> str | None:
    if spec.quotation_no is None:
        return None
    return f"Quotation no. {spec.quotation_no}"


def _date_line(spec: SyntheticSpec) -> str:
    return f"Date {spec.quotation_date}"


def _reference_line(spec: SyntheticSpec) -> str:
    return f"Reference {spec.pr_reference}"


def _item_row(spec: SyntheticSpec) -> str:
    return (
        f"{spec.item_code} {spec.description} {_quantity(spec.quantity)} "
        f"{spec.unit} {_money(spec.unit_price)} {_money(spec.money.line_amount)}"
    )


def _subtotal_line(spec: SyntheticSpec) -> str:
    return f"Subtotal {_money(spec.money.subtotal)}"


def _vat_line(spec: SyntheticSpec) -> str:
    return f"VAT {_trim(spec.vat_rate)}% {_money(spec.money.vat_amount)}"


def _total_line(spec: SyntheticSpec) -> str:
    return f"Total {_money(spec.money.total)}"


def _registration_line(spec: SyntheticSpec) -> str | None:
    parts: list[str] = []
    if spec.tin is not None:
        parts.append(f"Supplier TIN: {spec.tin}")
    if spec.bin is not None:
        parts.append(f"BIN: {spec.bin}")
    return " ".join(parts) if parts else None


def _advance_sentence(spec: SyntheticSpec) -> str:
    if spec.advance_zero_wording:
        return "No advance payment required."
    if spec.advance_pct is None:
        return "Advance payment is not stated."
    return f"{_trim(spec.advance_pct)}% advance payable with the purchase order."


def _balance_sentence(spec: SyntheticSpec) -> str:
    if spec.balance_days_min == spec.balance_days_max:
        return (
            f"Balance payable within {_trim(spec.balance_days_min)} days after "
            "delivery and acceptance of goods."
        )
    return (
        f"Balance payable between {_trim(spec.balance_days_min)} and "
        f"{_trim(spec.balance_days_max)} days after delivery and acceptance of goods."
    )


def _payment_lines(spec: SyntheticSpec) -> tuple[str, ...]:
    return (
        "1. Payment.",
        _advance_sentence(spec),
        _balance_sentence(spec),
    )


def _delivery_sentence(spec: SyntheticSpec) -> str:
    value = _trim(spec.delivery_value)
    unit = "week" if spec.delivery_unit.startswith("week") else "day"
    if spec.delivery_value != Decimal("1"):
        unit += "s"
    return f"Delivery within {value} {unit} of receiving the purchase order."


def _delivery_line(spec: SyntheticSpec) -> str:
    return f"2. Delivery. {_delivery_sentence(spec)}"


def _penalty_sentence(spec: SyntheticSpec) -> str:
    period = spec.penalty_period or "week"
    basis = spec.penalty_basis
    if basis:
        return (
            f"Late delivery penalty: {_trim(spec.penalty_pct)}% of {basis} "
            f"for each full {period} of delay."
        )
    return (
        f"Late delivery penalty: {_trim(spec.penalty_pct)}% per {period} of delay."
    )


def _penalty_line(spec: SyntheticSpec) -> str | None:
    if spec.penalty_pct is None:
        return None
    return f"3. Delay. {_penalty_sentence(spec)}"


def _validity_sentence(spec: SyntheticSpec) -> str:
    return (
        f"This quotation is valid for {_trim(spec.validity_days)} days from "
        "the date above."
    )


def _validity_line(spec: SyntheticSpec) -> str:
    return f"4. Validity. {_validity_sentence(spec)}"


def render_text(case: SyntheticCase) -> str:
    """Render deterministic, plain-line, English-only quotation text."""
    spec = case.spec
    lines: list[str] = []
    supplier = _supplier_line(spec)
    if supplier is not None:
        lines.append(supplier)
    quotation = _quotation_line(spec)
    if quotation is not None:
        lines.append(quotation)
    lines += [
        _date_line(spec),
        _reference_line(spec),
        "Item Description Qty Unit Unit price (BDT) Amount (BDT)",
        _item_row(spec),
        _subtotal_line(spec),
        _vat_line(spec),
        _total_line(spec),
    ]
    if (spec.price_includes_vat or "").strip().lower() == "true":
        lines.append("Prices include VAT.")
    registration = _registration_line(spec)
    if registration is not None:
        lines.append(registration)
    lines += [
        "TERMS AND CONDITIONS",
        *_payment_lines(spec),
        _delivery_line(spec),
    ]
    penalty = _penalty_line(spec)
    if penalty is not None:
        lines.append(penalty)
    lines += [
        _validity_line(spec),
        "5. Quality. Goods are supplied as per the buyer's approved sample.",
        "Defective goods will be replaced at the supplier's cost.",
        "6. Disputes. Any dispute will first be settled by discussion;",
        "failing that, through arbitration in Dhaka.",
    ]
    if spec.injection_note is not None:
        lines.append(spec.injection_note)
    if spec.supplier_name is not None:
        lines.append(
            f"For {spec.supplier_name} Authorised signatory "
            "(fictional synthetic document)"
        )
    return "\n".join(lines)


# ---- oracle extraction ----------------------------------------------------------
def _field(value: str | None, evidence: str | None) -> dict[str, str | None]:
    return {"value": value, "evidence": evidence}


def extraction_payload(case: SyntheticCase) -> dict[str, dict[str, str | None]]:
    """The exact JSON the model should return for this case, with single-line quotes."""
    spec = case.spec
    money = spec.money
    item = _item_row(spec)
    subtotal = _subtotal_line(spec)
    vat = _vat_line(spec)
    total = _total_line(spec)
    registration = _registration_line(spec)
    advance = _advance_sentence(spec)
    balance = _balance_sentence(spec)
    delivery = _delivery_sentence(spec)
    penalty = _penalty_sentence(spec) if spec.penalty_pct is not None else None
    validity = _validity_sentence(spec)
    supplier = _supplier_line(spec)
    quotation = _quotation_line(spec)
    date_line = _date_line(spec)
    reference = _reference_line(spec)
    if spec.price_includes_vat is None:
        price_evidence = None
    elif spec.price_includes_vat.strip().lower() == "true":
        price_evidence = "Prices include VAT."
    else:
        # "false" means VAT is extra or listed separately, so cite that line.
        price_evidence = vat

    fields = {
        "supplier_name": _field(spec.supplier_name, supplier),
        "quotation_no": _field(spec.quotation_no, quotation),
        "quotation_date": _field(spec.quotation_date, date_line),
        "pr_reference": _field(spec.pr_reference, reference),
        "item_code": _field(spec.item_code, item),
        "description": _field(spec.description, item),
        "quantity": _field(_quantity(spec.quantity), item),
        "unit": _field(spec.unit, item),
        "unit_price": _field(_money(spec.unit_price), item),
        "price_includes_vat": _field(
            spec.price_includes_vat,
            price_evidence,
        ),
        "line_amount": _field(_money(money.line_amount), item),
        "subtotal": _field(_money(money.subtotal), subtotal),
        "vat_rate": _field(
            f"{_trim(spec.vat_rate)}%" if spec.vat_rate is not None else None,
            vat if spec.vat_rate is not None else None,
        ),
        "vat_amount": _field(_money(money.vat_amount), vat),
        "total": _field(_money(money.total), total),
        "tin": _field(spec.tin, registration if spec.tin is not None else None),
        "bin": _field(spec.bin, registration if spec.bin is not None else None),
        "advance_pct": _field(
            _trim(spec.advance_pct) if spec.advance_pct is not None else None,
            advance if spec.advance_pct is not None else None,
        ),
        "balance_days_min": _field(_trim(spec.balance_days_min), balance),
        "balance_days_max": _field(_trim(spec.balance_days_max), balance),
        "delivery_value": _field(_trim(spec.delivery_value), delivery),
        "delivery_unit": _field(spec.delivery_unit, delivery),
        "penalty_pct": _field(
            _trim(spec.penalty_pct) if spec.penalty_pct is not None else None,
            penalty,
        ),
        "penalty_period": _field(
            spec.penalty_period if spec.penalty_pct is not None else None,
            penalty,
        ),
        "penalty_basis": _field(
            spec.penalty_basis if spec.penalty_pct is not None else None,
            penalty,
        ),
        "validity_days": _field(_trim(spec.validity_days), validity),
    }
    return {name: fields[name] for name in FIELD_NAMES}


# ---- PDF writer -----------------------------------------------------------------
def write_pdf(case: SyntheticCase, path: object) -> Path:
    """Write one deterministic-text PDF. PyMuPDF metadata may vary; text does not."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = render_text(case)
    document = pymupdf.open()
    try:
        page = document.new_page(width=595, height=842)
        y = 60.0
        usable_width = 500.0
        for line in text.splitlines():
            if pymupdf.get_text_length(line, fontname="helv", fontsize=9.5) > usable_width:
                raise SyntheticError(
                    f"{case.case_id}: rendered line is too wide for the PDF: {line!r}"
                )
            if y > 790.0:
                page = document.new_page(width=595, height=842)
                y = 60.0
            page.insert_text((50.0, y), line, fontname="helv", fontsize=9.5)
            y += 13.0
        document.save(str(destination))
    finally:
        document.close()
    return destination


def write_pdfs(
    cases: tuple[SyntheticCase, ...] | list[SyntheticCase], directory: object
) -> dict[str, Path]:
    """Write one PDF per case into a caller-owned temporary directory."""
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    return {
        case.case_id: write_pdf(case, folder / f"{case.case_id}.pdf")
        for case in cases
    }


# ---- assessment and runner ------------------------------------------------------
def assess_synthetic(
    case: SyntheticCase,
    result: CheckResult,
    *,
    duration_s: float = 0.0,
    pdf_path: object | None = None,
) -> evaluation.PackAssessment:
    """Use F011's assess() so the synthetic pass rule is identical."""
    path = Path(pdf_path) if pdf_path is not None else Path(f"{case.case_id}.pdf")
    return evaluation.assess(
        evaluation.PackCase(pack=case.case_id, pdf_path=path),
        result,
        case.expected_entry(),
        duration_s=duration_s,
    )


def _display_path(path: object) -> str:
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except (OSError, ValueError):
        return str(path)


def run_synthetic_evaluation(
    cases: tuple[SyntheticCase, ...] | list[SyntheticCase],
    pdf_paths: dict[str, Path],
    *,
    check_fn=check_supplier,
    purchase_request_path: object = evaluation.PURCHASE_REQUEST_PATH,
    pause_s: float = PAUSE_SECONDS,
    now: datetime | None = None,
    git_state: tuple[str, bool | None] | None = None,
) -> evaluation.EvaluationRun:
    """Run the real checker over synthetic PDFs, one assessment per case."""
    missing = [case.case_id for case in cases if case.case_id not in pdf_paths]
    if missing:
        raise SyntheticError("no PDF path for case(s): " + ", ".join(missing))

    commit, dirty = git_state if git_state is not None else evaluation.git_state()
    provider = (os.getenv("LLM_PROVIDER") or "").strip().lower() or "unknown"
    model = (os.getenv("LLM_MODEL") or "").strip() or "unknown"
    run_at = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")

    assessments: list[evaluation.PackAssessment] = []
    for index, case in enumerate(cases):
        pdf_path = Path(pdf_paths[case.case_id])
        pc = evaluation.PackCase(pack=case.case_id, pdf_path=pdf_path)
        start = time.perf_counter()
        try:
            result = check_fn(pdf_path, purchase_request_path)
        except Exception as exc:  # harness bug: record it, keep the other cases
            assessments.append(
                evaluation._harness_assessment(
                    pc,
                    case.expected_entry(),
                    exc,
                    time.perf_counter() - start,
                )
            )
        else:
            assessments.append(
                evaluation.assess(
                    pc,
                    result,
                    case.expected_entry(),
                    duration_s=time.perf_counter() - start,
                )
            )
        if pause_s > 0 and index < len(cases) - 1:
            time.sleep(pause_s)

    return evaluation.EvaluationRun(
        run_at=run_at,
        provider=provider,
        model=model,
        prompt_version=PROMPT_VERSION,
        git_commit=commit,
        git_dirty=dirty,
        expected_source=_display_path(CASES_PATH),
        purchase_request=_display_path(purchase_request_path),
        assessments=tuple(assessments),
    )


# ---- own F015 payload and Markdown ---------------------------------------------
def _rule_text(rule_ids: tuple[str, ...]) -> str:
    return ", ".join(rule_ids) if rule_ids else "-"


def _case_payload(item: evaluation.PackAssessment) -> dict:
    return {
        "case": item.pack,
        "pdf": item.pdf_name,
        "expected": {
            "supplier": item.expected_supplier,
            "decision": item.expected_decision,
            "rule_ids": list(item.expected_rule_ids),
        },
        "actual": {
            "supplier": item.actual_supplier,
            "decision": item.actual_decision,
            "rule_ids": list(item.actual_rule_ids),
            "status": item.status,
            "attempts": item.attempts,
            "match_ok": item.match_ok,
            "reason_codes": list(item.reason_codes),
            "error": (
                None
                if item.error_code is None
                else {
                    "code": item.error_code,
                    "message": item.error_message,
                    "stage": item.error_stage,
                    "retryable": item.error_retryable,
                }
            ),
        },
        "outcome": item.outcome,
        "passed": item.passed,
        "duration_s": round(item.duration_s, 3),
        "problems": list(item.problems),
    }


def run_payload(run: evaluation.EvaluationRun) -> dict:
    """JSON-safe F015 audit record. No evidence quotes or raw document text."""
    return {
        "feature": "F015",
        "kind": "synthetic",
        "run_at": run.run_at,
        "provider": run.provider,
        "model": run.model,
        "prompt_version": run.prompt_version,
        "git_commit": run.git_commit,
        "git_dirty": run.git_dirty,
        "expected_source": run.expected_source,
        "purchase_request": run.purchase_request,
        "totals": {
            "total": run.total,
            "passed": run.passed,
            "failed": run.failed,
        },
        "outcomes": run.counts_by_outcome(),
        "cases": [_case_payload(item) for item in run.assessments],
    }


def render_summary(run: evaluation.EvaluationRun) -> str:
    """Plain Markdown summary of one F015 synthetic run."""
    if run.git_dirty is True:
        git_note = " (working tree dirty)"
    elif run.git_dirty is False:
        git_note = " (working tree clean)"
    else:
        git_note = " (git state unknown)"
    counts = run.counts_by_outcome()
    outcome_line = ", ".join(
        f"{name}: {count}" for name, count in counts.items() if count
    ) or "none"

    lines = [
        "# F015 synthetic quotation evaluation",
        "",
        f"- Run at: {run.run_at}",
        f"- Provider: {run.provider}",
        f"- Model: {run.model}",
        f"- Prompt version: {run.prompt_version}",
        f"- Git commit: {run.git_commit}{git_note}",
        f"- Purchase request: {run.purchase_request}",
        f"- Expected source: {run.expected_source}",
        f"- Outcomes: {outcome_line}",
        "",
        f"**Result: {run.passed}/{run.total} synthetic cases matched their expected decision and rule IDs.**",
        "",
        "| Case | Expected | Actual | Rules (expected -> actual) | Status | Calls | Seconds | Outcome |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for item in run.assessments:
        actual = item.actual_decision if item.actual_decision is not None else "No decision"
        rules = f"{_rule_text(item.expected_rule_ids)} -> {_rule_text(item.actual_rule_ids)}"
        lines.append(
            f"| {item.pack} | {item.expected_decision} | {actual} | {rules} "
            f"| {item.status} | {item.attempts} | {item.duration_s:.1f} | {item.outcome} |"
        )

    if run.failed:
        lines += ["", "## Failed cases", ""]
        for item in run.assessments:
            if item.passed:
                continue
            lines.append(f"### Case {item.pack} - {item.outcome}")
            lines.append("")
            for problem in item.problems:
                lines.append(f"- {problem}")
            if item.reason_codes:
                lines.append(f"- reasons: {', '.join(item.reason_codes)}")
            lines.append("")
    else:
        lines += ["", "All synthetic cases matched their expected decision and rule IDs.", ""]

    lines += [
        "Model calls are the official `CheckResult.attempts` count, as far as known.",
        f"The prompt is frozen at `{run.prompt_version}`; no expected.json or rule was changed for this run.",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def write_artifacts(
    run: evaluation.EvaluationRun, out_dir: object = EVALUATION_DIR
) -> tuple[Path, Path]:
    """Write the F015 synthetic JSON and Markdown evidence files."""
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "f015-synthetic.json"
    md_path = directory / "f015-synthetic.md"
    json_path.write_text(
        json.dumps(run_payload(run), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(render_summary(run), encoding="utf-8")
    return json_path, md_path
