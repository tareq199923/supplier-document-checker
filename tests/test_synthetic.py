"""F015 synthetic quotation tests. Offline: the oracle uses LLM_PROVIDER=mock.

The tests cover the fixture arithmetic, deterministic text, the PDF round trip,
the oracle extraction through the real workflow, assessment reuse, artifact
content, the F011 file guard and the independent live opt-in flag.
"""
from __future__ import annotations

import dataclasses
import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from src import config, evaluation, synthetic
from src.llm import set_mock_responses
from src.parsing import to_days
from src.pdf_reader import gate_text, normalise_text, read_pdf
from src.result import (
    GATE2_FAILED,
    LLM_ERROR,
    SOURCE_RULE,
    CheckResult,
    ErrorInfo,
    MatchedRow,
    MatchSummary,
    Reason,
    check_supplier,
)
from src.rules import RuleInputs, decide, evaluate
from src.schema import FIELD_NAMES
from src.validators import validate_extraction

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
CSV = SAMPLES / "purchase-request.csv"
FIXED_NOW = __import__("datetime").datetime(
    2026, 11, 5, 12, 0, tzinfo=__import__("datetime").timezone.utc
)

CASES = synthetic.load_synthetic_cases()
CASE_BY_ID = {case.case_id: case for case in CASES}


@pytest.fixture(autouse=True)
def clear_mock_queue():
    yield
    set_mock_responses([])


def rule_inputs(case: synthetic.SyntheticCase) -> RuleInputs:
    """Build rule inputs directly from the spec, using Decimal only."""
    spec = case.spec
    money = spec.money
    present = {
        "supplier_name": spec.supplier_name,
        "quotation_no": spec.quotation_no,
        "quotation_date": spec.quotation_date,
        "item_code": spec.item_code,
        "quantity": spec.quantity,
        "unit_price": spec.unit_price,
        "line_amount": money.line_amount,
        "total": money.total,
        "advance_pct": spec.advance_pct,
        "delivery_value": spec.delivery_value,
        "delivery_unit": spec.delivery_unit,
        "validity_days": spec.validity_days,
    }
    missing = tuple(
        name for name in config.REQUIRED_FIELDS if present[name] is None
    )
    return RuleInputs(
        price=spec.unit_price,
        max_price=Decimal("480.00"),
        quantity=spec.quantity,
        requested_qty=Decimal("12000"),
        advance_pct=spec.advance_pct,
        balance_days_min=spec.balance_days_min,
        balance_days_max=spec.balance_days_max,
        delivery_days=to_days(str(spec.delivery_value), spec.delivery_unit),
        penalty_pct=spec.penalty_pct,
        penalty_period=spec.penalty_period,
        penalty_basis=spec.penalty_basis,
        tin=spec.tin,
        bin_no=spec.bin,
        vat_rate=spec.vat_rate,
        vat_amount=money.vat_amount,
        line_amount=money.line_amount,
        subtotal=money.subtotal,
        total=money.total,
        validity_days=spec.validity_days,
        missing_fields=missing,
    )


def completed(
    case: synthetic.SyntheticCase,
    *,
    decision: str | None = None,
    rule_ids: tuple[str, ...] | None = None,
    attempts: int = 1,
    evidence: tuple[tuple[str, str], ...] = (),
) -> CheckResult:
    decision = case.expected_decision if decision is None else decision
    rule_ids = case.expected_rule_ids if rule_ids is None else rule_ids
    reasons = tuple(
        Reason(
            "rule",
            code,
            f"{code} message",
            "reject" if code.startswith("R") else "review",
            evidence=evidence if code == rule_ids[0] else (),
        )
        for code in rule_ids
    )
    row = MatchedRow(
        pr_number="PR-2026-114",
        item="A",
        description="Cotton twill fabric, 280 gsm",
        unit="metre",
        max_unit_price=Decimal("480.00"),
        requested_quantity=Decimal("12000"),
    )
    return CheckResult(
        status="completed",
        decision=decision,
        reasons=reasons,
        match=MatchSummary(ok=True, row=row),
        attempts=attempts,
        supplier_name=case.spec.supplier_name,
    )


def llm_error() -> CheckResult:
    return CheckResult(
        status="error",
        decision=None,
        error=ErrorInfo(LLM_ERROR, "The language model could not be reached.", "extract", True),
        attempts=1,
    )


def synthetic_paths(directory: Path) -> dict[str, Path]:
    return {case.case_id: directory / f"{case.case_id}.pdf" for case in CASES}


def run_with(results, tmp_path, *, now=FIXED_NOW):
    calls: list[tuple[Path, Path]] = []

    def check(pdf_path, csv_path):
        calls.append((Path(pdf_path), Path(csv_path)))
        value = results[Path(pdf_path).stem]
        if isinstance(value, Exception):
            raise value
        return value

    run = synthetic.run_synthetic_evaluation(
        CASES,
        synthetic_paths(tmp_path),
        check_fn=check,
        pause_s=0,
        now=now,
        git_state=("deadbeef", False),
    )
    return run, calls


# ---- fixture and arithmetic ---------------------------------------------------
def test_fixture_has_twenty_cases_with_fixed_ids():
    assert len(CASES) == 20
    assert [case.case_id for case in CASES] == [
        f"S{number:02d}" for number in range(1, 21)
    ]
    assert len(set(CASE_BY_ID)) == 20
    assert all(case.expected_decision in {
        config.APPROVE, config.REJECT, config.NEEDS_CHECK
    } for case in CASES)
    assert all(
        case.expected_rule_ids == tuple(sorted(case.expected_rule_ids))
        for case in CASES
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_fixture_matches_the_real_rules(case):
    findings = evaluate(rule_inputs(case))
    assert decide(findings) == case.expected_decision
    assert tuple(sorted(finding.rule_id for finding in findings)) == case.expected_rule_ids


def test_special_specs_are_built_as_intended():
    assert rule_inputs(CASE_BY_ID["S07"]).delivery_days == Decimal("28")
    assert CASE_BY_ID["S07"].spec.delivery_unit == "week"
    assert rule_inputs(CASE_BY_ID["S18"]).advance_pct == Decimal("0")
    assert CASE_BY_ID["S18"].spec.advance_zero_wording is True
    assert CASE_BY_ID["S13"].spec.penalty_pct is None
    assert CASE_BY_ID["S17"].spec.tin is None
    assert CASE_BY_ID["S17"].spec.bin == "000170141-0101"
    assert CASE_BY_ID["S20"].spec.quotation_no is None
    assert rule_inputs(CASE_BY_ID["S20"]).missing_fields == ("quotation_no",)
    assert CASE_BY_ID["S12"].spec.money.line_amount == Decimal("5664000.02")
    assert CASE_BY_ID["S12"].spec.money.subtotal == Decimal("5664000.02")
    assert CASE_BY_ID["S12"].spec.money.vat_amount == Decimal("849600.00")
    assert CASE_BY_ID["S12"].spec.money.total == Decimal("6513600.02")
    assert CASE_BY_ID["S11"].spec.money.vat_amount == Decimal("792960.00")
    assert CASE_BY_ID["S19"].spec.unit_price == Decimal("600.00")


# ---- renderer and PDF round trip ----------------------------------------------
@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_renderer_is_deterministic(case):
    first = synthetic.render_text(case)
    second = synthetic.render_text(case)
    assert first == second
    assert len(normalise_text(first)) >= config.MIN_TEXT_CHARS
    assert all(ord(character) < 128 for character in first)


def test_special_renderings_are_explicit():
    assert "4 weeks" in synthetic.render_text(CASE_BY_ID["S07"])
    assert "No advance payment required." in synthetic.render_text(CASE_BY_ID["S18"])
    assert "the order value" in synthetic.render_text(CASE_BY_ID["S16"])
    assert "total price" in synthetic.render_text(CASE_BY_ID["S15"])
    assert "5,664,000.02" in synthetic.render_text(CASE_BY_ID["S12"])
    assert "494.40" in synthetic.render_text(CASE_BY_ID["S02"])
    assert "494.41" in synthetic.render_text(CASE_BY_ID["S03"])
    assert CASE_BY_ID["S19"].spec.injection_note in synthetic.render_text(CASE_BY_ID["S19"])

    no_penalty = synthetic.render_text(CASE_BY_ID["S13"])
    assert re.search(
        r"\b(?:penalt(?:y|ies)|late[\s-]+delivery|delay(?:ed|s)?)\b",
        no_penalty,
        re.IGNORECASE,
    ) is None

    assert re.search(r"\bTIN\b", synthetic.render_text(CASE_BY_ID["S17"]), re.I) is None
    assert "Quotation no." not in synthetic.render_text(CASE_BY_ID["S20"])


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_pdf_round_trip_preserves_normalised_text(case, tmp_path):
    path = synthetic.write_pdf(case, tmp_path / f"{case.case_id}.pdf")
    result = read_pdf(path)
    assert result.ok, result.error
    assert result.page_count >= 1
    assert normalise_text(result.text) == normalise_text(synthetic.render_text(case))
    assert gate_text(result.text) is None


# ---- oracle extraction through the real workflow -----------------------------
@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_extraction_payload_has_all_fields_and_verbatim_evidence(case):
    payload = synthetic.extraction_payload(case)
    assert set(payload) == set(FIELD_NAMES)
    assert all(set(item) == {"value", "evidence"} for item in payload.values())
    assert all(
        item["value"] is None or item["evidence"] is not None
        for item in payload.values()
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_oracle_extraction_passes_gate2_and_check_supplier(case, tmp_path, monkeypatch):
    path = synthetic.write_pdf(case, tmp_path / f"{case.case_id}.pdf")
    pdf = read_pdf(path)
    assert pdf.ok, pdf.error
    payload = synthetic.extraction_payload(case)
    raw = json.dumps(payload, ensure_ascii=False)

    gate = validate_extraction(raw, pdf.text)
    assert gate.ok, gate.failures

    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_MODEL", "mock")
    set_mock_responses([raw])
    result = check_supplier(path, CSV)

    assert result.status == "completed", result
    assert result.match is not None and result.match.ok, result.match
    assert result.decision == case.expected_decision
    actual_rules = tuple(
        sorted(reason.code for reason in result.reasons if reason.source == SOURCE_RULE)
    )
    assert actual_rules == case.expected_rule_ids
    assert result.attempts == 1

    assessment = synthetic.assess_synthetic(case, result, pdf_path=path)
    assert assessment.outcome == evaluation.OUTCOME_PASS
    assert assessment.passed

    expected_outcome = evaluation._classify(
        result, case.expected_decision, case.expected_rule_ids, True
    )
    assert assessment.outcome == expected_outcome


# ---- assessment reuses the F011 pass rule -------------------------------------
def test_assess_synthetic_passes_an_exact_match():
    case = CASE_BY_ID["S01"]
    assessment = synthetic.assess_synthetic(case, completed(case))
    assert assessment.passed
    assert assessment.outcome == evaluation.OUTCOME_PASS
    assert assessment.expected_rule_ids == ()


def test_assess_synthetic_fails_a_wrong_decision():
    case = CASE_BY_ID["S01"]
    assessment = synthetic.assess_synthetic(
        case, completed(case, decision=config.REJECT)
    )
    assert not assessment.passed
    assert assessment.outcome == evaluation.OUTCOME_ACCURACY


def test_assess_synthetic_fails_a_gate2_review_even_with_the_same_decision():
    case = CASE_BY_ID["S02"]
    result = CheckResult(
        status="needs_review",
        decision=config.NEEDS_CHECK,
        reasons=(Reason("extraction", GATE2_FAILED, "bad extraction", "review"),),
        attempts=2,
    )
    assessment = synthetic.assess_synthetic(case, result)
    assert not assessment.passed
    assert assessment.outcome == evaluation.OUTCOME_EXTRACTION


# ---- runner and artifacts ------------------------------------------------------
def test_runner_assesses_all_cases_and_calls_each_pdf(tmp_path):
    results = {case.case_id: completed(case) for case in CASES}
    run, calls = run_with(results, tmp_path)

    assert run.total == 20
    assert run.passed == 20
    assert run.failed == 0
    assert run.ok
    assert run.counts_by_outcome()[evaluation.OUTCOME_PASS] == 20
    assert [pdf.name for pdf, _ in calls] == [
        f"{case.case_id}.pdf" for case in CASES
    ]
    assert all(csv == CSV for _, csv in calls)
    assert [item.pack for item in run.assessments] == [case.case_id for case in CASES]


def test_runner_records_a_harness_exception_as_an_outcome(tmp_path):
    results = {case.case_id: completed(case) for case in CASES}
    results["S05"] = RuntimeError("boom")
    run, _ = run_with(results, tmp_path)
    assert run.passed == 19
    failed = [item for item in run.assessments if not item.passed][0]
    assert failed.pack == "S05"
    assert failed.outcome == evaluation.OUTCOME_HARNESS
    assert failed.error_code == "RuntimeError"


def test_synthetic_artifacts_json_and_markdown(tmp_path):
    results = {case.case_id: completed(case) for case in CASES}
    run, _ = run_with(results, tmp_path)
    json_path, md_path = synthetic.write_artifacts(run, tmp_path)

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = md_path.read_text(encoding="utf-8")
    assert payload["feature"] == "F015"
    assert payload["kind"] == "synthetic"
    assert payload["totals"] == {"total": 20, "passed": 20, "failed": 0}
    assert payload["outcomes"][evaluation.OUTCOME_PASS] == 20
    assert len(payload["cases"]) == 20
    assert payload["cases"][0]["case"] == "S01"
    assert "F015" in markdown
    assert "20/20" in markdown
    assert "S01" in markdown
    assert json_path.name == "f015-synthetic.json"
    assert md_path.name == "f015-synthetic.md"


def test_synthetic_artifacts_do_not_include_evidence_quotes(tmp_path):
    results = {case.case_id: completed(case) for case in CASES}
    results["S01"] = completed(
        CASE_BY_ID["S01"],
        evidence=(("unit_price", "SECRET SUPPLIER QUOTE"),),
    )
    run, _ = run_with(results, tmp_path)
    json_path, md_path = synthetic.write_artifacts(run, tmp_path)
    text = json_path.read_text(encoding="utf-8") + md_path.read_text(encoding="utf-8")
    assert "SECRET SUPPLIER QUOTE" not in text


def test_synthetic_writers_never_touch_f011_files(tmp_path, monkeypatch):
    json_sentinel = tmp_path / "f011-golden-run.json"
    md_sentinel = tmp_path / "f011-golden-run.md"
    json_sentinel.write_text("KEEP JSON", encoding="utf-8")
    md_sentinel.write_text("KEEP MD", encoding="utf-8")
    monkeypatch.setattr(
        evaluation,
        "write_artifacts",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("evaluation.write_artifacts must not be called")
        ),
    )

    results = {case.case_id: completed(case) for case in CASES}
    run, _ = run_with(results, tmp_path)
    synthetic.write_artifacts(run, tmp_path)

    assert json_sentinel.read_text(encoding="utf-8") == "KEEP JSON"
    assert md_sentinel.read_text(encoding="utf-8") == "KEEP MD"
    assert (tmp_path / "f015-synthetic.json").exists()
    assert (tmp_path / "f015-synthetic.md").exists()


# ---- live opt-in flag ----------------------------------------------------------
LIVE_ENV = (
    "RUN_LIVE_EVALUATION",
    "RUN_LIVE_F015",
    "LLM_PROVIDER",
    "LLM_MODEL",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_BASE_URL",
)


def clear_live_env(monkeypatch):
    for name in LIVE_ENV:
        monkeypatch.delenv(name, raising=False)


def test_default_live_guard_still_requires_run_live_evaluation(monkeypatch):
    clear_live_env(monkeypatch)
    reason = evaluation.live_skip_reason()
    assert reason is not None and "RUN_LIVE_EVALUATION" in reason


def test_f015_live_guard_uses_its_own_flag(monkeypatch):
    clear_live_env(monkeypatch)
    monkeypatch.setenv("RUN_LIVE_F015", "1")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    assert evaluation.live_skip_reason(env_flag="RUN_LIVE_F015") is None


def test_f015_guard_does_not_accept_the_f011_flag(monkeypatch):
    clear_live_env(monkeypatch)
    monkeypatch.setenv("RUN_LIVE_EVALUATION", "1")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    reason = evaluation.live_skip_reason(env_flag="RUN_LIVE_F015")
    assert reason is not None and "RUN_LIVE_F015" in reason


def test_f015_guard_still_rejects_mock(monkeypatch):
    clear_live_env(monkeypatch)
    monkeypatch.setenv("RUN_LIVE_F015", "1")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_MODEL", "mock")
    reason = evaluation.live_skip_reason(env_flag="RUN_LIVE_F015")
    assert reason is not None and "mock" in reason


def test_f015_guard_still_requires_the_provider_key(monkeypatch):
    clear_live_env(monkeypatch)
    monkeypatch.setenv("RUN_LIVE_F015", "1")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_MODEL", "claude-test")
    reason = evaluation.live_skip_reason(env_flag="RUN_LIVE_F015")
    assert reason is not None and "ANTHROPIC_API_KEY" in reason
