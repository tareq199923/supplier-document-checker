"""F011 evaluation-harness tests. Written first: red until src/evaluation.py exists.

No live LLM call here. The live run is opt-in in tests/test_live_evaluation.py;
these tests inject a fake checker, so they are deterministic and offline.
"""
import dataclasses
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src import config, evaluation
from src.result import (
    DOCUMENT_UNREADABLE,
    GATE2_FAILED,
    ITEM_MISMATCH,
    LLM_ERROR,
    PURCHASE_REQUEST_UNUSABLE,
    CheckResult,
    ErrorInfo,
    MatchedRow,
    MatchSummary,
    Reason,
)

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
CSV = SAMPLES / "purchase-request.csv"


# ---- helpers ------------------------------------------------------------------
@pytest.fixture(autouse=True)
def stable_git(monkeypatch):
    """Never call the real git binary from these tests."""
    monkeypatch.setattr(evaluation, "git_state", lambda root=evaluation.ROOT: ("deadbeef", False))


@pytest.fixture(scope="module")
def expected_packs():
    return evaluation.load_expected()


def expected(decision="Approve", rule_ids=(), supplier="Supplier"):
    return {"supplier": supplier, "decision": decision, "rule_ids": list(rule_ids)}


def case(pack="01"):
    return evaluation.PackCase(pack=pack, pdf_path=SAMPLES / f"supplier-pack-{pack}.pdf")


def completed(decision, rule_ids=(), *, match_ok=True, supplier="Supplier"):
    reasons = tuple(
        Reason("rule", code, f"{code} message", "reject" if code.startswith("R") else "review")
        for code in rule_ids
    )
    row = MatchedRow(
        pr_number="PR-2026-114",
        item="A",
        description="Cotton fabric",
        unit="metre",
        max_unit_price=Decimal("210"),
        requested_quantity=Decimal("20000"),
    )
    match = (
        MatchSummary(ok=True, row=row)
        if match_ok
        else MatchSummary(ok=False, problems=("description differs",))
    )
    return CheckResult(
        status="completed",
        decision=decision,
        reasons=reasons,
        match=match,
        attempts=1,
        supplier_name=supplier,
    )


def review(code=GATE2_FAILED):
    source = "extraction" if code == GATE2_FAILED else "document"
    return CheckResult(
        status="needs_review",
        decision=config.NEEDS_CHECK,
        reasons=(Reason(source, code, f"{code} message", "review"),),
        attempts=2,
    )


def error(code=LLM_ERROR):
    return CheckResult(
        status="error",
        decision=None,
        error=ErrorInfo(code, f"{code} message", "extract", code == LLM_ERROR),
        attempts=1,
    )


def assess(result, entry=None, pack="01"):
    return evaluation.assess(case(pack), result, entry if entry is not None else expected())


def write_pdfs(directory, packs):
    directory.mkdir(parents=True, exist_ok=True)
    for pack in packs:
        (directory / f"supplier-pack-{pack}.pdf").write_bytes(b"%PDF-1.4")


def passing_results(packs):
    return {
        pack: completed(entry["decision"], entry["rule_ids"], supplier=entry["supplier"])
        for pack, entry in packs.items()
    }


def run_with(results, *, pause_s=0, now=None):
    """Run the real runner with a fake checker. `results` maps pack -> result."""
    calls = []

    def check(pdf_path, csv_path):
        calls.append((Path(pdf_path), Path(csv_path)))
        pack = Path(pdf_path).stem.rsplit("-", 1)[-1]
        item = results[pack]
        if isinstance(item, Exception):
            raise item
        return item

    run = evaluation.run_evaluation(
        check_fn=check, pause_s=pause_s, now=now
    )
    return run, calls


# ---- expected data and pack discovery ------------------------------------------
def test_load_expected_returns_the_ten_approved_packs():
    packs = evaluation.load_expected()
    assert sorted(packs) == [f"{number:02d}" for number in range(1, 11)]
    assert packs["03"]["decision"] == "Reject"
    assert packs["03"]["rule_ids"] == ["R1"]


def test_load_expected_rejects_multiple_purchase_requests(tmp_path):
    path = tmp_path / "expected.json"
    path.write_text(
        json.dumps({"PR-A": {"01": expected()}, "PR-B": {"02": expected()}}),
        encoding="utf-8",
    )
    with pytest.raises(evaluation.EvaluationError, match="exactly one"):
        evaluation.load_expected(path)


def test_load_expected_rejects_an_empty_file(tmp_path):
    path = tmp_path / "expected.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(evaluation.EvaluationError):
        evaluation.load_expected(path)


def test_load_expected_requires_a_decision_and_rule_ids(tmp_path):
    path = tmp_path / "expected.json"
    path.write_text(
        json.dumps({"PR-A": {"01": {"rule_ids": []}}}), encoding="utf-8"
    )
    with pytest.raises(evaluation.EvaluationError, match="decision"):
        evaluation.load_expected(path)


def test_load_expected_requires_rule_ids_to_be_a_list(tmp_path):
    path = tmp_path / "expected.json"
    path.write_text(
        json.dumps({"PR-A": {"01": {"decision": "Approve", "rule_ids": "R1"}}}),
        encoding="utf-8",
    )
    with pytest.raises(evaluation.EvaluationError, match="rule_ids"):
        evaluation.load_expected(path)


def test_discover_packs_finds_the_ten_pdf_files():
    found = evaluation.discover_packs(SAMPLES)
    assert sorted(found) == [f"{number:02d}" for number in range(1, 11)]
    assert found["01"].name == "supplier-pack-01.pdf"


def test_load_cases_pairs_each_pack_with_its_pdf(expected_packs):
    cases = evaluation.load_cases(expected_packs, SAMPLES)
    assert len(cases) == 10
    assert cases[0].pack == "01"
    assert cases[0].pdf_path == SAMPLES / "supplier-pack-01.pdf"


def test_load_cases_rejects_a_missing_pack(tmp_path):
    packs = {"01": expected(), "02": expected()}
    write_pdfs(tmp_path, ["01"])
    with pytest.raises(evaluation.EvaluationError, match="missing: 02"):
        evaluation.load_cases(packs, tmp_path)


def test_load_cases_rejects_an_unexpected_pack(tmp_path):
    packs = {"01": expected()}
    write_pdfs(tmp_path, ["01", "02"])
    with pytest.raises(evaluation.EvaluationError, match="unexpected: 02"):
        evaluation.load_cases(packs, tmp_path)


def test_discover_packs_rejects_an_empty_folder(tmp_path):
    with pytest.raises(evaluation.EvaluationError, match="no supplier-pack"):
        evaluation.discover_packs(tmp_path)


# ---- assess: the pass rule and the outcome taxonomy ----------------------------
def test_assess_passes_on_an_exact_match():
    result = assess(completed("Approve"), expected("Approve"))
    assert result.passed
    assert result.outcome == evaluation.OUTCOME_PASS
    assert result.problems == ()


def test_assess_passes_a_legitimate_business_review():
    # A human-review finding is a completed business result, not a system failure.
    result = assess(
        completed(config.NEEDS_CHECK, ("H1",)),
        expected(config.NEEDS_CHECK, ("H1",)),
    )
    assert result.passed
    assert result.outcome == evaluation.OUTCOME_PASS


def test_assess_ignores_rule_id_order():
    result = assess(
        completed("Approve", ("H2", "H1")), expected("Approve", ("H1", "H2"))
    )
    assert result.passed
    assert result.actual_rule_ids == ("H1", "H2")


def test_assess_fails_on_a_decision_mismatch():
    result = assess(completed("Reject", ("R1",)), expected("Approve"))
    assert not result.passed
    assert result.outcome == evaluation.OUTCOME_ACCURACY
    assert any("decision" in problem for problem in result.problems)


def test_assess_fails_on_a_missing_rule():
    result = assess(completed("Approve"), expected("Approve", ("H1",)))
    assert result.outcome == evaluation.OUTCOME_ACCURACY
    assert any("missing rule(s): H1" in problem for problem in result.problems)


def test_assess_fails_on_an_extra_rule():
    result = assess(completed("Approve", ("H1",)), expected("Approve"))
    assert result.outcome == evaluation.OUTCOME_ACCURACY
    assert any("extra rule(s): H1" in problem for problem in result.problems)


def test_assess_fails_a_gate2_review_even_when_the_decision_matches():
    # The decision string is the same as a legitimate review, but Gate 2 means
    # the extraction failed, so this must never count as a pass.
    result = assess(review(GATE2_FAILED), expected(config.NEEDS_CHECK))
    assert not result.passed
    assert result.outcome == evaluation.OUTCOME_EXTRACTION
    assert any("GATE2_FAILED" in problem for problem in result.problems)


def test_assess_fails_a_document_review():
    result = assess(review(DOCUMENT_UNREADABLE), expected(config.NEEDS_CHECK))
    assert result.outcome == evaluation.OUTCOME_DOCUMENT


def test_assess_fails_an_llm_error_as_infrastructure():
    result = assess(error(LLM_ERROR), expected("Approve"))
    assert result.outcome == evaluation.OUTCOME_INFRASTRUCTURE
    assert result.error_code == LLM_ERROR
    assert result.error_retryable is True
    assert result.actual_decision is None


def test_assess_fails_a_csv_error_as_an_app_error():
    result = assess(error(PURCHASE_REQUEST_UNUSABLE), expected("Approve"))
    assert result.outcome == evaluation.OUTCOME_APP_ERROR
    assert result.error_retryable is False


def test_assess_fails_a_completed_result_with_a_failed_match():
    # Pack 09's H3/H6 can fire without the CSV, so matching decision and rule
    # IDs alone would hide a failed match. The evaluation must not pass this.
    result = assess(
        completed(config.NEEDS_CHECK, ("H3", "H6"), match_ok=False),
        expected(config.NEEDS_CHECK, ("H3", "H6")),
    )
    assert result.outcome == evaluation.OUTCOME_ACCURACY
    assert any("purchase-request match" in problem for problem in result.problems)


def test_assess_ignores_reasons_that_are_not_rules():
    base = completed(config.NEEDS_CHECK, ("H1",))
    result = dataclasses.replace(
        base,
        reasons=base.reasons
        + (Reason("match", ITEM_MISMATCH, "match message", "review"),),
    )
    assessed = assess(result, expected(config.NEEDS_CHECK, ("H1",)))
    assert assessed.actual_rule_ids == ("H1",)
    assert assessed.passed


def test_assess_treats_an_unknown_status_as_an_app_error():
    result = assess(
        CheckResult(status="weird", decision="Approve"), expected("Approve")
    )
    assert result.outcome == evaluation.OUTCOME_APP_ERROR
    assert any("unrecognised status" in problem for problem in result.problems)


# ---- run_evaluation aggregation -------------------------------------------------
def test_run_evaluation_assesses_all_ten_packs(expected_packs):
    run, calls = run_with(passing_results(expected_packs))
    assert len(calls) == 10
    assert run.total == 10
    assert run.passed == 10
    assert run.failed == 0
    assert run.ok
    assert run.counts_by_outcome()[evaluation.OUTCOME_PASS] == 10


def test_run_evaluation_records_a_mismatch(expected_packs):
    results = passing_results(expected_packs)
    results["03"] = completed("Approve")
    run, _ = run_with(results)
    assert run.passed == 9
    assert run.failed == 1
    assert not run.ok
    failed = [item for item in run.assessments if not item.passed]
    assert [item.pack for item in failed] == ["03"]
    assert failed[0].outcome == evaluation.OUTCOME_ACCURACY


def test_run_evaluation_turns_a_harness_exception_into_an_outcome(expected_packs):
    results = passing_results(expected_packs)
    results["05"] = RuntimeError("boom")
    run, _ = run_with(results)
    assert run.passed == 9
    assert run.failed == 1
    failed = [item for item in run.assessments if not item.passed][0]
    assert failed.pack == "05"
    assert failed.outcome == evaluation.OUTCOME_HARNESS
    assert failed.error_code == "RuntimeError"
    assert failed.error_stage == "harness"


def test_run_evaluation_passes_the_pdf_and_csv_to_the_checker(expected_packs):
    _, calls = run_with(passing_results(expected_packs))
    assert [pdf.name for pdf, _ in calls] == [
        f"supplier-pack-{number:02d}.pdf" for number in range(1, 11)
    ]
    assert all(csv == CSV for _, csv in calls)


def test_run_evaluation_returns_assessments_in_pack_order(expected_packs):
    run, _ = run_with(passing_results(expected_packs))
    assert [item.pack for item in run.assessments] == [
        f"{number:02d}" for number in range(1, 11)
    ]


def test_run_evaluation_records_the_run_time(expected_packs):
    fixed = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
    run, _ = run_with(passing_results(expected_packs), now=fixed)
    assert run.run_at == "2026-11-05T12:00:00+00:00"


def test_run_evaluation_records_provider_model_and_prompt_version(
    monkeypatch, expected_packs
):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    run, _ = run_with(passing_results(expected_packs))
    assert run.provider == "openai"
    assert run.model == "test-model"
    assert run.prompt_version == "v1"
    assert run.git_commit == "deadbeef"
    assert run.git_dirty is False


def test_run_evaluation_pauses_between_packs(monkeypatch, expected_packs):
    sleeps = []
    monkeypatch.setattr(evaluation.time, "sleep", sleeps.append)
    run_with(passing_results(expected_packs), pause_s=1.0)
    assert sleeps == [1.0] * 9


def test_run_evaluation_skips_the_pause_when_zero(expected_packs):
    run, _ = run_with(passing_results(expected_packs), pause_s=0)
    assert run.total == 10


# ---- summary and artifact -------------------------------------------------------
def test_summary_for_a_clean_run(expected_packs):
    run, _ = run_with(passing_results(expected_packs))
    summary = evaluation.render_summary(run)
    assert "10/10" in summary
    assert "All ten packs matched" in summary
    assert all(f"| {number:02d}" in summary for number in range(1, 11))


def test_summary_lists_failures_and_problems(expected_packs):
    results = passing_results(expected_packs)
    results["03"] = completed("Approve")
    run, _ = run_with(results)
    summary = evaluation.render_summary(run)
    assert "9/10" in summary
    assert "Failed packs" in summary
    assert "Pack 03" in summary
    assert "missing rule(s): R1" in summary


def test_payload_has_the_audit_fields_and_is_json_safe(expected_packs):
    run, _ = run_with(passing_results(expected_packs))
    payload = evaluation.run_payload(run)
    json.dumps(payload)  # must not raise on Decimal or other non-JSON values
    assert payload["feature"] == "F011"
    assert payload["run_at"]
    assert payload["provider"]
    assert payload["model"]
    assert payload["prompt_version"] == "v1"
    assert payload["git_commit"] == "deadbeef"
    assert payload["git_dirty"] is False
    assert payload["totals"] == {"total": 10, "passed": 10, "failed": 0}
    assert len(payload["packs"]) == 10
    assert payload["packs"][0]["pack"] == "01"
    assert payload["packs"][0]["expected"]["decision"] == "Approve"
    assert payload["packs"][0]["actual"]["rule_ids"] == []


def test_payload_does_not_leak_evidence_quotes():
    result = CheckResult(
        status="completed",
        decision="Approve",
        reasons=(
            Reason(
                "rule",
                "R1",
                "message",
                "reject",
                evidence=(("unit_price", "SECRET SUPPLIER QUOTE"),),
            ),
        ),
        match=MatchSummary(ok=True),
        attempts=1,
    )
    assessed = evaluation.assess(case(), result, expected("Approve", ("R1",)))
    run = evaluation.EvaluationRun(
        run_at="2026-11-05T12:00:00+00:00",
        provider="openai",
        model="test-model",
        prompt_version="v1",
        git_commit="deadbeef",
        git_dirty=False,
        expected_source="expected.json",
        purchase_request="samples/purchase-request.csv",
        assessments=(assessed,),
    )
    text = json.dumps(evaluation.run_payload(run))
    assert "SECRET SUPPLIER QUOTE" not in text


def test_write_artifacts_creates_both_files(expected_packs, tmp_path):
    run, _ = run_with(passing_results(expected_packs))
    json_path, md_path = evaluation.write_artifacts(run, tmp_path)
    assert json_path.exists() and md_path.exists()
    assert json.loads(json_path.read_text(encoding="utf-8")) == evaluation.run_payload(run)
    assert "F011" in md_path.read_text(encoding="utf-8")


# ---- live-run opt-in -----------------------------------------------------------------
LIVE_ENV = (
    "RUN_LIVE_EVALUATION",
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


def test_live_run_requires_explicit_opt_in(monkeypatch):
    clear_live_env(monkeypatch)
    reason = evaluation.live_skip_reason()
    assert reason is not None and "RUN_LIVE_EVALUATION" in reason


def test_live_run_rejects_the_mock_provider(monkeypatch):
    clear_live_env(monkeypatch)
    monkeypatch.setenv("RUN_LIVE_EVALUATION", "1")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_MODEL", "mock")
    reason = evaluation.live_skip_reason()
    assert reason is not None and "mock" in reason


def test_live_run_requires_an_anthropic_key(monkeypatch):
    clear_live_env(monkeypatch)
    monkeypatch.setenv("RUN_LIVE_EVALUATION", "1")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_MODEL", "claude-test")
    reason = evaluation.live_skip_reason()
    assert reason is not None and "ANTHROPIC_API_KEY" in reason


def test_live_run_allows_a_local_openai_endpoint_without_a_key(monkeypatch):
    clear_live_env(monkeypatch)
    monkeypatch.setenv("RUN_LIVE_EVALUATION", "1")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "local-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1")
    assert evaluation.live_skip_reason() is None


def test_live_run_requires_an_openai_key_without_a_base_url(monkeypatch):
    clear_live_env(monkeypatch)
    monkeypatch.setenv("RUN_LIVE_EVALUATION", "1")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-test")
    reason = evaluation.live_skip_reason()
    assert reason is not None and "OPENAI_API_KEY" in reason


def test_live_run_accepts_a_configured_provider(monkeypatch):
    clear_live_env(monkeypatch)
    monkeypatch.setenv("RUN_LIVE_EVALUATION", "1")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    assert evaluation.live_skip_reason() is None
