"""What the coach reads of a cycle's trame (`redline cycle`) and of the version in force."""

from __future__ import annotations

from .debrief import number

__all__ = ["number"]

KINDS = {"preparation": "Préparation", "general": "Préparation générale",
         "recovery": "Récupération", "rebuild": "Reprise"}
LEVELS = {"progress": "progresser", "maintain": "maintenir", "light": "détente"}
PHASES = {"recovery": "récupération", "development": "développement",
          "specific": "spécifique", "taper": "affûtage", "general": "générale",
          "rebuild": "reprise"}
SECOND = {"development": "dès le développement", "specific": "au spécifique"}

HEADER = ("{kind} « {template} » à partir de la semaine {origin} : départ {start} km/sem, "
          "plus haut volume tenu {held} km/sem.")
HEADER_GOAL = "  Objectif : course du {on:%d/%m/%Y}, {distance} km."
HEADER_CALIBRATION = "  Allures rendues depuis la calibration du {on:%d/%m/%Y}."
NO_CALIBRATION = "  Aucune calibration : les allures attendent un test."
HEADER_TIME = "  Temps disponible : {minutes} min/sem à {pace}/km en footing."
CANDIDATE = ("Candidat « {candidate} » : spécifique {specific} sem., décharge toutes les "
             "{every} sem., deuxième qualité {second}.")
NO_RISK = "  Aucun risque."
RISKS = "  Risques : {risks}."
RISK_TEXT = {
    "window_short": "fenêtre de {value:g} sem. pour {reference:g} au minimum",
    "specific_short": "spécifique de {value:g} sem. pour {reference:g} demandées",
    "time_bound": "le temps disponible plafonne le pic à {value:g} km (la rampe "
                  "permettait {reference:g})",
    "arrival_unreached": "{workout} ({phase}) finit à {value:g} cran(s) de son arrivée",
    "first_unplaced": "{workout} : aucune semaine de construction ne peut porter cette "
                      "première fois avant l'affûtage",
    "below_domain": "plus haut volume tenu {value:g} km, sous le domaine du gabarit "
                    "({reference:g} km)",
}
WEEK = "  {week} {phase}{deload} : {volume:g} km, SL {long_run}{segment}{keys}"
DELOAD = " (décharge)"
RACE_WEEK = "course"
LONG_RUN = "{km:g} km"
SEGMENT = " dont {km:g} km {intensity}"
KEYS = " ; {keys}"
KEY = "[{position}] {role}{workout} {dose} {intensity}"
FLAGSHIP = "★ "
TEST = "[0] test 1 200 m puis 3 600 m à fond (calibration)"
FIRST = "[{position}] 1re fois : {label}, sur la SL"
DOSE = "{reps} × {duration}, récup {recovery}"
PACE = "à {fast}–{slow}/km"
RPE = "RPE {low}–{high}"
HEART_RATE = "FC {low}–{high}"
UNCALIBRATED = "(allure à calibrer)"
NO_DURATION = "?"
UNKNOWN_DISTANCE = ("aucune course courue ou abandonnée dont lire la distance : la "
                    "récupération suit une course")
VERSION = ("Cycle en cours : {kind}{level}, version {version} ({cause}), de {start} à "
           "{end}. La semaine se prépare dans son cadre.")
VERSION_LEVEL = " « {level} »"
CAUSES = {"create": "créée pour l'objectif", "replan": "replanifiée",
          "transition": "après le cycle précédent", "level_change": "nouveau niveau",
          "block_end": "bloc suivant", "migration": "reprise de l'existant"}
NO_WEEK = "  Aucune semaine {week} dans ce cycle."
NO_CYCLE = "Aucun cycle en cours : le planificateur en met un en place à son prochain passage."
WINDOW_OPEN = ("La fenêtre de préparation de « {race} » ({on:%d/%m/%Y}) s'ouvre en {week} : "
               "une revue du plan propose un candidat (`redline cycle candidates`), "
               "l'athlète le prend.")
UNNAMED_RACE = "l'objectif"
PROPOSED = ("Candidat « {candidate} » proposé pour « {race} », ouvert jusqu'au {on:%d/%m} : "
            "l'athlète le prend d'un tap, le service lui envoie le résumé.")
LEVELED = "Préparation générale « {level} » à partir de {week}, annoncée à l'athlète."
STAGED = "{done} (appliqué à la fin du tour)"
CANDIDATES_UNAVAILABLE = ("[CANDIDATS indisponibles : calcul en échec, voir les journaux. "
                          "Relance toi-même `redline cycle candidates`.]")
PROPOSAL_OPEN = ("Proposition « {candidate} » envoyée, en attente de l'athlète jusqu'au "
                 "{until:%d/%m/%Y}.")
HYPOTHESES_HOLD = ("  Hypothèses de la préparation : toutes tiennent, le reste de la trame "
                   "est atteignable.")
HYPOTHESES_FALLEN = "  Hypothèses tombées ({outcome}) : {fallen}."
OUTCOMES = {"replan": "replanification : une revue du plan propose une nouvelle version",
            "recalibrate": "allures recalculées, la trame tient",
            "local": "la semaine s'adapte dans ses bornes"}
FALLEN_TEXT = {
    "volume_held": "volume : depuis le départ actuel, la rampe n'atteint que {value} km/sem "
                   "pour un pic de {reference} km/sem",
    "long_run_held": "sortie longue : depuis la plus longue récente, le pas n'atteint que "
                     "{value} km pour une SL de {reference} km",
    "lineage_position": "{workout} : {value} cran(s) de retard sur sa dernière dose, pour "
                        "{reference} exposition(s) qui progressent encore",
    "firsts": "premières fois : {value} restent à faire pour {reference} place(s) sur les "
              "semaines de construction",
    "availability": "temps disponible : {value} min/sem au lieu de {reference}, une semaine "
                    "restante n'y tient plus ou le pic plafonné pourrait monter",
    "goal_time": "chrono objectif changé",
    "calibration": "nouvelle calibration en vigueur",
}
FALLEN_GOAL = {"on": "la date de la course a changé",
               "distance_km": "la distance de la course a changé",
               "terrain": "le terrain de la course a changé",
               "firsts": "les premières fois que la course exige ont changé"}
NO_LIMIT = "sans limite"
NOTE_HEADER = ("# Plan en cours\n\n"
               "Projection de la version du cycle en cours, régénérée depuis PostgreSQL : ne "
               "pas l'éditer. `redline cycle show` en donne les allures du jour.\n")
NOTE_CYCLE = "\n{title}, version {version} ({cause}), de {start} à {end}.\n"
NOTE_RACE = "Course : {race}, le {on:%d/%m/%Y}.\n"
NOTE_TABLE = ("\n| Semaine | Phase | Volume au plus | SL au plus | Qualités | Séances clés |\n"
              "|---|---|---|---|---|---|\n")
NOTE_ROW = "| {week} | {phase}{deload} | {volume:g} km | {long_run} | {qualities} | {keys} |\n"


def note(kind: str, level: str, version: int, cause: str, weeks: list[tuple],
         race: tuple[str, object] | None) -> str:
    """The vault's note of the version in force: `weeks` holds, for each week, its label,
    phase, deload, volume, long run, qualities and key sessions in words."""
    title = KINDS[kind] + (f" « {LEVELS[level]} »" if level else "")
    text = NOTE_HEADER + NOTE_CYCLE.format(title=title, version=version, cause=CAUSES[cause],
                                           start=weeks[0][0], end=weeks[-1][0])
    if race is not None:
        text += NOTE_RACE.format(race=race[0], on=race[1])
    text += NOTE_TABLE
    for week, phase, deload, volume, long_run, qualities, keys in weeks:
        text += NOTE_ROW.format(week=week, phase=PHASES[phase], deload=DELOAD if deload else "",
                                volume=volume,
                                long_run=LONG_RUN.format(km=long_run) if long_run else RACE_WEEK,
                                qualities=qualities, keys=keys or "—")
    return text
