#!/usr/bin/env python3
"""Index of decisions + which falsifiable predictions are due for review.

Un journal append-only de plusieurs centaines de kilo-octets ne se relit pas ; il se
requête (F8 de l'audit). Le protocole
requires predictions "revues aux semaines d'assimilation" -- but nothing could list the
ones whose review window has arrived. This does, WITHOUT adding a redundant metadata
block: decisions.md already carries an inline convention (**Prédiction**, **Prédictions**
or **Prédiction falsifiable**, then **Critère d'échec**). This script parses every
accepted spelling and refuses a prediction rubric it cannot index.

Usage:
    python3 scripts/decisions-index.py          # regenerate the index between markers
    python3 scripts/decisions-index.py --due     # predictions whose review week has passed
    python3 scripts/decisions-index.py --check    # exit 1 if the on-disk index is stale (CI)
    python3 scripts/decisions-index.py --validate # syntax only; stable static gate
    python3 scripts/decisions-index.py --find injury # lit seulement les décisions correspondantes
    python3 scripts/decisions-index.py --calibration # issues des verdicts, part non testable

The index is rewritten in place between <!-- INDEX_START --> and <!-- INDEX_END -->.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.model import (  # noqa: E402
    paths,
    views,
)

# Le parseur vit dans le modèle (vault-check.py le partage) ; ses noms restent
# importables d'ici, c'est l'API des tests et des appelants historiques.
from scripts.model.decisions import (  # noqa: E402,F401
    HEADING,
    OUTCOMES,
    PLAN_WEEKS,
    PLAN_YEAR,
    PREDICTION,
    PREDICTION_START,
    RACE_DAY,
    STRICT_VERDICTS_FROM,
    VERDICT,
    WEEK,
    Decision,
    DecisionFormatError,
    calibration,
    overdue_days,
    parse,
    verdict_template,
    week_end,
)
from scripts.model.views import ViewDrift  # noqa: E402

DECISIONS = "coaching/decisions.md"
BLOCK = "INDEX"


def render_index(decisions: list[Decision], today: dt.date) -> str:
    """The table only -- `views.apply` owns the markers around it."""
    lines = [f"> Index régénéré par `scripts/decisions-index.py` — {len(decisions)} décisions, "
             f"{sum(d.has_prediction for d in decisions)} avec prédiction. "
             "Ne pas éditer à la main (réécrit entre les marqueurs).",
             "",
             "| Date | Décision | Prédiction | Revue |",
             "|---|---|---|---|"]
    for d in decisions:
        pred = "✅" if d.has_prediction else "—"
        if d.review_week:
            if not d.narrative_predictions:
                revue = f"W{d.review_week} ↗ PostgreSQL"
            elif d.verdict_on:
                revue = f"W{d.review_week} ✔ {d.verdict_on}"
            else:
                revue = f"W{d.review_week}{' 🔔' if d.is_due(today) else ''}"
        else:
            revue = "—"
        title = d.title if len(d.title) <= 70 else d.title[:67] + "…"
        lines.append(f"| {d.date} | {title} | {pred} | {revue} |")
    return "\n".join(lines)


#: Part de « non testable » au-delà de laquelle les prédictions s'écrivent mal
#: ([[decisions]] 2026-10-04).
UNTESTABLE_CEILING = 0.20


def render_calibration(decisions: list[Decision]) -> list[str]:
    lines = []
    for label, since in (("Tous les verdicts", None),
                         (f"Depuis le {STRICT_VERDICTS_FROM:%d/%m}", STRICT_VERDICTS_FROM)):
        counts = calibration(decisions, since)
        outcomes = counts["outcomes"]
        total = {name: sum(detail.values()) for name, detail in outcomes.items()}
        classified = sum(total.values())
        lines.append(f"{label} : {counts['verdicts']} verdict(s)"
                     + (f", dont {counts['unclassified']} en prose libre (historique)"
                        if counts["unclassified"] else ""))
        for name in OUTCOMES:
            if name in outcomes:
                detail = " · ".join(f"{key} {n}" for key, n in sorted(outcomes[name].items()))
                lines.append(f"  {name} {total[name]} ({detail})")
        tested = total.get("tenue", 0) + total.get("réfutée", 0)
        if tested:
            lines.append(f"  tenue sur les testables : {total.get('tenue', 0)}/{tested} "
                         f"= {total.get('tenue', 0) / tested:.0%}")
        if classified:
            share = total.get("non testable", 0) / classified
            flag = " ⚠" if share > UNTESTABLE_CEILING else ""
            lines.append(f"  non testable : {share:.0%} (plafond {UNTESTABLE_CEILING:.0%}){flag}")
    return lines


def find(decisions: list[Decision], query: str) -> list[Decision]:
    needle = query.casefold().strip()
    if not needle:
        return []
    return [decision for decision in decisions
            if needle in f"{decision.date} {decision.title} {decision.body}".casefold()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=paths.vault_root(), type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--due", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--validate", action="store_true")
    mode.add_argument("--find", metavar="TEXTE")
    mode.add_argument("--calibration", action="store_true")
    args = parser.parse_args()

    path = args.root / DECISIONS
    text = path.read_text(encoding="utf-8")
    today = dt.date.today()
    try:
        decisions = parse(text)
    except DecisionFormatError as exc:
        print(f"decisions.md refusé :\n{exc}", file=sys.stderr)
        return 1

    if args.validate:
        rubrics = sum(len(decision.predictions) for decision in decisions)
        predicted = sum(decision.has_prediction for decision in decisions)
        print(f"Prédictions indexables : {rubrics} rubrique(s), {predicted} décision(s).")
        return 0

    if args.calibration:
        print("\n".join(render_calibration(decisions)))
        return 0

    if args.find is not None:
        matches = find(decisions, args.find)
        if not matches:
            print(f"Aucune décision pour {args.find!r}.", file=sys.stderr)
            return 1
        if len(matches) > 10:
            print(f"{len(matches)} décisions correspondent ; précise la recherche.", file=sys.stderr)
            return 2
        for index, decision in enumerate(matches):
            if index:
                print("\n---\n")
            print(f"## {decision.date} — {decision.title}\n{decision.body.strip()}")
        return 0

    if args.due:
        due = [d for d in decisions if d.is_due(today)]
        if not due:
            print("Aucune prédiction arrivée à échéance.")
            return 0
        due.sort(key=lambda d: -overdue_days(d, today))
        print(f"Prédictions à revoir ({len(due)}), la plus en retard d'abord :")
        for d in due:
            print(f"  🔔 {d.date} (revue W{d.review_week}, en retard de "
                  f"{overdue_days(d, today)} j) — {d.title}")
        print("\nFerme chacune dans le corps de sa décision, sans toucher à la prédiction :\n  "
              + verdict_template(today))
        return 0

    try:
        drifted = views.apply(path, BLOCK, render_index(decisions, today), check=args.check)
    except ViewDrift as exc:
        sys.exit(str(exc))

    if not drifted:
        print("Index déjà à jour.")
    elif args.check:
        sys.exit("Index périmé — lancer `python3 scripts/decisions-index.py`.")
    else:
        print(f"Index régénéré ({len(decisions)} décisions).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
