"""Le carnet classe les artefacts sans lire leur prose et relit le plan avant action."""

import datetime as dt
import json
from unittest.mock import MagicMock

import pytest
from conftest import ENGINE_ROOT

from redline import clock
from redline.delivery import EnvelopeBuilder
from redline.notebook import NotebookService, period_day, review_period
from redline.outbox import Outbox
from redline.plans import PlanStatus
from scripts.cards import archive


def test_periods_belong_to_the_job_even_when_it_retries_next_month():
    assert review_period('proactive:weekly:2026-W39') == ('weekly', '2026-W39')
    assert review_period('proactive:monthly:2027-01') == ('monthly', '2026-12')
    assert period_day('weekly', '2026-W39', dt.date(2030, 1, 1)) == dt.date(2026, 9, 27)
    assert review_period('reactive:weekly:2026-W39') == ('other', '')
    with pytest.raises(ValueError):
        period_day('weekly', '2026-W99', dt.date.today())


def test_capture_freezes_image_and_message_before_outbox_pruning(tmp_path):
    outbox = Outbox(tmp_path)
    image = outbox.target('week.png')
    image.write_bytes(b'original')
    archive.write(image, kind='weekly', title='W39', period='2026-W39')
    store = MagicMock()
    store.source_entries.return_value = []
    notebook = NotebookService(store, MagicMock(), MagicMock(), ENGINE_ROOT, 'https://coach.test')
    envelope = EnvelopeBuilder('4242', 'proactive:weekly:2026-W39').text('Le bilan exact').image(outbox, image).build()
    notebook.capture(envelope)
    saved = store.save_many.call_args.args[2][0]
    outbox.archive(image)
    assert saved['image'] == b'original'
    assert saved['body'] == 'Le bilan exact'
    assert saved['period'] == '2026-W39'
    assert saved['occurred_on'] == dt.date(2026, 9, 27)
    assert not image.exists()
    store.source_entries.return_value = ['id-existant']
    assert notebook.capture(envelope) == ['id-existant']


def test_stale_metadata_cannot_label_a_replaced_image(tmp_path):
    image = tmp_path / 'week.png'
    image.write_bytes(b'old')
    archive.write(image, kind='weekly', title='W39', period='2026-W39')
    image.write_bytes(b'new')
    assert archive.read(image) == {}


def test_current_never_launches_a_stale_or_held_plan(monkeypatch):
    repo, state, store = MagicMock(), MagicMock(), MagicMock()
    service = NotebookService(store, repo, state, ENGINE_ROOT, 'https://coach.test')
    repo.validate_weekly_plan.return_value = PlanStatus.STALE
    assert service.launch('4242', 'old') is None
    state.issue_strength_link.assert_not_called()
    monkeypatch.setattr(clock, 'today', lambda: dt.date(2026, 10, 2))
    repo.validate_weekly_plan.return_value = PlanStatus.AWAITING
    repo.weekly_plan_context.return_value = json.dumps({'revision': 1, 'sessions': [{
        'id': 'a', 'date': '2026-10-02', 'kind': 'strength', 'workout': 'renfo-a',
        'status': 'planned', 'strength': {}}], 'gates': [{
            'id':'hold', 'due':'2026-10-02', 'when':'avant', 'holds':['a'],
            'question':'Comment va la blessure ?', 'on_fail':'Repos'}]})
    assert service.current()['status'] == 'blocked'
    assert service.launch('4242', 'old') is None


def test_launch_checks_the_exact_version_shown_to_the_user(monkeypatch):
    service = NotebookService(MagicMock(), MagicMock(), MagicMock(), ENGINE_ROOT, 'https://coach.test')
    monkeypatch.setattr(service, 'current', lambda: {'status':'ready', 'signature':'new', 'session':{'sid':'a'}})
    assert service.launch('4242', 'old') is None
    service.state.issue_strength_link.assert_not_called()


def test_the_shell_keeps_itself_and_launches_on_the_theme_background(make_config):
    """Le service worker et les écrans de lancement sont publics et sans donnée : le
    worker ne garde que la coquille, l'écran de lancement n'est qu'un fond uni."""
    from conftest import RecordingBot

    from redline import page
    from redline.web import create_app

    service = NotebookService(MagicMock(), MagicMock(), MagicMock(), ENGINE_ROOT, 'https://coach.test')
    client = create_app(make_config(), RecordingBot(), MagicMock(), notebook=service).test_client()
    worker = client.get('/carnet-sw.js')
    assert worker.status_code == 200 and worker.mimetype == 'text/javascript'
    assert b"url.pathname !== '/carnet'" in worker.data
    image = client.get('/carnet-launch/1179x2556-dark.png')
    assert image.status_code == 200 and image.data.startswith(b'\x89PNG')
    assert image.headers['Cache-Control'] == 'public, max-age=604800'
    assert client.get('/carnet-launch/100x100-dark.png').status_code == 404
    assert client.get('/carnet-launch/1179x2556-blue.png').status_code == 404
    document = page.notebook_document(ENGINE_ROOT / 'scripts' / 'cards')
    assert ('(device-width: 393px) and (device-height: 852px) and (-webkit-device-pixel-ratio: 3) '
            'and (orientation: portrait) and (prefers-color-scheme: dark)" '
            'href="/carnet-launch/1179x2556-dark.png"') in document
