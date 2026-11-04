"""F007: fixed, deterministic LangGraph workflow for one supplier quotation.

LangGraph only wires plain Python steps. The LLM extracts facts; Python validates,
matches, applies every company rule, and makes the final decision.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from src import config
from src.compare import (
    MatchResult,
    PurchaseRequestError,
    PurchaseRequestTable,
    load_purchase_requests,
    match_item,
)
from src.extractor import extract
from src.llm import LLMError
from src.parsing import to_days, to_decimal
from src.pdf_reader import gate_text, read_pdf
from src.rules import Finding, RuleInputs, decide, evaluate
from src.schema import Extraction
from src.validators import validate_extraction


class WorkflowState(TypedDict, total=False):
    pdf_path: str
    csv_path: str
    pdf_text: str
    page_count: int
    raw_text: str
    attempts: int
    failures: tuple[str, ...]
    extraction: Extraction
    purchase_table: PurchaseRequestTable
    match: MatchResult
    rule_inputs: RuleInputs
    findings: list[Finding]
    decision: str
    status: str
    error: str | None


_NUMERIC_FIELDS = (
    "quantity",
    "unit_price",
    "advance_pct",
    "balance_days_min",
    "balance_days_max",
    "line_amount",
    "subtotal",
    "vat_rate",
    "vat_amount",
    "total",
    "penalty_pct",
    "validity_days",
)


def _value(extraction: Extraction, field: str) -> str | None:
    value = getattr(extraction, field).value
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _number(extraction: Extraction, field: str) -> Decimal | None:
    return to_decimal(_value(extraction, field))


def map_extraction_to_rule_inputs(
    extraction: Extraction,
    *,
    max_price: Decimal | None,
    requested_qty: Decimal | None,
) -> RuleInputs:
    """Convert validated extraction strings into Decimal-backed rule inputs.

    Required fields that are absent or cannot be parsed are listed for rule M.
    Optional unparsable numbers become None and are handled by their own rules.
    Purchase-request numbers come only from a successful MatchResult.
    """
    numeric = {
        "quantity": _number(extraction, "quantity"),
        "price": _number(extraction, "unit_price"),
        "advance_pct": _number(extraction, "advance_pct"),
        "balance_days_min": _number(extraction, "balance_days_min"),
        "balance_days_max": _number(extraction, "balance_days_max"),
        "line_amount": _number(extraction, "line_amount"),
        "subtotal": _number(extraction, "subtotal"),
        "vat_rate": _number(extraction, "vat_rate"),
        "vat_amount": _number(extraction, "vat_amount"),
        "total": _number(extraction, "total"),
        "penalty_pct": _number(extraction, "penalty_pct"),
        "validity_days": _number(extraction, "validity_days"),
    }
    delivery_value = _number(extraction, "delivery_value")
    delivery_unit = _value(extraction, "delivery_unit")
    delivery_days = to_days(_value(extraction, "delivery_value"), delivery_unit)

    required_numeric = {
        "quantity": numeric["quantity"],
        "unit_price": numeric["price"],
        "line_amount": numeric["line_amount"],
        "total": numeric["total"],
        "advance_pct": numeric["advance_pct"],
        "delivery_value": delivery_value,
        "validity_days": numeric["validity_days"],
    }
    missing: list[str] = []
    for field in config.REQUIRED_FIELDS:
        source_value = _value(extraction, field)
        if source_value is None:
            missing.append(field)
        elif field in required_numeric and required_numeric[field] is None:
            missing.append(field)

    # A value that is written in the document but cannot be read as a number is
    # not the same as an absent value. Report it for rule M (a person looks at it)
    # instead of letting it vanish. Gate 2 normally stops these before this point.
    for field in _NUMERIC_FIELDS:
        if field not in missing and _value(extraction, field) is not None \
                and _number(extraction, field) is None:
            missing.append(field)
    if (
        delivery_value is not None
        and delivery_unit is not None
        and delivery_days is None
        and "delivery_unit" not in missing
    ):
        missing.append("delivery_unit")  # a unit other than day or week

    return RuleInputs(
        price=numeric["price"],
        max_price=max_price,
        quantity=numeric["quantity"],
        requested_qty=requested_qty,
        advance_pct=numeric["advance_pct"],
        balance_days_min=numeric["balance_days_min"],
        balance_days_max=numeric["balance_days_max"],
        delivery_days=delivery_days,
        penalty_pct=numeric["penalty_pct"],
        penalty_period=_value(extraction, "penalty_period"),
        penalty_basis=_value(extraction, "penalty_basis"),
        tin=_value(extraction, "tin"),
        bin_no=_value(extraction, "bin"),
        vat_rate=numeric["vat_rate"],
        vat_amount=numeric["vat_amount"],
        line_amount=numeric["line_amount"],
        subtotal=numeric["subtotal"],
        total=numeric["total"],
        validity_days=numeric["validity_days"],
        missing_fields=tuple(missing),
    )


def decide_with_match(findings: list[Finding], match: MatchResult) -> str:
    """A failed PR match blocks approval but never hides a rule-based rejection."""
    decision = decide(findings)
    if not match.ok and decision == config.APPROVE:
        return config.NEEDS_CHECK
    return decision


def _read_and_gate(state: WorkflowState) -> dict:
    result = read_pdf(state["pdf_path"])
    if not result.ok:
        return {"status": "error", "error": result.error, "attempts": 0}
    gate_error = gate_text(result.text)
    if gate_error:
        return {"status": "error", "error": gate_error, "attempts": 0}
    return {
        "pdf_text": result.text,
        "page_count": result.page_count,
        "attempts": 0,
        "failures": (),
        "status": "running",
        "error": None,
    }


def _extract(state: WorkflowState) -> dict:
    attempts = state.get("attempts", 0) + 1
    try:
        result = extract(state["pdf_text"], state.get("failures", ()))
    except LLMError as error:
        return {"attempts": attempts, "status": "error", "error": str(error)}
    return {"attempts": attempts, "raw_text": result.text, "error": None}


def _validate(state: WorkflowState) -> dict:
    result = validate_extraction(state["raw_text"], state["pdf_text"])
    if result.ok and result.extraction is not None:
        return {"extraction": result.extraction, "failures": ()}
    return {"extraction": None, "failures": result.failures}


def _compare(state: WorkflowState) -> dict:
    try:
        table = load_purchase_requests(state["csv_path"])
    except PurchaseRequestError as error:
        return {"status": "error", "error": str(error)}
    result = match_item(state["extraction"], table)
    return {"purchase_table": table, "match": result, "error": None}


def _evaluate(state: WorkflowState) -> dict:
    match = state["match"]
    inputs = map_extraction_to_rule_inputs(
        state["extraction"],
        max_price=match.max_price,
        requested_qty=match.requested_qty,
    )
    findings = evaluate(inputs)
    if "penalty_pct" in inputs.missing_fields:
        # The penalty is written but unreadable: that is not "no penalty clause".
        # Rule M already reports it for a person, so drop the false R3 reject.
        findings = [finding for finding in findings if finding.rule_id != "R3"]
    return {"rule_inputs": inputs, "findings": findings}


def _finalize(state: WorkflowState) -> dict:
    return {
        "decision": decide_with_match(state["findings"], state["match"]),
        "status": "completed",
        "error": None,
    }


def _needs_review(state: WorkflowState) -> dict:
    return {
        "decision": config.NEEDS_CHECK,
        "status": "needs_review",
        "error": None,
        "findings": [],
    }


def _after_read(state: WorkflowState) -> str:
    return "end" if state.get("status") == "error" else "extract"


def _after_extract(state: WorkflowState) -> str:
    return "end" if state.get("status") == "error" else "validate"


def _after_validation(state: WorkflowState) -> str:
    if state.get("extraction") is not None:
        return "compare"
    if state.get("attempts", 0) < config.MAX_ATTEMPTS:
        return "extract"
    return "needs_review"


def _after_compare(state: WorkflowState) -> str:
    return "end" if state.get("status") == "error" else "evaluate"


def build_workflow():
    """Build the fixed graph. Nodes are plain functions with explicit routing."""
    graph = StateGraph(WorkflowState)
    graph.add_node("read_pdf", _read_and_gate)
    graph.add_node("extract", _extract)
    graph.add_node("validate", _validate)
    graph.add_node("compare", _compare)
    graph.add_node("evaluate", _evaluate)
    graph.add_node("finalize", _finalize)
    graph.add_node("needs_review", _needs_review)

    graph.add_edge(START, "read_pdf")
    graph.add_conditional_edges("read_pdf", _after_read, {"extract": "extract", "end": END})
    graph.add_conditional_edges("extract", _after_extract, {"validate": "validate", "end": END})
    graph.add_conditional_edges(
        "validate",
        _after_validation,
        {"compare": "compare", "extract": "extract", "needs_review": "needs_review"},
    )
    graph.add_conditional_edges("compare", _after_compare, {"evaluate": "evaluate", "end": END})
    graph.add_edge("evaluate", "finalize")
    graph.add_edge("finalize", END)
    graph.add_edge("needs_review", END)
    return graph.compile()


def run_workflow(pdf_path: str | Path, csv_path: str | Path) -> WorkflowState:
    """Run one supplier PDF through the complete fixed workflow."""
    app = build_workflow()
    return app.invoke({"pdf_path": str(pdf_path), "csv_path": str(csv_path), "attempts": 0})
