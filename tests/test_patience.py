"""The waiting line: what it says, and how often it is allowed to say it.

Sam's complaint (2026-07-31) was not that the coach takes time, it is that
announcing it sounded canned: four static strings, drawn at random, fired after 5 s on
every single N2 or N3 -- while the triage is instructed to round UP when it hesitates.
Three things are locked here: the contextual line wins over the canned one, the canned
one never repeats twice running, and a refused allowance costs a message rather than a
retry loop.
"""

import time
from itertools import pairwise

from redline.service import messenger
from redline.service.messenger import DEEP_PATIENCE_LINES, PATIENCE_LINES, TypingIndicator


class Spy:
    """A MessengerClient stand-in: records the texts, swallows the indicators."""

    def __init__(self):
        self.texts: list = []
        self.actions: list = []

    def action(self, psid, action):
        self.actions.append(action)

    def send_text(self, psid, text):
        self.texts.append(text)


def _fire(holding=None, gate=None, deep=True):
    """Run one indicator to the point where it decides, without waiting for its delay."""
    client = Spy()
    indicator = TypingIndicator(client, "4242", deep=deep, holding=holding, gate=gate)
    indicator.PATIENCE_AFTER = 0 if deep else 75
    with indicator:
        time.sleep(0.05)
    return client.texts


# ------------------------------------------------------------------ the text

def test_the_contextual_line_wins():
    """Written by the triage from the message itself, so it proves the coach read the
    question -- which is the whole difference with an acknowledgement of receipt."""
    assert _fire(holding="Ah, le vélo à la place de samedi.") == \
        ["Ah, le vélo à la place de samedi."]


def test_without_one_the_canned_line_still_covers():
    """Triage off, unparseable or overlong: a silence with no explanation is worse
    than a generic sentence."""
    assert _fire()[0] in DEEP_PATIENCE_LINES


def test_static_waiting_lines_do_not_claim_an_unobserved_tool_read_or_duration():
    for line in PATIENCE_LINES + DEEP_PATIENCE_LINES:
        assert not any(word in line.lower() for word in (
            "strava", "données", "source", "vérifi", "seconde", "tout de suite", "bonne question",
        ))


def test_the_canned_line_never_repeats_twice_running():
    """random.choice over four items repeats one time in four, and a repeated canned
    line is the most robotic thing the coach does."""
    messenger._last_static_line = ""
    drawn = [_fire()[0] for _ in range(12)]
    assert all(a != b for a, b in pairwise(drawn))


def test_the_shallow_rungs_keep_their_own_set():
    line = _fire(deep=False)
    # PATIENCE_AFTER (75 s) has not elapsed: nothing goes out on a routine message.
    assert line == []
    assert set(PATIENCE_LINES).isdisjoint(DEEP_PATIENCE_LINES)


# ----------------------------------------------------------------- the rhythm

def test_a_refused_allowance_sends_nothing():
    assert _fire(gate=lambda: False) == []


def test_the_allowance_is_asked_once_not_on_every_tick():
    """The gate consumes the "no two in a row" allowance: re-asking it every refresh
    would burn it silently, and could send the line late once it came back."""
    calls = []
    _fire(gate=lambda: calls.append(1) or False)
    assert len(calls) == 1


def test_the_deep_delay_leaves_time_to_answer():
    """La lecture approfondie ne déclenche plus une attente dès les premières secondes."""
    assert TypingIndicator.PATIENCE_AFTER >= 75


def test_leaving_the_context_stops_the_indicator():
    indicator = TypingIndicator(Spy(), "4242", deep=True)
    with indicator:
        pass
    assert indicator._stop.is_set()


def test_background_does_not_bypass_a_refused_waiting_allowance():
    client = Spy()
    indicator = TypingIndicator(client, "4242", deep=True, gate=lambda: False)
    indicator.PATIENCE_AFTER = 0
    indicator.BACKGROUND_AFTER = 0
    with indicator:
        time.sleep(0.05)
    assert client.texts == []


class VirtualClock:
    """Avance le temps sans thread ni attente réelle."""

    def __init__(self):
        self.now = 0.0

    def is_set(self):
        return self.now > 120

    def wait(self, seconds):
        self.now += seconds
        return self.is_set()


def test_long_turns_send_at_most_one_waiting_message(monkeypatch):
    for deep in (False, True):
        for allowed in (False, True):
            for patience in (False, True):
                clock = VirtualClock()
                client = Spy()
                calls = []
                indicator = TypingIndicator(
                    client, "4242", deep=deep, patience=patience,
                    gate=lambda calls=calls, clock=clock, allowed=allowed: calls.append(clock.now) or allowed)
                indicator._stop = clock
                monkeypatch.setattr(messenger.time, "monotonic", lambda clock=clock: clock.now)
                indicator._refresh_loop()
                assert len(client.texts) == int(allowed and patience)
                assert len(calls) == int(patience)
                assert all(t >= 75 for t in calls)
                assert clock.now <= 135


def test_fast_deep_reply_never_sends_a_waiting_line(monkeypatch):
    clock = VirtualClock()
    clock.is_set = lambda: clock.now >= 30
    client = Spy()
    indicator = TypingIndicator(client, "4242", deep=True)
    indicator._stop = clock
    monkeypatch.setattr(messenger.time, "monotonic", lambda clock=clock: clock.now)
    indicator._refresh_loop()
    assert client.texts == []
