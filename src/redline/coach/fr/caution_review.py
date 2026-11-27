"""The coach's cautious decisions and what followed, as the Sunday review reads them."""

from __future__ import annotations

HEADER = (
    "Décisions prudentes des quatre dernières semaines, et ce qui a suivi. Réexamine aussi "
    "la prudence, pas seulement les excès : une bonne suite ne prouve pas que la restriction "
    "servait, une suite difficile ne prouve pas qu'elle ne servait pas. Si une restriction "
    "se répète sur un même fait sans suite qui la justifie, dis-le et propose ce qui change "
    "la prochaine fois ; sinon n'en parle pas :")

LINE = ("- {day:%d/%m} {title} : {cuts} (règle {rule}, fait {fact}) ; lendemain : {morning} ; "
        "{following}")

#: What a restriction removed, by `Cut` value.
CUTS = {"removed": "retirée", "cancelled": "annulée",
        "distance cap lowered": "plafond de distance baissé",
        "distance target lowered": "cible de distance baissée",
        "duration cap lowered": "plafond de durée baissé",
        "hard session replaced": "remplacée par une séance facile",
        "exercise dropped from strength": "exercice retiré du renfo",
        "strength at half dose": "renfo en demi-dose"}

MORNING_UNREAD = "trace non lue"

MORNING_AT_BASE = "trace à la base"

MORNING_ABOVE = "trace {above} au-dessus de la base"

NEXT_NONE = "aucune séance de {kind} depuis"

NEXT_ON = "{kind} suivant le {on:%d/%m}"

NEXT_RPE = "RPE {rpe} ({side})"

NEXT_PAIN = "douleur {pain}"

#: Where the next session's RPE fell against its range, by `RpeSide` slug.
SIDES = {"within": "dans la fourchette", "above": "au-dessus de la fourchette",
         "below": "en dessous de la fourchette", "unknown": "fourchette inconnue"}
