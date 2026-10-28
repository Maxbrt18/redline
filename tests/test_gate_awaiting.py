"""Un contrôle daté échu attend une RÉPONSE ; il ne signale pas une panne.

La conversation du 2026-08-12, 07h46, que ces tests existent pour rendre impossible :

    Redline — Le programme hebdomadaire n'a pas passé ses contrôles. Je reste
              silencieux sur les séances plutôt que de ressortir un ancien plan.
    Sam  — Et bien règle le problème de programme. Prends ton temps
    Redline — Le vault contient déjà des modifications non enregistrées. Je n'écris
              rien tant qu'elles ne sont pas isolées.

Ce qui s'était réellement passé : le contrôle de douleur du jour tombait ce matin-là, donc
`check` rendait non nul, donc la persistance refusait le tour — et le message de coach
qui posait EXACTEMENT la question du contrôle a été jeté au profit de la phrase de
plomberie. Un mécanisme fait pour produire une question produisait une avarie. Les
écritures du tour refusé restaient ensuite dans le worktree, et le garde-fou « worktree
propre » a bâillonné les 25 tours suivants, y compris la question de Sam.

Trois verrous, un par étage :
  1. modèle et CLI — contrôle échu ≠ dérive matérielle (code de sortie 2, pas 1) ;
  2. vault — `AWAITING` s'enregistre, et un tour refusé ne laisse rien derrière lui ;
  3. bot — le coach parle, seule la prescription attend.
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

# ------------------------------------------------------- 1. le modèle et la CLI

def test_a_due_gate_is_exposed_as_a_question_not_a_storage_failure():
    """Un contrôle ne peut être levé que par la réponse de Sam."""
    from redline.model import weekly_plan

    p = week_with_a_later_session([precondition()])
    today = dt.date(2026, 8, 8)
    assert len(weekly_plan.due_gates(p, today=today)) == 1
    assert weekly_plan.gate_reasons(p, today=today)


def test_due_gates_reads_the_context_already_loaded():
    """Pas de second sous-processus pour savoir ce qu'on vient de lire.

    Le service ne fait que LIRE la chaîne : ce qu'est un contrôle échu, et ce qui
    sépare une pré-condition d'une observation, est déclaré par le modèle.
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


# ------------------------------------------------------------------ 2. le vault

def vault_at(clone):
    return VaultRepo(str(clone), "origin", "main")


def test_a_refused_turn_leaves_nothing_behind(git_clone):
    """LE bug du 2026-08-12 : le résidu d'un tour refusé bâillonnait tous les suivants.

    Un tour qui ne livre rien doit être annulable, sinon la transaction n'en est pas une.
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
    """Le filet, pour le cas que `abandon_turn` ne couvre pas : un crash du processus.

    Ce clone n'est écrit que par le service, donc ce qui traîne est notre propre
    résidu. On le jette et on travaille. Perdre une note jamais committée coûte
    infiniment moins cher que le coach muet une heure sur la question de Sam.
    """
    _, clone = git_clone
    repo = vault_at(clone)
    (clone / "orphelin.md").write_text("résidu d'un tour mort en vol")
    assert repo.ensure_clean() == ["orphelin.md"]
    assert repo.is_clean()
    assert repo.ensure_clean() == []


def test_the_outbox_survives_a_cleanup(git_clone):
    """`clean -fd` sans `-x` : une carte déjà rendue attend encore son archivage."""
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


# -------------------------------------------------------------------- 3. le bot

def plan_context(revision_number=2, gates=None):
    return json.dumps({"revision": revision_number, "sessions": [],
                       "gates": gates or []})


DUE_GATE = [{"id": "w33-injury", "due": "2000-01-01",
             "when": "avant", "holds": ["w33-ef"],
             "question": "La blessure au réveil ce matin, mieux ou pire qu'hier ?",
             "on_fail": "repos à la place de l'EF"}]


def test_the_coach_speaks_while_a_gate_holds_the_prescription(make_bot):
    """Le message qui aurait dû partir à 07h46 le 2026-08-12.

    Claude avait écrit la question du contrôle. Elle a été jetée. Ici elle part, et
    c'est la LIGNE DE SÉANCES qui attend — l'inverse exact de ce qui s'est produit.
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
    # Et Claude a été prévenu de la nature de l'attente, pas d'une avarie.
    assert "CONTRÔLE DATÉ ÉCHU" in agent.calls[0]["message"]


def test_a_due_gate_surfaces_even_on_a_social_message(make_bot):
    """« salut » triage en N0, qui ne charge pas le plan — et le contrôle du jour
    serait passé à la trappe.

    Même famille de panne que le 2026-08-12 : un mécanisme fait pour poser une question
    qui ne la pose pas. Un contrôle daté est une obligation de la JOURNÉE, il ne dépend
    pas du niveau de lecture du message. `show` coûte 0,13 s, l'excuse ne tient pas.
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
    """Le vrai coût du 2026-08-12 : ce n'est pas le message de 07h46, c'est l'heure
    de silence derrière. Un tour refusé nettoie, le tour suivant travaille."""
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
    """Annuler un tour rembobine git, pas la mémoire du modèle.

    2026-08-12, 09h04 : la session de la tentative de 07h46 croyait avoir déjà écrit son
    message, donc la reprise a répondu silence. Un proactif léger part désormais d'une
    COPIE de la conversation (fork) : première tentative ou reprise, la session
    principale ne garde aucune trace d'une initiative non livrée, et rien n'est effacé.
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


# --------------------------------- 4. une observation ne ferme pas la journée (18/08)

def test_an_observation_gate_leaves_the_week_prescriptible():
    """Bout en bout : le modèle ne bloque pas, donc la persistance n'est pas AWAITING,
    donc le plan canonique part et la carte du jour avec lui."""
    from redline.model import weekly_plan

    p = plan([session(status="done")])
    p["revisions"][0]["gates"] = [gate(when="apres")]
    today = dt.date(2026, 8, 8)
    assert weekly_plan.due_gates(p, today=today)          # la question se pose
    assert weekly_plan.blocking_gates(p, today=today) == []   # elle ne ferme rien
    assert weekly_plan.gate_reasons(p, today=today) == []


def test_the_two_kinds_reach_the_prompt_as_two_separate_instructions():
    """Fondues en une seule note, l'interdiction de prescrire d'une pré-condition
    s'appliquait à une observation : le message du 18/08, exactement."""
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
    # L'observation porte l'instruction INVERSE, et nomme la carte explicitement.
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
