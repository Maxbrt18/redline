"""What a debrief compares, as the coach reads it."""

from __future__ import annotations


def number(value: float) -> str:
    """`6,5`: at most two decimals, with a decimal comma."""
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def signed(value: float) -> str:
    """`+1,5`, `-2`."""
    return ("+" if value > 0 else "") + number(value)


def pace(seconds_per_km: float) -> str:
    """`5:42`: minutes and seconds per kilometre, as a runner reads a pace."""
    total = round(seconds_per_km)
    return f"{total // 60}:{total % 60:02d}"


def kilometres(numbers: tuple[int, ...]) -> str:
    """`km 3, 7 et 8`: the kilometres named by a gap, the first eight at most."""
    if len(numbers) > 8:
        return "km " + ", ".join(str(n) for n in numbers[:8]) + "…"
    if len(numbers) == 1:
        return f"km {numbers[0]}"
    return "km " + ", ".join(str(n) for n in numbers[:-1]) + f" et {numbers[-1]}"


DISTANCE_GAP = "distance {done} km pour un {label} de {planned} ({signed})"

DURATION_GAP = "durée {minutes:.0f} min pour un plafond de {duration_cap_min} ({signed})"

HR_GAP = "FC moyenne {hr} pour un plafond de {hr_cap_bpm} ({signed})"

AT_MOST = "au plus {high}"

RPE_GAP = "RPE {rpe} pour une cible {span}"

HR_AT_PACE = "FC à allure comparable : "

SESSION = "Séance"

TARGET = "cible"

CAP = "plafond"

SPLIT_HR_ABOVE_CAP = ("FC au-dessus du plafond de {cap} sur {count} km ({kms}), "
                      "jusqu'à {peak}")

SPLIT_FAST_START = "départ rapide : 1er km en {first} pour {median} sur les autres ({signed} %)"

SPLIT_FADE = "fin qui faiblit : {kms} en {last} contre {before} avant ({signed} %)"

SPLIT_DECOUPLING = "découplage allure/FC de {value} % entre les deux moitiés"

#: The kilometres a gap leaves out, by terrain slug (`redline.model.terrain.Terrain`).
RELIEF_KMS = {"climb": "{kms} en montée", "descent": "{kms} en descente",
              "rolling": "{kms} vallonnés"}

RELIEF = "relief : {parts} ; FC et allure jugées sur {count} km de plat"

RELIEF_NO_FLAT = "relief : {parts} ; aucun km plat, FC et allure des kilomètres non jugées"

#: The service's verdict on a run's execution, by `DebriefVerdict` slug.
VERDICT = "verdict du service : {verdict}"
VERDICTS = {
    "conforming": "conforme à la consigne",
    "execution_gap": "écart d'exécution",
    "undetermined": "indéterminé (pas de consigne ou rien à lire)",
}
#: Each finding says whose it is, so the coach knows what to do with it.
FINDINGS = {
    "flat_too_fast": ("plat trop rapide : FC au-dessus du plafond sur {kms} à {pace}/km, plus "
                      "vite que l'allure EF de référence ({reference_pace}/km) ; choix d'allure, "
                      "à corriger"),
    "flat_hr_high": ("FC haute sur le plat ({kms}) à {pace}/km, pas plus vite que l'allure EF de "
                     "référence ({reference_pace}/km) ; signal d'état ou capteur, jamais un "
                     "reproche"),
    "dplus_over": ("D+ {value} m pour {reference} m au plus ({signed} %) ; choix du parcours, "
                   "à part de la FC"),
    "dminus_over": ("D- {value} m pour {reference} m au plus ({signed} %) ; choix du parcours, "
                    "à part de la FC"),
    "climb_walked": "montées marchées : {kms} (consigne : toutes en courant) ; à nommer, sans reproche",
    "steep_run": ("pentes de {reference} % et plus courues : {kms} (consigne : marche vite) ; "
                  "écart d'exécution"),
    "steep_walked_share": "pentes de {reference} % et plus marchées à {share} %",
    "descent_not_held": ("descentes plus rapides que l'allure EF à plat : {kms} à {pace}/km "
                         "(consigne : retenues) ; écart d'exécution"),
    "run_above_prescription": ("séance courue au-dessus de la consigne : sa dose attend un RPE "
                               "de {value}, au-delà de {reference} pour la séance prescrite ; "
                               "choix d'exécution, à corriger, jamais le chiffre"),
}
#: The RPE the dose should cost, computed on the athlete's history: never a target.
EXPECTED_RUN = "RPE attendu pour la séance courue : {low}-{high} ({count} séances de calibration)"
EXPECTED_PRESCRIBED = " ; pour la séance prescrite : {low}-{high}"
#: A declared RPE out of its range, by `FindingKind` slug and by what the signs allow.
RPE_SIDES = {"rpe_above": "au-dessus", "rpe_below": "en dessous"}
RPE_SIGNS = {
    "execution": "écart d'exécution",
    "flat_hr_high": "FC haute sur le plat à allure habituelle",
    "hr_at_pace": "FC haute à allure comparable",
    "poor_night": "mauvaise nuit déclarée",
    "pattern": "deuxième séance de suite du même côté",
}
RPE_SILENT = ("RPE {rpe} {side} de la fourchette, sans second indice : n'en parle pas, la "
              "séance est notée")
RPE_ABOVE_EXECUTION = ("RPE {rpe} au-dessus de la fourchette ({signs}) : il suit "
                       "l'exécution ; la correction porte sur le choix, jamais sur le chiffre")
RPE_ABOVE_COSTLIER = ("RPE {rpe} au-dessus de la fourchette, avec {signs} : ça a coûté plus "
                      "que prévu ; nomme l'indice une fois, on ajuste la suite, sans reproche")
RPE_BELOW_SPOKEN = ("RPE {rpe} en dessous de la fourchette, avec {signs} : nomme-le une fois ; "
                    "jamais de hausse de dose sur ce seul signe")
RPE_PATTERN = ("RPE {rpe} {side} de la fourchette, deuxième séance de suite du même côté : "
               "une seule question, si sa réponse change la suite")
