"""The Sunday review dossier, computed by the service BEFORE the model's turn.

Volume, gauge, pain, trajectory, signals, due predictions and deadlines are computed once,
by the same code as the CLIs (`ledger.week_report`, `signals.signals`,
`model.predictions`), and handed to the model as data. The model keeps what is its own:
judging, deciding, writing. It reruns a CLI only to check a specific point.

Not a CLI: no turn calls it, the service imports it (`redline/vault.py`).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import TYPE_CHECKING, Any

from redline.analysis import feasibility, ledger, permit_report, signals
from redline.model import predictions as prediction_model
from redline.model import programme

if TYPE_CHECKING:
    from redline.storage import OperationalStore

#: Beyond this, the dossier summarises instead of listing: it is injected into a prompt.
MAX_LISTED = 12


def _as_date(value: Any) -> dt.date:
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value)[:10])


def render(store: OperationalStore, root: Path, week: str, today: dt.date) -> str:
    """The dossier for week `week` (the one that CLOSES), seen from `today`."""
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
    lines += ["", f"Prédictions échues ({len(typed)}), ferme chacune avec "
                  "`fact-write.py prediction --key <clé> --outcome true|false|void`. Un "
                  "verdict s'adosse à un fait lu ; void est un verdict, l'invention n'en "
                  "est pas un. Une réfutée porte `--reason optimistic` (moins bien que "
                  "prédit) ou `cautious` (mieux) ; une void `--reason instrument` (la "
                  "mesure n'existait pas), `exposure` (la situation n'a pas eu lieu), "
                  "`scope` (le critère exigeait un inventaire exhaustif) ou `superseded` "
                  "(retirée avant sa revue) :"]
    lines.extend(f"- {row['prediction_key']} (échue le {_as_date(row['due_on']):%d/%m}) : "
                 f"{str(row.get('statement', ''))[:160]}" for row in typed[:MAX_LISTED])
    calibration = prediction_model.calibration_lines(every, today)
    lines += ["", "Calibration des prédictions typées par domaine (cite-la dans le bilan) :"]
    lines.extend(calibration or ["aucune prédiction typée"])

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
