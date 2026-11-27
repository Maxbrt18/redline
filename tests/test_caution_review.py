"""The coach's cautious decisions, with what followed, for the Sunday review."""

from __future__ import annotations

import datetime as dt

from redline.analysis import caution_review, expected_rpe, ledger
from redline.model.vocabulary import Cut, RpeSide

DAY = dt.date(2030, 3, 5)
ZONE = "shin"


def threshold(**extra):
    return {"id": "w10-thr", "title": "Seuil", "date": DAY.isoformat(), "kind": "run",
            "workout": "threshold", "status": "planned", "demanding": True, **extra}


def plan(after: dict, rule: str = "n3", before: dict | None = None) -> dict:
    change = {"session_id": "w10-thr", "fields": ["status"], "trigger": "observation:x",
              "context": "réveil", "rule": "n3",
              "restriction": {"rule": rule, "fact": "observation:2030-03-05 réveil 2"}}
    return {"week": "2030-W10", "revisions": [
        {"revision": 1, "sessions": [before or threshold()]},
        {"revision": 2, "sessions": [after], "changes": [change]}]}


def test_a_restriction_decided_by_rule_or_judgment_is_a_cautious_decision():
    [found] = caution_review.restrictions(plan(threshold(status="cancelled")))
    assert (found.day, found.title, found.cuts, found.rule) == (DAY, "Seuil", (Cut.CANCELLED,),
                                                               "n3")
    assert caution_review.restrictions(plan(threshold(status="cancelled"), "request")) == [], \
        "the athlete's own request is not the coach's caution"


def test_what_followed_is_read_from_the_ledger():
    rows = [ledger.Row(DAY - dt.timedelta(days=1), "wake-trace", pains={ZONE: 0.5}),
            ledger.Row(DAY + dt.timedelta(days=1), "wake-trace", pains={ZONE: 0.5}),
            ledger.Row(DAY + dt.timedelta(days=7), "threshold", km=10, hours=0.9, rpe=7.0,
                       pains={ZONE: 1.0})]
    [item] = caution_review.restrictions(plan(threshold(status="cancelled")))
    after = caution_review.followed(item, rows, [ZONE],
                                    lambda row: expected_rpe.Expectation(7.0, 6, 8, 5, 0.3))
    assert after.morning_above_base == 0 and after.next_on == DAY + dt.timedelta(days=7)
    assert after.next_side is RpeSide.WITHIN and after.next_pain == 1.0
    line = caution_review.render_line(item, after)
    assert line == ("- 05/03 Seuil : annulée (règle n3, fait observation:2030-03-05 réveil 2) ; "
                    "lendemain : trace à la base ; seuil suivant le 12/03, RPE 7 (dans la "
                    "fourchette), douleur 1")
    nothing = caution_review.followed(item, [], [ZONE], lambda row: None)
    assert "trace non lue" in caution_review.render_line(item, nothing)
    assert "aucune séance de seuil depuis" in caution_review.render_line(item, nothing)


def test_a_cut_dose_names_what_it_removed():
    [item] = caution_review.restrictions(plan(threshold(distance_cap_km=6.0),
                                              before=threshold(distance_cap_km=8.0)))
    assert item.cuts == (Cut.DISTANCE_CAP_LOWERED,)
    assert "plafond de distance baissé" in caution_review.render_line(
        item, caution_review.followed(item, [], [ZONE], lambda row: None))
    lengthened = plan(threshold(distance_cap_km=10.0), before=threshold(distance_cap_km=8.0))
    assert caution_review.restrictions(lengthened) == [], "lengthening is no caution"
