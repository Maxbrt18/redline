"""The notebook dashboard: today's session, the week, the race.

Everything is COMPUTED at read time from the owners: the active program revision
carries the intent, the PostgreSQL ledger carries the fact, and the `redline ledger`
functions do the counting (volume, pain, gauge, trajectory). Nothing is stored here: a
displayed number without a computation would be a copy, and a copy drifts.

Prescription rules stay those of the service: an absent program prescribes nothing, a
due dated check withholds its sessions, a revision to re-arbitrate is stated. The
notebook has no fallback on the macro plan.

Writes: only the feelings the ledger already requests (RPE, pain, enjoyment, wake
trace), through the same methods as `redline ledger-complete`, and only if the request is
still open at write time.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from redline.analysis import ledger, signals
from redline.coach import dates
from redline.coach.fr import notebook as notebook_text
from redline.coach.fr import session_labels
from redline.coach.fr import weather as weather_labels
from redline.coach.fr.notebook import HR_REASONS, PAIN_SCALE
from redline.model import (
    debrief,
    local_time,
    program,
    rulebook,
    session_cycle,
    session_kinds,
    transitions,
)
from redline.model import ledger as ledger_model
from redline.model import notebook_views as views
from redline.model.activity import Activity
from redline.model.errors import InputError
from redline.model.instance import Instance, derived_label
from redline.model.vocabulary import HrDoubt, HrReason, LifeLoad, MorningTrace, Sleep

from . import session_detail
from .plans import PlanStatus, blocked_session_ids

ACTOR = "notebook"
MOVING_TYPES = session_kinds.MOVING
#: The morning check-in: the declarative daily state, with the values of `redline fact-write daily`.
DAILY_FIELDS = {"sleep": tuple(Sleep), "life_load": tuple(LifeLoad)}
#: A high heart rate at comparable pace asks its question for two days; the answer goes
#: to the daily state of the run's day.
HR_ASK_DAYS = 2
PLAN_MESSAGES = {PlanStatus[name]: text for name, text in notebook_text.PLAN_MESSAGES.items()}


class AnswerError(InputError):
    """A refused answer: a closed request, or a value off the scale or unknown."""


def week_label(day: dt.date) -> str:
    return day.strftime("%G-W%V")


def week_monday(week: str) -> dt.date:
    return dt.date.fromisocalendar(int(week[:4]), int(week[-2:]), 1)


def _known(observation: dict, metric: str) -> float | None:
    measure = (observation.get("measurements") or {}).get(metric) or {}
    if measure.get("state") != "known" or measure.get("value") is None:
        return None
    return float(measure["value"])


def _activity(intake: dict | None) -> Activity | None:
    """The activity a Strava capture holds; None without a capture or after a failed one."""
    stored = (intake or {}).get("distilled")
    return Activity.from_json(stored) if stored else None


def known_pains(observation: dict, labels: dict[str, str]) -> list[views.PainReading]:
    """The known pain of each zone, in declared zone order."""
    pains = observation.get("pains") or {}
    ordered: list[str] = [zone for zone in labels if zone in pains] + sorted(set(pains) - set(labels))
    return [{"zone": zone, "label": labels.get(zone, zone),
             "value": float(pains[zone]["value"])} for zone in ordered
            if pains[zone].get("state") == "known" and pains[zone].get("value") is not None]


def observation_fact(observation: dict, session: dict | None = None,
                     labels: dict[str, str] | None = None) -> views.Fact:
    """The known measures of an observation, and the typed caps they exceed."""
    duration = _known(observation, "duration_s")
    fact: views.Fact = {
        "observation_id": str(observation["id"]), "kind": observation["kind"],
        "date": observation["occurred_on"].isoformat(),
        "km": _known(observation, "distance_km"),
        "duration_min": round(duration / 60) if duration is not None else None,
        # Without the armband, the average heart rate is worthless: neither shown nor compared.
        "hr_avg": (None if observation.get("hr_doubt") == HrDoubt.NO_SENSOR
                   else _known(observation, "hr_avg_bpm")),
        "rpe": _known(observation, "rpe"),
        "pains": known_pains(observation, labels or {}),
        "enjoyment": _known(observation, "enjoyment"),
        "dplus": _known(observation, "elevation_gain_m"),
        "started_at": (observation["started_at"].astimezone(local_time.zone())
                       .isoformat() if observation.get("started_at") else None),
        "over": [],
    }
    if session:
        fact["over"] = [key for key, value, cap in (
            ("hr_avg", fact["hr_avg"], "hr_cap_bpm"), ("rpe", fact["rpe"], "rpe_max"),
            ("duration_min", fact["duration_min"], "duration_cap_min"),
            ("km", fact["km"], "distance_cap_km"))
            if value is not None and session.get(cap) is not None and value > float(session[cap])]
    return fact


def _scale(metric: str, value: Any) -> float:
    """The athlete's scales: integer RPE and enjoyment, pain in half points."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AnswerError(notebook_text.NUMBER_EXPECTED)
    number = float(value)
    if not 0 <= number <= 10:
        raise AnswerError(notebook_text.OUT_OF_SCALE)
    step = 2 if metric == "pain" else 1
    if number * step != int(number * step):
        raise AnswerError(notebook_text.HALF_POINTS if metric == "pain"
                          else notebook_text.INTEGER_EXPECTED)
    return number


def _fr(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def _targets(session: dict) -> views.Targets:
    """The limits the plan sets for this session."""
    return cast("views.Targets", {key: session[key] for key in views.Targets.__optional_keys__
                                  if session.get(key) is not None})


def _distance(session: views.SessionItem) -> float | None:
    """The distance a session aims at: its target, else its cap."""
    targets = session["targets"]
    return targets.get("distance_target_km") or targets.get("distance_cap_km")


def _planned(session: dict) -> list[str]:
    """What is binding, short and plain, from the typed fields only."""
    parts = []
    target, cap = session.get("distance_target_km"), session.get("distance_cap_km")
    if target is not None:
        parts.append(f"{_fr(target)} km" + (f" (≤{_fr(cap)})" if cap is not None and cap != target else ""))
    elif cap is not None:
        parts.append(f"≤{_fr(cap)} km")
    elif session.get("duration_cap_min"):
        parts.append(f"≤{session['duration_cap_min']} min")
    if session.get("rpe_max") is not None:
        low = session.get("rpe_min")
        parts.append(f"RPE {low}-{session['rpe_max']}" if low is not None and low != session["rpe_max"]
                     else f"RPE ≤{session['rpe_max']}")
    if session.get("hr_zone"):
        parts.append(session["hr_zone"])
    if session.get("cadence_target"):
        parts.append(f"cadence ~{session['cadence_target']}")
    if session.get("carbs_target_g_h") is not None:
        parts.append(notebook_text.CARBS.format(grams=session["carbs_target_g_h"]))
    return parts


@dataclass
class Week:
    """A week read once: its revision, its cycles, its ledger."""

    label: str
    status: PlanStatus
    revision: dict | None
    held: frozenset[str]
    links: dict[str, dict]
    observations: list[dict]          # the week's raw observations
    rows: list[ledger.Row]            # the week's computable ledger
    strength: list[dict]              # the week's strength reports

    @property
    def days(self) -> list[dt.date]:
        monday = week_monday(self.label)
        return [monday + dt.timedelta(days=offset) for offset in range(7)]


class Board:
    """The three notebook screens, computed for a date."""

    def __init__(self, ops, repo, root: Path, today: dt.date, now: dt.datetime,
                 instance: Instance | None = None):
        self.ops, self.repo, self.root = ops, repo, root
        self.instance = instance
        self._zones: list[ledger.PainZone] | None = None
        self.today, self.now = today, now
        self._observations: list[dict] | None = None
        self._rows: list[ledger.Row] | None = None
        self._strength: list[dict] | None = None

    @property
    def zones(self) -> list[ledger.PainZone]:
        """The active monitored zones; none without a ledger or a declaration."""
        if self._zones is None:
            self._zones = ledger.pain_zones(self.ops) if self.ops is not None else []
        return self._zones

    @property
    def zone_labels(self) -> dict[str, str]:
        return {zone.slug: zone.label for zone in self.zones}

    def _capability_label(self, slug: str) -> str:
        if self.instance is None:
            return derived_label(slug)
        return self.instance.capability_label(slug)

    def _capability_icon(self, slug: str) -> str:
        return "run" if self.instance is None else self.instance.capability_icon(slug)

    # ------------------------------------------------------------ lectures
    @property
    def observations(self) -> list[dict]:
        if self._observations is None:
            self._observations = self.ops.observation_rows()
        return self._observations

    @property
    def rows(self) -> list[ledger.Row]:
        if self._rows is None:
            self._rows = ledger.parse_ledger(self.ops)
        return self._rows

    @property
    def strength_history(self) -> list[dict]:
        if self._strength is None:
            self._strength = self.ops.strength_history()
        return self._strength

    def week(self, label: str) -> Week:
        status = self.repo.validate_weekly_plan(label)
        context = self.repo.weekly_plan_context(label) if status is not PlanStatus.INVALID else ""
        revision = json.loads(context) if context else None
        held = blocked_session_ids(context, self.today.isoformat()) if context else frozenset()
        return Week(
            label=label, status=status if revision else PlanStatus.INVALID,
            revision=revision, held=held, links=self.ops.week_session_links(label),
            observations=[row for row in self.observations
                          if week_label(row["occurred_on"]) == label],
            rows=[row for row in self.rows if row.week == label],
            strength=[row for row in self.strength_history
                      if week_label(row["performed_on"]) == label])

    # ------------------------------------------------------------ sessions
    def session_item(self, week: Week, session: dict,
                     entries: dict[str, str]) -> views.SessionItem:
        status = session["status"]
        if status == "planned" and session["id"] in week.held:
            status = "held"
        item: views.SessionItem = {
            "id": session["id"], "date": session["date"], "title": session["title"],
            "kind": session["kind"], "workout": session["workout"], "status": status,
            "start_time": session.get("start_time"), "location": session.get("location_label"),
            "note": session.get("note") or (session.get("strength") or {}).get("note"),
            "planned": _planned(session), "fact": None, "entry_id": None,
            "transition": transitions.label(session["transition"])
            if session.get("transition") else None,
            "targets": _targets(session), "change": None,
        }
        # A change's context is written for the coach; the athlete reads what moved
        # and when, the session instruction already says the rest.
        change = next((c for c in (week.revision or {}).get("changes") or []
                       if c.get("session_id") == session["id"]), None)
        if change:
            stamp = dt.datetime.fromisoformat((week.revision or {})["computed_at"]).astimezone(
                local_time.zone())
            moved = "date" in change.get("fields", [])
            item["change"] = (notebook_text.MOVED if moved
                              else notebook_text.CHANGED).format(day=stamp)
        link = week.links.get(session["id"]) or {}
        if link.get("observation_id"):
            observation = next((row for row in week.observations
                                if row["id"] == link["observation_id"]), None)
            if observation:
                item["fact"] = observation_fact(observation, session, self.zone_labels)
        if session["kind"] == "strength":
            report = next((row for row in week.strength
                           if row["performed_on"].isoformat() == session["date"]), None)
            if report:
                item["fact"] = {"duration_min": report.get("duration_minutes"),
                                "pains": [{"zone": zone, "label": self.zone_labels.get(zone, zone),
                                           "value": float(moments["after"])}
                                          for zone, moments in (report.get("pains") or {}).items()
                                          if moments.get("after") is not None],
                                "over": []}
            item["entry_id"] = entries.get(session["date"])
        return item

    def sessions(self, week: Week,
                 entries: dict[str, str] | None = None) -> list[views.SessionItem]:
        if not week.revision:
            return []
        order = {"run": 0, "bike": 1, "hike": 1, "test": 2, "strength": 3, "rest": 4}
        sessions = sorted(week.revision["sessions"],
                          key=lambda s: (s["date"], order.get(s["kind"], 5)))
        return [self.session_item(week, session, entries or {}) for session in sessions]

    def unplanned(self, week: Week) -> list[views.UnplannedFact]:
        """What was run or walked with no program session to carry it."""
        linked = {link["observation_id"] for link in week.links.values()
                  if link.get("observation_id")}
        return [{**observation_fact(row, labels=self.zone_labels), "title": row["kind"]}
                for row in week.observations
                if row["kind"] in MOVING_TYPES and row["id"] not in linked]

    # ------------------------------------------------------------ aggregates
    def strip(self, week: Week, sessions: list[views.SessionItem]) -> list[views.StripDay]:
        days: list[views.StripDay] = []
        for day in week.days:
            iso = day.isoformat()
            same = [s for s in sessions if s["date"] == iso and s["status"] != "cancelled"]
            done = sum(row.km or 0 for row in week.rows
                       if row.date == day and row.type in ledger.RUN_TYPES)
            pending = [s for s in same if s["kind"] == "run" and s["status"] != "done"]
            planned_km: float | None = None
            planned_label: str | None = None
            if pending and not done:
                targets = pending[0]["targets"]
                planned_km = _distance(pending[0])
                if planned_km is None and targets.get("duration_cap_min"):
                    planned_label = f"≤{targets['duration_cap_min']}′"
            abbr = "+".join(dict.fromkeys(
                session_labels.short(s["workout"]) for s in same)) or ""
            days.append({
                "date": iso, "letter": dates.weekday(day, "narrow"), "abbr": abbr,
                "today": day == self.today, "done_km": round(done, 2) or None,
                "planned_km": planned_km, "planned_label": planned_label,
                "long": any(s["workout"] == session_kinds.LONG for s in same),
                "rest": not any(s["kind"] != "rest" for s in same),
            })
        return days

    def totals(self, week: Week, sessions: list[views.SessionItem]) -> views.Totals:
        volume = ledger.week_volume(week.rows)
        live = [s for s in sessions if s["status"] != "cancelled"]
        runs = [s for s in live if s["kind"] == "run"]
        strength = [s for s in live if s["kind"] == "strength"]
        instruments = ledger.load_instruments(self.ops).get(week.label, ledger.Instruments())
        _, level, _ = ledger.equivalent_hours(week.rows, instruments,
                                              program.rpe_targets(self.root))
        return {
            "km_done": round(volume.km_run, 2),
            "km_target": (week.revision or {}).get("volume_target_km"),
            "runs_done": volume.runs, "runs_planned": len(runs),
            "dplus": volume.dplus,
            "strength_done": sum(1 for s in strength if s["status"] == "done"),
            "strength_planned": len(strength),
            "load": level,
            "pain_peaks": [
                {"zone": zone.slug, "label": zone.label,
                 "peak": max((value for row in week.rows
                              if (value := row.pain_at(zone.slug)) is not None
                              and row.type in ledger_model.EFFORT_TYPES), default=None)}
                for zone in self.zones],
        }

    def pain(self) -> views.PainTrend:
        return {"cap": ledger.ESCULIER_CAP, "zones": [
            {"zone": zone.slug, "label": zone.label,
             "weeks": [{"week": item.week, "peak": item.effort.peak}
                       for item in ledger.pain_trend(self.rows, 4, zone.slug)]}
            for zone in self.zones]}

    def injury_view(self, zone: str | None = None, weeks: int = 5) -> views.InjuryView | None:
        """The notebook Pain screen: each rated session and each wake-up over the recent
        calendar weeks, the peak per week, and the wake-ups described without a rating.
        Facts, never a verdict (under-reporting bias).

        One zone at a time: with no `zone`, the first declared one. None when the
        instance monitors none, or the requested zone does not exist."""
        chosen = next((item for item in self.zones if zone in (None, item.slug)), None)
        if chosen is None:
            return None
        slug = chosen.slug
        start = week_monday(week_label(self.today)) - dt.timedelta(weeks=weeks - 1)
        rows = [row for row in self.rows
                if start <= row.date <= self.today and row.pain_at(slug) is not None]
        efforts = [row for row in rows if row.type in ledger_model.EFFORT_TYPES]
        wakes = [row for row in rows if row.type == session_kinds.WAKE_TRACE]
        table: list[views.InjuryWeek] = []
        for index in range(weeks):
            label = week_label(start + dt.timedelta(weeks=index))
            effort = [row.pains[slug] for row in efforts if row.week == label]
            wake = [row.pains[slug] for row in wakes if row.week == label]
            table.append({"week": label, "peak": max(effort, default=None), "n": len(effort),
                          "wake": max(wake, default=None)})
        column = ledger_model.pain_column(slug)
        missing = sorted({obs["occurred_on"] for obs in self.observations
                          if obs["kind"] == session_kinds.WAKE_TRACE and obs["occurred_on"] <= self.today
                          and self.today - obs["occurred_on"] < dt.timedelta(days=14)
                          and column in ledger_model.missing_observation_fields(obs)},
                         reverse=True)
        return {
            "zone": slug, "label": chosen.label,
            "zones": [{"zone": item.slug, "label": item.label} for item in self.zones],
            "cap": ledger.ESCULIER_CAP, "start": start.isoformat(), "today": self.today.isoformat(),
            "week": week_label(self.today),
            "points": [{"date": row.date.isoformat(), "pain": row.pains[slug], "kind": row.type,
                        "long": row.type == session_kinds.LONG} for row in efforts],
            "wakes": [{"date": row.date.isoformat(), "pain": row.pains[slug]} for row in wakes],
            "weeks": table, "missing": [day.isoformat() for day in missing],
            "scale": PAIN_SCALE,
        }

    def race(self) -> views.Race | None:
        race_on = ledger.next_race_date(self.ops, self.today)
        if race_on is None:
            return None
        row = next(row for row in self.ops.deadline_rows()
                   if row.get("kind") == "race" and row["due_on"] == race_on
                   and row["status"] != "done")
        context = row.get("context") or {}
        return cast("views.Race", {
            "name": row.get("athlete_label") or "Course objectif",
            "date": race_on.isoformat(), "days": (race_on - self.today).days,
            **{key: context[key] for key in views.Race.__optional_keys__
               if context.get(key) is not None}})

    # ------------------------------------------------------------ demandes
    def asks(self) -> list[views.Ask]:
        """The feelings the ledger requests and the athlete can give in one tap."""
        asks: list[views.Ask] = []
        yesterday = self.today - dt.timedelta(days=1)
        # The trace is asked every morning, rest days included: without it, nothing
        # says whether the pain subsides between two sessions.
        effort_yesterday = any(row.date == yesterday and row.type in ledger_model.EFFORT_TYPES
                               for row in self.rows)
        labels = self.zone_labels
        woke_today = False
        for row in self.observations:
            day = row["occurred_on"]
            if row["kind"] == session_kinds.WAKE_TRACE and day == self.today:
                woke_today = True
            if day > self.today or day < ledger_model.completeness_from() \
                    or self.today - day >= ledger_model.COMPLETION_WINDOW:
                continue
            missing = ledger_model.missing_observation_fields(row)
            if row["kind"] == session_kinds.WAKE_TRACE:
                if day == self.today and self._morning:
                    asks.extend({"id": f"wake-cell:{row['id']}:{zone}", "type": "wake",
                                 "zone": zone, "label": labels.get(zone, zone),
                                 "date": day.isoformat(), "after_effort": effort_yesterday}
                                for zone in labels
                                if ledger_model.pain_column(zone) in missing)
                continue
            fields = [f"pain:{zone}" for zone in labels
                      if ledger_model.pain_column(zone) in missing]
            fields += [column for column in ("rpe", "enjoyment") if column in missing]
            if fields:
                asks.append({"id": f"cell:{row['id']}", "type": "cells",
                             "date": day.isoformat(), "kind": row["kind"],
                             "km": _known(row, "distance_km"), "fields": fields,
                             "labels": {f"pain:{zone}": label for zone, label in labels.items()}})
        daily = self.daily(self.today)
        missing = [field for field in DAILY_FIELDS if not daily.get(field)]
        if missing and self._morning:
            asks.append({"id": f"daily:{self.today.isoformat()}", "type": "daily",
                         "date": self.today.isoformat(), "fields": missing})
        asks.extend(self.hr_asks())
        if not woke_today and self._morning:
            asks[0:0] = [{"id": f"wake:{self.today.isoformat()}:{zone}", "type": "wake",
                          "zone": zone, "label": label, "date": self.today.isoformat(),
                          "after_effort": effort_yesterday} for zone, label in labels.items()]
        return asks

    def daily(self, day: dt.date) -> dict:
        return next((row for row in self.ops.daily_rows() if row["checked_on"] == day), {})

    @staticmethod
    def _hr_marker(day: dt.date) -> str:
        return notebook_text.HR_MARKER.format(day=day)

    def hr_asks(self) -> list[views.HrAsk]:
        """The latest recent easy run whose heart rate exceeds the same-pace reference:
        one question at a time, on the freshest run in memory."""
        asks: list[views.HrAsk] = []
        for drift in signals.hr_at_pace(self.rows):
            if drift.delta < signals.HR_DRIFT_BPM or not \
                    0 <= (self.today - drift.date).days < HR_ASK_DAYS:
                continue
            if self._hr_marker(drift.date) in (self.daily(drift.date).get("note") or ""):
                continue
            asks.append({"id": f"hr:{drift.date.isoformat()}", "type": "hr",
                         "date": drift.date.isoformat(), "hr": round(drift.hr),
                         "reference": round(drift.reference), "n": drift.n,
                         "pace_s": round(drift.pace_s)})
        return asks[-1:]

    def _put_daily(self, day: dt.date, **values: str) -> None:
        """Complete the daily state without erasing what is already there."""
        row = self.daily(day)
        merged = {field: row.get(field) for field in ("sleep", "life_load", "desire")}
        note = row.get("note") or ""
        if values.get("note"):
            note = f"{note} · {values.pop('note')}" if note else values.pop("note")
        merged.update(values)
        self.ops.put_daily({"day": day, **merged, "note": note}, actor=ACTOR)

    @property
    def _morning(self) -> bool:
        return self.now.time() < rulebook.current().parameters.wake_trace_until

    def answer(self, ask_id: str, value: Any) -> dict:
        """Write ONE answer if the request is still open. Return the alert, if any."""
        if ask_id.startswith("reserve:"):
            return self._answer_reserve(ask_id, value)
        ask = next((item for item in self.asks()
                    if ask_id == item["id"] or ask_id.startswith(item["id"] + ":")), None)
        if ask is None:
            raise AnswerError(notebook_text.QUESTION_CLOSED)
        if ask["type"] == "daily":
            field = ask_id.rsplit(":", 1)[-1]
            if field not in ask["fields"] or value not in DAILY_FIELDS[field]:
                raise AnswerError(notebook_text.UNEXPECTED_ANSWER)
            self._put_daily(self.today, **{field: value})
            return {"alert": None}
        if ask["type"] == "hr":
            if value not in HR_REASONS:
                raise AnswerError(notebook_text.UNEXPECTED_ANSWER)
            day = dt.date.fromisoformat(ask["date"])
            note = f"{self._hr_marker(day)} {HR_REASONS[value]}"
            if value == HrReason.SLEEP:
                self._put_daily(day, sleep=Sleep.POOR, note=note)
            else:
                self._put_daily(day, note=note)
            alert = None
            if value == HrReason.NONE:
                alert = notebook_text.ALERT_HR.format(day=day, hr=ask["hr"],
                                                      reference=ask["reference"])
            return {"alert": alert}
        if ask["type"] == "wake":
            if value not in session_cycle.MORNING_TRACES:
                raise AnswerError(notebook_text.TRACE_EXPECTED)
            kind, _, key = ask["id"].partition(":")
            trace = session_cycle.MORNING_TRACE_VALUES[value]
            if kind == "wake-cell":
                self.ops.complete_measurement(uuid.UUID(key.rsplit(":", 1)[0]), "pain", trace,
                                              zone=ask["zone"], actor=ACTOR)
            else:
                # The first answered zone creates the wake-up; the others stay
                # `pending` there and are completed afterwards, one zone per tap.
                self.ops.add_observation({
                    "occurred_on": self.today, "kind": session_kinds.WAKE_TRACE,
                    "idempotency_key": f"notebook:wake-trace:{self.today.isoformat()}",
                    "note": notebook_text.WAKE_TRACE_NOTE, "source": ACTOR,
                    "measurements": {},
                    "pains": {ask["zone"]: {"state": "known", "value": trace}},
                }, actor=ACTOR)
            alert = None
            if value == MorningTrace.CLEAR:
                where = notebook_text.ZONE.format(label=ask["label"]) if len(self.zones) > 1 else ""
                alert = notebook_text.ALERT_WAKE_TRACE.format(where=where, today=self.today)
            return {"alert": alert}
        field = ask_id[len(ask["id"]) + 1:]
        if field not in ask["fields"]:
            raise AnswerError(notebook_text.ALREADY_FILLED)
        metric, _, zone = field.partition(":")
        number = _scale(metric, value)
        observation_id = uuid.UUID(ask["id"].split(":", 1)[1])
        self.ops.complete_measurement(observation_id, metric, number, zone=zone or None,
                                      actor=ACTOR)
        alert = None
        if metric == "pain" and number > ledger.ESCULIER_CAP:
            day = dt.date.fromisoformat(ask["date"])
            where = (notebook_text.ZONE.format(label=ask["labels"][field])
                     if len(self.zones) > 1 else "")
            alert = notebook_text.ALERT_PAIN.format(value=number, where=where, kind=ask["kind"],
                                                    day=day, cap=ledger.ESCULIER_CAP)
        # The run's feelings are complete: its debrief no longer has to wait.
        settled = (self.ops.strava_activity_for_observation(observation_id)
                   if set(ask["fields"]) == {field} else None)
        return {"alert": alert, "activity": settled}

    # ------------------------------------------------------------ session detail
    def _observation(self, observation_id: str) -> dict | None:
        return next((row for row in self.observations if str(row["id"]) == observation_id), None)

    def _linked_session(self, observation: dict) -> dict | None:
        """The program session the observation fulfilled, if any."""
        week = self.week(week_label(observation["occurred_on"]))
        session_id = next((key for key, link in week.links.items()
                           if str(link.get("observation_id")) == str(observation["id"])), None)
        if not session_id or not week.revision:
            return None
        return next((s for s in week.revision["sessions"] if s["id"] == session_id), None)

    def reserve_ask(self, observation: dict, session: dict | None) -> str | None:
        """A quality session's reserve (guardrail 6) is asked while the session is fresh."""
        if not debrief.is_quality(session) or _known(observation, "reps_in_reserve") is not None:
            return None
        if self.today - observation["occurred_on"] >= ledger_model.COMPLETION_WINDOW:
            return None
        return f"reserve:{observation['id']}"

    def _answer_reserve(self, ask_id: str, value: Any) -> dict:
        observation = self._observation(ask_id.split(":", 1)[1])
        if observation is None or self.reserve_ask(
                observation, self._linked_session(observation)) != ask_id:
            raise AnswerError(notebook_text.QUESTION_CLOSED)
        if isinstance(value, bool) or value not in (0, 1):
            raise AnswerError(notebook_text.UNEXPECTED_ANSWER)
        self.ops.complete_measurement(observation["id"], "reps_in_reserve", value, actor=ACTOR)
        return {"alert": None}

    def _previous(self, observation: dict) -> views.Previous | None:
        """The last session of the same type before this one, for comparison."""
        earlier = next((row for row in self.observations
                        if row["kind"] == observation["kind"]
                        and row["occurred_on"] < observation["occurred_on"]), None)
        if earlier is None:
            return None
        out: views.Previous = {
            "date": earlier["occurred_on"].isoformat(), "km": _known(earlier, "distance_km"),
            "hr_avg": None if earlier.get("hr_doubt") == HrDoubt.NO_SENSOR
            else _known(earlier, "hr_avg_bpm"),
            "rpe": _known(earlier, "rpe"), "pains": known_pains(earlier, self.zone_labels),
            "enjoyment": _known(earlier, "enjoyment"), "rep_dplus": None, "rep_hr": None}
        activity = _activity(self.ops.strava_detail(earlier["id"]))
        laps = activity.laps if activity else ()
        reading = session_detail.quality(laps, None)
        reps = reading["reps"] if reading else []
        if reps:
            out["rep_dplus"] = round(sum(rep["dplus"] for rep in reps) / len(reps))
            beats = [rep["hr"] for rep in reps if rep["hr"] is not None]
            if beats and out["hr_avg"] is not None:
                out["rep_hr"] = round(sum(beats) / len(beats))
        return out

    def _gear(self, gear_id: str) -> views.Gear | None:
        row = next((row for row in self.ops.gear_rows() if row["gear_id"] == gear_id), None)
        if row is None:
            return None
        return {"name": row["name"], "km": round(float(row["distance_km"])),
                "retire_at_km": row.get("retire_at_km"), "status": row.get("status")}

    def _wake(self, day: dt.date) -> views.WakeTrace:
        """The wake trace of the day (before the session) and the next day (after), per zone."""
        def pains_on(when: dt.date) -> dict[str, float]:
            row = next((row for row in self.observations
                        if row["kind"] == session_kinds.WAKE_TRACE and row["occurred_on"] == when), None)
            return {item["zone"]: item["value"]
                    for item in known_pains(row, self.zone_labels)} if row else {}

        after = day + dt.timedelta(days=1)
        before_pains, after_pains = pains_on(day), pains_on(after)
        return {"zones": [{"zone": zone.slug, "label": zone.label,
                           "before": before_pains.get(zone.slug),
                           "after": after_pains.get(zone.slug)} for zone in self.zones],
                "after_due": after > self.today}

    def _week_context(self, day: dt.date) -> views.WeekContext:
        week = self.week(week_label(day))
        sessions = self.sessions(week)
        totals = self.totals(week, sessions)
        out: views.WeekContext = {
            "week": week.label, "current": week.label == week_label(self.today),
            "km_done": totals["km_done"], "km_target": totals["km_target"],
            "runs_done": totals["runs_done"], "runs_planned": totals["runs_planned"],
            "next": None}
        if out["current"]:
            ahead = self.ahead(week, sessions, days=7)
            if ahead:
                same = [s for s in ahead if s["date"] == ahead[0]["date"]]
                out["next"] = {"date": same[0]["date"], "titles": [s["title"] for s in same],
                               "families": [[s["kind"], s["workout"]] for s in same],
                               "planned": same[0]["planned"]}
        return out

    def session_view(self, observation_id: str, intake: dict | None) -> views.SessionView | None:
        """The detail of a completed session: the fact, the plan, the streams read, the context."""
        observation = self._observation(observation_id)
        if observation is None or observation["kind"] not in MOVING_TYPES:
            return None
        session = self._linked_session(observation)
        day = observation["occurred_on"]
        activity = _activity(intake)
        streams = (intake or {}).get("streams")
        hr_valid = observation.get("hr_doubt") != HrDoubt.NO_SENSOR
        s = None if not streams else {**streams, "hr": streams.get("hr") if hr_valid else None}
        quality = debrief.is_quality(session)
        reading = session_detail.quality(activity.laps if activity else (), s) if quality else None
        fact = observation_fact(observation, session, self.zone_labels)
        dplus = fact["dplus"]
        series: views.Series | None = None
        if s:
            curves = session_detail.series(s)
            flat = dplus is None or dplus < session_detail.PROFILE_MIN_DPLUS_M
            series = {**curves, "alt": None if flat else curves["alt"],
                      "bands": [[rep["start"], rep["end"]]
                                for rep in (reading["reps"] if reading else [])],
                      "stops": [stop for stop in session_detail.stops(s)
                                if stop[1] - stop[0] >= session_detail.STOP_MARK_S]}
        limits = self.instance.hr_bounds if self.instance is not None else None
        seconds = session_detail.zone_seconds(s, limits) if s and limits else None
        conditions = (intake or {}).get("weather") or {}
        weather = cast("views.Conditions", {
            **{key: conditions.get(key) for key in ("temp", "feels_like", "humidity", "wind", "wbgt")},
            "conditions": weather_labels.sky(conditions)}) if conditions else None
        wbgt = (intake or {}).get("wbgt_value")
        if weather is None and wbgt is not None:
            weather = {"wbgt": float(wbgt)}
        gear_id = observation.get("gear_id") or (activity.gear_id if activity else None)
        ask = next((item for item in self.asks() if item["type"] == "cells"
                    and item["id"] == f"cell:{observation['id']}"), None)
        return {
            "id": str(observation["id"]), "date": day.isoformat(), "kind": observation["kind"],
            "title": session["title"] if session else None,
            "workout": session["workout"] if session else None, "quality": quality,
            "strava_id": (intake or {}).get("activity_id"),
            "fact": {**fact, "moving_s": _known(observation, "duration_s"),
                     "hr_max": _known(observation, "hr_max_bpm") if hr_valid else None,
                     "cadence": _known(observation, "cadence_spm"),
                     "reserve": _known(observation, "reps_in_reserve")},
            "plan": None if not session else {
                "targets": _targets(session), "note": session.get("note")},
            "ask": None if ask is None else {"id": ask["id"], "fields": ask["fields"],
                                             "labels": ask["labels"]},
            "reserve_ask": self.reserve_ask(observation, session),
            "wake": self._wake(day),
            "phases": reading["phases"] if reading else None,
            "reps": reading["reps"] if reading else None,
            "reading": reading["reading"] if reading else None,
            "km": None if quality or not s else session_detail.km_splits(s),
            "stops": session_detail.stop_summary(s) if s else None,
            "drift": None if quality or not s else session_detail.drift(s),
            "series": series,
            # The bounds are always sent: the average heart rate and the km are colored
            # by zone even when the watch stream is missing.
            "hr_bounds": list(limits) if limits else None,
            "zones": None if not seconds or not any(seconds) else {
                "max": self.instance.hr_max if self.instance else None,
                "seconds": seconds},
            "previous": self._previous(observation),
            "weather": weather,
            "gear": self._gear(gear_id) if gear_id else None,
            "week": self._week_context(day),
        }

    # ------------------------------------------------------------ screens
    def ahead(self, week: Week, sessions: list[views.SessionItem],
              days: int = 3) -> list[views.SessionItem]:
        """The sessions of the coming days, across the week boundary if needed."""
        horizon = self.today + dt.timedelta(days=days)
        pool = list(sessions)
        if week_label(horizon) != week.label:
            pool += self.sessions(self.week(week_label(horizon)))
        return [s for s in pool if self.today.isoformat() < s["date"] <= horizon.isoformat()
                and s["kind"] != "rest" and s["status"] not in {"cancelled", "done"}]

    def today_view(self) -> views.BoardToday:
        week = self.week(week_label(self.today))
        sessions = self.sessions(week)
        tomorrow = self.today + dt.timedelta(days=1)
        if week_label(tomorrow) == week.label:
            ahead, ahead_sessions = week, sessions
        else:
            ahead = self.week(week_label(tomorrow))
            ahead_sessions = self.sessions(ahead)
        upcoming = [s for s in ahead_sessions if s["date"] > tomorrow.isoformat()
                    and s["kind"] != "rest" and s["status"] not in {"cancelled", "done"}]
        # The whole next training DAY: "easy run + strength", not the run alone.
        upcoming = [s for s in upcoming if upcoming and s["date"] == upcoming[0]["date"]]
        today_items = [s for s in sessions if s["date"] == self.today.isoformat()
                       and s["status"] != "cancelled"]
        return {
            "date": self.today.isoformat(), "week": week.label,
            "plan": {"status": week.status.name.lower(),
                     "message": PLAN_MESSAGES.get(week.status, "")},
            "sessions": today_items,
            "extra": [item for item in self.unplanned(week)
                      if item["date"] == self.today.isoformat()],
            "tomorrow": {
                "date": tomorrow.isoformat(), "plan": ahead.status.name.lower(),
                "sessions": [s for s in ahead_sessions if s["date"] == tomorrow.isoformat()
                             and s["status"] != "cancelled"],
                "next": upcoming},
            "strip": self.strip(week, sessions),
            "totals": self.totals(week, sessions),
            "asks": self.asks(),
            "ahead": self.ahead(week, sessions),
            "pain": self.pain(),
            "race": self.race(),
        }

    def week_view(self, label: str, entries: list[views.Entry]) -> views.WeekView:
        week = self.week(label)
        strength_entries = {entry["period"]: entry["id"] for entry in entries
                            if entry["kind"] == "strength"}
        sessions = self.sessions(week, strength_entries)
        review = next((entry for entry in entries
                       if entry["kind"] == "weekly" and entry["period"] == label), None)
        return {
            "week": label, "monday": week.days[0].isoformat(),
            "current": week_label(self.today),
            "plan": {"status": week.status.name.lower(),
                     "message": PLAN_MESSAGES.get(week.status, "") if label >= week_label(self.today) else "",
                     "revision": (week.revision or {}).get("revision")},
            "sessions": sessions, "extra": self.unplanned(week),
            "totals": self.totals(week, sessions),
            "strip": self.strip(week, sessions),
            "review": review,
            "cards": [entry for entry in entries
                      if entry["kind"] not in {"weekly", "strength"}],
            "next": self.next_week() if label == week_label(self.today) else None,
        }

    def next_week(self) -> views.NextWeek | None:
        """The following week as soon as its program exists: on Sunday, it is the one
        being organized. Without a valid program, nothing is announced."""
        week = self.week(week_label(self.today + dt.timedelta(days=7)))
        if week.status is PlanStatus.INVALID:
            return None
        totals = self.totals(week, self.sessions(week))
        return {"week": week.label, "monday": week.days[0].isoformat(),
                "km_target": totals["km_target"], "runs_planned": totals["runs_planned"]}

    def weeks_view(self, reviews: dict[str, str], limit: int = 52) -> list[views.PastWeek]:
        """The closed weeks, most recent first: the ledger fact and the target of the
        active revision. The current week is not in it; it is the root of the Week tab."""
        current = week_label(self.today)
        out: list[views.PastWeek] = []
        for trace in reversed(ledger.trajectory(self.rows, self.today, weeks=limit)):
            if trace.label >= current:
                continue
            context = self.repo.weekly_plan_context(trace.label)
            target = json.loads(context).get("volume_target_km") if context else None
            out.append({"week": trace.label, "monday": week_monday(trace.label).isoformat(),
                        "km_done": round(trace.km, 2), "km_target": target,
                        "review": reviews.get(trace.label)})
        return out

    def race_view(self, archives: list[views.Entry]) -> views.RaceView:
        current = week_label(self.today)
        traces = ledger.trajectory(self.rows, self.today, weeks=6)
        week = self.week(current)
        planned = [s for s in self.sessions(week) if s["kind"] == "run"
                   and s["status"] not in {"done", "cancelled"}
                   and s["date"] >= self.today.isoformat()]
        # The current week's long run is the program's one until it has been run: the
        # longest of the past days is not one, it usually comes on the weekend.
        long_run = next((s for s in planned
                         if s["workout"] == session_kinds.LONG and _distance(s)), None)
        milestones: list[views.Milestone] = []
        key = max(planned, key=lambda s: _distance(s) or 0, default=None)
        if key and (key_km := _distance(key)):
            milestones.append({"date": key["date"], "label": key["title"], "kind": "session",
                               "km": key_km})
        for row in self.ops.deadline_rows():
            if not row.get("athlete_label") or row["status"] == "done" or row["kind"] == "race":
                continue
            milestones.append({"date": row["due_on"].isoformat(), "label": row["athlete_label"],
                               "kind": "deadline", "overdue": row["due_on"] < self.today})
        race = self.race()
        if race:
            milestones.append({"date": race["date"], "label": notebook_text.RACE_START, "kind": "race",
                               "start_time": race.get("start_time")})
        milestones.sort(key=lambda item: item["date"])
        capabilities: list[views.Capability] = sorted(
            ({"label": self._capability_label(row["slug"]), "icon": self._capability_icon(row["slug"]),
              "score": row["score"], "scale": row["scale"], "number": row["capability_number"],
              "evaluated_on": row["evaluated_on"].isoformat()}
             for row in self.ops.latest_capabilities()),
            key=lambda item: (-item["score"], item["number"]))
        longest = ledger.longest_recent(self.rows, self.today + dt.timedelta(days=1))
        # One period, one archive: the delivered image takes precedence over its recovered copy.
        seen: set[tuple[str, str]] = set()
        kept: list[views.Entry] = []
        for entry in sorted(archives, key=lambda item: item.get("origin") != "delivery"):
            identity = (entry["kind"], entry.get("period") or entry["id"])
            if identity not in seen:
                seen.add(identity)
                kept.append(entry)
        monthly = sorted((e for e in kept if e["kind"] == "monthly"),
                         key=lambda e: e.get("period") or "", reverse=True)
        weekly = sorted((e for e in kept if e["kind"] == "weekly"),
                        key=lambda e: e.get("period") or "", reverse=True)[:3]
        def target(label: str) -> float | None:
            if label == current:
                return (week.revision or {}).get("volume_target_km")
            context = self.repo.weekly_plan_context(label)
            return json.loads(context).get("volume_target_km") if context else None

        return {
            "race": race,
            # Each week's volume against its target, and its long run.
            "longest": {"km": longest,
                        "weeks": [{"week": trace.label, "km": round(trace.longest, 2),
                                   "volume": round(trace.km, 2), "target": target(trace.label),
                                   "open": trace.open} for trace in traces],
                        "planned_km": long_run and _distance(long_run),
                        "planned_on": long_run and long_run["date"]},
            "milestones": milestones, "capabilities": capabilities,
            "archives": [*monthly, *weekly],
        }
