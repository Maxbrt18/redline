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
    "below_domain": "plus haut volume tenu {value:g} km, sous le domaine du gabarit "
                    "({reference:g} km)",
}
WEEK = "  {week} {phase}{deload} : {volume:g} km, SL {long_run}{segment}{keys}"
DELOAD = " (décharge)"
RACE_WEEK = "course"
LONG_RUN = "{km:g} km"
SEGMENT = " dont {km:g} km {intensity}"
KEYS = " ; {keys}"
KEY = "{role}{workout} {dose} {intensity}"
FLAGSHIP = "★ "
TEST = "test 1 200 m puis 3 600 m à fond (calibration)"
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
               "`redline cycle candidates` donne les trames. Entrer en préparation est le "
               "choix de l'athlète.")
UNNAMED_RACE = "l'objectif"
