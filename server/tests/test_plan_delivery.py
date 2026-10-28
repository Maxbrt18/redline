"""QUAND le programme canonique part — la question que personne ne testait.

`test_plan_message.py` pince ce que le bloc DIT une fois rendu ; ici on pince ce qui
déclenche son envoi. Le trou entre les deux a coûté la conversation du 2026-08-12 :
Sam demande si une sortie vélo avec son père pose problème, le coach lui répond
trois fois — et lui colle trois fois le même programme W33 r5, inchangé, plus trois
fois la même carte, sous une question que le coach venait de lui poser. Verdict :
« c'est super long ta façon de répéter le programme à chaque message ».

La règle depuis : le bloc suit un FAIT (ce qui reste à faire a changé, une demande,
une livraison perdue), jamais l'hypothèse « ce message touche peut-être au plan ».

Le fait s'est précisé le 2026-08-16 : il ne s'agit PAS d'un numéro de révision qui
monte. Une révision s'écrit aussi pour enregistrer du passé, et ce jour-là celle des
ressentis de la sortie du matin a renvoyé à Sam la séance qu'il venait de courir.
Les contextes de ces tests portent donc de vraies séances, et le rendu réel tourne :
c'est la couture entre « ce que le bloc dit » et « quand il part » qui a lâché.
"""

import json
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from conftest import FakeAgent

from redline.agent import RunResult
from redline.messenger import DeliveryResult
from redline.proactive import ProactiveJob
from redline.vault import PersistenceResult

TODAY = date.today()
TOMORROW = TODAY + timedelta(days=1)


def session(**changed) -> dict:
    """Une séance à faire aujourd'hui, sauf mention contraire."""
    return {"id": "w33-r1-jour-ef", "title": "EF", "date": TODAY.isoformat(),
            "kind": "run", "workout": "EF", "status": "planned",
            "distance_target_km": 7.0, "distance_cap_km": 9.0,
            "demanding": False, **changed}


def context(revision, sessions=None, gates=None):
    return json.dumps({"revision": revision,
                       "sessions": [session()] if sessions is None else sessions,
                       "gates": gates or []})


@pytest.fixture
def repo():
    """Un vault dont la semaine est valide, prescriptible, et déjà connue de Sam."""
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    repo.ensure_clean.return_value = []
    repo.head.return_value = "0123456789abcdef"
    repo.weekly_plan_context.return_value = context(5)
    # Seul le chemin PROACTIF passe encore par la méthode du repo ; le réactif rend
    # lui-même, depuis les deux contextes qu'il compare.
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


#: Les ouvertures des textos de programme (sans semaine ISO ni révision depuis le 15/10).
CANONICAL = ("Programme", "Aujourd'hui", "Prochaine séance", "La suite de la semaine", "Changement", "Rien de prévu")


def programmes(client):
    """Les textos canoniques réellement partis, avarie technique exclue."""
    return [call.args[1] for call in client.send_text.call_args_list
            if call.args[1].startswith(CANONICAL)]


def current_week(bot):
    """La semaine que le bot calcule lui-même : un test ne code pas l'heure en dur."""
    return bot._iso_week(datetime.now())


# ---------------------------------------------------------------- se taire

def test_a_whole_conversation_about_one_bike_ride_repeats_no_programme(repo, client,
                                                                       make_bot):
    """La conversation du 2026-08-12, rejouée telle quelle.

    Trois messages « P » légitimes — disponibilité, durée, jour — et un plan que rien
    ne modifie : le coach parle trois fois, le programme zéro.
    """
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="Tu la ferais quel jour ?"),
                                     depth="N2", plan_change=True))
    bot._state.owe_plan("4242", current_week(bot), 5)
    bot._state.settle_plan("4242")          # r5 est arrivé : le compte est soldé

    for message in ("je fais une sortie vélo de route avec mon père ça pose problème ?",
                    "ça sera max 1h30/2h mais de toute façon pas de choc",
                    "peut être demain"):
        bot._respond("4242", message)

    assert client.send_burst.call_count == 3, "le coach doit répondre à chaque fois"
    assert programmes(client) == []


def test_the_reactive_path_never_renders_the_plan_card(repo, client, make_bot):
    """La carte disait une troisième fois ce que la prose et le bloc disaient déjà.

    Elle reste au dimanche, où elle publie une semaine entière et où elle est le
    message. En réactif, une révision touche un ou deux jours : le texto suffit.
    """
    due_gate = {"id": "g1", "due": "2000-01-01", "when": "avant",
                "holds": ["w33-r1-jour-ef"],
                "question": "La blessure ?", "on_fail": "repos"}
    repo.weekly_plan_context.side_effect = [context(5), context(
        6, [session(date=TOMORROW.isoformat())], [due_gate])]
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="Je décale ton renfo"),
                                     depth="N2", plan_change=True))
    bot._respond("4242", "je peux pas jeudi finalement")

    assert programmes(client), "une vraie révision doit partir"
    repo.render_weekly_plan_card.assert_not_called()


# ---------------------------------------------------------------- parler

def test_a_new_revision_leaves_once_and_only_once(repo, client, make_bot):
    """Le fait qui justifie l'envoi est consommé par l'envoi : il ne rejoue pas."""
    moved = [session(date=TOMORROW.isoformat())]
    repo.weekly_plan_context.side_effect = [context(5), context(6, moved),  # tour 1
                                            context(6, moved), context(6, moved)]  # tour 2
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="Je décale ton renfo"),
                                     depth="N2", plan_change=True))
    bot._respond("4242", "je peux pas jeudi finalement")
    bot._respond("4242", "ok ça me va")

    assert len(programmes(client)) == 1


def test_a_revision_that_only_records_the_past_says_nothing(repo, client, make_bot):
    """Le 2026-08-16, rejoué tel quel — et la vraie définition du « fait ».

    Sam finit sa SL vallonnée, la débriefe, donne ses trois repères. Le coach écrit
    r12 pour consigner RPE 3-4, pic de douleur 0,75-1 et plaisir 7 : une révision de plus,
    dont la liste de séances est IDENTIQUE à celle de r11. Il ne reste rien à faire de
    la semaine, et Sam a pourtant reçu « la suite de la semaine : dim 16/08 : SL
    vallonnée, cible 10 km » — la séance dont il venait de rendre compte.

    Deux fautes cousues ensemble : le bloc partait sur un numéro qui monte, et son rendu
    ignorait `status`. Ce test tient les deux bouts.
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
    """Le contrepoids indispensable : sans « D », resserrer l'envoi rendait le coach
    MUET sur « je fais quoi demain ? » — la prose n'a pas le droit de prescrire, et
    « P » ne se lève pas sur une question qui ne change rien."""
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="Tu as ça au programme"),
                                     depth="N1", plan_scope="next"))
    bot._respond("4242", "je fais quoi demain ?")

    assert len(programmes(client)) == 1


def test_asking_twice_answers_twice(repo, client, make_bot):
    """Une question posée deux fois mérite deux réponses : la dette de livraison ne
    doit pas se transformer en « je te l'ai déjà dit »."""
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="Voilà"),
                                     depth="N1", plan_asked=True))
    bot._respond("4242", "c'est quoi demain ?")
    bot._respond("4242", "et le reste de la semaine ?")

    assert len(programmes(client)) == 2


def test_morning_initiative_does_not_repeat_a_revision_already_shown_today(
        repo, client, make_bot):
    """Le marqueur limite l'initiative, jamais une nouvelle demande de Sam."""
    bot = make_bot(
        repo=repo, client=client,
        agent=FakeAgent(result=RunResult(full_text="Voilà"), plan_scope="today"))
    bot._respond("4242", "je fais quoi aujourd'hui ?")
    assert len(programmes(client)) == 1

    bot._respond_proactive(ProactiveJob(psid="4242", kind="morning", prompt="bonjour"))

    assert len(programmes(client)) == 1


# ---------------------------------------------------------------- la dette

def test_a_programme_lost_in_transport_is_still_owed_on_the_next_turn(repo, client,
                                                                      make_bot):
    """Le seul cas où un programme inchangé repart, et la raison d'avoir une dette.

    Sans elle, un texto perdu ne repartait JAMAIS : le tour suivant rend deux fois le
    même texto, conclut « rien de neuf », et la prescription reste dans le serveur.
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
    bot._respond("4242", "tu disais ?")     # ni « P » ni « D » : c'est la dette qui parle

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
    """Une dette est datée. Celle de la semaine dernière ne fait pas parler celle-ci —
    sinon un dimanche raté rendrait le lundi bavard pour toujours."""
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="ok"), depth="N1"))
    bot._state.owe_plan("4242", "1999-W01", 5)
    bot._respond("4242", "salut")

    assert programmes(client) == []


# ------------------------------------------------------ ce que « P » commande

def test_a_replan_announced_in_one_line_still_validates_its_week(repo, client, make_bot):
    """« je saute demain finalement » : N0 par la forme, prescriptif par le fond.

    Le niveau de lecture pilotait seul le verrou fail-closed, si bien qu'un replan dit
    en cinq mots partait sans plan canonique dans le prompt et sans semaine à valider.
    Depuis que « P » ne déclenche plus d'envoi, c'est exactement — et seulement — ce
    qu'il commande : relire le plan, et engager la validation.
    """
    repo.weekly_plan_context.side_effect = [context(5), context(6)]
    agent = FakeAgent(result=RunResult(full_text="Noté"), depth="N0", plan_change=True)
    bot = make_bot(repo=repo, client=client, agent=agent)
    bot._respond("4242", "je saute demain finalement")

    assert "PLAN HEBDOMADAIRE CANONIQUE" in agent.calls[0]["message"]
    required = repo.persist_turn.call_args.kwargs["required_weeks"]
    assert required == (current_week(bot),)


# ------------------------------------------------------- ce qui ne bouge pas

def test_a_gate_still_holds_the_prescription_even_on_a_fresh_revision(repo, client,
                                                                      make_bot):
    """Un contrôle daté échu retient la ligne de séances, révision neuve ou pas."""
    due_gate = {"id": "g1", "due": TODAY.isoformat(), "when": "avant",
                "holds": ["w33-r1-jour-ef"], "question": "la blessure ?",
                "on_fail": "repos"}
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
    """Sans révision valide, aucun bloc de séances : la réponse du coach part, suivie
    d'une ligne qui dit précisément la limite à celui qui demandait son programme."""
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
    """Le succès des faits et la prescriptibilité sont deux verdicts distincts."""
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
    """La publication du dimanche garde sa carte : c'est la semaine entière, et c'est
    le seul endroit où l'image apporte plus que le texte."""
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="Bilan de la semaine")))
    bot._respond_proactive(ProactiveJob(psid="4242", kind="weekly", prompt="bilan"))

    repo.render_weekly_plan_card.assert_called_once()


def test_weekly_retry_on_monday_keeps_the_sunday_period(
        repo, client, make_bot, monkeypatch):
    """Le job durable nomme W34 : son retry lundi prépare encore W35, jamais W36."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import redline.bot as bot_module

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
    """`/deep` fixe le NIVEAU, rien d'autre. Reconstruire le triage champ par champ
    perdait en silence tout champ ajouté après coup — `plan_asked` en aurait été le
    premier mort."""
    bot = make_bot(repo=repo, client=client,
                   agent=FakeAgent(result=RunResult(full_text="Analyse"),
                                     depth="N1", plan_asked=True))
    bot._respond("4242", "explique moi", deep_request=True)

    assert len(programmes(client)) == 1
