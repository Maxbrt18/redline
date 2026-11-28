"""A preparation proposed to the athlete, and the athlete's answer: only they take it."""

from __future__ import annotations

import datetime as dt
import json
import uuid

import pytest
import trajectory

from redline.analysis import cycle_choice
from redline.model import cycle_transitions, iso_week
from redline.model import plan_generator as generator
from redline.model import race as race_model
from redline.model.cycles import CycleVersion
from redline.model.errors import InputError
from redline.model.vocabulary import (
    CycleCause,
    CycleVersionState,
    GeneralLevel,
    HypothesisKind,
)
from redline.service.delivery import (
    EnvelopeBuilder,
    StoredDelivery,
    envelope_from_payload,
    envelope_payload,
)
from redline.service.proactive_messages import (
    LEAVE_CYCLE,
    TAKE_CYCLE,
    compose_closed_proactive,
)

TEMPLATES = trajectory.TEMPLATES
TEMPLATE = TEMPLATES.preparation_for(21.1, "road")
STARTS = generator.candidate_starts(TEMPLATE, trajectory.RACE_ON)


def _store(general_level: GeneralLevel | None = None) -> trajectory.Store:
    store = trajectory.Store(trajectory.HALF, trajectory.RACE_ON)
    for back in range(1, 5):
        trajectory.run_week(store, iso_week.shift(STARTS["balanced"], -back), 30, 14)
    if general_level is not None:
        trame = generator.general(TEMPLATES.general_at(general_level),
                                  generator.Inputs(trajectory.FIRST_WEEK, 30, 14, 34, 10, 10))
        store.cycle = CycleVersion(uuid.uuid4(), uuid.uuid4(), 1, CycleVersionState.ACTIVE,
                                   trame, CycleCause.MIGRATION, level=general_level)
    return store


def _eve(week: str) -> dt.date:
    """The Sunday before `week`."""
    return iso_week.monday(week) - dt.timedelta(days=1)


def test_each_candidate_starts_its_own_week_and_the_window_is_the_earliest():
    assert generator.window_start(TEMPLATE, trajectory.RACE_ON) == min(STARTS.values())
    assert len(set(STARTS.values())) > 1, "the candidates do not all start together"


def test_a_candidate_is_proposed_on_its_week_never_before():
    early = _eve(iso_week.shift(STARTS["balanced"], -1))
    with pytest.raises(InputError, match="starts in"):
        cycle_choice.proposal(_store(), early, "balanced")
    trame, race = cycle_choice.proposal(_store(), _eve(STARTS["balanced"]), "balanced")
    assert trame.weeks[0].label == STARTS["balanced"] and race.key == "race"
    with pytest.raises(InputError, match="no candidate bold"):
        cycle_choice.proposal(_store(), _eve(STARTS["balanced"]), "bold")


def test_a_lower_peak_may_be_chosen_never_a_higher_one():
    day = _eve(STARTS["balanced"])
    trame, _ = cycle_choice.proposal(_store(), day, "balanced")
    proposed = max(week.volume_km for week in trame.weeks)
    lower, _ = cycle_choice.proposal(_store(), day, "balanced", proposed - 3)
    assert max(week.volume_km for week in lower.weeks) == pytest.approx(proposed - 3, abs=0.1)
    assert lower.levers is not None and lower.levers.peak_km == proposed - 3
    with pytest.raises(InputError, match="never a higher one"):
        cycle_choice.proposal(_store(), day, "balanced", proposed + 1)


def test_a_proposal_made_while_the_goal_s_preparation_is_in_force_carries_what_fell():
    start = trajectory.window_start()
    lived = trajectory.simulate(short={2: 0.6, 3: 0.6}, through=iso_week.shift(start, 4))
    race = race_model.typed(lived.store)[0]
    day = trajectory.review_day(iso_week.shift(start, 5))
    assert cycle_choice.fallen(lived.store, day, race) == (HypothesisKind.VOLUME_HELD,)
    assert cycle_choice.fallen(_store(), day, race) == (), "no preparation in force"


def test_a_level_is_the_general_preparation_s_and_changes_it():
    day = _eve(trajectory.FIRST_WEEK)
    with pytest.raises(InputError, match="no general preparation in force"):
        cycle_choice.level(_store(), day, GeneralLevel.PROGRESS)
    with pytest.raises(InputError, match="already at maintain"):
        cycle_choice.level(_store(GeneralLevel.MAINTAIN), day, GeneralLevel.MAINTAIN)
    trame = cycle_choice.level(_store(GeneralLevel.MAINTAIN), day, GeneralLevel.PROGRESS)
    assert trame.template == GeneralLevel.PROGRESS and trame.weeks[0].label == trajectory.FIRST_WEEK


def test_a_plan_review_is_due_when_a_candidate_can_start_next_week():
    goal = race_model.typed(_store())[0]
    general = _store(GeneralLevel.MAINTAIN).cycle

    def due(week, **kwargs):
        flags = {"proposal_open": False, "reviewed": True, **kwargs}
        return cycle_transitions.review_week(general, goal, TEMPLATES, _eve(week), **flags)

    first = min(STARTS.values())
    assert due(iso_week.shift(first, -1), reviewed=False) is None, "the window is not open"
    assert due(first, reviewed=False) == first
    between = iso_week.shift(first, 1)
    if between not in STARTS.values():
        assert due(between) is None, "reviewed already, no new candidate starts"
        assert due(between, reviewed=False) == between, "a goal set in an open window"
    assert due(STARTS["balanced"]) == STARTS["balanced"]
    assert due(STARTS["balanced"], proposal_open=True) is None
    after = iso_week.shift(max(STARTS.values()), 2)
    assert due(after) is None, "reviewed already, every candidate's start has passed"
    assert due(after, returned=True) == after, "a rebuild that ends reopens the review"
    trame, _ = cycle_choice.proposal(_store(), _eve(STARTS["balanced"]), "balanced")
    preparation = CycleVersion(uuid.uuid4(), uuid.uuid4(), 1, CycleVersionState.ACTIVE, trame,
                               CycleCause.CREATE, race_key="race")
    assert cycle_transitions.review_week(preparation, goal, TEMPLATES, _eve(STARTS["balanced"]),
                                         proposal_open=False, reviewed=True) is None, \
        "a preparation in force is the window taken"


def test_the_proposal_message_carries_its_summary_and_the_two_answers():
    version = str(uuid.uuid4())
    detail = json.dumps({"version_id": version, "race": "Semi", "candidate": "balanced",
                         "starts_on": "2031-W02", "weeks": 13, "peak_km": 36.0,
                         "long_run_km": 12.0, "flagships": 2, "risks": 1,
                         "expires_on": "2031-01-11"})
    rendered = compose_closed_proactive("cycle_proposal", detail)
    assert rendered is not None
    assert "préparation pour « Semi » : 13 semaines à partir du lundi 06/01" in rendered.text
    assert "2 séances phares" in rendered.text and "jusqu’au 11/01" in rendered.text
    assert rendered.replies == (("Je la prends", TAKE_CYCLE + version),
                                ("Pas maintenant", LEAVE_CYCLE + version))
    assert compose_closed_proactive("cycle_proposal", '{"race": "Semi"}') is None


def test_an_envelope_keeps_its_replies_through_storage():
    envelope = EnvelopeBuilder("4242", "proactive:p").text(
        "Je te propose…", replies=(("Je la prends", "cycle-take:x"),)).build()
    stored = StoredDelivery("proactive:p", "4242", "pending", envelope_payload(envelope))
    assert envelope_from_payload(stored).parts == envelope.parts
    assert envelope.parts[0].mode == "text", "a text with answers goes out whole"
