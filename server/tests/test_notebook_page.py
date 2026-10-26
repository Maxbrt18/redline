"""Navigation mobile, confidentialité du DOM et erreurs réseau, dans Chromium."""

import json
import time

import pytest

pytest.importorskip('playwright')
from playwright.sync_api import sync_playwright

from redline.page import document as session_document
from redline.page import notebook_document
from scripts.cards import render
from scripts.model.dose import Dose

BASE = 'https://redline.test'
ENTRY = {'id':'one', 'kind':'monthly', 'title':'Monthly · 2026-09', 'period':'2026-09',
         'occurred_on':'2026-09-01', 'origin':'delivery', 'has_image':True}


def ef(identifier, date, status='planned', fact=None):
    return {'id': identifier, 'date': date, 'title': 'EF, permis accompagné', 'kind': 'run',
            'workout': 'EF', 'status': status, 'start_time': None, 'location': None,
            'note': 'Tu choisis la durée.', 'planned': ['≤55 min', 'RPE 2-3', 'Z1-Z2, 130-157 bpm'],
            'fact': fact, 'entry_id': None, 'transition': None,
            'targets': {'duration_cap_min': 55, 'rpe_min': 2, 'rpe_max': 3, 'hr_cap_bpm': 157}}


RENFO = {'id': 'w41-ven-renfo', 'date': '2026-10-09', 'title': 'Renfo, séance unique',
         'kind': 'strength', 'workout': 'renfo-a', 'status': 'planned', 'start_time': None,
         'location': None, 'note': None, 'planned': [], 'fact': None, 'entry_id': None,
         'transition': None, 'targets': {}}
DONE = ef('w41-mar-ef', '2026-10-06', 'done', {
    'km': 8.35, 'duration_min': 48, 'hr_avg': 162, 'rpe': 3, 'pain': 0.5,
    'enjoyment': 8, 'started_at': '2026-10-06T07:10:00+02:00', 'over': ['hr_avg']})
TOTALS = {'km_done': 14.06, 'km_target': 41, 'runs_done': 2, 'runs_planned': 5, 'dplus': 27,
          'strength_done': 1, 'strength_planned': 2, 'load': 25}
STRIP = [{'date': f'2026-10-{day:02d}', 'letter': letter, 'abbr': 'EF', 'today': day == 9,
          'done_km': 8.35 if day == 6 else None, 'planned_km': 16 if day == 11 else None,
          'planned_label': '≤55′' if day == 9 else None, 'long': day == 11, 'rest': day == 5}
         for day, letter in zip(range(5, 12), 'LMMJVSD', strict=True)]
TODAY = {
    'date': '2026-10-09', 'week': '2026-W41', 'plan': {'status': 'valid', 'message': ''},
    'sessions': [ef('w41-ven-ef', '2026-10-09'), RENFO], 'extra': [],
    'tomorrow': {'date': '2026-10-10', 'plan': 'valid', 'sessions': [], 'next': []},
    'ahead': [],
    'strip': STRIP, 'totals': TOTALS,
    'asks': [{'id': 'cell:6b0f3c1e-1d1a-4a39-9e0d-5c1b0b5d2f10', 'type': 'cells',
              'date': '2026-10-07', 'kind': 'EF', 'km': 5.71, 'fields': ['pain', 'rpe']}],
    'pain': {'cap': 2.0, 'weeks': [{'week': '2026-W40', 'peak': 1.5}, {'week': '2026-W41', 'peak': 1.0}]},
    'race': {'name': 'TargetRace', 'date': '2026-11-28', 'days': 50, 'start_time': '23:00',
             'distance_km': 45},
    'strength': {'status': 'ready', 'signature': 'old', 'date': '2026-10-09', 'session': {
        'title': 'Renfo A', 'exercises': [{'name': 'Mollets', 'dose': {'text': '3 × 12'}}]}},
}
WEEK = {'week': '2026-W41', 'monday': '2026-10-05', 'current': '2026-W41',
        'plan': {'status': 'valid', 'message': '', 'revision': 3},
        'sessions': [DONE, ef('w41-ven-ef', '2026-10-09'), RENFO], 'extra': [],
        'totals': TOTALS, 'strip': STRIP, 'review': None, 'cards': [ENTRY]}
INJURY = {'cap': 2.0, 'start': '2026-09-28', 'today': '2026-10-09', 'week': '2026-W41',
          'points': [{'date': '2026-10-03', 'pain': 1.5, 'kind': 'SL', 'long': True},
                     {'date': '2026-10-06', 'pain': 0.5, 'kind': 'EF', 'long': False},
                     {'date': '2026-10-07', 'pain': 2.5, 'kind': 'EF', 'long': False}],
          'wakes': [{'date': '2026-10-08', 'pain': 0.0}],
          'weeks': [{'week': '2026-W40', 'peak': 1.5, 'n': 1, 'wake': None},
                    {'week': '2026-W41', 'peak': 2.5, 'n': 2, 'wake': 0.0}],
          'missing': ['2026-10-07'], 'scale': [{'value': 1, 'text': 'Pas gênante.'}]}
WEEKS = {'weeks': [{'week': f'2026-W{n}', 'monday': monday, 'km_done': km, 'km_target': target,
                    'review': None} for n, monday, km, target in (
    (40, '2026-09-28', 38.2, 40.0), (39, '2026-09-21', 35.0, 36.0), (38, '2026-09-14', 31.5, 34.0),
    (37, '2026-09-07', 29.0, 30.0), (36, '2026-08-31', 27.4, None))]}
RACE = {'race': TODAY['race'] | {'target': '<4 h 30'},
        'longest': {'km': 15.84, 'planned_km': 16, 'planned_on': '2026-10-11', 'weeks': [
            {'week': '2026-W40', 'km': 15.84, 'volume': 39.66, 'target': 40.0, 'open': False},
            {'week': '2026-W41', 'km': 8.35, 'volume': 14.06, 'target': 41.0, 'open': True}]},
        'milestones': [{'date': '2026-10-04', 'label': 'Accès vélo', 'kind': 'deadline', 'overdue': True},
                       {'date': '2026-10-14', 'label': 'Frontale ≥400 lm', 'kind': 'deadline', 'overdue': False},
                       {'date': '2026-11-28', 'label': 'Départ', 'kind': 'race', 'start_time': '23:00'}],
        'capabilities': [{'label': 'Injury', 'score': 3, 'scale': 5, 'number': 5,
                          'evaluated_on': '2026-10-04'}],
        'archives': [ENTRY]}


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as engine:
        instance = engine.chromium.launch(**render._launch_kwargs())
        yield instance
        instance.close()


def serve(tab, answers=None):
    tab.route(BASE + '/carnet', lambda route: route.fulfill(
        content_type='text/html', body=notebook_document(render.CARDS_DIR)))
    def respond(route):
        path = route.request.url.split('/carnet-api/')[1]
        if path == 'today':
            data = TODAY
        elif path.startswith('week/'):
            data = WEEK
        elif path == 'race':
            data = RACE
        elif path == 'weeks':
            data = WEEKS
        elif path == 'injury':
            data = INJURY
        elif path == 'entries':
            data = {'entries': [ENTRY], 'next_offset': None, 'latest': [ENTRY]}
        elif path == 'entries/one':
            data = {**ENTRY, 'body': '<img src=x onerror=alert(1)> Texte exact', 'image_hash': '',
                    'snapshot': None, 'report': None, 'related': []}
        elif path == 'launch':
            route.fulfill(status=409, content_type='application/json', body=json.dumps({'error': 'La séance a changé.'}))
            return
        elif path == 'answer':
            if answers is not None:
                answers.append(json.loads(route.request.post_data))
            data = {'status': 'ok', 'alert': True}
        else:
            data = {'status': 'ok'}
        route.fulfill(content_type='application/json', body=json.dumps(data))
    tab.route(BASE + '/carnet-api/**', respond)


@pytest.mark.parametrize('width', [320, 390, 760])
def test_today_week_and_course_fit_a_phone_and_keep_text_inert(browser, width):
    tab = browser.new_page(viewport={'width': width, 'height': 844})
    errors = []
    tab.on('pageerror', lambda error: errors.append(str(error)))
    serve(tab)
    tab.goto(BASE + '/carnet#t=' + 'x' * 43)
    tab.get_by_role('heading', name='EF, permis accompagné', exact=True).wait_for()
    assert 't=' not in tab.url
    assert tab.get_by_text('Vendredi 9 octobre · semaine 41').is_visible()
    assert tab.get_by_role('heading', name='Renfo, séance unique').is_visible()
    assert tab.get_by_text('Alerte FC de la montre sur 157').is_visible()
    assert tab.locator('.n-nav').bounding_box()['y'] > 700
    tab.get_by_role('button', name='1 exercice', exact=True).click()
    assert tab.get_by_text('3 × 12', exact=True).is_visible()
    tab.get_by_role('button', name='Séance guidée →').click()
    tab.get_by_text('La séance a changé.', exact=True).wait_for()
    assert tab.evaluate('document.documentElement.scrollWidth <= innerWidth')
    tab.get_by_role('button', name='Semaine', exact=True).click()
    tab.get_by_text('FC +5', exact=True).wait_for()
    assert not tab.locator('.n-rail .out').is_visible()
    tab.locator('.n-krow', has_text='FC +5').click()
    # Ouvert, le dépassement se lit sur la jauge : l'étiquette s'efface, la ligne de faits ne
    # répète ni la durée ni la FC (maquette G1, 2026-10-10).
    assert tab.locator('.n-rail .out').is_visible()
    assert not tab.get_by_text('FC +5', exact=True).is_visible()
    assert tab.locator('.n-kday.open .n-kmore p').first.inner_text() == '8,35 km · 5:45/km · douleur 0,5 · plaisir 8'
    assert '›' not in tab.locator('#content').inner_text()
    assert tab.get_by_text('EF + renfo', exact=True).count() == 1
    assert not tab.evaluate("document.body.classList.contains('n-sky')")
    assert tab.evaluate('document.documentElement.scrollWidth <= innerWidth')
    tab.get_by_role('button', name='Monthly · 2026-09').click()
    tab.get_by_role('heading', name='Le mot du coach').wait_for()
    assert tab.locator('.n-prose').inner_text() == '<img src=x onerror=alert(1)> Texte exact'
    assert tab.locator('#content img').count() == 0
    tab.get_by_role('button', name='Retour').click()
    tab.get_by_role('heading', name='Semaine 41').wait_for()
    tab.get_by_role('button', name='Course', exact=True).click()
    tab.get_by_role('heading', name='J-50').wait_for()
    # Le volume sur son objectif, la sortie longue dessous ; celle de la semaine en cours
    # est celle du programme tant qu'elle n'est pas courue (2026-10-10).
    assert tab.locator('.n-vchart .vl').all_text_contents() == ['39,7/40', '14,1/41']
    assert tab.locator('.n-vchart .slv').all_text_contents() == ['15,8', '16']
    assert tab.locator('.n-vchart .sls').all_text_contents() == ['prévue dim.']
    late = tab.get_by_text('en retard · 04/10')
    assert 'n-over' in late.get_attribute('class')
    assert late.evaluate('e => getComputedStyle(e).color') == tab.evaluate(
        "() => { const p = document.createElement('p'); p.style.color = 'var(--over)'; document.body.append(p);"
        " const color = getComputedStyle(p).color; p.remove(); return color; }")
    assert tab.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert not errors
    tab.close()


def test_an_answer_is_one_tap_and_says_when_the_coach_takes_over(browser):
    tab = browser.new_page(viewport={'width': 390, 'height': 844})
    answers = []
    serve(tab, answers)
    tab.goto(BASE + '/carnet')
    tab.get_by_role('heading', name='EF de mercredi 7 · 5,7 km').wait_for()
    tab.locator('.n-check').first.get_by_role('button', name='2,5', exact=True).click()
    tab.get_by_text('Le coach te répond dans Messenger').wait_for()
    assert answers == [{'ask': 'cell:6b0f3c1e-1d1a-4a39-9e0d-5c1b0b5d2f10:pain', 'value': 2.5}]
    tab.close()


def test_expired_access_clears_private_content(browser):
    tab = browser.new_page(has_touch=True)
    serve(tab)
    tab.goto(BASE + '/carnet')
    tab.get_by_role('heading', name='EF, permis accompagné', exact=True).wait_for()
    tab.route(BASE + '/carnet-api/today', lambda route: route.fulfill(
        status=401, content_type='application/json', body=json.dumps({'error':'Reconnecte-toi.'})))
    pull(tab)
    tab.get_by_role('heading', name='Ton carnet privé').wait_for()
    assert tab.locator('#content').inner_text() == ''
    assert not tab.locator('#connected').is_visible()
    assert tab.evaluate("localStorage.getItem('carnet-data')") is None
    tab.close()


def test_a_first_connection_waits_for_its_cookie_before_asking_for_figures(browser):
    """Sans cookie, tout appel avant l'échange du lien revient en 401 et verrouillait le
    carnet alors que la connexion réussissait juste après (2026-10-26)."""
    tab = browser.new_page(viewport={'width': 390, 'height': 844})
    serve(tab)
    cookie = {'set': False}
    order = []

    def gate(route):
        path = route.request.url.split('/carnet-api/')[1]
        order.append(path.split('?')[0])
        if path == 'connect':
            time.sleep(0.3)
            cookie['set'] = True
            route.fulfill(content_type='application/json', body=json.dumps({'status': 'ok'}))
        elif not cookie['set']:
            route.fulfill(status=401, content_type='application/json',
                          body=json.dumps({'error': 'Ouvre un nouveau lien avec /carnet dans Messenger.'}))
        else:
            route.fallback()
    tab.route(BASE + '/carnet-api/**', gate)
    tab.goto(BASE + '/carnet#t=' + 'x' * 43)
    tab.get_by_role('heading', name='EF, permis accompagné', exact=True).wait_for()
    assert tab.locator('#connected').is_visible() and not tab.locator('#locked').is_visible()
    assert order[0] == 'connect'
    tab.close()


def test_an_expired_link_says_so_and_nothing_else_leaves(browser):
    tab = browser.new_page(viewport={'width': 390, 'height': 844})
    serve(tab)
    calls = []

    def expired(route):
        path = route.request.url.split('/carnet-api/')[1]
        calls.append(path)
        route.fulfill(status=401, content_type='application/json',
                      body=json.dumps({'error': 'Ce lien a expiré ou a déjà servi. Tape /carnet dans Messenger.'}))
    tab.route(BASE + '/carnet-api/**', expired)
    tab.goto(BASE + '/carnet#t=' + 'x' * 43)
    tab.get_by_text('Ce lien a expiré ou a déjà servi.', exact=False).wait_for()
    tab.wait_for_timeout(300)
    assert calls == ['connect']
    assert tab.get_by_role('heading', name='Ton carnet privé').is_visible()
    tab.close()


def test_messenger_browser_keeps_link_for_safari(browser):
    context = browser.new_context(user_agent='Mozilla/5.0 (iPhone) Mobile/15E148 [FBAN/MessengerForiOS;FBAV/480.0]')
    tab = context.new_page()
    calls = []
    tab.on('request', lambda request: calls.append(request.url) if '/carnet-api/' in request.url else None)
    serve(tab)
    tab.goto(BASE + '/carnet#t=' + 'x' * 43)
    tab.get_by_role('heading', name='Ouvre ton carnet dans Safari').wait_for()
    assert '#t=' + 'x' * 43 in tab.url
    assert tab.get_by_role('link', name='Ouvrir dans Safari').get_attribute('href') == \
        'x-safari-https://redline.test/carnet#t=' + 'x' * 43
    assert not calls
    tab.get_by_role('button', name='Me connecter ici quand même').click()
    tab.get_by_role('heading', name='EF, permis accompagné', exact=True).wait_for()
    assert 't=' not in tab.url
    context.close()


def pull(tab, distance=260):
    """Tire la page vers le bas depuis le haut, au doigt."""
    tab.evaluate("""distance => {
        const touch = y => new Touch({identifier: 1, target: document.body, clientX: 180, clientY: y});
        const fire = (type, y, list) => document.body.dispatchEvent(new TouchEvent(type, {
            bubbles: true, cancelable: true, touches: list ? [touch(y)] : [], changedTouches: [touch(y)]}));
        fire('touchstart', 100, true);
        for (let y = 120; y <= 100 + distance; y += 20) fire('touchmove', y, true);
        fire('touchend', 100 + distance, false);
    }""", distance)


def test_settings_live_in_a_sheet_and_pulling_refreshes(browser):
    """Plus de pied de page : thème, installation, appareil et fraîcheur dans une feuille
    ouverte depuis le haut ; tirer la page recalcule, un petit geste ne fait rien."""
    context = browser.new_context(viewport={'width': 390, 'height': 844}, has_touch=True, user_agent=(
        'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Version/18.0 Mobile/15E148 Safari/604.1'))
    tab = context.new_page()
    serve(tab)
    tab.goto(BASE + '/carnet')
    tab.get_by_role('heading', name='EF, permis accompagné', exact=True).wait_for()
    assert tab.locator('[aria-label="Options"]').count() == 0
    assert tab.locator('.n-foot').count() == 0
    assert not tab.get_by_text('Chiffres à jour à').is_visible()
    tab.get_by_role('button', name='Réglages').click()
    tab.get_by_text('Chiffres à jour à').wait_for()
    assert tab.get_by_role('button', name='Sur l’écran d’accueil').is_visible()
    tab.keyboard.press('Escape')
    tab.locator('#sheet').wait_for(state='hidden')
    requested = []
    tab.on('request', lambda request: requested.append(request.url.split('/carnet-api/')[-1]))
    pull(tab, 60)
    tab.wait_for_timeout(150)
    assert 'today' not in requested
    pull(tab)
    tab.wait_for_timeout(300)
    assert 'today' in requested
    tab.get_by_role('heading', name='Sur ton écran d’accueil').wait_for()
    assert tab.locator('#content').get_by_text('Dans Safari : Partager').is_visible()
    tab.get_by_role('button', name='Plus tard').click()
    assert tab.get_by_role('heading', name='Sur ton écran d’accueil').count() == 0
    tab.reload()
    tab.get_by_role('heading', name='EF, permis accompagné', exact=True).wait_for()
    assert tab.get_by_role('heading', name='Sur ton écran d’accueil').count() == 0
    tab.get_by_role('button', name='Réglages').click()
    tab.get_by_role('button', name='Déconnecter cet appareil').click()
    assert tab.get_by_text('/carnet-revoquer').is_visible()
    tab.get_by_role('button', name='Annuler').click()
    tab.get_by_role('button', name='Déconnecter cet appareil').click()
    tab.get_by_role('button', name='Déconnecter', exact=True).click()
    tab.get_by_role('heading', name='Ton carnet privé').wait_for()
    assert tab.locator('#content').inner_text() == ''
    context.close()


def test_tabs_are_prepared_while_today_is_read_and_shown_without_waiting(browser):
    """Semaine et Course se chargent derrière Aujourd'hui ; un onglet déjà vu s'affiche
    depuis la mémoire même si le réseau est tombé entre-temps."""
    context = browser.new_context(viewport={'width': 390, 'height': 844})
    tab = context.new_page()
    requested = []
    tab.on('request', lambda request: requested.append(request.url.split('/carnet-api/')[-1]))
    serve(tab)
    tab.goto(BASE + '/carnet')
    tab.get_by_role('heading', name='EF, permis accompagné', exact=True).wait_for()
    tab.wait_for_timeout(200)
    assert 'week/2026-W41' in requested and 'race' in requested
    tab.route(BASE + '/carnet-api/**', lambda route: route.abort())
    tab.get_by_role('button', name='Semaine', exact=True).click()
    tab.get_by_role('heading', name='Semaine 41').wait_for(timeout=1000)
    tab.get_by_role('button', name='Course', exact=True).click()
    tab.get_by_role('heading', name='J-50').wait_for(timeout=1000)
    context.close()


def test_the_morning_comes_first_and_a_high_heart_rate_asks_why(browser):
    """Avant midi, le point du matin passe devant la séance ; une FC haute pose sa question."""
    morning = {**TODAY, 'asks': [
        {'id': 'wake:2026-10-09', 'type': 'wake', 'date': '2026-10-09'},
        {'id': 'daily:2026-10-09', 'type': 'daily', 'date': '2026-10-09', 'fields': ['sleep', 'life_load']},
        {'id': 'hr:2026-10-07', 'type': 'hr', 'date': '2026-10-07', 'hr': 157, 'reference': 151,
         'n': 5, 'pace_s': 406}]}
    tab = browser.new_page(viewport={'width': 390, 'height': 844})
    answers = []
    serve(tab, answers)
    tab.route(BASE + '/carnet-api/today', lambda route: route.fulfill(
        content_type='application/json', body=json.dumps(morning)))
    tab.goto(BASE + '/carnet')
    tab.get_by_role('heading', name='Comment tu te réveilles ?').wait_for()
    first = tab.locator('#content h1, #content h2').first.inner_text()
    assert first == 'Comment tu te réveilles\u202f?'
    assert tab.locator('.n-step.now').count() == 1
    assert tab.locator('.n-step.now h2').inner_text() == first
    assert tab.get_by_text('157 bpm à 6:46/km, contre 151').is_visible()
    tab.get_by_role('button', name='Mauvais', exact=True).click()
    tab.get_by_text('Noté.').wait_for()
    tab.get_by_role('button', name='Rien de spécial').click()
    tab.wait_for_timeout(200)
    assert answers == [{'ask': 'daily:2026-10-09:sleep', 'value': 'mauvaise'},
                       {'ask': 'hr:2026-10-07', 'value': 'rien'}]
    tab.close()


def test_the_theme_follows_the_phone_unless_pinned_on_this_device(browser):
    """Auto par défaut ; Clair ou Sombre s'épinglent sur l'appareil et survivent au rechargement."""
    context = browser.new_context(viewport={'width': 390, 'height': 844}, color_scheme='light')
    tab = context.new_page()
    serve(tab)
    tab.goto(BASE + '/carnet')
    tab.get_by_role('heading', name='EF, permis accompagné', exact=True).wait_for()
    background = "getComputedStyle(document.body).backgroundColor"
    tab.get_by_role('button', name='Réglages').click()
    assert tab.get_by_role('button', name='Auto').get_attribute('aria-pressed') == 'true'
    light = tab.evaluate(background)
    tab.get_by_role('button', name='Sombre').click()
    assert tab.evaluate("document.documentElement.dataset.theme") == 'dark'
    assert tab.evaluate(background) != light
    # Sous la feuille, la bande d'état prend le voile ; refermée, le fond du thème.
    assert tab.evaluate("[...document.querySelectorAll('meta[name=theme-color]')].every(m => m.content === '#0e0b0a')")
    tab.keyboard.press('Escape')
    assert tab.evaluate("[...document.querySelectorAll('meta[name=theme-color]')].every(m => m.content === '#110e0d')")
    tab.reload()
    tab.get_by_role('heading', name='EF, permis accompagné', exact=True).wait_for()
    assert tab.evaluate("document.documentElement.dataset.theme") == 'dark'
    tab.get_by_role('button', name='Réglages').click()
    assert tab.get_by_role('button', name='Sombre').get_attribute('aria-pressed') == 'true'
    tab.get_by_role('button', name='Auto').click()
    assert tab.evaluate("document.documentElement.dataset.theme") is None
    assert tab.evaluate(background) == light
    context.close()


def test_the_weather_answers_what_to_wear_and_whether_night_falls_during_the_run(browser):
    """Météo B (2026-10-08) : le ciel et le ressenti au départ, puis la nuit dite par
    rapport à la séance ; la barre d'état garde le fond du thème."""
    tab = browser.new_page(viewport={'width': 390, 'height': 844})
    serve(tab)
    start = {**TODAY, 'asks': [], 'sessions': [{**TODAY['sessions'][0], 'start_time': '17:00'}, RENFO]}
    sky = {**SKY, 'sunrise': '07:55', 'hours': [
        {'hour': 17, 'temp': 16, 'feels': 15, 'rain': 0, 'wind': 12, 'code': 2, 'day': True},
        {'hour': 18, 'temp': 15, 'feels': 13, 'rain': 10, 'wind': 14, 'code': 3, 'day': True}]}
    tab.route(BASE + '/carnet-api/today', lambda route: route.fulfill(
        content_type='application/json', body=json.dumps(start)))
    tab.route(BASE + '/carnet-api/weather*', lambda route: route.fulfill(
        content_type='application/json', body=json.dumps(sky)))
    tab.goto(BASE + '/carnet')
    tab.get_by_text('Nuit à 19 h 05 : tu rentres plus d’une heure avant.').wait_for()
    assert tab.get_by_text('Ressenti 15° · éclaircies, sec').is_visible()
    assert tab.get_by_text('Vent 12 km/h · 15° à 18 h').is_visible()
    assert tab.locator('.n-step.now .n-skick').inner_text() == 'À FAIRE · 17\u00a0H'
    assert tab.evaluate("[...document.querySelectorAll('meta[name=theme-color]')].map(m => m.content)") == ['#f7f5f2']
    tab.close()


SKY = {'place': 'Paris', 'date': '2026-10-09', 'sunrise': '07:55', 'sunset': '19:05', 'hours': [
    {'hour': hour, 'temp': 16, 'feels': 15, 'rain': 0, 'wind': 12} for hour in (17, 18, 19)]}


def test_returning_to_the_app_keeps_the_page_still(browser):
    """Un retour rapide ne recharge rien ; un retour tardif recalcule sans redessiner ce qui
    n'a pas changé, sans redemander la météo, et la page reste où on l'avait laissée."""
    tab = browser.new_page(viewport={'width': 390, 'height': 600})
    requested = []
    tab.on('request', lambda request: requested.append(request.url.split('/carnet-api/')[-1].split('?')[0]))
    tab.clock.install(time='2026-10-09T10:00:00')
    serve(tab)
    tab.route(BASE + '/carnet-api/weather*', lambda route: route.fulfill(
        content_type='application/json', body=json.dumps(SKY)))
    tab.goto(BASE + '/carnet')
    tab.get_by_text('Nuit à 19 h 05.').wait_for()
    tab.evaluate("window.__hero = document.querySelector('#content h2'); scrollTo(0, 240)")
    back = "document.dispatchEvent(new Event('visibilitychange'))"
    requested.clear()
    tab.evaluate(back)
    tab.wait_for_timeout(150)
    assert requested == []
    tab.clock.fast_forward('02:00')
    tab.evaluate(back)
    tab.wait_for_timeout(300)
    assert 'today' in requested and 'weather' not in requested
    assert tab.evaluate("document.querySelector('#content h2') === window.__hero")
    assert tab.evaluate('scrollY') == 240
    tab.close()


def test_each_tab_keeps_its_place_and_messages_never_push_the_page(browser):
    tab = browser.new_page(viewport={'width': 390, 'height': 600})
    serve(tab)
    tab.goto(BASE + '/carnet')
    tab.get_by_role('heading', name='EF de mercredi 7 · 5,7 km').wait_for()
    tab.evaluate('scrollTo(0, 300)')
    tab.get_by_role('button', name='Semaine', exact=True).click()
    tab.get_by_role('heading', name='Semaine 41').wait_for()
    assert tab.evaluate('scrollY') == 0
    tab.get_by_role('button', name='Aujourd’hui', exact=True).click()
    assert tab.evaluate('scrollY') == 300
    tab.get_by_role('button', name='Aujourd’hui', exact=True).click()
    tab.wait_for_function('scrollY === 0')
    top = "document.querySelector('#content').getBoundingClientRect().top + scrollY"
    before = tab.evaluate(top)
    tab.locator('.n-check').first.get_by_role('button', name='1', exact=True).click()
    tab.get_by_text('Noté. Le coach te répond dans Messenger.').wait_for()
    assert tab.evaluate(top) == before
    assert tab.evaluate("getComputedStyle(document.getElementById('toast')).position") == 'fixed'
    tab.close()


def test_a_tab_never_seen_shows_its_outline_until_its_numbers_arrive(browser):
    tab = browser.new_page(viewport={'width': 390, 'height': 844})
    serve(tab)
    pending = []
    tab.route(BASE + '/carnet-api/race', lambda route: pending.append(route))
    tab.goto(BASE + '/carnet')
    tab.get_by_role('heading', name='EF, permis accompagné', exact=True).wait_for()
    tab.get_by_role('button', name='Course', exact=True).click()
    assert tab.locator('#content .n-skel').is_visible()
    assert tab.get_by_role('button', name='Réglages').is_visible()
    tab.close()


def title(tab):
    return tab.locator('#lt-title').inner_text()


SWIPE_BACK = """async ([fast]) => {
    const wait = ms => new Promise(r => setTimeout(r, ms));
    const send = (type, x) => {
        const touch = new Touch({identifier: 1, target: document.body, clientX: x, clientY: 450});
        window.dispatchEvent(new TouchEvent(type, {touches: type === 'touchend' ? [] : [touch],
            changedTouches: [touch], cancelable: true, bubbles: true}));
    };
    send('touchstart', 120);
    for (const x of [140, 200, 260]) { await wait(16); send('touchmove', x); }
    await wait(fast ? 10 : 200); send('touchend', 260);
}"""


def swipe_back(tab, fast=True):
    """Le glissement vers la droite depuis le milieu de l'écran, rapide (l'écran part) ou lâché à l'arrêt."""
    tab.evaluate(SWIPE_BACK, [fast])


def test_navigation_never_loses_the_athlete(browser):
    """Carte de navigation (2026-10-08) : trois racines, des écrans poussés qui reculent
    d'un cran, une pile gardée par onglet. Le glissement depuis le bord est celui du
    carnet, pas l'historique du navigateur, qui emportait la barre d'onglets (2026-10-09)."""
    tab = browser.new_page(viewport={'width': 393, 'height': 852})
    serve(tab)
    tab.goto(BASE + '/carnet')
    tab.get_by_role('heading', name='EF, permis accompagné', exact=True).wait_for()
    assert title(tab) == 'Aujourd’hui' and not tab.locator('#back').is_visible()
    entries = tab.evaluate('history.length')
    # Semaine s'ouvre sur la semaine en cours ; une semaine passée se pousse.
    tab.get_by_role('button', name='Semaine', exact=True).click()
    tab.get_by_role('heading', name='Semaine 41').wait_for()
    assert tab.locator('.n-wk').count() == 0
    tab.get_by_role('button', name='Semaine 40').click()
    tab.wait_for_function("document.getElementById('lt-title').textContent === 'Semaine 40'")
    assert tab.locator('#back').is_visible() and not tab.locator('#settings').is_visible()
    assert 'terminée' in tab.locator('#lt-sub').inner_text()
    tab.get_by_role('button', name='Retour').click()
    tab.wait_for_function("document.getElementById('lt-title').textContent === 'Semaine 41'")
    # Toutes les semaines, puis une semaine, puis le geste retour du navigateur.
    tab.get_by_role('button', name='Toutes les semaines').click()
    tab.wait_for_function("document.getElementById('lt-title').textContent === 'Toutes les semaines'")
    tab.get_by_role('button', name='Semaine 36').click()
    tab.wait_for_function("document.getElementById('lt-title').textContent === 'Semaine 36'")
    assert tab.evaluate('history.length') == entries
    swipe_back(tab, fast=False)
    tab.wait_for_timeout(500)
    assert title(tab) == 'Semaine 36' and tab.locator('.n-move').count() == 0
    swipe_back(tab)
    tab.wait_for_function("document.getElementById('lt-title').textContent === 'Toutes les semaines'")
    tab.locator('.n-move').wait_for(state='detached')
    assert tab.locator('.n-nav').count() == 1
    # Course : archives, puis une carte ; le retour remonte d'un cran, pas à la racine.
    tab.get_by_role('button', name='Course', exact=True).click()
    tab.get_by_role('heading', name='J-50').wait_for()
    tab.get_by_role('button', name='Toutes les archives').click()
    tab.wait_for_function("document.getElementById('lt-title').textContent === 'Archives'")
    tab.get_by_role('button', name='Monthly · 2026-09').click()
    tab.get_by_role('heading', name='Le mot du coach').wait_for()
    assert title(tab) == 'Septembre 2026' and '#entry=one' in tab.url
    tab.get_by_role('button', name='Retour').click()
    tab.wait_for_function("document.getElementById('lt-title').textContent === 'Archives'")
    tab.get_by_role('button', name='Monthly · 2026-09').click()
    tab.get_by_role('heading', name='Le mot du coach').wait_for()
    # Chaque onglet garde sa pile ; retoucher l'onglet actif ramène à sa racine.
    tab.get_by_role('button', name='Semaine', exact=True).click()
    tab.wait_for_function("document.getElementById('lt-title').textContent === 'Toutes les semaines'")
    tab.get_by_role('button', name='Course', exact=True).click()
    tab.wait_for_function("document.getElementById('lt-title').textContent === 'Septembre 2026'")
    tab.get_by_role('button', name='Course', exact=True).click()
    tab.get_by_role('heading', name='J-50').wait_for()
    assert title(tab) == 'TargetRace' and not tab.locator('#back').is_visible()
    tab.close()


def test_a_card_from_messenger_opens_over_today_and_goes_back_to_it(browser):
    tab = browser.new_page(viewport={'width': 393, 'height': 852})
    serve(tab)
    tab.goto(BASE + '/carnet#entry=one')
    tab.get_by_role('heading', name='Le mot du coach').wait_for()
    assert title(tab) == 'Septembre 2026'
    tab.get_by_role('button', name='Retour').click()
    tab.get_by_role('heading', name='EF, permis accompagné', exact=True).wait_for()
    assert title(tab) == 'Aujourd’hui' and tab.url.endswith('/carnet')
    tab.close()


SESSION = {'sid': 'w41-ven-renfo-2026-10-09', 'session': 'a', 'title': 'Renfo A', 'date': '2026-10-09',
           'duration_min': 25, 'rule': 'Une règle.', 'expires_at': int(time.time()) + 3600,
           'exercises': [{'name': name, 'target': '', 'dose': Dose.read({'measure': 'reps', 'sets': 1, 'reps': 8}).payload()}
                         for name in ('Step-up', 'Mollet', 'Pont')]}


def guided(tab, today=None):
    """Le carnet avec un renfo prêt : « Séance guidée » obtient un lien, la séance le résout."""
    serve(tab)
    data = today or {**TODAY, 'strength': {**TODAY['strength'], 'session': SESSION}}
    tab.route(BASE + '/carnet-api/today', lambda route: route.fulfill(content_type='application/json', body=json.dumps(data)))
    tab.route(BASE + '/carnet-api/launch', lambda route: route.fulfill(
        content_type='application/json', body=json.dumps({'url': '/s#t=' + 'A' * 32})))
    tab.route('**/strength-session', lambda route: route.fulfill(
        content_type='application/json', body=json.dumps({'status': 'ok', 'session': SESSION})))
    tab.goto(BASE + '/carnet')
    tab.get_by_role('heading', name='EF, permis accompagné', exact=True).wait_for()


def in_session(tab):
    return tab.evaluate("document.documentElement.classList.contains('n-seance') && !document.getElementById('seance').hidden")


def test_the_guided_session_opens_over_the_notebook_and_closes_back_to_it(browser):
    """Le renfo est un écran du carnet (maquette validée le 2026-10-09) : il s'ouvre en
    plein écran sans quitter /carnet ni rien empiler dans l'historique, sans onglets,
    avec ✕ et le son dans la barre ; non commencé, ✕ le referme sans rien demander."""
    tab = browser.new_page(viewport={'width': 393, 'height': 852})
    guided(tab)
    entries = tab.evaluate('history.length')
    tab.get_by_role('button', name='Séance guidée →').click()
    tab.wait_for_selector('.p-step--live')
    assert in_session(tab) and tab.url == BASE + '/carnet' and tab.evaluate('history.length') == entries
    assert tab.locator('#title').inner_text() == 'Renfo A'
    assert tab.locator('#meta').inner_text() == 'Vendredi 9 octobre · ~25 min'
    assert not tab.locator('#connected > .n-nav').is_visible() and not tab.locator('#settings').is_visible()
    assert tab.get_by_role('button', name='Fermer la séance').is_visible() and tab.get_by_role('button', name='Son').is_visible()
    tab.get_by_role('button', name='Fermer la séance').click()
    tab.wait_for_function("!document.documentElement.classList.contains('n-seance')")
    assert tab.locator('#leave-sheet').is_hidden() and tab.locator('#connected > .n-nav').is_visible()
    assert title(tab) == 'Aujourd’hui' and tab.evaluate('history.length') == entries
    tab.close()


def test_leaving_a_started_session_asks_and_loses_nothing(browser):
    """Commencée, la séance demande confirmation avant de se fermer, en disant que rien
    n'est perdu ; Aujourd'hui dit ensuite où elle en est, et la reprise y ramène."""
    tab = browser.new_page(viewport={'width': 393, 'height': 852})
    guided(tab)
    tab.get_by_role('button', name='Séance guidée →').click()
    tab.wait_for_selector('.p-step--live')
    tab.locator('.p-ctrl .p-btn--wide').first.click()    # Step-up fait : Mollet en cours
    tab.wait_for_function("document.querySelector('.p-step--live .p-step__name').textContent === 'Mollet'")
    tab.get_by_role('button', name='Fermer la séance').click()
    sheet = tab.locator('#leave-sheet')
    sheet.wait_for()
    assert 'Rien n’est perdu' in sheet.inner_text()
    tab.get_by_role('button', name='Continuer la séance').click()
    tab.wait_for_function("document.getElementById('leave-sheet').hidden")
    assert in_session(tab)
    tab.get_by_role('button', name='Fermer la séance').click()
    tab.get_by_role('button', name='Quitter', exact=True).click()
    tab.wait_for_function("!document.documentElement.classList.contains('n-seance')")
    step = tab.locator('.n-step', has_text='Renfo')
    assert 'EN COURS · COMMENCÉE À' in step.inner_text().upper() and '1 exercice sur 3' in step.inner_text()
    step.get_by_role('button', name='Reprendre la séance →').click()
    tab.wait_for_function("document.querySelector('.p-step--live .p-step__name').textContent === 'Mollet'")
    tab.close()


def test_no_gesture_leaves_the_session_by_accident(browser):
    """Entre deux séries, un glissement vers la droite ne fait pas sortir de la séance."""
    tab = browser.new_page(viewport={'width': 393, 'height': 852})
    guided(tab)
    tab.get_by_role('button', name='Séance guidée →').click()
    tab.wait_for_selector('.p-step--live')
    swipe_back(tab)
    tab.wait_for_timeout(500)
    assert in_session(tab) and tab.locator('.n-move').count() == 0
    tab.close()


def test_a_reloaded_page_comes_back_to_the_session(browser):
    """iOS recharge parfois une PWA restée en arrière-plan : la séance ouverte revient."""
    tab = browser.new_page(viewport={'width': 393, 'height': 852})
    guided(tab)
    tab.get_by_role('button', name='Séance guidée →').click()
    tab.wait_for_selector('.p-step--live')
    tab.reload()
    tab.wait_for_selector('.p-step--live')
    assert in_session(tab) and tab.locator('#title').inner_text() == 'Renfo A'
    tab.close()


def test_a_messenger_link_opens_the_session_alone_then_the_notebook(browser):
    """/s#t=… ouvre la même app directement sur la séance, sans connexion du carnet ;
    ✕ mène au carnet, en remplaçant la page."""
    tab = browser.new_page(viewport={'width': 393, 'height': 852})
    serve(tab)
    tab.route(BASE + '/s', lambda route: route.fulfill(content_type='text/html', body=session_document(render.CARDS_DIR)))
    tab.route('**/strength-session', lambda route: route.fulfill(
        content_type='application/json', body=json.dumps({'status': 'ok', 'session': SESSION})))
    tab.goto(BASE + '/s#t=' + 'A' * 32)
    tab.wait_for_selector('.p-step--live')
    assert in_session(tab) and not tab.locator('#notice').is_visible()
    entries = tab.evaluate('history.length')
    tab.get_by_role('button', name='Fermer la séance').click()
    tab.wait_for_url(BASE + '/carnet')
    tab.get_by_role('heading', name='EF, permis accompagné', exact=True).wait_for()
    assert tab.evaluate('history.length') == entries
    tab.close()


def test_the_notebook_neither_zooms_nor_selects_text(browser):
    tab = browser.new_page(viewport={'width': 393, 'height': 852})
    serve(tab)
    tab.goto(BASE + '/carnet')
    tab.get_by_role('heading', name='EF, permis accompagné', exact=True).wait_for()
    viewport = tab.locator('meta[name=viewport]').get_attribute('content')
    assert 'maximum-scale=1' in viewport and 'user-scalable=no' in viewport
    assert tab.evaluate("getComputedStyle(document.documentElement).touchAction") == 'pan-x pan-y'
    assert tab.evaluate("getComputedStyle(document.body).userSelect") == 'none'
    tab.close()


def test_the_top_bar_folds_the_title_when_it_scrolls_under(browser):
    tab = browser.new_page(viewport={'width': 393, 'height': 600})
    serve(tab)
    tab.goto(BASE + '/carnet')
    tab.get_by_role('heading', name='EF, permis accompagné', exact=True).wait_for()
    assert tab.evaluate("document.getElementById('bar').className") == 'n-topbar'
    tab.evaluate('scrollTo(0, 400)')
    tab.wait_for_function("document.getElementById('bar').classList.contains('titled')")
    assert tab.locator('#bar-title').inner_text() == 'Aujourd’hui'
    tab.close()


def replying(data):
    """Une route qui rend ces données (Playwright passe la requête à qui prend deux arguments)."""
    return lambda route: route.fulfill(content_type='application/json', body=json.dumps(data))


def test_every_value_of_a_scale_is_visible_without_scrolling(browser):
    """Une question à la fois : la douleur sur une ligne, le RPE et le plaisir sur deux."""
    for fields, shape in ((['pain', 'rpe'], [7, True, 1]), (['rpe'], [10, True, 2]), (['enjoyment'], [11, True, 2])):
        tab = browser.new_page(viewport={'width': 320, 'height': 700})
        serve(tab)
        data = {**TODAY, 'asks': [{**TODAY['asks'][0], 'fields': fields}]}
        tab.route(BASE + '/carnet-api/today', replying(data))
        tab.goto(BASE + '/carnet')
        tab.locator('.n-chips').wait_for()
        rows = tab.evaluate("""[...document.querySelectorAll('.n-chips')].map(row => [
            row.children.length, row.scrollWidth <= row.clientWidth,
            new Set([...row.children].map(button => button.getBoundingClientRect().top)).size])""")
        assert rows == [shape]
        assert tab.get_by_text(f'{4 - len(fields)} sur 3', exact=True).is_visible()
        tab.close()


PNG = bytes.fromhex('89504e470d0a1a0a0000000d4948445200000001000000010806000000'
                    '1f15c4890000000d49444154789c6360f8cf00000301010018dd8db00000000049454e44ae426082')


FROZEN_GAP = """() => {
    // Pendant la transition, la copie figée doit se poser au pixel près sur l'original :
    // le moindre écart se voit comme un saut quand elle disparaît.
    const layer = document.querySelector('.n-move:not(.under)');
    layer.style.transition = 'none'; layer.style.transform = 'none';
    const copy = layer.firstElementChild, page = document.getElementById('page');
    const copies = [copy, ...copy.querySelectorAll('*')], originals = [page, ...page.querySelectorAll('*')];
    if (copies.length !== originals.length) return 'nombre d\u2019éléments différent';
    let worst = 0;
    copies.forEach((copy, i) => {
        const a = copy.getBoundingClientRect(), b = originals[i].getBoundingClientRect();
        worst = Math.max(worst, Math.abs(a.top - b.top), Math.abs(a.left - b.left), Math.abs(a.width - b.width), Math.abs(a.height - b.height));
    });
    return worst;
}"""


def test_a_frozen_screen_lands_exactly_on_the_real_one(browser):
    """La cause racine du saut de fin de transition (2026-10-09) : la copie d'un écran
    doit avoir la géométrie exacte de l'original, à l'ouverture comme au retour."""
    tab = browser.new_page(viewport={'width': 393, 'height': 852}, has_touch=True)
    serve(tab)
    tab.goto(BASE + '/carnet')
    tab.get_by_role('button', name='Semaine', exact=True).click()
    tab.get_by_role('heading', name='Semaine 41').wait_for()
    # Un premier passage met la semaine en mémoire : la copie et l'original montrent alors
    # le même écran, sans donnée qui arrive entre les deux.
    tab.get_by_role('button', name='Semaine 40').click()
    tab.get_by_role('button', name='Retour').click()
    tab.wait_for_function("document.getElementById('lt-title').textContent === 'Semaine 41'")
    tab.locator('.n-move').first.wait_for(state='detached')
    tab.get_by_role('button', name='Semaine 40').click()
    assert tab.evaluate(FROZEN_GAP) == 0
    tab.locator('.n-move').first.wait_for(state='detached')
    tab.get_by_role('button', name='Retour').click()
    tab.wait_for_function("document.getElementById('lt-title').textContent === 'Semaine 41'")
    # Au retour, la copie est l'écran qu'on quitte : on la compare à lui, rouvert.
    gap = tab.evaluate("""() => {
        const layer = document.querySelector('.n-move:not(.under)');
        layer.style.transition = 'none'; layer.style.transform = 'none';
        return layer.querySelector('h1').getBoundingClientRect().top;
    }""")
    tab.locator('.n-move').first.wait_for(state='detached')
    tab.get_by_role('button', name='Semaine 40').click()
    tab.locator('.n-move').first.wait_for(state='detached')
    assert gap == tab.locator('#lt-title').bounding_box()['y']
    tab.close()


def test_a_card_opens_in_the_app_and_closes_like_a_screen(browser):
    """La visionneuse remplace l'onglet Safari : elle s'ouvre dans l'app, se ferme par
    la croix ou Échap, et sans feuille de partage l'image se télécharge."""
    tab = browser.new_page(viewport={'width': 393, 'height': 852}, accept_downloads=True)
    serve(tab)
    entry = {**ENTRY, 'body': 'Texte', 'image_hash': 'abc', 'snapshot': None, 'report': None, 'related': []}
    tab.route(BASE + '/carnet-api/entries/one', lambda route: route.fulfill(
        content_type='application/json', body=json.dumps(entry)))
    tab.route(BASE + '/carnet-api/entries/one/image', lambda route: route.fulfill(
        content_type='image/png', body=PNG))
    tab.goto(BASE + '/carnet#entry=one')
    tab.get_by_role('button', name='Agrandir la carte').click()
    assert tab.locator('#viewer').is_visible()
    tab.get_by_role('button', name='Fermer').click()
    tab.locator('#viewer').wait_for(state='hidden')
    assert tab.locator('#lt-title').inner_text() == 'Septembre 2026'
    tab.get_by_role('button', name='Agrandir la carte').click()
    tab.keyboard.press('Escape')
    tab.locator('#viewer').wait_for(state='hidden')
    assert tab.locator('#lt-title').inner_text() == 'Septembre 2026'
    with tab.expect_download() as download:
        tab.get_by_role('button', name='Télécharger la carte').click()
    assert download.value.suggested_filename == 'redline-one.png'
    tab.close()


def test_the_last_numbers_open_without_network_and_say_when_they_date_from(browser):
    """Sans réseau, le carnet rouvre sur les derniers chiffres gardés, datés ; une
    carte d'archive n'est jamais gardée sur l'appareil."""
    context = browser.new_context(viewport={'width': 393, 'height': 852})
    tab = context.new_page()
    serve(tab)
    tab.goto(BASE + '/carnet')
    tab.get_by_role('heading', name='EF, permis accompagné', exact=True).wait_for()
    tab.wait_for_timeout(300)
    kept = tab.evaluate("JSON.parse(localStorage.getItem('carnet-data'))")
    assert set(kept['views']) == {'today', 'course', 'weeks', 'week:2026-W41'}
    tab.route(BASE + '/carnet-api/**', lambda route: route.abort())
    tab.reload()
    tab.get_by_role('heading', name='EF, permis accompagné', exact=True).wait_for()
    assert tab.locator('#offline').inner_text().startswith('Hors ligne · chiffres de ')
    tab.get_by_role('button', name='Semaine', exact=True).click()
    tab.get_by_role('heading', name='Semaine 41').wait_for()
    context.close()


def test_a_rest_day_shows_tomorrow_whole_with_its_weather(browser):
    tab = browser.new_page(viewport={'width': 393, 'height': 852})
    serve(tab)
    rest = {**TODAY, 'sessions': [], 'asks': [], 'tomorrow': {
        'date': '2026-10-10', 'plan': 'valid', 'next': [],
        'sessions': [{**TODAY['sessions'][0], 'id': 'w41-sam-ef', 'date': '2026-10-10', 'start_time': '09:30'}]}}
    tab.route(BASE + '/carnet-api/today', lambda route: route.fulfill(
        content_type='application/json', body=json.dumps(rest)))
    tab.route(BASE + '/carnet-api/weather*', lambda route: route.fulfill(
        content_type='application/json', body=json.dumps({**SKY, 'hours': [
            {'hour': 9, 'temp': 11, 'feels': 9, 'rain': 0, 'wind': 8, 'code': 0, 'day': True}]})))
    tab.goto(BASE + '/carnet')
    tab.locator('.n-pill[data-sky="clear"]').wait_for()
    assert tab.get_by_text('Repos aujourd’hui.').is_visible()
    assert tab.get_by_text('Demain · samedi 10').is_visible()
    assert tab.locator('.n-step.now').count() == 0
    tab.close()


def test_the_red_point_moves_from_the_run_to_the_strength_once_done(browser):
    """Le fil du jour (2026-10-09) : la séance faite se coche et garde ses chiffres, ses
    ressentis viennent une question à la fois, le renfo prend le seul point rouge ; les
    repères mènent à leur onglet."""
    done = ef('w41-ven-ef', '2026-10-09', 'done', {
        'km': 8.35, 'duration_min': 48, 'hr_avg': 162, 'rpe': None, 'pain': None, 'enjoyment': None,
        'started_at': '2026-10-09T07:10:00+02:00', 'over': ['hr_avg'], 'observation_id': 'obs'})
    after = {**TODAY, 'sessions': [done, RENFO], 'asks': [
        {'id': 'cell:obs', 'type': 'cells', 'date': '2026-10-09', 'kind': 'EF', 'km': 8.35,
         'fields': ['pain', 'rpe', 'enjoyment']}]}
    tab = browser.new_page(viewport={'width': 393, 'height': 852})
    answers = []
    serve(tab, answers)
    tab.route(BASE + '/carnet-api/today', lambda route: route.fulfill(
        content_type='application/json', body=json.dumps(after)))
    tab.goto(BASE + '/carnet')
    tab.locator('.n-step.done').wait_for()
    assert tab.locator('.n-step.done .n-skick').inner_text() == 'FAIT · 7\u00a0H\u00a010'
    assert tab.get_by_text('FC moyenne 5 bpm au-dessus de ton plafond.').is_visible()
    assert tab.locator('.n-step.now').count() == 1
    assert tab.locator('.n-step.now h2').inner_text() == 'Renfo, séance unique'
    assert tab.get_by_text('1 sur 3', exact=True).is_visible()
    assert tab.locator('.n-step.done .n-chips button').count() == 7
    tab.locator('.n-step.done .n-chips').get_by_role('button', name='0,5', exact=True).click()
    tab.get_by_text('Noté').wait_for()
    assert answers == [{'ask': 'cell:obs:pain', 'value': 0.5}]
    tab.locator('.n-tile', has_text='TargetRace').click()
    tab.get_by_role('heading', name='J-50').wait_for()
    tab.close()


def test_the_landmarks_open_more_detail(browser):
    """Les repères mènent au détail : la semaine en barres, l'écran Injury poussé
    par-dessus Aujourd'hui (2026-10-09)."""
    tab = browser.new_page(viewport={'width': 393, 'height': 852})
    serve(tab)
    tab.goto(BASE + '/carnet')
    tile = tab.locator('.n-tile', has_text='Injury')
    tile.locator('.n-plot').wait_for()
    left = tile.bounding_box()['x'] + 8
    assert all(box['x'] >= left for box in tile.locator('.n-plot text').evaluate_all(
        'nodes => nodes.map(n => n.getBoundingClientRect().toJSON())'))
    tile.click()
    tab.wait_for_function("document.getElementById('lt-title').textContent === 'Injury'")
    tab.locator('.n-pchart').wait_for()
    assert tab.locator('.n-pchart circle').count() == 3
    assert tab.locator('.n-pchart circle.sl').count() == 1
    assert tab.locator('.n-pchart circle.out').count() == 1
    # Une seule échelle, graduée de 0 à 3 ; le réveil s'y place à sa valeur (2026-10-10).
    assert tab.locator('.n-pchart .yl').all_text_contents() == ['0', '1', '2', '3']
    wake, zero = tab.locator('.n-pchart rect.wk'), tab.locator('.n-pchart .yl').first
    assert wake.count() == 1
    assert abs(wake.bounding_box()['y'] + wake.bounding_box()['height'] / 2
               - zero.bounding_box()['y'] - zero.bounding_box()['height'] / 2) < 4
    assert tab.get_by_text('Réveil du 7 octobre').is_visible()
    assert 'n-over' in tab.locator('.n-trow .pk').first.get_attribute('class')
    assert tab.evaluate('document.documentElement.scrollWidth <= innerWidth')
    tab.get_by_role('button', name='Retour').click()
    tab.wait_for_function("document.getElementById('lt-title').textContent === 'Aujourd’hui'")
    tab.locator('.n-tile.wide').click()
    tab.get_by_role('heading', name='Semaine 41').wait_for()
    assert tab.locator('.n-strip .n-bar').count() == 7
    tab.close()
