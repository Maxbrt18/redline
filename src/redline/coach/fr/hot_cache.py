"""The generated blocks of the hot-cache, read first on every turn."""

from __future__ import annotations

EMPTY_LEDGER = "- **Forme** : registre vide."

NO_ZONE = "aucune zone surveillée"

LEVEL = "niveau"

AND = " et "

RED = " — **{join} au rouge**"

WALK_SHARE = ", dont **{walk_share} % de marche**"

WEEK_OPEN = "{week} en cours, semaine ouverte, ratio partiel"

WEEK_CLOSED = "{week} bouclée"

FORM = "- **Forme** : **{state}** — {run_km} km courus ({outings})"

FORM_WALK = " + {walk_km} km marchés"

FORM_TAIL = (
    ". **Tendance douleur, au PIC hebdo** (jamais la médiane), **effort/repos** : "
    "{trend}. **Jauge : elle se CALCULE, ne la réécris jamais de tête** — "
    "`redline ledger --week <W>`. {week} = **{h_eq} h-éq · {level}/100 · ratio "
    "{ratio}**{verdict}{walk_note}. Faits : PostgreSQL. Bloc **généré** par le service.")

STATE_HEADER = ("- **État calculé** depuis PostgreSQL et decisions.md par le service, ne pas "
                "l'éditer.")

LONGEST = "plus longue des 30 j **{km} km**"

STALL_PROTOCOL = " 🔴 protocole des baisses ouvert (s9)"

STALL = "sans nouveau record depuis {weeks} semaine(s) close(s){flag}"

RACE = "course le {race_on:%d/%m}, dans {weeks} semaine(s)"

TRAJECTORY = "- **Trajectoire** : "

DEADLINE_BLOCKS = ", bloque {blocks}"

OVERDUE = "- **Échéances échues** : {shown}{more}."

CAPABILITIES = "- **Capacités** : "

PREDICTIONS_DUE = (
    "- **Prédictions à solder** : {typed_due} (`redline fact-read predictions --due`).")
