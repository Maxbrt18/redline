"""An overdue dated check awaits an ANSWER; it does not signal a failure.

The conversation these tests exist to make impossible:

    Redline -- The weekly program did not pass its checks. I stay silent on the
               sessions rather than resurface an old plan.
    Athlete -- Then fix the program problem. Take your time.
    Redline -- The vault already holds unsaved changes. I write nothing until they
               are isolated.

What really happened: the pain check of the day was due that morning, so `check`
returned non-zero, so persistence refused the turn -- and the coach message asking
EXACTLY the check question was discarded in favor of the plumbing sentence. A mechanism
built to produce a question produced a failure. The writes of the refused turn then
stayed in the worktree, and the "clean worktree" guard gagged the following turns,
including the athlete's question.

Three locks, one per layer:
  1. model and CLI -- overdue check != material drift (exit code 2, not 1);
  2. vault -- `AWAITING` is recorded, and a refused turn leaves nothing behind;
  3. bot -- the coach speaks, only the prescription waits.
"""

import datetime as dt
import json
from unittest.mock import MagicMock

from conftest import FakeAgent, git
from test_weekly_plan import gate, plan, precondition, session, week_with_a_later_session

from redline.service.agent import RunResult, SessionMode
from redline.service.plans import due_gates_by_kind
from redline.service.proactive import ProactiveJob
from redline.service.vault import PersistenceResult, VaultRepo

# ------------------------------------------------------- 1. the model and the CLI

def test_a_due_gate_is_exposed_as_a_question_not_a_storage_failure():
    """A check can only be lifted by the athlete's answer."""
    from redline.model import weekly_plan

    p = week_with_a_later_session([precondition()])
    today = dt.date(2026, 8, 8)
    assert len(weekly_plan.due_gates(p, today=today)) == 1
    assert weekly_plan.gate_reasons(p, today=today)


def test_due_gates_reads_the_context_already_loaded():
    """No second subprocess to learn what was just read.

    The service only READS the string: what an overdue check is, and what separates a
    precondition from an observation, is declared by the model.
    """
    context = json.dumps({"revision": 3, "gates": [
        {"id": "g1", "due": "2026-08-12", "when": "avant", "holds": ["s1"],
         "question": "la blessure ?", "on_fail": "rest"},
        {"id": "g2", "due": "2026-08-14", "when": "apres",
         "question": "plus tard", "on_fail": "rest"},
    ]})
    blocking, observing = due_gates_by_kind(context, today="2026-08-12")
    assert [g["id"] for g in blocking] == ["g1"]
    assert observing == []
    assert due_gates_by_kind("pas du json", today="2026-08-12") == ([], [])
    assert due_gates_by_kind("", today="2026-08-12") == ([], [])


# ------------------------------------------------------------------ 2. the vault

def vault_at(clone):
    return VaultRepo(str(clone), "origin", "main")


def test_a_refused_turn_leaves_nothing_behind(git_clone):
    """The residue of a refused turn used to gag all the following ones.

    A turn that delivers nothing must be cancellable, otherwise it is not a transaction.
    """
    _, clone = git_clone
    repo = vault_at(clone)
    base = repo.head()
    (clone / "note.md").write_text("écrit par un tour qui va être refusé")
    (clone / "nouveau.md").write_text("et un fichier tout neuf")
    assert not repo.is_clean()
    repo.abandon_turn(base)
    assert repo.is_clean()
    assert (clone / "note.md").read_text() == "v1"
    assert not (clone / "nouveau.md").exists()


def test_abandon_turn_also_rewinds_a_commit_claude_made_itself(git_clone):
    _, clone = git_clone
    repo = vault_at(clone)
    base = repo.head()
    (clone / "note.md").write_text("v2")
    git(clone, "add", "-A")
    git(clone, "commit", "-m", "commit de Claude, tour ensuite refusé")
    assert repo.head() != base
    repo.abandon_turn(base)
    assert repo.head() == base
    assert repo.is_clean()


def test_ensure_clean_recovers_instead_of_blocking_forever(git_clone):
    """The safety net, for the case `abandon_turn` does not cover: a process crash.

    This clone is only written by the service, so what lingers is our own residue. It is
    discarded and work goes on. Losing a never-committed note costs far less than a mute
    coach for an hour on the athlete's question.
    """
    _, clone = git_clone
    repo = vault_at(clone)
    (clone / "orphelin.md").write_text("résidu d'un tour mort en vol")
    assert repo.ensure_clean() == ["orphelin.md"]
    assert repo.is_clean()
    assert repo.ensure_clean() == []


def test_the_outbox_survives_a_cleanup(git_clone):
    """`clean -fd` without `-x`: an already rendered card still awaits its archiving."""
    _, clone = git_clone
    (clone / "server").mkdir()
    (clone / "server" / ".gitignore").write_text("outbox/\n")
    git(clone, "add", "-A")
    git(clone, "commit", "-m", "outbox ignoré")
    outbox = clone / "server" / "outbox"
    outbox.mkdir()
    (outbox / "plan.png").write_bytes(b"png")
    repo = vault_at(clone)
    repo.ensure_clean()
    assert (outbox / "plan.png").exists()


# -------------------------------------------------------------------- 3. the bot

def plan_context(revision_number=2, gates=None):
    return json.dumps({"revision": revision_number, "sessions": [],
                       "gates": gates or []})


DUE_GATE = [{"id": "w33-injury", "due": "2000-01-01",
             "when": "avant", "holds": ["w33-ef"],
             "question": "La blessure au réveil ce matin, mieux ou pire qu'hier ?",
             "on_fail": "repos à la place de l'EF"}]


def test_the_coach_speaks_while_a_gate_holds_the_prescription(make_bot):
    """The message that should have gone out.

    The model had written the check question. It was discarded. Here it goes out, and
    the SESSION LINE waits -- the exact opposite of what happened.
    """
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    repo.ensure_clean.return_value = []
    repo.weekly_plan_context.return_value = plan_context(gates=DUE_GATE)
    repo.persist_turn.return_value = PersistenceResult(
        True, awaiting=("2026-W33",))
    repo.drain_alerts.return_value = []
    client = MagicMock()
    agent = FakeAgent(
        result=RunResult(full_text="La blessure, elle donne quoi au réveil ce matin ?"),
        depth="N2", plan_change=True)
    bot = make_bot(repo=repo, client=client, agent=agent)
    bot._respond("4242", "salut, on fait quoi aujourd'hui ?")

    burst = " ".join(call.args[1] for call in client.send_burst.call_args_list)
    assert "au réveil ce matin" in burst
    client.send_text.assert_not_called()
    repo.weekly_plan_message.assert_not_called()
    # And the model was told the nature of the wait, not of a failure.
    assert "CONTRÔLE DATÉ ÉCHU" in agent.calls[0]["message"]


def test_a_due_gate_surfaces_even_on_a_social_message(make_bot):
    """"hi" triages to N0, which does not load the plan -- and the check of the day
    would be missed.

    Same failure family: a mechanism built to ask a question that does not ask it. A
    dated check is an obligation of the DAY, it does not depend on the reading level of
    the message. `show` is cheap, so there is no excuse.
    """
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    repo.ensure_clean.return_value = []
    repo.weekly_plan_context.return_value = plan_context(gates=DUE_GATE)
    repo.persist_turn.return_value = PersistenceResult(True)
    repo.drain_alerts.return_value = []
    agent = FakeAgent(result=RunResult(full_text="salut !"), depth="N0")
    bot = make_bot(repo=repo, client=MagicMock(), agent=agent)
    bot._respond("4242", "salut")
    assert "CONTRÔLE DATÉ ÉCHU" in agent.calls[0]["message"]
    assert "La blessure au réveil" in agent.calls[0]["message"]


def test_the_morning_asks_the_gate_question_instead_of_reporting_a_failure(make_bot):
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    repo.ensure_clean.return_value = []
    repo.weekly_plan_context.return_value = plan_context(gates=DUE_GATE)
    repo.persist_turn.return_value = PersistenceResult(
        True, awaiting=(dt.date.today().strftime("%G-W%V"),))
    repo.drain_alerts.return_value = []
    client = MagicMock()
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(
                       full_text="Tes premiers pas au lever, raconte.")),
                   proactive_active_start=dt.time(0, 0))
    bot._respond_proactive(ProactiveJob(psid="4242", kind="morning", prompt="matin"))

    burst = " ".join(call.args[1] for call in client.send_burst.call_args_list)
    assert "premiers pas au lever" in burst
    assert "n'a pas passé ses contrôles" not in burst
    client.send_text.assert_not_called()


def test_a_failed_turn_does_not_gag_the_next_one(make_bot):
    """The real cost is not the lost message, it is the hour of silence behind it. A
    refused turn cleans up, the next turn works."""
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    repo.ensure_clean.return_value = []
    repo.head.return_value = "base-head"
    repo.weekly_plan_context.return_value = ""
    repo.persist_turn.return_value = PersistenceResult(False)
    repo.drain_alerts.return_value = []
    bot = make_bot(repo=repo, client=MagicMock(),
                   agent=FakeAgent(result=RunResult(full_text="ok"), depth="N1"))
    bot._respond("4242", "je fais quoi aujourd'hui ?")
    repo.abandon_turn.assert_called_once_with("base-head")


def test_a_model_failure_is_rolled_back_too(make_bot):
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    repo.ensure_clean.return_value = []
    repo.head.return_value = "base-head"
    repo.weekly_plan_context.return_value = plan_context()
    bot = make_bot(repo=repo, client=MagicMock(),
                   agent=FakeAgent(result=RunResult(error="process tué"), depth="N1"))
    bot._respond("4242", "quelle séance demain ?")
    repo.abandon_turn.assert_called_once_with("base-head")
    repo.persist_turn.assert_not_called()


def test_a_light_proactive_never_touches_the_conversation_session(make_bot):
    """Cancelling a turn rewinds git, not the model memory.

    The session of the failed attempt believed it had already written its message, so
    the retry answered silence. A light proactive turn now starts from a COPY of the
    conversation (fork): first attempt or retry, the main session keeps no trace of an
    undelivered initiative, and nothing is erased.
    """
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    repo.ensure_clean.return_value = []
    repo.weekly_plan_context.return_value = plan_context()
    repo.weekly_plan_message.return_value = "Aujourd'hui : EF, plafond 9 km."
    repo.persist_turn.return_value = PersistenceResult(True)
    repo.drain_alerts.return_value = []
    agent = FakeAgent(result=RunResult(full_text="La blessure, elle donne quoi ?"))
    bot = make_bot(repo=repo, client=MagicMock(), agent=agent,
                   proactive_active_start=dt.time(0, 0))
    bot._state.set_session("4242", "codex", "conversation")

    bot._respond_proactive(ProactiveJob(psid="4242", kind="morning", prompt="matin"))
    bot._respond_proactive(
        ProactiveJob(psid="4242", kind="morning", prompt="matin", attempts=2))

    assert [call["session_mode"] for call in agent.calls] == [
        SessionMode.FORK, SessionMode.FORK]
    assert bot._state.session_id("4242", "codex") == "conversation"


# --------------------------------- 4. an observation does not close the day

def test_an_observation_gate_leaves_the_week_prescriptible():
    """End to end: the model does not block, so persistence is not AWAITING, so the
    canonical plan goes out and the card of the day with it."""
    from redline.model import weekly_plan

    p = plan([session(status="done")])
    p["revisions"][0]["gates"] = [gate(when="apres")]
    today = dt.date(2026, 8, 8)
    assert weekly_plan.due_gates(p, today=today)          # the question is asked
    assert weekly_plan.blocking_gates(p, today=today) == []   # it closes nothing
    assert weekly_plan.gate_reasons(p, today=today) == []


def test_the_two_kinds_reach_the_prompt_as_two_separate_instructions():
    """Merged into a single note, the ban on prescribing of a precondition applied to
    an observation."""
    from redline.service.prompts import gate_note

    context = json.dumps({"revision": 3, "gates": [
        {"id": "g1", "due": "2026-08-18", "when": "avant", "holds": ["s1"],
         "question": "la blessure au réveil ?", "on_fail": "rest"},
        {"id": "g2", "due": "2026-08-18", "when": "apres",
         "question": "la cheville a parlé pendant la pliométrie ?", "on_fail": "on retire"},
    ]})
    blocking, observing = due_gates_by_kind(context, today="2026-08-18")
    assert [g["id"] for g in blocking] == ["g1"]
    assert [g["id"] for g in observing] == ["g2"]

    note = gate_note(blocking, observing)
    interdiction, permission = note.split("\n\n")
    assert "la blessure au réveil ?" in interdiction
    assert "NE PRESCRIS PAS les séances retenues (s1)" in interdiction
    # The observation carries the OPPOSITE instruction, and names the card explicitly.
    assert "la cheville a parlé" in permission
    assert "NE PRESCRIS PAS" not in permission
    assert "ELLE NE RETIENT RIEN" in permission
    assert "carte de renfo" in permission


def test_a_lone_observation_produces_no_prohibition_at_all():
    from redline.service.prompts import gate_note

    note = gate_note([], [{"id": "g", "due": "2026-08-18", "when": "apres",
                           "question": "la cheville ?", "on_fail": "on retire"}])
    assert "NE PRESCRIS" not in note
    assert gate_note([], []) == ""
