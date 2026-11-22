"""The debrief bench: every fictional run gets the verdict a fair coach expects."""

import json

import pytest

from redline.evaluation import debrief_bench

CASES = json.loads(debrief_bench.CASES.read_text())


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_each_case_gets_its_expected_verdict_and_findings(case):
    result = debrief_bench.outcome(case)
    assert result.passed, result


def test_the_bench_counts_an_unjustified_reproach_and_a_missed_intervention(tmp_path):
    case = next(case for case in CASES if case["id"] == "group-hanging-on")
    wrong = {**case, "expected": {"verdict": "conforming", "findings": ["descent_not_held"]}}
    result = debrief_bench.outcome(wrong)
    assert not result.verdict_matches
    assert result.unjustified == ("flat_too_fast",)
    assert result.missed == ("descent_not_held",)


def test_an_rpe_spoken_of_without_a_second_sign_is_an_unjustified_reproach():
    silent = next(case for case in CASES if case["id"] == "rpe-above-without-a-second-sign")
    spoken = next(case for case in CASES if case["id"] == "rpe-above-after-a-poor-night")
    assert debrief_bench.outcome({**spoken, "expected": silent["expected"]}).unjustified == (
        "rpe_above",)
    assert debrief_bench.outcome({**silent, "expected": spoken["expected"]}).missed == (
        "rpe_above",)


def test_the_command_fails_when_a_case_fails(tmp_path, monkeypatch, capsys):
    from redline.cli import evaluate_debriefs

    case = next(case for case in CASES if case["id"] == "flat-easy-run")
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([{**case, "expected": {"verdict": "execution_gap",
                                                      "findings": []}}]))
    monkeypatch.setattr("sys.argv", ["evaluate-debriefs", "--cases", str(path)])
    assert evaluate_debriefs.main() == 1
    assert "0 unjustified reproaches, 0 missed interventions" in capsys.readouterr().out
    monkeypatch.setattr("sys.argv", ["evaluate-debriefs"])
    assert evaluate_debriefs.main() == 0
