"""F011: live golden evaluation across the ten supplier sample packs.

This module only READS the approved expectation and the sample files, calls the
existing public entry point `check_supplier` once per pack, and compares the
returned CheckResult with expected.json. It adds no business rule and never
re-runs the workflow by hand.

The live run is opt-in (RUN_LIVE_EVALUATION=1). Offline tests inject a fake
checker, so the normal test suite never calls a model.

Pass rule: status "completed", the purchase-request match succeeded, the
decision is exactly expected.json's decision, and the sorted rule-ID list is
exactly expected.json's list. A legitimate "Needs a person to check" from a
business rule is a completed result, so it can pass. A Gate 2 failure also says
"Needs a person to check" but has status "needs_review", so it never passes.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.extractor import PROMPT_VERSION
from src.result import (
    DOCUMENT_UNREADABLE,
    GATE2_FAILED,
    LLM_ERROR,
    SOURCE_RULE,
    STATUS_COMPLETED,
    STATUS_ERROR,
    STATUS_NEEDS_REVIEW,
    CheckResult,
    check_supplier,
)

ROOT = Path(__file__).resolve().parent.parent
EXPECTED_PATH = ROOT / "expected.json"
SAMPLES_DIR = ROOT / "samples"
PURCHASE_REQUEST_PATH = SAMPLES_DIR / "purchase-request.csv"
EVALUATION_DIR = ROOT / "reports" / "evaluation"

PAUSE_SECONDS = 1.0
GIT_TIMEOUT_SECONDS = 5

# Outcome names. Every one except PASS is a failed evaluation; they are kept
# apart so accuracy, extraction, infrastructure and harness problems are clear.
OUTCOME_PASS = "pass"
OUTCOME_ACCURACY = "accuracy"
OUTCOME_EXTRACTION = "extraction"
OUTCOME_DOCUMENT = "document"
OUTCOME_INFRASTRUCTURE = "infrastructure"
OUTCOME_APP_ERROR = "app_error"
OUTCOME_HARNESS = "harness"
OUTCOMES = (
    OUTCOME_PASS,
    OUTCOME_ACCURACY,
    OUTCOME_EXTRACTION,
    OUTCOME_DOCUMENT,
    OUTCOME_INFRASTRUCTURE,
    OUTCOME_APP_ERROR,
    OUTCOME_HARNESS,
)

_PDF_RE = re.compile(r"^supplier-pack-(\d{2})\.pdf$", re.IGNORECASE)


class EvaluationError(Exception):
    """The evaluation setup is unusable (expected file, samples, ...)."""


@dataclass(frozen=True)
class PackCase:
    pack: str
    pdf_path: Path


@dataclass(frozen=True)
class PackAssessment:
    pack: str
    pdf_name: str
    expected_supplier: str | None
    expected_decision: str
    expected_rule_ids: tuple[str, ...]
    actual_supplier: str | None
    actual_decision: str | None
    actual_rule_ids: tuple[str, ...]
    status: str
    attempts: int
    reason_codes: tuple[str, ...]
    match_ok: bool | None
    error_code: str | None
    error_message: str | None
    error_stage: str | None
    error_retryable: bool | None
    outcome: str
    problems: tuple[str, ...]
    duration_s: float

    @property
    def passed(self) -> bool:
        return self.outcome == OUTCOME_PASS


@dataclass(frozen=True)
class EvaluationRun:
    run_at: str
    provider: str
    model: str
    prompt_version: str
    git_commit: str
    git_dirty: bool | None
    expected_source: str
    purchase_request: str
    assessments: tuple[PackAssessment, ...]

    @property
    def total(self) -> int:
        return len(self.assessments)

    @property
    def passed(self) -> int:
        return sum(1 for item in self.assessments if item.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def ok(self) -> bool:
        return self.total > 0 and self.failed == 0

    def counts_by_outcome(self) -> dict[str, int]:
        counts = {outcome: 0 for outcome in OUTCOMES}
        for item in self.assessments:
            counts[item.outcome] = counts.get(item.outcome, 0) + 1
        return counts


# ---- expected data and sample files ---------------------------------------------
def load_expected(path: object = EXPECTED_PATH) -> dict[str, dict]:
    """Return the pack map for the one purchase request in expected.json."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationError(f"expected file cannot be read: {type(exc).__name__}") from exc
    if not isinstance(data, dict) or not data:
        raise EvaluationError("expected.json must contain one purchase request")
    if len(data) != 1:
        raise EvaluationError("expected.json must contain exactly one purchase request")
    packs = next(iter(data.values()))
    if not isinstance(packs, dict) or not packs:
        raise EvaluationError("expected.json has no supplier packs")
    for pack, entry in packs.items():
        if not isinstance(entry, dict):
            raise EvaluationError(f"expected.json pack {pack!r} is not an object")
        decision = entry.get("decision")
        if not isinstance(decision, str) or not decision.strip():
            raise EvaluationError(f"expected.json pack {pack!r} has no decision")
        rule_ids = entry.get("rule_ids")
        if not isinstance(rule_ids, list) or any(not isinstance(rule, str) for rule in rule_ids):
            raise EvaluationError(f"expected.json pack {pack!r} has no rule_ids list")
    return packs


def discover_packs(samples_dir: object = SAMPLES_DIR) -> dict[str, Path]:
    """Map pack id ("01", ...) to supplier-pack-NN.pdf in the samples folder."""
    directory = Path(samples_dir)
    if not directory.is_dir():
        raise EvaluationError(f"samples folder not found: {directory}")
    found: dict[str, Path] = {}
    for path in directory.iterdir():
        match = _PDF_RE.match(path.name)
        if match and path.is_file():
            found[match.group(1)] = path
    if not found:
        raise EvaluationError("no supplier-pack-NN.pdf files found")
    return dict(sorted(found.items()))


def load_cases(
    expected: dict[str, dict], samples_dir: object = SAMPLES_DIR
) -> tuple[PackCase, ...]:
    """Pair every expected pack with its PDF, refusing anything unmatched."""
    discovered = discover_packs(samples_dir)
    missing = sorted(set(expected) - set(discovered))
    extra = sorted(set(discovered) - set(expected))
    if missing or extra:
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if extra:
            details.append("unexpected: " + ", ".join(extra))
        raise EvaluationError(
            "sample packs do not match expected.json (" + "; ".join(details) + ")"
        )
    return tuple(
        PackCase(pack=pack, pdf_path=discovered[pack]) for pack in sorted(expected)
    )


# ---- one assessment --------------------------------------------------------------
def _expected_values(entry: dict) -> tuple[str, tuple[str, ...], str | None]:
    decision = str(entry["decision"])
    rule_ids = tuple(sorted(str(rule) for rule in entry.get("rule_ids", ())))
    supplier = entry.get("supplier")
    if not isinstance(supplier, str) or not supplier.strip():
        supplier = None
    return decision, rule_ids, supplier


def _rule_ids(result: CheckResult) -> tuple[str, ...]:
    return tuple(
        sorted(reason.code for reason in result.reasons if reason.source == SOURCE_RULE)
    )


def _classify(
    result: CheckResult,
    expected_decision: str,
    expected_rules: tuple[str, ...],
    match_ok: bool | None,
) -> str:
    if result.status == STATUS_COMPLETED:
        if (
            result.decision == expected_decision
            and _rule_ids(result) == expected_rules
            and match_ok is True
        ):
            return OUTCOME_PASS
        return OUTCOME_ACCURACY
    if result.status == STATUS_NEEDS_REVIEW:
        codes = {reason.code for reason in result.reasons}
        if GATE2_FAILED in codes:
            return OUTCOME_EXTRACTION
        if DOCUMENT_UNREADABLE in codes:
            return OUTCOME_DOCUMENT
        return OUTCOME_EXTRACTION
    if result.status == STATUS_ERROR:
        if result.error is not None and result.error.code == LLM_ERROR:
            return OUTCOME_INFRASTRUCTURE
        return OUTCOME_APP_ERROR
    return OUTCOME_APP_ERROR


def assess(
    case: PackCase,
    result: CheckResult,
    expected_entry: dict,
    *,
    duration_s: float = 0.0,
) -> PackAssessment:
    """Compare one CheckResult with expected.json; never calls a model."""
    expected_decision, expected_rules, expected_supplier = _expected_values(expected_entry)
    actual_rules = _rule_ids(result)
    match_ok = result.match.ok if result.match is not None else None
    problems: list[str] = []

    if result.status != STATUS_COMPLETED:
        problems.append(f"status: expected {STATUS_COMPLETED!r}, got {result.status!r}")
    if result.decision != expected_decision:
        problems.append(
            f"decision: expected {expected_decision!r}, got {result.decision!r}"
        )
    missing = [rule for rule in expected_rules if rule not in actual_rules]
    extra = [rule for rule in actual_rules if rule not in expected_rules]
    if missing:
        problems.append("missing rule(s): " + ", ".join(missing))
    if extra:
        problems.append("extra rule(s): " + ", ".join(extra))
    if result.status == STATUS_COMPLETED and match_ok is not True:
        problems.append("purchase-request match did not succeed")
        if result.match is not None and result.match.problems:
            problems.append("match problems: " + " ".join(result.match.problems))
    if result.status == STATUS_ERROR and result.error is not None:
        problems.append(f"error: {result.error.code}: {result.error.message}")
    for reason in result.reasons:
        if reason.code in (GATE2_FAILED, DOCUMENT_UNREADABLE):
            problems.append(f"{reason.code}: {reason.message}")
    if result.status not in (STATUS_COMPLETED, STATUS_NEEDS_REVIEW, STATUS_ERROR):
        problems.append(f"unrecognised status: {result.status!r}")
    if result.status == STATUS_NEEDS_REVIEW and not any(
        reason.code in (GATE2_FAILED, DOCUMENT_UNREADABLE) for reason in result.reasons
    ):
        problems.append("review result without a recognised reason code")

    return PackAssessment(
        pack=case.pack,
        pdf_name=case.pdf_path.name,
        expected_supplier=expected_supplier,
        expected_decision=expected_decision,
        expected_rule_ids=expected_rules,
        actual_supplier=result.supplier_name,
        actual_decision=result.decision,
        actual_rule_ids=actual_rules,
        status=result.status,
        attempts=result.attempts,
        reason_codes=tuple(reason.code for reason in result.reasons),
        match_ok=match_ok,
        error_code=result.error.code if result.error is not None else None,
        error_message=result.error.message if result.error is not None else None,
        error_stage=result.error.stage if result.error is not None else None,
        error_retryable=result.error.retryable if result.error is not None else None,
        outcome=_classify(result, expected_decision, expected_rules, match_ok),
        problems=tuple(problems),
        duration_s=duration_s,
    )


def _harness_assessment(
    case: PackCase, expected_entry: dict, error: Exception, duration_s: float
) -> PackAssessment:
    expected_decision, expected_rules, expected_supplier = _expected_values(expected_entry)
    detail = str(error) or type(error).__name__
    return PackAssessment(
        pack=case.pack,
        pdf_name=case.pdf_path.name,
        expected_supplier=expected_supplier,
        expected_decision=expected_decision,
        expected_rule_ids=expected_rules,
        actual_supplier=None,
        actual_decision=None,
        actual_rule_ids=(),
        status="harness",
        attempts=0,
        reason_codes=(),
        match_ok=None,
        error_code=type(error).__name__,
        error_message=detail,
        error_stage="harness",
        error_retryable=False,
        outcome=OUTCOME_HARNESS,
        problems=(f"harness error: {type(error).__name__}: {detail}",),
        duration_s=duration_s,
    )


# ---- the runner -------------------------------------------------------------------
def git_state(root: object = ROOT) -> tuple[str, bool | None]:
    """Best-effort HEAD revision and dirty flag. Never raises."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
        if commit.returncode != 0 or not commit.stdout.strip():
            return "unknown", None
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
        dirty = bool(status.stdout.strip()) if status.returncode == 0 else None
        return commit.stdout.strip(), dirty
    except (OSError, subprocess.SubprocessError):
        return "unknown", None


def _display_path(path: object) -> str:
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except (OSError, ValueError):
        return str(path)


def run_evaluation(
    *,
    check_fn=check_supplier,
    expected_path: object = EXPECTED_PATH,
    samples_dir: object = SAMPLES_DIR,
    purchase_request_path: object = PURCHASE_REQUEST_PATH,
    pause_s: float = PAUSE_SECONDS,
    now: datetime | None = None,
) -> EvaluationRun:
    """Run the real checker over every expected pack and assess each result."""
    expected = load_expected(expected_path)
    cases = load_cases(expected, samples_dir)
    provider = (os.getenv("LLM_PROVIDER") or "").strip().lower() or "unknown"
    model = (os.getenv("LLM_MODEL") or "").strip() or "unknown"
    git_commit, git_dirty = git_state()
    run_at = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")

    assessments: list[PackAssessment] = []
    for index, case in enumerate(cases):
        start = time.perf_counter()
        try:
            result = check_fn(case.pdf_path, purchase_request_path)
        except Exception as exc:  # harness bug: record it, keep the other packs
            assessments.append(
                _harness_assessment(
                    case, expected[case.pack], exc, time.perf_counter() - start
                )
            )
        else:
            assessments.append(
                assess(
                    case,
                    result,
                    expected[case.pack],
                    duration_s=time.perf_counter() - start,
                )
            )
        if pause_s > 0 and index < len(cases) - 1:
            time.sleep(pause_s)

    return EvaluationRun(
        run_at=run_at,
        provider=provider,
        model=model,
        prompt_version=PROMPT_VERSION,
        git_commit=git_commit,
        git_dirty=git_dirty,
        expected_source=_display_path(expected_path),
        purchase_request=_display_path(purchase_request_path),
        assessments=tuple(assessments),
    )


# ---- summary and artifact ----------------------------------------------------------
def _rule_text(rule_ids: tuple[str, ...]) -> str:
    return ", ".join(rule_ids) if rule_ids else "-"


def render_summary(run: EvaluationRun) -> str:
    """Plain Markdown summary of one evaluation run."""
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
        "# F011 live golden evaluation",
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
        f"**Result: {run.passed}/{run.total} packs matched expected.json.**",
        "",
        "| Pack | Expected | Actual | Rules (expected -> actual) | Status | Calls | Seconds | Outcome |",
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
        lines += ["", "## Failed packs", ""]
        for item in run.assessments:
            if item.passed:
                continue
            lines.append(f"### Pack {item.pack} - {item.outcome}")
            lines.append("")
            for problem in item.problems:
                lines.append(f"- {problem}")
            if item.reason_codes:
                lines.append(f"- reasons: {', '.join(item.reason_codes)}")
            lines.append("")
    else:
        lines += ["", "All ten packs matched `expected.json`.", ""]

    lines += [
        "Model calls are the official `CheckResult.attempts` count, as far as known.",
        f"The prompt is frozen at `{run.prompt_version}` and `expected.json` was not changed for this run.",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _assessment_payload(item: PackAssessment) -> dict:
    return {
        "pack": item.pack,
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


def run_payload(run: EvaluationRun) -> dict:
    """JSON-safe audit record. No evidence quotes or raw document text."""
    return {
        "feature": "F011",
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
        "packs": [_assessment_payload(item) for item in run.assessments],
    }


def write_artifacts(
    run: EvaluationRun, out_dir: object = EVALUATION_DIR
) -> tuple[Path, Path]:
    """Write the JSON and Markdown evidence files; return their paths."""
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "f011-golden-run.json"
    md_path = directory / "f011-golden-run.md"
    json_path.write_text(
        json.dumps(run_payload(run), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(render_summary(run), encoding="utf-8")
    return json_path, md_path


# ---- opt-in guard -------------------------------------------------------------------
def live_skip_reason(env_flag: str = "RUN_LIVE_EVALUATION") -> str | None:
    """None when a live run is allowed; otherwise the reason to skip it.

    ``env_flag`` lets F015 use its own opt-in flag without changing F011's
    default behaviour.
    """
    if (os.getenv(env_flag) or "").strip() != "1":
        return f"live evaluation is opt-in: set {env_flag}=1"
    provider = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if not provider:
        return "LLM_PROVIDER is not set"
    if provider == "mock":
        return "LLM_PROVIDER=mock is not a live run"
    if provider not in {"anthropic", "openai", "gemini"}:
        return f"unsupported live provider: {provider!r}"
    if not (os.getenv("LLM_MODEL") or "").strip():
        return "LLM_MODEL is not set"
    if provider == "anthropic" and not os.getenv("ANTHROPIC_API_KEY"):
        return "ANTHROPIC_API_KEY is not set"
    if provider == "gemini" and not os.getenv("GEMINI_API_KEY"):
        return "GEMINI_API_KEY is not set"
    if (
        provider == "openai"
        and not os.getenv("OPENAI_API_KEY")
        and not os.getenv("OPENAI_BASE_URL")
    ):
        return "OPENAI_API_KEY is not set and OPENAI_BASE_URL is empty"
    return None
