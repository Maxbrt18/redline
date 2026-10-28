"""Une écriture refusée ne se confirme pas, mais la réponse du coach part toujours.

La porte déterministe agit sur l'effet (faits non enregistrés, séances non prescrites),
jamais sur la prose : Sam reçoit la réponse ET une ligne précise sur ce qui manque.
"""

from datetime import datetime
from datetime import time as dtime
from unittest.mock import MagicMock

from conftest import FakeAgent

from redline.agent import RunResult
from redline.proactive import ProactiveJob
from redline.vault import PersistenceFailure, PersistenceResult


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
    """Les faits refusés ne sont pas confirmés ; la réponse, elle, n'est pas jetée."""
    bot, client = _bot(make_bot, PersistenceResult(
        False, detail="mesure déjà connue", failure=PersistenceFailure.WRITE_REFUSED,
        refused=("measurement.complete",)))

    assert bot._respond("4242", "effort ressenti 7-8/10 et pic de douleur 0/10")

    envoye = _sent(client)
    assert "EF propre" in envoye
    assert "je n'ai pas pu enregistrer tes ressentis sur la séance" in envoye
    assert "mesure déjà connue" not in envoye


def test_a_refusal_of_internal_commands_only_adds_nothing(make_bot):
    """Une question déclarée perdue ne change rien pour Sam : aucune alarme."""
    bot, client = _bot(make_bot, PersistenceResult(
        False, detail="question inconnue", failure=PersistenceFailure.WRITE_REFUSED,
        refused=("question.ask",)))

    bot._respond("4242", "ok")

    envoye = _sent(client)
    assert "EF propre" in envoye
    assert "Attention" not in envoye


def test_a_refused_write_drops_the_session_that_now_believes_a_lie(make_bot):
    """Une session vit des heures et croit avoir écrit ce qui a été annulé. Le
    2026-08-14, après trois tours annulés, le coach a tenté de COMPLÉTER une
    observation jamais créée — « observation inconnue : strava:90000000002 » — et
    aurait recommencé indéfiniment. La base est relue au démarrage : repartir d'un
    contexte neuf est la seule correction sûre."""
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
    """Le contrôle négatif : on ne jette pas la continuité conversationnelle sans
    raison. Sans lui, un bot qui repart de zéro à chaque message passerait le test
    précédent."""
    bot, _ = _bot(make_bot, PersistenceResult(True))
    bot._state.set_session("4242", "codex", "session-vivante")

    bot._respond("4242", "salut")

    assert bot._state.session_id("4242", "codex") == "session-vivante"


def test_a_refused_write_prescribes_nothing(make_bot):
    """Le coach parle, mais un tour qui n'a rien pu écrire ne prescrit rien."""
    bot, client = _bot(make_bot, PersistenceResult(
        False, detail="mesure déjà connue", failure=PersistenceFailure.WRITE_REFUSED))

    bot._respond("4242", "effort ressenti 7-8/10")

    assert "plafond 9 km" not in _sent(client)


def test_a_stale_plan_holds_the_prescription_but_not_the_conversation(make_bot):
    """Même traitement qu'un contrôle daté échu : le programme ne décrit plus la
    semaine, il attend une révision. Ce n'est pas une avarie."""
    bot, client = _bot(make_bot, PersistenceResult(
        True, stale=("2026-W33",)))

    bot._respond("4242", "et ma sortie de ce matin ?")

    envoye = _sent(client)
    assert "EF propre" in envoye
    client.send_text.assert_not_called()
    assert "plafond 9 km" not in envoye


def test_an_absent_plan_holds_the_sessions_but_never_the_answer(make_bot):
    """Sans programme valide, aucune séance ne sort ; la réponse du coach, oui, et une
    ligne précise quand Sam a demandé son programme."""
    bot, client = _bot(make_bot, PersistenceResult(True, invalid=("2026-W39",)),
                       plan_context="", plan_change=True, plan_asked=True)

    bot._respond("4242", "je fais quoi demain ?")

    envoye = _sent(client)
    assert "EF propre" in envoye
    assert "plafond 9 km" not in envoye
    assert "pas de programme valide pour cette semaine" in envoye
    assert "absent ou invalide" not in envoye


def test_a_safety_answer_always_leaves_even_without_a_plan(make_bot):
    """RWF-002 : une consigne d'arrêt ou une orientation médicale n'attend ni lecture,
    ni programme valide (CLAUDE.md, Limites du coaching)."""
    answer = "Arrête maintenant et marche pour rentrer. Si la douleur persiste, avis médical."
    bot, client = _bot(make_bot, PersistenceResult(True, invalid=("2026-W39",)),
                       plan_context="", plan_change=True)
    bot._agent._result = RunResult(full_text=answer)

    bot._respond("4242", "douleur nette au genou en courant, j'arrête ?")

    envoye = _sent(client)
    assert answer in envoye
    assert "pas de programme valide" not in envoye


def test_the_morning_note_leaves_without_its_sessions_when_the_plan_is_stale(make_bot):
    """Le mot du matin prend des nouvelles, relance un fil, pose la question du jour :
    rien de tout ça ne dépend d'un programme à jour. Seule la ligne de séances attend
    que le programme soit remis en face du réalisé."""
    iso = datetime.now().isocalendar()
    semaine = f"{iso.year}-W{iso.week:02d}"
    # La fenêtre d'envoi par défaut (07:30-21:30) se lit en heure MURALE. Les runners
    # CI tournent en UTC : le 2026-08-15 à 06:41 puis 07:08 UTC, l'envoi était bloqué
    # par l'heure et ce test tombait sur un message vide — un rouge qui accusait le
    # rayon de souffle alors que la note du matin s'était assemblée correctement
    # (« programme périmé », journalisé). Son sujet n'est pas la fenêtre : on l'ouvre,
    # sinon il ne teste ce qu'il annonce que si l'horloge veut bien.
    bot, client = _bot(make_bot, PersistenceResult(
        True, stale=(semaine,)),
        proactive_active_start=dtime(0, 0))

    bot._respond_proactive(ProactiveJob(psid="4242", kind="morning", prompt="matin"))

    envoye = _sent(client)
    assert "EF propre" in envoye
    assert "plafond 9 km" not in envoye
    client.send_text.assert_not_called()


def test_a_healthy_turn_speaks_without_any_incident(make_bot):
    """Le contrôle négatif : sans panne ni péremption, le coach parle et rien ne
    ressemble à une avarie. Sans lui, un test qui vérifie l'absence d'un message
    passerait aussi sur un bot devenu complètement muet."""
    bot, client = _bot(make_bot, PersistenceResult(True),
                       plan_asked=True)

    bot._respond("4242", "c'est quoi mon programme ?")

    envoye = _sent(client)
    assert "EF propre" in envoye
    assert client.send_text.call_count == 1
    assert client.send_text.call_args.args[1].startswith(
        ("Programme", "La suite", "Rien de prévu", "Aujourd'hui", "Changement"))
