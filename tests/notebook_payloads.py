"""What the notebook API answers in the browser tests.

tests/test_notebook_contract.py reads each payload against `redline.model.notebook_views`,
so a key the service renames breaks a fixture too.
"""

from __future__ import annotations

CURRENT_WEEK = "2026-W41"
PREVIOUS_WEEK = "2026-W40"
KNEE = {"zone": "knee", "label": "Genou"}


def ef(identifier, date, status="planned", fact=None):
    """An easy run of the plan, carrying the fact of its observation once done."""
    return {
        "id": identifier,
        "date": date,
        "title": "EF, permis accompagné",
        "kind": "run",
        "workout": "easy",
        "status": status,
        "start_time": None,
        "location": None,
        "note": "Tu choisis la durée.",
        "planned": ["≤55 min", "RPE 2-3", "Z1-Z2, 130-157 bpm"],
        "fact": fact,
        "entry_id": None,
        "transition": None,
        "change": None,
        "targets": {"duration_cap_min": 55, "rpe_min": 2, "rpe_max": 3, "hr_cap_bpm": 157},
    }


def strip_day(day, letter):
    return {
        "date": f"2026-10-{day:02d}",
        "letter": letter,
        "abbr": "easy",
        "today": day == 9,
        "done_km": 8.35 if day == 6 else None,
        "planned_km": 16 if day == 11 else None,
        "planned_label": "≤55′" if day == 9 else None,
        "long": day == 11,
        "rest": day == 5,
    }


def past_week(number, monday, km_done, km_target):
    return {
        "week": f"2026-W{number}",
        "monday": monday,
        "km_done": km_done,
        "km_target": km_target,
        "review": None,
    }


ENTRY = {
    "id": "one",
    "kind": "monthly",
    "title": "Monthly · 2026-09",
    "period": "2026-09",
    "occurred_on": "2026-09-01",
    "created_at": "2026-09-01 08:00:00+00:00",
    "origin": "delivery",
    "has_image": True,
}

STRENGTH = {
    "id": "w41-ven-renfo",
    "date": "2026-10-09",
    "title": "Renfo, séance unique",
    "kind": "strength",
    "workout": "strength",
    "status": "planned",
    "start_time": None,
    "location": None,
    "note": None,
    "planned": [],
    "fact": None,
    "entry_id": None,
    "transition": None,
    "change": None,
    "targets": {},
}

DONE = ef("w41-mar-ef", "2026-10-06", "done", {
    "observation_id": "6b0f3c1e-1d1a-4a39-9e0d-5c1b0b5d2f11",
    "kind": "easy",
    "date": "2026-10-06",
    "km": 8.35,
    "duration_min": 48,
    "hr_avg": 162,
    "rpe": 3,
    "pains": [{**KNEE, "value": 0.5}],
    "enjoyment": 8,
    "dplus": 20,
    "started_at": "2026-10-06T07:10:00+02:00",
    "over": ["hr_avg"],
})

DETAIL = {
    "id": DONE["fact"]["observation_id"],
    "date": "2026-10-06",
    "kind": "easy",
    "title": DONE["title"],
    "workout": "easy",
    "quality": False,
    "strava_id": None,
    "fact": {**DONE["fact"], "moving_s": 2880, "hr_max": None, "cadence": None, "reserve": None},
    "plan": {"targets": DONE["targets"], "note": None},
    "ask": None,
    "reserve_ask": None,
    "wake": {"zones": [{**KNEE, "before": None, "after": None}], "after_due": False},
    "phases": None,
    "reps": None,
    "reading": None,
    "km": None,
    "stops": None,
    "drift": None,
    "series": None,
    "hr_bounds": None,
    "zones": None,
    "previous": None,
    "weather": None,
    "gear": None,
    "week": {
        "week": CURRENT_WEEK,
        "current": True,
        "km_done": 14.06,
        "km_target": 41,
        "runs_done": 2,
        "runs_planned": 5,
        "next": None,
    },
}

TOTALS = {
    "km_done": 14.06,
    "km_target": 41,
    "runs_done": 2,
    "runs_planned": 5,
    "dplus": 27,
    "strength_done": 1,
    "strength_planned": 2,
    "load": 25,
    "pain_peaks": [{**KNEE, "peak": 1.0}],
}

STRIP = [strip_day(day, letter)
         for day, letter in zip(range(5, 12), "LMMJVSD", strict=True)]

TODAY = {
    "date": "2026-10-09",
    "week": CURRENT_WEEK,
    "plan": {"status": "valid", "message": ""},
    "sessions": [ef("w41-ven-ef", "2026-10-09"), STRENGTH],
    "extra": [],
    "tomorrow": {"date": "2026-10-10", "plan": "valid", "sessions": [], "next": []},
    "ahead": [],
    "strip": STRIP,
    "totals": TOTALS,
    "asks": [{
        "id": "cell:6b0f3c1e-1d1a-4a39-9e0d-5c1b0b5d2f10",
        "type": "cells",
        "date": "2026-10-07",
        "kind": "easy",
        "km": 5.71,
        "fields": ["pain:knee", "rpe"],
        "labels": {"pain:knee": "Genou"},
    }],
    "pain": {"cap": 2.0, "zones": [{**KNEE, "weeks": [
        {"week": PREVIOUS_WEEK, "peak": 1.5},
        {"week": CURRENT_WEEK, "peak": 1.0},
    ]}]},
    "race": {
        "name": "TargetRace",
        "date": "2026-11-26",
        "days": 50,
        "start_time": "09:00",
        "distance_km": 45,
    },
    "strength": {
        "status": "ready",
        "signature": "old",
        "revision": 3,
        "date": "2026-10-09",
        "session": {
            "title": "Renfo A",
            "exercises": [{"name": "Mollets", "dose": {"text": "3 × 12"}}],
        },
    },
}

WEEK = {
    "week": CURRENT_WEEK,
    "monday": "2026-10-05",
    "current": CURRENT_WEEK,
    "plan": {"status": "valid", "message": "", "revision": 3},
    "sessions": [DONE, ef("w41-ven-ef", "2026-10-09"), STRENGTH],
    "extra": [],
    "totals": TOTALS,
    "strip": STRIP,
    "review": None,
    "cards": [ENTRY],
    "next": None,
}

INJURY = {
    **KNEE,
    "zones": [KNEE],
    "cap": 2.0,
    "start": "2026-09-28",
    "today": "2026-10-09",
    "week": CURRENT_WEEK,
    "points": [
        {"date": "2026-10-03", "pain": 1.5, "kind": "long", "long": True},
        {"date": "2026-10-06", "pain": 0.5, "kind": "easy", "long": False},
        {"date": "2026-10-07", "pain": 2.5, "kind": "easy", "long": False},
    ],
    "wakes": [{"date": "2026-10-08", "pain": 0.0}],
    "weeks": [
        {"week": PREVIOUS_WEEK, "peak": 1.5, "n": 1, "wake": None},
        {"week": CURRENT_WEEK, "peak": 2.5, "n": 2, "wake": 0.0},
    ],
    "missing": ["2026-10-07"],
    "scale": [{"value": 1, "text": "Pas gênante."}],
}

WEEKS = {"weeks": [
    past_week(40, "2026-09-28", 38.2, 40.0),
    past_week(39, "2026-09-21", 35.0, 36.0),
    past_week(38, "2026-09-14", 31.5, 34.0),
    past_week(37, "2026-09-07", 29.0, 30.0),
    past_week(36, "2026-08-31", 27.4, None),
]}

#: A fictional route: 42 km, two aid stations, timed at the chosen pace of 5:30/km.
RACE_PLAN = {
    "source": "ign", "length_km": 42.2, "dplus_m": 905, "dminus_m": 910,
    "dplus_band_m": [890, 921],
    "profile": [[0, 320], [5, 410], [10, 620], [15, 540], [20, 700], [25, 480], [30, 520],
                [35, 390], [40, 350], [42.2, 330]],
    "highest": [700, 20.0],
    "stretches": [{"to_km": 14.0, "dplus_m": 340, "dminus_m": 120},
                  {"to_km": 28.0, "dplus_m": 380, "dminus_m": 400},
                  {"to_km": 42.2, "dplus_m": 185, "dminus_m": 390}],
    "passages": [{"km": 14.0, "name": "Le Col", "hours": 1.42, "running_hours": 1.42},
                 {"km": 28.0, "name": "Le Lac", "hours": 2.85, "running_hours": 1.39},
                 {"km": 42.2, "name": "", "hours": 4.21, "running_hours": 1.32}],
    "reference_pace_s": 330.0, "pace_s_per_km": 330.0, "stop_minutes": 2.5, "carbs_g_h": 60,
    "model_error": [0.04, 0.085], "start": "2026-11-26T09:00",
}

RACE = {
    "race": TODAY["race"] | {"target": "<4 h 30"},
    "plan": RACE_PLAN,
    "longest": {
        "km": 15.84,
        "planned_km": 16,
        "planned_on": "2026-10-11",
        "weeks": [
            {"week": PREVIOUS_WEEK, "km": 15.84, "volume": 39.66, "target": 40.0, "open": False},
            {"week": CURRENT_WEEK, "km": 8.35, "volume": 14.06, "target": 41.0, "open": True},
        ],
    },
    "milestones": [
        {"date": "2026-10-04", "label": "Accès vélo", "kind": "deadline", "overdue": True},
        {"date": "2026-10-14", "label": "Frontale ≥400 lm", "kind": "deadline", "overdue": False},
        {"date": "2026-11-26", "label": "Départ", "kind": "race", "start_time": "09:00"},
    ],
    "capabilities": [{
        "label": "Genou",
        "icon": "run",
        "score": 3,
        "scale": 5,
        "number": 5,
        "evaluated_on": "2026-10-04",
    }],
    "archives": [ENTRY],
}


#: The plan screen, with the forecast at each passage.
RACE_PLAN_VIEW = {
    "plan": RACE_PLAN,
    "weather": [[{"time": f"2026-11-26T{hour:02d}:00", "temp": 3 + hour - 9, "feels": hour - 9,
                  "rain": 10, "wind": 14, "code": 2, "day": True} for hour in range(9, 16)]
                for _ in range(3)],
}
