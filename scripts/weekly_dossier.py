"""Le dossier du bilan du dimanche, calculé par le service AVANT le tour du modèle.

Audit du 2026-09-27 (constat RWF-009 resté partiel) : le bilan était un seul tour lourd
qui refaisait lui-même, CLI après CLI, des calculs déterministes — volume, jauge,
douleur, trajectoire, signaux, prédictions échues, échéances. Il lisait les prédictions
typées de PostgreSQL et pas celles de `decisions.md` (43 restées sans verdict), et il
pouvait sauter une étape sans que rien ne le voie.

Ici, tout ce qui se calcule est calculé une fois, par le même code que les CLI
(`ledger.week_report`, `signals.signals`, `model.decisions`), et remis au modèle comme
une donnée. Le modèle garde ce qui est à lui : juger, décider, écrire. Il relance une
CLI seulement pour vérifier un point précis.

Pas une CLI : aucun tour ne l'appelle, le service l'importe (`redline/vault.py`).
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import feasibility, ledger, permit_report, signals  # noqa: E402
from scripts.model import decisions as decision_log  # noqa: E402
from scripts.model import predictions as prediction_model  # noqa: E402
from scripts.model import programme  # noqa: E402

if TYPE_CHECKING:
    from scripts.storage import OperationalStore

#: Au-delà, le dossier résume au lieu d'énumérer : il est injecté dans un prompt.
MAX_LISTED = 12


def _as_date(value: Any) -> dt.date:
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value)[:10])


def render(store: OperationalStore, root: Path, week: str, today: dt.date) -> str:
    """Le dossier de la semaine `week` (celle qui se CLÔT), vu depuis `today`."""
    rows = ledger.parse_ledger(store)
    instruments = ledger.load_instruments(store)
    targets = programme.rpe_targets(root)
    lines = ["[DOSSIER DU BILAN, calculé par le service avant ce tour, invisible pour "
             "l'athlète. Ces chiffres sont déjà calculés par les mêmes fonctions que les CLI : "
             "cite-les, ne les recalcule pas ; relance une CLI seulement pour vérifier un "
             "point précis.]", ""]
    lines.extend(ledger.week_report(store, rows, instruments, targets, week, today,
                                    closing=True))

    weeks = signals._weeks(rows, 4)
    if weeks:
        found = signals.signals(rows, weeks, signals.intentional_cadence_keys(store))
        lines += ["", "Signaux faibles (4 semaines) :"]
        lines.extend(str(signal).strip() for signal in found)
        if not found:
            lines.append("rien à signaler")

    every = store.prediction_rows()
    typed = [row for row in every
             if row.get("status") == "open" and _as_date(row["due_on"]) <= today]
    lines += ["", f"Prédictions typées échues ({len(typed)}), ferme chacune avec "
                  "`fact-write.py prediction --key <clé> --outcome true|false|void` :"]
    lines.extend(f"- {row['prediction_key']} (échue le {_as_date(row['due_on']):%d/%m}) : "
                 f"{str(row.get('statement', ''))[:160]}" for row in typed[:MAX_LISTED])
    calibration = prediction_model.calibration_lines(every, today)
    lines += ["", "Calibration des prédictions typées par domaine (cite-la dans le bilan) :"]
    lines.extend(calibration or ["aucune prédiction typée"])

    decisions_path = root / "coaching" / "decisions.md"
    if decisions_path.exists():
        entries = decision_log.parse(decisions_path.read_text(encoding="utf-8"))
        due = sorted((entry for entry in entries if entry.is_due(today)),
                     key=lambda entry: -decision_log.overdue_days(entry, today))
        lines += ["", f"Prédictions narratives échues dans decisions.md ({len(due)}), la plus "
                      "en retard d'abord. Ferme-les dans le corps de leur décision, sans "
                      "toucher à la prédiction, avec : "
                      f"`{decision_log.verdict_template(today)}`. Un verdict s'adosse à un "
                      "fait lu ; non testable est un verdict, l'invention n'en est pas un. "
                      "Réfutée : optimiste si la décision a fait moins bien que prédit, "
                      "prudente si mieux. Non testable : instrument (la mesure n'existait "
                      "pas), exposition (la situation n'a pas eu lieu), portée (le critère "
                      "exigeait un inventaire exhaustif), remplacée (retirée avant sa revue). "
                      "Plusieurs volets : réfutée si un volet testable l'est, tenue si "
                      "tous les volets testables tiennent ; détail après le tiret."]
        lines.extend(f"- {entry.date} (revue W{entry.review_week}, +"
                     f"{decision_log.overdue_days(entry, today)} j) : {entry.title}"
                     for entry in due[:MAX_LISTED])
        if len(due) > MAX_LISTED:
            lines.append(f"- et {len(due) - MAX_LISTED} autre(s) "
                         "(`decisions-index.py --due`)")

    overdue = [row for row in store.deadline_rows()
               if row.get("status") != "done" and _as_date(row["due_on"]) < today]
    lines += ["", f"Échéances échues ({len(overdue)}). Pour chacune qui dépend d'une action "
                  "de l'athlète, donne-lui l'action prête à faire et une règle de repli datée :"]
    lines.extend(f"- {row['deadline_key']} (due le {_as_date(row['due_on']):%d/%m}) : "
                 f"{row['item']}" + (f" ; bloque {row['blocks']}" if row.get("blocks") else "")
                 for row in overdue[:MAX_LISTED])

    permit_lines = permit_report.report(store, today, week)
    if permit_lines:
        lines += ["", "Permis EF de la semaine close (choix scellés du coach face au "
                      "couru) : solde chaque prédiction permis-ef-* avec ces écarts, "
                      "puis scelle les EF de la semaine suivante avec "
                      "`fact-write.py permit` AVANT d'écrire la révision. L'EF ne "
                      "porte que son enveloppe (`duration_cap_min`), jamais ta durée, "
                      "et ton choix ne se révèle pas avant le bilan (protocols.md "
                      "§ Permis EF) :"]
        lines.extend(permit_lines)

    lines += ["", *feasibility.report(store, rows, today)]

    capabilities = store.latest_capabilities()
    if capabilities:
        lines += ["", "Capacités (dernière évaluation) : " + " · ".join(
            f"{row['slug']} {row['score']}/{row['scale']}" for row in capabilities)]
    lines.append("[/DOSSIER]")
    return "\n".join(lines)
