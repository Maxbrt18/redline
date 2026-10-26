import json
from datetime import date
from datetime import time as dtime

from conftest import FakeAgent

from redline.proactive import ProactiveJob
from redline.strength_artifact import render_strength_artifact


def test_strength_artifact_derives_identity_and_session_from_the_plan(monkeypatch, tmp_path):
    from redline.outbox import Outbox

    captured = {}

    def render(selection, target, *, root):
        captured.update(selection)
        target.write_bytes(b"png")
        return target, {
            "sid": selection["sid"], "session": selection["session"],
            "title": "Renfo A", "rule": "Garde une exécution propre.",
            "date": selection["date"], "duration_min": 30, "note": "",
            "exercises": [],
        }

    monkeypatch.setattr("redline.strength_artifact.strength_card.render_card", render)
    monkeypatch.setattr(
        "redline.strength_artifact.strength_card.snapshot",
        lambda built, root: {"sid": built["sid"], "exercises": [{"slug": "x"}]})
    session = {
        "id": "jeu-renfo-a", "workout": "renfo-a",
        "strength": {"half_dose": True},
    }

    target = render_strength_artifact(
        session, Outbox(tmp_path / "outbox"), "2026-08-27", root=tmp_path)

    assert target.name == "strength-2026-08-27.png"
    assert captured == {
        "session": "a", "date": "2026-08-27",
        "sid": "jeu-renfo-a-2026-08-27", "half_dose": True,
    }
    assert target.with_name(target.name + ".link").exists()


def test_morning_card_is_rendered_by_the_service_not_the_agent(
        monkeypatch, make_bot):
    today = date.today().isoformat()
    context = json.dumps({
        "revision": 3,
        "sessions": [{
            "id": "renfo-a", "title": "Renfo A", "date": today,
            "kind": "strength", "workout": "renfo-a", "status": "planned",
            "demanding": False, "strength": {}, "sources": ["strength.md"],
        }],
        "gates": [],
    })
    rendered = []
    def render(session, outbox, day):
        rendered.append((session["id"], day))
        return outbox.target(f"strength-{day}.png")

    monkeypatch.setattr("redline.bot.render_strength_artifact", render)
    agent = FakeAgent()
    bot = make_bot(
        agent=agent, proactive_active_start=dtime(0, 0))
    bot._repo.weekly_plan_context.return_value = context

    bot._respond_proactive(ProactiveJob(psid="4242", kind="morning", prompt="matin"))

    assert rendered == []
    queued = bot._state.proactive.jobs(active_only=True)
    assert [row["kind"] for row in queued] == ["strength_card"]
    row = queued[0]
    bot._respond_proactive(ProactiveJob(
        psid="4242", kind="strength_card", prompt="", job_id=row["id"],
        detail=row["detail"], priority=True))

    assert rendered == [("renfo-a", today)]
    assert len(agent.calls) == 1
    assert "chart-strength.py" not in agent.calls[0]["message"]
