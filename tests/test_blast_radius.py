"""A refused write is not confirmed, but the coach's reply always goes out.

The deterministic gate acts on the effect (facts not recorded, sessions not prescribed),
never on the prose: the athlete gets the reply AND a precise line about what is missing.
"""

from datetime import datetime
from datetime import time as dtime
from unittest.mock import MagicMock

from conftest import FakeAgent

from redline.service.agent import RunResult
from redline.service.proactive import ProactiveJob
from redline.service.vault import PersistenceFailure, PersistenceResult


def _bot(make_bot, persistence, *, plan_context="{\"revision\": 8, \"sessions\": []}",
         plan_change=False, plan_asked=False, **config):
    client = MagicMock()
    client.send_text.return_value = MagicMock(ok=True)
    client.send_burst.return_value = MagicMock(ok=True)
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    repo.is_clean.return_value = True
    repo.ensure_clean.return_value = []
    repo.head.return_value = "0123456789abcdef"
    repo.weekly_plan_context.return_value = plan_context
    repo.weekly_plan_message.return_value = "Aujourd'hui : EF, plafond 9 km."
    repo.persist_turn.return_value = persistence
    bot = make_bot(
        agent=FakeAgent(result=RunResult(full_text="EF propre, 6:11/km, tu es dans ta Z2."),
                        plan_change=plan_change, plan_asked=plan_asked),
        client=client, repo=repo, **config)
    return bot, client


def _sent(client) -> str:
    texts = [call.args[1] for call in client.send_text.call_args_list]
    texts += [call.args[1] for call in client.send_burst.call_args_list]
    return "\n".join(texts)


def test_a_refused_write_keeps_the_answer_and_names_what_was_not_recorded(make_bot):
    """Refused facts are not confirmed; the reply itself is not thrown away."""
    bot, client = _bot(make_bot, PersistenceResult(
        False, detail="mesure déjà connue", failure=PersistenceFailure.WRITE_REFUSED,
        refused=("measurement.complete",)))

    assert bot._respond("4242", "effort ressenti 7-8/10 et pic de douleur 0/10")

    sent = _sent(client)
    assert "EF propre" in sent
    assert "je n'ai pas pu enregistrer tes ressentis sur la séance" in sent
    assert "mesure déjà connue" not in sent


def test_a_refusal_of_internal_commands_only_adds_nothing(make_bot):
    """A question declared lost changes nothing for the athlete: no alarm."""
    bot, client = _bot(make_bot, PersistenceResult(
        False, detail="question inconnue", failure=PersistenceFailure.WRITE_REFUSED,
        refused=("question.ask",)))

    bot._respond("4242", "ok")

    sent = _sent(client)
    assert "EF propre" in sent
    assert "Attention" not in sent


def test_a_refused_write_drops_the_session_that_now_believes_a_lie(make_bot):
    """A session lives for hours and believes it wrote what was cancelled. After several
    cancelled turns the coach could try to COMPLETE an observation that was never created
    ("observation inconnue") and would retry forever. The database is re-read at startup:
    starting from a fresh context is the only safe correction."""
    bot, _ = _bot(make_bot, PersistenceResult(
        False, detail="observation inconnue", failure=PersistenceFailure.WRITE_REFUSED))
    bot._state.set_session("4242", "codex", "session-qui-croit-avoir-ecrit")

    bot._respond("4242", "effort ressenti 7-8/10")

    assert bot._state.session_id("4242", "codex") is None


def test_an_unexpected_persistence_exception_finalizes_the_turn(make_bot):
    bot, client = _bot(make_bot, PersistenceResult(True))
    bot._repo.persist_turn.side_effect = ValueError("payload illisible")
    bot._state.set_session("4242", "codex", "session-qui-croit-avoir-ecrit")

    bot._respond("4242", "effort ressenti 7")

    bot._repo.discard_turn.assert_called_once()
    bot._repo.abandon_turn.assert_called_once()
    assert bot._state.session_id("4242", "codex") is None
    assert "EF propre" in _sent(client)
    assert "n'a pas pu être enregistré" in _sent(client)
    assert "payload illisible" not in _sent(client)


def test_a_healthy_turn_keeps_its_session(make_bot):
    """Negative control: conversational continuity is not thrown away without reason.
    Without it, a bot that starts from scratch on every message would pass the previous
    test."""
    bot, _ = _bot(make_bot, PersistenceResult(True))
    bot._state.set_session("4242", "codex", "session-vivante")

    bot._respond("4242", "salut")

    assert bot._state.session_id("4242", "codex") == "session-vivante"


def test_a_refused_write_prescribes_nothing(make_bot):
    """The coach speaks, but a turn that could write nothing prescribes nothing."""
    bot, client = _bot(make_bot, PersistenceResult(
        False, detail="mesure déjà connue", failure=PersistenceFailure.WRITE_REFUSED))

    bot._respond("4242", "effort ressenti 7-8/10")

    assert "plafond 9 km" not in _sent(client)


def test_a_stale_plan_holds_the_prescription_but_not_the_conversation(make_bot):
    """Same treatment as an overdue dated check: the program no longer describes the
    week, it waits for a revision. This is not a failure."""
    bot, client = _bot(make_bot, PersistenceResult(
        True, stale=("2026-W33",)))

    bot._respond("4242", "et ma sortie de ce matin ?")

    sent = _sent(client)
    assert "EF propre" in sent
    client.send_text.assert_not_called()
    assert "plafond 9 km" not in sent


def test_an_absent_plan_holds_the_sessions_but_never_the_answer(make_bot):
    """Without a valid program no session goes out; the coach's reply does, along with a
    precise line when the athlete asked for the program."""
    bot, client = _bot(make_bot, PersistenceResult(True, invalid=("2026-W39",)),
                       plan_context="", plan_change=True, plan_asked=True)

    bot._respond("4242", "je fais quoi demain ?")

    sent = _sent(client)
    assert "EF propre" in sent
    assert "plafond 9 km" not in sent
    assert "pas de programme valide pour cette semaine" in sent
    assert "absent ou invalide" not in sent


def test_a_safety_answer_always_leaves_even_without_a_plan(make_bot):
    """A stop instruction or a medical referral waits for neither reading nor a valid
    program."""
    answer = "Arrête maintenant et marche pour rentrer. Si la douleur persiste, avis médical."
    bot, client = _bot(make_bot, PersistenceResult(True, invalid=("2026-W39",)),
                       plan_context="", plan_change=True)
    bot._agent._result = RunResult(full_text=answer)

    bot._respond("4242", "douleur nette au genou en courant, j'arrête ?")

    sent = _sent(client)
    assert answer in sent
    assert "pas de programme valide" not in sent


def test_the_morning_note_leaves_without_its_sessions_when_the_plan_is_stale(make_bot):
    """The morning note checks in, follows up a thread, asks the question of the day:
    none of that depends on an up-to-date program. Only the sessions line waits until the
    program is set against what was actually done."""
    iso = datetime.now().isocalendar()
    week_text = f"{iso.year}-W{iso.week:02d}"
    # The default send window (07:30-21:30) is read in WALL-CLOCK time. CI runners run in
    # UTC, so the send could be blocked by the hour and the test would see an empty message.
    # Its subject is not the window: it is opened, otherwise the test checks what it
    # claims only when the clock allows.
    bot, client = _bot(make_bot, PersistenceResult(
        True, stale=(week_text,)),
        proactive_active_start=dtime(0, 0))

    bot._respond_proactive(ProactiveJob(psid="4242", kind="morning", prompt="matin"))

    sent = _sent(client)
    assert "EF propre" in sent
    assert "plafond 9 km" not in sent
    client.send_text.assert_not_called()


def test_a_healthy_turn_speaks_without_any_incident(make_bot):
    """Negative control: with no failure and nothing stale, the coach speaks and nothing
    looks like an incident. Without it, a test that checks the absence of a message would
    also pass on a bot that has gone completely mute."""
    bot, client = _bot(make_bot, PersistenceResult(True),
                       plan_asked=True)

    bot._respond("4242", "c'est quoi mon programme ?")

    sent = _sent(client)
    assert "EF propre" in sent
    assert client.send_text.call_count == 1
    assert client.send_text.call_args.args[1].startswith(
        ("Programme", "La suite", "Rien de prévu", "Aujourd'hui", "Changement"))
