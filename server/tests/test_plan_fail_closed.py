"""Aucune séance ne part sans programme valide, et aucune réponse n'est jetée pour ça.

La porte déterministe retient le BLOC de séances et les faits non enregistrés ; la prose
du coach part toujours, avec une ligne précise quand une limite concerne Sam.
"""

import json
from datetime import date, timedelta
from datetime import time as dtime
from unittest.mock import MagicMock

import pytest
from conftest import FakeAgent

from redline.agent import RunResult
from redline.proactive import ProactiveJob
from redline.vault import PersistenceResult

#: Les ouvertures des textos de programme.
CANONICAL = ("Programme", "Aujourd'hui", "Prochaine séance", "La suite de la semaine", "Changement", "Rien de prévu")


def context(revision, sessions=None):
    return json.dumps({"revision": revision,
                       "sessions": [] if sessions is None else sessions})


def session(day, **changed) -> dict:
    """Une séance à faire ce jour-là. Les contextes de ce fichier en portent depuis le
    2026-08-16 : l'envoi ne suit plus un numéro de révision mais ce qu'il reste à
    faire, donc une semaine vide ne fait plus partir de bloc."""
    return {"id": f"w32-r1-{day.isoformat()}-ef", "title": "EF", "date": day.isoformat(),
            "kind": "run", "workout": "EF", "status": "planned",
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
    """Une écriture refusée n'efface ni la prose ni la conversation : Sam lit ce que
    le coach a écrit ET ce qui n'a pas été enregistré. Aucune séance ne part."""
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
    """Le triage juge « P » sur le TEXTE, avant d'avoir ouvert le vault — le prompt dit
    lui-même « MODIFIE potentiellement ». Exiger une révision derrière cette hypothèse
    punissait la bonne réponse : le 2026-08-10 à 10h15, Sam raconte sa sortie, le plan
    W33 r1 la couvrait déjà, Claude n'a donc rien révisé, et le coach s'est verrouillé en
    annonçant un « programme invalide » parfaitement valide — sans commit tenté et sans
    une ligne de log.

    Ce que le tour DOIT faire n'a pas changé : parler normalement. Ce qu'il ne fait plus,
    depuis le 2026-08-12, c'est ressortir le programme derrière — voir
    `test_plan_delivery.py`, et le drapeau « P » n'est plus le déclencheur d'un envoi.
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
    """Le verrou qui compte, lui, ne bouge pas : sans révision canonique, pas de bloc de
    séances. La réponse part ; Sam n'a pas demandé son programme, rien n'est ajouté."""
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
    """Reproduction du 31/08 : corriger la chaussure est N3, pas une prescription."""
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
    """Le plan ACCOMPAGNE le coach, il ne le remplace plus (2026-08-11).

    Remplacer verrouillait sur un indice de ce qu'on protège (« le modèle a parlé »)
    plutôt que sur ce qu'on protège (« une prescription non écrite engage Sam »), et
    au pire moment : un replan arrive quand la douleur monte ou qu'une séance saute,
    donc quand il y a le plus à expliquer. Ce matin-là, Sam a reçu « Tu dis que c'est
    plus haut que la normale ce matin. » puis sept jours de plan sérialisés, et rien
    d'autre — tout le coaching du message avait été jeté par cette ligne.

    Ce qui reste garanti, et ce que ce test pince : le plan persisté part, en dernier,
    dans son propre texto. C'est lui qui fait foi, et Sam l'a toujours sous les yeux.
    """
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    yesterday = date.today() - timedelta(days=1)
    # Le dimanche, demain appartient à la semaine suivante et ne peut donc pas
    # apparaître dans « la suite de la semaine ». Le test porte sur une séance encore
    # à livrer dans la semaine courante, pas sur le passage ISO dimanche -> lundi.
    remaining = date.today() + timedelta(days=1 if date.today().weekday() < 6 else 0)
    repo.weekly_plan_context.side_effect = [
        context(2, [session(yesterday), session(date.today())]),
        context(3, [session(yesterday), session(remaining, title="SL", workout="SL")])]
    repo.persist_turn.return_value = PersistenceResult(True)
    client = MagicMock()
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="On garde 9 km dimanche"),
                                     depth="N2", plan_change=True))
    bot._respond("4242", "finalement mon week-end est libre")
    assert "On garde 9 km dimanche" in client.send_burst.call_args_list[0].args[1]
    canonical = client.send_text.call_args_list[-1].args[1]
    assert canonical.startswith(CANONICAL)
    # La suite de la semaine, pas les jours déjà passés.
    assert f"{remaining:%d/%m}" in canonical and f"{yesterday:%d/%m}" not in canonical


def test_the_plan_still_leaves_on_an_n0_replan(make_bot):
    """Un replan ne demande pas de niveau de lecture : « je saute demain » est social par
    la forme et prescriptif par le fond. La semaine perd sa séance de demain — c'est CE
    CHANGEMENT qui fait partir le bloc, pas le niveau du triage ni son drapeau « P »."""
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
    """RWF-009 : le mot du matin n'est plus régénéré toutes les 5 minutes pour une
    semaine absente. Il part sans séance, et la réparation a un propriétaire unique."""
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    repo.weekly_plan_context.return_value = ""
    repo.persist_turn.return_value = PersistenceResult(True, invalid=("2026-W39",))
    repo.drain_alerts.return_value = []
    client = MagicMock()
    bot = make_bot(repo=repo, client=client, proactive_active_start=dtime(0, 0),
                   agent=FakeAgent(result=RunResult(full_text="Bien dormi ? Le injury ?")))
    store.proactive.enqueue_job("morning:today", "morning", "", 0)
    job = store.proactive.claim_due_job(1e12)

    bot._respond_proactive(ProactiveJob(psid="4242", kind="morning", prompt="matin",
                                        job_id=job["id"]))

    assert _sent(client) == ["Bien dormi ? Le injury ?"]
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
    """Un rendu cassé reste un travail du service, pas un signal pour l'athlète ni une
    raison de relancer le modèle : la prose part seule, l'incident reste au journal."""
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    repo.weekly_plan_context.return_value = context(2)
    repo.weekly_plan_message.return_value = ""      # le rendu casse
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
    """La fenêtre proactive est ouverte en grand, et c'est le point du test.

    Ces deux tests sont les premiers à aller jusqu'au budget d'envoi, qui lit
    `datetime.now()` : verts sur le Mac à 10h, rouges sur la CI qui tourne en UTC
    avant l'ouverture à 7h30 (2026-08-11). Une horloge implicite dans un chemin qu'on
    croit maîtrisé — la faute déjà commise le 2026-08-04 (voir README). Ce qui est
    mesuré ici est le contenu du message, jamais l'heure qu'il est.
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
    """Écraser `full_text` supprimait tout ce pour quoi le mot du matin existe : la
    question du contrôle daté, la relance de fil, la question `daily.csv`. Il ne restait
    qu'une ligne de séance — et un contrôle daté est justement une question à POSER
    avant de prescrire."""
    client = morning_bot(make_bot, "Le injury, il donne quoi au réveil ce matin ?")
    sent = " ".join(call.args[1] for call in client.send_burst.call_args_list)
    assert "au réveil ce matin" in sent
    assert "Renfo A" in sent


def test_a_silent_morning_still_announces_the_day(make_bot):
    """Le coach peut n'avoir rien à ajouter ; la séance du jour part quand même."""
    client = morning_bot(make_bot, "", silent=True)
    sent = " ".join(call.args[1] for call in client.send_burst.call_args_list)
    assert sent.strip() == "Aujourd'hui : Renfo A, 30 min"


def test_a_cell_is_marked_asked_only_when_a_message_really_leaves(make_bot):
    """« Demandé » veut dire qu'un message est parti, pas qu'un prompt a été assemblé.

    Le 2026-08-10 à 10h15, le tour s'est terminé en fail-closed sans qu'un mot sorte —
    et les trois cellules du jour ont quand même été classées « demandées ». La
    sentinelle `?` (« demandé, et perdu ») aurait pu tomber sur une question jamais
    posée, ce que le code s'interdit explicitement.
    """
    entry = {"date": "2026-08-10", "type": "EF", "missing": ["RPE", "Dlr", "Plaisir"]}
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    repo.weekly_plan_context.return_value = ""          # semaine sans plan valide
    repo.persist_turn.return_value = PersistenceResult(False)  # donc fail-closed
    repo.pending_ledger_cells.return_value = [entry]
    repo.drain_alerts.return_value = []
    bot = make_bot(repo=repo, client=MagicMock(),
                   agent=FakeAgent(result=RunResult(full_text="ok"), depth="N1",
                                   plan_asked=True))
    bot._respond("4242", "j'ai couru 7 km")
    markers = bot._state.proactive
    assert not any(markers.marked("asked:2026-08-10:EF:" + column)
                   for column in entry["missing"]), \
        "cellules classées « demandées » alors qu'aucun message n'est parti"
