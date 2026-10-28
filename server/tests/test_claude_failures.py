"""Translating Claude CLI failures: _classify_failure and _parse_reset_epoch."""

from datetime import datetime

from redline import clock
from redline.agent import FailureKind
from redline.claude import ClaudeCodeRunner, _parse_reset_epoch


def test_epoch_after_pipe():
    assert _parse_reset_epoch("Claude AI usage limit reached|1752320400") == 1752320400


def test_wall_clock_same_day():
    now = datetime(2026, 7, 14, 10, 0)
    epoch = _parse_reset_epoch("Limit reached, resets 1:30pm", now=now)
    assert clock.fromtimestamp(epoch) == datetime(2026, 7, 14, 13, 30, tzinfo=clock.zone())


def test_wall_clock_next_day_if_passed():
    now = datetime(2026, 7, 14, 15, 0)
    epoch = _parse_reset_epoch("resets 1:30pm", now=now)
    assert clock.fromtimestamp(epoch) == datetime(2026, 7, 15, 13, 30, tzinfo=clock.zone())


def test_nothing_readable():
    assert _parse_reset_epoch("unknown failure") is None


def test_classify_limit_with_epoch():
    failure, retry_at = ClaudeCodeRunner._classify_failure("usage limit reached|1752320400")
    assert failure is FailureKind.RATE_LIMIT
    assert retry_at == 1752320400.0


def test_classify_limit_without_time():
    failure, retry_at = ClaudeCodeRunner._classify_failure("rate limit exceeded")
    assert failure is FailureKind.RATE_LIMIT
    assert retry_at is None


def test_classify_expired_auth_names_the_operator_fix_in_the_logs_only(caplog):
    with caplog.at_level("ERROR", logger="redline"):
        failure, retry_at = ClaudeCodeRunner._classify_failure(
            "OAuth token expired, please run /login")
    assert failure is FailureKind.PREFLIGHT
    assert retry_at is None
    assert "setup-token" in caplog.text


def test_classify_unknown_failure():
    assert ClaudeCodeRunner._classify_failure("mystery segfault") == (None, None)
