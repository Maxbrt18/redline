"""What each provider call consumed: parsed from the Claude result, recorded, projected."""

import datetime as dt
import json
import subprocess
from unittest.mock import patch

from redline.analysis import agent_costs
from redline.model.vocabulary import CallPurpose
from redline.service.claude import ClaudeCodeRunner, reported_call
from redline.storage.repository.agent_calls import CallTally, CallWindow

RESULT = {
    "type": "result", "result": "ok", "is_error": False, "duration_ms": 8400,
    "total_cost_usd": 0.0731,
    "usage": {"input_tokens": 1200, "output_tokens": 640,
              "cache_read_input_tokens": 52000, "cache_creation_input_tokens": 3100},
}


def _runner(make_config, store):
    return ClaudeCodeRunner(make_config(agent_provider="claude"), store)


class _Proc:
    def __init__(self, lines):
        self.stdout = iter(lines)
        self.stderr = iter(())

    def wait(self):
        return 0

    def kill(self):  # pragma: no cover - watchdog path not exercised here
        pass

    def terminate(self):  # pragma: no cover - /cancel path not exercised here
        pass


def _stream(result):
    return [json.dumps({"type": "system", "session_id": "s-1"}),
            json.dumps({"type": "assistant", "message": {
                "model": "claude-sonnet-5-5", "content": [{"type": "text", "text": "ok"}]}}),
            json.dumps(result)]


def test_a_result_event_gives_its_tokens_cost_and_duration():
    call = reported_call(RESULT, CallPurpose.TURN, "sonnet", psid="4242", turn_id="t-1")

    assert call is not None
    assert (call.cost_usd, call.input_tokens, call.output_tokens) == (0.0731, 1200, 640)
    assert (call.cache_read_tokens, call.cache_write_tokens) == (52000, 3100)
    assert (call.duration_ms, call.psid, call.turn_id) == (8400, "4242", "t-1")


def test_a_result_without_usage_or_cost_reports_nothing():
    assert reported_call({"type": "result", "result": "ok"}, CallPurpose.TURN, None) is None


def test_a_malformed_count_reads_as_zero_and_a_negative_cost_as_unknown():
    call = reported_call({"usage": {"input_tokens": "many", "output_tokens": -3},
                          "total_cost_usd": -1}, CallPurpose.TRIAGE, None)

    assert call is not None
    assert (call.input_tokens, call.output_tokens, call.cost_usd) == (0, 0, None)


def test_a_turn_records_its_call_with_the_model_that_answered(make_config, store, monkeypatch):
    monkeypatch.setattr("redline.service.claude.subprocess.Popen",
                        lambda _command, **_kwargs: _Proc(_stream(RESULT)))

    _runner(make_config, store).run("4242", "salut", turn_id="t-9")

    [call] = store.calls
    assert (call.purpose, call.turn_id, call.psid) == (CallPurpose.TURN, "t-9", "4242")
    assert call.model == "claude-sonnet-5-5"
    assert call.cost_usd == 0.0731


def test_a_failed_record_never_costs_the_reply(make_config, store, monkeypatch):
    def refuse(_call):
        raise RuntimeError("database down")

    monkeypatch.setattr(store, "record_call", refuse)
    monkeypatch.setattr("redline.service.claude.subprocess.Popen",
                        lambda _command, **_kwargs: _Proc(_stream(RESULT)))

    assert _runner(make_config, store).run("4242", "salut", turn_id="t-9").full_text == "ok"


def test_a_triage_records_its_call_even_when_its_answer_is_unreadable(make_config, store):
    completed = subprocess.CompletedProcess(args=[], returncode=0, stderr="",
                                            stdout=json.dumps(RESULT))
    with patch("subprocess.run", return_value=completed):
        _runner(make_config, store).classify("salut")

    [call] = store.calls
    assert (call.purpose, call.turn_id) == (CallPurpose.TRIAGE, None)


NOW = dt.datetime(2030, 6, 30, tzinfo=dt.UTC)


def _tally(route, cost, **tokens):
    counts = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
              "cache_write_tokens": 0, **tokens}
    return CallTally(purpose="turn", route=route, model="sonnet", calls=2, turns=1,
                     cost_usd=cost, **counts)


def test_the_projection_divides_by_the_days_the_register_covers():
    window = CallWindow(since=NOW - dt.timedelta(days=30), first_call=NOW - dt.timedelta(days=10),
                        athletes=2, tallies=(_tally("n1", 4.0, input_tokens=100,
                                                    cache_read_tokens=300),
                                             _tally("heavy", 6.0)))

    projection = agent_costs.project(window, NOW)

    assert projection.days_covered == 10
    assert projection.per_month_usd == 30.0
    assert projection.per_athlete_month_usd == 15.0
    assert projection.cache_read_share == 0.75


def test_a_young_register_still_counts_at_least_one_day():
    window = CallWindow(since=NOW - dt.timedelta(days=30), first_call=NOW - dt.timedelta(hours=2),
                        athletes=1, tallies=(_tally("n1", 1.0),))

    assert agent_costs.project(window, NOW).per_day_usd == 1.0


def test_the_report_lists_the_costliest_route_first_and_says_when_empty():
    window = CallWindow(since=NOW - dt.timedelta(days=7), first_call=NOW - dt.timedelta(days=7),
                        athletes=1, tallies=(_tally("n1", 1.0), _tally("heavy", 5.0)))

    lines = agent_costs.render(window, NOW).splitlines()

    assert lines[3].startswith("heavy") and lines[4].startswith("n1")
    assert "No model call" in agent_costs.render(
        CallWindow(since=NOW, first_call=None, athletes=0, tallies=()), NOW)
