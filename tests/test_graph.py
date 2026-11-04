"""F007 workflow boundary tests. No live API calls; mapping stays plain Python."""
import json
from decimal import Decimal as D
from pathlib import Path

import pytest

from src import config
from src.compare import MatchResult, PurchaseRequestRow
from src.rules import Finding, RuleInputs
from src.schema import Evidenced, Extraction

ROOT = Path(__file__).resolve().parent.parent
GOLD = json.loads((ROOT / "tests" / "gold_extractions.json").read_text(encoding="utf-8"))


def extraction(pack="01", **overrides):
    fields = {
        name: Evidenced(value=pair[0], evidence=pair[1])
        for name, pair in GOLD[pack].items()
    }
    fields.update({name: Evidenced(value=value, evidence="test evidence" if value is not None else None)
                   for name, value in overrides.items()})
    return Extraction(**fields)


def test_map_extraction_to_rule_inputs_converts_all_rule_fields():
    from src.graph import map_extraction_to_rule_inputs

    result = map_extraction_to_rule_inputs(
        extraction("01"), max_price=D("480"), requested_qty=D("12000")
    )
    assert isinstance(result, RuleInputs)
    assert result.price == D("472")
    assert result.max_price == D("480")
    assert result.quantity == D("12000")
    assert result.requested_qty == D("12000")
    assert result.advance_pct == D("10")
    assert result.balance_days_min == D("45")
    assert result.balance_days_max == D("45")
    assert result.delivery_days == D("24")  # pack 01 states 24 days
    assert result.penalty_pct == D("1")
    assert result.penalty_period == "week"
    assert result.penalty_basis == "order value"
    assert result.tin == "101458013391"
    assert result.bin_no == "000170141-0101"
    assert result.vat_rate == D("15")
    assert result.vat_amount == D("849600")
    assert result.line_amount == D("5664000")
    assert result.subtotal == D("5664000")
    assert result.total == D("6513600")
    assert result.validity_days == D("45")
    assert result.missing_fields == ()


def test_mapping_preserves_zero_delivery_days():
    from src.graph import map_extraction_to_rule_inputs

    result = map_extraction_to_rule_inputs(
        extraction("01", delivery_value="0", delivery_unit="day"),
        max_price=D("480"), requested_qty=D("12000"),
    )
    assert result.delivery_days == D("0")


def test_mapping_uses_match_numbers_not_extracted_or_guessed_values():
    from src.graph import map_extraction_to_rule_inputs

    result = map_extraction_to_rule_inputs(
        extraction("01"), max_price=None, requested_qty=None
    )
    assert result.max_price is None
    assert result.requested_qty is None


def test_mapping_turns_missing_required_fields_into_rule_m_findings():
    from src.graph import map_extraction_to_rule_inputs

    result = map_extraction_to_rule_inputs(
        extraction("01", supplier_name=None, unit_price=None, delivery_value=None),
        max_price=D("480"), requested_qty=D("12000"),
    )
    assert "supplier_name" in result.missing_fields
    assert "unit_price" in result.missing_fields
    assert "delivery_value" in result.missing_fields
    assert "delivery_unit" not in result.missing_fields


def test_mapping_marks_unparseable_required_numbers_as_missing():
    from src.graph import map_extraction_to_rule_inputs

    result = map_extraction_to_rule_inputs(
        extraction("01", unit_price="not a number", quantity="12,5"),
        max_price=D("480"), requested_qty=D("12000"),
    )
    assert result.price is None
    assert result.quantity is None
    assert "unit_price" in result.missing_fields
    assert "quantity" in result.missing_fields


def test_mapping_does_not_add_optional_missing_fields_to_rule_m():
    from src.graph import map_extraction_to_rule_inputs

    result = map_extraction_to_rule_inputs(
        extraction("01", vat_rate=None, vat_amount=None),
        max_price=D("480"), requested_qty=D("12000"),
    )
    assert result.vat_rate is None and result.vat_amount is None
    assert "vat_rate" not in result.missing_fields
    assert "vat_amount" not in result.missing_fields


def test_mapping_does_not_mutate_the_extraction():
    from src.graph import map_extraction_to_rule_inputs

    item = extraction("01")
    before = item.model_dump()
    map_extraction_to_rule_inputs(item, max_price=D("480"), requested_qty=D("12000"))
    assert item.model_dump() == before


def test_failed_match_changes_only_an_approve_decision_to_needs_check():
    from src.graph import decide_with_match

    failed = MatchResult(problems=("description does not match",))
    assert decide_with_match([], failed) == config.NEEDS_CHECK
    assert decide_with_match([Finding("H1", "review", "review")], failed) == config.NEEDS_CHECK
    assert decide_with_match([Finding("R5", "reject", "reject")], failed) == config.REJECT


def test_successful_match_uses_the_rule_engine_decision():
    from src.graph import decide_with_match

    row = PurchaseRequestRow("PR-9", "A", "Widget", "piece", D("10"), D("5"))
    matched = MatchResult(row=row)
    assert decide_with_match([], matched) == config.APPROVE
    assert decide_with_match([Finding("H1", "review", "review")], matched) == config.NEEDS_CHECK
    assert decide_with_match([Finding("R1", "reject", "reject")], matched) == config.REJECT


def test_workflow_retries_gate2_once_then_completes(monkeypatch, tmp_path):
    import src.graph as graph
    from src.pdf_reader import PdfResult
    from src.validators import Gate2Result

    calls = []
    item = extraction("01")
    monkeypatch.setattr(graph, "read_pdf", lambda path: PdfResult(True, "x" * 120, 1, None))
    monkeypatch.setattr(graph, "extract", lambda text, failures=(): calls.append(tuple(failures)) or type("Raw", (), {"text": "{}"})())
    outcomes = iter([
        Gate2Result(False, None, ("unit_price: quote missing",)),
        Gate2Result(True, item, ()),
    ])
    monkeypatch.setattr(graph, "validate_extraction", lambda raw, text: next(outcomes))

    result = graph.run_workflow("supplier.pdf", ROOT / "samples" / "purchase-request.csv")
    assert result["status"] == "completed"
    assert result["attempts"] == 2
    assert calls == [(), ("unit_price: quote missing",)]
    assert result["decision"] == config.APPROVE


def test_workflow_stops_after_second_gate2_failure(monkeypatch):
    import src.graph as graph
    from src.pdf_reader import PdfResult
    from src.validators import Gate2Result

    calls = []
    monkeypatch.setattr(graph, "read_pdf", lambda path: PdfResult(True, "x" * 120, 1, None))
    monkeypatch.setattr(graph, "extract", lambda text, failures=(): calls.append(tuple(failures)) or type("Raw", (), {"text": "{}"})())
    monkeypatch.setattr(graph, "validate_extraction", lambda raw, text: Gate2Result(False, None, ("bad evidence",)))

    result = graph.run_workflow("supplier.pdf", ROOT / "samples" / "purchase-request.csv")
    assert result["status"] == "needs_review"
    assert result["decision"] == config.NEEDS_CHECK
    assert result["attempts"] == config.MAX_ATTEMPTS == 2
    assert len(calls) == 2


def test_workflow_does_not_call_llm_when_gate1_fails(monkeypatch):
    import src.graph as graph
    from src.pdf_reader import PdfResult

    def should_not_call(*args, **kwargs):
        pytest.fail("LLM must not be called after Gate 1 fails")

    monkeypatch.setattr(graph, "read_pdf", lambda path: PdfResult(False, "", 0, "bad PDF"))
    monkeypatch.setattr(graph, "extract", should_not_call)
    result = graph.run_workflow("bad.pdf", ROOT / "samples" / "purchase-request.csv")
    assert result["status"] == "error"
    assert result["error"] == "bad PDF"
    assert result["attempts"] == 0


def test_workflow_api_error_is_a_clean_error_without_retry(monkeypatch):
    import src.graph as graph
    from src.llm import LLMError
    from src.pdf_reader import PdfResult

    calls = []
    monkeypatch.setattr(graph, "read_pdf", lambda path: PdfResult(True, "x" * 120, 1, None))
    def fail(*args, **kwargs):
        calls.append(1)
        raise LLMError("provider unavailable")
    monkeypatch.setattr(graph, "extract", fail)
    result = graph.run_workflow("supplier.pdf", ROOT / "samples" / "purchase-request.csv")
    assert result["status"] == "error"
    assert "provider unavailable" in result["error"]
    assert result["attempts"] == 1
    assert len(calls) == 1


def test_workflow_failed_match_does_not_erase_a_reject(monkeypatch):
    import src.graph as graph
    from src.pdf_reader import PdfResult
    from src.validators import Gate2Result

    item = extraction("10")
    # A valid extraction that cannot match the PR line due to a changed description.
    item.description = Evidenced(value="Different item", evidence="Different item")
    monkeypatch.setattr(graph, "read_pdf", lambda path: PdfResult(True, "x" * 120, 1, None))
    monkeypatch.setattr(graph, "extract", lambda text, failures=(): type("Raw", (), {"text": "{}"})())
    monkeypatch.setattr(graph, "validate_extraction", lambda raw, text: Gate2Result(True, item, ()))
    result = graph.run_workflow("supplier.pdf", ROOT / "samples" / "purchase-request.csv")
    assert result["match"].ok is False
    assert result["decision"] == config.REJECT


ANSWER_KEY = json.loads((ROOT / "expected.json").read_text(encoding="utf-8"))["PR-2026-114"]


@pytest.mark.parametrize("pack", sorted(GOLD))
def test_fixed_workflow_matches_all_ten_approved_packs(monkeypatch, pack):
    import src.graph as graph
    from src.pdf_reader import PdfResult
    from src.validators import Gate2Result

    item = extraction(pack)
    monkeypatch.setattr(graph, "read_pdf", lambda path: PdfResult(True, "x" * 120, 1, None))
    monkeypatch.setattr(graph, "extract", lambda text, failures=(): type("Raw", (), {"text": "gold fixture"})())
    monkeypatch.setattr(graph, "validate_extraction", lambda raw, text: Gate2Result(True, item, ()))

    result = graph.run_workflow("supplier.pdf", ROOT / "samples" / "purchase-request.csv")
    assert result["status"] == "completed"
    assert result["match"].ok is True
    assert sorted(f.rule_id for f in result["findings"]) == sorted(ANSWER_KEY[pack]["rule_ids"])
    assert result["decision"] == ANSWER_KEY[pack]["decision"]


def test_workflow_unusable_csv_returns_clean_error(monkeypatch, tmp_path):
    import src.graph as graph
    from src.pdf_reader import PdfResult
    from src.validators import Gate2Result

    item = extraction("01")
    monkeypatch.setattr(graph, "read_pdf", lambda path: PdfResult(True, "x" * 120, 1, None))
    monkeypatch.setattr(graph, "extract", lambda text, failures=(): type("Raw", (), {"text": "gold fixture"})())
    monkeypatch.setattr(graph, "validate_extraction", lambda raw, text: Gate2Result(True, item, ()))

    result = graph.run_workflow("supplier.pdf", tmp_path / "missing.csv")
    assert result["status"] == "error"
    assert "not found" in result["error"]
    assert "decision" not in result


# ---- follow-up review: mapper hardening and failed-match workflow paths -------
def run_stubbed(monkeypatch, item):
    """Run the whole fixed workflow with PDF, LLM and Gate 2 stubbed."""
    import src.graph as graph
    from src.pdf_reader import PdfResult
    from src.validators import Gate2Result

    monkeypatch.setattr(graph, "read_pdf", lambda path: PdfResult(True, "x" * 120, 1, None))
    monkeypatch.setattr(
        graph, "extract",
        lambda text, failures=(): type("Raw", (), {"text": "gold fixture"})(),
    )
    monkeypatch.setattr(
        graph, "validate_extraction", lambda raw, text: Gate2Result(True, item, ())
    )
    return graph.run_workflow("supplier.pdf", ROOT / "samples" / "purchase-request.csv")


def test_mapping_lists_unusable_optional_numbers_for_rule_m():
    from src.graph import map_extraction_to_rule_inputs

    result = map_extraction_to_rule_inputs(
        extraction("01", penalty_pct="half a percent", balance_days_min="soon",
                   vat_rate="fifteen"),
        max_price=D("480"), requested_qty=D("12000"),
    )
    assert result.penalty_pct is None
    assert result.balance_days_min is None
    assert result.vat_rate is None
    for name in ("penalty_pct", "balance_days_min", "vat_rate"):
        assert name in result.missing_fields


def test_mapping_flags_an_unrecognised_delivery_unit():
    from src.graph import map_extraction_to_rule_inputs

    result = map_extraction_to_rule_inputs(
        extraction("01", delivery_value="3", delivery_unit="month"),
        max_price=D("480"), requested_qty=D("12000"),
    )
    assert result.delivery_days is None
    assert "delivery_unit" in result.missing_fields


def test_mapping_converts_weeks_to_days_without_flagging():
    from src.graph import map_extraction_to_rule_inputs

    result = map_extraction_to_rule_inputs(
        extraction("01", delivery_value="4", delivery_unit="weeks"),
        max_price=D("480"), requested_qty=D("12000"),
    )
    assert result.delivery_days == D("28")
    assert "delivery_unit" not in result.missing_fields
    assert "delivery_value" not in result.missing_fields


def test_failed_match_on_a_clean_pack_needs_a_person(monkeypatch):
    assert ANSWER_KEY["01"]["decision"] == config.APPROVE  # pack 01 is clean when matched
    item = extraction("01")
    item.description = Evidenced(value="Different item", evidence="Different item")
    result = run_stubbed(monkeypatch, item)
    assert result["status"] == "completed"
    assert result["match"].ok is False
    assert result["findings"] == []
    assert result["decision"] == config.NEEDS_CHECK


def test_failed_match_skips_price_rules_so_an_r1_pack_needs_a_person(monkeypatch):
    # Without a confirmed maximum price R1 cannot run. This is the safe direction:
    # the case goes to a person instead of being approved. Documented in NOTE.md.
    assert ANSWER_KEY["03"]["rule_ids"] == ["R1"]
    item = extraction("03")
    item.description = Evidenced(value="Different item", evidence="Different item")
    result = run_stubbed(monkeypatch, item)
    assert result["match"].ok is False
    assert "R1" not in [f.rule_id for f in result["findings"]]
    assert result["decision"] == config.NEEDS_CHECK


def test_unreadable_penalty_is_not_reported_as_a_missing_clause(monkeypatch):
    # An unreadable value must not become a false R3 reject ("no penalty clause").
    result = run_stubbed(monkeypatch, extraction("01", penalty_pct="half a percent"))
    ids = [f.rule_id for f in result["findings"]]
    assert "R3" not in ids
    assert "M" in ids
    assert result["decision"] == config.NEEDS_CHECK


def test_a_truly_absent_penalty_is_still_an_r3_reject(monkeypatch):
    result = run_stubbed(monkeypatch, extraction("06"))
    assert "R3" in [f.rule_id for f in result["findings"]]
    assert result["decision"] == config.REJECT
