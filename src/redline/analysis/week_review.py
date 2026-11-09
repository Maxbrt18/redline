"""The week as the coach reviews it: the load gate and the weekly report.

Both compose the ledger with the pain rules, the weak signals and the taper, so they sit
above all of them.
"""

from __future__ import annotations

import datetime as dt

from redline.coach.fr import ledger as text
from redline.model import (
    iso_week,
    rulebook,
    session_kinds,
)
from redline.model import race as race_model
from redline.model.vocabulary import WeekSleep

from . import ledger, pain_rules, signals, taper


def _pain_gate_cases(rows: list[ledger.Row], label: str, previous: str,
                     zone: str, as_of: dt.date) -> list[tuple[str, str]]:
    def peak(week: str) -> float | None:
        values = [value for row in rows if row.week == week and row.type in ledger.RUN_TYPES
                  and (value := row.pain_at(zone)) is not None]
        return max(values) if values else None

    now, before = peak(label), peak(previous)
    cases = []
    if now is None:
        cases.append(("?", text.GATE_NO_EFFORT_PEAK))
    else:
        ceiling = rulebook.current().parameters.gate_effort_peak
        cases.append(("✓" if now <= ceiling else "✗",
                      text.GATE_EFFORT_PEAK.format(now=now, ceiling=ceiling)))
    if now is None or before is None:
        cases.append(("?", text.GATE_TREND_PARTIAL.format(previous=previous, before=before if before is not None else "—", label=label, now=now if now is not None else "—")))
    else:
        cases.append(("✓" if now <= before else "✗",
                      text.GATE_TREND.format(before=before, now=now)))
    wakes = [row for row in rows if row.week == label and row.type == session_kinds.WAKE_TRACE]
    measured = [(row.date, value) for row in wakes
                if (value := row.pain_at(zone)) is not None]
    base = ledger.rest_peak(rows, iso_week.monday(label), zone)
    above = [(day, value) for day, value in measured
             if value > (base if base is not None else 0.0)]
    detail = (text.GATE_WAKES.format(measured=len(measured), wakes=len(wakes))
              + (text.GATE_WAKES_REFERENCE.format(base=base) if base is not None else ""))
    if above:
        cases.append(("✗", detail + text.GATE_WAKES_ABOVE
                      + ", ".join(f"{day:%d/%m} {value:g}" for day, value in above)))
    elif not measured or len(measured) < len(wakes):
        cases.append(("?", detail))
    else:
        cases.append(("✓", detail))
    cases.extend(_wake_trend_cases(rows, as_of, zone))
    return cases


def _wake_trend_cases(rows: list[ledger.Row], as_of: dt.date, zone: str) -> list[tuple[str, str]]:
    """The morning trend of the 14 days to `as_of`, and a persistence established in them."""

    trend = pain_rules.wake_trend(rows, zone, as_of)
    cases = []
    if trend.direction is None:
        cases.append(("?", text.GATE_WAKE_TREND_UNKNOWN.format(days=trend.days)))
    else:
        cases.append(("✗" if trend.direction == "rising" else "✓", text.GATE_WAKE_TREND.format(
            days=trend.days, earlier=trend.earlier, recent=trend.recent,
            direction=text.WAKE_DIRECTIONS[trend.direction])))
    book = rulebook.current()
    if book.parameters.persistent_wake_mornings:
        start = as_of - dt.timedelta(days=trend.days - 1)
        cases.extend(("✗", text.GATE_WAKE_PERSISTENT.format(day=persistence.established))
                     for persistence in pain_rules.persistences(
                         rows, zone, book.scales.morning_trace,
                         book.parameters.persistent_wake_mornings)
                     if start <= persistence.established <= as_of)
    return cases


def wake_lines(rows: list[ledger.Row], zone: str, today: dt.date) -> list[str]:
    """The morning trend and what followed each effort above the n3 cap, for the coach."""

    trend = pain_rules.wake_trend(rows, zone, today)
    lines = [text.WAKE_TREND.format(
        days=trend.days, n=len(trend.readings),
        earlier="—" if trend.earlier is None else f"{trend.earlier:g}",
        recent="—" if trend.recent is None else f"{trend.recent:g}",
        direction=text.WAKE_DIRECTIONS.get(trend.direction or "", text.WAKE_TREND_NONE))]
    book = rulebook.current()
    if book.parameters.persistent_wake_mornings is None:
        return lines
    after = pain_rules.settling(rows, zone, book.scales.effort_pain, book.scales.morning_trace)
    lines.append(text.SETTLING.format(settled=len(after.settled),
                                      persisted=len(after.persisted), unread=len(after.unread)))
    if after.too_sensitive:
        lines.append(text.SETTLING_TOO_SENSITIVE)
    return lines


def gate(rows: list[ledger.Row], label: str, instr: ledger.Instruments,
         zones: list[ledger.PainZone], today: dt.date | None = None) -> list[str]:
    """The gate (rule `po` of the rulebook), computed box by box from the ledger.

    Judging it by eye in the review lets prose claim readings the numeric series does not
    hold. The computation does not replace the behavioural reading the rule requires: it
    states what the ledger establishes, and names what it cannot establish (« ? »). The
    pain boxes are judged zone by zone: the gate opens only if each one passes. Without a
    monitored zone, they do not exist.
    """

    previous = iso_week.of(iso_week.monday(label) - dt.timedelta(days=7))
    as_of = iso_week.as_of(label, today or dt.date.today())
    cases: list[tuple[str, str]] = []
    for zone in zones:
        cases.extend((mark, ledger.zone_prefix(zone, zones) + text)
                     for mark, text in _pain_gate_cases(rows, label, previous, zone.slug,
                                                        as_of))
    drifts = [drift for drift in signals.hr_at_pace(rows)
              if iso_week.of(drift.date) in (previous, label)
              and drift.delta >= signals.HR_DRIFT_BPM]
    if drifts:
        cases.append(("✗", text.GATE_HR_HIGH + ", ".join(
            f"{drift.date:%d/%m} {drift.delta:+.0f}" for drift in drifts)))
    else:
        cases.append(("✓", text.GATE_HR_NORMAL))
    if instr.sleep is None:
        cases.append(("?", text.GATE_SLEEP_UNDECLARED))
    else:
        cases.append(("✗" if instr.sleep != WeekSleep.OK else "✓", text.GATE_SLEEP.format(sleep=instr.sleep)))
    marks = {mark for mark, _ in cases}
    verdict = text.GATE_RED if "✗" in marks else text.GATE_INCOMPLETE if "?" in marks else text.GATE_GREEN
    return [text.GATE_VERDICT.format(verdict=verdict)] + [f"  {mark} {text}"
                                                           for mark, text in cases]



def week_report(store: ledger.LedgerStore, rows: list[ledger.Row], instruments: dict, targets: dict,
                label: str, today: dt.date, *, target_km: float | None = None,
                closing: bool = False) -> list[str]:
    """A week's report: volume, gauge, pain, then the slope.

    ONE function for the CLI (`redline ledger --week`) and for the dossier the service
    computes before the Sunday review (`weekly_dossier.py`): two renderings of the same
    computation would diverge.
    """
    per_week = ledger.eq_hours_per_week(rows, instruments, targets)
    week_rows = [row for row in rows if row.week == label]
    instr = instruments.get(label, ledger.Instruments())
    h_eq, level, trace = ledger.equivalent_hours(week_rows, instr, targets)
    ratio = ledger.acute_chronic_ratio(per_week, label)
    lines = [text.REPORT_WEEK.format(label=label), ""]
    # Volume before the gauge: it is the figure the athlete hears most often, and the only
    # one they can recount themselves. It comes from here or not at all.
    lines.append(text.REPORT_VOLUME.format(week_volume=ledger.week_volume(week_rows)))
    lines.append(text.REPORT_VOLUME_SOURCE)
    lines.append("")
    lines.extend(f"  {line}" for line in trace)
    over_level = " 🔴" if level > ledger.LEVEL_CAP else ""
    over_ratio = " 🔴" if ratio and ratio > ledger.RATIO_CAP else ""
    lines.append("")
    lines.append(text.REPORT_GAUGE.format(h_eq=h_eq, level=level, over_level=over_level, ratio=ratio if ratio is not None else "—", over_ratio=over_ratio))
    lines.append(text.REPORT_INSTRUMENTS.format(sleep=instr.sleep or text.SLEEP_UNDECLARED,
                                                life_load=instr.life_load or text.LIFE_LOAD_UNDECLARED))
    dminus = ledger.week_descent(week_rows)
    if dminus:
        lines.append(text.REPORT_DESCENT.format(dminus=dminus))
    zones = ledger.pain_zones(store)
    for zone in zones:
        pain = ledger.pain_trend(week_rows, 1, zone.slug)
        if pain:
            lines.append(text.REPORT_PAIN.format(label=zone.label, trend=pain[0]))
        lines.extend(text.REPORT_ESCULIER.format(zone_prefix=ledger.zone_prefix(zone, zones), flag=flag)
                     for flag in ledger.esculier_check(week_rows, zone.slug, history=rows))
        lines.extend(wake_lines(rows, zone.slug, iso_week.as_of(label, today)))
    lines.extend(gate(rows, label, instr, zones, today))
    lines.extend(text.REPORT_OVER_TARGET.format(overshoot=overshoot)
                 for overshoot in ledger.over_target(week_rows, targets))
    lines.extend(text.REPORT_INCOMPLETE.format(problem=problem)
                 for problem in ledger.week_completeness(store, label, today))

    # The slope comes after the week: a preparation is not judged on seven days.
    reference = iso_week.as_of(label, today)
    # `closing`: the Sunday-evening review CLOSES the week. Otherwise it would stay "in
    # progress" for the slope and its long run would only count for the record from
    # Monday, i.e. after the review that needs it.
    slope_day = (iso_week.monday(label) + dt.timedelta(days=7)) if closing else today
    traces = ledger.trajectory(rows, slope_day)
    if traces:
        lines.append("")
        lines.append(text.REPORT_TRAJECTORY.format(weeks=len(traces)))
        lines.extend(f"  {week_trace}" for week_trace in traces)
        lines.extend(ledger.trajectory_verdict(traces))
        stall = ledger.long_run_stall(rows, slope_day)
        if stall:
            lines.append(str(stall))
        longest = ledger.longest_recent(rows, reference)
        if longest is not None:
            lines.append(text.REPORT_LONGEST.format(longest=longest))
        race = race_model.upcoming(store, today)
        projection = ledger.race_projection(rows, today, race.on, race.taper_weeks) if race else None
        if projection:
            lines.extend(ledger.projection_lines(projection, target_km))
        elif race is None:
            lines.append(text.REPORT_NO_RACE)
    # Imported here: the taper reads the ledger's volumes.
    lines.extend(taper.report_lines(store, rows, label, today, closing))
    return lines
