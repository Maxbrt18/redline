"""The interactive session page, driven in a real browser.

The page is the only part of this service written in JavaScript, and the only one
the athlete touches with their hands mid-session. Playwright is already a dependency
here (the cards are rendered with it), so there is no excuse for leaving it
untested.

Everything is asserted THROUGH THE DOM, never against internal functions: what
matters is what appears under the thumb, not how it was computed.

The page does not parse dose strings: the dose arrives structured from
`coaching/exercises/*.md`, already rendered for display. The doses below are built by the
real `Dose.payload()`, and what is asserted is that the page HONOURS them.
"""

import datetime as dt
import json
import time

import pytest

pytest.importorskip("playwright")

from playwright.sync_api import sync_playwright

from redline.cards import render
from redline.model.dose import Dose
from redline.service import page as session_page

CARDS_DIR = render.CARDS_DIR
BASE = "http://redline.test"


def dose(**data) -> dict:
    """A dose exactly as chart-strength.py hands it to the page."""
    return Dose.read(data).payload()


def exercise(name="Exercice", target="", **dose_data) -> dict:
    return {"name": name, "target": target, "dose": dose(**dose_data)}


def session(*exercises, **extra) -> dict:
    return dict({"sid": "test", "session": "a", "title": "Renfo A", "date": "test",
                 "duration_min": 30, "rule": "Une règle.",
                 "expires_at": int(time.time()) + 3600,
                 "exercises": list(exercises)}, **extra)


@pytest.fixture(scope="module")
def browser():
    """Launched the way the cards are, `CHROMIUM_PATH` included.

    `cards/render.py` honours `CHROMIUM_PATH`; this harness must start the browser the same
    way, otherwise on a platform without a Playwright download (the VPS is arm64) the cards
    render while these tests fail, not because the page is broken but because the test
    started the browser its own way.
    """
    with sync_playwright() as engine:
        instance = engine.chromium.launch(**render._launch_kwargs())
        yield instance
        instance.close()


CUES = {"step-down": ["Marche haute, ~30-40 cm — pas une marche d'escalier.",
                      "Le talon touche, on ne s'assoit pas."]}


@pytest.fixture(scope="module")
def document():
    return session_page.document(CARDS_DIR, CUES)


def serve(tab, document: str) -> None:
    """Answer GET /s with the real document, on a real origin.

    set_content() would be shorter, but it leaves the tab on about:blank -- no
    reload, no localStorage worth the name, and a relative fetch to
    /strength-report that cannot resolve. Three of the behaviours most worth
    testing are exactly those.
    """
    tab.route(f"{BASE}/s", lambda route: route.fulfill(
        status=200, content_type="text/html; charset=utf-8", body=document))


@pytest.fixture
def open_session(browser, document):
    """Load the page, resolve an opaque session capability, return the tab."""
    tabs = []

    def factory(data: dict, width: int = 375):
        tab = browser.new_page(viewport={"width": width, "height": 812})
        tabs.append(tab)
        serve(tab, document)
        token = "A" * 32
        tab.route("**/strength-session", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"status": "ok", "session": data, "zones": ZONES})))
        tab.goto(f"{BASE}/s#t={token}")
        # One OR the other: a session that carries a guardrail opens on the pain reading, and so has
        # no exercise on screen until it has been answered.
        tab.wait_for_selector(".p-step--live, #open:not([hidden])", timeout=5000)
        return tab

    yield factory
    for tab in tabs:
        tab.close()


def control(tab) -> dict:
    """What the open exercise actually offers, as seen from outside."""
    return tab.evaluate("""() => ({
        label: document.querySelector('.p-ctrl__label')?.textContent || '',
        countdown: document.getElementById('figure')?.textContent || null,
        value: document.querySelector('.p-ctrl__value')?.textContent || null,
        input: !!document.getElementById('amrap'),
        buttons: [...document.querySelectorAll('.p-ctrl .p-btn')].map(b => b.textContent),
        live: document.querySelector('.p-step--live .p-step__name')?.textContent || '',
    })""")


def click(tab, label: str) -> None:
    tab.evaluate("""label => {
        const found = [...document.querySelectorAll('.p-ctrl .p-btn')]
            .find(b => b.textContent.startsWith(label));
        if (!found) throw new Error('pas de bouton « ' + label + ' » parmi : '
            + [...document.querySelectorAll('.p-ctrl .p-btn')].map(b => b.textContent));
        found.click();
    }""", label)


def send(tab) -> None:
    """Take the send action on the review screen (« Envoyer » or « Renvoyer »)."""
    tab.evaluate("""() => {
        const found = [...document.querySelectorAll('#review .p-btn')]
            .find(b => /^(Envoyer|Renvoyer)/.test(b.textContent));
        if (!found) throw new Error("aucun bouton d'envoi");
        found.click();
    }""")


def forward(tab) -> None:
    """Take the single forward action, whatever it is called right now.

    The `quiet` list enumerates EVERYTHING that does not advance the session. It is the weak
    point of this harness: forgetting a button there does not break a test, it makes the
    click land elsewhere.
    """
    tab.evaluate("""() => {
        const quiet = ['Passer', 'Démarrer', 'Pause', 'Reprendre', '−1', '+1', '+10',
                       'Le geste', 'Son'];
        const found = [...document.querySelectorAll('.p-ctrl .p-btn')]
            .find(b => !quiet.some(q => b.textContent.startsWith(q)));
        if (!found) throw new Error("aucune action vers l'avant");
        found.click();
    }""")


# -------------------------------------------------- the dose drives the control

# Every measure the algebra can express, and what the page must do with it.
@pytest.mark.parametrize("data,countdown,input_,button", [
    ({"measure": "time", "seconds": 180}, "3:00", False, "Démarrer"),
    ({"measure": "time", "sets": 3, "seconds": [30, 45], "side": "each"},
     "0:45", False, "Démarrer"),
    # The counter aims at the BLOCK, not at the whole prescription: see
    # test_plyometrics_is_walked_one_block_at_a_time below.
    ({"measure": "contacts", "contacts": 40, "block": 10},
     "0 / 10 contacts", False, "10 contacts faits → repos"),
    ({"measure": "amrap", "sets": 2, "side": "each"}, None, True, None),
    ({"measure": "reps", "sets": 3, "reps": [8, 10], "side": "each"}, None, False, None),
])
def test_the_structured_dose_drives_the_control(open_session, data, countdown, input_, button):
    tab = open_session(session(exercise(**data)))
    state = control(tab)
    assert state["countdown"] == countdown
    assert state["input"] is input_
    if button:
        assert button in state["buttons"]


def test_a_countdown_aims_at_the_top_of_its_range(open_session):
    """Stopping at the minimum would cap the athlete at the least he was asked
    for; validating early is one tap away."""
    tab = open_session(session(exercise(measure="time", sets=3, seconds=[30, 45])))
    assert control(tab)["countdown"] == "0:45"


def test_the_dose_is_displayed_already_rendered(open_session):
    """One formatter in the repo builds a readable dose, and the page is not it."""
    tab = open_session(session(exercise(measure="reps", sets=3, reps=[8, 10], side="each"),
                               exercise(name="Suivant", measure="reps", sets=1, reps=15)))
    assert control(tab)["value"] == "3 × 8-10 / jambe"


@pytest.mark.parametrize("side,split", [("each", True), ("both", False)])
def test_a_per_side_dose_is_walked_side_by_side(open_session, side, split):
    """The side lived only in the French dose string, and an omission cost the left/right
    split that flags the calf test."""
    tab = open_session(session(exercise(measure="reps", sets=2, reps=8, side=side)))
    assert ("droite" in control(tab)["label"]) is split


# --------------------------------------------------------------- running down

def test_sets_and_sides_are_walked_in_order(open_session):
    tab = open_session(session(exercise("Step-up", measure="reps", sets=2, reps=8, side="each"),
                               exercise("Suivant", measure="reps", sets=2, reps=15)))
    seen = []
    for _ in range(4):
        seen.append(control(tab)["label"])
        forward(tab)
    assert seen == ["Série 1/2 · droite", "Série 1/2 · gauche",
                    "Série 2/2 · droite", "Série 2/2 · gauche"]
    assert control(tab)["live"] == "Suivant"


def test_one_forward_action_that_says_where_it_goes(open_session):
    """Three buttons with unclear relationships became one forward action naming
    its destination, plus one way out. Nothing else competes with it."""
    tab = open_session(session(exercise("Step-up", measure="reps", sets=2, reps=8, side="each"),
                               exercise("Gainage", measure="reps", sets=1, reps=15)))
    seen = []
    for _ in range(5):
        seen.append(control(tab)["buttons"])
        forward(tab)

    assert [buttons[0] for buttons in seen] == [
        "Puis la gauche", "Série 2 · droite", "Puis la gauche",
        "Exercice suivant", "Terminer",
    ]
    assert "Passer l'exercice" in seen[0][1:]
    assert all(any(label.startswith(('Passer', 'Arrêter ici')) for label in buttons[1:])
               for buttons in seen)


def test_rest_runs_only_when_the_session_prescribes_one(open_session):
    tab = open_session(session(exercise("A", measure="reps", sets=2, reps=15, rest_s=90),
                               exercise("B", measure="reps", sets=2, reps=15)))
    forward(tab)
    state = control(tab)
    assert state["label"] == "Repos" and state["countdown"] == "1:30"
    click(tab, "Reprendre")

    # Last set of A: the rest still runs, and it runs under B -- you are resting
    # before starting the next exercise, which is what actually happens.
    forward(tab)
    state = control(tab)
    assert state["label"] == "Repos" and state["live"] == "B"
    click(tab, "Reprendre")

    forward(tab)          # first set of B, which prescribes no rest
    assert control(tab)["label"] != "Repos"


def test_a_side_is_not_a_set_and_takes_no_rest(open_session):
    """One set per leg means "right then left": the rest only falls once both sides are done.
    The page used to cut between each side, which doubled the rests and broke the rhythm of
    the exercise."""
    tab = open_session(session(exercise("Mollet", measure="reps", sets=2, reps=8,
                                        side="each", rest_s=90)))
    forward(tab)                       # set 1 - right
    state = control(tab)
    assert state["label"] == "Série 1/2 · gauche"

    forward(tab)                       # set 1 - left: the rest happens here
    state = control(tab)
    assert state["label"] == "Repos"
    assert state["value"] == "Ensuite : Mollet · Série 2/2 · droite"
    assert state["buttons"] == ["Reprendre", "Annuler la dernière validation"]
    click(tab, "Reprendre")

    forward(tab)                       # set 2 - right
    assert control(tab)["label"] == "Série 2/2 · gauche"


def test_the_page_shows_what_the_last_session_banked(open_session):
    """The whole progression of the programme rests on "more than last time, or not", and
    the page used to reopen identical every week. The line is rendered vault-side
    (`strength_log.human`): the page formats nothing, it displays."""
    banked = exercise("Mollet", measure="amrap", sets=2, side="each")
    banked["last"] = "D 14·12 · G 13·11 — 6/10"
    tab = open_session(session(banked, exercise("Jambier", measure="reps", sets=1, reps=15)))
    assert tab.evaluate("() => document.querySelector('.p-last__label')?.textContent") \
        == "Réalisé la dernière fois"
    assert tab.evaluate("() => document.querySelector('.p-last__value')?.textContent") \
        == "D 14·12 · G 13·11 — 6/10"

    # An exercise without history shows nothing: no dash, no empty line.
    forward(tab)
    forward(tab)
    forward(tab)
    forward(tab)
    assert tab.evaluate("() => document.querySelector('.p-last')") is None


def test_the_counts_are_posted_positionally_so_the_sides_hold(open_session):
    """The side of a count is carried by its POSITION in the slot list, and the server
    re-reads it on the session. Filtering blanks before sending shifted everything by one
    as soon as a set was left empty."""
    tab = open_session(session(exercise("Mollet", measure="amrap", sets=2, side="each")))
    for reached in ("14", "", "11"):
        tab.evaluate("value => { document.getElementById('amrap').value = value; }", reached)
        forward(tab)
    forward(tab)
    posted = tab.evaluate("""() => {
        const state = JSON.parse(localStorage.getItem('redline:strength:test'));
        return state.steps[0].slots;
    }""")
    assert posted == [14, None, 11, None]


def test_plyometrics_is_walked_one_block_at_a_time(open_session):
    """"40 contacts, in blocks of 10, 30 s between blocks": the interval IS the mechanism:
    bone mechanosensitivity collapses after ~20 consecutive cycles, and spacing the jumps
    increases the gain for an equal number of contacts (strength.md, exercise 6). The page
    counted 40 in one go and put the rest after the whole exercise: it displayed the
    prescription without delivering it.
    """
    tab = open_session(session(exercise("Pliométrie", measure="contacts",
                                        contacts=40, block=10, rest_s=30),
                               exercise("Jambier", measure="reps", sets=1, reps=15)))
    labels, rests = [], 0
    for _ in range(4):
        labels.append(control(tab)["label"])
        click(tab, "10 contacts faits")
        if control(tab)["label"] == "Repos":
            rests += 1
            click(tab, "Reprendre")

    assert labels == ["Bloc 1/4", "Bloc 2/4", "Bloc 3/4", "Bloc 4/4"]
    assert rests == 4          # three between blocks, one before the next exercise
    # Four blocks of ten read "40 contacts", not "10 · 10 · 10 · 10".
    assert tab.evaluate(
        "() => document.querySelector('.p-step__dose').textContent") == "40 contacts"


def test_amrap_counts_are_kept_and_shown_back(open_session):
    tab = open_session(session(exercise("Mollet", measure="amrap", sets=2)))
    for reached in ("14", "12"):
        tab.evaluate("value => { document.getElementById('amrap').value = value; }", reached)
        forward(tab)
    assert tab.evaluate(
        "() => document.querySelector('.p-step__dose').textContent") == "14 · 12"


def test_the_clock_starts_at_the_first_move_not_at_the_first_tap(open_session):
    """The link arrives with the coach message and lingers in the phone: the duration was
    counted from the page OPENING, so inflated by all the waiting time."""
    tab = open_session(session(exercise("A", measure="reps", sets=1, reps=15),
                               sid="clock-test"))
    stored = ("() => JSON.parse(localStorage['redline:strength:clock-test'] || 'null')"
              "?.startedAt ?? null")
    assert tab.evaluate(stored) is None

    tab.evaluate("() => new Promise(done => setTimeout(done, 1200))")
    forward(tab)
    tab.wait_for_selector("#review:not([hidden])")
    started = tab.evaluate(stored)
    assert started is not None
    # The first gesture has just landed: the session lasts zero, not the second and a half it
    # spent waiting on the screen.
    assert tab.evaluate("() => Date.now()") - started < 1000


def test_progress_survives_a_reload(open_session):
    """The phone locks between two sets; coming back to a blank page would make
    the whole thing useless."""
    tab = open_session(session(exercise("A", measure="reps", sets=2, reps=8, side="each"),
                               exercise("B", measure="reps", sets=2, reps=15),
                               sid="reload-test"))
    forward(tab)
    forward(tab)
    tab.reload()
    tab.wait_for_selector(".p-step--live")
    assert control(tab)["label"] == "Série 2/2 · droite"


# ---------------------------------------------------------------- the layout

@pytest.mark.parametrize("width", [320, 375])
def test_no_dose_of_the_programme_runs_to_three_lines(open_session, width):
    """The layout guardrail.

    The cards have one (`cards/render.py` refuses a card whose text overflows); the page,
    which is the only surface the athlete touches during the session, had none, and broke
    "3 min" onto two lines and "40 contacts, in blocks of 10" onto three: the dose lived in a
    right-hand column, like on the card, except the card is 1080 px wide and the phone 375.

    **Two lines, not one**: the longest dose of the programme (37 characters) needs 265 px
    for 265 px available at 375 px wide. Fitting it on one line would mean dropping to 12 px,
    which contradicts the rule governing this whole page: legibility at arm's length before
    any visual finesse. What is forbidden is the third line.

    All the doses below are transcribed from `coaching/strength.md`, sessions A and B.
    """
    doses = [
        {"measure": "time", "seconds": 180},
        {"measure": "reps", "sets": 3, "reps": 8, "side": "each"},
        {"measure": "amrap", "sets": [2, 3], "side": "each"},
        {"measure": "reps", "sets": 3, "reps": [8, 10], "side": "each"},
        {"measure": "contacts", "contacts": 40, "block": 10},
        {"measure": "reps", "sets": 2, "reps": 15},
        {"measure": "time", "seconds": [120, 180]},
        {"measure": "time", "sets": 3, "seconds": [30, 45], "side": "each"},
    ]
    # The first exercise is the one that opens: put one in front so that ALL the tested doses
    # are rendered in a collapsed line.
    # The first exercise is the one that opens: put one in front so that ALL the tested doses
    # are rendered in a collapsed line.
    tab = open_session(session(exercise("Ouvert", measure="reps", sets=1, reps=1),
                               *[exercise("Un nom d'exercice plutôt long, comme ils "
                                          "le sont tous", **data) for data in doses]),
                       width=width)
    laid_out = tab.evaluate("""() => [...document.querySelectorAll('.p-step__dose')].map(
        node => ({text: node.textContent,
                  lines: Math.round(node.clientHeight
                                    / parseFloat(getComputedStyle(node).lineHeight))}))""")
    assert len(laid_out) == len(doses)
    broken = [item["text"] for item in laid_out if item["lines"] > 2]
    assert not broken, f"dose(s) sur 3 lignes ou plus à {width} px : {broken}"

    # And nothing overflows in width: a page that scrolls horizontally is a page that cannot
    # be used with one hand.
    assert tab.evaluate(
        "() => document.documentElement.scrollWidth <= document.documentElement.clientWidth")


# ----------------------------------------------------------------- the link

def test_a_broken_link_says_so_instead_of_going_blank(browser, document):
    tab = browser.new_page(viewport={"width": 375, "height": 812})
    serve(tab, document)
    tab.goto(f"{BASE}/s#tronque")
    tab.wait_for_selector("#boot:not([hidden])")
    assert "Lien incomplet" in tab.text_content("#boot-title")
    tab.close()


def test_an_expired_link_stops_before_the_workout(browser, document):
    tab = browser.new_page(viewport={"width": 375, "height": 812})
    serve(tab, document)
    tab.route("**/strength-session", lambda route: route.fulfill(
        status=410, content_type="application/json", body='{"status":"expired"}'))
    tab.goto(f"{BASE}/s#t={'E' * 32}")
    tab.wait_for_selector("#boot:not([hidden])")
    assert "Lien expiré" in tab.text_content("#boot-title")
    assert tab.locator(".p-step--live").count() == 0
    tab.close()


def test_a_second_tap_on_the_button_reopens_the_session(browser, document):
    """Changing only the fragment does not reload the document: without the
    hashchange listener, the second tap leaves "Lien incomplet" on screen."""
    tab = browser.new_page(viewport={"width": 375, "height": 812})
    serve(tab, document)
    tab.goto(f"{BASE}/s")
    tab.wait_for_selector("#boot:not([hidden])")
    data = session(exercise("Mollet", measure="reps", sets=2, reps=15))
    tab.route("**/strength-session", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"status": "ok", "session": data, "zones": ZONES})))
    tab.evaluate("fragment => { location.hash = fragment; }", f"t={'F' * 32}")
    tab.wait_for_selector(".p-step--live", timeout=5000)
    assert tab.text_content(".p-step--live .p-step__name") == "Mollet"
    tab.close()


def test_an_opaque_capability_resolves_then_posts_the_same_token(browser, document):
    tab = browser.new_page(viewport={"width": 375, "height": 812})
    serve(tab, document)
    token = "A" * 32
    data = session(exercise("Mollet", measure="reps", sets=1, reps=15),
                   sid="opaque-token")
    resolved = []
    tab.route("**/strength-session", lambda route: (
        resolved.append(json.loads(route.request.post_data)),
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"status": "ok", "session": data, "zones": ZONES}))))
    tab.route("**/strength-report", lambda route: route.fulfill(status=200, body="ok"))

    tab.goto(f"{BASE}/s#t={token}")
    tab.wait_for_selector(".p-step--live")
    assert resolved == [{"token": token}]
    forward(tab)
    tab.wait_for_selector("#review:not([hidden])")
    with tab.expect_request("**/strength-report") as caught:
        send(tab)
    body = json.loads(caught.value.post_data)
    assert body["token"] == token
    assert "payload" not in body and "sig" not in body
    tab.close()


def test_a_resolved_snapshot_reopens_offline_on_the_same_phone(browser, document):
    tab = browser.new_page(viewport={"width": 375, "height": 812})
    serve(tab, document)
    token = "B" * 32
    data = session(exercise("Hors ligne", measure="reps", sets=1, reps=8),
                   sid="offline-cache")
    online = {"value": True}

    def resolve(route):
        if online["value"]:
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"status": "ok", "session": data, "zones": ZONES}))
        else:
            route.abort()

    tab.route("**/strength-session", resolve)
    tab.goto(f"{BASE}/s#t={token}")
    tab.wait_for_selector(".p-step--live")
    assert tab.text_content(".p-step--live .p-step__name") == "Hors ligne"

    online["value"] = False
    tab.reload()
    tab.wait_for_selector(".p-step--live")
    assert tab.text_content(".p-step--live .p-step__name") == "Hors ligne"
    tab.close()


def test_a_first_open_without_network_explains_why_it_cannot_resume(browser, document):
    tab = browser.new_page(viewport={"width": 375, "height": 812})
    serve(tab, document)
    tab.route("**/strength-session", lambda route: route.abort())
    tab.goto(f"{BASE}/s#t={'C' * 32}")
    tab.wait_for_selector("#boot:not([hidden])")
    assert tab.text_content("#boot-title") == "Réseau indisponible"
    assert "première fois" in tab.text_content("#boot-msg")
    tab.close()


def test_server_revocation_wins_over_a_cached_snapshot(browser, document):
    tab = browser.new_page(viewport={"width": 375, "height": 812})
    serve(tab, document)
    token = "D" * 32
    data = session(exercise("Révoqué", measure="reps", sets=1, reps=8),
                   sid="revoked-cache")
    revoked = {"value": False}

    def resolve(route):
        if revoked["value"]:
            route.fulfill(status=410, content_type="application/json",
                          body='{"status":"expired"}')
        else:
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"status": "ok", "session": data, "zones": ZONES}))

    tab.route("**/strength-session", resolve)
    tab.goto(f"{BASE}/s#t={token}")
    tab.wait_for_selector(".p-step--live")
    revoked["value"] = True
    tab.reload()
    tab.wait_for_selector("#boot:not([hidden])")
    assert tab.text_content("#boot-title") == "Lien expiré"
    tab.close()


# --------------------------------------------------------------- the report

def test_the_report_names_what_the_number_measures(open_session):
    """Sent out of context, "7/10" on an exercise reads like a performance mark."""
    tab = open_session(session(exercise("A", measure="reps", sets=1, reps=15)))
    forward(tab)
    tab.wait_for_selector("#review:not([hidden])")
    hint = tab.text_content("#review > .p-review__hint")
    assert "dur" in hint and "facile" in hint
    assert "difficulté ressentie" in tab.text_content(".p-score__legend").lower()


def test_the_report_posted_back_is_the_session_that_was_done(open_session):
    tab = open_session(session(exercise("Mollet", measure="amrap", sets=1),
                               exercise("Sauté", measure="reps", sets=2, reps=15),
                               sid="report-test"))
    tab.route("**/strength-report", lambda route: route.fulfill(status=200, body="ok"))

    tab.evaluate("() => { document.getElementById('amrap').value = '17'; }")
    forward(tab)
    click(tab, "Passer")
    tab.wait_for_selector("#review:not([hidden])")

    with tab.expect_request("**/strength-report") as caught:
        tab.evaluate("""() => {
            document.querySelectorAll('.p-scale')[0]
                    .querySelector('[role=radio]:nth-child(8)').click();
        }""")
        send(tab)
    body = json.loads(caught.value.post_data)

    assert body["token"] == "A" * 32
    assert "sig" not in body and "payload" not in body
    assert body["report"]["performed_on"] == dt.date.today().isoformat()
    reported = {item["i"]: item for item in body["report"]["exercises"]}
    assert reported[0]["reps"] == [17] and reported[0]["score"] == 7
    assert reported[1]["skipped"] is True


def test_a_failed_report_is_kept_and_leaves_on_its_own(open_session):
    """The report is the ONLY path by which the session comes back to the coach, and it is
    used from a cellar, a locker room, or during a VPS redeploy. A failed POST must not just
    display "retry" and never try."""
    tab = open_session(session(exercise("A", measure="reps", sets=1, reps=15),
                               sid="retry-test"))
    tab.route("**/strength-report", lambda route: route.abort())
    forward(tab)
    tab.wait_for_selector("#review:not([hidden])")
    send(tab)
    tab.wait_for_function("() => document.getElementById('status').textContent.includes('gardé')")
    stored = tab.evaluate(
        "() => JSON.parse(localStorage.getItem('redline:strength:retry-test'))")
    assert stored["pending"] is True
    assert dt.date.fromisoformat(stored["performedOn"]) == dt.date.today()

    # The network returns: the backoff timer resends with no event or gesture.
    posted = []
    tab.route("**/strength-report", lambda route: (
        posted.append(route.request.post_data), route.fulfill(status=200, body="ok")))
    tab.wait_for_function(
        "() => document.getElementById('status').textContent.includes('Envoyé')",
        timeout=7000)
    assert len(posted) == 1


def test_a_permanent_refusal_is_not_retried_as_a_network_failure(open_session):
    tab = open_session(session(exercise("A", measure="reps", sets=1, reps=15),
                               sid="quota-test"))
    tab.route("**/strength-report", lambda route: route.fulfill(
        status=429, content_type="application/json", body='{"status":"quota"}'))
    forward(tab)
    tab.wait_for_selector("#review:not([hidden])")
    send(tab)
    tab.wait_for_function(
        "() => document.getElementById('status').textContent.includes('limite de sécurité')")
    stored = tab.evaluate(
        "() => JSON.parse(localStorage.getItem('redline:strength:quota-test'))")
    assert stored["pending"] is False and stored["sent"] is False


def open_sound(tab):
    """Sound is set in a sheet, opened from the bar icon, like the notebook settings (one
    style for the whole app)."""
    tab.locator("#seance-sound").click()
    tab.locator("#sound-sheet.open").wait_for()


def silence(tab):
    open_sound(tab)
    tab.locator("#sound-panel").get_by_role("button", name="Non").click()
    tab.locator("#sound-sheet [data-close]").last.click()


def test_the_sound_can_be_silenced_and_stays_silenced(open_session):
    """A beep during a 45 s isometric is the only way not to watch the screen; it must also be
    mutable, and remember it. The bar icon says so: crossed-out speaker."""
    tab = open_session(session(exercise("A", measure="time", seconds=45), sid="son-test"))
    icon = tab.locator("#seance-sound")
    assert icon.get_attribute("aria-label") == "Son" and "muted" not in icon.get_attribute("class")
    open_sound(tab)
    assert tab.locator("#sound-panel").get_by_role("button", name="Oui").get_attribute("aria-pressed") == "true"
    tab.locator("#sound-panel").get_by_role("button", name="Non").click()
    assert icon.get_attribute("aria-label") == "Son coupé" and "muted" in icon.get_attribute("class")

    tab.reload()
    tab.wait_for_selector(".p-step--live")
    assert tab.locator("#seance-sound").get_attribute("aria-label") == "Son coupé"
    open_sound(tab)
    assert tab.locator("#sound-panel").get_by_role("button", name="Non").get_attribute("aria-pressed") == "true"


# ---------------------------------------------------------------- the cues

def test_the_cues_come_from_the_page_not_from_the_link(open_session):
    """They are baked into the document, by slug, and the link does not carry them.

    The cues of the 9 exercises of session A bring the fragment to 106% of the ceiling, 103%
    even when reduced to two cues per exercise once "last time" is present. The link could
    not carry both, and it already carries the slug, which is enough to find the cues.
    """
    step = exercise("Squat unipodal sur marche haute", measure="reps", sets=1, reps=6)
    step["slug"] = "step-down"
    data = session(step, exercise("Sans consigne", measure="reps", sets=1, reps=8))
    tab = open_session(data)

    # The link carries not a letter of it.
    assert "talon" not in json.dumps(data, ensure_ascii=False)

    # Collapsed by default: nothing on screen until asked.
    assert tab.evaluate("() => !!document.querySelector('.p-how__list')") is False
    tab.evaluate("() => document.querySelector('.p-how__toggle').click()")
    cues = tab.evaluate(
        "() => [...document.querySelectorAll('.p-how__cue')].map(n => n.textContent)")
    assert cues == CUES["step-down"]

    # And the next exercise, which has no cue, has no button.
    forward(tab)
    assert tab.evaluate("() => !!document.querySelector('.p-how__toggle')") is False


def test_the_cue_panel_stays_open_across_exercises(open_session):
    """Whoever wants the cues wants them on every exercise, not to ask again nine times: the
    state is global and survives a reload."""
    first = exercise("Un", measure="reps", sets=1, reps=6)
    second = exercise("Deux", measure="reps", sets=1, reps=6)
    first["slug"] = second["slug"] = "step-down"
    tab = open_session(session(first, second, sid="geste"))
    tab.evaluate("() => document.querySelector('.p-how__toggle').click()")
    forward(tab)
    assert tab.evaluate("() => !!document.querySelector('.p-how__list')") is True
    tab.reload()
    tab.wait_for_selector(".p-step--live")
    assert tab.evaluate("() => !!document.querySelector('.p-how__list')") is True


def test_a_page_without_cues_still_runs(browser):
    """A broken exercise note must fail vault-check and the card render, never the service
    start: without cues, the button does not appear and the session runs as before."""
    bare = session_page.document(CARDS_DIR, {})
    tab = browser.new_page(viewport={"width": 375, "height": 812})
    serve(tab, bare)
    step = exercise("Squat", measure="reps", sets=1, reps=6)
    step["slug"] = "step-down"
    data = session(step)
    tab.route("**/strength-session", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"status": "ok", "session": data, "zones": ZONES})))
    tab.goto(f"{BASE}/s#t={'G' * 32}")
    tab.wait_for_selector(".p-step--live")
    assert tab.evaluate("() => !!document.querySelector('.p-how__toggle')") is False
    # The name appears in the page script (it reads it); what must be missing is the
    # ASSIGNMENT.
    assert "window.REDLINE_HOW=" not in bare
    tab.close()


# ------------------------------------------------------- the opening and the injury

# The watched zone that the page asks for before and after, as the server serves it.
ZONES = [{"zone": "injury", "label": "Injury"}]
GATE = {"pain_max": 2, "drop": ["plyometrics"],
        "say": "Douleur au-dessus de 2 : on saute la pliométrie et on garde le reste léger."}


def opening(tab) -> bool:
    return tab.evaluate("() => !document.getElementById('open').hidden")


def pick(tab, value: int) -> None:
    tab.evaluate("value => document.querySelector('#open .p-scale')"
                 ".querySelectorAll('[role=radio]')[value].click()", value)


def gated_session(*extra):
    plyo = exercise("Pliométrie", measure="contacts", contacts=40, block=10)
    plyo["slug"] = "plyometrics"
    mollet = exercise("Mollet", measure="reps", sets=1, reps=8)
    mollet["slug"] = "calf-raise-bent"
    return session(mollet, plyo, *extra, gate=GATE, sid="gate")


def test_a_session_without_a_gate_never_asks(open_session):
    """The page only asks for the injury at opening if the coach wrote what to do with it.
    Without a guardrail, nothing changes and the session opens directly."""
    tab = open_session(session(exercise("A", measure="reps", sets=1, reps=8)))
    assert opening(tab) is False


def test_the_gate_drops_what_the_coach_named_and_says_his_sentence(open_session):
    tab = open_session(gated_session())
    assert opening(tab) is True
    pick(tab, 4)                      # above the threshold of 2
    assert opening(tab) is False

    # The sentence is the coach's, word for word: the page writes nothing. And it has its own
    # line: writing it into `#note` erased the session note.
    assert tab.text_content("#gate") == GATE["say"]
    doses = tab.evaluate(
        "() => [...document.querySelectorAll('.p-step__dose')].map(n => n.textContent)")
    assert "retiré — garde-fou" in doses

    # And the removed exercise is not to be done: the session opens on the other one.
    assert tab.text_content(".p-step--live .p-step__name") == "Mollet"


def test_a_reading_under_the_threshold_changes_nothing(open_session):
    tab = open_session(gated_session())
    pick(tab, 1)
    assert tab.evaluate("() => document.getElementById('gate').hidden") is True
    doses = tab.evaluate(
        "() => [...document.querySelectorAll('.p-step__dose')].map(n => n.textContent)")
    assert "retiré — garde-fou" not in doses


def test_the_opening_can_be_skipped_and_then_nothing_is_gated(open_session):
    """Nothing is mandatory here: a tool you have to fight is a tool you stop opening.
    Without a reading, the guardrail cannot trigger, and it does not."""
    tab = open_session(gated_session())
    tab.evaluate("""() => [...document.querySelectorAll('#open .p-btn')]
        .find(b => b.textContent.startsWith('Je passe')).click()""")
    assert opening(tab) is False
    assert tab.evaluate("() => document.getElementById('gate').hidden") is True


def test_a_zero_is_an_answer_not_an_absence(open_session):
    """The boolean flag trap: a perfectly silent pain is 0, and without `opened` the opening
    screen would reopen at each return to the tab."""
    tab = open_session(gated_session())
    pick(tab, 0)
    assert opening(tab) is False
    tab.reload()
    tab.wait_for_selector(".p-step--live")
    assert opening(tab) is False


def test_both_readings_reach_the_report(open_session):
    tab = open_session(gated_session())
    pick(tab, 1)
    tab.route("**/strength-report", lambda route: route.fulfill(status=200, body="ok"))
    forward(tab)                                  # the calf
    for _ in range(4):                            # the four blocks
        forward(tab)
    tab.wait_for_selector("#review:not([hidden])")
    tab.evaluate("""() => document.querySelectorAll('.p-scale')
        [document.querySelectorAll('.p-scale').length - 1]
        .querySelectorAll('[role=radio]')[3].click()""")
    with tab.expect_request("**/strength-report") as caught:
        send(tab)
    body = json.loads(caught.value.post_data)
    assert body["report"]["pains"] == {"injury": {"before": 1, "after": 3}}


def test_the_gate_sentence_does_not_eat_the_session_note(open_session):
    """It used to be written into `#note`: the coach note silently disappeared, on the very
    day it has the most to say."""
    data = gated_session()
    data["note"] = "Demi-dose, sans pliométrie."
    tab = open_session(data)
    pick(tab, 5)
    assert tab.text_content("#note") == "Demi-dose, sans pliométrie."
    assert tab.text_content("#gate") == GATE["say"]


def test_the_beeps_actually_fire(browser, document):
    """The only "sound" test checked the button LABEL and its persistence: the whole audio
    path could be dead and the suite would stay green. Here `AudioContext` is instrumented
    before loading and the oscillators actually started are counted: the countdown must
    sound the last seconds, then the end.
    """
    tab = browser.new_page(viewport={"width": 375, "height": 812})
    tab.add_init_script("""
        window.__beeps = [];
        const Real = window.AudioContext;
        window.AudioContext = class extends Real {
            createOscillator() {
                const tone = super.createOscillator();
                // À la CRÉATION la fréquence vaut encore 440 (le défaut) : on la lit
                // au démarrage, c'est-à-dire une fois que la page l'a réglée.
                const start = tone.start.bind(tone);
                tone.start = (...args) => {
                    window.__beeps.push(tone.frequency.value);
                    return start(...args);
                };
                return tone;
            }
        };
    """)
    serve(tab, document)
    data = session(exercise("Iso", measure="time", seconds=2),
                   exercise("Suite", measure="reps", sets=1, reps=8), sid="bip")
    tab.route("**/strength-session", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"status": "ok", "session": data, "zones": ZONES})))
    tab.goto(f"{BASE}/s#t={'H' * 32}")
    tab.wait_for_selector(".p-step--live")
    click(tab, "Démarrer")
    # Wait for the countdown to have RUN TO THE END (the next exercise opens by itself), not for
    # a number of beeps to be reached: reading the counter at two beeps always missed the end
    # beep.
    tab.wait_for_function(
        "() => document.querySelector('.p-step--live .p-step__name').textContent === 'Suite'",
        timeout=10000)
    beeps = tab.evaluate("() => window.__beeps")
    # Countdown beeps (880 Hz) THEN the end one (523): two pitches, and the last one is the
    # end one.
    assert 880 in beeps and 988 in beeps and beeps[-2:] == [523, 523], beeps
    tab.close()


def test_sound_and_screen_lock_survive_a_phone_sleep(browser, document):
    """On iPhone, after sleep, the audio context stays "interrupted" and the screen lock is
    released: the page had to be closed and reopened. Sleep is simulated (page hidden,
    context interrupted, lock released) and a single tap on return must restore both the
    sound AND the lock.
    """
    tab = browser.new_page(viewport={"width": 375, "height": 812})
    tab.add_init_script("""
        window.__contexts = [];
        window.__beeps = [];
        window.__locks = [];
        const Real = window.AudioContext;
        window.AudioContext = class extends Real {
            constructor(...args) { super(...args); window.__contexts.push(this); }
            createOscillator() { window.__beeps.push(this); return super.createOscillator(); }
        };
        Object.defineProperty(navigator, 'wakeLock', { value: { request: async () => {
            const lock = new EventTarget();
            lock.released = false;
            lock.release = async () => { lock.released = true; lock.dispatchEvent(new Event('release')); };
            window.__locks.push(lock);
            return lock;
        } } });
        let hidden = false;
        Object.defineProperty(document, 'hidden', { get: () => hidden });
        window.__sleep = (value) => { hidden = value; document.dispatchEvent(new Event('visibilitychange')); };
    """)
    serve(tab, document)
    data = session(exercise("Iso", measure="time", seconds=30), sid="veille")
    tab.route("**/strength-session", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"status": "ok", "session": data, "zones": ZONES})))
    tab.goto(f"{BASE}/s#t={'V' * 32}")
    tab.wait_for_selector(".p-step--live")
    open_sound(tab)
    tab.get_by_role("button", name="Tester le son").click()
    tab.wait_for_function("() => window.__beeps.length > 0")
    assert tab.evaluate("() => window.__locks.some(lock => !lock.released)")

    tab.evaluate("() => { window.__locks.forEach(lock => lock.release()); window.__sleep(true); }")
    tab.evaluate("() => window.__sleep(false)")
    tab.evaluate("() => { window.__beeps = []; }")
    tab.get_by_role("button", name="Tester le son").click()
    tab.wait_for_function("() => window.__beeps.length > 0")
    first, *_, last = tab.evaluate("() => window.__contexts.map(c => c.state)")
    assert first == "closed" and last == "running"
    assert tab.evaluate("() => window.__beeps.every(c => c === window.__contexts.at(-1))")
    assert tab.evaluate("() => window.__locks.some(lock => !lock.released)")
    tab.close()


def test_silencing_really_silences(browser, document):
    tab = browser.new_page(viewport={"width": 375, "height": 812})
    tab.add_init_script("""
        window.__beeps = [];
        const Real = window.AudioContext;
        window.AudioContext = class extends Real {
            createOscillator() { window.__beeps.push(1); return super.createOscillator(); }
        };
    """)
    serve(tab, document)
    data = session(exercise("Iso", measure="time", seconds=2))
    tab.route("**/strength-session", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"status": "ok", "session": data, "zones": ZONES})))
    tab.goto(f"{BASE}/s#t={'I' * 32}")
    tab.wait_for_selector(".p-step--live")
    silence(tab)
    click(tab, "Démarrer")
    tab.wait_for_timeout(2600)
    assert tab.evaluate("() => window.__beeps.length") == 0
    tab.close()


def test_gated_exercise_is_consultable_but_has_no_execution_or_rating(open_session):
    tab = open_session(gated_session())
    pick(tab, 4)
    tab.locator('.p-step__head').nth(1).click()
    assert control(tab)['label'] == 'Retiré par le garde-fou de douleur'
    assert 'Bloc 2' not in control(tab)['buttons']
    click(tab, 'Revenir à la séance')
    forward(tab)
    assert tab.locator('#review .p-score__name').all_text_contents() == ['Mollet', 'Injury, maintenant', 'Durée (min)', 'À dire au coach (facultatif)']


def test_gate_can_remove_first_or_every_exercise(open_session):
    data = gated_session()
    data['gate'] = {**GATE, 'drop': ['calf-raise-bent', 'plyometrics']}
    tab = open_session(data)
    pick(tab, 4)
    assert tab.locator('#review').is_visible()
    assert tab.locator('#review .p-feedback').count() == 0
    assert tab.locator('#review input[type=number]').input_value() == ''


def test_partial_work_and_note_reach_report_and_can_resume(open_session):
    tab = open_session(session(exercise('Mollet', measure='amrap', sets=3)))
    tab.locator('#amrap').fill('17')
    forward(tab)
    click(tab, 'Arrêter ici')
    tab.locator('.p-exercise-note').fill('Équilibre difficile à gauche')
    posted = []
    tab.route('**/strength-report', lambda route: (posted.append(route.request.post_data_json), route.fulfill(status=201, json={'status': 'accepted'})))
    send(tab)
    tab.wait_for_function("document.querySelector('#status').textContent.startsWith('Envoyé')")
    result = posted[0]['report']['exercises'][0]
    assert result['status'] == 'partial' and result['reps'] == [17]
    assert result['skipped'] is False and result['note'] == 'Équilibre difficile à gauche'
    assert tab.get_by_text('Renvoyer', exact=True).count() == 0
    assert tab.locator('.p-exercise-note').is_disabled()
    tab.get_by_role('button', name='Ajouter une correction').click()
    assert 'Correction du compte-rendu' in tab.get_by_role('textbox', name='Correction à transmettre au coach').input_value()


def test_draft_survives_sound_cues_and_reload(open_session):
    tab = open_session(session({**exercise('Mollet', measure='amrap', sets=3), 'slug': 'step-down'}))
    tab.locator('#amrap').fill('17')
    silence(tab)
    assert tab.locator('#amrap').input_value() == '17'
    click(tab, 'Le geste')
    assert tab.locator('#amrap').input_value() == '17'
    tab.reload()
    tab.wait_for_selector('#amrap')
    assert tab.locator('#amrap').input_value() == '17'


def test_undo_keeps_other_sets_and_restores_last_input(open_session):
    tab = open_session(session(exercise('Mollet', measure='amrap', sets=3)))
    tab.locator('#amrap').fill('17')
    forward(tab)
    tab.locator('#amrap').fill('12')
    forward(tab)
    click(tab, 'Annuler la dernière validation')
    assert control(tab)['label'] == 'Série 2/3'
    assert tab.locator('#amrap').input_value() == '12'
    assert tab.locator('.p-tally').inner_text() == 'Déjà fait : 17'


def test_work_clock_reload_requires_confirmation_and_preserves_pause(open_session):
    tab = open_session(session(exercise('Planche', measure='time', seconds=45)))
    tab.clock.install()
    click(tab, 'Démarrer')
    tab.clock.fast_forward(5000)
    tab.clock.fast_forward(3000)
    click(tab, 'Pause')
    tab.reload()
    tab.wait_for_selector('.p-step--live')
    assert control(tab)['label'] == 'Reprise après interruption'
    assert control(tab)['countdown'] == '0:42'
    click(tab, 'Reprendre le chrono')
    assert control(tab)['countdown'] == '0:42'


def test_cached_session_survives_temporary_server_failure(open_session):
    tab = open_session(session(exercise('A', measure='reps', reps=8)))
    tab.route('**/strength-session', lambda route: route.fulfill(status=503, body='temporary failure'))
    tab.reload()
    tab.wait_for_selector('.p-step--live')
    assert control(tab)['live'] == 'A'


def test_rest_keeps_next_setup_cues_history_and_feedback(open_session):
    done = exercise('Mollet', measure='reps', reps=8, rest_s=30)
    next_ = {**exercise('RDL', measure='reps', reps=[8, 10]), 'slug': 'step-down', 'cue': 'Dos plat, bascule par la hanche.', 'equipment': ['Haltère'], 'load': {'count': 1, 'kg': 10}, 'last': '6/10'}
    tab = open_session(session(done, next_))
    forward(tab)
    assert control(tab)['label'] == 'Repos'
    assert 'RDL' in control(tab)['value']
    assert tab.locator('.p-setup').first.inner_text() == 'Charge prévue : 1 × 10 kg'
    click(tab, 'Le geste')
    assert control(tab)['label'] == 'Repos'
    assert tab.locator('.p-last__value').inner_text() == '6/10'
    tab.locator('.p-feedback .p-scale__step').nth(6).click()
    tab.locator('.p-exercise-note').fill('Facile à droite')
    click(tab, 'Reprendre')
    forward(tab)
    assert tab.locator('#review .p-feedback').first.locator('[aria-checked=true]').inner_text() == '6'
    assert tab.locator('#review .p-exercise-note').first.input_value() == 'Facile à droite'


def test_contact_block_uses_one_tap_or_an_explicit_smaller_count(open_session):
    tab = open_session(session(exercise('Sauts', measure='contacts', contacts=20, block=10)))
    click(tab, '10 contacts faits')
    click(tab, 'Autre nombre')
    for _ in range(8):
        click(tab, '+1')
    forward(tab)
    assert tab.locator('.p-step__dose').inner_text() == '18 contacts'


def test_automatic_iso_walks_sides_and_rest_but_not_next_exercise(open_session):
    tab = open_session(session(exercise('Iso', measure='time', sets=2, side='each', seconds=2, rest_s=5), exercise('Suite', measure='reps', reps=8)))
    tab.clock.install()
    tab.locator('.p-auto input').check()
    click(tab, 'Démarrer')
    assert control(tab)['label'] == 'Prépare-toi'
    tab.clock.fast_forward(5000)
    tab.clock.fast_forward(2000)
    assert control(tab)['label'] == 'Prépare-toi'
    assert 'GAUCHE' in tab.locator('.p-current').inner_text()
    tab.clock.fast_forward(5000)
    tab.clock.fast_forward(2000)
    assert control(tab)['label'] == 'Repos'
    tab.clock.fast_forward(5000)
    assert control(tab)['label'] == 'Prépare-toi'
    tab.clock.fast_forward(5000)
    tab.clock.fast_forward(2000)
    tab.clock.fast_forward(5000)
    tab.clock.fast_forward(2000)
    assert control(tab)['live'] == 'Suite' and control(tab)['label'] == 'Repos'
    tab.clock.fast_forward(5000)
    assert control(tab)['live'] == 'Suite' and control(tab)['countdown'] is None


def test_automatic_sequence_does_not_start_work_in_hidden_page(open_session):
    tab = open_session(session(exercise('Iso', measure='time', sets=2, seconds=2, rest_s=5)))
    tab.clock.install()
    tab.locator('.p-auto input').check()
    click(tab, 'Démarrer')
    tab.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, value: true}); document.dispatchEvent(new Event('visibilitychange'))")
    tab.clock.fast_forward(10000)
    tab.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, value: false}); document.dispatchEvent(new Event('visibilitychange'))")
    assert control(tab)['label'] == 'Reprise après interruption'
    assert 'Série réalisée' not in control(tab)['buttons']
    stored = tab.evaluate("JSON.parse(localStorage['redline:strength:test'])")
    assert stored['steps']['0']['slots'] == [] and stored['startedAt'] is None


def test_sound_settings_test_and_intensity_survive_reload(open_session):
    tab = open_session(session(exercise('A', measure='reps', reps=8)))
    open_sound(tab)
    tab.get_by_role('slider', name='Intensité des signaux').fill('60')
    tab.get_by_role('button', name='Tester le son').click()
    assert tab.evaluate("localStorage['redline:strength:intensite']") == '0.6'
    tab.reload()
    tab.wait_for_selector('.p-step--live')
    open_sound(tab)
    assert tab.get_by_role('slider', name='Intensité des signaux').input_value() == '60'


def test_voice_is_off_by_default_so_music_keeps_playing(open_session):
    """On iPhone, speechSynthesis pauses other apps' music: the voice only speaks if it was
    asked for, and the choice survives a reload."""
    tab = open_session(session(exercise('Iso', measure='time', sets=2, seconds=10, rest_s=5)))
    open_sound(tab)
    voice = tab.get_by_role('switch', name='Annoncer le côté à voix haute')
    assert not voice.is_checked()
    voice.check()
    assert tab.evaluate("localStorage['redline:strength:voix']") == 'oui'
    tab.reload()
    tab.wait_for_selector('.p-step--live')
    open_sound(tab)
    assert tab.get_by_role('switch', name='Annoncer le côté à voix haute').is_checked()


def test_preparation_uses_previous_prescription_and_not_realized_counts(browser, document):
    current = session({**exercise('RDL', measure='reps', reps=[8, 10]), 'slug': 'rdl', 'cue': 'Dos plat, bascule par la hanche.', 'load': {'kg': 6, 'count': 2}, 'equipment': ['Haltères'], 'last': 'D 7 · G 6 — 6/10'})
    previous = {**current, 'sid': 'previous', 'exercises': [{**current['exercises'][0], 'load': {'kg': 10, 'count': 1}}]}
    tab = browser.new_page(viewport={'width': 375, 'height': 812})
    serve(tab, document)
    tab.route('**/strength-session', lambda route: route.fulfill(json={'status': 'ok', 'session': current, 'previous_session': previous}))
    tab.goto(f"{BASE}/s#t={'A' * 32}")
    tab.wait_for_selector('.p-step--live')
    tab.locator('#prep').evaluate('node => node.open = true')
    text = tab.locator('#prep-content').inner_text()
    assert 'charge : 2 × 6 kg' in text and 'dose :' not in text
    assert '7 · G 6' not in text and 'Haltères' in text
    assert tab.locator('.p-current').inner_text() == '8-10 répétitions'
    tab.close()


def test_sound_has_a_sustained_audible_waveform_and_no_clipping(browser, document):
    tab = browser.new_page(viewport={'width': 375, 'height': 812})
    tab.add_init_script("""window.AudioContext = class {
        constructor() {
            const context = new OfflineAudioContext(1, 96000, 48000);
            Object.defineProperty(context, 'state', {get: () => 'running'});
            context.resume = async () => {};
            window.recordedAudio = context;
            return context;
        }
    };""")
    serve(tab, document)
    data = session(exercise('A', measure='reps', reps=8))
    tab.route('**/strength-session', lambda route: route.fulfill(json={'status': 'ok', 'session': data}))
    tab.goto(f"{BASE}/s#t={'A' * 32}")
    tab.wait_for_selector('.p-step--live')
    open_sound(tab)
    tab.get_by_role('button', name='Tester le son').click()
    measured = tab.evaluate("""async () => {
        const buffer = await window.recordedAudio.startRendering();
        const samples = buffer.getChannelData(0);
        let peak = 0, energy = 0;
        for (const value of samples) peak = Math.max(peak, Math.abs(value));
        for (let i = 2400; i < 12000; i++) energy += samples[i] ** 2;
        return {peak, rms: Math.sqrt(energy / 9600)};
    }""")
    assert 0.45 < measured['peak'] < 0.7
    assert measured['rms'] > 0.2
    tab.close()


def test_expired_hidden_work_needs_confirmation_and_never_banks_it(open_session):
    tab = open_session(session(exercise('Iso', measure='time', sets=2, seconds=10, rest_s=5)))
    tab.clock.install()
    tab.locator('.p-auto input').check()
    click(tab, 'Démarrer')
    tab.clock.fast_forward(5000)
    tab.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, value: true}); document.dispatchEvent(new Event('visibilitychange'))")
    tab.clock.fast_forward(15000)
    tab.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, value: false}); document.dispatchEvent(new Event('visibilitychange'))")
    assert control(tab)['label'] == 'Reprise après interruption'
    stored = tab.evaluate("JSON.parse(localStorage['redline:strength:test'])")
    assert stored['steps']['0']['slots'] == []
    click(tab, 'Série réalisée')
    assert control(tab)['label'] == 'Repos'
    assert tab.evaluate("JSON.parse(localStorage['redline:strength:test']).steps['0'].slots") == [None]


def test_rest_survives_reload_after_advancing_to_next_exercise(open_session):
    tab = open_session(session(exercise('Premier', measure='reps', reps=8, rest_s=90), exercise('Suite', measure='reps', reps=10)))
    forward(tab)
    assert control(tab)['live'] == 'Suite' and control(tab)['label'] == 'Repos'
    tab.reload()
    tab.wait_for_selector('.p-step--live')
    assert control(tab)['live'] == 'Suite' and control(tab)['label'] == 'Repos'
    stored = tab.evaluate("JSON.parse(localStorage['redline:strength:test'])")
    assert stored['timer']['index'] == 1 and stored['steps']['0']['slots'] == [None]
    click(tab, 'Reprendre')
    assert control(tab)['label'] != 'Repos'
    assert tab.locator('.p-feedback-disclosure').count() == 1
    assert not tab.locator('.p-feedback').is_visible()


def test_network_retry_freezes_note_and_reuses_exact_payload(open_session):
    tab = open_session(session(exercise('A', measure='reps', reps=8)))
    attempts = []
    def receive(route):
        attempts.append(route.request.post_data_json)
        if len(attempts) == 1:
            route.abort()
        else:
            route.fulfill(status=201, json={'status': 'accepted'})
    tab.route('**/strength-report', receive)
    forward(tab)
    tab.locator('#review .p-exercise-note').fill('Note conservée')
    send(tab)
    tab.wait_for_function("document.querySelector('#status').textContent.includes('gardé')")
    assert tab.locator('#review .p-exercise-note').is_disabled()
    tab.evaluate("window.dispatchEvent(new Event('online'))")
    tab.wait_for_function("document.querySelector('#status').textContent.includes('Envoyé')")
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert attempts[0]['report']['exercises'][0]['note'] == 'Note conservée'


def test_the_session_wears_the_theme_pinned_in_the_notebook(browser, document):
    """The notebook keeps the theme choice (Auto, Light, Dark); strength used to ignore it and
    follow the phone alone: with Dark pinned, the session opened light. Both pages now read
    the same key, before the first paint, iOS status bar included."""
    tab = browser.new_page(viewport={"width": 375, "height": 812}, color_scheme="light")
    serve(tab, document)
    tab.route("**/strength-session", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"status": "ok", "session": session(exercise("A", measure="reps", reps=8))})))
    tab.goto(f"{BASE}/s#t={'T' * 32}")
    tab.wait_for_selector(".p-step--live")
    theme = "() => [document.documentElement.dataset.theme || null, " \
            "document.querySelector('meta[name=theme-color]').content, getComputedStyle(document.body).backgroundColor]"
    assert tab.evaluate(theme) == [None, "#f7f5f2", "rgb(247, 245, 242)"]
    tab.evaluate("localStorage.setItem('carnet-theme', 'dark')")
    tab.reload()
    tab.wait_for_selector(".p-step--live")
    assert tab.evaluate(theme) == ["dark", "#110e0d", "rgb(17, 14, 13)"]
    tab.evaluate("localStorage.setItem('carnet-theme', 'auto')")
    tab.reload()
    tab.wait_for_selector(".p-step--live")
    assert tab.evaluate(theme) == [None, "#f7f5f2", "rgb(247, 245, 242)"]
    tab.close()
