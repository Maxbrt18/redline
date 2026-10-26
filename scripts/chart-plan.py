#!/usr/bin/env python3
"""Canonical weekly-plan card, rendered from the active persisted revision."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cards import html, render  # noqa: E402
from scripts.model.weekly_plan import (  # noqa: E402
    active,
    details_of,
    gate_reasons,
    scheduled,
    validate,
)

DAYS = ("Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche")


def detail(session: dict) -> str:
    """Ce qui engage, demandé au modèle.

    La carte portait sa propre copie, et elle avait déjà dérivé : elle ne rendait RIEN
    pour une séance dont le seul engagement est un plafond de durée -- une ligne de
    programme affichée sans sa consigne. `details_of` la donne comme les deux autres
    rendus le font depuis toujours.
    """
    return ", ".join(details_of(session))


def rows(sessions: list) -> str:
    by_date: dict[str, list[dict]] = {}
    for session in sessions:
        by_date.setdefault(session["date"], []).append(session)

    days = []
    for date_text in sorted(by_date):
        day_sessions = by_date[date_text]
        day = dt.date.fromisoformat(date_text)
        rendered = []
        for index, session in enumerate(day_sessions, 1):
            status = " (fait)" if session["status"] == "done" else ""
            rendered.append(
                '<li class="row">'
                f'<span class="row__index">{index:02d}</span>'
                '<div class="row__main">'
                f'<p class="row__name">{html.esc(session["title"])}{status}</p>'
                f'<p class="row__sub">{html.esc(detail(session))}</p>'
                '</div></li>')
        days.append(
            '<section class="day-group">'
            f'<h2 class="day-group__title">{html.esc(DAYS[day.weekday()])} {day:%d/%m}</h2>'
            f'<ol class="day-group__sessions">{"".join(rendered)}</ol>'
            '</section>')
    return '<div class="rows">' + "".join(days) + "</div>"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--today", default="")
    parser.add_argument("-o", "--out", required=True)
    parser.add_argument("--html", action="store_true")
    args = parser.parse_args()
    database_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL obligatoire : le journal ne contient qu'une projection")
    from scripts.storage import Database, OperationalStore
    store = OperationalStore(Database(database_url, application_name="chart-plan"))
    plan = store.load_plan(args.week)
    if plan is None:
        raise SystemExit(f"Programme {args.week} absent dans PostgreSQL")
    validate(plan)
    # `--today` datait le FILTRE des séances et pas le contrôle de fraîcheur : la card
    # décidait « à recalculer » d'après l'horloge tout en s'affichant à une autre date.
    # Même faute que le contrôle CI devenu rouge tout seul le 2026-08-04 (voir README) :
    # une date implicite dans un chemin qu'on croit daté.
    as_of = dt.date.fromisoformat(args.today) if args.today else None
    revision = active(plan)
    stale = gate_reasons(plan, as_of)
    if store.plan_needs_arbitration(args.week, revision["revision"]):
        stale.append("données de la semaine modifiées depuis le dernier calcul")
    if stale:
        raise SystemExit("Plan à recalculer : " + " ; ".join(stale))
    # Vue descriptive : la carte raconte la semaine, séances faites marquées « (fait) ».
    # Ce qu'est une séance annulée ou soldée se demande au modèle, jamais réécrit ici.
    sessions = scheduled(revision["sessions"])
    if args.today:
        sessions = [session for session in sessions if session["date"] == args.today]
    body = ('<main id="card">'
            + html.header(f"{args.week}, révision {revision['revision']}", revision["reason"])
            + html.section("programme canonique") + rows(sessions)
            + html.footer("Le total hebdomadaire est une conséquence, jamais une cible à défendre.")
            + "</main>")
    render.emit(body, args.out, html_only=args.html)


if __name__ == "__main__":
    main()
