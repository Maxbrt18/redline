"""Un débrief qui parle sans consigner sa séance est une panne interne.

⭐ LA CONTREPARTIE DE `required_weeks`. Un tour prescriptif doit PROUVER qu'il a révisé
la semaine, sinon rien ne part. Rien n'exigeait qu'un débrief prouve qu'il a consigné la
séance dont il venait de parler : son critère de succès était « un message est parti ».

Ce que ça a coûté, sur les deux jours de bascule vers Codex :

    13/08 10:21  Proactive strava_debrief sent in 18 s
                 (un seul exec, tué par le bac à sable :
                  « bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted »)
                 → « Comment ont réagi les jambes sur ce vélo ? » — sortie jamais lue,
                   jamais consignée, journalisée en SUCCÈS.
    14/08 08:34  strava.py sans identifiants → SILENCE. Deuxième sortie perdue.

Découvert le 16/08, par un volume hebdomadaire faux : 24,16 km pour 3 sorties au lieu
de 31,13 pour 4, et une jauge à 49/100 au lieu de 87. Le débrief muet était déjà traité
comme une panne (`test_debrief_silence.py`) ; le débrief BAVARD qui n'écrit rien, non —
et c'est celui du 13/08, le premier de la chaîne.
"""

from datetime import time as dtime
from unittest.mock import MagicMock

import pytest
from conftest import FakeAgent

from redline.service.agent import RunResult
from redline.service.proactive import ProactiveJob
from redline.service.vault import PersistenceResult

ACTIVITE = "90000000001"


@pytest.fixture
def client():
    client = MagicMock()
    for method in ("send_text", "send_burst", "send_image", "send_button", "action"):
        getattr(client, method).return_value = MagicMock(ok=True)
    return client


def _bot(make_bot, client, *, recorded: bool):
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    repo.ensure_clean.return_value = []
    repo.head.return_value = "0123456789abcdef"
    repo.weekly_plan_context.return_value = ""
    repo.weekly_plan_message.return_value = "Aujourd'hui : EF, plafond 9 km."
    repo.persist_turn.return_value = PersistenceResult(True)
    repo.drain_alerts.return_value = []
    repo.activity_recorded.return_value = recorded
    return make_bot(repo=repo, client=client,
                    # Fenêtre ouverte 24 h, comme partout ailleurs dans la suite : sans
                    # ça le test dépend de l'heure qu'il est. Écrit un dimanche matin,
                    # il passait ; relancé le soir même à 23 h, les trois assertions
                    # tombaient sur « budget/hour blocks the send » et le contrat du
                    # débrief n'était plus vérifié du tout.
                    proactive_active_start=dtime(0, 0),
                    agent=FakeAgent(result=RunResult(
                        full_text="Comment ont réagi les jambes sur ce vélo ?")))


def test_a_debrief_that_speaks_without_recording_is_logged_not_announced(
        make_bot, client, caplog):
    bot = _bot(make_bot, client, recorded=False)

    bot._respond_proactive(ProactiveJob(psid="4242", kind="strava_debrief",
                                        prompt="débriefe", detail=ACTIVITE))

    assert client.send_burst.called, "la prose du coach part quand même"
    client.send_text.assert_not_called()
    assert "debrief-non-consigne" in caplog.text


def test_the_check_asks_about_the_activity_of_this_very_job(make_bot, client):
    bot = _bot(make_bot, client, recorded=False)

    bot._respond_proactive(ProactiveJob(psid="4242", kind="strava_debrief",
                                        prompt="débriefe", detail=ACTIVITE))

    bot._repo.activity_recorded.assert_called_with(ACTIVITE)


def test_a_recorded_activity_raises_nothing(make_bot, client):
    """Le contrôle négatif : un débrief qui fait son travail ne produit aucun bruit."""
    bot = _bot(make_bot, client, recorded=True)

    bot._respond_proactive(ProactiveJob(psid="4242", kind="strava_debrief",
                                        prompt="débriefe", detail=ACTIVITE))

    client.send_text.assert_not_called()


def test_the_deferred_debrief_carries_the_same_contract(make_bot, client, caplog):
    bot = _bot(make_bot, client, recorded=False)

    bot._respond_proactive(ProactiveJob(psid="4242", kind="strava_debrief_differe",
                                        prompt="débriefe", detail=ACTIVITE))

    client.send_text.assert_not_called()
    assert "debrief-non-consigne" in caplog.text


def test_a_trigger_without_an_activity_is_never_accused(make_bot, client):
    """Le mot du matin ne part d'aucune activité : lui demander d'en consigner une
    inventerait une panne à chaque réveil."""
    bot = _bot(make_bot, client, recorded=False)

    bot._respond_proactive(ProactiveJob(psid="4242", kind="morning", prompt="matin"))

    client.send_text.assert_not_called()
    bot._repo.activity_recorded.assert_not_called()
