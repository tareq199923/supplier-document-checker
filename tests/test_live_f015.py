"""F015 live synthetic and repeat-run evaluation. Opt-in only: calls the real model.

Run from the project root:

    $env:RUN_LIVE_F015="1"
    .venv\\Scripts\\python.exe -m pytest -m live -q -s

Both tests write their F015 artifacts before asserting, so a failed run still
leaves diagnostic evidence behind. The F011 artifacts are never touched.
"""
from __future__ import annotations

import pytest

from src import consistency, evaluation, synthetic

# Capture once, before either test writes an artifact, so the recorded git state
# describes the checkout that was evaluated rather than the generated evidence.
_GIT_STATE = evaluation.git_state()
_SKIP = evaluation.live_skip_reason(env_flag="RUN_LIVE_F015")
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(_SKIP is not None, reason=_SKIP or "live F015 disabled"),
]


def test_twenty_synthetic_variants_match_the_oracle(tmp_path):
    cases = synthetic.load_synthetic_cases()
    pdf_paths = synthetic.write_pdfs(cases, tmp_path)
    run = synthetic.run_synthetic_evaluation(
        cases,
        pdf_paths,
        pause_s=evaluation.PAUSE_SECONDS,
        git_state=_GIT_STATE,
    )
    json_path, md_path = synthetic.write_artifacts(run)
    summary = synthetic.render_summary(run)
    print(summary)
    print(f"Artifacts: {json_path} | {md_path}")

    assert run.total == 20
    assert run.ok, summary


def test_three_repeat_runs_are_stable_and_accurate():
    run = consistency.run_repeated_evaluation(
        repeats=3,
        pause_s=evaluation.PAUSE_SECONDS,
        git_state=_GIT_STATE,
    )
    json_path, md_path = consistency.write_artifacts(run)
    summary = consistency.render_summary(run)
    print(summary)
    print(f"Artifacts: {json_path} | {md_path}")

    assert run.repeats == 3
    assert run.total_observations == 30
    assert run.failed_runs == 0, summary
    assert all(pack.stable for pack in run.packs), summary
