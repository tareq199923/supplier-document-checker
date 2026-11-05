"""F015: repeat-run consistency over the existing ten-pack evaluation.

This module calls evaluation.run_evaluation N times. It does not add a business
rule, does not retry a failed model call, and does not change the F011 pass rule.
It only records what happened in each run and groups the results by pack.

Categories:
  stable-and-accurate      identical result in every run, every run passed
  stable-but-inaccurate    identical result in every run, but not a pass
  flaky                    results differ between runs
  infrastructure-or-harness  a rate-limit/LLM/harness/app failure occurred; this
                           is reported separately and never called flakiness

"Stable" is identical (status, outcome, decision, rule IDs) across all runs;
attempts are deliberately excluded so a hidden retry stays visible.
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from src import evaluation
from src.extractor import PROMPT_VERSION
from src.result import check_supplier

ROOT = evaluation.ROOT
EVALUATION_DIR = evaluation.EVALUATION_DIR
PAUSE_SECONDS = evaluation.PAUSE_SECONDS
DEFAULT_REPEATS = 3
REPEATS_ENV = "EVALUATION_REPEATS"

STABLE_ACCURATE = "stable-and-accurate"
STABLE_INACCURATE = "stable-but-inaccurate"
FLAKY = "flaky"
INFRASTRUCTURE_OR_HARNESS = "infrastructure-or-harness"
CATEGORIES = (
    STABLE_ACCURATE,
    STABLE_INACCURATE,
    FLAKY,
    INFRASTRUCTURE_OR_HARNESS,
)

_FAILURE_OUTCOMES = frozenset(
    {
        evaluation.OUTCOME_INFRASTRUCTURE,
        evaluation.OUTCOME_HARNESS,
        evaluation.OUTCOME_APP_ERROR,
    }
)


class ConsistencyError(Exception):
    """The repeat-run configuration is not usable."""


@dataclass(frozen=True)
class RunObservation:
    run_index: int
    status: str
    outcome: str
    decision: str | None
    rule_ids: tuple[str, ...]
    attempts: int
    error_code: str | None
    error_stage: str | None


@dataclass(frozen=True)
class PackRepeat:
    pack: str
    pdf_name: str
    expected_decision: str
    expected_rule_ids: tuple[str, ...]
    runs: tuple[RunObservation, ...]
    stable: bool
    category: str
    hidden_flakiness: bool
    retry_runs: tuple[int, ...]
    failure_runs: tuple[int, ...]
    decision_distribution: tuple[tuple[str | None, int], ...]
    rule_distribution: tuple[tuple[tuple[str, ...], int], ...]
    problems: tuple[str, ...]


@dataclass(frozen=True)
class ConsistencyRun:
    run_at: str
    provider: str
    model: str
    prompt_version: str
    repeats: int
    git_commit: str
    git_dirty: bool | None
    expected_source: str
    purchase_request: str
    packs: tuple[PackRepeat, ...]

    @property
    def total_observations(self) -> int:
        return sum(len(pack.runs) for pack in self.packs)

    @property
    def passed_runs(self) -> int:
        return sum(
            1
            for pack in self.packs
            for run in pack.runs
            if run.outcome == evaluation.OUTCOME_PASS
        )

    @property
    def failed_runs(self) -> int:
        return self.total_observations - self.passed_runs

    @property
    def stable_packs(self) -> int:
        return sum(1 for pack in self.packs if pack.stable)

    @property
    def hidden_flakiness_packs(self) -> int:
        return sum(1 for pack in self.packs if pack.hidden_flakiness)

    def counts_by_category(self) -> dict[str, int]:
        counts = {name: 0 for name in CATEGORIES}
        for pack in self.packs:
            counts[pack.category] = counts.get(pack.category, 0) + 1
        return counts


# ---- repeat count --------------------------------------------------------------
def resolve_repeats(repeats: int | None = None) -> int:
    """Explicit repeats win; otherwise EVALUATION_REPEATS overrides the default 3."""
    if repeats is not None:
        if (
            isinstance(repeats, bool)
            or not isinstance(repeats, int)
            or repeats <= 0
        ):
            raise ConsistencyError(
                f"repeats must be a positive integer, got {repeats!r}"
            )
        return repeats

    raw = os.getenv(REPEATS_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_REPEATS
    cleaned = raw.strip()
    if not cleaned.isdigit() or int(cleaned) <= 0:
        raise ConsistencyError(
            f"{REPEATS_ENV} must be a positive integer, got {raw!r}"
        )
    return int(cleaned)


# ---- aggregation ---------------------------------------------------------------
def _observation(index: int, assessment: evaluation.PackAssessment) -> RunObservation:
    return RunObservation(
        run_index=index,
        status=assessment.status,
        outcome=assessment.outcome,
        decision=assessment.actual_decision,
        rule_ids=tuple(sorted(assessment.actual_rule_ids)),
        attempts=assessment.attempts,
        error_code=assessment.error_code,
        error_stage=assessment.error_stage,
    )


def _decision_distribution(
    observations: tuple[RunObservation, ...],
) -> tuple[tuple[str | None, int], ...]:
    counts = Counter(item.decision for item in observations)
    return tuple(
        sorted(
            counts.items(),
            key=lambda pair: (pair[0] is None, pair[0] or ""),
        )
    )


def _rule_distribution(
    observations: tuple[RunObservation, ...],
) -> tuple[tuple[tuple[str, ...], int], ...]:
    counts = Counter(item.rule_ids for item in observations)
    return tuple(sorted(counts.items(), key=lambda pair: pair[0]))


def _classify_pack(
    stable: bool,
    failure_runs: tuple[int, ...],
    observations: tuple[RunObservation, ...],
) -> str:
    if failure_runs:
        return INFRASTRUCTURE_OR_HARNESS
    if not stable:
        return FLAKY
    if all(item.outcome == evaluation.OUTCOME_PASS for item in observations):
        return STABLE_ACCURATE
    return STABLE_INACCURATE


def _pack_repeat(
    pack: str,
    assessments: list[evaluation.PackAssessment],
) -> PackRepeat:
    observations = tuple(
        _observation(index, assessment)
        for index, assessment in enumerate(assessments, start=1)
    )
    first = assessments[0]
    identities = {
        (item.status, item.outcome, item.decision, item.rule_ids)
        for item in observations
    }
    stable = len(identities) == 1
    failure_runs = tuple(
        item.run_index
        for item in observations
        if item.outcome in _FAILURE_OUTCOMES
    )
    retry_runs = tuple(
        item.run_index for item in observations if item.attempts >= 2
    )
    hidden_flakiness = stable and any(
        item.outcome == evaluation.OUTCOME_PASS and item.attempts >= 2
        for item in observations
    )
    category = _classify_pack(stable, failure_runs, observations)

    problems: list[str] = []
    for index, assessment in enumerate(assessments, start=1):
        if assessment.passed:
            continue
        if not assessment.problems:
            problems.append(f"run {index}: outcome {assessment.outcome}")
        for problem in assessment.problems:
            problems.append(f"run {index}: {problem}")

    return PackRepeat(
        pack=pack,
        pdf_name=first.pdf_name,
        expected_decision=first.expected_decision,
        expected_rule_ids=tuple(first.expected_rule_ids),
        runs=observations,
        stable=stable,
        category=category,
        hidden_flakiness=hidden_flakiness,
        retry_runs=retry_runs,
        failure_runs=failure_runs,
        decision_distribution=_decision_distribution(observations),
        rule_distribution=_rule_distribution(observations),
        problems=tuple(problems),
    )


def _display_path(path: object) -> str:
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except (OSError, ValueError):
        return str(path)


def run_repeated_evaluation(
    *,
    repeats: int | None = None,
    check_fn=check_supplier,
    expected_path: object = evaluation.EXPECTED_PATH,
    samples_dir: object = evaluation.SAMPLES_DIR,
    purchase_request_path: object = evaluation.PURCHASE_REQUEST_PATH,
    pause_s: float = PAUSE_SECONDS,
    now: datetime | None = None,
    git_state: tuple[str, bool | None] | None = None,
) -> ConsistencyRun:
    """Run the existing ten-pack evaluation N times and aggregate the results."""
    repeat_count = resolve_repeats(repeats)
    commit, dirty = git_state if git_state is not None else evaluation.git_state()
    run_at = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    provider = (os.getenv("LLM_PROVIDER") or "").strip().lower() or "unknown"
    model = (os.getenv("LLM_MODEL") or "").strip() or "unknown"

    evaluation_runs: list[evaluation.EvaluationRun] = []
    for index in range(repeat_count):
        run = evaluation.run_evaluation(
            check_fn=check_fn,
            expected_path=expected_path,
            samples_dir=samples_dir,
            purchase_request_path=purchase_request_path,
            pause_s=pause_s,
            now=now,
        )
        evaluation_runs.append(
            replace(run, git_commit=commit, git_dirty=dirty)
        )
        if pause_s > 0 and index < repeat_count - 1:
            time.sleep(pause_s)

    first = evaluation_runs[0]
    pack_order = [item.pack for item in first.assessments]
    by_run: list[dict[str, evaluation.PackAssessment]] = []
    for run in evaluation_runs:
        mapping = {item.pack: item for item in run.assessments}
        if [item.pack for item in run.assessments] != pack_order:
            raise ConsistencyError("repeat runs did not assess the same packs")
        by_run.append(mapping)

    packs = tuple(
        _pack_repeat(
            pack,
            [mapping[pack] for mapping in by_run],
        )
        for pack in pack_order
    )
    return ConsistencyRun(
        run_at=run_at,
        provider=provider,
        model=model,
        prompt_version=PROMPT_VERSION,
        repeats=repeat_count,
        git_commit=commit,
        git_dirty=dirty,
        expected_source=_display_path(expected_path),
        purchase_request=_display_path(purchase_request_path),
        packs=packs,
    )


# ---- own F015 payload and Markdown ---------------------------------------------
def _rule_text(rule_ids: tuple[str, ...]) -> str:
    return ", ".join(rule_ids) if rule_ids else "-"


def _decision_text(decision: str | None) -> str:
    return decision if decision is not None else "No decision"


def _distribution_text(distribution: tuple[tuple[str | None, int], ...]) -> str:
    return "; ".join(
        f"{_decision_text(name)}: {count}" for name, count in distribution
    )


def _rule_distribution_text(
    distribution: tuple[tuple[tuple[str, ...], int], ...],
) -> str:
    return "; ".join(
        f"{_rule_text(rules)}: {count}" for rules, count in distribution
    )


def run_payload(run: ConsistencyRun) -> dict:
    """JSON-safe F015 audit record. No evidence quotes or raw document text."""
    return {
        "feature": "F015",
        "kind": "repeat-run",
        "run_at": run.run_at,
        "provider": run.provider,
        "model": run.model,
        "prompt_version": run.prompt_version,
        "repeats": run.repeats,
        "git_commit": run.git_commit,
        "git_dirty": run.git_dirty,
        "expected_source": run.expected_source,
        "purchase_request": run.purchase_request,
        "stable_definition": (
            "identical (status, outcome, decision, rule IDs) across all runs; "
            "attempts are reported separately"
        ),
        "totals": {
            "packs": len(run.packs),
            "repeats": run.repeats,
            "observations": run.total_observations,
            "pack_repeats_passed": run.passed_runs,
            "pack_repeats_failed": run.failed_runs,
            "stable_packs": run.stable_packs,
            "hidden_flakiness_packs": run.hidden_flakiness_packs,
        },
        "categories": run.counts_by_category(),
        "packs": [
            {
                "pack": pack.pack,
                "pdf": pack.pdf_name,
                "expected": {
                    "decision": pack.expected_decision,
                    "rule_ids": list(pack.expected_rule_ids),
                },
                "runs": [
                    {
                        "run": item.run_index,
                        "status": item.status,
                        "outcome": item.outcome,
                        "decision": item.decision,
                        "rule_ids": list(item.rule_ids),
                        "attempts": item.attempts,
                        "error_code": item.error_code,
                        "error_stage": item.error_stage,
                    }
                    for item in pack.runs
                ],
                "stable": pack.stable,
                "category": pack.category,
                "hidden_flakiness": pack.hidden_flakiness,
                "retry_runs": list(pack.retry_runs),
                "failure_runs": list(pack.failure_runs),
                "decision_distribution": [
                    {"decision": name, "count": count}
                    for name, count in pack.decision_distribution
                ],
                "rule_distribution": [
                    {"rule_ids": list(rules), "count": count}
                    for rules, count in pack.rule_distribution
                ],
                "problems": list(pack.problems),
            }
            for pack in run.packs
        ],
    }


def render_summary(run: ConsistencyRun) -> str:
    """Plain Markdown summary of one F015 repeat run."""
    if run.git_dirty is True:
        git_note = " (working tree dirty)"
    elif run.git_dirty is False:
        git_note = " (working tree clean)"
    else:
        git_note = " (git state unknown)"
    categories = run.counts_by_category()
    category_line = ", ".join(
        f"{name}: {count}" for name, count in categories.items()
    )

    lines = [
        "# F015 repeat-run consistency evaluation",
        "",
        f"- Run at: {run.run_at}",
        f"- Provider: {run.provider}",
        f"- Model: {run.model}",
        f"- Prompt version: {run.prompt_version}",
        f"- Repeats: {run.repeats}",
        f"- Git commit: {run.git_commit}{git_note}",
        f"- Purchase request: {run.purchase_request}",
        f"- Expected source: {run.expected_source}",
        f"- Categories: {category_line}",
        f"- Hidden flakiness: {run.hidden_flakiness_packs} pack(s) with a stable result but a passing retry (attempts == 2)",
        "",
        (
            f"**Result: {run.passed_runs}/{run.total_observations} pack runs matched "
            f"expected.json; {run.stable_packs}/{len(run.packs)} packs stable.**"
        ),
        "",
        "| Pack | Expected | Stable | Category | Hidden retry | Retry runs |",
        "|---|---|---|---|---|---|",
    ]
    for pack in run.packs:
        lines.append(
            f"| {pack.pack} | {pack.expected_decision} | {pack.stable} "
            f"| {pack.category} | {pack.hidden_flakiness} "
            f"| {', '.join(str(item) for item in pack.retry_runs) or '-'} |"
        )

    lines += [
        "",
        "## Per-run results",
        "",
        "| Pack | Run | Status | Outcome | Decision | Rules | Attempts |",
        "|---|---|---|---|---|---|---|",
    ]
    for pack in run.packs:
        for item in pack.runs:
            lines.append(
                f"| {pack.pack} | {item.run_index} | {item.status} "
                f"| {item.outcome} | {_decision_text(item.decision)} "
                f"| {_rule_text(item.rule_ids)} | {item.attempts} |"
            )

    lines += [
        "",
        "## Distributions",
        "",
        "| Pack | Decision distribution | Rule distribution |",
        "|---|---|---|",
    ]
    for pack in run.packs:
        lines.append(
            f"| {pack.pack} | {_distribution_text(pack.decision_distribution)} "
            f"| {_rule_distribution_text(pack.rule_distribution)} |"
        )

    if run.failed_runs:
        lines += ["", "## Failed runs", ""]
        for pack in run.packs:
            if not pack.problems:
                continue
            lines.append(f"### Pack {pack.pack} - {pack.category}")
            lines.append("")
            for problem in pack.problems:
                lines.append(f"- {problem}")
            lines.append("")

    lines += [
        "Stable means identical (status, outcome, decision, rule IDs) across all runs; attempts are excluded.",
        "Infrastructure/harness failures are reported in their own category and are not counted as flakiness.",
        "No automatic retry is performed by this harness.",
        f"The prompt is frozen at `{run.prompt_version}`.",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def write_artifacts(
    run: ConsistencyRun, out_dir: object = EVALUATION_DIR
) -> tuple[Path, Path]:
    """Write the F015 repeat-run JSON and Markdown evidence files."""
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "f015-repeat-run.json"
    md_path = directory / "f015-repeat-run.md"
    json_path.write_text(
        json.dumps(run_payload(run), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(render_summary(run), encoding="utf-8")
    return json_path, md_path
