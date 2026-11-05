"""F011 live golden evaluation. Opt-in only: this calls the real model.

Run from the project root:

    $env:RUN_LIVE_EVALUATION="1"
    .venv\\Scripts\\python.exe -m pytest -m live -q

The run writes reports/evaluation/f011-golden-run.json and .md before asserting,
so a failed run still leaves diagnostic evidence behind.
"""
import pytest

from src.evaluation import (
    live_skip_reason,
    render_summary,
    run_evaluation,
    write_artifacts,
)

_SKIP = live_skip_reason()
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(_SKIP is not None, reason=_SKIP or "live evaluation disabled"),
]


def test_all_ten_packs_match_expected_json():
    run = run_evaluation()
    json_path, md_path = write_artifacts(run)
    summary = render_summary(run)
    print(summary)
    print(f"Artifacts: {json_path} | {md_path}")
    assert run.total == 10
    assert run.ok, summary
