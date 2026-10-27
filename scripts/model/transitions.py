"""Garde-fou n°8 calculé : transitions typées et faisabilité à rebours.

Né le 2026-10-01 pour porter la règle de la séance-transition, qui n'avait aucun porteur.
Le 2026-10-04, les fenêtres de fragilité +3/+4 sont retirées (decisions.md) : elles
modélisaient un délai que la biographie de l'athlète ne démontre pas, et leur combinaison
rendait la préparation infaisable. Ce qui reste :

1. **une transition au plus par semaine** (`per_week_errors`, appelé par la validation
   d'une révision) ;
2. **un solveur à rebours** qui place les premières fois encore à faire sur les semaines
   restantes avant l'affûtage, et qui, si aucun placement n'existe, dit quel renoncement
   ou quel assouplissement le rendrait possible.

Les hausses faciles (palier de SL, EF allongée, sortie de plus) ne sont pas des
transitions : elles passent par la porte observable (`rulebook.py`), lue au bilan.

⚠ Le solveur NE DÉCIDE RIEN. Ses hypothèses sont imprimées avec son verdict.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace
from itertools import combinations

from . import rulebook

#: Une nouveauté non typée, déduite : une sortie longue au-delà de la plus longue des
#: 30 jours. Ce n'est pas une transition : c'est une hausse facile, qui passe par la porte
#: observable et se cumule avec une transition (decisions.md 2026-10-04).
LONG_RUN_STEP = "palier-sl"


def kinds() -> tuple[str, ...]:
    """Les natures qu'une transition peut porter : celles du règlement de l'instance."""
    return rulebook.current().kinds


def label(kind: str) -> str:
    if kind == LONG_RUN_STEP:
        return "palier de SL"
    return rulebook.current().label(kind)


# ------------------------------------------------------------------ semaines ISO

def week_of(day: dt.date) -> str:
    year, number, _ = day.isocalendar()
    return f"{year}-W{number:02d}"


def monday_of(week: str) -> dt.date:
    year, number = week.split("-W")
    return dt.date.fromisocalendar(int(year), int(number), 1)


def shift(week: str, weeks: int) -> str:
    return week_of(monday_of(week) + dt.timedelta(weeks=weeks))


def short(week: str) -> str:
    return week.split("-")[1]


def day_of(value: dt.date | str) -> dt.date:
    """Une date de séance : texte ISO dans le JSON, `date` dans une commande de tour."""
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(value)


# ------------------------------------------------------------------ fenêtres

@dataclass(frozen=True)
class Transition:
    kind: str
    day: dt.date
    session_id: str

    @property
    def week(self) -> str:
        return week_of(self.day)

    def __str__(self) -> str:
        return f"{label(self.kind)} du {self.day:%d/%m} ({self.session_id})"


def opened(sessions: list[dict], as_of: dt.date) -> list[Transition]:
    """Les transitions d'une révision qui comptent : faites, ou encore au programme.

    Une séance passée qui n'a pas été faite (toujours `planned` après sa date) n'a pas eu
    lieu ; une séance annulée non plus.
    """
    found = []
    for session in sessions:
        kind = session.get("transition")
        if not kind or session.get("status") == "cancelled":
            continue
        day = day_of(session["date"])
        if session.get("status") == "done" or day >= as_of:
            found.append(Transition(kind, day, session["id"]))
    return found


def per_week_errors(sessions: list[dict], where: str) -> list[str]:
    """Une nature connue du règlement, et pas plus de transitions par semaine que lui :
    contrôle interne à une révision."""
    book = rulebook.current()
    problems = [f"{where} : séance {session['id']}, transition « {session['transition']} » "
                f"inconnue du règlement (attendues : {', '.join(book.kinds)})"
                for session in sessions
                if session.get("transition") and session["transition"] not in book.kinds]
    typed = [session for session in sessions
             if session.get("transition") and session.get("status") != "cancelled"]
    allowed = book.parameters.transitions_per_week
    if len(typed) <= allowed:
        return problems
    names = ", ".join(f"{s['id']} ({s['transition']})" for s in typed)
    return [*problems, f"{where} : {len(typed)} transitions dans la semaine ({names}), "
            f"garde-fou n°8 : {allowed} au plus"]


# ------------------------------------------------------------------ solveur à rebours

@dataclass(frozen=True)
class Problem:
    """Les semaines restantes, ce qui y est déjà décidé, et ce qu'il reste à y placer."""

    race_on: dt.date
    weeks: tuple[str, ...]                       # de la semaine courante à l'affûtage exclu
    assimilation: frozenset[str]                 # garde-fou n°2 : rien ne s'y ajoute
    taper: tuple[str, ...]                       # affûtage et course : aucune nouveauté
    fixed: dict[str, tuple[str, ...]]            # nouveautés portées par une révision active
    required: tuple[str, ...]                    # premières fois encore à placer
    done: tuple[str, ...] = ()                   # premières fois déjà faites


@dataclass(frozen=True)
class Rules:
    per_week: int | None = None   # garde-fou n°8 ; None : celui du règlement
    open_assimilation: bool = False


@dataclass(frozen=True)
class Schedule:
    novelties: dict[str, tuple[str, ...]]

    @property
    def long_run_steps(self) -> int:
        return sum(items.count(LONG_RUN_STEP) for items in self.novelties.values())

    def line(self, weeks: tuple[str, ...]) -> str:
        return " ; ".join(
            f"{short(week)} " + (" + ".join(label(item) for item in self.novelties[week])
                                 or "rien de neuf")
            for week in weeks)


def solve(problem: Problem, rules: Rules | None = None) -> Schedule | None:
    """Le meilleur placement, ou None s'il n'en existe aucun.

    Meilleur = toutes les premières fois placées, puis le plus de paliers de SL possibles
    (un palier ne compte pas comme transition : il est seulement exclu de l'assimilation),
    puis les transitions le plus tôt possible. Recherche exhaustive : l'espace est minuscule.
    """
    rules = rules or Rules()
    per_week = (rules.per_week if rules.per_week is not None
                else rulebook.current().parameters.transitions_per_week)
    best: tuple[tuple, Schedule] | None = None

    def options(week: str, remaining: tuple[str, ...]) -> list[tuple[str, ...]]:
        if week in problem.fixed:
            return [problem.fixed[week]]
        if week in problem.assimilation and not rules.open_assimilation:
            return [()]
        found: list[tuple[str, ...]] = []
        for size in range(per_week + 1):
            for kinds in combinations(remaining, size):
                found.extend([(LONG_RUN_STEP, *kinds), kinds])
        return found

    def walk(index: int, remaining: tuple[str, ...], order: list[int],
             novelties: dict[str, tuple[str, ...]]) -> None:
        nonlocal best
        if index == len(problem.weeks):
            if remaining:
                return
            schedule = Schedule(dict(novelties))
            score = (schedule.long_run_steps, [-i for i in order])
            if best is None or score > best[0]:
                best = (score, schedule)
            return
        week = problem.weeks[index]
        for choice in options(week, remaining):
            new = [item for item in choice if item != LONG_RUN_STEP]
            left = tuple(kind for kind in remaining if kind not in new)
            walk(index + 1, left, order + [index] * len(new), {**novelties, week: choice})

    walk(0, tuple(k for k in problem.required
                  if not any(k in items for items in problem.fixed.values())), [], {})
    return best[1] if best else None


@dataclass(frozen=True)
class Option:
    label: str
    schedule: Schedule | None


@dataclass(frozen=True)
class Verdict:
    problem: Problem
    feasible: bool
    options: tuple[Option, ...]


def diagnose(problem: Problem) -> Verdict:
    """Le verdict sous les règles actuelles ; s'il n'y en a pas, chaque levier isolé."""
    per_week = rulebook.current().parameters.transitions_per_week
    current = Option("règles actuelles", solve(problem))
    if current.schedule is not None:
        return Verdict(problem, True, (current,))
    levers: list[Option] = []
    for kind in problem.required:
        reduced = replace(problem, required=tuple(k for k in problem.required if k != kind))
        levers.append(Option(f"sans {label(kind)} avant la course", solve(reduced)))
    levers.append(Option("une transition de plus par semaine (garde-fou n°8)",
                         solve(problem, Rules(per_week=per_week + 1))))
    if problem.assimilation & set(problem.weeks):
        levers.append(Option("une transition en semaine d'assimilation (garde-fou n°2)",
                             solve(problem, Rules(open_assimilation=True))))
    return Verdict(problem, False, tuple(levers))


def _option_lines(option: Option, weeks: tuple[str, ...], prefix: str) -> list[str]:
    if option.schedule is None:
        return [f"{prefix}{option.label} : ne suffit pas"]
    return [f"{prefix}{option.label} : {option.schedule.long_run_steps} palier(s) de SL "
            f"possibles : {option.schedule.line(weeks)}"]


def render(verdict: Verdict) -> list[str]:
    """Le verdict tel qu'il part au bilan : court, hypothèses comprises."""
    problem = verdict.problem
    lines = [f"Faisabilité à rebours (garde-fou n°8, calculée) : course le "
             f"{problem.race_on:%d/%m}, affûtage {short(problem.taper[0])}-"
             f"{short(problem.taper[-1])}."]
    if not problem.weeks:
        return [*lines, "Aucune semaine ne peut plus porter de nouveauté avant l'affûtage."]
    assimilation = [short(w) for w in problem.weeks if w in problem.assimilation]
    lines.append(
        f"Semaines utiles {short(problem.weeks[0])}-{short(problem.weeks[-1])}"
        + (f", dont {', '.join(assimilation)} en assimilation" if assimilation else "")
        + ". Premières fois à placer : "
        + (", ".join(label(k) for k in problem.required) or "aucune")
        + (" ; déjà faites : " + ", ".join(label(k) for k in problem.done)
           if problem.done else "") + ".")
    if problem.fixed:
        lines.append("Déjà au programme : " + " ; ".join(
            f"{short(w)} " + (" + ".join(label(i) for i in items) or "rien de neuf")
            for w, items in sorted(problem.fixed.items())) + ".")
    lines.append(f"Hypothèses : {rulebook.current().parameters.transitions_per_week} "
                 "transition(s) par semaine (n8), "
                 "rien en assimilation (n2) ni en affûtage ; un palier de SL n'est pas une "
                 "transition, il passe par la porte. Placement possible, pas prescription.")
    if verdict.feasible:
        return [*lines, *_option_lines(verdict.options[0], problem.weeks, "FAISABLE, ")]
    lines.append("INFAISABLE avec les règles actuelles. Un seul levier à la fois :")
    for option in verdict.options:
        lines.extend(_option_lines(option, problem.weeks, "- "))
    lines.append("Le solveur ne tranche rien : le levier se choisit au bilan, avec "
                 "l'athlète, et se logge dans decisions.md.")
    return lines
