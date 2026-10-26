"""Permis EF (décision de l'athlète du 2026-09-27) : l'état du permis se CALCULE.

Rien n'est stocké hormis le choix scellé du coach (`permit_choices`) : ce que l'athlète a
couru vient du registre, la trace du lendemain aussi, les déclarations de charge de vie
de l'état du jour, le groupe de la capture Strava. Le statut (accompagné, permis,
retrait, restriction) en dérive, comme tout état dérivé du vault.

Ce qui n'est PAS détecté ici, et le rapport le dit : un parcours neuf (il faudrait
comparer des tracés), et la grosse forme tant que son seuil n'est pas fixé.
Méthode : `coaching/protocols.md` § Permis EF.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, field

#: Début de la conduite accompagnée et semaine où l'obtention se juge.
START = dt.date(2026, 9, 28)
OBTENTION_WEEK = "2026-W42"
#: Critères d'obtention (protocols.md § Permis EF).
MIN_EF = 5
TOLERANCE_MIN = 10
#: Une EF au-dessus de ce plafond de FC moyenne est « trop rapide » : borne haute de la
#: Z2 écrite dans toutes les EF prescrites (130-157 bpm).
EF_HR_CAP = 157
#: Durée de la conduite accompagnée après un déclencheur de retrait.
WITHDRAWAL_DAYS = 14
#: Grosse forme : progression de l'allure à FC égale. Sans historique, pas de seuil
#: honnête ; il se fixe au bilan W42. D'ici là le signal s'affiche, il ne déclenche rien.
BIG_FORM_THRESHOLD: float | None = None
#: Trace du réveil : 2 = nette (aucune 0, très légère 0,5, légère 1, nette 2).
NET_TRACE = 2
DAYS = ("lun", "mar", "mer", "jeu", "ven", "sam", "dim")


@dataclass(frozen=True)
class Run:
    """Une sortie du registre, réduite à ce que le permis lit."""

    date: dt.date
    type: str
    minutes: float | None
    hr_avg: int | None
    strava_id: int | None = None


@dataclass
class EfLine:
    session_key: str
    date: dt.date
    coach_minutes: int
    cap_minutes: int | None
    actual_minutes: float | None = None
    hr_avg: int | None = None
    next_trace: float | None = None
    group: bool | None = None
    status: str = "à venir"  # à venir · courue · sautée
    triggers: list[str] = field(default_factory=list)

    @property
    def gap(self) -> float | None:
        if self.actual_minutes is None:
            return None
        return self.actual_minutes - self.coach_minutes

    @property
    def agrees(self) -> bool | None:
        gap = self.gap
        return None if gap is None else abs(gap) <= TOLERANCE_MIN


def iso_week(day: dt.date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def week_end(week: str) -> dt.date:
    year, number = int(week[:4]), int(week[-2:])
    return dt.date.fromisocalendar(year, number, 7)


def evaluate(choices: Iterable[dict], runs: Iterable[Run], traces: dict[dt.date, float],
             caps: dict[str, int | None], groups: dict[int, bool],
             today: dt.date) -> list[EfLine]:
    """Une ligne par EF scellée : le choix du coach face à ce que l'athlète a couru."""
    by_day: dict[dt.date, list[Run]] = {}
    for run in runs:
        if run.type == "EF":
            by_day.setdefault(run.date, []).append(run)
    lines = []
    for choice in sorted(choices, key=lambda row: row["scheduled_on"]):
        day = choice["scheduled_on"]
        if isinstance(day, str):
            day = dt.date.fromisoformat(day)
        line = EfLine(choice["session_key"], day, int(choice["coach_minutes"]),
                      caps.get(choice["session_key"]))
        done = by_day.get(day, [])
        if done:
            run = done[0]
            line.status = "courue"
            line.actual_minutes = run.minutes
            line.hr_avg = run.hr_avg
            line.next_trace = traces.get(day + dt.timedelta(days=1))
            if run.strava_id is not None:
                line.group = groups.get(run.strava_id)
            if line.next_trace is not None and line.next_trace >= NET_TRACE:
                line.triggers.append("trace nette le lendemain")
            if (line.cap_minutes is not None and line.actual_minutes is not None
                    and line.actual_minutes > line.cap_minutes):
                line.triggers.append(f"au-delà de l'enveloppe ({line.cap_minutes} min)")
            if line.hr_avg is not None and line.hr_avg > EF_HR_CAP:
                line.triggers.append(f"trop rapide (FC moyenne {line.hr_avg} > {EF_HR_CAP})")
        elif day < today:
            line.status = "sautée"
        lines.append(line)
    return lines


@dataclass
class State:
    phase: str
    withdrawal_until: dt.date | None
    restricted_weeks: dict[str, list[str]]
    obtention: dict | None


def state(lines: list[EfLine], daily: Iterable[dict], today: dt.date) -> State:
    """Accompagné, permis ou retrait, dérivé des lignes et des déclarations."""
    withdrawal_until = None
    for line in lines:
        if line.triggers:
            until = line.date + dt.timedelta(days=WITHDRAWAL_DAYS)
            if withdrawal_until is None or until > withdrawal_until:
                withdrawal_until = until

    restricted: dict[str, list[str]] = {}
    for row in daily:
        day = row.get("checked_on") or row.get("date")
        if isinstance(day, str):
            day = dt.date.fromisoformat(day)
        if day is None or day < START:
            continue
        reasons = []
        if row.get("life_load") == "elevee" or row.get("charge_vie") == "elevee":
            reasons.append(f"charge de vie élevée déclarée le {day:%d/%m}")
        if row.get("sleep") == "mauvaise" or row.get("sommeil") == "mauvaise":
            reasons.append(f"mauvaise nuit déclarée le {day:%d/%m}")
        if reasons:
            restricted.setdefault(iso_week(day), []).extend(reasons)
    for line in lines:
        if line.group:
            restricted.setdefault(iso_week(line.date), []).append(
                f"sortie de groupe le {line.date:%d/%m}")

    obtention = None
    if today > week_end(OBTENTION_WEEK):
        window = [line for line in lines if line.date <= week_end(OBTENTION_WEEK)]
        run = [line for line in window if line.status == "courue"]
        agreements = sum(1 for line in run if line.agrees)
        failures = [f"{line.session_key} : {', '.join(line.triggers)}"
                    for line in window if line.triggers]
        obtention = {"courues": len(run), "accords": agreements, "retraits": failures,
                     "obtenu": len(run) >= MIN_EF and not failures}

    if withdrawal_until is not None and today <= withdrawal_until:
        phase = "retrait : conduite accompagnée"
    elif obtention is None:
        phase = "conduite accompagnée"
    elif obtention["obtenu"]:
        phase = "permis"
    else:
        phase = "conduite accompagnée (obtention refusée)"
    return State(phase, withdrawal_until, restricted, obtention)


def _fmt_minutes(value: float | None) -> str:
    return "—" if value is None else f"{round(value)} min"


def render(lines: list[EfLine], current: State, week: str | None = None) -> list[str]:
    """Le bloc lisible par le coach, au bilan ou à la demande."""
    shown = [line for line in lines if week is None or iso_week(line.date) == week]
    out = [f"Permis EF : {current.phase}."]
    if current.withdrawal_until is not None:
        out.append(f"Retrait actif jusqu'au {current.withdrawal_until:%d/%m}.")
    for line in shown:
        gap = line.gap
        agreement = "" if gap is None else (
            f", écart {gap:+.0f} min ({'accord' if line.agrees else 'désaccord'})")
        labels = {0: "aucune", 0.5: "très légère", 1: "légère", 2: "nette"}
        trace = ("trace J+1 inconnue" if line.next_trace is None
                 else f"trace J+1 {labels.get(line.next_trace, line.next_trace)}")
        group = " · en groupe" if line.group else ""
        detail = (f"coach {line.coach_minutes} min, couru {_fmt_minutes(line.actual_minutes)}"
                  f"{agreement} · {trace}{group}" if line.status == "courue"
                  else f"coach {line.coach_minutes} min")
        flags = f" ⇒ {'; '.join(line.triggers)}" if line.triggers else ""
        out.append(f"- {DAYS[line.date.weekday()]} {line.date:%d/%m} {line.session_key} [{line.status}] : "
                   f"{detail}{flags}")
    for iso, reasons in sorted(current.restricted_weeks.items()):
        if week is None or iso == week:
            out.append(f"Restriction {iso} : {'; '.join(reasons)}.")
    if current.obtention is not None:
        verdict = current.obtention
        out.append(f"Obtention ({OBTENTION_WEEK}) : {verdict['courues']} EF courues "
                   f"(≥{MIN_EF} requises), {verdict['accords']} accords à ±{TOLERANCE_MIN} min, "
                   f"retraits : {len(verdict['retraits'])} ⇒ "
                   f"{'OBTENU' if verdict['obtenu'] else 'NON OBTENU'}.")
    else:
        run = sum(1 for line in lines if line.status == "courue")
        out.append(f"Compteur d'obtention : {run} EF courues sur {MIN_EF} requises "
                   f"d'ici la fin de {OBTENTION_WEEK}.")
    out.append("Non détectés automatiquement : parcours neuf ; grosse forme "
               + ("(seuil non fixé, se lit sur « FC à allure » de signals.py)."
                  if BIG_FORM_THRESHOLD is None else f"(seuil {BIG_FORM_THRESHOLD})."))
    return out
