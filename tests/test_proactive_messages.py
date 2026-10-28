import json
from datetime import datetime
from datetime import time as dtime
from unittest.mock import MagicMock

import pytest
from conftest import FakeAgent

from redline.service.proactive import ProactiveJob
from redline.service.proactive_messages import (
    CLOSED_KINDS,
    compose_closed_proactive,
    missing_cells_in_notebook,
)


@pytest.mark.parametrize("kind,detail,fragment", [
    ("silence", "4", "4 jours"),
    ("registre_incomplet", json.dumps({
        "date": "2026-08-22", "type": "easy", "missing": ["RPE", "Dlr"],
        "markers": ["2026-08-22:easy:RPE", "2026-08-22:easy:Dlr"],
    }), "pic de douleur"),
    ("missed_session", '{"title":"EF 7 km"}', "EF 7 km"),
    ("gut_review", '{"title":"SL","target_g_h":60}', "glucides par heure"),
    ("gear_alert", '{"name":"Novablast","km":540.2,"limit":600}', "540.2 km"),
    ("operator_message", '{"text":"Bilan W39 : semaine tenue."}', "Bilan W39"),
])
def test_closed_proactives_are_rendered_from_their_typed_detail(kind, detail, fragment):
    rendered = compose_closed_proactive(kind, detail)

    assert rendered is not None
    assert fragment in rendered.text


def test_only_explicitly_closed_kinds_skip_judgement():
    assert compose_closed_proactive("morning", "{}") is None
    assert "morning" not in CLOSED_KINDS
    assert compose_closed_proactive("calibration_eve", '{"title":"SL"}') is None  # removed
    assert compose_closed_proactive("operator_message", '{"text":"  "}') is None


@pytest.mark.parametrize("kind,detail", [
    ("silence", "4"),
    ("missed_session", '{"title":"EF"}'),
    ("gut_review", '{"title":"SL"}'),
    ("gear_alert", '{"name":"Novablast","km":540,"limit":600}'),
    ("operator_message", '{"text":"Bilan W39 : semaine tenue."}'),
])
def test_bot_delivers_closed_proactives_without_waking_the_agent(
        make_bot, store, kind, detail):
    agent = FakeAgent()
    bot = make_bot(
        agent=agent, proactive_active_start=dtime(0, 0))

    store._session_cycles.append({"id": "test-cycle", "state": "missed"})
    store.proactive.enqueue_job("missed-session:test-cycle", kind, detail, 0)
    bot._respond_proactive(ProactiveJob(psid="4242", kind=kind, prompt="", detail=detail,
                                       job_id="missed-session:test-cycle"))

    assert agent.calls == []
    bot._client.send_burst.assert_called_once()


@pytest.mark.parametrize("state", ["completed", "closed", "cancelled", "planned"])
def test_a_queued_missed_session_is_rechecked_before_delivery(make_bot, store, state):
    bot = make_bot(proactive_active_start=dtime(0, 0))
    store._session_cycles.append({"id": "test-cycle", "state": state})
    job_id = "missed-session:test-cycle"
    store.proactive.enqueue_job(job_id, "missed_session", '{"title":"Renfo A"}', 0)
    bot._respond_proactive(ProactiveJob(
        psid="4242", kind="missed_session", prompt="", detail='{"title":"Renfo A"}',
        job_id=job_id))
    bot._client.send_burst.assert_not_called()
    assert store.proactive.jobs()[0]["status"] == "done"


def test_closed_ledger_claim_marks_exactly_the_cells_that_were_delivered(make_bot, store):
    detail = json.dumps({
        "date": "2026-08-22", "type": "easy", "missing": ["RPE"],
        "markers": ["cell-1"],
    })
    bot = make_bot(
        proactive_active_start=dtime(0, 0))
    bot._repo.pending_ledger_cells.return_value = [
        {"date": "2026-08-22", "type": "easy", "missing": ["RPE"]}]

    bot._respond_proactive(ProactiveJob(
        psid="4242", kind="registre_incomplet", prompt="", detail=detail))

    assert store.proactive.marked("asked:2026-08-22:easy:RPE")


def test_a_closed_claim_asks_one_metric_and_marks_only_that_metric():
    rendered = compose_closed_proactive("registre_incomplet", json.dumps({
        "date": "2026-09-05", "type": "easy", "missing": ["RPE", "Dlr", "Plaisir"],
        "markers": ["rpe", "pain", "enjoyment"],
    }))
    assert "pic de douleur" in rendered.text
    assert "RPE" not in rendered.text and "plaisir" not in rendered.text
    assert rendered.markers == ("pain",)


def test_a_wake_claim_is_never_rendered_as_a_numeric_evening_question():
    rendered = compose_closed_proactive("registre_incomplet", json.dumps({
        "date": "2026-09-14", "type": "wake-trace", "missing": ["Dlr"],
        "markers": ["2026-09-14:wake-trace:Dlr"],
    }))

    assert rendered is None


def test_a_queued_wake_claim_is_discarded_without_message_or_agent(make_bot, store):
    bot = make_bot(proactive_active_start=dtime(0, 0))
    detail = json.dumps({
        "date": "2026-09-14", "type": "wake-trace", "missing": ["Dlr"],
        "markers": ["2026-09-14:wake-trace:Dlr"],
    })
    job_id = "cells:2026-09-15"
    store.proactive.enqueue_job(job_id, "registre_incomplet", detail, 0)

    bot._respond_proactive(ProactiveJob(
        psid="4242", kind="registre_incomplet", prompt="", detail=detail,
        job_id=job_id))

    bot._client.send_burst.assert_not_called()
    assert bot._agent.calls == []
    assert store.proactive.jobs()[0]["status"] == "done"


def test_a_queued_claim_does_not_repeat_a_value_received_since_it_was_queued(make_bot, store):
    bot = make_bot(proactive_active_start=dtime(0, 0))
    bot._repo.pending_ledger_cells.return_value = []
    detail = json.dumps({"date": "2026-09-05", "type": "easy", "missing": ["RPE"]})
    store.proactive.enqueue_job("cells:test", "registre_incomplet", detail, 0)
    bot._respond_proactive(ProactiveJob(psid="4242", kind="registre_incomplet", prompt="",
                                       detail=detail, job_id="cells:test"))
    bot._client.send_burst.assert_not_called()
    assert store.proactive.jobs()[0]["status"] == "done"


def test_queued_rotation_check_is_discarded_without_message_or_agent(make_bot):
    bot = make_bot()
    bot._respond_proactive(ProactiveJob(
        psid="4242", kind="gear_alert", detail='{"reason":"rotation","name":"Mach 7"}',
        prompt="rotation"))
    bot._client.send_text.assert_not_called()
    bot._client.send_burst.assert_not_called()
    assert bot._agent.calls == []


@pytest.mark.parametrize("kind,detail,tracked", [
    ("missed_session", '{"title":"EF 7 km"}', True),
    ("gut_review", '{"title":"SL"}', True),
    ("gear_alert", '{"name":"Novablast","km":540,"limit":600}', True),
    ("registre_incomplet", json.dumps({
        "date": "2026-08-22", "type": "easy", "missing": ["RPE"], "markers": ["m"]}), True),
    # Neither debt nor agenda: social check-in, text written outside the service.
    ("silence", "4", False),
    ("operator_message", '{"text":"Bilan W39 : semaine tenue."}', False),
])
def test_a_closed_render_names_the_information_it_expects(kind, detail, tracked):
    rendered = compose_closed_proactive(kind, detail)
    assert bool(rendered.question) is tracked
    # Matching after delivery requires an exact substring of the text that was sent.
    assert rendered.question in rendered.text


def test_a_delivered_closed_question_is_tracked_and_never_asked_twice(make_bot, store):
    """Without `turn_id`, the question of a closed rendering never entered the
        question memory, so nothing prevented asking it again."""
    bot = make_bot(proactive_active_start=dtime(0, 0))
    detail = '{"title":"SL","target_g_h":60}'
    expected = compose_closed_proactive("gut_review", detail).question

    store.proactive.enqueue_job("gut:1", "gut_review", detail, 0)
    bot._respond_proactive(ProactiveJob(psid="4242", kind="gut_review", prompt="",
                                        detail=detail, job_id="gut:1"))
    bot._client.send_burst.assert_called_once()
    assert store._conversation[-1]["questions"] == (expected,)
    assert store.question_known("4242", expected)

    store.proactive.enqueue_job("gut:2", "gut_review", detail, 0)
    bot._respond_proactive(ProactiveJob(psid="4242", kind="gut_review", prompt="",
                                        detail=detail, job_id="gut:2"))
    bot._client.send_burst.assert_called_once()
    assert {job["status"] for job in store.proactive.jobs()} == {"done"}


def test_an_unreadable_question_memory_does_not_swallow_a_collection(make_bot, store,
                                                                      monkeypatch):
    bot = make_bot(proactive_active_start=dtime(0, 0))
    detail = '{"title":"SL"}'

    def broken(psid, text):
        raise RuntimeError("base indisponible")

    monkeypatch.setattr(store, "question_known", broken)
    store.proactive.enqueue_job("gut:1", "gut_review", detail, 0)
    bot._respond_proactive(ProactiveJob(psid="4242", kind="gut_review", prompt="",
                                        detail=detail, job_id="gut:1"))
    bot._client.send_burst.assert_called_once()


def _connected_notebook(bot, connected=True):
    notebook = MagicMock()
    notebook.connected.return_value = connected
    notebook.capture.return_value = []
    notebook.link.return_value = "https://coach.example/carnet#t=jeton"
    bot._delivery.notebook = notebook
    return notebook


def test_with_a_connected_notebook_the_evening_claim_is_a_button_not_a_question(
        make_bot, store):
    """The reply in Messenger cost a coach turn for a figure the logbook takes in one
        tap. The reminder lists everything and points to the logbook."""
    agent = FakeAgent()
    bot = make_bot(agent=agent, proactive_active_start=dtime(0, 0))
    _connected_notebook(bot)
    bot._repo.pending_ledger_cells.return_value = [
        {"date": "2026-10-08", "type": "easy", "missing": ["RPE", "Dlr", "Plaisir"]}]

    bot._respond_proactive(ProactiveJob(
        psid="4242", kind="registre_incomplet", prompt="", detail=json.dumps(
            {"date": "2026-10-08", "type": "easy", "missing": ["RPE"]})))

    assert agent.calls == []
    bot._client.send_burst.assert_not_called()
    _, text, url, title = bot._client.send_button.call_args.args
    assert text == ("Ta séance EF du 08/10 attend encore ton pic de douleur, ton RPE et "
                    "ton plaisir : un tap dans le carnet.")
    assert url.endswith("#t=jeton") and title == "Ouvrir mon carnet"
    # One reminder per cell: the next day, the safety net does not ask for it again.
    assert store.proactive.marked("asked:2026-10-08:easy:RPE")
    assert store.proactive.marked("asked:2026-10-08:easy:Plaisir")


def test_without_a_connected_notebook_the_evening_claim_still_asks_in_messenger(
        make_bot, store):
    bot = make_bot(proactive_active_start=dtime(0, 0))
    _connected_notebook(bot, connected=False)
    bot._repo.pending_ledger_cells.return_value = [
        {"date": "2026-10-08", "type": "easy", "missing": ["RPE"]}]

    bot._respond_proactive(ProactiveJob(
        psid="4242", kind="registre_incomplet", prompt="", detail=json.dumps(
            {"date": "2026-10-08", "type": "easy", "missing": ["RPE"]})))

    bot._client.send_button.assert_not_called()
    assert "RPE global sur 10" in bot._client.send_burst.call_args.args[1]


def test_the_notebook_reminder_never_carries_a_wake_trace():
    assert missing_cells_in_notebook(json.dumps(
        {"date": "2026-10-08", "type": "wake-trace", "missing": ["Dlr"]})) is None


def test_the_carnet_note_reaches_the_turns_that_used_to_ask(make_bot):
    from redline.service.prompts import CARNET_NOTE

    bot = make_bot()
    _connected_notebook(bot)
    moment = datetime(2026, 10, 9, 7, 30)
    for kind, expected in (("strava_debrief", True), ("morning", True),
                           ("strava_debrief_differe", True), ("weekly", False)):
        prompt, _, _ = bot._proactive_prompt(
            ProactiveJob(psid="4242", kind=kind, prompt="", detail=""), "", moment)
        assert (CARNET_NOTE in prompt) is expected, kind
    _connected_notebook(bot, connected=False)
    prompt, _, _ = bot._proactive_prompt(
        ProactiveJob(psid="4242", kind="morning", prompt="", detail=""), "", moment)
    assert CARNET_NOTE not in prompt


def test_a_stored_notebook_button_never_keeps_its_one_time_link():
    from redline.service.delivery import (
        NotebookButtonPart,
        OutboundEnvelope,
        StoredDelivery,
        envelope_from_payload,
        envelope_payload,
    )

    envelope = OutboundEnvelope("4242", "proactive:cells", (NotebookButtonPart("Il manque ton RPE."),))
    payload = envelope_payload(envelope)
    assert payload["parts"] == [{"kind": "notebook_button", "text": "Il manque ton RPE."}]
    replayed = envelope_from_payload(StoredDelivery("proactive:cells", "4242", "failed", payload))
    assert replayed.parts == envelope.parts


def test_the_wake_reminder_points_to_the_notebook_and_accepts_a_chat_answer(
        make_bot, store):
    agent = FakeAgent()
    bot = make_bot(agent=agent, proactive_active_start=dtime(0, 0))
    notebook = _connected_notebook(bot)
    notebook.wake_reminder_due.return_value = True
    store.proactive.enqueue_job("trace-reveil:1", "trace_reveil", "", 0)

    bot._respond_proactive(ProactiveJob(
        psid="4242", kind="trace_reveil", prompt="", detail="", job_id="trace-reveil:1"))

    assert agent.calls == []
    _, text, url, title = bot._client.send_button.call_args.args
    assert "aucune, très légère, légère ou nette ?" in text and "réponds-moi ici" in text
    assert url.endswith("#t=jeton") and title == "Ouvrir mon carnet"


def test_a_wake_trace_given_before_half_past_ten_cancels_the_reminder(make_bot, store):
    bot = make_bot(proactive_active_start=dtime(0, 0))
    notebook = _connected_notebook(bot)
    notebook.wake_reminder_due.return_value = False
    store.proactive.enqueue_job("trace-reveil:1", "trace_reveil", "", 0)

    bot._respond_proactive(ProactiveJob(
        psid="4242", kind="trace_reveil", prompt="", detail="", job_id="trace-reveil:1"))

    bot._client.send_button.assert_not_called()
    bot._client.send_burst.assert_not_called()
    assert store.proactive.jobs()[0]["status"] == "done"


def test_the_wake_reminder_follows_the_notebook_wake_ask():
    from redline.service.notebook import NotebookService

    service = NotebookService.__new__(NotebookService)
    board = MagicMock()
    service.board = lambda: board
    board.asks.return_value = [{"id": "wake:2026-10-09", "type": "wake"}]
    assert service.wake_reminder_due() is True
    board.asks.return_value = [{"id": "cell:x:rpe", "type": "cells"}]
    assert service.wake_reminder_due() is False
