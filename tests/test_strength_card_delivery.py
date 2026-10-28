"""La carte de renfo a un porteur, comme le plan hebdomadaire en a un.

La conversation du 2026-08-18, 07h45, que ces tests existent pour rendre impossible :

    Redline — C'est renfo A aujourd'hui. Sur la pliométrie, tu gardes bien le geste
              corrigé [...] Dis-le-moi ce soir.
    Sam  — Ok je ferai ça
    Redline — Nickel, tu me dis ça après la séance de renfo.
    Sam  — Tu ne m'as pas envoyé la séance de renfo, comment tu veux que je la
              fasse...
    Redline — Ah pardon, je pensais te l'avoir envoyée, la voilà.

Trois défauts empilés, un test par étage :
  1. la carte est rendue par le service depuis la prescription typée, sans déléguer
     sa génération au modèle ;
  2. rien ne constatait qu'un jour de renfo s'était terminé sans elle ;
  3. et le contrôle daté du jour, une observation typée comme une pré-condition,
     interdisait de prescrire dans le message même qui devait la livrer.
"""

import datetime as dt
import json
from unittest.mock import MagicMock

from conftest import FakeAgent

from redline.service.agent import RunResult
from redline.service.plans import strength_due
from redline.service.proactive import ProactiveJob

TODAY = dt.date.today().isoformat()


def context(sessions, gates=()):
    return json.dumps({"revision": 3, "sessions": sessions, "gates": list(gates)})


def renfo(status="planned", date=None):
    return {"id": "w34-mar-a", "title": "Renfo A, séance complète",
            "date": date or TODAY, "kind": "strength", "workout": "strength", "routine": "a",
            "status": status, "demanding": False,
            "strength": {},
            "note": "Séance complète, mollets dynamiques inclus.",
            "sources": ["[[strength]]"]}


def ef():
    return {"id": "w34-mar-ef", "title": "EF", "date": TODAY, "kind": "run",
            "workout": "easy", "status": "planned", "demanding": False,
            "sources": ["[[current-plan]]"]}


# --------------------------------------------------- 1. reconnaître le jour de renfo

def test_a_strength_session_due_today_is_recognised():
    assert strength_due(context([ef(), renfo()]), TODAY)["id"] == "w34-mar-a"


def test_a_strength_session_already_done_asks_for_nothing():
    assert strength_due(context([renfo(status="done")]), TODAY) is None
    assert strength_due(context([renfo(status="cancelled")]), TODAY) is None


def test_a_strength_session_on_another_day_asks_for_nothing():
    assert strength_due(context([renfo(date="2026-01-01")]), TODAY) is None
    assert strength_due(context([ef()]), TODAY) is None
    assert strength_due("pas du json", TODAY) is None


# ------------------------------------- 2. l'instruction arrive en tête, pas en clause

def _morning(make_bot, sessions, gates=(), reply="Bonne séance ce matin.",
             produced=()):
    from redline.service.vault import PersistenceResult

    repo = MagicMock()

    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    repo.ensure_clean.return_value = []
    repo.head.return_value = "0123456789abcdef"
    repo.weekly_plan_context.return_value = context(sessions, gates)
    repo.weekly_plan_message.return_value = "Aujourd'hui : renfo A."
    repo.persist_turn.return_value = PersistenceResult(True)
    repo.drain_alerts.return_value = []

    outbox = MagicMock()
    outbox.snapshot.return_value = set()
    outbox.leftovers.return_value = []
    outbox.collect_new.return_value = [MagicMock(name=name) for name in produced]
    for mock, name in zip(outbox.collect_new.return_value, produced, strict=True):
        type(mock).name = name
    outbox.link_for.return_value = None

    client = MagicMock()
    agent = FakeAgent(result=RunResult(full_text=reply))
    bot = make_bot(repo=repo, client=client, agent=agent, outbox=outbox,
                   proactive_active_start=dt.time(0, 0))
    bot._respond_proactive(ProactiveJob(psid="4242", kind="morning", prompt="matin"))
    return agent, client


def test_the_morning_does_not_delegate_the_card_to_the_agent(make_bot):
    agent, _ = _morning(make_bot, [renfo()], produced=["strength-2026-08-18.png"])
    message = agent.calls[0]["message"]
    assert "SÉANCE DE RENFO AUJOURD'HUI" not in message
    assert "chart-strength.py" not in message


def test_a_day_without_strength_gets_no_such_instruction(make_bot):
    agent, _ = _morning(make_bot, [ef()])
    assert "SÉANCE DE RENFO AUJOURD'HUI" not in agent.calls[0]["message"]


def test_the_scheduler_materializes_a_typed_due_session_without_an_agent(make_bot):
    agent = FakeAgent()
    bot = make_bot(agent=agent)
    bot._repo.weekly_plan_context.return_value = context([renfo()])

    assert bot.materialize_strength_card("4242", dt.datetime.now().astimezone()) is True

    jobs = bot._state.proactive.jobs(active_only=True)
    assert [job["kind"] for job in jobs] == ["strength_card"]
    assert agent.calls == []


# ------------------------------------------ 3. et le service vérifie derrière l'agent

def test_a_morning_arms_the_card_without_technical_text(make_bot):
    agent, client = _morning(make_bot, [renfo()], produced=[])
    client.send_text.assert_not_called()
    assert client.send_burst.called
    assert len(agent.calls) == 1


def test_a_morning_that_produced_its_card_says_nothing(make_bot):
    _, client = _morning(make_bot, [renfo()], produced=["strength-2026-08-18.png"])
    client.send_text.assert_not_called()


def test_another_card_is_not_reused_as_the_strength_card(make_bot):
    """La dette est typée ; aucun préfixe de fichier ne la déclenche."""
    _, client = _morning(make_bot, [renfo()], produced=["week-34.png"])
    client.send_text.assert_not_called()
    assert client.send_burst.called
