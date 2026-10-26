"""L'état du jour agrégé pour la jauge (PostgreSQL `daily_checkins`).

Le sommeil et la charge de vie sont deux **multiplicateurs de la jauge** (×1,1 à ×1,2,
`coaching/protocols.md`) et deux co-facteurs documentés des deux blessures. Ils ne
vivaient que dans `athlete/instruments.csv`, **une ligne par semaine**, renseignée le
dimanche : en semaine, le coach prescrivait sans savoir comment Sam avait dormi, et
le garde-fou n°4 (« 2-3 mauvaises nuits = semaine d'assimilation ») ne pouvait se
déclencher qu'avec jusqu'à six jours de retard.

**Contrainte de conception, non négociable** : cette donnée reste **déclarative et
conversationnelle**. Le ressenti de sommeil, la charge de vie et l'envie restent trois
mots, pas trois scores, et ne se restituent jamais en courbe.

**Une cellule vide est légitime** : il ne répond pas tous les jours, et une valeur
devinée serait pire qu'une absence (même règle qu'au registre).

"""

from __future__ import annotations

# --------------------------------------------------------------- l'agrégation
#
# Les deux seuils ci-dessous sont la seule chose de ce module qui touche à un nombre
# qui arbitre (la jauge). Ils sont ici, nommés, plutôt qu'enfouis dans une compréhension
# de liste : c'est ce qu'un rituel de confrontation doit pouvoir attaquer.

# Garde-fou n°4 du plan, mot pour mot : « 2-3 mauvaises nuits consécutives = on traite la
# semaine comme une semaine d'assimilation ». Le vocabulaire de la jauge distingue déjà
# ces deux paliers, on ne fait que les alimenter.
BAD_NIGHTS_FOR_ALERT = 2
BAD_NIGHTS_FOR_WEEK = 4

# Une journée lourde n'est pas une semaine lourde. Le multiplicateur ×1,1 vise le
# « seau à stress » d'une PÉRIODE (déménagement, rentrée, examens), pas un mardi pénible.
HEAVY_DAYS_FOR_ELEVEE = 3


def sleep_of(days: list) -> str | None:
    """La valeur `sommeil` de la jauge pour cette semaine, ou None si rien n'est déclaré.

    None et pas « ok » : une semaine sans déclaration n'est pas une bonne semaine, et
    lui faire dire « ok » ferait taire un multiplicateur par défaut.
    """
    declared = [day["sommeil"] for day in days if "sommeil" in day]
    if not declared:
        return None
    bad = declared.count("mauvaise")
    if bad >= BAD_NIGHTS_FOR_WEEK:
        return "degrade_semaine"
    if bad >= BAD_NIGHTS_FOR_ALERT:
        return "2_3_mauvaises_nuits"
    return "ok"


def life_load_of(days: list) -> str | None:
    declared = [day["charge_vie"] for day in days if "charge_vie" in day]
    if not declared:
        return None
    return "elevee" if declared.count("elevee") >= HEAVY_DAYS_FOR_ELEVEE else "normale"
