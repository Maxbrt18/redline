"""No session leaves without a valid program, and no reply is discarded for that.

The deterministic gate holds back the session BLOCK and the unrecorded facts; the
coach prose always goes out, with a precise line when a limit concerns the athlete.
"""

import json
from datetime import date, timedelta
from datetime import time as dtime
from unittest.mock import MagicMock

import pytest
from conftest import FakeAgent

from redline.service.agent import RunResult
from redline.service.proactive import ProactiveJob
from redline.service.vault import PersistenceResult

#: Openings of the program texts.
CANONICAL = ("Programme", "Aujourd'hui", "Prochaine séance", "La suite de la semaine", "Changement", "Rien de prévu")


def context(revision, sessions=None):
    return json.dumps({"revision": revision,
                       "sessions": [] if sessions is None else sessions})


def session(day, **changed) -> dict:
    """A session to do that day. The contexts of this file carry them: sending follows
    what remains to do, not a revision number, so an empty week sends no block."""
    return {"id": f"w32-r1-{day.isoformat()}-ef", "title": "EF", "date": day.isoformat(),
            "kind": "run", "workout": "easy", "status": "planned",
            "distance_cap_km": 9.0, "demanding": False, **changed}


def _sent(client) -> list[str]:
    return [call.args[1] for call in
            client.send_burst.call_args_list + client.send_text.call_args_list]


def test_an_invalid_plan_never_sends_sessions_and_says_so_when_asked(make_bot):
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    repo.weekly_plan_context.return_value = ""
    repo.persist_turn.return_value = PersistenceResult(True, invalid=("2026-W39",))
    client = MagicMock()
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="Pour aujourd'hui, repos."),
                                   depth="N1", plan_scope="today"))
    assert bot._respond("4242", "je fais quoi aujourd'hui ?")
    sent = _sent(client)
    assert "Pour aujourd'hui, repos." in sent
    assert any("pas de programme valide pour cette semaine" in text for text in sent)
    assert not any(text.startswith(CANONICAL) for text in sent)
    repo.weekly_plan_message.assert_not_called()


@pytest.mark.parametrize("claim", ["C’est enregistré.", "Je te passe à 14 km dimanche."])
def test_failed_writes_keep_the_answer_but_never_confirm_it(make_bot, store, claim):
    """A refused write erases neither the prose nor the conversation: the athlete reads
    what the coach wrote AND what was not recorded. No session is sent."""
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.weekly_plan_context.return_value = context(2, [session(date.today())])
    repo.persist_turn.return_value = PersistenceResult(
        False, refused=("observation.add",))
    client = MagicMock()
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text=claim), depth="N1",
                                   plan_scope="today"))

    assert bot._respond("4242", "retour de séance", message_ids=("test-failed-write",))

    sent = _sent(client)
    assert claim in sent
    assert "Attention : je n'ai pas pu enregistrer ta séance. Ne le considère pas comme " \
           "noté ; redis-le-moi dans un moment." in sent
    assert not any(text.startswith(CANONICAL) for text in sent)
    assert "n'a pas pu" not in store._conversation[-1]["text"].split("\n---\n")[0]
    assert "je n'ai pas pu enregistrer" in store._conversation[-1]["text"]


def test_a_failed_model_run_never_commits_partial_changes(make_bot):
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    repo.is_clean.return_value = True
    repo.weekly_plan_context.return_value = context(1)
    bot = make_bot(repo=repo, client=MagicMock(),
                   agent=FakeAgent(result=RunResult(error="process tué"), depth="N1"))
    bot._respond("4242", "quelle séance demain ?")
    repo.persist_turn.assert_not_called()


def test_a_plan_changing_message_answers_on_the_active_plan_when_no_revision_is_needed(make_bot):
    """Triage judges "P" on the TEXT, before the vault is opened -- the prompt itself
    says "potentially MODIFIES". Requiring a revision behind that hypothesis punished
    the right answer: when the plan already covers what the athlete reports, the model
    revises nothing, and the coach used to lock itself announcing an "invalid program"
    that was perfectly valid -- with no commit attempted and no log line.

    What the turn MUST do is unchanged: speak normally. What it no longer does is
    send the program behind it -- see `test_plan_delivery.py`; the "P" flag no longer
    triggers a send.
    """
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    repo.weekly_plan_context.return_value = context(2)
    repo.weekly_plan_message.return_value = "Programme 2026-W33 (r2) : dim EF, plafond 9 km"
    repo.persist_turn.return_value = PersistenceResult(True)
    repo.drain_alerts.return_value = []
    client = MagicMock()
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="Je te passe à 14 km dimanche"),
                                     depth="N2", plan_change=True))
    bot._respond("4242", "j'ai couru 7 km, ma montre a démarré en retard")
    sent = " ".join(call.args[1] for call in
                    client.send_text.call_args_list + client.send_burst.call_args_list)
    assert "aucune séance" not in sent
    assert "Je te passe à 14 km dimanche" in sent


def test_a_plan_changing_message_without_a_valid_plan_sends_no_session(make_bot):
    """The lock that matters does not move: without a canonical revision, no session
    block. The reply goes out; the athlete did not ask for the program, nothing is added."""
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    repo.weekly_plan_context.return_value = ""
    repo.persist_turn.return_value = PersistenceResult(True, invalid=("2026-W39",))
    repo.drain_alerts.return_value = []
    client = MagicMock()
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="Bien noté pour ce week-end."),
                                     depth="N2", plan_change=True))
    bot._respond("4242", "finalement mon week-end est libre")
    assert _sent(client) == ["Bien noté pour ce week-end."]
    repo.weekly_plan_message.assert_not_called()


def test_a_factual_correction_is_not_blocked_by_an_unrelated_missing_plan(make_bot):
    """Fixing the shoe is N3, not a prescription."""
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    repo.weekly_plan_context.return_value = ""
    repo.persist_turn.return_value = PersistenceResult(True)
    repo.drain_alerts.return_value = []
    client = MagicMock()
    bot = make_bot(
        repo=repo, client=client,
        agent=FakeAgent(
            result=RunResult(full_text="Oui, je corrige : TrailShoe pour le petit trail."),
            depth="N3"),
    )

    bot._respond("4242", "la dernière était en TrailShoe")

    sent = " ".join(call.args[1] for call in client.send_burst.call_args_list)
    assert "TrailShoe" in sent
    client.send_text.assert_not_called()
    assert repo.persist_turn.call_args.kwargs["required_weeks"] == ()


def test_the_persisted_plan_leaves_in_its_own_message_after_the_coach(make_bot):
    """The plan ACCOMPANIES the coach, it no longer replaces it.

    Replacing locked on a clue of what is protected ("the model spoke") rather than on
    what is protected ("an unwritten prescription binds the athlete"), and at the worst
    moment: a replan arrives when pain rises or a session is skipped, so when there is
    the most to explain. The athlete used to get a one-line reply and seven serialized
    days of plan, and nothing else -- all the coaching of the message had been dropped.

    What stays guaranteed, and what this test pins: the persisted plan goes out, last,
    in its own text message. It is authoritative, and the athlete always has it in view.
    """
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    yesterday = date.today() - timedelta(days=1)
    # On Sunday, tomorrow belongs to the next week and so cannot appear in "the rest of
    # the week". The test targets a session still to deliver in the current week, not
    # the ISO Sunday -> Monday crossing.
    remaining = date.today() + timedelta(days=1 if date.today().weekday() < 6 else 0)
    repo.weekly_plan_context.side_effect = [
        context(2, [session(yesterday), session(date.today())]),
        context(3, [session(yesterday), session(remaining, title="SL", workout="long")])]
    repo.persist_turn.return_value = PersistenceResult(True)
    client = MagicMock()
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="On garde 9 km dimanche"),
                                     depth="N2", plan_change=True))
    bot._respond("4242", "finalement mon week-end est libre")
    assert "On garde 9 km dimanche" in client.send_burst.call_args_list[0].args[1]
    canonical = client.send_text.call_args_list[-1].args[1]
    assert canonical.startswith(CANONICAL)
    # The rest of the week, not the days already past.
    assert f"{remaining:%d/%m}" in canonical and f"{yesterday:%d/%m}" not in canonical


def test_the_plan_still_leaves_on_an_n0_replan(make_bot):
    """A replan needs no reading level: "I skip tomorrow" is social by form and
    prescriptive by substance. The week loses its session of tomorrow -- THAT CHANGE
    sends the block, not the triage level nor its "P" flag."""
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    tomorrow = date.today() + timedelta(days=1)
    repo.weekly_plan_context.side_effect = [context(2, [session(tomorrow)]),
                                            context(3, [session(tomorrow, status="cancelled")])]
    repo.persist_turn.return_value = PersistenceResult(True)
    client = MagicMock()
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="ok"),
                                     depth="N0", plan_change=True))
    bot._respond("4242", "je saute demain finalement")
    assert client.send_text.call_args_list[-1].args[1].startswith(CANONICAL)


def test_a_morning_without_a_valid_plan_speaks_without_sessions_and_arms_the_repair(
        make_bot, store):
    """The morning message is no longer regenerated every 5 minutes for a missing week.
    It goes out without a session, and the repair has a single owner."""
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    repo.weekly_plan_context.return_value = ""
    repo.persist_turn.return_value = PersistenceResult(True, invalid=("2026-W39",))
    repo.drain_alerts.return_value = []
    client = MagicMock()
    bot = make_bot(repo=repo, client=client, proactive_active_start=dtime(0, 0),
                   agent=FakeAgent(result=RunResult(full_text="Bien dormi ? La blessure ?")))
    store.proactive.enqueue_job("morning:today", "morning", "", 0)
    job = store.proactive.claim_due_job(1e12)

    bot._respond_proactive(ProactiveJob(psid="4242", kind="morning", prompt="matin",
                                        job_id=job["id"]))

    assert _sent(client) == ["Bien dormi ? La blessure ?"]
    jobs = {row["id"]: row for row in store.proactive.jobs()}
    assert jobs["morning:today"]["status"] == "done"
    repair = [row for row in jobs.values() if row["kind"] == "weekly_repair"]
    assert len(repair) == 1 and repair[0]["status"] == "pending"


def test_sunday_requires_both_the_closing_and_next_week(make_bot):
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    repo.weekly_plan_context.return_value = context(2)
    repo.weekly_plan_message.return_value = "Programme suivant"
    repo.persist_turn.return_value = PersistenceResult(True)
    bot = make_bot(repo=repo,
                   agent=FakeAgent(result=RunResult(full_text="bilan")))
    bot._respond_proactive(ProactiveJob(psid="4242", kind="weekly", prompt="bilan"))
    required = repo.persist_turn.call_args.kwargs["required_weeks"]
    assert len(required) == 2 and required[0] != required[1]


def test_a_morning_with_a_broken_render_still_speaks_without_regenerating(make_bot,
                                                                           store):
    """A broken rendering remains a service matter, not a signal for the athlete nor a
    reason to re-run the model: the prose goes out alone, the incident stays in the log."""
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    repo.weekly_plan_context.return_value = context(2)
    repo.weekly_plan_message.return_value = ""      # the rendering breaks
    repo.persist_turn.return_value = PersistenceResult(True)
    repo.drain_alerts.return_value = []
    client = MagicMock()
    agent = FakeAgent(result=RunResult(full_text="Belle journée pour un footing."))
    bot = make_bot(repo=repo, client=client, agent=agent,
                   proactive_active_start=dtime(0, 0))
    bot._respond_proactive(ProactiveJob(psid="4242", kind="morning", prompt="matin"))
    assert _sent(client) == ["Belle journée pour un footing."]
    assert len(agent.calls) == 1


def morning_bot(make_bot, full_text, silent=False):
    """The proactive window is wide open, and that is the point of the test.

    These two tests are the first to reach the send budget, which reads
    `datetime.now()`: green on a laptop at 10:00, red on CI running in UTC before the
    7:30 opening. An implicit clock in a path assumed to be controlled. What is measured
    here is the message content, never the time of day.
    """
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    repo.weekly_plan_context.return_value = context(2)
    repo.weekly_plan_message.return_value = "Aujourd'hui : Renfo A, 30 min"
    repo.persist_turn.return_value = PersistenceResult(True)
    repo.drain_alerts.return_value = []
    client = MagicMock()
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text=full_text, silent=silent)),
                   proactive_active_start=dtime(0, 0))
    bot._respond_proactive(ProactiveJob(psid="4242", kind="morning", prompt="matin"))
    return client


def test_the_morning_note_survives_the_plan_it_announces(make_bot):
    """Overwriting `full_text` dropped everything the morning message exists for: the
    dated-check question, the thread reminder, the `daily.csv` question. Only a session
    line was left -- and a dated check is precisely a question to ASK before
    prescribing."""
    client = morning_bot(make_bot, "La blessure, elle donne quoi au réveil ce matin ?")
    sent = " ".join(call.args[1] for call in client.send_burst.call_args_list)
    assert "au réveil ce matin" in sent
    assert "Renfo A" in sent


def test_a_silent_morning_still_announces_the_day(make_bot):
    """The coach may have nothing to add; the session of the day goes out anyway."""
    client = morning_bot(make_bot, "", silent=True)
    sent = " ".join(call.args[1] for call in client.send_burst.call_args_list)
    assert sent.strip() == "Aujourd'hui : Renfo A, 30 min"


def test_a_cell_is_marked_asked_only_when_a_message_really_leaves(make_bot):
    """"Asked" means a message went out, not that a prompt was assembled.

    A turn once ended fail-closed without a word going out -- and the three cells of
    the day were nonetheless classed "asked". The `?` sentinel ("asked, and lost") could
    have landed on a question never asked, which the code explicitly forbids.
    """
    entry = {"date": "2026-08-10", "type": "easy", "missing": ["RPE", "Dlr", "Plaisir"]}
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    repo.weekly_plan_context.return_value = ""          # week without a valid plan
    repo.persist_turn.return_value = PersistenceResult(False)  # hence fail-closed
    repo.pending_ledger_cells.return_value = [entry]
    repo.drain_alerts.return_value = []
    bot = make_bot(repo=repo, client=MagicMock(),
                   agent=FakeAgent(result=RunResult(full_text="ok"), depth="N1",
                                   plan_asked=True))
    bot._respond("4242", "j'ai couru 7 km")
    markers = bot._state.proactive
    assert not any(markers.marked("asked:2026-08-10:easy:" + column)
                   for column in entry["missing"]), \
        "cellules classées « demandées » alors qu'aucun message n'est parti"
