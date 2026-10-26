"""Un débrief muet est une panne à reprendre, pas un message pour Sam.

Le 2026-08-14 au matin, ce que Sam a vécu :

    08:33  Proactive trigger: strava_debrief (priority)
    08:34  Proactive strava_debrief -> SILENCE (nothing useful to say)
    08:53  Sam — T'as vu ma sortie de ce matin ?
           Redline — Pas encore, je n'ai pas ses données sous les yeux.

La trace du run montre un coach qui a cherché honnêtement : outil Strava dans sa liste,
grep du dépôt, ouverture de la page de l'activité, `STRAVA_ACCESS_TOKEN`, curl. Cinq
tentatives, aucun accès — Strava n'existait que comme MCP déclaré pour le moteur Claude,
et le service tournait sur Codex depuis deux jours.

Le log a écrit « nothing useful to say » pour une activité qui venait d'arriver. C'est
la phrase qui a rendu la panne invisible : un mot du matin a le droit de n'avoir rien à
dire, un débrief non — il n'existe QUE parce qu'il y a quelque chose à débriefer.
"""

import time
from unittest.mock import MagicMock

from conftest import FakeAgent

from redline.agent import RunResult
from redline.bot import SILENCE_IS_A_FAILURE
from redline.proactive import ProactiveJob


def _silent_bot(make_bot):
    """Un coach qui choisit de se taire (`send=false`), comme le 2026-08-14 à 08h34."""
    client = MagicMock()
    client.send_text.return_value = MagicMock(ok=True)
    return make_bot(agent=FakeAgent(result=RunResult(full_text="", silent=True)),
                    client=client), client


def _claimed(store, kind):
    store.proactive.enqueue_job(f"{kind}:1", kind, "90000000002", time.time() - 1)
    record = store.proactive.claim_due_job(time.time())
    return ProactiveJob(psid="4242", kind=kind, prompt="débriefe", job_id=record["id"],
                        detail=record["detail"], attempts=record["attempts"])


def test_a_debrief_that_goes_silent_is_retried_once_then_failed(make_bot, store):
    """Une seule reprise : avant, toutes les 5 minutes pendant quatre heures."""
    bot, client = _silent_bot(make_bot)

    bot._respond_proactive(_claimed(store, "strava_debrief"))
    assert store.proactive.jobs(active_only=True)[0]["kind"] == "strava_debrief"
    store.proactive.retry_job("strava_debrief:1", time.time() - 1, time.time() + 3600)
    bot._respond_proactive(_claimed(store, "strava_debrief"))

    client.send_text.assert_not_called()
    job = store.proactive.jobs()[0]
    assert job["status"] == "failed" and "muet" in job["last_error"]
    assert len(bot._agent.calls) == 2


def test_the_deferred_debrief_is_held_to_the_same_rule(make_bot, store):
    bot, client = _silent_bot(make_bot)

    bot._respond_proactive(_claimed(store, "strava_debrief_differe"))

    assert not client.send_text.called
    assert store.proactive.jobs(active_only=True)[0]["kind"] == \
        "strava_debrief_differe"


def test_a_monthly_note_keeps_the_right_to_say_nothing(make_bot):
    """Le silence reste la bonne réponse quand rien ne mérite un message : transformer
    tout SILENCE en incident rendrait le canal bavard, ce qui est l'autre faute."""
    bot, client = _silent_bot(make_bot)

    bot._respond_proactive(ProactiveJob(psid="4242", kind="monthly", prompt="magazine"))

    client.send_text.assert_not_called()


def test_a_morning_note_may_also_stay_silent(make_bot, store):
    bot, client = _silent_bot(make_bot)
    bot._repo.weekly_plan_message.return_value = ""
    bot._repo.weekly_plan_context.return_value = '{"revision": 2, "sessions": []}'

    bot._respond_proactive(_claimed(store, "morning"))

    client.send_text.assert_not_called()
    client.send_burst.assert_not_called()
    assert store.proactive.jobs()[0]["status"] == "done"


def test_only_the_activity_driven_triggers_treat_silence_as_a_failure():
    """La liste est explicite : un déclencheur ajouté demain choisit son camp, il ne
    l'hérite pas d'un défaut."""
    assert {"strava_debrief", "strava_debrief_differe"} == SILENCE_IS_A_FAILURE


def test_a_debrief_that_actually_speaks_is_never_flagged_as_an_incident(make_bot):
    client = MagicMock()
    client.send_text.return_value = MagicMock(ok=True)
    client.send_burst.return_value = MagicMock(ok=True)
    bot = make_bot(agent=FakeAgent(result=RunResult(full_text="Belle sortie, 6,9 km.")),
                   client=client)

    bot._respond_proactive(ProactiveJob(psid="4242", kind="strava_debrief",
                                        prompt="débriefe", detail="90000000002"))

    client.send_text.assert_not_called()
