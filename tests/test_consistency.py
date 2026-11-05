"""F015 repeat-run consistency tests. Offline: the checker is always fake."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src import config, consistency, evaluation
from src.result import (
    LLM_ERROR,
    CheckResult,
    ErrorInfo,
    MatchedRow,
    MatchSummary,
    Reason,
)

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
CSV = SAMPLES / "purchase-request.csv"
FIXED_NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def stable_git(monkeypatch):
    monkeypatch.setattr(
        evaluation, "git_state", lambda root=evaluation.ROOT: ("deadbeef", False)
    )


@pytest.fixture(scope="module")
def expected_packs():
    return evaluation.load_expected()


def completed(decision="Approve", rule_ids=(), *, attempts=1, evidence=()):
    reasons = tuple(
        Reason(
            "rule",
            code,
            f"{code} message",
            "reject" if code.startswith("R") else "review",
            evidence=evidence,
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
        supplier_name="Supplier",
    )


def llm_error():
    return CheckResult(
        status="error",
        decision=None,
        error=ErrorInfo(LLM_ERROR, "The language model could not be reached.", "extract", True),
        attempts=1,
    )


def passing_results(packs):
    return {
        pack: completed(entry["decision"], entry["rule_ids"])
        for pack, entry in packs.items()
    }


def make_checker(results):
    calls = []
    counts = {}

    def check(pdf_path, csv_path):
        calls.append((Path(pdf_path), Path(csv_path)))
        pack = Path(pdf_path).stem.rsplit("-", 1)[-1]
        index = counts.get(pack, 0)
        counts[pack] = index + 1
        value = results[pack]
        if isinstance(value, (list, tuple)):
            value = value[min(index, len(value) - 1)]
        if isinstance(value, Exception):
            raise value
        return value

    return check, calls


def run_with(results, *, repeats=2, pause_s=0, now=FIXED_NOW, git_state=("deadbeef", False)):
    checker, calls = make_checker(results)
    run = consistency.run_repeated_evaluation(
        repeats=repeats,
        check_fn=checker,
        pause_s=pause_s,
        now=now,
        git_state=git_state,
    )
    return run, calls


def pack_by_id(run, pack):
    return next(item for item in run.packs if item.pack == pack)


# ---- categories ---------------------------------------------------------------
def test_stable_accurate_packs(expected_packs):
    run, calls = run_with(passing_results(expected_packs), repeats=2)

    assert run.repeats == 2
    assert run.total_observations == 20
    assert run.passed_runs == 20
    assert run.failed_runs == 0
    assert run.stable_packs == 10
    assert run.hidden_flakiness_packs == 0
    assert len(calls) == 20
    assert all(item.stable for item in run.packs)
    assert all(item.category == consistency.STABLE_ACCURATE for item in run.packs)
    assert all(item.hidden_flakiness is False for item in run.packs)
    assert all(len(item.runs) == 2 for item in run.packs)
    assert pack_by_id(run, "01").decision_distribution == ((config.APPROVE, 2),)
    assert pack_by_id(run, "01").rule_distribution == (((), 2),)


def test_stable_but_inaccurate_pack(expected_packs):
    results = passing_results(expected_packs)
    results["03"] = completed("Approve", ())
    run, _ = run_with(results, repeats=2)

    item = pack_by_id(run, "03")
    assert item.stable
    assert item.category == consistency.STABLE_INACCURATE
    assert item.expected_decision == config.REJECT
    assert item.expected_rule_ids == ("R1",)
    assert item.decision_distribution == ((config.APPROVE, 2),)
    assert item.rule_distribution == (((), 2),)
    assert run.passed_runs == 18
    assert run.failed_runs == 2


def test_flaky_pack(expected_packs):
    results = passing_results(expected_packs)
    results["05"] = [
        completed(config.REJECT, ("R2",)),
        completed(config.APPROVE, ()),
    ]
    run, _ = run_with(results, repeats=2)

    item = pack_by_id(run, "05")
    assert not item.stable
    assert item.category == consistency.FLAKY
    assert item.decision_distribution == (
        (config.APPROVE, 1),
        (config.REJECT, 1),
    )
    assert item.rule_distribution == (((), 1), (("R2",), 1))
    assert run.passed_runs == 19
    assert run.failed_runs == 1


def test_retry_is_visible_as_hidden_flakiness_even_when_stable(expected_packs):
    results = passing_results(expected_packs)
    results["07"] = [
        completed(config.NEEDS_CHECK, ("H2",), attempts=1),
        completed(config.NEEDS_CHECK, ("H2",), attempts=2),
    ]
    run, _ = run_with(results, repeats=2)

    item = pack_by_id(run, "07")
    assert item.stable
    assert item.category == consistency.STABLE_ACCURATE
    assert item.hidden_flakiness is True
    assert item.retry_runs == (2,)
    assert tuple(observation.attempts for observation in item.runs) == (1, 2)
    assert run.hidden_flakiness_packs == 1

    markdown = consistency.render_summary(run)
    assert "hidden" in markdown.lower()
    assert "| 2 |" in markdown


def test_infrastructure_error_is_not_mislabelled_as_flaky(expected_packs):
    results = passing_results(expected_packs)
    results["04"] = [llm_error(), completed(config.NEEDS_CHECK, ("H1",))]
    run, _ = run_with(results, repeats=2)

    item = pack_by_id(run, "04")
    assert not item.stable
    assert item.category == consistency.INFRASTRUCTURE_OR_HARNESS
    assert item.category != consistency.FLAKY
    assert item.failure_runs == (1,)
    assert item.runs[0].outcome == evaluation.OUTCOME_INFRASTRUCTURE
    assert item.runs[0].error_code == LLM_ERROR
    assert item.runs[1].outcome == evaluation.OUTCOME_PASS


def test_harness_exception_is_reported_separately(expected_packs):
    results = passing_results(expected_packs)
    results["06"] = [RuntimeError("boom"), completed(config.REJECT, ("R3",))]
    run, _ = run_with(results, repeats=2)

    item = pack_by_id(run, "06")
    assert item.category == consistency.INFRASTRUCTURE_OR_HARNESS
    assert item.failure_runs == (1,)
    assert item.runs[0].status == "harness"
    assert item.runs[0].outcome == evaluation.OUTCOME_HARNESS
    assert item.runs[0].error_code == "RuntimeError"


def test_all_harness_failures_are_stable_but_reported_as_failures(expected_packs):
    results = {pack: RuntimeError("boom") for pack in expected_packs}
    run, calls = run_with(results, repeats=2)

    assert len(calls) == 20
    assert run.passed_runs == 0
    assert run.stable_packs == 10
    assert all(
        item.category == consistency.INFRASTRUCTURE_OR_HARNESS
        for item in run.packs
    )
    assert all(item.failure_runs == (1, 2) for item in run.packs)


# ---- repeat count -------------------------------------------------------------
def test_evaluation_repeats_env_overrides_the_default(expected_packs):
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setenv("EVALUATION_REPEATS", "2")
        checker, calls = make_checker(passing_results(expected_packs))
        run = consistency.run_repeated_evaluation(
            check_fn=checker, pause_s=0, now=FIXED_NOW, git_state=("deadbeef", False)
        )
    finally:
        monkeypatch.undo()

    assert run.repeats == 2
    assert run.total_observations == 20
    assert len(calls) == 20


def test_explicit_repeats_wins_over_the_env(expected_packs):
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setenv("EVALUATION_REPEATS", "3")
        checker, calls = make_checker(passing_results(expected_packs))
        run = consistency.run_repeated_evaluation(
            repeats=1,
            check_fn=checker,
            pause_s=0,
            now=FIXED_NOW,
            git_state=("deadbeef", False),
        )
    finally:
        monkeypatch.undo()

    assert run.repeats == 1
    assert run.total_observations == 10
    assert len(calls) == 10


@pytest.mark.parametrize("value", ["abc", "0", "-1", "2.5", "true"])
def test_invalid_evaluation_repeats_raises_before_any_call(value, monkeypatch):
    monkeypatch.setenv("EVALUATION_REPEATS", value)
    checker, calls = make_checker({})
    with pytest.raises(consistency.ConsistencyError, match="EVALUATION_REPEATS"):
        consistency.run_repeated_evaluation(
            check_fn=checker,
            pause_s=0,
            now=FIXED_NOW,
            git_state=("deadbeef", False),
        )
    assert calls == []


@pytest.mark.parametrize("value", [0, -1, True, 2.5])
def test_invalid_explicit_repeats_raises(value):
    with pytest.raises(consistency.ConsistencyError, match="repeats"):
        consistency.resolve_repeats(value)


# ---- git state and artifacts ---------------------------------------------------
def test_git_state_is_captured_once_and_reused(expected_packs, monkeypatch):
    states = [("captured", False), ("later", True), ("later", True)]
    calls = []

    def fake_git(root=evaluation.ROOT):
        calls.append(1)
        return states[min(len(calls) - 1, len(states) - 1)]

    monkeypatch.setattr(evaluation, "git_state", fake_git)
    checker, _ = make_checker(passing_results(expected_packs))
    run = consistency.run_repeated_evaluation(
        repeats=2, check_fn=checker, pause_s=0, now=FIXED_NOW
    )

    assert run.git_commit == "captured"
    assert run.git_dirty is False
    assert consistency.run_payload(run)["git_dirty"] is False
    assert len(calls) >= 1


def test_consistency_artifacts_json_and_markdown(expected_packs, tmp_path):
    results = passing_results(expected_packs)
    results["07"] = [
        completed(config.NEEDS_CHECK, ("H2",), attempts=1),
        completed(config.NEEDS_CHECK, ("H2",), attempts=2),
    ]
    run, _ = run_with(results, repeats=2)
    json_path, md_path = consistency.write_artifacts(run, tmp_path)

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = md_path.read_text(encoding="utf-8")
    assert payload["feature"] == "F015"
    assert payload["kind"] == "repeat-run"
    assert payload["repeats"] == 2
    assert payload["totals"]["observations"] == 20
    assert payload["totals"]["pack_repeats_passed"] == 20
    assert payload["categories"][consistency.STABLE_ACCURATE] == 10
    assert len(payload["packs"]) == 10
    assert payload["packs"][6]["hidden_flakiness"] is True
    assert payload["packs"][6]["runs"][1]["attempts"] == 2
    assert "F015" in markdown
    assert "20/20" in markdown
    assert "stable-and-accurate" in markdown
    assert "hidden" in markdown.lower()
    assert json_path.name == "f015-repeat-run.json"
    assert md_path.name == "f015-repeat-run.md"


def test_consistency_artifacts_do_not_include_evidence_quotes(expected_packs, tmp_path):
    results = passing_results(expected_packs)
    results["01"] = completed(
        config.APPROVE, (), evidence=(("unit_price", "SECRET SUPPLIER QUOTE"),)
    )
    run, _ = run_with(results, repeats=2)
    json_path, md_path = consistency.write_artifacts(run, tmp_path)
    text = json_path.read_text(encoding="utf-8") + md_path.read_text(encoding="utf-8")
    assert "SECRET SUPPLIER QUOTE" not in text


def test_consistency_writers_never_touch_f011_files(expected_packs, tmp_path, monkeypatch):
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

    run, _ = run_with(passing_results(expected_packs), repeats=2)
    consistency.write_artifacts(run, tmp_path)

    assert json_sentinel.read_text(encoding="utf-8") == "KEEP JSON"
    assert md_sentinel.read_text(encoding="utf-8") == "KEEP MD"
    assert (tmp_path / "f015-repeat-run.json").exists()
    assert (tmp_path / "f015-repeat-run.md").exists()


def test_no_auto_retry(evaluation_packs=None):
    calls = []

    def failing_check(pdf_path, csv_path):
        calls.append((Path(pdf_path), Path(csv_path)))
        raise RuntimeError("boom")

    run = consistency.run_repeated_evaluation(
        repeats=2,
        check_fn=failing_check,
        pause_s=0,
        now=FIXED_NOW,
        git_state=("deadbeef", False),
    )
    assert len(calls) == 20
    assert run.total_observations == 20
    assert run.failed_runs == 20
