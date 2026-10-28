"""Le point du matin et la question d'une FC haute, sans base : un registre en mémoire.

Ce que le carnet écrit ici passe par `put_daily`, qui remplace la ligne du jour : le
carnet doit donc compléter l'état sans effacer ce qu'un autre canal y a déjà mis.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from redline.notebook_board import AnswerError, Board

PARIS = dt.timezone(dt.timedelta(hours=2))
TODAY = dt.date(2026, 10, 8)


def known(value):
    return {"state": "known", "value": value}


def ef(day: dt.date, hr: int) -> dict:
    """Une EF plate de 8 km en 48 min, complète : rien à réclamer."""
    return {"id": uuid.uuid4(), "kind": "easy", "occurred_on": day, "note": "",
            "idempotency_key": f"strava:{day}", "started_at": None,
            "measurements": {"distance_km": known(8.0), "duration_s": known(48 * 60),
                             "hr_avg_bpm": known(hr), "elevation_gain_m": known(20),
                             "rpe": known(2), "enjoyment": known(8)},
            "pains": {"injury": known(0.5)}}


class Ops:
    def __init__(self, observations=(), daily=()):
        self.observations, self.daily = list(observations), [dict(row) for row in daily]

    def observation_rows(self):
        return self.observations

    def daily_rows(self):
        return self.daily

    def put_daily(self, row, *, actor):
        assert actor == "carnet"
        self.daily = [item for item in self.daily if item["checked_on"] != row["day"]]
        self.daily.append({"checked_on": row["day"], **{key: row.get(key) for key in (
            "sleep", "life_load", "desire", "note")}})

    def strength_history(self):
        return []

    def pain_zones(self):
        return [{"slug": "injury", "label": "Injury", "active": True}]

    def session_cycle_status(self, week):
        return []


def board(ops, hour=8):
    return Board(ops, None, None, TODAY, dt.datetime.combine(TODAY, dt.time(hour), PARIS))


def test_the_morning_check_completes_the_day_without_erasing_it():
    ops = Ops(daily=[{"checked_on": TODAY, "sleep": None, "life_load": "elevee",
                      "desire": None, "note": "partiels"}])
    ask = next(item for item in board(ops).asks() if item["type"] == "daily")
    assert ask["fields"] == ["sleep"]
    board(ops).answer(ask["id"] + ":sleep", "mauvaise")
    assert ops.daily == [{"checked_on": TODAY, "sleep": "mauvaise", "life_load": "elevee",
                          "desire": None, "note": "partiels"}]
    assert not [item for item in board(ops).asks() if item["type"] == "daily"]
    with pytest.raises(AnswerError):
        board(ops).answer(ask["id"] + ":life_load", "normale")


def test_the_morning_check_is_not_asked_in_the_afternoon():
    assert not [item for item in board(Ops(), hour=15).asks() if item["type"] == "daily"]


def test_a_high_heart_rate_asks_once_and_the_answer_goes_to_that_day():
    history = [ef(TODAY - dt.timedelta(days=days), 150) for days in (14, 10, 7, 4)]
    latest = ef(TODAY - dt.timedelta(days=1), 160)
    ops = Ops([*history, latest])
    asks = [item for item in board(ops, hour=15).asks() if item["type"] == "hr"]
    assert [(item["date"], item["hr"], item["reference"]) for item in asks] == \
        [((TODAY - dt.timedelta(days=1)).isoformat(), 160, 150)]
    result = board(ops, hour=15).answer(asks[0]["id"], "sommeil")
    assert result == {"alert": None}
    day = ops.daily[0]
    assert day["checked_on"] == TODAY - dt.timedelta(days=1) and day["sleep"] == "mauvaise"
    assert day["note"] == "FC haute du 07/10 : nuit courte"
    assert not [item for item in board(ops, hour=15).asks() if item["type"] == "hr"]


def test_nothing_special_behind_a_high_heart_rate_goes_to_the_coach():
    ops = Ops([*[ef(TODAY - dt.timedelta(days=days), 150) for days in (14, 10, 7, 4)],
               ef(TODAY, 159)])
    ask = next(item for item in board(ops, hour=15).asks() if item["type"] == "hr")
    with pytest.raises(AnswerError):
        board(ops, hour=15).answer(ask["id"], "fatigue")
    alert = board(ops, hour=15).answer(ask["id"], "rien")["alert"]
    assert "159 contre 150" in alert


def test_the_weather_shows_three_hours_around_the_start_and_fails_quietly(monkeypatch):
    from redline import notebook_weather as weather
    now = dt.datetime(2026, 10, 8, 15, 20)
    assert weather.hours_for(dt.date(2026, 10, 9), "17:30", now) == [17, 18, 19]
    assert weather.hours_for(dt.date(2026, 10, 9), None, now) == [8, 12, 18]
    assert weather.hours_for(now.date(), None, now) == [16, 17, 18]
    hours = [f"2026-10-09T{hour:02d}:00" for hour in range(24)]
    replies = {weather.GEOCODE_URL: {"results": [{"name": "Vincennes", "latitude": 45.7, "longitude": 4.9}]},
               weather.FORECAST_URL: {"hourly": {"time": hours, "temperature_2m": [15.4] * 24,
                                                 "apparent_temperature": [11.8] * 24,
                                                 "precipitation_probability": [3] * 24,
                                                 "wind_speed_10m": [12.9] * 24,
                                                 "weather_code": [2] * 24, "is_day": [1] * 24},
                                      "daily": {"sunset": ["2026-10-09T19:05"]}}}
    monkeypatch.setattr(weather, "_get", lambda url, params: replies[url])
    monkeypatch.setattr(weather, "_places", {})
    monkeypatch.setattr(weather, "_forecasts", {})
    found = weather.forecast("Vincennes", dt.date(2026, 10, 9), "09:45", now)
    assert found["place"] == "Vincennes" and found["sunset"] == "19:05"
    assert found["hours"][0] == {"hour": 9, "temp": 15, "feels": 12, "rain": 3, "wind": 13,
                                 "code": 2, "day": True}

    def offline(url, params):
        raise OSError("réseau absent")
    monkeypatch.setattr(weather, "_get", offline)
    monkeypatch.setattr(weather, "_places", {})
    assert weather.forecast("Paris", dt.date(2026, 10, 9), None, now) is None
