"""WHEN the canonical program is sent.

`test_plan_message.py` pins what the block SAYS once rendered; here we pin what triggers
its sending. The gap between the two let the same unchanged program, plus the same card,
be pasted three times in one conversation, under a question the coach had just asked.

The rule: the block follows a FACT (what remains to do changed, a request, a lost
delivery), never the hypothesis "this message may touch the plan".

The fact is NOT a revision number going up. A revision is also written to record the
past, and one recording the feelings of a morning run once sent back the session just
run. The contexts of these tests therefore carry real sessions, and the real rendering
runs: the seam between "what the block says" and "when it is sent" is what is under test.
"""

import json
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from conftest import FakeAgent

from redline.service.agent import RunResult
from redline.service.messenger import DeliveryResult
from redline.service.proactive import ProactiveJob
from redline.service.vault import PersistenceResult

TODAY = date.today()
TOMORROW = TODAY + timedelta(days=1)


def session(**changed) -> dict:
    """A session to do today, unless stated otherwise."""
    return {"id": "w33-r1-jour-ef", "title": "EF", "date": TODAY.isoformat(),
            "kind": "run", "workout": "easy", "status": "planned",
            "distance_target_km": 7.0, "distance_cap_km": 9.0,
            "demanding": False, **changed}


def context(revision, sessions=None, gates=None):
    return json.dumps({"revision": revision,
                       "sessions": [session()] if sessions is None else sessions,
                       "gates": gates or []})


@pytest.fixture
def repo():
    """A vault whose week is valid, prescribable, and already known to the athlete."""
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    repo.ensure_clean.return_value = []
    repo.head.return_value = "0123456789abcdef"
    repo.weekly_plan_context.return_value = context(5)
    # Only the PROACTIVE path still goes through the repo method; the reactive one
    # renders itself, from the two contexts it compares.
    repo.weekly_plan_message.return_value = "Programme de la semaine suivante"
    repo.persist_turn.return_value = PersistenceResult(True)
    repo.pending_ledger_cells.return_value = []
    repo.drain_alerts.return_value = []
    return repo


@pytest.fixture
def client():
    client = MagicMock()
    for method in ("send_text", "send_burst", "send_image", "send_button", "action"):
        getattr(client, method).return_value = DeliveryResult.success()
    return client


#: Openings of the program texts (no ISO week nor revision number).
CANONICAL = ("Programme", "Aujourd'hui", "Prochaine séance", "La suite de la semaine", "Changement", "Rien de prévu")


def programmes(client):
    """Canonical texts actually sent, technical failures excluded."""
    return [call.args[1] for call in client.send_text.call_args_list
            if call.args[1].startswith(CANONICAL)]


def current_week(bot):
    """The week the bot computes itself: a test does not hardcode the time."""
    return bot._iso_week(datetime.now())


# ---------------------------------------------------------------- staying silent

def test_a_whole_conversation_about_one_bike_ride_repeats_no_programme(repo, client,
                                                                       make_bot):
    """A conversation replayed as is.

    Three legitimate "P" messages (availability, duration, day) and a plan nothing
    modifies: the coach speaks three times, the program zero.
    """
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="Tu la ferais quel jour ?"),
                                     depth="N2", plan_change=True))
    bot._state.owe_plan("4242", current_week(bot), 5)
    bot._state.settle_plan("4242")          # r5 has arrived: the debt is settled

    for message in ("je fais une sortie vélo de route avec mon père ça pose problème ?",
                    "ça sera max 1h30/2h mais de toute façon pas de choc",
                    "peut être demain"):
        bot._respond("4242", message)

    assert client.send_burst.call_count == 3, "le coach doit répondre à chaque fois"
    assert programmes(client) == []


def test_the_reactive_path_never_renders_the_plan_card(repo, client, make_bot):
    """The card said a third time what the prose and the block already said.

    It stays on Sunday, where it publishes a whole week and is the message. Reactively,
    a revision touches one or two days: the text message is enough.
    """
    due_gate = {"id": "g1", "due": "2000-01-01", "when": "avant",
                "holds": ["w33-r1-jour-ef"],
                "question": "La blessure ?", "on_fail": "rest"}
    repo.weekly_plan_context.side_effect = [context(5), context(
        6, [session(date=TOMORROW.isoformat())], [due_gate])]
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="Je décale ton renfo"),
                                     depth="N2", plan_change=True))
    bot._respond("4242", "je peux pas jeudi finalement")

    assert programmes(client), "une vraie révision doit partir"
    repo.render_weekly_plan_card.assert_not_called()


# ---------------------------------------------------------------- speaking

def test_a_new_revision_leaves_once_and_only_once(repo, client, make_bot):
    """The fact justifying the send is consumed by the send: it does not replay."""
    moved = [session(date=TOMORROW.isoformat())]
    repo.weekly_plan_context.side_effect = [context(5), context(6, moved),  # turn 1
                                            context(6, moved), context(6, moved)]  # turn 2
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="Je décale ton renfo"),
                                     depth="N2", plan_change=True))
    bot._respond("4242", "je peux pas jeudi finalement")
    bot._respond("4242", "ok ça me va")

    assert len(programmes(client)) == 1


def test_a_revision_that_only_records_the_past_says_nothing(repo, client, make_bot):
    """Replayed as is, and the true definition of the "fact".

    The athlete finishes a long run, debriefs it, gives his three markers. The coach
    writes a revision recording RPE, pain peak and enjoyment: one more revision, whose
    session list is IDENTICAL to the previous one. Nothing remains to do this week, yet
    the athlete received "the rest of the week" listing the session just reported.

    Two faults stitched together: the block left on a rising number, and its rendering
    ignored `status`. This test holds both ends.
    """
    done = [session(status="done")]
    repo.weekly_plan_context.side_effect = [context(11, done), context(12, done)]
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="Très bien, genou discret."),
                                     depth="N1", plan_change=True))
    bot._respond("4242", "RPE 3-4, pic de douleur 0.75-1, plaisir 7")

    assert programmes(client) == []
    assert client.send_burst.call_count == 1, "le coach débriefe quand même"


def test_alex_asking_for_his_programme_gets_it_even_unchanged(repo, client, make_bot):
    """The indispensable counterweight: without "D", tightening the send made the coach
    MUTE on "what do I do tomorrow?" -- the prose may not prescribe, and "P" does not
    fire on a question that changes nothing."""
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="Tu as ça au programme"),
                                     depth="N1", plan_scope="next"))
    bot._respond("4242", "je fais quoi demain ?")

    assert len(programmes(client)) == 1


def test_asking_twice_answers_twice(repo, client, make_bot):
    """A question asked twice deserves two answers: the delivery debt must not turn into
    "I already told you"."""
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="Voilà"),
                                     depth="N1", plan_asked=True))
    bot._respond("4242", "c'est quoi demain ?")
    bot._respond("4242", "et le reste de la semaine ?")

    assert len(programmes(client)) == 2


def test_morning_initiative_does_not_repeat_a_revision_already_shown_today(
        repo, client, make_bot):
    """The marker limits initiative, never a new request from the athlete."""
    bot = make_bot(
        repo=repo, client=client,
        agent=FakeAgent(result=RunResult(full_text="Voilà"), plan_scope="today"))
    bot._respond("4242", "je fais quoi aujourd'hui ?")
    assert len(programmes(client)) == 1

    bot._respond_proactive(ProactiveJob(psid="4242", kind="morning", prompt="bonjour"))

    assert len(programmes(client)) == 1


# ---------------------------------------------------------------- the debt

def test_a_programme_lost_in_transport_is_still_owed_on_the_next_turn(repo, client,
                                                                      make_bot):
    """The only case where an unchanged program is sent again, and the reason for a debt.

    Without it, a lost text message was NEVER resent: the next turn renders the same
    text twice, concludes "nothing new", and the prescription stays on the server.
    """
    moved = [session(date=TOMORROW.isoformat())]
    repo.weekly_plan_context.side_effect = [context(5), context(6, moved),
                                            context(6, moved), context(6, moved)]
    client.send_text.return_value = DeliveryResult.failure("graph 500")
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="Je décale ton renfo"),
                                     depth="N2", plan_change=True))
    bot._respond("4242", "je peux pas jeudi finalement")
    assert bot._state.plan_owed("4242") == (current_week(bot), 6)

    client.send_text.return_value = DeliveryResult.success()
    bot._respond("4242", "tu disais ?")     # neither "P" nor "D": the debt speaks

    sent = programmes(client)
    assert len(sent) == 2
    assert sent[0].startswith("Changement au programme")
    assert "suite de la semaine" in sent[1]
    assert bot._state.plan_owed("4242") == ("", 0)


def test_a_settled_programme_leaves_nothing_owed(repo, client, make_bot):
    repo.weekly_plan_context.side_effect = [context(5), context(6)]
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="Je décale ton renfo"),
                                     depth="N2", plan_change=True))
    bot._respond("4242", "je peux pas jeudi finalement")

    assert bot._state.plan_owed("4242") == ("", 0)


def test_a_debt_owed_for_another_week_never_replays_here(repo, client, make_bot):
    """A debt is dated. Last week's does not make this week speak -- otherwise a missed
    Sunday would make Monday chatty forever."""
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="ok"), depth="N1"))
    bot._state.owe_plan("4242", "1999-W01", 5)
    bot._respond("4242", "salut")

    assert programmes(client) == []


# ------------------------------------------------------ what "P" commands

def test_a_replan_announced_in_one_line_still_validates_its_week(repo, client, make_bot):
    """"I skip tomorrow after all": N0 by form, prescriptive by substance.

    The reading level alone drove the fail-closed lock, so a replan said in five words
    left without the canonical plan in the prompt and without a week to validate.
    Now that "P" no longer triggers a send, this is exactly -- and only -- what it
    commands: re-read the plan, and engage validation.
    """
    repo.weekly_plan_context.side_effect = [context(5), context(6)]
    agent = FakeAgent(result=RunResult(full_text="Noté"), depth="N0", plan_change=True)
    bot = make_bot(repo=repo, client=client, agent=agent)
    bot._respond("4242", "je saute demain finalement")

    assert "PLAN HEBDOMADAIRE CANONIQUE" in agent.calls[0]["message"]
    required = repo.persist_turn.call_args.kwargs["required_weeks"]
    assert required == (current_week(bot),)


# ------------------------------------------------------- what does not move

def test_a_gate_still_holds_the_prescription_even_on_a_fresh_revision(repo, client,
                                                                      make_bot):
    """An overdue dated check holds the session line, fresh revision or not."""
    due_gate = {"id": "g1", "due": TODAY.isoformat(), "when": "avant",
                "holds": ["w33-r1-jour-ef"], "question": "la blessure ?",
                "on_fail": "rest"}
    repo.weekly_plan_context.side_effect = [
        context(5),
        context(6, [session(date=TOMORROW.isoformat())], gates=[due_gate]),
    ]
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="La blessure, elle donne quoi ?"),
                                     depth="N2", plan_change=True))
    repo.persist_turn.return_value = PersistenceResult(
        True, awaiting=(current_week(bot),))
    bot._respond("4242", "je peux pas jeudi finalement")

    assert programmes(client) == []


def test_an_invalid_week_prescribes_nothing_at_all(repo, client, make_bot):
    """Without a valid revision, no session block: the coach reply goes out, followed
    by a line telling precisely the limit to whoever asked for the program."""
    repo.weekly_plan_context.return_value = ""
    repo.persist_turn.return_value = PersistenceResult(False)
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="Je regarde ta semaine."),
                                     depth="N1", plan_asked=True))
    bot._respond("4242", "je fais quoi demain ?")

    assert programmes(client) == []
    burst = " ".join(call.args[1] for call in client.send_burst.call_args_list)
    lines = " ".join(call.args[1] for call in client.send_text.call_args_list)
    assert "Je regarde ta semaine." in burst
    assert "pas de programme valide pour cette semaine" in lines


def test_applied_facts_do_not_open_the_gate_when_the_plan_is_invalid(
        repo, client, make_bot):
    """Fact success and prescribability are two distinct verdicts."""
    bot = make_bot(repo=repo, client=client)
    week = current_week(bot)
    repo.weekly_plan_context.return_value = ""
    repo.persist_turn.return_value = PersistenceResult(
        True, invalid=(week,))
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="Noté pour ta sortie."),
                                   depth="N1", plan_asked=True))

    bot._respond("4242", "je fais quoi demain ?")

    assert programmes(client) == []
    lines = " ".join(call.args[1] for call in client.send_text.call_args_list)
    assert "pas de programme valide pour cette semaine" in lines


def test_the_sunday_card_survives_the_reactive_cleanup(repo, client, make_bot):
    """The Sunday publication keeps its card: it is the entire week, and the only place
    where the image brings more than the text."""
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="Bilan de la semaine")))
    bot._respond_proactive(ProactiveJob(psid="4242", kind="weekly", prompt="bilan"))

    repo.render_weekly_plan_card.assert_called_once()


def test_weekly_retry_on_monday_keeps_the_sunday_period(
        repo, client, make_bot, monkeypatch):
    """The durable job names W34: its Monday retry still prepares W35, never W36."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import redline.service.bot as bot_module

    monday = datetime(2026, 8, 24, 7, 30, tzinfo=ZoneInfo("Europe/Paris"))
    monkeypatch.setattr(bot_module.clock, "now", lambda: monday)
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="Bilan W34")))
    bot._state.proactive.enqueue_job(
        "weekly:2026-W34", "weekly", "", monday.timestamp() - 1)

    bot._respond_proactive(ProactiveJob(
        psid="4242", kind="weekly", prompt="bilan",
        job_id="weekly:2026-W34", attempts=2))

    required = repo.persist_turn.call_args.kwargs["required_weeks"]
    assert required == ("2026-W34", "2026-W35")
    repo.weekly_plan_message.assert_called_with("2026-W35")
    assert all("2026-W36" not in str(call) for call in repo.mock_calls)


# ------------------------------------------------------------------- /deep

def test_deep_keeps_the_triage_flags_it_did_not_decide(repo, client, make_bot):
    """`/deep` sets the LEVEL, nothing else. Rebuilding the triage field by field
    silently lost any field added later -- `plan_asked` would have been the first."""
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="Analyse"),
                                     depth="N1", plan_asked=True))
    bot._respond("4242", "explique moi", deep_request=True)

    assert len(programmes(client)) == 1
